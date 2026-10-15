"""Selective dependent proposals, semantic comparison, branch merge and request retention."""

import pytest

from byax import engine
from byax.errors import ByaxError
from byax.model import new_document
from byax.storage import load_document, save_document


def edit(doc, *ops, **envelope):
    return engine.apply(doc, {'operations': list(ops), **envelope})


def _proposal(doc, operations, **values):
    doc, result = edit(doc, {'type': 'propose', 'values': {'operations': operations, **values}})
    return doc, result['changed_ids'][0]


def _record(doc, identity):
    return next(p for p in doc['review']['proposals'] if p['id'] == identity)


def test_selected_edits_honor_inferred_dependencies_and_partial_status():
    doc = new_document(text='Intro\nConclusion')
    intro, conclusion = (b['id'] for b in doc['blocks'])
    doc, proposal = _proposal(
        doc,
        [
            {'type': 'insert_paragraph', 'text': 'New', 'after': intro, 'id': 'p_new'},
            {'type': 'set_text', 'target': 'p_new', 'text': 'New middle'},
            {'type': 'replace_text', 'target': conclusion, 'match': 'Conclusion', 'replacement': 'Summary'},
            {'type': 'insert_text', 'target': conclusion, 'offset': 7, 'text': '!'},
        ],
        explanation='Restructure',
        author='model-x',
    )
    edits = _record(doc, proposal)['edits']
    assert [e['depends_on'] for e in edits] == [[], ['edit_1'], [], ['edit_3']]
    assert edits[0]['required_by'] == ['edit_2']
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'accept_proposal', 'target': proposal, 'edits': ['edit_2']})
    assert error.value.code == 'conflict' and error.value.details['missing_dependencies'] == ['edit_1']
    doc, _ = edit(doc, {'type': 'accept_proposal', 'target': proposal, 'edits': ['edit_3']})
    assert doc['blocks'][1]['text'] == 'Summary' and _record(doc, proposal)['status'] == 'pending'
    # The accepted edit refreshed preconditions so its dependent can follow later.
    doc, _ = edit(doc, {'type': 'accept_proposal', 'target': proposal, 'edits': ['edit_4']})
    assert doc['blocks'][1]['text'] == 'Summary!'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'reject_proposal', 'target': proposal, 'edits': ['edit_1']})
    assert error.value.details['dependent_edits'] == ['edit_2']
    doc, _ = edit(doc, {'type': 'reject_proposal', 'target': proposal, 'edits': ['edit_1', 'edit_2']})
    record = _record(doc, proposal)
    assert record['status'] == 'partially_accepted'
    assert [e['status'] for e in record['edits']] == ['rejected', 'rejected', 'accepted', 'accepted']
    with pytest.raises(ByaxError):
        edit(doc, {'type': 'accept_proposal', 'target': proposal})


def test_explicit_dependencies_protection_checks_and_attribution_are_enforced():
    doc = new_document(text='Keep this\nChange this')
    keep, change = (b['id'] for b in doc['blocks'])
    doc, _ = edit(doc, {'type': 'set_settings', 'values': {'track_changes': True}})
    doc, proposal = _proposal(
        doc,
        [
            {'type': 'set_text', 'target': change, 'text': 'Changed'},
            {'type': 'set_text', 'target': keep, 'text': 'Kept?'},
        ],
        dependencies={'edit_2': ['edit_1']},
        protect=[{'target': keep, 'kind': 'text'}],
        author='model-y',
    )
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'accept_proposal', 'target': proposal, 'edits': ['edit_2']})
    assert error.value.details['missing_dependencies'] == ['edit_1']
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'accept_proposal', 'target': proposal})
    assert error.value.code == 'protected_edit' and _record(doc, proposal)['status'] == 'pending'
    doc, _ = edit(doc, {'type': 'accept_proposal', 'target': proposal, 'edits': ['edit_1']}, author='Editor')
    change_record = doc['review']['tracked_changes'][-1]
    assert (change_record['author'], change_record['proposal'], change_record['accepted_by']) == (
        'model-y',
        proposal,
        'Editor',
    )
    assert change_record['edit'] == 'edit_1'
    with pytest.raises(ByaxError) as error:
        _proposal(
            doc, [{'type': 'set_text', 'target': keep, 'text': 'x'}], dependencies={'edit_1': ['edit_1']}
        )
    assert error.value.code == 'schema_error'
    doc['settings']['requirements'] = {'stay': {'kind': 'required_text', 'value': 'Changed'}}
    doc, required = _proposal(
        doc, [{'type': 'set_text', 'target': change, 'text': 'Gone'}], required_suites=['requirements']
    )
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'accept_proposal', 'target': required, 'edits': ['edit_1']})
    assert error.value.code == 'failed_requirement'


