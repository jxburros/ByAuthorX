"""Conservative diff3 over graphemes with Unicode scalar splice coordinates."""

import difflib

import regex

from .anchors import transform_marks


def splices(before, after):
    """Return replacements at grapheme boundaries in the original scalar space."""
    old, new = regex.findall(r'\X', before), regex.findall(r'\X', after)
    offsets = [0]
    for token in old:
        offsets.append(offsets[-1] + len(token))
    return [
        (offsets[a], offsets[b], ''.join(new[c:d]))
        for tag, a, b, c, d in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
        if tag != 'equal'
    ]


def overlaps(left, right):
    a, b, _ = left
    c, d, _ = right
    if a == b and c == d:
        return a == c
    if a == b:
        return c < a < d
    if c == d:
        return a < c < b
    return max(a, c) < min(b, d)


def conflict_ranges(base, ours, theirs):
    """Report exact conflicting replacements, with offsets relative to the base."""
    return [
        {
            'base_start': min(a[0], b[0]),
            'base_end': max(a[1], b[1]),
            'ours': {'start': a[0], 'end': a[1], 'text': a[2]},
            'theirs': {'start': b[0], 'end': b[1], 'text': b[2]},
        }
        for a in splices(base, ours)
        for b in splices(base, theirs)
        if a != b and overlaps(a, b)
    ]


def _mapped_marks(block, text):
    marks = block.get('marks', [])
    for start, end, replacement in reversed(splices(block.get('text', ''), text)):
        marks = transform_marks(marks, start, end, len(replacement))
    return marks


def merge_text(base, ours, theirs):
    """Return merged text/marks, or None if text or formatting is ambiguous.

    Both branches must yield identical formatting after mapping to the combined
    text. This deliberately conflicts on independent formatting changes instead
    of guessing which formatting should cover another contributor's words.
    """
    original = base.get('text', '')
    left, right = ours.get('text', ''), theirs.get('text', '')
    if conflict_ranges(original, left, right):
        return None
    edits = sorted(set(splices(original, left) + splices(original, right)))
    text = original
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    left_marks, right_marks = _mapped_marks(ours, text), _mapped_marks(theirs, text)
    if left_marks != right_marks:
        return None
    return {'text': text, 'marks': left_marks}
