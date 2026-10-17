"""Named drafts (CP A4): saved states of a manuscript, built on branches and delta history.

A draft is a branch head with a name and a note. ``save_draft`` records the working document's
current revision under a new name and keeps working on the current draft, so the saved state is
frozen until someone switches to it. Nothing here adds a second history: drafts reuse
``history.branches`` (heads), ``history.forks`` (fork points) and the retained revisions, and the
note and save time sit in ``history.drafts``.

Two drafts compare scene by scene: for every binder item the comparison reports whether it is
unchanged, changed, added or removed with word deltas, using the semantic comparison in ``compare``
for the details of a changed item. Bringing changes from a draft is ``propose_merge``: a reviewable
proposal that changes nothing until it is accepted.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from . import compare as compare_module
from . import engine, project
from .errors import ByaxError
from .model import text_of
from .statistics import word_count

MAX_NAME = 80
MAX_NOTE = 2000
ACTIONS = ('list', 'save', 'switch', 'compare', 'bring')

Doc = dict[str, Any]


def _clean_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ByaxError('invalid_input', 'A draft needs a name.', field='name')
    name = name.strip()
    if len(name) > MAX_NAME or re.search(r'[\x00-\x1f\x7f]', name):
        raise ByaxError(
            'invalid_input',
            f'Draft names are single-line text of at most {MAX_NAME} characters.',
            field='name',
        )
    return name


def _clean_note(note: Any) -> str:
    if note is None:
        return ''
    if not isinstance(note, str) or len(note) > MAX_NOTE or any(0xD800 <= ord(c) <= 0xDFFF for c in note):
        raise ByaxError(
            'invalid_input', f'A draft note is text of at most {MAX_NOTE} characters.', field='note'
        )
    return note.strip()


def current_draft(doc: Doc) -> str:
    return doc['history'].get('current_branch', 'main')


def _head(doc: Doc, name: str) -> str:
    history = doc['history']
    if name == current_draft(doc):
        return doc['revision']
    head = history.get('branches', {}).get(name)
    if head is None:
        raise ByaxError('missing_target', 'Draft does not exist.', target=name)
    return head


def draft_document(doc: Doc, name: str) -> Doc:
    """The document as it stands in a draft (no history); the working draft is the document itself."""
    if name == current_draft(doc):
        return doc
    return engine._revision(doc, _head(doc, name))


def list_drafts(doc: Doc) -> list[dict[str, Any]]:
    """Every draft: the working one first, then the saved ones, with note, save time and word count."""
    history = doc['history']
    saved = history.get('drafts', {})
    current = current_draft(doc)
    entries = []
    for name in dict.fromkeys([current, *history.get('branches', {})]):
        meta = saved.get(name) if isinstance(saved.get(name), dict) else {}
        try:
            words: int | None = word_count(text_of(draft_document(doc, name)))
        except ByaxError:
            words = None  # the head is no longer in retained history; the draft is listed, not hidden
        entries.append(
            {
                'name': name,
                'current': name == current,
                'revision': _head(doc, name),
                'note': meta.get('note', ''),
                'saved_at': meta.get('saved_at'),
                'words': words,
            }
        )
    return entries


def save_draft(
    doc: Doc, name: str, note: str | None = None, *, switch: bool = False
) -> tuple[Doc, dict[str, Any]]:
    """Save the working document as a named draft; ``switch`` also makes it the working draft."""
    name, note = _clean_name(name), _clean_note(note)
    history = doc['history']
    current = current_draft(doc)
    if name == current or name in history.get('branches', {}):
        raise ByaxError('conflict', 'A draft with this name already exists.', target=name)
    candidate = engine._candidate(doc)
    target = candidate['history']
    target.setdefault('branches', {})[current] = doc['revision']
    target['branches'][name] = doc['revision']
    target.setdefault('forks', {})[name] = doc['revision']
    target['drafts'] = {
        **history.get('drafts', {}),
        name: {
            'note': note,
            'saved_at': datetime.now(UTC).isoformat(),
            'revision': doc['revision'],
            'words': word_count(text_of(doc)),
        },
    }
    if switch:
        target['current_branch'] = name
    return candidate, engine._result(candidate, doc['revision'])


def switch_draft(doc: Doc, name: str) -> tuple[Doc, dict[str, Any]]:
    """Make a saved draft the working document (no revision is created)."""
    return engine.switch_branch(doc, _clean_name(name))


# Scene-level comparison


def _sections(state: Doc) -> tuple[dict[str, dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Binder items by identity (with their own blocks), binder order, and unassigned blocks."""
    blocks = {block['id']: block for block in state['blocks']}
    owned = {entry['item']: entry['block_ids'] for entry in project.block_map(state)}
    result: dict[str, dict[str, Any]] = {}
    order = []
    for row in project.items(state):
        order.append(row['id'])
        result[row['id']] = {
            'row': row,
            'blocks': [blocks[i] for i in owned.get(row['id'], [])],
        }
    assigned = project.item_for_block(state)
    loose = [block for block in state['blocks'] if block['id'] not in assigned]
    return result, order, loose


FIELDS = ('kind', 'status', 'synopsis', 'target_words', 'label', 'include_in_compile')


