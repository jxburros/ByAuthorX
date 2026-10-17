"""Manuscript project binder (CP A1): schema, queries, canonical operations, interfaces, exports."""

import copy
import importlib.util
import json
import time
from pathlib import Path

import pytest

from byax import engine, project
from byax.cli import execute
from byax.errors import ByaxError
from byax.exchange import export_document, import_document
from byax.mcp import handle
from byax.model import new_document, text_of, validate_document
from byax.statistics import counts, document_statistics
from byax.storage import load_document, save_document

ROOT = Path(__file__).resolve().parents[1]


def _h(identity, text, level):
    return {
        'id': identity,
        'type': 'heading',
        'text': text,
        'style': f'heading{level}',
        'marks': [],
        'attrs': {'level': level},
    }


def _p(identity, text):
    return {'id': identity, 'type': 'paragraph', 'text': text, 'style': 'body', 'marks': [], 'attrs': {}}


def manuscript():
    doc = new_document('Novel')
    doc['blocks'] = [
        _p('front', 'For the readers.'),
        _h('ch1', 'Chapter one', 1),
        _h('s1', 'Arrival', 2),
        _p('s1a', 'The boat came in on the evening tide.'),
        _p('s1b', 'Mara counted the gulls.'),
        _h('s2', 'The keeper', 2),
        _p('s2a', 'He did not look up.'),
        _h('ch2', 'Chapter two', 1),
        _h('s3', 'Night', 2),
        _p('s3a', 'The lamp failed at midnight.'),
        _h('notes', 'Character sheets', 1),
        _p('notes_a', 'Mara is twelve.'),
    ]
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'project_init', 'kinds': {'1': 'chapter', '2': 'scene'}}]}
    )
    notes = project.item_id_for('notes')
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'project_set_meta', 'target': notes, 'values': {'kind': 'note'}}]}
    )
    return doc


def ids(doc):
    return {row['heading']: row['id'] for row in project.items(doc)}


def apply(doc, *operations, **envelope):
    return engine.apply(
        doc, {'expected_revision': doc['revision'], 'operations': list(operations), **envelope}
    )


def titles(doc):
    return [row['title'] for row in project.items(doc)]


# Schema and validation


def test_new_documents_have_no_binder_and_validate():
    doc = new_document('Plain')
    assert 'project' not in doc and validate_document(doc)
    assert project.tree(doc)['enabled'] is False and project.items(doc) == []


@pytest.mark.parametrize(
    'mutate',
    [
        lambda d: d['project'].update(extra=1),
        lambda d: d['project'].update(items=[]),
        lambda d: next(iter(d['project']['items'].values())).update(kind='epilogue'),
        lambda d: next(iter(d['project']['items'].values())).update(status=''),
        lambda d: next(iter(d['project']['items'].values())).update(colour='red'),
        lambda d: next(iter(d['project']['items'].values())).update(target_words=0),
        lambda d: next(iter(d['project']['items'].values())).update(include_in_compile='yes'),
        lambda d: next(iter(d['project']['items'].values())).update(synopsis='\ud800'),
        lambda d: list(d['project']['items'].values())[1].update(heading='ch1'),
        lambda d: d['project']['items'].update(
            s1a={'id': 's1a', 'heading': 'x', 'kind': 'scene', 'status': 'a'}
        ),
    ],
)
def test_invalid_binder_structures_are_schema_errors(mutate):
    doc = manuscript()
    mutate(doc)
    with pytest.raises(ByaxError) as error:
        validate_document(doc)
    assert error.value.code == 'schema_error'


