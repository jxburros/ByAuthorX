"""Shared Qt text shaping, pagination and controlled output in physical points.

Qt supplies Unicode shaping and semantic flowing tables. Features Qt cannot
faithfully resolve are disclosed as located findings, never called validated.
"""
from __future__ import annotations

import base64
import copy
import math
import os
import unicodedata
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QImage,
    QImageReader,
    QPageLayout,
    QPageSize,
    QPainter,
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
    QTextTableFormat,
)
from PySide6.QtWidgets import QApplication

from .errors import ByaxError

MAX_IMAGE_PIXELS = 40_000_000
MAX_PAGE_POINTS = 14_400

BLOCK_ID_PROPERTY = int(QTextFormat.Property.UserProperty) + 1
_APP = None


def ensure_app():
    global _APP
    if QApplication.instance() is None:
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _APP = QApplication([])
    from .fonts import register_bundled_fonts
    register_bundled_fonts()
    return QApplication.instance()


def _finding(code, message, target=None):
    return {"code": code, "severity": "warning", "message": message, "target": target}


def geometry(doc):
    layout = doc.get("layout", {})
    paper = layout.get("paper", "Letter")
    width, height = (595.276, 841.89) if paper == "A4" else (612, 792)
    width, height = float(layout.get("width", width)), float(layout.get("height", height))
    if layout.get("orientation") == "landscape" and width < height:
        width, height = height, width
    margins = {key: float(layout.get("margins", {}).get(key, 72)) for key in ("top", "right", "bottom", "left")}
    if not all(math.isfinite(v) and v >= 0 for v in [width, height, *margins.values()]):
        raise ByaxError("invalid_input", "Page geometry must contain finite positive dimensions")
    if width > MAX_PAGE_POINTS or height > MAX_PAGE_POINTS:
        raise ByaxError("resource_limit", "Page dimensions exceed 14,400 physical points (200 inches)")
    if width <= margins["left"] + margins["right"] or height <= margins["top"] + margins["bottom"]:
        raise ByaxError("invalid_input", "Page margins leave no body area")
    return width, height, margins


def _style(doc, name, seen=None):
    seen = set() if seen is None else seen
    if name in seen:
        return {}
    seen.add(name)
    own = doc.get("styles", {}).get(name, {})
    if not isinstance(own, dict):
        return {}
    result = _style(doc, own["based_on"], seen) if own.get("based_on") else {}
    result.update(own)
    return result


def _char_format(values):
    fmt = QTextCharFormat()
    for key, value in values.items():
        if key in ("bold", "strong"):
            fmt.setFontWeight(QFont.Weight.Bold if value else QFont.Weight.Normal)
        elif key in ("italic", "emphasis"):
            fmt.setFontItalic(bool(value))
        elif key == "underline":
            fmt.setFontUnderline(bool(value))
        elif key in ("strikethrough", "strike"):
            fmt.setFontStrikeOut(bool(value))
        elif key in ("font", "font_family"):
            fmt.setFontFamilies([str(value)])
        elif key in ("font_size", "size"):
            fmt.setFontPointSize(float(value))
        elif key in ("color", "foreground"):
            fmt.setForeground(QColor(value))
        elif key in ("highlight", "background"):
            fmt.setBackground(QColor(value))
        elif key == "superscript" and value:
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)
        elif key == "subscript" and value:
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
        elif key in ("link", "href"):
            if _safe_link(str(value)):
                fmt.setAnchor(True)
                fmt.setAnchorHref(str(value))
                fmt.setForeground(QColor("#3156a0"))
                fmt.setFontUnderline(True)
        elif key == "code" and value:
            fmt.setFontFamilies(["DejaVu Sans Mono"])
            fmt.setBackground(QColor("#eef0f2"))
    return fmt


def _safe_link(value):
    return value.startswith(("https://", "http://", "mailto:", "#")) or ":" not in value


