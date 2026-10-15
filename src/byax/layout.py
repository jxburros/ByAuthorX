"""Shared Qt text shaping, pagination and controlled output in physical points.

Qt supplies Unicode shaping and semantic flowing tables. Features Qt cannot
faithfully resolve are disclosed as located findings, never called validated.
"""

from __future__ import annotations

import base64
import contextvars
import copy
import difflib
import hashlib
import itertools
import math
import os
import threading
import unicodedata
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QImage,
    QImageReader,
    QPageLayout,
    QPageSize,
    QPainter,
    QPalette,
    QPdfWriter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextFrameFormat,
    QTextImageFormat,
    QTextLength,
    QTextListFormat,
    QTextOption,
    QTextTable,
    QTextTableFormat,
)
from PySide6.QtWidgets import QApplication

from .errors import ByaxError

MAX_IMAGE_PIXELS = 40_000_000
MAX_PAGE_POINTS = 14_400
FLOW_HEIGHT = 10_000_000.0
EPSILON = 0.05

BLOCK_ID_PROPERTY = int(QTextFormat.Property.UserProperty) + 1
# Editing-view decorations: with hand-drawn marks on, authored underline and strikethrough are stored as
# these properties and drawn by the editor as pen strokes. Pages, print and exports never set them.
INK_UNDERLINE_PROPERTY = int(QTextFormat.Property.UserProperty) + 11
INK_STRIKE_PROPERTY = int(QTextFormat.Property.UserProperty) + 12
_DISPLAY = threading.local()


@contextmanager
def hand_drawn_marks(enabled=True):
    """Build editor documents with pen-stroke decorations; thread-local, so exports are never affected."""
    previous = getattr(_DISPLAY, 'hand', False)
    _DISPLAY.hand = bool(enabled)
    try:
        yield
    finally:
        _DISPLAY.hand = previous


def _decoration(fmt, kind, on):
    if getattr(_DISPLAY, 'hand', False):
        fmt.setProperty(INK_UNDERLINE_PROPERTY if kind == 'underline' else INK_STRIKE_PROPERTY, on)
    elif kind == 'underline':
        fmt.setFontUnderline(on)
    else:
        fmt.setFontStrikeOut(on)


_APP = None


def ensure_app():
    global _APP
    if QApplication.instance() is None:
        if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        _APP = QApplication([])
    from .fonts import register_bundled_fonts

    register_bundled_fonts()
    return QApplication.instance()


def _finding(code, message, target=None):
    return {'code': code, 'severity': 'warning', 'message': message, 'target': target}


def geometry(doc):
    layout = doc.get('layout', {})
    paper = layout.get('paper', 'Letter')
    width, height = (595.276, 841.89) if paper == 'A4' else (612, 792)
    width, height = float(layout.get('width', width)), float(layout.get('height', height))
    if layout.get('orientation') == 'landscape' and width < height:
        width, height = height, width
    margins = {
        key: float(layout.get('margins', {}).get(key, 72)) for key in ('top', 'right', 'bottom', 'left')
    }
    if not all(math.isfinite(v) and v >= 0 for v in [width, height, *margins.values()]):
        raise ByaxError('invalid_input', 'Page geometry must contain finite positive dimensions')
    if width > MAX_PAGE_POINTS or height > MAX_PAGE_POINTS:
        raise ByaxError('resource_limit', 'Page dimensions exceed 14,400 physical points (200 inches)')
    if width <= margins['left'] + margins['right'] or height <= margins['top'] + margins['bottom']:
        raise ByaxError('invalid_input', 'Page margins leave no body area')
    return width, height, margins


def _style(doc, name, seen=None):
    seen = set() if seen is None else seen
    if name in seen:
        return {}
    seen.add(name)
    own = doc.get('styles', {}).get(name, {})
    if not isinstance(own, dict):
        return {}
    result = _style(doc, own['based_on'], seen) if own.get('based_on') else {}
    result.update(own)
    return result


def _char_format(values):
    fmt = QTextCharFormat()
    for key, value in values.items():
        if key in ('bold', 'strong'):
            fmt.setFontWeight(QFont.Weight.Bold if value else QFont.Weight.Normal)
        elif key in ('italic', 'emphasis'):
            fmt.setFontItalic(bool(value))
        elif key == 'underline':
            _decoration(fmt, 'underline', bool(value))
        elif key in ('strikethrough', 'strike'):
            _decoration(fmt, 'strike', bool(value))
        elif key in ('font', 'font_family'):
            fmt.setFontFamilies([str(value)])
        elif key in ('font_size', 'size'):
            fmt.setFontPointSize(float(value))
        elif key in ('color', 'foreground'):
            fmt.setForeground(QColor(value))
        elif key in ('highlight', 'background'):
            fmt.setBackground(QColor(value))
        elif key == 'superscript' and value:
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)
        elif key == 'subscript' and value:
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
        elif key in ('link', 'href'):
            if _safe_link(str(value)):
                fmt.setAnchor(True)
                fmt.setAnchorHref(str(value))
                fmt.setForeground(QColor('#3156a0'))
                fmt.setFontUnderline(True)
        elif key == 'code' and value:
            fmt.setFontFamilies(['DejaVu Sans Mono'])
            fmt.setBackground(QColor('#eef0f2'))
    return fmt


def _safe_link(value):
    return value.startswith(('https://', 'http://', 'mailto:', '#')) or ':' not in value


