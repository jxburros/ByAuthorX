"""Provenance in the desktop app: origin tint in the editor, the Provenance panel and paste capture.

Context engine plan section 10. *View > Show text origin* tints text by where it came from and
draws a different underline for each origin (dashed for AI-generated, dotted for AI-edited,
dash-dot for pasted), so the distinction never depends on colour alone; in high-contrast mode only
the underlines are drawn. *Tools > Provenance…* shows the legend, the per-section shares, the
per-span listing and the AI claims, writes APA, MLA or Chicago disclosures to copy or insert as a
review proposal, re-finds the sources of AI claims and exports the optional C2PA sidecar.

Typing commits with ``provenance: {origin: human}``; a paste commits the text typed so far first,
then the pasted text with ``origin: pasted`` and the source the clipboard reports (its source URL
when a browser provides one, and the offered MIME formats). The log is local document data and
is never sent anywhere.
"""

from __future__ import annotations

import copy
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import disclosure, provenance
from .brand import prefers_high_contrast
from .gui_editor import qt_offset

# origin: (tint, underline style, legend symbol). Human typing is left unmarked.
ORIGIN_STYLES = {
    'ai_generated': ('#7c5cff', QTextCharFormat.DashUnderline, '- - -'),
    'ai_edited': ('#e0a100', QTextCharFormat.DotLine, '· · ·'),
    'pasted': ('#1f9d8a', QTextCharFormat.DashDotLine, '-·-·'),
    'human': (None, QTextCharFormat.NoUnderline, '(no mark)'),
}
# Clipboard formats that carry the page a browser copied from (Firefox, Chromium on Linux/macOS).
_URL_FORMATS = ('text/x-moz-url-priv', 'chromium/x-source-url', 'org.chromium.source-url', 'text/x-moz-url')
_WINDOWS_HTML = 'application/x-qt-windows-mime;value="HTML Format"'


def paste_source(mime) -> dict:
    """Where pasted text came from, as far as the clipboard says: source URL and offered formats.

    Qt exposes no source application on any platform, so ``app`` is never guessed.
    """
    formats = [f for f in mime.formats() if isinstance(f, str)][: provenance.MAX_FORMATS]
    source: dict = {'formats': [f[:200] for f in formats], 'mime': 'text/plain'}
    for name in _URL_FORMATS:
        if name in formats:
            raw = bytes(mime.data(name))
            text = raw.decode('utf-16-le' if raw[1:2] == b'\x00' else 'utf-8', errors='replace')
            url = text.replace('\x00', '').strip().splitlines()[0] if text.strip() else ''
            if url.startswith(('http://', 'https://')):
                source['url'] = url[: provenance.MAX_TEXT]
                break
    if 'url' not in source and _WINDOWS_HTML in formats:
        found = re.search(rb'SourceURL:(\S+)', bytes(mime.data(_WINDOWS_HTML)))
        if found:
            source['url'] = found.group(1).decode('utf-8', errors='replace')[: provenance.MAX_TEXT]
    return source


class ProvenancePanels:
    """Main-window behaviour for provenance (mixed into ``MainWindow``)."""

    def provenance_panel(self):
        if not self.flush_edits():
            return None
        dialog = ProvenanceDialog(self)
        dialog.show()
        return dialog

    def declared_provenance(self):
        """The origin the next desktop commit declares: a pending paste, otherwise typing."""
        return copy.deepcopy(getattr(self, '_pending_provenance', None) or {'origin': 'human'})

    def paste_started(self, source):
        self.flush_edits()
        self._pending_provenance = {'origin': 'pasted', 'source': source}

    def paste_finished(self):
        try:
            self.flush_edits()
        finally:
            self._pending_provenance = None

    def set_provenance_view(self, enabled):
        self.provenance_view = bool(enabled)
        action = getattr(self, 'provenance_view_action', None)
        if action is not None and action.isChecked() != self.provenance_view:
            action.setChecked(self.provenance_view)
        self.update_provenance_tint()

    def update_provenance_tint(self):
        editor = self.editor
        if not getattr(self, 'provenance_view', False) or getattr(self, 'file_view', 'byax') != 'byax':
            editor.setExtraSelections([])
            return
        log = self.doc.get('provenance') or {}
        records = log.get('records', {})
        contrast = prefers_high_contrast(QGuiApplication.instance())
        selections = []
        for target, spans in log.get('spans', {}).items():
            block = editor._block_for(target)
            if block is None:
                continue
            text = block.text().replace(' ', '\n')
            for start, end, rid in spans:
                origin = records.get(rid, {}).get('origin')
                tint, underline, _ = ORIGIN_STYLES.get(origin, ORIGIN_STYLES['human'])
                if tint is None or end > len(text):
                    continue
                selection = QTextEdit.ExtraSelection()
                cursor = QTextCursor(block)
                cursor.setPosition(block.position() + qt_offset(text, start))
                cursor.setPosition(block.position() + qt_offset(text, end), QTextCursor.KeepAnchor)
                selection.cursor = cursor
                fmt = QTextCharFormat()
                color = QColor(tint)
                if not contrast:
                    background = QColor(color)
                    background.setAlpha(46)
                    fmt.setBackground(background)
                fmt.setUnderlineStyle(underline)
                fmt.setUnderlineColor(self.palette().text().color() if contrast else color)
                selection.format = fmt
                selections.append(selection)
        editor.setExtraSelections(selections)


