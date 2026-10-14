import copy

import pytest

from byax import engine
from byax.errors import ByaxError
from byax.model import new_document


def edit(doc, *ops, **envelope):
    return engine.apply(doc, {'operations': list(ops), **envelope})


def test_batch_rollback_and_stale_revision():
    doc = new_document(text='Hello')
    original = copy.deepcopy(doc)
    with pytest.raises(ByaxError):
        edit(doc, {'type': 'set_text', 'target': doc['blocks'][0]['id'], 'text': 'Changed'}, {'type': 'delete_block', 'target': 'missing'})
    assert doc == original
    with pytest.raises(ByaxError, match='revision changed'):
        edit(doc, {'type': 'set_metadata', 'values': {'title': 'Changed'}}, expected_revision='rev_stale')


def test_idempotency_before_stale_check_and_conflicting_reuse():
    doc = new_document(text='Hi')
    env = {'request_id': 'once', 'expected_revision': doc['revision'], 'operations': [{'type': 'insert_text', 'target': doc['blocks'][0]['id'], 'offset': 2, 'text': '!'}]}
    changed, _ = engine.apply(doc, env)
    retried, result = engine.apply(changed, env)
    assert retried == changed
    assert result['execution'] == 'already_committed'
    env['operations'][0]['text'] = '?'
    with pytest.raises(ByaxError, match='already used'):
        engine.apply(changed, env)


def test_grapheme_offsets_and_utf16():
    doc = new_document(text='A👩‍💻éZ')
    target = doc['blocks'][0]['id']
    with pytest.raises(ByaxError, match='grapheme') as error:
        edit(doc, {'type': 'insert_text', 'target': target, 'offset': 2, 'text': 'bad'})
    assert error.value.details['valid_alternatives'] == [1, 4]
    changed, _ = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 4, 'text': '!'})
    assert changed['blocks'][0]['text'] == 'A👩‍💻!éZ'
    assert engine.scalar_to_utf16('A😀B', 2) == 3
    assert engine.utf16_to_scalar('A😀B', 3) == 2
    with pytest.raises(ByaxError):
        engine.utf16_to_scalar('A😀B', 2)


def test_quoted_match_ambiguous_and_expected_text():
    doc = new_document(text='a a')
    op = {'type': 'replace_text', 'target': doc['blocks'][0]['id'], 'match': 'a', 'replacement': 'b'}
    with pytest.raises(ByaxError) as error:
        edit(doc, op)
    assert error.value.code == 'ambiguous_selection'
    changed, _ = edit(doc, {**op, 'occurrence': 2, 'expected_text': 'a'})
    assert changed['blocks'][0]['text'] == 'a b'


def test_protection_and_dry_run_and_history():
    doc = new_document(text='Original')
    target = doc['blocks'][0]['id']
    op = {'type': 'set_text', 'target': target, 'text': 'Changed'}
    with pytest.raises(ByaxError) as error:
        edit(doc, op, protect=[{'target': target, 'kind': 'text'}])
    assert error.value.code == 'protected_edit'
    candidate, result = engine.apply(doc, {'operations': [op]}, dry_run=True)
    assert candidate['revision'] == doc['revision']
    assert candidate['history'] == doc['history']
    assert result['execution'] == 'dry_run'
    changed, _ = edit(doc, op)
    assert engine.diff(changed, doc['revision'])['changes']['modified']
    restored, _ = engine.restore(changed, doc['revision'])
    assert restored['blocks'] == doc['blocks']
    assert restored['revision'] not in (doc['revision'], changed['revision'])


def test_text_protection_allows_layout_and_style():
    doc = new_document(text='Original')
    target = doc['blocks'][0]['id']
    changed, _ = edit(doc, {'type': 'set_layout', 'values': {'width': 700}}, protect=[{'target': target, 'kind': 'text'}])
    assert changed['layout']['width'] == 700


def test_unknown_fields_and_values_are_schema_errors():
    doc = new_document()
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'insert_paragraph', 'text': 'x', 'typo': 1})
    assert error.value.code == 'schema_error'
    with pytest.raises(ByaxError):
        edit(doc, {'type': 'insert_text', 'target': doc['blocks'][0]['id'], 'offset': True, 'text': 'x'})


