# ruff: noqa: BLE001
"""Synced-folder notices and the conflicted-copy review flow.

On open, a non-modal info bar says when the document sits in a synced folder with risky patterns
or has conflicted copies beside it. Reviewing a copy imports it as a branch and registers the merge
as a pending proposal, so the existing Proposed changes list and merge-conflict resolution handle
the rest. Nothing is merged, moved or deleted without an explicit action.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import engine, sync_safety


def describe_status(status):
    """Plain-language summary of a sync status report, for the info bar and the details dialog."""
    sync = status['sync']
    lines = []
    if sync['synced']:
        lines.append(
            f'This document is in a {sync["provider_name"]} folder ({sync["confidence"]} confidence).'
        )
        lines.extend('  ' + item for item in sync['evidence'])
    else:
        lines.append('This document does not appear to be in a cloud-synced folder.')
    copies = status['conflicts']
    if copies['copies']:
        lines.append('')
        lines.append('Conflicted copies of this document:')
        for entry in copies['copies']:
            who = f' by {entry["owner"]}' if entry.get('owner') else ''
            lines.append(f'  {entry["name"]} ({entry["pattern_label"]}{who})')
    if copies['pending']:
        lines.append('')
        lines.append('Files that may be conflicted copies but are not fully downloaded yet:')
        lines.extend(f'  {entry["name"]}' for entry in copies['pending'])
    if status['risks']:
        lines.append('')
        lines.append('Things to know:')
        for risk in status['risks']:
            marker = 'Warning' if risk['severity'] == 'warning' else 'Note'
            lines.append(f'  {marker}: {risk["message"]}')
            if risk.get('advice'):
                lines.append(f'    {risk["advice"]}')
    return '\n'.join(lines)


class SyncInfoBar(QFrame):
    """A one-line, dismissible notice above the editor. It never blocks typing."""

    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self.status = None
        self._dismissed = set()
        self.setObjectName('syncInfoBar')
        self.setAccessibleName(self.tr('Synced folder notice'))
        self.setAccessibleDescription(
            self.tr('Shown when this document is in a synced folder with risks or has conflicted copies.')
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self.text = QLabel()
        self.text.setObjectName('syncInfoText')
        self.text.setWordWrap(True)
        self.text.setAccessibleName(self.tr('Synced folder message'))
        row.addWidget(self.text, 1)
        self.review_button = QPushButton(self.tr('Review conflicted copy…'))
        self.review_button.setAccessibleName(self.tr('Review conflicted copy'))
        self.review_button.setToolTip(self.tr('Merge a conflicted copy into this document as a proposal'))
        self.review_button.clicked.connect(lambda: window.review_conflicted_copies())
        row.addWidget(self.review_button)
        self.details_button = QPushButton(self.tr('Details…'))
        self.details_button.setAccessibleName(self.tr('Synced folder details'))
        self.details_button.clicked.connect(lambda: window.sync_safety_details())
        row.addWidget(self.details_button)
        self.dismiss_button = QPushButton(self.tr('Dismiss'))
        self.dismiss_button.setAccessibleName(self.tr('Dismiss synced folder notice'))
        self.dismiss_button.setToolTip(self.tr('Hide this notice until the document changes on disk'))
        self.dismiss_button.clicked.connect(self.dismiss)
        row.addWidget(self.dismiss_button)
        self.hide()

    def key(self):
        copies = self.status['conflicts']
        return (
            self.status['path'],
            tuple(sorted((e['path'], e.get('revision')) for e in copies['copies'] + copies['pending'])),
            tuple(sorted(r['code'] for r in self.status['risks'] if r['severity'] == 'warning')),
        )

    def dismiss(self):
        if self.status:
            self._dismissed.add(self.key())
        self.hide()

    def refresh(self, path):
        """Check the document's folder. Any failure leaves the bar hidden; opening never depends on it."""
        self.status = None
        self.hide()
        if path is None or Path(path).suffix.lower() != '.byax':
            return
        try:
            status = sync_safety.sync_status(path, light=True)
        except Exception:
            return
        self.status = status
        if not status['attention'] or self.key() in self._dismissed:
            return
        copies = status['conflicts']['copies']
        sync = status['sync']
        where = self.tr('in a {0} folder').format(sync['provider_name']) if sync['synced'] else ''
        if copies:
            message = self.tr(
                '{0} conflicted copy file(s) of this document were found {1}. '
                'Review one to merge its changes as a proposal; nothing is deleted.'
            ).format(len(copies), where or self.tr('beside it'))
        else:
            warning = next((r for r in status['risks'] if r['severity'] == 'warning'), None)
            message = (
                self.tr('This document is {0}. {1}').format(where, warning['message'])
                if warning and where
                else (warning['message'] if warning else '')
            )
        self.text.setText(message)
        self.review_button.setVisible(bool(copies))
        self.setVisible(bool(message))


