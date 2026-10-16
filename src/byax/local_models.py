"""Pinned catalogue, explicit download and verification of on-device search models.

Nothing here downloads on its own: :func:`download_model` runs only when the user asks (``byax models
--action download`` or the button in the library panel), after :func:`download_plan` has shown the
source, size and SHA-256 of every file. Files are written beside a ``.part`` file so an interrupted
download resumes, are checked against the catalogue's size and SHA-256 before they are renamed into
place, and live under the user data directory. Model weights are never committed to the repository
and the package imports cleanly without the optional ``byauthorx[local-models]`` runtime.

Inference (embedder and rerankers) is in :mod:`byax.local_inference`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import sys
import threading
from importlib import resources
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from .errors import ByaxError

CATALOGUE_RESOURCE = 'models.json'
MANIFEST = 'manifest.json'
CHUNK = 1 << 20
MAX_REDIRECTS = 5
# Loopback hosts may use plain http so tests can serve fixtures; everything else must be https.
LOOPBACK = ('127.0.0.1', 'localhost', '::1')
KINDS = ('embedder', 'reranker', 'layout')
SOURCES = ('auto', 'mirror', 'upstream')
RERANK_MODES = ('fast', 'accurate')
DEFAULT_RERANK_DEPTH = 40
MAX_RERANK_DEPTH = 100
PROMPT = 'Enable on-device semantic search? Nothing leaves this computer.'
DEFAULT_EMBEDDER = 'qwen3-embedding-0.6b-int8'
DEFAULT_RERANKER = 'answerai-colbert-small-v1'
OPT_IN_NOTE = (
    'Downloaded only on your request. Nothing is sent about your writing; only the model files are fetched.'
)

_LOCK = threading.RLock()


def _error(code, message, **details):
    raise ByaxError(code, message, **details)


def _now():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _url_allowed(url):
    parsed = urlparse(url)
    if parsed.scheme == 'https' and parsed.hostname:
        return True
    return parsed.scheme == 'http' and parsed.hostname in LOOPBACK


def _safe_relative(path):
    if (
        not isinstance(path, str)
        or not path
        or path.startswith(('/', '\\'))
        or '\\' in path
        or any(part in ('', '.', '..') for part in path.split('/'))
    ):
        _error('invalid_input', 'Invalid model file path in the catalogue', path=str(path))
    return path


def validate_catalogue(data):
    """Reject a catalogue that is malformed, uses an unsafe URL or lacks a pinned hash."""
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('models'), list):
        _error('invalid_input', 'Unsupported model catalogue')
    mirror = data.get('mirror')
    if not isinstance(mirror, dict) or not isinstance(mirror.get('base_url'), str):
        _error('invalid_input', 'Model catalogue lacks a mirror section')
    names = set()
    for model in data['models']:
        name = model.get('name') if isinstance(model, dict) else None
        if not isinstance(name, str) or not name or name in names or '/' in name or name.startswith('.'):
            _error('invalid_input', 'Invalid or duplicate model name in the catalogue', name=str(name))
        names.add(name)
        if model.get('kind') not in KINDS or not model.get('version') or not model.get('licence'):
            _error('invalid_input', 'Catalogue model needs kind, version and licence', name=name)
        files = model.get('files')
        if not isinstance(files, list) or not files:
            _error('invalid_input', 'Catalogue model lists no files', name=name)
        seen = set()
        for item in files:
            path = _safe_relative(item.get('path'))
            if path in seen or path == MANIFEST:
                _error('invalid_input', 'Duplicate model file path', name=name, path=path)
            seen.add(path)
            if not _hex(item.get('sha256')) or type(item.get('size')) is not int or item['size'] < 0:
                _error('invalid_input', 'Catalogue file needs a size and SHA-256', name=name, path=path)
            if not _url_allowed(item.get('url', '')):
                _error('invalid_input', 'Catalogue file URL must be https', name=name, path=path)
        model['size_bytes'] = sum(item['size'] for item in files)
    return data


def load_catalogue(path=None):
    """The pinned catalogue (``resources/models.json``), or a validated alternative for tests.

    ``BYAX_MODELS_CATALOGUE`` names another catalogue file (a mirror trial, or tests); it is validated
    exactly like the shipped one, so its URLs must still be https and its files hash-pinned.
    """
    path = path or os.environ.get('BYAX_MODELS_CATALOGUE')
    try:
        if path is None:
            text = resources.files('byax').joinpath('resources', CATALOGUE_RESOURCE).read_text('utf-8')
        else:
            text = Path(path).read_text(encoding='utf-8')
        data = json.loads(text)
    except (OSError, ValueError) as exc:
        raise ByaxError('invalid_input', 'Model catalogue cannot be read') from exc
    return validate_catalogue(data)


def get_model(name, catalogue=None):
    catalogue = catalogue or load_catalogue()
    for model in catalogue['models']:
        if model['name'] == name:
            return model
    raise ByaxError(
        'not_found',
        'Unknown model; run `byax models --action list`',
        name=name,
        models=[m['name'] for m in catalogue['models']],
    )


def runtime_status():
    """Whether the optional ONNX runtime is importable. Never raises."""
    info = {'installed': True, 'extra': 'byauthorx[local-models]'}
    for module in ('onnxruntime', 'tokenizers', 'numpy'):
        try:
            imported = __import__(module)
            info[module] = getattr(imported, '__version__', 'unknown')
        except ImportError:
            info[module] = None
            info['installed'] = False
    if not info['installed']:
        info['reason'] = 'not installed: install byauthorx[local-models] (onnxruntime and tokenizers)'
    return info


def models_dir():
    """User data directory for downloaded models (``BYAX_MODELS_DIR`` overrides it)."""
    override = os.environ.get('BYAX_MODELS_DIR')
    if override:
        return Path(override)
    from platformdirs import user_data_path

    return user_data_path('ByAuthorX', appauthor=False) / 'models'


def bundled_dir():
    """Where the standard installer places its models, if any (``BYAX_BUNDLED_MODELS`` overrides)."""
    override = os.environ.get('BYAX_BUNDLED_MODELS')
    if override:
        return Path(override)
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / 'models'
    return None


def _read_manifest(directory):
    try:
        value = json.loads((directory / MANIFEST).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) and isinstance(value.get('files'), dict) else None
    except (OSError, ValueError):
        return None


def _sha256(path, cancel_event=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while chunk := handle.read(CHUNK):
            if cancel_event is not None and cancel_event.is_set():
                _error('cancelled', 'Model verification cancelled')
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_dirs(name, directory=None):
    """(directory, origin) pairs where a model may live: the user copy first, then the bundled one."""
    roots = [(Path(directory) if directory else models_dir(), 'user')]
    bundled = bundled_dir()
    if bundled is not None and directory is None:
        roots.append((bundled, 'bundled'))
    return [(root / name, origin) for root, origin in roots]


def _inspect(model, directory):
    """State of one model directory without hashing: files present, sizes and manifest agreement.

    ``installed`` needs every file at its catalogue size with a manifest recording its SHA-256.
    A wrong size or recorded hash is ``corrupt``; anything incomplete is ``partial``.
    """
    present, partial, bad = [], [], []
    manifest = _read_manifest(directory)
    for item in model['files']:
        path = directory / item['path']
        part = path.with_name(path.name + '.part')
        if path.is_file():
            recorded = ((manifest or {}).get('files') or {}).get(item['path'])
            if path.stat().st_size != item['size'] or (recorded and recorded.get('sha256') != item['sha256']):
                bad.append(item['path'])
            elif recorded:
                present.append(item['path'])
            else:
                partial.append((item['path'], path.stat().st_size))
        elif part.is_file():
            partial.append((item['path'], part.stat().st_size))
    complete = len(present) == len(model['files']) and not bad
    if complete:
        state = 'installed'
    elif bad:
        state = 'corrupt'
    else:
        state = 'partial' if present or partial else 'absent'
    return state, present, partial, bad


def model_status(name, *, catalogue=None, directory=None):
    """Installation state of one model; cheap (sizes and manifest only, no hashing)."""
    model = get_model(name, catalogue)
    result = {
        'name': name,
        'title': model.get('title', name),
        'kind': model['kind'],
        'version': model['version'],
        'bundled_by_default': bool(model.get('bundled')),
        'size_bytes': model['size_bytes'],
        'licence': model['licence'].get('spdx'),
        'state': 'not_installed',
        'origin': None,
        'path': None,
        'partial_bytes': 0,
    }
    for candidate, origin in _candidate_dirs(name, directory):
        if not candidate.exists():
            continue
        state, present, partial, bad = _inspect(model, candidate)
        if state == 'installed':
            manifest = _read_manifest(candidate) or {}
            result.update(
                state='installed',
                origin=origin,
                path=str(candidate),
                verified_at=manifest.get('verified_at'),
                source=manifest.get('source'),
            )
            return result
        if state in ('partial', 'corrupt') and result['state'] == 'not_installed':
            result.update(
                state=state,
                origin=origin,
                path=str(candidate),
                partial_bytes=sum(size for _, size in partial)
                + sum((candidate / p).stat().st_size for p in present),
                bad_files=bad,
            )
    return result


def list_models(*, catalogue=None, directory=None):
    catalogue = catalogue or load_catalogue()
    return [model_status(m['name'], catalogue=catalogue, directory=directory) for m in catalogue['models']]


def _mirror_populated(catalogue):
    return bool(catalogue['mirror'].get('populated'))


def resolve_source(catalogue, source):
    if source not in SOURCES:
        _error('invalid_input', 'Download source must be auto, mirror or upstream', source=source)
    if source == 'auto':
        return 'mirror' if _mirror_populated(catalogue) else 'upstream'
    if source == 'mirror' and not _mirror_populated(catalogue):
        _error(
            'unsupported_feature',
            'The model mirror release has not been published yet; use --source upstream',
            release=catalogue['mirror'].get('release_tag'),
        )
    return source


def file_url(catalogue, item, source):
    if source == 'mirror':
        return catalogue['mirror']['base_url'].rstrip('/') + '/' + quote(item['mirror_asset'])
    return item['url']


def download_plan(name, *, source='auto', catalogue=None, directory=None):
    """Exactly what a download would fetch, shown before anything is requested."""
    catalogue = catalogue or load_catalogue()
    model = get_model(name, catalogue)
    chosen = resolve_source(catalogue, source)
    target = (Path(directory) if directory else models_dir()) / name
    status = model_status(name, catalogue=catalogue, directory=directory)
    files = [
        {
            'path': item['path'],
            'role': item.get('role'),
            'url': file_url(catalogue, item, chosen),
            'size_bytes': item['size'],
            'sha256': item['sha256'],
        }
        for item in model['files']
    ]
    return {
        'name': name,
        'title': model.get('title', name),
        'version': model['version'],
        'licence': model['licence'],
        'source': chosen,
        'upstream': model.get('upstream'),
        'files': files,
        'size_bytes': model['size_bytes'],
        'destination': str(target),
        'state': status['state'],
        'verification': 'Size and SHA-256 of every file are checked before the model can be used.',
        'privacy': OPT_IN_NOTE,
    }


def _open(client, url, headers):
    """GET ``url`` following redirects manually so every hop must be https (or loopback in tests)."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _url_allowed(url):
            _error('invalid_input', 'Refusing a non-https model URL', url=url)
        request = client.build_request('GET', url, headers=headers)
        response = client.send(request, stream=True)
        if response.is_redirect:
            location = response.headers.get('location')
            response.close()
            if not location:
                _error('provider_failure', 'Model download redirect without a location', url=url)
            url = urljoin(url, location)
            continue
        return response
    _error('provider_failure', 'Too many redirects while downloading a model', url=url)


