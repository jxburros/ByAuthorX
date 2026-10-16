"""Explicitly authorized retrieval for linked external dependencies.

The engine never reads files or the network. Callers inspect dependencies,
then retrieve selected ones here with explicit authorization and apply the
returned canonical ``refresh_dependency`` / ``mark_dependency_missing``
operations, which update hashes and invalidate dependent checks atomically.
"""

import base64
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .engine import dependency_report
from .errors import ByaxError

MAX_BYTES = 64 * 1024 * 1024


def _local_path(uri, base_dir):
    parts = urlsplit(uri)
    if parts.scheme.lower() == 'file':
        if parts.netloc not in ('', 'localhost'):
            raise ByaxError('unsupported_feature', 'Remote file shares are not retrieved.', target=uri)
        return Path(url2pathname(parts.path))
    path = Path(uri)
    if not path.is_absolute():
        if base_dir is None:
            raise ByaxError('invalid_input', 'A relative dependency needs the document folder.', target=uri)
        path = Path(base_dir) / path
    return path


def retrieve(dependency, *, base_dir=None, authorize=False, allow_network=False, timeout=15):
    """Return ``(bytes, mime)``; nothing is read without ``authorize=True``."""
    uri = dependency['uri']
    if not authorize:
        raise ByaxError(
            'review_required',
            'Retrieving linked content requires explicit authorization.',
            target=dependency['id'],
            uri=uri,
        )
    scheme = urlsplit(uri).scheme.lower()
    if scheme in ('http', 'https'):
        if not allow_network:
            raise ByaxError(
                'review_required',
                'Network retrieval requires explicit network permission.',
                target=dependency['id'],
                uri=uri,
            )
        from urllib.request import Request, urlopen

        with urlopen(Request(uri, headers={'User-Agent': 'ByAuthorX'}), timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
            mime = response.headers.get_content_type()
    elif scheme in ('', 'file') or len(scheme) == 1:
        path = _local_path(uri, base_dir)
        if not path.is_file():
            raise ByaxError('missing_target', 'Linked file does not exist.', target=dependency['id'], uri=uri)
        if path.stat().st_size > MAX_BYTES:
            raise ByaxError('limit_exceeded', 'Linked content exceeds 64 MiB.', limit='64 MiB per asset')
        with path.open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        mime = mimetypes.guess_type(path.name)[0]
    else:
        raise ByaxError(
            'unsupported_feature', 'Unsupported dependency scheme.', target=dependency['id'], uri=uri
        )
    if len(data) > MAX_BYTES:
        raise ByaxError('limit_exceeded', 'Linked content exceeds 64 MiB.', limit='64 MiB per asset')
    return data, dependency.get('mime') or mime or 'application/octet-stream'


def refresh_operations(doc, targets=None, *, base_dir=None, authorize=False, allow_network=False):
    """Canonical operations that record retrieved content or located failures.

    Unauthorized retrieval fails before any read. Retrieval failures become
    ``mark_dependency_missing`` operations so their locations stay reported.
    """
    if not authorize:
        raise ByaxError('review_required', 'Retrieving linked content requires explicit authorization.')
    report = dependency_report(doc)
    selected = [
        d for d in report['dependencies'] if d['policy'] == 'link' and (targets is None or d['id'] in targets)
    ]
    if targets is not None and len(selected) != len(set(targets)):
        missing = sorted(set(targets) - {d['id'] for d in selected})
        raise ByaxError(
            'missing_target', 'Linked dependency does not exist.', target=missing[0], missing=missing
        )
    operations, outcomes = [], []
    for dependency in selected:
        try:
            data, mime = retrieve(
                dependency, base_dir=base_dir, authorize=authorize, allow_network=allow_network
            )
        except ByaxError as exc:
            if exc.code in ('review_required', 'limit_exceeded'):
                raise
            operations.append(
                {'type': 'mark_dependency_missing', 'target': dependency['id'], 'reason': exc.message}
            )
            outcomes.append(
                {
                    'id': dependency['id'],
                    'status': 'missing',
                    'used_by': dependency['used_by'],
                    'error': exc.code,
                }
            )
            continue
        except OSError as exc:
            operations.append(
                {'type': 'mark_dependency_missing', 'target': dependency['id'], 'reason': str(exc)}
            )
            outcomes.append(
                {
                    'id': dependency['id'],
                    'status': 'missing',
                    'used_by': dependency['used_by'],
                    'error': 'os_error',
                }
            )
            continue
        operations.append(
            {
                'type': 'refresh_dependency',
                'target': dependency['id'],
                'data': base64.b64encode(data).decode('ascii'),
                'mime': mime,
            }
        )
        outcomes.append(
            {
                'id': dependency['id'],
                'status': 'retrieved',
                'bytes': len(data),
                'used_by': dependency['used_by'],
            }
        )
    return operations, outcomes