def _insert_marked(cursor, text, marks, base=None, doc=None):
    start = cursor.position()
    cursor.insertText(text.replace('\n', '\u2028'), base or QTextCharFormat())
    # Named character styles precede direct overrides regardless of mark order.
    for mark in sorted(marks, key=lambda item: item.get('type') != 'character_style'):
        lo, hi = max(0, int(mark.get('start', 0))), min(len(text), int(mark.get('end', len(text))))
        if hi <= lo:
            continue
        selection = QTextCursor(cursor.document())
        # Semantic offsets are Unicode scalar values; Qt counts UTF-16 code units.
        selection.setPosition(start + len(text[:lo].encode('utf-16-le')) // 2)
        selection.setPosition(
            start + len(text[:hi].encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor
        )
        values = (
            _style(doc, mark['value'])
            if doc is not None and mark.get('type') == 'character_style'
            else {mark.get('type'): mark.get('value', True)}
        )
        selection.mergeCharFormat(_char_format(values))


_IMAGE_CACHE = OrderedDict()
_IMAGE_CACHE_LOCK = threading.Lock()
_IMAGE_CACHE_BYTES = 64 * 1024 * 1024
_GLYPH_CACHE = contextvars.ContextVar('byax_glyph_cache', default=None)


def _decode_image(raw):
    # Hash the actual bytes: callers may supply an unvalidated/stale asset key.
    key = hashlib.sha256(raw).digest()
    with _IMAGE_CACHE_LOCK:
        cached = _IMAGE_CACHE.get(key)
        if cached is not None:
            _IMAGE_CACHE.move_to_end(key)
            return QImage(cached)
    image = _read_image(raw)
    if not image.isNull() and image.sizeInBytes() <= _IMAGE_CACHE_BYTES:
        with _IMAGE_CACHE_LOCK:
            _IMAGE_CACHE[key] = QImage(image)
            while (
                len(_IMAGE_CACHE) > 128
                or sum(item.sizeInBytes() for item in _IMAGE_CACHE.values()) > _IMAGE_CACHE_BYTES
            ):
                _IMAGE_CACHE.popitem(last=False)
    return image


def _read_image(raw):
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0:
        return QImage()
    if size.width() * size.height() > MAX_IMAGE_PIXELS:
        raise ByaxError('resource_limit', 'Decoded image exceeds 40 million pixels')
    if bytes(reader.format()).lower() not in {
        b'png',
        b'jpg',
        b'jpeg',
        b'gif',
        b'bmp',
        b'webp',
        b'ico',
        b'tiff',
    }:
        return QImage()
    reader.setAllocationLimit(160)
    return reader.read()


def _check_glyphs(qdoc, block=None, stop=None):
    checked = _GLYPH_CACHE.get()
    if checked is None:
        checked = {}
    block = qdoc.firstBlock() if block is None else block
    while block.isValid() and block != stop:
        missing = set()
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            if fragment.isValid():
                font = fragment.charFormat().font().resolve(qdoc.defaultFont())
                metrics = QFontMetrics(font)
                for character in set(fragment.text()):
                    if unicodedata.category(character) in {'Cc', 'Cf', 'Zl', 'Zp'} or character == '\ufffc':
                        continue
                    key = (font.toString(), ord(character))
                    if key not in checked:
                        checked[key] = metrics.inFontUcs4(ord(character))
                    if not checked[key]:
                        missing.add(f'U+{ord(character):04X}')
            fragments += 1
        if missing:
            target = block.blockFormat().property(BLOCK_ID_PROPERTY)
            qdoc.byax_findings.append(
                _finding(
                    'missing_glyph',
                    'Resolved font fallback cannot draw ' + ', '.join(sorted(missing)) + '.',
                    target,
                )
            )
        block = block.next()


_ALIGNMENTS = {
    'left': Qt.AlignmentFlag.AlignLeft,
    'right': Qt.AlignmentFlag.AlignRight,
    'center': Qt.AlignmentFlag.AlignHCenter,
    'justify': Qt.AlignmentFlag.AlignJustify,
}
# Top-level kinds whose rendering depends only on the block itself, its style and
# the document geometry; edits confined to them can update a document in place.
INCREMENTAL_KINDS = {'paragraph', 'heading', 'footnote', 'page_break', 'section_break'}


def _block_format(result, doc, block, flow=None):
    attrs = block.get('attrs', {})
    kind, ident = block.get('type', 'paragraph'), block.get('id')
    bf = QTextBlockFormat()
    bf.setProperty(BLOCK_ID_PROPERTY, ident)
    values = _style(doc, block.get('style', 'body'))
    values.update(attrs)
    if kind == 'heading':
        level = max(1, min(6, int(attrs.get('level', 1))))
        bf.setHeadingLevel(level)
        bf.setTopMargin(12)
        bf.setBottomMargin(6)
        values = {'bold': True, 'font_size': [24, 19, 16, 14, 12, 12][level - 1], **values}
    else:
        bf.setBottomMargin(float(values.get('space_after', 6)))
    bf.setTopMargin(float(values.get('space_before', bf.topMargin())))
    bf.setAlignment(_ALIGNMENTS.get(values.get('alignment', 'left'), Qt.AlignmentFlag.AlignLeft))
    bf.setLeftMargin(float(values.get('indent', 0)))
    bf.setTextIndent(float(values.get('first_line_indent', 0)))
    if values.get('line_spacing'):
        bf.setLineHeight(
            float(values['line_spacing']) * 100, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        )
    if values.get('tabs'):
        tabs = []
        for position in values['tabs']:
            tab = QTextOption.Tab()
            tab.position = float(position)
            tabs.append(tab)
        bf.setTabPositions(tabs)
    if flow is None and (
        kind in ('page_break', 'section_break')
        or attrs.get('page_break_before')
        or attrs.get('column_break_before')
    ):
        bf.setPageBreakPolicy(QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore)
        if kind == 'section_break' and any(k in attrs for k in ('layout', 'columns', 'width', 'height')):
            result.byax_findings.append(
                _finding(
                    'section_geometry',
                    'This renderer uses document page geometry; section-specific geometry/columns require a different layout backend.',
                    ident,
                )
            )
    return bf, values


def _font_finding(result, values, ident, families):
    font = values.get('font_family', values.get('font'))
    if font and font not in families:
        result.byax_findings.append(
            _finding(
                'font_substitution',
                f'Requested font {font!r} is unavailable; Qt substitutes a local font.',
                ident,
            )
        )


def _emit_text(result, cursor, block, values, list_objects):
    kind, ident = block.get('type', 'paragraph'), block.get('id')
    if kind == 'footnote':
        result.byax_findings.append(
            _finding(
                'footnote_placement',
                'Footnote text is retained in document flow; bottom-of-page placement and continuation are not resolved.',
                ident,
            )
        )
        values['font_size'] = 10
    if values.get('list'):
        listing = values['list']
        list_kind = listing.get('kind', 'bullet') if isinstance(listing, dict) else listing
        level = int(listing.get('level', 1) if isinstance(listing, dict) else values.get('level', 1))
        lf = QTextListFormat()
        lf.setStyle(
            QTextListFormat.Style.ListDecimal
            if list_kind in ('ordered', 'numbered', 'decimal')
            else QTextListFormat.Style.ListDisc
        )
        lf.setIndent(max(1, level))
        resolved = getattr(result, 'byax_lists', {}).get(ident, {})
        list_key = (resolved.get('list_id', list_kind), level, resolved.get('instance', 0))
        if (
            values.get('restart')
            or list_key not in list_objects
            or (resolved and list_kind in ('ordered', 'numbered', 'decimal'))
        ):
            if hasattr(lf, 'setStart'):
                lf.setStart(int(resolved.get('number', values.get('start', 1))))
            list_objects[list_key] = cursor.createList(lf)
        else:
            list_objects[list_key].add(cursor.block())
    _insert_marked(
        cursor,
        block.get('text', ''),
        block.get('marks', []),
        _char_format(values),
        {'styles': getattr(result, 'byax_source_styles', {})},
    )
    if kind not in ('paragraph', 'heading', 'footnote', 'page_break', 'section_break'):
        result.byax_findings.append(
            _finding('unknown_block', f'Unsupported block {kind!r} rendered as text.', ident)
        )


def build_document(doc: dict, _toc_pages=None, _flow=None) -> QTextDocument:
    """Editor/semantic document; ``_flow`` builds an unpaginated column run."""
    ensure_app()
    if _flow is None:
        width, height, margins = geometry(doc)
    else:
        width, height = float(_flow['width']), FLOW_HEIGHT
        margins = {key: 0.0 for key in ('top', 'right', 'bottom', 'left')}
    result = QTextDocument()
    # A fixed physical paint device decouples typography from preview raster DPI.
    device = QImage(1, 1, QImage.Format.Format_ARGB32)
    device.setDotsPerMeterX(2835)
    device.setDotsPerMeterY(2835)
    result._byax_device = device
    result.documentLayout().setPaintDevice(device)
    result.setDefaultFont(QFont('DejaVu Serif', 12))
    result.setPageSize(QSizeF(width, height))
    result.setDocumentMargin(0)
    frame_fmt = result.rootFrame().frameFormat()
    frame_fmt.setTopMargin(margins['top'])
    frame_fmt.setBottomMargin(margins['bottom'])
    frame_fmt.setLeftMargin(margins['left'])
    frame_fmt.setRightMargin(margins['right'])
    result.rootFrame().setFrameFormat(frame_fmt)
    from .formatting import resolve_lists

    result.byax_source_styles = doc.get('styles', {})
    result.byax_lists = {entry['id']: entry for entry in resolve_lists(doc)}
    result.byax_findings = []
    result.byax_positions = {}
    cursor = QTextCursor(result)
    headings = [b for b in doc.get('blocks', []) if b.get('type') == 'heading']
    families = set(QFontDatabase.families())
    loaded_assets = set()
    for key, asset in doc.get('assets', {}).items():
        if not asset.get('mime', '').startswith('image/'):
            continue
        try:
            raw = base64.b64decode(asset.get('data', ''), validate=True)
            picture = _decode_image(raw)
            if not picture.isNull():
                result.addResource(QTextDocument.ResourceType.ImageResource, QUrl('byax:' + key), picture)
                loaded_assets.add(key)
        except (ValueError, TypeError):
            pass
    list_objects = {}
    first = True
    for block in doc.get('blocks', []):
        attrs = block.get('attrs', {})
        if attrs.get('excluded') or attrs.get('redacted'):
            continue
        kind, ident = block.get('type', 'paragraph'), block.get('id')
        if _flow is not None and kind in ('page_break', 'section_break'):
            continue
        if not first:
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        first = False
        bf, values = _block_format(result, doc, block, _flow)
        cursor.setBlockFormat(bf)
        result.byax_positions[ident] = cursor.position()
        _font_finding(result, values, ident, families)
        if kind == 'table':
            rows = block.get('rows', [])
            columns = max((len(row.get('cells', [])) for row in rows), default=0)
            if rows and columns:
                tf = QTextTableFormat()
                tf.setBorder(float(attrs.get('border_width', 1)))
                tf.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                tf.setBorderBrush(QColor(attrs.get('border_color', '#808080')))
                if attrs.get('background'):
                    tf.setBackground(QColor(attrs['background']))
                if attrs.get('column_widths') and len(attrs['column_widths']) == columns:
                    tf.setColumnWidthConstraints(
                        [
                            QTextLength(QTextLength.Type.FixedLength, float(value))
                            for value in attrs['column_widths']
                        ]
                    )
                if attrs.get('width'):
                    tf.setWidth(
                        QTextLength(
                            QTextLength.Type.FixedLength,
                            min(float(attrs['width']), width - margins['left'] - margins['right']),
                        )
                    )
                tf.setAlignment(_ALIGNMENTS.get(attrs.get('alignment', 'left'), Qt.AlignmentFlag.AlignLeft))
                tf.setCellPadding(float(attrs.get('padding', 4)))
                tf.setCellSpacing(0)
                tf.setHeaderRowCount(int(attrs.get('header_rows', 0)))
                table = cursor.insertTable(len(rows), columns, tf)
                for r, row in enumerate(rows):
                    for c, cell in enumerate(row.get('cells', [])):
                        cc = table.cellAt(r, c).firstCursorPosition()
                        cbf = cc.blockFormat()
                        cbf.setProperty(BLOCK_ID_PROPERTY, cell.get('id'))
                        cc.setBlockFormat(cbf)
                        result.byax_positions[cell.get('id')] = cc.position()
                        cell_values = (
                            {**values, **_style(doc, cell['style'])} if cell.get('style') else dict(values)
                        )
                        if r < attrs.get('header_rows', 0):
                            cell_values['bold'] = True
                        cell_values.update(cell.get('attrs', {}))
                        _insert_marked(
                            cc, cell.get('text', ''), cell.get('marks', []), _char_format(cell_values), doc
                        )
                        cell_attrs = cell.get('attrs', {})
                        if cell_attrs.get('background'):
                            cf = table.cellAt(r, c).format()
                            cf.setBackground(QColor(cell_attrs['background']))
                            table.cellAt(r, c).setFormat(cf)
                for r, row in enumerate(rows):
                    for c, cell in enumerate(row.get('cells', [])):
                        ca = cell.get('attrs', {})
                        if ca.get('rowspan', 1) > 1 or ca.get('colspan', 1) > 1:
                            table.mergeCells(
                                r,
                                c,
                                min(int(ca.get('rowspan', 1)), len(rows) - r),
                                min(int(ca.get('colspan', 1)), columns - c),
                            )
                cursor = table.lastCursorPosition()
                cursor.movePosition(QTextCursor.MoveOperation.NextBlock)
            continue
        if kind == 'image':
            key = attrs.get('asset')
            if key in loaded_assets:
                imf = QTextImageFormat()
                imf.setName('byax:' + key)
                image = result.resource(QTextDocument.ResourceType.ImageResource, QUrl('byax:' + key))
                limit = None if _flow is None else _flow.get('max_height')
                iw, ih = _image_size(attrs, image, width - margins['left'] - margins['right'], limit)
                if _flow is not None and (iw, ih) != _image_size(attrs, image, math.inf, None):
                    result.byax_findings.append(
                        _finding(
                            'image_scaled',
                            'Image was scaled to the available column area preserving its aspect ratio.',
                            ident,
                        )
                    )
                if attrs.get('visible') is False:
                    blank = QImage(1, 1, QImage.Format.Format_ARGB32)
                    blank.fill(QColor(0, 0, 0, 0))
                    result.addResource(QTextDocument.ResourceType.ImageResource, QUrl('byax:hidden'), blank)
                    imf.setName('byax:hidden')
                imf.setWidth(iw)
                imf.setHeight(ih)
                imf.setProperty(QTextFormat.Property.ImageAltText, attrs.get('alt', ''))
                if _wrap(attrs) == 'square':
                    ff = QTextFrameFormat()
                    ff.setPosition(
                        QTextFrameFormat.Position.FloatRight
                        if attrs.get('side') == 'right'
                        else QTextFrameFormat.Position.FloatLeft
                    )
                    ff.setWidth(iw)
                    ff.setMargin(float(attrs.get('wrap_margin', 6)))
                    frame = cursor.insertFrame(ff)
                    image_cursor = frame.firstCursorPosition()
                    image_bf = image_cursor.blockFormat()
                    image_bf.setProperty(BLOCK_ID_PROPERTY, ident)
                    image_cursor.setBlockFormat(image_bf)
                    image_cursor.insertImage(imf)
                    cursor.setPosition(frame.lastPosition() + 1)
                else:
                    cursor.insertImage(imf)
                if (
                    iw > width - margins['left'] - margins['right']
                    or ih > height - margins['top'] - margins['bottom']
                ):
                    result.byax_findings.append(
                        _finding('image_overflow', 'Image dimensions exceed the available flow area.', ident)
                    )
            else:
                cursor.insertText('[Image: ' + attrs.get('alt', 'missing asset') + ']')
                result.byax_findings.append(
                    _finding(
                        'missing_image',
                        'Image bytes are unavailable; an alternative-text placeholder is shown.',
                        ident,
                    )
                )
            if _wrap(attrs) not in ('inline', 'square', 'above_below', 'behind_text', 'in_front'):
                result.byax_findings.append(
                    _finding('image_wrap', 'Unknown wrapping policy; image is rendered inline.', ident)
                )
            continue
        if kind == 'toc':
            for i, heading in enumerate(headings):
                if i:
                    cursor.insertBlock()
                page = (_toc_pages or {}).get(heading['id'], '…')
                cursor.insertText(
                    '  ' * (int(heading.get('attrs', {}).get('level', 1)) - 1)
                    + heading.get('text', '')
                    + '    '
                    + str(page)
                )
            continue
        _emit_text(result, cursor, block, values, list_objects)
    if _flow is not None:
        result.documentLayout().documentSize()
        _check_glyphs(result)
        return result
    if doc.get('layout', {}).get('columns', 1) != 1:
        result.byax_findings.append(
            _finding('columns', 'Multi-column flow is not resolved; output uses one column.', 'layout')
        )
    result.documentLayout().documentSize()
    _apply_keep_rules(result, doc)
    _check_glyphs(result)
    if _toc_pages is None and any(b.get('type') == 'toc' for b in doc.get('blocks', [])):
        pages = {h['id']: _block_page(result, result.byax_positions.get(h['id'], 0)) for h in headings}
        # Iterate until references stabilize, with an explicit bounded fallback.
        for _ in range(4):
            rebuilt = build_document(doc, pages)
            new_pages = {
                h['id']: _block_page(rebuilt, rebuilt.byax_positions.get(h['id'], 0)) for h in headings
            }
            if new_pages == pages:
                return rebuilt
            result, pages = rebuilt, new_pages
        result.byax_findings.append(
            _finding(
                'toc_unstable', 'Contents page numbers did not stabilize after four layout passes.', 'toc'
            )
        )
    return result


def _render_inputs(doc):
    return {
        k: v
        for k, v in doc.items()
        if k not in ('blocks', 'review', 'history', 'requests', 'revision', 'metadata', 'settings')
    }


def _incremental_block(doc, block):
    attrs = block.get('attrs', {})
    values = {**_style(doc, block.get('style', 'body')), **attrs}
    return (
        block.get('type', 'paragraph') in INCREMENTAL_KINDS
        and not attrs.get('excluded')
        and not attrs.get('redacted')
        and not values.get('list')
    )


def update_document(qdoc, old, new):
    """Bring a document built by ``build_document(old)`` up to date with ``new`` in place.

    Only the changed run of top-level blocks is rewritten, so Qt relayouts the
    affected region instead of the whole document. Returns False, leaving
    ``qdoc`` untouched, when the change needs a full rebuild (tables, images,
    lists, contents, excluded blocks, or styles/geometry/assets changes).
    Derived keep-rule breaks are not recomputed here; physical pages are
    resolved separately from the semantic model.
    """
    if not hasattr(qdoc, 'byax_positions') or _render_inputs(old) != _render_inputs(new):
        return False
    before, after = old.get('blocks', []), new.get('blocks', [])
    if any(b.get('type') == 'toc' for b in after) or not before or not after:
        return False
    hunks = _changed_runs(before, after)
    if hunks is None:
        return False
    if not hunks:
        return True
    spans = {}
    block = qdoc.firstBlock()
    while block.isValid():
        ident = block.blockFormat().property(BLOCK_ID_PROPERTY)
        if ident:
            span = spans.setdefault(ident, [block, block])
            if span[1] != block:
                if span[1].next() != block:
                    span.append(None)
                span[1] = block
        block = block.next()
    plan = []
    for i1, i2, j1, j2 in hunks:
        removed, inserted = before[i1:i2], after[j1:j2]
        if not all(_incremental_block(old, b) for b in removed) or not all(
            _incremental_block(new, b) for b in inserted
        ):
            return False
        chain = [spans.get(b['id']) for b in removed]
        if any(span is None or len(span) != 2 for span in chain) or any(
            left[1].next() != right[0] for left, right in itertools.pairwise(chain)
        ):
            return False
        plan.append((chain[0][0], chain[-1][1], removed, inserted))
    removed_ids = {b['id'] for _, _, removed, _ in plan for b in removed}
    editor_findings = getattr(qdoc, 'byax_editor_findings', qdoc.byax_findings)
    findings = [f for f in editor_findings if f.get('target') not in removed_ids]
    qdoc.byax_findings = []
    families = set(QFontDatabase.families())
    cursor = QTextCursor(qdoc)
    cursor.beginEditBlock()
    for first, last, _, inserted in reversed(plan):
        cursor.setPosition(first.position())
        cursor.setPosition(last.position() + last.length() - 1, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.setBlockCharFormat(QTextCharFormat())
        start = cursor.position()
        for index, block in enumerate(inserted):
            if index:
                cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
            bf, values = _block_format(qdoc, new, block)
            cursor.setBlockFormat(bf)
            _font_finding(qdoc, values, block['id'], families)
            _emit_text(qdoc, cursor, block, values, {})
        _check_glyphs(qdoc, qdoc.findBlock(start), cursor.block().next())
    cursor.endEditBlock()
    qdoc.byax_findings = findings + qdoc.byax_findings
    positions = {}
    block = qdoc.firstBlock()
    while block.isValid():
        ident = block.blockFormat().property(BLOCK_ID_PROPERTY)
        if ident and ident not in positions:
            positions[ident] = block.position()
        block = block.next()
    qdoc.byax_positions = positions
    invalidate_resolved(qdoc, editor_findings=True)
    return True


def invalidate_resolved(qdoc, *, editor_findings=False):
    """Drop cached physical pages after the semantic document changed."""
    names = ['byax_resolved', 'byax_resolved_outputs'] + (['byax_editor_findings'] if editor_findings else [])
    for name in names:
        if hasattr(qdoc, name):
            delattr(qdoc, name)


def _changed_runs(before, after):
    """Disjoint [i1, i2, j1, j2) runs of changed top-level blocks, neither side empty."""
    old, new = [b.get('id') for b in before], [b.get('id') for b in after]
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != 'equal':
            hunks.append([i1, i2, j1, j2])
            continue
        k = 0
        while k < i2 - i1:
            if before[i1 + k] == after[j1 + k]:
                k += 1
                continue
            start = k
            while k < i2 - i1 and before[i1 + k] != after[j1 + k]:
                k += 1
            hunks.append([i1 + start, i1 + k, j1 + start, j1 + k])
    for hunk in hunks:
        if hunk[0] == hunk[1] or hunk[2] == hunk[3]:
            if hunk[1] < len(before) and hunk[3] < len(after):
                hunk[1], hunk[3] = hunk[1] + 1, hunk[3] + 1
            elif hunk[0] and hunk[2]:
                hunk[0], hunk[2] = hunk[0] - 1, hunk[2] - 1
            else:
                return None
    merged = []
    for hunk in hunks:
        if merged and (hunk[0] <= merged[-1][1] or hunk[2] <= merged[-1][3]):
            merged[-1] = [
                min(merged[-1][0], hunk[0]),
                max(merged[-1][1], hunk[1]),
                min(merged[-1][2], hunk[2]),
                max(merged[-1][3], hunk[3]),
            ]
        else:
            merged.append(hunk)
    return merged


def _block_page(qdoc, position):
    rect = qdoc.documentLayout().blockBoundingRect(qdoc.findBlock(position))
    return int(rect.top() // qdoc.pageSize().height()) + 1


def _format_page_number(number, style='decimal'):
    style = str(style).lower().replace('-', '_')
    aliases = {
        'roman': 'lower_roman',
        'upperroman': 'upper_roman',
        'lowerroman': 'lower_roman',
        'upper_alphabetic': 'upper_alpha',
        'lower_alphabetic': 'lower_alpha',
        'alphabetic': 'lower_alpha',
    }
    style = aliases.get(style, style)
    if style == 'decimal':
        return str(number)
    if number < 1 or number > 999999:
        raise ByaxError('invalid_input', 'Nondecimal page numbers must be within 1..999999')
    if style in ('upper_roman', 'lower_roman'):
        value, result = number, []
        for divisor, token in (
            (1000, 'M'),
            (900, 'CM'),
            (500, 'D'),
            (400, 'CD'),
            (100, 'C'),
            (90, 'XC'),
            (50, 'L'),
            (40, 'XL'),
            (10, 'X'),
            (9, 'IX'),
            (5, 'V'),
            (4, 'IV'),
            (1, 'I'),
        ):
            count, value = divmod(value, divisor)
            result.append(token * count)
        result = ''.join(result)
        return result.lower() if style == 'lower_roman' else result
    if style in ('upper_alpha', 'lower_alpha'):
        value, result = number, ''
        while value:
            value, remainder = divmod(value - 1, 26)
            result = chr(65 + remainder) + result
        return result.lower() if style == 'lower_alpha' else result
    raise ByaxError('invalid_input', 'Unsupported page numbering format', format=style)


def _region(
    value, page, count, *, number=None, number_format='decimal', section_page=None, section_pages=None
):
    number = page + 1 if number is None else number
    section_page = page + 1 if section_page is None else section_page
    if isinstance(value, dict):
        variant = 'first' if section_page == 1 else 'even' if number % 2 == 0 else 'odd'
        value = value.get(variant, value.get('text', ''))
    return (
        str(value or '')
        .replace('{page}', _format_page_number(number, number_format))
        .replace('{pages}', str(count))
        .replace('{section_page}', str(section_page))
        .replace('{section_pages}', str(section_pages if section_pages is not None else count))
    )


def _region_parts(value, page, count, *, default_align='left', **kwargs):
    """Resolve a header/footer variant into left/center/right parts."""
    number = page + 1 if kwargs.get('number') is None else kwargs['number']
    section_page = page + 1 if kwargs.get('section_page') is None else kwargs['section_page']
    if isinstance(value, dict) and any(key in value for key in ('first', 'odd', 'even', 'text')):
        variant = 'first' if section_page == 1 and 'first' in value else 'even' if number % 2 == 0 else 'odd'
        value = value.get(variant, value.get('text', ''))
    if not isinstance(value, dict):
        value = {default_align: value}
    return {
        side: _region(value.get(side), page, count, **kwargs)
        for side in ('left', 'center', 'right')
        if value.get(side)
    }


def _utf16(text, offset):
    return len(text[:offset].encode('utf-16-le')) // 2


def _scalar(text, offset):
    units = 0
    for index, character in enumerate(text):
        if units >= offset:
            return index
        units += 2 if ord(character) > 0xFFFF else 1
    return len(text)


def _splice_text(block, start, end, replacement, mark=None):
    """Replace text[start:end] and keep marks/footnote offsets attached."""
    text = block.get('text', '')
    delta = len(replacement) - (end - start)
    block['text'] = text[:start] + replacement + text[end:]
    marks = []
    for item in block.get('marks', []):
        lo, hi = item.get('start', 0), item.get('end', 0)
        if hi <= start:
            marks.append(item)
        elif lo >= end:
            marks.append({**item, 'start': lo + delta, 'end': hi + delta})
        elif item is not mark:
            marks.append({**item, 'start': min(lo, start), 'end': max(start + len(replacement), hi + delta)})
    if mark is not None:
        marks.append({**mark, 'start': start, 'end': start + len(replacement)})
    block['marks'] = marks
    attrs = block.setdefault('attrs', {})
    attrs['_byax_refs'] = [
        [offset + delta if offset >= end else offset, note] for offset, note in attrs.get('_byax_refs', [])
    ]


def _image_size(attrs, image, max_width, max_height=None):
    natural_w, natural_h = max(1, image.width()), max(1, image.height())
    width, height = attrs.get('width'), attrs.get('height')
    if width is None and height is None:
        width = min(natural_w, max_width)
        height = width * natural_h / natural_w
    elif width is None:
        height = float(height)
        width = height * natural_w / natural_h
    elif height is None:
        width = float(width)
        height = width * natural_h / natural_w
    else:
        width, height = float(width), float(height)
        if attrs.get('lock_aspect'):
            scale = min(width / natural_w, height / natural_h)
            width, height = natural_w * scale, natural_h * scale
    scale = min(
        1.0,
        max_width / width if width > 0 else 1.0,
        max_height / height if max_height and height > 0 else 1.0,
    )
    return width * scale, height * scale


def _keep_settings(doc, block):
    values = {**_style(doc, block.get('style', 'body')), **block.get('attrs', {})}
    heading = block.get('type') == 'heading'

    def count(key, flag):
        # Line-count rules are opt-in (``True`` means two lines); a document default may be set in layout.
        default = doc.get('layout', {}).get(flag, False)
        return max(1, _line_rule(values.get(key, values.get(flag, default))))

    return {
        'together': bool(values.get('keep_together', heading)),
        'with_next': bool(values.get('keep_with_next', heading)),
        'widows': count('widows', 'widow_control'),
        'orphans': count('orphans', 'orphan_control'),
    }


def _caption_target(block):
    attrs = block.get('attrs', {})
    if attrs.get('caption_for'):
        return attrs['caption_for']
    return '' if block.get('style') == 'caption' else None


def _column_settings(layout):
    setting = layout.get('columns', 1)
    if isinstance(setting, dict):
        count = int(setting.get('count', len(setting.get('widths', [])) or 1))
        gap = float(setting.get('gap', 18))
    else:
        count = int(setting)
        gap = float(layout.get('column_gap', 18))
    if count < 1 or count > 12 or not math.isfinite(gap) or gap < 0:
        raise ValueError('Columns require a count of 1..12 and a nonnegative gap')
    return count, gap


def resolve_columns(layout, body_width, target='layout'):
    """Return [(x offset, width)] for explicit or equal columns plus located findings."""
    findings = []
    try:
        count, gap = _column_settings(layout)
    except (TypeError, ValueError):
        findings.append(
            _finding('column_geometry', 'Column settings are invalid; one full-width column is used.', target)
        )
        return [(0.0, body_width)], findings
    setting = layout.get('columns', 1)
    setting = setting if isinstance(setting, dict) else {}
    gaps = setting.get('gaps')
    gaps = (
        [float(value) for value in gaps]
        if isinstance(gaps, list) and len(gaps) == count - 1
        else [gap] * (count - 1)
    )
    widths = setting.get('widths')
    ratios = setting.get('ratios')
    available = body_width - sum(gaps)
    if isinstance(widths, list) and widths:
        if len(widths) != count or any(type(value) not in (int, float) or value <= 0 for value in widths):
            findings.append(
                _finding(
                    'column_geometry',
                    'Explicit column widths must list one positive width per column; equal widths are used.',
                    target,
                )
            )
            widths = [available / count] * count
        else:
            widths = [float(value) for value in widths]
            if sum(widths) > available + EPSILON:
                findings.append(
                    _finding(
                        'column_geometry',
                        'Explicit column widths and gaps exceed the body width; widths were scaled proportionally.',
                        target,
                    )
                )
                widths = [value * available / sum(widths) for value in widths]
    elif (
        isinstance(ratios, list)
        and len(ratios) == count
        and all(type(v) in (int, float) and v > 0 for v in ratios)
    ):
        widths = [available * value / sum(ratios) for value in ratios]
    else:
        widths = [available / count] * count
    if available <= 0 or min(widths) < 24:
        findings.append(
            _finding(
                'column_geometry',
                'Columns and gaps leave less than 24 points per column; one full-width column is used.',
                target,
            )
        )
        return [(0.0, body_width)], findings
    result, x = [], 0.0
    for index, value in enumerate(widths):
        result.append((x, value))
        x += value + (gaps[index] if index < len(gaps) else 0)
    return result, findings


def _same_geometry(a, b):
    return (
        a is not None
        and b is not None
        and geometry({'layout': a}) == geometry({'layout': b})
        and a.get('master') == b.get('master')
    )


def output_view(doc, output=None):
    """Apply decorative hiding, semantic exclusion and output policies without mutating ``doc``."""

    def selected(attrs):
        if attrs.get('excluded') or attrs.get('redacted'):
            return False
        if output is not None:
            if output in attrs.get('exclude_outputs', []):
                return False
            if 'outputs' in attrs and output not in attrs['outputs']:
                return False
        return True

    layers = {layer.get('id'): layer for layer in doc.get('layers', [])}
    report = {
        'output': output or 'default',
        'hidden': [],
        'excluded': [],
        'redacted': [],
        'redaction': 'not_performed',
    }
    blocks = []
    for block in doc.get('blocks', []):
        attrs = block.get('attrs', {})
        if attrs.get('redacted'):
            report['redacted'].append(block.get('id'))
        elif not selected(attrs):
            report['excluded'].append(block.get('id'))
        else:
            if block.get('type') == 'image' and attrs.get('visible') is False:
                report['hidden'].append(block.get('id'))
            blocks.append(block)
    kept = []
    for layer in doc.get('layers', []):
        group = layers.get(layer.get('group'), {})
        if layer.get('redacted') or group.get('redacted'):
            report['redacted'].append(layer.get('id'))
        elif not selected(layer) or not selected(group):
            report['excluded'].append(layer.get('id'))
        elif layer.get('visible', True) is False or group.get('visible', True) is False:
            report['hidden'].append(layer.get('id'))
        else:
            kept.append(layer)
    report['note'] = (
        'Hidden objects keep their authored content and are omitted only from drawing; excluded content is removed '
        'from this output and reflowed. Neither is redaction: only sanitized publication removes recoverable data.'
    )
    return {**doc, 'blocks': blocks, 'layers': kept}, report


class _Resolver:
    """Shared break decisions: one resolved list of physical pages for every output."""

    def __init__(self, doc, labels):
        self.doc = doc
        self.labels = labels
        self.findings = []
        self.runs = {}
        self.note_docs = {}
        self.pages = []
        self.positions = {}
        self.anchors = {}
        self.carry = []
        self.number = None
        assets = doc.get('assets', {})
        self.image_sizes = {}
        for key, asset in assets.items():
            if asset.get('mime', '').startswith('image/'):
                try:
                    picture = _decode_image(base64.b64decode(asset.get('data', ''), validate=True))
                except (ValueError, TypeError):
                    continue
                if not picture.isNull():
                    self.image_sizes[key] = picture

    # ------------------------------------------------------------------ model
    def find(self, code, message, target):
        self.findings.append(_finding(code, message, target))

    def bands(self):
        doc = self.doc
        base = copy.deepcopy(doc.get('layout', {}))
        settings = self.master(base, base.get('master'), {}, 'layout')
        settings['_byax_section'] = 0
        settings['_byax_page_number_restart'] = int(base.get('page_number_start', 1))
        bands, notes, current, pending, previous = [], [], None, 'page', None
        self.overlay_anchor = {}
        waiting = []

        def close():
            nonlocal current
            if current is not None and current['blocks']:
                bands.append(current)
            current = None

        for block in doc.get('blocks', []):
            attrs, kind = block.get('attrs', {}), block.get('type')
            if kind == 'footnote':
                note = copy.deepcopy(block)
                note['attrs']['reference'] = attrs.get(
                    'reference', attrs.get('reference_id', attrs.get('target', previous))
                )
                notes.append(note)
                continue
            if kind == 'image' and _wrap(attrs) in ('behind_text', 'in_front'):
                waiting.append(block.get('id'))
                if previous is not None:
                    self.overlay_anchor[block['id']] = previous
                continue
            if kind == 'page_break':
                close()
                pending = 'page'
                continue
            if kind == 'section_break':
                close()
                section_values = copy.deepcopy(attrs.get('layout', {}))
                section_values.update(
                    {
                        key: attrs[key]
                        for key in ('page_number_start', 'page_number_format', 'restart_page_numbering')
                        if key in attrs
                    }
                )
                for key in (
                    'columns',
                    'column_gap',
                    'width',
                    'height',
                    'margins',
                    'paper',
                    'orientation',
                    'header',
                    'footer',
                    'master',
                    'background',
                ):
                    if key in attrs:
                        section_values[key] = copy.deepcopy(attrs[key])
                restart = section_values.get('page_number_start')
                if restart is None and section_values.get('restart_page_numbering'):
                    restart = 1
                settings = self.master(
                    settings, section_values.get('master'), section_values, block.get('id')
                )
                settings['_byax_section'] += 1
                settings['_byax_page_number_restart'] = int(restart) if restart is not None else None
                start = str(attrs.get('start', section_values.get('start', 'next_page'))).replace('-', '_')
                pending = {
                    'continuous': 'continuous',
                    'odd_page': 'odd',
                    'odd': 'odd',
                    'even_page': 'even',
                    'even': 'even',
                }.get(start, 'page')
                continue
            if attrs.get('page_break_before'):
                close()
                pending = 'page'
            for ident in waiting:
                self.overlay_anchor.setdefault(ident, block.get('id'))
            waiting = []
            if attrs.get('full_width'):
                close()
                full = copy.deepcopy(settings)
                full['columns'] = 1
                bands.append({'settings': full, 'blocks': [block], 'start': pending, 'full_width': True})
                pending = 'continuous'
            else:
                if current is None:
                    current = {'settings': copy.deepcopy(settings), 'blocks': [], 'start': pending}
                    pending = 'continuous'
                current['blocks'].append(block)
            previous = block.get('id')
        close()
        if not bands:
            bands.append({'settings': copy.deepcopy(settings), 'blocks': [], 'start': 'page'})
        for ident in waiting:
            self.overlay_anchor.setdefault(ident, previous)
        return bands, notes

    def master(self, carried, name, overrides, target):
        result = copy.deepcopy(carried)
        if name:
            masters = self.doc.get('layout', {}).get('masters', {})
            if not isinstance(masters.get(name), dict):
                self.find(
                    'master_missing',
                    f'Page master {name!r} is not defined; section settings are used without it.',
                    target,
                )
            else:
                result.update(
                    {
                        key: copy.deepcopy(value)
                        for key, value in masters[name].items()
                        if key not in ('artwork', 'name')
                    }
                )
                result['master'] = name
        result.update(copy.deepcopy(overrides))
        return result

    # ------------------------------------------------------------ preparation
    def prepare(self, bands, notes):
        doc = self.doc
        order = {}
        for index, block in enumerate(b for band in bands for b in band['blocks']):
            order[block.get('id')] = (index, 0)
            for row in block.get('rows', []):
                for cell in row.get('cells', []):
                    order[cell.get('id')] = (index, 0)
        self.note_numbers, self.note_labels, self.note_section = {}, {}, {}
        attached, orphaned = [], []
        for note in notes:
            reference = note['attrs'].get('reference')
            if reference in order:
                attached.append(note)
            else:
                orphaned.append(note)
                self.find(
                    'footnote_orphaned',
                    'Footnote reference is missing; note is appended after body content.',
                    note['id'],
                )
        section_of = {}
        for band in bands:
            for block in band['blocks']:
                section_of[block.get('id')] = band['settings'].get('_byax_section', 0)
                for row in block.get('rows', []):
                    for cell in row.get('cells', []):
                        section_of[cell.get('id')] = section_of[block.get('id')]
        attached.sort(key=lambda n: (order[n['attrs']['reference']], int(n['attrs'].get('offset', 10**9))))
        layout = doc.get('layout', {})
        counters = {}
        for note in attached + orphaned:
            scope = (
                section_of.get(note['attrs'].get('reference'), -1)
                if layout.get('footnote_restart') == 'section'
                else 0
            )
            counters[scope] = counters.get(scope, 0) + 1
            self.note_numbers[note['id']] = counters[scope]
            self.note_labels[note['id']] = _format_page_number(
                counters[scope], layout.get('footnote_format', 'decimal')
            )
        self.notes = {note['id']: note for note in attached + orphaned}
        self.orphaned = orphaned
        by_reference = {}
        for note in attached:
            by_reference.setdefault(note['attrs']['reference'], []).append(note)
        headings = [b for band in bands for b in band['blocks'] if b.get('type') == 'heading']
        captions = [b for band in bands for b in band['blocks'] if _caption_target(b) is not None]
        self.caption_numbers = {}
        for index, caption in enumerate(captions, start=1):
            self.caption_numbers[caption['id']] = index
            if caption.get('attrs', {}).get('caption_for'):
                self.caption_numbers[caption['attrs']['caption_for']] = index
        self.texts = {b.get('id'): b.get('text', '') for b in doc.get('blocks', [])}
        prepared = []
        for band in bands:
            blocks = []
            for source in band['blocks']:
                block = copy.deepcopy(source)
                attrs = block.setdefault('attrs', {})
                attrs.pop('page_break_before', None)
                if block.get('type') == 'toc':
                    block['type'] = 'paragraph'
                    block['text'] = '\n'.join(
                        '  ' * (int(h.get('attrs', {}).get('level', 1)) - 1)
                        + h.get('text', '')
                        + '    '
                        + str(self.labels.get(h['id'], '…'))
                        for h in headings
                    )
                    block['marks'] = []
                    attrs['_byax_toc'] = True
                self.cross_references(block)
                for note in by_reference.get(block.get('id'), []):
                    self.marker(block, note)
                for row in block.get('rows', []):
                    for cell in row.get('cells', []):
                        self.cross_references(cell, block.get('id'))
                        for note in by_reference.get(cell.get('id'), []):
                            self.marker(cell, note)
                blocks.append(block)
            prepared.append(blocks)
        return prepared

    def marker(self, block, note):
        if block.get('type') not in (None, 'paragraph', 'heading'):
            block.setdefault('attrs', {}).setdefault('_byax_refs', []).append([0, note['id']])
            return
        label = self.note_labels[note['id']]
        text = block.get('text', '')
        offset = note['attrs'].get('offset')
        offset = len(text) if type(offset) is not int or not 0 <= offset <= len(text) else offset
        _splice_text(block, offset, offset, label, {'type': 'superscript', 'value': True})
        block['attrs'].setdefault('_byax_refs', []).append([offset, note['id']])

    def cross_references(self, block, owner=None):
        marks = [m for m in block.get('marks', []) if m.get('type') in ('xref', 'cross_reference')]
        for mark in sorted(marks, key=lambda m: -m.get('start', 0)):
            value = (
                mark.get('value') if isinstance(mark.get('value'), dict) else {'target': mark.get('value')}
            )
            label = self.reference_label(value.get('target'), value.get('show', 'page'))
            if label is None:
                label = '??'
                self.find(
                    'xref_unresolved',
                    f'Cross-reference target {value.get("target")!r} no longer exists; the reference is shown as unresolved.',
                    owner or block.get('id'),
                )
            if 'prefix' in value:
                label = str(value['prefix']) + label
            _splice_text(block, mark.get('start', 0), mark.get('end', 0), label, mark)

    def reference_label(self, target, show):
        bookmarks = self.doc.get('settings', {}).get('bookmarks', {})
        if isinstance(bookmarks.get(target), dict):
            target = bookmarks[target].get('target')
        if target in self.note_labels:
            return (
                self.note_labels[target] if show in ('number', 'label') else str(self.labels.get(target, '?'))
            )
        if target not in self.texts and not any(
            cell.get('id') == target
            for b in self.doc.get('blocks', [])
            for row in b.get('rows', [])
            for cell in row.get('cells', [])
        ):
            return None
        if show == 'text':
            return self.texts.get(target, '')
        if show == 'number':
            return str(self.caption_numbers.get(target, '?'))
        return str(self.labels.get(target, '?'))

    # ------------------------------------------------------------------- runs
    def run(self, band_index, cursor, width):
        key = (band_index, cursor, round(width, 3))
        if key in self.runs:
            return self.runs[key]
        blocks = self.prepared[band_index]
        index, kind, value = cursor
        part = [copy.deepcopy(b) for b in blocks[index:]]
        offset = 0
        if part and kind == 'text' and value:
            block = part[0]
            offset = value
            start = _scalar(block.get('text', ''), value)
            attrs = block['attrs']
            refs = [[o - start, n] for o, n in attrs.get('_byax_refs', []) if o >= start]
            attrs['_byax_refs'] = []
            _splice_text(block, 0, start, '')
            block['marks'] = [m for m in block['marks'] if m['end'] > m['start']]
            attrs['_byax_refs'] = refs
            attrs.update(first_line_indent=0, space_before=0, _byax_continued=True)
            listing = attrs.pop('list', None) or _style(self.doc, block.get('style', 'body')).get('list')
            if listing:
                level = int(listing.get('level', 1) if isinstance(listing, dict) else attrs.get('level', 1))
                attrs['list'] = None
                attrs['indent'] = float(attrs.get('indent', 0)) + 40 * level
        elif part and kind == 'row':
            table = part[0]
            header = int(table.get('attrs', {}).get('header_rows', 0))
            repeat = header and table['attrs'].get('repeat_header', True)
            table['rows'] = (table['rows'][:header] if repeat else []) + table['rows'][value:]
            table['attrs']['_byax_row_base'] = value
            table['attrs']['_byax_repeat'] = bool(repeat)
            if not repeat:
                table['attrs']['header_rows'] = 0
        settings = self.band_list[band_index]['settings']
        _, height, margins = geometry({'layout': settings})
        body_height = height - margins['top'] - margins['bottom']
        part_doc = {
            **self.doc,
            'blocks': part,
            'layers': [],
            'layout': {'width': width, 'height': 1, 'margins': {}, 'columns': 1},
            'assets': {
                k: a
                for k, a in self.doc.get('assets', {}).items()
                if any(b.get('attrs', {}).get('asset') == k for b in part)
            },
        }
        qdoc = build_document(part_doc, {}, _flow={'width': width, 'max_height': body_height})
        for item in qdoc.byax_findings:
            self.findings.append(item)
        result = {
            'qdoc': qdoc,
            'width': width,
            'band': band_index,
            'base': index,
            'offset': offset,
            'blocks': part,
            'tables': {},
        }
        result['units'] = self.units(result, body_height)
        self.runs[key] = result
        return result

    def units(self, run, column_height):
        qdoc, part = run['qdoc'], run['blocks']
        layout = qdoc.documentLayout()
        local_of, owner = {}, {}
        for index, block in enumerate(part):
            local_of[block.get('id')] = index
            for row in block.get('rows', []):
                for cell in row.get('cells', []):
                    owner[cell.get('id')] = (index, cell)
        units, floats = [], []
        current = 0
        iterator = qdoc.rootFrame().begin()
        while not iterator.atEnd():
            frame, block = iterator.currentFrame(), iterator.currentBlock()
            if frame is not None:
                if isinstance(frame, QTextTable):
                    units.extend(self.table_units(run, frame, owner, layout))
                else:
                    rect = layout.frameBoundingRect(frame)
                    ident = qdoc.findBlock(frame.firstPosition()).blockFormat().property(BLOCK_ID_PROPERTY)
                    floats.append((rect.top(), rect.bottom(), ident))
            elif block.isValid():
                ident = block.blockFormat().property(BLOCK_ID_PROPERTY)
                if ident in local_of:
                    current = local_of[ident]
                beside_table = any(
                    isinstance(qdoc.frameAt(other.position()), QTextTable)
                    for other in (block.next(), block.previous())
                    if other.isValid()
                )
                if not (block.length() <= 1 and beside_table) and part:
                    units.extend(
                        self.line_units(run, block, current, ident == part[current].get('id'), layout)
                    )
            iterator += 1
        for top, bottom, ident in floats:
            inside = [u for u in units if u['top'] < bottom - EPSILON and u['bottom'] > top + EPSILON]
            if not inside:
                local = local_of.get(ident, 0)
                units.append(
                    self.unit(top, bottom, [ident], local, local, 0, 1, (run['base'] + local, None, 0))
                )
                units.sort(key=lambda u: u['top'])
                continue
            merged = inside[0]
            for other in inside[1:]:
                merged['ids'] += [i for i in other['ids'] if i not in merged['ids']]
                merged['refs'] += other['refs']
                merged['last'] = other['last']
                merged['bottom'] = max(merged['bottom'], other['bottom'])
                units.remove(other)
            merged['top'], merged['bottom'] = min(merged['top'], top), max(merged['bottom'], bottom)
            if ident not in merged['ids']:
                merged['ids'].append(ident)
        run['keeps'] = self.keeps(run, units, column_height)
        return units

    def unit(self, top, bottom, ids, first, last, line, count, cursor, **extra):
        return {
            'top': top,
            'bottom': bottom,
            'ids': list(ids),
            'first': first,
            'last': last,
            'line': line,
            'count': count,
            'cursor': cursor,
            'restart': cursor is not None,
            'refs': [],
            'force': False,
            'table': None,
            **extra,
        }

    def line_units(self, run, block, local, primary, layout):
        source = run['blocks'][local]
        lines = block.layout()
        rect = layout.blockBoundingRect(block)
        refs = {}
        if primary:
            text = source.get('text', '')
            for offset, note in source.get('attrs', {}).get('_byax_refs', []):
                line = lines.lineForTextPosition(min(_utf16(text, offset), max(0, block.length() - 2)))
                index = line.lineNumber() if line.isValid() else 0
                refs.setdefault(index, []).append(note)
        result = []
        count = lines.lineCount()
        for index in range(count):
            line = lines.lineAt(index)
            top = rect.top() + line.y()
            offset = (run['offset'] if local == 0 else 0) + line.textStart()
            kind = 'text' if source.get('type') in ('paragraph', 'heading') and primary else None
            cursor = (
                (run['base'] + local, kind, offset if kind else 0)
                if primary and (index == 0 or kind)
                else None
            )
            unit = self.unit(
                top,
                top + line.height(),
                [source.get('id')] if index == 0 else [],
                local,
                local,
                index if primary else 0,
                count if primary else 1,
                cursor,
            )
            unit['refs'] = refs.get(index, [])
            unit['force'] = bool(
                index == 0 and primary and source.get('attrs', {}).get('column_break_before')
            )
            result.append(unit)
        return result

    def table_units(self, run, table, owner, layout):
        qdoc = run['qdoc']
        first = qdoc.findBlock(table.cellAt(0, 0).firstPosition()).blockFormat().property(BLOCK_ID_PROPERTY)
        local, _ = owner.get(first, (0, None))
        source = run['blocks'][local]
        attrs = source.get('attrs', {})
        header = int(attrs.get('header_rows', 0))
        base_row = int(attrs.get('_byax_row_base', 0))
        repeated = bool(attrs.get('_byax_repeat'))

        def actual(row):
            if base_row and repeated:
                return row if row < header else base_row + row - header
            return base_row + row

        rows, columns = table.rows(), table.columns()
        lines, ends, left, right = {}, {}, math.inf, -math.inf
        for r in range(rows):
            for c in range(columns):
                cell = table.cellAt(r, c)
                if cell.row() != r or cell.column() != c:
                    continue
                ends[r] = max(ends.get(r, r), r + cell.rowSpan() - 1)
                block = qdoc.findBlock(cell.firstPosition())
                ident = block.blockFormat().property(BLOCK_ID_PROPERTY)
                cell_source = owner.get(ident, (None, {}))[1] or {}
                while block.isValid() and block.position() <= cell.lastPosition():
                    rect = layout.blockBoundingRect(block)
                    left, right = min(left, rect.left()), max(right, rect.right())
                    lay = block.layout()
                    text = block.text()
                    refs = {}
                    if block.blockFormat().property(BLOCK_ID_PROPERTY) == ident:
                        for offset, note in cell_source.get('attrs', {}).get('_byax_refs', []):
                            line = lay.lineForTextPosition(
                                min(_utf16(cell_source.get('text', ''), offset), max(0, len(text) - 1))
                            )
                            refs.setdefault(line.lineNumber() if line.isValid() else 0, []).append(note)
                    for index in range(lay.lineCount()):
                        line = lay.lineAt(index)
                        lines.setdefault(r, []).append(
                            (
                                rect.top() + line.y(),
                                rect.top() + line.y() + line.height(),
                                refs.get(index, []),
                            )
                        )
                    block = block.next()
        groups, r = [], 0
        while r < rows:
            end = ends.get(r, r)
            probe = r
            while probe <= end:
                end = max(end, ends.get(probe, probe))
                probe += 1
            groups.append((r, end))
            r = end + 1
        padding = float(attrs.get('padding', 4)) + 2 * float(attrs.get('border_width', 0.5))
        extents = []
        for r0, r1 in groups:
            items = [item for row in range(r0, r1 + 1) for item in lines.get(row, [])]
            extents.append(
                (min((i[0] for i in items), default=None), max((i[1] for i in items), default=None), items)
            )
        info = {
            'row_split': str(attrs.get('row_split', 'auto')),
            'header_rows': header,
            'id': source.get('id'),
            'repeat': header > 0 and attrs.get('repeat_header', True) is not False and not repeated,
            'label': attrs.get('continued_label'),
            'left': left - padding,
            'right': right + padding,
            'color': attrs.get('border_color', '#808080'),
            'border': float(attrs.get('border_width', 0.5)),
        }
        run['tables'][source.get('id')] = info
        units = []
        cut = None
        for index, ((r0, r1), (top, bottom, items)) in enumerate(zip(groups, extents)):
            if top is None:
                continue
            start = top - padding if cut is None else cut
            following = next((e[0] for e in extents[index + 1 :] if e[0] is not None), None)
            cut = (bottom + following) / 2 if following is not None else None
            stop = bottom + padding if cut is None else cut
            clusters = []
            for item in sorted(items):
                if clusters and item[0] < clusters[-1][1] - EPSILON:
                    clusters[-1][1] = max(clusters[-1][1], item[1])
                    clusters[-1][2] += item[2]
                else:
                    clusters.append([item[0], item[1], list(item[2])])
            clusters[0][0], clusters[-1][1] = start, stop
            row_ids, table_refs = [], []
            for row in range(r0, r1 + 1):
                if (repeated and row < header) or row >= len(source.get('rows', [])):
                    continue
                row_ids.append(source['rows'][row].get('id'))
                row_ids += [cell.get('id') for cell in source['rows'][row].get('cells', [])]
            if not units and base_row == 0:
                row_ids.insert(0, source.get('id'))
                table_refs = [note for _, note in attrs.get('_byax_refs', [])]
            for number, (top_c, bottom_c, refs) in enumerate(clusters):
                restart = number == 0 and not (repeated and r0 < header)
                cursor = (
                    (run['base'] + local, 'row', actual(r0))
                    if restart and actual(r0) >= header
                    else ((run['base'] + local, None, 0) if restart and actual(r0) == 0 else None)
                )
                unit = self.unit(
                    top_c,
                    bottom_c,
                    row_ids if number == 0 else [],
                    local,
                    local,
                    number,
                    len(clusters),
                    cursor,
                    table=source.get('id'),
                    row=actual(r0),
                    header=r0 < header,
                    inrow=number > 0,
                    group_height=stop - start,
                )
                unit['refs'] = refs + (table_refs if number == 0 else [])
                unit['force'] = bool(
                    not units and source.get('attrs', {}).get('column_break_before') and base_row == 0
                )
                units.append(unit)
        if units and info['repeat']:
            last_header = [u for u in units if u['header']]
            if last_header:
                info['header'] = (units[0]['top'], last_header[-1]['bottom'])
        return units

    def keeps(self, run, units, column_height):
        part = run['blocks']
        keeps = [_keep_settings(self.doc, block) for block in part]
        for index, block in enumerate(part):
            if block.get('type') not in ('paragraph', 'heading'):
                keeps[index]['together'] = (
                    block.get('type') == 'image' or keeps[index]['together'] and block.get('type') != 'table'
                )
            target = _caption_target(block)
            if target is not None:
                if index and (
                    target == part[index - 1].get('id')
                    or target == ''
                    and part[index - 1].get('type') in ('image', 'table')
                ):
                    keeps[index - 1]['with_next'] = True
                    keeps[index - 1]['caption'] = True
                elif index + 1 < len(part) and target == part[index + 1].get('id'):
                    keeps[index]['with_next'] = True
                    keeps[index]['caption'] = True
        spans = {}
        for unit in units:
            for local in {unit['first'], unit['last']}:
                top, bottom = spans.get(local, (unit['top'], unit['bottom']))
                spans[local] = (min(top, unit['top']), max(bottom, unit['bottom']))
        for index, keep in enumerate(keeps):
            if index not in spans:
                continue
            if keep['together'] and spans[index][1] - spans[index][0] > column_height + EPSILON:
                keep['together'] = False
                if part[index].get('type') in ('paragraph', 'heading') and (
                    part[index].get('attrs', {}).get('keep_together') or part[index].get('type') == 'heading'
                ):
                    self.find(
                        'keep_rule_unsatisfiable',
                        'Keep-together content is taller than the column; it continues across the break.',
                        part[index].get('id'),
                    )
            if keep['with_next']:
                end = index
                while end < len(keeps) - 1 and keeps[end]['with_next']:
                    end += 1
                following = (
                    [u for u in units if u['first'] == end][: max(1, keeps[end]['orphans'])]
                    if end != index
                    else []
                )
                bottom = following[-1]['bottom'] if following else spans.get(end, spans[index])[1]
                if bottom - spans[index][0] > column_height + EPSILON:
                    keep['with_next'] = False
                    code = 'caption_keep_unsatisfiable' if keep.get('caption') else 'keep_rule_unsatisfiable'
                    self.find(
                        code,
                        'Keep-with-next chain is taller than the column; content continues across the break.',
                        part[index].get('id'),
                    )
        return keeps

    def cut(self, run, index, width_change):
        """None when a break before unit ``index`` is allowed, otherwise the violated rule."""
        units = run['units']
        if index <= 0 or index >= len(units):
            return None
        a, b = units[index - 1], units[index]
        if width_change and not b['restart']:
            return 'column_width_change'
        if b['table'] is not None and a['table'] == b['table']:
            if b['header'] or a['header']:
                return 'table_header_stranded'
            if b['inrow']:
                policy = run['tables'][b['table']]['row_split']
                if (
                    policy == 'never'
                    or policy != 'allow'
                    and b['group_height'] <= self.column_height + EPSILON
                ):
                    return 'table_row_split'
            return None
        if a['last'] == b['first'] and a['table'] is None and b['table'] is None:
            keep = run['keeps'][a['last']]
            if keep['together']:
                return 'keep_together'
            if b['line'] and b['count'] > 1:
                if b['line'] < keep['orphans']:
                    return 'orphan_control'
                if b['count'] - b['line'] < keep['widows']:
                    return 'widow_control'
            return None
        keep = run['keeps'][a['last']]
        if keep['with_next']:
            return 'caption_keep' if keep.get('caption') else 'keep_with_next'
        return None

    # ------------------------------------------------------------------ notes
    def note_doc(self, ident, width):
        key = (ident, round(width, 3))
        if key not in self.note_docs:
            note = copy.deepcopy(self.notes[ident])
            prefix = self.note_labels[ident] + '. '
            note['type'] = 'paragraph'
            note['text'] = prefix + note.get('text', '')
            note['marks'] = [
                {**m, 'start': m['start'] + len(prefix), 'end': m['end'] + len(prefix)}
                for m in note.get('marks', [])
            ]
            note['attrs'] = {
                **{k: v for k, v in note['attrs'].items() if k not in ('reference', 'offset')},
                'font_size': 9,
                'space_after': 3,
            }
            qdoc = build_document(
                {**self.doc, 'blocks': [note], 'layers': [], 'assets': {}, 'layout': {'width': width}},
                {},
                _flow={'width': width},
            )
            qdoc.byax_findings = []
            lines = []
            block = qdoc.firstBlock()
            while block.isValid():
                rect = qdoc.documentLayout().blockBoundingRect(block)
                for index in range(block.layout().lineCount()):
                    line = block.layout().lineAt(index)
                    lines.append((rect.top() + line.y(), rect.top() + line.y() + line.height()))
                block = block.next()
            height = qdoc.documentLayout().documentSize().height()
            self.note_docs[key] = (qdoc, lines or [(0, height)], height)
        return self.note_docs[key]

    def notes_height(self, page, extra):
        height = sum(item[2] - item[1] for item in page['notes'])
        height += sum(self.note_doc(ident, page['body_width'])[2] for ident in extra)
        return height + (12 if page['notes'] or extra else 0)

    def place_carry(self, page, cap, force=True):
        """Continue split notes first; ``force`` guarantees one line of progress on a new page."""
        remaining = []
        for ident, start in self.carry:
            if remaining:
                remaining.append((ident, start))
                continue
            _, lines, _ = self.note_doc(ident, page['body_width'])
            used = self.notes_height(page, []) or 12
            stop = start
            for top, bottom in lines:
                if top < start - EPSILON:
                    continue
                if bottom - start + used > cap + EPSILON and (stop > start or not force):
                    break
                stop = bottom
            if stop <= start + EPSILON:
                remaining.append((ident, start))
                continue
            if (
                page['notes']
                and page['notes'][-1][0] == ident
                and abs(page['notes'][-1][2] - start) < EPSILON
            ):
                page['notes'][-1] = (ident, page['notes'][-1][1], stop)
            else:
                page['notes'].append((ident, start, stop))
            force = False
            if any(top >= stop - EPSILON for top, _ in lines):
                remaining.append((ident, stop))
        self.carry = remaining

    # ------------------------------------------------------------------ pages
    def new_page(self, settings, blank=False):
        width, height, margins = geometry({'layout': settings})
        section = settings.get('_byax_section', 0)
        previous = self.pages[-1] if self.pages else None
        if previous is None:
            number = int(self.doc.get('layout', {}).get('page_number_start', 1))
            restart = settings.get('_byax_page_number_restart')
            number = restart if restart is not None else number
        elif previous['section'] != section and settings.get('_byax_page_number_restart') is not None:
            number = settings['_byax_page_number_restart']
        else:
            number = previous['number'] + 1
        page = {
            'width': width,
            'height': height,
            'layout': settings,
            'fragments': [],
            'section': section,
            'number': number,
            'physical_number': len(self.pages) + 1,
            'notes': [],
            'body_top': margins['top'],
            'body_bottom': height - margins['bottom'],
            'body_left': margins['left'],
            'body_width': width - margins['left'] - margins['right'],
            'used': margins['top'],
            'blank': blank,
        }
        self.pages.append(page)
        if self.carry:
            self.place_carry(page, (page['body_bottom'] - page['body_top']) * 0.5)
        return page

    def finish_notes(self, page):
        if not page['notes']:
            return
        y = page['body_bottom'] - sum(item[2] - item[1] for item in page['notes'])
        for ident, top, bottom in page['notes']:
            qdoc = self.note_doc(ident, page['body_width'])[0]
            page['fragments'].append(
                {
                    'document': qdoc,
                    'source': QRectF(0, top, page['body_width'], bottom - top),
                    'rect': QRectF(page['body_left'], y, page['body_width'], bottom - top),
                    'kind': 'footnotes',
                    'ids': [ident],
                    'continued': top > EPSILON,
                }
            )
            if top <= EPSILON:
                self.positions.setdefault(ident, page['physical_number'])
            y += bottom - top

    def fit(self, run, pos, top, bottom, state, fresh, width_change):
        units = run['units']
        start = units[pos]['top']
        header = self.continuation_header(run, pos)
        extra = (header[1] - header[0] + (14 if header[2] else 0)) if header else 0
        page = state['page']
        page_view = {'notes': page['notes'], 'body_width': page['body_width']}
        noted = list(state['notes'])
        j, notes = pos, []
        forced = False
        while j < len(units):
            unit = units[j]
            if j > pos and unit['force']:
                forced = True
                break
            candidate = notes + [n for n in unit['refs'] if n not in noted and n not in notes]
            reserve = self.notes_height(page_view, noted + candidate)
            limit = min(bottom, page['body_bottom'] - reserve)
            if (
                top + extra + unit['bottom'] - start > limit + EPSILON
                or state['used'] > page['body_bottom'] - reserve + EPSILON
            ):
                break
            notes = candidate
            j += 1
        reason = None
        if j < len(units) and not forced:
            end = next((e for e in range(j, pos, -1) if self.cut(run, e, width_change) is None), None)
            if end is None:
                if not fresh:
                    return {'end': pos, 'height': 0, 'notes': [], 'header': None, 'reason': None}
                end = max(j, pos + 1)
                reason = 'oversized_content' if j == pos else self.cut(run, end, width_change)
                if j == pos and units[pos]['refs']:
                    reason = 'footnote_continued'
            j = end
        notes = [n for unit in units[pos:j] for n in unit['refs'] if n not in noted]
        height = units[j - 1]['bottom'] - start + extra if j > pos else 0
        return {'end': j, 'height': height, 'notes': notes, 'header': header, 'reason': reason}

    def continuation_header(self, run, pos):
        """Repeated header rows and continued label for a table resuming in this column."""
        unit = run['units'][pos]
        if unit['table'] is None:
            return None
        info = run['tables'][unit['table']]
        if pos == 0 and run['blocks'][unit['first']].get('attrs', {}).get('_byax_row_base'):
            return (0, 0, info['label']) if info.get('label') else None
        if unit['header'] or not any(u['table'] == unit['table'] for u in run['units'][:pos]):
            return None
        if 'header' in info:
            return (info['header'][0], info['header'][1], info.get('label'))
        return (0, 0, info['label']) if info.get('label') else None

    def plan(self, band_index, run, pos, page, top, columns, bottom=None, fresh_allowed=True):
        """Pure: decide the columns of one page portion without committing them."""
        state = {'page': page, 'used': page['used'], 'notes': []}
        placements, findings = [], []
        bottom = page['body_bottom'] if bottom is None else bottom
        empty_page = (
            not page['fragments'] and page['used'] <= page['body_top'] + EPSILON and not page['notes']
        )
        for index, (x, width) in enumerate(columns):
            if pos >= len(run['units']):
                break
            if abs(run['width'] - width) > EPSILON:
                unit = run['units'][pos]
                if unit['restart'] and unit['cursor'] is not None:
                    run, pos = self.run(band_index, unit['cursor'], width), 0
                else:
                    findings.append(
                        _finding(
                            'column_width_change',
                            'Content continues into a column of a different width without a restartable boundary; it is clipped to the column.',
                            run['blocks'][unit['first']].get('id'),
                        )
                    )
            next_width = columns[index + 1][1] if index + 1 < len(columns) else columns[0][1]
            fresh = fresh_allowed and empty_page and index == 0
            fit = self.fit(run, pos, top, bottom, state, fresh, abs(next_width - run['width']) > EPSILON)
            if fit['reason']:
                unit = run['units'][max(pos, fit['end'] - 1)]
                findings.append(self.rule_finding(fit['reason'], run, unit))
            placements.append({'x': x, 'width': width, 'run': run, 'pos': pos, 'fit': fit})
            state['used'] = max(state['used'], top + fit['height']) if fit['height'] else state['used']
            state['notes'] += fit['notes']
            pos = fit['end']
        return {
            'placements': placements,
            'run': run,
            'pos': pos,
            'done': pos >= len(run['units']),
            'used': state['used'],
            'notes': state['notes'],
            'findings': findings,
        }

    def rule_finding(self, reason, run, unit):
        ident = (
            run['blocks'][unit['first']].get('id')
            if unit.get('first') is not None and unit['first'] < len(run['blocks'])
            else None
        )
        messages = {
            'oversized_content': (
                'oversized_content',
                'Content is taller than the available column and is clipped at the column edge; nothing is removed from the document.',
            ),
            'footnote_continued': (
                'footnote_continued',
                "Footnote text exceeds the page's note area; it continues in the note area of following pages.",
            ),
            'keep_together': (
                'keep_rule_unsatisfiable',
                'Keep-together could not be satisfied on a fresh page or column; content continues across the break.',
            ),
            'keep_with_next': (
                'keep_rule_unsatisfiable',
                'Keep-with-next could not be satisfied on a fresh page or column.',
            ),
            'caption_keep': (
                'caption_keep_unsatisfiable',
                'Caption and object could not be kept together on a fresh page or column.',
            ),
            'widow_control': (
                'widow_orphan_unsatisfiable',
                'Widow control could not be satisfied in the available column height.',
            ),
            'orphan_control': (
                'widow_orphan_unsatisfiable',
                'Orphan control could not be satisfied in the available column height.',
            ),
            'table_row_split': (
                'table_row_unsplittable',
                'A table row that may not split is taller than the available area; it is split at a line boundary.',
            ),
            'table_header_stranded': (
                'table_header_unsatisfiable',
                'Table header rows and the first body row do not fit together; the header is continued.',
            ),
            'column_width_change': (
                'column_width_change',
                'A row or object continues into a column of a different width and cannot be reflowed there.',
            ),
        }
        code, message = messages.get(reason, ('pagination_rule', 'A pagination rule could not be satisfied.'))
        return _finding(code, message, ident)

    def commit(self, band_index, plan, page, top):
        self.findings.extend(plan['findings'])
        page_number = page['physical_number']
        for placement in plan['placements']:
            run, pos, fit = placement['run'], placement['pos'], placement['fit']
            if fit['end'] <= pos:
                continue
            units = run['units'][pos : fit['end']]
            y, x = top, page['body_left'] + placement['x']
            header = fit['header']
            info = run['tables'].get(units[0]['table']) if units[0]['table'] else None
            if header:
                if header[2]:
                    page['fragments'].append(
                        {
                            'kind': 'label',
                            'text': str(header[2]),
                            'rect': QRectF(x, y, placement['width'], 14),
                            'ids': [],
                        }
                    )
                    y += 14
                if header[1] > header[0]:
                    page['fragments'].append(
                        {
                            'document': run['qdoc'],
                            'source': QRectF(0, header[0], run['width'], header[1] - header[0]),
                            'rect': QRectF(x, y, run['width'], header[1] - header[0]),
                            'kind': 'table_header',
                            'ids': [],
                            'table': info and info['id'],
                        }
                    )
                    y += header[1] - header[0]
            source_top, source_bottom = units[0]['top'], units[-1]['bottom']
            edges = []
            if (
                info
                and units[0]['table']
                and (pos > 0 and run['units'][pos - 1]['table'] == units[0]['table'])
            ):
                edges.append(0)
            if (
                info
                and units[-1]['table']
                and fit['end'] < len(run['units'])
                and run['units'][fit['end']]['table'] == units[-1]['table']
            ):
                edges.append(source_bottom - source_top)
            ids = [i for unit in units for i in unit['ids']]
            fragment = {
                'document': run['qdoc'],
                'source': QRectF(0, source_top, run['width'], source_bottom - source_top),
                'rect': QRectF(x, y, run['width'], source_bottom - source_top),
                'kind': 'body',
                'ids': ids,
            }
            if edges:
                fragment['edges'] = [
                    (edge, info['left'], info['right'], info['color'], max(0.5, info['border']))
                    for edge in edges
                ]
            page['fragments'].append(fragment)
            for unit in units:
                for ident in unit['ids']:
                    if ident is None:
                        continue
                    self.positions.setdefault(ident, page_number)
                    self.anchors.setdefault(
                        ident,
                        {
                            'page': page_number,
                            'x': x,
                            'y': y + unit['top'] - source_top,
                            'width': run['width'],
                        },
                    )
            page['used'] = max(page['used'], y + source_bottom - source_top)
        for ident in plan['notes']:
            _, lines, height = self.note_doc(ident, page['body_width'])
            room = page['body_bottom'] - page['used'] - (self.notes_height(page, []) or 12)
            if height <= room + EPSILON:
                page['notes'].append((ident, 0, height))
                continue
            stop = 0
            for line_top, line_bottom in lines:
                if line_bottom > room + EPSILON and stop > 0:
                    break
                stop = line_bottom
            page['notes'].append((ident, 0, stop))
            self.carry.append((ident, stop))
        page['used'] = max(page['used'], plan['used'])

    def place_band(self, band_index, band, following):
        settings = band['settings']
        page = self.pages[-1] if self.pages else None
        start = band['start']
        if page is None or start != 'continuous' or not _same_geometry(page['layout'], settings):
            if page is not None:
                self.finish_notes(page)
            page = self.new_page(settings)
            if start in ('odd', 'even') and (page['number'] % 2 == 1) != (start == 'odd'):
                page['blank'] = True
                self.finish_notes(page)
                page = self.new_page(settings)
            top = page['body_top']
        else:
            top = page['used'] + (12 if page['fragments'] else 0)
        columns, findings = resolve_columns(
            settings, page['body_width'], f'section:{settings.get("_byax_section", 0) + 1}'
        )
        self.findings.extend(findings)
        self.column_height = page['body_bottom'] - page['body_top']
        run = self.run(band_index, (0, None, 0), columns[0][1])
        pos = 0
        if not run['units']:
            return
        if (
            band.get('full_width')
            and following is not None
            and following['start'] == 'continuous'
            and run['keeps'][-1]['with_next']
        ):
            next_columns, _ = resolve_columns(following['settings'], page['body_width'])
            next_run = self.run(band_index + 1, (0, None, 0), next_columns[0][1])
            keep = next_run['keeps'][0] if next_run['keeps'] else {'orphans': 1}
            first = next_run['units'][: max(1, keep['orphans'])]
            needed = (
                run['units'][-1]['bottom']
                - run['units'][0]['top']
                + (first[-1]['bottom'] - first[0]['top'] + 12 if first else 0)
            )
            if (
                top + needed > page['body_bottom'] - self.notes_height(page, []) + EPSILON
                and page['fragments']
            ):
                self.finish_notes(page)
                page = self.new_page(settings)
                top = page['body_top']
        balance = (
            len(columns) > 1
            and following is not None
            and following['start'] == 'continuous'
            and _same_geometry(following['settings'], settings)
            and (not isinstance(settings.get('columns'), dict) or settings['columns'].get('balance', True))
        )
        guard = 0
        while pos < len(run['units']):
            guard += 1
            if guard > 10000:
                raise ByaxError('resource_limit', 'Pagination exceeded 10,000 pages for one flow')
            plan = self.plan(band_index, run, pos, page, top, columns)
            if plan['done'] and balance:
                plan = self.balanced(band_index, run, pos, page, top, columns, plan)
            self.commit(band_index, plan, page, top)
            run, pos = plan['run'], plan['pos']
            if pos < len(run['units']):
                self.finish_notes(page)
                page = self.new_page(settings)
                top = page['body_top']

    def balanced(self, band_index, run, pos, page, top, columns, plan):
        high = plan['used'] - top
        low = max(1.0, high / len(columns) - 1)
        best = plan
        for _ in range(16):
            if high - low < 0.5:
                break
            middle = (low + high) / 2
            trial = self.plan(
                band_index, run, pos, page, top, columns, bottom=top + middle, fresh_allowed=False
            )
            if trial['done'] and not trial['findings']:
                best, high = trial, middle
            else:
                low = middle
        return best

    def resolve(self):
        bands, notes = self.bands()
        self.band_list = bands
        self.prepared = self.prepare(bands, notes)
        for index, band in enumerate(bands):
            band['blocks'] = self.prepared[index]
        self.column_height = 0
        for index, band in enumerate(bands):
            self.place_band(index, band, bands[index + 1] if index + 1 < len(bands) else None)
        placed = {item[0] for page in self.pages for item in page['notes']} | {item[0] for item in self.carry}
        for ident, note in self.notes.items():
            if ident not in placed and note not in self.orphaned:
                self.find(
                    'footnote_unplaced',
                    'Footnote reference could not be located in a line; the note is appended after body content.',
                    ident,
                )
                self.carry.append((ident, 0))
        self.carry.extend((note['id'], 0) for note in self.orphaned)
        page = self.pages[-1]
        self.place_carry(page, page['body_bottom'] - max(page['used'], page['body_top']) - 6, force=False)
        while self.carry:
            self.finish_notes(page)
            page = self.new_page(page['layout'])
            self.place_carry(page, page['body_bottom'] - page['body_top'])
        self.finish_notes(page)
        section_counts = {}
        for page in self.pages:
            section_counts[page['section']] = section_counts.get(page['section'], 0) + 1
        current, local = None, 0
        for page in self.pages:
            if page['section'] != current:
                current, local = page['section'], 0
            local += 1
            page.update(section_page=local, section_pages=section_counts[current])
            page['number_label'] = _format_page_number(
                page['number'], page['layout'].get('page_number_format', 'decimal')
            )
        breaks = [
            [
                (f['kind'], f['ids'][0] if f.get('ids') else None, f['ids'][-1] if f.get('ids') else None)
                for f in page['fragments']
            ]
            for page in self.pages
        ]
        return {
            'pages': self.pages,
            'positions': self.positions,
            'anchors': self.anchors,
            'findings': self.findings,
            'notes': {
                ident: {
                    'number': self.note_numbers[ident],
                    'label': self.note_labels[ident],
                    'page': self.positions.get(ident),
                    'reference': self.notes[ident]['attrs'].get('reference'),
                }
                for ident in self.notes
            },
            'breaks': breaks,
            '_overlay_anchor': self.overlay_anchor,
        }


def _resolve_pass(doc, labels):
    return _Resolver(doc, labels).resolve()


def _dependent_targets(doc):
    targets = set()
    for block in doc.get('blocks', []):
        if block.get('type') == 'toc':
            targets.update(b['id'] for b in doc['blocks'] if b.get('type') == 'heading')
        for mark in [
            *block.get('marks', []),
            *(
                m
                for row in block.get('rows', [])
                for cell in row.get('cells', [])
                for m in cell.get('marks', [])
            ),
        ]:
            if mark.get('type') in ('xref', 'cross_reference'):
                value = (
                    mark.get('value')
                    if isinstance(mark.get('value'), dict)
                    else {'target': mark.get('value')}
                )
                target = value.get('target')
                bookmark = doc.get('settings', {}).get('bookmarks', {}).get(target)
                targets.add(bookmark.get('target') if isinstance(bookmark, dict) else target)
    return targets


def resolve_pages(doc, output=None):
    """Physical pages for printing, preview and export: always typographically exact lines."""
    token = _GLYPH_CACHE.set({})
    try:
        with hand_drawn_marks(False):
            return _resolve_pages(doc, output)
    finally:
        # Scope font coverage to one resolve, so font installation/substitution
        # changes cannot leave stale answers in subsequent resolves.
        _GLYPH_CACHE.reset(token)


def _resolve_pages(doc, output=None):
    """Resolve physical sheets from the semantic model alone.

    Builds only unparented QTextDocuments, so it may run on a worker thread;
    the caller moves them to the GUI thread and attaches the result with
    ``adopt_resolved``.
    """
    ensure_app()
    view, visibility = output_view(doc, output)
    from .citation_render import citation_view

    view = citation_view(view)
    targets = _dependent_targets(view)
    labels = {}
    resolved = None
    for _ in range(4):
        resolved = _resolve_pass(view, labels)
        new_labels = {
            ident: resolved['pages'][number - 1]['number_label']
            for ident, number in resolved['positions'].items()
            if ident in targets
        }
        if not targets or new_labels == labels:
            break
        labels = new_labels
    else:
        resolved['findings'].append(
            _finding(
                'toc_unstable',
                'Resolved contents and cross-reference pages did not stabilize in four passes.',
                'toc',
            )
        )
    resolved['visibility'] = visibility
    resolved['objects'] = _object_placements(view, resolved)
    resolved['layers'] = layer_inspector(doc, resolved)
    return resolved


def resolved_documents(resolved):
    """Every QTextDocument a resolved layout holds, for handing it to another thread."""
    found, pending = {}, [resolved]
    while pending:
        value = pending.pop()
        if isinstance(value, QTextDocument):
            found[id(value)] = value
        elif isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return list(found.values())


def adopt_resolved(qdoc, resolved, output=None):
    cache = getattr(qdoc, 'byax_resolved_outputs', None)
    if cache is None:
        cache = qdoc.byax_resolved_outputs = {}
    obsolete = {'columns', 'section_geometry', 'footnote_placement', 'toc_unstable', 'pagination_policy'}
    combined = [
        f for f in getattr(qdoc, 'byax_editor_findings', qdoc.byax_findings) if f['code'] not in obsolete
    ] + resolved['findings']
    if not hasattr(qdoc, 'byax_editor_findings'):
        qdoc.byax_editor_findings = list(qdoc.byax_findings)
    unique = {(f['code'], f.get('target'), f['message']): f for f in combined}
    qdoc.byax_findings = list(unique.values())
    resolved['findings'] = qdoc.byax_findings
    cache[output] = resolved
    qdoc.byax_resolved = resolved
    return resolved


def resolve_layout(qdoc, doc, output=None):
    """Resolve physical sheets independently of the semantic editor document."""
    cache = getattr(qdoc, 'byax_resolved_outputs', None)
    if cache is not None and output in cache:
        qdoc.byax_resolved = cache[output]
        return cache[output]
    return adopt_resolved(qdoc, resolve_pages(doc, output), output)


def resolved_page_count(qdoc, doc=None, output=None):
    if doc is not None:
        resolve_layout(qdoc, doc, output)
    if hasattr(qdoc, 'byax_resolved'):
        return len(qdoc.byax_resolved['pages'])
    return qdoc.pageCount()


def resolved_page_size(qdoc, page_index):
    if hasattr(qdoc, 'byax_resolved'):
        page = qdoc.byax_resolved['pages'][page_index]
        return page['width'], page['height']
    return qdoc.pageSize().width(), qdoc.pageSize().height()


def block_lines(resolved, ident):
    """Physical placement of every rendered line of one semantic block: page, x, y, text."""
    result = []
    for number, page in enumerate(resolved['pages'], start=1):
        for fragment in page['fragments']:
            part = fragment.get('document')
            if part is None or fragment['kind'] == 'table_header' or ident not in part.byax_positions:
                continue
            source, rect = fragment['source'], fragment['rect']
            block = part.findBlock(part.byax_positions[ident])
            top = part.documentLayout().blockBoundingRect(block).top()
            layout = block.layout()
            for index in range(layout.lineCount()):
                line = layout.lineAt(index)
                y = top + line.y()
                if y >= source.top() - EPSILON and y + line.height() <= source.bottom() + EPSILON:
                    text = block.text()[line.textStart() : line.textStart() + line.textLength()]
                    result.append(
                        {
                            'page': number,
                            'x': rect.x(),
                            'y': rect.y() + y - source.top(),
                            'width': rect.width(),
                            'text': text,
                        }
                    )
    return result


def layout_signature(resolved):
    """Stable digest of break decisions shared by preview, PNG, PDF and print."""
    import hashlib
    import json

    data = [[page['width'], page['height'], page['number_label']] for page in resolved['pages']]
    return hashlib.sha256(json.dumps([data, resolved.get('breaks')], default=str).encode()).hexdigest()


def _wrap(attrs):
    value = str(attrs.get('wrap', 'inline')).replace('-', '_').replace(' ', '_')
    return {
        'above_and_below': 'above_below',
        'behind': 'behind_text',
        'front': 'in_front',
        'in_front_of_text': 'in_front',
    }.get(value, value)


def _anchor_policy(attrs):
    anchor = attrs.get('anchor')
    policy = attrs.get('position') or (anchor.get('policy') if isinstance(anchor, dict) else None)
    if policy:
        return str(policy).replace('-', '_')
    if 'page' in attrs and not anchor:
        return 'fixed_page'
    if 'x' in attrs or 'y' in attrs:
        return 'fix_on_anchor_page'
    return 'move_with_text'


def _place_object(attrs, own_anchor, resolved, size, ident, findings):
    """Resolve an overlay object's page rectangle from its inspectable anchor policy."""
    policy = _anchor_policy(attrs)
    anchor = attrs.get('anchor')
    target = anchor.get('target') if isinstance(anchor, dict) else anchor
    anchors = resolved.get('anchors', {})
    position = None
    if target:
        position = anchors.get(target)
        if position is None:
            findings.append(
                _finding(
                    'anchor_unresolved',
                    f'Anchor target {target!r} is missing; the object stays with its own document position.',
                    ident,
                )
            )
    if position is None:
        position = anchors.get(own_anchor) or {'page': 1, 'x': 72, 'y': 72}
    if policy == 'fixed_page':
        page = int(attrs.get('page', 1))
        x, y = float(attrs.get('x', 72)), float(attrs.get('y', 72))
    elif policy == 'fix_on_anchor_page':
        page = int(attrs.get('page', position['page'])) if not target else position['page']
        x, y = float(attrs.get('x', position['x'])), float(attrs.get('y', position['y']))
    else:
        page = position['page']
        x, y = (
            position['x'] + float(attrs.get('offset_x', 0)),
            position['y'] + float(attrs.get('offset_y', 0)),
        )
    pages = resolved['pages']
    page = max(1, min(len(pages), page))
    sheet = pages[page - 1]
    width, height = size
    if x < 0 or y < 0 or x + width > sheet['width'] + EPSILON or y + height > sheet['height'] + EPSILON:
        findings.append(
            _finding('object_clipped', 'Object extends beyond the page edge and is clipped in output.', ident)
        )
    return {'page': page, 'rect': [x, y, width, height], 'policy': policy, 'anchor': target or own_anchor}


def _object_placements(doc, resolved):
    objects, order = [], 0
    for block in doc.get('blocks', []):
        if block.get('type') != 'image':
            order += 1
            continue
        attrs = block.get('attrs', {})
        mode = _wrap(attrs)
        ident = block.get('id')
        entry = {
            'id': ident,
            'kind': 'image',
            'wrap': mode,
            'alt': attrs.get('alt', ''),
            'reading_order': order,
            'z': float(attrs.get('z', 1 if mode == 'in_front' else -1 if mode == 'behind_text' else 0)),
            'visible': attrs.get('visible', True) is not False,
            'asset': attrs.get('asset'),
            'opacity': float(attrs.get('opacity', 1)),
        }
        order += 1
        picture = None
        asset = doc.get('assets', {}).get(attrs.get('asset'), {})
        if asset.get('data'):
            try:
                picture = _decode_image(base64.b64decode(asset['data']))
            except (ValueError, TypeError):
                picture = None
        if mode in ('behind_text', 'in_front'):
            if picture is None or picture.isNull():
                resolved['findings'].append(
                    _finding('missing_image', 'Image bytes are unavailable; the overlay is not drawn.', ident)
                )
                continue
            pages = resolved['pages']
            own = resolved.get('_overlay_anchor', {}).get(ident)
            sheet = pages[0]
            size = _image_size(attrs, picture, sheet['width'], sheet['height'])
            entry.update(_place_object(attrs, own, resolved, size, ident, resolved['findings']))
        else:
            anchor = resolved.get('anchors', {}).get(ident)
            if anchor is None:
                continue
            entry.update(
                page=anchor['page'],
                rect=[anchor['x'], anchor['y'], None, None],
                policy='in_flow',
                anchor=ident,
            )
        objects.append(entry)
    return objects


def layer_inspector(doc, resolved=None):
    """Inspectable layer state: stacking, grouping, locks, anchors and output semantics."""
    layers = doc.get('layers', [])
    groups = {
        layer['id']: layer for layer in layers if layer.get('kind') == 'group' or layer.get('type') == 'group'
    }
    stacking = sorted(
        [layer for layer in layers if layer.get('id') not in groups],
        key=lambda item: (bool(item.get('foreground', item.get('z', 0) > 0)), item.get('z', 0)),
    )
    result = []
    for index, layer in enumerate(layers):
        group = groups.get(layer.get('group'), {})
        visible = layer.get('visible', True) is not False and group.get('visible', True) is not False
        state = (
            'redacted'
            if layer.get('redacted') or group.get('redacted')
            else 'excluded'
            if layer.get('excluded') or group.get('excluded')
            else 'visible'
            if visible
            else 'hidden'
        )
        entry = {
            'id': layer.get('id'),
            'name': layer.get('name', 'Layer'),
            'kind': 'group' if layer.get('id') in groups else layer.get('type', 'object'),
            'group': layer.get('group'),
            'locked': bool(layer.get('locked') or group.get('locked')),
            'visible': visible,
            'state': state,
            'plane': 'foreground' if layer.get('foreground', layer.get('z', 0) > 0) else 'background',
            'z': layer.get('z', 0),
            'stack_index': next((i for i, item in enumerate(stacking) if item is layer), None),
            'list_index': index,
            'anchor': layer.get('anchor'),
            'policy': _anchor_policy(layer)
            if layer.get('anchor')
            else ('fixed_page' if layer.get('page') else 'every_page'),
            'wrap': layer.get('wrap', 'none'),
            'outputs': layer.get('outputs', 'all'),
            'redaction': 'not_redacted' if state != 'redacted' else 'sanitized_output_only',
        }
        if layer.get('id') in groups:
            entry['members'] = [item.get('id') for item in layers if item.get('group') == layer.get('id')]
        result.append(entry)
    return result


def _page_applies(item, page_index, page):
    if item.get('page') is not None and not item.get('anchor') and int(item['page']) != page_index + 1:
        return False
    pages = item.get('pages', 'all')
    if isinstance(pages, list):
        return page_index + 1 in pages
    if pages == 'first':
        return page.get('section_page', page_index + 1) == 1
    if pages == 'not_first':
        return page.get('section_page', page_index + 1) != 1
    if pages in ('odd', 'even'):
        return (page.get('number', page_index + 1) % 2 == 1) == (pages == 'odd')
    if item.get('sections') is not None and page.get('section') not in item['sections']:
        return False
    return item.get('master') is None or page.get('layout', {}).get('master') == item['master']


def _draw_item(painter, doc, item, rect):
    painter.save()
    painter.setOpacity(max(0, min(1, float(item.get('opacity', 1)))))
    if item.get('asset') in doc.get('assets', {}):
        image = _decode_image(base64.b64decode(doc['assets'][item['asset']].get('data', '')))
        if not image.isNull():
            painter.drawImage(rect, image)
    if item.get('fill'):
        painter.fillRect(rect, QColor(item['fill']))
    if item.get('text'):
        painter.setPen(QColor(item.get('color', '#b6becb')))
        painter.setFont(QFont(item.get('font', 'DejaVu Sans'), int(item.get('font_size', 28))))
        painter.drawText(
            rect,
            Qt.TextFlag.TextWordWrap | _ALIGN.get(item.get('alignment', 'left'), Qt.AlignmentFlag.AlignLeft),
            item['text'],
        )
    painter.restore()


_ALIGN = {
    'left': Qt.AlignmentFlag.AlignLeft,
    'center': Qt.AlignmentFlag.AlignHCenter,
    'right': Qt.AlignmentFlag.AlignRight,
}


def _layer_rect(item, resolved, page_index):
    rect = QRectF(
        float(item.get('x', 72)),
        float(item.get('y', 72)),
        float(item.get('width', 300)),
        float(item.get('height', 100)),
    )
    target = item.get('anchor')
    if target and resolved is not None:
        anchor = resolved.get('anchors', {}).get(target)
        if anchor is None or anchor['page'] != page_index + 1:
            return None
        if _anchor_policy(item) == 'move_with_text':
            rect.moveTo(
                anchor['x'] + float(item.get('offset_x', 0)), anchor['y'] + float(item.get('offset_y', 0))
            )
    return rect


def _draw_layers(painter, doc, page, foreground, resolved=None, page_info=None):
    page_info = page_info or {}
    layers = doc.get('layers', [])
    groups = {
        layer['id']: layer for layer in layers if layer.get('kind') == 'group' or layer.get('type') == 'group'
    }
    ordered = sorted(enumerate(layers), key=lambda pair: (pair[1].get('z', 0), pair[0]))
    master = doc.get('layout', {}).get('masters', {}).get(page_info.get('layout', {}).get('master'), {})
    artwork = [
        dict(item, z=item.get('z', 1 if item.get('foreground') else -1))
        for item in master.get('artwork', [])
        if isinstance(item, dict)
    ]
    for item in sorted(artwork, key=lambda value: value['z']):
        if bool(item.get('foreground', False)) == foreground and _page_applies(item, page, page_info):
            _draw_item(
                painter,
                doc,
                item,
                QRectF(
                    float(item.get('x', 0)),
                    float(item.get('y', 0)),
                    float(item.get('width', 100)),
                    float(item.get('height', 40)),
                ),
            )
    for _, layer in ordered:
        if layer.get('id') in groups:
            continue
        group = groups.get(layer.get('group'), {})
        if not layer.get('visible', True) or layer.get('excluded') or layer.get('redacted'):
            continue
        if group and (group.get('visible', True) is False or group.get('excluded') or group.get('redacted')):
            continue
        if bool(layer.get('foreground', layer.get('z', 0) > 0)) != foreground:
            continue
        if not layer.get('anchor') and not _page_applies(layer, page, page_info):
            continue
        rect = _layer_rect(layer, resolved, page)
        if rect is None:
            continue
        item = dict(layer, opacity=float(layer.get('opacity', 1)) * float(group.get('opacity', 1)))
        _draw_item(painter, doc, item, rect)


def _draw_image_overlays(painter, doc, resolved, page_index, foreground):
    for item in resolved.get('objects', []):
        if item.get('wrap') not in ('behind_text', 'in_front') or (item['wrap'] == 'in_front') != foreground:
            continue
        if item.get('page') != page_index + 1 or not item.get('visible', True):
            continue
        asset = doc.get('assets', {}).get(item.get('asset'), {})
        image = _decode_image(base64.b64decode(asset.get('data', '')))
        if image.isNull():
            continue
        painter.save()
        painter.setOpacity(max(0, min(1, item.get('opacity', 1))))
        painter.drawImage(QRectF(*item['rect']), image)
        painter.restore()


def _draw_regions(painter, page, page_index, total):
    settings = page['layout']
    width, height, margins = geometry({'layout': settings})
    painter.setPen(QColor('#525866'))
    painter.setFont(QFont('DejaVu Sans', int(settings.get('region_font_size', 9))))
    kwargs = {
        'number': page['number'],
        'number_format': settings.get('page_number_format', 'decimal'),
        'section_page': page['section_page'],
        'section_pages': page['section_pages'],
    }
    body = width - margins['left'] - margins['right']
    header_top = float(settings.get('header_distance', 18))
    footer_bottom = float(
        settings.get('footer_distance', margins['bottom'] - 8 - max(18, margins['bottom'] - 20))
    )
    regions = (
        (
            'header',
            QRectF(margins['left'], header_top, body, max(18, margins['top'] - header_top - 6)),
            settings.get('header_align', 'left'),
        ),
        (
            'footer',
            QRectF(
                margins['left'],
                height - footer_bottom - max(18, margins['bottom'] - 20),
                body,
                max(18, margins['bottom'] - 20),
            ),
            settings.get('footer_align', 'center'),
        ),
    )
    for key, rect, align in regions:
        parts = _region_parts(settings.get(key), page_index, total, default_align=align, **kwargs)
        for side, text in parts.items():
            painter.drawText(rect, _ALIGN[side] | Qt.AlignmentFlag.AlignVCenter, text)


def draw_page(painter, qdoc, doc, page_index, output=None):
    resolved = resolve_layout(qdoc, doc, output)
    view = output_view(doc, output)[0]
    page = resolved['pages'][page_index]
    width, height, settings = page['width'], page['height'], page['layout']
    painter.save()
    painter.fillRect(QRectF(0, 0, width, height), QColor(settings.get('background', 'white')))
    _draw_layers(painter, view, page_index, False, resolved, page)
    _draw_image_overlays(painter, view, resolved, page_index, False)
    for fragment in page['fragments']:
        rect = fragment['rect']
        if fragment['kind'] == 'label':
            painter.save()
            painter.setPen(QColor('#525866'))
            painter.setFont(QFont('DejaVu Sans', 8))
            painter.drawText(
                rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fragment['text']
            )
            painter.restore()
            continue
        source, part = fragment['source'], fragment['document']
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.x() - source.x(), rect.y() - source.y())
        # Shrinking the paint clip keeps boundary lines out of the neighbouring fragment's PDF text.
        # Publication colors belong to the document, independently of the desktop theme.
        context = QAbstractTextDocumentLayout.PaintContext()
        context.clip = source.adjusted(0, EPSILON, 0, -EPSILON)
        context.palette.setColor(QPalette.Text, QColor('black'))
        context.palette.setColor(QPalette.Base, QColor('white'))
        part.documentLayout().draw(painter, context)
        painter.restore()
        for edge, left, right, color, pen_width in fragment.get('edges', []):
            painter.save()
            pen = painter.pen()
            pen.setColor(QColor(color))
            pen.setWidthF(pen_width)
            painter.setPen(pen)
            y = rect.y() + edge
            painter.drawLine(QPointF(rect.x() + left, y), QPointF(rect.x() + right, y))
            painter.restore()
        if (
            fragment['kind'] == 'footnotes'
            and not fragment.get('continued')
            and fragment is next(f for f in page['fragments'] if f['kind'] == 'footnotes')
        ):
            painter.save()
            painter.setPen(QColor('#8b9098'))
            painter.drawLine(
                rect.topLeft() - QPointF(0, 6), rect.topLeft() + QPointF(min(120, rect.width()), -6)
            )
            painter.restore()
    _draw_regions(painter, page, page_index, len(resolved['pages']))
    _draw_layers(painter, view, page_index, True, resolved, page)
    _draw_image_overlays(painter, view, resolved, page_index, True)
    painter.restore()


def _selected_pages(pages, total):
    if pages is None:
        return list(range(total))
    if isinstance(pages, str):
        result = []
        for part in pages.split(','):
            bounds = part.strip().split('-')
            if len(bounds) > 2 or not all(bound.isdigit() for bound in bounds):
                raise ValueError('Invalid page range')
            first, last = int(bounds[0]), int(bounds[-1])
            if first < 1 or last < first or last > total:
                raise ByaxError('invalid_input', f'Page range must be within 1..{total}')
            result.extend(range(first, last + 1))
    else:
        result = list(pages)
    if not result or any(not isinstance(p, int) or p < 1 or p > total for p in result):
        raise ValueError(f'Page selection must be within 1..{total}')
    return sorted({p - 1 for p in result})


def _report(qdoc, outputs, selected):
    resolved = getattr(qdoc, 'byax_resolved', {})
    report = {
        'schema_version': 1,
        'execution': 'completed',
        'validation': 'not_run',
        'page_count': resolved_page_count(qdoc),
        'pages': [p + 1 for p in selected],
        'outputs': outputs,
        'findings': qdoc.byax_findings,
        'losses': qdoc.byax_findings,
        'coverage': {
            'content': 'Qt text/table flow',
            'structure': 'selectable text in PDF',
            'visual': 'shared Qt layout; see findings',
            'review': 'overlays excluded',
        },
        'reproduction': {
            'engine': 'Qt6 QTextDocument',
            'units': 'points',
            'default_font': qdoc.defaultFont().family(),
            'font_substitutions': [f for f in qdoc.byax_findings if f['code'] == 'font_substitution'],
            'font_embedding': 'Qt PDF font subset embedding subject to font permissions',
        },
    }
    if resolved:
        report['layout_signature'] = layout_signature(resolved)
        report['visibility'] = resolved.get('visibility')
        report['objects'] = [
            {k: v for k, v in item.items() if k != 'asset'} for item in resolved.get('objects', [])
        ]
        report['notes'] = resolved.get('notes', {})
        report['reproduction']['reading_order'] = 'semantic block order; overlays and layers do not change it'
    return report


def render(doc, out_dir, pages=None, output=None):
    qdoc = build_document(doc)
    selected = _selected_pages(pages, resolved_page_count(qdoc, doc, output))
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    outputs = []
    for page in selected:
        width, height = resolved_page_size(qdoc, page)
        if math.ceil(width * 2) * math.ceil(height * 2) > MAX_IMAGE_PIXELS:
            raise ByaxError('resource_limit', 'Preview raster exceeds 40 million pixels')
        image = QImage(math.ceil(width * 2), math.ceil(height * 2), QImage.Format.Format_ARGB32)
        if image.isNull():
            raise ByaxError('resource_limit', 'Unable to allocate preview image')
        image.fill(QColor('white'))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(2, 2)
        try:
            draw_page(painter, qdoc, doc, page, output)
        finally:
            painter.end()
        path = destination / f'page-{page + 1:04d}.png'
        if not image.save(str(path)):
            raise OSError(f'Unable to write {path}')
        outputs.append(str(path.resolve()))
    return _report(qdoc, outputs, selected)


def export_pdf(doc, path, pages=None, output=None):
    qdoc = build_document(doc)
    selected = _selected_pages(pages, resolved_page_count(qdoc, doc, output))
    width, height, _ = geometry(doc)
    writer = QPdfWriter(str(path))
    writer.setResolution(72)
    writer.setPageSize(QPageSize(QSizeF(width, height), QPageSize.Unit.Point))
    page_layout = writer.pageLayout()
    page_layout.setMode(QPageLayout.Mode.FullPageMode)
    writer.setPageLayout(page_layout)
    writer.setTitle(doc.get('metadata', {}).get('title', ''))
    writer.setCreator('ByAuthorX')
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f'Unable to open PDF output {path}')
    try:
        for i, page in enumerate(selected):
            pw, ph = resolved_page_size(qdoc, page)
            writer.setPageSize(QPageSize(QSizeF(pw, ph), QPageSize.Unit.Point))
            if i:
                writer.newPage()
            draw_page(painter, qdoc, doc, page, output)
    finally:
        painter.end()
    del writer
    return _report(qdoc, [str(Path(path).resolve())], selected)


def check_layout(doc):
    """Fresh physical layout checks, independent of prior cached reports."""
    try:
        qdoc = build_document(doc)
        resolve_layout(qdoc, doc)
    except ByaxError as exc:
        return {
            'findings': [{'code': exc.code, 'severity': 'error', 'message': exc.message, 'target': 'layout'}],
            'coverage': [{'suite': 'print', 'rule': 'resolved_geometry', 'status': 'completed'}],
        }
    findings = list(qdoc.byax_findings)
    incomplete = {'toc_unstable', 'unknown_block', 'image_wrap', 'column_width_change'}
    return {
        'findings': findings,
        'coverage': [
            {
                'suite': 'print',
                'rule': 'resolved_geometry',
                'status': 'incomplete' if any(f['code'] in incomplete for f in findings) else 'completed',
                'scope': 'selected_document',
                'page_count': resolved_page_count(qdoc),
            }
        ],
    }


def _line_rule(value):
    """Widow/orphan settings are line counts; ``True`` means the usual two."""
    if value is True:
        return 2
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0
    return max(0, min(int(value), 50))


def _line_pages(qdoc, block, height):
    lines = block.layout()
    top = qdoc.documentLayout().blockBoundingRect(block).top()
    return [int((top + lines.lineAt(index).y()) // height) for index in range(lines.lineCount())]


def _break_before(block):
    fmt = block.blockFormat()
    if fmt.pageBreakPolicy() & QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore:
        return False
    fmt.setPageBreakPolicy(fmt.pageBreakPolicy() | QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore)
    QTextCursor(block).setBlockFormat(fmt)
    block.document().documentLayout().documentSize()
    return True


def _apply_keep_rules(qdoc, doc):
    """Resolve keep and widow/orphan rules by adding derived page breaks.

    QTextBlockFormat.nonBreakableLines disables line wrapping, so it is not a
    pagination keep flag. Derived breaks preserve normal word wrapping instead.
    Text is never dropped: a rule that cannot be met yields a located finding.
    """
    _, height, margins = geometry(doc)
    available = height - margins['top'] - margins['bottom']
    flowed = []
    for source in doc.get('blocks', []):
        ident = source.get('id')
        if ident in qdoc.byax_positions and source.get('type') in ('heading', 'paragraph'):
            values = {**_style(doc, source.get('style', 'body')), **source.get('attrs', {})}
            flowed.append((ident, source, values))
    for index, (ident, source, values) in enumerate(flowed):
        together = values.get('keep_together', source.get('type') == 'heading')
        with_next = values.get('keep_with_next', source.get('type') == 'heading')
        block = qdoc.findBlock(qdoc.byax_positions[ident])
        lines = block.layout()
        if lines.lineCount() == 0:
            continue
        natural = sum(lines.lineAt(line).height() for line in range(lines.lineCount()))
        if together or with_next:
            line_pages = _line_pages(qdoc, block, height)
            split = line_pages[0] != line_pages[-1]
            following = block.next()
            stranded = False
            needed = natural
            if with_next and following.isValid():
                # Qt can push a following block's first line past its block
                # rectangle's top, so compare the first rendered line.
                if following.layout().lineCount():
                    stranded = line_pages[-1] != _line_pages(qdoc, following, height)[0]
                else:
                    stranded = line_pages[-1] != int(
                        qdoc.documentLayout().blockBoundingRect(following).top() // height
                    )
                if following.layout().lineCount():
                    needed += (
                        following.layout().lineAt(0).height()
                        + following.blockFormat().topMargin()
                        + block.blockFormat().bottomMargin()
                    )
            if together and natural > available or with_next and needed > available:
                qdoc.byax_findings.append(
                    _finding(
                        'keep_rule_unsatisfiable',
                        'Requested keep rule exceeds the available page/column height; all text remains in flow.',
                        ident,
                    )
                )
            elif together and split or with_next and stranded:
                if not _break_before(block):
                    qdoc.byax_findings.append(
                        _finding(
                            'keep_rule_unsatisfiable',
                            'Keep rule remains unsatisfied after a page/column break.',
                            ident,
                        )
                    )
                continue
        orphans = _line_rule(values.get('orphan_control'))
        widows = _line_rule(values.get('widow_control'))
        if not orphans and not widows:
            continue
        line_pages = _line_pages(qdoc, block, height)
        if line_pages[0] == line_pages[-1]:
            continue
        orphaned = line_pages.count(line_pages[0]) < orphans
        widowed = line_pages.count(line_pages[-1]) < widows
        if not orphaned and not widowed:
            continue
        if widowed and not orphaned and natural > available:
            # Moving lines individually would need a split block; moving the
            # whole paragraph cannot help when it is longer than a page.
            qdoc.byax_findings.append(
                _finding(
                    'widow_unsatisfiable',
                    f'Paragraph is longer than a page/column; its last page keeps {line_pages.count(line_pages[-1])} line(s), fewer than the requested {widows}.',
                    ident,
                )
            )
            continue
        # Move the paragraph, together with any directly preceding blocks that
        # keep with it on the same page, so headings are not stranded.
        target, first = block, index
        while first > 0:
            previous_ident, previous, previous_values = flowed[first - 1]
            previous_block = qdoc.findBlock(qdoc.byax_positions[previous_ident])
            if previous_block.next() != target or not previous_values.get(
                'keep_with_next', previous.get('type') == 'heading'
            ):
                break
            if _line_pages(qdoc, previous_block, height)[-1] != line_pages[0]:
                break
            target, first = previous_block, first - 1
        if not _break_before(target):
            qdoc.byax_findings.append(
                _finding(
                    'widow_orphan_unsatisfiable',
                    'Widow/orphan rule could not be met by moving this paragraph to the next page/column.',
                    ident,
                )
            )
            continue
        line_pages = _line_pages(qdoc, block, height)
        if line_pages[0] != line_pages[-1] and (
            line_pages.count(line_pages[0]) < orphans or line_pages.count(line_pages[-1]) < widows
        ):
            qdoc.byax_findings.append(
                _finding(
                    'widow_orphan_unsatisfiable',
                    'Widow/orphan rule remains unmet after moving this paragraph to the next page/column.',
                    ident,
                )
            )