class ConflictedCopiesDialog(QDialog):
    """Pick a conflicted copy, merge it as a proposal, or archive one that is already merged."""

    def __init__(self, window):
        super().__init__(window)
        self.window_ref = window
        self.setWindowTitle(self.tr('Conflicted copies'))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'Your sync service made these files when this document was changed in two places. '
                'Merging adds their changes to Proposed changes for you to review; nothing is '
                'deleted or replaced.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.copies = QListWidget()
        self.copies.setAccessibleName(self.tr('Conflicted copies of this document'))
        self.copies.currentRowChanged.connect(self.show_details)
        layout.addWidget(self.copies, 1)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setAccessibleName(self.tr('Selected copy details'))
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        self.merge_button = QPushButton(self.tr('Merge into Proposed changes'))
        self.merge_button.clicked.connect(self.merge_selected)
        row.addWidget(self.merge_button)
        self.archive_button = QPushButton(self.tr('Move merged copy to archive folder…'))
        self.archive_button.setToolTip(
            self.tr('Moves the file into a "{0}" folder beside the document. It is never deleted.').format(
                sync_safety.ARCHIVE_FOLDER
            )
        )
        self.archive_button.clicked.connect(self.archive_selected)
        row.addWidget(self.archive_button)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.reload()

    def reload(self):
        window = self.window_ref
        self.found = sync_safety.find_conflicted_copies(window.path)
        merged = set(engine.revision_ancestors(window.doc)) if window.doc else set()
        self.copies.clear()
        for entry in self.found['copies'] + self.found['pending']:
            ready = entry in self.found['copies']
            label = entry['name'] if ready else self.tr('{0} (still downloading)').format(entry['name'])
            if entry.get('revision') in merged:
                label += self.tr(' (already merged)')
            self.copies.addItem(label)
            item = self.copies.item(self.copies.count() - 1)
            item.setData(Qt.UserRole, dict(entry, ready=ready, merged=entry.get('revision') in merged))
        if self.copies.count():
            self.copies.setCurrentRow(0)
        else:
            self.detail.setText(self.tr('No conflicted copies were found beside this document.'))
            self.merge_button.setEnabled(False)
            self.archive_button.setEnabled(False)

    def selected(self):
        item = self.copies.currentItem()
        return item.data(Qt.UserRole) if item else None

    def show_details(self, *_):
        entry = self.selected()
        self.merge_button.setEnabled(bool(entry and entry['ready'] and not entry['merged']))
        self.archive_button.setEnabled(bool(entry and entry['ready'] and entry['merged']))
        if not entry:
            return
        parts = [entry['pattern_label']]
        if entry.get('owner'):
            parts.append(self.tr('from {0}').format(entry['owner']))
        if entry.get('modified'):
            parts.append(self.tr('modified {0}').format(entry['modified']))
        if entry.get('size') is not None:
            parts.append(self.tr('{0:,} bytes').format(entry['size']))
        self.detail.setText(' · '.join(parts))

    def merge_selected(self):
        entry = self.selected()
        if entry and entry['ready'] and self.window_ref.merge_conflicted_copy(entry['path']):
            self.accept()

    def archive_selected(self):
        entry = self.selected()
        if entry and entry['ready'] and self.window_ref.archive_conflicted_copy(entry['path']):
            self.reload()


