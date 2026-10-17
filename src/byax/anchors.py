"""Review-anchor remapping and range-level tracked changes.

Comments (with threaded replies), bookmarks, proposal edit ranges, tracked
changes and provenance spans and claims (``byax.provenance``) follow text
through insertions, deletions, paragraph splits/joins and section moves. Each moved anchor is reported; anchors whose text disappears
become explicitly ``unresolved`` and deleted targets become ``orphaned``.
"""

import contextvars
import copy
import re
from typing import Any

import regex as unicode_regex

from .errors import ByaxError
from .model import new_id

_JOURNAL: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar('byax_journal', default=None)
NEEDS_REVIEW = {'overlapped', 'replaced'}


def journal():
    return _JOURNAL.get()


def note(kind, entry):
    current = _JOURNAL.get()
    if current is not None:
        current.setdefault(kind, []).append(entry)


def attribution():
    current = _JOURNAL.get() or {}
    return copy.deepcopy(current.get('attribution', {}))


def skipped_proposal():
    current = _JOURNAL.get() or {}
    return current.get('skip_proposal')


def transform_marks(marks, start, end, length):
    """Move marks through replacing [start, end) with ``length`` scalars."""
    delta = length - (end - start)
    result = []
    for mark in marks:
        mark = copy.deepcopy(mark)
        a, b = mark['start'], mark['end']
        if b <= start:
            pass
        elif a >= end:
            mark['start'], mark['end'] = a + delta, b + delta
        elif mark.get('type') == 'field':
            mark['start'] = min(a, start)
            mark['end'] = max(start + length, b + delta) if b > end else start + length
        elif a <= start and b >= end:
            # A mark covering the replaced range covers its replacement.
            mark['end'] = b + delta
        elif a >= start and b <= end:
            # Formatting wholly inside replaced text does not spread over new text.
            continue
        elif a < start:
            mark['end'] = start
        else:
            mark['start'], mark['end'] = start + length, b + delta
        if mark['end'] > mark['start'] or mark.get('type') == 'field':
            result.append(mark)
    return result


def map_range(a, b, start, end, length):
    """Map the anchor range [a, b) through a splice. Returns (start, end, effect)."""
    if a == b:
        p, effect = map_point(a, start, end, length)
        return p, p, effect
    delta = length - (end - start)
    if end <= a:
        return a + delta, b + delta, 'shifted' if delta else 'none'
    if start >= b:
        return a, b, 'none'
    if start <= a and end >= b:
        return start, start + length, 'deleted' if not length else 'replaced'
    if a <= start and end <= b:
        return a, b + delta, 'edited'
    if start < a:
        return start, b + delta, 'overlapped'
    return a, start + length, 'overlapped'


def map_point(p, start, end, length):
    delta = length - (end - start)
    if start == end:
        return (p + delta, 'shifted') if p > start else (p, 'none')
    if p <= start:
        return p, 'none'
    if p >= end:
        return p + delta, 'shifted' if delta else 'none'
    return start + length, 'deleted'


def comments(doc):
    pending = list(doc['review']['comments'])
    while pending:
        item = pending.pop(0)
        pending.extend(item.get('replies', []))
        yield item


def _sync_replies(comment):
    for reply in comment.get('replies', []):
        reply['target'] = comment.get('target')
        _sync_replies(reply)


def _location(item, start='start', end='end'):
    return {'target': item.get('target'), 'start': item.get(start), 'end': item.get(end)}


def _report(kind, identity, before, after, status):
    if before != after or status not in ('none', 'shifted', 'open'):
        note('anchor_mappings', {'kind': kind, 'id': identity, 'from': before, 'to': after, 'status': status})


def _comment_status(comment, effect):
    if effect == 'deleted':
        comment['anchor_status'] = 'unresolved'
    elif effect in NEEDS_REVIEW and comment.get('anchor_status') != 'unresolved':
        comment['anchor_status'] = 'needs_review'
    return comment.get('anchor_status', 'open')


