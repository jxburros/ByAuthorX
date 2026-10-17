"""Grounding records, the verification cascade and the citation audit (context engine plan 6.1-6.5)."""

import copy
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from byax import engine, grounding, model
from byax.citations import source_from_hit
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.mcp import handle

FIXTURES = Path(__file__).parent / 'fixtures' / 'grounding'
PASSAGES = json.loads((FIXTURES / 'passages.json').read_text(encoding='utf-8'))
PLAN_SHAPE = {
    'answer_span',
    'chunk_id',
    'document_id',
    'library_id',
    'block_id',
    'source_span',
    'quote',
    'prefix',
    'suffix',
    'content_hash',
    'origin',
    'state',
    'checks',
}


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


# --- token citations ----------------------------------------------------------------------------


def test_token_records_follow_the_plan_shape_and_recover_source_spans():
    reply = (
        'The log says "the lamp burned all night" [[cite:chunk_log_1]]. '
        'Supplies came across before the gale [[cite:chunk_log_2]]. '
        'The town was proud [[cite:chunk_invented]].'
    )
    result = grounding.from_tokens(reply, PASSAGES)
    assert result['text'] == (
        'The log says "the lamp burned all night". Supplies came across before the gale. The town was proud.'
    )
    assert result['invalid'] == ['chunk_invented']
    quoted, paraphrase, invented = (record.to_dict() for record in result['records'])
    assert PLAN_SHAPE <= set(quoted)
    assert quoted['origin'] == 'token' and quoted['chunk_id'] == 'chunk_log_1'
    assert result['text'][slice(*quoted['answer_span'])] == 'The log says "the lamp burned all night"'
    text = PASSAGES[0]['text']
    assert text[slice(*quoted['source_span'])] == quoted['quote'] == 'the lamp burned all night'
    assert quoted['prefix'] == text[: quoted['source_span'][0]][-32:]
    assert quoted['suffix'] == text[quoted['source_span'][1] :][:32]
    assert quoted['content_hash'] == 'hash_logbook'
    # The paraphrase's span is recovered by fuzzily aligning the cited sentence with the chunk.
    assert 'before the gale' in paraphrase['quote']
    assert invented['valid_id'] is False and invented['state'] == 'unverified'
    assert invented['marker'] == len(result['text']) - 1


def test_cascade_stage_one_states():
    reply = (
        'Exact: "the lamp burned all night" [[cite:chunk_log_1]]. '
        'Near: "the lamp burnt all night and the glass held" [[cite:chunk_log_1]]. '
        'Wrong: "the lamp went out at midnight" [[cite:chunk_log_1]]. '
        'Mrs. Wren rowed the supplies across before the gale [[cite:chunk_log_2]]. '
        'Help arrived from the mainland in good time [[cite:chunk_log_2]]. '
        'Unknown [[cite:nope]].'
    )
    records = grounding.verify(
        grounding.from_tokens(reply, PASSAGES)['records'], texts=grounding.source_texts(PASSAGES)
    )
    assert [r.state for r in records] == [
        'supported',
        'partial',
        'unsupported',
        'supported',
        'not_checkable',
        'unverified',
    ]
    near = records[1].checks[0]
    assert near['stage'] == 'quote' and near['match'] == 'near' and near['differences']
    assert records[3].checks[-1]['stage'] == 'fuzzy'
    assert 'entailment' in records[4].checks[-1]['basis']
    # Without the source text a valid citation cannot be anchored.
    assert grounding.verify(records[:1], texts={})[0].state == 'unanchored'


