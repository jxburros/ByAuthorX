"""Additional CSL citation styles, loaded on explicit user action (context engine plan 9).

APA, MLA and Chicago ship with the application (``citeproc-py-styles``). Any other style from the
Citation Style Language repository can be added in one of two ways, both a deliberate action by the
person:

- **download** one style by its repository name (``ieee``, ``nature``, ``harvard-cite-them-right``)
  from ``https://raw.githubusercontent.com/citation-style-language/styles/master/<name>.csl``. The
  source URL, size and SHA-256 are returned for display before the style is used, and an expected
  hash can be supplied to refuse a file that differs from the one that was previewed;
- **import** a local ``.csl`` file.

Both paths check that the file is a standalone CSL 1.0 style (a dependent style names its parent
instead of defining rules) and that the citation processor can load it, then store it under the user
data directory with its hash. Every use re-checks the hash, so an edited file is refused rather than
trusted. The styles are licensed CC BY-SA 3.0 (``docs/citation-licenses.md``); each file keeps its
own author and licence information, which :func:`list_styles` reports.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import httpx

from .errors import ByaxError

REPOSITORY = 'https://github.com/citation-style-language/styles'
STYLE_URL = 'https://raw.githubusercontent.com/citation-style-language/styles/master/{name}.csl'
BUILTIN = {'apa': 'apa', 'mla': 'modern-language-association', 'chicago': 'chicago-notes-bibliography'}
BUILTIN_FORMATS = {'apa': 'author-date', 'mla': 'author-date', 'chicago': 'note'}
MAX_BYTES = 2 * 1024 * 1024
CSL_NS = '{http://purl.org/net/xbiblio/csl}'
_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,99}$')


def _invalid(message, **details):
    raise ByaxError('invalid_input', message, **details)


def styles_dir():
    from .citation_lookup import data_dir

    return data_dir() / 'csl-styles'


def valid_name(name):
    if not isinstance(name, str) or not _NAME.match(name):
        _invalid(
            'A style name is the lower-case file name in the CSL repository, such as ieee or nature',
            field='name',
        )
    if name in BUILTIN:
        _invalid(f'{name} is already built in', field='name')
    return name


def inspect_style(data):
    """Parse CSL ``data`` (bytes) and return its metadata; raise if it cannot be used as a style."""
    from defusedxml import DefusedXmlException, ElementTree

    if len(data) > MAX_BYTES:
        _invalid('A style file is at most 2 MiB')
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError, DefusedXmlException) as exc:
        raise ByaxError('invalid_input', 'The file is not valid CSL (XML) style', reason=str(exc)) from exc
    if root.tag != CSL_NS + 'style':
        _invalid('The file is not a CSL style (the root element must be style in the CSL namespace)')
    info = root.find(CSL_NS + 'info')
    if info is None:
        _invalid('The CSL style has no info section')
    parent = None
    categories = {}
    for link in info.findall(CSL_NS + 'link'):
        if link.get('rel') == 'independent-parent':
            parent = link.get('href')
    for category in info.findall(CSL_NS + 'category'):
        for key, value in category.attrib.items():
            categories[key] = value
    title = info.findtext(CSL_NS + 'title') or ''
    rights = info.find(CSL_NS + 'rights')
    citation_format = categories.get('citation-format') or (
        'note' if root.get('class') == 'note' else 'author-date'
    )
    return {
        'title': title.strip(),
        'id': (info.findtext(CSL_NS + 'id') or '').strip(),
        'citation_format': citation_format,
        'class': root.get('class'),
        'dependent': parent is not None,
        'parent': parent,
        'license': (rights.get('license') if rights is not None else None) or None,
        'rights': (rights.text or '').strip() if rights is not None else None,
        'authors': [
            (a.findtext(CSL_NS + 'name') or '').strip()
            for a in info.findall(CSL_NS + 'author')
            if a is not None
        ],
        'updated': (info.findtext(CSL_NS + 'updated') or '').strip() or None,
    }


def _check_loadable(data):
    import tempfile

    from citeproc import CitationStylesStyle

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'style.csl'
        path.write_bytes(data)
        try:
            CitationStylesStyle(str(path), validate=False)
        except Exception as exc:
            raise ByaxError(
                'unsupported_feature',
                'The citation processor cannot load this style',
                reason=f'{type(exc).__name__}: {exc}',
            ) from exc


def _validated(data):
    info = inspect_style(data)
    if info['dependent']:
        _invalid(
            'This is a dependent style. Add its parent style instead, which defines the rules',
            parent=info['parent'],
        )
    _check_loadable(data)
    return info


def fetch(name, *, transport=None, timeout=20.0):
    """Download ``name`` from the CSL repository. Returns ``{'url', 'data', 'size', 'sha256'}``."""
    from .citation_lookup import user_agent

    valid_name(name)
    url = STYLE_URL.format(name=name)
    agent = user_agent()
    try:
        with (
            httpx.Client(
                transport=transport, timeout=timeout, follow_redirects=False, headers={'User-Agent': agent}
            ) as client,
            client.stream('GET', url) as response,
        ):
            if response.status_code == 404:
                raise ByaxError('missing_target', f'No style named {name} in the CSL repository', url=url)
            if response.status_code != 200:
                raise ByaxError(
                    'provider_failure', f'The CSL repository answered {response.status_code}', url=url
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    _invalid('A style file is at most 2 MiB', url=url)
    except httpx.HTTPError as exc:
        raise ByaxError(
            'provider_failure', f'The CSL repository could not be reached ({type(exc).__name__})', url=url
        ) from exc
    data = bytes(body)
    return {'url': url, 'data': data, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def preview(name, *, transport=None):
    """Download for display only: the source, size, hash and style details; nothing is installed."""
    fetched = fetch(name, transport=transport)
    info = inspect_style(fetched['data'])
    return {
        'name': name,
        'url': fetched['url'],
        'size': fetched['size'],
        'sha256': fetched['sha256'],
        'installed': False,
        'style': info,
        'license': info['license'],
        'usable': not info['dependent'],
        'note': 'A dependent style must be added through its parent.' if info['dependent'] else None,
    }


def _index_path():
    return styles_dir() / 'index.json'


def _load_index():
    try:
        value = json.loads(_index_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _install(name, data, origin, *, replace=False):
    from .providers import _atomic_json

    info = _validated(data)
    folder = styles_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{name}.csl'
    if target.exists() and not replace:
        raise ByaxError('conflict', f'A style named {name} is already installed', target=name)
    target.write_bytes(data)
    index = _load_index()
    entry = {
        'title': info['title'],
        'citation_format': info['citation_format'],
        'license': info['license'],
        'origin': origin,
        'sha256': hashlib.sha256(data).hexdigest(),
        'size': len(data),
    }
    index[name] = entry
    _atomic_json(_index_path(), index)
    return {'name': name, 'installed': True, 'path': str(target), **entry, 'style': info}


def add_from_repository(name, *, expect_sha256=None, transport=None, replace=False):
    """Download ``name`` and install it. ``expect_sha256`` refuses a file that is not the one previewed."""
    fetched = fetch(name, transport=transport)
    if expect_sha256 and expect_sha256.lower() != fetched['sha256']:
        raise ByaxError(
            'conflict',
            'The downloaded style differs from the one you previewed; it was not installed',
            expected=expect_sha256.lower(),
            actual=fetched['sha256'],
            url=fetched['url'],
        )
    result = _install(name, fetched['data'], {'url': fetched['url']}, replace=replace)
    result.update(url=fetched['url'], verified_against_preview=bool(expect_sha256))
    return result


def add_from_file(path, *, name=None, replace=False):
    """Import a local ``.csl`` file (never touches the network)."""
    file = Path(path)
    if file.suffix.lower() != '.csl' or not file.is_file():
        _invalid('Choose a .csl style file', field='source_path')
    if file.stat().st_size > MAX_BYTES:
        _invalid('A style file is at most 2 MiB', field='source_path')
    data = file.read_bytes()
    chosen = name or re.sub(r'[^a-z0-9-]+', '-', file.stem.lower()).strip('-')[:100]
    valid_name(chosen)
    result = _install(chosen, data, {'file': file.name}, replace=replace)
    return result


def remove(name):
    from .providers import _atomic_json

    valid_name(name)
    index = _load_index()
    path = styles_dir() / f'{name}.csl'
    if name not in index and not path.exists():
        raise ByaxError('missing_target', f'No installed style named {name}', target=name)
    path.unlink(missing_ok=True)
    index.pop(name, None)
    _atomic_json(_index_path(), index)
    return {'removed': name}


def style_path(name):
    """File that renders ``name``: a built-in style, or an installed one whose hash still matches."""
    key = (name or 'apa').lower()
    if key in BUILTIN:
        from citeproc_styles import get_style_filepath

        return get_style_filepath(BUILTIN[key])
    if not _NAME.match(key):
        _invalid('Citation style must be APA, MLA, Chicago or an installed style', field='style')
    path = styles_dir() / f'{key}.csl'
    entry = _load_index().get(key)
    if not path.is_file() or not entry:
        _invalid(
            f'The style {name} is not installed. Add it with: byax bibliography --action styles-add --name {key}',
            field='style',
            installed=sorted(_load_index()),
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get('sha256'):
        raise ByaxError(
            'conflict', f'The installed style {name} was changed after it was added; reinstall it', target=key
        )
    return str(path)


def style_format(name):
    """``author-date``, ``numeric``, ``note`` or ``label`` for a style."""
    key = (name or 'apa').lower()
    if key in BUILTIN_FORMATS:
        return BUILTIN_FORMATS[key]
    entry = _load_index().get(key)
    if not entry:
        _invalid(f'The style {name} is not installed', field='style')
    return entry.get('citation_format') or 'author-date'


def list_styles():
    """Built-in and installed styles with where they came from and their licence."""
    rows = [
        {
            'name': key,
            'title': title,
            'built_in': True,
            'citation_format': BUILTIN_FORMATS[key],
            'license': 'http://creativecommons.org/licenses/by-sa/3.0/',
        }
        for key, title in (
            ('apa', 'American Psychological Association 7th edition'),
            ('mla', 'Modern Language Association 9th edition'),
            ('chicago', 'Chicago Manual of Style 17th edition (notes and bibliography)'),
        )
    ]
    for name, entry in sorted(_load_index().items()):
        rows.append({'name': name, 'built_in': False, **entry})
    return {
        'styles': rows,
        'directory': str(styles_dir()),
        'repository': REPOSITORY,
        'attribution': 'CSL styles are licensed CC BY-SA 3.0; see docs/citation-licenses.md.',
    }
