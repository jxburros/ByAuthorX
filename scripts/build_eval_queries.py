#!/usr/bin/env python3
"""Generate the golden retrieval queries `tests/fixtures/eval/<library>/queries.jsonl`.

Candidates come from deterministic rules over the fixture text (quotes, elided quotes, rare-term facts,
renamed-detail continuity, cross-story names, section headings, reference lists, absence probes) plus the
hand-written entries in `eval_queries_curated.py`. Every label is derived or checked mechanically:

* quote and elided-quote labels are all exact occurrences of the text in the corpus;
* curated labels are the paragraphs (or pages) containing hand-picked anchor strings, and the build
  fails if an anchor is missing or ambiguous;
* absence probes are verified against the whole corpus (an absent term occurs nowhere, a literal term
  occurs exactly where it is labelled).

Run `uv run python scripts/build_eval_queries.py` after the fixtures change; it rewrites the files (same
output for the same fixtures). The `papers` queries need the full 30-article set: run
`scripts/fetch_eval_fixtures.py` first.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from byax.evaluation import (  # noqa: E402
    SAMPLES,
    Shadow,
    default_papers_cache,
    fixtures_root,
    html_text,
    label_problems,
    page_texts,
    sample_paths,
    squash,
    truncate_text,
    validate_query,
)

SEED = 20261016
ABBREVIATIONS = (
    'Mr.',
    'Mrs.',
    'Ms.',
    'Dr.',
    'St.',
    'Col.',
    'Capt.',
    'Messrs.',
    'Esq.',
    'vs.',
    'etc.',
    'Sir.',
    'No.',
    'Fig.',
    'Eq.',
    'al.',
    'i.e.',
    'e.g.',
    'Jr.',
    'Hon.',
    'Rev.',
    'Ch.',
    'Vol.',
    'cf.',
)
STOP_START = re.compile(r'^(?:chapter|illustration|contents|volume|\[)', re.I)
WORD = re.compile(r"[A-Za-z][A-Za-z’'-]*[A-Za-z]")
BASE = {'novel': 'pride-and-prejudice.txt'}


class Lib:
    """Fixture library in locator coordinates: text by path, or page texts by path."""

    def __init__(self, name: str, root: Path, papers_dir: Path | None = None):
        self.name = name
        self.texts: dict[str, str] = {}
        self.pages: dict[str, list[str]] = {}
        source = papers_dir if (name == 'papers' and papers_dir) else root / name / 'corpus'
        self.source = source
        for path in sorted(p for p in source.rglob('*') if p.is_file()):
            relative = path.relative_to(source).as_posix()
            if path.suffix == '.pdf':
                self.pages[relative] = page_texts(path)
            elif path.suffix in {'.html', '.htm'}:
                self.texts[relative] = html_text(path.read_text(encoding='utf-8'))
            elif path.suffix == '.txt':
                self.texts[relative] = path.read_text(encoding='utf-8')
        self.shadows = {p: Shadow(t) for p, t in self.texts.items()}
        self.page_shadows = {p: [Shadow(t) for t in pages] for p, pages in self.pages.items()}
        self.page_squash = {p: [squash(t) for t in pages] for p, pages in self.pages.items()}
        self.rng = random.Random(f'{SEED}-{name}')
        # The pytest subset: which locator ranges survive in SAMPLES[name].
        sample = SAMPLES.get(name, {})
        kept = sample_paths(list(self.texts) + list(self.pages), sample)
        self.sample_extent: dict[str, int] = {}
        for path in kept:
            if path in self.texts:
                self.sample_extent[path] = len(truncate_text(self.texts[path], sample.get('prefix_chars')))
            else:
                self.sample_extent[path] = len(self.pages[path])
        self.units = self._units()
        self.df = Counter()
        self.inverted: dict[str, set[int]] = {}
        # Halves of words hyphenated at a line end: not real words, so never used as literal probes.
        self.split_words = {
            part.casefold()
            for _p, _a, _b, text in self.units
            for match in re.finditer(r'(\w+)-\s+(\w+)', text)
            for part in match.groups()
        }
        for number, (_, _, _, text) in enumerate(self.units):
            for word in {w.casefold() for w in WORD.findall(text)}:
                self.df[word] += 1
            for token in set(re.findall(r'[^\W_]+', text.casefold())):
                self.inverted.setdefault(token, set()).add(number)

    # -- units: paragraphs of text files, pages of PDFs --------------------------------------------
    def _units(self):
        units = []
        for path, text in self.texts.items():
            start = None
            offset = 0
            for line in text.splitlines(keepends=True):
                if line.strip():
                    if start is None:
                        start = offset
                    last = offset + len(line.rstrip())
                else:
                    if start is not None:
                        units.append((path, start, last, text[start:last]))
                    start = None
                offset += len(line)
            if start is not None:
                units.append((path, start, last, text[start:last]))
        for path, pages in self.pages.items():
            for number, text in enumerate(pages, 1):
                units.append((path, number, None, text))
        return units

    def in_sample(self, locator: dict) -> bool:
        size = self.sample_extent.get(locator['path'])
        if size is None:
            return False
        return locator['page'] <= size if 'page' in locator else locator['end'] <= size

    # -- locating strings -------------------------------------------------------------------------
    def occurrences(self, needle: str, *, casefold: bool = False) -> list[dict]:
        """Locators for every occurrence in the corpus (text spans; pages for PDFs)."""
        found = []
        for path, shadow in self.shadows.items():
            for start, end in shadow.find_all(needle, casefold=casefold):
                found.append({'path': path, 'start': start, 'end': end})
        key = needle.casefold() if casefold else needle
        for path, shadows in self.page_shadows.items():
            for number, shadow in enumerate(shadows, 1):
                haystack = shadow.text.casefold() if casefold else shadow.text
                if ' '.join(key.split()) in haystack:
                    found.append({'path': path, 'page': number})
        return found

    def paragraph_of(self, locator: dict) -> dict:
        if 'page' in locator:
            return {'path': locator['path'], 'page': locator['page']}
        for path, start, end, _ in self.units:
            if path == locator['path'] and end is not None and start <= locator['start'] < end:
                return {'path': path, 'start': start, 'end': end}
        raise ValueError(f'No paragraph for {locator}')

    def unit_locator(self, unit) -> dict:
        path, a, b, _ = unit
        return {'path': path, 'page': a} if b is None else {'path': path, 'start': a, 'end': b}

    def _candidates(self, terms: list[str]) -> list[int]:
        sets = [self.inverted.get(tok, set()) for t in terms for tok in re.findall(r'[^\W_]+', t.casefold())]
        return sorted(set.intersection(*sets)) if sets else []

    def units_containing(self, terms: list[str]) -> list[dict]:
        patterns = [
            re.compile(r'(?<![^\W_])' + re.escape(t).replace(r'\ ', r'\s+') + r'(?![^\W_])', re.I)
            for t in terms
        ]
        return [
            self.unit_locator(self.units[n])
            for n in self._candidates(terms)
            if all(p.search(self.units[n][3]) for p in patterns)
        ]

    def any_term(self, term: str) -> bool:
        return bool(self.units_containing([term]))

    def distinctive(self, word: str, limit: int = 3) -> bool:
        return len(word) >= 6 and self.df[word.casefold()] <= limit


def with_quote(locator: dict, quote: str) -> dict:
    return {**locator, 'quote': ' '.join(quote.split())[:160]}


def protect(text: str) -> str:
    for abbreviation in ABBREVIATIONS:
        text = text.replace(abbreviation, abbreviation.replace('.', '\x00'))
    return text


def sentences(shadow: Shadow):
    """Yield (shadow_start, shadow_end, sentence) with abbreviation dots protected."""
    text = protect(shadow.text)
    for match in re.finditer(r'[^.!?]+[.!?]+["”’]?', text):
        sentence = match.group().strip()
        start = match.start() + (len(match.group()) - len(match.group().lstrip()))
        yield start, start + len(sentence), shadow.text[start : start + len(sentence)]


def clean_edges(text: str) -> str:
    return text.strip(' “”"‘’,;:—-_()')


def good_sentence(sentence: str, minimum: int = 12, maximum: int = 70) -> bool:
    words = sentence.split()
    return (
        minimum <= len(words) <= maximum
        and not STOP_START.match(sentence)
        and '[' not in sentence
        and ']' not in sentence
        and '_' not in sentence
        and not re.search(r'\d{3}', sentence)
        and sum(1 for w in words if w[:1].isupper()) <= len(words) * 0.4
    )


def candidate_sentences(lib: Lib, *, minimum=12, maximum=70) -> list[tuple[str, int, int, str]]:
    result = []
    for path, shadow in lib.shadows.items():
        for start, end, sentence in sentences(shadow):
            if good_sentence(sentence, minimum, maximum):
                result.append((path, *shadow.span(start, end), sentence))
    for path, shadows in lib.page_shadows.items():
        for number, shadow in enumerate(shadows, 1):
            for _start, _end, sentence in sentences(shadow):
                # PDF text hyphenates at line ends; windows with a hyphen break cannot be matched literally.
                if good_sentence(sentence, minimum, maximum) and not re.search(r'\w- \w', sentence):
                    result.append((path, number, None, sentence))
    return result


def pick_window(lib: Lib, sentence: str, size=(8, 11)) -> str | None:
    words = sentence.split()
    best, best_score = None, 1
    for length in range(size[0], size[1] + 1):
        for i in range(0, len(words) - length + 1):
            window = words[i : i + length]
            score = sum(1 for w in window if lib.distinctive(re.sub(r'\W', '', w).casefold(), 6))
            if score > best_score or (score == best_score and best is None):
                best, best_score = window, score
    if best is None or best_score < 2:
        return None
    return clean_edges(' '.join(best))


def stratified(items: list, count: int, in_sample, rng: random.Random) -> list:
    """Half from the pytest sample region (when any exist), the rest from everywhere else."""
    items = list(items)
    rng.shuffle(items)
    inside = [i for i in items if in_sample(i)]
    outside = [i for i in items if not in_sample(i)]
    take_inside = min(len(inside), (count + 1) // 2)
    chosen = inside[:take_inside] + outside[: count - take_inside]
    if len(chosen) < count:
        chosen += [i for i in inside[take_inside:]][: count - len(chosen)]
    return chosen


class Builder:
    def __init__(self, lib: Lib):
        self.lib = lib
        self.records: list[dict] = []
        self.counters: Counter = Counter()
        self.seen_queries: set[str] = set()

    def add(self, kind: str, query: str, locators: list[dict], **extra) -> dict | None:
        key = ' '.join(query.split()).casefold()
        if key in self.seen_queries:
            return None
        self.seen_queries.add(key)
        self.counters[kind] += 1
        record = {
            'id': f'{self.lib.name}-{kind.split("_")[0][:5]}-{self.counters[kind]:03d}',
            'type': kind,
            'query': ' '.join(query.split()),
            'relevant_chunk_locators': locators,
            **extra,
        }
        validate_query(record)
        self.records.append(record)
        return record

    # -- known_item_quote ------------------------------------------------------------------------
    def quotes(self, count: int):
        lib = self.lib
        pool = []
        for path, a, b, sentence in candidate_sentences(lib):
            window = pick_window(lib, sentence)
            if window:
                pool.append((path, a, b, window))
        chosen = stratified(
            pool,
            count * 3,
            lambda item: lib.in_sample(self.locator_of(item)),
            random.Random(f'{SEED}-q-{lib.name}'),
        )
        for path, a, b, window in chosen:
            if self.counters['known_item_quote'] >= count:
                break
            labels = lib.occurrences(window)
            if not 1 <= len(labels) <= 6:
                continue
            self.add('known_item_quote', window, [with_quote(x, window) for x in labels])

    @staticmethod
    def locator_of(item):
        path, a, b, _ = item
        return {'path': path, 'page': a} if b is None else {'path': path, 'start': a, 'end': b}

    # -- citation_locating: elided quote of a long sentence --------------------------------------
    def elided(self, count: int):
        lib = self.lib
        pool = candidate_sentences(lib, minimum=22, maximum=60)
        chosen = stratified(
            pool,
            count * 3,
            lambda item: lib.in_sample(self.locator_of(item)),
            random.Random(f'{SEED}-e-{lib.name}'),
        )
        for path, a, b, sentence in chosen:
            if self.counters['citation_locating'] >= count:
                break
            words = sentence.split()
            head, tail = clean_edges(' '.join(words[:6])), clean_edges(' '.join(words[-6:]))
            if not (
                lib.df
                and any(lib.distinctive(re.sub(r'\W', '', w).casefold(), 20) for w in words[:6] + words[-6:])
            ):
                continue
            labels = lib.occurrences(' '.join(words))
            if not 1 <= len(labels) <= 4:
                continue
            self.add('citation_locating', f'{head} … {tail}', [with_quote(x, head) for x in labels])

    # -- factual_lookup: rare terms of one passage; every passage holding all of them is relevant -----
    def facts(self, count: int):
        lib = self.lib
        units = [u for u in lib.units if len(u[3]) > 200]
        rng = random.Random(f'{SEED}-f-{lib.name}')
        chosen = stratified(units, count * 4, lambda u: lib.in_sample(lib.unit_locator(u)), rng)
        for unit in chosen:
            if self.counters['factual_lookup'] >= count:
                break
            words = []
            for word in WORD.findall(unit[3]):
                low = word.casefold()
                if (
                    lib.distinctive(word, 3)
                    and low not in {w.casefold() for w in words}
                    and not word.endswith("'s")
                ):
                    words.append(word)
            if len(words) < 3:
                continue
            terms = sorted(words, key=lambda w: (lib.df[w.casefold()], w))[:3]
            terms = [w for w in words if w in terms]  # passage order
            labels = lib.units_containing(terms)
            if not 1 <= len(labels) <= 4:
                continue
            self.add('factual_lookup', ' '.join(terms), labels, terms=terms)

    # -- absence probes ----------------------------------------------------------------------------
    def absences(self, others: list[Lib], *, literal=10, straddle=4, absent=10, cooccur=6):
        lib = self.lib
        rng = random.Random(f'{SEED}-a-{lib.name}')
        # 1. rare single words that occur literally (false absence must not happen)
        words = sorted(
            {
                w.casefold()
                for w in WORD.findall(' '.join(u[3] for u in lib.units))
                if lib.distinctive(w, 2) and len(w) >= 7 and w.casefold() not in lib.split_words
            }
        )
        rng.shuffle(words)
        taken = 0
        sample_first = sorted(
            words, key=lambda w: not any(lib.in_sample(x) for x in lib.units_containing([w]))
        )
        for word in sample_first[: literal * 3]:
            labels = lib.units_containing([word])
            if 1 <= len(labels) <= 3 and taken < literal:
                self.add('absence_probe', word, labels, literal=True, expected_answer='present', terms=[word])
                taken += 1
        # 2. two-word phrases that occur only across a hard line wrap (text libraries)
        taken = 0
        if lib.texts:
            phrases = Counter()
            for path, text in lib.texts.items():
                for match in re.finditer(r'([A-Za-z]{4,})[ \t]*\n[ \t]*([A-Za-z]{4,})', text):
                    phrases[(match.group(1), match.group(2))] += 1
            keys = sorted(phrases)
            rng.shuffle(keys)
            for a, b in keys:
                phrase = f'{a} {b}'
                if taken >= straddle:
                    break
                if not (lib.distinctive(a, 40) and lib.distinctive(b, 40)):
                    continue
                labels = lib.occurrences(phrase, casefold=True)
                same_line = any(
                    re.search(r'(?<![^\W_])' + re.escape(phrase) + r'(?![^\W_])', t, re.I)
                    for t in lib.texts.values()
                )
                if same_line or not 1 <= len(labels) <= 3:
                    continue
                self.add(
                    'absence_probe', phrase, labels, literal=True, expected_answer='present', terms=[phrase],
                    note='phrase occurs only across a hard line wrap',
                )  # fmt: skip
                taken += 1
        # 3. words absent here but present in another library: genuinely absent terms
        taken = 0
        vocabulary = Counter()
        for other in others:
            for word in WORD.findall(' '.join(u[3] for u in other.units)):
                if len(word) >= 7:
                    vocabulary[word.casefold()] += 1
        candidates = sorted(w for w, n in vocabulary.items() if n >= 4)
        rng.shuffle(candidates)
        stems = {w[:6] for w in lib.df}
        for word in candidates:
            if taken >= absent:
                break
            if word[:6] in stems or lib.any_term(word):
                continue
            self.add('absence_probe', word, [], expected_absent=True, absence_kind='term')
            taken += 1
        # 4. two frequent words that never share a passage: the question is "in the same breath", not lexical
        taken = 0
        frequent = [w for w, n in lib.df.most_common(400) if 5 <= len(w) <= 10 and 12 <= n <= 60]
        rng.shuffle(frequent)
        for i in range(0, len(frequent) - 1, 2):
            if taken >= cooccur:
                break
            a, b = frequent[i], frequent[i + 1]
            if lib.units_containing([a, b]):
                continue
            self.add('absence_probe', f'{a} {b}', [], expected_absent=True, absence_kind='cooccurrence')
            taken += 1


def resolve_curated(builder: Builder, entries: list[dict]) -> None:
    """Hand-written queries: each anchor string is located in the corpus and expanded to its paragraph."""
    lib = builder.lib
    for entry in entries:
        locators = []
        for anchor in entry['anchors']:
            wanted = entry.get('docs')
            found = [
                x for x in lib.occurrences(anchor) if wanted is None or any(w in x['path'] for w in wanted)
            ]
            if not found:
                raise SystemExit(f'{lib.name}: anchor not found for {entry["query"]!r}: {anchor!r}')
            if len(found) > entry.get('max', 8):
                raise SystemExit(
                    f'{lib.name}: anchor too ambiguous ({len(found)}) for {entry["query"]!r}: {anchor!r}'
                )
            for x in found:
                paragraph = x if entry.get('scope') == 'exact' or 'page' in x else lib.paragraph_of(x)
                locators.append(with_quote(paragraph, anchor))
        unique = {
            json.dumps({k: v for k, v in x.items() if k != 'quote'}, sort_keys=True): x for x in locators
        }
        builder.add(entry['type'], entry['query'], list(unique.values()))


def build(name: str, lib: Lib, others: list[Lib]) -> list[dict]:
    import eval_queries_curated as curated

    builder = Builder(lib)
    plan = {
        'novel': dict(quotes=26, elided=10, facts=14, literal=10, straddle=4, absent=10, cooccur=6),
        'series': dict(quotes=28, elided=12, facts=16, literal=10, straddle=4, absent=10, cooccur=6),
        'humanities': dict(quotes=28, elided=12, facts=16, literal=10, straddle=4, absent=10, cooccur=6),
        'webclips': dict(quotes=30, elided=10, facts=16, literal=10, straddle=0, absent=10, cooccur=6),
        'papers': dict(quotes=0, elided=0, facts=0, literal=14, straddle=0, absent=14, cooccur=6),
    }[name]
    if plan['quotes']:
        builder.quotes(plan['quotes'])
    if plan['elided']:
        builder.elided(plan['elided'])
    if plan['facts']:
        builder.facts(plan['facts'])
    resolve_curated(builder, curated.CURATED.get(name, []))
    for hook in RECIPES.get(name, []):
        hook(builder, others)
    builder.absences(
        others,
        literal=plan['literal'],
        straddle=plan['straddle'],
        absent=plan['absent'],
        cooccur=plan['cooccur'],
    )
    return builder.records


def draft_continuity(builder: Builder, others: list[Lib], count: int = 28) -> None:
    """Details the earlier drafts changed: the canonical text, quoted, must not be confused with a draft."""
    import build_eval_corpus as corpus

    lib = builder.lib
    shadow = lib.shadows[BASE['novel']]
    rng = random.Random(f'{SEED}-draft-continuity')
    terms = []
    for spec in (corpus.DRAFT_A, corpus.DRAFT_B):
        for old, _new in spec['rules']:
            if old not in terms:
                terms.append(old)
    made, used = 0, []
    text = shadow.text
    for old in terms:
        spots = [m.start() for m in re.finditer(re.escape(old), text)]
        inside = [
            p
            for p in spots
            if lib.in_sample(
                {'path': BASE['novel'], 'start': shadow.original(p), 'end': shadow.original(p) + 1}
            )
        ]
        rng.shuffle(spots)
        for position in (inside[:1] + [p for p in spots if p not in inside])[:2]:
            before = text[max(0, position - 40) : position].split()[1:][-4:]
            after = text[position + len(old) : position + len(old) + 40].split()[:-1][:4]
            window = clean_edges(' '.join(before + [old] + after))
            labels = lib.occurrences(window)
            if not 1 <= len(labels) <= 3 or made >= count:
                continue
            if any(
                x['path'] == u['path'] and x['start'] < u['end'] and u['start'] < x['end']
                for x in labels
                for u in used
            ):
                continue  # one query per place, even when several renamed details share it
            if builder.add(
                'continuity',
                window,
                [with_quote(x, window) for x in labels],
                note=f'detail "{old}" differs in the drafts',
            ):
                used.extend(labels)
                made += 1


def entity_continuity(builder: Builder, others: list[Lib], count: int = 12, min_files: int = 2) -> None:
    """Names that recur across passages (and documents): every passage that mentions the name is relevant."""
    lib = builder.lib
    rng = random.Random(f'{SEED}-entity-{lib.name}')
    capital, lower = Counter(), Counter()
    for _, _, _, text in lib.units:
        for word in re.findall(r'\b[A-Za-z][a-z]{4,}\b', text):
            (capital if word[0].isupper() else lower)[word.casefold()] += 1
    skip = {
        m.casefold()
        for m in (
            'January',
            'February',
            'August',
            'September',
            'October',
            'November',
            'December',
            'Monday',
            'Tuesday',
            'Wednesday',
            'Thursday',
            'Friday',
            'Saturday',
            'Sunday',
            'Christmas',
            'English',
            'London',
            'Edit',
            'Retrieved',
            'Wikipedia',
            'Archived',
            'Wikimedia',
        )
    }
    names = sorted(w for w, n in capital.items() if n >= 4 and lower[w] <= 0.03 * n and w not in skip)
    rng.shuffle(names)
    made = 0
    for name in names:
        if made >= count:
            break
        labels = lib.units_containing([name])
        if not 4 <= len(labels) <= 12 or len({x['path'] for x in labels}) < min_files:
            continue
        if builder.add('continuity', name.capitalize(), [with_quote(x, name.capitalize()) for x in labels]):
            made += 1


def section_queries(builder: Builder, others: list[Lib], count: int = 18) -> None:
    """Thematic lookups of an article section by its heading (the label is the section's text)."""
    import html as htmllib

    lib = builder.lib
    rng = random.Random(f'{SEED}-sections')
    skip = {
        'see also',
        'references',
        'external links',
        'notes',
        'further reading',
        'bibliography',
        'sources',
        'citations',
        'footnotes',
        'contents',
    }
    options = []
    for path in sorted(lib.texts):
        html = (lib.source / path).read_text(encoding='utf-8')
        title_match = re.search(r'<title>(.*?) - Wikipedia', html)
        if not title_match:
            continue
        title = htmllib.unescape(title_match.group(1))
        headings = [
            htmllib.unescape(re.sub(r'<[^>]+>', '', h)).strip()
            for h in re.findall(r'<h2[^>]*>(.*?)</h2>', html, flags=re.S)
        ]
        text, cursor, starts = lib.texts[path], 0, []
        for heading in headings:
            index = text.find('\n\n' + heading + '\n', cursor)
            starts.append(None if index < 0 else index + 2)
            cursor = cursor if index < 0 else index + 2
        for number, heading in enumerate(headings):
            start = starts[number]
            if start is None or heading.casefold() in skip:
                continue
            following = [s for s in starts[number + 1 :] if s is not None]
            end = following[0] if following else len(text)
            if end - start > 400:
                options.append((path, title, heading, start, end))
    rng.shuffle(options)
    inside = [o for o in options if lib.in_sample({'path': o[0], 'start': o[3], 'end': o[4]})]
    ordered = [o for pair in zip(inside, [o for o in options if o not in inside], strict=False) for o in pair]
    ordered += [o for o in options if o not in ordered]
    seen, made = set(), 0
    for path, title, heading, start, end in ordered:
        if made >= count:
            break
        if path in seen:
            continue
        seen.add(path)
        locator = {'path': path, 'start': start, 'end': end, 'quote': heading}
        if builder.add('thematic', f'{title}: {heading}', [locator]):
            made += 1


_SKIP_TAGS = {
    'xref',
    'inline-formula',
    'disp-formula',
    'table-wrap',
    'fig',
    'sup',
    'sub',
    'ext-link',
    'supplementary-material',
    'inline-graphic',
}


def jats_text(element) -> str:
    """Running text of a JATS element without citation markers, formulas, figures and super/subscripts."""
    parts: list[str] = []

    def walk(node):
        if node.tag in _SKIP_TAGS:
            return
        parts.append(node.text or '')
        for child in node:
            walk(child)
            parts.append(child.tail or '')

    walk(element)
    return ' '.join(unicodedata.normalize('NFC', ''.join(parts)).split())


def paper_queries(builder: Builder, others: list[Lib]) -> None:
    """Citation locating on real PDF pages, using the JATS XML as the independent source of the text."""
    from defusedxml import ElementTree

    lib = builder.lib
    committed = set(SAMPLES['papers']['files'])
    for path in sorted(lib.pages, key=lambda p: (p not in committed, p)):
        rng = random.Random(f'{SEED}-paper-{path}')
        xml = lib.source / (Path(path).stem + '.xml')
        if not xml.exists():
            continue
        root = ElementTree.fromstring(xml.read_bytes())
        title = jats_text(root.find('.//article-title'))
        short = ' '.join(title.split()[:6])
        squashed = lib.page_squash[path]

        def pages_with(words: list[str]) -> list[int]:
            needle = squash(' '.join(words))
            return [n + 1 for n, page in enumerate(squashed) if needle in page]

        extra = 2 if path in committed else 1
        # citation_locating: an elided sentence from the body, located through its JATS wording
        paragraphs = [jats_text(p) for p in root.findall('.//body//p')]
        pool = []
        for paragraph in paragraphs:
            for _s, _e, sentence in sentences(Shadow(paragraph)):
                words = sentence.split()
                if 22 <= len(words) <= 50 and good_sentence(sentence, 22, 50):
                    pool.append(words)
        rng.shuffle(pool)
        made = 0
        for words in pool:
            head, tail = pages_with(words[:7]), pages_with(words[-7:])
            if len(head) == 1 and len(tail) == 1 and abs(head[0] - tail[0]) <= 1 and made < extra:
                query = f'{clean_edges(" ".join(words[:6]))} … {clean_edges(" ".join(words[-6:]))}'
                labels = {head[0]: ' '.join(words[:7]), tail[0]: ' '.join(words[-7:])}
                labels = [{'path': path, 'page': n, 'quote': q} for n, q in sorted(labels.items())]
                if builder.add('citation_locating', query, labels):
                    made += 1
        # known_item_quote: a verbatim fragment of the extracted PDF text
        sentences_here = [s for s in candidate_sentences(lib) if s[0] == path]
        rng.shuffle(sentences_here)
        made = 0
        for _p, number, _b, sentence in sentences_here:
            window = pick_window(lib, sentence)
            if not window or made >= extra:
                continue
            labels = lib.occurrences(window)
            labels = [x for x in labels if x['path'] == path]
            if 1 <= len(labels) <= 3 and builder.add(
                'known_item_quote', window, [with_quote(x, window) for x in labels]
            ):
                made += 1
        # factual_lookup: find the reference list entry (first author, year, start of the title)
        refs = []
        for ref in root.findall('.//ref-list/ref'):
            citation = ref.find('.//mixed-citation')
            if citation is None:
                citation = ref.find('.//element-citation')
            if citation is None:
                continue
            surname, year, ref_title = (
                citation.find('.//surname'),
                citation.find('.//year'),
                citation.find('.//article-title'),
            )
            if surname is None or year is None or ref_title is None or not (surname.text and year.text):
                continue
            words = jats_text(ref_title).split()
            if len(words) >= 6:
                refs.append((surname.text, year.text, words))
        rng.shuffle(refs)
        made = 0
        for surname, year, words in refs:
            found = pages_with(words[:6])
            if found and len(found) <= 2 and squash(surname) in squashed[found[0] - 1] and made < extra:
                query = f'{surname} {year} {" ".join(words[:7])}'
                if builder.add(
                    'factual_lookup',
                    query,
                    [{'path': path, 'page': n, 'quote': ' '.join(words[:6])} for n in found],
                ):
                    made += 1
        # thematic: a body section, asked for by its heading and the paper's subject
        sections = []
        for section in root.findall('.//body/sec'):
            heading = section.find('title')
            first = section.find('.//p')
            if heading is None or first is None:
                continue
            words = jats_text(first).split()
            if len(words) >= 12:
                sections.append((jats_text(heading), words))
        rng.shuffle(sections)
        made = 0
        for heading, words in sections:
            found = pages_with(words[:8])
            if len(found) == 1 and made < extra:
                query = f'{heading} {short}'
                if builder.add(
                    'thematic', query, [{'path': path, 'page': found[0], 'quote': ' '.join(words[:8])}]
                ):
                    made += 1


RECIPES: dict[str, list] = {
    'papers': [paper_queries],
    'novel': [draft_continuity],
    'series': [entity_continuity],
    'humanities': [lambda b, o: entity_continuity(b, o, 12, 1)],
    'webclips': [lambda b, o: entity_continuity(b, o, 12, 3), section_queries],
}


def write_records(path: Path, records: list[dict]) -> None:
    path.write_text(
        ''.join(json.dumps(r, ensure_ascii=False, sort_keys=False) + '\n' for r in records), encoding='utf-8'
    )


def verify(lib: Lib, records: list[dict]) -> list[str]:
    problems, cache = [], {}
    for record in records:
        problems += label_problems(record, lib.source, pages=lib.pages, texts=cache)
        for locator in record['relevant_chunk_locators']:
            for term in record.get('terms', []):
                text = (
                    lib.pages[locator['path']][locator['page'] - 1]
                    if 'page' in locator
                    else lib.texts[locator['path']][locator['start'] : locator['end']]
                )
                if not re.search(
                    r'(?<![^\W_])' + re.escape(term).replace(r'\ ', r'\s+') + r'(?![^\W_])', text, re.I
                ):
                    problems.append(f'{record["id"]}: term {term!r} missing from {locator}')
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--libraries', nargs='+', default=['novel', 'series', 'humanities', 'webclips', 'papers']
    )
    parser.add_argument(
        '--papers-dir', type=Path, default=None, help='flat folder holding the full paper set'
    )
    args = parser.parse_args(argv)
    root = fixtures_root()
    papers_dir = args.papers_dir
    if papers_dir is None and 'papers' in args.libraries:
        import fetch_eval_fixtures

        papers_dir = Path('artifacts') / 'papers-full'
        fetch_eval_fixtures.materialize(root, default_papers_cache(), papers_dir)
    libs = {name: Lib(name, root, papers_dir) for name in args.libraries}
    for name in args.libraries:
        lib = libs[name]
        others = [libs[o] for o in libs if o != name] or []
        records = build(name, lib, others)
        problems = verify(lib, records)
        if problems:
            print('\n'.join(problems[:40]), file=sys.stderr)
            return 1
        write_records(root / name / 'queries.jsonl', records)
        types = Counter(r['type'] for r in records)
        print(f'{name}: {len(records)} queries {dict(sorted(types.items()))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
