"""Transactional semantic operations. No filesystem, network or UI dependencies."""

import base64
import copy
import difflib
import hashlib
import re
import threading
from datetime import UTC, datetime

import regex as unicode_regex

from .checks import run_checks
from .errors import ByaxError
from .model import canonical_json, find_object, new_id, object_hash, snapshot, text_of, validate_document

REQUEST_LIMIT = 500

# Required fields, optional fields. Unknown fields are always rejected.
OP_SCHEMAS = {
    'insert_text': ('target offset text', ''),
    'replace_text': ('target match replacement', 'occurrence expected_text'),
    'delete_text': ('target start end', 'expected_text'),
    'set_text': ('target text', 'expected_text'),
    'insert_paragraph': ('text', 'after style id'),
    'delete_block': ('target', ''),
    'move_block': ('target', 'after'),
    'move_section': ('target', 'after'),
    'reorder_row': ('target index to_index', ''),
    'reorder_column': ('target index to_index', ''),
    'reply_comment': ('target text', 'author'),
    'split_block': ('target offset', 'id'),
    'join_blocks': ('target next', ''),
    'apply_style': ('target style', ''),
    'format_text': ('target start end marks', ''),
    'set_metadata': ('values', ''),
    'set_layout': ('values', ''),
    'set_settings': ('values', ''),
    'define_style': ('name values', ''),
    'insert_table': ('rows columns', 'after'),
    'update_cell': ('target text', 'expected_text'),
    'add_row': ('target', 'index values'),
    'remove_row': ('target index', ''),
    'add_column': ('target', 'index values'),
    'remove_column': ('target index', ''),
    'add_comment': ('target text', 'start end author'),
    'resolve_comment': ('target', ''),
    'add_reference': ('id values', ''),
    'fill_fields': ('values', ''),
    'add_layer': ('values', ''),
    'update_layer': ('target values', ''),
    'delete_layer': ('target', ''),
    'add_asset': ('data mime', 'name'),
    'place_image': ('asset alt', 'after width height'),
    'set_attributes': ('target values', ''),
    'protect': ('target kind', ''),
    'unprotect': ('target kind', ''),
    'set_tracking': ('enabled', ''),
    'propose': ('values', ''),
    'accept_proposal': ('target', ''),
    'reject_proposal': ('target', ''),
    'set_requirement': ('id values', ''),
    'set_profile': ('id values', ''),
    'accept_change': ('target', ''),
    'reject_change': ('target', ''),
    'compact_history': ('keep_last', ''),
    'replace_content': ('blocks', 'assets import_provenance'),
    'set_bookmark': ('name target', 'offset'),
    'remove_bookmark': ('name', ''),
}


def operation_schema():
    return {
        'schema_version': 1,
        'discriminator': 'type',
        'offset_unit': 'unicode_scalar',
        'end_exclusive': True,
        'grapheme_boundaries_required': True,
        'operations': {
            k: {
                'required': a.split(),
                'optional': b.split() + ['expected_hash'],
                'additional_properties': False,
            }
            for k, (a, b) in OP_SCHEMAS.items()
        },
    }


def _error(message, field=None, **details):
    raise ByaxError('schema_error', message, field=field, **details)


def _validate_op(op, index):
    if not isinstance(op, dict):
        _error('Operation must be an object.', f'operations[{index}]')
    if 'op' in op and 'type' in op:
        _error('Use one operation discriminator.', f'operations[{index}]')
    kind = op.get('type', op.get('op'))
    if kind not in OP_SCHEMAS:
        _error('Unknown operation type.', f'operations[{index}].type', supported=list(OP_SCHEMAS))
    required, optional = (set(x.split()) for x in OP_SCHEMAS[kind])
    missing, unknown = required - op.keys(), op.keys() - required - optional - {'type', 'op', 'expected_hash'}
    if missing or unknown:
        _error(
            'Missing required or unknown operation fields.',
            f'operations[{index}]',
            missing=sorted(missing),
            unknown=sorted(unknown),
        )
    for key in (
        'target',
        'next',
        'after',
        'text',
        'replacement',
        'match',
        'expected_text',
        'style',
        'id',
        'name',
        'data',
        'mime',
        'asset',
        'alt',
        'author',
        'kind',
        'expected_hash',
    ):
        if key in op and not isinstance(op[key], str) and not (key == 'after' and op[key] is None):
            _error('Expected a string.', f'operations[{index}].{key}')
    for key in ('offset', 'start', 'end', 'occurrence', 'rows', 'columns', 'index', 'to_index', 'keep_last'):
        if key in op and (type(op[key]) is not int or op[key] < 0):
            _error('Expected a nonnegative integer.', f'operations[{index}].{key}')
    for key in ('width', 'height'):
        if key in op and (type(op[key]) not in (int, float) or op[key] <= 0):
            _error('Expected a positive number.', f'operations[{index}].{key}')
    if 'values' in op and not isinstance(op['values'], list if kind in ('add_row', 'add_column') else dict):
        _error('Expected an object.', f'operations[{index}].values')
    if 'enabled' in op and type(op['enabled']) is not bool:
        _error('Expected a boolean.', f'operations[{index}].enabled')
    return kind


def grapheme_boundaries(text):
    return [0] + [m.end() for m in unicode_regex.finditer(r'\X', text)]


def scalar_to_utf16(text, offset):
    _boundaries(text, offset, offset)
    return len(text[:offset].encode('utf-16-le')) // 2


def utf16_to_scalar(text, offset):
    if type(offset) is not int or offset < 0:
        _error('UTF-16 offset must be a nonnegative integer.', 'offset')
    consumed = 0
    for i, ch in enumerate(text):
        if consumed == offset:
            _boundaries(text, i, i)
            return i
        consumed += 2 if ord(ch) > 0xFFFF else 1
    if consumed == offset:
        return len(text)
    _error('Offset splits a surrogate pair or is out of range.', 'offset')


def _boundaries(text, start, end):
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
        _error('Text range is out of bounds.', 'range', length=len(text))
    boundaries = grapheme_boundaries(text)
    for value in (start, end):
        if value not in boundaries:
            _error(
                'Selection cannot split a grapheme cluster.',
                'range',
                offset=value,
                valid_alternatives=[
                    max(x for x in boundaries if x < value),
                    min(x for x in boundaries if x > value),
                ],
            )


def _splice(obj, start, end, replacement):
    original = obj.get('text', '')
    _boundaries(original, start, end)
    obj['text'] = original[:start] + replacement + original[end:]
    delta = len(replacement) - (end - start)
    marks = []
    for mark in obj.get('marks', []):
        mark = copy.deepcopy(mark)
        a, b = mark['start'], mark['end']
        if b <= start:
            pass
        elif a >= end:
            mark['start'], mark['end'] = a + delta, b + delta
        elif mark.get('type') == 'field':
            mark['start'] = min(a, start)
            mark['end'] = max(start + len(replacement), b + delta) if b > end else start + len(replacement)
        elif a <= start and b >= end:
            # A mark covering the replaced range covers its replacement.
            mark['end'] = b + delta
        elif a >= start and b <= end:
            # Formatting wholly inside replaced text does not spread over new text.
            continue
        elif a < start:
            mark['end'] = start
        else:
            mark['start'], mark['end'] = start + len(replacement), b + delta
        if mark['end'] > mark['start'] or mark.get('type') == 'field':
            marks.append(mark)
    if 'marks' in obj:
        # Inserted combining characters can extend a grapheme past a mark edge.
        boundaries = grapheme_boundaries(obj['text'])
        for mark in marks:
            for key in ('start', 'end'):
                if mark[key] not in boundaries:
                    mark[key] = min(x for x in boundaries if x > mark[key])
        obj['marks'] = marks


