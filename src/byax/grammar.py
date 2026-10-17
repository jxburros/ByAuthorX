"""Offline grammar tiers (competitiveness plan 6.1) behind one engine interface.

Two engines feed the ``grammar`` suite of ``checks.py``:

* **Built-in rules** (always available, deterministic, narrow): data-driven regular-expression rules
  in ``resources/grammar/<language>.json`` or an installed language pack. Precision first: each rule
  is limited to contexts where the flagged form is wrong.
* **LanguageTool** (optional, high coverage): a *local* LanguageTool server that the user installed
  or started, or that ByAuthorX starts from a LanguageTool folder. Text is only ever sent to a
  loopback address. Nothing is downloaded unless the user runs ``install-languagetool`` and
  confirms the pinned URL, size and SHA-256.

Both return the same ``match`` dictionaries with Unicode scalar offsets, so findings, ranges and repair
proposals look alike. See ``docs/grammar.md`` for the decision record and the accuracy numbers.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Protocol

import regex

from . import language_packs
from .errors import ByaxError

BUILTIN_VERSION = 1
LEVELS = {'conservative': 0, 'balanced': 1, 'broad': 2}
SEVERITIES = ('advisory', 'warning')
MAX_RULES = 2000
MAX_PATTERN = 1500
MATCH_TIMEOUT = 0.5
# Official LanguageTool 6.6 (the last non-snapshot release). The site publishes no checksum; this
# digest was computed from a direct download on 2026-10-16 and must be re-verified by the maintainer
# against an independent download before a release (see docs/grammar.md).
LANGUAGETOOL = {
    'version': '6.6',
    'url': 'https://languagetool.org/download/LanguageTool-6.6.zip',
    'size': 251998221,
    'sha256': '53600506b399bb5ffe1e4c8dec794fd378212f14aaf38ccef9b6f89314d11631',
    'licence': 'LGPL-2.1-or-later',
    'archive_root': 'LanguageTool-6.6',
    'requires': 'Java 17 or later',
}
# LanguageTool rules that repeat deterministic checks already in checks.py (spec 12.1).
LT_DUPLICATES = frozenset(
    {
        'WHITESPACE_RULE',
        'CONSECUTIVE_SPACES',
        'COMMA_PARENTHESIS_WHITESPACE',
        'UPPERCASE_SENTENCE_START',
        'EN_A_VS_AN',
        'ENGLISH_WORD_REPEAT_RULE',
        'DOUBLE_PUNCTUATION',
        'EN_UNPAIRED_BRACKETS',
        'UNPAIRED_BRACKETS',
    }
)
_LT_SPELLING = re.compile(r'MORFOLOGIK|HUNSPELL|SPELLER|SPELLING')
_LT_REGIONS = {'en': 'en-US', 'de': 'de-DE', 'pt': 'pt-PT'}
_DEFAULT_CONFIG = {
    'version': 1,
    'builtin': {'enabled': True, 'disabled_rules': []},
    'languagetool': {
        'enabled': False,
        'port': 8081,
        'directory': None,
        'java': None,
        'memory_mb': 1024,
        'autostart': False,
        'include_spelling': False,
        'timeout': 15,
    },
}


class GrammarEngine(Protocol):
    """One grammar checker: ``check`` returns matches as dictionaries (see ``_match``)."""

    name: str

    def check(self, text: str, language: str) -> list[dict]: ...


def _match(rule, group, engine, message, start, end, text, replacements, severity='advisory', **extra):
    return {
        'rule': rule,
        'group': group,
        'engine': engine,
        'message': message,
        'start': start,
        'end': end,
        'text': text[start:end],
        'replacements': list(replacements),
        'severity': severity,
        **extra,
    }


# Built-in rule sets -----------------------------------------------------------------------
def _flags(spec):
    if spec in (None, ''):
        return 0
    if not isinstance(spec, str) or set(spec) - {'i', 'm', 's'}:
        raise ByaxError('invalid_input', 'Rule flags may only contain i, m and s', field='flags')
    return sum({'i': regex.IGNORECASE, 'm': regex.MULTILINE, 's': regex.DOTALL}[c] for c in set(spec))


_PROBES = (
    'a ' * 1500,
    'a' * 3000,
    'The quick brown fox, ' * 200,
    '(((( ' * 400,
    'I ' * 1500,
    'the the the ' * 300,
)


def _compile(entry, path):
    """Compile one rule's pattern with the safety probes; raises ``invalid_input`` for a bad rule."""
    identifier = entry.get('id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-z][a-z0-9_]{1,60}', identifier):
        raise ByaxError(
            'invalid_input', 'Grammar rule id must be lowercase letters, digits and underscores', path=path
        )
    pattern = entry.get('pattern')
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN:
        raise ByaxError(
            'invalid_input', 'Grammar rule pattern is missing or too long', path=path, rule=identifier
        )
    try:
        compiled = regex.compile(pattern, _flags(entry.get('flags')))
    except regex.error as exc:
        raise ByaxError(
            'invalid_input', f'Grammar rule pattern does not compile: {exc}', path=path, rule=identifier
        ) from exc
    for probe in _PROBES:
        try:
            for _ in compiled.finditer(probe, timeout=MATCH_TIMEOUT):
                pass
        except TimeoutError as exc:
            raise ByaxError(
                'invalid_input',
                'Grammar rule pattern is too slow (possible runaway backtracking)',
                path=path,
                rule=identifier,
            ) from exc
    return compiled