def _edit_ranges(proposal):
    """(edit, operation) pairs for pending edits of a pending proposal."""
    if proposal.get('status') != 'pending' or proposal.get('id') == skipped_proposal():
        return
    operations = proposal.get('operations', [])
    for edit in proposal.get('edits', []):
        if edit.get('status', 'pending') == 'pending' and edit['index'] < len(operations):
            yield edit, operations[edit['index']]


def _set_operation_range(operation, anchor, text):
    kind = operation.get('type', operation.get('op'))
    operation['target'] = anchor['target']
    if 'offset' in operation and kind in ('insert_text', 'split_block', 'set_bookmark'):
        operation['offset'] = anchor['start']
    if kind in ('delete_text', 'format_text', 'add_comment') and 'start' in operation:
        operation['start'], operation['end'] = anchor['start'], anchor['end']
    if kind == 'replace_text' and text is not None:
        positions = [m.start() for m in re.finditer(re.escape(operation['match']), text)]
        if anchor['start'] in positions:
            occurrence = positions.index(anchor['start']) + 1
            if occurrence > 1 or 'occurrence' in operation or len(positions) > 1:
                operation['occurrence'] = occurrence


def _mark_unresolved(proposal, edit, reason):
    if edit.get('state') not in ('unresolved', 'orphaned'):
        edit['state'] = 'unresolved'
        edit['reason'] = reason
        proposal['anchor_status'] = 'needs_review'
        note(
            'anchor_mappings',
            {
                'kind': 'proposal_edit',
                'id': f'{proposal["id"]}/{edit["id"]}',
                'from': copy.deepcopy(edit.get('anchor')),
                'to': None,
                'status': 'unresolved',
            },
        )


def _move_edit(proposal, edit, operation, anchor, text):
    before = copy.deepcopy(edit['anchor'])
    edit['anchor'] = anchor
    # The target changed, so its recorded object hash no longer applies even when this range did not
    # move; acceptance then revalidates the exact anchored text instead.
    if edit.get('state') == 'exact' or 'state' not in edit:
        edit['state'] = 'remapped'
    if anchor != before:
        _set_operation_range(operation, anchor, text)
        proposal['anchor_status'] = proposal.get('anchor_status', 'remapped')
        note(
            'anchor_mappings',
            {
                'kind': 'proposal_edit',
                'id': f'{proposal["id"]}/{edit["id"]}',
                'from': before,
                'to': copy.deepcopy(anchor),
                'status': edit['state'],
            },
        )


def remap_splice(doc, obj, start, end, length, *, skip_change=None):
    """Remap every anchor on ``obj`` after [start, end) became ``length`` scalars."""
    target = obj['id']
    for name, bookmark in doc['settings'].get('bookmarks', {}).items():
        if bookmark.get('target') == target:
            before = bookmark.get('offset', 0)
            bookmark['offset'], effect = map_point(before, start, end, length)
            _report(
                'bookmark',
                name,
                {'target': target, 'offset': before},
                {'target': target, 'offset': bookmark['offset']},
                'displaced' if effect == 'deleted' else effect,
            )
    for comment in comments(doc):
        if comment.get('target') != target or comment.get('start') is None:
            continue
        before = _location(comment)
        comment['start'], comment['end'], effect = map_range(
            comment['start'], comment['end'], start, end, length
        )
        status = _comment_status(comment, effect) if effect in NEEDS_REVIEW | {'deleted'} else effect
        _report('comment', comment['id'], before, _location(comment), status)
    for proposal in doc['review']['proposals']:
        for edit, operation in list(_edit_ranges(proposal)):
            if operation.get('target') != target and operation.get('next') != target:
                continue
            anchor = edit.get('anchor')
            if not anchor or anchor['target'] != target or operation.get('next') == target:
                _mark_unresolved(proposal, edit, 'Target text changed after the proposal was made.')
                continue
            if anchor['start'] == anchor['end'] and start == end == anchor['start']:
                _mark_unresolved(proposal, edit, 'Another insertion occurred at the proposed position.')
                continue
            a, b, effect = map_range(anchor['start'], anchor['end'], start, end, length)
            if effect not in ('none', 'shifted'):
                _mark_unresolved(proposal, edit, 'The proposed range was edited.')
                continue
            _move_edit(proposal, edit, operation, {**anchor, 'start': a, 'end': b}, obj.get('text'))
    for change in doc['review']['tracked_changes']:
        if change.get('target') != target or change.get('status') != 'pending' or change['id'] == skip_change:
            continue
        if change.get('kind') == 'formatting' and 'added_marks' in change:
            for key in ('added_marks', 'removed_marks'):
                change[key] = transform_marks(change[key], start, end, length)
            continue
        if 'start' not in change:
            continue
        before = _location(change)
        if change['start'] == change['end']:
            a, effect = map_point(change['start'], start, end, length)
            if start == end == change['start']:
                effect = 'none'
            b = a
        else:
            a, b, effect = map_range(change['start'], change['end'], start, end, length)
        change['start'], change['end'] = a, b
        if effect == 'edited':
            change['inserted'] = obj['text'][a:b]
            change['anchor_status'] = 'edited'
        elif effect not in ('none', 'shifted'):
            change['anchor_status'] = 'unresolved'
        _report(
            'tracked_change', change['id'], before, _location(change), change.get('anchor_status', effect)
        )
    from . import provenance

    provenance.remap_splice(doc, obj, start, end, length)


