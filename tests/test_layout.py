from pathlib import Path

import pytest
from PySide6.QtGui import QTextCursor

from byax.layout import BLOCK_ID_PROPERTY, _line_pages, build_document, export_pdf, render, resolve_layout
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
    doc['blocks'].append(
        {
            'id': 'table_test',
            'type': 'table',
            'text': '',
            'style': 'body',
            'marks': [],
            'attrs': {'header_rows': 1},
            'rows': [
                {
                    'id': 'row1',
                    'cells': [
                        {'id': 'cell1', 'text': 'Header', 'attrs': {}},
                        {'id': 'cell2', 'text': 'Value', 'attrs': {}},
                    ],
                },
                {
                    'id': 'row2',
                    'cells': [
                        {'id': 'cell3', 'text': 'café', 'attrs': {}},
                        {'id': 'cell4', 'text': '文', 'attrs': {}},
                    ],
                },
            ],
        }
    )
    qdoc = build_document(doc)
    assert 'Header' in qdoc.toPlainText()
    assert 'cell3' in qdoc.byax_positions
    pdf = export_pdf(doc, tmp_path / 'output.pdf')
    pictures = render(doc, tmp_path / 'pages')
    assert pdf['page_count'] == pictures['page_count']
    assert (tmp_path / 'output.pdf').read_bytes().startswith(b'%PDF')
    assert Path(pictures['outputs'][0]).read_bytes().startswith(b'\x89PNG')


def test_default_table_gridlines_and_inherited_cell_style(tmp_path):
    from PIL import Image

    from byax import engine

    doc = new_document(text='Body')
    doc, _ = engine.apply(doc, {'operations': [{'type': 'insert_table', 'rows': 3, 'columns': 3}]})
    table = doc['blocks'][-1]
    for r, row in enumerate(table['rows']):
        for c, cell in enumerate(row['cells']):
            cell['text'] = f'R{r}C{c}'
    qdoc = build_document(doc)
    cursor = QTextCursor(qdoc)
    cursor.setPosition(qdoc.byax_positions[table['rows'][0]['cells'][0]['id']] + 1)
    assert cursor.currentTable().format().border() == 1
    assert cursor.charFormat().fontFamilies() == ['DejaVu Sans']
    assert cursor.charFormat().fontPointSize() == 11
    pages = render(doc, tmp_path / 'pages')
    image = Image.open(pages['outputs'][0]).convert('RGB')
    # Text in this fixture is shorter than 60 pixels; long dark horizontal
    # runs are table borders. Group adjacent scanlines into distinct rules.
    rules, previous = [], -2
    pixels = image.load()
    for y in range(image.height):
        run = 0
        for x in range(image.width):
            red, green, blue = pixels[x, y]
            run = run + 1 if max(red, green, blue) < 200 else 0
            if run >= 60:
                if y > previous + 1:
                    rules.append(y)
                previous = y
                break
    assert len(rules) >= 4
    pdf = export_pdf(doc, tmp_path / 'table.pdf')
    assert pdf['page_count'] == pages['page_count']
    doc['styles']['body']['font_size'] = 13
    table['rows'][0]['cells'][0]['attrs'] = {'font_family': 'DejaVu Sans Mono', 'font_size': 9}
    qdoc = build_document(doc)
    cursor = QTextCursor(qdoc)
    for cell, family, size in [
        (table['rows'][0]['cells'][0], 'DejaVu Sans Mono', 9),
        (table['rows'][1]['cells'][0], 'DejaVu Sans', 13),
    ]:
        cursor.setPosition(qdoc.byax_positions[cell['id']] + 1)
        assert cursor.charFormat().fontFamilies() == [family]
        assert cursor.charFormat().fontPointSize() == size


def test_unresolved_layout_has_located_losses():
    doc = new_document('Report', 'Text')
    doc['blocks'][0]['attrs']['font_family'] = 'Definitely Not Installed Font'
    doc['blocks'].append(
        {'id': 'note1', 'type': 'footnote', 'text': 'Note text', 'style': 'body', 'marks': [], 'attrs': {}}
    )
    qdoc = build_document(doc)
    assert any(
        f['code'] == 'font_substitution' and f['target'] == doc['blocks'][0]['id'] for f in qdoc.byax_findings
    )
    assert any(f['code'] == 'footnote_placement' and f['target'] == 'note1' for f in qdoc.byax_findings)


