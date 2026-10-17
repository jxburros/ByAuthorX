"""Claim-level provenance: who or what wrote each span of text (context engine plan, section 10).

The optional top-level ``provenance`` object (document schema 3) is a local log of where text came
from. Nothing in it is sent anywhere; it is document data like comments and history::

    {
      "version": 1,
      "records": {record_id: {"origin", "author"?, "model"?, "provider"?, "provider_kind"?,
                              "task"?, "proposal"?, "tool"?, "source"?, "created"?,
                              "edited_by_human"?}},
      "spans": {object_id: [[start, end, record_id], ...]},
      "claims": {claim_id: {"target", "start", "end", "record", "state", "grounding": [GroundingRecord]}}
    }

``origin`` is ``human`` (typed in the desktop editor), ``ai_generated`` (an accepted AI proposal
that inserted new text), ``ai_edited`` (an accepted AI proposal that rewrote existing text) or
``pasted`` (``source`` says pasted-from-where when the clipboard told us: source URL, offered MIME
formats). Records are content-addressed (the identity ignores ``created``), so consecutive typing
by one author shares one record and adjacent spans of the same record merge (compaction).

Spans follow text exactly like other anchors: :mod:`byax.anchors` calls :func:`remap_splice`,
:func:`remap_split`, :func:`remap_join` and :func:`orphan` for every splice, paragraph split, join
and deletion, and moves keep identities. A splice carves the replaced range out of every span
(new text is never silently attributed to its neighbours); the edit's own origin is then recorded
over the inserted text by :func:`record`. Text with no span is *unrecorded*: written before the
log existed, imported, or changed by a tool that did not declare an origin.

Every sentence an accepted AI proposal inserted is a *claim* carrying one or more
:class:`byax.grounding.GroundingRecord` (an uncited sentence carries an ``origin: model`` record in
state ``not_checkable``). The answer side re-anchors through the same remapping (an edit inside a
claim marks it ``edited``); the source side re-anchors through :func:`reanchor_sources`
(``grounding.locate_record``, which uses ``quote_match.reanchor``) and its ``reanchor_claim``
operations.

Recording is on unless ``settings.provenance`` is ``false``. Only edits that declare an origin are
recorded: the desktop editor declares ``human`` (or ``pasted``), accepting an AI proposal declares
the proposal's model, and CLI/MCP/REST edits are recorded only when their envelope carries a
``provenance`` object (transports cannot claim ``human`` or ``pasted``; see ``transport_policy``).
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from . import anchors
from .errors import ByaxError
from .model import canonical_json

Doc = dict[str, Any]

VERSION = 1
ORIGINS = ('human', 'ai_generated', 'ai_edited', 'pasted')
AI_ORIGINS = ('ai_generated', 'ai_edited')
TASKS = ('drafting', 'editing', 'summarizing', 'citing', 'translating', 'other')
RECORD_FIELDS = frozenset(
    (
        'origin',
        'author',
        'model',
        'provider',
        'provider_kind',
        'task',
        'proposal',
        'tool',
        'source',
        'created',
        'edited_by_human',
        'note',
    )
)
SOURCE_FIELDS = frozenset(('app', 'url', 'title', 'formats', 'mime'))
CLAIM_STATES = ('anchored', 'edited', 'unanchored')
MAX_TEXT = 2000
MAX_FORMATS = 20
LABELS = {
    'human': 'Typed by a person',
    'ai_generated': 'AI-generated',
    'ai_edited': 'AI-edited',
    'pasted': 'Pasted',
    'unrecorded': 'Not recorded',
}
_SENTENCE_END = re.compile(r'[.!?]["”’)\]]*\s+|\n+')


# --- validation (called from model.validate_document) --------------------------------------------


def _short(value, limit=MAX_TEXT):
    return (
        isinstance(value, str) and len(value) <= limit and not any(0xD800 <= ord(c) <= 0xDFFF for c in value)
    )


def source_problem(source: Any) -> str | None:
    if not isinstance(source, dict) or set(source) - SOURCE_FIELDS:
        return 'Paste sources are objects with app, url, title, formats and mime.'
    for key in ('app', 'url', 'title', 'mime'):
        if key in source and not _short(source[key]):
            return 'Paste source fields must be short text.'
    formats = source.get('formats', [])
    if (
        not isinstance(formats, list)
        or len(formats) > MAX_FORMATS
        or any(not _short(f, 200) for f in formats)
    ):
        return 'Paste source formats must be up to 20 MIME type names.'
    return None


def record_problem(record: Any) -> str | None:
    """Why a provenance record is malformed, or None."""
    if not isinstance(record, dict) or set(record) - RECORD_FIELDS:
        return 'Provenance records use only known fields: ' + ', '.join(sorted(RECORD_FIELDS))
    if record.get('origin') not in ORIGINS:
        return 'Provenance origin must be human, ai_generated, ai_edited or pasted.'
    for key in ('author', 'model', 'provider', 'provider_kind', 'proposal', 'tool', 'created', 'note'):
        if key in record and not _short(record[key]):
            return 'Provenance record fields must be short text.'
    if 'task' in record and record['task'] not in TASKS:
        return 'Provenance task must be one of: ' + ', '.join(TASKS)
    if 'edited_by_human' in record and type(record['edited_by_human']) is not bool:
        return 'edited_by_human must be a boolean.'
    if 'source' in record:
        return source_problem(record['source'])
    return None


def validate_provenance(doc: Doc, fail: Callable[[str, str], Any]) -> None:
    """Structure and types of the optional ``provenance`` object (bounds are clamped, not rejected)."""
    settings = doc.get('settings', {})
    if 'provenance' in settings and type(settings['provenance']) is not bool:
        fail('settings.provenance must be a boolean.', 'settings.provenance')
    if 'provenance' not in doc:
        return
    log = doc['provenance']
    if (
        not isinstance(log, dict)
        or set(log) - {'version', 'records', 'spans', 'claims'}
        or log.get('version') != VERSION
    ):
        fail('Provenance requires version 1 and only records, spans and claims.', 'provenance')
    records, spans, claims = log.get('records', {}), log.get('spans', {}), log.get('claims', {})
    if not isinstance(records, dict) or not isinstance(spans, dict) or not isinstance(claims, dict):
        fail('Provenance records, spans and claims must be mappings.', 'provenance')
    for key, record in records.items():
        problem = record_problem(record)
        if problem:
            fail(problem, f'provenance.records.{key}')
    for target, items in spans.items():
        if not isinstance(items, list):
            fail('Provenance spans must be lists.', f'provenance.spans.{target}')
        for item in items:
            if (
                not isinstance(item, list)
                or len(item) != 3
                or any(type(v) is not int or v < 0 for v in item[:2])
                or item[0] >= item[1]
                or item[2] not in records
            ):
                fail(
                    'Provenance spans are [start, end, record] with start < end.',
                    f'provenance.spans.{target}',
                )
    for key, claim in claims.items():
        if (
            not isinstance(claim, dict)
            or not isinstance(claim.get('target'), str)
            or any(type(claim.get(k)) is not int or claim[k] < 0 for k in ('start', 'end'))
            or claim['start'] > claim['end']
            or claim.get('record') not in records
            or claim.get('state', 'anchored') not in CLAIM_STATES
            or not isinstance(claim.get('grounding', []), list)
            or any(not isinstance(g, dict) for g in claim.get('grounding', []))
        ):
            fail(
                'Provenance claims need a target, a range, a record and grounding records.',
                f'provenance.claims.{key}',
            )


# --- the log ----------------------------------------------------------------------------------------


def _log(doc: Doc, create: bool = False) -> dict[str, Any] | None:
    log = doc.get('provenance')
    if isinstance(log, dict):
        return log
    if not create:
        return None
    doc['provenance'] = {'version': VERSION, 'records': {}, 'spans': {}, 'claims': {}}
    return doc['provenance']


def record_id(record: dict[str, Any]) -> str:
    identity = {k: v for k, v in record.items() if k not in ('created', 'edited_by_human')}
    return 'prov_' + hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:16]


def enabled(doc: Doc) -> bool:
    return doc.get('settings', {}).get('provenance', True) is not False


def current() -> dict[str, Any] | None:
    """The provenance the running edit declared (envelope or accepted proposal), or None."""
    value = anchors.attribution().get('provenance')
    return value if isinstance(value, dict) and value.get('origin') in ORIGINS else None


def envelope_problem(value: Any) -> str | None:
    """Validation for the ``provenance`` field of an edit envelope."""
    if not isinstance(value, dict):
        return 'Envelope provenance must be an object.'
    allowed = {'origin', 'model', 'provider', 'provider_kind', 'task', 'tool', 'source', 'note'}
    if set(value) - allowed:
        return 'Envelope provenance fields are ' + ', '.join(sorted(allowed)) + '.'
    return record_problem(value)


def _new_record(doc: Doc, declared: dict[str, Any]) -> str:
    log = _log(doc, create=True)
    assert log is not None
    attribution = anchors.attribution()
    record = {k: copy.deepcopy(v) for k, v in declared.items() if k in RECORD_FIELDS and k != 'grounding'}
    if attribution.get('author') and 'author' not in record:
        record['author'] = attribution['author']
    if attribution.get('proposal') and record['origin'] in AI_ORIGINS:
        record.setdefault('proposal', attribution['proposal'])
    record.pop('created', None)
    identity = record_id(record)
    if identity not in log['records']:
        record['created'] = attribution.get('timestamp') or datetime.now(UTC).isoformat()
        log['records'][identity] = record
    return identity


def _objects(doc: Doc) -> dict[str, str]:
    """``{object_id: text}`` for every text-bearing object (blocks and table cells)."""
    result = {}
    for block in doc.get('blocks', []):
        if block.get('type') == 'table':
            for row in block.get('rows', []):
                for cell in row.get('cells', []):
                    result[cell['id']] = cell.get('text', '')
        else:
            result[block['id']] = block.get('text', '')
    return result


def _merge(items):
    """Sort, drop empty ranges and merge touching spans of the same record."""
    result: list[list[Any]] = []
    for start, end, rid in sorted(items, key=lambda item: (item[0], item[1])):
        if end <= start:
            continue
        if result and result[-1][2] == rid and result[-1][1] >= start:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end, rid])
    return result


def _set_spans(log, target, items):
    items = _merge(items)
    if items:
        log['spans'][target] = items
    else:
        log['spans'].pop(target, None)


# --- remapping (called from byax.anchors) -------------------------------------------------------


def _carve(items, start, end, length, log):
    """Spans after replacing [start, end) with ``length`` new scalars; the new text is uncovered."""
    delta = length - (end - start)
    declared = current()
    human = declared is not None and declared['origin'] == 'human'
    result = []
    for a, b, rid in items:
        if b <= start:
            result.append([a, b, rid])
        elif a >= end:
            # Includes an insertion exactly at a span's start, which pushes it right.
            result.append([a + delta, b + delta, rid])
        else:
            if human and log['records'].get(rid, {}).get('origin') in AI_ORIGINS:
                log['records'][rid]['edited_by_human'] = True
            if a < start:
                result.append([a, start, rid])
            if b > end:
                result.append([end + delta, b + delta, rid])
    return result


def remap_splice(doc: Doc, obj: dict[str, Any], start: int, end: int, length: int) -> None:
    log = _log(doc)
    if log is None:
        return
    target = obj['id']
    if target in log['spans']:
        _set_spans(log, target, _carve(log['spans'][target], start, end, length, log))
    for identity, claim in list(log['claims'].items()):
        if claim['target'] != target:
            continue
        before = {'target': target, 'start': claim['start'], 'end': claim['end']}
        a, b, effect = anchors.map_range(claim['start'], claim['end'], start, end, length)
        if effect == 'deleted' or b <= a:
            del log['claims'][identity]
            anchors.note(
                'anchor_mappings',
                {'kind': 'claim', 'id': identity, 'from': before, 'to': None, 'status': 'deleted'},
            )
            continue
        claim['start'], claim['end'] = a, b
        if effect in ('edited', 'overlapped', 'replaced'):
            claim['state'] = 'edited' if claim.get('state') != 'unanchored' else 'unanchored'
            anchors.note(
                'anchor_mappings',
                {
                    'kind': 'claim',
                    'id': identity,
                    'from': before,
                    'to': {'target': target, 'start': a, 'end': b},
                    'status': effect,
                },
            )


def remap_split(doc: Doc, obj: dict[str, Any], new: dict[str, Any], offset: int) -> None:
    log = _log(doc)
    if log is None:
        return
    source, created = obj['id'], new['id']
    items = log['spans'].pop(source, [])
    head = [[a, min(b, offset), rid] for a, b, rid in items if a < offset]
    tail = [[max(a, offset) - offset, b - offset, rid] for a, b, rid in items if b > offset]
    _set_spans(log, source, head)
    _set_spans(log, created, tail + log['spans'].pop(created, []))
    for claim in log['claims'].values():
        if claim['target'] != source:
            continue
        if claim['start'] >= offset:
            claim.update(target=created, start=claim['start'] - offset, end=claim['end'] - offset)
        elif claim['end'] > offset:
            claim['end'], claim['state'] = offset, 'edited'


def remap_join(doc: Doc, obj: dict[str, Any], other: dict[str, Any], offset: int) -> None:
    log = _log(doc)
    if log is None:
        return
    target, removed = obj['id'], other['id']
    moved = [[a + offset, b + offset, rid] for a, b, rid in log['spans'].pop(removed, [])]
    if moved or target in log['spans']:
        _set_spans(log, target, log['spans'].get(target, []) + moved)
    for claim in log['claims'].values():
        if claim['target'] == removed:
            claim.update(target=target, start=claim['start'] + offset, end=claim['end'] + offset)


def orphan(doc: Doc, removed_ids: Any) -> None:
    """Deleted objects take their spans and claims with them."""
    log = _log(doc)
    if log is None:
        return
    for identity in removed_ids:
        log['spans'].pop(identity, None)
    for key in [k for k, c in log['claims'].items() if c['target'] in removed_ids]:
        del log['claims'][key]


def forget(doc: Doc, ids: Any) -> None:
    """Objects whose text was replaced wholesale (replace_content) lose their old spans."""
    orphan(doc, set(ids))


def normalize(doc: Doc) -> None:
    """Drop spans and claims of missing objects, clamp to the text, merge, prune unused records."""
    log = _log(doc)
    if log is None:
        return
    texts = _objects(doc)
    for target in list(log['spans']):
        if target not in texts:
            del log['spans'][target]
            continue
        size = len(texts[target])
        _set_spans(log, target, [[a, min(b, size), rid] for a, b, rid in log['spans'][target] if a < size])
    for key, claim in list(log['claims'].items()):
        size = len(texts.get(claim['target'], ''))
        if claim['target'] not in texts or claim['start'] >= size:
            del log['claims'][key]
        else:
            claim['end'] = min(claim['end'], size)
    used = {rid for items in log['spans'].values() for _, _, rid in items} | {
        c['record'] for c in log['claims'].values()
    }
    for rid in [r for r in log['records'] if r not in used]:
        del log['records'][rid]
    if not log['records'] and not log['spans'] and not log['claims']:
        doc.pop('provenance', None)


# --- recording (called from byax.engine text operations) ----------------------------------------


def _sentences(text, start, length):
    """Sentence ranges (absolute) inside the inserted text [start, start + length)."""
    piece = text[start : start + length]
    result, cursor = [], 0
    for match in _SENTENCE_END.finditer(piece):
        result.append((cursor, match.end()))
        cursor = match.end()
    if cursor < len(piece):
        result.append((cursor, len(piece)))
    spans = []
    for a, b in result:
        chunk = piece[a:b]
        lead = len(chunk) - len(chunk.lstrip())
        trail = len(chunk) - len(chunk.rstrip())
        if b - trail > a + lead:
            spans.append((start + a + lead, start + b - trail))
    return spans


def _uncited(claim_text, span):
    from .grounding import GroundingRecord

    return GroundingRecord(
        answer_span=list(span),
        chunk_id=None,
        document_id=None,
        library_id=None,
        block_id=None,
        source_span=None,
        quote='',
        prefix='',
        suffix='',
        content_hash=None,
        origin='model',
        state='not_checkable',
        citation_id=None,
        claim=claim_text,
        reason='No source was cited for this sentence.',
    ).to_dict()


def _claims(doc, log, obj, start, length, rid, grounding):
    text = obj.get('text', '')
    for a, b in _sentences(text, start, length):
        relative = (a - start, b - start)
        attached = []
        for record in grounding:
            span = record.get('answer_span')
            if isinstance(span, list) and len(span) == 2 and span[0] < relative[1] and span[1] > relative[0]:
                item = copy.deepcopy(record)
                item.pop('operation', None)
                item['answer_span'] = [a, b]
                attached.append(item)
        if not attached:
            attached = [_uncited(text[a:b], (a, b))]
        identity = (
            'claim_' + hashlib.sha256(f'{rid}:{obj["id"]}:{a}:{b}:{text[a:b]}'.encode()).hexdigest()[:16]
        )
        log['claims'][identity] = {
            'target': obj['id'],
            'start': a,
            'end': b,
            'record': rid,
            'state': 'anchored',
            'grounding': attached,
        }


def record(doc: Doc, obj: dict[str, Any], start: int, length: int) -> None:
    """Attribute the ``length`` scalars inserted at ``start`` in ``obj`` to the running edit."""
    declared = current()
    if declared is None or length <= 0 or not enabled(doc) or 'text' not in obj:
        return
    rid = _new_record(doc, declared)
    log = _log(doc)
    assert log is not None
    items = log['spans'].get(obj['id'], [])
    _set_spans(log, obj['id'], items + [[start, start + length, rid]])
    if declared['origin'] in AI_ORIGINS:
        _claims(doc, log, obj, start, length, rid, declared.get('grounding') or [])


def record_object(doc: Doc, obj: dict[str, Any]) -> None:
    """Attribute the whole text of a newly created object (inserted paragraph or cell)."""
    record(doc, obj, 0, len(obj.get('text', '')))


# --- proposals -------------------------------------------------------------------------------------

_EDIT_KINDS = ('replace_text', 'set_text', 'update_cell', 'set_block')


def is_ai_proposal(proposal: dict[str, Any]) -> bool:
    meta = proposal.get('provenance')
    return isinstance(meta, dict) and bool(meta.get('model') or meta.get('provider'))


def task_of(proposal: dict[str, Any]) -> str:
    meta = proposal.get('provenance') or {}
    if meta.get('task') in TASKS:
        return meta['task']
    words = (
        ' '.join(str(proposal.get(key, '')) for key in ('kind', 'origin', 'explanation')).casefold()
        + ' '
        + str(meta.get('scope', '')).casefold()
    )
    if 'summar' in words or 'synopsis' in words:
        return 'summarizing'
    if 'translat' in words:
        return 'translating'
    operations = proposal.get('operations', [])
    kinds = {o.get('type', o.get('op')) for o in operations}
    if kinds & {'cite', 'add_reference'}:
        return 'citing'
    if kinds & set(_EDIT_KINDS) or any(o.get('expected_text') for o in operations):
        return 'editing'
    return 'drafting'


def from_proposal(proposal: dict[str, Any], index: int) -> dict[str, Any] | None:
    """The provenance an accepted AI proposal declares for its operation ``index``, or None."""
    if not is_ai_proposal(proposal):
        return None
    meta = proposal['provenance']
    operations = proposal.get('operations', [])
    rewrites = any(
        o.get('type', o.get('op')) in _EDIT_KINDS or o.get('expected_text') or o.get('type') == 'delete_text'
        for o in operations
    )
    declared: dict[str, Any] = {
        'origin': 'ai_edited' if rewrites else 'ai_generated',
        'task': task_of(proposal),
        'tool': 'assistant',
    }
    for key in ('model', 'provider', 'provider_kind'):
        if isinstance(meta.get(key), str) and meta[key]:
            declared[key] = meta[key][:MAX_TEXT]
    grounding = [
        g for g in meta.get('grounding') or [] if isinstance(g, dict) and g.get('operation', index) == index
    ]
    if grounding:
        declared['grounding'] = grounding
    return declared


def ground_proposal(
    proposal: dict[str, Any],
    passages: list[dict[str, Any]] | None = None,
    references: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach GroundingRecords to an AI proposal: ``[[cite:ID]]`` tokens in inserted text become records.

    The tokens are removed from the operation text (the record keeps where they were), so the
    accepted text contains no protocol markup. Records carry ``operation`` (the operation index)
    and an ``answer_span`` relative to that operation's inserted text.
    """
    from .grounding import ground_reply

    result = copy.deepcopy(proposal)
    records = []
    for index, operation in enumerate(result.get('operations', [])):
        key = 'replacement' if operation.get('type') == 'replace_text' else 'text'
        text = operation.get(key)
        if not isinstance(text, str) or '[[cite:' not in text:
            continue
        grounded = ground_reply(text, passages=passages, references=references)
        operation[key] = grounded['text']
        for item in grounded['records']:
            records.append({**item, 'operation': index})
    if records:
        result.setdefault('provenance', {})['grounding'] = records
    return result


