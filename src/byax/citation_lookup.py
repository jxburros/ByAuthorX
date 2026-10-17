"""Outside citation lookups: Crossref, DataCite, OpenAlex, the Wayback Machine and link checks.

Context engine plan 9 (B1). Everything here is opt-in and explicit:

- No lookup runs unless the person asked for it (a command, or a button in the Bibliography
  health panel) and every service involved has the ``citation_lookup`` permission in
  :class:`byax.retrieval.ConsentRegistry` (kept per service as ``bibliography:<service>``), or the
  person authorizes this one run. Nothing is requested at startup or in the background.
- Requests identify the application (``User-Agent`` with the project URL). A contact address is sent
  to Crossref and OpenAlex (their "polite pool") only when the person configured one.
- Each host has a minimum interval between requests, a 429 answer stops further requests to that host
  for the run, timeouts are short and bodies are size-limited.
- Answers are cached in the user cache, never in a document, so a repeated check works offline.
  Offline mode answers from the cache only and never opens a connection.
- A registry that does not know a DOI is reported as "unverified, please check", never as fake.

All HTTP goes through :mod:`httpx`; tests pass a ``transport`` (a recorded-fixture replay).
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import ipaddress
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx

from .errors import ByaxError

REPOSITORY_URL = 'https://github.com/jxburros/ByAuthorX'
CONSENT_PREFIX = 'bibliography:'
# Service -> (label, base URL, minimum seconds between requests, what is sent)
SERVICES = {
    'crossref': ('Crossref', 'https://api.crossref.org', 0.2, 'the DOI of each reference'),
    'datacite': (
        'DataCite',
        'https://api.datacite.org',
        0.2,
        'the DOI of each reference Crossref does not know',
    ),
    'openalex': (
        'OpenAlex',
        'https://api.openalex.org',
        0.2,
        'the DOI of each reference the other two do not know',
    ),
    'wayback': (
        'Internet Archive (Wayback Machine)',
        'https://archive.org',
        1.0,
        'the address of each dead link',
    ),
    'links': ('The websites your references link to', None, 0.5, 'a request to each linked address'),
}
SERVICE_ORDER = tuple(SERVICES)
MAX_BODY = 4 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = 15.0
FOUND_TTL = 30 * 86400
MISSING_TTL = 3 * 86400
LINK_TTL = 7 * 86400
DOI_SYNTAX = re.compile(r'^10\.\d{4,9}/\S+$')
_DOI_PREFIX = re.compile(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', re.IGNORECASE)
_TRAILING = '.,;:)]}>"\'”’'
_TAGS = re.compile(r'<[^>]+>')
_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
RETRACTION_TYPES = {'retraction', 'withdrawal', 'removal'}
CONCERN_TYPES = {'expression_of_concern', 'expression-of-concern'}


def _invalid(message, **details):
    raise ByaxError('invalid_input', message, **details)


# --- locations ---------------------------------------------------------------------------------


def _root(kind):
    """``config``, ``cache`` or ``data`` folder; ``BYAX_BIBLIOGRAPHY_DIR`` puts all three in one place."""
    override = os.environ.get('BYAX_BIBLIOGRAPHY_DIR')
    if override:
        return Path(override) / kind
    from platformdirs import user_cache_path, user_config_path, user_data_path

    base = {'config': user_config_path, 'cache': user_cache_path, 'data': user_data_path}[kind]
    return base('ByAuthorX', appauthor=False) / 'bibliography'


def cache_dir():
    return _root('cache')


def data_dir():
    return _root('data')


def settings_path():
    return _root('config') / 'settings.json'


def load_settings():
    try:
        value = json.loads(settings_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def save_settings(values):
    """Set the contact address sent to Crossref and OpenAlex (``mailto``); ``None`` or '' removes it."""
    from .providers import _atomic_json

    unknown = set(values) - {'mailto'}
    if unknown:
        _invalid('Unknown bibliography setting', field=min(unknown), supported=['mailto'])
    current = load_settings()
    mailto = values.get('mailto')
    if mailto in (None, ''):
        current.pop('mailto', None)
    elif not isinstance(mailto, str) or not _EMAIL.match(mailto.strip()) or len(mailto) > 200:
        _invalid('mailto must be an email address', field='mailto')
    else:
        current['mailto'] = mailto.strip()
    _atomic_json(settings_path(), current)
    return current


def user_agent():
    """Identify the application and its project page to every service we ask."""
    from . import __version__

    return f'ByAuthorX/{__version__} (+{REPOSITORY_URL})'


# --- DOI syntax --------------------------------------------------------------------------------


def normalize_doi(value):
    """The bare DOI (lower case, no ``doi:`` or resolver prefix), or ``None`` when ``value`` is empty."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _DOI_PREFIX.sub('', value.strip()).strip().lower()


