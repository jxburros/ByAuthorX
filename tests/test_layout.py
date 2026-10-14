import copy
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
    from byax.layout import block_lines

    lines = block_lines(resolve_layout(build_document(doc), doc), 'p')
    segments = [(line['page'], line['x']) for line in lines]
    assert segments.count(segments[0]) >= 2 and segments.count(segments[-1]) >= 1
    assert ''.join(line['text'] for line in lines).split() == doc['blocks'][-1]['text'].split()


# ---------------------------------------------------------------------------
# Shared physical paginator: columns, keeps, notes, tables, masters, objects.


def _para(ident, text, style='body', **attrs):
    return {'id': ident, 'type': 'paragraph', 'text': text, 'style': style, 'marks': [], 'attrs': attrs}


def _heading(ident, text, **attrs):
    return {
        'id': ident,
        'type': 'heading',
        'text': text,
        'style': 'heading2',
        'marks': [],
        'attrs': {'level': 2, **attrs},
    }


def _note(ident, reference, text, **attrs):
    return {
        'id': ident,
        'type': 'footnote',
        'text': text,
        'style': 'body',
        'marks': [],
        'attrs': {'reference': reference, **attrs},
    }


def _section(ident, **attrs):
    return {'id': ident, 'type': 'section_break', 'text': '', 'style': 'body', 'marks': [], 'attrs': attrs}


def _png(width, height, color):
    import base64

    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return base64.b64encode(bytes(buffer.data())).decode()


def _resolved(doc, output=None):
    return resolve_layout(build_document(doc), doc, output)


def _segments(lines):
    """Consecutive line counts per physical column segment."""
    counts = []
    for line in lines:
        key = (line['page'], line['x'])
        if counts and counts[-1][0] == key:
            counts[-1][1] += 1
        else:
            counts.append([key, 1])
    return counts


def _pdf_words(path):
    import shutil
    import subprocess

    if not shutil.which('pdftotext'):
        pytest.skip('pdftotext is unavailable')
    return subprocess.run(
        ['pdftotext', str(path), '-'], capture_output=True, text=True, check=True
    ).stdout.split()


def test_unequal_columns_resolve_explicit_widths_without_loss_or_duplication(tmp_path):
    from byax.layout import block_lines

    words = [f'w{index}' for index in range(900)]
    doc = new_document('Columns', 'x')
    doc['blocks'] = [_para('story', ' '.join(words))]
    doc['layout']['columns'] = {'count': 2, 'widths': [300, 150], 'gap': 18}
    resolved = _resolved(doc)
    lines = block_lines(resolved, 'story')
    assert {(line['x'], line['width']) for line in lines} == {(72, 300), (390, 150)}
    assert ' '.join(line['text'] for line in lines).split() == words
    assert not [f for f in resolved['findings'] if f['code'] == 'column_geometry']
    pdf = export_pdf(doc, tmp_path / 'columns.pdf')
    assert pdf['page_count'] == len(resolved['pages'])
    extracted = [word for word in _pdf_words(tmp_path / 'columns.pdf') if word.startswith('w')]
    assert sorted(extracted) == sorted(words)
    # Unsatisfiable geometry is reported at the section, never silently dropped.
    doc['layout']['columns'] = {'count': 2, 'widths': [400, 300], 'gap': 18}
    resolved = _resolved(doc)
    assert any(f['code'] == 'column_geometry' and f['target'] == 'section:1' for f in resolved['findings'])
    assert ' '.join(line['text'] for line in block_lines(resolved, 'story')).split() == words


def test_column_validation_rejects_mismatched_widths():
    from byax.errors import ByaxError
    from byax.model import validate_document

    doc = new_document('Columns', 'x')
    doc['layout']['columns'] = {'count': 3, 'widths': [100, 100]}
    with pytest.raises(ByaxError) as error:
        validate_document(doc)
    assert error.value.details['field'] == 'layout.columns.widths'