def test_semantic_comparison_distinguishes_categories_without_pagination_noise():
    doc = new_document(text='Heading\nBody text here\nTail')
    heading, body, tail = (b['id'] for b in doc['blocks'])
    before = doc
    doc, _ = edit(
        doc,
        {'type': 'set_attributes', 'target': heading, 'values': {'level': 1}},
        {'type': 'replace_text', 'target': body, 'match': 'text', 'replacement': 'words'},
        {'type': 'apply_style', 'target': tail, 'style': 'caption'},
        {'type': 'set_attributes', 'target': tail, 'values': {'page_break_before': True}},
        {'type': 'move_block', 'target': tail, 'after': heading},
        {'type': 'insert_table', 'rows': 1, 'columns': 2},
        {'type': 'set_layout', 'values': {'width': 600}},
        {'type': 'define_style', 'name': 'caption', 'values': {'based_on': 'body', 'font_size': 8}},
    )
    # Source corrections are a separate action from document edits.
    doc, _ = edit(doc, {'type': 'add_reference', 'id': 'ref1', 'values': {'title': 'Source'}})
    report = engine.diff(doc, before['revision'])['semantic']
    assert report['wording'][0]['changes'] == [
        {'change': 'replace', 'before': 'text', 'after': 'words', 'before_start': 5, 'after_start': 5}
    ]
    assert report['structure']['moved'] == [tail] and len(report['structure']['added']) == 1
    assert report['structure']['type_changes'][0]['after']['type'] == 'heading'
    assert report['styles']['applied'] == [{'id': tail, 'before': 'body', 'after': 'caption'}]
    assert 'caption' in report['styles']['definitions']
    assert report['layout']['document']['width'] == {'before': 612, 'after': 600}
    assert report['layout']['blocks'][0]['key'] == 'page_break_before'
    assert report['references']['added'] == ['ref1']
    assert any('Moved 1 block' in line for line in report['summary'])
    table = next(b for b in doc['blocks'] if b['type'] == 'table')
    cell = table['rows'][0]['cells'][1]['id']
    changed, _ = edit(doc, {'type': 'update_cell', 'target': cell, 'text': '42'})
    tables = engine.compare_documents(doc, changed)['tables']
    assert tables[0]['cells'] == [{'id': cell, 'row': 0, 'column': 1, 'before': '', 'after': '42'}]
    _candidate, result = engine.apply(
        doc, {'operations': [{'type': 'set_layout', 'values': {'height': 700}}]}, dry_run=True
    )
    assert result['layout_effects']['layout_changes']['document']['height']['after'] == 700


def _branch_scenario():
    doc = new_document(text='Title\nShared opening\nMiddle\nClosing')
    title, opening, middle, closing = (b['id'] for b in doc['blocks'])
    doc, _ = engine.checkpoint(doc, 'start')
    doc, _ = engine.branch(doc, 'draft')
    doc, _ = engine.apply(
        doc,
        {
            'origin': 'gui',
            'operations': [{'type': 'set_text', 'target': middle, 'text': 'Middle from draft'}],
        },
    )
    doc, _ = engine.apply(
        doc,
        {
            'origin': 'cli',
            'request_id': 'cli-1',
            'operations': [
                {'type': 'insert_paragraph', 'text': 'Draft addition', 'after': middle, 'id': 'p_added'},
                {'type': 'set_attributes', 'target': closing, 'values': {'alignment': 'center'}},
            ],
        },
    )
    doc, _ = engine.switch_branch(doc, 'main')
    assert doc['blocks'][2]['text'] == 'Middle'
    doc, _ = edit(doc, {'type': 'set_text', 'target': opening, 'text': 'Main opening'})
    return doc, (title, opening, middle, closing)