def doi_problem(value):
    """Why ``value`` is not a well-formed DOI, with a suggested repair when there is one; else ``None``."""
    doi = normalize_doi(value)
    if doi is None:
        return None
    if DOI_SYNTAX.match(doi):
        trimmed = doi.rstrip(_TRAILING)
        # A trailing bracket or full stop usually came from the sentence the DOI was copied from.
        if trimmed != doi and not (doi.endswith(')') and doi.count('(') >= doi.count(')')):
            return {
                'reason': 'The DOI ends with punctuation that is probably not part of it.',
                'suggestion': trimmed,
            }
        return None
    compact = re.sub(r'\s+', '', doi)
    if compact != doi and DOI_SYNTAX.match(compact):
        return {'reason': 'The DOI contains a space.', 'suggestion': compact}
    if not doi.startswith('10.'):
        return {'reason': 'A DOI starts with 10. followed by the registrant number.', 'suggestion': None}
    return {
        'reason': 'A DOI looks like 10.1234/suffix (a four to nine digit registrant, a slash, a suffix).',
        'suggestion': None,
    }


# --- registry records ---------------------------------------------------------------------------


def _text(value):
    return html.unescape(_TAGS.sub('', value)).strip() if isinstance(value, str) else ''


def _crossref_year(message):
    for key in ('issued', 'published', 'published-print', 'published-online', 'created'):
        parts = (message.get(key) or {}).get('date-parts') or []
        if parts and parts[0] and isinstance(parts[0][0], int):
            return parts[0][0]
    return None


def crossref_record(message):
    """Common shape for a Crossref ``message``: title, authors, year, type and retraction notices."""
    notices = []
    for update in message.get('updated-by') or []:
        if isinstance(update, dict):
            kind = str(update.get('type') or '').lower()
            date_parts = ((update.get('updated') or {}).get('date-parts') or [[None]])[0]
            notices.append(
                {
                    'type': kind,
                    'label': update.get('label') or kind.replace('_', ' ').capitalize(),
                    'doi': update.get('DOI'),
                    'date': '-'.join(f'{n:02d}' if i else str(n) for i, n in enumerate(date_parts) if n)
                    or None,
                    'source': update.get('source') or 'crossref',
                }
            )
    for item in (message.get('relation') or {}).get('is-retracted-by') or []:
        if isinstance(item, dict) and not any(n['doi'] == item.get('id') for n in notices):
            notices.append(
                {
                    'type': 'retraction',
                    'label': 'Retraction',
                    'doi': item.get('id'),
                    'date': None,
                    'source': 'crossref',
                }
            )
    title = _text((message.get('title') or [''])[0])
    updates_to = [
        str(u.get('type') or '').lower() for u in message.get('update-to') or [] if isinstance(u, dict)
    ]
    return {
        'registry': 'crossref',
        'doi': normalize_doi(message.get('DOI')),
        'title': title,
        'authors': [
            {'family': a.get('family') or a.get('name') or '', 'given': a.get('given') or ''}
            for a in message.get('author') or []
            if isinstance(a, dict)
        ],
        'year': _crossref_year(message),
        'type': message.get('type'),
        'container': _text((message.get('container-title') or [''])[0]),
        'publisher': message.get('publisher'),
        'url': message.get('URL'),
        'notices': notices,
        'is_notice': sorted({t for t in updates_to if t in RETRACTION_TYPES | CONCERN_TYPES}),
        'title_marked_retracted': bool(re.match(r'\s*(?:retracted|withdrawn)\b[:\s]', title, re.IGNORECASE)),
    }


