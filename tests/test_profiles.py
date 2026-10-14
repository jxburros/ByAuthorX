import copy
import json

import pytest

from byax import engine
from byax.checks import run_checks
from byax.errors import ByaxError
from byax.model import new_document
from byax.profiles import (
    check_suites,
    export_collection,
    filter_findings,
    import_collection,
    presets,
    validate_profile,
)


def test_presets_have_independent_controls_and_distinct_coverage():
    definitions = presets()
    conservative, editorial = definitions.values()
    assert conservative['sensitivity'] != editorial['sensitivity']
    assert conservative['editing_freedom'] != editorial['editing_freedom']
    assert conservative['voice_preservation'] == editorial['voice_preservation'] == 'strict'
    assert 'editorial' not in check_suites(conservative)
    assert 'editorial' in check_suites(editorial)
    changed = copy.deepcopy(conservative)
    changed['editing_freedom'] = 'broad'
    assert validate_profile(changed)['sensitivity'] == 'conservative'
    assert validate_profile(changed)['voice_preservation'] == 'strict'
    doc = new_document(text='The the phrase repeats.')
    first = run_checks(doc, profile=conservative)
    second = run_checks(doc, profile=editorial)
    assert not any(item['rule'] == 'word.repeated' for item in first['findings'])
    assert any(item['rule'] == 'word.repeated' for item in second['findings'])


@pytest.mark.parametrize(
    'changes',
    [
        {'sensitivity': 2},
        {'version': True},
        {'rule_families': ['made_up']},
        {'providers': {'review': 'https://provider.example'}},
        {'api_key': 'secret'},
        {'endpoint': 'https://provider.example'},
        {'application_policy': 'blindly_apply'},
        {'intentional_exceptions': [{'rule': '*'}]},
    ],
)
def test_invalid_profiles_and_secret_configuration_rejected(changes):
    profile = presets()['conservative_proofreading']
    profile.update(changes)
    with pytest.raises(ByaxError):
        validate_profile(profile)


def test_profile_collection_roundtrip_and_no_credentials(tmp_path):
    definitions = presets()
    definitions['editorial_review']['providers']['review'] = 'My local reviewer'
    path = tmp_path / 'profiles.json'
    export_collection(definitions, path)
    assert import_collection(path) == definitions
    data = json.loads(path.read_text())
    data['profiles']['editorial_review']['providers']['api_key'] = 'secret'
    path.write_text(json.dumps(data))
    with pytest.raises(ByaxError):
        import_collection(path)


def test_intentional_exception_remains_visible_and_cannot_hide_errors():
    profile = presets()['editorial_review']
    profile['intentional_exceptions'] = [{'rule': '*', 'target': 'p1', 'reason': 'Intentional dialect'}]
    report = {
        'validation': 'failed',
        'findings': [
            {
                'rule': 'word.repeated',
                'family': 'editorial',
                'affected_ids': ['p1'],
                'severity': 'advisory',
                'status': 'open',
            },
            {
                'rule': 'required.text',
                'family': 'requirements',
                'affected_ids': ['p1'],
                'severity': 'error',
                'status': 'open',
            },
        ],
        'summary': {'errors': 1, 'total': 2},
    }
    result = filter_findings(report, profile)
    assert len(result['findings']) == 2
    assert result['findings'][0]['status'] == 'intentional exception'
    assert result['findings'][1]['status'] == 'open'
    assert result['summary']['errors'] == 1 and result['validation'] == 'failed'
    assert report['findings'][0]['status'] == 'open'


def test_profile_commit_uses_engine_and_changes_revision():
    doc = new_document()
    updated, _ = engine.apply(
        doc,
        {
            'expected_revision': doc['revision'],
            'request_id': 'set-profile',
            'operations': [
                {
                    'type': 'set_profile',
                    'id': 'proofreading',
                    'values': presets()['conservative_proofreading'],
                }
            ],
        },
    )
    assert updated['revision'] != doc['revision']
    assert 'proofreading' in updated['profiles']
    assert 'proofreading' not in doc['profiles']


def test_native_profile_dialog_saves_canonical_profile(qtbot):
    from PySide6.QtWidgets import QLabel, QWidget

    from byax.gui_profiles import ProfileDialog

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.doc = new_document()
            self.status_label = QLabel()
            self.operations = []

        def commit(self, operations):
            self.operations.extend(operations)
            self.doc, _ = engine.apply(
                self.doc,
                {
                    'expected_revision': self.doc['revision'],
                    'request_id': 'gui-profile',
                    'operations': operations,
                },
            )
            return True

        def error(self, error):
            raise AssertionError(str(error))

    window = Window()
    qtbot.addWidget(window)
    dialog = ProfileDialog(window)
    qtbot.addWidget(dialog)
    dialog.name.setText('My voice')
    dialog.editing.setCurrentIndex(dialog.editing.findData('moderate'))
    dialog.save_profile()
    assert window.operations[0]['type'] == 'set_profile'
    saved = window.doc['profiles'][window.active_profile]
    assert saved['name'] == 'My voice'
    assert saved['editing_freedom'] == 'moderate'
    assert saved['sensitivity'] == 'conservative'
    assert saved['voice_preservation'] == 'strict'