def test_init_infers_kinds_deterministically_and_is_idempotent():
    doc = new_document()
    doc['blocks'] = [_h('a', 'Part', 1), _h('b', 'Chapter', 2), _h('c', 'Scene', 3), _p('d', 'Text here.')]
    first, _ = apply(doc, {'type': 'project_init'})
    second, _ = apply(doc, {'type': 'project_init'})
    assert first['project'] == second['project']
    assert [row['kind'] for row in project.items(first)] == ['part', 'chapter', 'scene']
    assert set(first['project']['items']) == {project.item_id_for(h) for h in 'abc'}
    again, result = apply(first, {'type': 'project_init'})
    assert result['execution'] == 'unchanged' and again['project'] == first['project']
    with pytest.raises(ByaxError):
        apply(doc, {'type': 'project_init', 'kinds': {'7': 'scene'}})


# Queries


def test_items_tree_counts_and_block_map():
    doc = manuscript()
    rows = project.items(doc)
    assert titles(doc) == ['Chapter one', 'Arrival', 'The keeper', 'Chapter two', 'Night', 'Character sheets']
    chapter = rows[0]
    assert chapter['children'] == [ids(doc)['s1'], ids(doc)['s2']] and chapter['depth'] == 0
    assert rows[1]['parent'] == chapter['id'] and rows[1]['depth'] == 1
    assert (
        rows[1]['words']
        == counts('Arrival\nThe boat came in on the evening tide.\nMara counted the gulls.')['words']
    )
    assert chapter['total_words'] == sum(r['words'] for r in rows[:3])
    assert (
        sum(r['words'] for r in rows) + counts('For the readers.')['words']
        == (document_statistics(doc)['authored']['words'])
    )
    tree = project.tree(doc)
    assert [node['title'] for node in tree['items']] == ['Chapter one', 'Chapter two', 'Character sheets']
    assert [child['title'] for child in tree['items'][0]['children']] == ['Arrival', 'The keeper']
    mapping = {entry['heading']: entry for entry in project.block_map(doc)}
    assert mapping['s1']['block_ids'] == ['s1', 's1a', 's1b']
    assert mapping['ch1']['block_ids'] == ['ch1'] and mapping['ch1']['section_block_ids'][-1] == 's2a'
    owners = project.item_for_block(doc)
    assert owners['s1b'] == ids(doc)['s1'] and 'front' not in owners


def test_notes_are_excluded_from_compile_by_default_and_exclusion_inherits():
    doc = manuscript()
    plan = project.compile_plan(doc)
    assert ids(doc)['notes'] in plan['excluded'] and 'notes_a' not in plan['block_ids']
    assert plan['block_ids'][0] == 'front' and plan['unassigned'] == ['front']
    doc, _ = apply(
        doc, {'type': 'project_set_meta', 'target': ids(doc)['ch2'], 'values': {'include_in_compile': False}}
    )
    plan = project.compile_plan(doc)
    assert {ids(doc)['ch2'], ids(doc)['s3']} <= set(plan['excluded']) and 's3a' not in plan['block_ids']
    doc, _ = apply(
        doc, {'type': 'project_set_meta', 'target': ids(doc)['notes'], 'values': {'include_in_compile': True}}
    )
    assert 'notes_a' in project.compile_plan(doc)['block_ids']


# Operations