def test_full_width_material_and_continuous_sections_share_a_physical_page():
    from byax.layout import block_lines

    doc = new_document('Newsletter', 'x')
    doc['layout']['columns'] = {'count': 2, 'gap': 18}
    doc['blocks'] = [
        _para('lead', 'Alpha ' * 120),
        _heading('banner', 'Full width banner', full_width=True),
        _para('body', 'Beta ' * 120),
        _section('three', start='continuous', layout={'columns': {'count': 3, 'gap': 12}}),
        _para('tail', 'Gamma ' * 120),
    ]
    resolved = _resolved(doc)
    assert len(resolved['pages']) == 1
    lead = _segments(block_lines(resolved, 'lead'))
    assert len(lead) == 2 and abs(lead[0][1] - lead[1][1]) <= 1  # balanced before the banner
    banner = block_lines(resolved, 'banner')[0]
    assert banner['width'] == 468 and banner['y'] > max(line['y'] for line in block_lines(resolved, 'lead'))
    assert min(line['y'] for line in block_lines(resolved, 'body')) > banner['y']
    tail = block_lines(resolved, 'tail')
    assert {round(line['width']) for line in tail} == {148}
    assert min(line['y'] for line in tail) > max(line['y'] for line in block_lines(resolved, 'body'))


def test_column_breaks_and_odd_page_sections_are_explicit():
    doc = new_document('Breaks', 'x')
    doc['layout'].update(
        width=300, height=220, margins={key: 20 for key in ('left', 'right', 'top', 'bottom')}
    )
    doc['layout']['columns'] = {'count': 2, 'gap': 10}
    doc['blocks'] = [
        _para('a', 'A'),
        _para('b', 'B', column_break_before=True),
        _para('c', 'C', column_break_before=True),
        _section('even', start='even_page', layout={'columns': 1}),
        _para('d', 'D'),
    ]
    resolved = _resolved(doc)
    assert resolved['anchors']['a']['x'] == 20 and resolved['anchors']['b']['x'] == 155
    assert resolved['positions']['c'] == 2
    assert resolved['positions']['d'] == 4 and resolved['pages'][2]['fragments'] == []
    assert resolved['pages'][3]['number'] % 2 == 0


@pytest.mark.parametrize(
    ('paper', 'font_size'),
    [({'width': 300, 'height': 220}, 11), ({'paper': 'A4', 'width': 595.276, 'height': 841.89}, 14), ({}, 9)],
)
def test_widow_orphan_and_keep_rules_hold_on_crowded_pages(paper, font_size):
    from byax.layout import block_lines

    doc = new_document('Crowded', 'x')
    doc['layout'].update(paper)
    doc['layout']['columns'] = {'count': 2, 'gap': 12}
    doc['styles']['body'].update(font_size=font_size, widow_control=2, orphan_control=2)
    doc['blocks'] = []
    for index in range(40):
        if index % 7 == 3:
            doc['blocks'].append(_heading(f'h{index}', f'Heading {index}'))
        doc['blocks'].append(_para(f'p{index}', f'Para{index} ' + 'word ' * (5 + index * 7 % 60)))
    resolved = _resolved(doc)
    findings = {(f['code'], f['target']) for f in resolved['findings']}
    for block in doc['blocks']:
        lines = block_lines(resolved, block['id'])
        assert ''.join(line['text'] for line in lines).split() == block['text'].split()
        segments = _segments(lines)
        if block['type'] == 'heading':
            following = block_lines(resolved, doc['blocks'][doc['blocks'].index(block) + 1]['id'])
            assert (lines[-1]['page'], lines[-1]['x']) == (following[0]['page'], following[0]['x'])
        elif len(segments) > 1 and ('widow_orphan_unsatisfiable', block['id']) not in findings:
            assert segments[0][1] >= 2 and segments[-1][1] >= 2


def test_impossible_keep_rules_report_findings_and_keep_all_text():
    from byax.layout import block_lines

    doc = new_document('Impossible', 'x')
    doc['layout'].update(
        width=300, height=220, margins={key: 20 for key in ('left', 'right', 'top', 'bottom')}
    )
    text = 'Word ' * 400
    doc['blocks'] = [_para('big', text, keep_together=True), _para('after', 'After')]
    resolved = _resolved(doc)
    assert any(f['code'] == 'keep_rule_unsatisfiable' and f['target'] == 'big' for f in resolved['findings'])
    assert ''.join(line['text'] for line in block_lines(resolved, 'big')).split() == text.split()
    assert resolved['positions']['after'] == len(resolved['pages'])


