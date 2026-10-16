"""Saved command macros (competitiveness plan 7.4): a named, versioned sequence of CLI commands.

A macro is JSON::

    {"schema": 1, "name": "proof-pass", "version": 1, "description": "...",
     "parameters": {"suite": {"type": "array", "default": ["grammar"]}},
     "steps": [{"label": "Check", "command": "check", "arguments": {"path": "{document}", "suite": "{suite}"}}]}

Every step is dispatched through ``cli.execute``, so it has the same envelope, validation and
preconditions as the same command typed at the CLI, and the same result over MCP and REST. A macro
adds no capability of its own:

* mutating steps carry ``expect_revision`` automatically (the revision the previous step produced), so
  a document that changes underneath a macro stops it with ``stale_revision``;
* ``dry_run`` runs read-only steps for real and every other step with its own ``dry_run`` option; steps
  that have none are skipped and said so in the transcript;
* the first failing step (an error, or a result whose exit code is not 0) stops the run;
* every step is checked with ``transport_policy.check_provider_admin`` exactly as a transport request
  would be, unless the run was started from the local CLI with ``allow_admin``; and when the macro runs
  behind the REST service the service's path policy is applied to each step too.

Macros live in the user config folder (``macros.json``) or in the document's ``settings.macros``
(changed through a canonical ``set_settings`` operation, so it is a revision like any other edit).
"""

from __future__ import annotations

import contextlib
import copy
import re
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .errors import ByaxError

SCHEMA = 1
MAX_STEPS = 100
MAX_JSON_BYTES = 256 * 1024
NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}')
PARAMETER = re.compile(r'[a-z_][a-z0-9_]{0,31}')
BUILTIN_PLACEHOLDERS = ('document', 'revision')
STORES = ('user', 'document')
ACTIONS = ('list', 'show', 'save', 'run', 'remove')
PARAMETER_TYPES = ('string', 'integer', 'boolean', 'array', 'object')
_PLACEHOLDER = re.compile(r'\{\{|\}\}|\{([a-z_][a-z0-9_]*)\}')

# Commands that never change a document, a file or a setting; for others only the action decides.
READ_ONLY_COMMANDS = frozenset(
    {
        'version',
        'capabilities',
        'schema',
        'inspect',
        'find',
        'context',
        'diff',
        'check',
        'statistics',
        'readability',
        'library-search',
        'library-read',
        'entity',
        'view',
    }
)
READ_ONLY_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    # command: (default action, read-only actions)
    'review': ('list', ('list',)),
    'history': ('list', ('list',)),
    'goals': ('status', ('status', 'history')),
    'drafts': ('list', ('list', 'compare')),
    'sync': ('status', ('status', 'conflicts')),
    'codex': ('list', ('list', 'lookup', 'mentions', 'heatmap', 'series')),
    'project': ('list', ('list', 'tree', 'reconcile', 'blocks', 'compile')),
    'project-search': (
        'find',
        ('find', 'status', 'saved-list', 'saved-run', 'backlinks', 'unlinked-mentions'),
    ),
    'library': ('list', ('list', 'status', 'search', 'read', 'verify-absence')),
    'grammar': ('status', ('status', 'rules', 'check')),
    'languages': ('list', ('list', 'verify', 'coverage')),
    'extensions': ('list', ('list', 'info', 'verify', 'scopes', 'run-check')),
    'compile': ('run', ('presets', 'show')),
    'fonts': ('inspect', ('inspect', 'glyphs')),
    'dependencies': ('inspect', ('inspect',)),
    'review-package': ('create', ('list',)),
    'providers': ('list', ('list', 'preview')),
    'jobs': ('list', ('list', 'status', 'result')),
}

# Set by the REST service while it runs a macro: its path policy is applied to every step.
STEP_POLICY: ContextVar[Callable[[str, dict], None] | None] = ContextVar(
    'byax_macro_step_policy', default=None
)


@contextlib.contextmanager
def step_policy(check: Callable[[str, dict], None]) -> Iterator[None]:
    token = STEP_POLICY.set(check)
    try:
        yield
    finally:
        STEP_POLICY.reset(token)


def _invalid(message: str, **details: Any):
    raise ByaxError('invalid_input', message, **details)


