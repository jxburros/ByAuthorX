"""Claim-level provenance: recording, remapping, claims, report, schema, CLI/MCP and transport policy."""

import copy
import json

import pytest

from byax import engine, migration, provenance, storage
from byax.cli import execute
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document, validate_document
from byax.transport_policy import check_provider_admin


def _doc(text='Hello world'):
    doc = new_document('Provenance', text)
    return doc, doc['blocks'][0]['id']


def _apply(doc, operations, origin=None, **envelope):
    request = {'operations': operations, **envelope}
    if origin:
        request['provenance'] = {'origin': origin} if isinstance(origin, str) else origin
    return engine.apply(doc, request)[0]


def _origins(doc, target):
    """Per-character origin list for one object (None where unrecorded)."""
    text = next(b for b in doc['blocks'] if b['id'] == target)['text']
    result = [None] * len(text)
    log = doc.get('provenance') or {}
    for start, end, rid in log.get('spans', {}).get(target, []):
        for i in range(start, end):
            result[i] = log['records'][rid]['origin']
    return result


def _ai_proposal(doc, operations, model='llama3.1:8b', **extra):
    proposal = {'operations': operations, 'provenance': {'provider': 'local', 'model': model}, **extra}
    doc = _apply(doc, [{'type': 'propose', 'values': proposal}])
    return doc, doc['review']['proposals'][-1]['id']


def test_typing_is_recorded_as_human_and_adjacent_spans_merge():
    doc, b = _doc()
    doc = _apply(doc, [{'type': 'insert_text', 'target': b, 'offset': 5, 'text': ' brave'}], 'human')
    doc = _apply(doc, [{'type': 'insert_text', 'target': b, 'offset': 11, 'text': ' new'}], 'human')
    spans = doc['provenance']['spans'][b]
    assert len(spans) == 1 and spans[0][:2] == [5, 15]  # one record, one merged span
    (record,) = doc['provenance']['records'].values()
    assert record['origin'] == 'human' and record['created']
    assert _origins(doc, b)[:5] == [None] * 5  # text from before recording stays unrecorded
    validate_document(doc)


def test_edits_without_a_declared_origin_are_never_attributed():
    doc, b = _doc()
    doc = _apply(doc, [{'type': 'insert_text', 'target': b, 'offset': 0, 'text': 'Hi '}], 'human')
    doc = _apply(doc, [{'type': 'insert_text', 'target': b, 'offset': 1, 'text': 'XX'}])
    assert _origins(doc, b)[:5] == ['human', None, None, 'human', 'human']


def test_paste_records_its_source():
    doc, b = _doc()
    source = {'url': 'https://example.org/page', 'formats': ['text/plain', 'text/x-moz-url-priv']}
    doc = _apply(
        doc,
        [{'type': 'insert_text', 'target': b, 'offset': 0, 'text': 'Quoted. '}],
        {'origin': 'pasted', 'source': source},
    )
    report = provenance.report(doc)
    assert report['spans'][0]['origin'] == 'pasted' and report['spans'][0]['source']['url'] == source['url']


def test_recording_can_be_turned_off_per_document():
    doc, b = _doc()
    doc, _ = engine.apply(doc, {'operations': [{'type': 'set_settings', 'values': {'provenance': False}}]})
    doc = _apply(doc, [{'type': 'insert_text', 'target': b, 'offset': 0, 'text': 'Hi '}], 'human')
    assert 'provenance' not in doc
    assert provenance.report(doc)['recording'] == 'off'


def test_accepted_ai_proposal_records_model_claims_and_task():
    doc, b = _doc('Opening line.')
    doc, pid = _ai_proposal(
        doc, [{'type': 'insert_text', 'target': b, 'offset': 13, 'text': ' The sky is blue. It rains.'}]
    )
    doc = _apply(doc, [{'type': 'accept_proposal', 'target': pid}], 'human')
    (rid,) = [k for k, r in doc['provenance']['records'].items() if r['origin'] == 'ai_generated']
    record = doc['provenance']['records'][rid]
    assert record['model'] == 'llama3.1:8b' and record['task'] == 'drafting' and record['proposal'] == pid
    claims = sorted(doc['provenance']['claims'].values(), key=lambda c: c['start'])
    text = doc['blocks'][0]['text']
    assert [text[c['start'] : c['end']] for c in claims] == ['The sky is blue.', 'It rains.']
    for claim in claims:
        (grounding,) = claim['grounding']
        assert grounding['origin'] == 'model' and grounding['state'] == 'not_checkable'
        assert grounding['answer_span'] == [claim['start'], claim['end']]


