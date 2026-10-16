"""Codex entries: validation, canonical operations, mention detection, AI visibility and proposals."""

import copy
import json

import pytest

from byax import codex, engine, fiction_checks, series
from byax.checks import run_checks
from byax.cli import execute
from byax.errors import ByaxError
from byax.exchange import export_document
from byax.mcp import handle
from byax.model import new_document, validate_document
from byax.publication import export_clean
from byax.storage import load_document, save_document
from byax.transport_policy import check_provider_admin

SECRET = 'Zephyrine-Moonglass'  # a hidden trait value that must never reach a model


def manuscript():
    doc = new_document(
        'Novel',
        'Chapter One\n'
        'Lizzy laughed, and Elizabeth Bennet smiled at Mr. Darcy’s aunt.\n'
        'The darcy family; lizzy. Darcy House stood empty.\n'
        'Miss Bennet and the Miss Bennets walked to Meryton.\n'
        'Chapter Two\n'
        'Darcy wrote. Mara watched Mara’s lamp.',
    )
    for index in (0, 4):
        doc['blocks'][index].update(type='heading', style='heading1', attrs={'level': 1})
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'codex_add',
                    'id': 'cx_eliz',
                    'values': {
                        'kind': 'character',
                        'name': 'Elizabeth Bennet',
                        'aliases': ['Lizzy', 'Elizabeth', 'Eliza'],
                        'description': 'Second daughter.',
                        'traits': [
                            {'name': 'eyes', 'value': 'fine'},
                            {'name': 'secret', 'value': SECRET, 'hide_from_ai': True},
                        ],
                    },
                },
                {
                    'type': 'codex_add',
                    'id': 'cx_darcy',
                    'values': {
                        'kind': 'character',
                        'name': 'Mr. Darcy',
                        'aliases': ['Darcy'],
                        'excluded': ['Darcy House'],
                        'case_sensitive': False,
                    },
                },
                {
                    'type': 'codex_add',
                    'id': 'cx_jane',
                    'values': {
                        'kind': 'character',
                        'name': 'Jane Bennet',
                        'aliases': ['Miss Bennet'],
                        'description': 'Hidden-Jane-Notes',
                        'hide_from_ai': True,
                    },
                },
                {'type': 'codex_add', 'id': 'cx_meryton', 'values': {'kind': 'place', 'name': 'Meryton'}},
                {'type': 'codex_add', 'id': 'cx_mara', 'values': {'kind': 'character', 'name': 'Mara'}},
            ]
        },
    )
    return doc


def block(doc, index):
    return doc['blocks'][index]['id']


# Model, validation and operations


def test_operations_create_edit_and_remove_entries_with_dates_and_history():
    doc = manuscript()
    entry = codex.find_entry(doc, 'cx_eliz')
    assert entry['created'] == entry['updated'] and [t['id'] for t in entry['traits']] == [
        'trait_1',
        'trait_2',
    ]
    edited, result = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'codex_update',
                    'target': 'cx_eliz',
                    'values': {'aliases': ['Lizzy'], 'description': None},
                },
                {'type': 'codex_add_trait', 'target': 'cx_eliz', 'name': 'age', 'value': '20'},
                {
                    'type': 'codex_update_trait',
                    'target': 'cx_eliz',
                    'trait': 'trait_1',
                    'values': {'value': 'dark'},
                },
                {'type': 'codex_remove_trait', 'target': 'cx_eliz', 'trait': 'trait_2'},
            ]
        },
    )
    entry = codex.find_entry(edited, 'cx_eliz')
    assert entry['aliases'] == ['Lizzy'] and 'description' not in entry
    assert [(t['id'], t['name'], t['value']) for t in entry['traits']] == [
        ('trait_1', 'eyes', 'dark'),
        ('trait_3', 'age', '20'),
    ]
    assert entry['updated'] >= entry['created'] and 'cx_eliz' in result['changed_ids']
    # Versioned like any edit: the previous revision still has the old entry.
    before = engine.revision_document(edited, doc['revision'])
    assert codex.find_entry(before, 'cx_eliz')['traits'][1]['value'] == SECRET
    removed, _ = engine.apply(edited, {'operations': [{'type': 'codex_remove', 'target': 'cx_eliz'}]})
    assert codex.find_entry(removed, 'cx_eliz') is None
    restored, _ = engine.restore(removed, edited['revision'])
    assert codex.find_entry(restored, 'cx_eliz')['aliases'] == ['Lizzy']