def test_branch_merge_through_proposal_full_history_scenario(tmp_path):
    doc, (_title, _opening, middle, _closing) = _branch_scenario()
    doc, _ = edit(doc, {'type': 'add_comment', 'target': middle, 'text': 'Watch', 'start': 0, 'end': 6})
    doc, result = edit(doc, {'type': 'propose_merge', 'source': 'draft'})
    proposal = _record(doc, result['changed_ids'][0])
    assert proposal['kind'] == 'merge' and proposal['conflicts'] == []
    assert proposal['merge']['base_revision'] == engine.merge_base(doc, 'draft')
    assert {o['type'] for o in proposal['operations']} == {'set_block', 'insert_block'}
    preview = engine.preview_proposal(doc, proposal['id'])
    assert preview['semantic']['wording'] and preview['proposal']['edits']
    merged, _ = edit(doc, {'type': 'accept_proposal', 'target': proposal['id']})
    texts = [b['text'] for b in merged['blocks']]
    assert texts == ['Title', 'Main opening', 'Middle from draft', 'Draft addition', 'Closing']
    assert merged['blocks'][-1]['attrs']['alignment'] == 'center'
    history = merged['history']
    assert history['parents'][merged['revision']] == [doc['revision'], doc['history']['branches']['draft']]
    assert history['merges'][0]['source_branch'] == 'draft'
    # The merge appended to the commented passage; its anchor still covers the same words.
    comment = merged['review']['comments'][0]
    assert (comment['start'], comment['end'], comment.get('anchor_status')) == (0, 6, None)
    provenance = list(history['provenance'].values())
    assert [p['origin'] for p in provenance] == ['gui', 'cli', 'api', 'api', 'api', 'api']
    assert provenance[1]['request_id'] == 'cli-1' and provenance[1]['operations'] == [
        'insert_paragraph',
        'set_attributes',
    ]
    assert provenance[-1]['operations'] == ['accept_proposal']
    assert 'branch_switch' in [r.get('origin') for r in history['revisions']]
    semantic = engine.diff(merged, 'start')['semantic']
    assert semantic['structure']['added'][0]['id'] == 'p_added'
    path = tmp_path / 'merged.byax'
    save_document(merged, path)
    reopened = load_document(path)
    restored, _ = engine.restore(reopened, 'start')
    assert [b['text'] for b in restored['blocks']] == ['Title', 'Shared opening', 'Middle', 'Closing']