def test_selected_pages_and_toc(tmp_path):
    doc = new_document('Report', 'Beginning')
    doc['blocks'] += [
        {'id': 'toc', 'type': 'toc', 'text': '', 'style': 'body', 'marks': [], 'attrs': {}},
        {'id': 'break', 'type': 'page_break', 'text': '', 'style': 'body', 'marks': [], 'attrs': {}},
        {
            'id': 'heading',
            'type': 'heading',
            'text': 'Second',
            'style': 'body',
            'marks': [],
            'attrs': {'level': 1},
        },
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
    doc['blocks'].append(
        {
            'id': 'section',
            'type': 'section_break',
            'text': '',
            'style': 'body',
            'marks': [],
            'attrs': {'layout': {'width': 595, 'height': 842, 'columns': 1}},
        }
    )
    doc['blocks'].append(
        {
            'id': 'ending',
            'type': 'paragraph',
            'text': 'Unique ending',
            'style': 'body',
            'marks': [],
            'attrs': {},
        }
    )
    qdoc = build_document(doc)
    resolved = resolve_layout(qdoc, doc)
    assert len(resolved['pages'][0]['fragments']) == 2
    assert resolved_page_size(qdoc, resolved_page_count(qdoc) - 1) == (595, 842)
    # Every virtual flow page is drawn once and only once, in column order.
    fragments = [f for p in resolved['pages'] for f in p['fragments']]
    keys = [(id(f['document']), f['source'].top(), f['source'].bottom()) for f in fragments]
    assert len(keys) == len(set(keys))
    ids = [i for f in fragments for i in f['ids']]
    assert len(ids) == len(set(ids))
    assert not any(f['code'] in ('columns', 'section_geometry') for f in qdoc.byax_findings)
    report = export_pdf(doc, tmp_path / 'columns.pdf')
    assert report['page_count'] == resolved_page_count(qdoc)


def test_footnotes_follow_reference_after_reflow():
    from byax.layout import resolve_layout

    doc = new_document('Notes', 'Reference sentence.')
    reference = doc['blocks'][0]['id']
    doc['blocks'].append(
        {
            'id': 'note',
            'type': 'footnote',
            'text': 'Unique footnote text',
            'style': 'body',
            'marks': [],
            'attrs': {'reference': reference},
        }
    )
    for repeats in (1, 500):
        doc['blocks'][0]['text'] = 'Reference sentence. ' * repeats
        qdoc = build_document(doc)
        result = resolve_layout(qdoc, doc)
        note_pages = [
            index
            for index, page in enumerate(result['pages'])
            if any(f['kind'] == 'footnotes' for f in page['fragments'])
        ]
        assert len(note_pages) == 1
        note_page = result['pages'][note_pages[0]]
        assert any(
            'Unique footnote text' in f['document'].toPlainText()
            for f in note_page['fragments']
            if f['kind'] == 'footnotes'
        )
        assert not any(
            f['code'] in ('footnote_placement', 'footnote_orphaned', 'footnote_reflow')
            for f in qdoc.byax_findings
        )
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

    assert [
        _format_page_number(28, kind)
        for kind in ('decimal', 'upper_roman', 'lower_roman', 'upper_alpha', 'lower_alpha')
    ] == ['28', 'XXVIII', 'xxviii', 'AB', 'ab']
    doc = new_document('Numbering', 'Front matter')
    doc['layout'].update(
        page_number_format='lower_roman',
        page_number_start=4,
        footer={'first': 'First {page}', 'odd': 'Odd {page}', 'even': 'Even {page}'},
    )
    doc['blocks'].extend(
        [
            {'id': 'pagebreak', 'type': 'page_break', 'text': '', 'style': 'body', 'marks': [], 'attrs': {}},
            {
                'id': 'preface',
                'type': 'paragraph',
                'text': 'Preface',
                'style': 'body',
                'marks': [],
                'attrs': {},
            },
            {
                'id': 'section',
                'type': 'section_break',
                'text': '',
                'style': 'body',
                'marks': [],
                'attrs': {'layout': {'page_number_format': 'decimal', 'page_number_start': 1}},
            },
            {'id': 'body', 'type': 'paragraph', 'text': 'Body', 'style': 'body', 'marks': [], 'attrs': {}},
        ]
    )
    pages = resolve_layout(build_document(doc), doc)['pages']
    assert [page['number_label'] for page in pages] == ['iv', 'v', '1']
    assert [page['section_page'] for page in pages] == [1, 2, 1]
    assert (
        _region(doc['layout']['footer'], 1, 3, number=5, number_format='lower_roman', section_page=2)
        == 'Odd v'
    )
    assert _region(doc['layout']['footer'], 2, 3, number=1, section_page=1) == 'First 1'


def test_keep_together_preserves_wrapping_and_moves_fitting_paragraph():
    doc = new_document('Keep', 'Filler\n' * 7)
    doc['layout'].update(
        width=300, height=220, margins={key: 20 for key in ('left', 'right', 'top', 'bottom')}
    )
    doc['blocks'] = [doc['blocks'][0]]
    doc['blocks'][0]['text'] = 'Filler\n' * 7
    paragraph = {
        'id': 'kept',
        'type': 'paragraph',
        'text': 'Word ' * 50,
        'style': 'body',
        'marks': [],
        'attrs': {'keep_together': True},
    }
    doc['blocks'].append(paragraph)
    qdoc = build_document(doc)
    block = qdoc.findBlock(qdoc.byax_positions['kept'])
    lines = block.layout()
    assert lines.lineCount() > 1
    rect = qdoc.documentLayout().blockBoundingRect(block)
    first = int((rect.top() + lines.lineAt(0).y()) // 220)
    last = int((rect.top() + lines.lineAt(lines.lineCount() - 1).y()) // 220)
    assert first == last
    assert first > 0


def _widow_doc(filler, words, attrs, heading=False):
    doc = new_document('Widows', 'x')
    doc['layout'].update(
        width=300, height=220, margins={key: 20 for key in ('left', 'right', 'top', 'bottom')}
    )
    doc['blocks'] = [
        {'id': f'f{i}', 'type': 'paragraph', 'text': 'Filler', 'style': 'body', 'marks': [], 'attrs': {}}
        for i in range(filler)
    ]
    if heading:
        doc['blocks'].append(
            {
                'id': 'h',
                'type': 'heading',
                'text': 'Heading',
                'style': 'heading2',
                'marks': [],
                'attrs': {'level': 2},
            }
        )
    doc['blocks'].append(
        {
            'id': 'p',
            'type': 'paragraph',
            'text': 'Word ' * words,
            'style': 'body',
            'marks': [],
            'attrs': attrs,
        }
    )
    return doc


def _pages_of(qdoc, ident):
    return _line_pages(qdoc, qdoc.findBlock(qdoc.byax_positions[ident]), 220)


def test_heading_keeps_following_first_line():
    doc = new_document('Keep next', 'Filler\n' * 10)
    doc['layout'].update(
        width=300, height=220, margins={key: 20 for key in ('left', 'right', 'top', 'bottom')}
    )
    doc['blocks'] = [doc['blocks'][0]]
    doc['blocks'][0]['text'] = 'Filler\n' * 10
    doc['blocks'].extend(
        [
            {
                'id': 'heading_keep',
                'type': 'heading',
                'text': 'Heading',
                'style': 'heading2',
                'marks': [],
                'attrs': {'level': 2},
            },
            {
                'id': 'following',
                'type': 'paragraph',
                'text': 'Following paragraph.',
                'style': 'body',
                'marks': [],
                'attrs': {'widow_control': 2},
            },
        ]
    )
    qdoc = build_document(doc)
    heading = qdoc.findBlock(qdoc.byax_positions['heading_keep'])
    following = qdoc.findBlock(qdoc.byax_positions['following'])
    assert int(qdoc.documentLayout().blockBoundingRect(heading).top() // 220) == int(
        qdoc.documentLayout().blockBoundingRect(following).top() // 220
    )
    assert not any(f['code'] == 'pagination_policy' for f in qdoc.byax_findings)


def test_heading_is_not_stranded_when_qt_pushes_following_first_line():
    qdoc = build_document(_widow_doc(9, 40, {}, heading=True))
    assert _pages_of(qdoc, 'h')[-1] == _pages_of(qdoc, 'p')[0]


def test_orphan_control_moves_paragraph_with_too_few_first_page_lines():
    plain = _pages_of(build_document(_widow_doc(10, 40, {})), 'p')
    assert plain.count(plain[0]) == 1 and plain[0] != plain[-1]
    controlled = build_document(_widow_doc(10, 40, {'orphan_control': 2}))
    pages = _pages_of(controlled, 'p')
    assert pages[0] == pages[-1] == plain[-1]
    assert not controlled.byax_findings


def test_widow_control_line_count_and_heading_chain_move_together():
    plain = _pages_of(build_document(_widow_doc(8, 40, {})), 'p')
    assert plain.count(plain[-1]) == 2
    assert _pages_of(build_document(_widow_doc(8, 40, {'widow_control': 2})), 'p') == plain
    pages = _pages_of(build_document(_widow_doc(8, 40, {'widow_control': 3})), 'p')
    assert pages[0] == pages[-1]
    qdoc = build_document(_widow_doc(8, 40, {'orphan_control': True}, heading=True))
    assert _pages_of(qdoc, 'h')[-1] == _pages_of(qdoc, 'p')[0] == _pages_of(qdoc, 'p')[-1]


def test_unsatisfiable_widow_rule_reports_finding_and_keeps_all_text():
    def lone_last_line(words):
        pages = _pages_of(build_document(_widow_doc(0, words, {})), 'p')
        return pages.count(pages[-1]) == 1 and len(set(pages)) > 1

    words = next(w for w in range(100, 160, 4) if lone_last_line(w))
    doc = _widow_doc(0, words, {'widow_control': 2})
    qdoc = build_document(doc)
    assert any(f['code'] == 'widow_unsatisfiable' and f['target'] == 'p' for f in qdoc.byax_findings)
    assert qdoc.findBlock(qdoc.byax_positions['p']).text() == doc['blocks'][-1]['text']


def test_orphan_control_applies_within_columns():
    doc = _widow_doc(9, 60, {'orphan_control': 2})
    doc['layout'].update(columns=2, width=500)
    flow = resolve_layout(build_document(doc), doc)['pages'][0]['fragments'][0]['document']
    pages = _line_pages(flow, flow.findBlock(flow.byax_positions['p']), flow.pageSize().height())
    assert pages[0] == pages[-1]
