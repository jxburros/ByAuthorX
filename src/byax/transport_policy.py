"""Shared transport policy: administrative actions and path-bearing arguments.

Administrative actions are available only in the local CLI and desktop. Every
argument that names a filesystem location is listed in PATH_KEYS so scoped
transports (REST) resolve it under authorized roots, wherever it is nested.
"""

from .errors import ByaxError

PATH_KEYS = frozenset(
    (
        'path',
        'out',
        'document',
        'config_dir',
        'root',
        'audio_path',
        'source_path',
        'job_root',
        'csv',
        'reference_dir',
    )
)


def is_path_argument(name):
    """Naming rule used to audit that every path-like argument is scoped."""
    return name in PATH_KEYS or name.endswith(('_path', '_dir', '_root', '_file'))


def check_provider_admin(command, arguments):
    if not isinstance(arguments, dict):
        raise ByaxError('invalid_input', 'Arguments must be an object')
    if command == 'providers' and arguments.get('action') == 'configure':
        raise ByaxError('unsupported_feature', 'Configure provider aliases through the local CLI or desktop')
    if command == 'jobs' and arguments.get('action') == 'submit':
        spec = arguments.get('input', {})
        if not isinstance(spec, dict):
            raise ByaxError('invalid_input', 'Job input must be an object')
        check_provider_admin(spec.get('command'), spec.get('arguments', {}))
