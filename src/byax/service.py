"""Opt-in, authenticated loopback JSON HTTP transport.

Start with explicit roots and a token sourced from the environment. No service
starts at import time. Remote binding is deliberately unsupported. Request paths
are resolved under authorized roots, including symlinks and nested batch paths.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .cli import _error, error_result, execute, result_exit_code

MAX_BODY = 16 * 1024 * 1024
PATH_KEYS = frozenset(('path', 'out', 'document', 'config_dir', 'root', 'audio_path', 'source_path', 'job_root', 'csv', 'reference_dir'))


class AccessPolicy:
    def __init__(self, roots):
        self.roots = tuple(Path(root).resolve(strict=True) for root in roots)
        if not self.roots or any(not root.is_dir() for root in self.roots):
            _error('invalid_input', 'At least one existing filesystem root is required')

    def check_path(self, value):
        path = Path(value).resolve()
        if not any(path == root or root in path.parents for root in self.roots):
            _error('invalid_input', 'Path is outside authorized filesystem roots', field='path')
        return str(path)

    def check(self, command, args):
        if not isinstance(args, dict):
            _error('invalid_input', 'Arguments must be an object')
        # Service-owned configuration and job state stay inside its authorized roots.
        if command in ('providers', 'transcribe'):
            args.setdefault('config_dir', str(self.roots[0] / '.byax' / 'config'))
        if command == 'jobs':
            args.setdefault('root', str(self.roots[0] / '.byax' / 'jobs'))
        if command == 'transcribe':
            args.setdefault('job_root', str(self.roots[0] / '.byax' / 'jobs'))
        # Provider configuration may name executable adapters or endpoints and is
        # an administrative local action. Existing aliases remain callable.
        if command == 'providers' and args.get('action') == 'configure':
            _error('unsupported_feature', 'Configure provider aliases through the local CLI or desktop')
        if command == 'jobs' and args.get('action') == 'submit':
            spec = args.get('input', {})
            self.check(spec.get('command'), spec.get('arguments', {}))
        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in PATH_KEYS and isinstance(child, str):
                        value[key] = self.check_path(child)
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(args)


def make_server(roots, *, token: str, host: str = '127.0.0.1', port: int = 8765):
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

        def do_POST(self):
            if not self.authorized():
                self.reply(401, {'error': {'code': 'unauthorized', 'message': 'Bearer token required'}})
                return
            # Browser cross-origin traffic is unnecessary for a native local API.
            if self.headers.get('Origin'):
                self.reply(403, {'error': {'code': 'forbidden', 'message': 'Browser origins are not supported'}})
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
                policy.check(command, args)
                result = execute(command, args)
                code = result_exit_code(result)
                self.reply(200, dict(result, exit_code=code))
            except Exception as exc:  # noqa: BLE001 - transport boundary
                result, code = error_result(exc)
                self.reply(409 if code == 3 else 400 if code == 2 else 422 if code in (4, 5) else 500, dict(result, exit_code=code))

        def do_GET(self):
            self.reply(405, {'error': {'code': 'invalid_input', 'message': 'Use authenticated POST /v1/execute'}})
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.timeout = 30
    return server


def serve(roots, *, token: str, host: str = '127.0.0.1', port: int = 8765):
    server = make_server(roots, token=token, host=host, port=port)
    print(f'ByAuthorX local service listening at http://{host}:{server.server_port}', file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