def is_read_only(command: str, arguments: dict) -> bool:
    if command in READ_ONLY_COMMANDS:
        return not (command == 'check' and arguments.get('record'))
    if command in READ_ONLY_ACTIONS:
        default, actions = READ_ONLY_ACTIONS[command]
        action = arguments.get('action', default)
        if command == 'compile':
            return action in actions or bool(arguments.get('preflight_only'))
        return action in actions
    return False


# Validation ---------------------------------------------------------------------------------
def _placeholders(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        for match in _PLACEHOLDER.finditer(value):
            if match.group(1):
                yield match.group(1)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _placeholders(child)
    elif isinstance(value, list):
        for child in value:
            yield from _placeholders(child)


def validate_macro(value: Any, *, name: str | None = None) -> dict:
    """Normalized macro or ``invalid_input`` naming the first problem."""
    from .cli import ARGUMENTS, COMMANDS

    if not isinstance(value, dict) or set(value) - {
        'schema',
        'name',
        'version',
        'description',
        'parameters',
        'steps',
    }:
        _invalid('A macro is an object with name, version, description, parameters and steps')
    if value.get('schema', SCHEMA) != SCHEMA:
        raise ByaxError(
            'unsupported_version', f'Unsupported macro schema; this version reads schema {SCHEMA}'
        )
    identifier = name if name is not None else value.get('name')
    if not isinstance(identifier, str) or not NAME.fullmatch(identifier):
        _invalid(
            'Macro names are 1 to 64 lowercase letters, digits, dots, dashes and underscores', field='name'
        )
    version = value.get('version', 1)
    if type(version) is not int or version < 1:
        _invalid('Macro version is a whole number from 1', field='version')
    description = value.get('description', '')
    if not isinstance(description, str) or len(description) > 1000:
        _invalid('Macro description must be short text', field='description')
    parameters = value.get('parameters', {})
    if not isinstance(parameters, dict) or len(parameters) > 20:
        _invalid('parameters is an object of at most 20 declarations', field='parameters')
    declared: dict[str, dict] = {}
    for key, spec in parameters.items():
        if not isinstance(key, str) or not PARAMETER.fullmatch(key) or key in BUILTIN_PLACEHOLDERS:
            _invalid(
                f'Parameter names are lowercase words and may not be {list(BUILTIN_PLACEHOLDERS)}', field=key
            )
        if not isinstance(spec, dict) or set(spec) - {'type', 'default', 'required', 'description'}:
            _invalid('A parameter declares type, default, required and description', field=key)
        kind = spec.get('type', 'string')
        if kind not in PARAMETER_TYPES:
            _invalid('Unknown parameter type', field=key, supported=list(PARAMETER_TYPES))
        if 'default' in spec and not _of_type(spec['default'], kind):
            _invalid('The default does not match the parameter type', field=key)
        declared[key] = {
            'type': kind,
            **({'default': spec['default']} if 'default' in spec else {}),
            'required': bool(spec.get('required', False)),
            'description': str(spec.get('description', ''))[:300],
        }
    steps = value.get('steps')
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        _invalid(f'A macro has between 1 and {MAX_STEPS} steps', field='steps')
    normalized = []
    for index, step in enumerate(steps, 1):
        field = f'steps[{index}]'
        if not isinstance(step, dict) or set(step) - {'label', 'command', 'arguments'}:
            _invalid('A step has label, command and arguments only', field=field)
        command, arguments = step.get('command'), step.get('arguments', {})
        if command == 'macro':
            _invalid('A macro cannot run another macro', field=field)
        if not isinstance(command, str) or command not in COMMANDS:
            _invalid(f'Unknown command: {command}', field=field)
        if not isinstance(arguments, dict):
            _invalid('Step arguments must be an object', field=field)
        for key in sorted(set(arguments) - set(ARGUMENTS[command])):
            _invalid(f'{command} has no argument {key}', field=field)
        for placeholder in _placeholders(arguments):
            if placeholder not in declared and placeholder not in BUILTIN_PLACEHOLDERS:
                _invalid(f'Placeholder {{{placeholder}}} is not a declared parameter', field=field)
        label = step.get('label', command)
        if not isinstance(label, str) or len(label) > 100:
            _invalid('A step label is short text', field=field)
        normalized.append({'label': label, 'command': command, 'arguments': copy.deepcopy(arguments)})
    result = {
        'schema': SCHEMA,
        'name': identifier,
        'version': version,
        'description': description,
        'parameters': declared,
        'steps': normalized,
    }
    import json

    if len(json.dumps(result)) > MAX_JSON_BYTES:
        raise ByaxError('limit_exceeded', 'The macro is too large')
    return result


def _of_type(value: Any, kind: str) -> bool:
    return {
        'string': isinstance(value, str),
        'integer': type(value) is int,
        'boolean': type(value) is bool,
        'array': isinstance(value, list),
        'object': isinstance(value, dict),
    }[kind]


def needs_admin(macro: dict) -> list[int]:
    """1-based indexes of steps a transport would refuse (they run only with ``allow_admin``)."""
    from .transport_policy import check_provider_admin

    flagged = []
    for index, step in enumerate(macro['steps'], 1):
        try:
            check_provider_admin(step['command'], copy.deepcopy(step['arguments']))
        except ByaxError:
            flagged.append(index)
    return flagged


def describe(macro: dict, store: str) -> dict:
    return {
        'name': macro['name'],
        'version': macro['version'],
        'description': macro['description'],
        'store': store,
        'steps': len(macro['steps']),
        'parameters': sorted(macro['parameters']),
        'admin_steps': needs_admin(macro),
    }


# Stores -------------------------------------------------------------------------------------
def _user_path(config_dir: str | Path | None) -> Path:
    from .goals import default_dir

    return Path(config_dir or default_dir()) / 'macros.json'


def user_macros(config_dir: str | Path | None = None) -> dict[str, dict]:
    import json

    path = _user_path(config_dir)
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ByaxError(
            'execution_failure', f'The macros file cannot be read; move or repair {path}.', path=str(path)
        ) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get('macros'), dict):
        raise ByaxError('execution_failure', f'The macros file {path} is not a macros file.', path=str(path))
    return {name: validate_macro(macro, name=name) for name, macro in raw['macros'].items()}


