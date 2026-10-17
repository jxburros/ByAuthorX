"""Extension layer (competitiveness plan 7.4): out-of-process, versioned, scoped.

An extension is a folder (or ZIP) with a ``byax-extension.json`` manifest and the files it names. It
adds one or both of two things to the application, and nothing else:

* **Checks** that enter the normal findings pipeline under the family ``extension:<id>`` (suite
  ``extension``). A *data* check is a JSON file of word lists and regular expressions that this
  module evaluates in-process (data is not code). A *script* check is a program the extension ships;
  it runs in a separate, resource-limited process, receives the document text as JSON on stdin and
  returns findings JSON on stdout, which is validated strictly before any of it is used.
* **Export hooks** that run after an export, out of process, on a read-only copy of the exported file
  plus the loss report, and may write extra files into a staging folder; only those files are copied
  to the output folder you chose. The master document is never given to a hook.

There is no in-process plugin loading. Extensions are installed into the user data directory only by
an explicit local action that shows the SHA-256 and the requested scopes, and are installed disabled.
Installing, enabling, disabling and removing are administrative actions refused over MCP and REST.
An extension that needs more than the stdin JSON talks to the authenticated REST service with a token
limited to the scopes it declared (``service.issue_extension_token``).

Extensions are *not sandboxed*: a script runs as you, with a memory, CPU, wall-clock and output limit.
Installing one is trusting its author. See ``docs/extensions.md``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import regex

from . import grammar
from .errors import ByaxError
from .model import object_hash

API_VERSION = 1
SUPPORTED_API_VERSIONS = (1,)
MANIFEST_NAME = 'byax-extension.json'
VERSION = 1  # bumped when the evaluation of extension checks changes (part of check fingerprints)
FAMILY_PREFIX = 'extension:'
SUITE = 'extension'

SCOPES = {
    'read': 'Receive the text of the document (blocks and table cells) as JSON; nothing else of the document.',
    'checks': 'Add findings to the document checks under the family extension:<id>.',
    'propose': 'Offer a replacement with a finding, or submit proposals through the REST service. Proposals only: never direct edits.',
    'export-hook': 'Run after an export on a read-only copy of the exported file and its loss report.',
}
KINDS = ('check', 'export-hook')
SEVERITIES = ('advisory', 'warning')  # extensions can never raise an error that blocks a gate
RUNTIMES = ('python', 'executable')

MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 64
MAX_MANIFEST_BYTES = 64 * 1024
MAX_FINDINGS = 1000
MAX_MESSAGE = 500
MAX_REPLACEMENT = 2000
MAX_STDIN_BYTES = 32 * 1024 * 1024
MAX_CHECK_OUTPUT = 4 * 1024 * 1024
MAX_HOOK_STDOUT = 64 * 1024
MAX_HOOK_FILES = 20
MAX_HOOK_FILE_BYTES = 16 * 1024 * 1024
MAX_LOSS_REPORT_BYTES = 1024 * 1024
DEFAULT_TIMEOUT = 20
MAX_TIMEOUT = 120
MEMORY_LIMIT = 512 * 1024**2
MATCH_TIMEOUT = 0.5
# Inputs that make nested quantifiers backtrack catastrophically (a pattern must survive them).
_PROBES = ('a' * 3000 + '!', 'ab' * 1500 + '!', ('word ' * 600) + '!', ' ' * 3000 + 'x', '1' * 3000 + '/')

_ID = re.compile(r'[a-z0-9][a-z0-9-]{1,47}')
_ENTRY_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,47}')
_VERSION = re.compile(r'\d+(?:\.\d+){0,2}')
_PATH = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*')
_OUTPUT_NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_. -]{0,119}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_MANIFEST_KEYS = {
    'id',
    'name',
    'version',
    'api_version',
    'description',
    'author',
    'licence',
    'homepage',
    'kinds',
    'scopes',
    'entry_points',
    'min_byax_version',
}
_ENTRY_POINT_KEYS = {'checks', 'export_hooks'}
_CHECK_KEYS = {'id', 'type', 'path', 'title', 'description', 'runtime', 'timeout', 'options', 'rules'}
_HOOK_KEYS = {'id', 'path', 'title', 'description', 'runtime', 'timeout', 'formats'}
_FINDING_KEYS = {'rule', 'target', 'start', 'end', 'severity', 'message', 'replacement', 'measured'}

# Restricts check evaluation to one extension while ``run-check`` is developing it.
ONLY: ContextVar[tuple[str, str | None] | None] = ContextVar('byax_extension_only', default=None)


class ExtensionFailure(Exception):
    """A script or hook run that produced nothing usable; ``kind`` says why."""

    def __init__(self, kind: str, message: str, **details: Any):
        super().__init__(message)
        self.kind, self.message, self.details = kind, message, details


def _invalid(message: str, **details: Any):
    raise ByaxError('invalid_input', message, **details)


def data_root() -> Path:
    """Folder holding installed extensions: ``BYAX_EXTENSIONS_DIR`` or the user data directory."""
    override = os.environ.get('BYAX_EXTENSIONS_DIR')
    if override:
        return Path(override)
    from platformdirs import user_data_path

    return user_data_path('ByAuthorX', appauthor=False) / 'extensions'


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _version_key(version: str) -> tuple[int, ...]:
    parts = [int(p) for p in version.split('.')]
    return tuple(parts + [0] * (3 - len(parts)))


# Manifest ---------------------------------------------------------------------------------
def _text(manifest: dict, key: str, limit: int, *, required: bool = True) -> str:
    value = manifest.get(key, '' if not required else None)
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        _invalid(f'Manifest {key} is missing or too long', field=key)
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        _invalid(f'Manifest {key} contains control characters', field=key)
    return value.strip()


def _entry_path(entry: dict, field: str) -> str:
    path = entry.get('path')
    if not isinstance(path, str) or not _PATH.fullmatch(path) or '..' in path.split('/') or len(path) > 120:
        _invalid('Entry point path is not a safe relative path', field=field, path=path)
    if path == MANIFEST_NAME:
        _invalid('The manifest cannot be an entry point', field=field)
    return path


def _timeout(entry: dict, field: str) -> int:
    value = entry.get('timeout', DEFAULT_TIMEOUT)
    if type(value) is not int or not 1 <= value <= MAX_TIMEOUT:
        _invalid(f'timeout must be a whole number of seconds from 1 to {MAX_TIMEOUT}', field=field)
    return value


def _runtime(entry: dict, path: str, field: str) -> str:
    runtime = entry.get('runtime', 'python')
    if runtime not in RUNTIMES:
        _invalid('Unknown runtime', field=field, supported=list(RUNTIMES))
    if runtime == 'python' and not path.endswith('.py'):
        _invalid('A python entry point must be a .py file', field=field, path=path)
    return runtime


def _titles(entry: dict, field: str) -> tuple[str, str]:
    title, description = entry.get('title', entry['id']), entry.get('description', '')
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        _invalid('Entry point title is required text', field=field)
    if not isinstance(description, str) or len(description) > 500:
        _invalid('Entry point description must be short text', field=field)
    return title.strip(), description


def validate_manifest(manifest: Any) -> dict:
    """Normalized manifest or ``invalid_input`` naming the first problem (strict: unknown keys fail)."""
    if not isinstance(manifest, dict):
        _invalid('Extension manifest must be a JSON object')
    unknown = sorted(set(manifest) - _MANIFEST_KEYS)
    if unknown:
        _invalid('Unknown manifest keys', field=unknown[0], keys=unknown)
    api = manifest.get('api_version')
    if type(api) is not int:
        _invalid('api_version is required and must be a whole number', field='api_version')
    if api not in SUPPORTED_API_VERSIONS:
        raise ByaxError(
            'unsupported_version',
            f'This version of ByAuthorX supports extension api_version {list(SUPPORTED_API_VERSIONS)}, not {api}',
            field='api_version',
            supported=list(SUPPORTED_API_VERSIONS),
        )
    result: dict[str, Any] = {'api_version': api}
    identifier = manifest.get('id')
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        _invalid('id must be 2 to 48 lowercase letters, digits and hyphens', field='id')
    result['id'] = identifier
    version = manifest.get('version')
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        _invalid('version must look like 1, 1.2 or 1.2.3', field='version')
    result['version'] = version
    result['name'] = _text(manifest, 'name', 100)
    result['description'] = _text(manifest, 'description', 2000)
    result['author'] = _text(manifest, 'author', 200)
    result['licence'] = _text(manifest, 'licence', 100)
    homepage = manifest.get('homepage')
    if homepage is not None:
        if not isinstance(homepage, str) or not re.fullmatch(r'https?://[^\s]{1,300}', homepage):
            _invalid('homepage must be an http or https address', field='homepage')
        result['homepage'] = homepage  # shown, never fetched
    minimum = manifest.get('min_byax_version')
    if minimum is not None:
        if not isinstance(minimum, str) or not _VERSION.fullmatch(minimum):
            _invalid('min_byax_version must look like 0.3 or 0.3.0', field='min_byax_version')
        result['min_byax_version'] = minimum
    kinds, scopes = manifest.get('kinds'), manifest.get('scopes')
    if (
        not isinstance(kinds, list)
        or not kinds
        or any(k not in KINDS for k in kinds)
        or len(set(kinds)) != len(kinds)
    ):
        _invalid('kinds must list check and/or export-hook once each', field='kinds', supported=list(KINDS))
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(s not in SCOPES for s in scopes)
        or len(set(scopes)) != len(scopes)
    ):
        _invalid('scopes must list known scopes once each', field='scopes', supported=sorted(SCOPES))
    result['kinds'], result['scopes'] = sorted(kinds), sorted(scopes)
    entry_points = manifest.get('entry_points')
    if not isinstance(entry_points, dict) or set(entry_points) - _ENTRY_POINT_KEYS:
        _invalid('entry_points holds checks and export_hooks only', field='entry_points')
    checks, hooks, seen = [], [], set()
    for index, entry in enumerate(entry_points.get('checks', [])):
        field = f'entry_points.checks[{index}]'
        if not isinstance(entry, dict) or set(entry) - _CHECK_KEYS:
            _invalid('A check entry has unknown keys', field=field)
        if not isinstance(entry.get('id'), str) or not _ENTRY_ID.fullmatch(entry['id']):
            _invalid('Check id must be lowercase letters, digits, hyphens and underscores', field=field)
        if ('check', entry['id']) in seen:
            _invalid('Duplicate check id', field=field)
        seen.add(('check', entry['id']))
        if entry.get('type') not in ('data', 'script'):
            _invalid('Check type is data or script', field=field)
        path = _entry_path(entry, field)
        title, description = _titles(entry, field)
        normalized = {
            'id': entry['id'],
            'type': entry['type'],
            'path': path,
            'title': title,
            'description': description,
        }
        if entry['type'] == 'data':
            if path[-5:] != '.json' or set(entry) & {'runtime', 'timeout', 'options', 'rules'}:
                _invalid(
                    'A data check is a .json file with no runtime, timeout, options or rules', field=field
                )
        else:
            normalized['runtime'] = _runtime(entry, path, field)
            normalized['timeout'] = _timeout(entry, field)
            options = entry.get('options', {})
            if not isinstance(options, dict) or len(json.dumps(options)) > 4096:
                _invalid('Check options must be a small JSON object', field=field)
            normalized['options'] = options
            rules = entry.get('rules')
            if rules is not None:
                if (
                    not isinstance(rules, list)
                    or not rules
                    or len(rules) > 100
                    or any(not isinstance(r, str) or not _ENTRY_ID.fullmatch(r) for r in rules)
                ):
                    _invalid('Declared rules are a list of rule ids', field=field)
                normalized['rules'] = sorted(set(rules))
        checks.append(normalized)
    for index, entry in enumerate(entry_points.get('export_hooks', [])):
        field = f'entry_points.export_hooks[{index}]'
        if not isinstance(entry, dict) or set(entry) - _HOOK_KEYS:
            _invalid('A hook entry has unknown keys', field=field)
        if not isinstance(entry.get('id'), str) or not _ENTRY_ID.fullmatch(entry['id']):
            _invalid('Hook id must be lowercase letters, digits, hyphens and underscores', field=field)
        if ('hook', entry['id']) in seen:
            _invalid('Duplicate hook id', field=field)
        seen.add(('hook', entry['id']))
        path = _entry_path(entry, field)
        title, description = _titles(entry, field)
        formats = entry.get('formats')
        if formats is not None and (
            not isinstance(formats, list)
            or not formats
            or any(not isinstance(f, str) or not re.fullmatch(r'[a-z0-9]{1,10}', f) for f in formats)
        ):
            _invalid('formats lists export formats such as docx or pdf', field=field)
        hooks.append(
            {
                'id': entry['id'],
                'path': path,
                'title': title,
                'description': description,
                'runtime': _runtime(entry, path, field),
                'timeout': _timeout(entry, field),
                'formats': sorted(set(formats)) if formats else None,
            }
        )
    if len(checks) + len(hooks) > 32:
        _invalid('An extension may declare at most 32 entry points', field='entry_points')
    if bool(checks) != ('check' in kinds) or bool(hooks) != ('export-hook' in kinds):
        _invalid('kinds must match the declared entry points', field='kinds')
    if checks and 'checks' not in scopes:
        _invalid('Checks need the checks scope', field='scopes')
    if any(c['type'] == 'script' for c in checks) and 'read' not in scopes:
        _invalid('A script check receives document text and needs the read scope', field='scopes')
    if hooks and 'export-hook' not in scopes:
        _invalid('Export hooks need the export-hook scope', field='scopes')
    result['entry_points'] = {'checks': checks, 'export_hooks': hooks}
    return result


# Data checks ------------------------------------------------------------------------------
def validate_data_check(value: Any, *, propose: bool, path: str = 'check') -> list[dict]:
    """Validate a data check file and return its rules with compiled-safe patterns checked."""
    if not isinstance(value, dict) or set(value) - {'rules', 'description'}:
        _invalid('A data check is {"rules": [...]}', path=path)
    rules = value.get('rules')
    if not isinstance(rules, list) or not rules or len(rules) > 500:
        _invalid('A data check needs between 1 and 500 rules', path=path)
    seen, result = set(), []
    for entry in rules:
        if not isinstance(entry, dict):
            _invalid('Data check rules are objects', path=path)
        allowed = {'id', 'kind', 'message', 'severity', 'languages', 'title'}
        kind = entry.get('kind')
        if kind == 'words':
            allowed |= {'words', 'case_sensitive', 'whole_word', 'replacements'}
        elif kind == 'regex':
            allowed |= {'pattern', 'flags', 'replacement'}
        else:
            _invalid('Rule kind is words or regex', path=path, rule=entry.get('id'))
        if set(entry) - allowed:
            _invalid('Unknown rule keys', path=path, rule=entry.get('id'), keys=sorted(set(entry) - allowed))
        identifier = entry.get('id')
        if not isinstance(identifier, str) or not _ENTRY_ID.fullmatch(identifier) or identifier in seen:
            _invalid('Rule id is missing, malformed or repeated', path=path, rule=identifier)
        seen.add(identifier)
        message = entry.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > 300:
            _invalid('Each rule needs a message of up to 300 characters', path=path, rule=identifier)
        if entry.get('severity', 'advisory') not in SEVERITIES:
            _invalid('Severity is advisory or warning', path=path, rule=identifier)
        languages = entry.get('languages')
        if languages is not None and (
            not isinstance(languages, list)
            or any(not isinstance(x, str) or not re.fullmatch(r'[a-z]{2,3}', x) for x in languages)
        ):
            _invalid('languages lists language codes such as en', path=path, rule=identifier)
        title = entry.get('title', identifier)
        if not isinstance(title, str) or len(title) > 100:
            _invalid('title must be short text', path=path, rule=identifier)
        compiled = compile_rule(entry, path)
        replaces = entry.get('replacement') is not None or bool(entry.get('replacements'))
        if replaces and not propose:
            _invalid('Offering replacements needs the propose scope', path=path, rule=identifier)
        result.append(dict(entry, _compiled=compiled))
    return result


def compile_rule(entry: dict, path: str = 'check') -> Any:
    """Compile one rule with the same runaway-backtracking probes as the grammar rule files."""
    identifier = entry.get('id')
    if entry['kind'] == 'words':
        words = entry.get('words')
        if (
            not isinstance(words, list)
            or not words
            or len(words) > 2000
            or any(not isinstance(w, str) or not w.strip() or len(w) > 100 for w in words)
        ):
            _invalid('words is a list of up to 2000 words or phrases', path=path, rule=identifier)
        for flag in ('case_sensitive', 'whole_word'):
            if flag in entry and type(entry[flag]) is not bool:
                _invalid(f'{flag} is true or false', path=path, rule=identifier)
        mapping = entry.get('replacements', {})
        if not isinstance(mapping, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and len(v) <= 200 for k, v in mapping.items()
        ):
            _invalid('replacements maps a word to its replacement', path=path, rule=identifier)
        alternatives = '|'.join(
            regex.escape(w.strip()) for w in sorted(set(words), key=lambda w: (-len(w), w))
        )
        pattern = (
            f'(?<![\\w])(?:{alternatives})(?![\\w])'
            if entry.get('whole_word', True)
            else f'(?:{alternatives})'
        )
        flags = 0 if entry.get('case_sensitive') else regex.IGNORECASE
    else:
        pattern = entry.get('pattern')
        if not isinstance(pattern, str) or not pattern or len(pattern) > 500:
            _invalid('Rule pattern is missing or too long', path=path, rule=identifier)
        flags = grammar._flags(entry.get('flags'))
        if entry.get('replacement') is not None and (
            not isinstance(entry['replacement'], str) or len(entry['replacement']) > 200
        ):
            _invalid('replacement must be short text', path=path, rule=identifier)
    try:
        compiled = regex.compile(pattern, flags)
    except regex.error as exc:
        raise ByaxError(
            'invalid_input', f'Rule pattern does not compile: {exc}', path=path, rule=identifier
        ) from exc
    for probe in (*grammar._PROBES, *_PROBES):
        try:
            for _ in compiled.finditer(probe, timeout=MATCH_TIMEOUT):
                pass
        except TimeoutError as exc:
            raise ByaxError(
                'invalid_input',
                'Rule pattern is too slow (possible runaway backtracking)',
                path=path,
                rule=identifier,
            ) from exc
    return compiled


# Packages: inspect, stage, install -------------------------------------------------------
def _safe_member(name: str) -> bool:
    return bool(_PATH.fullmatch(name)) and '..' not in name.split('/') and len(name) <= 120


def _read_manifest_bytes(data: bytes) -> dict:
    if len(data) > MAX_MANIFEST_BYTES:
        _invalid(f'{MANIFEST_NAME} is too large')
    try:
        return validate_manifest(json.loads(data.decode('utf-8')))
    except (ValueError, UnicodeError) as exc:
        raise ByaxError('invalid_input', f'{MANIFEST_NAME} is not valid UTF-8 JSON') from exc


def _stage_folder(source: Path, stage: Path) -> None:
    root = source.resolve()
    files = 0
    total = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__')
        for name in sorted(names):
            if name.startswith('.') or name.endswith(('.pyc', '.pyo')):
                continue
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                _invalid('Extension folders may not contain links or special files', path=relative)
            if not _safe_member(relative):
                _invalid('Unsafe file name in the extension folder', path=relative)
            size = path.stat().st_size
            files += 1
            total += size
            if files > MAX_FILES or size > MAX_FILE_BYTES or total > MAX_PACKAGE_BYTES:
                raise ByaxError(
                    'limit_exceeded', 'The extension folder is larger than the allowed package size'
                )
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _stage_archive(source: Path, stage: Path) -> None:
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ByaxError('invalid_input', 'The extension file is not a valid ZIP archive') from exc
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if not infos or len(infos) > MAX_FILES:
            _invalid('The archive has an unexpected number of files')
        if sum(i.file_size for i in infos) > MAX_PACKAGE_BYTES or any(
            i.file_size > MAX_FILE_BYTES for i in infos
        ):
            raise ByaxError('limit_exceeded', 'The extension archive expands beyond the allowed size')
        names = [i.filename for i in infos]
        # A ZIP of the folder itself has one wrapping directory; accept it, nothing deeper.
        wrapper = ''
        if MANIFEST_NAME not in names:
            tops = {n.split('/', 1)[0] for n in names}
            if len(tops) == 1 and f'{next(iter(tops))}/{MANIFEST_NAME}' in names:
                wrapper = next(iter(tops)) + '/'
        seen = set()
        for info in infos:
            if info.filename.startswith('/') or '\\' in info.filename:
                _invalid('The archive has an unsafe member name', path=info.filename)
            name = info.filename[len(wrapper) :] if wrapper else info.filename
            if not info.filename.startswith(wrapper) or not _safe_member(name) or name in seen:
                _invalid('The archive has an unsafe or repeated member name', path=info.filename)
            if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                _invalid('The archive may not contain links', path=info.filename)
            seen.add(name)
            with archive.open(info) as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES or len(data) != info.file_size:
                raise ByaxError('limit_exceeded', 'An archive member is larger than declared', path=name)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            mode = (info.external_attr >> 16) & 0o777
            target.chmod(0o755 if mode & 0o111 else 0o644)
        if MANIFEST_NAME not in seen:
            _invalid(f'The archive has no {MANIFEST_NAME}')


def _tree(stage: Path) -> list[dict]:
    entries = []
    for path in sorted(p for p in stage.rglob('*') if p.is_file()):
        relative = path.relative_to(stage).as_posix()
        if relative in ('install.json',):
            continue
        entries.append({'path': relative, 'size': path.stat().st_size, 'sha256': _sha256_file(path)})
    return entries


def _tree_digest(entries: list[dict]) -> str:
    return _sha256_bytes(
        ''.join(
            f'{e["path"]}\0{e["sha256"]}\0{e["size"]}\n' for e in sorted(entries, key=lambda e: e['path'])
        ).encode()
    )


def _verify_package(stage: Path, manifest: dict) -> None:
    """Every entry point exists; data checks are valid; executables are executable."""
    propose = 'propose' in manifest['scopes']
    for check in manifest['entry_points']['checks']:
        path = stage / check['path']
        if not path.is_file():
            _invalid('A declared entry point file is missing', path=check['path'])
        if check['type'] == 'data':
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise ByaxError(
                    'invalid_input', 'Data check is not valid UTF-8 JSON', path=check['path']
                ) from exc
            validate_data_check(data, propose=propose, path=check['path'])
        elif check['runtime'] == 'executable' and sys.platform != 'win32' and not os.access(path, os.X_OK):
            _invalid('An executable entry point must have its execute permission', path=check['path'])
    for hook in manifest['entry_points']['export_hooks']:
        path = stage / hook['path']
        if not path.is_file():
            _invalid('A declared entry point file is missing', path=hook['path'])
        if hook['runtime'] == 'executable' and sys.platform != 'win32' and not os.access(path, os.X_OK):
            _invalid('An executable entry point must have its execute permission', path=hook['path'])


def _stage(source: str | Path, stage: Path) -> dict:
    source = Path(source)
    if not source.exists():
        raise ByaxError('missing_target', 'Extension folder or archive does not exist', target=str(source))
    archive_sha = None
    if source.is_dir():
        _stage_folder(source, stage)
    else:
        if source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ByaxError('limit_exceeded', 'The extension archive is larger than the allowed size')
        archive_sha = _sha256_file(source)
        _stage_archive(source, stage)
    manifest_path = stage / MANIFEST_NAME
    if not manifest_path.is_file():
        _invalid(f'The extension has no {MANIFEST_NAME}')
    manifest = _read_manifest_bytes(manifest_path.read_bytes())
    _verify_package(stage, manifest)
    files = _tree(stage)
    tree = _tree_digest(files)
    return {
        'manifest': manifest,
        'files': files,
        'tree_sha256': tree,
        'archive_sha256': archive_sha,
        'shown_sha256': archive_sha or tree,
        'package': 'archive' if archive_sha else 'folder',
    }


def inspect_package(source: str | Path, *, root: str | Path | None = None) -> dict:
    """What installing would do: manifest, scopes, hashes and any installed version. Installs nothing."""
    stage = Path(tempfile.mkdtemp(prefix='byax-ext-inspect-'))
    try:
        info = _stage(source, stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    manifest = info['manifest']
    installed = next((e for e in list_extensions(root=root) if e['id'] == manifest['id']), None)
    return {
        'extension': _summary(manifest),
        'package': info['package'],
        'sha256': info['shown_sha256'],
        'sha256_of': 'archive' if info['archive_sha256'] else 'folder contents',
        'tree_sha256': info['tree_sha256'],
        'files': info['files'],
        'requested_scopes': manifest['scopes'],
        'scope_descriptions': {s: SCOPES[s] for s in manifest['scopes']},
        'installed_version': installed['version'] if installed else None,
        'compatible': _compatibility(manifest) is None,
        'incompatible_reason': _compatibility(manifest),
    }


def _compatibility(manifest: dict) -> str | None:
    from . import __version__

    minimum = manifest.get('min_byax_version')
    if minimum and _version_key(minimum) > _version_key(re.match(r'\d+(?:\.\d+){0,2}', __version__).group()):  # type: ignore[union-attr]
        return f'Needs ByAuthorX {minimum} or newer'
    return None


def _summary(manifest: dict) -> dict:
    return {
        key: manifest[key]
        for key in ('id', 'name', 'version', 'api_version', 'description', 'author', 'licence', 'kinds')
    } | ({'homepage': manifest['homepage']} if 'homepage' in manifest else {})


def install(
    source: str | Path,
    *,
    sha256: str | None,
    scopes: list[str] | None,
    enable: bool = False,
    replace: bool = False,
    root: str | Path | None = None,
) -> dict:
    """Install after the person has seen the SHA-256 and the requested scopes and repeated them here.

    ``sha256`` is the archive hash (ZIP) or the folder-contents digest; ``scopes`` must equal the
    manifest's requested scopes exactly, so an updated manifest asking for more is refused.
    """
    expected = str(sha256 or '').lower()
    if not _SHA256.fullmatch(expected):
        _invalid('Installing needs the SHA-256 that was shown for this extension', field='sha256')
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        _invalid('Installing needs the scopes you accept', field='scopes')
    root = Path(root) if root is not None else data_root()
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=root))
    try:
        info = _stage(source, stage)
        manifest = info['manifest']
        if info['shown_sha256'] != expected:
            raise ByaxError(
                'checksum_mismatch',
                'The extension does not match the SHA-256 you confirmed; nothing was installed',
                actual=info['shown_sha256'],
            )
        if sorted(set(scopes)) != manifest['scopes']:
            raise ByaxError(
                'conflict',
                'The accepted scopes differ from the scopes the extension requests; nothing was installed',
                requested=manifest['scopes'],
                accepted=sorted(set(scopes)),
            )
        reason = _compatibility(manifest)
        if reason:
            raise ByaxError('unsupported_version', reason, field='min_byax_version')
        target = root / manifest['id']
        previous = next((e for e in list_extensions(root=root) if e['id'] == manifest['id']), None)
        if previous is not None:
            if not replace:
                raise ByaxError(
                    'conflict',
                    'An extension with this id is installed; use replace to install over it',
                    installed=previous.get('version'),
                    offered=manifest['version'],
                )
            if previous.get('version') and _version_key(previous['version']) > _version_key(
                manifest['version']
            ):
                raise ByaxError(
                    'conflict',
                    'A newer version is installed; remove it first to install an older one',
                    installed=previous['version'],
                    offered=manifest['version'],
                )
        (stage / 'install.json').write_text(
            json.dumps(
                {
                    'format': 1,
                    'source': str(source),
                    'archive_sha256': info['archive_sha256'],
                    'tree_sha256': info['tree_sha256'],
                    'granted_scopes': manifest['scopes'],
                    'files': info['files'],
                },
                indent=2,
                sort_keys=True,
            )
            + '\n',
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
    _set_enabled(manifest['id'], bool(enable), root)
    return describe(manifest['id'], root=root)


# State, listing, enable/disable/remove ----------------------------------------------------
def _state_path(root: Path) -> Path:
    return root / 'state.json'


def _read_state(root: Path) -> dict:
    try:
        raw = json.loads(_state_path(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'enabled': {}}
    enabled = raw.get('enabled') if isinstance(raw, dict) else None
    return {
        'enabled': {k: True for k, v in enabled.items() if v is True} if isinstance(enabled, dict) else {}
    }


def _set_enabled(identifier: str, value: bool, root: Path) -> None:
    state = _read_state(root)
    if value:
        state['enabled'][identifier] = True
    else:
        state['enabled'].pop(identifier, None)
    root.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix='.state-', dir=root)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as out:
            json.dump(state, out, indent=2, sort_keys=True)
            out.write('\n')
        os.replace(name, _state_path(root))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise


_HASHES: dict[tuple[str, int, int], str] = {}


def _cached_sha(path: Path) -> str:
    info = path.stat()
    key = (str(path), info.st_mtime_ns, info.st_size)
    if key not in _HASHES:
        if len(_HASHES) > 4096:
            _HASHES.clear()
        _HASHES[key] = _sha256_file(path)
    return _HASHES[key]


def _read_installed(directory: Path, enabled: dict) -> dict:
    record: dict[str, Any] = {
        'id': directory.name,
        'directory': str(directory),
        'enabled': enabled.get(directory.name, False),
        'problems': [],
    }
    try:
        manifest = _read_manifest_bytes((directory / MANIFEST_NAME).read_bytes())
    except (OSError, ByaxError) as exc:
        message = exc.message if isinstance(exc, ByaxError) else str(exc)
        return {
            **record,
            'status': 'incompatible' if getattr(exc, 'code', '') == 'unsupported_version' else 'invalid',
            'problems': [f'manifest: {message}'],
            'enabled': False,
        }
    record.update(manifest)
    problems: list[str] = record['problems']
    if manifest['id'] != directory.name:
        problems.append('manifest id does not match its folder name')
    try:
        install_info = json.loads((directory / 'install.json').read_text(encoding='utf-8'))
        recorded = {e['path']: e for e in install_info['files']}
        granted = install_info['granted_scopes']
        if not isinstance(granted, list) or not set(granted) <= set(SCOPES):
            raise ValueError('granted scopes')
    except (OSError, ValueError, KeyError, TypeError):
        return {
            **record,
            'status': 'invalid',
            'problems': [*problems, 'install record is missing or unreadable'],
            'enabled': False,
        }
    record['granted_scopes'] = sorted(set(granted) & set(manifest['scopes']))
    record['archive_sha256'] = install_info.get('archive_sha256')
    record['tree_sha256'] = install_info.get('tree_sha256')
    present = {
        p.relative_to(directory).as_posix(): p
        for p in directory.rglob('*')
        if p.is_file() and p.name != 'install.json'
    }
    for name, entry in recorded.items():
        path = present.get(name)
        if path is None:
            problems.append(f'{name}: file is missing')
        elif path.stat().st_size != entry['size'] or _cached_sha(path) != entry['sha256']:
            problems.append(f'{name}: differs from what was installed')
    problems.extend(
        f'{name}: file was added after installation' for name in sorted(set(present) - set(recorded))
    )
    reason = _compatibility(manifest)
    status = 'ok'
    if problems:
        status = 'modified'
    elif reason:
        status, problems = 'incompatible', [reason]
    record['status'], record['problems'] = status, problems
    return record


def list_extensions(*, root: str | Path | None = None) -> list[dict]:
    root = Path(root) if root is not None else data_root()
    if not root.is_dir():
        return []
    enabled = _read_state(root)['enabled']
    return [
        _read_installed(d, enabled)
        for d in sorted(root.iterdir())
        if d.is_dir() and not d.name.startswith('.')
    ]


def _public(record: dict, *, detail: bool = False) -> dict:
    points = record.get('entry_points', {'checks': [], 'export_hooks': []})
    result = {
        'id': record['id'],
        'name': record.get('name'),
        'version': record.get('version'),
        'api_version': record.get('api_version'),
        'kinds': record.get('kinds', []),
        'requested_scopes': record.get('scopes', []),
        'granted_scopes': record.get('granted_scopes', []),
        'enabled': bool(record['enabled']),
        'status': record['status'],
        'checks': [c['id'] for c in points['checks']],
        'export_hooks': [h['id'] for h in points['export_hooks']],
    }
    if detail:
        for key in (
            'description',
            'author',
            'licence',
            'homepage',
            'min_byax_version',
            'archive_sha256',
            'tree_sha256',
        ):
            if record.get(key) is not None:
                result[key] = record[key]
        result['entry_points'] = points
        result['problems'] = list(record.get('problems', []))
        result['scope_descriptions'] = {s: SCOPES[s] for s in record.get('scopes', [])}
        result['directory'] = record['directory']
    return result


def get(identifier: str, *, root: str | Path | None = None) -> dict:
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        _invalid('Extension id is malformed', field='id')
    for record in list_extensions(root=root):
        if record['id'] == identifier:
            return record
    raise ByaxError('missing_target', 'No such extension', target=identifier)


def describe(identifier: str, *, root: str | Path | None = None) -> dict:
    return _public(get(identifier, root=root), detail=True)


def summaries(*, root: str | Path | None = None) -> list[dict]:
    return [_public(r) for r in list_extensions(root=root)]


def set_enabled(identifier: str, value: bool, *, root: str | Path | None = None) -> dict:
    record = get(identifier, root=root)
    if value and record['status'] != 'ok':
        raise ByaxError(
            'conflict',
            f'Only an intact, compatible extension can be enabled (status: {record["status"]})',
            problems=record['problems'],
        )
    _set_enabled(identifier, value, Path(root) if root is not None else data_root())
    return describe(identifier, root=root)


def remove(identifier: str, *, root: str | Path | None = None) -> dict:
    record = get(identifier, root=root)
    base = Path(root) if root is not None else data_root()
    _set_enabled(identifier, False, base)
    shutil.rmtree(record['directory'], ignore_errors=False)
    return {'removed': identifier, 'version': record.get('version')}


def verify(identifier: str | None = None, *, root: str | Path | None = None) -> list[dict]:
    records = [get(identifier, root=root)] if identifier else list_extensions(root=root)
    return [
        {'id': r['id'], 'status': r['status'], 'ok': r['status'] == 'ok', 'problems': list(r['problems'])}
        for r in records
    ]


def usable(identifier: str, scope: str, *, root: str | Path | None = None) -> dict:
    """The installed record when it is enabled, intact and granted ``scope``; otherwise ``ByaxError``."""
    record = get(identifier, root=root)
    if not record['enabled']:
        raise ByaxError('conflict', 'The extension is installed but not enabled', target=identifier)
    if record['status'] != 'ok':
        raise ByaxError(
            'conflict', f'The extension is {record["status"]} and will not run', problems=record['problems']
        )
    if scope not in record['granted_scopes']:
        raise ByaxError(
            'forbidden', f'The extension was not granted the {scope} scope', target=identifier, scope=scope
        )
    return record


def token_scopes(identifier: str, *, root: str | Path | None = None) -> set[str] | None:
    """Scopes a REST token for this extension may exercise right now, or ``None`` if it may not run."""
    try:
        record = get(identifier, root=root)
    except ByaxError:
        return None
    if not record['enabled'] or record['status'] != 'ok':
        return None
    return set(record['granted_scopes'])


# Out-of-process runner ---------------------------------------------------------------------
def _child_env() -> dict[str, str]:
    keep = (
        'PATH',
        'SYSTEMROOT',
        'SystemRoot',
        'WINDIR',
        'TMPDIR',
        'TEMP',
        'TMP',
        'LANG',
        'LC_ALL',
        'LC_CTYPE',
    )
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', BYAX_EXTENSION_API=str(API_VERSION))
    return env


def _worker_command(runtime: str, script: Path, timeout: int, file_bytes: int) -> list[str]:
    mode = (
        ['--byax-extension-worker']
        if getattr(sys, 'frozen', False)
        else ['-I', '-m', 'byax.extension_worker']
    )
    return [
        sys.executable,
        *mode,
        runtime,
        str(script),
        str(MEMORY_LIMIT),
        str(timeout + 2),
        str(file_bytes),
    ]


def _kill(process: subprocess.Popen) -> None:
    with contextlib.suppress(OSError, ProcessLookupError):
        if sys.platform == 'win32':
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.wait(timeout=5)


def run_isolated(
    runtime: str,
    script: Path,
    payload: bytes,
    *,
    timeout: int,
    max_output: int,
    cwd: Path,
    file_bytes: int,
) -> tuple[bytes, str, float]:
    """Run an entry point in its own resource-limited process group and return (stdout, stderr tail, seconds).

    Raises ``ExtensionFailure`` for a timeout, oversized output or a non-zero exit. The payload is
    handed over as a file so a script that never reads stdin cannot block us.
    """
    if len(payload) > MAX_STDIN_BYTES:
        raise ExtensionFailure('oversized_input', 'The document is too large to hand to a script')
    with tempfile.TemporaryDirectory(prefix='byax-ext-run-') as temporary:
        base = Path(temporary)
        (base / 'in.json').write_bytes(payload)
        started = time.monotonic()
        deadline = started + timeout
        with (
            (base / 'in.json').open('rb') as stdin,
            (base / 'out').open('wb') as stdout,
            (base / 'err').open('wb') as stderr,
        ):
            options: dict[str, Any] = {}
            if sys.platform == 'win32':
                options['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            else:
                options['start_new_session'] = True
            try:
                process = subprocess.Popen(
                    _worker_command(runtime, script, timeout, file_bytes),
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=str(cwd),
                    env=_child_env(),
                    **options,
                )
            except OSError as exc:
                raise ExtensionFailure(
                    'crash', f'The process could not be started: {exc.strerror or exc}'
                ) from exc
            failure: ExtensionFailure | None = None
            try:
                while process.poll() is None:
                    if (base / 'out').stat().st_size > max_output:
                        failure = ExtensionFailure('oversized', f'Output is larger than {max_output} bytes')
                        break
                    if time.monotonic() > deadline:
                        failure = ExtensionFailure('timeout', f'Did not finish within {timeout} seconds')
                        break
                    time.sleep(0.01)
            finally:
                if process.poll() is None:
                    _kill(process)
                else:
                    with contextlib.suppress(OSError, ProcessLookupError):
                        if sys.platform != 'win32':
                            os.killpg(process.pid, signal.SIGKILL)  # stragglers it left behind
        seconds = time.monotonic() - started
        stderr_tail = (base / 'err').read_bytes()[-400:].decode('utf-8', 'replace')
        size = (base / 'out').stat().st_size
        if failure is None and size > max_output:
            failure = ExtensionFailure('oversized', f'Output is larger than {max_output} bytes')
        if failure is not None:
            failure.details.update(stderr=stderr_tail, seconds=seconds)
            raise failure
        if process.returncode != 0:
            raise ExtensionFailure(
                'crash',
                f'Exited with status {process.returncode}',
                stderr=stderr_tail,
                seconds=seconds,
                returncode=process.returncode,
            )
        return (base / 'out').read_bytes(), stderr_tail, seconds


def _entry_file(record: dict, relative: str) -> Path:
    """The entry point file, re-hashed against what was installed immediately before it runs."""
    directory = Path(record['directory'])
    path = directory / relative
    try:
        install_info = json.loads((directory / 'install.json').read_text(encoding='utf-8'))
        expected = next(e for e in install_info['files'] if e['path'] == relative)
    except (OSError, ValueError, StopIteration, KeyError, TypeError) as exc:
        raise ExtensionFailure('tampered', 'The installation record does not list this file') from exc
    if path.is_symlink() or not path.is_file() or _sha256_file(path) != expected['sha256']:
        raise ExtensionFailure('tampered', f'{relative} differs from what was installed')
    return path


def _reject_constant(name: str):
    raise ValueError(f'{name} is not valid JSON')


def _parse_json(data: bytes) -> Any:
    try:
        return json.loads(data.decode('utf-8'), parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ExtensionFailure('malformed', f'Output is not valid UTF-8 JSON: {exc}') from exc


# Script check output ----------------------------------------------------------------------
def validate_findings(
    output: Any, targets: dict[str, str], *, propose: bool, declared: list[str] | None
) -> list[dict]:
    """Strictly validate a script's findings; any violation rejects the whole output (no partial trust)."""

    def bad(message: str, index: int | None = None):
        suffix = '' if index is None else f' (finding {index})'
        raise ExtensionFailure('invalid', message + suffix)

    if not isinstance(output, dict) or set(output) - {'api_version', 'findings'}:
        bad('Output must be an object with api_version and findings only')
    if 'api_version' in output and output['api_version'] != API_VERSION:
        bad(f'api_version in the output must be {API_VERSION}')
    findings = output.get('findings')
    if not isinstance(findings, list):
        bad('findings must be a list')
    if len(findings) > MAX_FINDINGS:
        bad(f'More than {MAX_FINDINGS} findings')
    result = []
    for index, item in enumerate(findings):
        if not isinstance(item, dict):
            bad('A finding must be an object', index)
        unknown = set(item) - _FINDING_KEYS
        if unknown:
            bad(f'Unknown finding keys {sorted(unknown)}', index)
        if not all(key in item for key in ('rule', 'target', 'start', 'end', 'message')):
            bad('A finding needs rule, target, start, end and message', index)
        rule = item['rule']
        if not isinstance(rule, str) or not _ENTRY_ID.fullmatch(rule):
            bad('rule must be lowercase letters, digits, hyphens and underscores', index)
        if declared is not None and rule not in declared:
            bad(f'rule {rule} is not declared in the manifest', index)
        target = item['target']
        if not isinstance(target, str) or target not in targets:
            bad('target is not a text the document check sent', index)
        start, end = item['start'], item['end']
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(targets[target]):
            bad('start and end must be whole numbers inside the target text', index)
        severity = item.get('severity', 'advisory')
        if severity not in SEVERITIES:
            bad(f'severity must be one of {list(SEVERITIES)}; extensions cannot raise errors', index)
        message = item['message']
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > MAX_MESSAGE
            or any(ord(c) < 32 and c not in '\n\t' for c in message)
        ):
            bad(f'message must be 1 to {MAX_MESSAGE} characters of text', index)
        replacement = item.get('replacement')
        if replacement is not None:
            if not propose:
                bad('A replacement needs the propose scope', index)
            if not isinstance(replacement, str) or len(replacement) > MAX_REPLACEMENT:
                bad('replacement must be text', index)
        measured = item.get('measured', {})
        if (
            not isinstance(measured, dict)
            or len(measured) > 20
            or any(
                not isinstance(k, str)
                or len(k) > 40
                or not (
                    isinstance(v, (int, float, bool)) or v is None or (isinstance(v, str) and len(v) <= 200)
                )
                for k, v in measured.items()
            )
        ):
            bad('measured is a small object of numbers, booleans and short text', index)
        result.append(
            {
                'rule': rule,
                'target': target,
                'start': start,
                'end': end,
                'severity': severity,
                'message': message.strip(),
                'replacement': replacement,
                'measured': measured,
            }
        )
    return result