@pytest.mark.parametrize(
    ('operation', 'code'),
    [
        ({'type': 'codex_add', 'values': {'kind': 'monster', 'name': 'X'}}, 'schema_error'),
        ({'type': 'codex_add', 'values': {'kind': 'term', 'name': '  '}}, 'schema_error'),
        (
            {'type': 'codex_add', 'values': {'kind': 'term', 'name': 'A', 'aliases': ['b', 'B']}},
            'schema_error',
        ),
        ({'type': 'codex_add', 'values': {'kind': 'term', 'name': 'A', 'colour': 'red'}}, 'schema_error'),
        ({'type': 'codex_add', 'id': 'cx_eliz', 'values': {'kind': 'term', 'name': 'A'}}, 'conflict'),
        ({'type': 'codex_update', 'target': 'cx_nobody', 'values': {}}, 'missing_target'),
        (
            {'type': 'codex_update_trait', 'target': 'cx_eliz', 'trait': 'nope', 'values': {}},
            'missing_target',
        ),
        (
            {'type': 'codex_add_trait', 'target': 'cx_eliz', 'name': 'x', 'value': 'y', 'hide_from_ai': 1},
            'schema_error',
        ),
    ],
)
def test_invalid_codex_operations_are_rejected_atomically(operation, code):
    doc = manuscript()
    with pytest.raises(ByaxError) as error:
        engine.apply(doc, {'operations': [operation]})
    assert error.value.code == code


def test_validation_rejects_malformed_codex_and_identity_collisions():
    doc = manuscript()
    broken = copy.deepcopy(doc)
    broken['codex']['entries']['cx_eliz']['traits'][0]['hide_from_ai'] = 'yes'
    with pytest.raises(ByaxError, match='hide_from_ai'):
        validate_document(broken)
    clash = copy.deepcopy(doc)
    first = clash['blocks'][1]['id']
    clash['codex']['entries'][first] = dict(clash['codex']['entries'].pop('cx_mara'), id=first)
    with pytest.raises(ByaxError, match='collides'):
        validate_document(clash)
    bad_series = copy.deepcopy(doc)
    bad_series['settings']['series'] = {'path': '/etc/passwd.byaxseries'}
    with pytest.raises(ByaxError, match='settings.series'):
        validate_document(bad_series)


# Mention detection


def test_mentions_respect_boundaries_case_exclusions_and_longest_match():
    doc = manuscript()
    found = codex.mentions(doc)
    pairs = [(m['entry'], m['surface']) for m in found['mentions']]
    assert pairs == [
        ('cx_eliz', 'Lizzy'),
        ('cx_eliz', 'Elizabeth Bennet'),  # longest match, not "Elizabeth"
        ('cx_darcy', 'Mr. Darcy'),  # possessive with a curly apostrophe
        ('cx_darcy', 'darcy'),  # case-insensitive entry; "Darcy\u00a0House" is excluded
        ('cx_jane', 'Miss Bennet'),  # "the Miss Bennets" is not a mention
        ('cx_meryton', 'Meryton'),
        ('cx_darcy', 'Darcy'),
        ('cx_mara', 'Mara'),
        ('cx_mara', 'Mara'),
    ]
    # "darcy family" matches (Darcy is case-insensitive), lowercase "lizzy" does not (case-sensitive),
    # and "Darcy House" is an excluded phrase.
    scanned = codex.mentions(doc, {'target': block(doc, 2)})
    assert [(m['surface'], m['scope']) for m in scanned['mentions']] == [('darcy', 'selection')]
    text = codex.mentions(doc, 'Eliza met Elizabeth; Elizabethan drama.')
    assert [m['surface'] for m in text['mentions']] == ['Eliza', 'Elizabeth']
    assert [(e['id'], e['mentions']) for e in found['entries'][:2]] == [('cx_darcy', 3), ('cx_eliz', 2)]


