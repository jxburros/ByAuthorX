"""Consistent authored, publication and selection counts for every interface."""

import math

from .errors import ByaxError
from .model import text_of


def word_count(text):
    """The one word definition behind every count; callers that need only words skip the character scan."""
    return len(text.split())


def counts(text):
    words = word_count(text)
    return {
        'words': words,
        'characters': len(text),
        'characters_without_spaces': sum(not c.isspace() for c in text),
        'reading_minutes': math.ceil(words / 225),
    }


def document_statistics(doc, *, output=None, target=None, start=None, end=None):
    result = {'authored': counts(text_of(doc))}
    if output:
        from .formats import project

        projected, _ = project(doc, output)
        result['output'] = output
        result['publication'] = counts(text_of(projected))
    if target:
        from .model import find_object

        obj = find_object(doc, target)
        text = obj.get('text', '')
        start, end = 0 if start is None else start, len(text) if end is None else end
        if not 0 <= start <= end <= len(text):
            raise ByaxError('invalid_input', 'Invalid selection range')
        result['selection'] = counts(text[start:end])
    return result