# Check integration (called from checks.py) ------------------------------------------------
def enabled_checks(root: str | Path | None = None) -> list[tuple[dict, dict]]:
    """(extension record, check entry) for every enabled, intact extension with checks, honouring ``ONLY``."""
    only = ONLY.get()
    result = []
    for record in list_extensions(root=root):
        if not record['enabled'] or record['status'] != 'ok' or 'checks' not in record['granted_scopes']:
            continue
        if only and only[0] != record['id']:
            continue
        for check in record['entry_points']['checks']:
            if only and only[1] and only[1] != check['id']:
                continue
            result.append((record, check))
    return result


def report_coverage(scope: str) -> list[dict]:
    """Report-level coverage for the ``extension`` suite: says so when nothing is enabled to run."""
    if enabled_checks():
        return []
    return [
        {
            'suite': SUITE,
            'rule': 'extension.none_enabled',
            'status': 'skipped',
            'reason': 'No extension checks are installed and enabled.',
            'by_policy': True,
            'level': 'report',
            'scope': scope,
        }
    ]


def _targets(ctx) -> tuple[list[dict], bool]:
    """Texts to check, in document order, as the script payload shape; second item: any skipped as intentional."""
    cached = getattr(ctx, '_extension_targets', None)
    if cached is not None:
        return cached
    from .checks import _intentional, _language

    targets, skipped = [], False
    for block in ctx.doc.get('blocks', []):
        if block.get('type') in ('image', 'toc'):
            continue
        objects = (
            [block]
            if block.get('type') != 'table'
            else [c for r in block.get('rows', []) for c in r['cells']]
        )
        for obj in objects:
            if not obj.get('text'):
                continue
            if _intentional(block):
                skipped = True
                continue
            targets.append(
                {
                    'id': obj['id'],
                    'block': block['id'],
                    'type': block.get('type'),
                    'text': obj['text'],
                    'language': _language(ctx.doc, block),
                }
            )
    ctx._extension_targets = (targets, skipped)
    return ctx._extension_targets


