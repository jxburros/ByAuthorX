"""Checks tab fiction families and the readability report dialog (offscreen)."""

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from byax import fiction_checks, model
from byax.exchange import import_document
from byax.gui import MainWindow
from byax.gui_readability import PacingChart, ReadabilityDialog
from byax.gui_review import FICTION_CHECKS

FIXTURES = Path(__file__).parent / 'fixtures' / 'fiction'


def make_window(qtbot, monkeypatch, tmp_path, fixture='seeded.md'):
    settings = QSettings(str(tmp_path / 'fiction.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    window = MainWindow()
    qtbot.addWidget(window)
    window.doc, _ = import_document(str(FIXTURES / fixture))
    window.refresh()
    return window


def test_checks_tab_lists_fiction_families_inactive_with_accessible_names(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    checks = window.checks_panel
    assert checks.active_checks() == ['spelling', 'grammar', 'health']
    assert {kind.split(':')[1] for kind, _, _ in FICTION_CHECKS} == set(fiction_checks.GROUPS.values())
    for kind, label, _ in FICTION_CHECKS:
        toggle, run = checks.toggles[kind], checks.run_buttons[kind]
        assert not toggle.isChecked() and toggle.text() == label
        assert 'Advisory' in toggle.toolTip() and run.accessibleName() == f'Run {label}'
        assert run.toolTip() == f'Run {label} now'


def test_running_a_fiction_family_runs_only_its_rules(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    captured = {}

    def fake_background(label, work, done, failed=None):
        captured['result'] = work()
        done(captured['result'])

    monkeypatch.setattr(window, 'background', fake_background)
    monkeypatch.setattr(window, 'report', lambda *args, **kwargs: captured.setdefault('shown', True))
    window.checks_panel.run_check('fiction:dialogue')
    result = captured['result']
    assert result['suites'] == ['fiction']
    assert {f['rule'] for f in result['findings']} == {
        'fiction.tag_adverb',
        'fiction.tag_bookism',
        'fiction.tag_repetition',
    }
    # Active fiction families join "Run all active checks" only when chosen.
    window.checks_panel.toggles['fiction:names'].setChecked(True)
    assert 'Invented-name spelling' in window.active_checks_label.text()
    captured.clear()
    window.checks_panel.run(['fiction:names'])
    assert {f['rule'] for f in captured['result']['findings']} == {'fiction.name_spelling'}


def test_tools_menu_offers_the_readability_report(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    tools = next(a.menu() for a in window.menuBar().actions() if a.text() == '&Tools')
    action = next(a for a in tools.actions() if a.text() == 'Readability report…')
    assert action.toolTip() == 'Readability report' and action.statusTip()
    shown = []
    monkeypatch.setattr(ReadabilityDialog, 'exec', lambda self: shown.append(self.windowTitle()) or 0)
    action.trigger()
    assert shown == ['Readability report']


def test_dialog_shows_indices_style_and_caveats_for_each_chapter(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = ReadabilityDialog(window)
    qtbot.addWidget(dialog)
    assert dialog.scope.count() == 5 and dialog.scope.itemText(0) == 'Whole document'
    assert dialog.indices.accessibleName() == 'Readability indices'
    labels = [dialog.indices.item(r, 0).text() for r in range(dialog.indices.rowCount())]
    assert labels[0] == 'Flesch Reading Ease' and 'SMOG' in labels and len(labels) == 6
    assert dialog.indices.item(0, 2).text() == 'Reliable'
    dialog.scope.setCurrentIndex(2)  # a ~54-word chapter
    assert dialog.indices.item(0, 2).text().startswith('Caution: ')
    assert 'needs at least' in dialog.note.text()
    assert dialog.style.item(0, 1).text() == str(dialog.entry()['style']['words'])


def test_pacing_graph_is_keyboard_operable_and_mirrored_by_a_table(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = ReadabilityDialog(window)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.scope.setCurrentIndex(4)  # The Quiet Count: six paragraphs
    chart, table = dialog.chart, dialog.pacing_table
    assert table.rowCount() == len(chart.series) == 6
    assert table.accessibleName() == 'Pacing table' and chart.accessibleName() == 'Pacing graph'
    assert [table.horizontalHeaderItem(c).text() for c in range(4)] == [
        'Paragraph',
        'Words',
        'Average sentence (words)',
        'Dialogue share',
    ]
    chart.setFocus()
    assert chart.index == 0 and 'Paragraph 1' in chart.accessibleDescription()
    QTest.keyClick(chart, Qt.Key_Right)
    QTest.keyClick(chart, Qt.Key_Right)
    assert chart.index == 2 and table.selectionModel().selectedRows()[0].row() == 2
    assert 'Paragraph 3: 22 words' in chart.accessibleDescription()
    QTest.keyClick(chart, Qt.Key_End)
    assert chart.index == 5
    QTest.keyClick(chart, Qt.Key_Home)
    assert chart.index == 0
    table.selectRow(4)
    assert chart.index == 4
    pixmap = chart.grab()
    assert not pixmap.isNull() and pixmap.toImage().pixelColor(5, 5) is not None


def test_chart_handles_empty_chapters_and_single_points(qtbot):
    chart = PacingChart()
    qtbot.addWidget(chart)
    chart.resize(300, 200)
    chart.set_series([])
    assert chart.index == -1 and 'No paragraphs' in chart.summary()
    assert not chart.grab().isNull()
    QTest.keyClick(chart, Qt.Key_Right)
    chart.set_series([{'words': 5, 'mean_sentence_words': 5, 'dialogue_ratio': 0.0}])
    assert chart.index == 0 and not chart.grab().isNull()


def test_dialog_saves_the_report_as_a_document_and_reports_losses(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = ReadabilityDialog(window)
    qtbot.addWidget(dialog)
    target = tmp_path / 'report.md'
    messages = []
    monkeypatch.setattr(
        QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), 'Markdown (*.md)')
    )
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args[2]))
    dialog.export()
    assert target.is_file() and '| Index | Value | Note |' in target.read_text(encoding='utf-8')
    assert messages and str(target) in messages[0]
    # The master document is untouched by a report.
    assert window.doc['blocks'] and model.validate_document(window.doc)
