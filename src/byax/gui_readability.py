"""Readability report dialog: indices, style statistics and a pacing graph with a table equivalent.

The pacing graph is drawn with QPainter (no charting dependency). Everything it shows is also in the
pacing table beside it, and the graph itself is keyboard operable: Left/Right move through
paragraphs and Home/End jump to the ends, announcing the selected point.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .readability import readability_document, readability_report

PACING_HEADERS = ('Paragraph', 'Words', 'Average sentence (words)', 'Dialogue share')


class PacingChart(QWidget):
    """Average sentence length (solid) and dialogue share (dashed) for each paragraph of a chapter."""

    selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.series = []
        self.index = -1
        self.setMinimumHeight(180)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(self.tr('Pacing graph'))
        self.setToolTip(
            self.tr(
                'Pacing graph: use the arrow keys to move between paragraphs. The table below has the same values.'
            )
        )

    def set_series(self, series):
        self.series = list(series)
        self.index = 0 if self.series else -1
        self._describe()
        self.update()

    def point(self, index):
        return self.series[index] if 0 <= index < len(self.series) else None

    def summary(self):
        if not self.series:
            return self.tr('No paragraphs in this chapter.')
        lengths = [p['mean_sentence_words'] for p in self.series]
        return self.tr('{0} paragraphs; average sentence length from {1} to {2} words.').format(
            len(self.series), min(lengths), max(lengths)
        )

    def _describe(self):
        point = self.point(self.index)
        text = self.summary()
        if point:
            text += ' ' + self.tr(
                'Paragraph {0}: {1} words, average sentence {2} words, dialogue {3}%.'
            ).format(
                self.index + 1,
                point['words'],
                point['mean_sentence_words'],
                round(100 * point['dialogue_ratio']),
            )
        self.setAccessibleDescription(text)

    def move(self, index):
        if self.series:
            self.index = max(0, min(len(self.series) - 1, index))
            self._describe()
            self.selected.emit(self.index)
            self.update()

    def keyPressEvent(self, event):
        steps = {Qt.Key_Left: -1, Qt.Key_Right: 1, Qt.Key_Up: -1, Qt.Key_Down: 1}
        if event.key() in steps:
            self.move(self.index + steps[event.key()])
        elif event.key() == Qt.Key_Home:
            self.move(0)
        elif event.key() == Qt.Key_End:
            self.move(len(self.series) - 1)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self.series:
            area = self._area()
            fraction = (event.position().x() - area.left()) / max(1.0, area.width())
            self.move(round(fraction * (len(self.series) - 1)))
        self.setFocus()

    def _area(self):
        return QRectF(self.rect()).adjusted(46, 12, -16, -26)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        text, grid = palette.color(palette.ColorRole.WindowText), palette.color(palette.ColorRole.Mid)
        area = self._area()
        painter.setPen(QPen(grid, 1))
        painter.drawRect(area)
        if not self.series:
            painter.setPen(text)
            painter.drawText(area, Qt.AlignCenter, self.tr('No paragraphs to graph'))
            return
        top = max(max(p['mean_sentence_words'] for p in self.series), 1.0)
        count = len(self.series)

        def x(i):
            return area.left() + (area.width() * i / (count - 1) if count > 1 else area.width() / 2)

        for fraction in (0.0, 0.5, 1.0):
            y = area.bottom() - area.height() * fraction
            painter.setPen(QPen(grid, 1, Qt.DotLine))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(text)
            painter.drawText(
                QRectF(0, y - 8, area.left() - 4, 16),
                Qt.AlignRight | Qt.AlignVCenter,
                f'{top * fraction:.0f}',
            )
        painter.drawText(
            QRectF(area.left(), area.bottom() + 4, area.width(), 18),
            Qt.AlignCenter,
            self.tr('Paragraph (sentence length in words; dashed: dialogue share)'),
        )
        for key, scale, style, color in (
            ('mean_sentence_words', top, Qt.SolidLine, palette.color(palette.ColorRole.Highlight)),
            ('dialogue_ratio', 1.0, Qt.DashLine, QColor(text)),
        ):
            path = QPainterPath()
            for i, point in enumerate(self.series):
                spot = QPointF(x(i), area.bottom() - area.height() * min(1.0, point[key] / scale))
                path.moveTo(spot) if i == 0 else path.lineTo(spot)
            painter.setPen(QPen(color, 2, style))
            painter.drawPath(path)
        if self.index >= 0:
            point = self.series[self.index]
            painter.setPen(QPen(text, 1))
            painter.drawLine(QPointF(x(self.index), area.top()), QPointF(x(self.index), area.bottom()))
            spot = QPointF(x(self.index), area.bottom() - area.height() * point['mean_sentence_words'] / top)
            painter.setBrush(palette.color(palette.ColorRole.Highlight))
            painter.drawEllipse(spot, 4, 4)
        if self.hasFocus():
            painter.setPen(QPen(palette.color(palette.ColorRole.Highlight), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -1, -1))


class ReadabilityDialog(QDialog):
    def __init__(self, window, report=None):
        super().__init__(window)
        self.window = window
        self.report = report or readability_report(window.doc)
        self.setWindowTitle(self.tr('Readability report'))
        self.resize(780, 620)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        label = QLabel(self.tr('Section'))
        self.scope = QComboBox()
        self.scope.setAccessibleName(self.tr('Report section'))
        self.scope.addItem(self.tr('Whole document'), None)
        for chapter in self.report['chapters']:
            self.scope.addItem(chapter['title'] or self.tr('Untitled chapter'), chapter['id'])
        label.setBuddy(self.scope)
        top.addWidget(label)
        top.addWidget(self.scope, 1)
        layout.addLayout(top)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setAccessibleName(self.tr('Sample-size caveats'))
        layout.addWidget(self.note)
        self.tabs = QTabWidget()
        self.indices = QTableWidget()
        self.style = QTableWidget()
        self.tabs.addTab(self._split(), self.tr('Readability'))
        pacing = QWidget()
        pacing_layout = QVBoxLayout(pacing)
        self.chart = PacingChart()
        pacing_layout.addWidget(self.chart)
        self.pacing_table = QTableWidget()
        pacing_layout.addWidget(self.pacing_table)
        self.tabs.addTab(pacing, self.tr('Pacing'))
        layout.addWidget(self.tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        export = QPushButton(self.tr('Save report as document…'))
        export.setAccessibleName(self.tr('Save report as document'))
        export.setToolTip(self.tr('Save this report as a ByAuthorX, Word, Markdown, HTML or PDF document'))
        export.clicked.connect(self.export)
        buttons.addButton(export, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.scope.currentIndexChanged.connect(self.show_scope)
        self.chart.selected.connect(lambda i: self.pacing_table.selectRow(i))
        self.pacing_table.itemSelectionChanged.connect(self._table_selected)
        self.show_scope()

    def _split(self):
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.indices)
        splitter.addWidget(self.style)
        return splitter

    def entry(self):
        identity = self.scope.currentData()
        if identity is None:
            return self.report['document']
        return next(c for c in self.report['chapters'] if c['id'] == identity)

    def show_scope(self, *_):
        entry = self.entry()
        rows = [
            (
                item['label'],
                '' if item['value'] is None else item['value'],
                self.tr('Reliable') if item['reliable'] else self.tr('Caution: ') + (item['caveat'] or ''),
            )
            for item in entry['indices'].values()
        ]
        self._fill(
            self.indices,
            (self.tr('Index'), self.tr('Value'), self.tr('Sample')),
            rows,
            self.tr('Readability indices'),
        )
        style = entry['style']
        facts = (
            ('Words', 'words'),
            ('Sentences', 'sentences'),
            ('Average sentence length (words)', 'average_sentence_words'),
            ('Longest sentence (words)', 'longest_sentence_words'),
            ('Passive-voice constructions (heuristic)', 'passive_voice_count'),
            ('Adverbs per 100 words', 'adverbs_per_100_words'),
            ('Dialogue share of words', 'dialogue_ratio'),
            ('Reading time (minutes)', 'reading_minutes'),
        )
        self._fill(
            self.style,
            (self.tr('Style measure'), self.tr('Value')),
            [(self.tr(label), style[key]) for label, key in facts],
            self.tr('Style statistics'),
        )
        self.note.setText(' '.join(entry['caveats']) or self.tr('Sample is long enough for every index.'))
        series = entry.get('pacing') or []
        self.chart.set_series(series)
        self._fill(
            self.pacing_table,
            [self.tr(h) for h in PACING_HEADERS],
            [
                (i + 1, p['words'], p['mean_sentence_words'], f'{round(100 * p["dialogue_ratio"])}%')
                for i, p in enumerate(series)
            ],
            self.tr('Pacing table'),
        )

    def _fill(self, table, headers, rows, name):
        table.setAccessibleName(name)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.clear()
        table.setColumnCount(len(headers))
        table.setRowCount(len(rows))
        table.setHorizontalHeaderLabels(list(headers))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(value)))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    def _table_selected(self):
        rows = self.pacing_table.selectionModel().selectedRows()
        if rows and rows[0].row() != self.chart.index:
            self.chart.index = rows[0].row()
            self.chart._describe()
            self.chart.update()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr('Save readability report'),
            '',
            self.tr('ByAuthorX (*.byax);;Word (*.docx);;Markdown (*.md);;HTML (*.html);;PDF (*.pdf)'),
        )
        if not path:
            return
        from .exchange import export_document

        title = self.tr('Readability report: ') + (self.window.doc.get('metadata', {}).get('title') or '')
        try:
            result = export_document(readability_document(self.report, title), path)
        except Exception as exc:  # noqa: BLE001 - GUI boundary reports the failure instead of closing
            QMessageBox.warning(self, self.tr('Readability report'), str(exc))
            return
        losses = result.get('losses') or []
        QMessageBox.information(
            self,
            self.tr('Readability report'),
            self.tr('Saved {0}.').format(path)
            + ('\n\n' + '\n'.join(f'{x.get("code")}: {x.get("message")}' for x in losses) if losses else ''),
        )
        self.saved = path