def test_conflicting_branch_edits_require_explicit_resolution():
    doc, (_title, _opening, middle, closing) = _branch_scenario()
    doc, _ = edit(
        doc,
        {'type': 'set_text', 'target': middle, 'text': 'Middle from main'},
        {'type': 'delete_block', 'target': closing},
    )
    doc, result = edit(doc, {'type': 'propose_merge', 'source': 'draft', 'author': 'Editor'})
    proposal = _record(doc, result['changed_ids'][0])
    kinds = {c['kind']: c for c in proposal['conflicts']}
    assert set(kinds) == {'block', 'block_deleted_in_ours'}
    assert (
        kinds['block']['fields'] == ['text+marks'] and kinds['block']['theirs']['text'] == 'Middle from draft'
    )
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'accept_proposal', 'target': proposal['id']})
    assert error.value.code == 'conflict' and len(error.value.details['conflicts']) == 2
    resolved, _ = edit(
        doc,
        {
            'type': 'resolve_conflict',
            'target': proposal['id'],
            'conflict': kinds['block']['id'],
            'choice': 'custom',
            'values': {**kinds['block']['ours'], 'text': 'Middle, reconciled'},
        },
        {
            'type': 'resolve_conflict',
            'target': proposal['id'],
            'conflict': kinds['block_deleted_in_ours']['id'],
            'choice': 'ours',
        },
    )
    with pytest.raises(ByaxError):
        edit(
            resolved,
            {
                'type': 'resolve_conflict',
                'target': proposal['id'],
                'conflict': kinds['block']['id'],
                'choice': 'ours',
            },
        )
    merged, _ = edit(resolved, {'type': 'accept_proposal', 'target': proposal['id']})
    assert [b['text'] for b in merged['blocks']] == [
        'Title',
        'Main opening',
        'Middle, reconciled',
        'Draft addition',
    ]
    theirs, _ = edit(
        doc,
        {
            'type': 'resolve_conflict',
            'target': proposal['id'],
            'conflict': kinds['block']['id'],
            'choice': 'theirs',
        },
        {
            'type': 'resolve_conflict',
            'target': proposal['id'],
            'conflict': kinds['block_deleted_in_ours']['id'],
            'choice': 'theirs',
        },
    )
    merged, _ = edit(theirs, {'type': 'accept_proposal', 'target': proposal['id']})
    assert [b['text'] for b in merged['blocks']][2:] == ['Middle from draft', 'Draft addition', 'Closing']
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'propose_merge', 'source': 'main'})
    assert error.value.code == 'conflict'


def test_merge_is_available_through_the_cli(tmp_path):
    from byax.cli import execute

    doc, _ = _branch_scenario()
    path = tmp_path / 'branches.byax'
    save_document(doc, path)
    proposed = execute(
        'apply', {'path': str(path), 'input': {'operations': [{'type': 'propose_merge', 'source': 'draft'}]}}
    )
    proposal = proposed['changed_ids'][0]
    accepted = execute(
        'apply',
        {'path': str(path), 'input': {'operations': [{'type': 'accept_proposal', 'target': proposal}]}},
    )
    assert accepted['execution'] == 'completed'
    switched = execute('history', {'path': str(path), 'action': 'switch_branch', 'name': 'draft'})
    assert switched['execution'] == 'completed'
    assert load_document(path)['history']['current_branch'] == 'draft'
    for section in ('requests', 'dependencies', 'lists', 'formatting', 'style_inheritance'):
        inspected = execute('inspect', {'path': str(path), 'section': section})
        assert inspected['execution'] == 'completed', section
    retention = execute('inspect', {'path': str(path), 'section': 'requests'})['items'][-1]['retention']
    assert retention['complete'] is True


def test_request_retention_keeps_replay_and_mismatch_across_boundaries(tmp_path):
    doc = new_document(text='0')
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'set_settings', 'values': {'request_retention': 2}})

    def request(doc, number, text=None):
        return engine.apply(
            doc,
            {
                'request_id': f'r{number}',
                'operations': [{'type': 'set_text', 'target': target, 'text': text or str(number)}],
            },
        )

    first_revision = None
    for number in range(1, 5):
        doc, result = request(doc, number)
        first_revision = first_revision or result['revision']
    assert engine.request_status(doc, 'r4')['state'] == 'recorded'
    status = engine.request_status(doc, 'r1')
    assert status['state'] == 'pruned' and status['committed_revision'] == first_revision
    replay_doc, replay = request(doc, 1)
    assert replay['execution'] == 'already_committed' and replay['result_available'] is False
    assert replay['revision'] == first_revision and replay_doc['revision'] == doc['revision']
    with pytest.raises(ByaxError) as error:
        request(doc, 1, 'different')
    assert error.value.code == 'conflict'
    # Reopen and compaction keep the replay records.
    compacted, _ = edit(doc, {'type': 'compact_history', 'keep_last': 1})
    path = tmp_path / 'requests.byax'
    save_document(compacted, path)
    reopened = load_document(path)
    assert request(reopened, 4)[1]['execution'] == 'already_committed'
    assert request(reopened, 1)[1]['result_available'] is False
    # Beyond the identity window the loss is disclosed instead of silently ignored.
    doc, _ = edit(reopened, {'type': 'set_settings', 'values': {'request_retention': 1}})
    for number in range(5, 18):
        doc, result = request(doc, number)
    assert (
        result['request_retention']['complete'] is False
        and result['request_retention']['forgotten_identities']
    )
    assert engine.request_status(doc, 'r1')['state'] == 'unknown'
    retention = engine.inspect(doc, section='requests', limit=1000)['items'][-1]['retention']
    assert retention['identities'] == 10 and not retention['complete']


