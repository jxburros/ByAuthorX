"""Regression evidence for the outstanding formatting and interface workflows."""

import copy

import pytest
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog

from byax import engine, model, profiles, storage
from byax.cli import execute
from byax.errors import ByaxError
from byax.gui import MainWindow
from byax.layout import build_document
from byax.transport_policy import check_provider_admin


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    w = MainWindow()
    monkeypatch.setattr(w, 'error', lambda error: pytest.fail(str(error)))
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


def test_character_style_renders_inherited_values_and_direct_overrides(app):
    doc = model.new_document(text='Styled text')
    doc['styles'].update(
        accent={'kind': 'character', 'italic': True, 'color': '#bb2200'},
        strong_accent={'kind': 'character', 'based_on': 'accent', 'bold': True},
    )
    doc['blocks'][0]['marks'] = [
        {'type': 'bold', 'value': False, 'start': 0, 'end': 6},
        {'type': 'character_style', 'value': 'strong_accent', 'start': 0, 'end': 6},
    ]
    qdoc = build_document(doc)
    cursor = QTextCursor(qdoc)
    cursor.setPosition(1)
    assert cursor.charFormat().fontItalic()
    assert cursor.charFormat().fontWeight() == QFont.Normal
    assert cursor.charFormat().foreground().color().name() == '#bb2200'


def test_distinct_list_identities_and_restart_render_actual_numbers(app):
    doc = model.new_document(text='A\nB\nA again\nRestart A')
    for block, identity in zip(doc['blocks'], ['a', 'b', 'a', 'a']):
        block['attrs'].update(list={'kind': 'ordered', 'level': 1}, list_id=identity)
    doc['blocks'][-1]['attrs'].update(restart=True, start=7)
    qdoc = build_document(doc)
    block = qdoc.begin()
    labels = []
    while block.isValid():
        labels.append(block.textList().itemText(block))
        block = block.next()
    assert labels == ['1.', '1.', '2.', '7.']


def test_statistics_distinguish_authored_output_and_selection(tmp_path):
    doc = model.new_document(text='Visible words\nHidden words')
    doc['blocks'][1]['attrs']['exclude_outputs'] = ['txt']
    path = tmp_path / 'stats.byax'
    storage.save_document(doc, path)
    result = execute(
        'statistics',
        {'path': str(path), 'format': 'txt', 'target': doc['blocks'][0]['id'], 'start': 0, 'end': 7},
    )
    assert result['authored']['words'] == 4
    assert result['publication']['words'] == 2
    assert result['selection']['words'] == 1


def test_settings_collection_cli_apply_and_conflict_preserve_master(tmp_path):
    template = model.new_document()
    template['styles']['accent'] = {'kind': 'character', 'bold': True}
    path = tmp_path / 'target.byax'
    storage.save_document(model.new_document(text='Keep content'), path)
    bundle = profiles.export_settings(template, ['styles'])
    result = execute('collection', {'path': str(path), 'action': 'apply', 'input': bundle})
    assert result['execution'] == 'completed'
    after = storage.load_document(path)
    assert after['styles']['accent']['bold']
    assert model.text_of(after) == 'Keep content'
    original = path.read_bytes()
    bundle['collections']['styles']['accent']['bold'] = False
    with pytest.raises(ByaxError, match='conflict'):
        execute('collection', {'path': str(path), 'action': 'apply', 'input': bundle})
    assert path.read_bytes() == original


def test_dependency_retrieval_requires_explicit_local_authorization(tmp_path):
    path = tmp_path / 'doc.byax'
    storage.save_document(model.new_document(), path)
    with pytest.raises(ByaxError, match='authorization'):
        execute('dependencies', {'path': str(path), 'action': 'refresh'})
    for command, args in [
        ('dependencies', {'action': 'refresh', 'authorize': True}),
        (
            'jobs',
            {
                'action': 'submit',
                'input': {'command': 'dependencies', 'arguments': {'action': 'refresh', 'authorize': True}},
            },
        ),
    ]:
        with pytest.raises(ByaxError, match='local CLI'):
            check_provider_admin(command, args)


