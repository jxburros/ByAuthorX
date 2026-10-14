"""Stable errors shared by every ByAuthorX interface."""


class ByaxError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details
        self.exit_code = details.pop(
            'exit_code',
            {
                'invalid_input': 2,
                'schema_error': 2,
                'unsupported_version': 2,
                'stale_revision': 3,
                'conflict': 3,
                'locked': 3,
                'ambiguous_selection': 3,
                'missing_target': 3,
                'precondition_failed': 3,
                'protected_edit': 4,
                'failed_requirement': 4,
                'incomplete_coverage': 5,
                'review_required': 5,
                'cancelled': 6,
                'pending': 7,
                'uncertain_outcome': 7,
                'uncertain_external_outcome': 7,
            }.get(code, 1),
        )

    def to_dict(self):
        return {'code': self.code, 'message': self.message, 'exit_code': self.exit_code, **self.details}
