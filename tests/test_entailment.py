"""Cascade stage 2: local entailment (context engine plan Phase 5), with fake scorers and a tiny fake ONNX session.

Nothing here needs the real model: the stage logic runs against a scripted scorer, and the ONNX scorer is
exercised through a stand-in session and a real ``tokenizers`` vocabulary. The real model is checked in
``test_entailment_fixture.py`` under the ``eval_models`` marker.
"""

import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from byax import engine, grounding, local_inference, local_models, model
from byax import grounding_entailment as ge
from byax.citations import source_from_hit
from byax.cli import execute
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.providers import ProviderConfig, ProviderRegistry

REAL_CLIENT = httpx.Client
FIXTURES = Path(__file__).parent / 'fixtures' / 'grounding'
PASSAGES = json.loads((FIXTURES / 'passages.json').read_text(encoding='utf-8'))
TEXTS = grounding.source_texts(PASSAGES)


class FakeScorer:
    """Scores a claim by the first table key it contains (default 0.9); remembers every request."""

    identity = 'local:fake-nli@1'

    def __init__(self, table=None, default=0.9):
        self.table = dict(table or {})
        self.default = default
        self.calls = []

    def score(self, pairs):
        self.calls.append(list(pairs))
        return [next((v for k, v in self.table.items() if k in claim), self.default) for _, claim in pairs]