def test_gui_character_style_and_override_inspector(window, monkeypatch):
    window.doc = model.new_document(text='Select me')
    window.doc['styles']['accent'] = {'kind': 'character', 'italic': True}
    window.refresh()
    window.editor.selectAll()
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('accent', True))
    window.character_style()
    assert window.doc['blocks'][0]['marks'][0]['type'] == 'character_style'
    captured = []
    monkeypatch.setattr(window, 'report', lambda title, report: captured.append(report))
    window.formatting_inspector()
    assert captured


def test_gui_collection_import_preserves_content(window, tmp_path, monkeypatch):
    template = model.new_document()
    template['styles']['accent'] = {'kind': 'character', 'italic': True}
    path = tmp_path / 'settings.json'
    profiles.write_collection(profiles.export_settings(template, ['styles']), path)
    answers = iter([('Import settings collection', True), ('Report conflicts', True)])
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: next(answers))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    monkeypatch.setattr(window, 'report', lambda *a: None)
    before = copy.deepcopy(window.doc['blocks'])
    window.reusable_settings()
    assert window.doc['styles']['accent']['italic']
    assert window.doc['blocks'] == before


def test_gui_branch_switch_and_merge_proposal(window, monkeypatch):
    window.doc = model.new_document(text='Original')
    window.doc, _ = engine.checkpoint(window.doc, 'Start')
    window.doc, _ = engine.branch(window.doc, 'alternate')
    window.refresh()
    target = window.doc['blocks'][0]['id']
    assert window.commit([{'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'Alternate '}])
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('main', True))
    window.switch_draft()
    assert window.doc['blocks'][0]['text'] == 'Original'
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('alternate', True))
    window.merge_draft()
    assert window.doc['review']['proposals']
    assert window.doc['blocks'][0]['text'] == 'Original'


def test_gui_selective_proposal_preserves_unselected_edit(window, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QListWidget

    window.doc = model.new_document(text='First\nSecond')
    window.refresh()
    first, second = [b['id'] for b in window.doc['blocks']]
    window.commit(
        [
            {
                'type': 'propose',
                'values': {
                    'operations': [
                        {'type': 'set_text', 'target': first, 'text': 'Changed first'},
                        {'type': 'set_text', 'target': second, 'text': 'Changed second'},
                    ]
                },
            }
        ]
    )
    record = window.doc['review']['proposals'][0]

    def select_first(dialog):
        dialog.findChild(QListWidget).item(1).setCheckState(Qt.Unchecked)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', select_first)
    assert window.review_proposal_edits(record)
    assert [b['text'] for b in window.doc['blocks']] == ['Changed first', 'Second']
    assert window.doc['review']['proposals'][0]['status'] == 'pending'


def test_export_policy_gui_roundtrip_and_staged_export(window, tmp_path):
    from byax.gui_document_settings import ExportPolicyDialog
    from byax.publication import export_clean

    window.doc = model.new_document(text='Publish this')
    window.refresh()
    dialog = ExportPolicyDialog(window)
    dialog.name.setText('Public release')
    dialog.format.setCurrentIndex(dialog.format.findData('docx'))
    dialog.includes['include_comments'].setChecked(True)
    dialog.suites['structure'].setChecked(True)
    dialog.save()
    key, saved = next(iter(window.doc['profiles'].items()))
    assert saved['clean'] and saved['format'] == 'docx'
    assert saved['include_comments'] and not saved['include_history']
    path = tmp_path / 'master.byax'
    storage.save_document(window.doc, path)
    assert storage.load_document(path)['profiles'][key] == saved
    collection = profiles.export_settings(window.doc, ['profiles'])
    compiled = profiles.compile_collection(model.new_document(), collection)
    imported, _ = engine.apply(model.new_document(), {'operations': compiled['operations']})
    assert imported['profiles'][key] == saved
    output = tmp_path / 'public.docx'
    report = export_clean(window.doc, output, profile=saved)
    assert output.exists() and report['validation'] == 'passed'
    dialog.close()


@pytest.mark.parametrize('values', [{'include_audio': 'yes'}, {'required_suites': ['unknown']}, {'clean': 1}])
def test_export_policy_rejects_invalid_values(values):
    with pytest.raises(ByaxError):
        profiles.validate_profile(values)


def test_master_gui_preserves_artwork_and_assigns_regions(window):
    from byax.gui_document_settings import MasterDialog

    window.doc = model.new_document(text='Body')
    artwork = [{'fill': '#ff0000', 'x': 10, 'y': 10, 'width': 40, 'height': 40}]
    window.doc['layout']['masters'] = {'letterhead': {'artwork': artwork, 'columns': 2}}
    window.refresh()
    dialog = MasterDialog(window)
    dialog.saved.setCurrentIndex(dialog.saved.findData('letterhead'))
    dialog.geometry['top'].setValue(120)
    dialog.regions[('header', 'first', 'left')].setText('First-page letterhead')
    dialog.regions[('footer', 'even', 'right')].setText('{page}')
    dialog.assignment.setCurrentIndex(dialog.assignment.findData('document'))
    dialog.save()
    master = window.doc['layout']['masters']['letterhead']
    assert master['artwork'] == artwork and master['columns'] == 2
    assert master['margins']['top'] == 120
    assert master['header']['first']['left'] == 'First-page letterhead'
    assert master['footer']['even']['right'] == '{page}'
    assert window.doc['layout']['master'] == 'letterhead'
    window.undo()
    assert 'master' not in window.doc['layout']
    dialog.close()


def test_section_profile_gui_preserves_dictionary_and_resolves_settings(window):
    from byax.gui_document_settings import SectionProfileDialog

    window.doc = model.new_document(text='Lyrics\nIntentional writing')
    heading = window.doc['blocks'][0]
    heading.update(type='heading', style='heading1', attrs={'level': 1})
    window.doc['settings']['section_profiles'] = {
        heading['id']: {'inherit': True, 'overrides': {'dictionary': ['unusualword']}}
    }
    window.refresh()
    dialog = SectionProfileDialog(window)
    dialog.overrides['sensitivity'].setCurrentIndex(dialog.overrides['sensitivity'].findData('broad'))
    dialog.save()
    result = profiles.effective_profile(window.doc, window.doc['blocks'][1]['id'])
    assert result['profile']['dictionary'] == ['unusualword']
    assert result['profile']['sensitivity'] == 'broad'
    dialog.close()
    remove = SectionProfileDialog(window)
    remove.remove.setChecked(True)
    remove.save()
    assert heading['id'] not in window.doc['settings']['section_profiles']
    remove.close()


def test_layer_gui_edit_group_and_ungroup_preserves_output_policy(window, monkeypatch):
    from byax.gui_document_settings import LayerDialog

    window.doc = model.new_document(text='Body')
    window.doc['layers'] = [
        {'id': 'one', 'type': 'text', 'name': 'One', 'text': 'First', 'visible': True, 'locked': False},
        {'id': 'two', 'type': 'text', 'name': 'Two', 'text': 'Second', 'visible': True, 'locked': False},
    ]
    window.refresh()
    editor = LayerDialog(window, window.doc['layers'][0])
    editor.values['x'].setValue(120)
    editor.outputs['exclude_outputs'].setText('md, txt')
    editor.save()
    assert window.doc['layers'][0]['x'] == 120
    assert window.doc['layers'][0]['exclude_outputs'] == ['md', 'txt']
    for index in range(window.layer_list.count()):
        window.layer_list.item(index).setSelected(True)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('Letterhead', True))
    window.group_selected_layers()
    group = next(layer for layer in window.doc['layers'] if layer.get('kind') == 'group')
    assert all(
        layer.get('group') == group['id'] for layer in window.doc['layers'] if layer['id'] != group['id']
    )
    window.layer_list.setCurrentRow(
        next(i for i, layer in enumerate(window.doc['layers']) if layer['id'] == group['id'])
    )
    window.ungroup_selected_layers()
    assert len(window.doc['layers']) == 2 and not any('group' in layer for layer in window.doc['layers'])
    assert window.doc['layers'][0]['exclude_outputs'] == ['md', 'txt']
    editor.close()