def datacite_record(data):
    attributes = (data or {}).get('attributes') or {}
    titles = attributes.get('titles') or [{}]
    creators = []
    for creator in attributes.get('creators') or []:
        if not isinstance(creator, dict):
            continue
        family = creator.get('familyName') or ''
        given = creator.get('givenName') or ''
        if not family:
            name = creator.get('name') or ''
            family, _, given = name.partition(',') if ',' in name else (name, '', '')
        creators.append({'family': family.strip(), 'given': given.strip()})
    publisher = attributes.get('publisher')
    if isinstance(publisher, dict):
        publisher = publisher.get('name')
    year = attributes.get('publicationYear')
    return {
        'registry': 'datacite',
        'doi': normalize_doi(attributes.get('doi')),
        'title': _text(titles[0].get('title') if isinstance(titles[0], dict) else ''),
        'authors': creators,
        'year': year if isinstance(year, int) else None,
        'type': (attributes.get('types') or {}).get('resourceTypeGeneral'),
        'container': _text((attributes.get('container') or {}).get('title')),
        'publisher': publisher,
        'url': attributes.get('url'),
        'notices': [],
        'is_notice': [],
        'title_marked_retracted': False,
    }


def openalex_record(work):
    authors = []
    for authorship in work.get('authorships') or []:
        name = ((authorship or {}).get('author') or {}).get('display_name') or ''
        family = name.rsplit(' ', 1)[-1] if name else ''
        authors.append({'family': family, 'given': name[: -len(family)].strip(), 'guessed': True})
    source = ((work.get('primary_location') or {}).get('source') or {}).get('display_name')
    notices = (
        [{'type': 'retraction', 'label': 'Retraction', 'doi': None, 'date': None, 'source': 'openalex'}]
        if work.get('is_retracted')
        else []
    )
    return {
        'registry': 'openalex',
        'doi': normalize_doi(work.get('doi')),
        'title': _text(work.get('title') or work.get('display_name')),
        'authors': authors,
        'year': work.get('publication_year') if isinstance(work.get('publication_year'), int) else None,
        'type': work.get('type'),
        'container': _text(source),
        'publisher': None,
        'url': work.get('doi') or work.get('id'),
        'notices': notices,
        'is_notice': [],
        'title_marked_retracted': False,
    }


# --- the HTTP client ----------------------------------------------------------------------------


def private_address(host):
    """True for names and literal addresses that point at this computer or a private network."""
    if not host:
        return True
    host = host.strip('[]').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home.arpa')):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return '.' not in host  # a bare single-label name resolves only on a local network
    return not address.is_global


