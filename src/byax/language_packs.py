"""Language packs: dictionaries, grammar rules, word lists and interface strings as installable data.

A pack is a ``manifest.json`` plus the data files it lists, distributed as a ``.byaxpack`` ZIP or a
folder. The manifest records the pack ``id``, a ``language`` tag, a ``version``, a ``licence``, a
``source`` and, for every file, its ``kind``, ``path``, ``size`` and ``sha256``. Installed packs live
under the user data directory and every file is verified against its recorded hash before it is read.
English data ships inside the application as built-in packs. An installed pack for the same
language and kind takes precedence over the built-in data; removing it restores the built-in data.

Nothing here touches the network unless the caller explicitly gives a URL together with the expected
SHA-256 (``install_pack_from_url``); installing from a local file or folder never does.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from .errors import ByaxError

PACK_FORMAT = 1
RESOURCES = Path(__file__).parent / 'resources'
# kind -> accepted file suffixes. ``spelling`` is a pyspellchecker word-frequency dictionary.
KINDS = {
    'spelling': ('.json', '.json.gz'),
    'grammar': ('.json',),
    'fiction': ('.json',),
    'readability': ('.json',),
    'strings': ('.json',),
}
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_FILES = 16
_ID = re.compile(r'[a-z0-9][a-z0-9._-]{1,63}')
_LANGUAGE = re.compile(r'[a-z]{2,3}')
_VERSION = re.compile(r'\d+(?:\.\d+){0,3}')
_PATH = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_PLACEHOLDER = re.compile(r'\{[^{}]*\}|%\d+|%[sd]')
_STATE: dict = {'installed': None, 'root': None, 'json': {}, 'hashes': {}, 'builtin': None}


def data_root():
    """Folder holding installed packs: ``BYAX_LANGUAGES_DIR`` or the user data directory."""
    override = os.environ.get('BYAX_LANGUAGES_DIR')
    if override:
        return Path(override)
    from platformdirs import user_data_path

    return user_data_path('ByAuthorX', appauthor=False) / 'languages'


_HOOKS: list[Callable[[], object]] = []


def on_refresh(callback):
    """Register a cache-clearing callback run by ``refresh`` (modules that cache pack-derived data)."""
    _HOOKS.append(callback)


def refresh():
    """Forget cached listings and loaded data; call after another process changed the packs."""
    _STATE.update(installed=None, root=None, json={}, hashes={})
    for callback in _HOOKS:
        callback()


def rescan():
    """Re-read the installed-pack folder on the next lookup (cheap; keeps loaded data and dictionaries)."""
    _STATE['installed'] = None


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _version_key(version):
    return tuple(int(part) for part in version.split('.'))


def _invalid(message, **details):
    raise ByaxError('invalid_input', message, **details)


# Manifest ---------------------------------------------------------------------------------
def validate_manifest(manifest):
    """Return a normalized manifest or raise ``invalid_input`` naming the first problem."""
    if not isinstance(manifest, dict):
        _invalid('Pack manifest must be a JSON object')
    if manifest.get('format') != PACK_FORMAT:
        _invalid('Unsupported pack format', field='format', supported=PACK_FORMAT)
    result = {'format': PACK_FORMAT}
    for key, pattern in (('id', _ID), ('language', _LANGUAGE), ('version', _VERSION)):
        value = manifest.get(key)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            _invalid(f'Pack manifest {key} is missing or malformed', field=key)
        result[key] = value
    for key in ('name', 'licence', 'source'):
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            _invalid(f'Pack manifest {key} is required', field=key)
        result[key] = value.strip()
    description = manifest.get('description', '')
    if not isinstance(description, str) or len(description) > 2000:
        _invalid('Pack description must be text', field='description')
    result['description'] = description
    files = manifest.get('files')
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        _invalid(f'Pack manifest must list between 1 and {MAX_FILES} files', field='files')
    seen_paths, seen_kinds, entries = set(), set(), []
    for entry in files:
        if not isinstance(entry, dict):
            _invalid('Pack file entries must be objects', field='files')
        path, kind = entry.get('path'), entry.get('kind')
        if (
            not isinstance(path, str)
            or not _PATH.fullmatch(path)
            or '..' in path.split('/')
            or len(path) > 120
        ):
            _invalid('Pack file path is not a safe relative path', field='files', path=path)
        if path == 'manifest.json':
            _invalid('manifest.json cannot be listed as a data file', field='files')
        if kind not in KINDS:
            _invalid('Unknown pack file kind', field='files', kind=kind, supported=sorted(KINDS))
        if not path.endswith(KINDS[kind]):
            _invalid('Pack file suffix does not match its kind', field='files', path=path, kind=kind)
        if path in seen_paths or kind in seen_kinds:
            _invalid('Each path and each kind may appear once per pack', field='files', path=path)
        digest, size = entry.get('sha256'), entry.get('size')
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _invalid('Pack file sha256 must be 64 lowercase hex digits', field='files', path=path)
        if type(size) is not int or not 0 < size <= MAX_FILE_BYTES:
            _invalid('Pack file size is missing or too large', field='files', path=path)
        seen_paths.add(path)
        seen_kinds.add(kind)
        entries.append({'path': path, 'kind': kind, 'sha256': digest, 'size': size})
    result['files'] = entries
    return result


def build_manifest(directory, *, pack_id, name, language, version, licence, source, files, description=''):
    """Write ``manifest.json`` for a folder of data files; ``files`` maps kind to relative path."""
    directory = Path(directory)
    entries = []
    for kind, relative in sorted(files.items()):
        data = (directory / relative).read_bytes()
        entries.append(
            {'path': relative, 'kind': kind, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
        )
    manifest = validate_manifest(
        {
            'format': PACK_FORMAT,
            'id': pack_id,
            'name': name,
            'language': language,
            'version': version,
            'licence': licence,
            'source': source,
            'description': description,
            'files': entries,
        }
    )
    (directory / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    return manifest


# Built-in packs ---------------------------------------------------------------------------
def _spellchecker_languages():
    try:
        import spellchecker
    except ImportError:
        return {}
    folder = Path(spellchecker.__file__).parent / 'resources'
    return {p.name[: -len('.json.gz')]: p for p in folder.glob('*.json.gz')}


def builtin_packs():
    """One synthetic pack per shipped language, with the hashes of the data that ships."""
    if _STATE['builtin'] is None:
        found: dict[str, dict] = {}
        for kind in ('grammar', 'fiction', 'readability', 'strings'):
            for path in sorted((RESOURCES / kind).glob('*.json')) if (RESOURCES / kind).is_dir() else ():
                found.setdefault(path.stem, {})[kind] = path
        for language, path in _spellchecker_languages().items():
            found.setdefault(language, {})['spelling'] = path
        packs = []
        for language in sorted(found):
            files = [
                {
                    'path': str(path),
                    'kind': kind,
                    'sha256': _sha256_file(path),
                    'size': path.stat().st_size,
                }
                for kind, path in sorted(found[language].items())
            ]
            packs.append(
                {
                    'id': 'builtin-' + language,
                    'name': language + ' (built in)',
                    'language': language,
                    'version': 'builtin',
                    'licence': 'MIT (ByAuthorX data); spelling dictionaries: pyspellchecker, MIT',
                    'source': 'Shipped with ByAuthorX',
                    'description': '',
                    'files': files,
                    'builtin': True,
                    'status': 'ok',
                    'problems': [],
                    'directory': None,
                }
            )
        _STATE['builtin'] = packs
    return _STATE['builtin']


# Installed packs ------------------------------------------------------------------------
def _read_installed(directory):
    problems = []
    try:
        manifest = validate_manifest(json.loads((directory / 'manifest.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, ByaxError) as exc:
        message = exc.message if isinstance(exc, ByaxError) else str(exc)
        return {
            'id': directory.name,
            'directory': str(directory),
            'builtin': False,
            'status': 'invalid',
            'problems': [f'manifest: {message}'],
            'files': [],
            'language': None,
            'version': None,
        }
    if manifest['id'] != directory.name:
        problems.append('manifest id does not match its folder name')
    status = 'invalid' if problems else 'ok'
    return {**manifest, 'directory': str(directory), 'builtin': False, 'status': status, 'problems': problems}


def installed_packs(root=None):
    """Installed packs with their manifest status; hashes are checked by ``verify_pack`` and on use."""
    root = Path(root) if root is not None else data_root()
    if root is not None and _STATE['installed'] is not None and _STATE['root'] == str(root):
        return _STATE['installed']
    packs = []
    if root.is_dir():
        for directory in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')):
            packs.append(_read_installed(directory))
    _STATE['installed'], _STATE['root'] = packs, str(root)
    return packs


def verify_pack(pack):
    """Recompute every file hash; returns ``{'ok', 'problems'}`` and never raises for bad data."""
    problems = list(pack.get('problems', []))
    if pack.get('builtin'):
        return {'ok': not problems, 'problems': problems}
    base = Path(pack['directory'])
    for entry in pack.get('files', []):
        path = base / entry['path']
        if not path.is_file():
            problems.append(f'{entry["path"]}: file is missing')
        elif path.stat().st_size != entry['size']:
            problems.append(f'{entry["path"]}: size differs from the manifest')
        elif _sha256_file(path) != entry['sha256']:
            problems.append(f'{entry["path"]}: SHA-256 differs from the manifest')
    extras = sorted(
        str(p.relative_to(base)).replace(os.sep, '/')
        for p in base.rglob('*')
        if p.is_file() and p.name not in ('manifest.json', 'install.json')
    )
    listed = {entry['path'] for entry in pack.get('files', [])}
    problems.extend(f'{name}: file is not listed in the manifest' for name in extras if name not in listed)
    return {'ok': not problems, 'problems': problems}


def _public(pack, *, detail=False):
    result = {
        'id': pack['id'],
        'name': pack.get('name'),
        'language': pack.get('language'),
        'version': pack.get('version'),
        'licence': pack.get('licence'),
        'source': pack.get('source'),
        'built_in': bool(pack.get('builtin')),
        'kinds': sorted(entry['kind'] for entry in pack.get('files', [])),
        'status': pack.get('status', 'ok'),
    }
    if detail:
        result['files'] = [
            {k: entry[k] for k in ('kind', 'sha256', 'size')}
            | {'path': entry['path'] if not pack.get('builtin') else Path(entry['path']).name}
            for entry in pack.get('files', [])
        ]
        result['problems'] = list(pack.get('problems', []))
    return result


def list_packs(*, detail=False, root=None):
    return [_public(p, detail=detail) for p in builtin_packs() + installed_packs(root)]


def verify_packs(pack_id=None, *, root=None):
    results = []
    for pack in builtin_packs() + installed_packs(root):
        if pack_id and pack['id'] != pack_id:
            continue
        outcome = verify_pack(pack)
        results.append({'id': pack['id'], 'built_in': bool(pack.get('builtin')), **outcome})
    if pack_id and not results:
        raise ByaxError('missing_target', 'No such language pack', target=pack_id)
    return results


# Install / remove ---------------------------------------------------------------------------
def _safe_member(name):
    return bool(_PATH.fullmatch(name)) and '..' not in name.split('/') and len(name) <= 120


def _extract_archive(archive, destination):
    """Extract only manifest.json and the files it lists, with size limits and no path tricks."""
    try:
        zf = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ByaxError('invalid_input', 'The pack file is not a valid .byaxpack archive') from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos or len(infos) > MAX_FILES + 1:
            _invalid('The pack archive has an unexpected number of files')
        if sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
            raise ByaxError('limit_exceeded', 'The pack archive expands beyond the allowed size')
        by_name = {i.filename: i for i in infos}
        if any(not _safe_member(name) for name in by_name) or 'manifest.json' not in by_name:
            _invalid('The pack archive has an unsafe member name or no manifest.json')
        manifest_info = by_name['manifest.json']
        if manifest_info.file_size > 1 << 20:
            _invalid('manifest.json is too large')
        try:
            manifest = validate_manifest(json.loads(zf.read(manifest_info).decode('utf-8')))
        except (ValueError, UnicodeError) as exc:
            raise ByaxError('invalid_input', 'manifest.json is not valid UTF-8 JSON') from exc
        listed = {entry['path'] for entry in manifest['files']}
        stray = sorted(set(by_name) - listed - {'manifest.json'})
        if stray:
            _invalid('The archive holds files the manifest does not list', files=stray)
        for entry in manifest['files']:
            info = by_name.get(entry['path'])
            if info is None:
                _invalid('A listed file is missing from the archive', path=entry['path'])
            with zf.open(info) as handle:
                data = handle.read(entry['size'] + 1)
            _check_entry(entry, data)
            target = destination / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (destination / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8'
        )
    return manifest


def _check_entry(entry, data):
    if len(data) != entry['size']:
        raise ByaxError(
            'checksum_mismatch', 'A pack file differs in size from its manifest', path=entry['path']
        )
    if hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ByaxError('checksum_mismatch', 'A pack file does not match its SHA-256', path=entry['path'])
    _validate_data(entry['kind'], entry['path'], data)


def _validate_data(kind, path, data):
    """Structural check of a data file so a malformed pack is refused at install time."""
    if kind == 'spelling':
        if path.endswith('.gz'):
            import gzip

            try:
                data = gzip.decompress(data)
            except (OSError, EOFError) as exc:
                raise ByaxError('invalid_input', 'Spelling dictionary is not valid gzip', path=path) from exc
        try:
            table = json.loads(data.decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise ByaxError('invalid_input', 'Spelling dictionary is not valid JSON', path=path) from exc
        if not isinstance(table, dict) or not all(type(v) is int and v > 0 for v in table.values()):
            _invalid('Spelling dictionary must map words to positive integer frequencies', path=path)
        return
    try:
        value = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ByaxError('invalid_input', 'Pack data is not valid UTF-8 JSON', path=path) from exc
    if not isinstance(value, dict):
        _invalid('Pack data must be a JSON object', path=path)
    if kind == 'grammar':
        from .grammar import validate_rule_set

        validate_rule_set(value, path=path)
    elif kind == 'strings':
        validate_strings(value, path=path)


def install_pack(source, *, root=None, sha256=None, replace=False):
    """Install a ``.byaxpack`` file or an unpacked folder from the local disk. Never uses the network."""
    source = Path(source)
    if not source.exists():
        raise ByaxError('missing_target', 'Pack file or folder does not exist', target=str(source))
    expected = None
    if sha256 is not None:
        expected = str(sha256).lower()
        if not _SHA256.fullmatch(expected):
            _invalid('--sha256 must be 64 hex digits', field='sha256')
    root = Path(root) if root is not None else data_root()
    root.mkdir(parents=True, exist_ok=True)
    archive_sha = None
    if source.is_file():
        if source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ByaxError('limit_exceeded', 'Pack file is larger than the allowed size')
        archive_sha = _sha256_file(source)
        if expected and archive_sha != expected:
            raise ByaxError(
                'checksum_mismatch', 'The pack file does not match the expected SHA-256', actual=archive_sha
            )
    elif expected:
        _invalid('--sha256 applies to a pack file, not a folder', field='sha256')
    stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=root))
    try:
        manifest = _install_into(source, stage)
        target = root / manifest['id']
        previous = next((p for p in installed_packs(root) if p['id'] == manifest['id']), None)
        if (
            previous is not None
            and previous.get('version')
            and _version_key(previous['version']) >= _version_key(manifest['version'])
            and not replace
        ):
            raise ByaxError(
                'conflict',
                'A pack with this id and the same or a newer version is installed; use --replace',
                installed=previous['version'],
                offered=manifest['version'],
            )
        (stage / 'install.json').write_text(
            json.dumps({'source': str(source), 'archive_sha256': archive_sha}, indent=2) + '\n',
            encoding='utf-8',
        )
        retired = None
        if target.exists():
            retired = root / f'.old-{manifest["id"]}-{os.getpid()}'
            shutil.rmtree(retired, ignore_errors=True)
            os.replace(target, retired)
        try:
            os.replace(stage, target)
        except OSError:
            if retired is not None:
                os.replace(retired, target)
            raise
        if retired is not None:
            shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    refresh()
    return {**_public(_read_installed(root / manifest['id']), detail=True), 'archive_sha256': archive_sha}


def _install_into(source, stage):
    if source.is_file():
        return _extract_archive(source, stage)
    try:
        manifest = validate_manifest(json.loads((source / 'manifest.json').read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        raise ByaxError('invalid_input', 'The folder has no readable manifest.json') from exc
    for entry in manifest['files']:
        path = (source / entry['path']).resolve()
        if source.resolve() not in path.parents or not path.is_file() or path.is_symlink():
            _invalid('A listed pack file is missing or outside the folder', path=entry['path'])
        data = path.read_bytes() if path.stat().st_size <= entry['size'] else b''
        _check_entry(entry, data)
        target = stage / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (stage / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    return manifest


def install_pack_from_url(url, sha256, *, root=None, replace=False, client=None):
    """Download one pack from an explicitly given URL, check the stated SHA-256, then install it.

    The hash is mandatory: the caller names the exact bytes it expects, so a changed or
    substituted file is refused before it is read as a pack.
    """
    from urllib.parse import urlparse

    expected = str(sha256 or '').lower()
    if not _SHA256.fullmatch(expected):
        _invalid('Installing from a URL requires the expected --sha256', field='sha256')
    parts = urlparse(url)
    loopback = parts.hostname in ('127.0.0.1', 'localhost', '::1')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and loopback):
        _invalid('Pack URLs must use https', field='url')
    import httpx

    directory = Path(tempfile.mkdtemp(prefix='byax-pack-'))
    archive = directory / 'download.byaxpack'
    try:
        digest, total = hashlib.sha256(), 0
        owned = client is None
        http = client or httpx.Client(follow_redirects=True, timeout=60)
        try:
            with http.stream('GET', url) as response:
                response.raise_for_status()
                with archive.open('wb') as handle:
                    for chunk in response.iter_bytes(1 << 16):
                        total += len(chunk)
                        if total > MAX_ARCHIVE_BYTES:
                            raise ByaxError('limit_exceeded', 'The download exceeds the allowed pack size')
                        digest.update(chunk)
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            raise ByaxError('execution_failure', f'Download failed: {exc}') from exc
        finally:
            if owned:
                http.close()
        if digest.hexdigest() != expected:
            raise ByaxError(
                'checksum_mismatch',
                'The downloaded file does not match the expected SHA-256; nothing was installed',
                actual=digest.hexdigest(),
                bytes=total,
            )
        result = install_pack(archive, root=root, sha256=expected, replace=replace)
        result['downloaded_from'] = url
        return result
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def remove_pack(pack_id, *, root=None):
    if not isinstance(pack_id, str) or not _ID.fullmatch(pack_id):
        _invalid('Pack id is malformed', field='id')
    if any(p['id'] == pack_id for p in builtin_packs()):
        raise ByaxError('conflict', 'Built-in packs ship with the application and cannot be removed')
    root = Path(root) if root is not None else data_root()
    target = root / pack_id
    if not target.is_dir():
        raise ByaxError('missing_target', 'No such installed language pack', target=pack_id)
    shutil.rmtree(target)
    refresh()
    return {'id': pack_id, 'removed': True}


# Resolution and loading ---------------------------------------------------------------------
def _candidates(kind, language, root=None):
    """Packs offering ``kind`` for ``language``: newest installed version first, built-in last."""
    installed = [
        p
        for p in installed_packs(root)
        if p.get('status') == 'ok'
        and p.get('language') == language
        and any(f['kind'] == kind for f in p['files'])
    ]
    installed.sort(key=lambda p: _version_key(p['version']), reverse=True)
    builtin = [
        p for p in builtin_packs() if p['language'] == language and any(f['kind'] == kind for f in p['files'])
    ]
    return installed + builtin


def _verified(pack, entry):
    """Whether an installed pack file still matches its manifest; the result is cached per file version."""
    path = Path(pack['directory']) / entry['path']
    try:
        stat = path.stat()
    except OSError:
        return False
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _STATE['hashes']:
        try:
            data = path.read_bytes()
        except OSError:
            return False
        _STATE['hashes'][key] = (
            len(data) == entry['size'] and hashlib.sha256(data).hexdigest() == entry['sha256']
        )
    return _STATE['hashes'][key]


def resolve(kind, language):
    """Where the data for ``kind`` and ``language`` comes from, or None when no pack offers it."""
    if kind not in KINDS or not isinstance(language, str) or not language.isalpha():
        return None
    for pack in _candidates(kind, language):
        entry = next(f for f in pack['files'] if f['kind'] == kind)
        if pack.get('builtin'):
            return {
                'pack': pack['id'],
                'version': pack['version'],
                'path': Path(entry['path']),
                'built_in': True,
            }
        if _verified(pack, entry):
            path = Path(pack['directory']) / entry['path']
            return {
                'pack': pack['id'],
                'version': pack['version'],
                'path': path,
                'built_in': False,
                'sha256': entry['sha256'],
            }
    return None


def load_json(kind, language):
    """Parsed JSON data for a kind and language from the preferred verified pack, or None."""
    source = resolve(kind, language)
    if source is None or kind == 'spelling':
        return None
    path = source['path']
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (kind, language, str(path), stat.st_mtime_ns, stat.st_size)
    cache = _STATE['json']
    if key not in cache:
        try:
            data = path.read_bytes()
            if not source['built_in'] and hashlib.sha256(data).hexdigest() != source['sha256']:
                cache[key] = None
            else:
                cache[key] = json.loads(data.decode('utf-8'))
        except (OSError, ValueError):
            cache[key] = None
    return cache[key]


def available_languages(kind):
    """Language codes with data of ``kind`` from built-in or verified installed packs."""
    codes = {p['language'] for p in builtin_packs() if any(f['kind'] == kind for f in p['files'])}
    codes.update(
        p['language']
        for p in installed_packs()
        if p.get('status') == 'ok' and any(f['kind'] == kind for f in p['files'])
    )
    return sorted(c for c in codes if resolve(kind, c) is not None)


def data_signature(kind, language):
    """A short string that changes when the pack behind ``kind``/``language`` changes (for caches)."""
    source = resolve(kind, language)
    if source is None:
        return None
    if source['built_in']:
        return 'builtin'
    return f'{source["pack"]}@{source["version"]}:{source["sha256"][:12]}'


def spelling_dictionary(language):
    """Path of an installed spelling dictionary for ``language`` or None to use the built-in one."""
    source = resolve('spelling', language)
    if source is None or source['built_in']:
        return None
    return source['path']


# Interface strings -----------------------------------------------------------------------
def placeholders(text):
    return sorted(_PLACEHOLDER.findall(text))


def validate_strings(value, *, path='strings'):
    """A string table is ``{"language", "strings": {source: translation}}``; empty values are untranslated."""
    table = value.get('strings')
    if not isinstance(table, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in table.items()
    ):
        _invalid('A strings file needs a "strings" object of text to text', path=path)
    contexts = value.get('contexts', {})
    if not isinstance(contexts, dict) or not all(
        isinstance(table_, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in table_.items())
        for table_ in contexts.values()
    ):
        _invalid('"contexts" must map a context name to a table of text', path=path)


def usable_translation(source, translation):
    """A translation is used only when it is not empty and keeps the source's placeholders."""
    return bool(translation.strip()) and placeholders(source) == placeholders(translation)


