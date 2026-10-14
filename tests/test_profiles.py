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
