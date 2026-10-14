"""Review-anchor remapping and grouped, attributed tracked changes (spec 11.2, 16.5)."""

import pytest

from byax import engine
from byax.errors import ByaxError
from byax.model import new_document
from byax.storage import load_document, save_document


def edit(doc, *ops, **envelope):
    return engine.apply(doc, {'operations': list(ops), **envelope})


def _comment(doc, target, start, end, text='Note'):
    doc, result = edit(
        doc, {'type': 'add_comment', 'target': target, 'text': text, 'start': start, 'end': end}
    )
    return doc, result['changed_ids'][0]


def _find(doc, identity):
    pending = list(doc['review']['comments'])
    while pending:
        item = pending.pop()
        if item['id'] == identity:
            return item
        pending.extend(item.get('replies', []))
    raise KeyError(identity)


def test_passage_comments_follow_insertions_and_report_unresolved_deletions():
    doc = new_document(text='Hello brave new world')
    target = doc['blocks'][0]['id']
    doc, comment = _comment(doc, target, 6, 11)  # "brave"
    doc, result = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'Oh, '})
    item = _find(doc, comment)
    assert (item['start'], item['end']) == (10, 15) and doc['blocks'][0]['text'][10:15] == 'brave'
    assert result['anchor_mappings'][0]['from'] == {'target': target, 'start': 6, 'end': 11}
    doc, _ = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 12, 'text': 'XX'})
    item = _find(doc, comment)
    assert doc['blocks'][0]['text'][item['start'] : item['end']] == 'brXXave'
    assert 'anchor_status' not in item
    doc, result = edit(doc, {'type': 'delete_text', 'target': target, 'start': 8, 'end': 12})
    assert _find(doc, comment)['anchor_status'] == 'needs_review'
    doc, result = edit(doc, {'type': 'delete_text', 'target': target, 'start': 0, 'end': 13})
    item = _find(doc, comment)
    assert item['anchor_status'] == 'unresolved' and item['start'] == item['end'] == 0
    assert result['anchor_mappings'][0]['status'] == 'unresolved'


def test_split_and_join_remap_comments_replies_and_bookmarks_with_identity_report():
    doc = new_document(text='First part. Second part.')
    target = doc['blocks'][0]['id']
    doc, later = _comment(doc, target, 12, 18)  # "Second"
    doc, spanning = _comment(doc, target, 6, 16)
    doc, reply = edit(doc, {'type': 'reply_comment', 'target': later, 'text': 'Agreed'})
    reply_id = reply['changed_ids'][-1]
    doc, _ = edit(doc, {'type': 'set_bookmark', 'name': 'tail', 'target': target, 'offset': 19})
    doc, result = edit(doc, {'type': 'split_block', 'target': target, 'offset': 12, 'id': 'p_second'})
    assert result['identity_changes'] == [
        {'operation': 'split_block', 'source': target, 'created': 'p_second', 'offset': 12}
    ]
    moved = _find(doc, later)
    assert (moved['target'], moved['start'], moved['end']) == ('p_second', 0, 6)
    assert _find(doc, reply_id)['target'] == 'p_second'
    cut = _find(doc, spanning)
    assert (cut['target'], cut['end'], cut['anchor_status'], cut['continues_in']) == (
        target,
        12,
        'needs_review',
        'p_second',
    )
    assert doc['settings']['bookmarks']['tail'] == {'target': 'p_second', 'offset': 7}
    kinds = {(m['kind'], m['status']) for m in result['anchor_mappings']}
    assert {('comment', 'moved'), ('comment', 'needs_review'), ('bookmark', 'moved')} <= kinds
    doc, result = edit(doc, {'type': 'join_blocks', 'target': target, 'next': 'p_second'})
    assert result['identity_changes'][0]['removed'] == 'p_second'
    back = _find(doc, later)
    assert (back['target'], back['start'], back['end']) == (target, 12, 18)
    assert _find(doc, reply_id)['target'] == target
    assert 'continues_in' not in _find(doc, spanning)
    assert doc['settings']['bookmarks']['tail'] == {'target': target, 'offset': 19}