def source_catalog():
    """The interface texts that can be translated (generated by scripts/extract_strings.py)."""
    try:
        return json.loads((RESOURCES / 'catalog' / 'strings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'strings': [], 'dynamic_call_sites': 0}


def extract_sources(source_dir):
    """Literal ``tr('...')`` texts and the number of ``tr`` calls with a non-literal argument."""
    texts, dynamic = set(), 0
    for path in sorted(Path(source_dir).glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == 'tr' and node.args:
                argument = node.args[0]
            else:
                continue
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                texts.add(argument.value)
            else:
                dynamic += 1
    return {
        'version': 1,
        'generated_by': 'scripts/extract_strings.py',
        'dynamic_call_sites': dynamic,
        'strings': sorted(texts),
    }


def _strings_for(language):
    data = load_json('strings', language)
    if not data:
        return {}, {}
    return data.get('strings', {}), data.get('contexts', {})


def translation_coverage(language, catalog=None):
    """Share of catalog texts that have a usable translation (English is the source language)."""
    catalog = catalog or source_catalog()
    texts = catalog.get('strings', [])
    if language == 'en':
        return {
            'language': 'en',
            'total': len(texts),
            'translated': len(texts),
            'percent': 100.0,
            'source_language': True,
            'dynamic_call_sites': catalog.get('dynamic_call_sites', 0),
        }
    table, contexts = _strings_for(language)
    merged = dict(table)
    for entries in contexts.values():
        merged.update({k: v for k, v in entries.items() if k not in merged})
    translated = sum(1 for text in texts if text in merged and usable_translation(text, merged[text]))
    rejected = sorted(
        text
        for text in merged
        if text in texts and merged[text].strip() and not usable_translation(text, merged[text])
    )
    return {
        'language': language,
        'total': len(texts),
        'translated': translated,
        'percent': round(100.0 * translated / len(texts), 1) if texts else 0.0,
        'source_language': False,
        'placeholder_mismatches': rejected[:20],
        'dynamic_call_sites': catalog.get('dynamic_call_sites', 0),
    }


def coverage_report():
    languages = sorted({'en', *available_languages('strings')})
    return [translation_coverage(language) for language in languages]


def make_translator(language, parent=None):
    """A ``QTranslator`` serving a language's string table, or None when no verified table exists."""
    table, contexts = _strings_for(language)
    if not table and not contexts:
        return None
    from PySide6.QtCore import QTranslator

    class PackTranslator(QTranslator):
        def translate(self, context, source_text, disambiguation=None, n=-1):
            candidate = contexts.get(context, {}).get(source_text) or table.get(source_text) or ''
            # None is a null QString: "not found", so Qt falls through to the source text.
            return candidate if candidate and usable_translation(source_text, candidate) else None

        def isEmpty(self):
            return False

    translator = PackTranslator(parent)
    translator.language = language
    return translator


def install_translator(app, language):
    """Install the pack translator for ``language`` on ``app``; returns it, or None for English/unknown."""
    if not language or language == 'en':
        return None
    translator = make_translator(language, app)
    if translator is not None:
        app.installTranslator(translator)
    return translator


# Command (CLI, MCP, REST) -----------------------------------------------------------------
def languages_command(a, owner):
    from .cli import _error, envelope

    action = a.get('action', 'list')
    if action == 'list':
        return envelope({'packs': list_packs(detail=bool(a.get('verbose')))})
    if action == 'verify':
        results = verify_packs(a.get('id'))
        return envelope(
            {'verification': results}, validation='passed' if all(r['ok'] for r in results) else 'failed'
        )
    if action == 'coverage':
        report = coverage_report()
        if a.get('language'):
            report = [r for r in report if r['language'] == a['language']] or [
                translation_coverage(a['language'])
            ]
        return envelope({'translation_coverage': report})
    if action == 'install':
        if a.get('url'):
            return envelope(
                {'pack': install_pack_from_url(a['url'], a.get('sha256'), replace=a.get('replace', False))}
            )
        if not a.get('pack_file'):
            _error(
                'invalid_input',
                'languages install needs --pack-file (or --url with --sha256)',
                field='pack_file',
            )
        return envelope(
            {'pack': install_pack(a['pack_file'], sha256=a.get('sha256'), replace=a.get('replace', False))}
        )
    if action == 'remove':
        if not a.get('id'):
            _error('invalid_input', 'languages remove needs --id', field='id')
        return envelope(remove_pack(a['id']))
    _error('invalid_input', f'Unknown languages action: {action}', field='action')


def pack_archive(directory, out):
    """Zip a folder that holds ``manifest.json`` into a ``.byaxpack`` (deterministic member order and times)."""
    directory = Path(directory)
    manifest = validate_manifest(json.loads((directory / 'manifest.json').read_text(encoding='utf-8')))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in ['manifest.json', *[e['path'] for e in manifest['files']]]:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, (directory / name).read_bytes())
    Path(out).write_bytes(buffer.getvalue())
    return _sha256_file(out)