def unit_fingerprints(ctx) -> dict[str, str]:
    """One document-wide unit per enabled check; no process is started here."""
    checks = enabled_checks()
    if not checks:
        return {}
    targets, _ = _targets(ctx)
    digest = object_hash([(t['id'], t['text'], t['language']) for t in targets])
    return {
        f'context:extension:{record["id"]}:{check["id"]}': object_hash(
            {
                'v': VERSION,
                'tree': record['tree_sha256'],
                'check': check,
                'granted': record['granted_scopes'],
                'text': digest,
                'language': ctx.doc.get('metadata', {}).get('language'),
            }
        )
        for record, check in checks
    }


def evaluate(ctx, sink, key: str) -> None:
    """Evaluate one ``context:extension:<id>:<check>`` unit and add findings or honest coverage."""
    identifier, check_id = key.split(':')[2:4]
    match = next(((r, c) for r, c in enabled_checks() if r['id'] == identifier and c['id'] == check_id), None)
    rule_base = f'extension.{identifier}.{check_id}'
    if match is None:
        sink.cover(
            SUITE, rule_base, 'skipped', 'The extension is no longer enabled or intact.', extension=identifier
        )
        return
    record, check = match
    targets, skipped = _targets(ctx)
    if skipped:
        sink.cover(
            SUITE,
            rule_base,
            'skipped',
            'Passages marked intentional (code, quotation, lyric, dialect or verbatim) are not sent to extension checks.',
            by_policy=True,
            extension=identifier,
        )
    try:
        if check['type'] == 'data':
            emitted = _run_data_check(record, check, targets, sink)
            if emitted >= MAX_FINDINGS:
                sink.cover(
                    SUITE,
                    rule_base,
                    'incomplete',
                    f'Stopped after {MAX_FINDINGS} findings from this check.',
                    extension=identifier,
                )
        else:
            _run_script_check(ctx, record, check, targets, sink)
    except ExtensionFailure as exc:
        sink.cover(
            SUITE,
            rule_base,
            'failed',
            f'Extension {identifier} check {check_id} produced no usable result ({exc.kind}): {exc.message}',
            extension=identifier,
            failure=exc.kind,
        )
        return
    sink.cover(
        SUITE,
        rule_base,
        'completed',
        f'Extension {record["name"]} {record["version"]}, check {check["title"]}.',
        extension=identifier,
        version=record['version'],
    )