def _write_user(macros: dict[str, dict], config_dir: str | Path | None) -> None:
    from .providers import _atomic_json

    _atomic_json(_user_path(config_dir), {'schema': SCHEMA, 'macros': macros})


def document_macros(doc: dict) -> dict[str, dict]:
    stored = doc.get('settings', {}).get('macros', {})
    if not isinstance(stored, dict):
        return {}
    result = {}
    for name, macro in stored.items():
        with contextlib.suppress(ByaxError):  # an invalid stored macro is listed as absent, never run
            result[name] = validate_macro(macro, name=name)
    return result


def find(
    name: str, *, doc: dict | None, config_dir: str | Path | None, store: str | None
) -> tuple[dict, str]:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        _invalid('Macro names are lowercase letters, digits, dots, dashes and underscores', field='name')
    found = []
    if store in (None, 'user'):
        macros = user_macros(config_dir)
        if name in macros:
            found.append((macros[name], 'user'))
    if store in (None, 'document') and doc is not None:
        macros = document_macros(doc)
        if name in macros:
            found.append((macros[name], 'document'))
    if not found:
        raise ByaxError('missing_target', 'No such macro', target=name)
    if len(found) > 1:
        raise ByaxError(
            'ambiguous_selection',
            'A user macro and a document macro share this name; choose --store',
            target=name,
        )
    return found[0]


# Running ------------------------------------------------------------------------------------
def _substitute(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole and whole.group(1):
            return copy.deepcopy(params[whole.group(1)])

        def replace(match: re.Match) -> str:
            if match.group(0) == '{{':
                return '{'
            if match.group(0) == '}}':
                return '}'
            return str(params[match.group(1)])

        return _PLACEHOLDER.sub(replace, value)
    if isinstance(value, dict):
        return {k: _substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params) for v in value]
    return value


def resolve_parameters(macro: dict, supplied: dict | None, document: str | None) -> dict[str, Any]:
    supplied = dict(supplied or {})
    params: dict[str, Any] = {}
    unknown = sorted(set(supplied) - set(macro['parameters']) - {'document'})
    if unknown:
        _invalid(f'Unknown macro parameters: {unknown}', field='params')
    for key, spec in macro['parameters'].items():
        if key in supplied:
            if not _of_type(supplied[key], spec['type']):
                _invalid(f'Parameter {key} must be {spec["type"]}', field='params')
            params[key] = supplied[key]
        elif 'default' in spec:
            params[key] = copy.deepcopy(spec['default'])
        elif spec['required']:
            _invalid(f'Macro parameter {key} is required', field='params')
        else:
            params[key] = None
    if document is not None:
        params['document'] = document
    return params