def test_add_item_binds_or_creates_headings_with_placement():
    doc = manuscript()
    doc, result = apply(
        doc,
        {
            'type': 'project_add_item',
            'kind': 'scene',
            'text': 'Dawn',
            'parent': ids(doc)['ch2'],
            'heading': 'h_dawn',
        },
    )
    assert titles(doc)[-2:] == ['Dawn', 'Character sheets']
    dawn = next(r for r in project.items(doc) if r['heading'] == 'h_dawn')
    assert dawn['parent'] == ids(doc)['ch2'] and dawn['level'] == 2 and dawn['id'] in result['changed_ids']
    doc, _ = apply(
        doc, {'type': 'project_add_item', 'kind': 'scene', 'text': 'Prologue', 'before': ids(doc)['s1']}
    )
    assert titles(doc)[:3] == ['Chapter one', 'Prologue', 'Arrival']
    doc, _ = apply(
        doc,
        {
            'type': 'project_add_item',
            'kind': 'note',
            'text': 'Places',
            'values': {'synopsis': 'Research on lighthouses.', 'label': 'research'},
        },
    )
    places = project.items(doc)[-1]
    assert places['title'] == 'Places' and not places['include_in_compile'] and places['label'] == 'research'
    # Bind an existing unbound heading.
    doc['blocks'].append(_h('loose', 'Epilogue', 1))
    assert project.reconcile(doc)['unbound_headings'] == [{'id': 'loose', 'text': 'Epilogue', 'level': 1}]
    doc, _ = apply(doc, {'type': 'project_add_item', 'kind': 'back', 'target': 'loose'})
    assert project.reconcile(doc)['unbound_headings'] == []
    for bad in (
        {'type': 'project_add_item', 'kind': 'back', 'target': 'loose'},
        {'type': 'project_add_item', 'kind': 'scene', 'text': 'X', 'parent': ids(doc)['s1'], 'level': 1},
        {'type': 'project_add_item', 'kind': 'villain', 'text': 'X'},
        {'type': 'project_add_item', 'kind': 'scene', 'target': 's1a'},
        {'type': 'project_add_item', 'kind': 'scene', 'text': 'X', 'heading': 's1a'},
    ):
        with pytest.raises(ByaxError):
            apply(doc, bad)


def test_move_reorders_sections_and_reports_anchors():
    doc = manuscript()
    doc, _ = apply(
        doc,
        {'type': 'add_comment', 'target': 's3a', 'start': 4, 'end': 8, 'text': 'Which lamp?'},
        {'type': 'set_bookmark', 'name': 'night', 'target': 's3a', 'offset': 9},
    )
    comments, bookmarks = (
        copy.deepcopy(doc['review']['comments']),
        copy.deepcopy(doc['settings']['bookmarks']),
    )
    moved, result = apply(doc, {'type': 'project_move', 'target': ids(doc)['ch2'], 'before': ids(doc)['ch1']})
    assert titles(moved)[:5] == ['Chapter two', 'Night', 'Chapter one', 'Arrival', 'The keeper']
    assert moved['review']['comments'] == comments and moved['settings']['bookmarks'] == bookmarks
    assert {m['status'] for m in result['anchor_mappings']} == {'moved_with_section'}
    assert sorted(b['id'] for b in moved['blocks']) == sorted(b['id'] for b in doc['blocks'])
    # A scene moves into another chapter; nesting follows heading levels.
    moved, _ = apply(moved, {'type': 'project_move', 'target': ids(doc)['s2'], 'parent': ids(doc)['ch2']})
    keeper = next(r for r in project.items(moved) if r['heading'] == 's2')
    assert keeper['parent'] == ids(doc)['ch2'] and titles(moved)[:3] == ['Chapter two', 'Night', 'The keeper']
    moved, _ = apply(moved, {'type': 'project_move', 'target': ids(doc)['ch2'], 'after': None})
    assert titles(moved)[-3:] == ['Chapter two', 'Night', 'The keeper']
    for bad, code in (
        ({'type': 'project_move', 'target': ids(doc)['ch1'], 'after': ids(doc)['s1']}, 'conflict'),
        ({'type': 'project_move', 'target': ids(doc)['ch1'], 'parent': ids(doc)['ch2']}, 'conflict'),
        ({'type': 'project_move', 'target': ids(doc)['ch1']}, 'schema_error'),
        ({'type': 'project_move', 'target': 'missing', 'after': None}, 'missing_target'),
        (
            {'type': 'project_move', 'target': ids(doc)['ch1'], 'after': None, 'expected_hash': 'x'},
            'precondition_failed',
        ),
    ):
        with pytest.raises(ByaxError) as error:
            apply(doc, bad)
        assert error.value.code == code
    stale = copy.deepcopy(doc)
    with pytest.raises(ByaxError) as error:
        engine.apply(
            stale,
            {
                'expected_revision': 'rev_old',
                'operations': [{'type': 'project_move', 'target': ids(doc)['ch1'], 'after': None}],
            },
        )
    assert error.value.code == 'stale_revision'