@pytest.fixture(autouse=True)
def no_models(tmp_path, monkeypatch):
    """No model is installed unless a test says so, and the stage registry is left as it was found."""
    monkeypatch.setenv('BYAX_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.delenv('BYAX_BUNDLED_MODELS', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    yield
    grounding.register_stage('entailment', None)


def records_for(reply):
    return grounding.from_tokens(reply, PASSAGES)['records']


def stage_with(table=None, **kwargs):
    return ge.EntailmentStage(FakeScorer(table), **kwargs)


# --- categories, never numbers -------------------------------------------------------------------------


def test_one_piece_is_supported_partial_or_unsupported_at_the_thresholds():
    assert [ge.classify(s, 0.8, 0.65) for s in (1.0, 0.8, 0.79, 0.66, 0.65, 0.0)] == [
        'supported',
        'supported',
        'partial',
        'partial',
        'unsupported',
        'unsupported',
    ]


def test_a_claim_is_supported_only_if_every_piece_is_and_unsupported_only_if_none_is():
    assert ge.aggregate(['supported', 'supported']) == 'supported'
    assert ge.aggregate(['unsupported', 'unsupported']) == 'unsupported'
    assert ge.aggregate(['supported', 'unsupported']) == 'partial'
    assert ge.aggregate(['supported', 'partial']) == 'partial'
    assert ge.aggregate(['partial']) == 'partial'


def test_the_premise_window_keeps_short_texts_whole_and_centres_on_the_span_otherwise():
    assert ge.premise_window('short text', [0, 5]) == 'short text'
    text = ' '.join(f'word{n}' for n in range(2000))
    window = ge.premise_window(text, [len(text) // 2, len(text) // 2 + 10], limit=500)
    assert (
        len(window) <= 500 and window.split()[0].startswith('word') and window.split()[-1].startswith('word')
    )
    assert text[len(text) // 2 : len(text) // 2 + 10] in window
    assert ge.premise_window(text, None, limit=500) in text


def test_tiny_fragments_are_not_scored():
    found = ge.pieces('Yes. The lamp burned all night, but the glass did not hold at dawn.')
    assert [c['text'] for c in found] == [
        'The lamp burned all night',
        'but the glass did not hold at dawn.',
    ]


# --- the stage ----------------------------------------------------------------------------------------


def test_the_stage_judges_a_paraphrase_stage_one_could_not_and_reports_only_categories():
    records = records_for('Help arrived from the mainland in good time [[cite:chunk_log_2]].')
    stage = stage_with({'mainland': 0.05})
    verified = grounding.verify(
        records, ('quote', 'fuzzy', 'entailment'), texts=TEXTS, overrides={'entailment': stage}
    )
    record = verified[0]
    assert record.state == 'unsupported'
    check = record.checks[-1]
    assert check['stage'] == 'entailment' and check['result'] == 'unsupported'
    assert check['model'] == 'local:fake-nli@1' and 'advisory' in check['basis']
    assert check['claims'][0]['text'] == 'Help arrived from the mainland in good time'
    # The model's number is internal: no score, no percentage anywhere in the stored check.
    text = json.dumps(record.to_dict())
    assert '0.05' not in text and '%' not in text and 'score' not in check
    assert [c['stage'] for c in record.checks] == ['fuzzy', 'entailment'] or record.checks[-1][
        'stage'
    ] == 'entailment'


def test_clauses_are_scored_separately_and_disagreement_is_partial():
    reply = 'The lamp burned for a week, but the glass held fast through the gale [[cite:chunk_log_1]].'
    stage = stage_with({'lamp': 0.1, 'glass': 0.95})
    record = grounding.verify(
        records_for(reply), ('quote', 'fuzzy', 'entailment'), texts=TEXTS, overrides={'entailment': stage}
    )[0]
    assert record.state == 'partial'
    assert [(c['text'], c['result']) for c in record.checks[-1]['claims']] == [
        ('The lamp burned for a week', 'unsupported'),
        ('but the glass held fast through the gale', 'supported'),
    ]
    assert len(stage.scorer.calls) == 1 and len(stage.scorer.calls[0]) == 2  # one batch for both pieces


def test_the_stage_abstains_where_stage_one_decided():
    verbatim = records_for('Mrs. Wren rowed the supplies across before the gale [[cite:chunk_log_2]].')
    quoted = records_for('The log says "the lamp went out at midnight" [[cite:chunk_log_1]].')
    stage = stage_with()
    out = grounding.verify(
        [*verbatim, *quoted], ('quote', 'fuzzy', 'entailment'), texts=TEXTS, overrides={'entailment': stage}
    )
    assert [r.state for r in out] == ['supported', 'unsupported']
    assert not stage.scorer.calls  # a near-verbatim claim and a quotation are settled by stage 1
    assert all(c['stage'] != 'entailment' for r in out for c in r.checks)


def test_the_stage_reads_the_window_around_the_cited_span_and_skips_empty_claims():
    long_text = (
        'Padding words fill this chunk. ' * 400 + 'The lamp burned all night. ' + 'More padding. ' * 400
    )
    record = grounding.GroundingRecord(
        answer_span=[0, 20],
        chunk_id='big',
        document_id='d',
        library_id=None,
        block_id=None,
        source_span=[long_text.index('The lamp'), long_text.index('The lamp') + 26],
        quote='',
        prefix='',
        suffix='',
        content_hash=None,
        origin='token',
        citation_id='big',
        claim='The lamp burned for hours and hours.',
    )
    stage = stage_with()
    check = stage(record, long_text)
    premise = stage.scorer.calls[0][0][0]
    assert len(premise) <= ge.MAX_PREMISE_CHARS and 'The lamp burned all night.' in premise
    assert check['result'] == 'supported'
    blank = copy.deepcopy(record)
    blank.claim = '   '
    assert stage(blank, long_text) is None and stage(record, '') is None


# --- the cascade and honesty about a missing model ----------------------------------------------------------


def test_without_the_model_the_registered_stage_reports_not_run_with_the_reason():
    records = records_for('Help arrived from the mainland in good time [[cite:chunk_log_2]].')
    record = grounding.verify(records, ('quote', 'fuzzy', 'entailment'), texts=TEXTS)[0]
    assert record.state == 'not_checkable'  # nothing was guessed
    check = record.checks[-1]
    assert check['stage'] == 'entailment' and check['result'] == 'not_run' and check['model'] is None
    assert 'Model not installed' in check['reason'] and 'byax models --action download' in check['reason']
    assert 'entailment' in grounding.available_stages()


def test_availability_names_every_reason_the_stage_cannot_run(monkeypatch):
    state = ge.availability()
    assert (
        state['available'] is False and state['state'] == 'not_installed' and state['licence'] == 'Apache-2.0'
    )
    assert (
        state['model'] == 'hhem-2.1-open-int8'
        and 'Nothing is sent' in state['reason']
        or 'nothing is sent' in state['reason']
    )
    assert ge.availability('no-such-model')['state'] == 'unknown_model'
    monkeypatch.setattr(
        local_models, 'runtime_status', lambda: {'installed': False, 'reason': 'runtime missing'}
    )
    assert ge.availability()['reason'] == 'runtime missing'
    with pytest.raises(ByaxError) as caught:
        ge.make_stage()
    assert caught.value.code == 'missing_dependency' and 'local-models' in caught.value.details['extra']
    assert ge.try_stage() is None


def test_make_stage_refuses_a_missing_model_with_the_remedy_and_builds_one_when_installed(monkeypatch):
    with pytest.raises(ByaxError) as caught:
        ge.make_stage()
    assert caught.value.code == 'model_missing' and 'byax models --action download' in caught.value.message
    monkeypatch.setattr(
        ge, 'availability', lambda *a, **k: {'available': True, 'model': 'hhem-2.1-open-int8'}
    )
    stage = ge.make_stage()
    assert stage.supported_at == 0.8 and stage.unsupported_at == 0.65  # from the catalogue
    assert stage.model == 'local:hhem-2.1-open-int8@1'


def test_entail_grounding_adds_categories_and_survives_a_missing_model():
    reply = 'Help arrived from the mainland in good time [[cite:chunk_log_2]]. The lamp burned all night [[cite:chunk_log_1]].'
    grounded = grounding.ground_reply(reply, passages=PASSAGES)
    untouched = ge.entail_grounding(grounded, passages=PASSAGES)
    assert untouched['records'] == grounded['records']
    assert (
        untouched['entailment']['available'] is False and 'not installed' in untouched['entailment']['reason']
    )
    checked = ge.entail_grounding(grounded, passages=PASSAGES, stage=stage_with({'mainland': 0.05}))
    assert 'entailment' in checked['stages'] and checked['entailment'] == {
        'available': True,
        'model': 'local:fake-nli@1',
        'checked': 1,
    }
    assert [r['state'] for r in checked['records']] == ['unsupported', 'supported']
    assert checked['summary']['unsupported'] == 1
    assert grounded['records'][0]['state'] == 'not_checkable'  # the input is not modified


def test_underlines_locate_each_piece_in_the_displayed_reply():
    reply = (
        'First a framing sentence. The lamp burned for a week, but the glass held fast through the gale '
        '[[cite:chunk_log_1]].'
    )
    grounded = grounding.ground_reply(reply, passages=PASSAGES)
    checked = ge.entail_grounding(grounded, passages=PASSAGES, stage=stage_with({'lamp': 0.1, 'glass': 0.95}))
    text = checked['text']
    marks = ge.underlines(checked['records'][0], text)
    assert [(text[m['start'] : m['end']], m['result']) for m in marks] == [
        ('The lamp burned for a week', 'unsupported'),
        ('but the glass held fast through the gale', 'supported'),
    ]
    # If the displayed text moved (a leading line was added), the claim is found again; if it is gone, nothing.
    assert [m['result'] for m in ge.underlines(checked['records'][0], 'Intro line. ' + text)] == [
        'unsupported',
        'supported',
    ]
    assert ge.underlines(checked['records'][0], 'something else entirely') == []
    assert ge.underlines(grounded['records'][0], text) == []  # no entailment check, no underline


def test_proposed_edits_are_checked_by_what_they_cite_and_never_modified():
    proposals = [
        {
            'target': 'b1',
            'start': 0,
            'end': 3,
            'expected_text': 'The',
            'replacement': 'The keeper saw help arrive from the mainland [[cite:chunk_log_2]]',
            'explanation': 'adds the rescue',
        },
        {'target': 'b1', 'start': 4, 'end': 10, 'expected_text': 'keeper', 'replacement': 'warden'},
    ]
    original = copy.deepcopy(proposals)
    out = ge.entail_proposals(proposals, passages=PASSAGES, stage=stage_with({'mainland': 0.05}))
    assert proposals == original
    assert out[1] is None  # cites nothing, so nothing to check
    assert out[0]['records'][0]['state'] == 'unsupported' and out[0]['text'].endswith('the mainland')
    assert ge.entail_proposals(proposals, passages=PASSAGES) == [None, None]  # no model installed


# --- the writer's own citations ------------------------------------------------------------------------------


def paraphrase_document():
    doc = model.new_document(text='Help arrived from the mainland in good time.')
    doc['references']['ref'] = {
        'id': 'ref',
        'title': 'Logbook',
        'csl': {'id': 'ref', 'type': 'document', 'title': 'Logbook'},
        'excerpt': 'Mrs. Wren rowed the supplies across before the gale.',
        'verification': 'manual',
    }
    block = doc['blocks'][0]
    block['marks'] = [
        {
            'type': 'citation',
            'value': 'ref',
            'start': 0,
            'end': len(block['text']),
            'kind': 'paraphrase',
        }
    ]
    return doc


def test_the_audit_checks_paraphrases_from_the_reference_excerpt_and_keeps_stage_one_status():
    doc = paraphrase_document()
    report = grounding.audit_citations(doc)
    assert report['citations'][0]['status'] == 'not_checkable' and report['deterministic'] is True
    stage = stage_with({'mainland': 0.05})
    checked = ge.audit_entailment(report, doc, stage)
    item = checked['citations'][0]
    assert item['status'] == 'not_checkable' and item['entailment']['result'] == 'unsupported'
    assert item['record']['state'] == 'unsupported' and item['record']['checks'][-1]['stage'] == 'entailment'
    assert 'Mrs. Wren rowed' in stage.scorer.calls[0][0][0]
    assert checked['deterministic'] is False and checked['network'] is False and checked['advisory'] is True
    assert checked['consent_required'] is False and 'entailment' in checked['stages_run']
    assert checked['entailment']['support'] == {'supported': 0, 'partial': 0, 'unsupported': 1, 'not_run': 0}
    assert checked['review'] == 'required' and 'advisory' in checked['note']
    assert report['deterministic'] is True and 'entailment' not in report  # the input report is untouched
    lines = grounding.describe_audit(checked)
    assert 'support check (advisory): unsupported' in lines[1]


def test_quotations_and_sources_without_text_are_left_alone():
    doc = model.new_document(text='“the tide was high”\nHelp came.')
    doc['references']['manual'] = {
        'id': 'manual',
        'title': 'Diary',
        'csl': {'id': 'manual', 'type': 'document', 'title': 'Diary'},
        'excerpt': 'In March the tide was high.',
        'verification': 'manual',
    }
    doc['references']['empty'] = {
        'id': 'empty',
        'title': 'Empty',
        'csl': {'id': 'empty', 'type': 'document', 'title': 'Empty'},
        'verification': 'manual',
    }
    first, second = doc['blocks']
    first['marks'] = [
        {'type': 'citation', 'value': 'manual', 'start': 0, 'end': len(first['text']), 'kind': 'quotation'}
    ]
    second['marks'] = [
        {'type': 'citation', 'value': 'empty', 'start': 0, 'end': len(second['text']), 'kind': 'paraphrase'}
    ]
    report = grounding.audit_citations(doc)
    stage = stage_with()
    checked = ge.audit_entailment(report, doc, stage)
    assert [i['status'] for i in checked['citations']] == ['quote_found', 'not_checkable']
    assert 'entailment' not in checked['citations'][0]  # stage 1 settled the quotation
    assert (
        checked['citations'][1]['entailment']['result'] == 'not_run'
        and checked['citations'][1]['entailment']['reason']
    )
    assert not stage.scorer.calls
    assert checked['entailment']['checked'] == 0
    assert 'support check not run' in grounding.describe_audit(checked)[2]


NOTES = '\n\n'.join(
    line + ' Margin note about nothing in particular.' * 40
    for line in ('Aria is the captain.', 'Bren is the navigator of the long ship.', 'Cora keeps the lamp.')
)


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    (root / 'notes.txt').write_text(NOTES, encoding='utf-8')
    registry = LibraryRegistry(tmp_path / 'libconfig')
    definition = registry.create('Research', [root])
    cache = tmp_path / 'cache'
    LibraryIndex(definition, cache).refresh()
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda *a, **k: registry)
    original = LibraryIndex
    monkeypatch.setattr(
        'byax.libraries.LibraryIndex', lambda library, cache_dir=None, **k: original(library, cache)
    )
    return definition, original(definition, cache)


def library_paraphrase(library, locator=True):
    definition, index = library
    hit = index.search('Aria')['results'][0]
    source = source_from_hit(hit)
    doc = model.new_document(text='Aria commands the whole vessel.')
    doc['settings']['libraries'] = [{'library_id': definition['library_id'], 'name': 'Research'}]
    block = doc['blocks'][0]
    operation = {
        'type': 'cite',
        'target': block['id'],
        'start': 0,
        'end': len(block['text']),
        'source': source['id'],
        'kind': 'paraphrase',
    }
    if locator:
        operation['locator'] = {'chunk': hit['chunk_id'], 'ordinal': hit['locator']['ordinal']}
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'add_reference', 'id': source['id'], 'values': source}, operation]}
    )
    return doc