def _add(
    sink, record, check, rule, severity, message, target, start, end, text, replacement, measured, granted
):
    from .checks import _replace_ops

    operations = []
    if replacement is not None and 'propose' in granted:
        operations = _replace_ops(target, text, start, end, replacement)
    sink.add(
        f'extension.{record["id"]}.{rule}',
        FAMILY_PREFIX + record['id'],
        severity,
        message,
        target,
        start,
        end,
        text[start:end],
        operations,
        measured=dict(measured, extension=record['id'], check=check['id']),
    )


def _run_data_check(record: dict, check: dict, targets: list[dict], sink) -> int:
    path = _entry_file(record, check['path'])
    rules = validate_data_check(
        json.loads(path.read_text(encoding='utf-8')),
        propose='propose' in record['granted_scopes'],
        path=check['path'],
    )
    emitted = 0
    for entry in rules:
        compiled, mapping = (
            entry['_compiled'],
            {k.lower(): v for k, v in entry.get('replacements', {}).items()},
        )
        for target in targets:
            languages = entry.get('languages')
            if languages and target['language'] not in languages:
                continue
            text = target['text']
            try:
                for hit in compiled.finditer(text, timeout=MATCH_TIMEOUT):
                    if emitted >= MAX_FINDINGS:
                        return emitted
                    if hit.end() == hit.start():
                        continue
                    replacement = None
                    if entry['kind'] == 'words':
                        replacement = mapping.get(hit.group().lower())
                    elif entry.get('replacement') is not None:
                        try:
                            replacement = hit.expand(entry['replacement'])
                        except (regex.error, IndexError):
                            replacement = None
                    message = entry['message'].replace('{match}', hit.group())
                    _add(
                        sink,
                        record,
                        check,
                        entry['id'],
                        entry.get('severity', 'advisory'),
                        message,
                        target['id'],
                        hit.start(),
                        hit.end(),
                        text,
                        replacement,
                        {'match': hit.group()[:100]},
                        record['granted_scopes'],
                    )
                    emitted += 1
            except TimeoutError as exc:
                raise ExtensionFailure('timeout', f'Rule {entry["id"]} took too long on one passage') from exc
    return emitted