def test_selection_and_nearby_scopes_and_pagination():
    doc = manuscript()
    target = block(doc, 1)
    result = codex.mentions(doc, {'target': target, 'start': 0, 'end': 5, 'radius': 1})
    scopes = [(m['surface'], m['scope']) for m in result['mentions']]
    assert scopes[0] == ('Lizzy', 'selection') and ('Elizabeth Bennet', 'nearby') in scopes
    assert ('darcy', 'nearby') in scopes and all(m['surface'] != 'Meryton' for m in result['mentions'])
    page = codex.mentions(doc, limit=3)
    assert len(page['mentions']) == 3 and page['truncated'] and page['continuation'] == 3
    rest = codex.mentions(doc, limit=100, offset=3)
    assert rest['total'] == page['total'] and not rest['truncated']
    with pytest.raises(ByaxError):
        codex.mentions(doc, {'target': target, 'start': 3, 'end': 999})


def test_pure_python_automaton_matches_a_naive_search():
    automaton = codex.Automaton()
    keys = ['he', 'she', 'his', 'hers', 'ushers', 'h']
    for key in keys:
        automaton.add(key, key)
    automaton.build()
    text = 'ushers said his hers she he'
    found = sorted((s, e, v) for s, e, v in automaton.iter(text))
    naive = sorted((i, i + len(k), k) for k in keys for i in range(len(text)) if text.startswith(k, i))
    assert found == naive


def test_native_automaton_is_optional():
    entries = {'cx': {'id': 'cx', 'kind': 'term', 'name': 'lamp', 'case_sensitive': False}}
    assert [m['surface'] for m in codex.scan('A Lamp, lamps, lamp.', entries, native=False)] == [
        'Lamp',
        'lamp',
    ]
    try:
        import ahocorasick  # noqa: F401
    except ImportError:
        with pytest.raises(ByaxError):
            codex.scan('lamp', entries, native=True)
        assert codex.automaton_backend() == 'python'
    else:
        assert codex.scan('A Lamp, lamps, lamp.', entries, native=True) == codex.scan(
            'A Lamp, lamps, lamp.', entries, native=False
        )


def test_heatmap_counts_mentions_per_binder_item():
    doc = manuscript()
    heat = codex.heatmap(doc)
    assert heat['grouping'] == 'binder'
    first, second = heat['sections']
    assert first['title'] == 'Chapter One' and first['counts'] == {
        'cx_eliz': 2,
        'cx_darcy': 2,
        'cx_jane': 1,
        'cx_meryton': 1,
    }
    assert second['counts'] == {'cx_darcy': 1, 'cx_mara': 2}
    assert heat['entries'][0]['id'] == 'cx_darcy' and heat['unmentioned'] == []
    ai = codex.heatmap(doc, for_ai=True)
    assert 'cx_jane' not in json.dumps(ai)


def test_lookup_by_identity_name_alias_case_and_fuzzy():
    doc = manuscript()
    assert codex.lookup(doc, 'cx_mara')['match'] == 'id'
    assert codex.lookup(doc, 'Lizzy')['entry']['id'] == 'cx_eliz'
    assert codex.lookup(doc, 'mr. darcy')['match'] == 'folded'
    fuzzy = codex.lookup(doc, 'Elizabth Benet')
    assert fuzzy['entry'] is None and fuzzy['candidates'][0]['id'] == 'cx_eliz'
    hidden = codex.lookup(doc, 'Jane Bennet', include_hidden=False)
    assert hidden['entry'] is None and all(c['id'] != 'cx_jane' for c in hidden['candidates'])


# Hide from AI


def _payload_has_no_hidden_data(payload):
    text = json.dumps(payload, ensure_ascii=False)
    assert SECRET not in text and 'Hidden-Jane-Notes' not in text and 'cx_jane' not in text


def test_ai_views_never_include_hidden_entries_or_traits():
    doc = manuscript()
    visible = codex.ai_visible_entries(doc)
    assert {e['id'] for e in visible} == {'cx_eliz', 'cx_darcy', 'cx_meryton', 'cx_mara'}
    _payload_has_no_hidden_data(visible)
    context = codex.ai_context(doc, {'target': block(doc, 3), 'radius': 2})
    _payload_has_no_hidden_data(context)
    entries = {e['id']: e for e in context['entries']}
    assert context['hidden_omitted'] == 1 and entries['cx_eliz']['traits'] == [
        {'name': 'eyes', 'value': 'fine'}
    ]
    payload = {
        'document': {'codex': copy.deepcopy(doc['codex'])},
        'entries': list(doc['codex']['entries'].values()),
    }
    redacted = codex.redact_for_ai(doc, payload)
    _payload_has_no_hidden_data(redacted)
    assert set(redacted['document']['codex']['entries']) == {'cx_eliz', 'cx_darcy', 'cx_meryton', 'cx_mara'}
    assert codex.redact_for_ai(doc, {'values': [None, 1, {'a': None}]}) == {'values': [None, 1, {'a': None}]}