def test_a_library_paraphrase_is_read_against_the_cited_chunk(library):
    doc = library_paraphrase(library)
    report = grounding.audit_citations(doc)
    assert report['citations'][0]['status'] == 'not_checkable'
    stage = stage_with({'vessel': 0.2})
    checked = ge.audit_entailment(report, doc, stage)
    assert checked['citations'][0]['entailment']['result'] == 'unsupported'
    premise = stage.scorer.calls[0][0][0]
    assert 'Aria is the captain.' in premise and 'Bren is the navigator' not in premise


def test_a_locator_too_wide_to_find_the_passage_is_not_checked(library):
    doc = library_paraphrase(library, locator=False)
    item = grounding.audit_citations(doc)['citations'][0]
    text, reason = grounding.audit_source_text(item, doc, max_chunks=2)
    assert text == '' and 'narrow enough' in reason
    text, reason = grounding.audit_source_text(item, doc)
    assert 'Aria is the captain.' in text and reason is None
    missing = dict(item, source_id='nope')
    assert grounding.audit_source_text(missing, doc)[0] == ''


def test_the_command_line_audit_refuses_honestly_without_the_model_and_runs_with_it(tmp_path, monkeypatch):
    from byax.storage import save_document

    path = str(tmp_path / 'p.byax')
    save_document(paraphrase_document(), path)
    with pytest.raises(ByaxError) as caught:
        execute('check', {'path': path, 'citations': True, 'entailment': True})
    assert caught.value.code == 'model_missing' and 'byax models --action download' in caught.value.message
    with pytest.raises(ByaxError, match='apply to --citations'):
        execute('check', {'path': path, 'entailment': True})
    monkeypatch.setattr(ge, 'make_stage', lambda **kw: stage_with({'mainland': 0.05}))
    result = execute('check', {'path': path, 'citations': True, 'entailment': True})
    assert result['citations'][0]['entailment']['result'] == 'unsupported'
    assert result['stages_run'][-1] == 'entailment' and result['advisory'] is True
    plain = execute('check', {'path': path, 'citations': True})
    assert 'entailment' not in plain and plain['deterministic'] is True


