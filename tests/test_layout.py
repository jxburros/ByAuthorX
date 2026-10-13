from pathlib import Path

import pytest
from PySide6.QtGui import QTextCursor

from byax.layout import BLOCK_ID_PROPERTY, build_document, export_pdf, render
from byax.model import new_document


def test_unicode_marks_and_stable_mapping():
    doc = new_document('Example', '😀 café 文')
    block = doc['blocks'][0]
    block['marks'] = [{'start': 2, 'end': 6, 'type': 'bold', 'value': True}]
    qdoc = build_document(doc)
    assert qdoc.toPlainText() == '😀 café 文'
    assert qdoc.firstBlock().blockFormat().property(BLOCK_ID_PROPERTY) == block['id']
    cursor = QTextCursor(qdoc)
    cursor.setPosition(4)
    assert cursor.charFormat().fontWeight() > 500


def test_table_and_page_render_same_engine(tmp_path):
    doc = new_document('Report', 'Introduction')
    doc['layout']['footer'] = '{page} / {pages}'
    doc['blocks'].append({'id': 'table_test', 'type': 'table', 'text': '', 'style': 'body', 'marks': [], 'attrs': {'header_rows': 1}, 'rows': [
        {'id': 'row1', 'cells': [{'id': 'cell1', 'text': 'Header', 'attrs': {}}, {'id': 'cell2', 'text': 'Value', 'attrs': {}}]},
        {'id': 'row2', 'cells': [{'id': 'cell3', 'text': 'café', 'attrs': {}}, {'id': 'cell4', 'text': '文', 'attrs': {}}]},
    ]})
    qdoc = build_document(doc)
    assert 'Header' in qdoc.toPlainText()
    assert 'cell3' in qdoc.byax_positions
    pdf = export_pdf(doc, tmp_path / 'output.pdf')
    pictures = render(doc, tmp_path / 'pages')
    assert pdf['page_count'] == pictures['page_count']
    assert (tmp_path / 'output.pdf').read_bytes().startswith(b'%PDF')
    assert Path(pictures['outputs'][0]).read_bytes().startswith(b'\x89PNG')


def test_unresolved_layout_has_located_losses():
    doc = new_document('Report', 'Text')
    doc['blocks'][0]['attrs']['font_family'] = 'Definitely Not Installed Font'
    doc['blocks'].append({'id': 'note1', 'type': 'footnote', 'text': 'Note text', 'style': 'body', 'marks': [], 'attrs': {}})
    qdoc = build_document(doc)
    assert any(f['code'] == 'font_substitution' and f['target'] == doc['blocks'][0]['id'] for f in qdoc.byax_findings)
    assert any(f['code'] == 'footnote_placement' and f['target'] == 'note1' for f in qdoc.byax_findings)


def test_selected_pages_and_toc(tmp_path):
    doc = new_document('Report', 'Beginning')
    doc['blocks'] += [
        {'id': 'toc', 'type': 'toc', 'text': '', 'style': 'body', 'marks': [], 'attrs': {}},
        {'id': 'break', 'type': 'page_break', 'text': '', 'style': 'body', 'marks': [], 'attrs': {}},
        {'id': 'heading', 'type': 'heading', 'text': 'Second', 'style': 'body', 'marks': [], 'attrs': {'level': 1}},
    ]
    qdoc = build_document(doc)
    assert 'Second    2' in qdoc.toPlainText()
    report = render(doc, tmp_path, pages=[2])
    assert report['pages'] == [2]
    with pytest.raises(ValueError):
        render(doc, tmp_path, pages=[99])


def test_resolved_columns_and_section_geometry_preserve_flow(tmp_path):
    from byax.layout import resolve_layout, resolved_page_count, resolved_page_size
    doc = new_document('Newsletter', 'Opening ' * 200)
    doc['layout']['columns'] = {'count': 2, 'gap': 18}
    doc['blocks'].append({'id': 'section', 'type': 'section_break', 'text': '', 'style': 'body', 'marks': [], 'attrs': {'layout': {'width': 595, 'height': 842, 'columns': 1}}})
    doc['blocks'].append({'id': 'ending', 'type': 'paragraph', 'text': 'Unique ending', 'style': 'body', 'marks': [], 'attrs': {}})
    qdoc = build_document(doc)
    resolved = resolve_layout(qdoc, doc)
    assert len(resolved['pages'][0]['fragments']) == 2
    assert resolved_page_size(qdoc, resolved_page_count(qdoc)-1) == (595, 842)
    # Every virtual flow page is drawn once and only once, in column order.
    fragments = [f for p in resolved['pages'] for f in p['fragments']]
    keys = [(id(f['document']), f['page']) for f in fragments]
    assert len(keys) == len(set(keys))
    assert not any(f['code'] in ('columns', 'section_geometry') for f in qdoc.byax_findings)
    report = export_pdf(doc, tmp_path/'columns.pdf')
    assert report['page_count'] == resolved_page_count(qdoc)