def _details(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    semantic = compare_module.compare({'blocks': before}, {'blocks': after})
    structure = semantic['structure']
    return {
        'edits': sum(len(entry['changes']) for entry in semantic['wording']),
        'blocks_added': len(structure['added']),
        'blocks_removed': len(structure['removed']),
        'blocks_moved': len(structure['moved']),
        'summary': [line for line in semantic['summary'] if line != 'No semantic changes.'][:5],
    }


def _words(blocks: list[dict[str, Any]]) -> int:
    return word_count(text_of({'blocks': blocks}))


def compare_drafts(doc: Doc, source: str, target: str | None = None) -> dict[str, Any]:
    """What ``target`` (default: the working draft) changes relative to ``source``, scene by scene."""
    target = target or current_draft(doc)
    if source == target:
        raise ByaxError('invalid_input', 'Choose two different drafts to compare.', field='against')
    before, after = draft_document(doc, source), draft_document(doc, target)
    old, old_order, old_loose = _sections(before)
    new, new_order, new_loose = _sections(after)
    moved = set(compare_module._moved([i for i in old_order if i in new], [i for i in new_order if i in old]))
    items: list[dict[str, Any]] = []
    for identity in new_order:
        entry = new[identity]
        row = entry['row']
        record: dict[str, Any] = {
            'id': identity,
            'title': row['title'],
            'kind': row['kind'],
            'depth': row['depth'],
            'status': 'added',
            'words_before': 0,
            'words_after': row['words'],
        }
        if identity in old:
            before_row = old[identity]['row']
            fields = [f for f in FIELDS if before_row.get(f) != row.get(f)]
            changed = old[identity]['blocks'] != entry['blocks']
            record.update(words_before=before_row['words'])
            record['status'] = 'unchanged'
            if changed or fields or identity in moved or before_row['parent'] != row['parent']:
                record['status'] = 'changed'
                record['fields'] = fields
                record['moved'] = identity in moved or before_row['parent'] != row['parent']
                record.update(_details(old[identity]['blocks'], entry['blocks']))
                if before_row['title'] != row['title']:
                    record['previous_title'] = before_row['title']
        record['word_delta'] = record['words_after'] - record['words_before']
        items.append(record)
    for position, identity in enumerate(old_order):
        if identity in new:
            continue
        row = old[identity]['row']
        removed = {
            'id': identity,
            'title': row['title'],
            'kind': row['kind'],
            'depth': row['depth'],
            'status': 'removed',
            'words_before': row['words'],
            'words_after': 0,
            'word_delta': -row['words'],
        }
        anchor = next((p for p in reversed(old_order[:position]) if p in new), None)
        index = 0 if anchor is None else next(i for i, r in enumerate(items) if r['id'] == anchor) + 1
        while index < len(items) and items[index]['status'] == 'removed':
            index += 1
        items.insert(index, removed)
    binder = 'project' in before or 'project' in after
    if binder and old_loose != new_loose:
        items.append(
            {
                'id': None,
                'title': 'Text outside the binder',
                'kind': 'unassigned',
                'depth': 0,
                'status': 'changed',
                'words_before': _words(old_loose),
                'words_after': _words(new_loose),
                'word_delta': _words(new_loose) - _words(old_loose),
                **_details(old_loose, new_loose),
            }
        )
    totals = {status: sum(1 for i in items if i['status'] == status) for status in ('unchanged', 'changed')}
    totals.update(added=sum(1 for i in items if i['status'] == 'added'))
    totals.update(removed=sum(1 for i in items if i['status'] == 'removed'))
    words_before, words_after = word_count(text_of(before)), word_count(text_of(after))
    return {
        'from': source,
        'to': target,
        'binder': binder,
        'summary': {
            **totals,
            'words_before': words_before,
            'words_after': words_after,
            'word_delta': words_after - words_before,
        },
        'items': items,
        'document': compare_module.compare(before, after)['summary'],
    }


def bring_operation(doc: Doc, name: str, note: str | None = None) -> dict[str, Any]:
    """The canonical operation that proposes bringing a draft's changes into the working draft."""
    name = _clean_name(name)
    if name not in doc['history'].get('branches', {}) or name == current_draft(doc):
        raise ByaxError('missing_target', 'Choose a saved draft other than the working one.', target=name)
    operation: dict[str, Any] = {'type': 'propose_merge', 'source': name}
    note = _clean_note(note)
    if note:
        operation['explanation'] = note
    return operation


def drafts_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    """``byax drafts``: list, save, switch, compare, bring."""
    from .cli import _load, _mutate, envelope

    action = a.get('action', 'list')
    if action not in ACTIONS:
        raise ByaxError('invalid_input', f'Unknown drafts action: {action}', supported=list(ACTIONS))
    if action == 'list':
        doc = _load(a['path'])
        return envelope({'drafts': list_drafts(doc), 'current': current_draft(doc)}, doc)
    if action == 'compare':
        doc = _load(a['path'])
        if not a.get('name'):
            raise ByaxError('invalid_input', 'compare requires --name', field='name')
        return envelope({'comparison': compare_drafts(doc, a['name'], a.get('against'))}, doc)
    if not a.get('name'):
        raise ByaxError('invalid_input', f'{action} requires --name', field='name')

    def change(doc: Doc) -> tuple[Doc, dict[str, Any], bool]:
        if a.get('expect_revision') and doc['revision'] != a['expect_revision']:
            raise ByaxError(
                'stale_revision',
                'Document revision changed',
                expected=a['expect_revision'],
                actual=doc['revision'],
            )
        dry = a.get('dry_run', False)
        if action == 'save':
            updated, result = save_draft(doc, a['name'], a.get('note'), switch=a.get('switch', False))
        elif action == 'switch':
            updated, result = switch_draft(doc, a['name'])
        else:
            request: dict[str, Any] = {'operations': [bring_operation(doc, a['name'], a.get('note'))]}
            for option, key in (('expect_revision', 'expected_revision'), ('request_id', 'request_id')):
                if a.get(option) is not None:
                    request[key] = a[option]
            updated, result = engine.apply(doc, request, dry_run=dry)
        result['drafts'] = list_drafts(updated)
        return updated, result, not dry

    return _mutate(a['path'], change)
