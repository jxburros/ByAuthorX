"""Precision and recall of the grammar tiers on the labelled fixture (docs/benchmarks/0.3-grammar-*.json).

    uv run python scripts/grammar_benchmark.py                       # built-in rules: print the numbers
    uv run python scripts/grammar_benchmark.py --write               # refresh the published built-in JSON
    uv run python scripts/grammar_benchmark.py --languagetool DIR    # also start LanguageTool from DIR
    uv run python scripts/grammar_benchmark.py --languagetool-url http://127.0.0.1:8081 --write

The fixture (tests/fixtures/grammar/en.txt) is written by hand, one sentence per line, with each error
marked as [[flagged text|rule_id]] and every other line a clean sentence. A built-in finding counts as
correct when its rule group and exact range equal a label; a label written [[text|?group]] marks a real
error the rules are known not to see. LanguageTool has no matching rule groups, so it is scored on
overlap: a labelled error is found when any LanguageTool match overlaps its range, and every match on a
clean sentence or away from every label is counted as unlabelled (it may be a true catch of an error the
fixture does not mark, or a style suggestion). Precision for LanguageTool is therefore a lower bound.
These are small curated fixtures; they show where each rule family stands, not how it performs on a novel.
"""

import argparse
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
FIXTURES = ROOT / 'tests' / 'fixtures' / 'grammar'
OUTPUT = ROOT / 'docs' / 'benchmarks' / '0.3-grammar-builtin.json'
LT_OUTPUT = ROOT / 'docs' / 'benchmarks' / '0.3-grammar-languagetool.json'
MARKER = re.compile(r'\[\[(.*?)\|([?a-z_0-9]+)\]\]')


def load_corpus(path=FIXTURES / 'en.txt'):
    """Items as ``{'text', 'errors': [{'start', 'end', 'rule'}]}``; lines without markers are clean."""
    items = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        text, errors, last = '', [], 0
        for match in MARKER.finditer(line):
            text += line[last : match.start()]
            errors.append(
                {'start': len(text), 'end': len(text) + len(match.group(1)), 'rule': match.group(2)}
            )
            text += match.group(1)
            last = match.end()
        text += line[last:]
        items.append({'text': text, 'errors': errors})
    return items


def measure_open(check, corpus=None):
    """Overlap-scored breadth on the open fixture; ``check(text)`` returns matches with start and end."""
    corpus = corpus if corpus is not None else load_corpus(FIXTURES / 'en-open.txt')
    labelled = found = clean_findings = elsewhere = on_label = 0
    for item in corpus:
        matches = check(item['text'])
        for error in item['errors']:
            labelled += 1
            found += any(m['start'] < error['end'] and error['start'] < m['end'] for m in matches)
        for m in matches:
            if any(m['start'] < e['end'] and e['start'] < m['end'] for e in item['errors']):
                on_label += 1
            elif item['errors']:
                elsewhere += 1
            else:
                clean_findings += 1
    return {
        'sentences': len(corpus),
        'labelled_errors': labelled,
        'found': found,
        'recall': _ratio(found, labelled),
        'matches_on_clean_sentences': clean_findings,
        'matches_elsewhere_in_sentences_with_errors': elsewhere,
    }


def _ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def _scores(tp, fp, fn):
    return {
        'true_positives': tp,
        'false_positives': fp,
        'missed': fn,
        'precision': _ratio(tp, tp + fp),
        'recall': _ratio(tp, tp + fn),
    }


