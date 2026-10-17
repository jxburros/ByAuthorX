"""Read-only manuscript outline with its parameters exposed (context engine 8.5).

``byax inspect --outline`` lists headings. :func:`outline` is the same outline with the options an
agent needs: a depth limit, word counts per section, and the binder metadata bound to each heading
(kind, status, synopsis, targets). It is the ``get_outline`` tool of route R3 and answers the
structure questions route R2 handles without retrieval (R-meta). It never changes the document and is
bounded with an ``offset`` continuation.
"""

from __future__ import annotations

from typing import Any

from .errors import ByaxError

Doc = dict[str, Any]
MAX_ITEMS = 1000
BINDER_FIELDS = ('kind', 'status', 'synopsis', 'target_words', 'label', 'include_in_compile')


def _block_words(block):
    from .model import text_of
    from .statistics import word_count

    return word_count(text_of({'blocks': [block]}))


def outline(
    doc: Doc,
    *,
    depth: int | None = None,
    counts: bool = False,
    binder: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Headings in body order with relative depth, optional word counts and binder metadata.

    ``depth`` keeps headings whose relative depth (1 for the shallowest level used) is at most
    ``depth``. With ``counts`` each row has ``words`` (its own text up to the next heading) and
    ``total_words`` (its whole section, nested headings included) plus the document total. With
    ``binder`` rows bound to a binder item carry ``item`` and the item's metadata.
    """
    if depth is not None and (type(depth) is not int or not 1 <= depth <= 6):
        raise ByaxError('invalid_input', 'Depth must be an integer from 1 to 6', field='depth')
    if type(limit) is not int or not 1 <= limit <= MAX_ITEMS or type(offset) is not int or offset < 0:
        raise ByaxError('invalid_input', 'Outline limit must be 1–1000 and offset nonnegative')
    blocks = doc.get('blocks', [])
    headings = [(i, b) for i, b in enumerate(blocks) if b.get('type') == 'heading']
    levels = sorted({b.get('attrs', {}).get('level', 1) for _, b in headings})
    rank = {level: position + 1 for position, level in enumerate(levels)}
    words = [_block_words(b) for b in blocks] if counts else []
    prefix = [0]
    for value in words:
        prefix.append(prefix[-1] + value)
    items: dict[str, dict[str, Any]] = {}
    if binder and isinstance(doc.get('project'), dict):
        from . import project

        items = {row['heading']: row for row in project.items(doc)}
    rows = []
    for position, (index, block) in enumerate(headings):
        level = block.get('attrs', {}).get('level', 1)
        relative = rank[level]
        if depth is not None and relative > depth:
            continue
        row: dict[str, Any] = {
            'id': block['id'],
            'text': block.get('text', ''),
            'level': level,
            'depth': relative,
        }
        if counts:
            own_end = headings[position + 1][0] if position + 1 < len(headings) else len(blocks)
            section_end = next(
                (i for i, b in headings[position + 1 :] if b.get('attrs', {}).get('level', 1) <= level),
                len(blocks),
            )
            row['words'] = prefix[own_end] - prefix[index]
            row['total_words'] = prefix[section_end] - prefix[index]
        item = items.get(block['id'])
        if item is not None:
            row['item'] = item['id']
            row.update({key: item[key] for key in BINDER_FIELDS if key in item})
        rows.append(row)
    page = rows[offset : offset + limit]
    more = offset + len(page) < len(rows)
    result: dict[str, Any] = {
        'items': page,
        'total': len(rows),
        'offset': offset,
        'truncated': more,
        'continuation': offset + len(page) if more else None,
        'depth': depth,
        'binder': {'enabled': isinstance(doc.get('project'), dict), 'included': bool(binder)},
    }
    if counts:
        result['words'] = prefix[-1]
        result['counting'] = 'authored words (statistics.word_count), headings included'
    return result