def test_crowded_footnotes_stay_with_references_without_uniform_reservation():
    from byax.layout import block_lines

    doc = new_document('Notes', 'x')
    doc['blocks'] = []
    for index in range(30):
        doc['blocks'].append(_para(f'p{index}', f'Paragraph {index} ' + 'text ' * 30))
        long_note = index % 10 == 0
        doc['blocks'].append(
            _note(f'n{index}', f'p{index}', f'Note {index} ' + 'detail ' * (60 if long_note else 4))
        )
    resolved = _resolved(doc)
    assert [resolved['notes'][f'n{index}']['number'] for index in range(30)] == list(range(1, 31))
    for index in range(30):
        marker_pages = [
            line['page'] for line in block_lines(resolved, f'p{index}') if str(index + 1) in line['text']
        ]
        assert resolved['notes'][f'n{index}']['page'] in marker_pages
    used = []
    for page in resolved['pages']:
        body = max(f['rect'].bottom() for f in page['fragments'] if f['kind'] == 'body')
        notes = [f['rect'] for f in page['fragments'] if f['kind'] == 'footnotes']
        assert not notes or body <= min(rect.top() for rect in notes) - 6
        assert not notes or abs(max(rect.bottom() for rect in notes) - (page['height'] - 72)) < 0.5
        used.append(body)
    # Body height varies with each page's own notes instead of one section-wide reservation.
    assert len({round(value) for value in used[:-1]}) > 1
    assert not any(
        f['code'] in ('footnote_reflow', 'footnote_overflow', 'footnote_orphaned')
        for f in resolved['findings']
    )


def test_oversized_footnote_continues_after_its_reference_page():
    doc = new_document('Long note', 'x')
    doc['blocks'] = [_para('a', 'Reference here.'), _note('big', 'a', 'Long ' * 3000), _para('b', 'After')]
    resolved = _resolved(doc)
    slices = [f for page in resolved['pages'] for f in page['fragments'] if f['kind'] == 'footnotes']
    assert resolved['notes']['big']['page'] == resolved['positions']['a'] == 1
    assert len(slices) > 1 and not slices[0]['continued'] and all(f['continued'] for f in slices[1:])
    covered = sum(f['source'].height() for f in slices)
    assert abs(covered - slices[0]['document'].documentLayout().documentSize().height()) < 3.5
    tops = [f['source'].top() for f in slices]
    assert tops == sorted(tops) and len(set(tops)) == len(tops)
    assert any(f['code'] == 'footnote_continued' and f['target'] == 'a' for f in resolved['findings'])


def test_footnote_numbering_offsets_formats_and_section_restart():
    from byax.layout import block_lines

    doc = new_document('Numbering', 'x')
    doc['layout'].update(footnote_format='lower_roman', footnote_restart='section')
    doc['blocks'] = [
        _para('a', 'First second'),
        _note('n2', 'a', 'Later note'),
        _note('n1', 'a', 'Earlier note', offset=5),
        _section('s'),
        _para('b', 'Next section'),
        _note('n3', 'b', 'Restarted'),
    ]
    resolved = _resolved(doc)
    assert {key: value['label'] for key, value in resolved['notes'].items()} == {
        'n1': 'i',
        'n2': 'ii',
        'n3': 'i',
    }
    assert block_lines(resolved, 'a')[0]['text'] == 'Firsti secondii'


def test_cross_references_follow_reflow_bookmarks_and_report_deleted_targets():
    from byax import engine
    from byax.layout import block_lines

    doc = new_document('Report', 'See page  for details.')
    source = doc['blocks'][0]['id']
    doc['blocks'] += [_para('filler', 'Short.'), _heading('target', 'Results'), _para('end', 'Fin.')]
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'insert_cross_reference', 'target': source, 'offset': 9, 'ref': 'target'},
                {'type': 'set_bookmark', 'name': 'results', 'target': 'target'},
                {'type': 'insert_paragraph', 'text': 'Section  is named.', 'id': 'named'},
            ]
        },
    )
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'insert_cross_reference',
                    'target': 'named',
                    'offset': 8,
                    'ref': 'results',
                    'show': 'text',
                }
            ]
        },
    )
    assert block_lines(_resolved(doc), source)[0]['text'].startswith('See page 1 for')
    doc['blocks'][1]['text'] = 'Long filler. ' * 400
    resolved = _resolved(doc)
    page = resolved['positions']['target']
    assert page > 1
    assert block_lines(resolved, source)[0]['text'].startswith(f'See page {page} for')
    assert block_lines(resolved, 'named')[0]['text'] == 'Section Results is named.'
    assert doc['blocks'][0]['text'] == 'See page ? for details.'  # semantic target, not a stored page
    doc, _ = engine.apply(doc, {'operations': [{'type': 'delete_block', 'target': 'target'}]})
    resolved = _resolved(doc)
    assert block_lines(resolved, source)[0]['text'].startswith('See page ?? for')
    assert any(f['code'] == 'xref_unresolved' and f['target'] == source for f in resolved['findings'])