def test_section_move_reports_anchors_and_deletion_orphans_threads():
    doc = new_document(text='One\nbody\nTwo\nmore')
    for index in (0, 2):
        doc['blocks'][index]['type'] = 'heading'
        doc['blocks'][index]['attrs']['level'] = 1
    ids = [b['id'] for b in doc['blocks']]
    doc, comment = _comment(doc, ids[1], 0, 4)
    doc, _ = edit(doc, {'type': 'set_bookmark', 'name': 'b', 'target': ids[1], 'offset': 2})
    doc, result = edit(doc, {'type': 'move_section', 'target': ids[0], 'after': ids[2]})
    statuses = {(m['kind'], m['status']) for m in result['anchor_mappings']}
    assert statuses == {('comment', 'moved_with_section'), ('bookmark', 'moved_with_section')}
    assert _find(doc, comment)['target'] == ids[1] and [b['id'] for b in doc['blocks']][2:] == ids[:2]
    doc, reply = edit(doc, {'type': 'reply_comment', 'target': comment, 'text': 'Reply'})
    doc, result = edit(doc, {'type': 'delete_block', 'target': ids[1]})
    assert _find(doc, reply['changed_ids'][-1])['status'] == 'orphaned'
    assert doc['settings']['bookmarks']['b']['status'] == 'orphaned'
    assert {m['status'] for m in result['anchor_mappings']} == {'orphaned'}


def test_proposal_ranges_remap_through_edits_split_and_join_then_accept():
    doc = new_document(text='Hello wonderful world')
    target = doc['blocks'][0]['id']
    doc, result = edit(
        doc,
        {
            'type': 'propose',
            'values': {'operations': [{'type': 'delete_text', 'target': target, 'start': 6, 'end': 16}]},
        },
    )
    proposal = result['changed_ids'][0]
    doc, result = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'Oh! '})
    record = doc['review']['proposals'][0]
    assert record['operations'][0]['start'] == 10 and record['edits'][0]['state'] == 'remapped'
    assert result['anchor_mappings'][0]['kind'] == 'proposal_edit'
    doc, _ = edit(doc, {'type': 'split_block', 'target': target, 'offset': 4, 'id': 'p_tail'})
    assert doc['review']['proposals'][0]['operations'][0] == {
        'type': 'delete_text',
        'target': 'p_tail',
        'start': 6,
        'end': 16,
    }
    preview = engine.preview_proposal(doc, proposal)
    assert preview['semantic']['wording'][0]['changes'][0]['before'] == 'wonderful '
    accepted, _ = edit(doc, {'type': 'accept_proposal', 'target': proposal})
    assert accepted['blocks'][1]['text'] == 'Hello world'
    # Joining moves the anchor back; overlapping edits make the proposal stale.
    joined, _ = edit(doc, {'type': 'join_blocks', 'target': target, 'next': 'p_tail'})
    assert joined['review']['proposals'][0]['operations'][0]['target'] == target
    overlapped, _ = edit(joined, {'type': 'insert_text', 'target': target, 'offset': 12, 'text': '!'})
    assert overlapped['review']['proposals'][0]['edits'][0]['state'] == 'unresolved'
    with pytest.raises(ByaxError) as error:
        edit(overlapped, {'type': 'accept_proposal', 'target': proposal})
    assert error.value.code == 'stale_revision'


def test_mixed_grapheme_review_state_survives_save_and_reopen(tmp_path):
    doc = new_document(text='Café 👩‍💻 naïve')
    target = doc['blocks'][0]['id']
    doc, comment = _comment(doc, target, 6, 9)  # the ZWJ emoji sequence
    doc, _ = edit(doc, {'type': 'set_settings', 'values': {'track_changes': True}})
    doc, _result = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 0, 'text': '🇨🇦 '})
    item = _find(doc, comment)
    assert doc['blocks'][0]['text'][item['start'] : item['end']] == '👩‍💻'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'insert_text', 'target': target, 'offset': 1, 'text': 'x'})
    assert error.value.details['valid_alternatives'] == [0, 2]
    change = doc['review']['tracked_changes'][0]
    assert (change['start'], change['end'], change['inserted']) == (0, 3, '🇨🇦 ')
    path = tmp_path / 'review.byax'
    save_document(doc, path)
    reopened = load_document(path)
    assert reopened['review'] == doc['review']
    rejected, _ = edit(reopened, {'type': 'reject_change', 'target': change['id']})
    assert rejected['blocks'][0]['text'] == 'Café 👩‍💻 naïve'
    item = _find(rejected, comment)
    assert (item['start'], item['end']) == (6, 9)