def measure_builtin(corpus=None, language='en'):
    from byax.grammar import BuiltinEngine, LEVELS, rule_set

    corpus = corpus if corpus is not None else load_corpus()
    rules = rule_set(language)
    group_of = {r.id.split('.', 1)[1]: r.group for r in rules['rules']}
    engine = BuiltinEngine(LEVELS['broad'])
    tp, fp, fn = Counter(), Counter(), Counter()
    rule_tp, rule_fp = Counter(), Counter()
    expected_by_rule, clean_findings, false_positives, missed = Counter(), 0, [], []
    clean = positives = 0
    for item in corpus:
        found = engine.check(item['text'], language)
        keys = Counter((group_of[m['rule'].split('.', 1)[1]], m['start'], m['end']) for m in found)
        wanted = Counter()
        for error in item['errors']:
            group = error['rule'][1:] if error['rule'].startswith('?') else group_of[error['rule']]
            wanted[(group, error['start'], error['end'])] += 1
            expected_by_rule[error['rule']] += 1
        for key, count in (keys & wanted).items():
            tp[key[0]] += count
        for key, count in (keys - wanted).items():
            fp[key[0]] += count
            false_positives.append(
                {'text': item['text'], 'group': key[0], 'flagged': item['text'][key[1] : key[2]]}
            )
        for key, count in (wanted - keys).items():
            fn[key[0]] += count
            missed.append({'text': item['text'], 'group': key[0], 'expected': item['text'][key[1] : key[2]]})
        for m in found:
            rule = m['rule'].split('.', 1)[1]
            if (group_of[rule], m['start'], m['end']) in wanted:
                rule_tp[rule] += 1
            else:
                rule_fp[rule] += 1
        if item['errors']:
            positives += 1
        else:
            clean += 1
            clean_findings += len(found)
    groups = sorted(set(group_of.values()))
    result = {
        'corpus': {
            'sentences': len(corpus),
            'with_errors': positives,
            'clean': clean,
            'labelled_errors': sum(len(i['errors']) for i in corpus),
        },
        'groups': {g: _scores(tp[g], fp[g], fn[g]) for g in groups},
        'overall': _scores(sum(tp.values()), sum(fp.values()), sum(fn.values())),
        'clean_sentence_findings': clean_findings,
        'rules': {
            rule: {
                'labelled': expected_by_rule[rule],
                'true_positives': rule_tp[rule],
                'false_positives': rule_fp[rule],
            }
            for rule in sorted(group_of)
            if expected_by_rule[rule] or rule_fp[rule]
        },
        'rules_without_a_labelled_example': sorted(r for r in group_of if not expected_by_rule[r]),
        'false_positives': false_positives,
        'missed': missed,
    }
    from byax.grammar import BuiltinEngine as _Engine

    result['open_coverage'] = measure_open(lambda text: _Engine(LEVELS['broad']).check(text, language))
    return result


def measure_languagetool(url, language='en-US', corpus=None):
    from byax.grammar import LanguageToolEngine

    corpus = corpus if corpus is not None else load_corpus()
    engine = LanguageToolEngine(url)
    found_by_group, total_by_group = Counter(), Counter()
    matches_clean, matches_unlabelled, matches_on_labels, latencies = 0, 0, 0, []
    rule_ids = Counter()
    for item in corpus:
        start = time.perf_counter()
        matches = engine.check(item['text'], language)
        latencies.append(time.perf_counter() - start)
        for error in item['errors']:
            group = error['rule'][1:] if error['rule'].startswith('?') else None
            if group is None:
                group = _group(error['rule'])
            total_by_group[group] += 1
            if any(m['start'] < error['end'] and error['start'] < m['end'] for m in matches):
                found_by_group[group] += 1
        for m in matches:
            rule_ids[m['lt_rule']] += 1
            if any(m['start'] < e['end'] and e['start'] < m['end'] for e in item['errors']):
                matches_on_labels += 1
            elif item['errors']:
                matches_unlabelled += 1
            else:
                matches_clean += 1
    ordered = sorted(latencies)
    return {
        'languagetool_version': engine.version,
        'language': language,
        'labelled_errors': sum(total_by_group.values()),
        'recall_overall': _ratio(sum(found_by_group.values()), sum(total_by_group.values())),
        'recall_by_group': {g: _ratio(found_by_group[g], total_by_group[g]) for g in sorted(total_by_group)},
        'matches_overlapping_a_label': matches_on_labels,
        'matches_elsewhere_in_sentences_with_errors': matches_unlabelled,
        'matches_on_clean_sentences': matches_clean,
        'precision_lower_bound': _ratio(
            matches_on_labels, matches_on_labels + matches_unlabelled + matches_clean
        ),
        'open_coverage': measure_open(lambda text: engine.check(text, language)),
        'top_rules': dict(rule_ids.most_common(12)),
        'latency_ms': {
            'median': round(1000 * statistics.median(latencies), 1),
            'p95': round(1000 * ordered[int(0.95 * (len(ordered) - 1))], 1),
            'sentences': len(latencies),
        },
    }


_GROUPS = None


def _group(rule):
    global _GROUPS
    if _GROUPS is None:
        from byax.grammar import rule_set

        _GROUPS = {r.id.split('.', 1)[1]: r.group for r in rule_set('en')['rules']}
    return _GROUPS[rule]


def measure_builtin_speed(repeat=5):
    from byax.grammar import BuiltinEngine, LEVELS

    corpus = load_corpus()
    engine = BuiltinEngine(LEVELS['broad'])
    words = sum(len(i['text'].split()) for i in corpus)
    engine.check('warm up', 'en')
    timings = []
    for _ in range(repeat):
        start = time.perf_counter()
        for item in corpus:
            engine.check(item['text'], 'en')
        timings.append(time.perf_counter() - start)
    best = min(timings)
    return {
        'sentences': len(corpus),
        'words': words,
        'seconds_best_of': best,
        'words_per_second': round(words / best),
    }