def validate_rule_set(value, *, path='grammar'):
    """Structural and safety validation of a grammar rule file; raises ``invalid_input``."""
    rules = value.get('rules')
    if not isinstance(rules, list) or not rules or len(rules) > MAX_RULES:
        raise ByaxError('invalid_input', 'A grammar file needs a non-empty "rules" list', path=path)
    seen = set()
    for entry in rules:
        if not isinstance(entry, dict):
            raise ByaxError('invalid_input', 'Grammar rules must be objects', path=path)
        compiled = _compile(entry, path)
        identifier = entry['id']
        if identifier in seen:
            raise ByaxError('invalid_input', 'Duplicate grammar rule id', path=path, rule=identifier)
        seen.add(identifier)
        if (
            entry.get('level', 'balanced') not in LEVELS
            or entry.get('severity', 'advisory') not in SEVERITIES
        ):
            raise ByaxError('invalid_input', 'Unknown rule level or severity', path=path, rule=identifier)
        if not isinstance(entry.get('group'), str) or not isinstance(entry.get('message'), str):
            raise ByaxError(
                'invalid_input', 'Each rule needs a "group" and a "message"', path=path, rule=identifier
            )
        highlight = entry.get('highlight', 0)
        if isinstance(highlight, int):
            if not 0 <= highlight <= compiled.groups:
                raise ByaxError(
                    'invalid_input', 'Rule highlight group does not exist', path=path, rule=identifier
                )
        elif highlight not in compiled.groupindex:
            raise ByaxError(
                'invalid_input', 'Rule highlight group does not exist', path=path, rule=identifier
            )
        for key in ('replacements',):
            items = entry.get(key, [])
            if not isinstance(items, list) or not all(isinstance(i, str) and len(i) <= 200 for i in items):
                raise ByaxError(
                    'invalid_input', 'Rule replacements must be short text', path=path, rule=identifier
                )
        mapping = entry.get('replacement_map', {})
        if not isinstance(mapping, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()
        ):
            raise ByaxError(
                'invalid_input', 'Rule replacement_map must map text to text', path=path, rule=identifier
            )


class _Rule:
    __slots__ = (
        'compiled',
        'group',
        'highlight',
        'id',
        'level',
        'mapping',
        'message',
        'replacements',
        'severity',
        'skip_dialogue',
        'title',
    )

    def __init__(self, entry):
        self.id = 'grammar.' + entry['id']
        self.group = entry['group']
        self.level = LEVELS[entry.get('level', 'balanced')]
        self.severity = entry.get('severity', 'advisory')
        self.compiled = _compile(entry, 'grammar')
        self.message = entry['message']
        self.highlight = entry.get('highlight', 0)
        self.replacements = entry.get('replacements', [])
        self.mapping = {k.lower(): v for k, v in entry.get('replacement_map', {}).items()}
        self.skip_dialogue = bool(entry.get('skip_dialogue'))
        self.title = entry.get('title', entry['id'])


_RULE_SETS: dict = {}
language_packs.on_refresh(_RULE_SETS.clear)


def rule_set(language):
    """Compiled rules for a language from the preferred verified pack, or None when none exist."""
    signature = language_packs.data_signature('grammar', language)
    if signature is None:
        return None
    key = (language, signature)
    if key not in _RULE_SETS:
        data = language_packs.load_json('grammar', language)
        compiled, problems = [], []
        for entry in (data or {}).get('rules', []):
            try:
                compiled.append(_Rule(entry))
            except (ByaxError, KeyError, TypeError) as exc:
                problems.append(f'{entry.get("id", "?") if isinstance(entry, dict) else "?"}: {exc}')
        _RULE_SETS[key] = {
            'language': language,
            'version': (data or {}).get('version'),
            'signature': signature,
            'groups': (data or {}).get('groups', {}),
            'rules': compiled,
            'problems': problems,
        }
    return _RULE_SETS[key]