def test_split_creates_item_at_exact_offset_and_remaps_anchors():
    doc = manuscript()
    doc, _ = apply(
        doc,
        {'type': 'add_comment', 'target': 's1a', 'start': 24, 'end': 31, 'text': 'Evening?'},
        {'type': 'set_bookmark', 'name': 'tide', 'target': 's1a', 'offset': 32},
    )
    scene = ids(doc)['s1']
    split, result = apply(
        doc,
        {
            'type': 'project_split',
            'target': scene,
            'at': 's1a',
            'offset': 13,
            'text': 'Landing',
            'heading': 'h_land',
        },
    )
    rows = project.items(split)
    assert titles(split)[:3] == ['Chapter one', 'Arrival', 'Landing']
    landing = next(r for r in rows if r['heading'] == 'h_land')
    assert (
        landing['kind'] == 'scene' and landing['parent'] == ids(doc)['ch1'] and landing['status'] == 'to_do'
    )
    blocks = {entry['heading']: entry['block_ids'] for entry in project.block_map(split)}
    assert blocks['s1'] == ['s1', 's1a']
    continuation = blocks['h_land'][1]
    text = {b['id']: b['text'] for b in split['blocks']}
    assert text['s1a'] == 'The boat came' and text[continuation] == ' in on the evening tide.'
    comment = split['review']['comments'][0]
    assert (
        comment['target'] == continuation
        and text[continuation][comment['start'] : comment['end']] == 'evening'
    )
    assert split['settings']['bookmarks']['tide'] == {'target': continuation, 'offset': 19}
    assert any(c['operation'] == 'project_split' for c in result['identity_changes'])
    # Offset zero splits before a block; the end offset leaves an empty new item.
    before, _ = apply(doc, {'type': 'project_split', 'target': scene, 'at': 's1b', 'offset': 0})
    assert next(
        e['block_ids'][1:] for e in project.block_map(before) if e['heading'] not in ('ch1', 's1')
    ) == ['s1b']
    end, _ = apply(
        doc, {'type': 'project_split', 'target': scene, 'at': 's1b', 'offset': 23, 'text': 'After'}
    )
    assert project.items(end)[2]['title'] == 'After' and project.items(end)[2]['words'] == 1
    for bad, code in (
        ({'type': 'project_split', 'target': scene, 'at': 's2a', 'offset': 0}, 'schema_error'),
        ({'type': 'project_split', 'target': scene, 'at': 's1', 'offset': 0}, 'schema_error'),
        ({'type': 'project_split', 'target': scene, 'at': 's1a', 'offset': 99}, 'schema_error'),
        (
            {'type': 'project_split', 'target': scene, 'at': 's1a', 'offset': 3, 'expected_text': 'x'},
            'precondition_failed',
        ),
    ):
        with pytest.raises(ByaxError) as error:
            apply(doc, bad)
        assert error.value.code == code
    combining = new_document()
    combining['blocks'] = [_h('h', 'Scene', 1), _p('p', 'Café au lait')]
    combining, _ = apply(combining, {'type': 'project_init'})
    with pytest.raises(ByaxError) as error:
        apply(combining, {'type': 'project_split', 'target': ids(combining)['h'], 'at': 'p', 'offset': 4})
    assert error.value.details['valid_alternatives'] == [3, 5]