def measure_document_speed(words_target=100_000):
    """Full and one-paragraph incremental grammar checks on a document of about 100,000 words."""
    from byax import engine
    from byax.checks import run_checks
    from byax.model import new_document

    sentences = [i['text'] for i in load_corpus()]
    paragraphs, count, index = [], 0, 0
    while count < words_target:
        chunk = [sentences[(index + k) % len(sentences)] for k in range(6)]
        index += 6
        paragraphs.append(' '.join(chunk))
        count += sum(len(c.split()) for c in chunk)
    doc = new_document(text='\n'.join(paragraphs))
    start = time.perf_counter()
    first = run_checks(doc, ['grammar'], limit=10**6, track=True)
    full = time.perf_counter() - start
    edited, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'set_text', 'target': doc['blocks'][500]['id'], 'text': 'An edited paragraph.'}
            ]
        },
    )
    start = time.perf_counter()
    second = run_checks(edited, ['grammar'], limit=10**6, previous=first, track=True)
    incremental = time.perf_counter() - start
    return {
        'words': count,
        'paragraphs': len(paragraphs),
        'findings': len(first['findings']),
        'full_check_seconds': round(full, 2),
        'one_paragraph_edit_seconds': round(incremental, 3),
        'units_rechecked_after_edit': len(second['evaluation']['rechecked']),
    }


def measure_languagetool_startup(directory, port=8099, memory_mb=1024):
    """Cold start of a server from ``directory`` until its health check answers; stops it afterwards."""
    from byax import grammar

    config = grammar.load_config()
    config['languagetool'].update(directory=str(directory), port=port, memory_mb=memory_mb)
    begin = time.perf_counter()
    status = grammar.start_languagetool(config, wait=120)
    ready = time.perf_counter() - begin
    first = time.perf_counter()
    grammar.LanguageToolEngine(status['url']).check('This is a first request after the start.', 'en-US')
    first_request = time.perf_counter() - first
    rss = None
    try:
        pid = json.loads(grammar._pid_file().read_text(encoding='utf-8'))['pid']
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS'):
                rss = int(line.split()[1]) // 1024
    except (OSError, ValueError, KeyError):
        pass
    size = sum(f.stat().st_size for f in Path(directory).rglob('*') if f.is_file()) // (1024 * 1024)
    return {
        'status': status,
        'seconds_until_healthy': round(ready, 1),
        'seconds_first_check': round(first_request, 1),
        'resident_memory_mb': rss,
        'installed_size_mb': size,
        'stop': grammar.stop_languagetool(config),
    }


def _environment():
    java = subprocess.run(['java', '-version'], capture_output=True, text=True, check=False)
    return {
        'python': platform.python_version(),
        'platform': platform.platform(),
        'java': (java.stderr or java.stdout).strip().splitlines()[-3:] if java.returncode == 0 else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--write', action='store_true', help='refresh the published JSON files')
    parser.add_argument('--languagetool', metavar='DIR', help='start a server from this LanguageTool folder')
    parser.add_argument('--languagetool-url', help='measure an already running local server')
    args = parser.parse_args()
    built = measure_builtin()
    print(
        json.dumps(
            {k: built[k] for k in ('corpus', 'groups', 'overall', 'clean_sentence_findings')}, indent=1
        )
    )
    if args.write:
        OUTPUT.write_text(json.dumps(built, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    if args.languagetool or args.languagetool_url:
        url, startup = args.languagetool_url, None
        if args.languagetool:
            startup = measure_languagetool_startup(args.languagetool)
            url = startup['status']['url']
            print(json.dumps({k: v for k, v in startup.items() if k != 'status'}, indent=1))
            from byax import grammar

            config = grammar.load_config()
            config['languagetool'].update(directory=args.languagetool, port=startup['status']['port'])
            grammar.start_languagetool(config, wait=120)
        try:
            measured = measure_languagetool(url)
        finally:
            if args.languagetool:
                from byax import grammar

                grammar.stop_languagetool(config)
        measured['environment'] = _environment()
        measured['measured_on'] = datetime.now(UTC).strftime('%Y-%m-%d')
        if startup:
            measured['startup'] = {k: v for k, v in startup.items() if k not in ('status', 'stop')}
        print(json.dumps(measured, indent=1))
        if args.write:
            LT_OUTPUT.write_text(json.dumps(measured, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    if args.write:
        speed = {
            'builtin': measure_builtin_speed(),
            'document_100k_words': measure_document_speed(),
            'environment': _environment(),
        }
        (ROOT / 'docs' / 'benchmarks' / '0.3-grammar-performance.json').write_text(
            json.dumps(speed, indent=1) + '\n', encoding='utf-8'
        )


if __name__ == '__main__':
    main()
