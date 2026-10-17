# ruff: noqa: BLE001
"""File > Send to beta readers… and File > Import reader comments….

Sending writes a read-and-comment copy (a self-contained HTML page, a PDF or a Word file) and records the
sent revision as the review snapshot before the file exists. The writer sends the file however they like;
there is no hosted service. Importing reads the comment files readers send back (and PDFs they annotated),
adds each comment attributed to its reader and each suggestion as a pending proposal, then opens the review
panel; nothing in the text changes until a proposal is accepted there.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from . import beta_readers
from .errors import ByaxError

FORMAT_LABELS = (
    ('html', 'Web page: one file, opens in any browser, works on a phone (recommended)'),
    ('pdf', 'PDF: readers mark it up with their own PDF app'),
    ('docx', 'Word: comments only'),
)
SUFFIX = {'html': '.html', 'pdf': '.pdf', 'docx': '.docx'}
FILE_FILTERS = {
    'html': 'Web page (*.html)',
    'pdf': 'PDF document (*.pdf)',
    'docx': 'Word document (*.docx)',
}


class SendToBetaReadersDialog(QDialog):
    """Which kind of copy, who it is for and what to tell the readers."""

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle(self.tr('Send to beta readers'))
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'ByAuthorX writes one file for your readers and remembers this version. Send the file '
                'however you like (email, a shared folder, a message); nothing is uploaded. When readers '
                'send their comments back, choose File > Import reader comments…. Their comments arrive '
                'as comments and suggestions for you to accept or reject; your text is never changed for you.'
            )
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.PlainText)
        layout.addWidget(intro)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.setAccessibleName(self.tr('Kind of copy to send'))
        self.format.setToolTip(self.tr('The web page works offline on phones, tablets and computers.'))
        for key, label in FORMAT_LABELS:
            self.format.addItem(self.tr(label), key)
        form.addRow(self.tr('Kind of copy:'), self.format)
        self.reader = QLineEdit()
        self.reader.setAccessibleName(self.tr('Reader name'))
        self.reader.setToolTip(
            self.tr('Optional. Names this copy, and fills in the name box of the web page.')
        )
        form.addRow(self.tr('Reader:'), self.reader)
        self.companion = QCheckBox(self.tr('Read-only copy with no comment tools'))
        self.companion.setAccessibleName(self.tr('Read-only copy'))
        self.companion.setToolTip(
            self.tr(
                'A web page for reading on a phone or in a browser. It cannot take comments and is not '
                'remembered in this document.'
            )
        )
        form.addRow('', self.companion)
        self.note = QPlainTextEdit()
        self.note.setAccessibleName(self.tr('Note to your readers'))
        self.note.setToolTip(
            self.tr('Shown at the top of the web page, for example what kind of feedback you want.')
        )
        self.note.setPlaceholderText(self.tr('A note for your readers (web page only)'))
        self.note.setMaximumHeight(110)
        form.addRow(self.tr('Note:'), self.note)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(self.tr('Choose file…'))
        buttons.button(QDialogButtonBox.Ok).setAccessibleName(self.tr('Choose where to save the reader copy'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.format.currentIndexChanged.connect(self.sync)
        self.sync()

    def sync(self, *_):
        html = self.format.currentData() == 'html'
        self.companion.setEnabled(html)
        self.note.setEnabled(html)
        if not html:
            self.companion.setChecked(False)

    def values(self):
        return {
            'format': self.format.currentData(),
            'reviewer': self.reader.text().strip(),
            'companion': self.companion.isChecked(),
            'note': self.note.toPlainText().strip() if self.format.currentData() == 'html' else '',
        }


def import_summary(report):
    """Plain-language import report for the result dialog."""
    lines = list(report.get('summary', []))
    for entry in report.get('comment_merge', {}).get('files', []):
        who = entry.get('reader') or ''
        lines.append(
            f'  • {entry["file"]}' + (f' ({who})' if who else '') + f': {entry["status"].replace("_", " ")}'
        )
    if report.get('proposal_summaries'):
        lines += ['', 'Suggestions (review them in the review panel):']
        lines += [f'  • {line}' for line in report['proposal_summaries']]
    if report.get('losses'):
        lines += ['', 'Placed less precisely than the reader marked them:']
        lines += [
            f'  • {item["message"]}' + (f' ({item["target"]})' if item.get('target') else '')
            for item in report['losses']
        ]
    if report.get('rejected'):
        lines += ['', 'Not merged:']
        lines += [f'  • {item["file"]}: {item["message"]}' for item in report['rejected']]
    return '\n'.join(lines)


class BetaReaderPanels:
    """Main-window behavior for the beta-reader round trip (mixed into ``MainWindow``)."""

    def send_to_beta_readers(self, *, target=None, values=None):
        """Write a reader copy and record its snapshot; returns the review package ID (or None)."""
        title = self.tr('Send to beta readers')
        if not self._review_document_ready(title):
            return None
        if values is None:
            dialog = SendToBetaReadersDialog(self)
            if dialog.exec() != QDialog.Accepted:
                return None
            values = dialog.values()
        fmt = values.get('format', 'html')
        if target is None:
            default = self.path.with_name(f'{self.path.stem} (readers){SUFFIX[fmt]}')
            target, _ = QFileDialog.getSaveFileName(
                self, self.tr('Save reader copy'), str(default), self.tr(FILE_FILTERS[fmt])
            )
            if not target:
                return None
        target = Path(target)
        if target.suffix.lower() not in beta_readers.SUFFIXES[fmt]:
            target = target.with_suffix(SUFFIX[fmt])
        try:
            data, operations, info = beta_readers.prepare_beta_copy(
                self.doc,
                fmt,
                reviewer=values.get('reviewer', ''),
                note=values.get('note', ''),
                companion=values.get('companion', False),
                file_name=target.name,
            )
        except Exception as exc:
            self.error(exc)
            return None
        if operations and not self.commit(operations):
            return None
        try:
            beta_readers.publish_review_copy(data, target, overwrite=True)
        except Exception as exc:
            self.error(exc)
            return None
        if operations:
            self.save(wait=True)
        lines = [self.tr('Reader copy saved to {0}.').format(target)]
        if info['companion']:
            lines.append(
                self.tr('This read-only copy takes no comments, so nothing was recorded in this document.')
            )
        else:
            lines.append(
                self.tr(
                    'Send it to your readers. When their comments come back, choose File > Import reader comments….'
                )
            )
        if info['losses']:
            lines += ['', self.tr('Not included in the copy (it stays in your document):')]
            lines += [f'  • {loss["message"]}' for loss in info['losses']]
        self.status_label.setText(self.tr('Reader copy saved.'))
        self.report(title, '\n'.join(lines))
        return info['review_id']

    def import_reader_comments(self, *, sources=None, review_id=None):
        """Merge comment files (and annotated PDFs) as comments and proposals; returns the merge report."""
        title = self.tr('Import reader comments')
        if not self._review_document_ready(title):
            return None
        packages = self.doc.get('metadata', {}).get('review_packages', {})
        if not any(p.get('format') in ('html', 'pdf') for p in packages.values()):
            self.message(
                title,
                self.tr(
                    'This document has no reader copy to import comments for. Use File > Send to beta readers… first.'
                ),
            )
            return None
        if sources is None:
            sources, _ = QFileDialog.getOpenFileNames(
                self,
                self.tr('Import reader comments'),
                str(self.path.parent),
                self.tr('Reader comments (*.byaxcomments.json *.json *.pdf)'),
            )
            if not sources:
                return None
        try:
            operations, report = beta_readers.plan_comment_merge(self.doc, sources, review_id=review_id)
        except ByaxError as exc:
            if exc.code != 'missing_target' or review_id or not exc.details.get('candidates'):
                self.error(exc)
                return None
            choices = sorted(exc.details['candidates'])
            labels = [
                f'{key} ({packages[key].get("file") or packages[key].get("created", "")})' for key in choices
            ]
            label, accepted = QInputDialog.getItem(
                self,
                title,
                self.tr('The file does not name its reader copy. Which copy was sent?'),
                labels,
                0,
                False,
            )
            if not accepted:
                return None
            return self.import_reader_comments(sources=sources, review_id=choices[labels.index(label)])
        except Exception as exc:
            self.error(exc)
            return None
        if not operations:
            self.report(title, import_summary(report))
            return report
        if not self.commit(operations):
            return None
        self.inspector.setCurrentIndex(0)
        shas = {f['sha256'] for f in report['comment_merge']['files']}
        first = next(
            (
                p['id']
                for p in self.doc['review']['proposals']
                if p.get('kind') == 'beta_reader' and p.get('review_source', {}).get('sha256') in shas
            ),
            None,
        )
        mine = {op['id'] for op in operations if op['type'] in ('add_comment', 'reply_comment')}
        for row in range(self.review_list.count()):
            kind, record = self.review_list.item(row).data(Qt.UserRole) or (None, {})
            if (kind == 'proposals' and record.get('id') == first) or (
                first is None and kind == 'comments' and record.get('id') in mine
            ):
                self.review_list.setCurrentRow(row)
                break
        self.status_label.setText(self.tr('Reader comments imported; review them in the review panel.'))
        self.report(title, import_summary(report))
        return report