def _fetch(client, url, part, item, *, progress=None, cancel_event=None):
    """Download one file into ``part``, resuming from its length; verify size and SHA-256."""
    import httpx

    expected, digest = item['size'], item['sha256']
    start = part.stat().st_size if part.exists() else 0
    if start > expected:
        part.unlink()
        start = 0
    restarted = False
    while start < expected:
        headers = {'Accept-Encoding': 'identity', **({'Range': f'bytes={start}-'} if start else {})}
        try:
            response = _open(client, url, headers)
        except httpx.HTTPError as exc:
            _error('network_unavailable', f'Cannot reach the model server: {exc}', url=url, resumable=True)
        try:
            if response.status_code == 416 and not restarted:
                # Our partial file does not fit the server's copy; start again once.
                restarted = True
                part.unlink(missing_ok=True)
                start = 0
                continue
            if response.status_code not in (200, 206):
                _error(
                    'provider_failure',
                    f'Model server returned HTTP {response.status_code}',
                    url=url,
                    status=response.status_code,
                )
            if response.status_code == 200 and start:
                # The server ignored Range: it is sending the whole file again.
                start = 0
                part.unlink(missing_ok=True)
            mode = 'ab' if start else 'wb'
            received = start
            try:
                with open(part, mode) as handle:
                    # iter_raw yields as bytes arrive, so a dropped connection keeps what was received.
                    for chunk in response.iter_raw():
                        if cancel_event is not None and cancel_event.is_set():
                            _error(
                                'cancelled', 'Model download cancelled; the partial file is kept to resume'
                            )
                        received += len(chunk)
                        if received > expected:
                            handle.close()
                            part.unlink(missing_ok=True)
                            _error(
                                'checksum_mismatch', 'Model file is larger than the catalogue size', url=url
                            )
                        handle.write(chunk)
                        if progress:
                            progress(item['path'], received, expected)
                    handle.flush()
                    os.fsync(handle.fileno())
            except httpx.HTTPError as exc:
                _error(
                    'network_unavailable',
                    f'Connection lost while downloading; run the command again to resume ({exc})',
                    url=url,
                    resumable=True,
                    received=received,
                )
            start = received
        finally:
            response.close()
        if start < expected:
            _error(
                'network_unavailable',
                'Download ended early; run the command again to resume',
                url=url,
                resumable=True,
                received=start,
            )
    if part.stat().st_size != expected or _sha256(part, cancel_event) != digest:
        part.unlink(missing_ok=True)
        _error(
            'checksum_mismatch',
            'Downloaded file does not match the pinned size and SHA-256; it was discarded',
            path=item['path'],
            expected_sha256=digest,
        )