def _summary(result: dict) -> dict:
    summary = {
        key: result[key]
        for key in ('execution', 'validation', 'review', 'revision', 'outputs')
        if key in result
    }
    summary['changed'] = len(result.get('changed_ids', []))
    if isinstance(result.get('summary'), dict):
        summary['summary'] = result['summary']
    return summary


def run_macro(
    macro: dict,
    *,
    params: dict[str, Any] | None = None,
    document: str | None = None,
    dry_run: bool = False,
    allow_admin: bool = False,
    expect_revision: str | None = None,
    verbose: bool = False,
    execute: Callable[[str, dict], dict] | None = None,
) -> dict:
    """Run a validated macro and return its transcript; stops at the first failing step."""
    from .cli import ARGUMENTS, error_result, result_exit_code
    from .cli import execute as run_command
    from .transport_policy import check_provider_admin

    execute = execute or run_command
    values = resolve_parameters(macro, params, document)
    revisions: dict[str, str] = {}
    if expect_revision and document:
        revisions[document] = expect_revision
    transcript: list[dict[str, Any]] = []
    outcome: dict[str, Any] = {
        'dry_run': dry_run,
        'status': 'completed',
        'transcript': transcript,
        'failed_step': None,
    }
    for index, step in enumerate(macro['steps'], 1):
        command = step['command']
        entry: dict[str, Any] = {
            'step': index,
            'label': step['label'],
            'command': command,
            'status': 'completed',
        }
        transcript.append(entry)
        started = time.monotonic()
        try:
            # {revision} resolves to the revision the previous step left the step's document at.
            holder = {
                **values,
                'revision': revisions.get(str(_substitute(step['arguments'].get('path'), values))),
            }
            arguments = _substitute(step['arguments'], holder)
            if 'action' in arguments:
                entry['action'] = arguments['action']
            if not allow_admin:
                try:
                    check_provider_admin(command, arguments)
                except ByaxError as exc:
                    entry['status'] = 'refused'
                    raise ByaxError(
                        'unsupported_feature',
                        f'Step {index} ({command}) is an administrative action and is refused: {exc.message} '
                        '(run it from the local CLI with --allow-admin)',
                        step=index,
                    ) from exc
            policy = STEP_POLICY.get()
            if policy is not None:
                policy(command, arguments)  # raises; may rewrite scoped paths in place
            read_only = is_read_only(command, arguments)
            path = arguments.get('path') if isinstance(arguments.get('path'), str) else None
            if (
                not read_only
                and path
                and 'expect_revision' in ARGUMENTS[command]
                and 'expect_revision' not in arguments
            ):
                if path not in revisions:
                    revisions[path] = str(run_command('inspect', {'path': path, 'limit': 1})['revision'])
                arguments['expect_revision'] = revisions[path]
            if dry_run and not read_only:
                if 'dry_run' not in ARGUMENTS[command]:
                    entry.update(
                        status='skipped', note='No dry run is available for this step; it was not run.'
                    )
                    continue
                arguments['dry_run'] = True
                entry['dry_run'] = True
            result = execute(command, arguments)
            code = result_exit_code(result)
            entry['exit_code'] = code
            entry['summary'] = _summary(result)
            if verbose:
                entry['result'] = result
            if code:
                error = result.get('error') or {
                    'code': {4: 'failed_requirement', 5: 'incomplete_coverage'}.get(
                        code, 'execution_failure'
                    ),
                    'message': f'Step {index} ({command}) finished with exit code {code}',
                }
                raise ByaxError(error.get('code', 'execution_failure'), error.get('message', ''), step=index)
            if not dry_run and not read_only and path and result.get('revision'):
                revisions[path] = str(result['revision'])
        except Exception as exc:  # noqa: BLE001 - every failure becomes a transcript entry
            data, code = error_result(exc)
            entry.setdefault('status', 'failed')
            if entry['status'] != 'refused':
                entry['status'] = 'failed'
            entry['exit_code'] = code
            entry['error'] = data['error']
            outcome.update(status='failed', failed_step=index, error=data['error'], exit_code=code)
            entry['seconds'] = round(time.monotonic() - started, 3)
            break
        finally:
            entry['seconds'] = round(time.monotonic() - started, 3)
    outcome['steps_run'] = sum(1 for e in transcript if e['status'] in ('completed', 'failed', 'refused'))
    outcome['steps_total'] = len(macro['steps'])
    return outcome