# --- source-side re-anchoring ----------------------------------------------------------------------


def reanchor_sources(doc: Doc, *, registry: Any = None, cache_dir: Any = None) -> dict[str, Any]:
    """Re-find every claim's cited source span (``grounding.locate_record``); propose updates.

    Returns ``{'claims': [...], 'operations': [...], 'summary'}``. A source that changed is
    re-anchored when the quote is found again; otherwise the record becomes ``unanchored`` and is
    kept. Reads only attached local library indexes; ``operations`` change nothing until applied.
    """
    from .grounding import locate_record
    from .quote_match import selectors

    log = _log(doc) or {'claims': {}}
    rows, operations = [], []
    summary = {'available': 0, 'reanchored': 0, 'unanchored': 0, 'unavailable': 0, 'uncited': 0}
    for identity, claim in sorted(log['claims'].items()):
        updated, changed, statuses = [], False, []
        for item in claim.get('grounding', []):
            if not item.get('library_id') or not item.get('document_id'):
                updated.append(item)
                statuses.append('uncited' if item.get('origin') == 'model' else 'unavailable')
                continue
            located = locate_record(item, registry=registry, cache_dir=cache_dir)
            status = located['status']
            statuses.append(status)
            new = copy.deepcopy(item)
            if status == 'reanchored':
                chunk, span = located['chunk'], located['span']
                anchor = selectors(chunk['text'], span)
                new.update(
                    chunk_id=chunk['chunk_id'],
                    document_id=located['document_id'],
                    source_span=list(span),
                    quote=anchor['quote'],
                    prefix=anchor['prefix'],
                    suffix=anchor['suffix'],
                    content_hash=chunk.get('content_hash') or new.get('content_hash'),
                    locator=located['locator'],
                )
                changed = True
            elif status == 'unanchored' and new.get('state') != 'unanchored':
                new['state'] = 'unanchored'
                new['reason'] = located.get('reason')
                changed = True
            updated.append(new)
        for status in statuses:
            summary[status] = summary.get(status, 0) + 1
        rows.append(
            {'claim': identity, **{k: claim[k] for k in ('target', 'start', 'end')}, 'statuses': statuses}
        )
        if changed:
            operations.append({'type': 'reanchor_claim', 'target': identity, 'grounding': updated})
    return {
        'claims': rows,
        'operations': operations,
        'summary': summary,
        'network': False,
        'note': 'Source-side re-anchoring of AI claims. Proposed operations change nothing until applied.',
    }