def script_payload(record: dict, check: dict, targets: list[dict], language: str | None) -> bytes:
    """Exactly what a script check receives: block and cell text and language, nothing else."""
    return json.dumps(
        {
            'api_version': API_VERSION,
            'extension': record['id'],
            'check': check['id'],
            'language': language or '',
            'targets': targets,
            'options': check.get('options', {}),
        },
        ensure_ascii=False,
    ).encode('utf-8')


def _run_script_check(ctx, record: dict, check: dict, targets: list[dict], sink) -> None:
    if 'read' not in record['granted_scopes']:
        raise ExtensionFailure('scope', 'The read scope was not granted')
    script = _entry_file(record, check['path'])
    payload = script_payload(record, check, targets, ctx.doc.get('metadata', {}).get('language'))
    with tempfile.TemporaryDirectory(prefix='byax-ext-work-') as work:
        out, _, _seconds = run_isolated(
            check['runtime'],
            script,
            payload,
            timeout=check['timeout'],
            max_output=MAX_CHECK_OUTPUT,
            cwd=Path(work),
            file_bytes=MAX_CHECK_OUTPUT + (1 << 20),
        )
    texts = {t['id']: t['text'] for t in targets}
    findings = validate_findings(
        _parse_json(out), texts, propose='propose' in record['granted_scopes'], declared=check.get('rules')
    )
    for item in findings:
        _add(
            sink,
            record,
            check,
            item['rule'],
            item['severity'],
            item['message'],
            item['target'],
            item['start'],
            item['end'],
            texts[item['target']],
            item['replacement'],
            item['measured'],
            record['granted_scopes'],
        )