def test_verify_citations_reply_mode_adds_categories_and_says_when_the_model_is_missing(
    tmp_path, monkeypatch
):
    from byax.storage import save_document

    doc = model.new_document(text='Mrs. Wren rowed the supplies across before the gale.')
    path = str(tmp_path / 'v.byax')
    save_document(doc, path)
    block = doc['blocks'][0]['id']
    cite = f'{doc["id"]}:{block}'
    arguments = {
        'path': path,
        'reply': f'Help came from the mainland by morning [[cite:{cite}]].',
        'input': {'passages': [{'target': block}]},
        'entailment': True,
    }
    absent = execute('verify-citations', arguments)
    assert absent['entailment']['available'] is False and absent['records'][0]['state'] == 'not_checkable'
    monkeypatch.setattr(ge, 'availability', lambda *a, **k: {'available': True})
    monkeypatch.setattr(ge, 'try_stage', lambda **k: stage_with({'mainland': 0.05}))
    present = execute('verify-citations', arguments)
    assert present['entailment']['available'] is True and present['records'][0]['state'] == 'unsupported'
    assert 'entailment' in present['stages']
    audit = execute('verify-citations', {'path': path})
    assert audit['mode'] == 'audit'


def test_entailment_runs_over_mcp_because_it_is_local(tmp_path, monkeypatch):
    from byax.mcp import handle
    from byax.storage import save_document

    path = str(tmp_path / 'm.byax')
    save_document(paraphrase_document(), path)
    monkeypatch.setattr(ge, 'make_stage', lambda **kw: stage_with({'mainland': 0.05}))
    reply = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {
                'name': 'byax_check',
                'arguments': {'path': path, 'citations': True, 'entailment': True},
            },
        }
    )['result']
    assert reply['structuredContent']['citations'][0]['entailment']['result'] == 'unsupported'