def _client(client=None):
    if client is not None:
        return client, False
    import httpx

    from . import __version__

    return (
        httpx.Client(
            timeout=httpx.Timeout(30.0, read=60.0),
            headers={'User-Agent': f'ByAuthorX/{__version__} (explicit model download)'},
        ),
        True,
    )


def download_model(
    name,
    *,
    source='auto',
    catalogue=None,
    directory=None,
    client=None,
    progress=None,
    cancel_event=None,
):
    """Fetch and verify every file of ``name``. Call only after the user confirmed the plan.

    Already verified files are kept, partial files resume, and the model is usable only once all
    files match the catalogue and the manifest has been written.
    """
    catalogue = catalogue or load_catalogue()
    plan = download_plan(name, source=source, catalogue=catalogue, directory=directory)
    model = get_model(name, catalogue)
    target = Path(plan['destination'])
    target.mkdir(parents=True, exist_ok=True)
    needed = 0
    for item in model['files']:
        path = target / item['path']
        part = path.with_name(path.name + '.part')
        if path.is_file() and path.stat().st_size == item['size']:
            continue
        needed += item['size'] - (part.stat().st_size if part.exists() else 0)
    free = shutil.disk_usage(target).free
    if needed > free:
        _error(
            'insufficient_space',
            f'Not enough disk space: {needed} bytes needed, {free} available',
            needed=needed,
            available=free,
        )
    http, owned = _client(client)
    fetched, kept = [], []
    try:
        with _LOCK:
            for item in model['files']:
                path = target / item['path']
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_file() and path.stat().st_size == item['size'] and _sha256(path) == item['sha256']:
                    kept.append(item['path'])
                    continue
                path.unlink(missing_ok=True)
                part = path.with_name(path.name + '.part')
                url = file_url(catalogue, item, plan['source'])
                _fetch(http, url, part, item, progress=progress, cancel_event=cancel_event)
                os.replace(part, path)
                fetched.append(item['path'])
            _write_manifest(model, target, plan['source'])
    finally:
        if owned:
            http.close()
    return {
        **model_status(name, catalogue=catalogue, directory=directory),
        'downloaded': fetched,
        'kept': kept,
        'source': plan['source'],
        'verified': True,
    }