# Export hooks -----------------------------------------------------------------------------
def hooks_for(fmt: str | None, *, root: str | Path | None = None) -> list[tuple[dict, dict]]:
    result = []
    for record in list_extensions(root=root):
        if not record['enabled'] or record['status'] != 'ok' or 'export-hook' not in record['granted_scopes']:
            continue
        for hook in record['entry_points']['export_hooks']:
            if hook['formats'] is None or (fmt or '').lower() in hook['formats']:
                result.append((record, hook))
    return result


def run_hook(
    record: dict,
    hook: dict,
    exported: str | Path,
    *,
    fmt: str,
    loss_report: dict | list | None,
    output_dir: str | Path,
    overwrite: bool = False,
) -> dict:
    """Run one export hook on a read-only copy of ``exported``; copy what it produced into ``output_dir``."""
    result: dict[str, Any] = {
        'extension': record['id'],
        'hook': hook['id'],
        'status': 'completed',
        'outputs': [],
        'skipped': [],
        'message': '',
    }
    exported = Path(exported)
    work = Path(tempfile.mkdtemp(prefix='byax-ext-hook-'))
    try:
        if 'export-hook' not in record['granted_scopes']:
            raise ExtensionFailure('scope', 'The export-hook scope was not granted')
        if not exported.is_file():
            raise ExtensionFailure('missing', 'The exported file does not exist')
        script = _entry_file(record, hook['path'])
        inputs, staging = work / 'input', work / 'output'
        inputs.mkdir()
        staging.mkdir()
        copy = inputs / exported.name
        shutil.copyfile(exported, copy)
        report = json.dumps(loss_report if loss_report is not None else {}, ensure_ascii=False, default=str)
        if len(report) > MAX_LOSS_REPORT_BYTES:
            report = json.dumps({'truncated': True, 'note': 'The loss report was too large to hand over'})
        payload = json.dumps(
            {
                'api_version': API_VERSION,
                'extension': record['id'],
                'hook': hook['id'],
                'format': fmt,
                'file': str(copy),
                'output_dir': str(staging),
                'loss_report': json.loads(report),
            },
            ensure_ascii=False,
        ).encode('utf-8')
        if sys.platform != 'win32':
            copy.chmod(0o444)
            inputs.chmod(0o555)
        try:
            out, _, seconds = run_isolated(
                hook['runtime'],
                script,
                payload,
                timeout=hook['timeout'],
                max_output=MAX_HOOK_STDOUT,
                cwd=staging,
                file_bytes=MAX_HOOK_FILE_BYTES,
            )
        finally:
            if sys.platform != 'win32':
                inputs.chmod(0o755)
        result['seconds'] = round(seconds, 3)
        if out.strip():
            parsed = _parse_json(out)
            if (
                not isinstance(parsed, dict)
                or set(parsed) - {'message'}
                or not isinstance(parsed.get('message', ''), str)
            ):
                raise ExtensionFailure('malformed', 'A hook may print {"message": "..."} or nothing')
            result['message'] = parsed.get('message', '')[:1000]
        _collect_outputs(staging, Path(output_dir), overwrite, result)
    except ExtensionFailure as exc:
        result.update(
            status='timeout' if exc.kind == 'timeout' else 'failed',
            failure=exc.kind,
            error=exc.message,
            stderr=exc.details.get('stderr', ''),
        )
        result['outputs'] = []
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


