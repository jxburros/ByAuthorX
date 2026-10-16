"""Live excerpts and "where is this source used" (context engine plan 9, A2).

An **excerpt** is text taken from a source into the manuscript that keeps a pointer back to it. In the
document it is an ordinary paragraph (a block quotation by default) carrying an ``excerpt`` mark:
``value`` is the source ID, ``quote`` the source text, ``locator`` where in the source (a library chunk,
page or block) and ``anchor`` the same three source-side selectors a citation keeps (position, quote and
32 characters either side, plus the source's content hash). Nothing about it changes the text; the mark
is metadata.

*Live* means the pointer can be followed and checked: :func:`check` compares each excerpt with the
current source text and reports ``current``, ``edited`` (you changed your copy), ``source_changed``
(re-found in the changed source, with a proposed refresh), ``unanchored`` (not found any more) or
``source_removed``. Deleting a source from the reference list never erases an excerpt: the text stays,
the pointer stays, and the mark becomes ``unanchored`` (:func:`detach_source`). Adding the source
back and running :func:`check` re-anchors it.

:func:`where_used` lists every citation and excerpt per source for the "Where is this source used"
view. Everything here reads only the document and the attached library indexes; no network.
"""

from __future__ import annotations

import copy
import hashlib

from .errors import ByaxError
from .grounding import STRUCTURE_KEYS, GroundingRecord, locate_record
from .quote_match import normalize, selectors

SNIPPET = 160
EXCERPT_STATES = (
    'current',
    'edited',
    'source_changed',
    'unanchored',
    'source_removed',
    'source_unavailable',
    'unchecked',
)
LOCATOR_KEYS = (
    'page',
    'paragraph',
    'block',
    'chunk',
    'label',
    'text',
    'heading_path',
    'start',
    'end',
    'ordinal',
)
MAX_EXCERPT_CHARS = 4000


def _objects(doc):
    for block in doc.get('blocks', []):
        yield block
        for row in block.get('rows', []):
            yield from row.get('cells', [])


def excerpt_marks(doc):
    """``(block or cell, mark)`` for every excerpt mark, in document order."""
    for obj in _objects(doc):
        for mark in obj.get('marks', []):
            if isinstance(mark, dict) and mark.get('type') == 'excerpt':
                yield obj, mark


def detach_source(doc, source_id):
    """Mark every excerpt of ``source_id`` unanchored (the source was removed); returns changed block IDs.

    The excerpt text, its mark and its source pointer are kept, so adding the source back lets
    :func:`check` re-anchor it. Citation marks are not touched (they have always tolerated a
    missing source and are reported by the citation audit).
    """
    changed = []
    for obj, mark in excerpt_marks(doc):
        if mark.get('value') != source_id:
            continue
        anchor = dict(mark.get('anchor') or {'quote': mark.get('quote', '')})
        anchor['state'] = 'unanchored'
        mark['anchor'] = anchor
        mark['unanchored_reason'] = 'source_removed'
        changed.append(obj['id'])
    return changed


def _headings(doc):
    """Map each block ID to its nearest preceding heading ``(id, text)``."""
    current, owner = (None, None), {}
    for block in doc.get('blocks', []):
        if block.get('type') == 'heading':
            current = (block['id'], block.get('text', ''))
        owner[block['id']] = current
        for row in block.get('rows', []):
            for cell in row.get('cells', []):
                owner[cell['id']] = current
    return owner


def excerpt_state(doc, mark):
    """Cheap state from the document alone: ``unanchored``, ``source_removed`` or ``anchored``."""
    if mark.get('value') not in doc.get('references', {}):
        return 'source_removed'
    if (mark.get('anchor') or {}).get('state') == 'unanchored':
        return 'unanchored'
    return 'anchored'


