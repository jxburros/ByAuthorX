"""Randomized (seeded, deterministic) properties: binder split, merge and move keep every anchor.

Each seed builds a manuscript with passage comments, replies, bookmarks and pending
tracked changes, then runs random binder operations. After every operation:

- no comment, reply, bookmark or tracked change disappears or becomes orphaned;
- every anchor still targets a block in the body;
- for split and merge, the body text outside headings is unchanged as one stream, and
  each paragraph anchor still covers the same characters of that stream;
- for move, anchor records are identical and the multiset of blocks is unchanged;
- rejected operations leave the document unchanged (atomicity).
"""

import copy
import random

import pytest

from byax import engine, project
from byax.anchors import comments
from byax.errors import ByaxError
from byax.model import new_document

WORDS = ['tide', 'lamp', 'gull', 'Café', 'naïve', 'rope', '👩🏽‍💻', 'salt', 'keeper', 'night']


def _sentence(rng):
    return ' '.join(rng.choice(WORDS) for _ in range(rng.randint(3, 9))) + '.'


def _manuscript(rng):
    doc = new_document('Property')
    blocks = []
    for chapter in range(rng.randint(2, 4)):
        blocks.append(_heading(f'c{chapter}', f'Chapter {chapter}', 1))
        for scene in range(rng.randint(1, 4)):
            blocks.append(_heading(f'c{chapter}s{scene}', f'Scene {chapter}.{scene}', 2))
            for paragraph in range(rng.randint(0, 3)):
                blocks.append(
                    {
                        'id': f'c{chapter}s{scene}p{paragraph}',
                        'type': 'paragraph',
                        'text': _sentence(rng),
                        'style': 'body',
                        'marks': [],
                        'attrs': {},
                    }
                )
    doc['blocks'] = blocks
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'set_tracking', 'enabled': True}]}, allow_policy_changes=True
    )
    paragraphs = [b for b in doc['blocks'] if b['type'] == 'paragraph']
    operations = []
    for index in range(rng.randint(2, 8)):
        block = rng.choice(paragraphs)
        bounds = engine.grapheme_boundaries(block['text'])
        start, end = sorted(rng.sample(bounds, 2))
        operations.append(
            {'type': 'add_comment', 'target': block['id'], 'start': start, 'end': end, 'text': 'c'}
        )
        operations.append(
            {'type': 'set_bookmark', 'name': f'b{index}', 'target': block['id'], 'offset': rng.choice(bounds)}
        )
    headings = [b for b in doc['blocks'] if b['type'] == 'heading']
    operations.append({'type': 'add_comment', 'target': rng.choice(headings)['id'], 'text': 'heading note'})
    doc, _ = engine.apply(doc, {'operations': operations})
    first = doc['review']['comments'][0]['id']
    doc, _ = engine.apply(doc, {'operations': [{'type': 'reply_comment', 'target': first, 'text': 'reply'}]})
    for _ in range(rng.randint(1, 4)):
        block = rng.choice([b for b in doc['blocks'] if b['type'] == 'paragraph'])
        offset = rng.choice(engine.grapheme_boundaries(block['text']))
        doc, _ = engine.apply(
            doc,
            {
                'operations': [
                    {'type': 'insert_text', 'target': block['id'], 'offset': offset, 'text': ' new'}
                ]
            },
        )
    return doc


def _heading(identity, text, level):
    return {
        'id': identity,
        'type': 'heading',
        'text': text,
        'style': f'heading{level}',
        'marks': [],
        'attrs': {'level': level},
    }


def _stream(doc):
    """Concatenated non-heading text and each paragraph's global start offset."""
    starts, parts, position = {}, [], 0
    for block in doc['blocks']:
        if block['type'] == 'heading':
            continue
        starts[block['id']] = position
        parts.append(block.get('text', ''))
        position += len(block.get('text', ''))
    return ''.join(parts), starts


def _anchors(doc):
    return {
        'comments': {c['id']: copy.deepcopy(c) for c in comments(doc)},
        'bookmarks': copy.deepcopy(doc['settings'].get('bookmarks', {})),
        'changes': {c['id']: copy.deepcopy(c) for c in doc['review']['tracked_changes']},
    }