def test_merge_removes_heading_and_keeps_every_anchor():
    doc = manuscript()
    doc, _ = apply(
        doc,
        {'type': 'add_comment', 'target': 's2', 'start': 4, 'end': 10, 'text': 'Rename?'},
        {'type': 'add_comment', 'target': 's2a', 'start': 3, 'end': 6, 'text': 'Not?'},
        {'type': 'set_bookmark', 'name': 'keeper', 'target': 's2', 'offset': 0},
        {
            'type': 'project_set_meta',
            'target': ids(doc)['s1'],
            'values': {'synopsis': 'Arrival.', 'target_words': 500},
        },
        {
            'type': 'project_set_meta',
            'target': ids(doc)['s2'],
            'values': {'synopsis': 'Keeper.', 'target_words': 700},
        },
    )
    reply_parent = next(c for c in doc['review']['comments'] if c['target'] == 's2')
    doc, _ = apply(doc, {'type': 'reply_comment', 'target': reply_parent['id'], 'text': 'Yes.'})
    merged, result = apply(doc, {'type': 'project_merge', 'target': ids(doc)['s1'], 'next': ids(doc)['s2']})
    assert titles(merged)[:3] == ['Chapter one', 'Arrival', 'Chapter two']
    assert (
        's2' not in {b['id'] for b in merged['blocks']} and ids(doc)['s2'] not in merged['project']['items']
    )
    first = merged['project']['items'][ids(doc)['s1']]
    assert first['synopsis'] == 'Arrival.\n\nKeeper.' and first['target_words'] == 1200
    assert next(e['block_ids'] for e in project.block_map(merged) if e['heading'] == 's1') == [
        's1',
        's1a',
        's1b',
        's2a',
    ]
    moved = next(c for c in merged['review']['comments'] if c['id'] == reply_parent['id'])
    assert (
        moved['target'] == 's2a' and moved['anchor_status'] == 'needs_review' and moved['quote'] == 'keeper'
    )
    assert moved['replies'][0]['target'] == 's2a' and moved['merged_from'] == 's2'
    untouched = next(
        c for c in merged['review']['comments'] if c['target'] == 's2a' and c['id'] != moved['id']
    )
    assert untouched['start'] == 3 and untouched['end'] == 6
    assert merged['settings']['bookmarks']['keeper'] == {'target': 's2a', 'offset': 0}
    assert {m['kind'] for m in result['anchor_mappings']} >= {'comment', 'bookmark'}
    assert len(merged['review']['comments']) == len(doc['review']['comments'])
    for bad in (
        {'type': 'project_merge', 'target': ids(doc)['s1'], 'next': ids(doc)['s3']},
        {'type': 'project_merge', 'target': ids(doc)['s2'], 'next': ids(doc)['ch2']},
    ):
        with pytest.raises(ByaxError) as error:
            apply(doc, bad)
        assert error.value.code == 'conflict'


def test_merge_refuses_pending_tracked_changes_in_removed_heading():
    doc = manuscript()
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'set_tracking', 'enabled': True}]}, allow_policy_changes=True
    )
    doc, _ = apply(doc, {'type': 'insert_text', 'target': 's2', 'offset': 0, 'text': 'Meet '})
    with pytest.raises(ByaxError) as error:
        apply(doc, {'type': 'project_merge', 'target': ids(doc)['s1'], 'next': ids(doc)['s2']})
    assert error.value.code == 'conflict' and error.value.details['changes']
    change = doc['review']['tracked_changes'][0]['id']
    accepted, _ = apply(doc, {'type': 'accept_change', 'target': change})
    merged, _ = apply(accepted, {'type': 'project_merge', 'target': ids(doc)['s1'], 'next': ids(doc)['s2']})
    assert merged['review']['tracked_changes'][0]['status'] == 'orphaned'