def _sectioned():
    doc = new_document('Book', 'Intro the the.')
    doc['blocks'] += [
        {
            'id': 'h_lyrics',
            'type': 'heading',
            'text': 'Lyrics',
            'style': 'heading1',
            'marks': [],
            'attrs': {'level': 1},
        },
        {
            'id': 'p_lyric',
            'type': 'paragraph',
            'text': 'Oh oh gonna sing.',
            'style': 'body',
            'marks': [],
            'attrs': {},
        },
        {
            'id': 'h_verse',
            'type': 'heading',
            'text': 'Verse',
            'style': 'heading2',
            'marks': [],
            'attrs': {'level': 2},
        },
        {
            'id': 'p_verse',
            'type': 'paragraph',
            'text': 'La la land.',
            'style': 'body',
            'marks': [],
            'attrs': {},
        },
        {
            'id': 'h_notes',
            'type': 'heading',
            'text': 'Notes',
            'style': 'heading1',
            'marks': [],
            'attrs': {'level': 1},
        },
        {
            'id': 'p_notes',
            'type': 'paragraph',
            'text': 'Notes the the end.',
            'style': 'body',
            'marks': [],
            'attrs': {},
        },
    ]
    doc['profiles']['editorial'] = presets()['editorial_review']
    doc['profiles']['lyric'] = {
        **presets()['conservative_proofreading'],
        'name': 'Lyric voice',
        'dictionary': ['gonna'],
        'voice_preservation': 'strict',
    }
    return doc


def test_section_profiles_inherit_override_and_report_origins():
    from byax.profiles import effective_profile, request_settings

    doc = _sectioned()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'set_section_profile',
                    'target': 'h_lyrics',
                    'values': {
                        'profile': 'lyric',
                        'overrides': {
                            'intentional_exceptions': [
                                {'rule': 'word.repeated', 'reason': 'Lyric repetition'}
                            ]
                        },
                    },
                },
                {
                    'type': 'set_section_profile',
                    'target': 'h_verse',
                    'values': {'overrides': {'dictionary': ['la'], 'sensitivity': 'broad'}},
                },
            ]
        },
    )
    resolved = effective_profile(doc, 'p_verse', 'editorial')
    assert resolved['sections'] == ['h_lyrics', 'h_verse']
    assert resolved['profile']['dictionary'] == ['gonna', 'la']
    assert (
        resolved['profile']['sensitivity'] == 'broad'
        and resolved['origins']['sensitivity'] == 'section:h_verse'
    )
    assert resolved['origins']['voice_preservation'].startswith('section:h_lyrics/profile:lyric')
    assert effective_profile(doc, 'p_notes', 'editorial')['profile']['dictionary'] == []
    settings = request_settings(doc, 'p_lyric', 'editorial')
    assert (
        settings['voice']['editing_freedom'] == 'minimal'
        and settings['source_policy']['authority'] == 'exclusive'
    )
    doc['profiles']['editorial']['sensitivity'] = 'balanced'
    report = run_checks(doc, ['editorial', 'spelling'], profile='editorial')
    by_target = {}
    for finding in report['findings']:
        by_target.setdefault(finding['affected_ids'][0], []).append(finding)
    assert not [
        f for f in by_target.get('p_lyric', []) if f['rule'] == 'word.repeated'
    ]  # conservative lyric section
    verse = [f for f in by_target.get('p_verse', []) if f['rule'] == 'word.repeated']
    assert verse and verse[0]['status'] == 'intentional exception'  # inherited exception, broad override
    assert [f['status'] for f in by_target['p_notes'] if f['rule'] == 'word.repeated'] == ['open']
    assert not any(f['rule'] == 'spelling.unknown' and f['text'] == 'gonna' for f in report['findings'])
    package = engine.context(doc, 'p_verse', profile='editorial')
    assert package['effective_settings']['sections'] == ['h_lyrics', 'h_verse']
    assert package['profile']['dictionary'] == ['gonna', 'la']