def test_footnotes_follow_reference_after_reflow():
    from byax.layout import resolve_layout
    doc = new_document('Notes', 'Reference sentence.')
    reference = doc['blocks'][0]['id']
    doc['blocks'].append({'id': 'note', 'type': 'footnote', 'text': 'Unique footnote text', 'style': 'body', 'marks': [], 'attrs': {'reference': reference}})
    for repeats in (1, 500):
        doc['blocks'][0]['text'] = 'Reference sentence. ' * repeats
        qdoc = build_document(doc)
        result = resolve_layout(qdoc, doc)
        note_pages = [index for index, page in enumerate(result['pages']) if any(f['kind'] == 'footnotes' for f in page['fragments'])]
        assert len(note_pages) == 1
        note_page = result['pages'][note_pages[0]]
        assert any('Unique footnote text' in f['document'].toPlainText() for f in note_page['fragments'] if f['kind'] == 'footnotes')
        assert not any(f['code'] in ('footnote_placement', 'footnote_orphaned', 'footnote_reflow') for f in qdoc.byax_findings)
        if repeats == 500:
            assert note_pages[0] > 0


def test_canonical_newline_stays_in_single_qt_block():
    doc = new_document('Soft break', 'first\nsecond')
    # new_document may split paragraphs; make one explicitly.
    doc['blocks'] = [doc['blocks'][0]]
    doc['blocks'][0]['text'] = 'first\nsecond'
    qdoc = build_document(doc)
    assert qdoc.blockCount() == 1
    assert qdoc.toPlainText() == 'first\nsecond'


def test_resource_limits_before_image_allocation(tmp_path):
    from byax.errors import ByaxError
    doc = new_document()
    doc['layout']['width'] = 1e9
    with pytest.raises(ByaxError) as error:
        render(doc, tmp_path)
    assert error.value.code == 'resource_limit'


def test_page_number_formats_and_section_restarts():
    from byax.layout import _format_page_number, _region, resolve_layout
    assert [_format_page_number(28, kind) for kind in ('decimal', 'upper_roman', 'lower_roman', 'upper_alpha', 'lower_alpha')] == ['28', 'XXVIII', 'xxviii', 'AB', 'ab']
    doc = new_document('Numbering', 'Front matter')
    doc['layout'].update(page_number_format='lower_roman', page_number_start=4, footer={'first': 'First {page}', 'odd': 'Odd {page}', 'even': 'Even {page}'})
    doc['blocks'].extend([
        {'id': 'pagebreak', 'type': 'page_break', 'text': '', 'style': 'body', 'marks': [], 'attrs': {}},
        {'id': 'preface', 'type': 'paragraph', 'text': 'Preface', 'style': 'body', 'marks': [], 'attrs': {}},
        {'id': 'section', 'type': 'section_break', 'text': '', 'style': 'body', 'marks': [], 'attrs': {'layout': {'page_number_format': 'decimal', 'page_number_start': 1}}},
        {'id': 'body', 'type': 'paragraph', 'text': 'Body', 'style': 'body', 'marks': [], 'attrs': {}},
    ])
    pages = resolve_layout(build_document(doc), doc)['pages']
    assert [page['number_label'] for page in pages] == ['iv', 'v', '1']
    assert [page['section_page'] for page in pages] == [1, 2, 1]
    assert _region(doc['layout']['footer'], 1, 3, number=5, number_format='lower_roman', section_page=2) == 'Odd v'
    assert _region(doc['layout']['footer'], 2, 3, number=1, section_page=1) == 'First 1'


def test_keep_together_preserves_wrapping_and_moves_fitting_paragraph():
    doc = new_document('Keep', 'Filler\n'*7)
    doc['layout'].update(width=300, height=220, margins={key:20 for key in ('left','right','top','bottom')})
    doc['blocks'] = [doc['blocks'][0]]
    doc['blocks'][0]['text'] = 'Filler\n'*7
    paragraph = {'id': 'kept', 'type': 'paragraph', 'text': 'Word '*50, 'style': 'body', 'marks': [], 'attrs': {'keep_together': True}}
    doc['blocks'].append(paragraph)
    qdoc = build_document(doc)
    block = qdoc.findBlock(qdoc.byax_positions['kept'])
    lines = block.layout()
    assert lines.lineCount() > 1
    rect = qdoc.documentLayout().blockBoundingRect(block)
    first = int((rect.top()+lines.lineAt(0).y())//220)
    last = int((rect.top()+lines.lineAt(lines.lineCount()-1).y())//220)
    assert first == last
    assert first > 0


def test_heading_keeps_following_first_line_and_reports_widow_request():
    doc = new_document('Keep next', 'Filler\n'*10)
    doc['layout'].update(width=300, height=220, margins={key:20 for key in ('left','right','top','bottom')})
    doc['blocks'] = [doc['blocks'][0]]
    doc['blocks'][0]['text'] = 'Filler\n'*10
    doc['blocks'].extend([
        {'id': 'heading_keep', 'type': 'heading', 'text': 'Heading', 'style': 'heading2', 'marks': [], 'attrs': {'level':2}},
        {'id': 'following', 'type': 'paragraph', 'text': 'Following paragraph.', 'style': 'body', 'marks': [], 'attrs': {'widow_control': 2}},
    ])
    qdoc = build_document(doc)
    heading = qdoc.findBlock(qdoc.byax_positions['heading_keep'])
    following = qdoc.findBlock(qdoc.byax_positions['following'])
    assert int(qdoc.documentLayout().blockBoundingRect(heading).top()//220) == int(qdoc.documentLayout().blockBoundingRect(following).top()//220)
    assert any(f['code'] == 'pagination_policy' and f['target'] == 'following' for f in qdoc.byax_findings)