def remap_split(doc, obj, new, offset):
    """Anchors at or after ``offset`` in ``obj`` move to the new block ``new``."""
    source, created = obj['id'], new['id']
    note(
        'identity_changes',
        {'operation': 'split_block', 'source': source, 'created': created, 'offset': offset},
    )
    for name, bookmark in doc['settings'].get('bookmarks', {}).items():
        if bookmark.get('target') == source and bookmark.get('offset', 0) > offset:
            before = {'target': source, 'offset': bookmark['offset']}
            bookmark['target'], bookmark['offset'] = created, bookmark['offset'] - offset
            _report('bookmark', name, before, {'target': created, 'offset': bookmark['offset']}, 'moved')
    for comment in comments(doc):
        if comment.get('target') != source or comment.get('parent_id'):
            continue
        before = _location(comment)
        if comment.get('start') is not None and comment['start'] >= offset and comment['end'] > offset:
            comment['target'], comment['start'], comment['end'] = (
                created,
                comment['start'] - offset,
                comment['end'] - offset,
            )
            status = 'moved'
        elif comment.get('end') is not None and comment['end'] > offset:
            comment['end'], comment['anchor_status'] = offset, 'needs_review'
            comment['continues_in'] = created
            status = 'needs_review'
        else:
            continue
        _sync_replies(comment)
        _report('comment', comment['id'], before, _location(comment), status)
    for proposal in doc['review']['proposals']:
        for edit, operation in list(_edit_ranges(proposal)):
            if source not in (operation.get('target'), operation.get('next')):
                continue
            anchor = edit.get('anchor')
            if not anchor or anchor['target'] != source or operation.get('next') == source:
                _mark_unresolved(proposal, edit, 'Target paragraph was split after the proposal was made.')
            elif anchor['end'] <= offset and anchor['start'] <= offset:
                continue
            elif anchor['start'] >= offset:
                moved = {
                    **anchor,
                    'target': created,
                    'start': anchor['start'] - offset,
                    'end': anchor['end'] - offset,
                }
                _move_edit(proposal, edit, operation, moved, new.get('text'))
            else:
                _mark_unresolved(proposal, edit, 'Paragraph split divides the proposed range.')
    additions = []
    for change in doc['review']['tracked_changes']:
        if change.get('target') != source or change.get('status') != 'pending':
            continue
        if change.get('kind') == 'formatting' and 'added_marks' in change:
            moved = {}
            for key in ('added_marks', 'removed_marks'):
                marks = change[key]
                change[key] = [dict(m, end=min(m['end'], offset)) for m in marks if m['start'] < offset]
                moved[key] = [
                    dict(m, start=max(0, m['start'] - offset), end=m['end'] - offset)
                    for m in marks
                    if m['end'] > offset
                ]
            if any(moved.values()) or change.get('attr_changes') or change.get('style'):
                change.setdefault('group', change['id'])
                additions.append(
                    {
                        **copy.deepcopy(change),
                        **moved,
                        'id': new_id('change'),
                        'target': created,
                        'split_from': change['id'],
                    }
                )
            continue
        if 'start' not in change:
            continue
        before = _location(change)
        if change['start'] >= offset and (change['end'] > offset or change['start'] > offset):
            change['target'], change['start'], change['end'] = (
                created,
                change['start'] - offset,
                change['end'] - offset,
            )
            _report('tracked_change', change['id'], before, _location(change), 'moved')
        elif change['start'] < offset < change['end']:
            change.setdefault('group', change['id'])
            tail = change['end'] - offset
            second = {
                **copy.deepcopy(change),
                'id': new_id('change'),
                'target': created,
                'start': 0,
                'end': tail,
                'inserted': change['inserted'][offset - change['start'] :],
                'deleted': '',
                'deleted_marks': [],
                'kind': 'insertion',
                'split_from': change['id'],
            }
            change['inserted'] = change['inserted'][: offset - change['start']]
            change['end'] = offset
            additions.append(second)
            _report('tracked_change', change['id'], before, _location(change), 'split')
            _report('tracked_change', second['id'], before, _location(second), 'split')
    doc['review']['tracked_changes'].extend(additions)
    from . import provenance

    provenance.remap_split(doc, obj, new, offset)