def _shipped_rules():
    path = language_packs.RESOURCES / 'grammar' / 'en.json'
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('rules', [])
    except (OSError, ValueError):
        return []


# ``checks.RULES`` entries for the shipped English rules: (suite, family, level, languages, severity).
RULES = {
    'grammar.' + entry['id']: (
        'grammar',
        'grammar',
        entry.get('level', 'balanced'),
        ('en',),
        entry.get('severity', 'advisory'),
    )
    for entry in _shipped_rules()
}


def _apply_case(original, replacement):
    if not replacement or original == 'I':
        return replacement
    if len(original) > 1 and original.isupper() and replacement[0].isalpha():
        return replacement.upper() if len(replacement) > 1 or original == 'I' else replacement
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replacement(rule, match):
    start, end = _span(rule, match)
    original = match.string[start:end]
    results = []
    if rule.mapping:
        mapped = rule.mapping.get(original.lower().replace('’', "'"))
        if mapped is not None:
            results.append(_apply_case(original, mapped))
    for template in rule.replacements:
        try:
            expanded = match.expand(template)
        except (regex.error, IndexError):
            continue
        results.append(_apply_case(original, expanded) if template != '' else '')
    seen, unique = set(), []
    for item in results:
        if item not in seen and item != original:
            seen.add(item)
            unique.append(item)
    return unique


def _span(rule, match):
    group = rule.highlight
    if group in (0, None):
        return match.start(), match.end()
    start, end = match.span(group)
    return (start, end) if start >= 0 else (match.start(), match.end())


class BuiltinEngine:
    """The always-available deterministic tier."""

    name = 'builtin'

    def __init__(self, level=LEVELS['balanced'], disabled=()):
        self.level = level
        self.disabled = frozenset(disabled)

    def check(self, text, language):
        rules = rule_set(language)
        if rules is None or not text.strip():
            return []
        from .prose import dialogue_spans

        dialogue = None
        found = []
        for rule in rules['rules']:
            if (
                rule.level > self.level
                or rule.id in self.disabled
                or rule.id.split('.', 1)[1] in self.disabled
            ):
                continue
            try:
                matches = list(rule.compiled.finditer(text, timeout=MATCH_TIMEOUT))
            except TimeoutError:
                continue
            for match in matches:
                start, end = _span(rule, match)
                if rule.skip_dialogue:
                    if dialogue is None:
                        dialogue = dialogue_spans(text)
                    if any(a <= start < b for a, b in dialogue):
                        continue
                replacements = _replacement(rule, match)
                found.append(
                    _match(
                        rule.id,
                        rule.group,
                        'builtin',
                        rule.message.replace('{0}', text[start:end]),
                        start,
                        end,
                        text,
                        replacements,
                        rule.severity,
                    )
                )
        return _drop_overlaps(found)

    def describe(self, language):
        rules = rule_set(language)
        if rules is None:
            return None
        return {
            'language': language,
            'version': rules['version'],
            'source': rules['signature'],
            'groups': rules['groups'],
            'rules': [
                {
                    'id': r.id,
                    'group': r.group,
                    'level': next(k for k, v in LEVELS.items() if v == r.level),
                    'severity': r.severity,
                    'title': r.title,
                }
                for r in rules['rules']
            ],
            'problems': rules['problems'],
        }


def _drop_overlaps(matches):
    """Keep the first of any overlapping matches, in text order, so one error yields one finding."""
    kept = []
    for item in sorted(matches, key=lambda m: (m['start'], m['end'], m['rule'])):
        if kept and item['start'] < kept[-1]['end'] and item['rule'] != kept[-1]['rule']:
            continue
        if (
            kept
            and item['rule'] == kept[-1]['rule']
            and (item['start'], item['end']) == (kept[-1]['start'], kept[-1]['end'])
        ):
            continue
        kept.append(item)
    return kept


# Configuration and locations -------------------------------------------------------------
def settings_dir():
    override = os.environ.get('BYAX_GRAMMAR_DIR')
    if override:
        return Path(override)
    from platformdirs import user_config_path

    return user_config_path('ByAuthorX', appauthor=False)


def data_dir():
    override = os.environ.get('BYAX_GRAMMAR_DIR')
    if override:
        return Path(override) / 'data'
    from platformdirs import user_data_path

    return user_data_path('ByAuthorX', appauthor=False) / 'languagetool'


