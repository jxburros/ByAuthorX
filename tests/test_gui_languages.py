"""Tools > Languages (packs, interface language, grammar tiers) and the Checks tab's tier status."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from byax import grammar, gui_languages, language_packs, model
from byax.gui import MainWindow
from byax.gui_languages import LanguagesDialog, tier_summary


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'languages.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    w = MainWindow()
    qtbot.addWidget(w)
    w.doc = model.new_document(text='I should of gone.')
    w.refresh()
    return w


def make_pack(tmp_path, strings=None):
    directory = tmp_path / 'pack'
    directory.mkdir()
    files = {}
    rules = {
        'rules': [
            {
                'id': 'foo_rule',
                'group': 'test',
                'pattern': r'\bfoo\b',
                'message': 'm',
                'replacements': ['bar'],
            }
        ]
    }
    (directory / 'grammar').mkdir()
    (directory / 'grammar' / 'xx.json').write_text(json.dumps(rules))
    files['grammar'] = 'grammar/xx.json'
    if strings:
        (directory / 'strings').mkdir()
        (directory / 'strings' / 'xx.json').write_text(json.dumps({'language': 'xx', 'strings': strings}))
        files['strings'] = 'strings/xx.json'
    language_packs.build_manifest(
        directory,
        pack_id='xx-core',
        name='Test',
        language='xx',
        version='1.0.0',
        licence='CC0-1.0',
        source='https://example.invalid',
        files=files,
    )
    archive = tmp_path / 'xx.byaxpack'
    language_packs.pack_archive(directory, archive)
    return archive


def test_tools_menu_has_languages_and_the_checks_tab_shows_both_tiers(window):
    tools = next(a.menu() for a in window.menuBar().actions() if a.text() == '&Tools')
    assert 'Languages…' in [a.text() for a in tools.actions()]
    panel = window.checks_panel
    assert panel.grammar_builtin_label.text().startswith('Built-in rules: on (')
    assert panel.grammar_lt_label.text() == 'LanguageTool: not installed'
    assert panel.grammar_lt_label.accessibleName() and panel.grammar_builtin_label.accessibleName()


def test_the_dialog_lists_built_in_packs_and_the_honest_tier_state(window, qtbot):
    dialog = LanguagesDialog(window)
    qtbot.addWidget(dialog)
    ids = [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())]
    assert 'builtin-en' in ids
    assert dialog.lt_label.text() == 'LanguageTool: not installed'
    assert not dialog.lt_start.isEnabled() and not dialog.lt_stop.isEnabled()
    assert not dialog.lt_enabled.isEnabled()
    assert 'Not installed' in dialog.lt_output.toPlainText()
    plan = dialog.install_plan_text()
    for fragment in (grammar.LANGUAGETOOL['url'], grammar.LANGUAGETOOL['sha256'], '251,998,221', 'LGPL-2.1'):
        assert fragment in plan
    assert dialog.tabs.count() == 3 and LanguagesDialog(window, tab='grammar').tabs.currentIndex() == 2


def test_declining_the_languagetool_download_does_nothing(window, qtbot, monkeypatch):
    dialog = LanguagesDialog(window)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.No)
    called = []
    monkeypatch.setattr(grammar, 'install_languagetool', lambda **kwargs: called.append(kwargs))
    assert dialog.install_languagetool() is False
    assert called == [] and dialog.lt_install.isEnabled()


def test_installing_a_pack_from_a_file_needs_confirmation_and_updates_the_table(
    window, qtbot, monkeypatch, tmp_path
):
    archive = make_pack(tmp_path)
    dialog = LanguagesDialog(window)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.No)
    assert dialog.install_from_file(str(archive)) is None
    assert 'xx-core' not in [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    installed = dialog.install_from_file(str(archive))
    assert installed['id'] == 'xx-core'
    ids = [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
    assert 'xx-core' in ids and 'Installed xx-core' in dialog.pack_status.text()
    dialog.table.selectRow(ids.index('xx-core'))
    assert dialog.selected_pack() == 'xx-core'
    assert all(r['ok'] for r in dialog.verify_all())
    dialog.remove_selected()
    assert 'xx-core' not in [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
    dialog.table.selectRow(ids.index('builtin-en'))
    assert dialog.remove_selected() is None and 'cannot be removed' in dialog.pack_status.text()


def test_the_interface_language_choice_reports_coverage_and_is_saved(window, qtbot, tmp_path):
    language_packs.install_pack(make_pack(tmp_path, strings={'Save': 'Guardar'}))
    dialog = LanguagesDialog(window)
    qtbot.addWidget(dialog)
    codes = [dialog.language_box.itemData(i) for i in range(dialog.language_box.count())]
    assert codes == ['en', 'xx']
    assert 'English is the source language' in dialog.coverage_label.text()
    dialog.language_box.setCurrentIndex(codes.index('xx'))
    assert 'of' in dialog.coverage_label.text() and 'texts translated' in dialog.coverage_label.text()
    assert dialog.save_language() == 'xx'
    assert window.settings.value('interface/language') == 'xx'
    dialog.language_box.setCurrentIndex(0)
    dialog.save_language()
    assert window.settings.value('interface/language') == ''


def test_choosing_a_languagetool_folder_enables_the_tier_and_updates_the_checks_tab(window, qtbot, tmp_path):
    folder = tmp_path / 'LanguageTool-6.6'
    folder.mkdir()
    (folder / 'languagetool-server.jar').write_bytes(b'jar')
    dialog = LanguagesDialog(window)
    qtbot.addWidget(dialog)
    assert dialog.choose_folder(str(tmp_path / 'empty')) is None
    assert dialog.choose_folder(str(folder)) == str(folder)
    assert dialog.lt_label.text() == 'LanguageTool: stopped'
    assert dialog.lt_enabled.isChecked() and dialog.lt_start.isEnabled() and not dialog.lt_stop.isEnabled()
    assert window.checks_panel.grammar_lt_label.text() == 'LanguageTool: stopped'
    dialog.lt_enabled.setChecked(False)
    assert tier_summary()['languagetool'] == 'LanguageTool: stopped (off)'
    assert grammar.load_config()['languagetool']['enabled'] is False


def test_the_setup_button_in_the_checks_tab_opens_the_grammar_page(window, monkeypatch):
    opened = []
    monkeypatch.setattr(
        gui_languages.LanguagePanels, 'show_languages', lambda self, tab=None: opened.append(tab)
    )
    button = next(
        b
        for b in window.checks_panel.findChildren(type(window.run_all_button))
        if 'grammar tiers' in b.text()
    )
    button.click()
    assert opened == ['grammar']


def test_the_grammar_check_uses_the_built_in_rules_from_the_desktop(window, monkeypatch):
    seen = []
    monkeypatch.setattr(window, 'run_checks', lambda suites: seen.append(suites))
    window.checks_panel.run_buttons['grammar'].click()
    assert seen == [['grammar']]
