# ruff: noqa: BLE001
"""Session-only file views and explicit save/export affordances."""

from itertools import pairwise
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .formats import FORMATS, canonical_format, project, supports


class FileViews:
    def build_file_views(self, toolbar, menus, workspace_layout):
        self.file_view = 'byax'
        self._projection_cache = None
        self._view_report = {'losses': [], 'hidden': [], 'locked': []}
        self.file_view_box = QComboBox()
        self.file_view_box.setAccessibleName(self.tr('File view'))
        self.file_view_box.setToolTip(self.tr('Edit the master through a selected file format'))
        view_menu = menus['&View'].addMenu(self.tr('File view'))
        self._view_actions = {}
        for ident, entry in FORMATS.items():
            self.file_view_box.addItem(self.tr(entry['label']), ident)
            action = self.action(
                self.tr(entry['label']), lambda checked=False, v=ident: self.set_file_view(v), checkable=True
            )
            view_menu.addAction(action)
            self._view_actions[ident] = action
        self.file_view_box.activated.connect(lambda: self.set_file_view(self.file_view_box.currentData()))
        toolbar.addWidget(self.file_view_box)
        self.source_mode = QComboBox()
        self.source_mode.setAccessibleName(self.tr('Rendered or source'))
        self.source_mode.setToolTip(self.tr('Source displays the exact exported text, read-only'))
        self.source_mode.addItems([self.tr('Rendered'), self.tr('Source')])
        self.source_mode.activated.connect(self.set_source_mode)
        toolbar.addWidget(self.source_mode)
        self.source_editor = QPlainTextEdit()
        self.source_editor.setReadOnly(True)
        self.source_editor.setFont(QFont('monospace', 11))
        self.source_editor.setAccessibleName(self.tr('Exported source (read-only)'))
        self.stack.addWidget(self.source_editor)
        self.view_status = QPushButton()
        self.view_status.setAccessibleName(self.tr('Backing file, file view and hidden features'))
        self.view_status.setToolTip(self.tr('Show located format limitations'))
        self.view_status.clicked.connect(self.show_view_losses)
        self.statusBar().addWidget(self.view_status)
        self.export_offer = QWidget()
        self.export_offer.setAccessibleName(self.tr('Saved document export offer'))
        row = QHBoxLayout(self.export_offer)
        self.export_offer_text = QLabel()
        self.export_offer_text.setWordWrap(True)
        row.addWidget(self.export_offer_text, 1)
        for title, callback in (
            ('Export…', self.export),
            ('Details', lambda: self.show_view_losses(self._offered_format)),
            ('Not now', self.export_offer.hide),
            ("Don't ask for this document", lambda: self.suppress_export_offer(False)),
            ("Don't ask again", lambda: self.suppress_export_offer(True)),
        ):
            button = QPushButton(self.tr(title))
            button.clicked.connect(callback)
            row.addWidget(button)
        workspace_layout.insertWidget(1, self.export_offer)
        self.export_offer.hide()
        menus['&File'].addAction(self.action(self.tr('Convert to ByAuthorX…'), self.save_as))
        menus['&Tools'].addAction(
            self.action(self.tr('Markdown opening preference…'), self.markdown_preferences)
        )
        # Commands carry explicit capability requirements; unsupported commands remain discoverable.
        requirements = FORMATS['byax']['commands']
        self._capability_actions = []
        for menu in menus.values():
            for action in menu.actions():
                capability = next(
                    (cap for label, cap in requirements.items() if action.text() == self.tr(label)), None
                )
                if action.text() in [self.tr('Align ' + align) for align in ('left', 'center', 'right')] + [
                    self.tr('Justify')
                ]:
                    capability = 'block_attrs.alignment'
                if menu is menus['&Table'] and not capability:
                    capability = 'blocks.table'
                if capability:
                    self._capability_actions.append((action, capability, action.toolTip()))

    def in_place_markdown(self):
        return bool(self.path and self.path.suffix.lower() in ('.md', '.markdown'))

    def restore_file_view(self):
        self.file_view = (
            'md'
            if self.in_place_markdown()
            else self.settings.value(f'views/{self.doc["id"]}/format', 'byax')
        )
        if self.file_view not in FORMATS:
            self.file_view = 'byax'
        self._projection_cache = None
        self.source_mode.setCurrentIndex(0)
        self._restore_source = FORMATS[self.file_view]['source'] and self.settings.value(
            f'views/{self.doc["id"]}/{self.file_view}/source', False, type=bool
        )
        self.stack.setCurrentIndex(0)
        self.export_offer.hide()

    def projection(self):
        key = (self.doc['id'], self.doc['revision'], self.file_view)
        if self._projection_cache is None or self._projection_cache[0] != key:
            if self.file_view == 'byax':
                self._projection_cache = (key, self.doc, {'losses': [], 'hidden': [], 'locked': []})
            else:
                display = {**self.doc, 'history': {}, 'requests': {}}
                self._projection_cache = (key, *project(display, self.file_view))
        self._view_report = self._projection_cache[2]
        return self._projection_cache[1]

    def set_file_view(self, view):
        view = canonical_format(view)
        if self.in_place_markdown() and view != 'md':
            return False
        if not self.flush_edits():
            return False
        target = self.editor.current_id()
        scroll = self.editor.verticalScrollBar().value()
        self.file_view = view
        self._restore_source = FORMATS[view]['source'] and self.settings.value(
            f'views/{self.doc["id"]}/{view}/source', False, type=bool
        )
        self.settings.setValue(f'views/{self.doc["id"]}/format', view)
        self.source_mode.setCurrentIndex(0)
        self.stack.setCurrentIndex(0)
        self.refresh()
        if target:
            self.editor.navigate(target)
        self.editor.verticalScrollBar().setValue(scroll)
        self.motion.fade(self.editor)
        return True

    def sync_file_view(self):
        entry = FORMATS[self.file_view]
        self.file_view_box.setCurrentIndex(self.file_view_box.findData(self.file_view))
        self.file_view_box.setEnabled(not self.in_place_markdown())
        self.file_view_box.setToolTip(
            self.tr('Convert to a ByAuthorX document to use other views')
            if self.in_place_markdown()
            else self.tr('Edit the master through a selected file format')
        )
        self.source_mode.setEnabled(entry['source'])
        self.style_box.setEnabled(self.file_view != 'txt')
        from .formats import MD_STYLES

        for index in range(self.style_box.count()):
            item = self.style_box.model().item(index)
            if item is not None:
                item.setEnabled(entry['styles'] or self.style_box.itemData(index) in MD_STYLES)
        from PySide6.QtGui import QAction

        for action in self.findChildren(QAction):
            if action.text() in (self.tr('Pages'), self.tr('Writing / pages')):
                action.setEnabled(entry['paged'])
                action.setToolTip(
                    self.tr('Switch between writing and page preview')
                    if entry['paged']
                    else self.tr('This format has no pages')
                )
                shortcut = action.shortcut().toString()
                if shortcut:
                    action.setToolTip(action.toolTip() + ' (' + shortcut + ')')
        self.editor.view_format = self.file_view
        self.editor.view_locked = set(self._view_report['locked'])
        visible = [b['id'] for b in self.projection()['blocks']]
        positions = {b['id']: i for i, b in enumerate(self.doc['blocks'])}
        self.editor.view_gaps = {(a, b) for a, b in pairwise(visible) if positions[b] != positions[a] + 1}
        self.editor.view_gaps |= {(b, a) for a, b in self.editor.view_gaps}
        self.editor.set_hand_marks(self.hand_marks and self.file_view == 'byax')
        self.hand_marks_action.setEnabled(self.file_view == 'byax')
        for ident, action in self._view_actions.items():
            action.setChecked(ident == self.file_view)
            action.setEnabled(not self.in_place_markdown() or ident == 'md')
        for action, capability, tooltip in self._capability_actions:
            enabled = supports(self.file_view, capability)
            action.setEnabled(enabled)
            action.setToolTip(
                tooltip if enabled else self.tr('Not available in the {0} view').format(entry['label'])
            )
            action.setStatusTip(action.toolTip())
        count = len(self._view_report.get('losses', []))
        backing = str(self.path) if self.path else self.tr('Unsaved ByAuthorX master')
        self.view_status.setText(
            self.tr('{0} · Viewing as {1} · {2} features hidden or simplified').format(
                backing, entry['label'], count
            )
        )
        if entry['fidelity'] == 'approximate':
            self.view_label.setText(
                self.tr('Approximate preview. Fonts and page breaks may differ in other applications.')
            )
        else:
            self.view_label.setText(entry['label'])
        if (
            not any(b.get('text') or b.get('rows') for b in self.projection()['blocks'])
            and self._view_report['hidden']
        ):
            self.status_label.setText(
                self.tr(
                    '{0} hidden items remain in this document. Switch to the Full view to see them.'
                ).format(len(self._view_report['hidden']))
            )

    def set_source_mode(self, index):
        from .exchange import source_text

        if not self.flush_edits():
            return
        index = int(bool(index and FORMATS[self.file_view]['source']))
        self.source_mode.setCurrentIndex(index)
        self.settings.setValue(f'views/{self.doc["id"]}/{self.file_view}/source', bool(index))
        if index:
            try:
                self.source_editor.setPlainText(source_text(self.doc, self.file_view))
                self.stack.setCurrentIndex(2)
            except Exception as exc:
                self.error(exc)
        else:
            self.stack.setCurrentIndex(0)

    def navigate_target(self, target):
        if self.editor.navigate(target):
            return True
        if self.file_view != 'byax' and self.set_file_view('byax'):
            self.status_label.setText(self.tr('Showing previously hidden content in the Full view'))
            return self.editor.navigate(target)
        return False

    def show_view_losses(self, format=None):
        from .exchange import conversion_preview

        report = conversion_preview(self.doc, format) if isinstance(format, str) else self._view_report
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Hidden and simplified features'))
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        items.setAccessibleName(self.tr('Located format limitations; activate to show in Full view'))
        for loss in report.get('losses', []):
            items.addItem(str(loss.get('target', 'document')) + ': ' + loss['message'])
            items.item(items.count() - 1).setData(Qt.UserRole, loss.get('target'))

        def navigate(item):
            target = item.data(Qt.UserRole)
            if not self.editor.navigate(target) and self.set_file_view('byax'):
                self.editor.navigate(target)
            dialog.accept()

        items.itemActivated.connect(navigate)
        layout.addWidget(items)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def offer_export_after_save(self):
        if (
            self.in_place_markdown()
            or not self.settings.value('export/offerAfterSave', True, type=bool)
            or self.settings.value(f'export/{self.doc["id"]}/suppressed', False, type=bool)
        ):
            return
        fmt = (
            self.file_view
            if self.file_view != 'byax'
            else self.doc.get('metadata', {}).get('import', {}).get('format', 'byax')
        )
        fmt = canonical_format(fmt) if fmt != 'doc' else 'docx'
        if fmt == 'byax':
            return
        self._offered_format = fmt
        from .exchange import conversion_preview

        count = len(conversion_preview(self.doc, fmt)['losses'])
        self.export_offer_text.setText(
            self.tr("Saved. Export as {0} too? {1} features won't carry over.").format(
                FORMATS[fmt]['label'], count
            )
        )
        self.export_offer.show()
        from PySide6.QtGui import QAccessible, QAccessibleEvent

        QAccessible.updateAccessibility(QAccessibleEvent(self.export_offer, QAccessible.Event.Alert))

    def suppress_export_offer(self, globally):
        self.settings.setValue(
            'export/offerAfterSave' if globally else f'export/{self.doc["id"]}/suppressed', not globally
        )
        self.export_offer.hide()

    def markdown_preferences(self):
        self.choose_markdown_mode(force=True)

    def choose_markdown_mode(self, force=False):
        mode = self.settings.value('files/markdownOpenMode', 'ask')
        if mode != 'ask' and not force:
            return mode
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Opening Markdown files'))
        layout = QVBoxLayout(dialog)
        choice = QComboBox()
        choice.setAccessibleName(self.tr('Markdown opening mode'))
        choice.addItem(self.tr('Open as a ByAuthorX document (recommended)'), 'import')
        choice.addItem(self.tr('Edit the .md file directly'), 'inplace')
        choice.setCurrentIndex(max(0, choice.findData(mode)))
        layout.addWidget(choice)
        remember = QCheckBox(self.tr('Remember my choice'))
        remember.setChecked(True)
        layout.addWidget(remember)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return None
        mode = choice.currentData()
        self.settings.setValue('files/markdownOpenMode', mode if remember.isChecked() else 'ask')
        return mode

    def export_suggestion(self):
        fmt = self.file_view if self.file_view != 'byax' else getattr(self, '_offered_format', 'pdf')
        if fmt == 'byax':
            fmt = 'pdf'
        origin = self.doc.get('metadata', {}).get('import', {}).get('original_path')
        path = self.path or (Path(origin) if origin else Path(self.doc['metadata'].get('title', 'Untitled')))
        return str(path.with_name(path.stem + '-export').with_suffix('.' + fmt))
