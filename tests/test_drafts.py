"""Named drafts (CP A4): save, switch, scene-level compare and bring changes as a proposal."""

import copy
import json

import pytest

from byax import drafts, engine, project
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document, validate_document
from byax.storage import load_document, save_document


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
        _h('ch1', 'Chapter one', 1),
        _h('s1', 'Arrival', 2),
        _p('s1a', 'The boat came in on the evening tide.'),
        _h('s2', 'The keeper', 2),
        _p('s2a', 'He did not look up.'),
        _h('ch2', 'Chapter two', 1),
        _h('s3', 'Night', 2),
        _p('s3a', 'The lamp failed at midnight.'),
    ]
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    return doc


def apply(doc, *operations):
    return engine.apply(doc, {'expected_revision': doc['revision'], 'operations': list(operations)})[0]


def by_title(comparison):
    return {entry['title']: entry for entry in comparison['items']}


def test_saving_a_draft_freezes_the_state_and_keeps_working_on_the_current_draft():
    doc = manuscript()
    saved, result = drafts.save_draft(doc, 'First draft', 'Before the rewrite')
    assert result['execution'] == 'completed' and validate_document(saved)
    assert saved['history']['current_branch'] == 'main'
    names = {entry['name']: entry for entry in drafts.list_drafts(saved)}
    assert list(names) == ['main', 'First draft']
    assert names['First draft']['note'] == 'Before the rewrite' and names['First draft']['saved_at']
    assert names['main']['current'] and not names['First draft']['current']
    edited = apply(saved, {'type': 'insert_text', 'target': 's1a', 'offset': 0, 'text': 'Dawn. '})
    listing = {entry['name']: entry for entry in drafts.list_drafts(edited)}
    assert listing['First draft']['words'] + 1 == listing['main']['words']
    state = drafts.draft_document(edited, 'First draft')
    assert next(b for b in state['blocks'] if b['id'] == 's1a')['text'].startswith('The boat')
    # The original document was not modified.
    assert 'drafts' not in doc['history']


def test_malformed_draft_records_are_rejected_by_validation():
    saved, _ = drafts.save_draft(manuscript(), 'Keep')
    for bad in ([], {'Keep': 'note'}, {'Keep': {'note': 5}}, {1: {}}):
        broken = copy.deepcopy(saved)
        broken['history']['drafts'] = bad
        with pytest.raises(ByaxError):
            validate_document(broken)
    assert validate_document(saved)


def test_a_draft_whose_head_left_retained_history_is_listed_not_hidden():
    saved, _ = drafts.save_draft(manuscript(), 'Keep')
    saved['history']['branches']['Keep'] = 'rev_gone'
    entry = {d['name']: d for d in drafts.list_drafts(saved)}['Keep']
    assert entry['words'] is None and entry['revision'] == 'rev_gone'
    with pytest.raises(ByaxError):
        drafts.compare_drafts(saved, 'Keep')


def test_save_validates_names_and_notes_and_can_switch():
    doc = manuscript()
    for bad in ('', '   ', None, 'x' * 81, 'two\nlines'):
        with pytest.raises(ByaxError):
            drafts.save_draft(doc, bad)
    with pytest.raises(ByaxError):
        drafts.save_draft(doc, 'ok', 'n' * 2001)
    saved, _ = drafts.save_draft(doc, 'Keep')
    with pytest.raises(ByaxError) as caught:
        drafts.save_draft(saved, 'Keep')
    assert caught.value.code == 'conflict'
    with pytest.raises(ByaxError):
        drafts.save_draft(saved, 'main')
    switched, _ = drafts.save_draft(doc, 'Second', switch=True)
    assert switched['history']['current_branch'] == 'Second'
    assert drafts.current_draft(switched) == 'Second'


def test_switching_between_saved_drafts_restores_their_text():
    doc = manuscript()
    saved, _ = drafts.save_draft(doc, 'Calm')
    edited = apply(saved, {'type': 'set_text', 'target': 's2a', 'text': 'He ran.'})
    back, _ = drafts.switch_draft(edited, 'Calm')
    assert next(b for b in back['blocks'] if b['id'] == 's2a')['text'] == 'He did not look up.'
    assert back['history']['current_branch'] == 'Calm'
    again, _ = drafts.switch_draft(back, 'main')
    assert next(b for b in again['blocks'] if b['id'] == 's2a')['text'] == 'He ran.'
    with pytest.raises(ByaxError):
        drafts.switch_draft(again, 'missing')