# --- chat: the support check on a reply and on proposed edits ---------------------------------------------------


def chat_registry(tmp_path, monkeypatch, reply):
    registry = ProviderRegistry(tmp_path / 'providers')
    registry.save(ProviderConfig('writer', 'ollama', 'writer-model'))

    def handler(request):
        return httpx.Response(200, json={'message': {'content': reply}})

    monkeypatch.setattr(
        httpx, 'Client', lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)
    )
    return registry


def chat_context():
    doc = model.new_document(text='The keeper watched the lamp.')
    context = engine.context(doc, limit=20000)
    context['library_passages'] = [dict(p, retrieval_policy='ask') for p in PASSAGES]
    return context, doc


def test_chat_with_the_support_check_underlines_replies_and_proposals(tmp_path, monkeypatch):
    context, doc = chat_context()
    block = doc['blocks'][0]
    reply = json.dumps(
        {
            'reply': 'Help arrived from the mainland in good time [[cite:chunk_log_2]].',
            'proposed_edits': [
                {
                    'target': block['id'],
                    'start': 0,
                    'end': 27,
                    'expected_text': 'The keeper watched the lamp',
                    'replacement': 'The keeper watched the lamp burn all night [[cite:chunk_log_1]]',
                }
            ],
        }
    )
    registry = chat_registry(tmp_path, monkeypatch, reply)
    messages = [{'role': 'user', 'content': 'Q?'}]
    plain = registry.chat('writer', messages, mode='work', context=context, library_authorized=True)
    assert 'entailment' not in plain['grounding'] and 'proposal_grounding' not in plain
    monkeypatch.setattr(ge, 'availability', lambda *a, **k: {'available': True, 'model': 'x'})
    monkeypatch.setattr(ge, 'try_stage', lambda **k: stage_with({'mainland': 0.05}))
    result = registry.chat(
        'writer', messages, mode='work', context=context, library_authorized=True, entailment=True
    )
    grounded = result['grounding']
    assert grounded['entailment']['available'] is True and grounded['records'][0]['state'] == 'unsupported'
    assert result['proposal_grounding'][0]['records'][0]['state'] == 'supported'
    assert result['proposals'][0]['replacement'].endswith('[[cite:chunk_log_1]]')  # unchanged
    assert 'grounding' not in result['proposals'][0]