def _long_table(policy=None, tall=60, **attrs):
    rows = []
    for index in range(50):
        words = 'long ' * (tall if index == 7 else 3)
        rows.append(
            {
                'id': f'r{index}',
                'cells': [
                    {'id': f'c{index}a', 'text': f'Row{index} ' + words, 'attrs': {}},
                    {'id': f'c{index}b', 'text': f'V{index}', 'attrs': {}},
                ],
            }
        )
    table_attrs = {'header_rows': 1, **attrs}
    if policy:
        table_attrs['row_split'] = policy
    doc = new_document('Table', 'x')
    doc['blocks'] = [
        _para('intro', 'Intro ' * 300),
        {
            'id': 't',
            'type': 'table',
            'text': '',
            'style': 'body',
            'marks': [],
            'attrs': table_attrs,
            'rows': rows,
        },
    ]
    return doc


def test_tables_repeat_headers_and_keep_every_cell_exactly_once(tmp_path):
    doc = _long_table(continued_label='Release table (continued)')
    resolved = _resolved(doc)
    ids = [i for page in resolved['pages'] for f in page['fragments'] for i in f.get('ids', [])]
    cells = [f'c{index}{side}' for index in range(50) for side in 'ab']
    assert sorted(i for i in ids if i in cells) == sorted(cells)
    continuation = [
        page
        for page in resolved['pages'][1:]
        if any(f.get('ids') and f['ids'][0].startswith('r') for f in page['fragments'])
    ]
    assert continuation
    for page in continuation:
        kinds = [f['kind'] for f in page['fragments']]
        assert kinds[:3] == ['label', 'table_header', 'body']
    assert export_pdf(doc, tmp_path / 'table.pdf')['page_count'] == len(resolved['pages'])
    words = _pdf_words(tmp_path / 'table.pdf')
    assert all(words.count(f'Row{index}') == 1 for index in range(1, 50))
    assert words.count('Row0') == 1 + len(continuation)  # only the repeated header row is drawn again


def test_table_row_split_policies_and_oversized_rows():
    from byax.layout import block_lines

    def row_pages(doc):
        resolved = _resolved(doc)
        return resolved, {(line['page']) for line in block_lines(resolved, 'c7a')}

    for policy, expect_split in (('never', False), ('auto', False), ('allow', None)):
        doc = _long_table(policy, tall=40)
        doc['blocks'][0]['text'] = 'Intro ' * 700
        resolved, pages = row_pages(doc)
        if expect_split is False:
            assert len(pages) == 1
            assert not any(f['code'] == 'table_row_unsplittable' for f in resolved['findings'])
        text = ''.join(line['text'] for line in block_lines(resolved, 'c7a')).split()
        assert text == doc['blocks'][1]['rows'][7]['cells'][0]['text'].split()
    doc = _long_table('never', tall=1200)
    resolved, pages = row_pages(doc)
    assert len(pages) > 1
    assert any(f['code'] == 'table_row_unsplittable' for f in resolved['findings'])
    text = ' '.join(line['text'] for line in block_lines(resolved, 'c7a')).split()
    assert text == doc['blocks'][1]['rows'][7]['cells'][0]['text'].split()