def test_compare_reports_each_scene_as_unchanged_changed_added_or_removed_with_word_deltas():
    doc = manuscript()
    saved, _ = drafts.save_draft(doc, 'First draft')
    ids = {row['title']: row['id'] for row in project.items(saved)}
    edited = apply(
        saved,
        {'type': 'insert_text', 'target': 's1a', 'offset': 0, 'text': 'At dusk, '},
        {'type': 'project_remove_item', 'target': ids['The keeper'], 'delete_content': True},
        {'type': 'project_add_item', 'kind': 'scene', 'text': 'Storm', 'parent': ids['Chapter two']},
        {'type': 'project_set_status', 'target': ids['Night'], 'status': 'revised'},
    )
    comparison = drafts.compare_drafts(edited, 'First draft')
    scenes = by_title(comparison)
    assert comparison['from'] == 'First draft' and comparison['to'] == 'main' and comparison['binder']
    assert scenes['Arrival']['status'] == 'changed' and scenes['Arrival']['word_delta'] == 2
    assert scenes['Arrival']['edits'] >= 1 and scenes['Arrival']['summary']
    assert scenes['The keeper']['status'] == 'removed' and scenes['The keeper']['word_delta'] == -7
    assert scenes['Storm']['status'] == 'added' and scenes['Storm']['words_before'] == 0
    assert scenes['Night']['status'] == 'changed' and scenes['Night']['fields'] == ['status']
    assert scenes['Night']['word_delta'] == 0
    assert scenes['Chapter one']['status'] == 'unchanged' and scenes['Chapter two']['status'] == 'unchanged'
    summary = comparison['summary']
    assert (summary['changed'], summary['added'], summary['removed'], summary['unchanged']) == (2, 1, 1, 2)
    assert summary['word_delta'] == summary['words_after'] - summary['words_before']
    # Removed items stay in the order they had, between their neighbours.
    order = [entry['title'] for entry in comparison['items']]
    assert order.index('Arrival') < order.index('The keeper') < order.index('Chapter two')
    # The other direction reverses added and removed.
    reverse = by_title(drafts.compare_drafts(edited, 'main', 'First draft'))
    assert reverse['Storm']['status'] == 'removed' and reverse['The keeper']['status'] == 'added'
    with pytest.raises(ByaxError):
        drafts.compare_drafts(edited, 'main', 'main')
    with pytest.raises(ByaxError):
        drafts.compare_drafts(edited, 'missing')


def test_compare_notices_moved_scenes_and_text_outside_the_binder():
    doc = manuscript()
    doc['blocks'].insert(0, _p('front', 'For the readers.'))
    saved, _ = drafts.save_draft(doc, 'Order one')
    ids = {row['title']: row['id'] for row in project.items(saved)}
    edited = apply(
        saved,
        {'type': 'project_move', 'target': ids['Chapter two'], 'before': ids['Chapter one']},
        {'type': 'insert_text', 'target': 'front', 'offset': 0, 'text': 'Dedication. '},
    )
    comparison = drafts.compare_drafts(edited, 'Order one')
    scenes = by_title(comparison)
    assert scenes['Chapter two']['status'] == 'changed' and scenes['Chapter two']['moved'] is True
    assert scenes['Chapter one']['status'] == 'unchanged' or scenes['Chapter one']['moved'] is True
    assert scenes['Text outside the binder']['status'] == 'changed'
    assert scenes['Text outside the binder']['id'] is None


def test_compare_without_a_binder_falls_back_to_the_document_summary():
    doc = new_document(text='One two three')
    saved, _ = drafts.save_draft(doc, 'Plain')
    edited = apply(
        saved, {'type': 'insert_text', 'target': saved['blocks'][0]['id'], 'offset': 0, 'text': 'So '}
    )
    comparison = drafts.compare_drafts(edited, 'Plain')
    assert comparison['binder'] is False and comparison['items'] == []
    assert comparison['summary']['word_delta'] == 1 and comparison['document']