def test_section_profile_scope_survives_moves_and_validation():
    from byax.profiles import effective_profile

    doc = _sectioned()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'set_section_profile', 'target': 'h_lyrics', 'values': {'profile': 'lyric'}}
            ]
        },
    )
    moved, _ = engine.apply(
        doc, {'operations': [{'type': 'move_section', 'target': 'h_lyrics', 'after': 'h_notes'}]}
    )
    assert [b['id'] for b in moved['blocks']][-4:] == ['h_lyrics', 'p_lyric', 'h_verse', 'p_verse']
    assert effective_profile(moved, 'p_lyric')['profile']['name'] == 'Lyric voice'
    assert effective_profile(moved, 'p_notes') is None
    for values in (
        {'profile': 'missing'},
        {'overrides': {'api_key': 'x'}},
        {'overrides': {'sensitivity': 'loud'}},
        {'unknown': 1},
    ):
        with pytest.raises(ByaxError):
            engine.apply(
                doc, {'operations': [{'type': 'set_section_profile', 'target': 'h_lyrics', 'values': values}]}
            )
    with pytest.raises(ByaxError):
        engine.apply(
            doc,
            {
                'operations': [
                    {'type': 'set_section_profile', 'target': 'p_lyric', 'values': {'profile': 'lyric'}}
                ]
            },
        )
    removed, _ = engine.apply(
        doc, {'operations': [{'type': 'set_section_profile', 'target': 'h_lyrics', 'values': {}}]}
    )
    assert removed['settings']['section_profiles'] == {}
    deleted, _ = engine.apply(doc, {'operations': [{'type': 'delete_block', 'target': 'h_lyrics'}]})
    assert any(
        f['rule'] == 'section_profile.orphaned' for f in run_checks(deleted, ['structure'])['findings']
    )


def test_presets_stay_independent_and_protected_passages_are_respected_per_section():
    doc = _sectioned()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'set_section_profile',
                    'target': 'h_notes',
                    'values': {'profile': 'editorial', 'inherit': False},
                },
                {'type': 'protect', 'target': 'p_notes', 'kind': 'text'},
            ]
        },
    )
    conservative = run_checks(doc, ['editorial'], profile=presets()['conservative_proofreading'])
    notes = [f for f in conservative['findings'] if f['affected_ids'] == ['p_notes']]
    assert notes and all(f['operations'] == [] and f['repair']['kind'] == 'blocked' for f in notes)
    assert not [f for f in conservative['findings'] if f['affected_ids'] != ['p_notes']]
    doc['settings']['requirements'] = {'r': {'kind': 'required_text', 'value': 'Missing'}}
    doc['profiles']['editorial']['intentional_exceptions'] = [
        {'rule': '*', 'reason': 'Everything is intentional'}
    ]
    report = run_checks(doc, ['requirements', 'editorial'], profile='editorial')
    assert report['validation'] == 'failed'
    assert all(f['status'] == 'open' for f in report['findings'] if f['family'] == 'requirements')


def test_section_assignments_persist_and_exchange_without_credentials(tmp_path):
    from byax.profiles import compile_collection, export_settings, read_collection, write_collection
    from byax.storage import load_document, save_document

    doc = _sectioned()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'set_section_profile', 'target': 'h_lyrics', 'values': {'profile': 'lyric'}}
            ]
        },
    )
    save_document(doc, tmp_path / 'book.byax')
    assert (
        load_document(tmp_path / 'book.byax')['settings']['section_profiles']['h_lyrics']['profile']
        == 'lyric'
    )
    collection = export_settings(doc, ['section_profiles'])
    write_collection(collection, tmp_path / 'sections.json')
    other = _sectioned()
    other['blocks'][1]['id'] = 'h_other_identity'
    other['profiles'].pop('lyric')
    compiled = compile_collection(other, read_collection(tmp_path / 'sections.json'))
    updated, _ = engine.apply(other, {'operations': compiled['operations']})
    assert updated['settings']['section_profiles']['h_other_identity']['profile'] == 'lyric'
    assert 'api_key' not in (tmp_path / 'sections.json').read_text()