def test_status_target_and_metadata_operations():
    doc = manuscript()
    scene = ids(doc)['s1']
    doc, _ = apply(
        doc,
        {'type': 'project_set_status', 'target': scene, 'status': 'revised'},
        {'type': 'project_set_target', 'target': scene, 'words': 1500},
        {'type': 'project_set_target', 'words': 90000},
        {
            'type': 'project_set_meta',
            'target': scene,
            'values': {'label': 'POV Mara', 'synopsis': 'She lands.'},
        },
    )
    row = project.items(doc)[1]
    assert (row['status'], row['target_words'], row['label'], row['synopsis']) == (
        'revised',
        1500,
        'POV Mara',
        'She lands.',
    )
    assert project.tree(doc)['target_words'] == 90000
    doc, _ = apply(
        doc,
        {'type': 'project_set_target', 'target': scene, 'words': None},
        {'type': 'project_set_meta', 'target': scene, 'values': {'synopsis': None, 'label': None}},
    )
    assert not {'target_words', 'synopsis', 'label'} & set(doc['project']['items'][scene])
    for bad in (
        {'type': 'project_set_status', 'target': scene, 'status': ' padded '},
        {'type': 'project_set_target', 'target': scene, 'words': 0},
        {'type': 'project_set_target', 'target': scene, 'words': True},
        {'type': 'project_set_meta', 'target': scene, 'values': {'colour': 'red'}},
        {'type': 'project_set_meta', 'target': scene, 'values': {'synopsis': 'x' * 20001}},
        {'type': 'project_set_meta', 'target': scene, 'values': {'heading': 's2'}},
    ):
        with pytest.raises(ByaxError):
            apply(doc, bad)
    with pytest.raises(ByaxError) as error:
        apply(new_document(), {'type': 'project_set_target', 'words': 10})
    assert error.value.code == 'missing_target'


def test_remove_item_unbinds_or_deletes_section_with_orphaned_review():
    doc = manuscript()
    doc, _ = apply(doc, {'type': 'add_comment', 'target': 's3a', 'text': 'Darker.'})
    unbound, _ = apply(doc, {'type': 'project_remove_item', 'target': ids(doc)['ch2']})
    assert 'Chapter two' not in titles(unbound) and 'ch2' in {b['id'] for b in unbound['blocks']}
    night = next(r for r in project.items(unbound) if r['heading'] == 's3')
    assert night['parent'] is None  # the unbound chapter heading no longer nests it
    deleted, result = apply(
        doc, {'type': 'project_remove_item', 'target': ids(doc)['ch2'], 'delete_content': True}
    )
    assert {'ch2', 's3', 's3a'}.isdisjoint(b['id'] for b in deleted['blocks'])
    assert ids(doc)['s3'] not in deleted['project']['items']
    assert deleted['review']['comments'][0]['status'] == 'orphaned'
    assert any(m['status'] == 'orphaned' for m in result['anchor_mappings'])


def test_headings_removed_outside_binder_operations_are_reported_not_dropped(tmp_path):
    doc = manuscript()
    keeper = ids(doc)['s2']
    edited, result = apply(doc, {'type': 'join_blocks', 'target': 's1b', 'next': 's2'})
    assert result['project_orphans'] == [
        {'item': keeper, 'heading': 's2', 'kind': 'scene', 'reason': 'missing'}
    ]
    assert keeper in edited['project']['items']  # kept, never silently dropped
    report = project.reconcile(edited)
    assert not report['consistent'] and report['orphaned'][0]['item'] == keeper
    assert project.tree(edited)['orphaned'][0]['item'] == keeper
    path = tmp_path / 'orphan.byax'
    save_document(edited, path)
    reopened = load_document(path)
    assert project.reconcile(reopened)['orphaned'][0]['item'] == keeper
    with pytest.raises(ByaxError) as error:
        apply(reopened, {'type': 'project_move', 'target': keeper, 'after': None})
    assert error.value.code == 'conflict'
    _, result = apply(doc, {'type': 'set_block', 'target': 's3', 'values': _p('s3', 'Night')})
    assert result['project_orphans'][0]['reason'] == 'not_a_heading'
    # Rebinding to an unbound heading repairs the item; removing it is explicit.
    reopened['blocks'].insert(6, _h('s2b', 'The keeper', 2))
    repaired, _ = apply(
        reopened, {'type': 'project_set_meta', 'target': keeper, 'values': {'heading': 's2b'}}
    )
    assert project.reconcile(repaired)['consistent']
    removed, _ = apply(reopened, {'type': 'project_remove_item', 'target': keeper})
    assert project.reconcile(removed)['consistent']