def test_table_protection_and_structural_ops():
    doc, _ = edit(new_document(), {'type': 'insert_table', 'rows': 2, 'columns': 2})
    table = doc['blocks'][-1]
    cell = table['rows'][0]['cells'][0]['id']
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'update_cell', 'target': cell, 'text': 'Changed'}, protect=[{'target': table['id'], 'kind': 'values'}])
    assert error.value.code == 'protected_edit'
    changed, _ = edit(doc, {'type': 'add_column', 'target': table['id'], 'index': 1})
    assert len(changed['blocks'][-1]['rows'][0]['cells']) == 3


def test_split_join_preserves_marks():
    doc = new_document(text='Hello world')
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'format_text', 'target': target, 'start': 0, 'end': 11, 'marks': {'bold': True}})
    split, _ = edit(doc, {'type': 'split_block', 'target': target, 'offset': 5})
    assert [b['text'] for b in split['blocks']] == ['Hello', ' world']
    joined, _ = edit(split, {'type': 'join_blocks', 'target': target, 'next': split['blocks'][1]['id']})
    assert joined['blocks'][0]['text'] == 'Hello world'
    assert joined['blocks'][0]['marks'][1]['end'] == 11


def test_required_policy_frozen_during_edit():
    doc = new_document(text='must stay')
    doc['settings']['requirements'] = {'required': {'kind': 'required_text', 'value': 'must stay'}}
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'set_requirement', 'id': 'required', 'values': {'kind': 'required_text', 'value': 'changed'}}, {'type': 'set_text', 'target': doc['blocks'][0]['id'], 'text': 'changed'}, required_suites=['requirements'])
    assert error.value.code == 'failed_requirement'


def test_proposal_stale_target_and_unrelated_edits():
    doc = new_document(text='Before')
    target = doc['blocks'][0]['id']
    proposed, result = edit(doc, {'type': 'propose', 'values': {'operations': [{'type': 'set_text', 'target': target, 'text': 'After'}]}})
    proposal_id = result['changed_ids'][0]
    accepted, _ = edit(proposed, {'type': 'accept_proposal', 'target': proposal_id})
    assert accepted['blocks'][0]['text'] == 'After'
    changed, _ = edit(proposed, {'type': 'set_text', 'target': target, 'text': 'Someone else'})
    with pytest.raises(ByaxError) as error:
        edit(changed, {'type': 'accept_proposal', 'target': proposal_id})
    assert error.value.code == 'stale_revision'


def test_context_bound_and_find_cells():
    doc = new_document(text='too long')
    report = engine.context(doc, limit=2)
    assert report['execution'] == 'incomplete'
    assert report['blocks'] == []
    doc, _ = edit(doc, {'type': 'insert_table', 'rows': 1, 'columns': 1})
    cell = doc['blocks'][-1]['rows'][0]['cells'][0]['id']
    doc, _ = edit(doc, {'type': 'update_cell', 'target': cell, 'text': 'needle'})
    assert engine.find(doc, 'needle')['matches'][0]['target'] == cell


def test_delta_history_restores_every_revision_and_compaction_preserves_checkpoint():
    doc = new_document(text='0')
    target = doc['blocks'][0]['id']
    versions = [(doc['revision'], '0')]
    for i in range(1, 26):
        doc, _ = edit(doc, {'type': 'set_text', 'target': target, 'text': str(i)})
        versions.append((doc['revision'], str(i)))
        if i == 3:
            doc, _ = engine.checkpoint(doc, 'keep me')
    assert any('delta' in r for r in doc['history']['revisions'])
    for revision, text in versions:
        restored, _ = engine.restore(doc, revision)
        assert restored['blocks'][0]['text'] == text
    compacted, _ = edit(doc, {'type': 'compact_history', 'keep_last': 2})
    assert compacted['history']['last_compaction']['removed_revisions']
    restored, _ = engine.restore(compacted, 'keep me')
    assert restored['blocks'][0]['text'] == '3'
    assert len(doc['history']['revisions']) == 25


def test_proposal_own_policy_is_enforced_and_cell_preconditions():
    doc = new_document(text='keep')
    target = doc['blocks'][0]['id']
    proposed, result = edit(doc, {'type': 'propose', 'values': {'operations': [{'type': 'set_text', 'target': target, 'text': 'changed'}], 'protect': [{'target': target, 'kind': 'text'}]}})
    with pytest.raises(ByaxError) as error:
        edit(proposed, {'type': 'accept_proposal', 'target': result['changed_ids'][0]})
    assert error.value.code == 'protected_edit'
    doc, _ = edit(doc, {'type': 'insert_table', 'rows': 1, 'columns': 1})
    cell = doc['blocks'][-1]['rows'][0]['cells'][0]['id']
    doc, result = edit(doc, {'type': 'propose', 'values': {'operations': [{'type': 'update_cell', 'target': cell, 'text': 'proposed'}]}})
    proposal = result['changed_ids'][0]
    doc, _ = edit(doc, {'type': 'update_cell', 'target': cell, 'text': 'human'})
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'accept_proposal', 'target': proposal})
    assert error.value.code == 'stale_revision'


