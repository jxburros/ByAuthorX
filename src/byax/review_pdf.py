"""PDF review copies and the import of sticky notes and highlights a reader added to them.

``build_pdf_copy`` is the ordinary controlled PDF export of a snapshot with a review footer that carries the
review identifier ("Review copy rv_… · Page n of m"); the identifier is also the PDF's ``/Creator`` so the
returned file names its package even when a reader's tool rewrites the page content.

``read_pdf_annotations`` reads the annotations of a returned PDF with pypdf: sticky notes (``Text``), free
text, and highlight, underline, strike-out and squiggly marks with their ``/Contents``, author (``/T``),
position (``/Rect``, ``/QuadPoints``) and reply links (``/IRT``). ``locate_annotations`` maps each one to a
block and, for text markup, to a character range through the page map of the sent layout (the lines of every
block with their rectangles), so a comment lands on the lines the reader marked. When the laid-out text of a
block is not the block text (a footnote mark, say), the comment keeps the block and loses the range, and that
is reported. Annotation kinds that carry no text (ink, stamps, shapes) are reported as located losses.

The returned PDF is untrusted data: sizes and counts are bounded, text is cleaned, and nothing in it selects
behaviour.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from .docx_review import _clean, _loss, snapshot_hash
from .errors import ByaxError
from .layout import _scalar as layout_scalar

PDF_LIMIT = 64 * 1024 * 1024
MAX_PAGES = 5000
MAX_ANNOTATIONS = 5000
TEXT_LIMIT = 10000
_ID = re.compile(r'rv_[0-9a-f]{16}')
_MARKUP = {
    '/Highlight': 'highlighted',
    '/Underline': 'underlined',
    '/StrikeOut': 'struck through',
    '/Squiggly': 'marked',
}
_NOTE = {'/Text', '/FreeText', '/Caret'}
_SILENT = {'/Link', '/Popup', '/Widget', '/Screen', '/TrapNet', '/PrinterMark', '/Watermark'}
_OVERLAP = 0.4


def pdf_projection(snapshot, review_id):
    """``(candidate, losses, units)`` for a PDF review copy: the exported blocks with the review footer."""
    from .exchange import _exchange_losses
    from .formats import output_document
    from .review_html import block_units

    candidate, policy = output_document(snapshot, 'pdf')
    losses = [
        _loss(
            'review_block_not_sent',
            'Excluded from the review copy by the authored output policy; it stays in your document.',
            entry.get('target'),
        )
        for entry in policy
    ]
    layout = dict(candidate.get('layout', {}))
    layout['footer'] = {'left': f'Review copy {review_id}', 'right': 'Page {page} of {pages}'}
    candidate['layout'] = layout
    exchange_losses: list[dict] = []
    _exchange_losses(candidate, 'pdf', exchange_losses)
    losses.extend(entry for entry in exchange_losses if entry['code'] != 'review_not_exported')
    review = snapshot.get('review', {})
    if any(c.get('status') != 'resolved' for c in review.get('comments', [])):
        losses.append(
            _loss(
                'review_comments_not_shown',
                'Your open comments are not printed in the PDF copy; readers see the text only.',
                'review',
            )
        )
    if any(p.get('status') == 'pending' for p in review.get('proposals', [])):
        losses.append(
            _loss(
                'review_proposals_not_sent', 'Pending proposals stay in ByAuthorX and are not sent.', 'review'
            )
        )
    candidate['review'] = {'comments': [], 'proposals': [], 'tracked_changes': [], 'findings': []}
    return candidate, losses, block_units(candidate['blocks'])


def build_pdf_copy(snapshot, *, review_id) -> tuple[bytes, dict[str, Any]]:
    """PDF bytes of the review copy and ``{losses, units, snapshot_hash, sha256, pages}``.

    The layout is reproducible; the file is not byte for byte, because Qt stamps its creation time.
    """
    from .layout import ensure_app, export_pdf

    ensure_app()
    candidate, losses, units = pdf_projection(snapshot, review_id)
    with tempfile.TemporaryDirectory(prefix='.byax-pdf-') as temporary:
        stage = Path(temporary) / 'review.pdf'
        report = export_pdf(candidate, stage, creator=f'ByAuthorX review {review_id}')
        losses.extend(report.get('losses', []))
        data = stage.read_bytes()
    return data, {
        'losses': losses,
        'units': units,
        'snapshot_hash': snapshot_hash(units),
        'sha256': hashlib.sha256(data).hexdigest(),
        'pages': report.get('pages'),
    }


def _text(value, limit=TEXT_LIMIT):
    if value is None:
        return ''
    text = _clean(str(value)).replace('\x00', '').replace('\r\n', '\n').replace('\r', '\n')
    text = ''.join(ch for ch in text if ch in '\n\t' or ch >= ' ' and not 0x7F <= ord(ch) < 0xA0)
    return text.encode('utf-8', 'replace').decode('utf-8')[:limit]


def _number(values, count):
    out = [float(v) for v in values]
    if len(out) < count or any(math.isnan(v) or abs(v) > 1e7 for v in out):
        raise ValueError('bad geometry')
    return out


def read_pdf_annotations(raw: bytes) -> dict[str, Any]:
    """Annotations of a returned PDF: ``{review_id, pages, annotations, losses}``. Bounded, no side effects."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    if len(raw) > PDF_LIMIT:
        raise ByaxError('resource_limit', 'Annotated PDF exceeds the 64 MiB limit')
    if not raw.startswith(b'%PDF'):
        raise ByaxError('invalid_input', 'The reader’s file is not a PDF')
    losses: list[dict[str, Any]] = []
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(''):
            raise ByaxError('invalid_input', 'The annotated PDF is password protected')
        creator = _text((reader.metadata or {}).get('/Creator'), 200)
        count = len(reader.pages)
        if count > MAX_PAGES:
            raise ByaxError('resource_limit', f'The annotated PDF has more than {MAX_PAGES} pages')
        found = _ID.search(creator)
        review_id = found.group(0) if found else None
        annotations: list[dict[str, Any]] = []
        for number, page in enumerate(reader.pages, start=1):
            if review_id is None and number == 1:
                try:
                    # Shaping can turn the identifier's 'ff' into one ligature glyph (U+FB00), so fold it back first.
                    found = _ID.search(unicodedata.normalize('NFKC', page.extract_text() or ''))
                except Exception:  # noqa: BLE001 - text extraction of untrusted PDFs may fail in many ways
                    found = None
                review_id = found.group(0) if found else None
            entries = page.get('/Annots')
            if entries is None:
                continue
            box = page.mediabox
            height, bottom = float(box.height), float(box.bottom)
            for reference in entries.get_object() or []:
                if len(annotations) >= MAX_ANNOTATIONS:
                    losses.append(
                        _loss(
                            'pdf_annotation_limit', f'Only the first {MAX_ANNOTATIONS} annotations were read.'
                        )
                    )
                    break
                try:
                    node = reference.get_object()
                    subtype = str(node.get('/Subtype'))
                    if subtype in _SILENT:
                        continue
                    contents = _text(node.get('/Contents'))
                    if subtype not in _NOTE and subtype not in _MARKUP:
                        if contents.strip():
                            losses.append(
                                _loss(
                                    'pdf_annotation_unsupported',
                                    f'A {subtype.lstrip("/")} annotation on page {number} was not imported; '
                                    'only notes and text markup are.',
                                    None,
                                    page=number,
                                )
                            )
                        continue
                    rect = _number(node.get('/Rect') or [], 4)
                    boxes = [rect]
                    quads = node.get('/QuadPoints')
                    if quads and subtype in _MARKUP:
                        points = _number(quads, 8)
                        boxes = []
                        for i in range(0, len(points) - len(points) % 8, 8):
                            xs, ys = points[i : i + 8 : 2], points[i + 1 : i + 8 : 2]
                            boxes.append([min(xs), min(ys), max(xs), max(ys)])
                        rect = [
                            min(b[0] for b in boxes),
                            min(b[1] for b in boxes),
                            max(b[2] for b in boxes),
                            max(b[3] for b in boxes),
                        ]
                    parent = node.get('/IRT')
                    annotations.append(
                        {
                            'object': getattr(reference, 'idnum', len(annotations) + 1),
                            'page': number,
                            'subtype': subtype,
                            'contents': contents,
                            'author': _text(node.get('/T'), 80),
                            'created': _text(node.get('/CreationDate') or node.get('/M'), 40),
                            'rect': [rect[0], rect[1] - bottom, rect[2], rect[3] - bottom],
                            'boxes': [[b[0], b[1] - bottom, b[2], b[3] - bottom] for b in boxes],
                            'page_height': height,
                            'parent': getattr(parent, 'idnum', None),
                            'markup': subtype in _MARKUP,
                        }
                    )
                except (ValueError, TypeError, KeyError, AttributeError, PyPdfError):
                    losses.append(
                        _loss(
                            'pdf_annotation_unreadable',
                            f'An annotation on page {number} could not be read and was not imported.',
                            None,
                            page=number,
                        )
                    )
    except ByaxError:
        raise
    except (PyPdfError, ValueError, TypeError, KeyError, RecursionError, OSError) as exc:
        raise ByaxError('invalid_input', f'The annotated PDF could not be read: {str(exc)[:120]}') from exc
    return {'review_id': review_id, 'pages': count, 'annotations': annotations, 'losses': losses}