# --- canonical operations --------------------------------------------------------------------------

OP_SCHEMAS = {
    'reanchor_claim': ('target grounding', ''),
    'clear_provenance': ('', 'targets'),
}


def _op_reanchor_claim(doc, op, base_revision, kind, changed, allow_policy_changes):
    log = _log(doc)
    claim = (log or {}).get('claims', {}).get(op['target'])
    if claim is None:
        raise ByaxError('missing_target', 'Provenance claim does not exist.', target=op['target'])
    grounding = op['grounding']
    if not isinstance(grounding, list) or not grounding or any(not isinstance(g, dict) for g in grounding):
        raise ByaxError('schema_error', 'Grounding must be a nonempty list of records.', field='grounding')
    claim['grounding'] = copy.deepcopy(grounding)
    if all(g.get('state') == 'unanchored' for g in grounding if g.get('library_id')) and any(
        g.get('library_id') for g in grounding
    ):
        claim['state'] = 'unanchored'
    changed.append(op['target'])


def _op_clear(doc, op, base_revision, kind, changed, allow_policy_changes):
    """Forget the provenance of some objects (``targets``) or of the whole document."""
    log = _log(doc)
    if log is None:
        return
    if 'targets' in op:
        orphan(doc, set(op['targets']))
        normalize(doc)
    else:
        doc.pop('provenance', None)
    changed.append('provenance')