def where_used(doc, source_id=None, *, include_unused=False):
    """Citations and excerpts per source, with the section each sits in.

    With ``source_id`` only that source (a removed source that excerpts still point to is included).
    """
    references = doc.get('references', {})
    owner = _headings(doc)
    rows = {}

    def row(ident):
        source = references.get(ident)
        csl = (source or {}).get('csl') or {}
        return rows.setdefault(
            ident,
            {
                'source': ident,
                'title': csl.get('title') or (source or {}).get('title') or ident,
                'in_references': source is not None,
                'citations': [],
                'excerpts': [],
            },
        )

    for obj in _objects(doc):
        heading_id, heading = owner.get(obj['id'], (None, None))
        for mark in obj.get('marks', []):
            if not isinstance(mark, dict) or mark.get('type') not in ('citation', 'excerpt'):
                continue
            ident = mark.get('value')
            if source_id and ident != source_id:
                continue
            text = (obj.get('text') or '')[mark['start'] : mark['end']]
            item = {
                'block_id': obj['id'],
                'start': mark['start'],
                'end': mark['end'],
                'text': text if len(text) <= SNIPPET else text[: SNIPPET - 1] + '…',
                'heading_id': heading_id,
                'heading': heading,
                'locator': copy.deepcopy(mark.get('locator', {})),
            }
            if mark['type'] == 'citation':
                item['kind'] = mark.get('kind', 'paraphrase')
                item['anchor_state'] = (mark.get('anchor') or {}).get('state')
                row(ident)['citations'].append(item)
            else:
                item['state'] = excerpt_state(doc, mark)
                item['reason'] = mark.get('unanchored_reason')
                row(ident)['excerpts'].append(item)
    used = set(rows)
    if source_id and source_id in references:
        row(source_id)
    elif include_unused:
        for ident in references:
            row(ident)
    result = sorted(rows.values(), key=lambda r: (r['title'].casefold(), r['source']))
    for entry in result:
        entry['counts'] = {'citations': len(entry['citations']), 'excerpts': len(entry['excerpts'])}
    return {
        'sources': result,
        'total': len(result),
        'unused': sorted(i for i in references if i not in used),
        'removed_sources_with_excerpts': [r['source'] for r in result if not r['in_references']],
    }


# --- adding an excerpt ---------------------------------------------------------------------------------


def _locator_of(chunk):
    locator = {
        k: v for k, v in (chunk.get('locator') or {}).items() if k in LOCATOR_KEYS and v not in (None, '')
    }
    locator['chunk'] = chunk['chunk_id']
    return locator


def _read_chunk(doc, source, chunk_id, text, registry, cache_dir):
    """The chunk an excerpt comes from, read from the attached library (no indexing, no network)."""
    from .cli_libraries import attached_library
    from .libraries import LibraryIndex

    pointer = source.get('library')
    if not pointer:
        raise ByaxError(
            'invalid_input', 'Only a source that points into an attached library can be excerpted'
        )
    library = (
        attached_library(doc, pointer['library_id'])
        if registry is None
        else registry.get(pointer['library_id'])
    )
    index = LibraryIndex(library, cache_dir=cache_dir)
    if chunk_id:
        page = index.read(pointer['document_id'], chunk_id=chunk_id)
        return page, page['results'][0]
    from .quote_match import locate

    chunks, offset, page = [], 0, None
    for _ in range(10000):
        page = index.read(pointer['document_id'], limit=1_000_000, offset=offset)
        chunks.extend(page['results'])
        if page.get('continuation') is None:
            break
        offset = page['continuation']
    for chunk in chunks:
        if locate(text, chunk['text'])['match'] in ('exact', 'normalized'):
            return page, chunk
    raise ByaxError(
        'missing_target',
        'That text was not found in the source; excerpt a passage that exists there',
        source=source.get('id'),
    )