def test_binder_survives_save_reopen_history_restore_and_proposals(tmp_path):
    doc = manuscript()
    before = doc['revision']
    moved, _ = apply(doc, {'type': 'project_move', 'target': ids(doc)['ch2'], 'before': ids(doc)['ch1']})
    path = tmp_path / 'novel.byax'
    save_document(moved, path)
    assert load_document(path)['project'] == moved['project']
    restored, _ = engine.restore(moved, before)
    assert titles(restored) == titles(doc)
    proposed, result = apply(
        doc,
        {
            'type': 'propose',
            'values': {
                'operations': [{'type': 'project_set_status', 'target': ids(doc)['s1'], 'status': 'final'}]
            },
        },
    )
    assert result['review'] == 'required' and project.items(proposed)[1]['status'] == 'to_do'
    accepted, _ = apply(
        proposed, {'type': 'accept_proposal', 'target': proposed['review']['proposals'][0]['id']}
    )
    assert project.items(accepted)[1]['status'] == 'final'


# Interfaces


def test_cli_actions_dispatch_through_execute_and_mcp(tmp_path):
    path = tmp_path / 'novel.byax'
    save_document(manuscript(), path)
    listing = execute('project', {'path': str(path)})
    assert listing['enabled'] and next(r['title'] for r in listing['items']) == 'Chapter one'
    doc = load_document(path)
    preview = execute(
        'project',
        {
            'path': str(path),
            'action': 'move',
            'target': ids(doc)['ch2'],
            'before': ids(doc)['ch1'],
            'dry_run': True,
        },
    )
    assert preview['execution'] == 'dry_run' and load_document(path)['revision'] == doc['revision']
    result = execute(
        'project',
        {
            'path': str(path),
            'action': 'move',
            'target': ids(doc)['ch2'],
            'before': ids(doc)['ch1'],
            'expect_revision': doc['revision'],
            'request_id': 'move-1',
        },
    )
    assert next(r['title'] for r in result['project']['items']) == 'Chapter two'
    assert titles(load_document(path))[0] == 'Chapter two'
    split = execute(
        'project',
        {
            'path': str(path),
            'action': 'split',
            'target': ids(doc)['s1'],
            'at': 's1a',
            'offset': 13,
            'text': 'Landing',
        },
    )
    assert 'Landing' in [r['title'] for r in split['project']['items']]
    execute(
        'project', {'path': str(path), 'action': 'set-status', 'target': ids(doc)['s1'], 'status': 'draft'}
    )
    execute('project', {'path': str(path), 'action': 'set-target', 'target': ids(doc)['s1'], 'words': 800})
    execute('project', {'path': str(path), 'action': 'set-target', 'target': ids(doc)['s1'], 'clear': True})
    execute(
        'project',
        {'path': str(path), 'action': 'set-meta', 'target': ids(doc)['s1'], 'input': {'label': 'A'}},
    )
    execute('project', {'path': str(path), 'action': 'add', 'kind': 'note', 'text': 'Places'})
    row = next(r for r in project.items(load_document(path)) if r['heading'] == 's1')
    assert row['status'] == 'draft' and row['label'] == 'A' and 'target_words' not in row
    for action in ('tree', 'reconcile', 'blocks', 'compile'):
        assert execute('project', {'path': str(path), 'action': action})['execution'] == 'completed'
    with pytest.raises(ByaxError) as error:
        execute('project', {'path': str(path), 'action': 'set-target', 'target': ids(doc)['s1']})
    assert error.value.code == 'invalid_input'
    with pytest.raises(ByaxError) as error:
        execute('project', {'path': str(path), 'action': 'shuffle'})
    assert error.value.code == 'invalid_input'
    tools = handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    assert 'byax_project' in {tool['name'] for tool in tools}
    call = handle(
        {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {'name': 'byax_project', 'arguments': {'path': str(path), 'action': 'compile'}},
        }
    )['result']
    assert not call['isError'] and call['structuredContent'] == {
        **json.loads(json.dumps(execute('project', {'path': str(path), 'action': 'compile'}))),
        'exit_code': 0,
    }