HANDLERS = {'reanchor_claim': _op_reanchor_claim, 'clear_provenance': _op_clear}


# --- reporting -------------------------------------------------------------------------------------


def _sections(doc):
    """``[(section_id, title, [object ids])]`` by heading (binder items when present)."""
    sections: list[tuple[str | None, str, list[str]]] = [(None, '(before the first heading)', [])]
    for block in doc.get('blocks', []):
        if block.get('type') == 'heading':
            sections.append((block['id'], block.get('text', ''), []))
        ids = (
            [c['id'] for r in block.get('rows', []) for c in r.get('cells', [])]
            if block.get('type') == 'table'
            else [block['id']]
        )
        sections[-1][2].extend([*ids] if block.get('type') != 'heading' else ids)
    if not sections[0][2]:
        sections.pop(0)
    return sections


def _scope(doc, target):
    if target is None:
        return None
    for section_id, _, ids in _sections(doc):
        if section_id == target:
            return set(ids)
    from . import project

    item = project.find_item(doc, target) if isinstance(doc.get('project'), dict) else None
    if item is not None:
        return _scope(doc, item['heading'])
    texts = _objects(doc)
    if target in texts:
        return {target}
    for block in doc.get('blocks', []):
        if block['id'] == target and block.get('type') == 'table':
            return {c['id'] for r in block['rows'] for c in r['cells']}
    raise ByaxError('missing_target', 'The provenance target does not exist.', target=target)