def test_rejecting_one_tracked_change_keeps_unrelated_later_edits_and_marks():
    doc = new_document(text='Hello world')
    target = doc['blocks'][0]['id']
    doc['blocks'][0]['marks'] = [{'type': 'bold', 'start': 6, 'end': 11, 'value': True}]
    doc, _ = edit(doc, {'type': 'set_settings', 'values': {'track_changes': True}})
    doc, _ = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 5, 'text': ' there'}, author='Ann')
    doc, _ = edit(doc, {'type': 'delete_text', 'target': target, 'start': 11, 'end': 17}, author='Bob')
    doc, _ = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'Oh, '}, author='Cy')
    assert doc['blocks'][0]['text'] == 'Oh, Hello there'
    insertion, deletion, _prefix = doc['review']['tracked_changes']
    assert (insertion['kind'], insertion['author'], insertion['origin']) == ('insertion', 'Ann', 'api')
    assert insertion['timestamp'] and deletion['kind'] == 'deletion' and deletion['deleted'] == ' world'
    # Rejecting the deletion restores text and its formatting in place, keeping later edits.
    doc, _ = edit(doc, {'type': 'reject_change', 'target': deletion['id']})
    assert doc['blocks'][0]['text'] == 'Oh, Hello there world'
    assert {(m['start'], m['end']) for m in doc['blocks'][0]['marks'] if m['type'] == 'bold'} == {(16, 21)}
    doc, _ = edit(doc, {'type': 'reject_change', 'target': insertion['id']})
    assert doc['blocks'][0]['text'] == 'Oh, Hello world'
    assert doc['review']['tracked_changes'][2]['status'] == 'pending'
    assert doc['review']['tracked_changes'][0]['resolution']['origin'] == 'api'


def test_grouped_accept_reject_by_author_kind_and_identity_is_atomic():
    doc = new_document(text='alpha beta gamma')
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'set_settings', 'values': {'track_changes': True}})
    doc, _ = edit(
        doc, {'type': 'replace_text', 'target': target, 'match': 'alpha', 'replacement': 'ALPHA'}, author='A'
    )
    doc, _ = edit(
        doc, {'type': 'replace_text', 'target': target, 'match': 'gamma', 'replacement': 'GAMMA'}, author='B'
    )
    doc, _ = edit(
        doc,
        {'type': 'format_text', 'target': target, 'start': 6, 'end': 10, 'marks': {'italic': True}},
        author='A',
    )
    doc, _ = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 6, 'text': 'big '}, author='B')
    changes = doc['review']['tracked_changes']
    assert [c['kind'] for c in changes] == ['replacement', 'replacement', 'formatting', 'insertion']
    # Formatting marks were remapped through the later insertion.
    assert changes[2]['added_marks'][0]['start'] == 10
    rejected, _result = edit(doc, {'type': 'reject_changes', 'author': 'A'})
    assert rejected['blocks'][0]['text'] == 'alpha big beta GAMMA'
    assert not any(m['type'] == 'italic' for m in rejected['blocks'][0]['marks'])
    assert [c['status'] for c in rejected['review']['tracked_changes']] == [
        'rejected',
        'pending',
        'rejected',
        'pending',
    ]
    accepted, _ = edit(rejected, {'type': 'accept_changes', 'kind': 'text'})
    assert {c['status'] for c in accepted['review']['tracked_changes']} == {'accepted', 'rejected'}
    # One conflicting change in a selected group commits nothing.
    conflicted, _ = edit(doc, {'type': 'set_text', 'target': target, 'text': 'replaced entirely'}, author='C')
    before = conflicted['blocks'][0]['text']
    with pytest.raises(ByaxError) as error:
        edit(conflicted, {'type': 'reject_changes', 'targets': [changes[0]['id'], changes[1]['id']]})
    assert error.value.code == 'conflict' and conflicted['blocks'][0]['text'] == before
    with pytest.raises(ByaxError) as error:
        edit(rejected, {'type': 'accept_changes', 'targets': [changes[0]['id']]})
    assert error.value.code == 'conflict'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'accept_changes'})
    assert error.value.code == 'schema_error'


def test_split_inside_tracked_insertion_creates_grouped_halves():
    doc = new_document(text='Start end')
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'set_settings', 'values': {'track_changes': True}})
    doc, _ = edit(doc, {'type': 'insert_text', 'target': target, 'offset': 6, 'text': 'middle part '})
    doc, _ = edit(doc, {'type': 'split_block', 'target': target, 'offset': 13, 'id': 'p_next'})
    first, second = doc['review']['tracked_changes']
    assert (first['inserted'], second['inserted'], second['target']) == ('middle ', 'part ', 'p_next')
    assert second['group'] == first['group'] == first['id']
    doc, _ = edit(doc, {'type': 'reject_changes', 'group': first['id']})
    assert [b['text'] for b in doc['blocks']] == ['Start ', 'end']