def load_config():
    config = json.loads(json.dumps(_DEFAULT_CONFIG))
    path = settings_dir() / 'grammar.json'
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return config
    if isinstance(stored, dict):
        for section in ('builtin', 'languagetool'):
            if isinstance(stored.get(section), dict):
                config[section].update({k: v for k, v in stored[section].items() if k in config[section]})
    return config


def save_config(config):
    from .providers import _atomic_json

    _atomic_json(settings_dir() / 'grammar.json', config)


# LanguageTool -----------------------------------------------------------------------------
def _loopback(url):
    from urllib.parse import urlparse

    return urlparse(url).hostname in ('127.0.0.1', 'localhost', '::1')


def server_url(config=None):
    config = config or load_config()
    return f'http://127.0.0.1:{int(config["languagetool"]["port"])}'


def _client(timeout):
    import httpx

    # trust_env=False: never route loopback traffic through a proxy.
    return httpx.Client(trust_env=False, timeout=timeout)


def _healthy(url, timeout=1.0):
    if not _loopback(url):
        return False
    try:
        with _client(timeout) as http:
            return http.get(url + '/v2/healthcheck').status_code == 200
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


def find_languagetool(directory):
    """The folder holding ``languagetool-server.jar`` given a configured folder or its parent."""
    if not directory:
        return None
    base = Path(directory).expanduser()
    if (base / 'languagetool-server.jar').is_file():
        return base
    if base.is_dir():
        for child in sorted(base.glob('LanguageTool-*'), reverse=True):
            if (child / 'languagetool-server.jar').is_file():
                return child
    return None


def _installed_directory(config):
    configured = find_languagetool(config['languagetool'].get('directory'))
    if configured:
        return configured
    return find_languagetool(data_dir() / LANGUAGETOOL['version'])


def _java(config):
    chosen = config['languagetool'].get('java')
    return chosen if chosen and Path(chosen).exists() else shutil.which(chosen or 'java')


def languagetool_status(config=None, *, probe=True):
    """Tier state: ``not installed``, ``stopped`` or ``running`` plus where and why."""
    config = config or load_config()
    lt = config['languagetool']
    directory = _installed_directory(config)
    url = server_url(config)
    running = probe and _healthy(url)
    state = 'running' if running else 'stopped' if directory else 'not installed'
    version = None
    if directory:
        match = re.search(r'LanguageTool-([\d.]+)', directory.name)
        version = match.group(1) if match else None
    java = _java(config)
    return {
        'state': state,
        'label': f'LanguageTool: {state}',
        'enabled': bool(lt['enabled']),
        'installed': directory is not None,
        'running': bool(running),
        'directory': str(directory) if directory else None,
        'version': version,
        'java': java,
        'port': int(lt['port']),
        'url': url,
        'managed_by_byax': _read_pid(config) is not None,
    }


def _pid_file():
    return data_dir() / 'languagetool.pid.json'