def _write_manifest(model, directory, source):
    files = {}
    for item in model['files']:
        stat = (directory / item['path']).stat()
        files[item['path']] = {'size': stat.st_size, 'sha256': item['sha256'], 'mtime_ns': stat.st_mtime_ns}
    manifest = {
        'name': model['name'],
        'version': model['version'],
        'source': source,
        'verified_at': _now(),
        'files': files,
    }
    temporary = directory / (MANIFEST + '.tmp')
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    os.replace(temporary, directory / MANIFEST)


def verify_model(name, *, catalogue=None, directory=None, cancel_event=None):
    """Re-hash every file against the catalogue. A mismatch is reported, never repaired silently."""
    catalogue = catalogue or load_catalogue()
    model = get_model(name, catalogue)
    for candidate, origin in _candidate_dirs(name, directory):
        if not candidate.exists():
            continue
        problems = []
        for item in model['files']:
            path = candidate / item['path']
            if not path.is_file():
                problems.append({'path': item['path'], 'problem': 'missing'})
            elif path.stat().st_size != item['size']:
                problems.append({'path': item['path'], 'problem': 'wrong size'})
            elif _sha256(path, cancel_event) != item['sha256']:
                problems.append({'path': item['path'], 'problem': 'SHA-256 mismatch'})
        if not problems:
            if origin == 'user':
                _write_manifest(model, candidate, (_read_manifest(candidate) or {}).get('source', 'unknown'))
            return {'name': name, 'verified': True, 'origin': origin, 'path': str(candidate), 'problems': []}
        return {
            'name': name,
            'verified': False,
            'origin': origin,
            'path': str(candidate),
            'problems': problems,
        }
    return {'name': name, 'verified': False, 'origin': None, 'path': None, 'problems': ['not installed']}


