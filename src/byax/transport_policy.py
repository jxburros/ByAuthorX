"""Administrative actions available only in the local CLI and desktop."""

from .errors import ByaxError


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
