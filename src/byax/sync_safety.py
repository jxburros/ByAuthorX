"""Sync-folder safety: detect cloud-synced folders, risky patterns and conflicted copies.

Writers keep documents in Dropbox, iCloud Drive, OneDrive and similar folders. A sync client
can leave partial files, placeholders and "conflicted copy" files beside a document when two
computers edit it. Everything here is local: path patterns, marker files and environment
variables, never the network and never a registry or sync-client API. Detection reads names,
file attributes and package manifests only. Nothing is deleted or moved except by the explicit
``archive_conflicted_copy`` action, and a merge is always a reviewable proposal built on the
existing branch-merge flow (``engine.import_branch`` then ``propose_merge``).

The co-editing decision stands (docs/coediting-spike.md): no live co-typing. This module is the
safe, local-first substitute: detect divergence after the fact and merge it through review.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

from platformdirs import user_cache_path, user_data_path

from .errors import ByaxError

PROVIDERS = {
    'dropbox': 'Dropbox',
    'icloud': 'iCloud Drive',
    'onedrive': 'OneDrive',
    'google_drive': 'Google Drive',
    'box': 'Box',
    'nextcloud': 'Nextcloud',
    'owncloud': 'ownCloud',
    'syncthing': 'Syncthing',
    'pcloud': 'pCloud',
}
ARCHIVE_FOLDER = 'ByAuthorX conflicted copies'
LARGE_PACKAGE_BYTES = 20 * 1024 * 1024
LARGE_HISTORY_BYTES = 10 * 1024 * 1024
LARGE_HISTORY_REVISIONS = 500
MAX_FOLDER_ENTRIES = 2000
_RANK = {'none': 0, 'low': 1, 'medium': 2, 'high': 3}
_MAX_ANCESTORS = 40
_SCAN_ENTRIES = 5000

# Windows file attributes and the macOS "dataless" flag that mark online-only placeholders.
_ATTR_OFFLINE = 0x1000
_ATTR_RECALL_ON_OPEN = 0x40000
_ATTR_RECALL_ON_DATA_ACCESS = 0x400000
_SF_DATALESS = 0x40000000


# -- Provider detection -----------------------------------------------------------------------

# (provider, confidence, regular expression over one path component, description)
_COMPONENT_RULES = (
    ('icloud', 'high', r'com~apple~CloudDocs', 'iCloud Drive container folder'),
    ('icloud', 'high', r'Mobile Documents', 'iCloud Drive Mobile Documents folder'),
    ('icloud', 'medium', r'iCloud ?Drive', 'folder named iCloud Drive'),
    ('onedrive', 'high', r'OneDrive-.+', 'macOS File Provider OneDrive folder'),
    ('onedrive', 'medium', r'OneDrive( - .+| -.+| \(.+\))?', 'folder named OneDrive'),
    ('google_drive', 'high', r'GoogleDrive-.+', 'macOS File Provider Google Drive folder'),
    ('google_drive', 'medium', r'My Drive|Shared drives|Google Drive', 'Google Drive folder name'),
    ('dropbox', 'medium', r'Dropbox( \(.+\))?', 'folder named Dropbox'),
    ('box', 'high', r'Box-Box', 'macOS File Provider Box folder'),
    ('box', 'medium', r'Box Sync|Box', 'folder named Box'),
    ('nextcloud', 'medium', r'Nextcloud', 'folder named Nextcloud'),
    ('owncloud', 'medium', r'ownCloud', 'folder named ownCloud'),
    ('pcloud', 'medium', r'pCloud ?(Drive|Sync)', 'pCloud folder name'),
)
_COMPILED_RULES = tuple(
    (provider, confidence, re.compile(rf'^(?:{pattern})$', re.IGNORECASE), note)
    for provider, confidence, pattern, note in _COMPONENT_RULES
)
# Marker names found inside the synchronized root: (provider, confidence, exact name, note).
_MARKERS = (
    ('dropbox', 'high', '.dropbox', 'Dropbox marker file'),
    ('dropbox', 'high', '.dropbox.cache', 'Dropbox cache folder'),
    ('syncthing', 'high', '.stfolder', 'Syncthing folder marker'),
    ('syncthing', 'medium', '.stversions', 'Syncthing versions folder'),
    ('nextcloud', 'high', '.nextcloudsync.log', 'Nextcloud sync log'),
    ('owncloud', 'high', '.owncloudsync.log', 'ownCloud sync log'),
    ('owncloud', 'high', '.csync_journal.db', 'ownCloud sync journal'),
)
_ENV_ROOTS = (
    ('OneDrive', 'onedrive'),
    ('OneDriveConsumer', 'onedrive'),
    ('OneDriveCommercial', 'onedrive'),
)


def _norm(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _is_under(path: str, root: str) -> bool:
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _ancestors(path: Path) -> list[Path]:
    result, current = [], path
    for _ in range(_MAX_ANCESTORS):
        result.append(current)
        if current.parent == current:
            break
        current = current.parent
    return result


def _dropbox_roots(env: Mapping[str, str], home: Path) -> list[tuple[str, str]]:
    """Folders named by Dropbox's own info.json, a small local file (never an API call)."""
    places = [home / '.dropbox' / 'info.json']
    for variable in ('APPDATA', 'LOCALAPPDATA'):
        if env.get(variable):
            places.append(Path(env[variable]) / 'Dropbox' / 'info.json')
    roots = []
    for place in places:
        try:
            if place.stat().st_size > 64 * 1024:
                continue
            data = json.loads(place.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            for account, value in data.items():
                if isinstance(value, dict) and isinstance(value.get('path'), str):
                    roots.append((value['path'], f'{place} ({account} account)'))
    return roots


def detect_sync_folder(
    path: str | os.PathLike[str], *, env: Mapping[str, str] | None = None, home: Path | None = None
) -> dict[str, Any]:
    """Report whether ``path`` lies in a cloud-synced folder, with provider, confidence, evidence.

    Evidence comes from marker files in ancestor folders, provider environment variables, a
    Dropbox ``info.json`` and folder-name patterns. Confidence is ``high`` for markers and
    provider-owned locations, ``medium`` for a folder name alone. A report is a hint, not proof.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    given = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    try:
        resolved = given.resolve()
    except (OSError, RuntimeError):
        resolved = given
    spellings = [given] if given == resolved else [given, resolved]
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def note(provider: str, confidence: str, root: Path | str, evidence: str) -> None:
        entry = found.setdefault(
            (provider, str(root)),
            {
                'provider': provider,
                'provider_name': PROVIDERS[provider],
                'confidence': confidence,
                'evidence': [],
                'root': str(root),
            },
        )
        if _RANK[confidence] > _RANK[entry['confidence']]:
            entry['confidence'] = confidence
        if evidence not in entry['evidence']:
            entry['evidence'].append(evidence)

    for variable, provider in _ENV_ROOTS:
        value = env.get(variable)
        if value and any(_is_under(str(p), value) for p in spellings):
            note(provider, 'high', value, f'inside the folder named by the {variable} environment variable')
    for root, source in _dropbox_roots(env, home):
        if any(_is_under(str(p), root) for p in spellings):
            note('dropbox', 'high', root, f'inside the Dropbox folder recorded in {source}')
    for spelling in spellings:
        for folder in _ancestors(spelling):
            try:
                names = _scan_names(folder) if folder != spelling or folder.is_dir() else []
            except OSError:
                names = []
            lowered = {n.lower(): n for n in names}
            for provider, confidence, marker, description in _MARKERS:
                if marker in lowered:
                    note(provider, confidence, folder, f'{description} {lowered[marker]} in {folder}')
            for name in names:
                if re.fullmatch(r'\.?_?\.?sync_[0-9a-f]+\.db', name):
                    note(
                        'nextcloud', 'high', folder, f'Nextcloud or ownCloud sync database {name} in {folder}'
                    )
            if folder.name:
                for provider, confidence, pattern, description in _COMPILED_RULES:
                    if pattern.match(folder.name):
                        note(provider, confidence, folder, f'{description}: {folder}')
    candidates = sorted(
        found.values(),
        key=lambda c: (-_RANK[c['confidence']], -len(PurePath(c['root']).parts), c['provider']),
    )
    top = candidates[0] if candidates else None
    return {
        'synced': bool(top),
        'provider': top['provider'] if top else None,
        'provider_name': top['provider_name'] if top else None,
        'confidence': top['confidence'] if top else 'none',
        'evidence': list(top['evidence']) if top else [],
        'root': top['root'] if top else None,
        'candidates': candidates,
    }


def _scan_names(folder: Path) -> list[str]:
    names = []
    with os.scandir(folder) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) >= _SCAN_ENTRIES:
                break
    return names


# -- Placeholders, partial files and package identity -----------------------------------------


def placeholder_state(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Describe an online-only placeholder, zero-byte file or missing-but-announced file, if any.

    Opening an online-only file can make the sync client download it, so callers must check
    this before reading. ``None`` means the file looks like an ordinary local file.
    """
    target = Path(path)
    icloud = target.with_name('.' + target.name + '.icloud')
    try:
        if not target.exists():
            if icloud.exists():
                return {
                    'kind': 'icloud_placeholder',
                    'placeholder': str(icloud),
                    'message': 'The file is in iCloud Drive but not downloaded to this computer yet.',
                }
            return None
        status = target.stat()
    except OSError:
        return None
    attributes = getattr(status, 'st_file_attributes', 0)
    if attributes & (_ATTR_OFFLINE | _ATTR_RECALL_ON_OPEN | _ATTR_RECALL_ON_DATA_ACCESS):
        return {
            'kind': 'online_only',
            'message': 'The file is online-only (Files On-Demand) and not downloaded to this computer yet.',
        }
    if getattr(status, 'st_flags', 0) & _SF_DATALESS:
        return {
            'kind': 'online_only',
            'message': 'The file is online-only and not downloaded to this computer yet.',
        }
    if status.st_size == 0:
        return {
            'kind': 'zero_bytes',
            'message': 'The file is empty (0 bytes). A sync client may still be creating or downloading it.',
        }
    return None


def package_identity(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read only the manifest of a package: its document identity, without loading the content.

    States: ``ok`` (with ``document_id``/``revision``), ``pending`` (placeholder, empty or
    incomplete, so a sync is probably still running) and ``unreadable`` (not a ByAuthorX package).
    """
    held = placeholder_state(path)
    if held:
        return {'state': 'pending', 'reason': held['kind'], 'message': held['message']}
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo('manifest.json')
            if info.file_size > 1024 * 1024:
                return {'state': 'unreadable', 'reason': 'manifest_too_large'}
            manifest = json.loads(archive.read(info).decode('utf-8'))
    except FileNotFoundError:
        return {'state': 'unreadable', 'reason': 'missing', 'message': 'The file does not exist.'}
    except (zipfile.BadZipFile, EOFError):
        return {
            'state': 'pending',
            'reason': 'incomplete',
            'message': 'The file is not a complete package; it may still be syncing or may be damaged.',
        }
    except (OSError, KeyError, ValueError, RuntimeError, NotImplementedError):
        return {'state': 'unreadable', 'reason': 'not_a_package', 'message': 'Not a ByAuthorX package.'}
    if (
        not isinstance(manifest, dict)
        or manifest.get('format') != 'ByAuthorX'
        or not isinstance(manifest.get('document_id'), str)
    ):
        return {'state': 'unreadable', 'reason': 'not_a_package', 'message': 'Not a ByAuthorX package.'}
    return {
        'state': 'ok',
        'document_id': manifest['document_id'],
        'revision': manifest.get('revision'),
        'schema_version': (manifest.get('schema_versions') or {}).get('document'),
    }


# -- Conflicted copies ------------------------------------------------------------------------

_APOSTROPHE = "['’]"
# (pattern id, providers, name confidence, label, regex over the copy's stem). ``{s}`` is the
# escaped stem of the original document. First match wins, so distinctive patterns come first.
_COPY_PATTERNS = (
    (
        'conflicted_copy',
        ('dropbox',),
        'high',
        'Dropbox conflicted copy',
        r'{s} \((?:(?P<owner>.+?){apostrophe}s )conflicted copy (?P<stamp>\d{{4}}-\d{{2}}-\d{{2}})\)(?: \(\d+\))?',
    ),
    (
        'case_conflict',
        ('dropbox',),
        'high',
        'Dropbox case conflict',
        r'{s} \(Case Conflict(?: \d+)?\)',
    ),
    (
        'nextcloud_conflict',
        ('nextcloud',),
        'high',
        'Nextcloud conflicted copy',
        r'{s} \(conflicted copy (?P<stamp>\d{{4}}-\d{{2}}-\d{{2}}(?: \d{{6}})?)\)',
    ),
    (
        'owncloud_conflict',
        ('owncloud',),
        'high',
        'ownCloud conflict file',
        r'{s}_conflict-(?P<stamp>\d{{8}}-\d{{6}})(?:-\d+)?',
    ),
    (
        'syncthing_conflict',
        ('syncthing',),
        'high',
        'Syncthing sync conflict',
        r'{s}\.sync-conflict-(?P<stamp>\d{{8}}-\d{{6}})(?:-(?P<owner>[A-Z2-7]{{7}}))?',
    ),
    (
        'numbered_parentheses',
        ('google_drive', 'box'),
        'medium',
        'Google Drive or Box numbered copy',
        r'{s} \((?P<number>\d+)\)',
    ),
    ('numbered_space', ('icloud',), 'medium', 'iCloud numbered copy', r'{s} (?P<number>\d+)'),
    (
        'host_suffix',
        ('onedrive',),
        'low',
        'OneDrive computer-name copy',
        r'{s}-(?P<owner>(?=[A-Za-z0-9 _.\-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9 _.\-]{{0,62}}?)(?:-(?P<number>\d+))?',
    ),
)
_PLACEHOLDER = re.compile(r'^\.(?P<real>.+)\.icloud$')


def _copy_regexes(stem: str) -> list[tuple[str, tuple[str, ...], str, str, re.Pattern[str]]]:
    escaped = re.escape(stem)
    return [
        (
            pid,
            providers,
            confidence,
            label,
            re.compile(
                '^' + template.format(s=escaped, apostrophe=_APOSTROPHE) + '$',
                re.IGNORECASE if pid != 'syncthing_conflict' else 0,
            ),
        )
        for pid, providers, confidence, label, template in _COPY_PATTERNS
    ]


def _match_copy(base_stem: str, candidate_stem: str, regexes: list | None = None) -> dict[str, Any] | None:
    if len(candidate_stem) <= len(base_stem) or not candidate_stem.lower().startswith(base_stem.lower()):
        return None
    for pid, providers, confidence, label, regex in regexes or _copy_regexes(base_stem):
        match = regex.match(candidate_stem)
        if match:
            groups = match.groupdict()
            return {
                'pattern': pid,
                'provider': providers[0],
                'providers': list(providers),
                'pattern_label': label,
                'name_confidence': confidence,
                'owner': (groups.get('owner') or '').strip() or None,
                'stamp': groups.get('stamp'),
                'number': int(groups['number']) if groups.get('number') else None,
            }
    return None


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec='seconds')


def _describe_copy(
    directory: Path, listed: str, real: str, match: dict[str, Any], hint: str | None
) -> dict[str, Any]:
    path = directory / real
    entry: dict[str, Any] = {
        'path': str(path),
        'name': real,
        **match,
        'size': None,
        'modified': None,
    }
    placeholder = directory / listed
    if listed != real:
        entry.update(
            identity='pending', reason='icloud_placeholder', message='Not downloaded from iCloud yet.'
        )
        entry['placeholder'] = str(placeholder)
        return entry
    try:
        status = path.stat()
        entry.update(size=status.st_size, modified=_iso(status.st_mtime))
    except OSError:
        pass
    identity = package_identity(path)
    entry['identity'] = identity['state']
    if identity['state'] == 'ok':
        entry.update(document_id=identity['document_id'], revision=identity['revision'])
    else:
        entry.update(reason=identity.get('reason'), message=identity.get('message'))
    if hint and hint in entry['providers']:
        entry['name_confidence'] = 'high'
    return entry


def find_conflicted_copies(
    path: str | os.PathLike[str],
    *,
    analyze: bool = False,
    sync: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Conflicted copies beside a document, validated to share the document's identity.

    Names alone are never enough: a candidate is offered (``copies``) only when its manifest
    carries the same ``document_id``. Files still syncing (empty, placeholder or truncated) are
    listed under ``pending``; other documents with a similar name go to ``skipped``. With
    ``analyze`` each copy is loaded and compared with the document for its ``relation``:
    ``diverged``, ``ahead`` (contains this document's latest revision), ``behind`` (already
    contained), ``identical`` or ``unrelated_history`` (no common revision, cannot be merged).
    """
    document = Path(path)
    own = package_identity(document)
    result: dict[str, Any] = {
        'document': str(document),
        'document_id': own.get('document_id'),
        'revision': own.get('revision'),
        'copies': [],
        'pending': [],
        'skipped': [],
    }
    if own['state'] != 'ok':
        # Without the document's own identity nothing can be validated, so offer nothing.
        result['document_state'] = own
        return result
    hint = (sync or {}).get('provider')
    for listed, real, match in _candidates(document):
        entry = _describe_copy(document.parent, listed, real, match, hint)
        identity = entry['identity']
        if identity == 'pending':
            result['pending'].append(entry)
        elif identity == 'ok':
            if entry['document_id'] != own['document_id']:
                entry.update(identity='different_document', reason='different_document')
                result['skipped'].append(entry)
                continue
            entry['identity'] = 'same_document'
            entry['confidence'] = 'high' if entry['name_confidence'] == 'high' else 'medium'
            result['copies'].append(entry)
        else:
            result['skipped'].append(entry)
    for list_name in ('copies', 'pending', 'skipped'):
        result[list_name].sort(key=lambda e: (e.get('modified') or '', e['name']))
    if analyze:
        for entry in result['copies']:
            entry.update(_relation_to(document, Path(entry['path'])))
    return result


def _candidates(document: Path) -> list[tuple[str, str, dict[str, Any]]]:
    suffix = document.suffix.lower()
    try:
        names = _scan_names(document.parent)
    except OSError:
        return []
    regexes = _copy_regexes(document.stem)
    found = []
    for listed in sorted(names):
        placeholder = _PLACEHOLDER.match(listed)
        real = placeholder.group('real') if placeholder else listed
        if real == document.name or not real.lower().endswith(suffix):
            continue
        match = _match_copy(document.stem, real[: -len(suffix)], regexes)
        if match:
            found.append((listed, real, match))
    return found


def _relation_to(document: Path, copy_path: Path) -> dict[str, Any]:
    from . import engine, storage

    try:
        ours, _ = storage.open_document(document)
        theirs, _ = storage.open_document(copy_path)
    except ByaxError as exc:
        return {'relation': 'unknown', 'relation_error': exc.message}
    return document_relation(ours, theirs, engine)


def document_relation(ours: dict[str, Any], theirs: dict[str, Any], engine: Any = None) -> dict[str, Any]:
    """How a copy relates to a document through retained revision ancestry."""
    if engine is None:
        from . import engine
    if ours['id'] != theirs['id']:
        return {'relation': 'different_document'}
    if ours['revision'] == theirs['revision']:
        return {'relation': 'identical'}
    mine, other = engine.revision_ancestors(ours), engine.revision_ancestors(theirs)
    if theirs['revision'] in mine:
        return {'relation': 'behind'}
    if ours['revision'] in other:
        return {'relation': 'ahead', 'merge_base': ours['revision']}
    mine_set = set(mine)
    base = next((r for r in other if r in mine_set), None)
    if base is None:
        return {'relation': 'unrelated_history'}
    return {'relation': 'diverged', 'merge_base': base}


# -- Risk report ------------------------------------------------------------------------------


def _risk(code: str, severity: str, message: str, advice: str = '', **extra: Any) -> dict[str, Any]:
    return {'code': code, 'severity': severity, 'message': message, 'advice': advice, **extra}


def _staging_files(document: Path) -> list[Path]:
    prefix, suffix = '.' + document.name + '.', '.tmp'
    try:
        names = _scan_names(document.parent)
    except OSError:
        return []
    return sorted(
        document.parent / n
        for n in names
        if (n.startswith(prefix) and n.endswith(suffix))
        or n in (document.name + '.tmp', document.name + '.part', document.name + '.partial')
        or n.lower().endswith(document.name.lower() + '.crdownload')
    )


def _beside_files(document: Path) -> list[tuple[str, Path]]:
    """Backup, autosave and other-application lock files named after the document."""
    name, stem = document.name, document.stem
    patterns = (
        ('backup', re.compile(re.escape(name) + r'(\.bak|~|\.backup|\.autosave)$', re.IGNORECASE)),
        ('backup', re.compile(r'^(Backup of |Autosave of )' + re.escape(name) + '$', re.IGNORECASE)),
        (
            'preserved_original',
            re.compile(re.escape(stem) + r'\.schema\d+-[0-9a-f]{12}' + re.escape(document.suffix) + '$'),
        ),
        ('other_app_lock', re.compile(r'^(~\$|\.~lock\.)' + re.escape(name) + '#?$')),
        ('other_app_lock', re.compile(r'^\.' + re.escape(name) + r'\.(swp|swo)$')),
    )
    try:
        names = _scan_names(document.parent)
    except OSError:
        return []
    found = []
    for listed in sorted(names):
        for kind, pattern in patterns:
            if pattern.search(listed):
                found.append((kind, document.parent / listed))
                break
    return found


def lock_state(path: str | os.PathLike[str]) -> str:
    """``free`` or ``held`` for the advisory editing lock; the lock file is created in the user cache."""
    from .storage import DocumentLock

    try:
        with DocumentLock(path):
            return 'free'
    except ByaxError as exc:
        if exc.code == 'locked':
            return 'held'
        raise


def os_lock_state(path: str | os.PathLike[str]) -> str:
    """Whether another program has the file open in a way that blocks writing (Windows sync clients).

    Opens read/write without changing anything. ``unknown`` on platforms where an open never
    reveals other programs' handles; their sync clients rely on rename-and-replace instead.
    """
    try:
        with open(path, 'r+b'):
            return 'free'
    except PermissionError:
        return 'blocked' if os.name == 'nt' else 'read_only'
    except OSError:
        return 'unknown'


def _history_load(document: Path) -> dict[str, Any] | None:
    from . import storage

    try:
        doc, _ = storage.open_document(document)
    except ByaxError:
        return None
    records = doc['history'].get('revisions', [])
    total = sum(r.get('bytes') or 0 for r in records)
    return {'revisions': len(records), 'bytes': total}


def sync_risks(
    path: str | os.PathLike[str],
    sync: Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    light: bool = False,
) -> list[dict[str, Any]]:
    """Risky patterns for one document. ``warning`` risks matter; ``info`` ones explain behavior.

    ``light`` skips the checks that load the whole document or probe its lock, for callers (the
    desktop window) that already hold the document open.
    """
    document = Path(path)
    sync = sync if sync is not None else detect_sync_folder(document, env=env, home=home)
    synced = bool(sync.get('synced'))
    risks: list[dict[str, Any]] = []
    held = placeholder_state(document)
    if held:
        risks.append(
            _risk(
                'file_not_available',
                'warning',
                held['message'],
                'Wait for the sync client to finish, or make the file available offline, then open it again.',
                path=str(document),
                kind=held['kind'],
            )
        )
    for staged in _staging_files(document):
        try:
            info = staged.stat()
            extra = {'size': info.st_size, 'modified': _iso(info.st_mtime)}
        except OSError:
            extra = {}
        risks.append(
            _risk(
                'staging_file_left_behind',
                'warning' if synced else 'info',
                f'A temporary save file {staged.name} was left beside the document.',
                'It comes from a save or download that was interrupted. Once no ByAuthorX window has the '
                'document open and sync has finished, it is safe to delete.',
                path=str(staged),
                **extra,
            )
        )
    for kind, other in _beside_files(document):
        if kind == 'other_app_lock':
            risks.append(
                _risk(
                    'other_application_lock',
                    'warning',
                    f'{other.name} suggests another application has this file open.',
                    'Close it there before editing here.',
                    path=str(other),
                )
            )
        elif kind == 'backup':
            risks.append(
                _risk(
                    'backup_in_synced_folder' if synced else 'backup_file',
                    'warning' if synced else 'info',
                    f'A backup or autosave file {other.name} sits beside the document.',
                    'Backup copies in a synced folder are uploaded and can later appear as conflicts; '
                    'keep them elsewhere.',
                    path=str(other),
                )
            )
        else:
            risks.append(
                _risk(
                    'preserved_original',
                    'info',
                    f'{other.name} is a byte-for-byte copy kept when this document was upgraded.',
                    'Keep it until you are sure the upgrade is correct.',
                    path=str(other),
                )
            )
    identity = package_identity(document) if document.exists() and not held else {'state': 'skipped'}
    if identity['state'] == 'pending':
        risks.append(
            _risk(
                'document_incomplete',
                'warning',
                identity['message'],
                'Do not edit or save over it. Wait for sync to finish; if it stays this way, restore the '
                "file from the sync service's version history or from an autosave recovery copy.",
                path=str(document),
            )
        )
    if identity['state'] == 'ok':
        if document.stat().st_size >= LARGE_PACKAGE_BYTES:
            risks.append(
                _risk(
                    'large_package',
                    'warning' if synced else 'info',
                    f'The document is {document.stat().st_size // (1024 * 1024)} MiB; every save uploads the whole file.',
                    'Large files take longer to sync, which widens the window for conflicts.',
                    path=str(document),
                )
            )
        history = None if light else _history_load(document)
        if history and (
            history['bytes'] >= LARGE_HISTORY_BYTES or history['revisions'] >= LARGE_HISTORY_REVISIONS
        ):
            risks.append(
                _risk(
                    'large_history',
                    'warning' if synced else 'info',
                    f'The saved history holds {history["revisions"]} revisions '
                    f'({history["bytes"] // 1024} KiB), re-uploaded with each save.',
                    'Compact history (the compact_history operation) or name the revisions you want to keep.',
                    **history,
                )
            )
        if os_lock_state(document) == 'blocked':
            risks.append(
                _risk(
                    'file_open_elsewhere',
                    'warning',
                    'Another program has the file open (often a sync client busy uploading it).',
                    'Wait a moment and try again.',
                    path=str(document),
                )
            )
        if not light and lock_state(document) == 'held':
            risks.append(
                _risk(
                    'open_in_another_session',
                    'info',
                    'The document is open in another ByAuthorX window on this computer.',
                    'Close it there before editing from the command line.',
                    path=str(document),
                )
            )
    cache = user_cache_path('ByAuthorX') / 'locks'
    recovery = user_data_path('ByAuthorX') / 'recovery'
    for code, folder, what in (
        ('lock_folder_in_synced_folder', cache, 'The editing-lock folder'),
        ('recovery_folder_in_synced_folder', recovery, 'The autosave recovery folder'),
    ):
        report = detect_sync_folder(folder, env=env, home=home)
        if report['synced']:
            risks.append(
                _risk(
                    code,
                    'warning',
                    f'{what} is inside a {report["provider_name"]} folder.',
                    'Locks and autosave copies would sync between computers and can corrupt each other. '
                    'Point XDG_CACHE_HOME or XDG_DATA_HOME (or the equivalent) outside the synced folder.',
                    path=str(folder),
                    provider=report['provider'],
                )
            )
    if synced:
        risks.append(
            _risk(
                'lock_is_per_computer',
                'info',
                'The editing lock is stored on this computer only, so it cannot stop another computer '
                'from editing the same synced file.',
                'Close the document and let sync finish before switching computers. If two computers do '
                'edit it, the sync client keeps a conflicted copy that can be merged from here.',
            )
        )
    return risks


# -- Status -----------------------------------------------------------------------------------


def sync_status(
    path: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    analyze: bool = False,
    light: bool = False,
) -> dict[str, Any]:
    """Sync report for a document or a folder of documents (``light``: see :func:`sync_risks`)."""
    target = Path(path)
    if target.is_dir():
        return _folder_status(target, env=env, home=home, analyze=analyze)
    if not target.exists() and not placeholder_state(target):
        raise ByaxError('missing_target', 'The document does not exist.', target=str(target))
    sync = detect_sync_folder(target, env=env, home=home)
    copies = find_conflicted_copies(target, analyze=analyze, sync=sync)
    risks = sync_risks(target, sync, env=env, home=home, light=light)
    if copies['pending']:
        risks.append(
            _risk(
                'conflicted_copy_still_syncing',
                'info',
                f'{len(copies["pending"])} possible conflicted copy file(s) are not fully downloaded yet.',
                'Wait for sync to finish, then check again.',
            )
        )
    return {
        'kind': 'document',
        'path': str(target),
        'sync': sync,
        'risks': risks,
        'conflicts': copies,
        'attention': _attention(sync, risks, copies),
    }


def _attention(sync: Mapping[str, Any], risks: list[dict[str, Any]], copies: Mapping[str, Any]) -> bool:
    return (
        bool(copies['copies'])
        or (bool(sync.get('synced')) and any(risk['severity'] == 'warning' for risk in risks))
        or any(risk['code'] in ('file_not_available', 'document_incomplete') for risk in risks)
    )


def _folder_status(
    folder: Path, *, env: Mapping[str, str] | None, home: Path | None, analyze: bool
) -> dict[str, Any]:
    sync = detect_sync_folder(folder, env=env, home=home)
    try:
        names = sorted(_scan_names(folder))
    except OSError as exc:
        raise ByaxError(
            'invalid_input', f'Cannot list folder: {exc.strerror or exc}', target=str(folder)
        ) from exc
    documents = [n for n in names if n.lower().endswith('.byax') and not n.startswith('.')]
    truncated = len(documents) > MAX_FOLDER_ENTRIES
    documents = documents[:MAX_FOLDER_ENTRIES]
    claimed: set[str] = set()
    reports = []
    for name in documents:
        copies = find_conflicted_copies(folder / name, analyze=analyze, sync=sync)
        if copies['copies'] or copies['pending']:
            claimed.update(Path(e['path']).name for e in copies['copies'] + copies['pending'])
        reports.append((name, copies))
    summary = [
        {
            'path': str(folder / name),
            'conflicted_copies': len(copies['copies']),
            'pending': len(copies['pending']),
            'copies': copies['copies'],
        }
        for name, copies in reports
        if name not in claimed and (copies['copies'] or copies['pending'])
    ]
    risks = []
    for code, location, what in (
        ('lock_folder_in_synced_folder', user_cache_path('ByAuthorX') / 'locks', 'The editing-lock folder'),
        (
            'recovery_folder_in_synced_folder',
            user_data_path('ByAuthorX') / 'recovery',
            'The autosave recovery folder',
        ),
    ):
        report = detect_sync_folder(location, env=env, home=home)
        if report['synced']:
            risks.append(
                _risk(
                    code,
                    'warning',
                    f'{what} is inside a {report["provider_name"]} folder.',
                    path=str(location),
                )
            )
    leftovers = [str(folder / n) for n in names if n.startswith('.') and n.endswith('.tmp') and '.byax.' in n]
    if leftovers:
        risks.append(
            _risk(
                'staging_file_left_behind',
                'warning' if sync['synced'] else 'info',
                f'{len(leftovers)} temporary save file(s) were left in the folder.',
                'They come from interrupted saves; delete them once nothing has the documents open.',
                paths=leftovers,
            )
        )
    return {
        'kind': 'folder',
        'path': str(folder),
        'sync': sync,
        'risks': risks,
        'documents': len([n for n in documents if n not in claimed]),
        'documents_with_conflicts': summary,
        'truncated': truncated,
        'attention': bool(summary) or (sync['synced'] and any(r['severity'] == 'warning' for r in risks)),
    }


# -- Merge and archive ------------------------------------------------------------------------


def merge_branch_name(revision: str) -> str:
    """Deterministic branch name for a conflicted copy, so repeating a merge reuses its branch."""
    return 'conflicted-copy-' + re.sub(r'[^0-9a-zA-Z]', '', revision)[-8:]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_merge(
    doc: dict[str, Any], copy_path: str | os.PathLike[str], *, name: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Import a conflicted copy as a branch of ``doc``; returns ``(candidate, info)``.

    The caller then proposes the merge (``propose_merge`` with ``info['branch']``) so the
    user reviews it. Refuses a file that is not a complete package of this same document, or
    that adds nothing. ``info['already_imported']`` is true when the copy is already a branch.
    """
    from . import engine, storage

    source = Path(copy_path)
    identity = package_identity(source)
    if identity['state'] == 'pending':
        raise ByaxError(
            'pending',
            f'The conflicted copy is not ready: {identity["message"]}',
            target=str(source),
            retry='Wait for the sync client to finish, then try again.',
        )
    if identity['state'] != 'ok':
        raise ByaxError(
            'invalid_input', identity.get('message') or 'Not a ByAuthorX package.', target=str(source)
        )
    if identity['document_id'] != doc['id']:
        raise ByaxError(
            'conflict', 'The file is a different document and cannot be merged.', target=str(source)
        )
    other, _ = storage.open_document(source)
    relation = document_relation(doc, other, engine)
    kind = relation['relation']
    if kind in ('identical', 'behind'):
        raise ByaxError(
            'conflict',
            'The conflicted copy has nothing new: its latest revision is already part of this document.',
            target=str(source),
            relation=kind,
        )
    if kind == 'unrelated_history':
        raise ByaxError(
            'missing_target',
            'The copy shares no retained revision with this document, so a safe three-way merge is not possible. '
            'Compare the files manually; the copy has not been changed.',
            target=str(source),
            relation=kind,
        )
    branch = name or merge_branch_name(other['revision'])
    info = {
        'branch': branch,
        'relation': kind,
        'copy_revision': other['revision'],
        'copy_sha256': _sha256(source),
        'already_imported': False,
    }
    if doc['history']['branches'].get(branch) == other['revision']:
        info.update(already_imported=True, merge_base=relation.get('merge_base'))
        return doc, info
    candidate, result = engine.import_branch(doc, other, branch)
    history = candidate['history']
    history['provenance'] = {
        **history.get('provenance', {}),
        other['revision']: {
            **history.get('provenance', {}).get(other['revision'], {}),
            'imported_from': {'file': source.name, 'sha256': info['copy_sha256']},
        },
    }
    info.update(merge_base=result['merge_base'], imported_revisions=result['imported_revisions'])
    return candidate, info


def propose_conflicted_copy_merge(
    doc: dict[str, Any],
    copy_path: str | os.PathLike[str],
    *,
    name: str | None = None,
    author: str | None = None,
    document_path: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Import the copy and register the merge as a pending proposal, for CLI and library callers.

    Returns ``(document, result)``; ``result['info']`` describes the branch, the proposal and its
    conflicts. On a dry run the original document comes back unchanged. Repeating the call for a
    copy that already has a pending merge proposal changes nothing and names that proposal.
    """
    from . import engine

    candidate, info = prepare_merge(doc, copy_path, name=name)
    existing = next(
        (
            p
            for p in candidate['review']['proposals']
            if p.get('kind') == 'merge'
            and p.get('status') == 'pending'
            and p.get('merge', {}).get('source_revision') == info['copy_revision']
        ),
        None,
    )
    if existing:
        info.update(
            proposal=existing['id'], conflicts=len(existing.get('conflicts', [])), already_proposed=True
        )
        return doc, {'info': info, 'changed_ids': []}
    pattern = _match_copy(Path(document_path).stem, Path(copy_path).stem) if document_path else None
    operation: dict[str, Any] = {
        'type': 'propose_merge',
        'source': info['branch'],
        'explanation': f'Merge conflicted copy "{Path(copy_path).name}"'
        + (f' ({pattern["pattern_label"]})' if pattern else '')
        + f' into {candidate["history"].get("current_branch", "main")}',
    }
    if author:
        operation['author'] = author
    merged, result = engine.apply(
        candidate, {'expected_revision': candidate['revision'], 'operations': [operation]}, dry_run=dry_run
    )
    proposal = next((p for p in reversed(merged['review']['proposals']) if p.get('kind') == 'merge'), None)
    if proposal is not None:
        info.update(
            proposal=proposal['id'],
            conflicts=len(proposal.get('conflicts', [])),
            operations=len(proposal.get('operations', [])),
            not_merged=proposal['merge'].get('not_merged', []),
            already_proposed=False,
        )
    return (doc if dry_run else merged), {'info': info, **result}


def archive_conflicted_copy(
    path: str | os.PathLike[str], copy_path: str | os.PathLike[str]
) -> dict[str, Any]:
    """Move an already merged conflicted copy into a ``ByAuthorX conflicted copies`` folder.

    This is an explicit action and never runs automatically. It refuses unless the document's
    history shows the copy's latest revision was merged (an accepted merge makes it an ancestor of
    the document), so unmerged work is never moved away. The file is moved, not deleted, and an
    existing archived file is never overwritten.
    """
    from . import engine, storage

    document, source = Path(path), Path(copy_path)
    identity = package_identity(source)
    if identity['state'] != 'ok':
        raise ByaxError(
            'invalid_input',
            identity.get('message') or 'The copy is not a complete package.',
            target=str(source),
        )
    doc, _ = storage.open_document(document)
    if identity['document_id'] != doc['id']:
        raise ByaxError('conflict', 'The file is a different document.', target=str(source))
    if identity['revision'] not in engine.revision_ancestors(doc):
        raise ByaxError(
            'conflict',
            'The conflicted copy has not been merged into this document yet; accept its merge proposal first.',
            target=str(source),
        )
    folder = source.parent / ARCHIVE_FOLDER
    folder.mkdir(exist_ok=True)
    destination = folder / source.name
    counter = 1
    while destination.exists():
        counter += 1
        destination = folder / f'{source.stem} ({counter}){source.suffix}'
    os.rename(source, destination)
    return {'moved_from': str(source), 'moved_to': str(destination), 'archive_folder': str(folder)}


# -- Command ----------------------------------------------------------------------------------


def sync_command(a: dict[str, Any], owner: str | None) -> dict[str, Any]:
    """The ``sync`` command shared by CLI, MCP and REST: status, conflicts, merge-conflict, archive-conflict."""
    from .cli import _error, _mutate, envelope

    action = a.get('action', 'status')
    if action == 'status':
        return envelope(sync_status(a['path']))
    if action == 'conflicts':
        document = Path(a['path'])
        if document.is_dir():
            return envelope(_folder_status(document, env=None, home=None, analyze=True))
        sync = detect_sync_folder(document)
        return envelope(find_conflicted_copies(document, analyze=True, sync=sync))
    if action in ('merge-conflict', 'archive-conflict') and not a.get('copy_path'):
        _error('invalid_input', f'sync {action} requires --copy-path', field='copy_path')
    if action == 'merge-conflict':

        def merge(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bool]:
            if a.get('expect_revision') and doc['revision'] != a['expect_revision']:
                _error(
                    'stale_revision',
                    'Document revision changed',
                    expected=a['expect_revision'],
                    actual=doc['revision'],
                )
            updated, result = propose_conflicted_copy_merge(
                doc,
                a['copy_path'],
                name=a.get('name'),
                author=a.get('author'),
                document_path=a['path'],
                dry_run=a.get('dry_run', False),
            )
            info = result.pop('info')
            changed = updated is not doc
            return updated, {**result, 'sync': info, 'changed_ids': result.get('changed_ids', [])}, changed

        return _mutate(a['path'], merge)
    if action == 'archive-conflict':
        return envelope(archive_conflicted_copy(a['path'], a['copy_path']))
    _error('invalid_input', f'Unknown sync action: {action}', field='action')