def test_locked_layer_can_be_unlocked_explicitly():
    doc, result = edit(new_document(), {'type': 'add_layer', 'values': {'locked': True}})
    identity = result['changed_ids'][0]
    with pytest.raises(ByaxError):
        edit(doc, {'type': 'update_layer', 'target': identity, 'values': {'name': 'forbidden'}})
    doc, _ = edit(doc, {'type': 'update_layer', 'target': identity, 'values': {'locked': False}})
    assert not doc['layers'][0]['locked']


def test_tracked_format_rejection_restores_formatting():
    doc = new_document(text='Review this')
    doc['settings']['track_changes'] = True
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'format_text', 'target': target, 'start': 0, 'end': 6, 'marks': {'bold': True}})
    change = doc['review']['tracked_changes'][0]
    assert change['kind'] == 'formatting'
    rejected, _ = edit(doc, {'type': 'reject_change', 'target': change['id']})
    assert rejected['blocks'][0]['marks'] == []
    assert rejected['blocks'][0]['text'] == 'Review this'


def test_bookmark_follows_split_join_and_deletion_reports_unresolved():
    from byax.checks import run_checks
    doc = new_document(text='Hello world')
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'set_bookmark', 'name': 'world', 'target': target, 'offset': 6})
    doc, _ = edit(doc, {'type': 'split_block', 'target': target, 'offset': 5})
    assert doc['settings']['bookmarks']['world'] == {'target': doc['blocks'][1]['id'], 'offset': 1}
    doc, _ = edit(doc, {'type': 'delete_block', 'target': doc['blocks'][1]['id']})
    assert any(f['rule'] == 'bookmark.orphaned' for f in run_checks(doc, ['structure'])['findings'])


def test_fill_fields_materializes_repeated_tokens_and_updates_marks():
    doc = new_document(text='Dear {{name}}, hi {{name}}. {{number}}')
    doc['blocks'][0]['marks'] = [{'start': 0, 'end': 4, 'type': 'bold', 'value': True}]
    doc['fields'] = {'name': {'type': 'text', 'required': True}, 'number': {'type': 'number'}}
    doc, _ = edit(doc, {'type': 'fill_fields', 'values': {'name': 'Author', 'number': 0}})
    assert doc['blocks'][0]['text'] == 'Dear Author, hi Author. 0'
    assert doc['fields']['name']['type'] == 'text'
    assert doc['blocks'][0]['marks'][0] == {'start': 0, 'end': 4, 'type': 'bold', 'value': True}
    doc, _ = edit(doc, {'type': 'fill_fields', 'values': {'name': 'X', 'number': 12}})
    assert doc['blocks'][0]['text'] == 'Dear X, hi X. 12'
    doc, _ = edit(doc, {'type': 'fill_fields', 'values': {'name': ''}})
    assert doc['blocks'][0]['text'] == 'Dear , hi . 12'
    doc, _ = edit(doc, {'type': 'fill_fields', 'values': {'name': 'Y'}})
    assert doc['blocks'][0]['text'] == 'Dear Y, hi Y. 12'


def test_engine_rejects_credential_embedding_before_commit():
    doc = new_document()
    with pytest.raises(ByaxError, match='Credentials'):
        edit(doc, {'type': 'set_metadata', 'values': {'api_key': 'do not save'}})
    assert 'api_key' not in doc['metadata']


@pytest.mark.parametrize('operation', ['remove_row', 'remove_column'])
def test_removed_table_cells_orphan_review_and_bookmarks(operation):
    doc, _ = edit(new_document(), {'type': 'insert_table', 'rows': 2, 'columns': 2})
    table = doc['blocks'][-1]
    cell = table['rows'][1]['cells'][1]['id']
    doc, _ = edit(doc,
        {'type': 'add_comment', 'target': cell, 'text': 'Comment'},
        {'type': 'set_bookmark', 'name': 'cell', 'target': cell},
        {'type': 'propose', 'values': {'operations': [{'type': 'update_cell', 'target': cell, 'text': 'Proposed'}]}})
    doc, _ = edit(doc, {'type': operation, 'target': table['id'], 'index': 1})
    assert doc['review']['comments'][0]['status'] == 'orphaned'
    assert doc['review']['proposals'][0]['status'] == 'orphaned'
    assert doc['settings']['bookmarks']['cell']['status'] == 'orphaned'