def remap_join(doc, obj, other, offset):
    """Anchors on ``other`` move into ``obj`` shifted by ``offset``."""
    target, removed = obj['id'], other['id']
    note(
        'identity_changes',
        {'operation': 'join_blocks', 'target': target, 'removed': removed, 'offset': offset},
    )
    for name, bookmark in doc['settings'].get('bookmarks', {}).items():
        if bookmark.get('target') == removed:
            before = {'target': removed, 'offset': bookmark.get('offset', 0)}
            bookmark['target'], bookmark['offset'] = target, bookmark.get('offset', 0) + offset
            _report('bookmark', name, before, {'target': target, 'offset': bookmark['offset']}, 'moved')
    for comment in comments(doc):
        if comment.get('target') != removed or comment.get('parent_id'):
            continue
        before = _location(comment)
        comment['target'] = target
        if comment.get('start') is not None:
            comment['start'] += offset
            comment['end'] += offset
        _sync_replies(comment)
        _report('comment', comment['id'], before, _location(comment), 'moved')
    for comment in comments(doc):
        if comment.get('continues_in') == removed and comment.get('target') == target:
            comment.pop('continues_in')
    for proposal in doc['review']['proposals']:
        for edit, operation in list(_edit_ranges(proposal)):
            if removed not in (operation.get('target'), operation.get('next'), operation.get('after')):
                continue
            anchor = edit.get('anchor')
            if (
                operation.get('target') == removed
                and anchor
                and anchor['target'] == removed
                and operation.get('next') is None
            ):
                moved = {
                    **anchor,
                    'target': target,
                    'start': anchor['start'] + offset,
                    'end': anchor['end'] + offset,
                }
                _move_edit(proposal, edit, operation, moved, obj.get('text'))
            else:
                _mark_unresolved(proposal, edit, 'A paragraph referenced by the proposal was joined.')
    for change in doc['review']['tracked_changes']:
        if change.get('target') != removed or change.get('status') != 'pending':
            continue
        before = _location(change)
        change['target'] = target
        if change.get('kind') == 'formatting' and 'added_marks' in change:
            for key in ('added_marks', 'removed_marks'):
                change[key] = [dict(m, start=m['start'] + offset, end=m['end'] + offset) for m in change[key]]
        elif 'start' in change:
            change['start'] += offset
            change['end'] += offset
        else:
            change['anchor_status'] = 'unresolved'
        _report('tracked_change', change['id'], before, _location(change), 'moved')
    from . import provenance

    provenance.remap_join(doc, obj, other, offset)