def _shares(counts, total):
    return {key: round(100.0 * value / total, 1) if total else 0.0 for key, value in counts.items()}


def _counts(doc, log, ids, texts):
    counts = dict.fromkeys((*ORIGINS, 'unrecorded'), 0)
    total = 0
    for identity in ids:
        size = len(texts.get(identity, ''))
        total += size
        covered = 0
        for a, b, rid in (log or {}).get('spans', {}).get(identity, []):
            a, b = min(a, size), min(b, size)
            counts[log['records'][rid]['origin']] += b - a
            covered += b - a
        counts['unrecorded'] += size - covered
    return counts, total


def report(doc: Doc, target: str | None = None, *, limit: int = 500) -> dict[str, Any]:
    """Per-span provenance listing plus the share of each origin, per section and overall.

    ``target`` limits the report to a section heading, binder item, block or table. Shares are
    percentages of characters (Unicode scalars) of body text. Local and read-only.
    """
    log = _log(doc)
    texts = _objects(doc)
    wanted = _scope(doc, target)
    sections = []
    for section_id, title, ids in _sections(doc):
        ids = [i for i in ids if wanted is None or i in wanted]
        if not ids:
            continue
        counts, total = _counts(doc, log, ids, texts)
        sections.append(
            {
                'id': section_id,
                'title': title,
                'characters': total,
                'counts': counts,
                'percentages': _shares(counts, total),
            }
        )
    order = [i for _, _, ids in _sections(doc) for i in ids]
    scope_ids = [i for i in order if wanted is None or i in wanted]
    counts, total = _counts(doc, log, scope_ids, texts)
    records = (log or {}).get('records', {})
    spans = []
    for identity in scope_ids:
        for a, b, rid in (log or {}).get('spans', {}).get(identity, []):
            meta = records[rid]
            excerpt = texts.get(identity, '')[a:b]
            spans.append(
                {
                    'target': identity,
                    'start': a,
                    'end': b,
                    'origin': meta['origin'],
                    'record': rid,
                    'text': excerpt if len(excerpt) <= 80 else excerpt[:77] + '…',
                    **{k: meta[k] for k in ('model', 'provider', 'author', 'task', 'source') if k in meta},
                }
            )
    claims = [
        {
            'id': key,
            **{k: c[k] for k in ('target', 'start', 'end', 'record', 'state')},
            'text': texts.get(c['target'], '')[c['start'] : c['end']],
            'grounding': [
                {
                    'state': g.get('state'),
                    'title': g.get('title'),
                    'citation_id': g.get('citation_id'),
                    'quote': g.get('quote', ''),
                    'library_id': g.get('library_id'),
                    'content_hash': g.get('content_hash'),
                    'origin': g.get('origin'),
                }
                for g in c.get('grounding', [])
            ],
        }
        for key, c in sorted(
            (log or {}).get('claims', {}).items(), key=lambda kv: (kv[1]['target'], kv[1]['start'])
        )
        if c['target'] in set(scope_ids)
    ]
    used = {s['record'] for s in spans} | {c['record'] for c in claims}
    models = sorted(
        {
            (r.get('model', ''), r.get('provider', ''))
            for k, r in records.items()
            if k in used and r['origin'] in AI_ORIGINS
        }
    )
    return {
        'scope': target or 'document',
        'recording': 'on' if enabled(doc) else 'off',
        'characters': total,
        'counts': counts,
        'percentages': _shares(counts, total),
        'sections': sections,
        'spans': spans[:limit],
        'total_spans': len(spans),
        'truncated': len(spans) > limit,
        'claims': claims[:limit],
        'total_claims': len(claims),
        'records': {k: copy.deepcopy(v) for k, v in sorted(records.items()) if k in used},
        'models': [{'model': m, 'provider': p} for m, p in models],
        'tasks': sorted({r['task'] for k, r in records.items() if k in used and r.get('task')}),
        'labels': dict(LABELS),
        'network': False,
        'note': 'Provenance is local document data and is never sent anywhere. Text without a span was '
        'written before recording began, imported, or changed by a tool that did not declare an origin.',
    }


def _phrase(origin: str) -> str:
    label = LABELS[origin]
    return label if label.startswith('AI') else label[0].lower() + label[1:]


def describe(result: dict[str, Any]) -> list[str]:
    """Plain-language lines for the report (the GUI panel and the human CLI view)."""
    shares = result['percentages']
    lines = [
        f'{result["characters"]} characters: '
        + ', '.join(f'{shares[k]}% {_phrase(k)}' for k in (*ORIGINS, 'unrecorded') if result['counts'][k])
    ]
    for section in result['sections']:
        parts = ', '.join(
            f'{section["percentages"][k]}% {_phrase(k)}'
            for k in (*ORIGINS, 'unrecorded')
            if section['counts'][k]
        )
        lines.append(f'{section["title"] or "(untitled)"}: {parts or "empty"}')
    return lines