def remove_model(name, *, catalogue=None, directory=None):
    """Delete the downloaded copy (including partial files). A bundled copy is part of the install."""
    get_model(name, catalogue)
    removed = None
    for candidate, origin in _candidate_dirs(name, directory):
        if candidate.exists() and origin == 'user':
            with _LOCK:
                shutil.rmtree(candidate)
            removed = str(candidate)
    from . import local_inference

    local_inference.forget(name)
    bundled = [o for c, o in _candidate_dirs(name, directory) if o == 'bundled' and c.exists()]
    return {'name': name, 'removed': removed, 'bundled_copy_kept': bool(bundled)}


def ready_path(name, *, catalogue=None, directory=None):
    """Directory of a model that is installed and verified; raises ``model_missing`` or ``model_corrupt``.

    The full hash runs once per file (and again only when size or modification time changed),
    so "verified before use" does not cost a re-hash on every query.
    """
    catalogue = catalogue or load_catalogue()
    model = get_model(name, catalogue)
    for candidate, _origin in _candidate_dirs(name, directory):
        if not candidate.is_dir():
            continue
        state, _present, _partial, bad = _inspect(model, candidate)
        if state == 'corrupt':
            _error('model_corrupt', 'Installed model files do not match the catalogue', name=name, files=bad)
        if state != 'installed':
            continue
        manifest = _read_manifest(candidate) or {}
        changed = [
            item
            for item in model['files']
            if manifest['files'][item['path']].get('mtime_ns')
            != (candidate / item['path']).stat().st_mtime_ns
        ]
        for item in changed:
            if _sha256(candidate / item['path']) != item['sha256']:
                _error(
                    'model_corrupt',
                    'Installed model file failed its SHA-256 check; remove and download it again',
                    name=name,
                    path=item['path'],
                )
        if changed:
            # Every file that moved was just re-hashed, so the manifest may record the new timestamps.
            # A read-only install folder (the bundled models) simply re-hashes again next time.
            try:
                _write_manifest(model, candidate, manifest.get('source', 'unknown'))
            except OSError:
                pass
        return candidate
    _error(
        'model_missing',
        f'Model {name} is not installed; run `byax models --action download --name {name}`',
        name=name,
    )


def settings_path(config_dir=None):
    from .providers import _default_dir

    return Path(config_dir or _default_dir()) / 'local-models.json'