def _fill_fields(doc, names, revision):
    changed = []
    for block in doc['blocks']:
        targets = [block] if block['type'] != 'table' else [c for row in block['rows'] for c in row['cells']]
        for obj in targets:
            text = obj.get('text', '')
            replacements = []
            field_marks = [
                m for m in obj.get('marks', []) if m.get('type') == 'field' and m.get('value') in names
            ]
            for mark in field_marks:
                replacements.append((mark['start'], mark['end'], mark['value']))
            for match in re.finditer(r'\{\{\s*([\w.-]+)\s*\}\}', text):
                if match.group(1) in names and not any(
                    m['start'] <= match.start() and m['end'] >= match.end() for m in field_marks
                ):
                    replacements.append((match.start(), match.end(), match.group(1)))
            if not replacements:
                continue
            if (
                _is_locked(block)
                or _is_locked(obj)
                or any(_is_locked(row) for row in block.get('rows', []) if obj in row['cells'])
            ):
                raise ByaxError('protected_edit', 'A field occurs in locked content.', target=obj['id'])
            before_text, before_marks = obj['text'], copy.deepcopy(obj.get('marks', []))
            for start, end, name in sorted(replacements, reverse=True):
                value = doc['fields'][name]
                value = value.get('value') if isinstance(value, dict) else value
                if isinstance(value, (dict, list)):
                    _error('Materialized fields must have scalar values.', 'values.' + name)
                replacement = (
                    '' if value is None else str(value).lower() if type(value) is bool else str(value)
                )
                obj.setdefault('marks', [])
                obj['marks'] = [
                    m
                    for m in obj['marks']
                    if not (
                        m.get('type') == 'field'
                        and m.get('value') == name
                        and m['start'] == start
                        and m['end'] == end
                    )
                ]
                _splice(obj, start, end, replacement)
                obj['marks'].append(
                    {'type': 'field', 'value': name, 'start': start, 'end': start + len(replacement)}
                )
                _adjust_anchors(doc, obj['id'], start, end, len(replacement))
            changed.append(obj['id'])
            if doc['settings'].get('track_changes') and before_text != obj['text']:
                doc['review']['tracked_changes'].append(
                    {
                        'id': new_id('change'),
                        'target': obj['id'],
                        'before': before_text,
                        'after': obj['text'],
                        'before_marks': before_marks,
                        'after_marks': copy.deepcopy(obj['marks']),
                        'status': 'pending',
                        'base_revision': revision,
                    }
                )
    return changed


def _orphan_review(doc, removed_ids):
    for bookmark in doc['settings'].get('bookmarks', {}).values():
        if bookmark.get('target') in removed_ids:
            bookmark['status'] = 'orphaned'
    for collection in ('comments', 'proposals', 'tracked_changes'):
        pending = list(doc['review'][collection])
        while pending:
            item = pending.pop()
            if collection == 'comments':
                pending.extend(item.get('replies', []))
            targets = {item.get('target')} | {o.get('target') for o in item.get('operations', [])}
            if targets & removed_ids:
                item['status'] = 'orphaned'


def _format_state(obj):
    return copy.deepcopy({k: obj[k] for k in ('marks', 'style', 'attrs', 'type') if k in obj})


def _track_format(doc, obj, before, revision):
    after = _format_state(obj)
    if doc['settings'].get('track_changes') and before != after:
        doc['review']['tracked_changes'].append(
            {
                'id': new_id('change'),
                'kind': 'formatting',
                'target': obj['id'],
                'before': before,
                'after': after,
                'status': 'pending',
                'base_revision': revision,
            }
        )


def _adjust_anchors(doc, target, start, end, inserted):
    delta = inserted - (end - start)
    for bookmark in doc['settings'].get('bookmarks', {}).values():
        if bookmark.get('target') == target:
            offset = bookmark.get('offset', 0)
            bookmark['offset'] = (
                offset if offset <= start else offset + delta if offset >= end else start + inserted
            )
    for comment in doc['review']['comments']:
        if comment.get('target') != target or comment.get('start') is None:
            continue
        a, b = comment['start'], comment['end']
        if b <= start:
            continue
        if a >= end:
            comment['start'], comment['end'] = a + delta, b + delta
        else:
            comment['start'] = min(a, start)
            comment['end'] = max(start + inserted, b + delta)
            comment['anchor_status'] = 'needs_review'


def _paragraph(text, style='body', identity=None):
    return {
        'id': identity or new_id('p'),
        'type': 'paragraph',
        'text': text,
        'style': style,
        'marks': [],
        'attrs': {},
    }


def _cell(text=''):
    return {'id': new_id('cell'), 'text': text, 'attrs': {}}


def _row(columns):
    return {'id': new_id('row'), 'cells': [_cell() for _ in range(columns)]}


def _insert(doc, obj, after=None):
    if after is None:
        doc['blocks'].append(obj)
    else:
        indices = [i for i, b in enumerate(doc['blocks']) if b['id'] == after]
        if not indices:
            raise ByaxError('missing_target', 'Insertion anchor is missing.', target=after)
        doc['blocks'].insert(indices[0] + 1, obj)


def _target(doc, op):
    obj = find_object(doc, op['target'])
    if op.get('expected_hash') and object_hash(obj) != op['expected_hash']:
        raise ByaxError('precondition_failed', 'Target hash changed.', target=op['target'])
    if _is_locked(obj) or any(_is_locked(parent) for parent in _containers(doc, obj['id'])):
        raise ByaxError('protected_edit', 'Target is locked.', target=op['target'])
    return obj


def _is_locked(obj):
    return bool(obj.get('attrs', {}).get('locked') or obj.get('locked'))


def _containers(doc, target):
    """Tables and rows that contain a row or cell; locks apply to their contents."""
    for block in doc['blocks']:
        for row in block.get('rows', []):
            if row.get('id') == target:
                return [block]
            if any(cell.get('id') == target for cell in row.get('cells', [])):
                return [block, row]
    return []


def _find_review(doc, collection, target):
    pending = list(doc['review'][collection])
    while pending:
        item = pending.pop(0)
        if item['id'] == target:
            return item
        if collection == 'comments':
            pending.extend(item.get('replies', []))
    raise ByaxError('missing_target', 'Review item does not exist.', target=target)


def _section_end(blocks, start):
    level = blocks[start].get('attrs', {}).get('level', 1)
    return next(
        (
            i
            for i in range(start + 1, len(blocks))
            if blocks[i]['type'] == 'heading' and blocks[i].get('attrs', {}).get('level', 1) <= level
        ),
        len(blocks),
    )


def _move_section(doc, op):
    heading = _target(doc, op)
    if heading.get('type') != 'heading' or heading not in doc['blocks']:
        _error('Section target must be a heading.', 'target')
    blocks = doc['blocks']
    start = blocks.index(heading)
    members = {b['id'] for b in blocks[start : _section_end(blocks, start)]}
    # Explicit anchors may lie outside semantic reading order; move them with
    # the referenced section without recreating identities or changing comments.
    while True:
        old_count = len(members)
        for block in blocks:
            attrs = block.get('attrs', {})
            anchor = attrs.get('anchor')
            anchor = anchor.get('target') if isinstance(anchor, dict) else anchor
            if (
                block.get('section_id') in members
                or attrs.get('section_id') in members
                or isinstance(anchor, str)
                and anchor in members
            ):
                members.add(block['id'])
        if len(members) == old_count:
            break
    after = op.get('after')
    if after in members:
        raise ByaxError('conflict', 'A section cannot be moved inside itself.', target=after)
    moving = [b for b in blocks if b['id'] in members]
    for block in moving:
        _target(doc, {'target': block['id']})
    retained = [b for b in blocks if b['id'] not in members]
    if after is None:
        insertion = len(retained)
    else:
        destination = next((i for i, b in enumerate(retained) if b['id'] == after), None)
        if destination is None:
            raise ByaxError('missing_target', 'Section destination does not exist.', target=after)
        insertion = (
            _section_end(retained, destination)
            if retained[destination]['type'] == 'heading'
            else destination + 1
        )
    doc['blocks'] = retained[:insertion] + moving + retained[insertion:]
    return [b['id'] for b in moving]