def test_chat_keeps_the_reply_when_the_model_is_missing(tmp_path, monkeypatch):
    context, _ = chat_context()
    registry = chat_registry(tmp_path, monkeypatch, 'Help arrived in good time [[cite:chunk_log_2]].')
    result = registry.chat(
        'writer',
        [{'role': 'user', 'content': 'Q?'}],
        context=context,
        library_authorized=True,
        entailment=True,
    )
    assert (
        result['reply'].startswith('Help arrived') and result['grounding']['entailment']['available'] is False
    )
    assert result['grounding']['records'][0]['state'] == 'not_checkable'


# --- the ONNX scorer, with a stand-in session ---------------------------------------------------------------------

WORDS = [
    'premise',
    ':',
    'hypothesis',
    'lamp',
    'burned',
    'all',
    'night',
    'glass',
    'held',
    'tide',
    'rose',
    'storm',
]
VOCAB = {'[PAD]': 0, '[UNK]': 1, **{w: 2 + i for i, w in enumerate(WORDS)}}


def make_tokenizer():
    from tokenizers import Tokenizer, models, pre_tokenizers

    tokenizer = Tokenizer(models.WordLevel(VOCAB, unk_token='[UNK]'))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    return tokenizer


class FakeEntailmentSession:
    """Consistent (logit 4 on class 1) when every claim word occurs in the premise; records its feeds."""

    def __init__(self):
        self.feeds = []

    def run(self, names, feeds):
        import numpy as np

        assert names is None
        self.feeds.append({k: v.copy() for k, v in feeds.items()})
        out = []
        for ids, mask in zip(feeds['input_ids'], feeds['attention_mask'], strict=True):
            tokens = [int(t) for t, m in zip(ids, mask, strict=True) if m]
            hyp = tokens.index(VOCAB['hypothesis'])
            premise, claim = set(tokens[2:hyp]), tokens[hyp + 2 :]
            out.append([0.0, 4.0] if set(claim) <= premise else [4.0, 0.0])
        return [np.array(out, dtype=np.float32)]