def test_rewriting_proposal_is_ai_edited_and_tagged_editing():
    doc, b = _doc('The colour is red.')
    doc, pid = _ai_proposal(
        doc,
        [
            {
                'type': 'replace_text',
                'target': b,
                'match': 'colour',
                'replacement': 'color',
                'expected_text': 'colour',
            }
        ],
    )
    doc = _apply(doc, [{'type': 'accept_proposal', 'target': pid}], 'human')
    report = provenance.report(doc)
    assert [s['origin'] for s in report['spans']] == ['ai_edited']
    assert report['tasks'] == ['editing'] and report['counts']['ai_edited'] == len('color')


def test_cited_tokens_become_grounding_records_and_are_removed_from_the_text():
    doc, b = _doc('Notes.')
    passage = {
        'chunk_id': 'chunk_1',
        'document_id': 'doc_lib',
        'library_id': 'lib_1',
        'text': 'Aria is the captain of the long ship.',
        'content_hash': 'hash_1',
        'locator': {'ordinal': 1},
    }
    proposal = provenance.ground_proposal(
        {
            'operations': [
                {
                    'type': 'insert_text',
                    'target': b,
                    'offset': 6,
                    'text': ' Aria is the captain [[cite:chunk_1]]. Uncited.',
                }
            ],
            'provenance': {'provider': 'local', 'model': 'qwen2.5'},
        },
        passages=[passage],
    )
    assert '[[cite:' not in proposal['operations'][0]['text']
    doc = _apply(doc, [{'type': 'propose', 'values': proposal}])
    doc = _apply(doc, [{'type': 'accept_proposal', 'target': doc['review']['proposals'][-1]['id']}], 'human')
    text = doc['blocks'][0]['text']
    assert '[[cite' not in text
    claims = sorted(doc['provenance']['claims'].values(), key=lambda c: c['start'])
    cited, uncited = claims
    assert text[cited['start'] : cited['end']] == 'Aria is the captain.'
    assert (
        cited['grounding'][0]['chunk_id'] == 'chunk_1' and cited['grounding'][0]['content_hash'] == 'hash_1'
    )
    assert uncited['grounding'][0]['origin'] == 'model'
    assert provenance.report(doc)['tasks'] == ['drafting']


def test_answer_side_follows_edits_and_marks_edited_claims():
    doc, b = _doc('Lead.')
    doc, pid = _ai_proposal(
        doc, [{'type': 'insert_text', 'target': b, 'offset': 5, 'text': ' Gulls cry at dawn.'}]
    )
    doc = _apply(doc, [{'type': 'accept_proposal', 'target': pid}], 'human')
    doc = _apply(doc, [{'type': 'insert_text', 'target': b, 'offset': 0, 'text': 'New. '}], 'human')
    (claim,) = doc['provenance']['claims'].values()
    text = doc['blocks'][0]['text']
    assert text[claim['start'] : claim['end']] == 'Gulls cry at dawn.' and claim['state'] == 'anchored'
    offset = text.index('cry')
    doc = _apply(doc, [{'type': 'insert_text', 'target': b, 'offset': offset, 'text': 'loudly '}], 'human')
    (claim,) = doc['provenance']['claims'].values()
    assert claim['state'] == 'edited'
    ai = next(r for r in doc['provenance']['records'].values() if r['origin'] == 'ai_generated')
    assert ai['edited_by_human'] is True
    text = doc['blocks'][0]['text']
    doc = _apply(doc, [{'type': 'delete_text', 'target': b, 'start': claim['start'], 'end': claim['end']}])
    assert not (doc.get('provenance') or {}).get('claims')