def load_settings(config_dir=None):
    """Search-quality setting: ``fast`` (no reranker, the default) or ``accurate`` (rerank the top hits)."""
    settings = {
        'rerank': 'fast',
        'reranker': DEFAULT_RERANKER,
        'rerank_depth': DEFAULT_RERANK_DEPTH,
        'embedder': DEFAULT_EMBEDDER,
    }
    try:
        stored = json.loads(settings_path(config_dir).read_text(encoding='utf-8'))
        if isinstance(stored, dict):
            settings.update({k: v for k, v in stored.items() if k in settings})
    except (OSError, ValueError):
        pass
    return settings


def save_settings(values, config_dir=None, *, catalogue=None):
    from .providers import _atomic_json

    if not isinstance(values, dict) or set(values) - {'rerank', 'reranker', 'rerank_depth', 'embedder'}:
        _error('invalid_input', 'Settings accept rerank, reranker, rerank_depth and embedder')
    settings = load_settings(config_dir)
    settings.update(values)
    if settings['rerank'] not in RERANK_MODES:
        _error('invalid_input', 'rerank must be fast or accurate', rerank=settings['rerank'])
    depth = settings['rerank_depth']
    if type(depth) is not int or not 5 <= depth <= MAX_RERANK_DEPTH:
        _error('invalid_input', f'rerank_depth must be between 5 and {MAX_RERANK_DEPTH}')
    for key, kind in (('reranker', 'reranker'), ('embedder', 'embedder')):
        if get_model(settings[key], catalogue)['kind'] != kind:
            _error('invalid_input', f'{key} must name a catalogue {kind}', name=settings[key])
    _atomic_json(settings_path(config_dir), settings)
    return settings


def bundle_models(destination, *, catalogue=None, names=None, client=None, progress=None):
    """Download every ``bundled`` model into ``destination`` for an installer (build time only).

    Refuses with a clear message while the mirror release has not been populated: installers must
    not depend on a third-party host, so the standard variant waits for the project's own mirror.
    """
    catalogue = catalogue or load_catalogue()
    if not _mirror_populated(catalogue):
        _error(
            'unsupported_feature',
            'Cannot build the standard installer: the models-v1 mirror release is not populated yet. '
            'Create it as described in docs/packaging.md and set "populated": true in models.json, '
            'or build the lean variant.',
            release=catalogue['mirror'].get('release_tag'),
        )
    selected = names or [m['name'] for m in catalogue['models'] if m.get('bundled')]
    http, owned = _client(client)
    try:
        _check_mirror_checksums(catalogue, selected, http)
        return _bundle(catalogue, selected, destination, http, progress)
    finally:
        if owned:
            http.close()


def _check_mirror_checksums(catalogue, names, client):
    """Cross-check the mirror's own ``SHA256SUMS.txt`` against the pinned catalogue before bundling."""
    import httpx

    mirror = catalogue['mirror']
    url = mirror['base_url'].rstrip('/') + '/' + quote(mirror.get('checksums_asset', 'SHA256SUMS.txt'))
    try:
        response = _open(client, url, {'Accept-Encoding': 'identity'})
        try:
            if response.status_code != 200:
                _error(
                    'provider_failure', f'Mirror checksum file returned HTTP {response.status_code}', url=url
                )
            text = response.read().decode('utf-8', 'replace')
        finally:
            response.close()
    except httpx.HTTPError as exc:
        _error('network_unavailable', f'Cannot reach the model mirror: {exc}', url=url)
    published = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            published[parts[1].strip().lstrip('*')] = parts[0].lower()
    for name in names:
        for item in get_model(name, catalogue)['files']:
            if published.get(item['mirror_asset']) != item['sha256']:
                _error(
                    'checksum_mismatch',
                    'The mirror SHA256SUMS.txt does not list this file with the pinned SHA-256',
                    asset=item['mirror_asset'],
                )


def _bundle(catalogue, selected, destination, http, progress):
    results = []
    for name in selected:
        results.append(
            download_model(
                name,
                source='mirror',
                catalogue=catalogue,
                directory=destination,
                client=http,
                progress=progress,
            )
        )
    return results