def add_operations(
    doc,
    *,
    source,
    after=None,
    chunk_id=None,
    text=None,
    start=None,
    end=None,
    style='quotation',
    registry=None,
    cache_dir=None,
):
    """Operations that insert an excerpt paragraph after block ``after`` (or at the end).

    Give ``chunk_id`` (optionally with ``start``/``end`` offsets inside the chunk text), or ``text``
    to find in the source. Returns ``{'operations', 'excerpt'}``; nothing is applied. The new text is
    the source text itself, verified against the source when ``text`` is given.
    """
    from .quote_match import locate

    record = doc.get('references', {}).get(source)
    if not isinstance(record, dict):
        raise ByaxError('missing_target', 'The source does not exist in this document', target=source)
    if not chunk_id and not text:
        raise ByaxError('invalid_input', 'Name the library chunk or the text to excerpt', field='chunk_id')
    page, chunk = _read_chunk(doc, record, chunk_id, text, registry, cache_dir)
    chunk_text = chunk['text']
    if text and not chunk_id:
        found = locate(text, chunk_text)
        span = found['span']
    elif text:
        found = locate(text, chunk_text)
        if found['match'] not in ('exact', 'normalized'):
            raise ByaxError('invalid_input', 'The text is not in that chunk of the source', field='text')
        span = found['span']
    else:
        span = [start if start is not None else 0, end if end is not None else len(chunk_text)]
    if not 0 <= span[0] < span[1] <= len(chunk_text):
        raise ByaxError('invalid_input', 'Excerpt range is outside the chunk text', field='start')
    if span[1] - span[0] > MAX_EXCERPT_CHARS:
        raise ByaxError(
            'limit_exceeded', f'An excerpt is at most {MAX_EXCERPT_CHARS} characters', field='end'
        )
    excerpt = chunk_text[span[0] : span[1]]
    if not excerpt.strip():
        raise ByaxError('invalid_input', 'The excerpt would be empty', field='start')
    anchor = {**selectors(chunk_text, span), 'content_hash': page['content_hash'], 'state': 'anchored'}
    identity = (
        'p_excerpt_'
        + hashlib.sha256(f'{source}:{chunk["chunk_id"]}:{span}:{after}'.encode()).hexdigest()[:16]
    )
    existing = {b['id'] for b in doc.get('blocks', [])}
    while identity in existing:
        identity += '_'
    insert = {'type': 'insert_paragraph', 'id': identity, 'text': excerpt}
    if style in doc.get('styles', {}):
        insert['style'] = style
    if after:
        insert['after'] = after
    elif doc.get('blocks'):
        insert['after'] = doc['blocks'][-1]['id']
    operations = [
        insert,
        {
            'type': 'excerpt',
            'target': identity,
            'start': 0,
            'end': len(excerpt),
            'source': source,
            'quote': excerpt,
            'locator': _locator_of(chunk),
            'anchor': anchor,
        },
    ]
    return {
        'operations': operations,
        'excerpt': {
            'block_id': identity,
            'source': source,
            'chunk': chunk['chunk_id'],
            'span': span,
            'characters': len(excerpt),
        },
    }


# --- checking --------------------------------------------------------------------------------------------


def _record(obj, mark, source):
    anchor = mark.get('anchor') or {}
    position = anchor.get('position') or {'start': 0, 'end': 0}
    pointer = source.get('library') or {}
    locator = {k: v for k, v in (mark.get('locator') or {}).items()}
    return GroundingRecord(
        answer_span=[mark['start'], mark['end']],
        chunk_id=locator.get('chunk'),
        document_id=pointer.get('document_id'),
        library_id=pointer.get('library_id'),
        block_id=locator.get('block'),
        source_span=[position['start'], position['end']],
        quote=anchor.get('quote') or mark.get('quote', ''),
        prefix=anchor.get('prefix', ''),
        suffix=anchor.get('suffix', ''),
        content_hash=anchor.get('content_hash') or pointer.get('content_hash'),
        origin='user',
        locator={k: v for k, v in locator.items() if k in STRUCTURE_KEYS or k == 'chunk'},
        title=(source.get('csl') or {}).get('title'),
        path=pointer.get('path') or None,
    )