def test_split_join_move_and_delete_keep_spans_with_their_text():
    doc = new_document('P', 'First para\nSecond')
    a, c = (x['id'] for x in doc['blocks'])
    doc = _apply(doc, [{'type': 'insert_text', 'target': a, 'offset': 5, 'text': 'XYZ'}], 'human')
    doc = _apply(doc, [{'type': 'split_block', 'target': a, 'offset': 6, 'id': 'p_tail'}])
    assert doc['provenance']['spans'][a][0][:2] == [5, 6] and doc['provenance']['spans']['p_tail'][0][:2] == [
        0,
        2,
    ]
    doc = _apply(doc, [{'type': 'join_blocks', 'target': a, 'next': 'p_tail'}])
    assert doc['provenance']['spans'][a][0][:2] == [5, 8] and 'p_tail' not in doc['provenance']['spans']
    before = copy.deepcopy(doc['provenance'])
    doc = _apply(doc, [{'type': 'move_block', 'target': a, 'after': c}])
    assert doc['provenance'] == before
    doc = _apply(doc, [{'type': 'delete_block', 'target': a}])
    assert 'provenance' not in doc  # nothing left to attribute; empty logs are dropped


def test_report_shares_per_section_and_target_scope():
    doc = new_document('R', 'Chapter\nAlpha\nPart\nBeta')
    h1, p1, h2, _p2 = (x['id'] for x in doc['blocks'])
    doc = _apply(doc, [{'type': 'apply_style', 'target': h1, 'style': 'heading1'}])
    doc = _apply(
        doc,
        [
            {
                'type': 'set_block',
                'target': h1,
                'values': {**doc['blocks'][0], 'type': 'heading', 'attrs': {'level': 1}},
            },
            {
                'type': 'set_block',
                'target': h2,
                'values': {**doc['blocks'][2], 'type': 'heading', 'attrs': {'level': 1}},
            },
        ],
    )
    doc = _apply(doc, [{'type': 'insert_text', 'target': p1, 'offset': 5, 'text': '!!!'}], 'human')
    report = provenance.report(doc)
    first = next(s for s in report['sections'] if s['id'] == h1)
    assert first['counts']['human'] == 3 and first['characters'] == len('Chapter') + len('Alpha!!!')
    scoped = provenance.report(doc, h2)
    assert scoped['counts']['human'] == 0 and scoped['characters'] == len('Part') + len('Beta')
    assert provenance.report(doc, p1)['percentages']['human'] == 37.5
    with pytest.raises(ByaxError):
        provenance.report(doc, 'missing')
    assert any('typed by a person' in line for line in provenance.describe(report))


def test_clear_forgets_the_log_or_selected_targets():
    doc = new_document('C', 'One\nTwo')
    a, b = (x['id'] for x in doc['blocks'])
    doc = _apply(
        doc,
        [
            {'type': 'insert_text', 'target': a, 'offset': 0, 'text': 'x'},
            {'type': 'insert_text', 'target': b, 'offset': 0, 'text': 'y'},
        ],
        'human',
    )
    partial = _apply(doc, [{'type': 'clear_provenance', 'targets': [a]}])
    assert set(partial['provenance']['spans']) == {b}
    assert 'provenance' not in _apply(doc, [{'type': 'clear_provenance'}])


def test_schema_validation_and_migration_of_the_optional_log():
    doc, b = _doc()
    doc = _apply(doc, [{'type': 'insert_text', 'target': b, 'offset': 0, 'text': 'x'}], 'human')
    broken = copy.deepcopy(doc)
    broken['provenance']['spans'][b] = [[3, 1, 'nope']]
    with pytest.raises(ByaxError) as error:
        validate_document(broken)
    assert error.value.code == 'schema_error'
    with pytest.raises(ByaxError):
        engine.apply(
            doc,
            {
                'operations': [{'type': 'insert_text', 'target': b, 'offset': 0, 'text': 'y'}],
                'provenance': {'origin': 'robot'},
            },
        )
    legacy = copy.deepcopy(doc)
    legacy['schema_version'] = 2
    with pytest.raises(ByaxError) as error:
        migration.migrate_document(legacy)
    assert error.value.code == 'unsupported_feature' and error.value.details['field'] == 'provenance'


def test_schema_v2_fixture_reports_the_provenance_structure(tmp_path):
    from pathlib import Path

    fixture = Path(__file__).parent / 'fixtures' / 'schema-v2-manuscript.byax'
    doc, report = storage.open_document(fixture)
    steps = report['migration']['steps'] if isinstance(report.get('migration'), dict) else None
    flattened = json.dumps(report, default=str)
    assert '"field": "provenance"' in flattened, steps
    assert 'provenance' not in doc