def legend_widget(parent=None):
    """Colour swatch, underline symbol and label for every origin (never colour alone)."""
    widget = QWidget(parent)
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    for row, origin in enumerate(('human', 'ai_generated', 'ai_edited', 'pasted')):
        tint, _, symbol = ORIGIN_STYLES[origin]
        swatch = QLabel()
        swatch.setFixedSize(18, 12)
        swatch.setStyleSheet(
            f'background: {tint}; border: 1px solid palette(text);'
            if tint
            else 'border: 1px solid palette(text);'
        )
        label = QLabel(provenance.LABELS[origin])
        mark = QLabel(symbol)
        mark.setAccessibleName(widget.tr('Underline for {0}').format(provenance.LABELS[origin]))
        grid.addWidget(swatch, row, 0)
        grid.addWidget(mark, row, 1)
        grid.addWidget(label, row, 2)
    note = QLabel(widget.tr('Text with no mark and no span listed below has no recorded origin.'))
    note.setWordWrap(True)
    grid.addWidget(note, 4, 0, 1, 3)
    widget.setAccessibleName(widget.tr('Text origin legend'))
    return widget


class ProvenanceDialog(QDialog):
    """Legend, shares, spans and claims, disclosures and the C2PA sidecar for the open document."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(self.tr('Provenance'))
        self.resize(820, 600)
        layout = QVBoxLayout(self)
        privacy = QLabel(self.tr('Provenance is stored in this document only. Nothing is sent anywhere.'))
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        top = QHBoxLayout()
        top.addWidget(legend_widget(self), 1)
        self.view_toggle = QCheckBox(self.tr('Show text origin in the editor'))
        self.view_toggle.setChecked(bool(getattr(window, 'provenance_view', False)))
        self.view_toggle.toggled.connect(window.set_provenance_view)
        top.addWidget(self.view_toggle, 0, Qt.AlignTop)
        layout.addLayout(top)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.summary = QTextBrowser()
        self.summary.setAccessibleName(self.tr('Provenance summary'))
        self.tabs.addTab(self.summary, self.tr('Summary'))
        self.spans = QTableWidget(0, 5)
        self.spans.setHorizontalHeaderLabels(
            [self.tr('Origin'), self.tr('Text'), self.tr('Model'), self.tr('Source'), self.tr('Where')]
        )
        self.spans.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.spans.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.spans.setAccessibleName(self.tr('Provenance spans'))
        self.spans.cellDoubleClicked.connect(self.navigate)
        self.tabs.addTab(self.spans, self.tr('Spans'))
        self.claims = QTextBrowser()
        self.claims.setAccessibleName(self.tr('AI claims and their sources'))
        self.tabs.addTab(self.claims, self.tr('AI claims'))
        self.tabs.addTab(self._disclosure_tab(), self.tr('Disclosure'))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.reanchor_button = buttons.addButton(
            self.tr('Re-find claim sources'), QDialogButtonBox.ActionRole
        )
        self.reanchor_button.clicked.connect(self.reanchor)
        self.c2pa_button = buttons.addButton(self.tr('Export C2PA sidecar…'), QDialogButtonBox.ActionRole)
        self.c2pa_button.clicked.connect(self.export_c2pa)
        self.clear_button = buttons.addButton(self.tr('Clear log…'), QDialogButtonBox.ActionRole)
        self.clear_button.clicked.connect(self.clear)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.populate()

    def _disclosure_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr('Style')))
        self.style = QComboBox()
        for value, label in (('apa', 'APA'), ('mla', 'MLA'), ('chicago', 'Chicago')):
            self.style.addItem(label, value)
        self.style.setAccessibleName(self.tr('Disclosure style'))
        self.style.currentIndexChanged.connect(self.update_disclosure)
        row.addWidget(self.style)
        row.addStretch(1)
        layout.addLayout(row)
        self.disclosure = QTextBrowser()
        self.disclosure.setAccessibleName(self.tr('AI-use disclosure'))
        layout.addWidget(self.disclosure, 1)
        actions = QHBoxLayout()
        self.copy_button = QPushButton(self.tr('Copy'))
        self.copy_button.clicked.connect(self.copy_disclosure)
        self.insert_button = QPushButton(self.tr('Add to Review as a proposal'))
        self.insert_button.clicked.connect(self.propose_disclosure)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.insert_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return widget

    def populate(self):
        doc = self.window.doc
        self.report = provenance.report(doc, limit=2000)
        lines = provenance.describe(self.report)
        html = ''.join(f'<p>{_escape(line)}</p>' for line in lines)
        if self.report['models']:
            html += '<p><b>' + _escape(self.tr('Models')) + ':</b> '
            html += _escape(', '.join(m['model'] or m['provider'] for m in self.report['models'])) + '</p>'
        html += f'<p><i>{_escape(self.report["note"])}</i></p>'
        self.summary.setHtml(html)
        self.spans.setRowCount(0)
        for span in self.report['spans']:
            row = self.spans.rowCount()
            self.spans.insertRow(row)
            source = (span.get('source') or {}).get('url', '')
            values = [
                provenance.LABELS[span['origin']],
                span['text'],
                span.get('model', ''),
                source,
                f'{span["target"]} {span["start"]}–{span["end"]}',
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, span['target'])
                self.spans.setItem(row, column, item)
        claims = []
        for claim in self.report['claims']:
            sources = '; '.join(
                f'{g.get("title") or g.get("citation_id") or "uncited"} ({g.get("state")})'
                for g in claim['grounding']
            )
            claims.append(
                f'<p>“{_escape(claim["text"])}” — {_escape(claim["state"])}<br>{_escape(sources)}</p>'
            )
        self.claims.setHtml(''.join(claims) or _escape(self.tr('No AI claims are recorded.')))
        self.update_disclosure()

    def update_disclosure(self):
        self.statement = disclosure.disclose(self.window.doc, self.style.currentData() or 'apa')
        parts = [f'<p>{_escape(self.statement["text"])}</p>']
        for reference in self.statement['references']:
            parts.append(f'<p>{_escape(reference["text"])}</p>')
        if self.statement['needs_review']:
            parts.append('<p><b>Check:</b> ' + _escape('; '.join(self.statement['needs_review'])) + '</p>')
        guidance = self.statement['guidance']
        parts.append(f'<p><i>Format: {_escape(guidance["publisher"])}, {_escape(guidance["title"])}.</i></p>')
        self.disclosure.setHtml(''.join(parts))
        self.insert_button.setEnabled(bool(self.statement['ai_used']))

    def disclosure_text(self):
        return '\n\n'.join([self.statement['text'], *(r['text'] for r in self.statement['references'])])

    def copy_disclosure(self):
        QGuiApplication.clipboard().setText(self.disclosure_text())
        self.window.status_label.setText(self.tr('Disclosure copied.'))

    def propose_disclosure(self):
        window = self.window
        window.flush_edits()
        operation = disclosure.disclosure_proposal(window.doc, self.style.currentData() or 'apa')
        if window.commit([operation]):
            window.status_label.setText(self.tr('Disclosure added to Review. Accept it there to insert it.'))
            return True
        return False

    def navigate(self, row, _column):
        item = self.spans.item(row, 0)
        if item is not None:
            self.window.editor.navigate(item.data(Qt.UserRole))

    def reanchor(self):
        window = self.window
        result = provenance.reanchor_sources(window.doc)
        if not result['operations']:
            window.status_label.setText(self.tr('Claim sources are unchanged.'))
            return result
        if window.commit(copy.deepcopy(result['operations'])):
            window.status_label.setText(self.tr('Claim sources re-anchored.'))
            self.populate()
        return result

    def export_c2pa(self, path=None):
        window = self.window
        if not path:
            base = (
                str(window.path.with_suffix('.c2pa.json'))
                if getattr(window, 'path', None)
                else 'document.c2pa.json'
            )
            path, _ = QFileDialog.getSaveFileName(
                self, self.tr('Export C2PA sidecar'), base, 'C2PA JSON (*.json)'
            )
        if not path:
            return None
        written = disclosure.export_c2pa(window.doc, path, overwrite=True)
        window.status_label.setText(
            self.tr('Unsigned, informational C2PA sidecar written: {0}').format(written['path'])
        )
        return written

    def clear(self, confirmed=False):
        window = self.window
        if not confirmed:
            answer = QMessageBox.question(
                self,
                self.tr('Clear provenance log?'),
                self.tr('Forget where the text of this document came from? This can be undone with Undo.'),
            )
            if answer != QMessageBox.Yes:
                return False
        if window.commit([{'type': 'clear_provenance'}]):
            self.populate()
            window.update_provenance_tint()
            return True
        return False


def _escape(text):
    import html

    return html.escape(str(text))
