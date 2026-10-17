# ruff: noqa: BLE001
"""File > Send for review (Word)… and File > Merge reviewed copy….

Sending records the current revision as the review snapshot (a pinned merge base) before the copy is
written. Merging reads the returned file and adds each editor change as a pending proposal and each
comment and reply as a comment thread, then opens Proposed changes; nothing in the text changes until
a proposal is accepted there.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from . import docx_review
from .errors import ByaxError

RESTRICTION_LABELS = (
    ('tracked_changes', 'Tracked changes and comments only (recommended)'),
    ('comments', 'Comments only'),
    ('none', 'No restriction (Track Changes is still on)'),
)


class SendForReviewDialog(QDialog):
    """Who the copy is for and what Word lets the editor do with it."""

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle(self.tr('Send for review (Word)'))
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'ByAuthorX writes a Word copy with Track Changes on and remembers this version. When the '
                'copy comes back, Merge reviewed copy… turns the editor’s changes and comments into '
                'proposals you accept or reject; your own later edits are kept.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.reviewer = QLineEdit()
        self.reviewer.setAccessibleName(self.tr('Reviewer name'))
        self.reviewer.setToolTip(
            self.tr('Optional. Recorded with the review copy so you can tell copies apart.')
        )
        form.addRow(self.tr('Reviewer:'), self.reviewer)
        self.restriction = QComboBox()
        self.restriction.setAccessibleName(self.tr('Editing allowed in the review copy'))
        self.restriction.setToolTip(
            self.tr('Word enforces this through Restrict Editing; no password is set.')
        )
        for key, label in RESTRICTION_LABELS:
            self.restriction.addItem(self.tr(label), key)
        form.addRow(self.tr('Editing allowed:'), self.restriction)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(self.tr('Choose file…'))
        buttons.button(QDialogButtonBox.Ok).setAccessibleName(self.tr('Choose where to save the review copy'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {'reviewer': self.reviewer.text().strip(), 'restrict': self.restriction.currentData()}


def merge_summary(report):
    """Plain-language merge report for the result dialog."""
    lines = list(report.get('summary', []))
    if report.get('proposal_summaries'):
        lines += ['', 'Proposed changes (review them in Proposed changes):']
        lines += [f'  • {line}' for line in report['proposal_summaries']]
    comments = report.get('comments', {})
    if comments.get('marked_done'):
        lines += [
            '',
            f'The editor marked {len(comments["marked_done"])} of your comments as done; they stay open.',
        ]
    if report.get('conflicts'):
        lines += ['', 'Not placed, because you changed or removed that text after sending:']
        lines += [f'  • {item["message"]}' for item in report['conflicts']]
    if report.get('losses'):
        lines += ['', 'Not merged from the Word file (review these in Word):']
        lines += [
            f'  • {item["message"]}' + (f' ({item["target"]})' if item.get('target') else '')
            for item in report['losses']
        ]
    return '\n'.join(lines)


class DocxReviewPanels:
    """Main-window behavior for the Word review round trip (mixed into ``MainWindow``)."""

    def _review_document_ready(self, title):
        if self.path is None or self.path.suffix.lower() != '.byax':
            self.message(
                title, self.tr('Save the document as a ByAuthorX file first, so the review snapshot is kept.')
            )
            return False
        return bool(self.flush_edits())

    def send_for_review(self, *, target=None, values=None):
        """Write a Word review copy and record its snapshot; returns the review package ID or None."""
        title = self.tr('Send for review (Word)')
        if not self._review_document_ready(title):
            return None
        if values is None:
            dialog = SendForReviewDialog(self)
            if dialog.exec() != QDialog.Accepted:
                return None
            values = dialog.values()
        if target is None:
            default = self.path.with_name(f'{self.path.stem} (review).docx')
            target, _ = QFileDialog.getSaveFileName(
                self, self.tr('Save review copy'), str(default), self.tr('Word document (*.docx)')
            )
            if not target:
                return None
        target = Path(target)
        if target.suffix.lower() != '.docx':
            target = target.with_suffix('.docx')
        try:
            data, operations, info = docx_review.prepare_review_package(
                self.doc,
                restrict=values.get('restrict', 'tracked_changes'),
                reviewer=values.get('reviewer', ''),
                file_name=target.name,
            )
        except Exception as exc:
            self.error(exc)
            return None
        if not self.commit(operations):
            return None
        try:
            docx_review.publish_review_copy(data, target, overwrite=True)
        except Exception as exc:
            self.error(exc)
            return None
        self.save(wait=True)
        lines = [
            self.tr('Review copy saved to {0}.').format(target),
            self.tr('Send it to your editor. When it comes back, choose File > Merge reviewed copy….'),
        ]
        if info['losses']:
            lines += ['', self.tr('Not included in the Word copy (it stays in your document):')]
            lines += [f'  • {loss["message"]}' for loss in info['losses']]
        self.status_label.setText(self.tr('Review copy saved; its snapshot is recorded in this document.'))
        self.report(title, '\n'.join(lines))
        return info['review_id']

    def merge_reviewed_word_copy(self, *, source=None, review_id=None):
        """Merge a returned Word copy as pending proposals and comments; returns the merge report."""
        title = self.tr('Merge reviewed copy')
        if not self._review_document_ready(title):
            return None
        packages = self.doc.get('metadata', {}).get('review_packages', {})
        if not packages:
            self.message(
                title,
                self.tr(
                    'This document has no review copy to merge. Use File > Send for review (Word)… first.'
                ),
            )
            return None
        if source is None:
            source, _ = QFileDialog.getOpenFileName(
                self, self.tr('Merge reviewed copy'), str(self.path.parent), self.tr('Word document (*.docx)')
            )
            if not source:
                return None
        try:
            operations, report = docx_review.plan_review_merge(self.doc, source, review_id=review_id)
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
                self.tr('The file does not name its review copy. Which copy was sent?'),
                labels,
                0,
                False,
            )
            if not accepted:
                return None
            return self.merge_reviewed_word_copy(source=source, review_id=choices[labels.index(label)])
        except Exception as exc:
            self.error(exc)
            return None
        if not operations:
            self.message(title, self.tr('This returned copy was already merged.'))
            return report
        if not self.commit(operations):
            return None
        self.inspector.setCurrentIndex(0)
        first = next(
            (
                p['id']
                for p in self.doc['review']['proposals']
                if p.get('kind') == 'docx_review'
                and p.get('review_source', {}).get('sha256') == report['review_merge']['sha256']
            ),
            None,
        )
        for row in range(self.review_list.count()):
            kind, record = self.review_list.item(row).data(Qt.UserRole) or (None, {})
            if kind == 'proposals' and record.get('id') == first:
                self.review_list.setCurrentRow(row)
                break
        self.status_label.setText(
            self.tr('Reviewed copy merged; review the editor’s changes in Proposed changes.')
        )
        self.report(title, merge_summary(report))
        return report
