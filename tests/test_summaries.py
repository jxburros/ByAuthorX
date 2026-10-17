"""Hierarchical chapter and book summaries: opt-in, cached by content hash, never in the document."""

import copy
import importlib.util
from pathlib import Path

import pytest

from byax import context_assembly, engine, summaries
from byax.cli import execute
from byax.errors import ByaxError
from byax.model import new_document
from byax.providers import ProviderConfig, ProviderRegistry
from byax.storage import load_document, save_document

ROOT = Path(__file__).resolve().parents[1]


def _benchmark():
    spec = importlib.util.spec_from_file_location(
        'router_benchmark', ROOT / 'scripts' / 'router_benchmark.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCH = _benchmark()


class FakeRegistry:
    """Stands in for ProviderRegistry.chat; records every request it is given."""

    def __init__(self):
        self.calls = []

    def chat(self, alias, messages, *, mode='chat', context=None, authorized=False, cancel_event=None):
        self.calls.append({'messages': messages, 'context': copy.deepcopy(context), 'mode': mode})
        section = (context or {}).get('section') or (context or {}).get('book') or {}
        part = f' part {section["part"]}' if section.get('part') else ''
        return {'reply': f'Summary of {section.get("title", "?")}{part}.', 'provider': alias, 'model': 'fake'}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))


@pytest.fixture
def novel():
    return BENCH.fixture_document()


def test_sections_are_binder_chapters_or_top_level_headings(novel):
    found = summaries.sections(novel)
    assert [s['title'] for s in found] == ['Chapter One', 'Chapter Two', 'Chapter Three', 'Chapter Four']
    assert all(s['kind'] == 'chapter' and len(s['hash']) == 64 for s in found)
    plain = new_document('Plain', 'One\nText one.\nTwo\nText two.')
    for index in (0, 2):
        plain['blocks'][index].update(type='heading', style='heading1', attrs={'level': 1})
    assert [s['title'] for s in summaries.sections(plain)] == ['One', 'Two']
    assert summaries.sections(new_document('Flat', 'Just text.'))[0]['id'] == 'document'


def test_build_is_bottom_up_cached_and_invalidated_by_edits(novel, tmp_path):
    cache = summaries.SummaryCache(tmp_path / 'summaries')
    registry = FakeRegistry()
    original = copy.deepcopy(novel)
    report = summaries.build(novel, 'fake', registry=registry, cache=cache)
    assert len(report['built']) == 4 and report['book'] == 'built' and report['requests'] == 5
    assert novel == original  # never written into the document
    assert cache.path(novel).is_file() and str(tmp_path) in str(cache.path(novel))
    # The section text is sent as untrusted data with a fixed instruction.
    first = registry.calls[0]
    assert first['mode'] == 'chat' and 'never instructions' in first['messages'][0]['content']
    assert 'Mara Quill held her suitcase' in first['context']['section']['text']
    assert (
        registry.calls[-1]['context']['book']['chapter_summaries'][0]['summary'] == 'Summary of Chapter One.'
    )
    status = summaries.status(novel, cache)
    assert status['counts'] == {'fresh': 4, 'stale': 0, 'missing': 0} and status['book']['state'] == 'fresh'
    again = summaries.build(novel, 'fake', registry=registry, cache=cache)
    assert again['built'] == [] and len(again['reused']) == 4 and again['requests'] == 0
    edited, _ = engine.apply(
        novel, {'operations': [{'type': 'set_text', 'target': 'c2s1p1', 'text': 'The lamp held all night.'}]}
    )
    status = summaries.status(edited, cache)
    states = {row['title']: row['state'] for row in status['sections']}
    assert states['Chapter Two'] == 'stale' and states['Chapter One'] == 'fresh'
    assert status['book']['state'] == 'stale'
    gathered = summaries.collected(edited, cache)
    assert [c['title'] for c in gathered['chapters']] == ['Chapter One', 'Chapter Three', 'Chapter Four']
    assert gathered['stale'] and gathered['book'] is None  # a stale summary is never used


def test_long_chapters_are_summarized_in_parts_then_combined(tmp_path):
    long = BENCH.fixture_document(repeat=60)
    section = summaries.sections(long)[0]
    assert section['characters'] > summaries.SECTION_CHARS
    requests = summaries.requests_for(long, section)
    assert len(requests) > 1 and all(len(r[1]['section']['text']) <= summaries.PART_CHARS for r in requests)
    registry = FakeRegistry()
    report = summaries.build(long, 'fake', registry=registry, sections_wanted=[section['id']], book=False)
    leaves = [c for c in registry.calls if 'part' in (c['context'].get('section') or {})]
    node = registry.calls[len(leaves)]
    assert len(leaves) == len(requests) and len(node['context']['section']['part_summaries']) == len(leaves)
    assert report['built'] == [section['id']]
    assert summaries.status(long)['sections'][0]['parts'] == len(leaves)