def test_cli_report_and_mcp_parity(tmp_path):
    path = str(tmp_path / 'p.byax')
    execute('new', {'out': path, 'text': 'Alpha'})
    target = storage.load_document(path)['blocks'][0]['id']
    execute(
        'apply',
        {
            'path': path,
            'input': {
                'operations': [{'type': 'insert_text', 'target': target, 'offset': 5, 'text': ' beta'}],
                'provenance': {
                    'origin': 'ai_generated',
                    'model': 'gpt-4o',
                    'provider': 'work',
                    'tool': 'script',
                },
            },
        },
    )
    local = execute('provenance', {'path': path})
    assert local['counts']['ai_generated'] == 5 and local['models'] == [
        {'model': 'gpt-4o', 'provider': 'work'}
    ]
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_provenance', 'arguments': {'path': path}},
        }
    )['result']
    assert response['structuredContent']['counts'] == local['counts']


def test_transports_cannot_claim_a_person_typed_or_erase_the_log(tmp_path):
    envelope = {'operations': [{'type': 'insert_text', 'target': 'p', 'offset': 0, 'text': 'x'}]}
    for origin in ('human', 'pasted'):
        with pytest.raises(ByaxError) as error:
            check_provider_admin(
                'apply', {'path': 'x', 'input': {**envelope, 'provenance': {'origin': origin}}}
            )
        assert error.value.code == 'unsupported_feature'
    check_provider_admin(
        'apply', {'path': 'x', 'input': {**envelope, 'provenance': {'origin': 'ai_generated'}}}
    )
    for op in ({'type': 'clear_provenance'}, {'type': 'reanchor_claim', 'target': 'c', 'grounding': [{}]}):
        with pytest.raises(ByaxError):
            check_provider_admin('apply', {'path': 'x', 'input': {'operations': [op]}})
    for arguments in ({'action': 'clear'}, {'action': 'export-c2pa', 'key_file': 'k.pem'}):
        with pytest.raises(ByaxError):
            check_provider_admin('provenance', {'path': 'x', **arguments})


def test_source_side_reanchoring_proposes_updates(monkeypatch):
    doc, b = _doc('Notes.')
    proposal = {
        'operations': [{'type': 'insert_text', 'target': b, 'offset': 6, 'text': ' Aria leads.'}],
        'provenance': {
            'provider': 'local',
            'model': 'qwen2.5',
            'grounding': [
                {
                    'operation': 0,
                    'answer_span': [1, 12],
                    'chunk_id': 'old',
                    'document_id': 'doc_old',
                    'library_id': 'lib',
                    'quote': 'Aria leads',
                    'prefix': '',
                    'suffix': '',
                    'content_hash': 'h_old',
                    'origin': 'token',
                    'state': 'supported',
                    'source_span': [0, 10],
                }
            ],
        },
    }
    doc = _apply(doc, [{'type': 'propose', 'values': proposal}])
    doc = _apply(doc, [{'type': 'accept_proposal', 'target': doc['review']['proposals'][-1]['id']}], 'human')
    chunk = {
        'chunk_id': 'new',
        'text': 'Now Aria leads the crew.',
        'content_hash': 'h_new',
        'locator': {'ordinal': 2},
    }

    def locate(record, **_):
        return {
            'status': 'reanchored',
            'chunk': chunk,
            'span': [4, 14],
            'document_id': 'doc_new',
            'locator': {'ordinal': 2, 'chunk': 'new'},
        }

    monkeypatch.setattr('byax.grounding.locate_record', locate)
    result = provenance.reanchor_sources(doc)
    assert result['summary']['reanchored'] == 1 and len(result['operations']) == 1
    updated = _apply(doc, result['operations'])
    (claim,) = updated['provenance']['claims'].values()
    assert claim['grounding'][0]['chunk_id'] == 'new' and claim['grounding'][0]['source_span'] == [4, 14]
    assert claim['grounding'][0]['prefix'] == 'Now '

    def lost(record, **_):
        return {'status': 'unanchored', 'reason': 'gone'}

    monkeypatch.setattr('byax.grounding.locate_record', lost)
    result = provenance.reanchor_sources(doc)
    updated = _apply(doc, result['operations'])
    (claim,) = updated['provenance']['claims'].values()
    assert claim['state'] == 'unanchored' and claim['grounding'][0]['state'] == 'unanchored'