def test_move_section_preserves_subtree_anchors_and_comment_ids():
    doc = new_document(text='First\nbody one\nNested\nchild body\nSecond\nbody two\nThird\nbody three\nanchor')
    blocks = doc['blocks']
    for index, level in ((0, 1), (2, 2), (4, 1), (6, 1)):
        blocks[index]['type'] = 'heading'
        blocks[index]['attrs']['level'] = level
    original_ids = [b['id'] for b in blocks]
    blocks[-1]['attrs']['anchor'] = {'target': blocks[1]['id']}
    doc, result = edit(doc, {'type': 'add_comment', 'target': blocks[3]['id'], 'text': 'Nested section comment'})
    comment_id = result['changed_ids'][0]
    moved, _ = edit(doc, {'type': 'move_section', 'target': original_ids[0], 'after': original_ids[4]})
    assert [b['text'] for b in moved['blocks']] == ['Second', 'body two', 'First', 'body one', 'Nested', 'child body', 'anchor', 'Third', 'body three']
    assert {b['id'] for b in moved['blocks']} == set(original_ids)
    assert moved['review']['comments'][0]['id'] == comment_id
    assert moved['review']['comments'][0]['target'] == original_ids[3]
    with pytest.raises(ByaxError):
        edit(doc, {'type': 'move_section', 'target': original_ids[0], 'after': original_ids[2]})


@pytest.mark.parametrize('operation', ['reorder_row', 'reorder_column'])
def test_reorder_table_preserves_id_attributes_and_comment_anchors(operation):
    doc, _ = edit(new_document(), {'type': 'insert_table', 'rows': 3, 'columns': 3})
    table = doc['blocks'][-1]
    for row_index, row in enumerate(table['rows']):
        row['attrs'] = {'custom': row_index}
        for column_index, cell in enumerate(row['cells']):
            cell['text'] = f'{row_index}:{column_index}'
            cell['attrs'] = {'custom': cell['id']}
    table['attrs']['column_widths'] = [20, 30, 40]
    target = table['rows'][0]['cells'][0]['id']
    original = copy.deepcopy(table)
    doc, _ = edit(doc, {'type': 'add_comment', 'target': target, 'text': 'Stable'})
    moved, _ = edit(doc, {'type': operation, 'target': table['id'], 'index': 0, 'to_index': 2})
    result = moved['blocks'][-1]
    if operation == 'reorder_row':
        assert result['rows'][2] == original['rows'][0]
    else:
        assert result['rows'][0]['cells'][2] == original['rows'][0]['cells'][0]
        assert result['attrs']['column_widths'] == [30, 40, 20]
    assert moved['review']['comments'][0]['target'] == target
    assert moved['review']['comments'][0]['status'] == 'open'


def test_threaded_comment_reply_resolves_and_orphans_with_parent():
    doc = new_document(text='Discuss this')
    target = doc['blocks'][0]['id']
    doc, result = edit(doc, {'type': 'add_comment', 'target': target, 'text': 'First', 'author': 'A'})
    parent = result['changed_ids'][0]
    doc, result = edit(doc, {'type': 'reply_comment', 'target': parent, 'text': 'Reply', 'author': 'B'})
    reply_id = result['changed_ids'][-1]
    doc, _ = edit(doc, {'type': 'reply_comment', 'target': reply_id, 'text': 'Nested', 'author': 'A'})
    reply = doc['review']['comments'][0]['replies'][0]
    assert reply['parent_id'] == parent and reply['author'] == 'B'
    assert reply['replies'][0]['text'] == 'Nested'
    doc, _ = edit(doc, {'type': 'resolve_comment', 'target': reply_id})
    assert doc['review']['comments'][0]['replies'][0]['status'] == 'resolved'
    doc, _ = edit(doc, {'type': 'delete_block', 'target': target})
    assert doc['review']['comments'][0]['replies'][0]['replies'][0]['status'] == 'orphaned'