def test_later_stages_plug_into_the_cascade_and_report_not_run_until_installed():
    record = grounding.from_tokens('Help arrived in time [[cite:chunk_log_2]].', PASSAGES)['records']
    texts = grounding.source_texts(PASSAGES)
    pending = grounding.verify(record, stages=('quote', 'fuzzy', 'entailment', 'judge'), texts=texts)[0]
    assert [c['result'] for c in pending.checks if c['stage'] in ('entailment', 'judge')] == ['not_run'] * 2
    assert pending.state == 'not_checkable'
    seen = []

    def entailment(item, text):
        seen.append(item.claim)
        return {'stage': 'entailment', 'result': 'supported', 'model': 'fixture-nli'}

    grounding.register_stage('entailment', entailment)
    try:
        assert grounding.available_stages() == ['quote', 'fuzzy', 'entailment']
        done = grounding.verify(record, stages=('quote', 'fuzzy', 'entailment'), texts=texts)[0]
        assert done.state == 'supported' and done.checks[-1]['model'] == 'fixture-nli'
        assert seen == ['Help arrived in time']
    finally:
        grounding.register_stage('entailment', None)
    assert grounding.available_stages() == ['quote', 'fuzzy']
    with pytest.raises(ByaxError):
        grounding.verify(record, stages=('telepathy',))


def test_reference_ids_are_cited_with_their_excerpt():
    references = {'ref_wren': {'title': 'Wren letters', 'excerpt': 'I rowed across before the gale.'}}
    result = grounding.ground_reply(
        'She "rowed across before the gale" [[cite:ref_wren]].', references=references
    )
    record = result['records'][0]
    assert record['citation_id'] == 'ref_wren' and record['title'] == 'Wren letters'
    assert record['state'] == 'supported' and result['summary']['supported'] == 1


# --- native citations -----------------------------------------------------------------------------


def test_anthropic_content_block_citations_map_to_chunks():
    documents = grounding.anthropic_documents(PASSAGES)
    assert [d['title'] for d in documents] == ['logbook.txt', 'harbour.pdf']
    assert [len(d['passages']) for d in documents] == [2, 1]
    result = grounding.from_anthropic(fixture('anthropic-citations.json'), documents)
    text = result['text']
    assert text.startswith("The keeper's log says the lamp burned all night. The storm")
    by_chunk = {}
    for record in result['records']:
        by_chunk.setdefault(record.citation_id, []).append(record)
    lamp = by_chunk['chunk_log_1'][0]
    assert lamp.origin == 'native' and text[slice(*lamp.answer_span)] == 'the lamp burned all night'
    assert lamp.quote == PASSAGES[0]['text'] and lamp.source_span == [0, len(PASSAGES[0]['text'])]
    harbour = by_chunk['chunk_harbour_1'][0]
    assert harbour.locator['page'] == 2 and harbour.quote.startswith('The storm reached')
    # A citation spanning two content blocks yields one record per chunk.
    assert len(by_chunk['chunk_log_1']) == 2 and len(by_chunk['chunk_log_2']) == 1
    assert by_chunk['chunk_log_2'][0].quote == PASSAGES[1]['text']
    assert result['invalid'] == ['document 7']
    verified = grounding.verify(result['records'], texts=grounding.source_texts(PASSAGES))
    assert [r.state for r in verified if r.valid_id] == ['supported'] * 4
    assert [r.state for r in verified if not r.valid_id] == ['unverified']


def test_gemini_grounding_supports_use_byte_offsets():
    result = grounding.from_gemini(fixture('gemini-grounding.json'), PASSAGES)
    text = result['text']
    first, second = result['records'][:2]
    assert (
        text[slice(*first.answer_span)]
        == 'The café roof was lost when the storm reached the harbour at dawn.'
    )
    assert first.chunk_id == 'chunk_harbour_1'
    assert text[slice(*second.answer_span)] == 'The lamp burned all night.'
    assert second.chunk_id == 'chunk_log_1' and second.quote == 'the lamp burned all night'
    assert result['invalid'] == ['grounding chunk 2']


def test_cohere_citation_spans_map_to_supplied_documents():
    result = grounding.from_cohere(fixture('cohere-citations.json'), PASSAGES)
    rowed, glass, missing = result['records']
    assert result['text'][slice(*rowed.answer_span)] == 'Mrs. Wren rowed the supplies across'
    assert rowed.chunk_id == 'chunk_log_2' and rowed.quote == 'Mrs. Wren rowed the supplies across'
    assert glass.chunk_id == 'chunk_log_1' and glass.quote == 'the glass held'
    assert missing.valid_id is False and result['invalid'] == ['doc_not_supplied']
    states = [r.state for r in grounding.verify(result['records'], texts=grounding.source_texts(PASSAGES))]
    assert states == ['supported', 'partial', 'unverified']


