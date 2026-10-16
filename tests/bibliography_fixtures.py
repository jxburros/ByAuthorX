"""Recorded HTTP fixtures for bibliography checks: replay in CI, record on demand.

``ReplayTransport`` answers requests from ``tests/fixtures/bibliography/*.json`` and fails on any
request that has no recording, so a test can never reach the network by accident. Re-record with::

    uv run python tests/bibliography_fixtures.py record

which runs the real :class:`byax.citation_lookup.Lookup` against the live services for the DOIs and
links listed in ``SCENARIOS`` and writes one JSON file per request. The only edit made to a
recorded answer is dropping Crossref's ``reference`` list (hundreds of entries the checks never
read); that is noted in the file. Nothing here runs in the normal test suite.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx

FIXTURES = Path(__file__).parent / 'fixtures' / 'bibliography'
# DOIs and addresses recorded: a normal journal article, a retracted article, a DataCite (Zenodo)
# record, a DOI that is registered nowhere, and links that are alive, moved and gone.
SCENARIOS = {
    'article': '10.1038/nature14539',
    'retracted': '10.1016/S0140-6736(97)11096-0',
    'datacite': '10.5281/zenodo.3233118',
    'nonexistent': '10.1234/this-doi-does-not-exist-byax',
}
LINKS = {
    'alive': 'https://www.crossref.org/',
    'moved': 'https://doi.org/10.1038/nature14539',
    'gone': 'https://www.crossref.org/this-page-does-not-exist-byax',
}


# Fields of registry answers that are large and unused by the checks; dropped when recording.
BULKY = {
    'api.openalex.org': {
        'abstract_inverted_index',
        'counts_by_year',
        'referenced_works',
        'related_works',
        'topics',
        'concepts',
        'keywords',
        'sustainable_development_goals',
        'mesh',
        'locations',
        'institutions_distinct_count',
        'cited_by_percentile_year',
        'biblio',
        'fwci',
        'grants',
        'ids',
    },
    'api.datacite.org': {
        'xml',
        'viewsOverTime',
        'downloadsOverTime',
        'citationsOverTime',
        'relatedIdentifiers',
        'relatedItems',
    },
}


def request_key(method, url):
    """Method and URL with the contact address removed and the query sorted."""
    parsed = urlparse(str(url))
    query = sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != 'mailto')
    return f'{method.upper()} {parsed.scheme}://{parsed.netloc}{parsed.path}' + (
        f'?{urlencode(query)}' if query else ''
    )


def _filename(key):
    slug = re.sub(r'[^a-z0-9]+', '-', key.lower().split(' ', 1)[1].split('://', 1)[1])[:70].strip('-')
    return f'{key.split(" ", 1)[0].lower()}-{hashlib.sha256(key.encode()).hexdigest()[:8]}-{slug}.json'


class ReplayTransport(httpx.BaseTransport):
    """Answer from recordings; ``requests`` lists every request seen, ``extra`` adds ad hoc answers."""

    def __init__(self, directory=FIXTURES, extra=None):
        self.answers = {}
        for path in sorted(Path(directory).glob('*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and 'request' in data:
                self.answers[request_key(data['request']['method'], data['request']['url'])] = data[
                    'response'
                ]
        self.answers.update(extra or {})
        self.requests = []

    def handle_request(self, request):
        key = request_key(request.method, request.url)
        self.requests.append(key)
        if key not in self.answers:
            raise AssertionError(f'No recorded answer for {key}')
        answer = self.answers[key]
        headers = dict(answer.get('headers', {}))
        if 'json' in answer:
            return httpx.Response(answer['status'], json=answer['json'], headers=headers, request=request)
        return httpx.Response(answer['status'], text=answer.get('text', ''), headers=headers, request=request)


class RecordingTransport(httpx.BaseTransport):
    """Pass requests through to the network and save every answer as a fixture file."""

    def __init__(self, directory=FIXTURES):
        self.directory = Path(directory)
        self.inner = httpx.HTTPTransport()

    def handle_request(self, request):
        response = self.inner.handle_request(request)
        body = b''.join(response.iter_bytes())
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() in ('content-type', 'location', 'retry-after')
        }
        record = {'status': response.status_code, 'headers': headers}
        note = None
        try:
            payload = json.loads(body)
        except ValueError:
            record['text'] = body.decode('utf-8', errors='replace')[:2000]
        else:
            message = payload.get('message') if isinstance(payload, dict) else None
            if isinstance(message, dict) and 'reference' in message:
                message.pop('reference')
                note = 'The Crossref "reference" list was dropped to keep the fixture small.'
            dropped = BULKY.get(request.url.host)
            target = (
                payload.get('data', {}).get('attributes')
                if request.url.host == 'api.datacite.org'
                else payload
            )
            if dropped and isinstance(target, dict) and dropped & set(target):
                for key in dropped & set(target):
                    target.pop(key)
                note = f'Bulky fields the checks never read were dropped to keep the fixture small: {sorted(dropped)}.'
            record['json'] = payload
        self.directory.mkdir(parents=True, exist_ok=True)
        key = request_key(request.method, request.url)
        out = {'request': {'method': request.method, 'url': key.split(' ', 1)[1]}, 'response': record}
        if note:
            out['note'] = note
        (self.directory / _filename(key)).write_text(
            json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8'
        )
        passed = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ('content-encoding', 'content-length')
        }
        return httpx.Response(response.status_code, headers=passed, content=body, request=request)


def record():
    import tempfile

    from byax.citation_lookup import SERVICE_ORDER, Lookup

    lookup = Lookup(
        transport=RecordingTransport(), authorized=True, cache=tempfile.mkdtemp(prefix='byax-record-')
    )
    for name, doi in SCENARIOS.items():
        result = lookup.registry_record(doi.lower())
        print(name, doi, result['status'], result['tried'])
    # OpenAlex is only reached for DOIs the two other registries do not know; record one it does.
    from byax.citation_lookup import SERVICES

    lookup.get_json('openalex', f'{SERVICES["openalex"][1]}/works/https://doi.org/{SCENARIOS["article"]}')
    for name, url in LINKS.items():
        print(name, url, lookup.check_url(url)['status'])
    for url in (LINKS['gone'], 'https://example.com/'):
        print('wayback', url, lookup.archived_copy(url))
    assert set(SERVICE_ORDER)


if __name__ == '__main__':
    if sys.argv[1:] == ['record']:
        sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
        record()
    else:
        print(__doc__)
