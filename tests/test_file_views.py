"""File-view purity, transport parity, source agreement and hidden-content editing."""

import copy
import random

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from byax import model, storage
from byax.cli import build_parser, execute
from byax.exchange import EXPORT_FORMATS, conversion_preview, export_document, import_document, source_text
from byax.formats import FORMATS, canonical_format, project, supports
from byax.gui import MainWindow
from byax.mcp import handle


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / 'preferences.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *args: settings)
    w = MainWindow()
    monkeypatch.setattr(w, 'error', lambda exc: pytest.fail(str(exc)))
    yield w
    w.edit_timer.stop()
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()


def example():
    doc = model.new_document(text='Alpha beta\nHidden note\nLast paragraph')
    doc['blocks'][0]['marks'] = [{'start': 0, 'end': 5, 'type': 'underline', 'value': True}]
    doc['blocks'][1]['type'] = 'footnote'
    return doc


def test_registry_covers_export_formats_and_block_types():
    assert {canonical_format(f) for f in EXPORT_FORMATS} == set(FORMATS)
    assert 'doc' not in FORMATS
    for entry in FORMATS.values():
        assert set(entry['blocks']) == model.BLOCK_TYPES
    assert supports('md', 'marks.bold')
    assert not supports('txt', 'marks.bold')
    assert not supports('md', 'tables.spans')