# --- the writer's own citations ------------------------------------------------------------------


# Chunker v3 merges short neighbouring paragraphs; padding keeps one paragraph per chunk (past half of the
# 400-token target) so a quote can be cited against a passage that does not contain it.
PAD = ' Margin note about nothing in particular.' * 40
NOTES = '\n\n'.join(
    line + PAD
    for line in (
        'Aria is the captain.',
        'Bren is the navigator of the long ship.',
        'Cora keeps the lighthouse lamp.',
    )
)


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    (root / 'notes.txt').write_text(NOTES, encoding='utf-8')
    registry = LibraryRegistry(tmp_path / 'config')
    definition = registry.create('Research', [root])
    cache = tmp_path / 'cache'
    LibraryIndex(definition, cache).refresh()
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda *a, **k: registry)
    original = LibraryIndex
    monkeypatch.setattr(
        'byax.libraries.LibraryIndex', lambda library, cache_dir=None, **k: original(library, cache)
    )
    return root, registry, definition, cache


CITED_LINES = (
    '“Aria is the captain.”',
    '“Bren is the navigater of the long ship.”',
    '“Bren is the navigator of the long ship.”',
    'Aria commands the vessel.',
    '“the tide was high”',
    '“Dara sails alone.”',
)


def _hit(index, term):
    return next(h for h in index.search(term)['results'])


def cited_document(library):
    _root, _registry, definition, cache = library
    index = LibraryIndex(definition, cache)
    aria, bren = _hit(index, 'Aria'), _hit(index, 'Bren')
    source = source_from_hit(aria)
    doc = model.new_document(text='\n'.join(CITED_LINES))
    doc['settings']['libraries'] = [{'library_id': definition['library_id'], 'name': 'Research'}]
    manual = {'title': 'Harbour diary', 'excerpt': 'In March the tide was high.', 'verification': 'manual'}
    detached = dict(copy.deepcopy(source), id='source_detached')
    detached['library'] = dict(detached['library'], library_id='lib_detached')
    blocks = [b['id'] for b in doc['blocks']]
    texts = [b['text'] for b in doc['blocks']]

    def cite(i, src, hit, kind):
        op = {
            'type': 'cite',
            'target': blocks[i],
            'start': 0,
            'end': len(texts[i]),
            'source': src,
            'kind': kind,
        }
        if hit:
            op['locator'] = {'chunk': hit['chunk_id'], 'ordinal': hit['locator']['ordinal']}
        return op

    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'add_reference', 'id': source['id'], 'values': source},
                {'type': 'add_reference', 'id': 'source_manual', 'values': manual},
                {'type': 'add_reference', 'id': 'source_detached', 'values': detached},
                cite(0, source['id'], aria, 'quotation'),
                cite(1, source['id'], bren, 'quotation'),
                cite(2, source['id'], aria, 'quotation'),
                cite(3, source['id'], aria, 'paraphrase'),
                cite(4, 'source_manual', None, 'quotation'),
                cite(5, 'source_detached', aria, 'quotation'),
            ]
        },
    )
    return doc, source['id']


def test_audit_reports_every_stage_one_outcome(library):
    doc, _ = cited_document(library)
    original = copy.deepcopy(doc)
    report = grounding.audit_citations(doc)
    assert doc == original  # read-only
    statuses = [item['status'] for item in report['citations']]
    assert statuses == [
        'quote_found',
        'near_match',
        'quote_not_found',
        'not_checkable',
        'quote_found',
        'source_unavailable',
    ]
    near = report['citations'][1]
    assert near['differences'] and "quote 'e' vs source 'o'" in near['message']
    elsewhere = report['citations'][2]['found_elsewhere']
    assert elsewhere and elsewhere[0]['ordinal'] == 2  # the Bren paragraph, not the cited Aria chunk
    assert report['deterministic'] and not report['network'] and not report['consent_required']
    assert report['validation'] == 'failed' and report['review'] == 'required'
    assert report['operations'] == []
    record = report['citations'][0]['record']
    assert PLAN_SHAPE <= set(record) and record['origin'] == 'user' and record['state'] == 'supported'
    assert grounding.audit_citations(doc) == report  # deterministic
    assert 'Quote found' in grounding.describe_audit(report)[1]