def test_interrupted_unsaved_work_replays_once_after_reopen(tmp_path):
    doc = new_document(text='Draft')
    path = tmp_path / 'work.byax'
    save_document(doc, path)
    envelope = {
        'request_id': 'job-1',
        'operations': [{'type': 'insert_text', 'target': doc['blocks'][0]['id'], 'offset': 5, 'text': '!'}],
    }
    engine.apply(load_document(path), envelope)  # committed in memory, then the process stopped
    reopened = load_document(path)
    assert engine.request_status(reopened, 'job-1')['state'] == 'unknown'
    applied, result = engine.apply(reopened, envelope)
    assert result['execution'] == 'completed' and applied['blocks'][0]['text'] == 'Draft!'
    save_document(applied, path)
    again, result = engine.apply(load_document(path), envelope)
    assert result['execution'] == 'already_committed' and again['blocks'][0]['text'] == 'Draft!'


def test_ancestry_stays_bounded_after_compaction_and_keeps_merge_base():
    doc = new_document(text='0\nother')
    target, other = (b['id'] for b in doc['blocks'])
    doc['settings']['history_limit'] = 6
    doc, _ = engine.checkpoint(doc, 'base')
    doc, _ = engine.branch(doc, 'draft')
    doc, _ = edit(doc, {'type': 'set_text', 'target': other, 'text': 'draft change'})
    doc, _ = engine.switch_branch(doc, 'main')
    for number in range(1, 140):
        doc, _ = engine.apply(
            doc,
            {'operations': [{'type': 'set_text', 'target': target, 'text': str(number)}]},
            auto_compact_history=True,
        )
    history = doc['history']
    assert len(history['parents']) < 130 and len(history['provenance']) <= len(history['parents'])
    assert engine.merge_base(doc, 'draft') == engine._revision(doc, 'base')['revision']
    doc, result = engine.apply(
        doc, {'operations': [{'type': 'propose_merge', 'source': 'draft'}]}, auto_compact_history=True
    )
    merged, _ = engine.apply(
        doc,
        {'operations': [{'type': 'accept_proposal', 'target': result['changed_ids'][0]}]},
        auto_compact_history=True,
    )
    assert [b['text'] for b in merged['blocks']] == ['139', 'draft change']


def test_proposals_may_target_dependencies_and_review_records_but_not_unknown_ids():
    import base64

    doc = new_document(text='Body')
    target = doc['blocks'][0]['id']
    doc, result = edit(
        doc,
        {'type': 'add_comment', 'target': target, 'text': 'Fix'},
        {'type': 'link_asset', 'uri': 'pic.png', 'id': 'dep_pic', 'mime': 'image/png'},
    )
    comment = result['changed_ids'][0]
    data = base64.b64encode(b'png bytes').decode()
    doc, proposal = _proposal(
        doc,
        [
            {'type': 'refresh_dependency', 'target': 'dep_pic', 'data': data},
            {'type': 'resolve_comment', 'target': comment},
        ],
    )
    accepted, _ = edit(doc, {'type': 'accept_proposal', 'target': proposal})
    assert accepted['dependencies']['dep_pic']['status'] == 'current'
    assert accepted['review']['comments'][0]['status'] == 'resolved'
    with pytest.raises(ByaxError) as error:
        _proposal(doc, [{'type': 'set_text', 'target': 'p_missing', 'text': 'x'}])
    assert error.value.code == 'missing_target'