# Command ------------------------------------------------------------------------------------
def macro_command(a: dict, owner: str | None = None) -> dict:
    """``byax macro``: list, show, save, run and remove saved command macros."""
    from .cli import _error, _load, envelope

    action = a.get('action', 'list')
    if action not in ACTIONS:
        _error('invalid_input', f'Unknown macro action: {action}', supported=list(ACTIONS))
    store = a.get('store')
    if store is not None and store not in STORES:
        _error('invalid_input', 'store is user or document', field='store')
    config_dir = a.get('config_dir')
    document = a.get('document')
    doc = _load(document) if document else None
    if store == 'document' and doc is None:
        _error('invalid_input', 'The document store needs --document', field='document')

    if action == 'list':
        rows = []
        if store in (None, 'user'):
            rows += [describe(m, 'user') for m in user_macros(config_dir).values()]
        if doc is not None and store in (None, 'document'):
            rows += [describe(m, 'document') for m in document_macros(doc).values()]
        return envelope({'macros': sorted(rows, key=lambda r: (r['name'], r['store']))}, doc)
    if action == 'show':
        macro, where = find(a.get('name', ''), doc=doc, config_dir=config_dir, store=store)
        return envelope({'macro': macro, 'store': where, 'admin_steps': needs_admin(macro)}, doc)
    if action == 'save':
        macro = validate_macro(a.get('input'), name=a.get('name'))
        where = store or 'user'
        existing = (user_macros(config_dir) if where == 'user' else document_macros(doc or {})).get(
            macro['name']
        )
        if existing and macro['version'] <= existing['version'] and not a.get('replace'):
            raise ByaxError(
                'conflict',
                'A macro with this name and the same or a newer version is saved; raise its version or use --replace',
                installed=existing['version'],
                offered=macro['version'],
            )
        if where == 'user':
            macros = user_macros(config_dir)
            macros[macro['name']] = macro
            _write_user(macros, config_dir)
            return envelope({'macro': describe(macro, 'user')})
        macros = document_macros(doc or {})
        macros[macro['name']] = macro
        request: dict[str, Any] = {
            'path': document,
            'input': {'operations': [{'type': 'set_settings', 'values': {'macros': macros}}]},
        }
        for option in ('expect_revision', 'request_id'):
            if a.get(option):
                request[option] = a[option]
        from .cli import execute

        result = execute('apply', request)
        result['macro'] = describe(macro, 'document')
        return result
    if action == 'remove':
        name = a.get('name', '')
        macro, where = find(name, doc=doc, config_dir=config_dir, store=store)
        if where == 'user':
            macros = user_macros(config_dir)
            macros.pop(name)
            _write_user(macros, config_dir)
            return envelope({'removed': name, 'store': 'user'})
        remaining = document_macros(doc or {})
        remaining.pop(name)
        from .cli import execute

        request = {
            'path': document,
            'input': {'operations': [{'type': 'set_settings', 'values': {'macros': remaining}}]},
        }
        for option in ('expect_revision', 'request_id'):
            if a.get(option):
                request[option] = a[option]
        result = execute('apply', request)
        result.update(removed=name, store='document')
        return result
    # run
    if a.get('input') is not None:
        macro, where = validate_macro(a['input'], name=a.get('name')), 'inline'
    else:
        macro, where = find(a.get('name', ''), doc=doc, config_dir=config_dir, store=store)
    outcome = run_macro(
        macro,
        params=a.get('params'),
        document=document,
        dry_run=bool(a.get('dry_run')),
        allow_admin=bool(a.get('allow_admin')),
        expect_revision=a.get('expect_revision'),
        verbose=bool(a.get('verbose')),
    )
    if outcome['status'] == 'failed':
        error = outcome['error']
        details = {k: v for k, v in error.items() if k not in ('code', 'message', 'exit_code')}
        details.update(
            macro=macro['name'],
            step=outcome['failed_step'],
            transcript=outcome['transcript'],
            exit_code=error.get('exit_code', 1),
        )
        raise ByaxError(
            error.get('code', 'execution_failure'), error.get('message', 'A macro step failed'), **details
        )
    return envelope({'macro': {**describe(macro, where)}, **outcome})
