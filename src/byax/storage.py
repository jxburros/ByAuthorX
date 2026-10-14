"""Atomic, validated ZIP persistence and advisory cross-process document locks."""

import base64
import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, Self

from platformdirs import user_cache_path

from .errors import ByaxError
from .model import new_id, validate_document

MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 10000
_LOCKS: dict[str, dict[str, Any]] = {}
_MUTEX = threading.RLock()
_SECRET_KEYS = {
    'api_key',
    'apikey',
    'access_token',
    'refresh_token',
    'password',
    'client_secret',
    'provider_credentials',
    'executable_provider_config',
}


def _check_secrets(value: Any, path: str = '$') -> None:
    pending = [(value, path, 0)]
    while pending:
        current, current_path, depth = pending.pop()
        if depth > 100:
            raise ByaxError(
                'limit_exceeded', 'Structured data exceeds the 100-level nesting limit.', field=current_path
            )
        if isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    raise ByaxError('invalid_input', 'JSON object keys must be strings.', field=current_path)
                if key.lower() in _SECRET_KEYS:
                    raise ByaxError(
                        'invalid_input',
                        'Credentials cannot be embedded in document packages.',
                        field=current_path + '.' + key,
                    )
                pending.append((child, current_path + '.' + key, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, current_path, depth + 1) for child in current)


class DocumentLock:
    def __init__(self, path: str | os.PathLike[str], owner: str | None = None) -> None:
        self.path = str(Path(path).resolve())
        self.owner = owner or new_id('owner')
        self.acquired = False

    def acquire(self) -> Self:
        if self.acquired:
            return self
        with _MUTEX:
            held = _LOCKS.get(self.path)
            if held:
                if held['owner'] != self.owner:
                    raise ByaxError(
                        'locked',
                        'Document is open in another editing session.',
                        target=self.path,
                        retry='Close the other editor before retrying.',
                    )
                held['count'] += 1
            else:
                # Keep a stable inode across contenders. Unlinking a sidecar on
                # release can let two processes lock different inodes for one file.
                lock_key = hashlib.sha256(os.path.normcase(self.path).encode('utf-8')).hexdigest()
                lock_path = user_cache_path('ByAuthorX') / 'locks' / (lock_key + '.lock')
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = open(lock_path, 'a+b')  # noqa: SIM115 -- held until DocumentLock.release across calls
                try:
                    if sys.platform == 'win32':
                        import msvcrt

                        handle.seek(0)
                        if not handle.read(1):
                            handle.write(b' ')
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    handle.close()
                    raise ByaxError(
                        'locked', 'Document is locked by another process.', target=self.path
                    ) from exc
                _LOCKS[self.path] = {'owner': self.owner, 'count': 1, 'handle': handle}
            self.acquired = True
        return self

    def release(self) -> None:
        with _MUTEX:
            if not self.acquired:
                return
            held = _LOCKS[self.path]
            held['count'] -= 1
            if not held['count']:
                handle = held['handle']
                if sys.platform == 'win32':
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
                del _LOCKS[self.path]
            self.acquired = False

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


def _json(data: bytes, name: str) -> Any:
    try:

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError('Duplicate JSON key: ' + key)
                result[key] = value
            return result

        return json.loads(
            data.decode('utf-8'),
            object_pairs_hook=pairs,
            parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)),
        )
    except RecursionError as exc:
        raise ByaxError('limit_exceeded', 'JSON nesting exceeds parser limits.', target=name) from exc
    except (ValueError, UnicodeError) as exc:
        raise ByaxError('invalid_input', 'Invalid UTF-8 JSON resource.', target=name) from exc


