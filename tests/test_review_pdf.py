"""PDF review copies: the review footer and identity, and the import of sticky notes and highlights.

The page map is checked against positions read back from the PDF itself with pypdf (the baseline of each
text line), not against the layout code that produced it.
"""

import io
import unicodedata

import pytest
from docx_word import sample_document
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Highlight, Text
from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject

from byax import beta_readers, engine, review_pdf
from byax.errors import ByaxError

PAGE_TOP = 720  # the first line's baseline offsets come from the content origin at (72, 720)


@pytest.fixture(scope='module')
def sent():
    doc = sample_document()
    data, operations, info = beta_readers.prepare_beta_copy(doc, 'pdf')
    doc, _ = engine.apply(doc, {'operations': operations})
    return doc, info['review_id'], data


def baselines(data):
    """``{first words: baseline y}`` of every text run on page 1, as the PDF itself records them."""
    found = {}

    def visit(text, cm, tm, font_dict, font_size):
        if text.strip() and cm[3] < 0:
            found.setdefault(text.split('\t')[0], tm[5] if cm[5] > 700 else None)

    PdfReader(io.BytesIO(data)).pages[0].extract_text(visitor_text=visit)
    return {k: v for k, v in found.items() if v is not None}


def annotate(data, notes, *, creator=None):
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)
    added = []
    for note in notes:
        kind = note['kind']
        if kind == 'highlight':
            rect = note['rect']
            quads = [rect[0], rect[3], rect[2], rect[3], rect[0], rect[1], rect[2], rect[1]]
            annotation = Highlight(
                rect=rect, quad_points=ArrayObject([FloatObject(v) for v in quads]), highlight_color='ffff00'
            )
        else:
            annotation = Text(rect=note['rect'], text=note.get('text', ''), open=False)
        done = writer.add_annotation(0, annotation)
        if note.get('text') is not None:
            done[NameObject('/Contents')] = TextStringObject(note['text'])
        if note.get('author'):
            done[NameObject('/T')] = TextStringObject(note['author'])
        if note.get('reply_to') is not None:
            done[NameObject('/IRT')] = added[note['reply_to']].indirect_reference
        added.append(done)
    if creator is not None:
        writer.add_metadata({'/Creator': creator})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_the_pdf_copy_has_the_review_footer_and_names_its_package(sent):
    doc, review_id, data = sent
    reader = PdfReader(io.BytesIO(data))
    assert review_id in reader.metadata.creator and reader.metadata.title == 'The Lighthouse'
    text = unicodedata.normalize('NFKC', reader.pages[0].extract_text())
    assert f'Review copy {review_id}' in text.replace('\t', ' ')
    assert 'Page' in text and 'The' in text and 'Chapter' in text
    record = doc['metadata']['review_packages'][review_id]
    assert (
        record['format'] == 'pdf'
        and doc['history']['peer_bases']['review:' + review_id] == record['revision']
    )


def test_highlights_and_notes_become_comments_on_the_lines_they_mark(sent, tmp_path):
    doc, _review_id, data = sent
    lines = baselines(data)
    below, kept = lines['Below,'], lines['She']  # the second paragraph, then the third
    highlight = (72, PAGE_TOP - below - 3, 72 + 120, PAGE_TOP - below + 9)
    note = (330, PAGE_TOP - kept - 5, 348, PAGE_TOP - kept + 10)
    path = tmp_path / 'annotated.pdf'
    path.write_bytes(
        annotate(
            data,
            [
                {'kind': 'highlight', 'rect': highlight, 'text': 'Love this opening', 'author': 'Pat'},
                {'kind': 'note', 'rect': note, 'text': 'Check the years', 'author': 'Pat'},
            ],
        )
    )
    operations, report = beta_readers.plan_comment_merge(doc, [path])
    comments = [op for op in operations if op['type'] == 'add_comment']
    assert [(c['target'], c['text'], c['author'], c['source']) for c in comments] == [
        ('p_2', 'Love this opening', 'Pat', 'pdf_review'),
        ('p_3', 'Check the years', 'Pat', 'pdf_review'),
    ]
    first = comments[0]
    paragraph = next(b for b in doc['blocks'] if b['id'] == 'p_2')['text']
    assert first['start'] == 0 and 8 <= first['end'] < len(
        paragraph
    )  # the left part of the line, not all of it
    assert 'start' not in comments[1]  # a sticky note marks the paragraph, not a range
    assert report['losses'] == [] and report['comment_merge']['files'][0]['reader'] == 'Pat'
    updated, _ = beta_readers.merge_comments(doc, [path])
    assert [b['text'] for b in updated['blocks']] == [b['text'] for b in doc['blocks']]
    _, result = beta_readers.merge_comments(updated, [path])
    assert result['comment_merge']['files'][0]['status'] == 'already_merged'