def _page_lines(candidate, pages):
    """Rectangles of the laid-out lines on ``pages``: ``(lines, keepalive)``.

    Each line is ``{page, block, left, top, right, bottom, start, end, line, base, text}``; offsets are
    Unicode scalar offsets in the block text when the laid-out text is the block text.
    """
    from . import layout

    qdoc = layout.build_document(candidate)
    resolved = layout.resolve_layout(qdoc, candidate)
    offset = layout.print_marks(candidate.get('layout', {}))['offset']
    texts = {}
    for block in candidate['blocks']:
        texts[block['id']] = block.get('text', '')
        for row in block.get('rows', []):
            for cell in row.get('cells', []):
                texts[cell['id']] = cell.get('text', '')
    lines = []
    for number, page in enumerate(resolved['pages'], start=1):
        if number not in pages:
            continue
        for fragment in page['fragments']:
            part = fragment.get('document')
            if part is None or fragment['kind'] in ('table_header', 'label'):
                continue
            source, rect = fragment['source'], fragment['rect']
            for ident, position in part.byax_positions.items():
                block = part.findBlock(position)
                top = part.documentLayout().blockBoundingRect(block).top()
                left = part.documentLayout().blockBoundingRect(block).left()
                text = block.text().replace('\u2028', '\n')
                exact = text == texts.get(ident)
                layout_ = block.layout()
                for index in range(layout_.lineCount()):
                    line = layout_.lineAt(index)
                    y = top + line.y()
                    if y < source.top() - 0.01 or y + line.height() > source.bottom() + 0.01:
                        continue
                    # QTextLine counts UTF-16 units; the model counts scalar values.
                    start = layout._scalar(block.text(), line.textStart())
                    end = layout._scalar(block.text(), line.textStart() + line.textLength())
                    lines.append(
                        {
                            'page': number,
                            'block': ident,
                            # The extent of the text itself, so cells of one table row stay apart.
                            'left': rect.x() - source.x() + left + offset + line.x(),
                            'right': rect.x()
                            - source.x()
                            + left
                            + offset
                            + line.x()
                            + line.naturalTextWidth(),
                            'top': rect.y() + y - source.top() + offset,
                            'bottom': rect.y() + y - source.top() + line.height() + offset,
                            'start': start if exact else None,
                            'end': end if exact else None,
                            'line': line,
                            'shift': rect.x() - source.x() + left + offset,
                            'raw': block.text(),
                        }
                    )
    # The lines point into the laid-out documents, which must outlive them.
    return lines, (qdoc, resolved)