def _collect_outputs(staging: Path, destination: Path, overwrite: bool, result: dict) -> None:
    produced = sorted(staging.iterdir())
    if len(produced) > MAX_HOOK_FILES:
        raise ExtensionFailure('invalid', f'The hook produced more than {MAX_HOOK_FILES} files')
    plan = []
    for path in produced:
        if path.is_symlink() or not path.is_file():
            raise ExtensionFailure('invalid', f'{path.name} is not a plain file (hooks may only write files)')
        if not _OUTPUT_NAME.fullmatch(path.name):
            raise ExtensionFailure('invalid', f'{path.name!r} is not an acceptable file name')
        if path.stat().st_size > MAX_HOOK_FILE_BYTES:
            raise ExtensionFailure('invalid', f'{path.name} is larger than {MAX_HOOK_FILE_BYTES} bytes')
        plan.append(path)
    if plan:
        destination.mkdir(parents=True, exist_ok=True)
    for path in plan:
        target = destination / path.name
        if target.exists() and not overwrite:
            result['skipped'].append({'file': path.name, 'reason': 'exists; use overwrite to replace it'})
            continue
        shutil.copyfile(path, target)
        result['outputs'].append(str(target))


def run_export_hooks(
    exported: str | Path,
    *,
    fmt: str,
    loss_report: dict | list | None,
    output_dir: str | Path,
    overwrite: bool = False,
    root: str | Path | None = None,
) -> list[dict]:
    """Every enabled hook that applies to ``fmt``; a failing hook never fails the export."""
    return [
        run_hook(
            record,
            hook,
            exported,
            fmt=fmt,
            loss_report=loss_report,
            output_dir=output_dir,
            overwrite=overwrite,
        )
        for record, hook in hooks_for(fmt, root=root)
    ]


