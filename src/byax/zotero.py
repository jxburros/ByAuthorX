"""Zotero (context engine plan 9, Z1): read the local API, import items and annotations, export ``.bib``.

Zotero 7 serves a read-only copy of its web API on the computer where it runs, at
``http://localhost:23119/api/`` (the "Allow other applications on this computer to communicate with
Zotero" setting, off by default). Only ``GET`` requests are made, only to a loopback address, and
nothing is ever written to Zotero.

Endpoints used (Zotero Web API v3, ``users/0`` is the local library):

- ``/api/users/0/items/top`` (or ``/api/users/0/collections/<key>/items/top``): regular items,
  ``format=json&include=data,csljson``, paged with ``limit`` and ``start`` (``Total-Results`` header);
- ``/api/users/0/items?itemType=attachment`` and ``?itemType=annotation``: the PDF attachments and the
  highlights, notes and images made in Zotero's reader, which belong to an attachment (``parentItem``)
  that belongs to a regular item.

Each regular item becomes a source record (idempotent by the Zotero item key: importing again
updates the record). Each annotation becomes a **linked note** on that source: its text and comment,
its page as a locator (``{'page': '12'}``, the page *label* when Zotero has one), its colour and the
attachment it was made in. A note can then be cited with its page and quotation. The Better BibTeX key
in ``citationKey`` or in ``extra`` (``Citation Key: smith2020``) is kept so :func:`export_bib` writes the same
keys Zotero users already cite with; sources without one get a generated key.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date
from urllib.parse import urlparse

import httpx

from .errors import ByaxError

BASE_URL = 'http://localhost:23119'
PAGE = 100
MAX_ITEMS = 20000
SKIP_TYPES = {'attachment', 'note', 'annotation'}
_TYPES = {
    'journalArticle': 'article-journal',
    'book': 'book',
    'bookSection': 'chapter',
    'conferencePaper': 'paper-conference',
    'thesis': 'thesis',
    'report': 'report',
    'webpage': 'webpage',
    'blogPost': 'post-weblog',
    'magazineArticle': 'article-magazine',
    'newspaperArticle': 'article-newspaper',
    'preprint': 'article',
    'dataset': 'dataset',
    'computerProgram': 'software',
    'film': 'motion_picture',
    'videoRecording': 'motion_picture',
    'audioRecording': 'song',
    'map': 'map',
    'patent': 'patent',
    'encyclopediaArticle': 'entry-encyclopedia',
    'dictionaryEntry': 'entry-dictionary',
    'statute': 'legislation',
    'case': 'legal_case',
    'bill': 'bill',
    'interview': 'interview',
    'letter': 'personal_communication',
    'manuscript': 'manuscript',
    'presentation': 'speech',
    'artwork': 'graphic',
    'standard': 'standard',
    'document': 'document',
}
_CONTAINERS = (
    'publicationTitle',
    'bookTitle',
    'proceedingsTitle',
    'websiteTitle',
    'blogTitle',
    'encyclopediaTitle',
    'dictionaryTitle',
    'forumTitle',
)
_FIELDS = {
    'volume': 'volume',
    'issue': 'issue',
    'pages': 'page',
    'publisher': 'publisher',
    'place': 'publisher-place',
    'DOI': 'DOI',
    'ISBN': 'ISBN',
    'ISSN': 'ISSN',
    'url': 'URL',
    'abstractNote': 'abstract',
    'edition': 'edition',
    'language': 'language',
    'series': 'collection-title',
    'archive': 'archive',
    'archiveLocation': 'archive_location',
    'shortTitle': 'title-short',
    'numPages': 'number-of-pages',
    'genre': 'genre',
    'university': 'publisher',
    'institution': 'publisher',
}
_CITATION_KEY = re.compile(r'^\s*(?:citation\s*key|bibtex)\s*:\s*(\S+)\s*$', re.IGNORECASE | re.MULTILINE)
_ROLES = {
    'author': 'author',
    'editor': 'editor',
    'translator': 'translator',
    'seriesEditor': 'collection-editor',
    'contributor': 'contributor',
    'bookAuthor': 'container-author',
    'director': 'director',
    'interviewer': 'interviewer',
    'recipient': 'recipient',
}


def _error(message, **details):
    raise ByaxError('provider_failure', message, **details)


def is_loopback(url):
    host = (urlparse(url).hostname or '').lower()
    return host in ('localhost', '127.0.0.1', '::1') or host.endswith('.localhost')


class ZoteroClient:
    """A read-only client for Zotero's local API. There is no method that is not a ``GET``."""

    def __init__(self, base=BASE_URL, *, transport=None, timeout=15.0):
        if not is_loopback(base) or urlparse(base).scheme != 'http':
            raise ByaxError(
                'invalid_input',
                'ByAuthorX reads Zotero only on this computer (an http://localhost address)',
                field='base',
            )
        self.base = base.rstrip('/')
        self.transport = transport
        self.timeout = timeout
        self.requests_made = 0

    def get(self, path, params=None):
        """One ``GET`` to the local API: ``(json, headers)``. Explains the common failures."""
        from . import __version__

        self.requests_made += 1
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,  # a loopback request never goes through a proxy
                headers={'User-Agent': f'ByAuthorX/{__version__}', 'Zotero-API-Version': '3'},
            ) as client:
                response = client.get(self.base + path, params=params)
        except httpx.ConnectError as exc:
            raise ByaxError(
                'provider_failure',
                'Zotero is not running, or its local API is off. Start Zotero 7 and turn on '
                'Settings > Advanced > "Allow other applications on this computer to communicate with Zotero".',
                retry_guidance='Start Zotero, enable the setting, then try again',
            ) from exc
        except httpx.HTTPError as exc:
            _error(f'Zotero could not be reached ({type(exc).__name__})')
        if response.status_code == 403:
            raise ByaxError(
                'provider_failure',
                'Zotero refused the request. Turn on Settings > Advanced > "Allow other applications on this '
                'computer to communicate with Zotero" and try again.',
            )
        if response.status_code == 404:
            raise ByaxError('missing_target', 'Zotero does not have that item or collection', path=path)
        if response.status_code != 200:
            _error(f'Zotero answered {response.status_code}', path=path)
        try:
            return response.json(), response.headers
        except ValueError:
            _error('Zotero returned an unreadable answer', path=path)

    def pages(self, path, params=None, limit=MAX_ITEMS):
        """Every object of a listing, following ``start``/``limit`` until ``Total-Results`` is reached."""
        out, start = [], 0
        while len(out) < limit:
            data, headers = self.get(
                path, {**(params or {}), 'limit': PAGE, 'start': start, 'format': 'json'}
            )
            if not isinstance(data, list):
                _error('Zotero returned something other than a list', path=path)
            out.extend(data)
            start += len(data)
            total = headers.get('Total-Results')
            if not data or (start >= int(total) if total and total.isdigit() else len(data) < PAGE):
                break
        return out[:limit]

    def collections(self):
        return [
            {
                'key': c['key'],
                'name': c['data'].get('name'),
                'parent': c['data'].get('parentCollection') or None,
            }
            for c in self.pages('/api/users/0/collections')
            if isinstance(c, dict) and isinstance(c.get('data'), dict)
        ]