def test_bring_changes_is_a_reviewable_proposal_and_changes_nothing_until_accepted():
    doc = manuscript()
    alternate, _ = drafts.save_draft(doc, 'Alternate', switch=True)
    alternate = apply(alternate, {'type': 'set_text', 'target': 's3a', 'text': 'The lamp stayed lit.'})
    main, _ = drafts.switch_draft(alternate, 'main')
    operation = drafts.bring_operation(main, 'Alternate', 'Try the lit lamp')
    assert operation == {'type': 'propose_merge', 'source': 'Alternate', 'explanation': 'Try the lit lamp'}
    proposed, result = engine.apply(main, {'operations': [operation]})
    assert result['review'] == 'required'
    assert next(b for b in proposed['blocks'] if b['id'] == 's3a')['text'] == 'The lamp failed at midnight.'
    proposal = proposed['review']['proposals'][-1]
    accepted = apply(proposed, {'type': 'accept_proposal', 'target': proposal['id']})
    assert next(b for b in accepted['blocks'] if b['id'] == 's3a')['text'] == 'The lamp stayed lit.'
    with pytest.raises(ByaxError):
        drafts.bring_operation(main, 'main')
    with pytest.raises(ByaxError):
        drafts.bring_operation(main, 'missing')


def test_drafts_survive_save_and_reload_and_history_compaction(tmp_path):
    doc = manuscript()
    saved, _ = drafts.save_draft(doc, 'Keep me', 'a note')
    for index in range(6):
        saved = apply(saved, {'type': 'insert_text', 'target': 's1a', 'offset': 0, 'text': f'{index} '})
    saved['settings']['history_limit'] = 3
    saved, _ = engine.apply(
        saved,
        {'operations': [{'type': 'insert_text', 'target': 's1a', 'offset': 0, 'text': 'x'}]},
        auto_compact_history=True,
    )
    path = tmp_path / 'novel.byax'
    save_document(saved, path)
    reloaded = load_document(path)
    entry = {d['name']: d for d in drafts.list_drafts(reloaded)}['Keep me']
    assert entry['note'] == 'a note'
    state = drafts.draft_document(reloaded, 'Keep me')
    assert (
        next(b for b in state['blocks'] if b['id'] == 's1a')['text']
        == 'The boat came in on the evening tide.'
    )


def test_cli_drafts_list_save_switch_compare_and_bring(tmp_path, capsys):
    path = tmp_path / 'novel.byax'
    save_document(manuscript(), path)
    base = {'path': str(path)}
    listing = execute('drafts', base)
    assert listing['current'] == 'main' and [d['name'] for d in listing['drafts']] == ['main']
    saved = execute('drafts', {**base, 'action': 'save', 'name': 'First', 'note': 'Start'})
    assert [d['name'] for d in saved['drafts']] == ['main', 'First']
    assert load_document(path)['history']['drafts']['First']['note'] == 'Start'
    with pytest.raises(ByaxError) as caught:
        execute('drafts', {**base, 'action': 'save', 'name': 'Late', 'expect_revision': 'rev_stale'})
    assert caught.value.code == 'stale_revision'
    execute(
        'apply',
        {
            'path': str(path),
            'input': {'operations': [{'type': 'set_text', 'target': 's2a', 'text': 'He nodded.'}]},
        },
    )
    comparison = execute('drafts', {**base, 'action': 'compare', 'name': 'First'})['comparison']
    assert by_title(comparison)['The keeper']['status'] == 'changed'
    switched = execute('drafts', {**base, 'action': 'switch', 'name': 'First'})
    assert switched['drafts'][0]['name'] == 'First' and switched['drafts'][0]['current']
    assert load_document(path)['history']['current_branch'] == 'First'
    brought = execute('drafts', {**base, 'action': 'bring', 'name': 'main', 'note': 'Take the nod'})
    assert brought['review'] == 'required'
    assert load_document(path)['review']['proposals'][-1]['explanation'] == 'Take the nod'
    dry = execute('drafts', {**base, 'action': 'save', 'name': 'Dry', 'dry_run': True})
    assert 'Dry' in [d['name'] for d in dry['drafts']]
    assert 'Dry' not in load_document(path)['history']['branches']
    for arguments in (
        {'action': 'nope'},
        {'action': 'save'},
        {'action': 'compare'},
        {'action': 'switch', 'name': 'missing'},
    ):
        with pytest.raises(ByaxError):
            execute('drafts', {**base, **arguments})
    assert main(['drafts', str(path), '--action', 'list']) == 0
    assert json.loads(capsys.readouterr().out)['current'] == 'First'


def test_drafts_are_available_over_mcp(tmp_path):
    path = tmp_path / 'novel.byax'
    save_document(manuscript(), path)

    def call(arguments):
        return handle(
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'byax_drafts', 'arguments': {'path': str(path), **arguments}},
            }
        )['result']

    saved = call({'action': 'save', 'name': 'Via MCP'})
    assert saved['isError'] is False
    listing = call({'action': 'list'})['structuredContent']
    assert [d['name'] for d in listing['drafts']] == ['main', 'Via MCP']