def test_changed_source_reanchors_or_becomes_unanchored_and_is_never_dropped(library):
    root, _registry, definition, cache = library
    doc, _source_id = cited_document(library)
    (root / 'notes.txt').write_text(
        'Preface added later.\n\nAria is the captain.\n\nCora keeps the lighthouse lamp.', encoding='utf-8'
    )
    LibraryIndex(definition, cache).refresh()
    report = grounding.audit_citations(doc)
    by_text = {item['text']: item for item in report['citations']}
    aria = by_text['“Aria is the captain.”']
    assert aria['status'] == 'source_changed' and aria['reanchor']['structure'] is False
    assert aria['changed']['cited_content_hash'] != aria['changed']['current_content_hash']
    bren = by_text['“Bren is the navigator of the long ship.”']
    assert bren['status'] == 'unanchored'
    paraphrase = by_text['Aria commands the vessel.']
    assert paraphrase['status'] == 'source_changed'
    kinds = [op['type'] for op in report['operations']]
    assert kinds[0] == 'add_reference' and kinds.count('reanchor_citation') == 3
    updated, _ = engine.apply(doc, {'operations': report['operations']})
    marks = [m for b in updated['blocks'] for m in b['marks'] if m['type'] == 'citation']
    assert len(marks) == 6  # nothing dropped
    states = {m['anchor']['state'] for m in marks if 'anchor' in m}
    assert states == {'anchored', 'unanchored'}
    again = {item['text']: item['status'] for item in grounding.audit_citations(updated)['citations']}
    assert again['“Aria is the captain.”'] == 'quote_found'
    assert again['“Bren is the navigator of the long ship.”'] == 'unanchored'
    assert again['“Bren is the navigater of the long ship.”'] == 'unanchored'


def test_reanchoring_tries_structure_first():
    chunks = [
        {'chunk_id': 'a', 'text': 'The lamp burned. The glass held.', 'locator': {'page': 1}},
        {'chunk_id': 'b', 'text': 'Later the glass held firm. The glass held.', 'locator': {'page': 2}},
    ]
    anchor = {
        'quote': 'The glass held.',
        'prefix': 'firm. ',
        'suffix': '',
        'position': {'start': 27, 'end': 42},
    }
    found = grounding.reanchor_chunks(anchor, chunks, {'page': 2})
    assert found['chunk']['chunk_id'] == 'b' and found['structure'] and found['method'] == 'position'
    moved = grounding.reanchor_chunks(anchor, chunks, {'page': 9})
    assert moved['chunk']['chunk_id'] == 'a' and moved['method'] == 'anywhere'
    assert grounding.reanchor_chunks({'quote': 'Not present anywhere at all.'}, chunks) is None


def test_anchor_fields_are_validated_and_separate_from_rendering_affixes():
    doc = model.new_document(text='Aria is the captain.')
    block = doc['blocks'][0]['id']
    doc['references']['s'] = {'title': 'S'}
    anchor = {'quote': 'Aria', 'prefix': '', 'suffix': ' is the', 'position': {'start': 0, 'end': 4}}
    cited, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'cite',
                    'target': block,
                    'start': 0,
                    'end': 4,
                    'source': 's',
                    'prefix': 'see ',
                    'anchor': anchor,
                }
            ]
        },
    )
    mark = cited['blocks'][0]['marks'][0]
    assert mark['prefix'] == 'see ' and mark['anchor'] == anchor
    for bad in (
        {'prefix': 'x'},
        dict(anchor, prefix='p' * 33),
        dict(anchor, position={'start': 5, 'end': 1}),
        dict(anchor, state='lost'),
        dict(anchor, extra=1),
    ):
        with pytest.raises(ByaxError):
            engine.apply(
                doc,
                {
                    'operations': [
                        {'type': 'cite', 'target': block, 'start': 0, 'end': 4, 'source': 's', 'anchor': bad}
                    ]
                },
            )
    broken = copy.deepcopy(cited)
    broken['blocks'][0]['marks'][0]['anchor'] = 'not an object'
    with pytest.raises(ByaxError):
        model.validate_document(broken)
    lost, _ = engine.apply(
        cited,
        {
            'operations': [
                {
                    'type': 'reanchor_citation',
                    'target': block,
                    'start': 0,
                    'end': 4,
                    'source': 's',
                    'anchor': dict(anchor, state='unanchored'),
                }
            ]
        },
    )
    assert lost['blocks'][0]['marks'][0]['anchor']['state'] == 'unanchored'
    with pytest.raises(ByaxError) as missing:
        engine.apply(
            cited,
            {
                'operations': [
                    {
                        'type': 'reanchor_citation',
                        'target': block,
                        'start': 1,
                        'end': 4,
                        'source': 's',
                        'anchor': anchor,
                    }
                ]
            },
        )
    assert missing.value.code == 'missing_target'