def test_view_list_in_this_document_matches_the_registry():
    """The views table in docs/file-views.md lists exactly the registry's formats, with their labels."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / 'docs' / 'file-views.md').read_text(encoding='utf-8')
    section = text.split('## 4. Views', 1)[1].split('\n## ', 1)[0]
    rows = dict(re.findall(r'^\| `(\w+)` \| ([^|]+) \|', section, re.MULTILINE))
    assert set(rows) == set(FORMATS)
    assert all(rows[ident].strip() == entry['label'] for ident, entry in FORMATS.items())


@pytest.mark.parametrize('fmt', FORMATS)
def test_projection_is_pure_deterministic_and_preserves_identities(fmt):
    doc = example()
    before = copy.deepcopy(doc)
    projected, report = project(doc, fmt)
    assert doc == before
    assert project(doc, fmt) == (projected, report)
    assert {b['id'] for b in projected['blocks']} <= {b['id'] for b in doc['blocks']}
    assert report['losses'] == conversion_preview(doc, fmt)['losses']
    projected['metadata']['title'] = 'Changed projection'
    assert doc == before


@pytest.mark.parametrize('fmt', ['txt', 'md', 'html', 'rtf'])
def test_source_matches_exact_export_bytes(fmt, tmp_path):
    doc = model.new_document(text='Unicode 😀 café שלום\n**Literal**')
    target = tmp_path / ('export.' + fmt)
    export_document(doc, target)
    assert source_text(doc, fmt).encode() == target.read_bytes()
    assert project(doc, fmt, 'source')[1]['source'] == source_text(doc, fmt)


@pytest.mark.parametrize('fmt', ['txt', 'md', 'html', 'docx', 'odt', 'rtf'])
def test_output_policies_are_shared_by_view_and_export(fmt, tmp_path):
    doc = model.new_document(text='Keep\nExclude\nOnly PDF')
    doc['blocks'][1]['attrs']['exclude_outputs'] = [fmt]
    doc['blocks'][2]['attrs']['outputs'] = ['pdf']
    projected, report = project(doc, fmt)
    assert [b['text'] for b in projected['blocks']] == ['Keep']
    target = tmp_path / ('out.' + fmt)
    actual = export_document(doc, target)
    reopened, _ = import_document(target)
    assert model.text_of(reopened).strip() == 'Keep'
    assert actual['losses'] == report['losses']
    assert len(doc['blocks']) == 3


def test_cli_mcp_view_parity_and_no_writes(tmp_path):
    path = tmp_path / 'master.byax'
    storage.save_document(example(), path)
    before = path.read_bytes()
    args = {'path': str(path), 'as': 'md', 'source': True}
    cli = execute('view', args)
    rpc = handle(
        {
            'jsonrpc': '2.0',
            'id': 7,
            'method': 'tools/call',
            'params': {'name': 'byax_view', 'arguments': args},
        }
    )
    assert not rpc['result'].get('isError')
    assert cli['format'] == 'md'
    assert cli['source'] == source_text(example(), 'md')
    assert path.read_bytes() == before
    assert build_parser().parse_args(['view', str(path), '--as', 'txt']).command == 'view'


def test_view_switch_keeps_master_undo_and_caret(window):
    window.doc = example()
    window.refresh()
    window.editor.insertPlainText('X')
    assert window.flush_edits()
    before = copy.deepcopy(window.doc)
    undo = list(window._undo)
    target = window.editor.current_id()
    for fmt in FORMATS:
        assert window.set_file_view(fmt)
        assert window.doc == before
        assert window._undo == undo
        assert window.editor.current_id() == target
        actions = {a.text(): a for a in window.findChildren(QAction)}
        for label in ('Bulleted list', 'Apply character style…', 'Page masters…'):
            assert actions[label].isEnabled() == supports(fmt, FORMATS[fmt]['commands'][label])
    window.undo()
    assert window.doc['blocks'][0]['text'] == 'Alpha beta'


def test_hidden_gap_blocks_backspace_and_delete(window):
    window.doc = example()
    window.refresh()
    window.set_file_view('md')
    last = window.doc['blocks'][-1]['id']
    window.editor.navigate(last)
    before = copy.deepcopy(window.doc)
    QTest.keyClick(window.editor, Qt.Key_Backspace)
    assert window.flush_edits()
    assert window.doc == before
    window.editor.navigate(window.doc['blocks'][0]['id'])
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.EndOfBlock)
    window.editor.setTextCursor(cursor)
    QTest.keyClick(window.editor, Qt.Key_Delete)
    assert window.flush_edits()
    assert window.doc == before


@pytest.mark.parametrize('fmt', [f for f in FORMATS if f != 'byax'])
def test_seeded_text_edits_keep_hidden_blocks_and_unoverlapped_marks(window, fmt):
    rng = random.Random(427)
    window.doc = example()
    window.doc['blocks'][1]['attrs']['exclude_outputs'] = [fmt]
    window.refresh()
    hidden = copy.deepcopy(window.doc['blocks'][1])
    mark = copy.deepcopy(window.doc['blocks'][0]['marks'])
    window.set_file_view(fmt)
    for _ in range(25):
        window.editor.navigate(window.doc['blocks'][0]['id'])
        cursor = window.editor.textCursor()
        cursor.movePosition(QTextCursor.EndOfBlock)
        window.editor.setTextCursor(cursor)
        if rng.randrange(3) == 0 and len(window.doc['blocks'][0]['text']) > 10:
            cursor.deletePreviousChar()
        else:
            window.editor.insertPlainText(rng.choice(['x', 'é', '😀']))
        assert window.flush_edits()
        assert window.doc['blocks'][1] == hidden
        assert window.doc['blocks'][0]['marks'] == mark


def test_select_all_delete_preserves_hidden_block(window):
    window.doc = example()
    window.refresh()
    hidden = copy.deepcopy(window.doc['blocks'][1])
    window.set_file_view('txt')
    window.editor.selectAll()
    window.editor.insertPlainText('')
    assert window.flush_edits()
    assert next(b for b in window.doc['blocks'] if b['id'] == hidden['id']) == hidden
    assert 'hidden items remain' in window.status_label.text()


def test_txt_heading_split_becomes_body_and_tracks(window):
    window.doc = model.new_document(text='Heading tail')
    window.doc['blocks'][0].update(type='heading', style='heading1')
    window.doc['blocks'][0]['attrs']['level'] = 1
    window.doc['settings']['track_changes'] = True
    window.refresh()
    window.set_file_view('txt')
    cursor = window.editor.textCursor()
    cursor.setPosition(7)
    window.editor.setTextCursor(cursor)
    window.editor.insertPlainText('\n')
    assert window.flush_edits()
    assert window.doc['blocks'][1]['style'] == 'body'
    assert window.doc['review']['tracked_changes']


def test_markdown_import_default_and_inplace_opt_out(window, tmp_path, monkeypatch):
    source = tmp_path / 'notes.md'
    source.write_bytes(b'# Notes\r\n\r\nRead me.\r\n')
    window.settings.setValue('files/markdownOpenMode', 'import')
    assert window.open_path(source)
    assert window.path is None and not window.dirty and window.file_view == 'byax'
    window.settings.setValue('files/markdownOpenMode', 'inplace')
    assert window.open_path(source)
    assert window.path == source and window.file_view == 'md'
    assert not window.file_view_box.isEnabled()
    assert window.save(wait=True)
    assert source.read_bytes() == b'# Notes\r\n\r\nRead me.\r\n'
    assert not window.export_offer.isVisible()


def test_markdown_chooser_default_and_remember(window, monkeypatch):
    def accept(dialog):
        choice = dialog.findChild(QComboBox)
        assert choice.currentData() == 'import'
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', accept)
    assert window.choose_markdown_mode() == 'import'
    assert window.settings.value('files/markdownOpenMode') == 'import'


def test_export_offer_suppression_and_source_readonly(window):
    window.set_file_view('md')
    window.offer_export_after_save()
    assert not window.export_offer.isHidden()
    window.suppress_export_offer(False)
    window.offer_export_after_save()
    assert window.export_offer.isHidden()
    window.set_source_mode(1)
    assert window.stack.currentIndex() == 2 and window.source_editor.isReadOnly()
    assert window.source_editor.toPlainText() == source_text(window.doc, 'md')


@pytest.mark.parametrize('fmt', ['txt', 'md', 'html', 'docx', 'odt', 'rtf'])
def test_roundtrip_text_structure_and_explicit_marks(fmt, tmp_path):
    doc = model.new_document(text='Title\nRich text\nHidden note')
    doc['blocks'][0].update(type='heading', style='heading1')
    doc['blocks'][0]['attrs']['level'] = 1
    doc['blocks'][1]['marks'] = [
        {'type': 'bold', 'value': True, 'start': 0, 'end': 4},
        {'type': 'underline', 'value': True, 'start': 5, 'end': 9},
    ]
    doc['blocks'][2]['type'] = 'footnote'
    projected, _ = project(doc, fmt)
    target = tmp_path / ('roundtrip.' + fmt)
    export_document(doc, target)
    reopened, _ = import_document(target)
    assert [(b['type'], b['text']) for b in projected['blocks']] == [
        (b['type'], b['text']) for b in reopened['blocks']
    ]
    # ODT materializes inherited fonts as marks; compare explicit authored marks
    # separately from those inherited defaults.
    for before, after in zip(projected['blocks'], reopened['blocks']):
        assert all(mark in after['marks'] for mark in before['marks'])


def test_partial_selection_across_hidden_keeps_remaining_marks(window):
    window.doc = example()
    last = window.doc['blocks'][-1]
    last['marks'] = [{'type': 'bold', 'value': True, 'start': 5, 'end': 14}]
    window.refresh()
    hidden = copy.deepcopy(window.doc['blocks'][1])
    window.set_file_view('txt')
    window.editor.navigate(last['id'])
    end = window.editor.textCursor().position() + 5
    cursor = window.editor.textCursor()
    cursor.setPosition(5)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    window.editor.setTextCursor(cursor)
    QTest.keyClick(window.editor, Qt.Key_Delete)
    assert window.flush_edits()
    assert window.doc['blocks'][1] == hidden
    assert window.doc['blocks'][-1]['text'] == 'paragraph'
    assert window.doc['blocks'][-1]['marks'] == [{'type': 'bold', 'value': True, 'start': 0, 'end': 9}]


def test_dark_theme_persists_and_keeps_document_unchanged(window):
    from PySide6.QtGui import QPalette

    from byax.brand import DARK_COLORS, contrast_ratio

    settings = QSettings('ByAuthorX', 'ByAuthorX')
    previous = settings.value('appearance/theme', 'system')
    old = copy.deepcopy(window.doc)
    try:
        # The test window has isolated preferences; the application theme reads
        # the application's persisted setting, as all real windows do.
        settings.setValue('appearance/theme', 'dark')
        window.apply_theme()
        assert window.palette().color(QPalette.Window).lightness() < 128
        assert window.doc == old
        for role in ('ink', 'muted'):
            for surface in ('surface', 'paper'):
                assert contrast_ratio(DARK_COLORS[role], DARK_COLORS[surface]) >= 4.5
        assert contrast_ratio(DARK_COLORS['control'], DARK_COLORS['surface']) >= 3
    finally:
        settings.setValue('appearance/theme', previous)
        window.apply_theme()


def test_locked_table_projection_cannot_replace_master(window):
    from byax import engine

    doc = model.new_document(text='Start')
    doc, _ = engine.apply(
        doc,
        {'operations': [{'type': 'insert_table', 'after': doc['blocks'][0]['id'], 'rows': 1, 'columns': 2}]},
    )
    window.doc = doc
    window.refresh()
    window.set_file_view('txt')
    table = doc['blocks'][1]
    assert window.editor.navigate(table['id'])
    window.editor.insertPlainText('Overwrite')
    with pytest.raises(ValueError, match='read-only'):
        window.editor.collect_changes(window.doc)
    assert window.doc == doc
    window.refresh()


def test_registry_refactor_retains_golden_adapter_loss_reports():
    import json
    from pathlib import Path

    from byax.exchange import _exchange_losses

    fixture = json.loads((Path(__file__).parent / 'fixtures/file-view-losses.json').read_text())
    for fmt, expected in fixture['losses'].items():
        actual = []
        _exchange_losses(fixture['document'], fmt, actual)
        assert actual == expected


@pytest.mark.parametrize('offset,expected', [(0, (1, 6)), (5, (0, 5)), (2, (0, 6))])
def test_hidden_mark_edge_and_interior_insertions(window, offset, expected):
    window.doc = example()
    window.refresh()
    window.set_file_view('txt')
    cursor = window.editor.textCursor()
    cursor.setPosition(offset)
    window.editor.setTextCursor(cursor)
    window.editor.insertPlainText('x')
    assert window.flush_edits()
    mark = window.doc['blocks'][0]['marks'][0]
    assert (mark['start'], mark['end']) == expected


def test_enter_inserts_before_hidden_block_and_undo_redo(window):
    window.doc = example()
    hidden = copy.deepcopy(window.doc['blocks'][1])
    window.refresh()
    window.set_file_view('md')
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.EndOfBlock)
    window.editor.setTextCursor(cursor)
    window.editor.insertPlainText('\nNew')
    assert window.flush_edits()
    assert window.doc['blocks'][1]['text'] == 'New'
    assert window.doc['blocks'][2] == hidden
    revision = window.doc['revision']
    window.set_file_view('txt')
    window.undo()
    assert window.doc['blocks'][1] == hidden
    window.redo()
    assert window.doc['blocks'][2] == hidden
    assert window.doc['revision'] != revision  # restore itself records an ordinary revision


def test_literal_markdown_paste_and_source_preference(window, tmp_path):
    from PySide6.QtCore import QMimeData

    window.set_file_view('md')
    mime = QMimeData()
    mime.setHtml('<b>Rich clipboard</b>')
    mime.setText('**literal**')
    window.editor.insertFromMimeData(mime)
    assert window.flush_edits()
    assert not window.doc['blocks'][0]['marks']
    target = tmp_path / 'literal.md'
    export_document(window.doc, target)
    reopened, _ = import_document(target)
    assert model.text_of(reopened) == '**literal**'
    window.set_source_mode(1)
    window.set_file_view('txt')
    window.set_file_view('md')
    assert window.stack.currentIndex() == 2
    assert window.source_editor.toPlainText() == target.read_text()


@pytest.mark.parametrize('fmt', ['txt', 'md', 'html', 'docx', 'odt', 'rtf'])
def test_registered_inline_capabilities_match_real_exporters(fmt, tmp_path):
    values = {
        'bold': True,
        'italic': True,
        'underline': True,
        'strikethrough': True,
        'superscript': True,
        'subscript': True,
        'font_family': 'DejaVu Serif',
        'font_size': 18,
        'color': '#bb2200',
        'highlight': '#ffff00',
        'code': True,
        'link': 'https://example.com',
        'font': 'DejaVu Serif',
        'size': 18,
    }

    def normalized(mark):
        value = mark['value']
        if mark['type'] in ('color', 'highlight'):
            value = value.lower()
        return (
            {'font': 'font_family', 'size': 'font_size'}.get(mark['type'], mark['type']),
            value,
            mark['start'],
            mark['end'],
        )

    for kind, value in values.items():
        doc = model.new_document(text='ABC def')
        doc['blocks'][0]['marks'] = [{'type': kind, 'value': value, 'start': 0, 'end': 3}]
        candidate, _ = project(doc, fmt)
        path = tmp_path / ('out.' + fmt)
        export_document(doc, path, profile={'allow_markdown_losses': True})
        restored, _ = import_document(path)
        expected = [normalized(m) for m in candidate['blocks'][0]['marks']]
        actual = [normalized(m) for m in restored['blocks'][0]['marks']]
        assert all(m in actual for m in expected), (fmt, kind, expected, actual)
        # Inherited font marks may be materialized by ODT, but an unsupported
        # authored mark must not appear on precisely the authored range.
        if not expected:
            assert normalized(doc['blocks'][0]['marks'][0]) not in actual, (fmt, kind)


def test_odt_direct_spacing_survives_inherited_paragraph_style(tmp_path):
    from byax.formatting import style_values

    doc = model.new_document(text='Spaced paragraph')
    attrs = {'space_before': 12, 'space_after': 8, 'indent': 18, 'first_line_indent': 6}
    doc['blocks'][0]['attrs'].update(attrs)
    path = tmp_path / 'spacing.odt'
    export_document(doc, path)
    restored, _ = import_document(path)
    block = restored['blocks'][0]
    resolved = {**style_values(restored, block['style']), **block['attrs']}
    assert {key: resolved[key] for key in attrs} == attrs
    assert project(doc, 'odt')[0]['blocks'][0]['attrs'] == attrs


def test_approximate_view_drops_unexported_master_and_variants():
    doc = model.new_document(text='Page')
    doc['layout'].update(
        master='cover', masters={'cover': {'width': 1000}}, header={'first': 'Cover', 'text': 'Default'}
    )
    for fmt in ('docx', 'odt', 'rtf'):
        projected, _ = project(doc, fmt)
        assert 'masters' not in projected['layout'] and 'master' not in projected['layout']
        assert projected['layout']['header'] == 'Default'
    assert project(doc, 'pdf')[0]['layout'] == doc['layout']


def test_multiline_paste_across_hidden_block_preserves_hidden_and_paragraph_structure(window):
    window.doc = example()
    hidden = copy.deepcopy(window.doc['blocks'][1])
    window.refresh()
    window.set_file_view('txt')
    window.editor.selectAll()
    window.editor.insertPlainText('First\nSecond\nThird')
    assert window.flush_edits()
    assert window.doc['blocks'][3] == hidden
    assert [b['text'] for b in window.doc['blocks'][:3]] == ['First', 'Second', 'Third']
    assert all(b['style'] == 'body' for b in window.doc['blocks'][:3])


def test_publication_pixels_ignore_application_theme_and_keep_authored_colors(app, monkeypatch, tmp_path):
    from PIL import Image
    from PySide6.QtGui import QFont, QPalette

    from byax import brand
    from byax.layout import render

    settings = QSettings(str(tmp_path / 'theme.ini'), QSettings.IniFormat)
    monkeypatch.setattr(brand, 'QSettings', lambda *args: settings)
    old_palette, old_font = QPalette(app.palette()), QFont(app.font())
    old_style, old_theme = app.styleSheet(), app.property('byaxTheme')
    doc = model.new_document(text='Publication colors\nPlain black text must stay readable.')
    doc['blocks'][0].update(type='heading', style='heading1', attrs={'level': 1})
    doc['blocks'][0]['marks'] = [{'type': 'color', 'value': '#bb2200', 'start': 0, 'end': 18}]
    results = []
    try:
        for theme in ('light', 'dark'):
            settings.setValue('appearance/theme', theme)
            brand.configure_application(app)
            report = render(doc, tmp_path / theme)
            pixels = Image.open(report['outputs'][0]).convert('RGB')
            results.append(pixels.tobytes())
            colors = [color for _, color in pixels.getcolors(pixels.width * pixels.height)]
            assert any(max(color) < 50 for color in colors)
            assert any(color[0] > 150 and color[1] < 60 and color[2] < 30 for color in colors)
        assert results[0] == results[1]
    finally:
        app.setPalette(old_palette)
        app.setFont(old_font)
        app.setStyleSheet(old_style)
        app.setProperty('byaxTheme', old_theme)
