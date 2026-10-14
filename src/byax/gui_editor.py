"""Native editor helpers. QText blocks retain canonical identities, never flattened text."""

from __future__ import annotations

import uuid

from PySide6.QtCore import QRectF, QSize, Signal
from PySide6.QtGui import QColor, QPainter, QTextCursor
from PySide6.QtWidgets import QTextEdit, QWidget

from .brand import COLORS
from .layout import BLOCK_ID_PROPERTY, draw_page, resolved_page_count, resolved_page_size

BLOCK_ID = BLOCK_ID_PROPERTY


def scalar_offset(text: str, qt_offset: int) -> int:
    """Qt positions are UTF-16; canonical offsets count Unicode scalars."""
    return len(text.encode('utf-16-le')[: qt_offset * 2].decode('utf-16-le', errors='ignore'))


def qt_offset(text: str, scalar: int) -> int:
    return len(text[:scalar].encode('utf-16-le')) // 2


def text_operations(target, before, after):
    """One localized replacement keeps unaffected marks and anchors intact."""
    if before == after:
        return []
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    tail = 0
    while tail < min(len(before), len(after)) - start and before[-tail - 1] == after[-tail - 1]:
        tail += 1
    # Never split a user-perceived grapheme, including emoji and combining accents.
    import regex

    before_bounds = {0, *[m.end() for m in regex.finditer(r'\X', before)]}
    after_bounds = {0, *[m.end() for m in regex.finditer(r'\X', after)]}
    while start not in before_bounds or start not in after_bounds:
        start -= 1
    while tail and (len(before) - tail not in before_bounds or len(after) - tail not in after_bounds):
        tail -= 1
    end = len(before) - tail
    replacement = after[start : len(after) - tail if tail else None]
    ops = []
    if end > start:
        ops.append(
            {
                'type': 'delete_text',
                'target': target,
                'start': start,
                'end': end,
                'expected_text': before[start:end],
            }
        )
    if replacement:
        ops.append({'type': 'insert_text', 'target': target, 'offset': start, 'text': replacement})
    return ops


