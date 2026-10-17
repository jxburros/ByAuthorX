"""Opt-in, authenticated loopback JSON HTTP transport.

Start with explicit roots and a token sourced from the environment. No service
starts at import time. Remote binding is deliberately unsupported. Request paths
are resolved under authorized roots, including symlinks and nested batch paths.

Besides the service token, an installed extension can be given a *scoped* token derived from it
(``issue_extension_token``): signed with the service token, bound to one extension id, a list of
scopes (``read``, ``propose``) and an expiry, and honoured only while that extension is still
installed, enabled, intact and granted those scopes. ``read`` allows the read-only commands;
``propose`` allows ``review --action propose`` with text-edit operations only. Nothing else.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import macros
from .cli import _error, error_result, execute, result_exit_code
from .transport_policy import PATH_KEYS, check_provider_admin, is_path_argument

MAX_BODY = 16 * 1024 * 1024
RESERVED = '.byax'
__all__ = [
    'PATH_KEYS',
    'AccessPolicy',
    'ScopeDenied',
    'check_extension_request',
    'issue_extension_token',
    'make_server',
    'serve',
    'verify_extension_token',
]

TOKEN_PREFIX = 'bxe1'
TOKEN_SCOPES = ('read', 'propose')  # the only scopes a REST token can carry for an extension
MAX_TOKEN_TTL = 30 * 24 * 3600
# Commands (and their actions) a ``read`` token may call. Everything here is read-only.
READ_COMMANDS = {
    'inspect': None,
    'find': None,
    'context': None,
    'diff': None,
    'statistics': None,
    'readability': None,
    'schema': None,
    'capabilities': None,
    'version': None,
    'check': None,
    'review': ('list',),
    'history': ('list',),
    'project': ('list', 'tree', 'blocks', 'reconcile'),
}
PROPOSE_OPERATIONS = ('replace_text', 'insert_text', 'delete_text', 'set_text', 'update_cell')
PROPOSE_KEYS = ('operations', 'explanation', 'expected_revision')


class ScopeDenied(PermissionError):
    """A scoped extension token asked for something its scopes do not allow (HTTP 403)."""


def _signature(main_token, extension_id, scopes, expires):
    message = f'{TOKEN_PREFIX}|{extension_id}|{scopes}|{expires}'.encode()
    return hmac.new(main_token.encode('utf-8'), message, hashlib.sha256).hexdigest()


def issue_extension_token(main_token, extension_id, scopes, *, ttl=3600, now=None):
    """A bearer token for one extension, limited to ``scopes`` and ``ttl`` seconds, signed with the service token."""
    if not isinstance(main_token, str) or len(main_token) < 24:
        _error('invalid_input', 'The service token must contain at least 24 characters', field='token_env')
    if not isinstance(extension_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,47}', extension_id):
        _error('invalid_input', 'Extension id is malformed', field='id')
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(scope not in TOKEN_SCOPES for scope in scopes)
        or len(set(scopes)) != len(scopes)
    ):
        _error(
            'invalid_input',
            'A REST token may carry read and propose scopes only',
            field='scopes',
            supported=list(TOKEN_SCOPES),
        )
    if type(ttl) is not int or not 60 <= ttl <= MAX_TOKEN_TTL:
        _error('invalid_input', f'ttl is from 60 to {MAX_TOKEN_TTL} seconds', field='ttl')
    from . import extensions

    granted = extensions.token_scopes(extension_id)
    if granted is None:
        _error(
            'conflict',
            'Only an installed, enabled, intact extension can be given a token',
            target=extension_id,
        )
    missing = sorted(set(scopes) - granted)
    if missing:
        _error('conflict', 'The extension was not granted these scopes', scopes=missing)
    expires = int((now if now is not None else time.time()) + ttl)
    csv = ','.join(sorted(scopes))
    return (
        f'{TOKEN_PREFIX}.{extension_id}.{csv}.{expires}.{_signature(main_token, extension_id, csv, expires)}'
    )


def verify_extension_token(main_token, presented, *, now=None):
    """(extension id, effective scopes) for a valid, unexpired token whose extension may still run, else None."""
    parts = presented.split('.') if isinstance(presented, str) else []
    if len(parts) != 5 or parts[0] != TOKEN_PREFIX or not parts[3].isdigit():
        return None
    _, extension_id, csv, expires, signature = parts
    if not hmac.compare_digest(signature, _signature(main_token, extension_id, csv, int(expires))):
        return None
    if int(expires) < (now if now is not None else time.time()):
        return None
    scopes = set(csv.split(','))
    if not scopes <= set(TOKEN_SCOPES):
        return None
    from . import extensions

    granted = extensions.token_scopes(extension_id)
    if granted is None:
        return None
    effective = scopes & granted
    return (extension_id, effective) if effective else None


def check_extension_request(extension_id, scopes, command, args):
    """Raise ``ScopeDenied`` unless a token with ``scopes`` may run this request; may narrow ``args`` in place."""
    action = args.get('action')
    if 'read' in scopes and command in READ_COMMANDS:
        allowed = READ_COMMANDS[command]
        if allowed is not None and (action or allowed[0]) not in allowed:
            raise ScopeDenied(f'The read scope does not allow {command} --action {action}')
        if command == 'check' and 'extension' in [*(args.get('suite') or []), *(args.get('family') or [])]:
            raise ScopeDenied('Extension tokens cannot run extension checks (they would call themselves)')
        return
    if 'propose' in scopes and command == 'review' and action == 'propose':
        values = args.get('input')
        if not isinstance(values, dict) or set(values) - set(PROPOSE_KEYS) - {'author'}:
            raise ScopeDenied(f'A proposal may carry only {list(PROPOSE_KEYS)}')
        operations = values.get('operations')
        if not isinstance(operations, list) or not operations:
            raise ScopeDenied('A proposal needs operations')
        if any(not isinstance(op, dict) or op.get('type') not in PROPOSE_OPERATIONS for op in operations):
            raise ScopeDenied(f'A proposal may contain {list(PROPOSE_OPERATIONS)} operations only')
        if not args.get('expect_revision'):
            raise ScopeDenied('A proposal must name the revision it was made against (expect_revision)')
        values['author'] = f'extension:{extension_id}'
        return
    raise ScopeDenied(
        f'The scopes {sorted(scopes)} do not allow {command}' + (f' --action {action}' if action else '')
    )


class AccessPolicy:
    def __init__(self, roots):
        self.roots = tuple(Path(root).resolve(strict=True) for root in roots)
        if not self.roots or any(not root.is_dir() for root in self.roots):
            _error('invalid_input', 'At least one existing filesystem root is required')

    def check_path(self, value):
        path = Path(value).resolve()
        if not any(path == root or root in path.parents for root in self.roots):
            _error('invalid_input', 'Path is outside authorized filesystem roots', field='path')
        # Clients may write files under the roots (export, unpack), so they must
        # never reach service-owned provider configuration or job state.
        if any(path == reserved or reserved in path.parents for reserved in self.reserved):
            _error('invalid_input', 'Path is inside the service-reserved .byax folder', field='path')
        return str(path)

    @property
    def reserved(self):
        return tuple(root / RESERVED for root in self.roots)

    def _owned(self, command, args):
        state = self.roots[0] / RESERVED
        owned = {}
        if command in ('providers', 'transcribe', 'project', 'goals', 'macro'):
            owned['config_dir'] = state / 'config'
        if command == 'jobs':
            owned['root'] = state / 'jobs'
        if command == 'transcribe':
            owned['job_root'] = state / 'jobs'
        for key, value in owned.items():
            if key in args and (not isinstance(args[key], str) or Path(args[key]).resolve() != value):
                _error(
                    'invalid_input',
                    'Service configuration and job folders are fixed by the service',
                    field=key,
                )
            args.pop(key, None)
        return {key: str(value) for key, value in owned.items()}

    def check(self, command, args):
        if not isinstance(args, dict):
            _error('invalid_input', 'Arguments must be an object')
        check_provider_admin(command, args)
        # Service-owned configuration and job state stay in a reserved folder
        # inside the first root; clients cannot select or write to it.
        owned = self._owned(command, args)
        if command == 'pack' and isinstance(args.get('path'), str):
            source = Path(args['path']).resolve()
            if any(source in reserved.parents for reserved in self.reserved):
                _error('invalid_input', 'Cannot pack an ancestor of the service-reserved .byax folder')

        # Provider configuration may name executable adapters or endpoints and is
        # an administrative local action. Existing aliases remain callable.
        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if (key in PATH_KEYS or is_path_argument(key)) and isinstance(child, str):
                        value[key] = self.check_path(child)
                    elif (
                        (key in PATH_KEYS or is_path_argument(key))
                        and isinstance(child, list)
                        and all(isinstance(item, str) for item in child)
                    ):
                        value[key] = [self.check_path(item) for item in child]
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        if command == 'macro':
            # A macro definition holds placeholders, not paths. Each step is checked after substitution
            # (macros.step_policy), which is where a path can first be known.
            held = {key: args.pop(key) for key in ('input', 'params') if key in args}
            try:
                visit(args)
            finally:
                args.update(held)
        else:
            visit(args)
        if command == 'jobs' and args.get('action') == 'submit':
            spec = args.get('input', {})
            if not isinstance(spec, dict):
                _error('invalid_input', 'Job input must be an object')
            self.check(spec.get('command'), spec.setdefault('arguments', {}))
        args.update(owned)


def make_server(
    roots: list[str | Path], *, token: str, host: str = '127.0.0.1', port: int = 8765
) -> ThreadingHTTPServer:
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if not local or ':' in host:
        _error('invalid_input', 'Service requires an IPv4 loopback address')
    if not isinstance(token, str) or len(token) < 24:
        _error('invalid_input', 'Service token must contain at least 24 characters')
    policy = AccessPolicy(roots)
    # Own Qt's application/font system on the serving main thread before any
    # HTTP worker builds a QTextDocument or rasterizes a page.
    from .layout import ensure_app

    ensure_app()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'ByAuthorX/1'

        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, format, *args):
            # Never log tokens or document material.
            print('ByAuthorX service request completed', file=sys.stderr)

        def reply(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            auth = self.headers.get('Authorization', '')
            return hmac.compare_digest(auth.encode('utf-8'), ('Bearer ' + token).encode('utf-8'))

        def extension_principal(self):
            """(extension id, scopes) when the bearer token is a valid scoped extension token."""
            auth = self.headers.get('Authorization', '')
            if auth.startswith('Bearer ' + TOKEN_PREFIX + '.'):
                return verify_extension_token(token, auth[len('Bearer ') :])
            return None

        def do_POST(self):
            principal = None
            if not self.authorized():
                principal = self.extension_principal()
                if principal is None:
                    self.reply(401, {'error': {'code': 'unauthorized', 'message': 'Bearer token required'}})
                    return
            # Browser cross-origin traffic is unnecessary for a native local API.
            if self.headers.get('Origin'):
                self.reply(
                    403, {'error': {'code': 'forbidden', 'message': 'Browser origins are not supported'}}
                )
                return
            if self.path != '/v1/execute':
                self.reply(404, {'error': {'code': 'not_found', 'message': 'Use POST /v1/execute'}})
                return
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= MAX_BODY:
                    _error('limit_exceeded', 'Request must have Content-Length between 0 and 16 MiB')
                if self.headers.get('Transfer-Encoding'):
                    _error('invalid_input', 'Transfer-Encoding is not supported')
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(body, dict) or set(body) - {'command', 'arguments'}:
                    _error('invalid_input', 'Expected {command, arguments}')
                command, args = body.get('command'), body.get('arguments', {})
                if principal is not None:
                    if not isinstance(args, dict):
                        _error('invalid_input', 'Arguments must be an object')
                    check_extension_request(principal[0], principal[1], command, args)
                policy.check(command, args)
                # A macro run here applies the same path policy to each of its steps.
                with macros.step_policy(policy.check):
                    result = execute(command, args)
                code = result_exit_code(result)
                self.reply(200, dict(result, exit_code=code))
            except ScopeDenied as exc:
                self.reply(403, {'error': {'code': 'forbidden', 'message': str(exc)}})
            except Exception as exc:  # noqa: BLE001 - transport boundary
                result, code = error_result(exc)
                self.reply(
                    409 if code == 3 else 400 if code == 2 else 422 if code in (4, 5) else 500,
                    dict(result, exit_code=code),
                )

        def do_GET(self):
            self.reply(
                405, {'error': {'code': 'invalid_input', 'message': 'Use authenticated POST /v1/execute'}}
            )

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.timeout = 30
    return server


def serve(roots: list[str | Path], *, token: str, host: str = '127.0.0.1', port: int = 8765) -> None:
    server = make_server(roots, token=token, host=host, port=port)
    print(f'ByAuthorX local service listening at http://{host}:{server.server_port}', file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