def test_cli_context_and_transport_tools_hide_codex_data(tmp_path):
    path = str(tmp_path / 'novel.byax')
    save_document(manuscript(), path)
    _payload_has_no_hidden_data(execute('context', {'path': path}))
    for action in ('list', 'mentions', 'heatmap'):
        _payload_has_no_hidden_data(execute('codex', {'path': path, 'action': action}))
        response = handle(
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'byax_entity', 'arguments': {'path': path, 'action': action}},
            }
        )['result']
        assert not response['isError']
        _payload_has_no_hidden_data(response['structuredContent'])
    lookup = handle(
        {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {'name': 'byax_entity', 'arguments': {'path': path, 'name': 'Elizabeth Bennet'}},
        }
    )['result']['structuredContent']
    assert lookup['entry']['traits'] == [
        {k: v for k, v in codex.find_entry(load_document(path), 'cx_eliz')['traits'][0].items()}
    ]
    _payload_has_no_hidden_data(lookup)
    # Only the local CLI may ask for hidden entries; transports refuse the flag.
    local = execute('codex', {'path': path, 'action': 'get', 'target': 'cx_jane', 'include_hidden': True})
    assert local['entry']['description'] == 'Hidden-Jane-Notes'
    with pytest.raises(ByaxError):
        execute('codex', {'path': path, 'action': 'get', 'target': 'cx_jane'})
    with pytest.raises(ByaxError, match='hidden from AI'):
        check_provider_admin('codex', {'path': path, 'action': 'list', 'include_hidden': True})
    refused = handle(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {'name': 'byax_codex', 'arguments': {'path': path, 'include_hidden': True}},
        }
    )['result']
    assert refused['isError']
    tools = {
        t['name']: t for t in handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/list'})['result']['tools']
    }
    assert tools['byax_entity']['annotations']['readOnlyHint'] is True
    with pytest.raises(ByaxError, match='read-only'):
        execute('entity', {'path': path, 'action': 'add', 'name': 'X'})