class SemanticEditor(QTextEdit):
    fileDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)  # Imported HTML must go through the bounded exchange adapter.
        self.setAcceptDrops(True)
        self.setAccessibleName(self.tr('Document editor'))
        self.setPlaceholderText(self.tr('Write your first line…'))
        self.setUndoRedoEnabled(False)
        self.setTabChangesFocus(False)
        self.setStyleSheet(f"""QTextEdit {{ background: white; color: {COLORS['ink']};
            border: 2px solid {COLORS['control']}; border-radius: 8px; padding: 24px;
            selection-background-color: {COLORS['greenText']}; selection-color: white; }}
            QTextEdit:focus {{ border: 2px solid {COLORS['greenText']}; }}""")

    def insertFromMimeData(self, source):
        if source.hasUrls() and source.urls()[0].isLocalFile():
            self.fileDropped.emit(source.urls()[0].toLocalFile())
        else:
            self.insertPlainText(source.text())

    def capture_baseline(self):
        self.object_text = {}
        block = self.document().begin()
        while block.isValid():
            ident = block.blockFormat().property(BLOCK_ID)
            if ident:
                self.object_text.setdefault(ident, []).append(block.text())
            block = block.next()

    def selection_targets(self):
        cursor = self.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        block = self.document().findBlock(start)
        targets = []
        while block.isValid() and block.position() <= end:
            ident = block.blockFormat().property(BLOCK_ID)
            if ident:
                text = block.text()
                a = scalar_offset(text, max(0, start - block.position()))
                b = scalar_offset(text, min(len(text.encode('utf-16-le')) // 2, end - block.position()))
                targets.append((str(ident), a, b))
            if not cursor.hasSelection() or block.position() + block.length() > end:
                break
            block = block.next()
        return targets

    def current_id(self):
        return self.textCursor().blockFormat().property(BLOCK_ID)

    def navigate(self, target):
        block = self.document().begin()
        while block.isValid():
            if block.blockFormat().property(BLOCK_ID) == target:
                self.setTextCursor(QTextCursor(block))
                self.ensureCursorVisible()
                self.setFocus()
                return True
            block = block.next()
        return False

    def collect_changes(self, canonical):
        """Translate native edits into localized canonical operations.

        Splits and joins use semantic operations to remap marks and anchors. Cells
        keep independent identities; table structure is edited by the Table menu.
        """
        records = {b['id']: b for b in canonical['blocks']}
        cells = {c['id']: c for b in canonical['blocks'] for r in b.get('rows', []) for c in r['cells']}
        observed = []
        block = self.document().begin()
        while block.isValid():
            observed.append(
                (block, block.blockFormat().property(BLOCK_ID), block.text().replace('\u2028', '\n'))
            )
            block = block.next()
        observed_ids = {ident for _, ident, _ in observed if ident}
        rendered_ids = set(getattr(self, 'object_text', records))
        observed_text = {}
        for _, ident, text in observed:
            observed_text.setdefault(ident, []).append(text)
        for ident, record in records.items():
            if (
                ident in rendered_ids
                and record['type'] not in ('paragraph', 'heading', 'footnote')
                and (
                    ident not in observed_ids
                    or observed_text.get(ident) != getattr(self, 'object_text', {}).get(ident)
                )
            ):
                raise ValueError(
                    'Use the object or table commands to change this object. The original object has been restored.'
                )
        seen, operations, tags = set(), [], []
        previous = None
        index = 0
        ordered_ids = list(records)
        while index < len(observed):
            qtblock, ident, text = observed[index]
            if ident in cells:
                if ident in seen:
                    raise ValueError('Use Shift+Enter for a line break within a table cell.')
                seen.add(ident)
                if text != cells[ident]['text']:
                    operations.append(
                        {
                            'type': 'update_cell',
                            'target': ident,
                            'text': text,
                            'expected_text': cells[ident]['text'],
                        }
                    )
            elif ident in records and records[ident]['type'] not in ('paragraph', 'heading', 'footnote'):
                previous = ident
                seen.add(ident)
            elif ident in records and ident not in seen:
                seen.add(ident)
                original = records[ident].get('text', '')
                # Backspace/Delete can join adjacent paragraphs. Canonical join
                # carries the removed paragraph's marks, comments, and anchors.
                for following in ordered_ids[ordered_ids.index(ident) + 1 :]:
                    record = records[following]
                    if (
                        following not in rendered_ids
                        or following in observed_ids
                        or record['type'] not in ('paragraph', 'heading', 'footnote')
                    ):
                        break
                    operations.append({'type': 'join_blocks', 'target': ident, 'next': following})
                    original += record.get('text', '')
                    seen.add(following)
                # Enter clones the QTextBlock identity; split after reconciling
                # textual changes, retaining formatting on both resulting blocks.
                parts = [(qtblock, text)]
                while index + 1 < len(observed) and observed[index + 1][1] == ident:
                    index += 1
                    parts.append((observed[index][0], observed[index][2]))
                joined = ''.join(part[1] for part in parts)
                operations.extend(text_operations(ident, original, joined))
                previous = ident
                for part_index in range(len(parts) - 1):
                    fresh = 'p_' + uuid.uuid4().hex
                    operations.append(
                        {
                            'type': 'split_block',
                            'target': previous,
                            'offset': len(parts[part_index][1]),
                            'id': fresh,
                        }
                    )
                    tags.append((parts[part_index + 1][0], fresh))
                    seen.add(fresh)
                    previous = fresh
            elif text or (ident in seen and ident in records) or not canonical['blocks']:
                fresh = 'p_' + uuid.uuid4().hex
                style = records.get(ident, {}).get('style', 'body')
                operations.append(
                    {'type': 'insert_paragraph', 'after': previous, 'text': text, 'style': style, 'id': fresh}
                )
                tags.append((qtblock, fresh))
                seen.add(fresh)
                previous = fresh
            index += 1
        if (set(cells) & rendered_ids) - seen:
            raise ValueError('Table structure cannot be removed by typing. Use the Table menu.')
        for ident, record in records.items():
            if (
                ident in rendered_ids
                and ident not in seen
                and record['type'] in ('paragraph', 'heading', 'footnote')
            ):
                operations.append({'type': 'delete_block', 'target': ident})
        return operations, tags

    def apply_tags(self, tags):
        for block, ident in tags:
            cursor = QTextCursor(block)
            fmt = block.blockFormat()
            fmt.setProperty(BLOCK_ID, ident)
            cursor.setBlockFormat(fmt)


class PageCanvas(QWidget):
    """Scrollable paper preview rendered from the same QTextDocument as PDF."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.document = None
        self.zoom = 1.0
        self.setAccessibleName(self.tr('Paginated print preview'))

    def set_document(self, document, canonical=None):
        self.document = document
        self.canonical = canonical
        self.update_size()

    def update_size(self):
        if self.document is None:
            return
        count = resolved_page_count(self.document, self.canonical)
        self.page_sizes = [resolved_page_size(self.document, page) for page in range(count)]
        self.resize(
            round(max(w for w, h in self.page_sizes) * self.zoom + 64),
            round(sum(h * self.zoom + 28 for w, h in self.page_sizes) + 28),
        )
        self.update()

    def sizeHint(self):
        return self.size() if self.document else QSize(680, 840)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(COLORS['paper']))
        if self.document is None:
            return
        top = 28
        for index, (width, height) in enumerate(self.page_sizes):
            rect = QRectF(32, top, width * self.zoom, height * self.zoom)
            top += height * self.zoom + 28
            if not rect.intersects(QRectF(event.rect())):
                continue
            painter.fillRect(rect.translated(3, 4), QColor(COLORS['rule']))
            painter.fillRect(rect, QColor('#ffffff'))
            painter.save()
            painter.translate(rect.left(), rect.top())
            painter.scale(self.zoom, self.zoom)
            painter.setClipRect(QRectF(0, 0, width, height))
            if self.canonical:
                draw_page(painter, self.document, self.canonical, index)
            else:
                painter.translate(0, -index * height)
                self.document.drawContents(painter, QRectF(0, index * height, width, height))
            painter.restore()