def _insert_marked(cursor, text, marks, base=None):
    start = cursor.position()
    cursor.insertText(text.replace("\n", "\u2028"), base or QTextCharFormat())
    for mark in marks:
        lo, hi = max(0, int(mark.get("start", 0))), min(len(text), int(mark.get("end", len(text))))
        if hi <= lo:
            continue
        selection = QTextCursor(cursor.document())
        # Semantic offsets are Unicode scalar values; Qt counts UTF-16 code units.
        selection.setPosition(start + len(text[:lo].encode("utf-16-le")) // 2)
        selection.setPosition(start + len(text[:hi].encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor)
        selection.mergeCharFormat(_char_format({mark.get("type"): mark.get("value", True)}))



def _decode_image(raw):
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0:
        return QImage()
    if size.width()*size.height() > MAX_IMAGE_PIXELS:
        raise ByaxError("resource_limit", "Decoded image exceeds 40 million pixels")
    if bytes(reader.format()).lower() not in {b"png", b"jpg", b"jpeg", b"gif", b"bmp", b"webp", b"ico", b"tiff"}:
        return QImage()
    reader.setAllocationLimit(160)
    return reader.read()


def _check_glyphs(qdoc):
    checked = {}
    block = qdoc.firstBlock()
    while block.isValid():
        missing = set()
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            if fragment.isValid():
                font = fragment.charFormat().font().resolve(qdoc.defaultFont())
                metrics = QFontMetrics(font)
                for character in set(fragment.text()):
                    if unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} or character == "\ufffc":
                        continue
                    key = (font.toString(), ord(character))
                    if key not in checked:
                        checked[key] = metrics.inFontUcs4(ord(character))
                    if not checked[key]:
                        missing.add(f"U+{ord(character):04X}")
            fragments += 1
        if missing:
            target = block.blockFormat().property(BLOCK_ID_PROPERTY)
            qdoc.byax_findings.append(_finding("missing_glyph", "Resolved font fallback cannot draw " + ", ".join(sorted(missing)) + ".", target))
        block = block.next()


def build_document(doc: dict, _toc_pages=None) -> QTextDocument:
    ensure_app()
    width, height, margins = geometry(doc)
    result = QTextDocument()
    # A fixed physical paint device decouples typography from preview raster DPI.
    device = QImage(1, 1, QImage.Format.Format_ARGB32)
    device.setDotsPerMeterX(2835)
    device.setDotsPerMeterY(2835)
    result._byax_device = device
    result.documentLayout().setPaintDevice(device)
    result.setDefaultFont(QFont("DejaVu Serif", 12))
    result.setPageSize(QSizeF(width, height))
    result.setDocumentMargin(0)
    frame_fmt = result.rootFrame().frameFormat()
    frame_fmt.setTopMargin(margins["top"])
    frame_fmt.setBottomMargin(margins["bottom"])
    frame_fmt.setLeftMargin(margins["left"])
    frame_fmt.setRightMargin(margins["right"])
    result.rootFrame().setFrameFormat(frame_fmt)
    result.byax_findings = []
    result.byax_positions = {}
    cursor = QTextCursor(result)
    headings = [b for b in doc.get("blocks", []) if b.get("type") == "heading"]
    families = set(QFontDatabase.families())
    loaded_assets = set()
    for key, asset in doc.get("assets", {}).items():
        if not asset.get("mime", "").startswith("image/"):
            continue
        try:
            raw = base64.b64decode(asset.get("data", ""), validate=True)
            picture = _decode_image(raw)
            if not picture.isNull():
                result.addResource(QTextDocument.ResourceType.ImageResource, QUrl("byax:" + key), picture)
                loaded_assets.add(key)
        except (ValueError, TypeError):
            pass
    list_objects = {}
    first = True
    for block in doc.get("blocks", []):
        attrs = block.get("attrs", {})
        if attrs.get("excluded") or attrs.get("redacted"):
            continue
        kind, ident = block.get("type", "paragraph"), block.get("id")
        if not first:
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        first = False
        bf = QTextBlockFormat()
        bf.setProperty(BLOCK_ID_PROPERTY, ident)
        values = _style(doc, block.get("style", "body"))
        values.update(attrs)
        if kind == "heading":
            level = max(1, min(6, int(attrs.get("level", 1))))
            bf.setHeadingLevel(level)
            bf.setTopMargin(12)
            bf.setBottomMargin(6)
            values = {"bold": True, "font_size": [24, 19, 16, 14, 12, 12][level - 1], **values}
        else:
            bf.setBottomMargin(float(values.get("space_after", 6)))
        bf.setTopMargin(float(values.get("space_before", bf.topMargin())))
        alignments = {"left": Qt.AlignmentFlag.AlignLeft, "right": Qt.AlignmentFlag.AlignRight,
                      "center": Qt.AlignmentFlag.AlignHCenter, "justify": Qt.AlignmentFlag.AlignJustify}
        bf.setAlignment(alignments.get(values.get("alignment", "left"), Qt.AlignmentFlag.AlignLeft))
        bf.setLeftMargin(float(values.get("indent", 0)))
        bf.setTextIndent(float(values.get("first_line_indent", 0)))
        if values.get("line_spacing"):
            bf.setLineHeight(float(values["line_spacing"]) * 100, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        if values.get("tabs"):
            tabs = []
            for position in values["tabs"]:
                tab = QTextOption.Tab()
                tab.position = float(position)
                tabs.append(tab)
            bf.setTabPositions(tabs)
        if kind in ("page_break", "section_break") or attrs.get("page_break_before") or attrs.get("column_break_before"):
            bf.setPageBreakPolicy(QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore)
            if kind == "section_break" and any(k in attrs for k in ("layout", "columns", "width", "height")):
                result.byax_findings.append(_finding("section_geometry", "This renderer uses document page geometry; section-specific geometry/columns require a different layout backend.", ident))
        cursor.setBlockFormat(bf)
        result.byax_positions[ident] = cursor.position()
        font = values.get("font_family", values.get("font"))
        if font and font not in families:
            result.byax_findings.append(_finding("font_substitution", f"Requested font {font!r} is unavailable; Qt substitutes a local font.", ident))
        if kind == "table":
            rows = block.get("rows", [])
            columns = max((len(row.get("cells", [])) for row in rows), default=0)
            if rows and columns:
                tf = QTextTableFormat()
                tf.setBorder(float(attrs.get("border_width", 0.5)))
                tf.setBorderBrush(QColor(attrs.get("border_color", "#808080")))
                if attrs.get("background"):
                    tf.setBackground(QColor(attrs["background"]))
                if attrs.get("column_widths"):
                    tf.setColumnWidthConstraints([QTextLength(QTextLength.Type.FixedLength, float(value)) for value in attrs["column_widths"]])
                if attrs.get("width"):
                    tf.setWidth(QTextLength(QTextLength.Type.FixedLength, float(attrs["width"])))
                tf.setAlignment(alignments.get(attrs.get("alignment", "left"), Qt.AlignmentFlag.AlignLeft))
                tf.setCellPadding(float(attrs.get("padding", 4)))
                tf.setCellSpacing(0)
                tf.setHeaderRowCount(int(attrs.get("header_rows", 0)))
                table = cursor.insertTable(len(rows), columns, tf)
                for r, row in enumerate(rows):
                    for c, cell in enumerate(row.get("cells", [])):
                        cc = table.cellAt(r, c).firstCursorPosition()
                        cbf = cc.blockFormat()
                        cbf.setProperty(BLOCK_ID_PROPERTY, cell.get("id"))
                        cc.setBlockFormat(cbf)
                        result.byax_positions[cell.get("id")] = cc.position()
                        _insert_marked(cc, cell.get("text", ""), cell.get("marks", []), _char_format({"bold": r < attrs.get("header_rows", 0), **cell.get("attrs", {})}))
                        cell_attrs = cell.get("attrs", {})
                        if cell_attrs.get("background"):
                            cf = table.cellAt(r, c).format()
                            cf.setBackground(QColor(cell_attrs["background"]))
                            table.cellAt(r, c).setFormat(cf)
                for r, row in enumerate(rows):
                    for c, cell in enumerate(row.get("cells", [])):
                        ca = cell.get("attrs", {})
                        if ca.get("rowspan", 1) > 1 or ca.get("colspan", 1) > 1:
                            table.mergeCells(r, c, min(int(ca.get("rowspan", 1)), len(rows)-r), min(int(ca.get("colspan", 1)), columns-c))
                cursor = table.lastCursorPosition()
                cursor.movePosition(QTextCursor.MoveOperation.NextBlock)
            continue
        if kind == "image":
            key = attrs.get("asset")
            if key in loaded_assets:
                imf = QTextImageFormat()
                imf.setName("byax:" + key)
                image = result.resource(QTextDocument.ResourceType.ImageResource, QUrl("byax:" + key))
                iw = float(attrs.get("width", min(image.width(), width - margins["left"] - margins["right"])))
                ih = float(attrs.get("height", iw * image.height() / max(image.width(), 1)))
                imf.setWidth(iw)
                imf.setHeight(ih)
                imf.setProperty(QTextFormat.Property.ImageAltText, attrs.get("alt", ""))
                if _wrap(attrs) == "square":
                    ff = QTextFrameFormat()
                    ff.setPosition(QTextFrameFormat.Position.FloatRight if attrs.get("side") == "right" else QTextFrameFormat.Position.FloatLeft)
                    ff.setWidth(iw)
                    ff.setMargin(float(attrs.get("wrap_margin", 6)))
                    frame = cursor.insertFrame(ff)
                    image_cursor = frame.firstCursorPosition()
                    image_bf = image_cursor.blockFormat()
                    image_bf.setProperty(BLOCK_ID_PROPERTY, ident)
                    image_cursor.setBlockFormat(image_bf)
                    image_cursor.insertImage(imf)
                    cursor.setPosition(frame.lastPosition()+1)
                else:
                    cursor.insertImage(imf)
                if iw > width-margins["left"]-margins["right"] or ih > height-margins["top"]-margins["bottom"]:
                    result.byax_findings.append(_finding("image_overflow", "Image dimensions exceed the available flow area.", ident))
            else:
                cursor.insertText("[Image: " + attrs.get("alt", "missing asset") + "]")
                result.byax_findings.append(_finding("missing_image", "Image bytes are unavailable; an alternative-text placeholder is shown.", ident))
            if _wrap(attrs) not in ("inline", "square", "above_below", "behind_text", "in_front"):
                result.byax_findings.append(_finding("image_wrap", "Unknown wrapping policy; image is rendered inline.", ident))
            continue
        if kind == "toc":
            for i, heading in enumerate(headings):
                if i:
                    cursor.insertBlock()
                page = (_toc_pages or {}).get(heading["id"], "…")
                cursor.insertText("  " * (int(heading.get("attrs", {}).get("level", 1)) - 1) + heading.get("text", "") + "    " + str(page))
            continue
        if kind == "footnote":
            result.byax_findings.append(_finding("footnote_placement", "Footnote text is retained in document flow; bottom-of-page placement and continuation are not resolved.", ident))
            values["font_size"] = 10
        if values.get("list"):
            listing = values["list"]
            list_kind = listing.get("kind", "bullet") if isinstance(listing, dict) else listing
            level = int(listing.get("level", 1) if isinstance(listing, dict) else values.get("level", 1))
            lf = QTextListFormat()
            lf.setStyle(QTextListFormat.Style.ListDecimal if list_kind in ("ordered", "numbered", "decimal") else QTextListFormat.Style.ListDisc)
            lf.setIndent(max(1, level))
            list_key = (list_kind, level)
            if values.get("restart") or list_key not in list_objects:
                if hasattr(lf, "setStart"):
                    lf.setStart(int(values.get("start", 1)))
                list_objects[list_key] = cursor.createList(lf)
            else:
                list_objects[list_key].add(cursor.block())
        _insert_marked(cursor, block.get("text", ""), block.get("marks", []), _char_format(values))
        if kind not in ("paragraph", "heading", "footnote", "page_break", "section_break"):
            result.byax_findings.append(_finding("unknown_block", f"Unsupported block {kind!r} rendered as text.", ident))
    if doc.get("layout", {}).get("columns", 1) != 1:
        result.byax_findings.append(_finding("columns", "Multi-column flow is not resolved; output uses one column.", "layout"))
    result.documentLayout().documentSize()
    _apply_keep_rules(result, doc)
    _check_glyphs(result)
    if _toc_pages is None and any(b.get("type") == "toc" for b in doc.get("blocks", [])):
        pages = {h["id"]: _block_page(result, result.byax_positions.get(h["id"], 0)) for h in headings}
        # Iterate until references stabilize, with an explicit bounded fallback.
        for _ in range(4):
            rebuilt = build_document(doc, pages)
            new_pages = {h["id"]: _block_page(rebuilt, rebuilt.byax_positions.get(h["id"], 0)) for h in headings}
            if new_pages == pages:
                return rebuilt
            result, pages = rebuilt, new_pages
        result.byax_findings.append(_finding("toc_unstable", "Contents page numbers did not stabilize after four layout passes.", "toc"))
    return result


def _block_page(qdoc, position):
    rect = qdoc.documentLayout().blockBoundingRect(qdoc.findBlock(position))
    return int(rect.top() // qdoc.pageSize().height()) + 1


def _format_page_number(number, style="decimal"):
    style = str(style).lower().replace("-", "_")
    aliases = {"roman": "lower_roman", "upperroman": "upper_roman", "lowerroman": "lower_roman", "upper_alphabetic": "upper_alpha", "lower_alphabetic": "lower_alpha", "alphabetic": "lower_alpha"}
    style = aliases.get(style, style)
    if style == "decimal":
        return str(number)
    if number < 1 or number > 999999:
        raise ByaxError("invalid_input", "Nondecimal page numbers must be within 1..999999")
    if style in ("upper_roman", "lower_roman"):
        value, result = number, []
        for divisor, token in ((1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")):
            count, value = divmod(value, divisor)
            result.append(token*count)
        result = "".join(result)
        return result.lower() if style == "lower_roman" else result
    if style in ("upper_alpha", "lower_alpha"):
        value, result = number, ""
        while value:
            value, remainder = divmod(value-1, 26)
            result = chr(65+remainder) + result
        return result.lower() if style == "lower_alpha" else result
    raise ByaxError("invalid_input", "Unsupported page numbering format", format=style)


def _region(value, page, count, *, number=None, number_format="decimal", section_page=None, section_pages=None):
    number = page+1 if number is None else number
    section_page = page+1 if section_page is None else section_page
    if isinstance(value, dict):
        variant = "first" if section_page == 1 else "even" if number % 2 == 0 else "odd"
        value = value.get(variant, value.get("text", ""))
    return (str(value or "").replace("{page}", _format_page_number(number, number_format))
            .replace("{pages}", str(count)).replace("{section_page}", str(section_page))
            .replace("{section_pages}", str(section_pages if section_pages is not None else count)))


def _draw_simple_page(painter: QPainter, qdoc: QTextDocument, doc: dict, page_index: int):
    """Paint a page at origin, in points, including repeated regions and layers."""
    width, height, margins = geometry(doc)
    painter.save()
    painter.fillRect(QRectF(0, 0, width, height), QColor(doc.get("layout", {}).get("background", "white")))
    _draw_layers(painter, doc, page_index, foreground=False)
    painter.save()
    painter.setClipRect(QRectF(0, margins["top"], width, height - margins["top"] - margins["bottom"]))
    painter.translate(0, -page_index * height)
    qdoc.drawContents(painter, QRectF(0, page_index * height, width, height))
    painter.restore()
    painter.setPen(QColor("#525866"))
    painter.setFont(QFont("DejaVu Sans", 9))
    layout = doc.get("layout", {})
    painter.drawText(QRectF(margins["left"], 18, width-margins["left"]-margins["right"], max(18, margins["top"]-24)), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, _region(layout.get("header"), page_index, qdoc.pageCount()))
    painter.drawText(QRectF(margins["left"], height-margins["bottom"]+8, width-margins["left"]-margins["right"], max(18, margins["bottom"]-20)), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, _region(layout.get("footer"), page_index, qdoc.pageCount()))
    _draw_layers(painter, doc, page_index, foreground=True)
    if hasattr(qdoc, "byax_resolved"):
        _draw_image_overlays(painter, doc, qdoc.byax_resolved, page_index, True)
    painter.restore()


def _draw_layers(painter, doc, page, foreground):
    for layer in sorted(doc.get("layers", []), key=lambda item: item.get("z", 0)):
        if not layer.get("visible", True) or layer.get("excluded") or layer.get("redacted"):
            continue
        if bool(layer.get("foreground", layer.get("z", 0) > 0)) != foreground:
            continue
        if layer.get("page") is not None and int(layer["page"]) != page + 1:
            continue
        painter.save()
        painter.setOpacity(max(0, min(1, float(layer.get("opacity", 1)))))
        rect = QRectF(float(layer.get("x", 72)), float(layer.get("y", 72)), float(layer.get("width", 300)), float(layer.get("height", 100)))
        if layer.get("asset") in doc.get("assets", {}):
            image = _decode_image(base64.b64decode(doc["assets"][layer["asset"]].get("data", "")))
            painter.drawImage(rect, image)
        if layer.get("text"):
            painter.setPen(QColor(layer.get("color", "#b6becb")))
            painter.setFont(QFont(layer.get("font", "DejaVu Sans"), int(layer.get("font_size", 28))))
            painter.drawText(rect, Qt.TextFlag.TextWordWrap, layer["text"])
        painter.restore()


def _selected_pages(pages, total):
    if pages is None:
        return list(range(total))
    if isinstance(pages, str):
        result = []
        for part in pages.split(","):
            bounds = part.strip().split("-")
            if len(bounds) > 2 or not all(bound.isdigit() for bound in bounds):
                raise ValueError("Invalid page range")
            first, last = int(bounds[0]), int(bounds[-1])
            if first < 1 or last < first or last > total:
                raise ByaxError("invalid_input", f"Page range must be within 1..{total}")
            result.extend(range(first, last+1))
    else:
        result = list(pages)
    if not result or any(not isinstance(p, int) or p < 1 or p > total for p in result):
        raise ValueError(f"Page selection must be within 1..{total}")
    return sorted({p-1 for p in result})


def _report(qdoc, outputs, selected):
    return {"schema_version": 1, "execution": "completed", "validation": "not_run", "page_count": resolved_page_count(qdoc), "pages": [p+1 for p in selected], "outputs": outputs, "findings": qdoc.byax_findings, "losses": qdoc.byax_findings, "coverage": {"content": "Qt text/table flow", "structure": "selectable text in PDF", "visual": "shared Qt layout; see findings", "review": "overlays excluded"}, "reproduction": {"engine": "Qt6 QTextDocument", "units": "points", "default_font": qdoc.defaultFont().family(), "font_substitutions": [f for f in qdoc.byax_findings if f["code"] == "font_substitution"], "font_embedding": "Qt PDF font subset embedding subject to font permissions"}}


def render(doc, out_dir, pages=None):
    qdoc = build_document(doc)
    selected = _selected_pages(pages, resolved_page_count(qdoc, doc))
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    outputs = []
    for page in selected:
        width, height = resolved_page_size(qdoc, page)
        if math.ceil(width*2) * math.ceil(height*2) > MAX_IMAGE_PIXELS:
            raise ByaxError("resource_limit", "Preview raster exceeds 40 million pixels")
        image = QImage(math.ceil(width * 2), math.ceil(height * 2), QImage.Format.Format_ARGB32)
        if image.isNull():
            raise ByaxError("resource_limit", "Unable to allocate preview image")
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(2, 2)
        try:
            draw_page(painter, qdoc, doc, page)
        finally:
            painter.end()
        path = destination / f"page-{page+1:04d}.png"
        if not image.save(str(path)):
            raise OSError(f"Unable to write {path}")
        outputs.append(str(path.resolve()))
    return _report(qdoc, outputs, selected)


def export_pdf(doc, path, pages=None):
    qdoc = build_document(doc)
    selected = _selected_pages(pages, resolved_page_count(qdoc, doc))
    width, height, _ = geometry(doc)
    writer = QPdfWriter(str(path))
    writer.setResolution(72)
    writer.setPageSize(QPageSize(QSizeF(width, height), QPageSize.Unit.Point))
    page_layout = writer.pageLayout()
    page_layout.setMode(QPageLayout.Mode.FullPageMode)
    writer.setPageLayout(page_layout)
    writer.setTitle(doc.get("metadata", {}).get("title", ""))
    writer.setCreator("ByAuthorX")
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f"Unable to open PDF output {path}")
    try:
        for i, page in enumerate(selected):
            pw, ph = resolved_page_size(qdoc, page)
            writer.setPageSize(QPageSize(QSizeF(pw, ph), QPageSize.Unit.Point))
            if i:
                writer.newPage()
            draw_page(painter, qdoc, doc, page)
    finally:
        painter.end()
    del writer
    return _report(qdoc, [str(Path(path).resolve())], selected)


def _column_settings(layout):
    setting = layout.get("columns", 1)
    if isinstance(setting, dict):
        count = int(setting.get("count", 1))
        gap = float(setting.get("gap", 18))
    else:
        count = int(setting)
        gap = float(layout.get("column_gap", 18))
    if count < 1 or count > 12 or not math.isfinite(gap) or gap < 0:
        raise ValueError("Columns require a count of 1..12 and a nonnegative gap")
    return count, gap


def _flow_segments(doc):
    """Section and deliberate page boundaries are stable semantic boundaries."""
    layout = copy.deepcopy(doc.get("layout", {}))
    layout["_byax_section"] = 0
    layout["_byax_page_number_restart"] = int(layout.get("page_number_start", 1))
    segments, blocks, notes = [], [], []
    previous = None
    for block in doc.get("blocks", []):
        attrs = block.get("attrs", {})
        if attrs.get("excluded") or attrs.get("redacted"):
            continue
        if block.get("type") == "footnote":
            note = copy.deepcopy(block)
            note["attrs"]["reference"] = attrs.get("reference", attrs.get("reference_id", attrs.get("target", previous)))
            notes.append(note)
            continue
        is_break = block.get("type") in ("section_break", "page_break")
        if is_break or attrs.get("page_break_before") or attrs.get("full_width"):
            if blocks:
                segments.append((copy.deepcopy(layout), blocks))
                blocks = []
            if block.get("type") == "section_break":
                section_values = copy.deepcopy(attrs.get("layout", {}))
                section_values.update({key: attrs[key] for key in ("page_number_start", "page_number_format", "restart_page_numbering") if key in attrs})
                layout["_byax_section"] += 1
                restart = section_values.get("page_number_start")
                if restart is None and section_values.get("restart_page_numbering"):
                    restart = 1
                layout["_byax_page_number_restart"] = int(restart) if restart is not None else None
                layout.update(section_values)
                for key in ("columns", "column_gap", "width", "height", "margins", "paper", "orientation", "header", "footer"):
                    if key in attrs:
                        layout[key] = copy.deepcopy(attrs[key])
            if is_break:
                continue
        if attrs.get("full_width"):
            full_layout = copy.deepcopy(layout)
            full_layout["columns"] = 1
            segments.append((full_layout, [block]))
        else:
            blocks.append(block)
        previous = block.get("id")
    if blocks or not segments:
        segments.append((copy.deepcopy(layout), blocks))
    return segments, notes


def _flow_document(doc, blocks, width, height, *, note=False):
    part = {**doc, "blocks": [copy.deepcopy(b) for b in blocks if not (b.get("type") == "image" and _wrap(b.get("attrs", {})) in ("behind_text", "in_front"))], "layout": {"width": width, "height": max(height, 24), "margins": {key: 0 for key in ("top", "right", "bottom", "left")}, "columns": 1}, "layers": []}
    if note:
        for block in part["blocks"]:
            block["type"] = "paragraph"
            block.setdefault("attrs", {})["font_size"] = 9
            block["attrs"]["space_after"] = 3
    else:
        for block in part["blocks"]:
            block.setdefault("attrs", {}).pop("page_break_before", None)
    return build_document(part, {})


def _resolve_pass(doc, toc_pages):
    segments, notes = _flow_segments(doc)
    findings, pages, positions, anchors = [], [], {}, {}
    note_numbers = {note["id"]: index+1 for index, note in enumerate(notes)}
    headings = [b for b in doc.get("blocks", []) if b.get("type") == "heading"]
    all_ids = {b.get("id") for _, blocks in segments for b in blocks}
    for note in notes:
        if note["attrs"].get("reference") not in all_ids:
            findings.append(_finding("footnote_orphaned", "Footnote reference is missing; note is appended after body content.", note["id"]))
    for segment_index, (settings, source_blocks) in enumerate(segments):
        section_doc = {**doc, "layout": settings}
        width, height, margins = geometry(section_doc)
        count, gap = _column_settings(settings)
        body_width = width - margins["left"] - margins["right"]
        body_height = height - margins["top"] - margins["bottom"]
        column_width = (body_width - gap*(count-1)) / count
        if column_width < 24:
            raise ValueError("Columns and gaps leave less than 24 points per column")
        column_values = settings.get("columns", {})
        if isinstance(column_values, dict) and column_values.get("widths"):
            findings.append(_finding("column_widths", "Column count and gap are honored; unequal explicit column widths are not yet resolved.", f"section:{segment_index+1}"))
        blocks = copy.deepcopy(source_blocks)
        block_ids = {block.get("id") for block in blocks}
        section_notes = [note for note in notes if note["attrs"].get("reference") in block_ids]
        for block in blocks:
            if block.get("type") == "toc":
                block["type"] = "paragraph"
                block["text"] = "\n".join("  " * (int(h.get("attrs", {}).get("level", 1))-1) + h.get("text", "") + "    " + str(toc_pages.get(h["id"], "…")) for h in headings)
            for note in section_notes:
                if note["attrs"]["reference"] == block.get("id") and block.get("type") in ("paragraph", "heading"):
                    start = len(block.get("text", ""))
                    number = str(note_numbers[note["id"]])
                    block["text"] = block.get("text", "") + number
                    block.setdefault("marks", []).append({"start": start, "end": start+len(number), "type": "superscript", "value": True})
        reserve = 0
        note_docs, assignment = {}, {}
        flow = None
        stable = False
        for iteration in range(8):
            flow_height = max(36, body_height - reserve)
            flow = _flow_document(doc, blocks, column_width, flow_height)
            new_assignment = {}
            for note in section_notes:
                reference = note["attrs"]["reference"]
                position = flow.byax_positions.get(reference, 0)
                reference_block = flow.findBlock(position)
                line = reference_block.layout().lineForTextPosition(max(0, reference_block.length()-2))
                reference_y = flow.documentLayout().blockBoundingRect(reference_block).top()
                if line.isValid():
                    reference_y += line.y()
                virtual_page = int(reference_y // flow.pageSize().height())
                new_assignment.setdefault(virtual_page // count, []).append(note)
            new_note_docs = {}
            requested = 0
            for page_index, page_notes in new_assignment.items():
                note_blocks = []
                for note in page_notes:
                    out = copy.deepcopy(note)
                    out["text"] = str(note_numbers[note["id"]]) + ". " + out.get("text", "")
                    note_blocks.append(out)
                note_doc = _flow_document(doc, note_blocks, body_width, MAX_PAGE_POINTS, note=True)
                natural_height = note_doc.documentLayout().documentSize().height() + 12
                requested = max(requested, natural_height)
                new_note_docs[page_index] = (note_doc, natural_height)
            # Reserving the largest region across this section makes flow stable
            # while retaining every note on its reference sheet. It can leave
            # extra white space on pages with fewer notes, never lost text.
            next_reserve = max(reserve, requested)
            assignment, note_docs = new_assignment, new_note_docs
            if next_reserve == reserve:
                stable = True
                break
            if next_reserve > body_height - 36:
                findings.append(_finding("footnote_overflow", "Notes exceed a page's available note region; overflow continues on dedicated note pages.", f"section:{segment_index+1}"))
                reserve = max(0, body_height * 0.4)
                break
            reserve = next_reserve
        if not stable and section_notes:
            findings.append(_finding("footnote_reflow", "Footnote placement reached the bounded reflow limit; inspect continuation pages.", f"section:{segment_index+1}"))
        flow_height = flow.pageSize().height()
        total = max(1, math.ceil(flow.pageCount()/count))
        page_offset = len(pages)
        findings.extend(flow.byax_findings)
        for ident, position in flow.byax_positions.items():
            virtual = _block_page(flow, position)-1
            positions[ident] = page_offset + virtual//count + 1
            block_rect = flow.documentLayout().blockBoundingRect(flow.findBlock(position))
            anchors[ident] = {"page": positions[ident], "x": margins["left"]+(virtual%count)*(column_width+gap), "y": margins["top"]+block_rect.top()%flow_height}
        for local_page in range(total):
            fragments = []
            for column in range(count):
                virtual_page = local_page * count + column
                if virtual_page >= flow.pageCount():
                    break
                rect = QRectF(margins["left"] + column*(column_width+gap), margins["top"], column_width, flow_height)
                fragments.append({"document": flow, "page": virtual_page, "rect": rect, "kind": "body"})
            note_part = note_docs.get(local_page)
            if note_part:
                note_doc, note_height = note_part
                available = body_height - flow_height
                if note_height <= available + 0.1:
                    rect = QRectF(margins["left"], height-margins["bottom"]-note_height+12, body_width, note_height-12)
                    fragments.append({"document": note_doc, "page": 0, "rect": rect, "kind": "footnotes"})
                    for note in assignment.get(local_page, []):
                        positions[note["id"]] = page_offset + local_page + 1
                else:
                    findings.append(_finding("footnote_continuation", "Oversized footnotes are continued after this section; references remain identified.", assignment[local_page][0]["id"]))
            pages.append({"width": width, "height": height, "layout": settings, "fragments": fragments, "section": settings.get("_byax_section", segment_index)})
        # Never clip oversized notes. Dedicated pages carry full text and an
        # explicit finding; normal notes stay with their references.
        overflow = []
        for local_page, (note_doc, note_height) in note_docs.items():
            if note_height > body_height - flow_height + 0.1:
                overflow.extend(assignment.get(local_page, []))
        if overflow:
            note_flow = _flow_document(doc, [{**n, "text": str(note_numbers[n["id"]])+". "+n.get("text", "")} for n in overflow], body_width, body_height, note=True)
            for index in range(note_flow.pageCount()):
                pages.append({"width": width, "height": height, "layout": settings, "fragments": [{"document": note_flow, "page": index, "rect": QRectF(margins["left"], margins["top"], body_width, body_height), "kind": "footnotes"}], "section": settings.get("_byax_section", segment_index)})
    orphaned = [note for note in notes if note["attrs"].get("reference") not in all_ids]
    if orphaned:
        settings = doc.get("layout", {})
        width, height, margins = geometry(doc)
        body_width, body_height = width-margins["left"]-margins["right"], height-margins["top"]-margins["bottom"]
        note_flow = _flow_document(doc, orphaned, body_width, body_height, note=True)
        for index in range(note_flow.pageCount()):
            pages.append({"width": width, "height": height, "layout": settings, "fragments": [{"document": note_flow, "page": index, "rect": QRectF(margins["left"], margins["top"], body_width, body_height), "kind": "footnotes"}], "section": len(segments)})
    section_counts = {}
    for page in pages:
        section_counts[page["section"]] = section_counts.get(page["section"], 0)+1
    current_section, number, local = None, int(doc.get("layout", {}).get("page_number_start", 1)), 0
    for page in pages:
        if page["section"] != current_section:
            current_section, local = page["section"], 0
            restart = page["layout"].get("_byax_page_number_restart")
            if restart is not None:
                number = restart
        local += 1
        page.update(number=number, section_page=local, section_pages=section_counts[current_section])
        page["number_label"] = _format_page_number(number, page["layout"].get("page_number_format", "decimal"))
        number += 1
    return {"pages": pages, "positions": positions, "anchors": anchors, "findings": findings}


def resolve_layout(qdoc, doc):
    """Resolve physical sheets independently of the semantic editor document."""
    if hasattr(qdoc, "byax_resolved"):
        return qdoc.byax_resolved
    pages = {}
    resolved = None
    for iteration in range(4):
        resolved = _resolve_pass(doc, pages)
        labels = {ident: resolved["pages"][number-1]["number_label"] for ident, number in resolved["positions"].items()}
        if not any(b.get("type") == "toc" for b in doc.get("blocks", [])) or labels == pages:
            break
        pages = labels
    else:
        resolved["findings"].append(_finding("toc_unstable", "Resolved contents references did not stabilize in four passes.", "toc"))
    obsolete = {"columns", "section_geometry", "footnote_placement", "toc_unstable"}
    combined = [f for f in qdoc.byax_findings if f["code"] not in obsolete] + resolved["findings"]
    unique = {(f["code"], f.get("target"), f["message"]): f for f in combined}
    qdoc.byax_findings = list(unique.values())
    qdoc.byax_resolved = resolved
    return resolved


def resolved_page_count(qdoc, doc=None):
    if doc is not None:
        resolve_layout(qdoc, doc)
    if hasattr(qdoc, "byax_resolved"):
        return len(qdoc.byax_resolved["pages"])
    return qdoc.pageCount()


def resolved_page_size(qdoc, page_index):
    if hasattr(qdoc, "byax_resolved"):
        page = qdoc.byax_resolved["pages"][page_index]
        return page["width"], page["height"]
    return qdoc.pageSize().width(), qdoc.pageSize().height()


def draw_page(painter, qdoc, doc, page_index):
    resolved = resolve_layout(qdoc, doc)
    page = resolved["pages"][page_index]
    width, height, settings = page["width"], page["height"], page["layout"]
    page_doc = {**doc, "layout": settings}
    _, _, margins = geometry(page_doc)
    painter.save()
    painter.fillRect(QRectF(0, 0, width, height), QColor(settings.get("background", "white")))
    _draw_layers(painter, doc, page_index, foreground=False)
    _draw_image_overlays(painter, doc, resolved, page_index, False)
    for fragment in page["fragments"]:
        painter.save()
        rect, part = fragment["rect"], fragment["document"]
        painter.setClipRect(rect)
        painter.translate(rect.x(), rect.y() - fragment["page"]*part.pageSize().height())
        part.drawContents(painter, QRectF(0, fragment["page"]*part.pageSize().height(), rect.width(), rect.height()))
        painter.restore()
        if fragment["kind"] == "footnotes":
            painter.save()
            painter.setPen(QColor("#8b9098"))
            painter.drawLine(rect.topLeft() - QPointF(0, 6), rect.topLeft() + QPointF(min(120, rect.width()), -6))
            painter.restore()
    painter.setPen(QColor("#525866"))
    painter.setFont(QFont("DejaVu Sans", 9))
    total = len(resolved["pages"])
    painter.drawText(QRectF(margins["left"], 18, width-margins["left"]-margins["right"], max(18, margins["top"]-24)), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, _region(settings.get("header"), page_index, total, number=page["number"], number_format=settings.get("page_number_format", "decimal"), section_page=page["section_page"], section_pages=page["section_pages"]))
    painter.drawText(QRectF(margins["left"], height-margins["bottom"]+8, width-margins["left"]-margins["right"], max(18, margins["bottom"]-20)), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, _region(settings.get("footer"), page_index, total, number=page["number"], number_format=settings.get("page_number_format", "decimal"), section_page=page["section_page"], section_pages=page["section_pages"]))
    _draw_layers(painter, doc, page_index, foreground=True)
    if hasattr(qdoc, "byax_resolved"):
        _draw_image_overlays(painter, doc, qdoc.byax_resolved, page_index, True)
    painter.restore()


def _wrap(attrs):
    value = str(attrs.get("wrap", "inline")).replace("-", "_").replace(" ", "_")
    return {"above_and_below": "above_below", "behind": "behind_text", "front": "in_front", "in_front_of_text": "in_front"}.get(value, value)


def _draw_image_overlays(painter, doc, resolved, page_index, foreground):
    for block in doc.get("blocks", []):
        attrs = block.get("attrs", {})
        if block.get("type") != "image" or attrs.get("excluded") or attrs.get("redacted"):
            continue
        mode = _wrap(attrs)
        if mode not in ("behind_text", "in_front") or (mode == "in_front") != foreground:
            continue
        anchor = attrs.get("anchor")
        if isinstance(anchor, dict):
            anchor = anchor.get("target")
        position = resolved.get("anchors", {}).get(anchor, {"page": int(attrs.get("page", 1)), "x": 72, "y": 72})
        if int(attrs.get("page", position["page"])) != page_index+1:
            continue
        asset = doc.get("assets", {}).get(attrs.get("asset"), {})
        if not asset.get("data"):
            continue
        image = _decode_image(base64.b64decode(asset["data"]))
        if image.isNull():
            continue
        width = float(attrs.get("width", min(288, image.width())))
        height = float(attrs.get("height", width * image.height()/image.width()))
        rect = QRectF(float(attrs.get("x", position["x"])), float(attrs.get("y", position["y"])), width, height)
        painter.save()
        painter.setOpacity(max(0, min(1, float(attrs.get("opacity", 1)))))
        painter.drawImage(rect, image)
        painter.restore()


def check_layout(doc):
    """Fresh physical layout checks, independent of prior cached reports."""
    try:
        qdoc = build_document(doc)
        resolve_layout(qdoc, doc)
    except ByaxError as exc:
        return {"findings": [{"code": exc.code, "severity": "error", "message": exc.message, "target": "layout"}], "coverage": [{"suite": "print", "rule": "resolved_geometry", "status": "completed"}]}
    findings = list(qdoc.byax_findings)
    incomplete = {"toc_unstable", "footnote_reflow", "column_widths", "pagination_policy", "unknown_block", "image_wrap"}
    return {"findings": findings, "coverage": [{"suite": "print", "rule": "resolved_geometry", "status": "incomplete" if any(f["code"] in incomplete for f in findings) else "completed", "scope": "selected_document", "page_count": resolved_page_count(qdoc)}]}


def _apply_keep_rules(qdoc, doc):
    """Keep fitting paragraphs/headings together by adding derived page breaks.

    QTextBlockFormat.nonBreakableLines disables line wrapping, so it is not a
    pagination keep flag. Derived breaks preserve normal word wrapping instead.
    """
    _, height, margins = geometry(doc)
    available = height-margins["top"]-margins["bottom"]
    for source in doc.get("blocks", []):
        ident = source.get("id")
        values = {**_style(doc, source.get("style", "body")), **source.get("attrs", {})}
        if values.get("widow_control") or values.get("orphan_control"):
            qdoc.byax_findings.append(_finding("pagination_policy", "Explicit widow/orphan line-count rules are not resolved by this backend.", ident))
        if ident not in qdoc.byax_positions or source.get("type") not in ("heading", "paragraph"):
            continue
        together = values.get("keep_together", source.get("type") == "heading")
        with_next = values.get("keep_with_next", source.get("type") == "heading")
        if not together and not with_next:
            continue
        block = qdoc.findBlock(qdoc.byax_positions[ident])
        lines = block.layout()
        if lines.lineCount() == 0:
            continue
        rect = qdoc.documentLayout().blockBoundingRect(block)
        first_y = rect.top()+lines.lineAt(0).y()
        last_y = rect.top()+lines.lineAt(lines.lineCount()-1).y()
        natural = sum(lines.lineAt(index).height() for index in range(lines.lineCount()))
        split = int(first_y//height) != int(last_y//height)
        following = block.next()
        stranded = False
        needed = natural
        if with_next and following.isValid():
            next_rect = qdoc.documentLayout().blockBoundingRect(following)
            stranded = int(last_y//height) != int(next_rect.top()//height)
            if following.layout().lineCount():
                needed += following.layout().lineAt(0).height()+following.blockFormat().topMargin()+block.blockFormat().bottomMargin()
        if together and natural > available or with_next and needed > available:
            qdoc.byax_findings.append(_finding("keep_rule_unsatisfiable", "Requested keep rule exceeds the available page/column height; all text remains in flow.", ident))
            continue
        if together and split or with_next and stranded:
            cursor = QTextCursor(block)
            fmt = block.blockFormat()
            if fmt.pageBreakPolicy() & QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore:
                qdoc.byax_findings.append(_finding("keep_rule_unsatisfiable", "Keep rule remains unsatisfied after a page/column break.", ident))
                continue
            fmt.setPageBreakPolicy(fmt.pageBreakPolicy() | QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore)
            cursor.setBlockFormat(fmt)
            qdoc.documentLayout().documentSize()