# --- mapping Zotero items to sources ---------------------------------------------------------------------


def citation_key(data):
    """The Better BibTeX (or native) citation key of an item's ``data``, or None."""
    native = data.get('citationKey')
    if isinstance(native, str) and native.strip():
        return native.strip()
    found = _CITATION_KEY.search(data.get('extra') or '')
    return found.group(1) if found else None


def _date_parts(text):
    if not isinstance(text, str) or not text.strip():
        return None
    match = re.search(r'(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?', text)
    if not match:
        return None
    parts = [int(g) for g in match.groups() if g]
    if len(parts) > 1 and not 1 <= parts[1] <= 12:
        parts = parts[:1]
    if len(parts) > 2 and not 1 <= parts[2] <= 31:
        parts = parts[:2]
    return {'date-parts': [parts]}


def _person(creator):
    if creator.get('name'):
        return {'literal': creator['name']}
    name = {}
    if creator.get('lastName'):
        name['family'] = creator['lastName']
    if creator.get('firstName'):
        name['given'] = creator['firstName']
    return name


def csl_from_data(data, key):
    """CSL-JSON for an item's ``data`` (used when Zotero did not supply its own ``csljson``)."""
    csl = {'id': key, 'type': _TYPES.get(data.get('itemType'), 'document'), 'title': data.get('title') or ''}
    for field in _CONTAINERS:
        if data.get(field):
            csl['container-title'] = data[field]
            break
    for field, target in _FIELDS.items():
        if data.get(field) and target not in csl:
            csl[target] = data[field]
    for creator in data.get('creators') or []:
        role = _ROLES.get(creator.get('creatorType'), 'contributor')
        person = _person(creator)
        if person:
            csl.setdefault(role, []).append(person)
    for source, target in (('date', 'issued'), ('accessDate', 'accessed'), ('originalDate', 'original-date')):
        parts = _date_parts(data.get(source))
        if parts:
            csl[target] = parts
    return csl