def test_page_masters_assign_geometry_artwork_letterhead_and_regions(tmp_path):
    from PIL import Image

    from byax import engine
    from byax.storage import load_document, save_document

    doc = new_document('Press kit', 'x')
    letterhead = {
        'margins': {'top': 144, 'left': 72, 'right': 72, 'bottom': 72},
        'header': {
            'first': {'left': 'ACME RECORDS', 'right': 'Press'},
            'odd': 'Odd {page}',
            'even': 'Even {page}',
        },
        'artwork': [{'fill': '#ff0000', 'x': 72, 'y': 20, 'width': 60, 'height': 40, 'pages': 'first'}],
    }
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'define_master', 'name': 'letterhead', 'values': letterhead},
                {
                    'type': 'define_master',
                    'name': 'plain',
                    'values': {'margins': {'top': 50, 'left': 50, 'right': 50, 'bottom': 50}},
                },
                {'type': 'set_layout', 'values': {'master': 'letterhead'}},
            ]
        },
    )
    doc['blocks'] = [
        _para('bio', 'Biography ' * 700),
        _section('s', master='plain', restart_page_numbering=True, page_number_format='upper_roman'),
        _para('end', 'End'),
    ]
    save_document(doc, tmp_path / 'kit.byax')
    doc = load_document(tmp_path / 'kit.byax')
    assert doc['layout']['masters']['letterhead'] == letterhead
    resolved = _resolved(doc)
    masters = [(page['layout'].get('master'), page['body_top']) for page in resolved['pages']]
    assert masters[0] == ('letterhead', 144) and masters[-1] == ('plain', 50)
    assert resolved['pages'][-1]['number_label'] == 'I'
    report = render(doc, tmp_path / 'pages')
    first, second = (Image.open(path).convert('RGB') for path in report['outputs'][:2])
    assert first.getpixel((200, 80)) == (255, 0, 0) and second.getpixel((200, 80)) != (255, 0, 0)
    assert (
        first.crop((144, 36, 400, 280)).getbbox()
        and Image.eval(first.crop((144, 36, 400, 280)), lambda v: 255 - v).getbbox()
    )
    with pytest.raises(Exception) as error:
        engine.apply(doc, {'operations': [{'type': 'delete_master', 'name': 'plain'}]})
    assert error.value.code == 'precondition_failed'
    with pytest.raises(Exception) as error:
        engine.apply(doc, {'operations': [{'type': 'set_layout', 'values': {'master': 'missing'}}]})
    assert error.value.code == 'schema_error'


def test_header_footer_regions_use_first_odd_even_parts():
    from byax.layout import _region_parts

    value = {
        'first': {'left': 'L', 'right': 'R {page}'},
        'odd': 'Odd {page}',
        'even': {'center': 'Even {page}'},
    }
    assert _region_parts(value, 0, 3, number=1, section_page=1) == {'left': 'L', 'right': 'R 1'}
    assert _region_parts(value, 1, 3, number=2, section_page=2) == {'center': 'Even 2'}
    assert _region_parts(value, 2, 3, number=3, section_page=3, default_align='right') == {'right': 'Odd 3'}
    assert _region_parts({'odd': 'O', 'even': 'E'}, 0, 1, number=1, section_page=1) == {'left': 'O'}


def test_anchored_objects_policies_caption_keeps_and_reading_order():
    doc = new_document('Objects', 'x')
    doc['assets']['img'] = {'mime': 'image/png', 'data': _png(200, 100, 'red')}

    def image(ident, **attrs):
        return {
            'id': ident,
            'type': 'image',
            'text': '',
            'style': 'body',
            'marks': [],
            'attrs': {'asset': 'img', 'alt': 'Alt ' + ident, **attrs},
        }

    doc['blocks'] = [
        _para('a', 'Intro ' * 470),
        image('fig', width=300),
        _para('cap', 'Figure 1. Caption', style='caption'),
        _para('b', 'Body ' * 50),
        image(
            'over',
            wrap='behind_text',
            anchor={'target': 'b', 'policy': 'move_with_text'},
            offset_x=10,
            offset_y=5,
            width=100,
        ),
        image('fixed', wrap='in_front', position='fixed_page', page=1, x=400, y=600, width=50),
        image('lost', wrap='in_front', anchor='nope', width=20),
        image('huge', width=2000),
        image('boxed', width=100, height=100, lock_aspect=True),
    ]
    resolved = _resolved(doc)
    assert resolved['positions']['fig'] == resolved['positions']['cap']
    objects = {item['id']: item for item in resolved['objects']}
    assert [item['id'] for item in sorted(resolved['objects'], key=lambda i: i['reading_order'])] == [
        'fig',
        'over',
        'fixed',
        'lost',
        'huge',
        'boxed',
    ]
    anchor = resolved['anchors']['b']
    assert objects['over']['page'] == anchor['page'] and objects['over']['rect'][:2] == [
        anchor['x'] + 10,
        anchor['y'] + 5,
    ]
    assert objects['over']['rect'][2:] == [100, 50] and objects['over']['alt'] == 'Alt over'
    assert objects['fixed']['page'] == 1 and objects['fixed']['rect'][:2] == [400, 600]
    assert {(f['code'], f['target']) for f in resolved['findings']} >= {
        ('anchor_unresolved', 'lost'),
        ('image_scaled', 'huge'),
    }
    # Growing text moves anchored objects with their paragraph; fixed objects stay put.
    doc['blocks'][0]['text'] = 'Intro ' * 1400
    moved = {item['id']: item for item in _resolved(doc)['objects']}
    assert moved['over']['page'] > objects['over']['page'] and moved['fixed']['page'] == 1
    assert moved['fig']['page'] == _resolved(doc)['positions']['cap']
    from PySide6.QtGui import QImage

    from byax.layout import _image_size

    assert _image_size(
        {'width': 100, 'height': 100, 'lock_aspect': True}, QImage(200, 100, QImage.Format.Format_ARGB32), 500
    ) == (100, 50)