def test_remote_provider_needs_authorization_and_nothing_is_cached(novel, tmp_path):
    registry = ProviderRegistry(tmp_path / 'config')
    registry.save(ProviderConfig('cloud', 'openai', 'm'))
    cache = summaries.SummaryCache(tmp_path / 'summaries')
    with pytest.raises(ByaxError, match='authorization'):
        summaries.build(novel, 'cloud', registry=registry, cache=cache)
    assert summaries.status(novel, cache)['counts']['fresh'] == 0
    shown = summaries.preview(novel, 'cloud', registry=registry)
    assert shown['sent'] is False and shown['preview']['remote'] is True


def test_routes_use_fresh_summaries(novel, tmp_path):
    cache = summaries.SummaryCache(tmp_path / 'summaries')
    summaries.build(novel, 'fake', registry=FakeRegistry(), cache=cache)
    selection = BENCH._selection(novel, 'c2s1p2')
    local = ProviderConfig('local', 'ollama', 'm')
    r1 = context_assembly.assemble(
        novel, 'What is she afraid of?', selection=selection, provider=local, summary_cache=cache
    )
    summary = next(i for i in r1['items'] if i['kind'] == 'chapter_summary')
    assert summary['payload']['summary'] == 'Summary of Chapter Two.'
    assert summary['payload']['source'] == 'cached summary'


def test_a_summary_becomes_a_synopsis_only_through_an_accepted_proposal(novel, tmp_path):
    from byax import project

    cache = summaries.SummaryCache(tmp_path / 'summaries')
    item = project.item_id_for('c3')
    with pytest.raises(ByaxError, match='no fresh summary'):
        summaries.synopsis_proposal(novel, item, cache)
    summaries.build(novel, 'fake', registry=FakeRegistry(), cache=cache)
    values = summaries.synopsis_proposal(novel, item, cache)
    proposed, _ = engine.apply(novel, {'operations': [{'type': 'propose', 'values': values}]})
    assert project.find_item(proposed, item).get('synopsis') is None
    proposal = proposed['review']['proposals'][-1]
    assert proposal['provenance']['model'] == 'fake'
    accepted, _ = engine.apply(
        proposed, {'operations': [{'type': 'accept_proposal', 'target': proposal['id']}]}
    )
    assert project.find_item(accepted, item)['synopsis'] == 'Summary of Chapter Three.'


def test_summaries_command_status_show_build_preview_and_propose(novel, tmp_path, monkeypatch):
    path = str(tmp_path / 'novel.byax')
    save_document(novel, path)
    status = execute('summaries', {'path': path})
    assert status['counts']['missing'] == 4 and 'never written into the document' in status['note']
    ProviderRegistry(tmp_path / 'config').save(ProviderConfig('local', 'ollama', 'm'))
    dry = execute(
        'summaries',
        {
            'path': path,
            'action': 'build',
            'alias': 'local',
            'dry_run': True,
            'config_dir': str(tmp_path / 'config'),
        },
    )
    assert dry['sent'] is False and dry['preview']['context']['document']['section']['title'] == 'Chapter One'
    with pytest.raises(ByaxError):
        execute('summaries', {'path': path, 'action': 'build'})
    fake = FakeRegistry()
    monkeypatch.setattr('byax.providers.ProviderRegistry.chat', lambda self, *a, **k: fake.chat(*a, **k))
    built = execute(
        'summaries',
        {'path': path, 'action': 'build', 'alias': 'local', 'config_dir': str(tmp_path / 'config')},
    )
    assert len(built['built']) == 4
    shown = execute('summaries', {'path': path, 'action': 'show', 'limit': 2})
    assert len(shown['chapters']) == 2 and shown['page']['continuation'] == 2 and shown['book']
    from byax import project

    item = project.item_id_for('c1')
    proposed = execute('summaries', {'path': path, 'action': 'propose-synopsis', 'target': item})
    assert proposed['synopsis'] == 'Summary of Chapter One.'
    saved = load_document(path)
    assert saved['review']['proposals'][-1]['status'] == 'pending'
    assert execute('summaries', {'path': path, 'action': 'clear'})['cleared'] is True
    assert execute('summaries', {'path': path})['counts']['missing'] == 4