def _op_text_edit(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _target(doc, op)
    if 'text' not in obj or obj.get('type') in ('table', 'image', 'page_break', 'section_break', 'toc'):
        _error('Target is not editable text.', 'target')
    original = obj['text']
    if kind == 'replace_text':
        match = op['match']
        if not match:
            _error('Match must not be empty.', 'match')
        locations = [m.start() for m in re.finditer(re.escape(match), original)]
        if not locations:
            raise ByaxError('precondition_failed', 'Quoted text is absent from target.', target=obj['id'])
        if len(locations) > 1 and 'occurrence' not in op:
            raise ByaxError(
                'ambiguous_selection',
                'Quoted text has multiple matches; supply a one-based occurrence.',
                target=obj['id'],
                candidates=[
                    {'occurrence': i + 1, 'start': p, 'end': p + len(match)} for i, p in enumerate(locations)
                ],
            )
        occurrence = op.get('occurrence', 1)
        if not 1 <= occurrence <= len(locations):
            _error('Occurrence is outside the match list.', 'occurrence')
        start, replacement = locations[occurrence - 1], op['replacement']
        end = start + len(match)
    elif kind == 'insert_text':
        start = end = op['offset']
        replacement = op['text']
    elif kind == 'delete_text':
        start, end, replacement = op['start'], op['end'], ''
    else:
        start, end, replacement = 0, len(original), op['text']
    _boundaries(original, start, end)
    if 'expected_text' in op and original[start:end] != op['expected_text']:
        raise ByaxError(
            'precondition_failed',
            'Expected text does not match selected text.',
            target=obj['id'],
            actual=original[start:end],
        )
    before_marks = copy.deepcopy(obj.get('marks', []))
    _splice(obj, start, end, replacement)
    _adjust_anchors(doc, obj['id'], start, end, len(replacement))
    changed.append(obj['id'])
    if doc['settings'].get('track_changes') and original != obj['text']:
        doc['review']['tracked_changes'].append(
            {
                'id': new_id('change'),
                'target': obj['id'],
                'before': original,
                'after': obj['text'],
                'before_marks': before_marks,
                'after_marks': copy.deepcopy(obj.get('marks', [])),
                'status': 'pending',
                'base_revision': base_revision,
            }
        )


def _op_insert_paragraph(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _paragraph(op['text'], op.get('style', 'body'), op.get('id'))
    if obj['style'] not in doc['styles']:
        _error('Unknown named style.', 'style')
    _insert(doc, obj, op.get('after'))
    changed.append(obj['id'])


def _op_delete_or_move_block(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _target(doc, op)
    if obj not in doc['blocks']:
        _error('Target must be a top-level block.', 'target')
    if op.get('after') == obj['id']:
        _error('Block cannot be moved after itself.', 'after')
    doc['blocks'].remove(obj)
    if kind == 'move_block':
        _insert(doc, obj, op.get('after'))
    else:
        removed = (
            {obj['id']}
            | {r['id'] for r in obj.get('rows', [])}
            | {c['id'] for r in obj.get('rows', []) for c in r['cells']}
        )
        _orphan_review(doc, removed)
    changed.append(obj['id'])


def _op_move_section(doc, op, base_revision, kind, changed, allow_policy_changes):
    changed.extend(_move_section(doc, op))


def _op_split_block(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _target(doc, op)
    if obj.get('type') not in ('paragraph', 'heading', 'footnote'):
        _error('Only text blocks can be split.', 'target')
    offset = op['offset']
    _boundaries(obj['text'], offset, offset)
    new = copy.deepcopy(obj)
    new['id'] = op.get('id') or new_id('p')
    new['text'], obj['text'] = obj['text'][offset:], obj['text'][:offset]
    new['marks'] = [
        dict(m, start=max(0, m['start'] - offset), end=m['end'] - offset)
        for m in new['marks']
        if m['end'] > offset
    ]
    obj['marks'] = [dict(m, end=min(m['end'], offset)) for m in obj['marks'] if m['start'] < offset]
    _insert(doc, new, obj['id'])
    for bookmark in doc['settings'].get('bookmarks', {}).values():
        if bookmark.get('target') == obj['id'] and bookmark.get('offset', 0) > offset:
            bookmark['target'], bookmark['offset'] = new['id'], bookmark['offset'] - offset
    for comment in doc['review']['comments']:
        if comment.get('target') == obj['id'] and comment.get('start', -1) >= offset:
            comment['target'], comment['start'], comment['end'] = (
                new['id'],
                comment['start'] - offset,
                comment['end'] - offset,
            )
        elif comment.get('target') == obj['id'] and comment.get('end', 0) > offset:
            comment['end'], comment['anchor_status'] = offset, 'needs_review'
    changed.extend((obj['id'], new['id']))


def _op_join_blocks(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _target(doc, op)
    other = _target(doc, {'target': op['next']})
    if (
        obj.get('type') not in ('paragraph', 'heading', 'footnote')
        or other.get('type') not in ('paragraph', 'heading', 'footnote')
        or doc['blocks'].index(other) != doc['blocks'].index(obj) + 1
    ):
        _error('Join requires consecutive text blocks.', 'next')
    offset = len(obj['text'])
    obj['text'] += other['text']
    obj['marks'].extend(dict(m, start=m['start'] + offset, end=m['end'] + offset) for m in other['marks'])
    doc['blocks'].remove(other)
    for bookmark in doc['settings'].get('bookmarks', {}).values():
        if bookmark.get('target') == other['id']:
            bookmark['target'], bookmark['offset'] = obj['id'], bookmark.get('offset', 0) + offset
    for item in doc['review']['comments']:
        if item.get('target') == other['id']:
            item['target'] = obj['id']
            if item.get('start') is not None:
                item['start'] += offset
                item['end'] += offset
    changed.extend((obj['id'], other['id']))


def _op_apply_style(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _target(doc, op)
    before_format = _format_state(obj)
    if op['style'] not in doc['styles']:
        _error('Unknown named style.', 'style')
    obj['style'] = op['style']
    _track_format(doc, obj, before_format, base_revision)
    changed.append(obj['id'])


def _op_format_text(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _target(doc, op)
    before_format = _format_state(obj)
    _boundaries(obj.get('text', ''), op['start'], op['end'])
    marks = op['marks']
    if isinstance(marks, dict):
        marks = [{'type': k, 'value': v} for k, v in marks.items()]
    if not isinstance(marks, list) or any(
        not isinstance(m, dict) or set(m) - {'type', 'value'} or not isinstance(m.get('type'), str)
        for m in marks
    ):
        _error('Marks must map mark types to values or be an array of {type,value}.', 'marks')
    for mark in marks:
        # Split existing marks of this type around the formatted range.
        kept = []
        for old in obj.get('marks', []):
            if old['type'] != mark['type'] or old['end'] <= op['start'] or old['start'] >= op['end']:
                kept.append(old)
            else:
                if old['start'] < op['start']:
                    kept.append(dict(old, end=op['start']))
                if old['end'] > op['end']:
                    kept.append(dict(old, start=op['end']))
        obj['marks'] = kept
        if mark.get('value') is not False and mark.get('value') is not None and op['end'] > op['start']:
            obj['marks'].append({'start': op['start'], 'end': op['end'], **mark})
    _track_format(doc, obj, before_format, base_revision)
    changed.append(obj['id'])


def _op_fill_fields(doc, op, base_revision, kind, changed, allow_policy_changes):
    for name, supplied in op['values'].items():
        old = doc['fields'].get(name)
        doc['fields'][name] = (
            {**copy.deepcopy(old), 'value': supplied}
            if isinstance(old, dict) and not isinstance(supplied, dict)
            else copy.deepcopy(supplied)
        )
    changed.extend(_fill_fields(doc, set(op['values']), base_revision))
    changed.append('fields')


def _op_set_properties(doc, op, base_revision, kind, changed, allow_policy_changes):
    key = {'set_metadata': 'metadata', 'set_layout': 'layout', 'set_settings': 'settings'}[kind]
    if kind == 'set_settings':
        forbidden = set(op['values']) & {'protections', 'requirements'}
        if forbidden or (doc['settings'].get('track_changes') and op['values'].get('track_changes') is False):
            raise ByaxError(
                'protected_edit',
                'Use explicit policy operations; disabling protection or tracking requires local confirmation.',
                fields=sorted(forbidden),
            )
    if kind == 'set_layout' and 'margins' in op['values']:
        if not isinstance(op['values']['margins'], dict):
            _error('Margins must be an object.', 'values.margins')
        doc[key]['margins'].update(op['values']['margins'])
        doc[key].update({k: v for k, v in op['values'].items() if k != 'margins'})
    else:
        doc[key].update(op['values'])
    changed.append(key)


def _op_define_style(doc, op, base_revision, kind, changed, allow_policy_changes):
    doc['styles'][op['name']] = copy.deepcopy(op['values'])
    changed.append('styles')


def _op_insert_table(doc, op, base_revision, kind, changed, allow_policy_changes):
    if not 1 <= op['rows'] <= 10000 or not 1 <= op['columns'] <= 1000 or op['rows'] * op['columns'] > 100000:
        _error('Table dimensions exceed limits.', 'rows')
    obj = {
        'id': new_id('table'),
        'type': 'table',
        'text': '',
        'style': 'body',
        'marks': [],
        'attrs': {'header_rows': 1},
        'rows': [_row(op['columns']) for _ in range(op['rows'])],
    }
    _insert(doc, obj, op.get('after'))
    changed.append(obj['id'])


def _op_table_dimension(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _target(doc, op)
    if obj.get('type') != 'table':
        _error('Target must be a table.', 'target')
    rows, columns = obj['rows'], len(obj['rows'][0]['cells'])
    size = len(rows) if kind.endswith('row') else columns
    index = op.get('index', size)
    adding = kind.startswith('add')
    if (index < 0 or index > size) if adding else (index < 0 or index >= size):
        _error('Table index is out of bounds.', 'index')
    if not adding and size == 1:
        _error('Cannot remove the final table row or column.', 'index')
    if adding:
        new_rows, new_columns = (len(rows) + 1, columns) if kind == 'add_row' else (len(rows), columns + 1)
        if new_rows > 10000 or new_columns > 1000 or new_rows * new_columns > 100000:
            raise ByaxError('limit_exceeded', 'Table dimensions exceed limits.')
        if 'values' in op and (
            len(op['values']) != (columns if kind == 'add_row' else len(rows))
            or any(not isinstance(v, str) for v in op['values'])
        ):
            _error('Values must contain one string for every cell in the new row or column.', 'values')
    if kind == 'add_row':
        row = _row(columns)
        for cell, value in zip(row['cells'], op.get('values', [])):
            cell['text'] = value
        rows.insert(index, row)
    elif kind == 'remove_row':
        if _is_locked(rows[index]) or any(_is_locked(c) for c in rows[index]['cells']):
            raise ByaxError('protected_edit', 'Row contains locked content.', target=rows[index]['id'])
        removed_row = rows.pop(index)
        removed_ids = {removed_row['id']} | {c['id'] for c in removed_row['cells']}
        _orphan_review(doc, removed_ids)
    else:
        if not adding and any(_is_locked(row['cells'][index]) for row in rows):
            raise ByaxError('protected_edit', 'Column contains locked content.', target=obj['id'])
        widths = obj.get('attrs', {}).get('column_widths')
        if isinstance(widths, list) and len(widths) == columns:
            if adding:
                # A new column takes the mean width so existing columns keep their widths.
                widths.insert(index, sum(widths) / len(widths))
            else:
                widths.pop(index)
        for ri, row in enumerate(rows):
            if adding:
                row['cells'].insert(index, _cell(op['values'][ri] if 'values' in op else ''))
            else:
                removed_cell = row['cells'].pop(index)
                _orphan_review(doc, {removed_cell['id']})
    changed.append(obj['id'])


def _op_table_reorder(doc, op, base_revision, kind, changed, allow_policy_changes):
    table = _target(doc, op)
    if table.get('type') != 'table':
        _error('Reorder target must be a table.', 'target')
    size = len(table['rows']) if kind == 'reorder_row' else len(table['rows'][0]['cells'])
    source, destination = op['index'], op['to_index']
    if not 0 <= source < size or not 0 <= destination < size:
        _error('Source and destination indices must identify existing rows or columns.', 'index')
    if any(
        c.get('attrs', {}).get('rowspan', 1) > 1 or c.get('attrs', {}).get('colspan', 1) > 1
        for r in table['rows']
        for c in r['cells']
    ):
        raise ByaxError(
            'unsupported_feature',
            'Reordering merged table cells requires an explicit unmerge first.',
            target=table['id'],
        )
    if kind == 'reorder_row':
        moved = table['rows'].pop(source)
        table['rows'].insert(destination, moved)
        changed.extend([moved['id']] + [c['id'] for c in moved['cells']])
    else:
        for row in table['rows']:
            cell = row['cells'].pop(source)
            row['cells'].insert(destination, cell)
            changed.append(cell['id'])
        widths = table.get('attrs', {}).get('column_widths')
        if isinstance(widths, list) and len(widths) == size:
            widths.insert(destination, widths.pop(source))
    changed.append(table['id'])


def _op_add_comment(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = find_object(doc, op['target'])
    if 'start' in op or 'end' in op:
        if not {'start', 'end'} <= op.keys():
            _error('Comment ranges require both start and end.', 'range')
        _boundaries(obj.get('text', ''), op['start'], op['end'])
    comment = {k: v for k, v in op.items() if k not in ('type', 'op')}
    comment.update(id=new_id('comment'), status='open', revision=base_revision)
    doc['review']['comments'].append(comment)
    changed.append(comment['id'])


def _op_reply_comment(doc, op, base_revision, kind, changed, allow_policy_changes):
    parent = _find_review(doc, 'comments', op['target'])
    reply = {
        'id': new_id('comment'),
        'parent_id': parent['id'],
        'target': parent['target'],
        'text': op['text'],
        'author': op.get('author', ''),
        'status': 'orphaned' if parent.get('status') == 'orphaned' else 'open',
        'revision': base_revision,
        'replies': [],
    }
    parent.setdefault('replies', []).append(reply)
    changed.extend((parent['id'], reply['id']))


def _op_resolve_comment(doc, op, base_revision, kind, changed, allow_policy_changes):
    _find_review(doc, 'comments', op['target'])['status'] = 'resolved'
    changed.append(op['target'])


def _op_document_record(doc, op, base_revision, kind, changed, allow_policy_changes):
    collection = (
        doc['references']
        if kind == 'add_reference'
        else doc['profiles']
        if kind == 'set_profile'
        else doc['settings'].setdefault('requirements', {})
    )
    if kind == 'set_profile':
        from .profiles import validate_profile

        collection[op['id']] = validate_profile(op['values'])
    else:
        collection[op['id']] = copy.deepcopy(op['values'])
    changed.append(op['id'])


def _op_add_layer(doc, op, base_revision, kind, changed, allow_policy_changes):
    layer = {'id': new_id('layer'), 'name': 'Layer', 'visible': True, 'locked': False, **op['values']}
    doc['layers'].append(layer)
    changed.append(layer['id'])


def _op_layer_change(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = (
        find_object(doc, op['target'])
        if kind == 'update_layer' and op['values'] == {'locked': False}
        else _target(doc, op)
    )
    if obj not in doc['layers']:
        _error('Target must be a layer.', 'target')
    if kind == 'delete_layer':
        doc['layers'].remove(obj)
    else:
        if 'id' in op['values']:
            _error('Stable object identities cannot be changed.', 'values.id')
        obj.update(op['values'])
    changed.append(obj['id'])


def _op_add_asset(doc, op, base_revision, kind, changed, allow_policy_changes):
    try:
        data = base64.b64decode(op['data'], validate=True)
    except ValueError as exc:
        raise ByaxError('schema_error', 'Asset must be base64 encoded.', field='data') from exc
    if len(data) > 64 * 1024 * 1024:
        raise ByaxError('limit_exceeded', 'Asset exceeds 64 MiB.')
    digest = hashlib.sha256(data).hexdigest()
    doc['assets'][digest] = {'data': op['data'], 'mime': op['mime'], 'name': op.get('name', '')}
    changed.append(digest)


def _op_place_image(doc, op, base_revision, kind, changed, allow_policy_changes):
    if op['asset'] not in doc['assets']:
        _error('Image asset has not been added to the document.', 'asset')
    obj = {
        'id': new_id('image'),
        'type': 'image',
        'text': '',
        'style': 'body',
        'marks': [],
        'attrs': {k: op[k] for k in ('asset', 'alt', 'width', 'height') if k in op},
    }
    _insert(doc, obj, op.get('after'))
    changed.append(obj['id'])


def _op_set_attributes(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = _target(doc, op)
    before_format = _format_state(obj)
    obj.setdefault('attrs', {}).update(op['values'])
    if obj.get('type') in ('paragraph', 'heading') and 'level' in op['values']:
        obj['type'] = 'heading' if op['values']['level'] else 'paragraph'
    _track_format(doc, obj, before_format, base_revision)
    changed.append(obj['id'])


def _op_protect(doc, op, base_revision, kind, changed, allow_policy_changes):
    find_object(doc, op['target'])
    rule = {'target': op['target'], 'kind': op['kind']}
    _measure(doc, rule)
    rules = doc['settings'].setdefault('protections', [])
    if rule not in rules:
        rules.append(rule)
    changed.append(op['target'])


def _op_policy_change(doc, op, base_revision, kind, changed, allow_policy_changes):
    if not allow_policy_changes:
        raise ByaxError('protected_edit', 'This policy change requires confirmation in the local desktop.')
    if kind == 'unprotect':
        rule = {'target': op['target'], 'kind': op['kind']}
        rules = doc['settings'].get('protections', [])
        if rule not in rules:
            raise ByaxError('missing_target', 'Protection rule does not exist.', target=op['target'])
        rules.remove(rule)
        changed.append(op['target'])
    else:
        doc['settings']['track_changes'] = op['enabled']
        changed.append('settings')


def _op_propose(doc, op, base_revision, kind, changed, allow_policy_changes):
    values = copy.deepcopy(op['values'])
    if not isinstance(values.get('operations'), list) or not values['operations']:
        _error('Proposal requires exact operations.', 'values.operations')
    for i, operation in enumerate(values['operations']):
        proposed_kind = _validate_op(operation, i)
        if proposed_kind in ('propose', 'accept_proposal', 'reject_proposal'):
            _error('Proposals cannot contain review-management operations.', 'values.operations')
    if values.get('expected_revision', base_revision) != base_revision:
        raise ByaxError(
            'stale_revision',
            'Proposal expected revision does not match its starting document.',
            expected=values['expected_revision'],
            actual=base_revision,
        )
    target_ids = {o[k] for o in values['operations'] for k in ('target', 'after', 'next') if o.get(k)}
    proposal = {
        **values,
        'id': new_id('proposal'),
        'status': 'pending',
        'expected_revision': values.get('expected_revision', base_revision),
        'preconditions': {target: object_hash(find_object(doc, target)) for target in target_ids},
    }
    doc['review']['proposals'].append(proposal)
    changed.append(proposal['id'])


def _op_resolve_proposal(doc, op, base_revision, kind, changed, allow_policy_changes):
    proposal = _find_review(doc, 'proposals', op['target'])
    if proposal['status'] != 'pending':
        raise ByaxError('conflict', 'Proposal is not pending.', target=op['target'])
    if kind == 'accept_proposal':
        proposal_before = _candidate(doc)
        for target, digest in proposal.get('preconditions', {}).items():
            if object_hash(find_object(doc, target)) != digest:
                raise ByaxError(
                    'stale_revision', 'Proposal target changed since it was reviewed.', target=target
                )
        for operation in proposal['operations']:
            changed.extend(_operation(doc, operation, base_revision))
        _preserve(proposal_before, doc, proposal.get('protect', []))
        required = proposal.get('required_suites', [])
        if required:
            frozen = _candidate(doc)
            frozen['settings']['requirements'] = copy.deepcopy(
                proposal_before['settings'].get('requirements', {})
            )
            frozen['profiles'] = copy.deepcopy(proposal_before['profiles'])
            report = run_checks(frozen, required)
            if report['validation'] != 'passed':
                raise ByaxError(
                    'failed_requirement' if report['validation'] == 'failed' else 'incomplete_coverage',
                    'Proposal required policy was not satisfied.',
                    findings=report['findings'],
                    coverage=report['coverage'],
                )
        proposal['status'] = 'accepted'
    else:
        proposal['status'] = 'rejected'
    changed.append(proposal['id'])


def _op_resolve_change(doc, op, base_revision, kind, changed, allow_policy_changes):
    change = _find_review(doc, 'tracked_changes', op['target'])
    if change['status'] != 'pending':
        raise ByaxError('conflict', 'Tracked change is not pending.', target=op['target'])
    if kind == 'reject_change':
        obj = _target(doc, {'target': change['target']})
        if change.get('kind') == 'formatting':
            if _format_state(obj) != change['after']:
                raise ByaxError(
                    'conflict',
                    'Formatting has changed again; rejection needs reconciliation.',
                    target=obj['id'],
                )
            obj.update(copy.deepcopy(change['before']))
        else:
            if obj['text'] != change['after']:
                raise ByaxError(
                    'conflict',
                    'Tracked text has changed again; rejection needs reconciliation.',
                    target=obj['id'],
                )
            _splice(obj, 0, len(obj['text']), change['before'])
            obj['marks'] = copy.deepcopy(change.get('before_marks', []))
            _adjust_anchors(doc, obj['id'], 0, len(change['after']), len(change['before']))
        changed.append(obj['id'])
    change['status'] = 'accepted' if kind == 'accept_change' else 'rejected'
    changed.append(change['id'])


def _op_set_bookmark(doc, op, base_revision, kind, changed, allow_policy_changes):
    obj = find_object(doc, op['target'])
    if 'offset' in op:
        _boundaries(obj.get('text', ''), op['offset'], op['offset'])
    doc['settings'].setdefault('bookmarks', {})[op['name']] = {
        'target': op['target'],
        'offset': op.get('offset', 0),
    }
    changed.append(op['target'])


def _op_remove_bookmark(doc, op, base_revision, kind, changed, allow_policy_changes):
    if op['name'] not in doc['settings'].get('bookmarks', {}):
        raise ByaxError('missing_target', 'Bookmark does not exist.', target=op['name'])
    del doc['settings']['bookmarks'][op['name']]
    changed.append('bookmarks')


def _op_replace_content(doc, op, base_revision, kind, changed, allow_policy_changes):
    if not isinstance(op['blocks'], list) or any(not isinstance(b, dict) for b in op['blocks']):
        _error('Replacement blocks must be an array of block objects.', 'blocks')
    for key in ('assets', 'import_provenance'):
        if key in op and not isinstance(op[key], dict):
            _error('Expected an object.', key)
    old_ids = {b['id'] for b in doc['blocks']} | {
        c['id'] for b in doc['blocks'] for r in b.get('rows', []) for c in r['cells']
    }
    incoming = {b.get('id'): b for b in op['blocks']}
    for block in doc['blocks']:
        locked = _is_locked(block) or any(
            _is_locked(r) or any(_is_locked(c) for c in r['cells']) for r in block.get('rows', [])
        )
        if locked and incoming.get(block['id']) != block:
            raise ByaxError(
                'protected_edit',
                'Replacement content must keep locked blocks unchanged.',
                target=block['id'],
            )
    doc['blocks'] = copy.deepcopy(op['blocks'])
    doc['assets'].update(copy.deepcopy(op.get('assets', {})))
    if 'import_provenance' in op:
        doc['metadata']['import'] = copy.deepcopy(op['import_provenance'])
    new_ids = {b.get('id') for b in doc['blocks']} | {
        c['id'] for b in doc['blocks'] for r in b.get('rows', []) for c in r['cells']
    }
    removed = old_ids - new_ids
    _orphan_review(doc, removed)
    changed.extend(sorted(old_ids | {x for x in new_ids if x}))


def _op_compact_history(doc, op, base_revision, kind, changed, allow_policy_changes):
    _compact_history(doc, op['keep_last'])
    changed.append('history')


_HANDLERS = {
    'insert_text': _op_text_edit,
    'delete_text': _op_text_edit,
    'replace_text': _op_text_edit,
    'set_text': _op_text_edit,
    'update_cell': _op_text_edit,
    'insert_paragraph': _op_insert_paragraph,
    'delete_block': _op_delete_or_move_block,
    'move_block': _op_delete_or_move_block,
    'move_section': _op_move_section,
    'split_block': _op_split_block,
    'join_blocks': _op_join_blocks,
    'apply_style': _op_apply_style,
    'format_text': _op_format_text,
    'fill_fields': _op_fill_fields,
    'set_metadata': _op_set_properties,
    'set_layout': _op_set_properties,
    'set_settings': _op_set_properties,
    'define_style': _op_define_style,
    'insert_table': _op_insert_table,
    'add_row': _op_table_dimension,
    'remove_row': _op_table_dimension,
    'add_column': _op_table_dimension,
    'remove_column': _op_table_dimension,
    'reorder_row': _op_table_reorder,
    'reorder_column': _op_table_reorder,
    'add_comment': _op_add_comment,
    'reply_comment': _op_reply_comment,
    'resolve_comment': _op_resolve_comment,
    'add_reference': _op_document_record,
    'set_profile': _op_document_record,
    'set_requirement': _op_document_record,
    'add_layer': _op_add_layer,
    'update_layer': _op_layer_change,
    'delete_layer': _op_layer_change,
    'add_asset': _op_add_asset,
    'place_image': _op_place_image,
    'set_attributes': _op_set_attributes,
    'protect': _op_protect,
    'unprotect': _op_policy_change,
    'set_tracking': _op_policy_change,
    'propose': _op_propose,
    'accept_proposal': _op_resolve_proposal,
    'reject_proposal': _op_resolve_proposal,
    'accept_change': _op_resolve_change,
    'reject_change': _op_resolve_change,
    'set_bookmark': _op_set_bookmark,
    'remove_bookmark': _op_remove_bookmark,
    'replace_content': _op_replace_content,
    'compact_history': _op_compact_history,
}


def _operation(doc, op, base_revision, *, allow_policy_changes=False):
    kind = op.get('type', op.get('op'))
    changed = []
    handler = _HANDLERS.get(kind)
    if handler is not None:
        handler(doc, op, base_revision, kind, changed, allow_policy_changes)
    return changed


def _measure(doc, rule):
    if not isinstance(rule, dict) or set(rule) != {'target', 'kind'}:
        _error('Protection requires exactly target and kind.', 'protect')
    obj = find_object(doc, rule['target'])
    kind = rule['kind']
    if kind in ('text', 'wording'):
        return obj.get('text', '')
    if kind in ('values', 'table_values'):
        if obj.get('type') != 'table':
            _error('Values protection requires a table.', 'protect.kind')
        return [[c['text'] for c in r['cells']] for r in obj['rows']]
    if kind in ('attributes', 'object_attributes'):
        return obj.get('attrs', {})
    if kind in ('style', 'styles', 'formatting'):
        definitions = {}
        name = obj.get('style')
        while name and name not in definitions:
            definitions[name] = doc['styles'].get(name)
            name = (definitions[name] or {}).get('based_on')
        return {'style': obj.get('style'), 'marks': obj.get('marks'), 'definitions': definitions}
    if kind in ('citations', 'references'):
        marks = [m for m in obj.get('marks', []) if m['type'] in ('citation', 'reference')]
        return {
            'citations': [
                {'value': m.get('value'), 'text': obj.get('text', '')[m['start'] : m['end']]} for m in marks
            ],
            'references': {m['value']: doc['references'].get(m['value']) for m in marks},
        }
    if kind in ('all', 'object'):
        return obj
    if kind in ('visual', 'visual_region'):
        raise ByaxError(
            'incomplete_coverage',
            'Visual preservation requires rendered comparison and cannot be guaranteed by semantic edits.',
            target=rule['target'],
        )
    _error('Unknown protection kind.', 'protect.kind')


def _preserve(before, after, rules, *, allow_policy_changes=False):
    if not allow_policy_changes:
        old_rules = before['settings'].get('protections', [])
        if any(rule not in after['settings'].get('protections', []) for rule in old_rules):
            raise ByaxError(
                'protected_edit', 'Retained protection rules cannot be removed without local confirmation.'
            )
        if before['settings'].get('track_changes') and not after['settings'].get('track_changes'):
            raise ByaxError('protected_edit', 'Tracking cannot be disabled without local confirmation.')
    for rule in rules:
        measured = _measure(before, rule)
        try:
            result = _measure(after, rule)
        except ByaxError as exc:
            if exc.code == 'missing_target':
                raise ByaxError(
                    'protected_edit', 'Protected target would be removed.', target=rule['target']
                ) from exc
            raise
        if measured != result:
            raise ByaxError(
                'protected_edit',
                'Edit violates a preservation rule.',
                target=rule['target'],
                kind=rule['kind'],
            )


def _result(doc, base, changed=(), execution='completed', report=None):
    report = report or {'validation': 'passed', 'findings': [], 'coverage': []}
    return {
        'schema_version': 1,
        'execution': execution,
        'validation': report['validation'],
        'review': 'required'
        if any(p.get('status') == 'pending' for p in doc['review']['proposals'])
        else 'not_required',
        'document_id': doc['id'],
        'base_revision': base,
        'revision': doc['revision'],
        'changed_ids': list(dict.fromkeys(changed)),
        'findings': report['findings'],
        'coverage': report['coverage'],
        'outputs': [],
    }


def _candidate(doc):
    # Historical records are immutable after creation; share their large payloads.
    result = copy.deepcopy({k: v for k, v in doc.items() if k != 'history'})
    result['history'] = {
        **doc['history'],
        'revisions': list(doc['history'].get('revisions', [])),
        'branches': dict(doc['history'].get('branches', {})),
    }
    return result


def _order_diff(old, new):
    # Splices turning the previous block order into the new one: [start, end, ids]
    # against the previous order, applied back to front.
    if old == new:
        return None
    start, limit = 0, min(len(old), len(new))
    while start < limit and old[start] == new[start]:
        start += 1
    end_old, end_new = len(old), len(new)
    while end_old > start and end_new > start and old[end_old - 1] == new[end_new - 1]:
        end_old, end_new = end_old - 1, end_new - 1
    if end_old - start <= 64 and end_new - start <= 64:
        return [[start, end_old, new[start:end_new]]]
    matcher = difflib.SequenceMatcher(None, old[start:end_old], new[start:end_new], autojunk=False)
    return [
        [start + i1, start + i2, new[start + j1 : start + j2]]
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != 'equal'
    ]


def _view(doc):
    return {k: v for k, v in doc.items() if k not in ('history', 'requests')}


def _delta(before, after):
    before, after = _view(before), _view(after)
    old, new = {b['id']: b for b in before['blocks']}, {b['id']: b for b in after['blocks']}
    delta = {
        'set': {k: copy.deepcopy(v) for k, v in after.items() if k != 'blocks' and before.get(k) != v},
        'delete': [k for k in before if k not in after],
        'blocks': {k: copy.deepcopy(v) for k, v in new.items() if old.get(k) != v},
    }
    order = _order_diff(list(old), list(new))
    if order is not None:
        delta['block_order_diff'] = order
    return delta


def _patch(state, delta):
    result = state
    result.update(copy.deepcopy(delta['set']))
    for key in delta['delete']:
        result.pop(key, None)
    blocks = {b['id']: b for b in result['blocks']}
    blocks.update(copy.deepcopy(delta['blocks']))
    if 'block_order' in delta:
        order = delta['block_order']
    else:
        order = [b['id'] for b in result['blocks']]
        for start, end, inserted in reversed(delta.get('block_order_diff', [])):
            order[start:end] = inserted
    result['blocks'] = [blocks[key] for key in order]
    return result


# Recently committed states keyed by revision. Committed documents are replaced,
# never mutated, so these read-only references let a commit diff against the
# previous record without replaying the delta chain.
_STATES = {}
_STATES_LOCK = threading.Lock()
_STATE_CACHE = 8
# A snapshot is taken once the deltas since the previous one add up to that
# snapshot's size, so history bytes per edit stay proportional to the edit.
_SNAPSHOT_MIN_BYTES = 16 * 1024
_SNAPSHOT_MAX_CHAIN = 400


def _remember(doc):
    with _STATES_LOCK:
        _STATES.pop(doc['revision'], None)
        _STATES[doc['revision']] = doc
        while len(_STATES) > _STATE_CACHE:
            _STATES.pop(next(iter(_STATES)))


def _cached(revision):
    with _STATES_LOCK:
        state = _STATES.get(revision)
    return state if state is not None and state.get('revision') == revision else None


def _record_bytes(record):
    if 'bytes' in record:
        return record['bytes']
    return len(canonical_json(record.get('snapshot', record.get('delta'))))


def _needs_snapshot(records):
    accumulated = 0
    for count, record in enumerate(reversed(records)):
        if 'snapshot' in record:
            return count >= _SNAPSHOT_MAX_CHAIN or accumulated >= max(
                _SNAPSHOT_MIN_BYTES, _record_bytes(record)
            )
        accumulated += _record_bytes(record)
    return True


def _compact_history(doc, keep_last):
    history = doc['history']
    records = history['revisions']
    protected = set(history.get('branches', {}).values())
    keep = {r['revision'] for r in records[-keep_last:]} if keep_last else set()
    keep.update(r['revision'] for r in records if r.get('name') or r['revision'] in protected)
    rebuilt, state, previous = [], None, None
    for record in records:
        state = copy.deepcopy(record['snapshot']) if 'snapshot' in record else _patch(state, record['delta'])
        if record['revision'] in keep:
            retained = {k: v for k, v in record.items() if k not in ('snapshot', 'delta')}
            if previous is None:
                retained['snapshot'] = copy.deepcopy(state)
            else:
                retained['delta'] = _delta(previous, state)
            rebuilt.append(retained)
            previous = copy.deepcopy(state)
    removed = [r['revision'] for r in records if r['revision'] not in keep]
    history['revisions'] = rebuilt
    history['last_compaction'] = {
        'removed_revisions': removed,
        'removed_assets': [],
        'recovery_removed': ['Restore and compare removed unnamed revisions'],
        'timestamp': datetime.now(UTC).isoformat(),
    }


def _commit(before, candidate, operations, request_id=None, origin='api', *, auto_compact_history=False):
    history = candidate['history']
    history.setdefault('revisions', [])
    history.setdefault('branches', {})
    history.setdefault('current_branch', 'main')
    # Snapshot only the current semantic state; snapshots never recursively include history.
    if not any(r['revision'] == before['revision'] for r in history['revisions']):
        record = {
            'revision': before['revision'],
            'timestamp': datetime.now(UTC).isoformat(),
            'origin': origin,
            'request_id': request_id,
            'operations': copy.deepcopy(operations),
        }
        if _needs_snapshot(history['revisions']):
            record['snapshot'] = snapshot(before)
            record['bytes'] = len(canonical_json(record['snapshot']))
        else:
            previous = _cached(history['revisions'][-1]['revision'])
            if previous is None:
                previous = _revision(before, history['revisions'][-1]['revision'])
            record['delta'] = _delta(previous, before)
            record['bytes'] = len(canonical_json(record['delta']))
        history['revisions'].append(record)
    _remember(before)
    candidate['revision'] = new_id('rev')
    history['branches'][history['current_branch']] = candidate['revision']
    limit = candidate['settings'].get('history_limit', 1000)
    if type(limit) is not int or limit < 1:
        _error('history_limit must be a positive integer.', 'settings.history_limit')
    if len(history['revisions']) > limit:
        if auto_compact_history:
            # Named checkpoints and all branch heads are retained even when
            # their count exceeds the desktop's soft history cap.
            _compact_history(candidate, max(1, limit // 2))
        else:
            raise ByaxError(
                'limit_exceeded',
                'History limit reached. Increase history_limit or explicitly compact a copy before further edits.',
                limit=limit,
            )
    for finding in candidate['review']['findings']:
        finding['status'] = 'stale'
    return candidate


def apply(doc, envelope, *, dry_run=False, allow_policy_changes=False, auto_compact_history=False):
    validate_document(doc, include_history=False)
    if not isinstance(envelope, dict):
        _error('Edit envelope must be an object.', '$')
    unknown = set(envelope) - {
        'schema_version',
        'request_id',
        'expected_revision',
        'operations',
        'protect',
        'required_suites',
        'origin',
    }
    if unknown:
        _error('Unknown edit envelope fields.', '$', unknown=sorted(unknown))
    if type(envelope.get('schema_version', 1)) is not int or envelope.get('schema_version', 1) != 1:
        _error('Unsupported operation schema version.', 'schema_version')
    for key in ('request_id', 'expected_revision', 'origin'):
        if key in envelope and (not isinstance(envelope[key], str) or not envelope[key]):
            _error('Expected a nonempty string.', key)
    operations = envelope.get('operations')
    if not isinstance(operations, list) or not operations:
        _error('At least one operation is required.', 'operations')
    if len(operations) > 10000:
        raise ByaxError('limit_exceeded', 'An edit batch may contain at most 10,000 operations.')
    rules = envelope.get('protect', [])
    if not isinstance(rules, list):
        _error('Protection rules must be an array.', 'protect')
    suites = envelope.get('required_suites', [])
    if not isinstance(suites, list) or any(not isinstance(s, str) for s in suites):
        _error('Required suites must be an array of names.', 'required_suites')
    request_id = envelope.get('request_id')
    fingerprint = object_hash(envelope)
    if request_id and request_id in doc['requests']:
        previous = doc['requests'][request_id]
        if previous['fingerprint'] != fingerprint:
            raise ByaxError(
                'conflict', 'Request identity was already used for different operations.', target=request_id
            )
        result = copy.deepcopy(previous['result'])
        result['execution'] = 'already_committed'
        result['current_revision'] = doc['revision']
        return copy.deepcopy(doc), result
    if 'expected_revision' in envelope and envelope['expected_revision'] != doc['revision']:
        raise ByaxError(
            'stale_revision',
            'Document revision changed.',
            expected=envelope['expected_revision'],
            actual=doc['revision'],
            retry='Inspect the current document and rebuild the edit.',
        )
    for i, operation in enumerate(operations):
        _validate_op(operation, i)
    candidate = _candidate(doc)
    changed = []
    for i, operation in enumerate(operations):
        try:
            changed.extend(
                _operation(candidate, operation, doc['revision'], allow_policy_changes=allow_policy_changes)
            )
        except ByaxError as exc:
            exc.details.setdefault('operation_index', i)
            raise
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ByaxError('schema_error', f'Invalid operation value: {exc}', operation_index=i) from exc
    validate_document(candidate, include_history=False)
    _preserve(
        doc,
        candidate,
        doc['settings'].get('protections', []) + rules,
        allow_policy_changes=allow_policy_changes,
    )
    structure = run_checks(candidate, ['structure'], limit=100)
    if structure['validation'] == 'failed':
        raise ByaxError(
            'schema_error', 'Candidate contains invalid structure.', findings=structure['findings']
        )
    report = structure
    if suites:
        frozen = copy.deepcopy(candidate)
        frozen['profiles'] = copy.deepcopy(doc['profiles'])
        frozen['settings']['requirements'] = copy.deepcopy(doc['settings'].get('requirements', {}))
        report = run_checks(frozen, suites, limit=100)
        if report['validation'] == 'failed':
            raise ByaxError(
                'failed_requirement',
                'Candidate failed a required check suite.',
                findings=report['findings'],
                coverage=report['coverage'],
            )
        if report['validation'] == 'incomplete':
            raise ByaxError(
                'incomplete_coverage',
                'A required suite could not be fully evaluated.',
                findings=report['findings'],
                coverage=report['coverage'],
            )
    if dry_run:
        result = _result(candidate, doc['revision'], changed, 'dry_run', report)
        result['changes'] = _compare(doc, candidate)
        result['layout_effects'] = {
            'status': 'unmeasured',
            'reason': 'Semantic dry run does not render candidate pages.',
        }
        return candidate, result
    if _view(candidate) == _view(doc) and candidate['history'] == doc['history']:
        result = _result(candidate, doc['revision'], [], 'unchanged', report)
    else:
        _commit(
            doc,
            candidate,
            operations,
            request_id,
            envelope.get('origin', 'api'),
            auto_compact_history=auto_compact_history,
        )
        result = _result(candidate, doc['revision'], changed, report=report)
    if request_id and envelope.get('origin') != 'gui':
        # Desktop commits are never retried by identity; other callers keep a
        # bounded idempotency window of the most recent request identities.
        requests = candidate['requests']
        requests.pop(request_id, None)
        requests[request_id] = {'fingerprint': fingerprint, 'result': copy.deepcopy(result)}
        for stale in list(requests)[: max(0, len(requests) - REQUEST_LIMIT)]:
            del requests[stale]
    return candidate, result


def _compare(before, after):
    old = {b['id']: b for b in before['blocks']}
    new = {b['id']: b for b in after['blocks']}
    return {
        'added': [new[k] for k in new.keys() - old.keys()],
        'removed': [old[k] for k in old.keys() - new.keys()],
        'modified': [
            {'id': k, 'before': old[k], 'after': new[k]} for k in old.keys() & new.keys() if old[k] != new[k]
        ],
        'order_changed': list(old) != list(new),
        'properties': {
            k: {'before': before.get(k), 'after': after.get(k)}
            for k in set(before) | set(after)
            if k not in ('blocks', 'revision', 'history', 'requests') and before.get(k) != after.get(k)
        },
    }


def _revision(doc, revision):
    if revision == doc['revision']:
        return snapshot(doc)
    records = doc['history']['revisions']
    target_index = next(
        (i for i, r in enumerate(records) if r['revision'] == revision or r.get('name') == revision), None
    )
    if target_index is not None:
        start = target_index
        while start >= 0 and 'snapshot' not in records[start] and _cached(records[start]['revision']) is None:
            start -= 1
        if start < 0:
            raise ByaxError('invalid_input', 'History delta has no reconstructable base.', target=revision)
        cached = _cached(records[start]['revision'])
        state = snapshot(cached) if cached is not None else copy.deepcopy(records[start]['snapshot'])
        for record in records[start + 1 : target_index + 1]:
            state = (
                copy.deepcopy(record['snapshot']) if 'snapshot' in record else _patch(state, record['delta'])
            )
        return state
    branch_revision = doc['history'].get('branches', {}).get(revision)
    if branch_revision and branch_revision != revision:
        return _revision(doc, branch_revision)
    raise ByaxError('missing_target', 'Revision or checkpoint is not in retained history.', target=revision)


def diff(doc, from_revision):
    before = _revision(doc, from_revision)
    return {
        'schema_version': 1,
        'document_id': doc['id'],
        'from_revision': before['revision'],
        'revision': doc['revision'],
        'changes': _compare(before, doc),
        'text_diff': '\n'.join(
            difflib.unified_diff(
                text_of(before).splitlines(),
                text_of(doc).splitlines(),
                fromfile=before['revision'],
                tofile=doc['revision'],
                lineterm='',
            )
        ),
    }


def restore(doc, revision, *, auto_compact_history=False):
    state = _revision(doc, revision)
    candidate = {
        **state,
        'history': _candidate({'history': doc['history']})['history'],
        'requests': copy.deepcopy(doc['requests']),
    }
    _preserve(doc, candidate, doc['settings'].get('protections', []))
    validate_document(candidate, include_history=False)
    _commit(
        doc, candidate, [{'type': 'restore', 'revision': revision}], auto_compact_history=auto_compact_history
    )
    return candidate, _result(candidate, doc['revision'], [b['id'] for b in candidate['blocks']])


def checkpoint(doc, name):
    if not isinstance(name, str) or not name.strip():
        _error('Checkpoint name cannot be empty.', 'name')
    if any(r.get('name') == name for r in doc['history']['revisions']):
        raise ByaxError('conflict', 'Checkpoint name already exists.', target=name)
    candidate = _candidate(doc)
    record = next((r for r in candidate['history']['revisions'] if r['revision'] == doc['revision']), None)
    if record is None:
        record = {
            'revision': doc['revision'],
            'snapshot': snapshot(doc),
            'timestamp': datetime.now(UTC).isoformat(),
            'origin': 'checkpoint',
            'operations': [],
        }
        candidate['history']['revisions'].append(record)
    if record.get('name'):
        raise ByaxError('conflict', 'This revision already has a checkpoint name.', target=record['name'])
    if record in doc['history']['revisions']:
        replacement = {k: v for k, v in record.items() if k != 'delta'}
        replacement.update(name=name, snapshot=snapshot(doc))
        candidate['history']['revisions'][candidate['history']['revisions'].index(record)] = replacement
    else:
        record['name'] = name
    return candidate, _result(candidate, doc['revision'])


def branch(doc, name):
    if not isinstance(name, str) or not name.strip():
        _error('Branch name cannot be empty.', 'name')
    if name in doc['history']['branches']:
        raise ByaxError('conflict', 'Branch already exists.', target=name)
    candidate = copy.deepcopy(doc)
    candidate['history']['branches'][name] = doc['revision']
    candidate['history']['current_branch'] = name
    return candidate, _result(candidate, doc['revision'])


def inspect(doc, section=None, target=None, limit=100):
    if type(limit) is not int or limit < 0:
        _error('Limit must be a nonnegative integer.', 'limit')
    if target:
        items = [copy.deepcopy(find_object(doc, target))]
    elif section == 'outline':
        items = [
            {'id': b['id'], 'text': b['text'], 'level': b.get('attrs', {}).get('level', 1)}
            for b in doc['blocks']
            if b['type'] == 'heading'
        ]
    elif section in ('comments', 'proposals', 'tracked_changes', 'findings'):
        items = copy.deepcopy(doc['review'][section])
    elif section in (
        'metadata',
        'styles',
        'layout',
        'assets',
        'references',
        'profiles',
        'fields',
        'layers',
        'history',
        'review',
        'settings',
    ):
        value = copy.deepcopy(doc[section])
        if section == 'assets':
            value = {k: {key: v for key, v in a.items() if key != 'data'} for k, a in value.items()}
        if section == 'history':
            value['revisions'] = [
                {k: v for k, v in r.items() if k not in ('snapshot', 'delta')} for r in value['revisions']
            ]
        items = value if isinstance(value, list) else [{'key': k, 'value': v} for k, v in value.items()]
    elif section and section not in ('document', 'blocks'):
        indices = [i for i, b in enumerate(doc['blocks']) if b['id'] == section]
        if not indices:
            raise ByaxError('missing_target', 'Section does not exist.', target=section)
        start = indices[0]
        first = doc['blocks'][start]
        stop = next(
            (
                i
                for i in range(start + 1, len(doc['blocks']))
                if doc['blocks'][i]['type'] == 'heading'
                and doc['blocks'][i].get('attrs', {}).get('level', 1)
                <= first.get('attrs', {}).get('level', 1)
            ),
            len(doc['blocks']),
        )
        items = copy.deepcopy(doc['blocks'][start:stop])
    else:
        items = copy.deepcopy(doc['blocks'])
    return {
        'schema_version': 1,
        'document_id': doc['id'],
        'revision': doc['revision'],
        'scope': target or section or 'document',
        'items': items[:limit],
        'total': len(items),
        'returned': min(limit, len(items)),
        'truncated': len(items) > limit,
        'continuation': {'increase_limit_to': len(items)} if len(items) > limit else None,
    }


def find(doc, query, case_sensitive=False, whole_word=False, regex=False):
    if not isinstance(query, str) or not query:
        _error('Search query must be nonempty text.', 'query')
    pattern = query if regex else re.escape(query)
    if whole_word:
        pattern = r'\b(?:' + pattern + r')\b'
    try:
        compiled = unicode_regex.compile(pattern, 0 if case_sensitive else unicode_regex.IGNORECASE)
    except unicode_regex.error as exc:
        raise ByaxError('schema_error', f'Invalid regular expression: {exc}', field='query') from exc
    matches = []
    for block in doc['blocks']:
        targets = [block] + [cell for row in block.get('rows', []) for cell in row['cells']]
        for obj in targets:
            try:
                for match in compiled.finditer(obj.get('text', ''), timeout=0.1):
                    matches.append(
                        {
                            'target': obj['id'],
                            'start': match.start(),
                            'end': match.end(),
                            'text': match.group(),
                            'block': block['id'],
                        }
                    )
                    if len(matches) > 100000:
                        raise ByaxError('limit_exceeded', 'Search exceeded 100,000 matches.')
            except TimeoutError as exc:
                raise ByaxError('limit_exceeded', 'Regular expression exceeded search time limit.') from exc
    return {
        'schema_version': 1,
        'revision': doc['revision'],
        'matches': matches,
        'total': len(matches),
        'offset_unit': 'unicode_scalar',
    }


def context(doc, target=None, limit=10000, profile=None):
    if type(limit) is not int or limit < 0:
        _error('Context limit must be a nonnegative integer.', 'limit')
    if profile and profile not in doc['profiles']:
        raise ByaxError('missing_target', 'Unknown profile.', target=profile)
    config = copy.deepcopy(doc['profiles'].get(profile, {}))
    if target:
        selected = find_object(doc, target)
        index = next(
            (
                i
                for i, b in enumerate(doc['blocks'])
                if b['id'] == target or any(c['id'] == target for r in b.get('rows', []) for c in r['cells'])
            ),
            None,
        )
        blocks = doc['blocks'][max(0, index - 1) : index + 2] if index is not None else [selected]
    else:
        blocks = doc['blocks']
    included, omitted, remaining = [], [], limit

    def include(value, kind, identity):
        nonlocal remaining
        length = len(canonical_json(value))
        if length <= remaining:
            remaining -= length
            return copy.deepcopy(value)
        omitted.append(
            {'id': identity, 'kind': kind, 'reason': 'Context payload limit exceeded.', 'characters': length}
        )
        return None

    # Policy and protection are required context and receive budget before text.
    selected_profile = include(config, 'profile', profile or 'profile')
    protections = include(doc['settings'].get('protections', []), 'protections', 'protections')
    requirements = include(doc['settings'].get('requirements', {}), 'requirements', 'requirements')
    for block in blocks:
        selected_block = include(block, 'block', block['id'])
        if selected_block is not None:
            included.append(selected_block)
    style_names = {b.get('style', 'body') for b in included}
    for name in list(style_names):
        parent = doc['styles'].get(name, {}).get('based_on')
        while parent and parent not in style_names:
            style_names.add(parent)
            parent = doc['styles'].get(parent, {}).get('based_on')
    styles = {}
    for name in sorted(style_names):
        selected_style = include(doc['styles'].get(name, {}), 'style', name)
        if selected_style is not None:
            styles[name] = selected_style
    terminology = include(doc['settings'].get('dictionary', []), 'terminology', 'terminology')
    authorized = config.get('references', [])
    references = {}
    for identity in authorized:
        if identity not in doc['references']:
            omitted.append({'id': identity, 'kind': 'reference', 'reason': 'Selected reference is missing.'})
            continue
        reference = include(doc['references'][identity], 'reference', identity)
        if reference is not None:
            references[identity] = reference
    findings = []
    for finding in doc['review']['findings']:
        if any(b['id'] in finding.get('affected_ids', []) for b in included):
            selected_finding = include(finding, 'finding', finding.get('id', 'finding'))
            if selected_finding is not None:
                findings.append(selected_finding)
    result = {
        'schema_version': 1,
        'document_id': doc['id'],
        'revision': doc['revision'],
        'profile': selected_profile or {},
        'blocks': included,
        'styles': styles,
        'terminology': terminology or [],
        'protections': protections or [],
        'requirements': requirements or {},
        'references': references,
        'findings': findings,
        'provenance': {
            'engine': 'byax/1',
            'retrieval': 'none',
            'source_versions': {k: doc['references'][k].get('version') for k in references},
        },
        'inclusion_report': {
            'included_ids': [b['id'] for b in included],
            'omitted': omitted,
            'unselected_references': [k for k in doc['references'] if k not in authorized],
        },
        'limit': {
            'unit': 'serialized_payload_characters',
            'maximum': limit,
            'used': limit - remaining,
            'excludes': 'Report framing, identities, provenance and omission diagnostics',
        },
        'execution': 'incomplete' if omitted else 'completed',
        'continuation': {'target_ids': [o['id'] for o in omitted]} if omitted else None,
    }
    from .storage import _check_secrets

    _check_secrets(result)
    return result


def revision_document(doc, revision):
    """Independent historical document suitable for layout, inspection or export."""
    state = _revision(doc, revision)
    state['history'] = {'revisions': [], 'branches': {}, 'current_branch': 'historical'}
    state['requests'] = {}
    return state