def check(doc, *, registry=None, cache_dir=None):
    """Compare every excerpt with its source. Returns ``{'excerpts': [...], 'summary', 'operations'}``.

    ``operations`` are proposals only: ``reanchor_excerpt`` for a pointer that moved or was lost, and,
    where the source text changed, a ``replace_text`` that would bring your copy up to date. Nothing is
    applied, and an excerpt whose source cannot be found keeps its text. ``source_operations`` update
    the source's library pointer after the file changed; they are a separate action from the excerpt
    operations (source corrections and document corrections are never mixed), so apply them first.
    """
    references = doc.get('references', {})
    items, operations, moved = [], [], {}
    for obj, mark in excerpt_marks(doc):
        text = (obj.get('text') or '')[mark['start'] : mark['end']]
        item = {
            'block_id': obj['id'],
            'start': mark['start'],
            'end': mark['end'],
            'source': mark.get('value'),
            'text': text if len(text) <= SNIPPET else text[: SNIPPET - 1] + '…',
        }
        source = references.get(mark.get('value'))
        if not isinstance(source, dict):
            item.update(
                state='source_removed',
                message='The source was removed from the reference list. The excerpt and its pointer are kept.',
            )
            if (mark.get('anchor') or {}).get('state') != 'unanchored':
                operations.append(
                    _reanchor(
                        obj,
                        mark,
                        {**(mark.get('anchor') or {'quote': mark.get('quote', '')}), 'state': 'unanchored'},
                    )
                )
            items.append(item)
            continue
        if not source.get('library'):
            item.update(
                state='unchecked', message='This source is not in a library, so its text cannot be compared.'
            )
            items.append(item)
            continue
        located = locate_record(_record(obj, mark, source), registry=registry, cache_dir=cache_dir)
        status = located['status']
        if status == 'available':
            chunk_text = located['chunk']['text']
            lo, hi = located['span']
            current = chunk_text[lo:hi] if hi > lo else mark.get('quote', '')
            if _same(current, text):
                item.update(state='current', message='The excerpt matches the source.')
            else:
                item.update(
                    state='edited',
                    message='Your copy differs from the source text at the pointer.',
                    source_text=current[:SNIPPET],
                )
            if (mark.get('anchor') or {}).get('state') == 'unanchored':
                operations.append(_reanchor(obj, mark, _anchor_for(located, current), located['locator']))
        elif status == 'reanchored':
            chunk_text = located['chunk']['text']
            lo, hi = located['span']
            fresh = chunk_text[lo:hi]
            item.update(
                state='source_changed',
                message='The source changed; the excerpt was found again'
                + ('' if _same(fresh, text) else ' with different wording')
                + '.',
                source_text=fresh[:SNIPPET],
                method=located.get('method'),
            )
            anchor = _anchor_for(located, fresh)
            pointer = source['library']
            if located['document_id'] != pointer.get('document_id') or anchor.get(
                'content_hash'
            ) != pointer.get('content_hash'):
                moved[mark['value']] = {
                    'document_id': located['document_id'],
                    'chunk_id': located['chunk']['chunk_id'],
                    **({'content_hash': anchor['content_hash']} if anchor.get('content_hash') else {}),
                }
            if not _same(fresh, text) and fresh.strip():
                operations.extend(_refresh(obj, mark, text, fresh))
                operations.append(_reanchor(obj, mark, anchor, located['locator'], length=len(fresh)))
            else:
                operations.append(_reanchor(obj, mark, anchor, located['locator']))
        elif status == 'unanchored':
            item.update(
                state='unanchored',
                message=located.get('reason', 'The excerpt could not be found in the changed source.'),
            )
            if (mark.get('anchor') or {}).get('state') != 'unanchored':
                operations.append(
                    _reanchor(
                        obj,
                        mark,
                        {**(mark.get('anchor') or {'quote': mark.get('quote', '')}), 'state': 'unanchored'},
                    )
                )
        else:
            item.update(
                state='source_unavailable', message=located.get('reason', 'The source is not available.')
            )
        items.append(item)
    summary = dict.fromkeys(EXCERPT_STATES, 0)
    for item in items:
        summary[item['state']] += 1
    source_operations = []
    for source_id, pointer in moved.items():
        updated = copy.deepcopy(references[source_id])
        updated['library'] = {**updated['library'], **pointer}
        updated['verification'] = 'unconfirmed'
        source_operations.append({'type': 'add_reference', 'id': source_id, 'values': updated})
    return {
        'excerpts': items,
        'summary': summary,
        'total': len(items),
        'operations': operations,
        'source_operations': source_operations,
        'review': 'required' if operations or source_operations else 'not_required',
        'note': 'Proposed operations change nothing until applied. An excerpt whose source is gone keeps its text.',
    }


def _same(a, b):
    return normalize(a or '')[0] == normalize(b or '')[0]


def _content_hash(located):
    """The source version now: a re-found excerpt records the current document hash."""
    from .libraries import LibraryIndex

    try:
        index = LibraryIndex(located['library'])
        return index.read(located['document_id'], chunk_id=located['chunk']['chunk_id'])['content_hash']
    except (ByaxError, OSError, KeyError):
        return None


def _anchor_for(located, text):
    chunk_text = located['chunk']['text']
    lo, hi = located['span']
    try:
        anchor = selectors(chunk_text, (lo, hi))
    except ValueError:
        anchor = {'quote': text}
    return {**anchor, 'content_hash': _content_hash(located), 'state': 'anchored'}


def _reanchor(obj, mark, anchor, locator=None, length=None):
    anchor = {
        k: v
        for k, v in anchor.items()
        if k in ('quote', 'prefix', 'suffix', 'position', 'content_hash', 'state') and v is not None
    }
    end = mark['start'] + length if length is not None else mark['end']
    operation = {
        'type': 'reanchor_excerpt',
        'target': obj['id'],
        'start': mark['start'],
        'end': end,
        'source': mark['value'],
        'anchor': anchor,
    }
    if locator:
        operation['locator'] = {k: v for k, v in locator.items() if k in LOCATOR_KEYS}
    return operation


def _refresh(obj, mark, old, new):
    text = obj.get('text') or ''
    occurrence = text[: mark['start']].count(old) + 1 if old else 1
    return [
        {
            'type': 'replace_text',
            'target': obj['id'],
            'match': old,
            'occurrence': occurrence,
            'expected_text': old,
            'replacement': new,
        }
    ]