def locate_annotations(snapshot, review_id, annotations, losses) -> list[dict[str, Any]]:
    """Comment dicts (``id, block, kind, text, quote, prefix, suffix, position, author, created, parent``).

    Appends located losses for annotations that could not be placed more precisely than a block.
    """
    from .quote_match import selectors

    candidate, _, _ = pdf_projection(snapshot, review_id)
    pages = {a['page'] for a in annotations}
    lines, _alive = _page_lines(candidate, pages) if pages else ([], None)
    texts = {}
    for block in candidate['blocks']:
        texts[block['id']] = block.get('text', '')
        for row in block.get('rows', []):
            for cell in row.get('cells', []):
                texts[cell['id']] = cell.get('text', '')
    comments = []
    for note in annotations:
        height = note['page_height']
        x0, y0, x1, y1 = note['rect']
        top, bottom = height - y1, height - y0
        page_lines = [ln for ln in lines if ln['page'] == note['page']]
        if not page_lines:
            losses.append(
                _loss(
                    'pdf_comment_unplaced',
                    f'An annotation on page {note["page"]} is outside the sent layout; kept as a document comment.',
                    None,
                    page=note['page'],
                )
            )
            comments.append(_base(note, None))
            continue
        if note['markup']:
            hit = [
                ln
                for ln in page_lines
                if min(bottom, ln['bottom']) - max(top, ln['top']) > _OVERLAP * (ln['bottom'] - ln['top'])
                and min(x1, ln['right']) > max(x0, ln['left'])
            ]
        else:
            hit = []
        if hit:
            block = hit[0]['block']
            hit = [ln for ln in hit if ln['block'] == block]
            comment = _base(note, block)
            if all(ln['start'] is not None for ln in hit):
                text = texts.get(block, '')
                start = _edge(hit[0], note['boxes'], height, left=True)
                end = _edge(hit[-1], note['boxes'], height, left=False)
                start, end = max(0, min(start, len(text))), max(0, min(end, len(text)))
                if end > start and text[start:end].strip():
                    comment.update(selectors(text, (start, end)))
                    if not comment['text']:
                        comment['text'] = f'({_MARKUP[note["subtype"]]} passage)'
            else:
                losses.append(
                    _loss(
                        'pdf_comment_paragraph_only',
                        'The laid-out text of this paragraph differs from its text; the markup is kept on the paragraph.',
                        block,
                        page=note['page'],
                    )
                )
            comments.append(comment)
            continue
        # A note or free text: the nearest line by distance from the note's top-left corner.
        centre = (top + bottom) / 2
        nearest = min(
            page_lines,
            key=lambda ln: (
                max(ln['top'] - centre, centre - ln['bottom'], 0),
                max(ln['left'] - x0, x0 - ln['right'], 0),
            ),
        )
        comments.append(_base(note, nearest['block']))
    return comments


def _edge(entry, boxes, height, *, left):
    """Scalar offset in the block where the marked text begins (``left``) or ends on this line."""
    spans = [
        b
        for b in boxes
        if min(height - b[1], entry['bottom']) - max(height - b[3], entry['top'])
        > _OVERLAP * (entry['bottom'] - entry['top'])
    ]
    if not spans:
        return entry['start'] if left else entry['end']
    x = min(b[0] for b in spans) if left else max(b[2] for b in spans)
    line = entry['line']
    index = layout_scalar(entry['raw'], line.xToCursor(x - entry['shift']))
    return max(entry['start'], min(index, entry['end']))


def _base(note, block):
    text = note['contents'].strip() or (f'({_MARKUP[note["subtype"]]} passage)' if note['markup'] else '')
    return {
        'id': f'pdf{note["object"]}',
        'object': note['object'],
        'block': block,
        'kind': 'comment',
        'text': text,
        'author': note['author'],
        'created': note['created'],
        'parent': f'pdf{note["parent"]}' if note.get('parent') else None,
        'page': note['page'],
    }