def _read_pid(config):
    try:
        record = json.loads(_pid_file().read_text(encoding='utf-8'))
        pid = int(record['pid'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return record if _alive(pid) else None


def _alive(pid):
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def start_languagetool(config=None, *, wait=60.0, popen=subprocess.Popen):
    """Start the local server from the installed folder; refuses without a folder or Java."""
    config = config or load_config()
    status = languagetool_status(config)
    if status['running']:
        return {**status, 'started': False, 'message': 'A LanguageTool server already answers on this port.'}
    directory = _installed_directory(config)
    if directory is None:
        raise ByaxError(
            'missing_dependency',
            'LanguageTool is not installed. Point to a LanguageTool folder with `grammar --action configure --languagetool-dir`, or install it with `grammar --action install-languagetool`.',
        )
    java = _java(config)
    if not java:
        raise ByaxError('missing_dependency', f'Java was not found; LanguageTool {LANGUAGETOOL["requires"]}.')
    lt = config['languagetool']
    log = data_dir() / 'languagetool.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [
        java,
        f'-Xmx{int(lt["memory_mb"])}m',
        '-cp',
        'languagetool-server.jar',
        'org.languagetool.server.HTTPServer',
        '--port',
        str(int(lt['port'])),
    ]
    options = {'cwd': str(directory), 'stdin': subprocess.DEVNULL}
    if os.name == 'nt':
        options['creationflags'] = 0x08000000 | 0x00000008
    else:
        options['start_new_session'] = True
    with log.open('ab') as handle:
        process = popen(command, stdout=handle, stderr=handle, **options)
    _pid_file().write_text(json.dumps({'pid': process.pid, 'port': int(lt['port'])}), encoding='utf-8')
    # Reap the child when it exits so a stopped server does not stay a zombie for the session.
    threading.Thread(target=process.wait, daemon=True).start()
    deadline = time.monotonic() + wait
    url = server_url(config)
    while time.monotonic() < deadline:
        if _healthy(url, 1.0):
            return {**languagetool_status(config), 'started': True, 'pid': process.pid}
        if process.poll() is not None:
            break
        time.sleep(0.25)
    raise ByaxError(
        'execution_failure',
        'LanguageTool did not become ready in time; see the log for details.',
        log=str(log),
    )


def stop_languagetool(config=None, *, wait=10.0):
    """Stop a server this application started; never signals a process it did not record."""
    config = config or load_config()
    record = _read_pid(config)
    if record is None:
        status = languagetool_status(config)
        message = (
            'The running server was not started by ByAuthorX; stop it where you started it.'
            if status['running']
            else 'No server started by ByAuthorX is running.'
        )
        return {**status, 'stopped': False, 'message': message}
    pid = int(record['pid'])
    cmdline = Path(f'/proc/{pid}/cmdline')
    if cmdline.exists() and b'languagetool' not in cmdline.read_bytes().lower():
        _pid_file().unlink(missing_ok=True)
        return {
            **languagetool_status(config),
            'stopped': False,
            'message': 'The recorded process is not LanguageTool.',
        }
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.monotonic() + wait
    url = server_url(config)
    while time.monotonic() < deadline and _healthy(url, 0.5):
        time.sleep(0.2)
    _pid_file().unlink(missing_ok=True)
    return {**languagetool_status(config), 'stopped': True}


def installation_plan():
    return {k: LANGUAGETOOL[k] for k in ('version', 'url', 'size', 'sha256', 'licence', 'requires')}


def install_languagetool(*, confirm_sha256=None, plan=None, progress=None, client=None):
    """Download and unpack the pinned official LanguageTool zip after explicit confirmation.

    Without ``confirm_sha256`` equal to the pinned digest nothing is downloaded and the plan (source
    URL, size, SHA-256, licence) is returned. The archive is verified for size and SHA-256 before a
    single file is extracted, and extraction refuses unsafe member names.
    """
    plan = plan or LANGUAGETOOL
    shown = {k: plan[k] for k in ('version', 'url', 'size', 'sha256', 'licence', 'requires') if k in plan}
    if str(confirm_sha256 or '').lower() != plan['sha256']:
        return {
            'downloaded': False,
            'confirmation_required': True,
            'plan': shown,
            'message': 'Nothing was downloaded. To install, repeat the command with --sha256 set to the digest shown, confirming the source, size and checksum.',
        }
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = root / plan['version']
    stage = Path(tempfile.mkdtemp(prefix='.install-', dir=root))
    archive = stage / 'download.zip'
    try:
        digest, total = hashlib.sha256(), 0
        owned = client is None
        if owned:
            import httpx

            client = httpx.Client(follow_redirects=True, timeout=120)
        try:
            with client.stream('GET', plan['url']) as response:
                response.raise_for_status()
                with archive.open('wb') as handle:
                    for chunk in response.iter_bytes(1 << 20):
                        total += len(chunk)
                        if total > plan['size']:
                            raise ByaxError(
                                'limit_exceeded', 'The download is larger than the published size'
                            )
                        digest.update(chunk)
                        handle.write(chunk)
                        if progress:
                            progress(total, plan['size'])
        finally:
            if owned:
                client.close()
        if total != plan['size'] or digest.hexdigest() != plan['sha256']:
            raise ByaxError(
                'checksum_mismatch',
                'The downloaded file does not match the pinned size and SHA-256; nothing was installed',
                actual=digest.hexdigest(),
                bytes=total,
            )
        unpacked = stage / 'unpacked'
        _extract_languagetool(archive, unpacked, plan['archive_root'])
        if target.exists():
            shutil.rmtree(target)
        os.replace(unpacked, target)
        (target / 'install.json').write_text(
            json.dumps({'source': plan['url'], 'sha256': plan['sha256'], 'size': plan['size']}, indent=2),
            encoding='utf-8',
        )
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    config = load_config()
    config['languagetool']['directory'] = str(target / plan['archive_root'])
    config['languagetool']['enabled'] = True
    save_config(config)
    return {
        'downloaded': True,
        'confirmation_required': False,
        'plan': shown,
        'directory': str(target / plan['archive_root']),
        'status': languagetool_status(config),
    }


def _extract_languagetool(archive, destination, root):
    limit = 2 * 1024**3
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > 20000 or sum(i.file_size for i in infos) > limit:
            raise ByaxError('limit_exceeded', 'The LanguageTool archive is larger than expected')
        for info in infos:
            name = info.filename
            parts = name.split('/')
            mode = info.external_attr >> 16
            if (
                name.startswith('/')
                or '\\' in name
                or '..' in parts
                or parts[0] != root
                or (mode and stat.S_ISLNK(mode))
            ):
                raise ByaxError(
                    'invalid_input', 'The LanguageTool archive has an unsafe member name', member=name
                )
        zf.extractall(destination)
    if not (destination / root / 'languagetool-server.jar').is_file():
        raise ByaxError('invalid_input', 'The LanguageTool archive does not contain languagetool-server.jar')


# Offsets ------------------------------------------------------------------------------------
def utf16_index(text):
    """UTF-16 offset of each scalar index, plus the total; LanguageTool (Java) counts UTF-16 units."""
    boundaries, position = [], 0
    for char in text:
        boundaries.append(position)
        position += 2 if ord(char) > 0xFFFF else 1
    boundaries.append(position)
    return boundaries


def to_scalar_range(boundaries, offset, length):
    """Map a UTF-16 ``offset``/``length`` to ``(start, end)`` scalar indices, snapping out of surrogates."""
    start = bisect.bisect_right(boundaries, offset) - 1
    end = bisect.bisect_left(boundaries, offset + length)
    return max(start, 0), min(max(end, start), len(boundaries) - 1)


class LanguageToolEngine:
    """Talks to a local LanguageTool HTTP server; results are cached per text and language."""

    name = 'languagetool'

    def __init__(self, url, *, timeout=15, include_spelling=False, client=None):
        if not _loopback(url):
            raise ByaxError(
                'invalid_input', 'LanguageTool is only used through a loopback address', field='url'
            )
        self.url = url.rstrip('/')
        self.timeout = timeout
        self.include_spelling = include_spelling
        self._client = client
        self._cache: dict = {}
        self._languages: set[str] | None = None
        self.version = None

    def _http(self):
        return self._client or _client(self.timeout)

    def supported(self, tag):
        if self._languages is None:
            try:
                with self._http() as http:
                    response = http.get(self.url + '/v2/languages')
                    response.raise_for_status()
                self._languages = {
                    c for entry in response.json() for c in (entry.get('code'), entry.get('longCode')) if c
                }
            except Exception:  # noqa: BLE001
                self._languages = set()
        return tag in self._languages

    @staticmethod
    def language_code(tag):
        """LanguageTool code for a document language tag (``en`` becomes ``en-US``)."""
        tag = (tag or 'en').replace('_', '-')
        base = tag.split('-')[0].lower()
        if '-' in tag:
            return base + '-' + tag.split('-', 1)[1].upper()
        return _LT_REGIONS.get(base, base)

    def check(self, text, language):
        code = self.language_code(language)
        key = (text, code, self.include_spelling)
        if key in self._cache:
            return self._cache[key]
        if not self.supported(code):
            raise ByaxError(
                'unsupported_feature', f'LanguageTool does not support the language "{code}"', language=code
            )
        try:
            with self._http() as http:
                response = http.post(
                    self.url + '/v2/check',
                    data={'text': text, 'language': code, 'level': 'default'},
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise ByaxError('execution_failure', f'LanguageTool request failed: {exc}') from exc
        self.version = (payload.get('software') or {}).get('version', self.version)
        boundaries = utf16_index(text)
        matches = []
        for item in payload.get('matches', []):
            rule = item.get('rule') or {}
            rule_id = rule.get('id', 'UNKNOWN')
            if rule_id in LT_DUPLICATES or (not self.include_spelling and _LT_SPELLING.search(rule_id)):
                continue
            start, end = to_scalar_range(boundaries, int(item['offset']), int(item['length']))
            category = (rule.get('category') or {}).get('id', '')
            matches.append(
                _match(
                    'grammar.languagetool.' + rule_id,
                    category.lower() or 'languagetool',
                    'languagetool',
                    item.get('message') or rule.get('description') or rule_id,
                    start,
                    end,
                    text,
                    [r['value'] for r in item.get('replacements', [])[:5] if isinstance(r.get('value'), str)],
                    'advisory',
                    lt_rule=rule_id,
                    issue_type=rule.get('issueType'),
                    category=category,
                )
            )
        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[key] = matches
        return matches


# Integration with checks.py --------------------------------------------------------------------
_TIERS: dict = {}


def tier_state(config=None):
    """What the report says about the optional tier, without contacting the server unless enabled."""
    config = config or load_config()
    status = languagetool_status(config, probe=bool(config['languagetool']['enabled']))
    return config, status


def tier_signature():
    """A short fingerprint of the grammar tiers; part of every block unit's dependency fingerprint."""
    config, status = tier_state()
    lt = config['languagetool']
    if lt['enabled'] and lt['autostart'] and status['installed'] and not status['running']:
        try:
            status = start_languagetool(config)
        except ByaxError:
            pass
    signature = {
        'builtin': [
            BUILTIN_VERSION,
            config['builtin']['enabled'],
            sorted(config['builtin']['disabled_rules']),
        ],
        'rules': language_packs.data_signature('grammar', 'en'),
        'languagetool': [status['state'], status['version'], lt['include_spelling']]
        if lt['enabled']
        else 'off',
    }
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]


def _tiers(ctx):
    """Per-run engines, built once per ``checks._Context``."""
    cached = getattr(ctx, '_grammar_tiers', None)
    if cached is None:
        config, status = tier_state()
        builtin = (
            BuiltinEngine(LEVELS['broad'], config['builtin']['disabled_rules'])
            if config['builtin']['enabled']
            else None
        )
        lt = config['languagetool']
        engine = None
        if lt['enabled'] and status['running']:
            engine = LanguageToolEngine(
                status['url'], timeout=lt['timeout'], include_spelling=lt['include_spelling']
            )
        cached = ctx._grammar_tiers = {
            'config': config,
            'status': status,
            'builtin': builtin,
            'languagetool': engine,
        }
    return cached


def language_tag(doc, block):
    """The full language tag for a block: its own attribute, else the document language."""
    value = block.get('attrs', {}).get('language') or doc.get('metadata', {}).get('language') or 'en'
    return value if isinstance(value, str) else 'en'


def evaluate_text(ctx, sink, block, target, text, language, level):
    """Run the grammar tiers over one text and add findings to ``sink`` (called from checks.py)."""
    from .checks import _replace_ops

    tiers = _tiers(ctx)
    tag = language_tag(ctx.doc, block)
    claimed = []

    def add(item):
        replacements = item['replacements']
        operations = (
            _replace_ops(target, text, item['start'], item['end'], replacements[0]) if replacements else []
        )
        measured = {'engine': item['engine'], 'group': item['group']}
        if item['engine'] == 'languagetool':
            measured.update(rule=item['lt_rule'], category=item['category'], issue_type=item['issue_type'])
        if len(replacements) > 1:
            measured['alternatives'] = replacements[1:]
        sink.add(
            item['rule'],
            'grammar',
            item['severity'],
            item['message'],
            target,
            item['start'],
            item['end'],
            item['text'],
            operations,
            measured=measured,
        )

    builtin = tiers['builtin']
    if builtin is not None:
        builtin.level = level
        rules = rule_set(language)
        if rules is not None and rules['problems']:
            sink.cover(
                'grammar',
                'grammar.builtin',
                'incomplete',
                f'{len(rules["problems"])} built-in {language} grammar rules could not be loaded and did not run.',
                language=language,
                targets=[block['id']],
            )
        for item in builtin.check(text, language):
            claimed.append((item['start'], item['end']))
            add(item)
    engine = tiers['languagetool']
    if engine is not None:
        try:
            for item in engine.check(text, tag):
                if any(item['start'] < b and a < item['end'] for a, b in claimed):
                    continue
                add(item)
        except ByaxError as exc:
            sink.cover(
                'grammar',
                'grammar.languagetool',
                'incomplete' if exc.code != 'unsupported_feature' else 'unmeasurable',
                f'LanguageTool could not check this text: {exc.message}',
                language=language,
                targets=[block['id']],
            )


def tier_coverage():
    """One report-level coverage entry telling the truth about the optional tier."""
    config, status = tier_state()
    lt = config['languagetool']
    base = {'suite': 'grammar', 'rule': 'grammar.languagetool', 'level': 'report', 'tier': 'languagetool'}
    if not lt['enabled']:
        if status['installed']:
            reason = 'LanguageTool: installed but off. Enable it with `byax grammar --action enable`; only the built-in rules ran.'
        else:
            reason = 'LanguageTool: not installed. Only the narrow built-in rules ran; the optional LanguageTool tier adds broad grammar and style coverage.'
        return {**base, 'status': 'unmeasurable', 'reason': reason, 'state': status['state']}
    if not status['running']:
        return {
            **base,
            'status': 'incomplete',
            'reason': f'LanguageTool: {status["state"]}. It is enabled but no local server answered; start it with `byax grammar --action start`.',
            'state': status['state'],
        }
    return {
        **base,
        'status': 'completed',
        'reason': f'LanguageTool {status["version"] or ""} answered on {status["url"]}.'.replace('  ', ' '),
        'state': 'running',
    }


def check_text(text, language='en', *, engine='all', level='broad'):
    """Check plain text with the chosen tier(s); returns matches in text order."""
    if engine not in ('all', 'builtin', 'languagetool'):
        raise ByaxError('invalid_input', 'engine must be all, builtin or languagetool', field='engine')
    config = load_config()
    status = languagetool_status(config)
    result = []
    if engine in ('all', 'builtin'):
        result.extend(
            BuiltinEngine(LEVELS[level], config['builtin']['disabled_rules']).check(
                text, language.split('-')[0].lower()
            )
        )
    if engine in ('all', 'languagetool'):
        lt = config['languagetool']
        if status['running'] and (lt['enabled'] or engine == 'languagetool'):
            tier = LanguageToolEngine(
                status['url'], timeout=lt['timeout'], include_spelling=lt['include_spelling']
            )
            claimed = [(m['start'], m['end']) for m in result]
            result.extend(
                m
                for m in tier.check(text, language)
                if not any(m['start'] < b and a < m['end'] for a, b in claimed)
            )
        elif engine == 'languagetool':
            raise ByaxError(
                'missing_dependency',
                f'LanguageTool is {status["state"]}; see `byax grammar --action status`.',
            )
    return sorted(result, key=lambda m: (m['start'], m['end'], m['rule']))


# Command (CLI, MCP, REST) ---------------------------------------------------------------------
ADMIN_ACTIONS = ('configure', 'enable', 'disable', 'install-languagetool', 'start', 'stop')


def grammar_command(a, owner):
    from .cli import _error, envelope

    action = a.get('action', 'status')
    if action == 'status':
        config, status = tier_state()
        builtin = BuiltinEngine().describe('en')
        return envelope(
            {
                'grammar': {
                    'builtin': {
                        'enabled': config['builtin']['enabled'],
                        'label': 'Built-in rules',
                        'languages': language_packs.available_languages('grammar'),
                        'rules': len(builtin['rules']) if builtin else 0,
                        'version': BUILTIN_VERSION,
                    },
                    'languagetool': status,
                    'languagetool_plan': installation_plan(),
                }
            }
        )
    if action == 'rules':
        languages = [a['language']] if a.get('language') else language_packs.available_languages('grammar')
        sets = [d for d in (BuiltinEngine().describe(lang) for lang in languages) if d]
        if a.get('group'):
            for item in sets:
                item['rules'] = [r for r in item['rules'] if r['group'] == a['group']]
        return envelope({'rule_sets': sets})
    if action == 'check':
        if not isinstance(a.get('text'), str):
            _error('invalid_input', 'grammar check needs --text', field='text')
        matches = check_text(a['text'], a.get('language', 'en'), engine=a.get('engine', 'all'))
        return envelope({'matches': matches, 'count': len(matches)})
    config = load_config()
    lt = config['languagetool']
    if action == 'install-languagetool':
        return envelope(install_languagetool(confirm_sha256=a.get('sha256')))
    if action == 'enable':
        lt['enabled'] = True
        save_config(config)
        return envelope({'languagetool': languagetool_status(config)})
    if action == 'disable':
        lt['enabled'] = False
        save_config(config)
        return envelope({'languagetool': languagetool_status(config)})
    if action == 'configure':
        for key, field in (
            ('port', 'port'),
            ('languagetool_dir', 'directory'),
            ('java', 'java'),
            ('memory_mb', 'memory_mb'),
            ('autostart', 'autostart'),
            ('include_spelling', 'include_spelling'),
        ):
            if key in a and a[key] is not None:
                lt[field] = a[key]
        if not 1024 <= int(lt['port']) <= 65535:
            _error('invalid_input', 'port must be between 1024 and 65535', field='port')
        if lt['directory'] and find_languagetool(lt['directory']) is None:
            _error(
                'invalid_input', 'No languagetool-server.jar found in that folder', field='languagetool_dir'
            )
        save_config(config)
        return envelope({'languagetool': languagetool_status(config)})
    if action == 'start':
        return envelope({'languagetool': start_languagetool(config)})
    if action == 'stop':
        return envelope({'languagetool': stop_languagetool(config)})
    _error('invalid_input', f'Unknown grammar action: {action}', field='action')