def test_a_highlight_to_the_end_of_the_line_runs_to_the_end_of_the_paragraph(sent, tmp_path):
    doc, _review_id, data = sent
    kept = baselines(data)['Tonight']
    path = tmp_path / 'a.pdf'
    path.write_bytes(
        annotate(
            data,
            [
                {
                    'kind': 'highlight',
                    'rect': (200, PAGE_TOP - kept - 3, 540, PAGE_TOP - kept + 9),
                    'text': 'x',
                }
            ],
        )
    )
    operations, _ = beta_readers.plan_comment_merge(doc, [path])
    comment = next(op for op in operations if op['type'] == 'add_comment')
    text = next(b for b in doc['blocks'] if b['id'] == comment['target'])['text']
    assert text.startswith('Tonight') and comment['end'] == len(text) and 0 < comment['start'] < len(text)
    assert comment['author'] == 'PDF reader'


def test_replies_markup_without_text_and_unsupported_annotations(sent, tmp_path):
    doc, _review_id, data = sent
    below = baselines(data)['Below,']
    rect = (72, PAGE_TOP - below - 3, 200, PAGE_TOP - below + 9)
    path = tmp_path / 'r.pdf'
    path.write_bytes(
        annotate(
            data,
            [
                {'kind': 'note', 'rect': rect, 'text': 'Root question', 'author': 'Pat'},
                {'kind': 'note', 'rect': rect, 'text': 'My answer', 'author': 'Quinn', 'reply_to': 0},
                {'kind': 'highlight', 'rect': rect, 'author': 'Pat'},
            ],
        )
    )
    updated, report = beta_readers.merge_comments(doc, [path])
    mine = [c for c in updated['review']['comments'] if c.get('source') == 'pdf_review']
    root = next(c for c in mine if c['text'] == 'Root question')
    assert [(r['text'], r['author']) for r in root['replies']] == [('My answer', 'Quinn')]
    assert any(c['text'] == '(highlighted passage)' for c in mine)
    assert report['comments']['replies'] == 1


def test_the_package_is_found_by_the_footer_when_the_creator_was_rewritten(sent, tmp_path):
    doc, review_id, data = sent
    below = baselines(data)['Below,']
    notes = [{'kind': 'note', 'rect': (72, PAGE_TOP - below, 90, PAGE_TOP - below + 12), 'text': 'Hi'}]
    path = tmp_path / 'p.pdf'
    path.write_bytes(annotate(data, notes, creator='Preview'))
    assert review_pdf.read_pdf_annotations(path.read_bytes())['review_id'] == review_id
    updated, _ = beta_readers.merge_comments(doc, [path])
    assert any(c.get('source') == 'pdf_review' for c in updated['review']['comments'])


def test_files_that_are_not_usable_pdfs_are_refused_without_a_crash(sent, tmp_path):
    doc, _review_id, data = sent
    for name, raw, match in (
        ('empty.pdf', b'', 'not a PDF'),
        ('html.pdf', b'<html></html>', 'not a PDF'),
        ('truncated.pdf', data[: len(data) // 3], 'could not be read'),
        ('garbage.pdf', b'%PDF-1.7\n' + bytes(range(256)) * 20, 'could not be read'),
    ):
        path = tmp_path / name
        path.write_bytes(raw)
        with pytest.raises(ByaxError, match=match):
            beta_readers.merge_comments(doc, [path])


def test_a_pdf_of_another_package_or_format_is_refused(sent, tmp_path):
    doc, _review_id, data = sent
    html = sample_document()
    _, operations, info = beta_readers.prepare_beta_copy(html, 'html')
    html, _ = engine.apply(html, {'operations': operations})
    path = tmp_path / 'p.pdf'
    path.write_bytes(
        annotate(
            data,
            [{'kind': 'note', 'rect': (72, 600, 90, 612), 'text': 'x'}],
            creator='ByAuthorX review ' + info['review_id'],
        )
    )
    with pytest.raises(ByaxError, match='HTML copy; this file is a PDF'):
        beta_readers.merge_comments(html, [path], review_id=info['review_id'])
    with pytest.raises(ByaxError, match='different review package'):
        beta_readers.merge_comments(doc, [path], review_id='rv_0000000000000000')


def test_a_highlight_in_a_table_cell_lands_in_that_cell_not_its_neighbour(sent, tmp_path):
    doc, _review_id, data = sent
    row = baselines(data)['Lamp']
    path = tmp_path / 'cell.pdf'
    # The second column starts 62 pt into the text area (the first column holds "12 March").
    rect = (72 + 63, PAGE_TOP - row - 3, 72 + 63 + 40, PAGE_TOP - row + 9)
    notes = [{'kind': 'highlight', 'rect': rect, 'text': 'About the lamp', 'author': 'Pat'}]
    path.write_bytes(annotate(data, notes))
    operations, report = beta_readers.plan_comment_merge(doc, [path])
    comment = next(op for op in operations if op['type'] == 'add_comment')
    table = next(b for b in doc['blocks'] if b['type'] == 'table')
    cells = {c['id']: c['text'] for r in table['rows'] for c in r['cells']}
    assert cells[comment['target']] == 'Lamp cleaned'
    assert comment['start'] == 0 and 4 <= comment['end'] < len('Lamp cleaned') and report['losses'] == []