def _members(doc: dict[str, Any]) -> dict[str, bytes]:
    _check_secrets(doc)
    validate_document(doc)
    stored = copy.deepcopy(doc)
    members = {}
    for digest, asset in stored['assets'].items():
        try:
            data = base64.b64decode(asset.pop('data'), validate=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise ByaxError('invalid_input', 'Asset data must be valid base64.', target=digest) from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise ByaxError('invalid_input', 'Asset content hash does not match its identity.', target=digest)
        members[f'assets/{digest}'] = data
        asset['resource'] = f'assets/{digest}'
    members['document.json'] = (
        json.dumps(stored, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
    ).encode()
    manifest = {
        'format': 'ByAuthorX',
        'format_version': 1,
        'schema_versions': {'document': 1},
        'document_id': doc['id'],
        'revision': doc['revision'],
        'resources': {
            k: {'sha256': hashlib.sha256(v).hexdigest(), 'bytes': len(v)} for k, v in sorted(members.items())
        },
    }
    members['manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    _validate_sizes(members)
    return members


def _validate_sizes(members: dict[str, bytes]) -> None:
    if (
        len(members) > MAX_MEMBERS
        or sum(len(v) for v in members.values()) > MAX_PACKAGE_BYTES
        or any(len(v) > MAX_MEMBER_BYTES for v in members.values())
    ):
        raise ByaxError('limit_exceeded', 'Package exceeds configured storage limits.')


def _safe_name(name: str) -> bool:
    p = PurePosixPath(name)
    return (
        bool(name)
        and not p.is_absolute()
        and '..' not in p.parts
        and '\\' not in name
        and ':' not in name
        and str(p) == name
    )


def _decode(members: dict[str, bytes]) -> dict[str, Any]:
    _validate_sizes(members)
    if 'manifest.json' not in members or 'document.json' not in members:
        raise ByaxError('invalid_input', 'Package is missing required manifest or document resource.')
    manifest = _json(members['manifest.json'], 'manifest.json')
    if (
        not isinstance(manifest, dict)
        or manifest.get('format') != 'ByAuthorX'
        or manifest.get('format_version') != 1
    ):
        raise ByaxError('unsupported_version', 'Unsupported ByAuthorX package format.')
    unknown = set(manifest) - {
        'format',
        'format_version',
        'schema_versions',
        'document_id',
        'revision',
        'resources',
    }
    if unknown:
        raise ByaxError(
            'unsupported_feature',
            'Unknown manifest fields cannot be safely rewritten.',
            fields=sorted(unknown),
        )
    if manifest.get('schema_versions') != {'document': 1}:
        raise ByaxError('unsupported_version', 'Unsupported package resource schema versions.')
    resources = manifest.get('resources', {})
    if not isinstance(resources, dict) or set(resources) != set(members) - {'manifest.json'}:
        raise ByaxError('invalid_input', 'Manifest does not account for every resource.')
    for name, entry in resources.items():
        if (
            not isinstance(entry, dict)
            or entry.get('sha256') != hashlib.sha256(members[name]).hexdigest()
            or entry.get('bytes') != len(members[name])
        ):
            raise ByaxError('invalid_input', 'Resource integrity check failed.', target=name)
    doc = _json(members['document.json'], 'document.json')
    _check_secrets(doc)
    validate_document(doc)
    if manifest.get('document_id') != doc['id'] or manifest.get('revision') != doc['revision']:
        raise ByaxError('invalid_input', 'Manifest identities disagree with document.')
    used = {'document.json', 'manifest.json'}
    for digest, asset in doc['assets'].items():
        resource = asset.pop('resource', None)
        if (
            resource != f'assets/{digest}'
            or resource not in members
            or hashlib.sha256(members[resource]).hexdigest() != digest
        ):
            raise ByaxError('invalid_input', 'Missing or invalid asset resource.', target=digest)
        asset['data'] = base64.b64encode(members[resource]).decode('ascii')
        used.add(resource)
    if used != set(members):
        raise ByaxError(
            'unsupported_feature',
            'Unknown package resources cannot be safely rewritten.',
            resources=sorted(set(members) - used),
        )
    _check_secrets(doc)
    return doc


def load_document(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_PACKAGE_BYTES:
                raise ByaxError('limit_exceeded', 'Archive exceeds configured storage limits.')
            names = [i.filename for i in infos]
            if len(set(names)) != len(names):
                raise ByaxError('invalid_input', 'Duplicate archive members are forbidden.')
            for i in infos:
                if (
                    not _safe_name(i.filename)
                    or i.is_dir()
                    or stat.S_ISLNK(i.external_attr >> 16)
                    or i.flag_bits & 1
                ):
                    raise ByaxError('invalid_input', 'Unsafe or encrypted archive member.', target=i.filename)
                if i.file_size > MAX_MEMBER_BYTES:
                    raise ByaxError('limit_exceeded', 'Archive member exceeds size limit.', target=i.filename)
            return _decode({i.filename: archive.read(i) for i in infos})
    except ByaxError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error, EOFError) as exc:
        raise ByaxError('invalid_input', f'Cannot read native package: {exc}', target=str(path)) from exc


def save_document(doc: dict[str, Any], path: str | os.PathLike[str], *, owner: str | None = None) -> None:
    members = _members(doc)
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with DocumentLock(target, owner=owner):
        fd, tmp = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
        try:
            with os.fdopen(fd, 'w+b') as stream:
                with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for name, data in sorted(members.items()):
                        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        info.external_attr = 0o600 << 16
                        archive.writestr(info, data)
                stream.flush()
                os.fsync(stream.fileno())
            load_document(tmp)
            if target.exists():
                # mkstemp creates 0600 files; keep the document's existing mode.
                os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
            os.replace(tmp, target)
            if os.name != 'nt':
                dirfd = os.open(target.parent, os.O_DIRECTORY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def unpack(
    path: str | os.PathLike[str], out_dir: str | os.PathLike[str], include_history: bool = False
) -> dict[str, Any]:
    doc = load_document(path)
    if not include_history:
        doc['history'] = {'revisions': [], 'branches': {}, 'current_branch': 'main'}
        doc['requests'] = {}
    doc['settings'].pop('cache', None)
    target = Path(out_dir)
    if target.exists() and any(target.iterdir()):
        raise ByaxError('conflict', 'Unpack destination must be empty.', target=str(target))
    target.mkdir(parents=True, exist_ok=True)
    for name, data in _members(doc).items():
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return {'path': str(target), 'history_included': include_history}


def pack(source_dir: str | os.PathLike[str], out_path: str | os.PathLike[str]) -> dict[str, Any]:
    source = Path(source_dir)
    members: dict[str, bytes] = {}
    total = 0
    for entry in source.rglob('*'):
        if entry.is_symlink():
            raise ByaxError('invalid_input', 'Source folders cannot contain symlinks.', target=str(entry))
        if entry.is_file():
            name = entry.relative_to(source).as_posix()
            if not _safe_name(name):
                raise ByaxError('invalid_input', 'Invalid source resource.', target=name)
            size = entry.stat().st_size
            if size > MAX_MEMBER_BYTES or total + size > MAX_PACKAGE_BYTES or len(members) >= MAX_MEMBERS:
                raise ByaxError(
                    'limit_exceeded', 'Source folder exceeds configured storage limits.', target=name
                )
            with entry.open('rb') as stream:
                data = stream.read(min(MAX_MEMBER_BYTES, MAX_PACKAGE_BYTES - total) + 1)
            total += len(data)
            if len(data) > MAX_MEMBER_BYTES or total > MAX_PACKAGE_BYTES:
                raise ByaxError(
                    'limit_exceeded', 'Source resource grew beyond configured storage limits.', target=name
                )
            members[name] = data
    if 'manifest.json' not in members or 'document.json' not in members:
        raise ByaxError('invalid_input', 'Source folder requires manifest.json and document.json.')
    manifest = _json(members['manifest.json'], 'manifest.json')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('resources'), dict):
        raise ByaxError('invalid_input', 'Source manifest is malformed.')
    original_resources = manifest['resources']
    document_hash = hashlib.sha256(members['document.json']).hexdigest()
    original_document = original_resources.get('document.json')
    if not isinstance(original_document, dict):
        raise ByaxError('invalid_input', 'Source manifest must declare document.json.')
    changed = original_document.get('sha256') != document_hash
    # Source folders are editable working copies. Rebuild integrity metadata while
    # _decode still enforces identities, schemas and SHA-named asset contents.
    manifest['resources'] = {
        name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        for name, data in sorted(members.items())
        if name != 'manifest.json'
    }
    changed = changed or set(original_resources) != set(manifest['resources'])
    members['manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    doc = _decode(members)
    base_revision = doc['revision']
    if changed:
        # Identical edited source folders produce identical packages; no wall clock
        # or random identity is introduced by a reproducible pack operation.
        revision_hash = hashlib.sha256((doc['id'] + base_revision + document_hash).encode()).hexdigest()
        doc['revision'] = 'rev_' + revision_hash[:32]
        doc['metadata']['source_pack'] = {
            'origin': 'edited_source_folder',
            'base_revision': base_revision,
            'source_document_sha256': document_hash,
        }
        doc['history']['branches'][doc['history']['current_branch']] = doc['revision']
        doc['requests'] = {}
        for finding in doc['review']['findings']:
            finding['status'] = 'stale'
    save_document(doc, out_path)
    return {
        'path': str(out_path),
        'document_id': doc['id'],
        'base_revision': base_revision,
        'revision': doc['revision'],
        'source_modified': changed,
    }