# Command ----------------------------------------------------------------------------------
READ_ACTIONS = ('list', 'info', 'verify', 'run-check', 'run-hook', 'scopes')
ADMIN_ACTIONS = ('inspect-package', 'install', 'enable', 'disable', 'remove', 'token')


def extensions_command(a: dict, owner: str | None = None) -> dict:
    """``byax extensions``: list, info, verify, run-check, run-hook; local-only install/enable/disable/remove/token."""
    from .cli import _error, _load, envelope

    action = a.get('action', 'list')
    if action not in READ_ACTIONS + ADMIN_ACTIONS:
        _error(
            'invalid_input',
            f'Unknown extensions action: {action}',
            supported=list(READ_ACTIONS + ADMIN_ACTIONS),
        )

    def need(key: str) -> Any:
        if a.get(key) in (None, ''):
            _error('invalid_input', f'extensions {action} needs --{key.replace("_", "-")}', field=key)
        return a[key]

    if action == 'list':
        return envelope({'extensions': summaries(), 'api_version': API_VERSION, 'root': str(data_root())})
    if action == 'scopes':
        return envelope({'scopes': SCOPES, 'api_version': API_VERSION})
    if action == 'info':
        return envelope({'extension': describe(need('id'))})
    if action == 'verify':
        results = verify(a.get('id'))
        return envelope(
            {'verification': results}, validation='passed' if all(r['ok'] for r in results) else 'failed'
        )
    if action == 'inspect-package':
        return envelope({'package': inspect_package(need('source_path'))})
    if action == 'install':
        plan = inspect_package(need('source_path'))
        if not a.get('sha256') and not a.get('scopes'):
            return envelope(
                {
                    'package': plan,
                    'installed': False,
                    'next': 'Review the scopes and SHA-256, then repeat this command with '
                    f'--sha256 {plan["sha256"]} and --scopes for each of {plan["requested_scopes"]}.',
                },
                review='required',
            )
        installed = install(
            a['source_path'],
            sha256=a.get('sha256'),
            scopes=a.get('scopes'),
            enable=bool(a.get('enable')),
            replace=bool(a.get('replace')),
        )
        return envelope({'extension': installed, 'installed': True})
    if action == 'enable':
        return envelope({'extension': set_enabled(need('id'), True)})
    if action == 'disable':
        return envelope({'extension': set_enabled(need('id'), False)})
    if action == 'remove':
        return envelope(remove(need('id')))
    if action == 'token':
        from .service import issue_extension_token

        identifier = need('id')
        scopes = a.get('scopes') or sorted(set(describe(identifier)['granted_scopes']) & {'read', 'propose'})
        env_name = a.get('token_env') or 'BYAX_SERVICE_TOKEN'
        main = os.environ.get(env_name, '')
        issued = issue_extension_token(main, identifier, scopes, ttl=a.get('ttl', 3600))
        return envelope({'token': issued, 'note': 'Shown once; give it only to this extension.'})
    if action == 'run-check':
        from .checks import run_checks

        identifier = need('id')
        usable(identifier, 'checks')
        doc = _load(need('document'))
        token = ONLY.set((identifier, a.get('check')))
        try:
            report = run_checks(doc, ['extension'], limit=a.get('limit', 100))
        finally:
            ONLY.reset(token)
        return envelope(report, doc)
    # run-hook
    identifier, hook_id = need('id'), need('hook')
    record = usable(identifier, 'export-hook')
    hook = next((h for h in record['entry_points']['export_hooks'] if h['id'] == hook_id), None)
    if hook is None:
        raise ByaxError('missing_target', 'No such export hook', target=hook_id)
    exported = Path(need('exported_file'))
    fmt = (a.get('format') or exported.suffix.lstrip('.')).lower()
    if hook['formats'] is not None and fmt not in hook['formats']:
        raise ByaxError('conflict', 'This hook does not apply to that format', formats=hook['formats'])
    output_dir = a.get('hooks_dir') or str(exported.with_name(exported.name + '.hooks'))
    outcome = run_hook(
        record,
        hook,
        exported,
        fmt=fmt,
        loss_report=a.get('input'),
        output_dir=output_dir,
        overwrite=bool(a.get('overwrite')),
    )
    failed = outcome['status'] != 'completed'
    return envelope(
        {'hook': outcome, 'outputs': outcome['outputs']},
        execution='failed' if failed else 'completed',
        **({'error': {'code': 'execution_failure', 'message': outcome.get('error', '')}} if failed else {}),
    )


@contextlib.contextmanager
def only(identifier: str, check: str | None = None) -> Iterator[None]:
    token = ONLY.set((identifier, check))
    try:
        yield
    finally:
        ONLY.reset(token)