def _check_present(before, after, doc):
    ids = {b['id'] for b in doc['blocks']}
    assert set(before['comments']) <= set(after['comments'])
    assert set(before['bookmarks']) == set(after['bookmarks'])
    assert set(before['changes']) <= set(after['changes'])
    for comment in after['comments'].values():
        assert comment['target'] in ids and comment.get('status') != 'orphaned', comment
    for bookmark in after['bookmarks'].values():
        assert bookmark['target'] in ids and bookmark.get('status') != 'orphaned', bookmark
    for change in after['changes'].values():
        assert change['target'] in ids and change['status'] == 'pending', change


def _check_stream(old_doc, new_doc, before, after):
    old_text, old_starts = _stream(old_doc)
    new_text, new_starts = _stream(new_doc)
    assert old_text == new_text
    for name, bookmark in before['bookmarks'].items():
        if bookmark['target'] in old_starts:
            moved = after['bookmarks'][name]
            assert moved['target'] in new_starts
            assert (
                new_starts[moved['target']] + moved['offset']
                == old_starts[bookmark['target']] + bookmark['offset']
            )
    for identity, comment in before['comments'].items():
        moved = after['comments'][identity]
        if comment['target'] in old_starts and comment.get('start') is not None:
            begin = old_starts[comment['target']] + comment['start']
            assert new_starts[moved['target']] + moved['start'] == begin
            if moved.get('anchor_status') != 'needs_review':
                assert (
                    new_text[begin : new_starts[moved['target']] + moved['end']]
                    == (old_text[begin : old_starts[comment['target']] + comment['end']])
                )
    groups = {}
    for change in after['changes'].values():
        groups.setdefault(change.get('group', change['id']), []).append(change)
    for identity, change in before['changes'].items():
        if change['target'] not in old_starts:
            continue
        pieces = sorted(
            groups[change.get('group', identity)], key=lambda c: new_starts[c['target']] + c['start']
        )
        for piece in pieces:
            begin = new_starts[piece['target']] + piece['start']
            assert new_text[begin : begin + len(piece['inserted'])] == piece['inserted']
        group = change.get('group', identity)
        originals = sorted(
            (c for c in before['changes'].values() if c.get('group', c['id']) == group),
            key=lambda c: old_starts[c['target']] + c['start'],
        )
        assert ''.join(p['inserted'] for p in pieces) == ''.join(c['inserted'] for c in originals)


def _random_operation(rng, doc):
    rows = project.items(doc)
    row = rng.choice(rows)
    choice = rng.choice(('move', 'move', 'split', 'split', 'merge'))
    if choice == 'move':
        other = rng.choice(rows)
        placement = rng.choice(('after', 'before', 'parent'))
        return 'move', {'type': 'project_move', 'target': row['id'], placement: other['id']}
    if choice == 'split':
        own = next(entry for entry in project.block_map(doc) if entry['item'] == row['id'])['block_ids'][1:]
        if not own:
            return 'split', {'type': 'project_split', 'target': row['id'], 'at': row['heading'], 'offset': 0}
        chosen = rng.choice(own)
        block = next(b for b in doc['blocks'] if b['id'] == chosen)
        offset = rng.choice(engine.grapheme_boundaries(block['text'])) if block['type'] == 'paragraph' else 0
        return 'split', {'type': 'project_split', 'target': row['id'], 'at': block['id'], 'offset': offset}
    position = rows.index(row)
    following = rows[position + 1]['id'] if position + 1 < len(rows) else row['id']
    return 'merge', {'type': 'project_merge', 'target': row['id'], 'next': following}


@pytest.mark.parametrize('seed', range(30))
def test_split_merge_and_move_preserve_every_anchor(seed):
    rng = random.Random(seed)
    doc = _manuscript(rng)
    applied = {'move': 0, 'split': 0, 'merge': 0}
    for _ in range(25):
        kind, operation = _random_operation(rng, doc)
        before = _anchors(doc)
        snapshot = copy.deepcopy(doc)
        try:
            updated, _ = engine.apply(doc, {'expected_revision': doc['revision'], 'operations': [operation]})
        except ByaxError as error:
            assert error.code in ('conflict', 'schema_error', 'missing_target'), error.to_dict()
            assert doc == snapshot  # rejected operations change nothing
            continue
        applied[kind] += 1
        after = _anchors(updated)
        _check_present(before, after, updated)
        if kind == 'move':
            assert after == before
            assert sorted(map(engine.canonical_json, doc['blocks'])) == sorted(
                map(engine.canonical_json, updated['blocks'])
            )
        else:
            _check_stream(doc, updated, before, after)
        assert project.reconcile(updated)['consistent']
        doc = updated
    assert sum(applied.values()) > 5, applied