def report_moved(doc, ids):
    """Section and block moves keep identities; report the anchors that travelled."""
    ids = set(ids)
    for name, bookmark in doc['settings'].get('bookmarks', {}).items():
        if bookmark.get('target') in ids:
            note(
                'anchor_mappings',
                {
                    'kind': 'bookmark',
                    'id': name,
                    'from': copy.deepcopy(bookmark),
                    'to': copy.deepcopy(bookmark),
                    'status': 'moved_with_section',
                },
            )
    for comment in comments(doc):
        if comment.get('target') in ids:
            note(
                'anchor_mappings',
                {
                    'kind': 'comment',
                    'id': comment['id'],
                    'from': _location(comment),
                    'to': _location(comment),
                    'status': 'moved_with_section',
                },
            )
    for change in doc['review']['tracked_changes']:
        if change.get('target') in ids and change.get('status') == 'pending':
            note(
                'anchor_mappings',
                {
                    'kind': 'tracked_change',
                    'id': change['id'],
                    'from': _location(change),
                    'to': _location(change),
                    'status': 'moved_with_section',
                },
            )


def orphan(doc, removed_ids):
    for name, bookmark in doc['settings'].get('bookmarks', {}).items():
        if bookmark.get('target') in removed_ids:
            bookmark['status'] = 'orphaned'
            note(
                'anchor_mappings',
                {
                    'kind': 'bookmark',
                    'id': name,
                    'from': {'target': bookmark['target']},
                    'to': None,
                    'status': 'orphaned',
                },
            )
    for comment in comments(doc):
        if comment.get('target') in removed_ids and comment.get('status') != 'orphaned':
            comment['status'] = 'orphaned'
            note(
                'anchor_mappings',
                {
                    'kind': 'comment',
                    'id': comment['id'],
                    'from': _location(comment),
                    'to': None,
                    'status': 'orphaned',
                },
            )
    for collection in ('proposals', 'tracked_changes'):
        for item in doc['review'][collection]:
            targets = {item.get('target')} | {o.get('target') for o in item.get('operations', [])}
            if targets & removed_ids and item.get('status') != 'orphaned':
                item['status'] = 'orphaned'
                for edit in item.get('edits', []):
                    if edit.get('status', 'pending') == 'pending':
                        edit['state'] = 'orphaned'
                note(
                    'anchor_mappings',
                    {
                        'kind': collection[:-1],
                        'id': item['id'],
                        'from': {'target': item.get('target')},
                        'to': None,
                        'status': 'orphaned',
                    },
                )
    from . import provenance

    provenance.orphan(doc, removed_ids)


def _clusters(text):
    return unicode_regex.findall(r'\X', text)


def minimal_change(original, start, end, replacement):
    """Trim a splice to the grapheme-aligned region that actually differs."""
    removed, added = _clusters(original[start:end]), _clusters(replacement)
    prefix = 0
    while prefix < min(len(removed), len(added)) and removed[prefix] == added[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(removed), len(added)) - prefix
        and removed[len(removed) - 1 - suffix] == added[len(added) - 1 - suffix]
    ):
        suffix += 1
    lead = len(''.join(removed[:prefix]))
    deleted = ''.join(removed[prefix : len(removed) - suffix])
    inserted = ''.join(added[prefix : len(added) - suffix])
    return start + lead, deleted, inserted


def _clip_marks(marks, start, end):
    return [
        dict(m, start=max(m['start'], start) - start, end=min(m['end'], end) - start)
        for m in marks
        if m['start'] < end and m['end'] > start and m.get('type') != 'field'
    ]


def track_text(doc, obj, original, before_marks, start, end, replacement, revision):
    """Record a range-level attributed insertion, deletion or replacement."""
    if not doc['settings'].get('track_changes') or original == obj['text']:
        return None
    position, deleted, inserted = minimal_change(original, start, end, replacement)
    if not deleted and not inserted:
        return None
    record = {
        'id': new_id('change'),
        'kind': 'replacement' if deleted and inserted else 'deletion' if deleted else 'insertion',
        'target': obj['id'],
        'start': position,
        'end': position + len(inserted),
        'inserted': inserted,
        'deleted': deleted,
        'deleted_marks': _clip_marks(before_marks, position, position + len(deleted)),
        'before': original,
        'after': obj['text'],
        'before_marks': copy.deepcopy(before_marks),
        'after_marks': copy.deepcopy(obj.get('marks', [])),
        'status': 'pending',
        'base_revision': revision,
        **attribution(),
    }
    doc['review']['tracked_changes'].append(record)
    return record