def test_settings_collections_resolve_dependencies_aliases_and_conflicts(tmp_path):
    from byax.profiles import compile_collection, export_settings, read_collection, write_collection

    doc = new_document('Source')
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'define_style', 'name': 'base', 'values': {'font_size': 12}},
                {'type': 'define_style', 'name': 'callout', 'values': {'based_on': 'base', 'italic': True}},
                {
                    'type': 'set_settings',
                    'values': {'dictionary': ['ByAuthorX'], 'terminology': {'email': ['e-mail']}},
                },
                {'type': 'set_requirement', 'id': 'len', 'values': {'kind': 'max_words', 'value': 500}},
                {
                    'type': 'fill_fields',
                    'values': {'client': {'type': 'text', 'required': True, 'description': 'Client'}},
                },
            ]
        },
    )
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'add_reference',
                    'id': 'style_guide',
                    'values': {'title': 'House', 'text': 'Use email.'},
                }
            ]
        },
    )
    profile = {**presets()['editorial_review'], 'references': ['style_guide']}
    profile['providers']['review'] = 'Office reviewer'
    doc, _ = engine.apply(doc, {'operations': [{'type': 'set_profile', 'id': 'house', 'values': profile}]})
    collection = export_settings(
        doc, ['styles', 'profiles', 'terminology', 'fields', 'layout', 'requirements'], styles=['callout']
    )
    assert set(collection['collections']['styles']) == {'base', 'callout'}
    assert 'style_guide' in collection['collections']['sources']
    assert collection['requires']['provider_aliases'] == ['Office reviewer']
    path = tmp_path / 'house.json'
    write_collection(collection, path)
    target = new_document('Elsewhere')
    compiled = compile_collection(
        target, read_collection(path), aliases={'Office reviewer': 'Local model'}, available_providers=[]
    )
    assert {
        'kind': 'provider_alias',
        'profile': 'house',
        'role': 'review',
        'alias': 'Local model',
    } in compiled['report']['unresolved']
    source_ops = [op for op in compiled['operations'] if op['type'] == 'add_reference']
    applied, _ = engine.apply(target, {'operations': source_ops})
    applied, _ = engine.apply(
        applied, {'operations': [op for op in compiled['operations'] if op['type'] != 'add_reference']}
    )
    assert applied['styles']['callout']['based_on'] == 'base'
    assert applied['profiles']['house']['providers']['review'] is None
    assert applied['references']['style_guide']['text'] == 'Use email.'
    assert (
        applied['settings']['terminology'] == {'email': ['e-mail']}
        and applied['settings']['requirements']['len']['value'] == 500
    )
    assert applied['fields']['client']['required'] is True
    applied['styles']['callout'] = {'italic': False}
    with pytest.raises(ByaxError) as error:
        compile_collection(applied, read_collection(path))
    assert error.value.code == 'conflict'
    assert compile_collection(applied, read_collection(path), on_conflict='keep')['report']['kept']
    data = json.loads(path.read_text())
    data['collections']['sources']['style_guide']['text'] = 'Tampered'
    path.write_text(json.dumps(data))
    with pytest.raises(ByaxError):
        read_collection(path)
    data['collections']['sources']['style_guide'].pop('content_hash')
    data['collections']['sources']['style_guide']['password'] = 'x'
    path.write_text(json.dumps(data))
    with pytest.raises(ByaxError):
        read_collection(path)


def test_templates_typed_json_fill_and_outcomes_preserve_structure():
    from byax.profiles import instantiate_template, template_operations

    template = new_document('Letter', 'Dear {{name}}, total {{amount}} due {{due}}. Regards {{name}}.')
    target = template['blocks'][0]['id']
    template, _ = engine.apply(
        template,
        {
            'operations': [
                {'type': 'format_text', 'target': target, 'start': 0, 'end': 4, 'marks': {'bold': True}},
                {
                    'type': 'fill_fields',
                    'values': {
                        'name': {'type': 'text', 'required': True},
                        'amount': {'type': 'number', 'required': True},
                        'due': {'type': 'date', 'default': '2026-01-01'},
                        'tier': {'type': 'enum', 'options': ['a', 'b']},
                        'logo': {'type': 'asset', 'mime': ['image/']},
                    },
                },
                *template_operations(),
            ]
        },
    )
    filled, report = instantiate_template(
        template, {'name': 'Ada', 'amount': 'ten', 'tier': 'c', 'logo': 'nope', 'extra': 1}
    )
    assert report['fields']['name']['status'] == 'filled' and report['fields']['due']['status'] == 'default'
    assert {report['fields'][k]['status'] for k in ('amount', 'tier', 'logo')} == {'invalid'}
    assert report['fields']['extra']['status'] == 'unknown_field'
    assert report['validation'] == 'failed'
    text = filled['blocks'][0]['text']
    assert text.count('Ada') == 2 and '{{amount}}' in text and '2026-01-01' in text
    assert {'type': 'bold', 'value': True, 'start': 0, 'end': 4} in filled['blocks'][0]['marks']
    assert filled['metadata']['template']['id'] == template['id'] and template['blocks'][0][
        'text'
    ].startswith('Dear {{name}}')
    complete, report = instantiate_template(template, {'name': 'Ada', 'amount': 10})
    assert report['validation'] == 'passed' and complete['id'] != template['id']
    with pytest.raises(ByaxError):
        instantiate_template(new_document(), {})
