"""Provenance in the desktop app (offscreen): origins from typing, paste and accepted AI proposals."""

from PySide6.QtCore import QMimeData, QSettings
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox

from byax import engine
from byax.gui import MainWindow
from byax.gui_provenance import ORIGIN_STYLES, ProvenanceDialog, paste_source
from byax.model import new_document


def make_window(qtbot, monkeypatch, tmp_path, text='Hello world'):
    settings = QSettings(str(tmp_path / 'provenance.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    window = MainWindow()
    qtbot.addWidget(window)
    window.doc = new_document('Origins', text)
    window.refresh()
    monkeypatch.setattr(window, 'confirm_discard', lambda *args: True)
    return window


def _end(window):
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.End)
    window.editor.setTextCursor(cursor)


def _origins(window):
    log = window.doc.get('provenance') or {}
    return [
        (a, b, log['records'][rid]['origin'])
        for spans in log.get('spans', {}).values()
        for a, b, rid in spans
    ]


def test_typing_is_human_and_paste_is_pasted_with_its_source(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    _end(window)
    window.editor.insertPlainText(' typed')
    assert window.flush_edits()
    assert _origins(window) == [(11, 17, 'human')]
    mime = QMimeData()
    mime.setText(' pasted')
    mime.setData('text/x-moz-url-priv', 'https://example.org/article'.encode('utf-16-le'))
    window.editor.insertFromMimeData(mime)
    assert _origins(window) == [(11, 17, 'human'), (17, 24, 'pasted')]
    record = next(r for r in window.doc['provenance']['records'].values() if r['origin'] == 'pasted')
    assert record['source']['url'] == 'https://example.org/article'
    assert 'text/x-moz-url-priv' in record['source']['formats']
    assert getattr(window, '_pending_provenance', None) is None
    window.editor.insertPlainText('!')
    assert window.flush_edits()
    assert _origins(window)[-1] == (24, 25, 'human')


def test_paste_source_reads_the_windows_html_source_url():
    mime = QMimeData()
    mime.setText('x')
    mime.setData(
        'application/x-qt-windows-mime;value="HTML Format"',
        b'Version:0.9\r\nSourceURL:https://example.com/page\r\n<html></html>',
    )
    assert paste_source(mime)['url'] == 'https://example.com/page'
    plain = QMimeData()
    plain.setText('y')
    assert 'url' not in paste_source(plain) and 'app' not in paste_source(plain)


def test_accepting_an_ai_proposal_in_the_gui_records_the_model(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path, 'Lead.')
    target = window.doc['blocks'][0]['id']
    proposal = {
        'operations': [{'type': 'insert_text', 'target': target, 'offset': 5, 'text': ' The tide turned.'}],
        'provenance': {'provider': 'local', 'model': 'llama3.1:8b'},
    }
    window.commit([{'type': 'propose', 'values': proposal}])
    window.commit([{'type': 'accept_proposal', 'target': window.doc['review']['proposals'][-1]['id']}])
    assert _origins(window) == [(5, 22, 'ai_generated')]
    record = next(iter(window.doc['provenance']['records'].values()))
    assert record['model'] == 'llama3.1:8b'


def test_origin_view_tints_with_distinct_underlines_and_the_panel_lists_spans(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path, 'Lead.')
    target = window.doc['blocks'][0]['id']
    window.doc, _ = engine.apply(
        window.doc,
        {
            'operations': [{'type': 'insert_text', 'target': target, 'offset': 5, 'text': ' Generated.'}],
            'provenance': {'origin': 'ai_generated', 'model': 'm'},
        },
    )
    window.doc, _ = engine.apply(
        window.doc,
        {
            'operations': [{'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'Pasted '}],
            'provenance': {'origin': 'pasted'},
        },
    )
    window.refresh()
    assert window.editor.extraSelections() == []
    view = next(
        a for a in window.findChildren(type(window.provenance_view_action)) if a.text() == 'Show text origin'
    )
    view.trigger()
    styles = {s.format.underlineStyle() for s in window.editor.extraSelections()}
    assert styles == {ORIGIN_STYLES['ai_generated'][1], ORIGIN_STYLES['pasted'][1]}
    assert QTextCharFormat.NoUnderline not in styles  # never colour alone
    selected = {s.cursor.selectedText() for s in window.editor.extraSelections()}
    assert selected == {'Pasted ', ' Generated.'}
    dialog = window.provenance_panel()
    qtbot.addWidget(dialog)
    assert isinstance(dialog, ProvenanceDialog) and dialog.spans.rowCount() == 2
    assert {dialog.spans.item(r, 0).text() for r in range(2)} == {'AI-generated', 'Pasted'}
    assert any('Nothing is sent anywhere' in label.text() for label in dialog.findChildren(QLabel))
    view.trigger()
    assert window.editor.extraSelections() == []


def test_panel_disclosure_proposal_export_and_clear(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path, 'Lead.')
    target = window.doc['blocks'][0]['id']
    window.doc, _ = engine.apply(
        window.doc,
        {
            'operations': [{'type': 'insert_text', 'target': target, 'offset': 5, 'text': ' Generated.'}],
            'provenance': {'origin': 'ai_generated', 'model': 'gpt-4o'},
        },
    )
    window.refresh()
    dialog = ProvenanceDialog(window)
    qtbot.addWidget(dialog)
    dialog.style.setCurrentIndex(dialog.style.findData('mla'))
    assert (
        dialog.statement['style'] == 'mla' and 'Acknowledgment of AI use' in dialog.disclosure.toPlainText()
    )
    dialog.copy_disclosure()
    assert dialog.insert_button.isEnabled() and dialog.propose_disclosure()
    assert window.doc['review']['proposals'][-1]['origin'] == 'disclosure'
    written = dialog.export_c2pa(str(tmp_path / 'lead.c2pa.json'))
    assert written['signed'] is False and (tmp_path / 'lead.c2pa.json').is_file()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    assert dialog.clear()
    assert 'provenance' not in window.doc and dialog.spans.rowCount() == 0


def test_menus_offer_the_provenance_panel(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    menus = {a.text(): a.menu() for a in window.menuBar().actions()}
    action = next(a for a in menus['&Tools'].actions() if a.text() == 'Provenance…')
    action.trigger()
    assert 'Provenance' in {w.windowTitle() for w in window.findChildren(QDialog) if w.isVisible()}