@pytest.mark.parametrize('automatic', [False, True])
def test_peer_base_survives_compaction_and_round_trip(tmp_path, automatic):
    doc = new_document(text='Shared base')
    base = doc['revision']
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'pin_merge_base', 'peer': 'offline-copy', 'revision': base})
    doc['settings']['history_limit'] = 4 if automatic else 100
    for index in range(12):
        doc, _ = engine.apply(
            doc,
            {
                'operations': [
                    {'type': 'set_text', 'target': target, 'text': f'Changed {index}'},
                ]
            },
            auto_compact_history=automatic,
        )
    if not automatic:
        doc, _ = edit(doc, {'type': 'compact_history', 'keep_last': 1})
    assert base in doc['history']['last_compaction']['pinned_revisions_retained']
    assert engine._revision(doc, base)['blocks'][0]['text'] == 'Shared base'
    path = tmp_path / 'shared.byax'
    save_document(doc, path)
    restored = load_document(path)
    assert restored['history']['peer_bases'] == {'offline-copy': base}
    assert engine._revision(restored, base)['blocks'][0]['text'] == 'Shared base'


def test_merge_base_does_not_guess_fork_when_graph_is_disconnected():
    doc, _ = _branch_scenario()
    doc['history']['parents'] = {}
    with pytest.raises(ByaxError, match='merge base is not recorded'):
        engine.merge_base(doc, 'draft')


def test_peer_pin_validation_and_atomicity():
    doc = new_document(text='Base')
    with pytest.raises(ByaxError):
        edit(doc, {'type': 'pin_merge_base', 'peer': 'copy', 'revision': 'missing'})
    assert 'peer_bases' not in doc['history']
    pinned, _ = edit(doc, {'type': 'pin_merge_base', 'peer': 'copy', 'revision': doc['revision']})
    unpinned, _ = edit(pinned, {'type': 'unpin_merge_base', 'peer': 'copy'})
    assert pinned['history']['peer_bases'] == {'copy': doc['revision']}
    assert unpinned['history']['peer_bases'] == {}


def test_unpinned_removed_common_base_fails_instead_of_merging_wrong_state():
    doc = new_document(text='Shared original')
    base = doc['revision']
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'set_text', 'target': target, 'text': 'First edit'})
    doc, _ = edit(doc, {'type': 'set_text', 'target': target, 'text': 'Second edit'})
    doc, _ = edit(doc, {'type': 'compact_history', 'keep_last': 1})
    with pytest.raises(ByaxError, match='not in retained history'):
        engine._revision(doc, base)


def test_same_paragraph_disjoint_edits_merge_through_review_with_marks():
    doc = new_document(text='First sentence. Last sentence.')
    target = doc['blocks'][0]['id']
    doc, _ = edit(
        doc, {'type': 'format_text', 'target': target, 'start': 0, 'end': 5, 'marks': {'bold': True}}
    )
    doc, _ = engine.branch(doc, 'draft')
    doc, _ = edit(
        doc,
        {'type': 'replace_text', 'target': target, 'match': 'Last sentence', 'replacement': 'Last phrase'},
    )
    doc, _ = engine.switch_branch(doc, 'main')
    doc, _ = edit(
        doc,
        {
            'type': 'replace_text',
            'target': target,
            'match': 'sentence',
            'replacement': 'phrase',
            'occurrence': 1,
        },
    )
    doc, result = edit(doc, {'type': 'propose_merge', 'source': 'draft'})
    proposal = _record(doc, result['changed_ids'][0])
    assert not proposal['conflicts']
    merged, _ = edit(doc, {'type': 'accept_proposal', 'target': proposal['id']})
    assert merged['blocks'][0]['text'] == 'First phrase. Last phrase.'
    assert merged['blocks'][0]['marks'] == doc['blocks'][0]['marks']
