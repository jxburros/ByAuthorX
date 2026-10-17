#!/usr/bin/env python3
"""Measure quote verification: raw versus normalised pass rate and the `near` threshold sweep.

    uv run python scripts/eval_quotes.py                       # writes docs/benchmarks/context-0.4-quotes.json
    uv run python scripts/eval_quotes.py --out artifacts/q.json

* Pass rates: genuine quotes (the JATS wording of the 30 PLOS articles) checked against the PDF page they cite,
  with `quote_match.locate`: how many pass raw, how many only after normalisation.
* Threshold: `noisy` quotes (a few typing or OCR slips) and four kinds of not-a-quote (two words reworded,
  two sentences spliced, words shuffled, a sentence from a different passage) against passage-sized sources
  taken from the fixtures. `quote_match.NEAR_THRESHOLD` is chosen from this table, precision first.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from byax import evaluation as ev  # noqa: E402
from byax import quote_match  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = [70, 75, 80, 84, 86, 88, 90, 92, 94, 96, 98]


def passages(root: Path, papers: Path | None, per_library: int = 60, size: int = 1400) -> list[str]:
    """Passage-sized sources: windows of the text libraries and pages of the PDFs."""
    rng = random.Random(20261016)
    result = []
    for library in ('novel', 'series', 'humanities', 'webclips'):
        texts = []
        for path in sorted((root / library / 'corpus').rglob('*')):
            if path.suffix in {'.txt', '.html'}:
                raw = path.read_text(encoding='utf-8')
                texts.append(ev.html_text(raw) if path.suffix == '.html' else raw)
        for _ in range(per_library):
            text = rng.choice(texts)
            start = rng.randrange(0, max(1, len(text) - size))
            result.append(text[start : start + size])
    pdfs = sorted((papers or root / 'papers' / 'corpus').glob('*.pdf'))
    for path in pdfs:
        result.extend(p for p in ev.page_texts(path) if len(p) > 800)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--out', type=Path, default=ROOT / 'docs' / 'benchmarks' / 'context-0.4-quotes.json')
    args = parser.parse_args(argv)
    root = ev.fixtures_root()
    import tempfile

    import fetch_eval_fixtures as fetch

    with tempfile.TemporaryDirectory(prefix='byax-quotes-') as scratch:
        full = Path(scratch) / 'papers'
        fetch.materialize(root, ev.default_papers_cache(), full)
        have_full = len(list(full.glob('*.pdf'))) == 30
        corpus_dir = full if have_full else root / 'papers' / 'corpus'
        queries = ev.load_queries('papers')
        present = [q for q in queries if q['relevant_chunk_locators']]
        genuine = ev.genuine_quote_cases(corpus_dir, present)
        rates = ev.quote_pass_rates(genuine)
        cases = ev.altered_quote_cases(passages(root, corpus_dir))
        sweep = ev.sweep_near_threshold(cases, THRESHOLDS)
    report = {
        'schema_version': 1,
        'measured_at_utc': datetime.now(UTC).isoformat(),
        'environment': ev.environment(),
        'papers': 'full 30-article set' if have_full else 'committed PDFs only',
        'current_near_threshold': quote_match.NEAR_THRESHOLD,
        'genuine_quotes': rates,
        'threshold_cases': {k: sum(1 for c in cases if c['kind'] == k) for k in ev.QUOTE_KINDS},
        'threshold_sweep': sweep,
        'definitions': {
            'raw_pass_rate': 'Share of genuine quotes found byte for byte in the cited page text.',
            'normalized_pass_rate': 'Share found exactly or after NFC, quote/dash/ligature folding and hyphenation joining.',
            'precision': "Of altered or genuine-but-noisy quotes accepted as a near match, the share that were the passage's own words.",
            'recall': 'Share of noisy genuine quotes that a near match recovered.',
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'genuine_quotes': rates}, indent=1))
    for row in sweep:
        print(
            row['threshold'],
            'precision',
            row['precision'],
            'recall',
            row['recall'],
            'fpr',
            row['false_positive_rate'],
            row['false_positives_by_kind'],
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