def _csl(item):
    data = item.get('data') or {}
    supplied = item.get('csljson')
    if isinstance(supplied, dict) and supplied.get('type'):
        csl = copy.deepcopy(supplied)
        csl['id'] = item.get('key') or data.get('key') or csl.get('id')
        # Zotero's own mapping wins, but its gaps (a field it omits) are filled from the item data.
        for field, value in csl_from_data(data, csl['id']).items():
            csl.setdefault(field, value)
        return csl
    return csl_from_data(data, item.get('key') or data.get('key'))


def _page_of(data):
    label = data.get('annotationPageLabel')
    if isinstance(label, str) and label.strip():
        return label.strip(), None
    try:
        index = json.loads(data.get('annotationPosition') or '{}').get('pageIndex')
    except (ValueError, AttributeError):
        index = None
    return (str(index + 1) if type(index) is int and index >= 0 else None), index


def annotation_note(item, attachment=None):
    """A linked note for one Zotero annotation (``highlight``, ``underline``, ``note``, ``image``, ``ink``, ``text``)."""
    data = item.get('data') or {}
    page, index = _page_of(data)
    note = {
        'id': 'zotero:' + str(item.get('key') or data.get('key')),
        'origin': 'zotero',
        'kind': data.get('annotationType') or 'note',
        'text': data.get('annotationText') or '',
        'comment': data.get('annotationComment') or '',
        'locator': {'page': page} if page else {},
    }
    optional = {
        'color': data.get('annotationColor'),
        'created': data.get('dateAdded'),
        'author': data.get('annotationAuthorName'),
        'tags': [t.get('tag') for t in data.get('tags') or [] if isinstance(t, dict) and t.get('tag')],
        'position': {'page_index': index} if index is not None else None,
        'attachment': attachment,
    }
    note.update({k: v for k, v in optional.items() if v})
    return note


def _attachment_info(item):
    data = item.get('data') or {}
    return {
        'key': item.get('key') or data.get('key'),
        'title': data.get('title') or data.get('filename') or '',
        'content_type': data.get('contentType') or None,
    }


def collect(client, *, collection=None, annotations=True, limit=MAX_ITEMS):
    """Read regular items (and their annotations) from Zotero. Returns ``{'items', 'annotations', 'counts'}``.

    ``annotations`` maps a regular item key to its annotation notes, in Zotero's reading order.
    """
    if collection is not None and not re.fullmatch(r'[A-Za-z0-9]{1,16}', collection):
        raise ByaxError(
            'invalid_input',
            'A Zotero collection is named by its key (letters and digits)',
            field='collection',
        )
    base = f'/api/users/0/collections/{collection}' if collection else '/api/users/0'
    raw = client.pages(base + '/items/top', {'include': 'data,csljson'}, limit)
    items = [
        i for i in raw if isinstance(i, dict) and (i.get('data') or {}).get('itemType') not in SKIP_TYPES
    ]
    notes = {}
    found_annotations = 0
    if annotations and items:
        wanted = {i['key'] for i in items}
        attachments = {}
        for entry in client.pages('/api/users/0/items', {'itemType': 'attachment', 'include': 'data'}, limit):
            data = entry.get('data') or {}
            if data.get('parentItem') in wanted:
                attachments[entry['key']] = (data['parentItem'], _attachment_info(entry))
        for entry in client.pages('/api/users/0/items', {'itemType': 'annotation', 'include': 'data'}, limit):
            data = entry.get('data') or {}
            if data.get('parentItem') in attachments:
                parent, info = attachments[data['parentItem']]
                notes.setdefault(parent, []).append(
                    (data.get('annotationSortIndex') or '', annotation_note(entry, info))
                )
                found_annotations += 1
        notes = {k: [n for _, n in sorted(v, key=lambda p: (p[0], p[1]['id']))] for k, v in notes.items()}
    return {
        'items': items,
        'annotations': notes,
        'counts': {'items': len(items), 'annotations': found_annotations, 'skipped': len(raw) - len(items)},
    }