def test_docx_and_markdown_round_trip_binder_order_with_located_losses(tmp_path):
    doc = manuscript()
    scene = ids(doc)['s1']
    doc, _ = apply(
        doc,
        {'type': 'project_set_status', 'target': scene, 'status': 'draft'},
        {'type': 'project_set_target', 'target': scene, 'words': 1200},
        {'type': 'project_move', 'target': ids(doc)['ch2'], 'before': ids(doc)['ch1']},
    )
    order = [(row['title'], row['level']) for row in project.items(doc)]
    for fmt in ('docx', 'md'):
        out = tmp_path / f'novel.{fmt}'
        result = export_document(doc, out)
        losses = {loss['target']: loss for loss in result['losses'] if loss['code'] == 'binder_metadata'}
        assert set(losses) == {row['heading'] for row in project.items(doc)}
        assert {'status', 'target_words'} <= set(losses['s1']['fields']) and losses['s1']['item'] == scene
        imported, _ = import_document(out)
        headings = [
            (b['text'], b['attrs'].get('level', 1)) for b in imported['blocks'] if b['type'] == 'heading'
        ]
        assert headings[: len(order)] == order
        rebuilt, _ = engine.apply(
            imported, {'operations': [{'type': 'project_init', 'kinds': {'1': 'chapter', '2': 'scene'}}]}
        )
        assert [(r['title'], r['level']) for r in project.items(rebuilt)] == order
        assert all(r['status'] == project.DEFAULT_STATUS for r in project.items(rebuilt))
    assert not [
        loss
        for loss in export_document(doc, tmp_path / 'novel.byax')['losses']
        if loss['code'].startswith('binder')
    ]


def test_clean_publication_drops_binder_metadata(tmp_path):
    doc = manuscript()
    doc, _ = apply(
        doc, {'type': 'project_set_meta', 'target': ids(doc)['s1'], 'values': {'synopsis': 'Private plan.'}}
    )
    out = tmp_path / 'clean.byax'
    export_document(doc, out, profile={'clean': True})
    assert 'project' not in load_document(out)


# Exit criterion: a 100,000-word project opens, reorders a chapter and saves.

_SPEC = importlib.util.spec_from_file_location('byax_fixtures', ROOT / 'scripts' / 'generate_fixtures.py')
_FIXTURES = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURES)


def test_100k_word_project_opens_reorders_a_chapter_and_saves(tmp_path):
    doc = _FIXTURES.make_fixture('project_100k')
    assert counts(text_of(doc))['words'] >= 100_000
    path = tmp_path / 'project.byax'
    save_document(doc, path)
    started = time.perf_counter()
    opened = load_document(path)
    chapters = [row for row in project.items(opened) if row['kind'] == 'chapter']
    moved, result = engine.apply(
        opened,
        {
            'expected_revision': opened['revision'],
            'operations': [{'type': 'project_move', 'target': chapters[0]['id'], 'after': chapters[4]['id']}],
        },
    )
    save_document(moved, path)
    elapsed = time.perf_counter() - started
    # Generous bound for shared CI hosts; measured timings live in docs/benchmarks.
    assert elapsed < 30, elapsed
    reordered = [row['title'] for row in project.items(load_document(path)) if row['kind'] == 'chapter']
    assert reordered[:6] == ['Chapter 2', 'Chapter 3', 'Chapter 4', 'Chapter 5', 'Chapter 1', 'Chapter 6']
    assert moved['review']['comments'] == opened['review']['comments']
    assert any(m['status'] == 'moved_with_section' for m in result['anchor_mappings'])