def semantic_offer(library_id, model=None, *, catalogue=None):
    """What turning on on-device semantic search for a library involves: the prompt, model and runtime."""
    catalogue = catalogue or load_catalogue()
    name = model or load_settings()['embedder']
    entry = get_model(name, catalogue)
    if entry['kind'] != 'embedder':
        _error('invalid_input', 'Model is not an embedder', name=name)
    status = model_status(name, catalogue=catalogue)
    return {
        'prompt': PROMPT,
        'confirmation_required': True,
        'library_id': library_id,
        'model': status,
        'runtime': runtime_status(),
        'plan': download_plan(name, catalogue=catalogue) if status['state'] != 'installed' else None,
    }


def enable_semantic_search(library_id, model=None, *, registry=None, catalogue=None):
    """Configure a library to use an installed on-device embedder; returns the updated library.

    Call only after the user answered the prompt. It indexes nothing by itself: the caller starts the
    embedding job, so progress and cancellation stay with the job manager.
    """
    from .libraries import LibraryRegistry

    catalogue = catalogue or load_catalogue()
    name = model or load_settings()['embedder']
    entry = get_model(name, catalogue)
    if entry['kind'] != 'embedder':
        _error('invalid_input', 'Model is not an embedder', name=name)
    runtime = runtime_status()
    if not runtime['installed']:
        _error('missing_dependency', runtime['reason'], extra=runtime['extra'])
    ready_path(name, catalogue=catalogue)
    parameters = entry.get('runtime', {})
    dimension = int(parameters.get('dimension') or parameters['native_dimension'])
    return (registry or LibraryRegistry()).configure_embedding(
        library_id, {'provider': 'local:' + name, 'dimension': dimension}
    )


def models_command(a, owner=None):
    """``byax models``: list, status, download, verify, remove, configure."""
    from .cli import envelope

    action = a.get('action', 'list')
    catalogue = load_catalogue()
    directory = a.get('models_dir')
    name = a.get('name')
    runtime = runtime_status()
    if action in ('list', 'status') and not name:
        return envelope(
            {
                'runtime': runtime,
                'models_dir': str(Path(directory) if directory else models_dir()),
                'bundled_dir': str(bundled_dir()) if bundled_dir() else None,
                'models': list_models(catalogue=catalogue, directory=directory),
                'settings': load_settings(a.get('config_dir')),
                'mirror': {k: v for k, v in catalogue['mirror'].items() if k != 'note'}
                | {'note': catalogue['mirror'].get('note')},
                'prompt': PROMPT,
            }
        )
    if action == 'configure':
        values = a.get('input') or {}
        return envelope({'settings': save_settings(values, a.get('config_dir'), catalogue=catalogue)})
    if not name:
        _error('invalid_input', f'models --action {action} requires --name', field='name')
    if action == 'status':
        return envelope(
            {
                'runtime': runtime,
                'model': model_status(name, catalogue=catalogue, directory=directory),
                'plan': download_plan(
                    name, source=a.get('source', 'auto'), catalogue=catalogue, directory=directory
                ),
            }
        )
    if action == 'download':
        plan = download_plan(name, source=a.get('source', 'auto'), catalogue=catalogue, directory=directory)
        if plan['state'] == 'installed':
            return envelope(
                {'plan': plan, 'model': model_status(name, catalogue=catalogue, directory=directory)}
            )
        if not a.get('confirm'):
            return envelope(
                {
                    'plan': plan,
                    'confirmation_required': True,
                    'message': 'Nothing was downloaded. Review the source, size and SHA-256 above, then '
                    f'repeat with --confirm to download {plan["size_bytes"]} bytes.',
                }
            )
        return envelope(
            {
                'model': download_model(
                    name, source=a.get('source', 'auto'), catalogue=catalogue, directory=directory
                )
            }
        )
    if action == 'verify':
        result = verify_model(name, catalogue=catalogue, directory=directory)
        return envelope({'verification': result}, execution='completed' if result['verified'] else 'failed')
    if action == 'remove':
        return envelope(remove_model(name, catalogue=catalogue, directory=directory))
    _error('invalid_input', 'Unknown models action', action=action)