def scorer_with(session, monkeypatch, max_tokens=64):
    pytest.importorskip('numpy')
    scorer = local_inference.EntailmentScorer('hhem-2.1-open-int8')
    scorer.parameters.update(prompt='premise : {text1} hypothesis : {text2}', max_tokens=max_tokens)
    fake = SimpleNamespace(session=session, tokenizer=make_tokenizer(), lock=threading.Lock())
    monkeypatch.setattr(local_inference, 'load', lambda name, **kwargs: fake)
    return scorer


def test_the_scorer_returns_the_probability_of_consistency_per_pair_in_input_order(monkeypatch):
    session = FakeEntailmentSession()
    scorer = scorer_with(session, monkeypatch)
    scores = scorer.score(
        [
            ('lamp burned all night', 'lamp burned'),
            ('glass held', 'storm rose'),
            ('tide rose storm lamp burned all night glass held', 'tide rose'),
        ]
    )
    assert scores[0] == scores[2] == pytest.approx(0.9820, abs=1e-3) and scores[1] == pytest.approx(
        0.0180, abs=1e-3
    )
    assert scorer.identity == 'local:hhem-2.1-open-int8@1' and scorer.score([]) == []
    # Batches are padded to their longest member, with the mask marking real tokens.
    feed = session.feeds[0]
    assert feed['input_ids'].shape == feed['attention_mask'].shape and feed['input_ids'].dtype.kind == 'i'
    assert any(m.min() == 0 for m in feed['attention_mask'])  # at least one row was padded
    assert all(
        row[mask == 0].sum() == 0 for row, mask in zip(feed['input_ids'], feed['attention_mask'], strict=True)
    )
    with pytest.raises(ByaxError):
        scorer.score([('premise', 3)])


