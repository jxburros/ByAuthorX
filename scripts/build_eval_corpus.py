#!/usr/bin/env python3
"""Rebuild the committed retrieval-evaluation corpora (maintainer tool; uses the network).

Writes `tests/fixtures/eval/<library>/corpus/**` for the `novel`, `series`, `humanities` and
`webclips` libraries and records every file's source, licence and SHA-256 in
`tests/fixtures/eval/MANIFEST.json`. Papers are handled by `fetch_eval_fixtures.py`, which owns the
`papers` section of the manifest; this script preserves that section when it rewrites the file.

Everything is deterministic: the Project Gutenberg texts and the pinned Wikipedia revisions are
downloaded once into a cache (default `artifacts/eval-cache`), transformed by the code below and
written out. Nothing here runs in tests or at application run time.

    uv run python scripts/build_eval_corpus.py [--cache DIR] [--only novel series humanities webclips]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / 'tests' / 'fixtures' / 'eval'
USER_AGENT = 'ByAuthorX-eval-fixtures/0.1 (+https://github.com/jxburros/ByAuthorX; evaluation fixtures)'

PG_LICENSE = (
    'Public domain in the United States (author died before 1930). Text from Project Gutenberg with the '
    'Project Gutenberg header, footer and trademark licence text removed, as that licence permits for public-domain works.'
)
PG_BOOKS = {
    'pride': (
        'https://www.gutenberg.org/cache/epub/1342/pg1342.txt',
        'Jane Austen, Pride and Prejudice (Project Gutenberg #1342)',
    ),
    'adventures': (
        'https://www.gutenberg.org/cache/epub/1661/pg1661.txt',
        'Arthur Conan Doyle, The Adventures of Sherlock Holmes (Project Gutenberg #1661)',
    ),
    'memoirs': (
        'https://www.gutenberg.org/cache/epub/834/pg834.txt',
        'Arthur Conan Doyle, The Memoirs of Sherlock Holmes (Project Gutenberg #834)',
    ),
    'gibbon': (
        'https://www.gutenberg.org/cache/epub/731/pg731.txt',
        'Edward Gibbon, History of the Decline and Fall of the Roman Empire, vol. 1 (Project Gutenberg #731)',
    ),
}

# Pinned Wikipedia revisions: (group, title, revision id, revision date). Permalinks never change.
WIKI_PINS = [
    ('regency', 'Regency architecture', 1308267364, '2025-08-28'),
    ('regency', 'Spencer (clothing)', 1316678688, '2025-10-13'),
    ('regency', 'Pelisse', 1348016668, '2026-04-10'),
    ('regency', 'Curricle', 1375913716, '2026-09-20'),
    ('regency', 'Tattersalls', 1378664805, '2026-10-05'),
    ('regency', 'Grand Pump Room', 1369285192, '2026-08-14'),
    ('regency', 'Gretna Green', 1373055591, '2026-09-03'),
    ('regency', 'Carlton House', 1375508312, '2026-09-18'),
    ('regency', 'Maria Fitzherbert', 1350973683, '2026-04-25'),
    ('regency', 'Royal Pavilion', 1380528286, '2026-10-16'),
    ('regency', 'Bow Street Runners', 1375531506, '2026-09-18'),
    ('regency', 'Vauxhall Gardens', 1375049707, '2026-09-15'),
    ('regency', 'Beau Brummell', 1376374869, '2026-09-23'),
    ('regency', 'Dandy', 1377101398, '2026-09-27'),
    ('victorian', 'Rookery', 1380561695, '2026-10-16'),
    ('victorian', 'Thames Embankment', 1373990799, '2026-09-09'),
    ('victorian', 'Mudlark', 1373858068, '2026-09-08'),
    ('victorian', 'Baker Street', 1377901590, '2026-10-01'),
    ('victorian', 'Pea soup fog', 1367712917, '2026-08-04'),
    ('victorian', 'Hansom cab', 1380278210, '2026-10-14'),
    ('victorian', 'Penny Black', 1375925358, '2026-09-20'),
    ('victorian', 'Ragged school', 1379331355, '2026-10-09'),
    ('victorian', 'Billingsgate Fish Market', 1376078599, '2026-09-21'),
    ('victorian', 'Joseph Bazalgette', 1380362386, '2026-10-15'),
    ('victorian', 'Peabody Trust', 1349776835, '2026-04-18'),
    ('victorian', '1854 Broad Street cholera outbreak', 1380080422, '2026-10-13'),
    ('compbio', 'Position weight matrix', 1281175370, '2025-03-18'),
    ('compbio', 'De Bruijn graph', 1368876410, '2026-08-11'),
    ('compbio', 'FASTA', 1368225930, '2026-08-07'),
    ('compbio', 'T-distributed stochastic neighbor embedding', 1374704843, '2026-09-13'),
    ('compbio', 'Sequence motif', 1355505261, '2026-05-22'),
    ('compbio', 'Sequence assembly', 1374886077, '2026-09-14'),
    ('compbio', 'Needleman–Wunsch algorithm', 1369642006, '2026-08-16'),
    ('compbio', 'Suffix array', 1343994068, '2026-03-17'),
    ('compbio', 'Gene prediction', 1368510425, '2026-08-09'),
    ('compbio', 'Smith–Waterman algorithm', 1376348078, '2026-09-23'),
    ('compbio', 'BLAST (biotechnology)', 1369222431, '2026-08-13'),
    ('compbio', 'Burrows–Wheeler transform', 1378563264, '2026-10-05'),
]
WIKI_LICENSE = (
    'CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/). Share-alike: these fixture files keep this '
    'licence and are not relicensed under the repository MIT licence.'
)

# --------------------------------------------------------------------------------------------------
# Downloading


def download(url: str, destination: Path, *, attempts: int = 12) -> bytes:
    """Fetch `url` once into the cache, honouring HTTP 429 Retry-After."""
    if destination.exists():
        return destination.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(attempts):
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
            destination.write_bytes(data)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 503}:
                raise
            time.sleep(int(exc.headers.get('retry-after', '30')) + 3)
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(5)
    raise RuntimeError(f'Gave up downloading {url}')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------------------------------
# Project Gutenberg text


def strip_gutenberg(raw: bytes) -> str:
    text = raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    start = re.search(r'^\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*\*\*\*\s*$', text, re.M)
    end = re.search(r'^\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*\*\*\*\s*$', text, re.M)
    if not start or not end or end.start() < start.end():
        raise ValueError('Project Gutenberg start/end markers not found')
    body = text[start.end() : end.start()]
    lines = [line.rstrip() for line in body.split('\n')]
    result = '\n'.join(lines).strip('\n') + '\n'
    if re.search(r'gutenberg', result, re.I):
        raise ValueError('Project Gutenberg text remains after stripping')
    return result


def slug(value: str) -> str:
    value = value.lower().replace('’', '').replace("'", '')
    return re.sub(r'[^a-z0-9]+', '-', value).strip('-')


ROMAN = r'(?:XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)'


def split_stories(text: str, directory: str) -> dict[str, str]:
    """One file per story. The contents list names the stories; the body repeats each title."""
    lines = text.split('\n')
    contents = [
        (m.group(1), re.sub(r'\s+', ' ', m.group(2).strip()))
        for line in lines[:80]
        if (m := re.match(rf'^\s+({ROMAN})\.\s+(\S.*)$', line))
    ]
    if len(contents) != 12:
        raise ValueError(f'Expected 12 stories in {directory}, found {len(contents)}')
    first_body = (
        max(index for index, line in enumerate(lines[:80]) if re.match(rf'^\s+{ROMAN}\.\s+\S', line)) + 1
    )
    starts = []
    cursor = first_body
    for numeral, title in contents:
        for index in range(cursor, len(lines)):
            if (
                re.sub(r'\s+', ' ', lines[index].replace('_', '').strip()).lower()
                == f'{numeral}. {title}'.lower()
            ):
                starts.append(index)
                cursor = index + 1
                break
        else:
            raise ValueError(f'Story heading not found: {numeral}. {title}')
    files = {}
    for position, (start, (numeral, title)) in enumerate(zip(starts, contents, strict=True)):
        stop = starts[position + 1] if position + 1 < len(starts) else len(lines)
        body = '\n'.join(lines[start:stop]).strip('\n') + '\n'
        files[f'{directory}/{position + 1:02d}-{slug(title)}.txt'] = body
    return files


# --------------------------------------------------------------------------------------------------
# Earlier-draft distractors for the novel

# Each map is applied to whole paragraphs after unwrapping, so names split across a line break are
# still renamed. Every rule must match at least once (the build fails otherwise), which also keeps the
# drafts honest if the source text ever changes.
DRAFT_A = {
    'title': 'FIRST IMPRESSIONS',
    'note': 'Earlier draft, chapters 1-6 (hand-made evaluation distractor: minor characters and places renamed, details changed).',
    'rules': [
        ('Mrs. Long', 'Mrs. Lang'),
        ('Mr. Morris', 'Mr. Morrow'),
        ('Mr. Robinson', 'Mr. Robertson'),
        ('Colonel Forster', 'Colonel Fairley'),
        ('Sir William', 'Sir Walter'),
        ('Lady Lucas', 'Lady Lucan'),
        ('Charlotte', 'Caroline'),
        ('Mr. Hurst', 'Mr. Hunt'),
        ('Mrs. Hurst', 'Mrs. Hunt'),
        ('Meryton', 'Merriton'),
        ('Longbourn', 'Longhurst'),
        ('four or five thousand a year', 'three or four thousand a year'),
        ('a chaise and four', 'a chaise and pair'),
        ('before Michaelmas', 'before Lady-day'),
        ('on Monday in a chaise', 'on Tuesday in a chaise'),
        ('three-and-twenty years', 'two-and-twenty years'),
        ('To-morrow fortnight.', 'To-morrow week.'),
        ('Her report was highly favourable.', 'Her report was far from favourable.'),
        (
            'he had a pleasant countenance, and easy, unaffected manners',
            'he had a ruddy countenance, and rather loud manners',
        ),
    ],
}
DRAFT_B = {
    'title': 'FIRST IMPRESSIONS (SECOND DRAFT)',
    'note': 'Later draft, chapters 1-6 (hand-made evaluation distractor: different renames and changed details).',
    'rules': [
        ('Mrs. Long', 'Mrs. Lyle'),
        ('Mr. Morris', 'Mr. Mortimer'),
        ('Mr. Robinson', 'Mr. Russell'),
        ('Colonel Forster', 'Colonel Fenton'),
        ('Sir William', 'Sir Thomas'),
        ('Charlotte', 'Harriet'),
        ('Mr. Hurst', 'Mr. Hargrave'),
        ('Mrs. Hurst', 'Mrs. Hargrave'),
        ('Kitty', 'Fanny'),
        ('Meryton', 'Maidenhall'),
        ('Hertfordshire', 'Berkshire'),
        ('four or five thousand a year', 'five or six thousand a year'),
        ('on Monday in a chaise and four', 'on Saturday in a curricle'),
        ('from the north of England', 'from the west of England'),
        ('these twenty years at least', 'these ten years at least'),
        ('many young men of four thousand a year', 'many young men of five thousand a year'),
    ],
}


def strip_brackets(text: str, opener: str) -> str:
    """Delete balanced `[...]` groups that start with `opener` (used for the 1894 plate markers)."""
    result, index = [], 0
    while index < len(text):
        if text.startswith(opener, index):
            depth = 0
            while index < len(text):
                depth += text[index] == '['
                depth -= text[index] == ']'
                index += 1
                if depth == 0:
                    break
            continue
        result.append(text[index])
        index += 1
    return ''.join(result)


def make_draft(novel: str, spec: dict) -> tuple[str, list[dict]]:
    start = novel.index('Chapter I.]')
    stop = novel.index('CHAPTER VII.')
    chapters = novel[start:stop].replace('Chapter I.]', 'CHAPTER I.', 1)
    chapters = strip_brackets(chapters, '[Illustration')
    paragraphs = [re.sub(r'\s+', ' ', p).strip() for p in re.split(r'\n\s*\n', chapters)]
    paragraphs = [p for p in paragraphs if p]
    report = []
    for old, new in spec['rules']:
        count = sum(p.count(old) for p in paragraphs)
        if count == 0:
            raise ValueError(f'Draft rule does not apply: {old!r}')
        paragraphs = [p.replace(old, new) for p in paragraphs]
        report.append({'from': old, 'to': new, 'occurrences': count})
    wrapped = [
        p if re.fullmatch(r'CHAPTER [IVXL]+\.', p) else textwrap.fill(p, width=72, break_long_words=False)
        for p in paragraphs
    ]
    header = f'{spec["title"]}\n\n{textwrap.fill(spec["note"], width=72)}\n'
    return header + '\n' + '\n\n'.join(wrapped) + '\n', report


# --------------------------------------------------------------------------------------------------
# Wikipedia


def clean_wikipedia(raw: bytes) -> str:
    """Drop invisible bulk only: scripts, styles, comments, <link> tags and presentation attributes.

    Every element and every piece of visible text of the page chrome (header, menus, sidebar tools, language
    list, footer) and of the article stays, as do ids and hrefs; classes, titles and ARIA/data attributes are
    removed because they roughly doubled the size without adding any text.
    """
    html = raw.decode('utf-8')
    html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S)
    html = re.sub(r'<style\b.*?</style>', '', html, flags=re.S)
    html = re.sub(r'<!--.*?-->', '', html, flags=re.S)
    html = re.sub(r'<link\b[^>]*>', '', html)
    html = re.sub(
        r'\s(?:srcset|sizes|decoding|loading|style|class|title|data-[a-z0-9-]+|aria-[a-z]+)="[^"]*"', '', html
    )
    html = re.sub(r'[ \t]+\n', '\n', html)
    return re.sub(r'\n{2,}', '\n', html)


def permalink(title: str, revision: int) -> str:
    return 'https://en.wikipedia.org/w/index.php?' + urllib.parse.urlencode(
        {'title': title, 'oldid': revision}
    )


# --------------------------------------------------------------------------------------------------


def entry(path: Path, **fields) -> dict:
    data = path.read_bytes()
    return {'path': path.relative_to(EVAL).as_posix(), 'bytes': len(data), 'sha256': sha256(data), **fields}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode('utf-8'))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--cache', type=Path, default=ROOT / 'artifacts' / 'eval-cache')
    parser.add_argument('--only', nargs='+', choices=['novel', 'series', 'humanities', 'webclips'])
    args = parser.parse_args(argv)
    wanted = set(args.only or ['novel', 'series', 'humanities', 'webclips'])
    manifest_path = EVAL / 'MANIFEST.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    libraries = manifest.setdefault('libraries', {})

    def gutenberg(key):
        url, citation = PG_BOOKS[key]
        raw = download(url, args.cache / 'gutenberg' / url.rsplit('/', 1)[1])
        return strip_gutenberg(raw), {
            'source_url': url,
            'source_sha256': sha256(raw),
            'attribution': citation,
        }

    if 'novel' in wanted:
        text, source = gutenberg('pride')
        files = []
        base = EVAL / 'novel' / 'corpus'
        files.append(
            entry(
                write(base / 'pride-and-prejudice.txt', text),
                license=PG_LICENSE,
                modifications='Header, footer and trademark text removed; CRLF to LF; trailing spaces removed.',
                **source,
            )
        )
        for name, spec in (
            ('draft-first-impressions-1.txt', DRAFT_A),
            ('draft-first-impressions-2.txt', DRAFT_B),
        ):
            draft, report = make_draft(text, spec)
            files.append(
                entry(
                    write(base / 'drafts' / name, draft),
                    license='Derived from the public-domain text above; edits made by scripts/build_eval_corpus.py and dedicated to the public domain.',
                    attribution='Synthetic distractor derived from ' + source['attribution'],
                    source_url=source['source_url'],
                    source_sha256=source['source_sha256'],
                    modifications='Chapters 1-6, plates removed, paragraphs rewrapped at 72 columns, edits: '
                    + json.dumps(report, ensure_ascii=False),
                )
            )
        libraries['novel'] = {
            'description': 'Pride and Prejudice plus two earlier-draft distractors covering chapters 1-6.',
            'license': PG_LICENSE,
            'files': files,
        }

    if 'series' in wanted:
        files = []
        for key, directory in (('adventures', 'adventures'), ('memoirs', 'memoirs')):
            text, source = gutenberg(key)
            for relative, body in split_stories(text, directory).items():
                files.append(
                    entry(
                        write(EVAL / 'series' / 'corpus' / relative, body),
                        license=PG_LICENSE,
                        modifications='One story per file; header, footer and trademark text removed.',
                        **source,
                    )
                )
        libraries['series'] = {
            'description': 'The Adventures and The Memoirs of Sherlock Holmes, one file per story (24 files).',
            'license': PG_LICENSE,
            'files': files,
        }

    if 'humanities' in wanted:
        text, source = gutenberg('gibbon')
        libraries['humanities'] = {
            'description': 'Gibbon, Decline and Fall of the Roman Empire, vol. 1, with Milman notes.',
            'license': PG_LICENSE,
            'files': [
                entry(
                    write(EVAL / 'humanities' / 'corpus' / 'decline-and-fall-vol-1.txt', text),
                    license=PG_LICENSE,
                    modifications='Header, footer and trademark text removed; CRLF to LF; trailing spaces removed.',
                    **source,
                )
            ],
        }

    if 'webclips' in wanted:
        files = []
        for group, title, revision, date in WIKI_PINS:
            url = permalink(title, revision)
            raw = download(url, args.cache / 'wikipedia' / f'{slug(title)}-{revision}.html')
            html = clean_wikipedia(raw)
            path = write(EVAL / 'webclips' / 'corpus' / group / f'{slug(title)}.html', html)
            files.append(
                entry(
                    path,
                    license=WIKI_LICENSE,
                    attribution=f'Wikipedia contributors, "{title}", revision {revision} ({date}), '
                    f'https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(" ", "_"))}; history lists the authors.',
                    source_url=url,
                    source_sha256=sha256(raw),
                    modifications='Removed <script>, <style>, comments, <link> tags and class/title/ARIA/data/style attributes to save space; all elements and visible text of the page chrome and article are as served.',
                    group=group,
                )
            )
        libraries['webclips'] = {
            'description': '38 Wikipedia articles (Regency era, Victorian London, computational biology methods) as served, chrome kept.',
            'license': WIKI_LICENSE,
            'files': files,
        }

    manifest['schema_version'] = 1
    manifest['notes'] = (
        'Every fixture file with its source, licence, attribution and SHA-256. Paths are relative to this '
        'directory. The papers section is maintained by scripts/fetch_eval_fixtures.py.'
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    total = sum(f['bytes'] for lib in libraries.values() for f in lib.get('files', []))
    print(f'{sum(len(lib.get("files", [])) for lib in libraries.values())} files, {total / 1e6:.2f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
