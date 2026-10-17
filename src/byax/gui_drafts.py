# ruff: noqa: BLE001
"""Named drafts in the desktop window: save a draft with a note, compare two drafts scene by scene, bring changes.

The words in the interface are draft and compare. Underneath they are branches and the semantic
comparison (``byax.drafts``); bringing changes is ``propose_merge``, a proposal reviewed in Proposed changes.
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import drafts
from .errors import ByaxError

STATUS_TEXT = {'unchanged': 'Unchanged', 'changed': 'Changed', 'added': 'Added', 'removed': 'Removed'}


def saved_text(value):
    if not value:
        return ''
    try:
        return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return value


class SaveDraftDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle(self.tr('Save draft as'))
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'Save the document as it is now under a name. You keep writing in the current draft; '
                'switch to the saved one later to read or continue it.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setAccessibleName(self.tr('Draft name'))
        self.name.setToolTip(self.tr('A short name such as First draft or After the beta read'))
        self.name.setMaxLength(drafts.MAX_NAME)
        form.addRow(self.tr('Name'), self.name)
        self.note = QPlainTextEdit()
        self.note.setAccessibleName(self.tr('Draft note'))
        self.note.setToolTip(self.tr('Optional note about this draft'))
        self.note.setMaximumHeight(110)
        form.addRow(self.tr('Note'), self.note)
        layout.addLayout(form)
        self.switch = QCheckBox(self.tr('Switch to the new draft now'))
        self.switch.setToolTip(self.tr('Continue writing in the new draft instead of the current one'))
        layout.addWidget(self.switch)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CompareDraftsDialog(QDialog):
    """Two drafts, side by side, one row per binder item."""

    def __init__(self, window, source=None, target=None):
        super().__init__(window)
        self.host = window
        self.setWindowTitle(self.tr('Compare drafts'))
        self.resize(760, 560)
        self.comparison = None
        names = [entry['name'] for entry in drafts.list_drafts(window.doc)]
        current = drafts.current_draft(window.doc)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.source = QComboBox()
        self.target = QComboBox()
        for box, label, tip in (
            (self.source, self.tr('From draft'), self.tr('The draft to compare from')),
            (self.target, self.tr('To draft'), self.tr('The draft to compare to')),
        ):
            box.addItems(names)
            box.setAccessibleName(label)
            box.setToolTip(tip)
            caption = QLabel(label)
            caption.setBuddy(box)
            row.addWidget(caption)
            row.addWidget(box, 1)
        others = [n for n in names if n != current]
        self.source.setCurrentText(source or (others[0] if others else current))
        self.target.setCurrentText(target or current)
        self.source.currentIndexChanged.connect(self.update_comparison)
        self.target.currentIndexChanged.connect(self.update_comparison)
        layout.addLayout(row)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName(self.tr('Comparison summary'))
        layout.addWidget(self.summary)
        self.table = QTreeWidget()
        self.table.setColumnCount(5)
        self.table.setHeaderLabels(
            [
                self.tr('Item'),
                self.tr('Result'),
                self.tr('Words before'),
                self.tr('Words after'),
                self.tr('Change'),
            ]
        )
        self.table.setRootIsDecorated(False)
        self.table.setAccessibleName(self.tr('Scene by scene comparison'))
        self.table.setToolTip(
            self.tr('One row per binder item. Select a row for details; Enter shows it in the editor.')
        )
        self.table.itemSelectionChanged.connect(self.show_details)
        self.table.itemActivated.connect(self.go_to)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(110)
        self.details.setAccessibleName(self.tr('Details of the selected item'))
        layout.addWidget(self.details)
        buttons = QDialogButtonBox()
        self.bring_button = buttons.addButton(
            self.tr('Bring changes from draft…'), QDialogButtonBox.ActionRole
        )
        self.bring_button.setToolTip(
            self.tr(
                'Propose the changes saved in the "From" draft for the current draft. Nothing changes until you accept.'
            )
        )
        self.bring_button.clicked.connect(self.bring)
        close = buttons.addButton(QDialogButtonBox.Close)
        close.setToolTip(self.tr('Close'))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_comparison()

    def update_comparison(self, *_):
        self.table.clear()
        self.details.clear()
        source, target = self.source.currentText(), self.target.currentText()
        current = drafts.current_draft(self.host.doc)
        self.bring_button.setEnabled(source != current and target == current)
        if source == target:
            self.comparison = None
            self.summary.setText(self.tr('Choose two different drafts.'))
            return
        try:
            self.comparison = drafts.compare_drafts(self.host.doc, source, target)
        except ByaxError as exc:
            self.comparison = None
            self.summary.setText(str(exc))
            return
        result = self.comparison
        total = result['summary']
        self.summary.setText(
            self.tr(
                '{0} changed, {1} added, {2} removed, {3} unchanged. Words: {4:,} to {5:,} ({6:+,}).'
            ).format(
                total['changed'],
                total['added'],
                total['removed'],
                total['unchanged'],
                total['words_before'],
                total['words_after'],
                total['word_delta'],
            )
        )
        if not result['binder']:
            self.details.setPlainText(
                self.tr(
                    'This document has no binder, so drafts compare as a whole. Create a binder to compare scene by scene.'
                )
                + '\n'
                + '\n'.join(result['document'])
            )
            return
        for entry in result['items']:
            item = QTreeWidgetItem(
                [
                    '    ' * entry['depth'] + (entry['title'] or self.tr('Untitled')),
                    self.tr(STATUS_TEXT[entry['status']]),
                    f'{entry["words_before"]:,}',
                    f'{entry["words_after"]:,}',
                    f'{entry["word_delta"]:+,}' if entry['word_delta'] else '0',
                ]
            )
            for column in (2, 3, 4):
                item.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
            item.setData(0, Qt.UserRole, entry)
            self.table.addTopLevelItem(item)
        for column in range(1, 5):
            self.table.resizeColumnToContents(column)

    def show_details(self):
        item = self.table.currentItem()
        if item is None:
            return
        entry = item.data(0, Qt.UserRole)
        lines = [self.tr(STATUS_TEXT[entry['status']])]
        if entry.get('previous_title'):
            lines.append(self.tr('Renamed from “{0}”.').format(entry['previous_title']))
        if entry.get('fields'):
            lines.append(self.tr('Changed: {0}.').format(', '.join(entry['fields'])))
        if entry.get('moved'):
            lines.append(self.tr('Moved within the binder.'))
        if entry['status'] == 'changed':
            lines.append(
                self.tr('{0} wording edit(s), {1} block(s) added, {2} removed.').format(
                    entry['edits'], entry['blocks_added'], entry['blocks_removed']
                )
            )
            lines.extend(entry['summary'])
        self.details.setPlainText('\n'.join(lines))

    def go_to(self, item, column=0):
        entry = item.data(0, Qt.UserRole)
        row = self.host.binder_panel.rows.get(entry['id']) if entry['id'] else None
        if row is None and entry['id']:
            from . import project

            row = next((r for r in project.items(self.host.doc) if r['id'] == entry['id']), None)
        if row and entry['status'] != 'removed':
            self.host.navigate_target(row['heading'])

    def bring(self):
        source = self.source.currentText()
        if self.host.bring_changes_from(source):
            self.accept()


class DraftsDialog(QDialog):
    """Every saved draft with its note, and what can be done with it."""

    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self.setWindowTitle(self.tr('Drafts'))
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        self.table = QTreeWidget()
        self.table.setColumnCount(4)
        self.table.setHeaderLabels([self.tr('Draft'), self.tr('Saved'), self.tr('Words'), self.tr('Note')])
        self.table.setRootIsDecorated(False)
        self.table.setAccessibleName(self.tr('Saved drafts'))
        self.table.setToolTip(self.tr('The drafts of this document. The current draft is marked.'))
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.buttons = {}
        for key, label, tip, slot in (
            (
                'switch',
                self.tr('Switch to draft'),
                self.tr('Make the selected draft the one you write in'),
                self.switch,
            ),
            (
                'compare',
                self.tr('Compare…'),
                self.tr('Compare the selected draft with the current draft'),
                self.compare,
            ),
            (
                'bring',
                self.tr('Bring changes…'),
                self.tr('Propose the selected draft’s changes for the current draft'),
                self.bring,
            ),
            ('save', self.tr('Save draft as…'), self.tr('Save the document as a new draft'), self.save),
        ):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.buttons[key] = button
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.table.itemSelectionChanged.connect(self.sync)
        self.fill()

    def fill(self):
        self.table.clear()
        for entry in drafts.list_drafts(self.host.doc):
            item = QTreeWidgetItem(
                [
                    entry['name'] + (self.tr(' (current)') if entry['current'] else ''),
                    saved_text(entry['saved_at']),
                    f'{entry["words"]:,}' if entry['words'] is not None else '—',
                    entry['note'].replace('\n', ' '),
                ]
            )
            item.setData(0, Qt.UserRole, entry['name'])
            item.setToolTip(3, entry['note'])
            self.table.addTopLevelItem(item)
        for column in range(3):
            self.table.resizeColumnToContents(column)
        self.table.setCurrentItem(self.table.topLevelItem(0))
        self.sync()

    def selected(self):
        item = self.table.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def sync(self):
        name = self.selected()
        other = name is not None and name != drafts.current_draft(self.host.doc)
        for key in ('switch', 'compare', 'bring'):
            self.buttons[key].setEnabled(other)

    def switch(self):
        name = self.selected()
        if name and self.host.switch_to_draft(name):
            self.fill()

    def compare(self):
        name = self.selected()
        if name:
            CompareDraftsDialog(self.host, source=name).exec()

    def bring(self):
        name = self.selected()
        if name and self.host.bring_changes_from(name):
            self.accept()

    def save(self):
        if self.host.save_draft_as():
            self.fill()


class DraftPanels:
    def save_draft_as(self):
        if not self.flush_edits():
            return False
        dialog = SaveDraftDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return False
        try:
            self.doc, _ = drafts.save_draft(
                self.doc, dialog.name.text(), dialog.note.toPlainText(), switch=dialog.switch.isChecked()
            )
        except Exception as exc:
            self.error(exc)
            return False
        self.dirty = True
        self.update_status()
        self.update_sidebar()
        self.status_label.setText(self.tr('Draft saved: ') + dialog.name.text().strip())
        return True

    def drafts_dialog(self):
        if self.flush_edits():
            DraftsDialog(self).exec()

    def compare_drafts_dialog(self):
        if not self.flush_edits():
            return
        if len(drafts.list_drafts(self.doc)) < 2:
            self.message(
                self.tr('Compare drafts'),
                self.tr('Save a draft first (Review, Save draft as…), then compare it.'),
            )
            return
        CompareDraftsDialog(self).exec()

    def switch_to_draft(self, name):
        """Make a saved draft the working document; the undo history of the previous draft is dropped."""
        if not self.flush_edits():
            return False
        try:
            self.doc, _ = drafts.switch_draft(self.doc, name)
        except Exception as exc:
            self.error(exc)
            return False
        self._undo.clear()
        self._redo.clear()
        self.dirty = True
        self.refresh()
        self.status_label.setText(self.tr('Now writing in draft: ') + name)
        return True

    def bring_changes_from(self, name):
        """Propose the changes saved in another draft; nothing changes until the proposal is accepted."""
        if not self.flush_edits():
            return False
        try:
            operation = drafts.bring_operation(self.doc, name)
        except Exception as exc:
            self.error(exc)
            return False
        if self.commit([operation]):
            self.status_label.setText(self.tr('Changes from “{0}” are in Proposed changes').format(name))
            return True
        return False