class SyncPanels:
    """Main-window behavior for synced-folder safety (mixed into ``MainWindow``)."""

    def build_sync_bar(self, layout):
        self.sync_bar = SyncInfoBar(self)
        layout.addWidget(self.sync_bar)

    def refresh_sync_bar(self):
        bar = getattr(self, 'sync_bar', None)
        if bar is not None:
            bar.refresh(self.path)

    def sync_safety_details(self):
        if self.path is None:
            self.message(self.tr('Synced folder safety'), self.tr('Save the document first.'))
            return
        try:
            status = sync_safety.sync_status(self.path, light=True)
        except Exception as exc:
            self.error(exc)
            return
        self.report(self.tr('Synced folder safety'), describe_status(status))

    def review_conflicted_copies(self):
        if self.path is None or self.path.suffix.lower() != '.byax':
            self.message(
                self.tr('Conflicted copies'),
                self.tr('Save the document as a ByAuthorX file to check for copies.'),
            )
            return None
        if not self.flush_edits():
            return None
        dialog = ConflictedCopiesDialog(self)
        dialog.exec()
        return dialog

    def merge_conflicted_copy(self, copy_path):
        """Import a conflicted copy as a branch and register its merge as a pending proposal."""
        if not self.flush_edits():
            return False
        original, was_dirty = self.doc, self.dirty
        try:
            candidate, info = sync_safety.prepare_merge(self.doc, copy_path)
            self.doc = candidate
            self.dirty = True
            committed = self.commit(
                [
                    {
                        'type': 'propose_merge',
                        'source': info['branch'],
                        'explanation': f'Merge conflicted copy "{Path(copy_path).name}" into '
                        f'{candidate["history"].get("current_branch", "main")}',
                    }
                ]
            )
        except Exception as exc:
            self.doc, self.dirty = original, was_dirty
            self.error(exc)
            return False
        if not committed:
            self.doc, self.dirty = original, was_dirty
            return False
        proposal = next(
            (p for p in reversed(self.doc['review']['proposals']) if p.get('kind') == 'merge'), None
        )
        self.inspector.setCurrentIndex(0)
        if proposal:
            for row in range(self.review_list.count()):
                kind, record = self.review_list.item(row).data(Qt.UserRole) or (None, {})
                if kind == 'proposals' and record.get('id') == proposal['id']:
                    self.review_list.setCurrentRow(row)
                    break
        conflicts = len(proposal.get('conflicts', [])) if proposal else 0
        self.status_label.setText(
            self.tr('Merge proposed from the conflicted copy; review it in Proposed changes.')
        )
        if conflicts:
            self.message(
                self.tr('Merge needs your decision'),
                self.tr(
                    '{0} change(s) touch the same section in both versions. Select the proposal, then '
                    'choose Review > Versions and drafts > Resolve selected merge conflicts… to keep one '
                    'version for each. Accepting is a separate step.'
                ).format(conflicts),
            )
        return True

    def archive_conflicted_copy(self, copy_path):
        if self.dirty:
            self.message(
                self.tr('Conflicted copies'),
                self.tr('Save the document first so the merged result is on disk before moving the copy.'),
            )
            return False
        answer = QMessageBox.question(
            self,
            self.tr('Move conflicted copy?'),
            self.tr(
                'Move {0} into the "{1}" folder beside the document? The file is kept, not deleted.'
            ).format(Path(copy_path).name, sync_safety.ARCHIVE_FOLDER),
        )
        if answer != QMessageBox.Yes:
            return False
        try:
            sync_safety.archive_conflicted_copy(self.path, copy_path)
        except Exception as exc:
            self.error(exc)
            return False
        self.refresh_sync_bar()
        return True