def test_gui_assistant_context_is_redacted(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    from byax.gui import MainWindow

    settings = QSettings(str(tmp_path / 'codex.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    window = MainWindow()
    qtbot.addWidget(window)
    window.doc = manuscript()
    window.refresh()
    real = engine.context

    def leaky(doc, **kwargs):
        # A context builder that (wrongly) carried the codex must still be redacted.
        return dict(real(doc, **kwargs), codex=copy.deepcopy(doc['codex']))

    monkeypatch.setattr(engine, 'context', leaky)
    context = window.ai_panel.context(copy.deepcopy(window.doc))
    _payload_has_no_hidden_data(context)
    assert 'cx_eliz' in context['codex']['entries']


# Proposals


def test_assistant_facts_become_review_proposals_with_provenance():
    doc = manuscript()
    provenance = {'target': block(doc, 1), 'start': 0, 'end': 5, 'quote': 'Lizzy', 'label': 'ch. 1 ¶2'}
    operation = codex.propose_fact(doc, entry='Lizzy', trait='eyes', value='dark', provenance=provenance)
    assert (
        operation['type'] == 'propose'
        and operation['values']['operations'][0]['type'] == 'codex_update_trait'
    )
    proposed, _ = engine.apply(doc, {'operations': [operation]})
    assert codex.find_entry(proposed, 'cx_eliz') == codex.find_entry(doc, 'cx_eliz')  # nothing written yet
    proposal = proposed['review']['proposals'][-1]
    assert (
        'eyes "fine" → "dark" (ch. 1 ¶2)' in proposal['explanation']
        and 'cx_eliz' in proposal['preconditions']
    )
    accepted, _ = engine.apply(
        proposed, {'operations': [{'type': 'accept_proposal', 'target': proposal['id']}]}
    )
    trait = codex.find_entry(accepted, 'cx_eliz')['traits'][0]
    assert trait['value'] == 'dark' and trait['provenance']['quote'] == 'Lizzy'
    assert trait['provenance']['origin'] == 'assistant' and trait['provenance']['proposed_at']
    assert trait['updated'] > trait['created']
    rejected, _ = engine.apply(
        proposed, {'operations': [{'type': 'reject_proposal', 'target': proposal['id']}]}
    )
    assert codex.find_entry(rejected, 'cx_eliz') == codex.find_entry(doc, 'cx_eliz')
    # Unknown names propose a new entry; new traits use codex_add_trait.
    new = codex.propose_fact(doc, entry='Captain Harville', trait='ship', value='Laconia')
    assert new['values']['operations'][0]['type'] == 'codex_add'
    added = codex.propose_fact(doc, entry='Mara', trait='eyes', value='grey')
    assert added['values']['operations'][0]['type'] == 'codex_add_trait'
    # A proposal goes stale when the entry changes before review.
    changed, _ = engine.apply(
        proposed,
        {'operations': [{'type': 'codex_update', 'target': 'cx_eliz', 'values': {'tags': ['lead']}}]},
    )
    with pytest.raises(ByaxError):
        engine.apply(changed, {'operations': [{'type': 'accept_proposal', 'target': proposal['id']}]})


def test_cli_codex_edits_and_proposals(tmp_path):
    path = str(tmp_path / 'novel.byax')
    save_document(manuscript(), path)
    added = execute(
        'codex',
        {
            'path': path,
            'action': 'add',
            'name': 'Harville',
            'kind': 'character',
            'input': {'aliases': ['Captain Harville']},
        },
    )
    identity = added['entries'][0]['id']
    execute(
        'codex', {'path': path, 'action': 'add-trait', 'target': identity, 'name': 'rank', 'value': 'Captain'}
    )
    execute(
        'codex', {'path': path, 'action': 'update', 'target': identity, 'input': {'description': 'A friend.'}}
    )
    entry = codex.find_entry(load_document(path), identity)
    assert entry['description'] == 'A friend.' and entry['traits'][0]['value'] == 'Captain'
    proposal = execute(
        'codex',
        {
            'path': path,
            'action': 'propose-fact',
            'name': 'Harville',
            'trait': 'ship',
            'value': 'Laconia',
            'input': {'provenance': {'label': 'ch. 2'}},
        },
    )
    doc = load_document(path)
    assert proposal['proposal'] == doc['review']['proposals'][-1]['id']
    assert len(codex.find_entry(doc, identity)['traits']) == 1
    execute('codex', {'path': path, 'action': 'remove', 'target': identity, 'dry_run': True})
    assert codex.find_entry(load_document(path), identity) is not None


# Fiction checks, exports, publication and migration


def test_codex_names_feed_the_name_spelling_check():
    doc = new_document('N', 'Quillan arrived. Quillan left. Quilan stayed.')
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'codex_add', 'values': {'kind': 'character', 'name': 'Quillan'}}]}
    )
    assert codex.spelling_names in fiction_checks.NAME_SOURCES
    assert 'Quillan' in fiction_checks.canonical_names(doc)
    report = run_checks(doc, ['fiction'], profile={'sensitivity': 'broad'}, limit=10**6)
    [finding] = [f for f in report['findings'] if f['rule'] == 'fiction.name_spelling']
    assert finding['text'] == 'Quilan' and 'known name' in finding['explanation']


def test_exports_report_codex_losses_and_clean_publication_drops_it(tmp_path):
    doc = manuscript()
    doc['settings']['series'] = {'path': '../saga.byaxseries'}
    result = export_document(doc, tmp_path / 'out.md')
    assert any(loss['code'] == 'codex_not_exported' for loss in result['losses'])
    export_clean(doc, tmp_path / 'clean.byax')
    published = load_document(tmp_path / 'clean.byax')
    assert 'codex' not in published and 'series' not in published['settings']