def _table_doc():
    doc = new_document(text='Intro')
    doc, _ = edit(doc, {'type': 'insert_table', 'rows': 2, 'columns': 2})
    return doc, next(b for b in doc['blocks'] if b['type'] == 'table')


def test_locks_cover_fields_replacement_and_table_contents():
    doc = new_document(text='Dear {{name}}')
    doc['blocks'][0]['attrs']['locked'] = True
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'fill_fields', 'values': {'name': 'Bob'}})
    assert error.value.code == 'protected_edit'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'replace_content', 'blocks': [{'id': 'p_new', 'type': 'paragraph', 'text': 'Gone', 'style': 'body', 'marks': [], 'attrs': {}}]})
    assert error.value.code == 'protected_edit'
    # Unchanged locked blocks may be carried through a replacement.
    kept = copy.deepcopy(doc['blocks'])
    changed, _ = edit(doc, {'type': 'replace_content', 'blocks': [*kept, {'id': 'p_new', 'type': 'paragraph', 'text': 'Added', 'style': 'body', 'marks': [], 'attrs': {}}]})
    assert changed['blocks'][0] == doc['blocks'][0]
    doc, table = _table_doc()
    table['attrs']['locked'] = True
    cell = table['rows'][0]['cells'][0]
    for op in ({'type': 'update_cell', 'target': cell['id'], 'text': 'changed'},
               {'type': 'remove_row', 'target': table['id'], 'index': 0}):
        with pytest.raises(ByaxError) as error:
            edit(doc, op)
        assert error.value.code == 'protected_edit'
    doc, table = _table_doc()
    table['rows'][1]['cells'][1]['attrs']['locked'] = True
    for op in ({'type': 'remove_row', 'target': table['id'], 'index': 1},
               {'type': 'remove_column', 'target': table['id'], 'index': 1}):
        with pytest.raises(ByaxError) as error:
            edit(doc, op)
        assert error.value.code == 'protected_edit'
    edit(doc, {'type': 'remove_row', 'target': table['id'], 'index': 0})


def test_column_widths_follow_added_and_removed_columns():
    doc, table = _table_doc()
    table['attrs']['column_widths'] = [100, 200]
    added, _ = edit(doc, {'type': 'add_column', 'target': table['id'], 'index': 1})
    assert next(b for b in added['blocks'] if b['id'] == table['id'])['attrs']['column_widths'] == [100, 150, 200]
    removed, _ = edit(doc, {'type': 'remove_column', 'target': table['id'], 'index': 0})
    assert next(b for b in removed['blocks'] if b['id'] == table['id'])['attrs']['column_widths'] == [200]


def test_replacing_text_does_not_spread_inner_formatting():
    doc = new_document(text='Hello world')
    target = doc['blocks'][0]['id']
    doc['blocks'][0]['marks'] = [{'type': 'bold', 'start': 6, 'end': 11, 'value': True}]
    changed, _ = edit(doc, {'type': 'set_text', 'target': target, 'text': 'Goodbye cruel world'})
    assert changed['blocks'][0]['marks'] == []
    # A mark covering the whole replaced range covers the replacement.
    changed, _ = edit(doc, {'type': 'replace_text', 'target': target, 'match': 'world', 'replacement': 'there'})
    assert [(m['start'], m['end']) for m in changed['blocks'][0]['marks']] == [(6, 11)]
    doc['blocks'][0]['marks'] = [{'type': 'bold', 'start': 0, 'end': 8, 'value': True}]
    changed, _ = edit(doc, {'type': 'replace_text', 'target': target, 'match': 'world', 'replacement': 'everyone'})
    assert [(m['start'], m['end']) for m in changed['blocks'][0]['marks']] == [(0, 6)]


def test_combining_insert_at_mark_edge_keeps_marks_on_grapheme_boundaries():
    doc = new_document(text='cafe x')
    target = doc['blocks'][0]['id']
    doc['blocks'][0]['marks'] = [{'type': 'bold', 'start': 0, 'end': 4, 'value': True}]
    changed, _ = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 4, 'text': '́'})
    assert changed['blocks'][0]['text'] == 'café x'
    assert [(m['start'], m['end']) for m in changed['blocks'][0]['marks']] == [(0, 5)]


