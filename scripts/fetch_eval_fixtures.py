#!/usr/bin/env python3
"""Download the 30 PLOS articles (PDF + JATS XML) used by the `papers` evaluation library.

Only three small articles are committed under `tests/fixtures/eval/papers/corpus/` so offline tests
cover the PDF paths. This script fetches the other 27 from pinned PLOS URLs, verifies the SHA-256
recorded in `tests/fixtures/eval/MANIFEST.json` for every file, and caches them outside the repository
(`--cache-dir`, `$BYAX_EVAL_CACHE`, or the user cache directory). It runs only when you invoke it; no
test, import or application code downloads anything.

    uv run python scripts/fetch_eval_fixtures.py                # fetch + verify the full set
    uv run python scripts/fetch_eval_fixtures.py --check        # verify what is present, never download
    uv run python scripts/fetch_eval_fixtures.py --record       # maintainers: (re)pin URLs and hashes

All articles are CC BY 4.0 (the licence URL is read from each article's JATS XML and recorded).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from byax.evaluation import default_papers_cache, fixtures_root, sha256_bytes  # noqa: E402

USER_AGENT = 'ByAuthorX-eval-fixtures/0.1 (+https://github.com/jxburros/ByAuthorX; evaluation fixtures)'
# DOI list of the pinned set; the first three are the small articles committed to the repository.
COMMITTED = ['10.1371/journal.pcbi.1004150', '10.1371/journal.pcbi.1004961', '10.1371/journal.pcbi.1004743']
FETCHED = [
    '10.1371/journal.pcbi.1012024', '10.1371/journal.pcbi.1009041', '10.1371/journal.pcbi.1010944',
    '10.1371/journal.pcbi.1010445', '10.1371/journal.pcbi.1010984', '10.1371/journal.pcbi.1011990',
    '10.1371/journal.pcbi.1008032', '10.1371/journal.pcbi.1006036', '10.1371/journal.pcbi.1004388',
    '10.1371/journal.pcbi.1004384', '10.1371/journal.pcbi.1010372', '10.1371/journal.pcbi.1012018',
    '10.1371/journal.pcbi.1010540', '10.1371/journal.pcbi.1011698', '10.1371/journal.pcbi.1008260',
    '10.1371/journal.pcbi.1005373', '10.1371/journal.pone.0301069', '10.1371/journal.pone.0255260',
    '10.1371/journal.pone.0220182', '10.1371/journal.pone.0221631', '10.1371/journal.pone.0226256',
    '10.1371/journal.pone.0253949', '10.1371/journal.pone.0242366', '10.1371/journal.pone.0278301',
    '10.1371/journal.pone.0288140', '10.1371/journal.pone.0276337', '10.1371/journal.pcbi.1011305',
]  # fmt: skip


def urls(doi: str) -> tuple[str, str]:
    journal = 'ploscompbiol' if '.pcbi.' in doi else 'plosone'
    base = f'https://journals.plos.org/{journal}/article/file?id={doi}&type='
    return base + 'printable', base + 'manuscript'


def stem(doi: str) -> str:
    return doi.split('journal.', 1)[1]


def download(url: str, attempts: int = 8) -> bytes:
    for _ in range(attempts):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 502, 503}:
                raise
            time.sleep(int(exc.headers.get('retry-after', '20')) + 2)
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(5)
    raise RuntimeError(f'Gave up downloading {url}')


def jats_metadata(xml: bytes) -> dict:
    from defusedxml import ElementTree

    root = ElementTree.fromstring(xml)
    namespaces = {'xlink': 'http://www.w3.org/1999/xlink'}
    title = ''.join(root.find('.//article-title').itertext()).strip()
    journal = ''.join(root.find('.//journal-title').itertext()).strip()
    year = root.find('.//pub-date/year')
    license_element = root.find('.//license')
    license_url = (
        license_element.get('{%s}href' % namespaces['xlink']) if license_element is not None else None
    )
    authors = []
    for contrib in root.findall('.//contrib[@contrib-type="author"]'):
        surname, given = contrib.find('.//surname'), contrib.find('.//given-names')
        if surname is not None:
            authors.append(f'{surname.text}, {given.text if given is not None else ""}'.strip(', '))
    return {
        'title': ' '.join(title.split()),
        'journal': journal,
        'year': int(year.text) if year is not None else None,
        'license_url': license_url,
        'authors': authors,
    }


def attribution(meta: dict, doi: str) -> str:
    names = meta['authors']
    byline = '; '.join(names[:3]) + (' et al' if len(names) > 3 else '')
    return f'{byline}. "{meta["title"]}". {meta["journal"]} ({meta["year"]}). https://doi.org/{doi}. Licensed CC BY 4.0.'


def load_manifest(root: Path) -> dict:
    path = root / 'MANIFEST.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def record(root: Path, cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    corpus = root / 'papers' / 'corpus'
    corpus.mkdir(parents=True, exist_ok=True)
    pinned = []
    for doi in COMMITTED + FETCHED:
        pdf_url, xml_url = urls(doi)
        name = stem(doi)
        entry = {
            'doi': doi,
            'name': name,
            'committed': doi in COMMITTED,
            'pdf_url': pdf_url,
            'xml_url': xml_url,
        }
        for kind, url in (('pdf', pdf_url), ('xml', xml_url)):
            target = (corpus if entry['committed'] else cache) / f'{name}.{kind}'
            data = target.read_bytes() if target.exists() else download(url)
            target.write_bytes(data)
            entry[f'{kind}_bytes'], entry[f'{kind}_sha256'] = len(data), sha256_bytes(data)
            if kind == 'xml':
                meta = jats_metadata(data)
                if not (meta['license_url'] or '').rstrip('/').endswith('/licenses/by/4.0'):
                    raise SystemExit(f'{doi}: not CC BY 4.0 ({meta["license_url"]})')
                entry.update(title=meta['title'], journal=meta['journal'], year=meta['year'],
                             license='CC BY 4.0', license_url=meta['license_url'],
                             attribution=attribution(meta, doi))  # fmt: skip
        pinned.append(entry)
        print('pinned', doi, entry['pdf_bytes'], flush=True)
    manifest = load_manifest(root)
    manifest.setdefault('libraries', {})['papers'] = {
        'description': '30 PLOS Computational Biology / PLOS ONE articles as PDF plus JATS XML; 3 committed, 27 fetched.',
        'license': 'CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); attribution per article below.',
        'fetch': 'scripts/fetch_eval_fixtures.py verifies every file against the SHA-256 recorded here.',
        'pinned': pinned,
    }
    (root / 'MANIFEST.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
    )
    return manifest


def locate(entry: dict, kind: str, root: Path, cache: Path) -> Path:
    name = f'{entry["name"]}.{kind}'
    return (root / 'papers' / 'corpus' / name) if entry['committed'] else (cache / name)


def fetch(root: Path, cache: Path, *, download_missing: bool = True) -> dict[str, list[str]]:
    manifest = load_manifest(root)
    pinned = manifest.get('libraries', {}).get('papers', {}).get('pinned', [])
    if not pinned:
        raise SystemExit('MANIFEST.json has no pinned papers; run with --record first')
    cache.mkdir(parents=True, exist_ok=True)
    result: dict[str, list[str]] = {'ok': [], 'downloaded': [], 'missing': [], 'mismatch': []}
    for entry in pinned:
        for kind in ('pdf', 'xml'):
            path = locate(entry, kind, root, cache)
            label = path.name
            if not path.exists():
                if not download_missing or entry['committed']:
                    result['missing'].append(label)
                    continue
                path.write_bytes(download(entry[f'{kind}_url']))
                result['downloaded'].append(label)
            if sha256_bytes(path.read_bytes()) != entry[f'{kind}_sha256']:
                result['mismatch'].append(label)
                if not entry['committed']:
                    path.unlink()
            else:
                result['ok'].append(label)
    return result


def materialize(root: Path, cache: Path, destination: Path) -> int:
    """Copy the full verified set into one flat folder (the layout the harness indexes)."""
    manifest = load_manifest(root)
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in manifest['libraries']['papers']['pinned']:
        for kind in ('pdf', 'xml'):
            source = locate(entry, kind, root, cache)
            if source.exists():
                shutil.copyfile(source, destination / source.name)
                count += 1
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--cache-dir', type=Path, default=None)
    parser.add_argument('--check', action='store_true', help='verify present files; never download')
    parser.add_argument('--record', action='store_true', help='maintainers: download and pin URLs and hashes')
    parser.add_argument('--materialize', type=Path, help='also copy the full verified set into this folder')
    args = parser.parse_args(argv)
    root, cache = fixtures_root(), args.cache_dir or default_papers_cache()
    if args.record:
        record(root, cache)
        return 0
    result = fetch(root, cache, download_missing=not args.check)
    print(json.dumps({key: len(value) for key, value in result.items()}))
    for key in ('missing', 'mismatch'):
        for label in result[key]:
            print(f'{key}: {label}', file=sys.stderr)
    if args.materialize:
        print(f'materialized {materialize(root, cache, args.materialize)} files into {args.materialize}')
    return 1 if result['mismatch'] or (result['missing'] and not args.check) else 0


if __name__ == '__main__':
    sys.exit(main())