def test_layer_groups_locks_reordering_and_output_visibility(tmp_path):
    from byax import engine
    from byax.layout import layer_inspector

    doc = new_document('Layers', 'Body text')
    doc['blocks'].append(_para('draft', 'Draft only', exclude_outputs=['pdf']))
    doc['layers'] = [
        {'id': 'wm', 'name': 'Watermark', 'text': 'DRAFT', 'z': -1, 'visible': True, 'locked': False},
        {'id': 'logo', 'name': 'Logo', 'text': 'LOGO', 'z': 1, 'visible': True, 'locked': False},
        {'id': 'secret', 'name': 'Hidden', 'text': 'HIDDEN', 'visible': False, 'locked': False},
    ]
    doc, _ = engine.apply(
        doc,
        {'operations': [{'type': 'group_layers', 'name': 'Decor', 'targets': ['wm', 'logo'], 'id': 'decor'}]},
    )
    assert [layer['id'] for layer in doc['layers']][:3] == ['decor', 'wm', 'logo']
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'update_layer', 'target': 'decor', 'values': {'locked': True}}]}
    )
    with pytest.raises(Exception) as error:
        engine.apply(doc, {'operations': [{'type': 'update_layer', 'target': 'wm', 'values': {'name': 'x'}}]})
    assert error.value.code == 'protected_edit'
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'update_layer', 'target': 'decor', 'values': {'locked': False}}]}
    )
    doc, _ = engine.apply(doc, {'operations': [{'type': 'reorder_layer', 'target': 'secret', 'to_index': 0}]})
    inspector = {entry['id']: entry for entry in layer_inspector(doc)}
    assert inspector['decor']['members'] == ['wm', 'logo'] and inspector['wm']['group'] == 'decor'
    assert inspector['secret']['state'] == 'hidden' and inspector['secret']['redaction'] == 'not_redacted'
    assert inspector['secret']['stack_index'] < inspector['wm']['stack_index']
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'update_layer', 'target': 'decor', 'values': {'visible': False}}]}
    )
    original = copy.deepcopy(doc)
    screen = render(doc, tmp_path / 'screen', output='screen')
    pdf = export_pdf(doc, tmp_path / 'out.pdf', output='pdf')
    assert doc == original  # hiding/exclusion never deletes authored content
    assert (
        set(screen['visibility']['hidden']) == {'secret', 'decor', 'wm', 'logo'}
        and screen['visibility']['excluded'] == []
    )
    assert pdf['visibility']['excluded'] == ['draft'] and pdf['visibility']['redaction'] == 'not_performed'
    words = _pdf_words(tmp_path / 'out.pdf')
    assert 'Body' in words and 'Draft' not in words and 'HIDDEN' not in words and 'DRAFT' not in words
    doc, _ = engine.apply(doc, {'operations': [{'type': 'ungroup_layers', 'target': 'decor'}]})
    assert all('group' not in layer for layer in doc['layers'])


def test_preview_png_pdf_and_print_consume_the_same_break_decisions(tmp_path):
    from PySide6.QtGui import QImage, QPainter

    from byax.layout import draw_page, layout_signature, resolved_page_count

    doc = _long_table(continued_label='Continued')
    doc['blocks'].insert(1, _note('n', 'intro', 'A note'))
    doc['layout']['columns'] = {'count': 2, 'widths': [250, 190]}
    png = render(doc, tmp_path / 'png')
    pdf = export_pdf(doc, tmp_path / 'out.pdf')
    editor = build_document(doc)  # the GUI preview/print path paints the editor document's resolution
    count = resolved_page_count(editor, doc)
    image = QImage(612, 792, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    for page in range(count):
        draw_page(painter, editor, doc, page)
    painter.end()
    assert png['layout_signature'] == pdf['layout_signature'] == layout_signature(editor.byax_resolved)
    assert png['page_count'] == pdf['page_count'] == count