def test_rejecting_tracked_insert_moves_comments_and_bookmarks_back():
    doc = new_document(text='Hi')
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'set_settings', 'values': {'track_changes': True}})
    doc, _ = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 2, 'text': ' there, friend'})
    doc, _ = edit(doc, {'type': 'add_comment', 'target': target, 'text': 'Note', 'start': 9, 'end': 15},
                  {'type': 'set_bookmark', 'name': 'end', 'target': target, 'offset': 16})
    change = doc['review']['tracked_changes'][-1]['id']
    rejected, _ = edit(doc, {'type': 'reject_change', 'target': change})
    assert rejected['blocks'][0]['text'] == 'Hi'
    comment = rejected['review']['comments'][-1]
    assert comment['end'] <= 2 and comment['anchor_status'] == 'needs_review'
    assert rejected['settings']['bookmarks']['end']['offset'] <= 2


def test_second_checkpoint_name_on_same_revision_is_rejected():
    doc = new_document(text='Hi')
    named, _ = engine.checkpoint(doc, 'A')
    with pytest.raises(ByaxError) as error:
        engine.checkpoint(named, 'B')
    assert error.value.code == 'conflict'
    assert [r.get('name') for r in named['history']['revisions'] if r.get('name')] == ['A']


def test_policy_cannot_be_disabled_by_settings_origin_or_restore():
    doc = new_document(text='Protected text')
    target = doc['blocks'][0]['id']
    initial = doc['revision']
    doc, _ = edit(doc, {'type': 'protect', 'target': target, 'kind': 'text'},
                  {'type': 'set_settings', 'values': {'track_changes': True}})
    for op in ({'type': 'set_settings', 'values': {'protections': []}},
               {'type': 'set_settings', 'values': {'requirements': {}}},
               {'type': 'set_settings', 'values': {'track_changes': False}},
               {'type': 'unprotect', 'target': target, 'kind': 'text'},
               {'type': 'set_tracking', 'enabled': False}):
        with pytest.raises(ByaxError) as error:
            engine.apply(doc, {'operations': [op], 'origin': 'gui'})
        assert error.value.code == 'protected_edit'
    with pytest.raises(ByaxError, match='protection'):
        engine.restore(doc, initial)
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'set_text', 'target': target, 'text': 'Hacked'})
    assert error.value.code == 'protected_edit'
    changed, _ = engine.apply(doc, {'origin': 'gui', 'operations': [
        {'type': 'unprotect', 'target': target, 'kind': 'text'},
        {'type': 'set_tracking', 'enabled': False}]}, allow_policy_changes=True)
    assert changed['settings']['protections'] == []
    assert not changed['settings']['track_changes']
    assert changed['history']['revisions'][-1]['origin'] == 'gui'
    assert changed['history']['revisions'][-1]['operations'][0]['type'] == 'unprotect'
    changed, _ = edit(changed, {'type': 'set_text', 'target': target, 'text': 'Authorized'})
    assert changed['blocks'][0]['text'] == 'Authorized'


def test_history_delta_omits_unchanged_order_and_reads_legacy_order():
    before = new_document(text='\n'.join('Paragraph' for _ in range(300)))
    after = copy.deepcopy(before)
    after['blocks'][0]['text'] += '!'
    delta = engine._delta(before, after)
    assert 'block_order' not in delta
    assert list(delta['blocks']) == [before['blocks'][0]['id']]
    assert engine._patch(copy.deepcopy(before), delta) == after
    legacy = dict(delta, block_order=[b['id'] for b in after['blocks']])
    assert engine._patch(copy.deepcopy(before), legacy) == after
    after['blocks'].reverse()
    structural = engine._delta(before, after)
    assert 'block_order' in structural
    assert engine._patch(copy.deepcopy(before), structural) == after


def test_api_history_cap_stays_hard_and_desktop_compaction_keeps_branch_heads():
    doc = new_document(text='0')
    target = doc['blocks'][0]['id']
    doc['settings']['history_limit'] = 2
    doc, _ = engine.branch(doc, 'retained branch')
    doc, _ = edit(doc, {'type': 'set_text', 'target': target, 'text': '1'})
    doc, _ = engine.branch(doc, 'active branch')
    for i in range(2, 4):
        doc, _ = engine.apply(doc, {'operations': [{'type': 'set_text', 'target': target, 'text': str(i)}]}, auto_compact_history=True)
    assert engine._revision(doc, 'retained branch')['blocks'][0]['text'] == '1'
    assert engine._revision(doc, 'active branch')['blocks'][0]['text'] == '3'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'set_text', 'target': target, 'text': '4'})
    assert error.value.code == 'limit_exceeded'