def test_schema_2_documents_with_an_unknown_codex_are_not_upgraded():
    from byax.migration import TRANSITIONS

    state = {'codex': {'something': 'else'}, 'blocks': []}
    with pytest.raises(ByaxError, match='codex'):
        TRANSITIONS[2][2](state)
    report = TRANSITIONS[2][2]({'blocks': []})
    assert {'kind': 'added_optional_structure', 'field': 'codex', 'count': 0} in report


# Series sharing


def test_series_codex_and_libraries_are_shared_with_document_precedence(tmp_path):
    saga = tmp_path / 'saga'
    book = saga / 'book-one'
    book.mkdir(parents=True)
    shared = series.new_series(
        'The Saga',
        {
            'cx_shared': {
                'id': 'cx_shared',
                'kind': 'place',
                'name': 'Harrow Point',
                'aliases': ['the Point'],
            },
            'cx_mara_series': {
                'id': 'cx_mara_series',
                'kind': 'character',
                'name': 'Mara',
                'description': 'series',
            },
        },
        [{'library_id': 'lib_' + '1' * 32, 'role': 'supplementary'}],
        ['book-one/book.byax'],
    )
    series.save(saga / 'saga.byaxseries', shared)
    with pytest.raises(ByaxError):
        series.save(saga / 'saga.byaxseries', shared)
    path = book / 'book.byax'
    save_document(manuscript(), path)
    attach = execute(
        'codex', {'path': str(path), 'action': 'series-attach', 'series_path': str(saga / 'saga.byaxseries')}
    )
    assert attach['changed_ids'] == ['settings']
    doc = load_document(path)
    assert doc['settings']['series'] == {'path': '../saga.byaxseries'}
    data = series.series_of(doc, path)
    effective = codex.effective_entries(doc, data)
    assert effective['cx_shared']['scope'] == 'series' and 'cx_mara_series' not in effective
    assert codex.shadowed_series_entries(doc, data) == ['cx_mara_series']
    listed = execute('codex', {'path': str(path), 'action': 'list'})
    assert {e['id'] for e in listed['entries']} >= {'cx_shared', 'cx_mara'}
    status = execute('codex', {'path': str(path), 'action': 'series'})['series']
    assert status['state'] == 'ok' and status['overridden'] == ['cx_mara_series']
    view = series.with_series(doc, path)
    assert view['settings']['libraries'][0]['scope'] == 'series' and 'cx_shared' in view['codex']['entries']
    assert 'cx_shared' not in doc.get('codex', {}).get('entries', {})
    assert codex.mentions(doc, 'Back at the Point.', series=data)['mentions'][0]['entry'] == 'cx_shared'
    # A series cannot point outside the project area, and transports may not attach one.
    outside = copy.deepcopy(doc)
    outside['settings']['series'] = {'path': '../../../elsewhere.byaxseries'}
    assert series.resolve(outside, path)['state'] == 'outside_project'
    for arguments in (
        {'path': str(path), 'action': 'series-attach', 'series_path': 'x.byaxseries'},
        {'path': str(path), 'action': 'series-detach'},
    ):
        with pytest.raises(ByaxError, match='local CLI'):
            check_provider_admin('codex', arguments)
    with pytest.raises(ByaxError, match='series'):
        check_provider_admin(
            'apply', {'input': {'operations': [{'type': 'set_settings', 'values': {'series': None}}]}}
        )
    exported = execute(
        'codex', {'path': str(path), 'action': 'series-export', 'out': str(tmp_path / 'mine.byaxseries')}
    )
    assert series.load(exported['series']['path'])['codex']['entries'].keys() == codex._entries(doc).keys()
    detached = execute('codex', {'path': str(path), 'action': 'series-detach'})
    assert detached['changed_ids'] == ['settings'] and series.resolve(load_document(path), path) is None


def test_series_files_are_validated():
    with pytest.raises(ByaxError):
        series.validate({'format': 'byax-series', 'version': 1, 'id': 's', 'documents': ['/abs.byax']})
    with pytest.raises(ByaxError) as error:
        series.validate({'format': 'byax-series', 'version': 9, 'id': 's'})
    assert error.value.code == 'unsupported_version'
    with pytest.raises(ByaxError):
        series.validate(
            {'format': 'byax-series', 'version': 1, 'id': 's', 'codex': {'entries': {'a': {'id': 'b'}}}}
        )
