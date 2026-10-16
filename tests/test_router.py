"""Router R0–R4, R-rev thresholds, R-meta scoping, escalation, privacy and the inspector (plan 8.3, 8.4)."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from byax import context_assembly, router
from byax.errors import ByaxError
from byax.providers import ProviderConfig, validate_chat_output
from byax.research_log import ResearchLog, cite_from_grounding, export_conversation, log_markdown

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / 'docs' / 'benchmarks' / '0.3-router.json'


def _benchmark():
    spec = importlib.util.spec_from_file_location(
        'router_benchmark', ROOT / 'scripts' / 'router_benchmark.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCH = _benchmark()
LOCAL = ProviderConfig('local', 'ollama', 'fixture-8k', max_context_tokens=8192)
REMOTE = ProviderConfig('cloud', 'openai', 'fixture-remote')


@pytest.fixture(scope='module')
def novel():
    return BENCH.fixture_document()


@pytest.fixture(scope='module')
def large():
    return BENCH.fixture_document(repeat=BENCH.LARGE_REPEAT)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))


def selection(doc, block):
    return BENCH._selection(doc, block)


# Route accuracy on the labelled request set (exit criterion)


def test_labelled_request_set_covers_every_route():
    rows = BENCH.load_requests()
    assert len(rows) >= 100
    assert len({row['id'] for row in rows}) == len(rows)
    counts = {route: sum(row['expected'] == route for row in rows) for route in router.ROUTES}
    assert all(count >= 15 for count in counts.values()), counts


def test_route_accuracy_matches_the_published_benchmark():
    result = BENCH.measure()
    assert result['accuracy'] >= 0.9, result['misses']
    assert all(route['accuracy'] >= 0.85 for route in result['per_route'].values()), result['per_route']
    published = json.loads(PUBLISHED.read_text(encoding='utf-8'))['routing']
    assert published['accuracy'] == result['accuracy']
    assert published['requests'] == result['requests']
    assert [m['id'] for m in published['misses']] == [m['id'] for m in result['misses']]


def test_decision_is_deterministic_and_explains_itself(novel):
    first = router.decide('Where does Tom keep the brass key?', novel, provider=LOCAL, libraries=[])
    second = router.decide('Where does Tom keep the brass key?', novel, provider=LOCAL, libraries=[])
    assert first == second
    assert first['route'] == 'R2' and first['reason'] and first['rule'] == 'entity_lookup'
    assert first['rules'][0]['rule'] == 'privacy'  # privacy always runs first
    assert [e['name'] for e in first['signals']['codex_hits']] == ['Tom Vane', 'brass key']
    assert 'not used' in first['learned_routing']


def test_override_replaces_the_suggestion_but_keeps_it(novel):
    decision = router.decide('Summarize the whole book.', novel, provider=LOCAL, libraries=[], override='R2')
    assert decision['route'] == 'R2' and decision['rule'] == 'override'
    assert decision['suggested']['route'] == 'R4'
    assert decision['reason'].startswith('Chosen by you')
    with pytest.raises(ByaxError):
        router.decide('Hello', novel, override='R9', libraries=[])


# R-rev: context-size-aware thresholds


def test_thresholds_follow_the_real_context_window():
    small = router.thresholds(LOCAL)
    assert small['top_k'] == 4 and small['radius'] == 2
    assert small['full_document_tokens'] < router.LOCAL_FULL_CONTEXT_TOKENS  # limited by the 8K window
    big_local = router.thresholds(
        ProviderConfig('l32', 'ollama', 'm', max_context_tokens=65536, context_tokens=65536)
    )
    assert big_local['top_k'] == 20 and big_local['full_document_tokens'] == router.LOCAL_FULL_CONTEXT_TOKENS
    verified = router.thresholds(ProviderConfig('long', 'anthropic', 'm', context_tokens=200_000))
    assert verified['full_document_tokens'] == 100_000 and 'verified' in verified['full_document_basis']
    unverified = router.thresholds(REMOTE)
    assert unverified['full_document_tokens'] <= router.LOCAL_FULL_CONTEXT_TOKENS
    middle = router.thresholds(ProviderConfig('l16', 'ollama', 'm', max_context_tokens=16384))
    assert 5 <= middle['top_k'] <= 15
    assert router.thresholds(None)['window'] is None


def test_full_document_only_below_the_model_threshold(novel, large):
    assert router.decide('Fix the typo please.', novel, provider=LOCAL, libraries=[])['route'] == 'R4'
    local = router.decide('Fix the typo please.', large, provider=LOCAL, libraries=[], cursor='c2s1p1')
    assert local['route'] == 'R1' and not local['thresholds']['fits_full_document']
    long_context = ProviderConfig('long', 'anthropic', 'm', context_tokens=200_000, max_input_chars=1_000_000)
    assert router.decide('Fix the typo please.', large, provider=long_context, libraries=[])['route'] == 'R4'


# Gathering per route


def test_r0_sends_the_selection_profile_and_protections_only(novel):
    sel = selection(novel, 'c1s1p2')
    assembled = context_assembly.assemble(novel, 'Tighten this paragraph.', selection=sel, provider=LOCAL)
    assert assembled['route']['route'] == 'R0'
    kinds = {item['kind'] for item in assembled['items']}
    assert kinds == {'selection', 'block'}
    context = assembled['context']
    assert [b['id'] for b in context['blocks']] == ['c1s1p2'] and 'nearby' not in context
    assert 'protections' in context and 'profile' in context
    edits = json.dumps(
        {
            'reply': 'Tighter.',
            'proposed_edits': [
                {'target': 'c1s1p2', 'start': 0, 'end': 4, 'expected_text': 'Mara', 'replacement': 'Miss'}
            ],
        }
    )
    assert validate_chat_output(edits, context)['proposals']


def test_r1_adds_nearby_blocks_codex_entries_and_the_chapter_summary(novel):
    sel = selection(novel, 'c2s2p2')  # mentions Tom, the key and Edwin (hidden from AI)
    assembled = context_assembly.assemble(novel, 'Why would he write this?', selection=sel, provider=LOCAL)
    assert assembled['route']['route'] == 'R1'
    names = [i['payload']['name'] for i in assembled['items'] if i['kind'] == 'codex_entry']
    assert 'Tom Vane' in names and 'Edwin Marsh' not in names
    assert 'Edwin Marsh' not in json.dumps(assembled['context'])
    assert any(d['reason'] == 'Hidden from AI' for d in assembled['dropped'])
    nearby = [i for i in assembled['items'] if i['kind'] == 'nearby']
    assert 1 <= len(nearby) <= 2 * router.RADIUS_SMALL
    # Chapter one has a binder synopsis; chapter two does not, and no summary was built.
    assert not [i for i in assembled['items'] if i['kind'] == 'chapter_summary']
    first = context_assembly.assemble(novel, 'What is she feeling?', selection=selection(novel, 'c1s1p2'))
    summary = [i for i in first['items'] if i['kind'] == 'chapter_summary']
    assert summary and summary[0]['payload']['source'] == 'binder synopsis'


def test_r1_escalates_to_r2_when_an_unknown_entity_is_named(novel):
    sel = selection(novel, 'c2s1p1')
    assembled = context_assembly.assemble(novel, 'Who is Bram?', selection=sel, provider=LOCAL)
    route = assembled['route']
    assert route['route'] == 'R2' and route['escalations'][0] == {
        'from': 'R1',
        'to': 'R2',
        'reason': route['escalations'][0]['reason'],
    }
    assert 'Bram' in route['escalations'][0]['reason']
    assert any(
        i['kind'] == 'codex_entry' and i['payload']['name'] == 'Bram Hollis' for i in assembled['items']
    )


def test_r2_looks_up_the_codex_first_then_searches_and_logs(novel):
    assembled = context_assembly.assemble(novel, 'Where does Tom keep the brass key?', provider=LOCAL)
    assert assembled['route']['route'] == 'R2'
    kinds = [i['kind'] for i in assembled['items']]
    assert kinds.index('codex_entry') < kinds.index('manuscript_passage')
    passages = [i for i in assembled['items'] if i['kind'] == 'manuscript_passage']
    assert len(passages) <= router.thresholds(LOCAL)['top_k']
    assert any('brass key' in p['payload']['text'] for p in passages)
    assert all(p['score_source'] for p in passages)
    log = assembled['research_log']
    assert log['route'] == 'R2' and log['flow']['queries'] == 1 and log['outcome'] == 'sufficient'
    assert any(d['policy'] == 'top_k' for d in assembled['dropped'])
    assert 'manuscript_passages' in assembled['context'] and 'blocks' not in assembled['context']


def test_r2_scopes_by_chapter_and_ranks_by_metadata(novel):
    assembled = context_assembly.assemble(
        novel, 'What does Tom say about the key in chapter 3?', provider=LOCAL
    )
    passages = [i for i in assembled['items'] if i['kind'] == 'manuscript_passage']
    assert passages and all(p['payload']['block_id'].startswith('c3') for p in passages)
    hits = [
        {'chunk_id': 'a', 'block_id': 'c1s1p1', 'library_id': None, 'text': ''},
        {'chunk_id': 'b', 'block_id': 'c3s1p2', 'library_id': None, 'text': ''},
    ]
    sig = router.signals('In chapter 3, what does Tom say?', novel)
    ordered, reasons = router.metadata_boosts(novel, hits, sig)
    assert [h['chunk_id'] for h in ordered] == ['b', 'a'] and reasons


def test_structure_questions_use_the_outline_without_retrieval(novel):
    assembled = context_assembly.assemble(novel, 'Which chapters are still marked draft?', provider=LOCAL)
    assert assembled['route']['route'] == 'R2' and assembled['route']['variant'] == 'structure'
    assert [i['kind'] for i in assembled['items']] == ['outline']
    statuses = {row['text']: row.get('status') for row in assembled['context']['outline']['items']}
    assert statuses['Chapter Three'] == 'draft'
    assert assembled['research_log'] is None


def test_r2_escalates_to_r3_when_the_evidence_is_insufficient(novel):
    assembled = context_assembly.assemble(novel, 'Who is Ada?', provider=LOCAL)
    route = assembled['route']
    # "Ada" is in the text, so the first search covers it; a name nobody wrote escalates.
    assert route['route'] == 'R2'
    missing = context_assembly.assemble(novel, 'Who is Ingrid Solberg?', provider=LOCAL)
    assert [e['to'] for e in missing['route']['escalations']] == ['R3', 'R4']
    assert missing['research_log']['route'] == 'R3'


def test_r3_runs_read_only_tools_and_records_a_research_log(novel):
    assembled = context_assembly.assemble(novel, 'Find every place Tom lies to Mara.', provider=LOCAL)
    log = assembled['research_log']
    assert assembled['route']['route'] == 'R3'
    tools = [step['tool'] for step in log['steps']]
    assert set(tools) <= {*router.READ_ONLY_TOOLS, 'verify_absence'}
    assert {'get_entity', 'search', 'semantic_search', 'read_range', 'get_outline'} <= set(tools)
    assert log['flow']['queries'] == len(log['steps']) and log['flow']['kept'] == len(log['kept'])
    assert any(s['tool'] == 'semantic_search' and s.get('notice') for s in log['steps'])
    absence = [i for i in assembled['items'] if i['kind'] == 'absence_check']
    assert absence and 'lies' in absence[0]['payload']['absent']


def test_r3_loop_refuses_any_tool_that_is_not_read_only(novel):
    tools = router.ReadTools(novel, libraries=[])
    loop = router.ToolLoop(tools, ResearchLog('R3', 'x'), max_calls=1)
    for name in ('apply', 'providers', 'send', 'write'):
        with pytest.raises(ByaxError) as error:
            loop.call(name)
        assert error.value.code == 'unsupported_feature'
    loop.call('get_entity', name='Tom')
    with pytest.raises(ByaxError) as error:
        loop.call('get_entity', name='Mara')
    assert error.value.code == 'limit_exceeded'

    class Writer:
        read_only = False

    with pytest.raises(ByaxError):
        router.ToolLoop(Writer(), ResearchLog('R3', 'x'))


def test_r3_abstains_after_verified_absence_when_nothing_fits(large):
    assembled = context_assembly.assemble(large, 'Does Ingrid ever mention the zeppelin?', provider=LOCAL)
    assert assembled['route']['route'] == 'R3'
    report = assembled['abstain']
    assert report is not None and report['absent'] and not report['found']
    assert report['searched']['manuscript'] is not None
    assert 'abstains' in assembled['route']['reason']
    assert assembled['research_log']['outcome'] == 'abstained after verified absence'


def test_r3_offers_literal_matches_instead_of_absence(novel):
    planner = lambda request, sig: []  # a planner that searches nothing
    assembled = context_assembly.assemble(novel, 'Is the lens ever cleaned?', provider=LOCAL, planner=planner)
    item = next(i for i in assembled['items'] if i['kind'] == 'absence_check')
    assert 'lens' in item['payload']['found'] and item['payload']['matches']
    assert assembled['abstain'] is None


def test_r4_snapshot_is_in_the_stable_prefix_and_valid_for_work_mode(novel):
    from byax.context_assembly import layout_prompt
    from byax.providers import chat_instruction

    assembled = context_assembly.assemble(novel, 'Summarize the whole book.', provider=LOCAL, mode='work')
    assert assembled['route']['route'] == 'R4'
    context = assembled['context']
    snapshot = context['manuscript_snapshot']
    assert len(snapshot['blocks']) == len(novel['blocks']) and 'blocks' not in context
    layout = layout_prompt(chat_instruction('work'), context, [{'role': 'user', 'content': 'x'}])
    assert [s['name'] for s in layout['system']][-1] == 'manuscript_snapshot'
    again = context_assembly.assemble(novel, 'What are the main themes?', provider=LOCAL, mode='work')
    stable = layout_prompt(chat_instruction('work'), again['context'], [{'role': 'user', 'content': 'y'}])
    assert stable['stable_sha256'] == layout['stable_sha256']  # identical prefix for provider caches
    edits = json.dumps(
        {
            'reply': 'ok',
            'proposed_edits': [
                {'target': 'c4s2p3', 'start': 0, 'end': 4, 'expected_text': 'That', 'replacement': 'This'}
            ],
        }
    )
    assert validate_chat_output(edits, context)['proposals']


def test_r4_too_large_uses_summaries_or_searches(large, tmp_path):
    from byax import summaries

    assembled = context_assembly.assemble(large, 'Summarize the whole book.', provider=LOCAL)
    assert [e['to'] for e in assembled['route']['escalations']] == ['R3']
    assert any(d['policy'] == 'summaries' for d in assembled['dropped'])
    cache = summaries.SummaryCache(tmp_path / 'summaries')
    summaries.build(large, 'fake', registry=FakeRegistry(), cache=cache)
    built = context_assembly.assemble(large, 'Summarize the whole book.', provider=LOCAL, summary_cache=cache)
    assert built['route']['route'] == 'R4' and not built['route']['escalations']
    kinds = [i['kind'] for i in built['items']]
    assert kinds.count('chapter_summary') == 4 and 'book_summary' in kinds and 'outline' in kinds
    assert 'chapter summaries are used' in built['route']['reason'].lower()


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def chat(self, alias, messages, *, mode='chat', context=None, authorized=False, cancel_event=None):
        self.calls.append(context)
        section = (context or {}).get('section') or (context or {}).get('book') or {}
        return {'reply': f'Summary of {section.get("title", "?")}.', 'provider': alias, 'model': 'fake'}


# Budgets, pins and exclusions


def test_budget_keeps_pinned_items_first_and_reports_drops(novel):
    tiny = ProviderConfig('tiny', 'ollama', 'm', max_context_tokens=1024)
    assembled = context_assembly.assemble(novel, 'Find every place Tom lies to Mara.', provider=tiny)
    assert assembled['tokens']['items'] <= assembled['tokens']['available']
    assert any(d['policy'] == 'budget' for d in assembled['dropped'])
    previous = context_assembly.assemble(novel, 'Where does Tom keep the brass key?', provider=LOCAL)
    passage = next(i for i in previous['items'] if i['kind'] == 'manuscript_passage')
    pinned = context_assembly.assemble(
        novel, 'Tighten this.', selection=selection(novel, 'c1s1p1'), pinned=[passage]
    )
    assert pinned['items'][0]['id'] == passage['id'] and pinned['items'][0]['pinned_by_you']
    excluded = context_assembly.assemble(
        novel, 'Where does Tom keep the brass key?', provider=LOCAL, excluded=[passage['id']]
    )
    assert passage['id'] not in {i['id'] for i in excluded['items']}
    assert any(d['id'] == passage['id'] and d['reason'] == 'Excluded by you' for d in excluded['dropped'])


# Privacy, libraries and the project index


@pytest.fixture
def libraries(tmp_path):
    from byax.libraries import LibraryIndex, LibraryRegistry

    registry = LibraryRegistry(tmp_path / 'libraries')
    cache = tmp_path / 'index-cache'
    made = {}
    for name, policy, text in (
        ('Open notes', 'ask', 'The brass key of Gull Point opens the lamp room.'),
        ('Diary', 'local_only', 'Private: Tom kept the brass key in his coat.'),
    ):
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'notes.txt').write_text(text, encoding='utf-8')
        definition = registry.create(name, [folder], retrieval_policy=policy)
        LibraryIndex(definition, cache).refresh()
        made[name] = definition
    return registry, cache, made


def _with_libraries(doc, made):
    doc = copy.deepcopy(doc)
    doc['settings']['libraries'] = [{'library_id': d['library_id'], 'name': d['name']} for d in made.values()]
    return doc


def test_privacy_rule_excludes_local_only_sources_from_remote_models(novel, libraries):
    registry, cache, made = libraries
    doc = _with_libraries(novel, made)
    options = {'use_libraries': True, 'registry': registry, 'cache_dir': cache}
    remote = context_assembly.assemble(
        doc, 'What do my sources say about the brass key?', provider=REMOTE, **options
    )
    privacy = remote['route']['privacy']
    assert privacy['action'] == 'exclude_sources' and privacy['excluded_libraries'] == [
        made['Diary']['library_id']
    ]
    sent = json.dumps(remote['context'])
    assert 'Private: Tom' not in sent and 'lamp room' in sent
    assert any(d['policy'] == 'privacy' for d in remote['dropped'])
    payload = context_assembly.inspector_payload(remote, provider=REMOTE)
    assert payload['privacy']['reason'].startswith('Diary is local-only')
    assert all(
        i['where'].startswith('Sent to cloud') for i in payload['items'] if i['kind'] == 'library_passage'
    )
    local = context_assembly.assemble(
        doc, 'What do my sources say about the brass key?', provider=LOCAL, **options
    )
    assert local['route']['privacy']['action'] == 'none' and 'Private: Tom' in json.dumps(local['context'])
    switched = context_assembly.assemble(
        doc,
        'What do my sources say about the brass key?',
        provider=REMOTE,
        privacy='local',
        local_providers=[{'alias': 'local', 'kind': 'ollama', 'model': 'fixture-8k', 'locality': 'local'}],
        **options,
    )
    assert switched['route']['privacy']['action'] == 'force_local_model' and switched['provider'] == 'local'
    assert 'Private: Tom' in json.dumps(switched['context'])
    assert switched['retrieval']['included'] and all(
        p['chunk_id'] in switched['retrieval']['allowed_citation_ids']
        for p in switched['retrieval']['included']
    )


def test_r2_searches_sibling_documents_through_the_project_index(novel, tmp_path):
    from byax.model import new_document
    from byax.project_index import ProjectIndex
    from byax.storage import save_document

    folder = tmp_path / 'project'
    folder.mkdir()
    path = folder / 'novel.byax'
    save_document(novel, path)
    save_document(
        new_document('Notes', 'Bram Hollis once sailed to Lindqvist Island.'), folder / 'notes.byax'
    )
    ProjectIndex(path, doc=novel).refresh(novel)
    assembled = context_assembly.assemble(
        novel, 'What do I know about Lindqvist Island?', provider=LOCAL, document_path=path
    )
    assert assembled['corpus']['manuscript'] == 'project index'
    assert any(
        i['payload'].get('path') == 'notes.byax'
        for i in assembled['items']
        if i['kind'] == 'manuscript_passage'
    )


# Inspector payload and research log


def test_inspector_payload_shows_route_items_model_cache_and_cost(novel):
    priced = ProviderConfig(
        'priced',
        'ollama',
        'm',
        cost_rates={'input_per_million': 2.0, 'output_per_million': 4.0, 'source': 'my notes'},
    )
    assembled = context_assembly.assemble(novel, 'Where does Tom keep the brass key?', provider=priced)
    before = context_assembly.inspector_payload(assembled, provider=priced)
    assert before['route']['route'] == 'R2' and before['route']['reason']
    assert before['pinned'] and before['retrieved'] and not before['sent']
    for item in before['items']:
        assert {'tokens', 'score_source', 'library', 'where', 'tier'} <= set(item)
    assert before['model']['locality'] == 'local' and before['model']['alias'] == 'priced'
    assert before['cost']['before_sending']['amount'] > 0 and before['cost']['after_reply'] is None
    assert before['cache']['read_tokens'] is None
    result = {
        'cache': {'read_tokens': 120, 'write_tokens': 0, 'strategy': 'kv reuse'},
        'cost_estimate': {'amount': 0.001, 'currency': 'USD', 'exact': False, 'rates_source': 'my notes'},
    }
    after = context_assembly.inspector_payload(assembled, provider=priced, result=result)
    assert after['sent'] and after['cache']['read_tokens'] == 120 and after['cost']['after_reply']['amount']
    assert 'opt-in' in after['ai_note']


def test_research_log_records_citations_and_exports(novel):
    assembled = context_assembly.assemble(novel, 'Find every place Tom lies to Mara.', provider=LOCAL)
    log = assembled['research_log']
    kept = next(i for i in log['kept'] if ':' in i)
    cited = cite_from_grounding(
        log, {'records': [{'chunk_id': kept, 'valid_id': True}, {'chunk_id': 'nope'}]}
    )
    assert cited['cited'] == [kept] and cited['flow']['cited'] == 1
    markdown = log_markdown(cited)
    assert '| Step | Tool |' in markdown and 'Cited: ' + kept in markdown
    payload = context_assembly.inspector_payload(dict(assembled, research_log=cited), provider=LOCAL)
    turn = {
        'request': 'Find every place',
        'route': payload['route'],
        'inspector': payload,
        'research_log': cited,
    }
    messages = [{'role': 'user', 'content': 'Find every place'}, {'role': 'assistant', 'content': 'Here.'}]
    text = export_conversation(messages, [turn], 'markdown')
    assert text == export_conversation(messages, [turn], 'markdown')
    assert '## What the AI saw' in text and '### Research log (R3)' in text
    data = json.loads(export_conversation(messages, [turn], 'json'))
    assert data['format'] == 'byax-conversation' and data['turns'][0]['research_log']['cited'] == [kept]
    with pytest.raises(ByaxError):
        export_conversation(messages, [], 'html')