def test_locate_record_follows_a_changed_source(library):
    root, _registry, definition, cache = library
    index = LibraryIndex(definition, cache)
    hit = _hit(index, 'Bren')
    record = grounding.from_tokens('Bren "navigator of the long ship" [[cite:x]].', [dict(hit, chunk_id='x')])
    record = record['records'][0].to_dict()
    record['chunk_id'] = hit['chunk_id']
    located = grounding.locate_record(record)
    assert located['status'] == 'available'
    assert located['chunk']['text'][slice(*located['span'])] == 'navigator of the long ship'
    (root / 'notes.txt').write_text(
        f'New opening.{PAD}\n\nMore.{PAD}\n\nBren is the navigator of the long ship.', encoding='utf-8'
    )
    LibraryIndex(definition, cache).refresh()
    moved = grounding.locate_record(record)
    assert moved['status'] == 'reanchored' and moved['chunk']['locator']['ordinal'] == 4
    assert moved['chunk']['text'][slice(*moved['span'])] == 'navigator of the long ship'
    (root / 'notes.txt').write_text('Nothing about the navigator.', encoding='utf-8')
    LibraryIndex(definition, cache).refresh()
    assert grounding.locate_record(record)['status'] == 'unanchored'
    assert grounding.locate_record(dict(record, library_id=None))['status'] == 'unavailable'


# --- interfaces ------------------------------------------------------------------------------------

TOKEN = 'grounding-test-token-with-32-characters'


@pytest.fixture
def rest(tmp_path):
    from byax.service import make_server

    server = make_server([tmp_path], token=TOKEN, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/v1/execute'

    def call(command, arguments):
        request = Request(
            url,
            data=json.dumps({'command': command, 'arguments': arguments}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + TOKEN},
        )
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    yield call
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _strip(value):
    volatile = {'revision', 'base_revision', 'document_id', 'exit_code', 'outputs', 'retrieved'}
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in volatile}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def test_citation_audit_parity_across_library_cli_mcp_and_rest(library, tmp_path, rest, capsys):
    from byax.storage import save_document

    doc, _ = cited_document(library)
    path = str(tmp_path / 'audit.byax')
    save_document(doc, path)
    direct = grounding.audit_citations(doc)
    library_result = execute('check', {'path': path, 'citations': True})
    code = main(['check', path, '--citations'])
    cli = json.loads(capsys.readouterr().out)
    mcp = handle(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {'name': 'byax_check', 'arguments': {'path': path, 'citations': True}},
        }
    )['result']
    status, rest_body = rest('check', {'path': path, 'citations': True})
    assert status == 200
    assert code == 4 == mcp['structuredContent']['exit_code'] == rest_body['exit_code']
    assert _strip(cli) == _strip(mcp['structuredContent']) == _strip(rest_body) == _strip(library_result)
    assert (
        library_result['citations'] == direct['citations'] and library_result['summary'] == direct['summary']
    )
    with pytest.raises(ByaxError):
        execute('check', {'path': path, 'citations': True, 'suite': ['spelling']})
