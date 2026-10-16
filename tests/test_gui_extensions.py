"""Tools > Extensions and Tools > Macros (offscreen): install confirmation, scopes, enable, runs, accessibility."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json

import pytest
from extension_support import EXAMPLES, install_example
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox, QPushButton

from byax import extensions, macros, model, storage
from byax.cli import execute
from byax.errors import ByaxError
from byax.gui import MainWindow
from byax.gui_extensions import ExtensionsDialog, MacrosDialog, confirmation_text


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'extensions.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    w = MainWindow()
    qtbot.addWidget(w)
    w.doc = model.new_document(text='This is very plain. ' + ' '.join(['word'] * 50) + '.')
    w.refresh()
    return w


def answer(monkeypatch, value):
    shown = []

    def question(parent, title, text, *args):
        shown.append(text)
        return value

    monkeypatch.setattr(QMessageBox, 'question', staticmethod(question))
    return shown


def rows(dialog):
    return [
        [dialog.table.item(r, c).text() for c in range(dialog.table.columnCount())]
        for r in range(dialog.table.rowCount())
    ]


def test_tools_menu_has_extensions_and_macros(window):
    tools = next(a.menu() for a in window.menuBar().actions() if a.text() == '&Tools')
    labels = [a.text() for a in tools.actions()]
    assert 'Extensions…' in labels and 'Macros…' in labels


def test_install_shows_the_scopes_and_hash_and_installs_nothing_unless_confirmed(window, qtbot, monkeypatch):
    dialog = ExtensionsDialog(window)
    qtbot.addWidget(dialog)
    shown = answer(monkeypatch, QMessageBox.No)
    assert dialog.install_from(str(EXAMPLES / 'long-sentences')) is None
    text = shown[0]
    plan = extensions.inspect_package(EXAMPLES / 'long-sentences')
    assert plan['sha256'] in text and 'checks:' in text and 'read:' in text
    assert 'not sandboxed' in text and 'switched off' in text and 'ByAuthorX project' in text
    assert extensions.summaries() == [] and dialog.status.text() == 'Nothing was installed.'
    answer(monkeypatch, QMessageBox.Yes)
    record = dialog.install_from(str(EXAMPLES / 'long-sentences'))
    assert record['enabled'] is False and rows(dialog)[0][0] == 'Sentences over N words'
    assert rows(dialog)[0][3:] == ['checks, read', 'ok', 'No']
    assert 'switched off' in dialog.status.text()


def test_the_confirmation_text_states_the_hash_kind_for_archives_and_folders():
    plan = extensions.inspect_package(EXAMPLES / 'house-style-words')
    text = confirmation_text(plan)
    assert 'folder contents' in text and 'propose:' in text and 'No code runs' in text


def test_enable_disable_verify_and_remove_from_the_dialog(window, qtbot, monkeypatch):
    install_example('long-sentences', enable=False)
    dialog = ExtensionsDialog(window)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    assert dialog.toggle_button.text() == 'Enable' and not dialog.check_button.isEnabled()
    dialog.toggle_enabled()
    assert (
        rows(dialog)[0][5] == 'Yes'
        and dialog.toggle_button.text() == 'Disable'
        and dialog.check_button.isEnabled()
    )
    assert (
        'Sentences over N words' in dialog.details.toPlainText()
        and 'Permissions granted' in dialog.details.toPlainText()
    )
    results = dialog.verify_all()
    assert results[0]['ok'] and 'match what was installed' in dialog.status.text()
    path = extensions.get('long-sentences')['directory']
    (extensions.Path(path) / 'checks' / 'long_sentences.py').write_text('# edited\n', encoding='utf-8')
    dialog.verify_all()
    assert 'differs from what was installed' in dialog.status.text() and rows(dialog)[0][4] == 'modified'
    dialog.table.selectRow(0)
    dialog.toggle_enabled()
    assert dialog.status.text().endswith('is now disabled.'), (
        'a modified extension can always be switched off'
    )
    dialog.table.selectRow(0)
    assert dialog.toggle_enabled() is None and 'Only an intact' in dialog.status.text()
    answer(monkeypatch, QMessageBox.No)
    assert dialog.remove_selected() is None and rows(dialog)
    answer(monkeypatch, QMessageBox.Yes)
    dialog.remove_selected()
    assert rows(dialog) == [] and dialog.details.toPlainText() == 'No extensions are installed.'


def test_run_checks_runs_the_selected_extension_on_the_open_document_without_blocking(window, qtbot):
    install_example('long-sentences')
    install_example('house-style-words')
    dialog = ExtensionsDialog(window)
    qtbot.addWidget(dialog)
    dialog.table.selectRow([r[0] for r in rows(dialog)].index('Sentences over N words'))
    with qtbot.waitSignal(dialog.task.done, timeout=30000):
        dialog.run_checks_now()
    assert dialog.last_result['summary']['total'] == 1
    assert {f['family'] for f in dialog.last_result['findings']} == {'extension:long-sentences'}
    assert dialog.status.text() == '1 findings from the extension.'


def test_run_hook_on_a_file_reports_the_outputs(window, qtbot, tmp_path):
    install_example('wordcount-csv')
    exported = tmp_path / 'chapter.md'
    exported.write_text('one two three', encoding='utf-8')
    dialog = ExtensionsDialog(window)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    with qtbot.waitSignal(dialog.task.done, timeout=30000):
        folder = dialog.run_hook_on_file(str(exported))
    assert (tmp_path / 'chapter.md.hooks' / 'chapter.md.wordcount.csv').is_file() and folder.endswith(
        '.hooks'
    )
    assert 'chapter.md.wordcount.csv' in dialog.status.text()
    assert exported.read_text(encoding='utf-8') == 'one two three'


def test_the_export_hooks_setting_is_off_by_default_and_gates_hooks_after_export(window, qtbot, tmp_path):
    install_example('wordcount-csv')
    exported = tmp_path / 'e.md'
    exported.write_text('a b c', encoding='utf-8')
    assert window.run_export_hooks_after(str(exported), 'md', {'losses': []}) == []
    dialog = ExtensionsDialog(window)
    qtbot.addWidget(dialog)
    assert not dialog.hooks_box.isChecked()
    dialog.hooks_box.setChecked(True)
    (result,) = window.run_export_hooks_after(str(exported), 'md', {'losses': [{'code': 'x'}]})
    assert result['status'] == 'completed' and (tmp_path / 'e.md.hooks' / 'e.md.wordcount.csv').is_file()
    assert window.run_export_hooks_after(str(exported), 'pdf', {}) == [], 'no hook applies to that format'


def test_dialogs_are_accessible_named_and_keyboard_reachable(window, qtbot):
    install_example('long-sentences')
    for dialog in (ExtensionsDialog(window), MacrosDialog(window)):
        qtbot.addWidget(dialog)
        assert dialog.accessibleName() and dialog.windowTitle()
        for button in dialog.findChildren(QPushButton):
            assert button.accessibleName() or button.text(), button
            assert button.focusPolicy() != Qt.NoFocus
        assert dialog.table.accessibleName() and dialog.status.accessibleName()
    ext = ExtensionsDialog(window)
    qtbot.addWidget(ext)
    assert ext.details.accessibleName() and ext.hooks_box.accessibleName()


# Macros -----------------------------------------------------------------------------------
def macro(name='add-note', text='Added by the macro.'):
    return {
        'schema': 1,
        'name': name,
        'version': 1,
        'description': 'Test',
        'steps': [
            {
                'label': 'Add',
                'command': 'apply',
                'arguments': {
                    'path': '{document}',
                    'input': {'operations': [{'type': 'insert_paragraph', 'text': text}]},
                },
            },
            {
                'label': 'Check',
                'command': 'check',
                'arguments': {'path': '{document}', 'suite': ['structure']},
            },
        ],
    }


@pytest.fixture
def saved_window(window, tmp_path):
    path = tmp_path / 'macro.byax'
    storage.save_document(window.doc, path)
    assert window.open_path(str(path))
    return window


def paragraph_texts(window):
    return [b['text'] for b in window.doc['blocks']]


def test_macros_list_run_dry_run_and_show_a_transcript(saved_window, qtbot, tmp_path):
    file = tmp_path / 'm.json'
    file.write_text(json.dumps(macro()), encoding='utf-8')
    dialog = MacrosDialog(saved_window)
    qtbot.addWidget(dialog)
    assert dialog.import_from_file(str(file), store='user')['name'] == 'add-note'
    assert rows(dialog)[0][:4] == ['add-note', '1', 'My macros', '2']
    assert 'apply' in dialog.steps_view.toPlainText()
    before = paragraph_texts(saved_window)
    outcome = dialog.run(True)
    assert outcome['dry_run'] and paragraph_texts(saved_window) == before
    assert dialog.transcript.rowCount() == 2 and 'Dry run: nothing was changed.' in dialog.status.text()
    assert 'dry_run' in dialog.entry_view.toPlainText()
    outcome = dialog.run(False)
    assert outcome['status'] == 'completed' and paragraph_texts(saved_window)[-1] == 'Added by the macro.'
    assert dialog.status.text() == '2 of 2 steps finished.'
    assert not saved_window.dirty and saved_window.lock is not None
    assert [dialog.transcript.item(r, 2).text() for r in range(2)] == ['completed', 'completed']
    assert (
        execute('inspect', {'path': str(saved_window.path), 'limit': 50})['items'][-1]['text']
        == 'Added by the macro.'
    )


def test_a_failing_macro_reports_the_stopped_step_in_the_dialog(saved_window, qtbot, tmp_path):
    bad = macro('breaks')
    bad['steps'].insert(
        1,
        {'label': 'Break', 'command': 'inspect', 'arguments': {'path': '{document}', 'target': 'p_missing'}},
    )
    file = tmp_path / 'bad.json'
    file.write_text(json.dumps(bad), encoding='utf-8')
    dialog = MacrosDialog(saved_window)
    qtbot.addWidget(dialog)
    dialog.import_from_file(str(file), store='user')
    outcome = dialog.run(False)
    assert outcome['status'] == 'failed' and outcome['failed_step'] == 2
    assert dialog.status.text().startswith('Stopped at step 2:')
    assert dialog.transcript.rowCount() == 2 and dialog.transcript.item(1, 2).text() == 'failed'
    assert paragraph_texts(saved_window)[-1] == 'Added by the macro.', (
        'earlier steps stay applied, like any other edits'
    )


def test_admin_steps_are_refused_in_the_dialog_and_flagged_in_the_list(saved_window, qtbot, tmp_path):
    definition = macro('admin')
    definition['steps'] = [
        {
            'command': 'goals',
            'arguments': {
                'path': '{document}',
                'action': 'set-daily',
                'words': 100,
                'config_dir': str(tmp_path / 'g'),
            },
        }
    ]
    file = tmp_path / 'admin.json'
    file.write_text(json.dumps(definition), encoding='utf-8')
    dialog = MacrosDialog(saved_window)
    qtbot.addWidget(dialog)
    dialog.import_from_file(str(file), store='user')
    assert rows(dialog)[0][4] == '1'
    outcome = dialog.run(False)
    assert outcome['status'] == 'failed' and dialog.transcript.item(0, 2).text() == 'refused'
    assert not (tmp_path / 'g').exists()


def test_document_macros_are_saved_as_an_edit_and_removed_with_confirmation(
    saved_window, qtbot, tmp_path, monkeypatch
):
    file = tmp_path / 'm.json'
    file.write_text(json.dumps(macro('in-document')), encoding='utf-8')
    dialog = MacrosDialog(saved_window)
    qtbot.addWidget(dialog)
    revision = saved_window.doc['revision']
    dialog.import_from_file(str(file), store='document')
    assert saved_window.doc['revision'] != revision and 'in-document' in macros.document_macros(
        saved_window.doc
    )
    assert rows(dialog)[0][2] == 'This document'
    dialog.table.selectRow(0)
    answer(monkeypatch, QMessageBox.No)
    assert dialog.remove_selected() is None
    answer(monkeypatch, QMessageBox.Yes)
    assert dialog.remove_selected() == 'in-document' and macros.document_macros(saved_window.doc) == {}


def test_running_needs_a_saved_byax_document_and_bad_input_is_reported(window, qtbot, tmp_path):
    file = tmp_path / 'm.json'
    file.write_text(json.dumps(macro()), encoding='utf-8')
    dialog = MacrosDialog(window)
    qtbot.addWidget(dialog)
    dialog.import_from_file(str(file), store='user')
    assert (
        dialog.run(False) is None and 'Save this document as a ByAuthorX file first.' in dialog.status.text()
    )
    dialog.params.setText('[1, 2]')
    dialog.run(True)
    assert 'JSON object' in dialog.status.text()
    dialog.params.setText('{broken')
    dialog.run(True)
    assert 'JSON object' in dialog.status.text()
    junk = tmp_path / 'junk.json'
    junk.write_text('{"name": "Bad Name", "steps": []}', encoding='utf-8')
    assert dialog.import_from_file(str(junk), store='user') is None and 'names' in dialog.status.text()
    with pytest.raises(ByaxError):
        window.run_macro_on_open_document(macro(), {}, True)


def test_the_checks_tab_offers_extension_checks_off_by_default(window, monkeypatch):
    panel = window.checks_panel
    assert 'extensions' in panel.toggles and not panel.toggles['extensions'].isChecked()
    assert panel.toggles['extensions'].accessibleName()
    seen = []
    monkeypatch.setattr(window, 'run_checks', lambda suites=None, **kwargs: seen.append(suites))
    panel.run(['extensions'])
    assert seen == [['extension']]