def format_state(obj):
    return copy.deepcopy({k: obj[k] for k in ('marks', 'style', 'attrs', 'type') if k in obj})


def _multiset_difference(left, right):
    remaining = list(right)
    result = []
    for item in left:
        if item in remaining:
            remaining.remove(item)
        else:
            result.append(copy.deepcopy(item))
    return result


def track_format(doc, obj, before, revision):
    after = format_state(obj)
    if not doc['settings'].get('track_changes') or before == after:
        return None
    old_attrs, new_attrs = before.get('attrs', {}), after.get('attrs', {})
    record = {
        'id': new_id('change'),
        'kind': 'formatting',
        'target': obj['id'],
        'before': before,
        'after': after,
        'added_marks': _multiset_difference(after.get('marks', []), before.get('marks', [])),
        'removed_marks': _multiset_difference(before.get('marks', []), after.get('marks', [])),
        'attr_changes': [
            {
                'key': key,
                'before_present': key in old_attrs,
                'before': copy.deepcopy(old_attrs.get(key)),
                'after_present': key in new_attrs,
                'after': copy.deepcopy(new_attrs.get(key)),
            }
            for key in sorted(set(old_attrs) | set(new_attrs))
            if old_attrs.get(key, KeyError) != new_attrs.get(key, KeyError)
        ],
        'status': 'pending',
        'base_revision': revision,
        **attribution(),
    }
    for key in ('style', 'type'):
        if before.get(key) != after.get(key):
            record[key] = [before.get(key), after.get(key)]
    doc['review']['tracked_changes'].append(record)
    return record


def _conflict(message, target):
    raise ByaxError(
        'conflict', message + ' Reconcile the newer edit before rejecting this change.', target=target
    )


def reject_text(doc, obj, change, splice):
    """Undo one range-level text change without touching unrelated edits."""
    if change.get('anchor_status') == 'unresolved':
        _conflict('The tracked text was overwritten by a later edit.', change['id'])
    start, end = change['start'], change['end']
    if obj['text'][start:end] != change['inserted']:
        _conflict('Tracked text has changed again.', change['id'])
    splice(obj, start, end, change['deleted'])
    for mark in change.get('deleted_marks', []):
        restored = dict(mark, start=mark['start'] + start, end=mark['end'] + start)
        if not any(
            m.get('type') == restored.get('type')
            and m.get('value') == restored.get('value')
            and m['start'] <= restored['start']
            and m['end'] >= restored['end']
            for m in obj.get('marks', [])
        ):
            obj.setdefault('marks', []).append(restored)
    remap_splice(doc, obj, start, end, len(change['deleted']), skip_change=change['id'])


def reject_format(obj, change):
    marks = obj.setdefault('marks', [])
    for mark in change.get('added_marks', []):
        if mark not in marks:
            _conflict('Tracked formatting has changed again.', change['id'])
        marks.remove(mark)
    for mark in change.get('removed_marks', []):
        if mark not in marks:
            marks.append(copy.deepcopy(mark))
    attrs = obj.setdefault('attrs', {})
    for item in change.get('attr_changes', []):
        current_present = item['key'] in attrs
        if current_present != item['after_present'] or (
            current_present and attrs[item['key']] != item['after']
        ):
            _conflict('Tracked paragraph formatting has changed again.', change['id'])
        if item['before_present']:
            attrs[item['key']] = copy.deepcopy(item['before'])
        else:
            attrs.pop(item['key'], None)
    for key in ('style', 'type'):
        if key in change:
            if obj.get(key) != change[key][1]:
                _conflict('Tracked style has changed again.', change['id'])
            obj[key] = change[key][0]