class Lookup:
    """Consented, rate-limited, cached HTTP lookups. ``offline=True`` answers from the cache only."""

    def __init__(
        self,
        *,
        transport=None,
        offline=False,
        authorized=False,
        consent=None,
        config_dir=None,
        cache=None,
        mailto=None,
        timeout=TIMEOUT,
        sleep=time.sleep,
        clock=time.monotonic,
        now=time.time,
    ):
        self.transport = transport
        self.offline = offline
        self.authorized = authorized
        self.config_dir = config_dir
        self._consent = consent
        self.cache = Path(cache) if cache else cache_dir()
        self.mailto = mailto if mailto is not None else load_settings().get('mailto')
        self.timeout = timeout
        self.sleep, self.clock, self.now = sleep, clock, now
        self._last: dict = {}
        self._blocked: set = set()
        self.requests_made = 0
        self.cache_hits = 0

    # consent -------------------------------------------------------------------------------

    @property
    def consent(self):
        if self._consent is None:
            from .retrieval import ConsentRegistry

            self._consent = ConsentRegistry(self.config_dir)
        return self._consent

    def allowed(self, service):
        return self.authorized or self.consent.allows(CONSENT_PREFIX + service, 'citation_lookup')

    def require(self, services):
        """Raise ``authorization_required`` naming each service that lacks permission."""
        if self.offline:
            return
        missing = [s for s in services if not self.allowed(s)]
        if missing:
            raise ByaxError(
                'authorization_required',
                'Outside lookups need your permission for: '
                + ', '.join(SERVICES[s][0] for s in missing)
                + '. Nothing was sent.',
                services=missing,
                sends={s: SERVICES[s][3] for s in missing},
                how='byax bibliography --action consent, or --allow-network for one run',
            )

    def grant(self, services, allowed=True):
        for service in services:
            if service not in SERVICES:
                _invalid('Unknown lookup service', field='services', supported=list(SERVICES))
            self.consent.set(CONSENT_PREFIX + service, 'citation_lookup', bool(allowed))

    # cache ---------------------------------------------------------------------------------

    def _path(self, key):
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache / 'lookups' / digest[:2] / (digest + '.json')

    def _cached(self, key, ttl):
        try:
            entry = json.loads(self._path(key).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or 'status' not in entry:
            return None
        age = self.now() - entry.get('fetched', 0)
        if not self.offline and age > ttl:
            return None
        return dict(entry, age_days=round(age / 86400, 1))

    def _store(self, key, entry):
        from .providers import _atomic_json

        try:
            _atomic_json(self._path(key), dict(entry, fetched=self.now(), key=key))
        except OSError:
            pass  # a cache that cannot be written only costs a repeat request

    def clear_cache(self):
        removed = 0
        folder = self.cache / 'lookups'
        if folder.is_dir():
            for path in folder.rglob('*.json'):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    # requests ------------------------------------------------------------------------------

    @property
    def user_agent(self):
        return user_agent()

    def _throttle(self, host, interval):
        last = self._last.get(host)
        if last is not None:
            wait = interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def _send(self, method, url, params, interval, accept='*/*'):
        """One bounded request; returns ``(status_code, headers, body bytes)`` or raises ``httpx.HTTPError``."""
        host = urlparse(url).hostname
        self._throttle(host, interval)
        self.requests_made += 1
        with (
            httpx.Client(
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=self.transport is None,
                headers={'User-Agent': self.user_agent, 'Accept': accept},
            ) as client,
            client.stream(method, url, params=params) as response,
        ):
            body = bytearray()
            if method != 'HEAD':
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise ByaxError('limit_exceeded', 'An outside answer was larger than 4 MiB')
            return response.status_code, response.headers, bytes(body)

    def get_json(self, service, url, params=None):
        """A cached JSON lookup. ``status`` is ok, not_found, not_cached, rate_limited or error."""
        label, _base, interval, _ = SERVICES[service]
        params = dict(params or {})
        key = f'{service} {url}?' + '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
        cached = self._cached(key, FOUND_TTL)
        if cached and (
            cached['status'] == 'ok' or self.offline or self.now() - cached['fetched'] <= MISSING_TTL
        ):
            self.cache_hits += 1
            return dict(cached, cached=True)
        if self.offline:
            return {'status': 'not_cached', 'cached': False}
        self.require([service])
        if service in self._blocked:
            return {'status': 'rate_limited', 'cached': False, 'message': f'{label} asked us to slow down.'}
        sent = dict(params)
        if self.mailto and service in ('crossref', 'openalex'):
            sent['mailto'] = self.mailto  # the polite pool, only when the person configured an address
        try:
            code, headers, body = self._send('GET', url, sent, interval, 'application/json')
            if code == 429:
                retry = headers.get('retry-after', '')
                if retry.isdigit() and int(retry) <= 5:
                    self.sleep(int(retry))
                    code, headers, body = self._send('GET', url, sent, interval, 'application/json')
                if code == 429:
                    self._blocked.add(service)
                    return {'status': 'rate_limited', 'cached': False, 'http_status': 429}
        except httpx.TimeoutException:
            return {'status': 'error', 'cached': False, 'message': f'{label} did not answer in time.'}
        except httpx.HTTPError as exc:
            return {
                'status': 'error',
                'cached': False,
                'message': f'{label} could not be reached ({type(exc).__name__}).',
            }
        if code == 404:
            entry = {'status': 'not_found', 'http_status': 404}
            self._store(key, entry)
            return dict(entry, cached=False)
        if code != 200:
            return {
                'status': 'error',
                'cached': False,
                'http_status': code,
                'message': f'{label} answered {code}.',
            }
        try:
            data = json.loads(body)
        except ValueError:
            return {'status': 'error', 'cached': False, 'message': f'{label} returned an unreadable answer.'}
        entry = {'status': 'ok', 'http_status': 200, 'data': data}
        self._store(key, entry)
        return dict(entry, cached=False)

    # registries ----------------------------------------------------------------------------

    def registry_record(self, doi):
        """Look a DOI up in Crossref, then DataCite, then OpenAlex.

        Returns ``{'status': 'found'|'unverified'|'not_cached'|'error', 'record', 'tried'}``.
        ``unverified`` means no registry knew the DOI; it is not a statement that the work is fake.
        """
        tried = []
        outcomes = {}
        quoted = quote(doi, safe='/')
        steps = (
            (
                'crossref',
                f'{SERVICES["crossref"][1]}/works/{quoted}',
                crossref_record,
                lambda d: d.get('message'),
            ),
            (
                'datacite',
                f'{SERVICES["datacite"][1]}/dois/{quoted}',
                datacite_record,
                lambda d: d.get('data'),
            ),
            (
                'openalex',
                f'{SERVICES["openalex"][1]}/works/https://doi.org/{quoted}',
                openalex_record,
                lambda d: d if d.get('id') else None,
            ),
        )
        for service, url, build, pick in steps:
            if not self.offline and not self.allowed(service):
                # Crossref is the only required service; DataCite and OpenAlex are skipped without consent.
                if service == 'crossref':
                    self.require([service])
                continue
            result = self.get_json(service, url)
            tried.append(service)
            outcomes[service] = result['status']
            if result['status'] == 'ok':
                try:
                    payload = pick(result['data'])
                    record = build(payload) if isinstance(payload, dict) else None
                except (AttributeError, TypeError, KeyError, IndexError, ValueError):
                    # An answer in an unexpected shape is a failed check, never a verdict on the DOI.
                    outcomes[service] = 'error'
                    record = None
                if record is not None:
                    return {
                        'status': 'found',
                        'record': record,
                        'tried': tried,
                        'cached': result.get('cached', False),
                    }
            if service == 'crossref' and result['status'] in ('rate_limited', 'error'):
                # Without Crossref's answer nothing can be said about the DOI, so the others are not tried.
                return {'status': 'error', 'record': None, 'tried': tried, 'outcomes': outcomes}
        if 'not_cached' in outcomes.values():
            return {'status': 'not_cached', 'record': None, 'tried': tried}
        if any(v in ('error', 'rate_limited') for v in outcomes.values()):
            return {'status': 'error', 'record': None, 'tried': tried, 'outcomes': outcomes}
        return {'status': 'unverified', 'record': None, 'tried': tried, 'outcomes': outcomes}

    # links ---------------------------------------------------------------------------------

    def check_url(self, url):
        """Is a linked address alive? ``ok``, ``dead`` (404/410), ``unverified`` or ``blocked``.

        Redirects are followed by hand (at most five) and every hop is checked, so an address cannot
        send the check to this computer or a private network. HEAD is tried first, then GET.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return {'status': 'blocked', 'message': 'Only http and https addresses are checked.'}
        key = f'links {url}'
        cached = self._cached(key, LINK_TTL)
        if cached:
            self.cache_hits += 1
            return dict(cached, cached=True)
        if self.offline:
            return {'status': 'not_cached', 'cached': False}
        self.require(['links'])
        current, hops = url, 0
        while True:
            host = urlparse(current).hostname
            if private_address(host):
                return {
                    'status': 'blocked',
                    'message': 'Skipped: the address points at this computer or a private network.',
                }
            try:
                code, headers, _ = self._send('HEAD', current, None, SERVICES['links'][2])
                if code in (403, 405, 501):
                    code, headers, _ = self._send('GET', current, None, SERVICES['links'][2])
            except httpx.TimeoutException:
                return {
                    'status': 'unverified',
                    'message': 'The site did not answer in time.',
                    'cached': False,
                }
            except httpx.HTTPError as exc:
                return {
                    'status': 'unverified',
                    'message': f'The site could not be reached ({type(exc).__name__}).',
                    'cached': False,
                }
            if code in (301, 302, 303, 307, 308) and headers.get('location'):
                hops += 1
                if hops > MAX_REDIRECTS:
                    return {'status': 'unverified', 'message': 'Too many redirects.', 'cached': False}
                current = urljoin(current, headers['location'])
                if urlparse(current).scheme not in ('http', 'https'):
                    return {
                        'status': 'blocked',
                        'message': 'Redirected to a non-web address.',
                        'cached': False,
                    }
                continue
            break
        if 200 <= code < 300:
            entry = {'status': 'ok', 'http_status': code, 'final_url': current if current != url else None}
        elif code in (404, 410):
            entry = {
                'status': 'dead',
                'http_status': code,
                'message': f'The server answered {code} (not found).',
            }
        else:
            entry = {
                'status': 'unverified',
                'http_status': code,
                'message': f'The server answered {code}; it may not allow automatic checks. Open the link to confirm.',
            }
        self._store(key, entry)
        return dict(entry, cached=False)

    def archived_copy(self, url):
        """Closest Wayback Machine snapshot of ``url`` (``available`` False when there is none)."""
        result = self.get_json('wayback', f'{SERVICES["wayback"][1]}/wayback/available', {'url': url})
        if result['status'] != 'ok':
            return {'status': result['status'], 'available': False}
        closest = ((result['data'] or {}).get('archived_snapshots') or {}).get('closest') or {}
        snapshot = closest.get('url')
        if closest.get('available') and isinstance(snapshot, str):
            parsed = urlparse(snapshot)
            if (
                parsed.hostname == 'web.archive.org'
            ):  # never offer a link the archive's answer chose elsewhere
                return {
                    'status': 'ok',
                    'available': True,
                    'url': parsed._replace(scheme='https').geturl(),
                    'timestamp': closest.get('timestamp'),
                }
        return {'status': 'ok', 'available': False}


# --- Retraction Watch (offline CSV imported by the person) ------------------------------------------

RETRACTION_COLUMNS = {
    'required': ['OriginalPaperDOI', 'RetractionNature'],
    'optional': ['Record ID', 'Title', 'Reason', 'RetractionDate', 'RetractionDOI', 'Journal', 'Notes'],
}
_RW_NATURES = {
    'retraction': 'retraction',
    'correction': 'correction',
    'expression of concern': 'expression_of_concern',
    'reinstatement': 'reinstatement',
}


def retraction_db_path():
    return data_dir() / 'retraction-watch.sqlite3'


def _rw_column(row, *names):
    for name in names:
        for key, value in row.items():
            if key and key.strip().lower().replace(' ', '') == name.lower().replace(' ', ''):
                return (value or '').strip()
    return ''


def import_retraction_watch(source, *, path=None):
    """Replace the offline Retraction Watch index with the rows of a CSV file the person downloaded.

    ``source`` is a file path or text. The expected columns are those of the public Retraction Watch
    database export (:data:`RETRACTION_COLUMNS`); only ``OriginalPaperDOI`` and ``RetractionNature``
    are required. Rows without a DOI are counted and skipped. Returns a summary with the file's
    SHA-256 and the number of rows read.
    """
    if isinstance(source, (str, Path)) and '\n' not in str(source) and Path(source).is_file():
        raw = Path(source).read_bytes()
    elif isinstance(source, bytes):
        raw = source
    elif isinstance(source, str):
        raw = source.encode('utf-8')
    else:
        _invalid('Name the Retraction Watch CSV file to import', field='source_path')
    text = raw.decode('utf-8-sig', errors='replace')
    csv.field_size_limit(1 << 24)
    reader = csv.DictReader(io.StringIO(text))
    names = {(n or '').strip().lower().replace(' ', '') for n in reader.fieldnames or []}
    missing = [c for c in RETRACTION_COLUMNS['required'] if c.lower().replace(' ', '') not in names]
    if missing:
        _invalid(
            'This does not look like a Retraction Watch export; columns are missing',
            missing=missing,
            expected=RETRACTION_COLUMNS,
        )
    target = Path(path) if path else retraction_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp')
    temporary.unlink(missing_ok=True)
    rows = skipped = 0
    connection = sqlite3.connect(temporary)
    try:
        connection.execute(
            'create table entries (doi text, nature text, label text, date text, reason text, '
            'retraction_doi text, title text, record_id text)'
        )
        connection.execute('create table meta (key text primary key, value text)')
        for row in reader:
            doi = normalize_doi(_rw_column(row, 'OriginalPaperDOI'))
            nature_text = _rw_column(row, 'RetractionNature')
            if not doi or not DOI_SYNTAX.match(doi) or not nature_text:
                skipped += 1
                continue
            nature = _RW_NATURES.get(nature_text.lower(), nature_text.lower().replace(' ', '_'))
            connection.execute(
                'insert into entries values (?,?,?,?,?,?,?,?)',
                (
                    doi,
                    nature,
                    nature_text,
                    _rw_column(row, 'RetractionDate')[:10],
                    _rw_column(row, 'Reason').strip(';')[:500],
                    normalize_doi(_rw_column(row, 'RetractionDOI')) or None,
                    _rw_column(row, 'Title')[:300],
                    _rw_column(row, 'Record ID', 'RecordID'),
                ),
            )
            rows += 1
        connection.execute('create index entries_doi on entries (doi)')
        for key, value in (
            ('sha256', hashlib.sha256(raw).hexdigest()),
            ('rows', str(rows)),
            ('skipped', str(skipped)),
            ('imported', time.strftime('%Y-%m-%d', time.gmtime())),
        ):
            connection.execute('insert into meta values (?,?)', (key, value))
        connection.commit()
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    connection.close()
    os.replace(temporary, target)
    return {
        'imported': rows,
        'skipped': skipped,
        'sha256': hashlib.sha256(raw).hexdigest(),
        'path': str(target),
    }


class RetractionIndex:
    """Read access to the imported Retraction Watch database; empty when nothing was imported."""

    def __init__(self, path=None):
        self.path = Path(path) if path else retraction_db_path()

    def available(self):
        return self.path.is_file()

    def info(self):
        if not self.available():
            return {'available': False}
        connection = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
        try:
            meta = dict(connection.execute('select key, value from meta'))
        finally:
            connection.close()
        return {'available': True, **meta}

    def lookup(self, doi):
        if not self.available() or not doi:
            return []
        connection = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
        try:
            rows = connection.execute(
                'select nature, label, date, reason, retraction_doi, title, record_id from entries where doi = ?',
                (doi.lower(),),
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                'type': nature,
                'label': label,
                'date': date or None,
                'reason': reason or None,
                'doi': retraction_doi,
                'source': 'retraction-watch (imported file)',
                'record_id': record_id or None,
            }
            for nature, label, date, reason, retraction_doi, title, record_id in rows
        ]