def test_scoring_batches_and_keeps_the_claim_when_the_premise_must_be_trimmed(monkeypatch):
    session = FakeEntailmentSession()
    scorer = scorer_with(session, monkeypatch, max_tokens=14)
    scorer.MIN_PREMISE_CHARS = 10
    scorer.batch = 2
    premise = ' '.join(['lamp', 'burned', 'all', 'night', 'glass', 'held', 'tide', 'rose'] * 5)
    scores = scorer.score([(premise, 'storm'), (premise, 'lamp'), (premise, 'tide rose'), ('lamp', 'storm')])
    assert len(session.feeds) == 2  # four pairs, batch of two
    for feed in session.feeds:
        assert feed['input_ids'].shape[1] <= 14
        for ids, mask in zip(feed['input_ids'], feed['attention_mask'], strict=True):
            tokens = [int(t) for t, m in zip(ids, mask, strict=True) if m]
            assert VOCAB['hypothesis'] in tokens  # the claim end of the prompt is never cut off
    assert scores[1] > 0.9 and scores[0] < 0.1


def test_the_scorer_refuses_a_model_that_is_not_an_entailment_checker():
    with pytest.raises(ByaxError):
        local_inference.EntailmentScorer('answerai-colbert-small-v1')
    with pytest.raises(ByaxError):
        local_inference.EntailmentScorer('no-such-model')


# --- the catalogue entry ------------------------------------------------------------------------------------------


def test_the_catalogue_entry_is_licensed_pinned_and_not_bundled():
    catalogue = local_models.load_catalogue()
    entry = local_models.get_model('hhem-2.1-open-int8', catalogue)
    assert entry['kind'] == 'entailment' and entry['licence']['spdx'] == 'Apache-2.0'
    assert (
        entry['bundled'] is False
    )  # licence passes; size (167 MB) and measured agreement do not justify bundling
    assert entry['upstream']['revision'] == 'de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8'
    assert entry['upstream']['export_recipe'] == 'scripts/export_hhem_onnx.py'
    model_file = next(f for f in entry['files'] if f['path'] == 'model.onnx')
    assert model_file['mirror_only'] and len(model_file['sha256']) == 64 and model_file['size'] > 100_000_000
    assert {f['path'] for f in entry['files']} == {
        'model.onnx',
        'tokenizer.json',
        'MODEL-CARD.md',
        'BASE-MODEL-CARD.md',
    }
    assert 0 < entry['runtime']['unsupported_at'] < entry['runtime']['supported_at'] < 1
    assert local_models.load_settings()['entailer'] == 'hhem-2.1-open-int8'
    assert local_models.save_settings({'entailer': 'hhem-2.1-open-int8'})['entailer'] == 'hhem-2.1-open-int8'
    with pytest.raises(ByaxError):
        local_models.save_settings({'entailer': 'answerai-colbert-small-v1'})


def test_a_mirror_only_model_cannot_be_downloaded_before_the_mirror_exists():
    catalogue = local_models.load_catalogue()
    for source in ('auto', 'upstream', 'mirror'):
        with pytest.raises(ByaxError) as caught:
            local_models.download_plan('hhem-2.1-open-int8', source=source, catalogue=catalogue)
        assert caught.value.code == 'unsupported_feature' and 'mirror' in caught.value.message
    status = execute('models', {'action': 'status', 'name': 'hhem-2.1-open-int8'})
    assert status['plan'] is None and 'mirror' in status['plan_unavailable']
    populated = copy.deepcopy(catalogue)
    populated['mirror']['populated'] = True
    plan = local_models.download_plan('hhem-2.1-open-int8', catalogue=populated)
    urls = {f['path']: f['url'] for f in plan['files']}
    assert plan['source'] == 'mirror' and urls['model.onnx'].startswith(populated['mirror']['base_url'])
    assert plan['licence']['spdx'] == 'Apache-2.0' and all(f['sha256'] for f in plan['files'])