def source_records(collected, existing=None):
    """``add_reference``-ready sources for collected items; the Zotero item key makes re-import idempotent."""
    existing = existing or {}
    by_key = {
        str(s.get('import', {}).get('key')): ident
        for ident, s in existing.items()
        if isinstance(s, dict) and s.get('import', {}).get('origin') == 'zotero'
    }
    result = {}
    for item in collected['items']:
        data = item.get('data') or {}
        key = item.get('key') or data.get('key')
        if not isinstance(key, str) or not key:
            continue
        ident = by_key.get(key) or 'source_' + hashlib.sha256(f'zotero:{key}'.encode()).hexdigest()[:24]
        record = copy.deepcopy(existing.get(ident, {}))
        library = item.get('library') or {}
        record.update(
            id=ident,
            csl=_csl(item),
            retrieved=date.today().isoformat(),
            verification=record.get('verification', 'unconfirmed') if ident in existing else 'unconfirmed',
            zotero={
                'key': key,
                'version': item.get('version'),
                'library': library.get('type') or 'user',
                'citation_key': citation_key(data),
                'select_uri': f'zotero://select/library/items/{key}',
            },
            **{'import': {'origin': 'zotero', 'key': key}},
        )
        record['zotero'] = {k: v for k, v in record['zotero'].items() if v is not None}
        # Notes made in Zotero are replaced on re-import; notes the writer added here are kept.
        kept = [n for n in record.get('notes', []) if n.get('origin') != 'zotero']
        imported = collected['annotations'].get(key, [])
        if kept or imported:
            record['notes'] = kept + imported
        else:
            record.pop('notes', None)
        result[ident] = record
    return list(result.values())


def import_from_zotero(client, existing=None, *, collection=None, annotations=True, limit=MAX_ITEMS):
    """Read Zotero and return ``{'sources', 'counts', 'new', 'updated'}``; nothing is written anywhere."""
    from .citations import validate_source

    collected = collect(client, collection=collection, annotations=annotations, limit=limit)
    sources = [validate_source(s, s['id']) for s in source_records(collected, existing)]
    known = set(existing or {})
    return {
        'sources': sources,
        'counts': {
            **collected['counts'],
            'new': sum(1 for s in sources if s['id'] not in known),
            'updated': sum(1 for s in sources if s['id'] in known),
        },
    }


# --- export ----------------------------------------------------------------------------------------------


def export_bib(references, *, only_zotero=False):
    """``{'text', 'keys', 'count'}``: BibTeX for the references with stable citation keys.

    A Better BibTeX key kept from Zotero is used as it is; other sources get ``familyYYYY`` (with a, b,
    c on a clash). ``only_zotero`` limits the file to the sources imported from Zotero.
    """
    from .citations import citation_keys, export_sources

    chosen = {
        ident: source
        for ident, source in references.items()
        if not only_zotero or source.get('import', {}).get('origin') == 'zotero'
    }
    keys = citation_keys(chosen)
    return {
        'text': export_sources(chosen, 'bibtex', keys=keys),
        'keys': keys,
        'count': len(chosen),
        'from_zotero': sum(1 for s in chosen.values() if (s.get('zotero') or {}).get('citation_key')),
    }


def citation_for_note(source, note_id):
    """The ``quote`` and ``locator`` to cite a linked note with (``cite`` operation fields)."""
    for note in source.get('notes') or []:
        if note.get('id') == note_id:
            if not note.get('text'):
                raise ByaxError('invalid_input', 'This note has no highlighted text to cite', target=note_id)
            return {'quote': note['text'], 'locator': dict(note.get('locator') or {}), 'kind': 'quotation'}
    raise ByaxError('missing_target', 'No such note on this source', target=note_id)
