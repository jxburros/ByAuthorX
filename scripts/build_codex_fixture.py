#!/usr/bin/env python3
"""Build tests/fixtures/codex/pride-and-prejudice-1-6.json from a local copy of Project Gutenberg #1342.

    curl -L -o pg1342.txt https://www.gutenberg.org/cache/epub/1342/pg1342.txt   # explicit, by hand
    uv run python scripts/build_codex_fixture.py pg1342.txt

The script never downloads anything. It checks the file's SHA-256, keeps chapters I-VI of the novel
text only (no Project Gutenberg header, licence or trademark), removes illustration captions and
the underscores that mark italics, and joins wrapped lines into paragraphs.

Gold mentions come from the rule list below, which is independent of ``byax.codex`` (Python ``re``
alternation, longest surface first, word-bounded). Every context of the ambiguous surfaces
("Miss Bennet", "Lucas", "Catherine", the plural family names) was reviewed by hand: in these
chapters "Miss Bennet" always means Jane, "Catherine" is Kitty, and plurals ("the Miss Bennets",
"the Lucases", "a young Lucas") name no codex entry. The labels therefore measure the matcher
(boundaries, longest match, letter case, possessives, curly quotes) against a writer's codex, not
coreference: pronouns and descriptions ("his wife", "the eldest") are never mentions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'tests' / 'fixtures' / 'codex' / 'pride-and-prejudice-1-6.json'
SHA256 = '3f6bb9d6f78e0293b56acd4714dd68cb7d6d1d293402031ce9d5a216bcaf9d75'

# entry id: (kind, canonical name, aliases, case sensitive)
CODEX = {
    'cx_elizabeth': (
        'character',
        'Elizabeth Bennet',
        ['Elizabeth', 'Lizzy', 'Eliza', 'Miss Eliza', 'Miss Eliza Bennet', 'Miss Elizabeth Bennet'],
        True,
    ),
    'cx_jane': ('character', 'Jane Bennet', ['Jane', 'Miss Bennet'], True),
    'cx_mary': ('character', 'Mary Bennet', ['Mary'], True),
    'cx_kitty': ('character', 'Kitty Bennet', ['Kitty', 'Catherine'], True),
    'cx_lydia': ('character', 'Lydia Bennet', ['Lydia'], True),
    'cx_mr_bennet': ('character', 'Mr. Bennet', [], True),
    'cx_mrs_bennet': ('character', 'Mrs. Bennet', [], True),
    'cx_bingley': ('character', 'Mr. Bingley', ['Bingley', 'Charles Bingley'], True),
    'cx_caroline': ('character', 'Miss Bingley', ['Caroline Bingley', 'Caroline'], True),
    'cx_darcy': ('character', 'Mr. Darcy', ['Darcy', 'Fitzwilliam Darcy'], True),
    'cx_mrs_hurst': ('character', 'Mrs. Hurst', ['Louisa'], True),
    'cx_mr_hurst': ('character', 'Mr. Hurst', [], True),
    'cx_charlotte': ('character', 'Charlotte Lucas', ['Charlotte', 'Miss Lucas'], True),
    'cx_sir_william': ('character', 'Sir William Lucas', ['Sir William'], True),
    'cx_lady_lucas': ('character', 'Lady Lucas', [], True),
    'cx_maria': ('character', 'Maria Lucas', [], True),
    'cx_mrs_long': ('character', 'Mrs. Long', [], True),
    'cx_netherfield': ('place', 'Netherfield Park', ['Netherfield', 'Netherfield House'], True),
    'cx_longbourn': ('place', 'Longbourn', [], True),
    'cx_meryton': ('place', 'Meryton', [], True),
    'cx_lucas_lodge': ('place', 'Lucas Lodge', [], True),
    'cx_london': ('place', 'London', [], True),
    'cx_hertfordshire': ('place', 'Hertfordshire', [], True),
    'cx_derbyshire': ('place', 'Derbyshire', [], True),
    'cx_pride': ('term', 'pride', [], False),
}
# Gold rules: (surface, entry, case-insensitive). Written from reading the chapters, not from CODEX.
GOLD_RULES = [
    ('Miss Elizabeth Bennet', 'cx_elizabeth', False),
    ('Elizabeth Bennet', 'cx_elizabeth', False),
    ('Miss Eliza Bennet', 'cx_elizabeth', False),
    ('Miss Eliza', 'cx_elizabeth', False),
    ('Elizabeth', 'cx_elizabeth', False),
    ('Lizzy', 'cx_elizabeth', False),
    ('Eliza', 'cx_elizabeth', False),
    ('Miss Bennet', 'cx_jane', False),
    ('Jane', 'cx_jane', False),
    ('Mary', 'cx_mary', False),
    ('Kitty', 'cx_kitty', False),
    ('Catherine', 'cx_kitty', False),
    ('Lydia', 'cx_lydia', False),
    ('Mr. Bennet', 'cx_mr_bennet', False),
    ('Mrs. Bennet', 'cx_mrs_bennet', False),
    ('Mr. Bingley', 'cx_bingley', False),
    ('Miss Bingley', 'cx_caroline', False),
    ('Bingley', 'cx_bingley', False),
    ('Mr. Darcy', 'cx_darcy', False),
    ('Darcy', 'cx_darcy', False),
    ('Mrs. Hurst', 'cx_mrs_hurst', False),
    ('Mr. Hurst', 'cx_mr_hurst', False),
    ('Miss Lucas', 'cx_charlotte', False),
    ('Charlotte', 'cx_charlotte', False),
    ('Sir William Lucas', 'cx_sir_william', False),
    ('Sir William', 'cx_sir_william', False),
    ('Lady Lucas', 'cx_lady_lucas', False),
    ('Maria Lucas', 'cx_maria', False),
    ('Mrs. Long', 'cx_mrs_long', False),
    ('Netherfield Park', 'cx_netherfield', False),
    ('Netherfield House', 'cx_netherfield', False),
    ('Netherfield', 'cx_netherfield', False),
    ('Longbourn', 'cx_longbourn', False),
    ('Meryton', 'cx_meryton', False),
    ('Lucas Lodge', 'cx_lucas_lodge', False),
    ('London', 'cx_london', False),
    ('Hertfordshire', 'cx_hertfordshire', False),
    ('Derbyshire', 'cx_derbyshire', False),
    ('pride', 'cx_pride', True),
]
# Reviewed surfaces that are not mentions of any entry (kept for documentation and tests).
NEGATIVES = ['the Miss Bennets', 'the Miss Lucases', 'the Bennets', 'the Lucases', 'a young Lucas', 'proud']


def chapters(source: str) -> list[dict]:
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == 'Chapter I.]')
    end = next(i for i, line in enumerate(lines) if line.strip() == 'CHAPTER VII.')
    result = [{'title': 'Chapter I', 'paragraphs': []}]
    paragraph: list[str] = []
    in_caption = False

    def flush():
        text = re.sub(r'\s+', ' ', ' '.join(part.strip() for part in paragraph)).replace('_', '').strip()
        if text:
            result[-1]['paragraphs'].append(text)
        paragraph.clear()

    for line in lines[start + 1 : end]:
        stripped = line.strip()
        if in_caption:
            in_caption = not stripped.endswith(']')
            continue
        if stripped.startswith('[Illustration'):
            flush()
            in_caption = not stripped.endswith(']')
            continue
        heading = re.fullmatch(r'CHAPTER ([IVX]+)\.', stripped)
        if heading:
            flush()
            result.append({'title': 'Chapter ' + heading.group(1), 'paragraphs': []})
        elif not stripped:
            flush()
        else:
            paragraph.append(stripped)
    flush()
    return result


def gold(chapter_list: list[dict]) -> list[dict]:
    exact = sorted((r for r in GOLD_RULES if not r[2]), key=lambda r: -len(r[0]))
    folded = [r for r in GOLD_RULES if r[2]]
    pattern = re.compile(r'(?<!\w)(' + '|'.join(re.escape(s) for s, _, _ in exact) + r')(?!\w)')
    loose = re.compile(r'(?<!\w)(' + '|'.join(re.escape(s) for s, _, _ in folded) + r')(?!\w)', re.IGNORECASE)
    entry_of = {s: e for s, e, _ in exact}
    labels = []
    for c, chapter in enumerate(chapter_list):
        for p, text in enumerate(chapter['paragraphs']):
            spans = [(m.start(), m.end(), entry_of[m.group(1)]) for m in pattern.finditer(text)]
            spans += [(m.start(), m.end(), 'cx_pride') for m in loose.finditer(text)]
            for start, end, entry in sorted(spans):
                labels.append(
                    {
                        'chapter': c,
                        'paragraph': p,
                        'start': start,
                        'end': end,
                        'entry': entry,
                        'surface': text[start:end],
                    }
                )
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('source', help='Local copy of https://www.gutenberg.org/cache/epub/1342/pg1342.txt')
    parser.add_argument('--out', default=str(OUTPUT))
    args = parser.parse_args()
    raw = Path(args.source).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SHA256:
        raise SystemExit(
            f'Unexpected SHA-256 {digest}; expected {SHA256} (the edition this fixture records).'
        )
    chapter_list = chapters(raw.decode('utf-8'))
    entries = {
        key: {
            'id': key,
            'kind': kind,
            'name': name,
            'aliases': aliases,
            **({} if case else {'case_sensitive': False}),
        }
        for key, (kind, name, aliases, case) in CODEX.items()
    }
    fixture = {
        'source': {
            'title': 'Pride and Prejudice, chapters I-VI',
            'author': 'Jane Austen',
            'url': 'https://www.gutenberg.org/ebooks/1342',
            'file': 'https://www.gutenberg.org/cache/epub/1342/pg1342.txt',
            'sha256': SHA256,
            'rights': 'Public domain in the United States. Novel text only: the Project Gutenberg header, '
            'licence and trademark are not included.',
            'processing': 'Chapters I-VI; illustration captions and italic underscores removed; wrapped lines joined.',
        },
        'labelling': 'Rule list in scripts/build_codex_fixture.py (independent of byax.codex), with every context of '
        'the ambiguous surfaces reviewed by hand. Pronouns and descriptions are not mentions.',
        'negatives': NEGATIVES,
        'chapters': chapter_list,
        'codex': entries,
        'gold': gold(chapter_list),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    words = sum(len(p.split()) for c in chapter_list for p in c['paragraphs'])
    print(
        f'{args.out}: {len(chapter_list)} chapters, {words} words, {len(fixture["gold"])} labelled mentions'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
