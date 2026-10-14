# ruff: noqa: BLE001
# GUI event boundaries report errors rather than terminating the application.
"""ByAuthorX native desktop application (Windows 11 / Ubuntu 24.04)."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from platformdirs import user_data_path
from PySide6.QtCore import QObject, QRunnable, QSettings, QSizeF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import engine, model, storage
from .brand import COLORS, BrandGraphic, app_icon, configure_application
from .gui_art import ActivityGraphic, EmptyPanel
from .gui_editor import PageCanvas, SemanticEditor
from .gui_motion import MotionController, PenLineAnimation

FILE_FILTER = 'All supported documents (*.byax *.BYAX *.txt *.md *.markdown *.doc *.docx *.odt *.rtf *.html *.htm);;ByAuthorX (*.byax *.BYAX);;All files (*)'


class WorkerSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.done.emit(self.fn())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setWindowTitle('ByAuthorX')
        self.resize(1320, 900)
        self.settings = QSettings('ByAuthorX', 'ByAuthorX')
        configure_application(QApplication.instance())
        self.setWindowIcon(app_icon())
        self.motion = MotionController(self.settings, self)
        self.setDockOptions(self.dockOptions() & ~QMainWindow.AnimatedDocks)
        self.doc = model.new_document()
        self.path = None
        self.lock = None
        self.owner = 'gui_' + uuid.uuid4().hex
        self.dirty = False
        self._refreshing = False
        self._disk_stamp = None
        self._undo = []
        self._redo = []
        self._workers = set()
        self._active_tasks = {}
        self._last_report = None
        self._recording = None
        self.recovery_dir = user_data_path('ByAuthorX') / 'recovery'
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        self._build_ui()
        self.edit_timer = QTimer(self)
        self.edit_timer.setSingleShot(True)
        self.edit_timer.setInterval(300)
        self.edit_timer.timeout.connect(self.flush_edits)
        self.editor.textChanged.connect(self._text_changed)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()
        self.external_timer = QTimer(self)
        self.external_timer.setInterval(4000)
        self.external_timer.timeout.connect(self.check_external_change)
        self.external_timer.start()
        self.refresh()
        if path:
            self.open_path(path)

    def action(self, title, slot, shortcut=None, checkable=False):
        action = QAction(title, self)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        return action

    def _build_ui(self):
        menus = {name: self.menuBar().addMenu(name) for name in ('&File', '&Edit', 'F&ormat', '&Insert', '&Table', '&Review', '&View', '&Tools', '&Help')}
        file_actions = [('New', self.new_file, QKeySequence.New), ('New from template…', self.templates, None),
                        ('Open…', self.open_file, QKeySequence.Open), ('Save', self.save, QKeySequence.Save),
                        ('Save as…', self.save_as, QKeySequence.SaveAs), ('Export…', self.export, 'Ctrl+Shift+E'), ('Redact a sanitized copy…', self.redact_copy, None),
                        ('Page setup…', self.page_setup, None), ('Print preview…', self.print_preview, None),
                        ('Print…', self.print_document, QKeySequence.Print), ('Recover autosave…', self.recover, None)]
        for title, slot, shortcut in file_actions:
            menus['&File'].addAction(self.action(title, slot, shortcut))
        self.recent_menu = menus['&File'].addMenu('Recent documents')
        self.update_recents()
        menus['&File'].addSeparator()
        menus['&File'].addAction(self.action('Quit', self.close, QKeySequence.Quit))
        self.editor = SemanticEditor()
        self.editor.fileDropped.connect(self.open_path)
        self.editor.cursorPositionChanged.connect(self.selection_changed)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.editor)
        self.pages = PageCanvas()
        scroll = QScrollArea()
        scroll.setObjectName('pagePreview')
        scroll.setWidget(self.pages)
        scroll.setAlignment(Qt.AlignHCenter)
        self.stack.addWidget(scroll)
        workspace = QWidget()
        workspace.setObjectName('writingWorkspace')
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(24, 16, 24, 24)
        workspace_layout.setSpacing(12)
        document_bar = QHBoxLayout()
        document_bar.setSpacing(16)
        self.document_title = QLabel()
        self.document_title.setObjectName('documentTitle')
        self.document_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        document_bar.addWidget(self.document_title, 1)
        self.document_state = QLabel()
        self.document_state.setObjectName('documentState')
        document_bar.addWidget(self.document_state)
        self.view_label = QLabel('Writing view')
        self.view_label.setObjectName('workspaceView')
        document_bar.addWidget(self.view_label)
        workspace_layout.addLayout(document_bar)
        workspace_layout.addWidget(self.stack, 1)
        self.setCentralWidget(workspace)
        for title, slot, key in [('Undo', self.undo, QKeySequence.Undo), ('Redo', self.redo, QKeySequence.Redo),
                                 ('Cut', self.editor.cut, QKeySequence.Cut), ('Copy', self.editor.copy, QKeySequence.Copy),
                                 ('Paste', self.editor.paste, QKeySequence.Paste), ('Select all', self.editor.selectAll, QKeySequence.SelectAll),
                                 ('Find and replace…', self.find_replace, QKeySequence.Find)]:
            menus['&Edit'].addAction(self.action(title, slot, key))
        toolbar = QToolBar('Writing tools')
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.brand_mark = BrandGraphic('wordmark-light.png', 240, clear_space=20)
        self.brand_mark.setAccessibleName('ByAuthorX')
        toolbar.addWidget(self.brand_mark)
        toolbar.addSeparator()
        self.style_box = QComboBox()
        self.style_box.setAccessibleName('Paragraph style')
        for key, label in [('body', 'Body'), ('title', 'Title'), ('subtitle', 'Subtitle'),
                           ('heading1', 'Heading 1'), ('heading2', 'Heading 2'), ('heading3', 'Heading 3'),
                           ('quotation', 'Quotation'), ('caption', 'Caption'), ('code', 'Code')]:
            self.style_box.addItem(label, key)
        self.style_box.activated.connect(lambda: self.apply_style(self.style_box.currentData() or self.style_box.currentText()))
        toolbar.addWidget(self.style_box)
        toolbar.addSeparator()
        for label, mark, shortcut in [('Bold', 'bold', 'Ctrl+B'), ('Italic', 'italic', 'Ctrl+I'), ('Underline', 'underline', 'Ctrl+U')]:
            action = self.action(label, lambda checked=False, m=mark: self.format_selection(m, True), shortcut)
            toolbar.addAction(action)
            menus['F&ormat'].addAction(action)
        for title, mark in [('Strikethrough', 'strike'), ('Superscript', 'superscript'), ('Subscript', 'subscript')]:
            menus['F&ormat'].addAction(self.action(title, lambda checked=False, m=mark: self.format_selection(m, True)))
        menus['F&ormat'].addAction(self.action('Font and paragraph…', self.paragraph_format))
        menus['F&ormat'].addAction(self.action('Define named style…', self.define_style))
        menus['F&ormat'].addAction(self.action('Text color…', lambda: self.choose_color('color')))
        menus['F&ormat'].addAction(self.action('Highlight…', lambda: self.choose_color('highlight')))
        menus['F&ormat'].addAction(self.action('Hyperlink…', self.hyperlink))
        menus['F&ormat'].addAction(self.action('Clear text formatting', self.clear_formatting))
        for alignment in ('left', 'center', 'right', 'justify'):
            menus['F&ormat'].addAction(self.action('Align ' + alignment, lambda checked=False, a=alignment: self.attributes({'alignment': a})))
        menus['F&ormat'].addAction(self.action('Bulleted list', lambda: self.attributes({'list': {'kind': 'bullet', 'level': 1}})))
        menus['F&ormat'].addAction(self.action('Numbered list', lambda: self.attributes({'list': {'kind': 'ordered', 'level': 1}})))
        menus['F&ormat'].addAction(self.action('Increase list level', lambda: self.list_indent(1), 'Alt+Shift+Right'))
        menus['F&ormat'].addAction(self.action('Decrease list level', lambda: self.list_indent(-1), 'Alt+Shift+Left'))
        menus['F&ormat'].addAction(self.action('Restart list numbering', lambda: self.attributes({'restart': True, 'start': 1})))
        menus['F&ormat'].addAction(self.action('Continue list numbering', lambda: self.attributes({'restart': False})))
        menus['F&ormat'].addAction(self.action('Remove list', lambda: self.attributes({'list': None})))
        toolbar.addSeparator()
        toolbar.addAction(self.action('Comment', self.add_comment, 'Ctrl+Alt+M'))
        toolbar.addAction(self.action('Check', self.run_checks, 'F7'))
        toolbar.addAction(self.action('Pages', self.toggle_pages, 'Ctrl+Alt+P'))
        for title, slot in [('Table…', self.insert_table), ('Image…', self.insert_image), ('Page break', self.insert_page_break),
                            ('Section / columns…', self.insert_section), ('Table of contents', lambda: self.insert_structure('toc')),
                            ('Footnote…', self.insert_footnote), ('Bookmark…', self.add_bookmark), ('Cross reference…', self.cross_reference),
                            ('Watermark / layer…', self.add_layer), ('Source reference…', self.add_reference),
                            ('Define template field…', self.define_field), ('Template fields…', self.fill_fields), ('Delete selected object', self.delete_object)]:
            menus['&Insert'].addAction(self.action(title, slot))
        for title, kind in [('Add row', 'add_row'), ('Remove row…', 'remove_row'), ('Add column', 'add_column'), ('Remove column…', 'remove_column')]:
            menus['&Table'].addAction(self.action(title, lambda checked=False, k=kind: self.table_operation(k)))
        menus['&Table'].addAction(self.action('Move row…', lambda: self.reorder_table('row')))
        menus['&Table'].addAction(self.action('Move column…', lambda: self.reorder_table('column')))
        menus['&Table'].addAction(self.action('Table properties…', self.table_properties))
        menus['&Table'].addAction(self.action('Merge cells…', self.merge_cells))
        menus['&Table'].addAction(self.action('Unmerge selected cell', self.unmerge_cell))
        menus['&Table'].addAction(self.action('Import CSV as table…', self.import_csv))
        menus['&Table'].addAction(self.action('Update selected table from CSV…', self.update_table_csv))
        menus['&Table'].addAction(self.action('Export selected table as CSV…', self.export_csv))
        menus['&Table'].addAction(self.action('Delete selected table', self.delete_table))
        for title, slot in [('Run document checks', self.run_checks), ('Checking and voice profiles…', self.edit_profiles), ('Review with a model…', self.model_review),
                            ('Add comment…', self.add_comment), ('Protect selected paragraph', self.protect),
                            ('Move section…', self.move_section), ('Reply to selected comment…', self.reply_comment), ('Checkpoint…', self.checkpoint), ('Create branch…', self.branch), ('Revision history…', self.history)]:
            menus['&Review'].addAction(self.action(title, slot))
        self.track_action = self.action('Track changes', self.toggle_tracking, checkable=True)
        menus['&Review'].addAction(self.track_action)
        menus['&View'].addAction(self.action('Writing / pages', self.toggle_pages, 'Ctrl+Alt+L'))
        menus['&View'].addAction(self.action('Markdown source…', self.source_view))
        self.reduce_motion_action = self.action('Reduce motion', self.motion.set_reduced, checkable=True)
        self.reduce_motion_action.setChecked(self.motion.reduced)
        self.motion.reducedChanged.connect(self.reduce_motion_action.setChecked)
        menus['&View'].addAction(self.reduce_motion_action)
        for title, slot in [('Provider settings…', self.provider_settings), ('Import audio…', self.import_audio),
                            ('Dictation…', self.dictation), ('Background jobs…', self.jobs_dialog), ('Document properties…', self.document_properties),
                            ('Document statistics', self.statistics)]:
            menus['&Tools'].addAction(self.action(title, slot))
        menus['&Help'].addAction(self.action('About ByAuthorX', self.about))
        self.outline = QTreeWidget()
        self.outline.setHeaderHidden(True)
        self.outline.setAccessibleName('Document outline')
        self.outline.itemActivated.connect(lambda item, column: self.editor.navigate(item.data(0, Qt.UserRole)))
        self.outline.itemClicked.connect(lambda item, column: self.editor.navigate(item.data(0, Qt.UserRole)))
        self.outline_stack = QStackedWidget()
        self.outline_stack.addWidget(self.outline)
        self.outline_stack.addWidget(EmptyPanel('Your document outline',
                                              'Headings, tables, images, and bookmarks appear here.'))
        outline_dock = QDockWidget('Outline', self)
        outline_dock.setWidget(self.outline_stack)
        outline_dock.setMinimumWidth(195)
        self.addDockWidget(Qt.LeftDockWidgetArea, outline_dock)
        self.inspector = QTabWidget()
        self.review_list = QListWidget()
        self.review_list.setWordWrap(True)
        self.review_list.setAccessibleName('Comments, proposals, and findings')
        self.review_list.itemActivated.connect(self.review_item)
        review = QWidget()
        rv = QVBoxLayout(review)
        rv.setContentsMargins(8, 12, 8, 12)
        rv.setSpacing(8)
        self.review_stack = QStackedWidget()
        self.review_stack.addWidget(self.review_list)
        self.review_stack.addWidget(EmptyPanel('No open review items',
            'Comments, suggested edits, and check results appear here.',
            graphic='ui/review-empty.png', action=('Run document checks', self.run_checks)))
        rv.addWidget(self.review_stack)
        self.review_buttons = []
        for label, slot in [('Open selected item', self.review_item), ('Reply to comment…', self.reply_comment), ('Resolve / accept…', self.accept_item), ('Reject proposal', self.reject_item)]:
            btn = QPushButton(label)
            if label == 'Resolve / accept…':
                btn.setProperty('primary', True)
            btn.clicked.connect(slot)
            rv.addWidget(btn)
            self.review_buttons.append(btn)
        self.review_list.currentRowChanged.connect(self._sync_review_actions)
        self.inspector.addTab(review, 'Review')
        self.layer_list = QListWidget()
        self.layer_list.setAccessibleName('Document layers')
        layers = QWidget()
        ll = QVBoxLayout(layers)
        ll.setContentsMargins(8, 12, 8, 12)
        ll.setSpacing(8)
        self.layer_stack = QStackedWidget()
        self.layer_stack.addWidget(self.layer_list)
        self.layer_stack.addWidget(EmptyPanel('No document layers',
                                            'Add a watermark or a layer from the Insert menu.'))
        ll.addWidget(self.layer_stack)
        for label, slot in [('Add layer…', self.add_layer), ('Show / hide', self.toggle_layer), ('Lock / unlock', self.lock_layer)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            ll.addWidget(button)
        self.inspector.addTab(layers, 'Layers')
        inspector_dock = QDockWidget('Inspector', self)
        inspector_dock.setWidget(self.inspector)
        inspector_dock.setMinimumWidth(260)
        self.addDockWidget(Qt.RightDockWidgetArea, inspector_dock)
        self.resizeDocks([outline_dock, inspector_dock], [230, 300], Qt.Horizontal)
        menus['&View'].addAction(outline_dock.toggleViewAction())
        menus['&View'].addAction(inspector_dock.toggleViewAction())
        for dock in (outline_dock, inspector_dock):
            widget = dock.widget()
            dock.visibilityChanged.connect(
                lambda visible, widget=widget: self.motion.fade(widget) if visible else self.motion.finish(widget))
        self.inspector.currentChanged.connect(lambda index: self.motion.fade(self.inspector.widget(index)))
        self.status_label = QLabel('Ready')
        self.activity_graphic = ActivityGraphic(self.motion, self)
        self.activity_slot = QWidget()
        self.activity_slot.setFixedSize(56, 28)
        activity_layout = QHBoxLayout(self.activity_slot)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        activity_layout.addWidget(self.activity_graphic, alignment=Qt.AlignCenter)
        self.statusBar().addWidget(self.activity_slot)
        self.statusBar().addWidget(self.status_label, 1)
        self.word_label = QLabel()
        self.statusBar().addPermanentWidget(self.word_label)

    def create_about_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('About ByAuthorX')
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(40, 40, 40, 24)
        layout.setSpacing(24)
        wordmark = BrandGraphic('wordmark-light.png', 360)
        wordmark.setAccessibleName('ByAuthorX')
        layout.addWidget(wordmark)
        tagline = QLabel('Your words. Your call.')
        tagline.setObjectName('brandTagline')
        layout.addWidget(tagline)
        layout.addWidget(PenLineAnimation(self.motion))
        description = QLabel('A native, local-first writing workspace.\n\n'
                             'Write. Review. Keep what works.\n'
                             'Provider workflows run only when requested.\n'
                             'Version 0.1.0 · MIT license')
        description.setWordWrap(True)
        layout.addWidget(description)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def about(self):
        self.create_about_dialog().exec()

    def message(self, title, text):
        QMessageBox.information(self, title, text)

    def error(self, text):
        QMessageBox.warning(self, 'ByAuthorX', str(text))

    def report(self, title, result):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setAccessibleName(title + ' summary')
        view.setPlainText(self.readable_report(result))
        layout.addWidget(view)
        if not isinstance(result, str):
            advanced = QCheckBox('Advanced technical details')
            details = QPlainTextEdit()
            details.setReadOnly(True)
            details.setPlainText(json.dumps(result, indent=2, ensure_ascii=False))
            details.setVisible(False)
            advanced.toggled.connect(details.setVisible)
            advanced.toggled.connect(lambda checked: view.setVisible(not checked))
            layout.addWidget(advanced)
            layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def readable_report(self, result):
        if isinstance(result, str):
            return result
        labels = {b['id']: (b.get('text') or b.get('attrs', {}).get('alt') or b['type'].replace('_', ' ').title())[:85] for b in self.doc['blocks']}
        lines = []
        def describe(record):
            if isinstance(record, str):
                return record
            if not isinstance(record, dict):
                return str(record)
            message = record.get('explanation') or record.get('message') or record.get('text') or record.get('title') or record.get('reason') or record.get('path') or record.get('file')
            if not message:
                message = str(record.get('rule') or record.get('name') or record.get('family') or record.get('type') or record.get('status') or 'Recorded item').replace('_', ' ')
            severity = record.get('severity', record.get('status', ''))
            location = record.get('target')
            suffix = ('\n  At: ' + labels.get(location, 'Document')) if location else ''
            return ((str(severity).replace('_', ' ').capitalize() + ': ') if severity else '') + str(message) + suffix
        if isinstance(result, list):
            return '\n\n'.join(describe(item) for item in result) or 'No items.'
        for key in ('execution', 'validation', 'review'):
            if key in result and not isinstance(result[key], (dict, list)):
                lines.append(key.title() + ': ' + str(result[key]).replace('_', ' ').capitalize())
        if result.get('stale'):
            lines.append('This result was produced for an earlier document revision and needs review.')
        for key, label in [('summary', 'Summary'), ('explanation', 'Explanation'), ('message', 'Result'), ('raw_transcript', 'Recognized transcript'), ('proposed_text', 'Proposed text'), ('text_diff', 'Exact text comparison')]:
            if result.get(key):
                value = result[key]
                if isinstance(value, dict):
                    value = ' · '.join(str(k).replace('_', ' ').title() + ': ' + str(v) for k, v in value.items() if not isinstance(v, (dict, list)))
                lines += ['', label, str(value)]
        for key, label in [('findings', 'Findings'), ('losses', 'Compatibility notes'), ('outputs', 'Saved outputs'), ('changes', 'Changes'), ('operations', 'Exact edits')]:
            values = result.get(key)
            if not values:
                continue
            lines += ['', label]
            if key == 'changes' and isinstance(values, dict):
                for change_kind in ('added', 'removed'):
                    for block in values.get(change_kind, []):
                        lines.append(change_kind.capitalize() + ': ' + (block.get('text') or block.get('type', 'Object')))
                for change in values.get('modified', []):
                    before, after = change.get('before', {}), change.get('after', {})
                    if before.get('text') != after.get('text'):
                        lines.extend(['Before: ' + before.get('text', ''), 'After: ' + after.get('text', '')])
                    else:
                        lines.append('Formatting or structure changed: ' + labels.get(change.get('id'), before.get('type', 'Object')))
                if values.get('order_changed'):
                    lines.append('Document reading order changed.')
                if values.get('properties'):
                    lines.append('Updated document properties: ' + ', '.join(key.replace('_', ' ') for key in values['properties']))
            elif isinstance(values, dict):
                lines.extend(str(k).replace('_', ' ').title() + ': ' + describe(v) for k, v in values.items())
            elif key == 'operations':
                for op in values:
                    target = labels.get(op.get('target'), 'Document')
                    lines.append(op.get('type', op.get('op', 'Edit')).replace('_', ' ').capitalize() + ' — ' + target)
                    for field in ('text', 'match', 'replacement', 'expected_text'):
                        if field in op:
                            lines.append('  ' + field.replace('_', ' ').title() + ': ' + str(op[field]))
            else:
                lines.extend(describe(v) for v in values)
        coverage = result.get('coverage')
        if coverage:
            lines += ['', 'What was checked']
            if isinstance(coverage, dict):
                for key, value in coverage.items():
                    if isinstance(value, list):
                        value = ', '.join(describe(item) for item in value) or 'None'
                    lines.append(key.replace('_', ' ').capitalize() + ': ' + str(value))
            else:
                lines.extend(describe(item) for item in coverage)
        if not lines:
            lines.append(describe(result))
            for key in ('name', 'status', 'provider', 'model', 'cost', 'path'):
                if result.get(key) is not None:
                    lines.append(key.title() + ': ' + str(result[key]))
        return '\n'.join(lines).strip()

    def background(self, label, fn, done):
        worker = Worker(fn)
        self._workers.add(worker)
        self._begin_activity(worker, label)
        def finish(result):
            self._workers.discard(worker)
            self._end_activity(worker)
            done(result)
        def fail(error):
            self._workers.discard(worker)
            self._end_activity(worker, 'Task failed')
            self.error(error)
        worker.signals.done.connect(finish)
        worker.signals.failed.connect(fail)
        QThreadPool.globalInstance().start(worker)

    def _begin_activity(self, token, label):
        self._active_tasks[token] = label
        self._sync_activity()

    def _end_activity(self, token, message='Ready'):
        self._active_tasks.pop(token, None)
        self._sync_activity(message)

    def _sync_activity(self, message='Ready'):
        if self._active_tasks:
            count = len(self._active_tasks)
            label = next(reversed(self._active_tasks.values()))
            self.status_label.setText(label if count == 1 else f'{count} tasks in progress · {label}')
            self.activity_graphic.show_busy()
        else:
            self.activity_graphic.clear()
            self.status_label.setText(message)

    def _saved_feedback(self):
        if not self._active_tasks:
            self.status_label.setText('Saved locally')
            self.activity_graphic.show_saved()
            self.motion.fade(self.status_label)

    def start_job(self, kind, payload, worker, done):
        from .jobs import JobManager
        if not hasattr(self, 'job_manager'):
            self.job_manager = JobManager()
        job = self.job_manager.submit(kind, payload, worker)
        self._begin_activity(job['id'], kind.replace('_', ' ').title() + ' in progress…')
        timer = QTimer(self)
        timer.setInterval(350)
        def poll():
            try:
                status = self.job_manager.status(job['id'], current_revision=self.doc['revision'])
                if status['state'] not in ('completed', 'failed', 'cancelled', 'uncertain'):
                    return
                timer.stop()
                timer.deleteLater()
                self._end_activity(job['id'], kind.title() + ': ' + status['state'] + ' · Results available in Tools → Background jobs')
                recorded = self.job_manager.result(job['id'], current_revision=self.doc['revision'])
                if status['state'] == 'completed':
                    done(recorded['result'])
                elif status['state'] == 'failed':
                    self.error((status.get('error') or {}).get('message', 'Background job failed.'))
            except Exception as exc:
                timer.stop()
                timer.deleteLater()
                self._end_activity(job['id'], 'Task failed')
                self.error(str(exc))
        timer.timeout.connect(poll)
        timer.start()
        return job

    def refresh(self, position=None):
        from .layout import build_document
        self._refreshing = True
        try:
            position = self.editor.textCursor().position() if position is None else position
            document = build_document(self.doc)
            document.setParent(self.editor)
            self.editor.setDocument(document)
            self.editor.capture_baseline()
            self.editor.setUndoRedoEnabled(False)
            cursor = self.editor.textCursor()
            cursor.setPosition(min(position, max(0, document.characterCount() - 1)))
            self.editor.setTextCursor(cursor)
            if self.stack.currentIndex() == 1:
                self.pages.set_document(document, self.doc)
            self.update_sidebar()
            self.update_status()
            self.track_action.setChecked(bool(self.doc['settings'].get('track_changes')))
        finally:
            self._refreshing = False

    def update_sidebar(self):
        self.outline.clear()
        for block in self.doc['blocks']:
            if block['type'] in ('heading', 'table', 'image', 'footnote', 'section_break') or block.get('style', '').startswith('heading'):
                label = block.get('text') or block.get('attrs', {}).get('alt') or block['type'].title()
                item = QTreeWidgetItem([label[:65]])
                item.setData(0, Qt.UserRole, block['id'])
                self.outline.addTopLevelItem(item)
        for name, bookmark in self.doc.get('settings', {}).get('bookmarks', {}).items():
            item = QTreeWidgetItem(['Bookmark: ' + name])
            item.setData(0, Qt.UserRole, bookmark.get('target'))
            self.outline.addTopLevelItem(item)
        self.review_list.clear()
        for kind in ('comments', 'proposals', 'tracked_changes', 'findings'):
            for record in self.doc['review'].get(kind, []):
                if record.get('status') in ('resolved', 'accepted', 'rejected'):
                    continue
                self.review_list.addItem(record.get('text') or record.get('explanation') or record.get('message') or record.get('title') or kind.replace('_', ' ').title())
                self.review_list.item(self.review_list.count() - 1).setData(Qt.UserRole, (kind, record))
                if kind == 'comments':
                    def show_replies(comment, depth=1):
                        for reply in comment.get('replies', []):
                            if reply.get('status') != 'resolved':
                                self.review_list.addItem('    ' * min(depth, 4) + '↳ ' + reply.get('text', 'Reply'))
                                self.review_list.item(self.review_list.count() - 1).setData(Qt.UserRole, ('comments', reply))
                            show_replies(reply, depth + 1)
                    show_replies(record)
        if self._last_report:
            for finding in self._last_report.get('findings', []):
                if finding in self.doc['review'].get('findings', []):
                    continue
                self.review_list.addItem(finding.get('message') or finding.get('explanation') or str(finding.get('rule', 'Finding')))
                self.review_list.item(self.review_list.count() - 1).setData(Qt.UserRole, ('findings', finding))
        self.layer_list.clear()
        for layer in self.doc.get('layers', []):
            self.layer_list.addItem(('● ' if layer.get('visible', True) else '○ ') + layer.get('name', 'Layer') + ('  🔒' if layer.get('locked') else ''))
            self.layer_list.item(self.layer_list.count() - 1).setData(Qt.UserRole, layer)
        self.outline_stack.setCurrentIndex(0 if self.outline.topLevelItemCount() else 1)
        self.review_stack.setCurrentIndex(0 if self.review_list.count() else 1)
        self.layer_stack.setCurrentIndex(0 if self.layer_list.count() else 1)
        self._sync_review_actions()

    def _sync_review_actions(self, *_):
        item = self.review_list.currentItem()
        kind = item.data(Qt.UserRole)[0] if item else None
        enabled = (bool(item), kind == 'comments', kind in ('comments', 'proposals', 'tracked_changes'),
                   kind in ('proposals', 'tracked_changes'))
        for button, available in zip(self.review_buttons, enabled):
            button.setVisible(bool(self.review_list.count()))
            button.setEnabled(available)

    def update_status(self):
        text = model.text_of(self.doc)
        words = len(text.split())
        self.word_label.setText(f'{words:,} words   ·   {len(text):,} characters   ·   {max(1, round(words / 225))} min read')
        self.setWindowTitle(f'{self.doc["metadata"].get("title", "Untitled")}{" •" if self.dirty else ""} — ByAuthorX')
        title = self.doc['metadata'].get('title', 'Untitled')
        self.document_title.setText(title)
        self.document_title.setToolTip(title)
        self.document_state.setText('Unsaved changes' if self.dirty else 'Saved locally' if self.path else 'New document')

    def selection_changed(self):
        if not hasattr(self, 'word_label'):
            return
        block = next((block for block in self.doc['blocks'] if block['id'] == self.editor.current_id()), None)
        if block is not None:
            index = self.style_box.findData(block.get('style', 'body'))
            if index >= 0:
                self.style_box.setCurrentIndex(index)
        if self._active_tasks:
            return
        selection = self.editor.textCursor().selectedText()
        if selection:
            self.status_label.setText(f'Selection: {len(selection.split())} words · {len(selection)} characters')

    def _text_changed(self):
        if not self._refreshing:
            self.dirty = True
            self.edit_timer.start()

    def flush_edits(self):
        self.edit_timer.stop()
        if self._refreshing:
            return True
        try:
            operations, tags = self.editor.collect_changes(self.doc)
            if operations:
                self._commit(operations, refresh=False)
                self._refreshing = True
                self.editor.apply_tags(tags)
                self.editor.capture_baseline()
                self._refreshing = False
            return True
        except Exception as exc:
            self._refreshing = False
            self.error(exc)
            self.refresh()
            return False

    def _commit(self, operations, refresh=True):
        if not operations:
            return None
        old_revision = self.doc['revision']
        candidate, result = engine.apply(self.doc, {'schema_version': 1, 'expected_revision': old_revision,
                                                   'request_id': 'gui_' + uuid.uuid4().hex, 'origin': 'gui', 'operations': operations})
        self._undo.append(old_revision)
        self._redo.clear()
        self.doc = candidate
        self.dirty = True
        self._last_report = None
        if refresh:
            self.refresh()
        else:
            if hasattr(self.editor.document(), 'byax_resolved'):
                del self.editor.document().byax_resolved
            self.update_sidebar()
            self.update_status()
        return result

    def commit(self, operations):
        if self.path and self.path.suffix.lower() in ('.md', '.markdown'):
            simple = {'insert_text', 'replace_text', 'delete_text', 'set_text', 'insert_paragraph', 'delete_block',
                      'move_block', 'split_block', 'join_blocks', 'insert_table', 'update_cell', 'add_row',
                      'remove_row', 'add_column', 'remove_column', 'move_section', 'reorder_row', 'reorder_column'}
            compatible = all(op['type'] in simple or
                (op['type'] == 'replace_content' and 'import_provenance' in op) or
                (op['type'] == 'format_text' and set(op['marks']) <= {'bold', 'italic', 'code', 'link'}) or
                (op['type'] == 'apply_style' and op['style'] in {'body', 'heading1', 'heading2', 'heading3', 'code', 'quotation'}) or
                (op['type'] == 'set_attributes' and set(op['values']) <= {'list', 'level'})
                for op in operations)
            if not compatible and (QMessageBox.question(self, 'Convert to ByAuthorX?', 'This feature cannot be represented in standard Markdown. Save a native .byax copy before applying it? Your Markdown original is preserved.') != QMessageBox.Yes or not self.save_as()):
                return False
        if not self.flush_edits():
            return False
        try:
            self._commit(operations)
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def confirm_discard(self):
        self.flush_edits()
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, 'Save changes?', 'Save your changes before closing this document?',
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def release_lock(self):
        if self.lock:
            self.lock.release()
            self.lock = None

    def new_file(self):
        if self.confirm_discard():
            self.release_lock()
            self.doc, self.path, self.dirty = model.new_document(), None, False
            self._undo.clear()
            self._redo.clear()
            self.refresh(0)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open document', '', FILE_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path):
        from .exchange import import_document
        if not self.confirm_discard():
            return False
        selected = Path(path).resolve()
        if self.path == selected:
            return True
        candidate_lock = None
        try:
            if selected.suffix.lower() in ('.byax', '.md', '.markdown'):
                candidate_lock = storage.DocumentLock(selected, owner=self.owner)
                candidate_lock.acquire()
            if selected.suffix.lower() == '.byax':
                document = storage.load_document(selected)
                report = None
            else:
                document, report = import_document(selected)
            self.release_lock()
            self.lock = candidate_lock
            self.doc = document
            self.path = selected if selected.suffix.lower() in ('.byax', '.md', '.markdown') else None
            self.dirty = self.path is None
            self._disk_stamp = self.file_stamp(self.path)
            self._undo.clear()
            self._redo.clear()
            self.refresh(0)
            self.add_recent(selected)
            if report and report.get('losses'):
                self.report('Import compatibility report', report)
            return True
        except Exception as exc:
            if candidate_lock:
                candidate_lock.release()
            self.error(exc)
            return False

    @staticmethod
    def file_stamp(path):
        if path and path.exists():
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_size)
        return None

    def save(self):
        if not self.flush_edits():
            return False
        if not self.path:
            return self.save_as()
        if self.file_stamp(self.path) != self._disk_stamp:
            self.error('The file changed outside this session. Save as a new file to preserve both versions.')
            return False
        try:
            if self.path.suffix.lower() in ('.md', '.markdown'):
                from .exchange import export_document
                with tempfile.TemporaryDirectory(prefix='.byax-save-', dir=self.path.parent) as temporary:
                    stage = Path(temporary) / 'document.md'
                    result = export_document(self.doc, stage)
                    normalized = [loss for loss in result.get('losses', []) if loss.get('code', loss.get('type')) == 'markdown_normalized']
                    if normalized and QMessageBox.question(self, 'Normalize Markdown source?', 'The structural changes require regenerating Markdown whitespace and syntax. Save these changes?') != QMessageBox.Yes:
                        return False
                    os.replace(stage, self.path)
            else:
                storage.save_document(self.doc, self.path, owner=self.owner)
            self._disk_stamp = self.file_stamp(self.path)
            self.dirty = False
            self.recovery_path().unlink(missing_ok=True)
            self.update_status()
            self._saved_feedback()
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def save_as(self):
        if not self.flush_edits():
            return False
        path, _ = QFileDialog.getSaveFileName(self, 'Save editable master', str(self.path or self.doc['metadata'].get('title', 'Untitled') + '.byax'), 'ByAuthorX (*.byax)')
        if not path:
            return False
        target = Path(path)
        if target.suffix.lower() != '.byax':
            target = target.with_suffix('.byax')
        target = target.resolve()
        if target == self.path:
            return self.save()
        candidate_lock = storage.DocumentLock(target, owner=self.owner)
        try:
            candidate_lock.acquire()
            storage.save_document(self.doc, target, owner=self.owner)
            self.release_lock()
            self.lock, self.path = candidate_lock, target
            self._disk_stamp = self.file_stamp(target)
            self.dirty = False
            self.recovery_path().unlink(missing_ok=True)
            self.add_recent(target)
            self.update_status()
            self._saved_feedback()
            return True
        except Exception as exc:
            candidate_lock.release()
            self.error(exc)
            return False

    def recovery_path(self):
        return self.recovery_dir / (self.doc['id'] + '.byax')

    def autosave(self):
        if self.dirty and self.flush_edits():
            try:
                storage.save_document(self.doc, self.recovery_path(), owner=self.owner)
                if not self._active_tasks:
                    self.status_label.setText('Recovery copy saved')
            except Exception as exc:
                self.status_label.setText('Recovery copy failed: ' + str(exc))

    def recover(self):
        files = sorted(self.recovery_dir.glob('*.byax'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            self.message('Recovery', 'No recovery copies are available.')
            return
        labels = [f'{p.name} · {p.stat().st_size:,} bytes' for p in files]
        chosen, ok = QInputDialog.getItem(self, 'Recover document', 'Recovery copy', labels, 0, False)
        if ok and self.confirm_discard():
            try:
                document = storage.load_document(files[labels.index(chosen)])
                self.release_lock()
                self.doc, self.path, self.dirty = document, None, True
                self.refresh(0)
            except Exception as exc:
                self.error(exc)

    def check_external_change(self):
        if self.path and self.file_stamp(self.path) != self._disk_stamp:
            self.status_label.setText('File changed outside ByAuthorX. Save as a new file to preserve both versions.')

    def add_recent(self, path):
        recents = self.settings.value('recent', [], type=list)
        recents = [str(path)] + [p for p in recents if p != str(path)]
        self.settings.setValue('recent', recents[:12])
        self.update_recents()

    def update_recents(self):
        self.recent_menu.clear()
        for path in self.settings.value('recent', [], type=list):
            self.recent_menu.addAction(self.action(path, lambda checked=False, p=path: self.open_path(p)))

    def closeEvent(self, event):
        if self.confirm_discard():
            self.release_lock()
            event.accept()
        else:
            event.ignore()

    def undo(self):
        self.flush_edits()
        if self._undo:
            revision = self._undo.pop()
            self._redo.append(self.doc['revision'])
            try:
                self.doc, _ = engine.restore(self.doc, revision)
                self.dirty = True
                self.refresh()
            except Exception as exc:
                self.error(exc)

    def redo(self):
        if self._redo:
            revision = self._redo.pop()
            self._undo.append(self.doc['revision'])
            try:
                self.doc, _ = engine.restore(self.doc, revision)
                self.dirty = True
                self.refresh()
            except Exception as exc:
                self.error(exc)

    def target_ids(self):
        self.flush_edits()
        return list(dict.fromkeys(target for target, _, _ in self.editor.selection_targets()))

    def apply_style(self, style):
        operations = []
        if style not in self.doc['styles']:
            defaults = {'title': {'font_size': 32, 'bold': True}, 'subtitle': {'font_size': 15, 'color': '#677b76'},
                        'quotation': {'italic': True, 'indent': 24}, 'caption': {'font_size': 9},
                        'code': {'font_family': 'DejaVu Sans Mono', 'font_size': 10}}
            operations.append({'type': 'define_style', 'name': style, 'values': defaults.get(style, {})})
        operations.extend({'type': 'apply_style', 'target': target, 'style': style} for target in self.target_ids())
        self.commit(operations)

    def attributes(self, values):
        self.commit([{'type': 'set_attributes', 'target': target, 'values': values} for target in self.target_ids()])

    def format_selection(self, mark, value):
        self.flush_edits()
        targets = self.editor.selection_targets()
        if not self.editor.textCursor().hasSelection():
            self.status_label.setText('Select the text you want to format.')
            return
        self.commit([{'type': 'format_text', 'target': target, 'start': start, 'end': end,
                      'marks': {mark: value}} for target, start, end in targets if end > start])

    def clear_formatting(self):
        self.flush_edits()
        self.commit([{'type': 'format_text', 'target': target, 'start': start, 'end': end, 'marks': {key: False for key in ('bold', 'italic', 'underline', 'strike', 'superscript', 'subscript', 'color', 'highlight', 'link', 'font_family', 'font_size')}}
                     for target, start, end in self.editor.selection_targets() if end > start])

    def choose_color(self, mark):
        color = QColorDialog.getColor(QColor(COLORS['ink']), self)
        if color.isValid():
            self.format_selection(mark, color.name())

    def hyperlink(self):
        value, ok = QInputDialog.getText(self, 'Hyperlink', 'URL (https://…)')
        if ok:
            self.format_selection('link', value)

    def paragraph_format(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Font and paragraph')
        form = QFormLayout(dialog)
        block = next((b for b in self.doc['blocks'] if b['id'] == self.editor.current_id()), {})
        values = {**self.doc['styles'].get(block.get('style', 'body'), {}), **block.get('attrs', {})}
        font = QFontComboBox()
        size = QDoubleSpinBox()
        size.setRange(6, 144)
        size.setValue(11)
        spacing = QDoubleSpinBox()
        spacing.setRange(0.5, 4)
        spacing.setSingleStep(.1)
        spacing.setValue(1.15)
        indent = QDoubleSpinBox()
        indent.setRange(-144, 432)
        left_indent = QDoubleSpinBox()
        left_indent.setRange(0, 432)
        left_indent.setValue(values.get('indent', 0))
        indent.setValue(values.get('first_line_indent', 0))
        tabs = QLineEdit(', '.join(str(value) for value in values.get('tabs', [])))
        tabs.setPlaceholderText('For example: 36, 72, 144')
        before, after = QDoubleSpinBox(), QDoubleSpinBox()
        before.setRange(0, 144)
        after.setRange(0, 144)
        for label, control in [('Font', font), ('Size (pt)', size), ('Line spacing', spacing), ('First line indent (negative = hanging, pt)', indent), ('Left indent (pt)', left_indent), ('Tab stops (pt, comma separated)', tabs), ('Space before (pt)', before), ('Space after (pt)', after)]:
            form.addRow(label, control)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            try:
                tab_values = sorted({float(value.strip()) for value in tabs.text().split(',') if value.strip()})
                if any(value <= 0 or value > 2000 for value in tab_values):
                    raise ValueError('Tab stops must be greater than 0 and at most 2,000 points.')
            except ValueError as exc:
                self.error(exc)
                return
            self.attributes({'font_family': font.currentFont().family(), 'font_size': size.value(),
                             'indent': left_indent.value(), 'tabs': tab_values,
                             'line_spacing': spacing.value(), 'first_line_indent': indent.value(),
                             'space_before': before.value(), 'space_after': after.value()})

    def list_indent(self, delta):
        operations = []
        for target in self.target_ids():
            block = next((b for b in self.doc['blocks'] if b['id'] == target), None)
            if not block:
                continue
            attrs = block.get('attrs', {})
            listing = attrs.get('list', {'kind': 'bullet', 'level': 1})
            listing = dict(listing) if isinstance(listing, dict) else {'kind': listing, 'level': max(1, attrs.get('level', 1))}
            listing['level'] = max(1, min(9, listing.get('level', 1) + delta))
            operations.append({'type': 'set_attributes', 'target': target, 'values': {'list': listing}})
        self.commit(operations)

    def section_targets(self, target):
        blocks = self.doc['blocks']
        index = next((i for i, block in enumerate(blocks) if block['id'] == target or any(cell['id'] == target for row in block.get('rows', []) for cell in row['cells'])), 0)
        section_id = blocks[index].get('section_id') if blocks else None
        if section_id:
            return {b['id'] for b in blocks if b.get('section_id') == section_id}
        start = index
        while start > 0 and blocks[start]['type'] not in ('heading', 'section_break'):
            start -= 1
        end = start + 1
        while end < len(blocks) and blocks[end]['type'] not in ('heading', 'section_break'):
            end += 1
        return {b['id'] for b in blocks[start:end]}

    def find_replace(self):
        self.flush_edits()
        dialog = QDialog(self)
        dialog.setWindowTitle('Find and replace')
        dialog.resize(540, 240)
        form = QFormLayout(dialog)
        query, replacement = QLineEdit(), QLineEdit()
        case, whole, regex = QCheckBox('Match case'), QCheckBox('Whole words'), QCheckBox('Regular expression')
        selected_ranges = {target: (start, end) for target, start, end in self.editor.selection_targets()}
        current_target = self.editor.current_id()
        scope = QComboBox()
        scope.addItems(['Document', 'Selection', 'Current paragraph', 'Current section'])
        scope.setAccessibleName('Search scope')
        form.addRow('Scope', scope)
        form.addRow('Find', query)
        form.addRow('Replace with', replacement)
        for control in (case, whole, regex):
            form.addRow(control)
        count = QLabel('Find text before replacing. Replacement always previews the affected matches.')
        count.setWordWrap(True)
        form.addRow(count)
        matches = []
        match_index = 0
        def navigate_match():
            if matches:
                from PySide6.QtGui import QTextCursor

                from .gui_editor import qt_offset
                match = matches[match_index]
                if self.editor.navigate(match['target']):
                    cursor = self.editor.textCursor()
                    text = cursor.block().text()
                    base = cursor.block().position()
                    cursor.setPosition(base + qt_offset(text, match['start']))
                    cursor.setPosition(base + qt_offset(text, match['end']), QTextCursor.KeepAnchor)
                    self.editor.setTextCursor(cursor)
                count.setText(f'Match {match_index + 1} of {len(matches)}')
        def next_match():
            nonlocal match_index
            if matches:
                match_index = (match_index + 1) % len(matches)
                navigate_match()
            else:
                search()
        def search():
            nonlocal matches, match_index
            match_index = 0
            try:
                result = engine.find(self.doc, query.text(), case_sensitive=case.isChecked(), whole_word=whole.isChecked(), regex=regex.isChecked())
                matches = result.get('matches', result.get('results', []))
                if scope.currentText() == 'Current paragraph':
                    matches = [match for match in matches if match['target'] == current_target]
                elif scope.currentText() == 'Current section':
                    selected_blocks = self.section_targets(current_target)
                    matches = [match for match in matches if match['block'] in selected_blocks]
                elif scope.currentText() == 'Selection':
                    matches = [match for match in matches if match['target'] in selected_ranges and selected_ranges[match['target']][0] <= match['start'] and match['end'] <= selected_ranges[match['target']][1]]
                count.setText(f'{len(matches)} matches')
                navigate_match()
            except Exception as exc:
                count.setText(str(exc))
        def replace_all():
            search()
            if not matches:
                return
            try:
                replacements = self.replacement_preview(matches, query.text(), replacement.text(), case.isChecked(), regex.isChecked())
            except Exception as exc:
                self.error(exc)
                return
            self.report('Replacement preview', replacements)
            if QMessageBox.question(dialog, 'Replace matches?', f'Replace {len(matches)} matches with {replacement.text()!r}?') != QMessageBox.Yes:
                return
            operations = []
            for match in reversed(replacements):
                target = match.get('target') or match.get('id')
                start, end = match['start'], match['end']
                operations += [{'type': 'delete_text', 'target': target, 'start': start, 'end': end},
                               {'type': 'insert_text', 'target': target, 'offset': start, 'text': match['replacement']}]
            if self.commit(operations):
                dialog.accept()
        find_btn, replace_btn = QPushButton('Find'), QPushButton('Preview replace all…')
        next_btn = QPushButton('Next match')
        next_btn.clicked.connect(next_match)
        form.addRow(next_btn)
        find_btn.clicked.connect(search)
        replace_btn.clicked.connect(replace_all)
        form.addRow(find_btn, replace_btn)
        dialog.exec()

    def replacement_preview(self, matches, query, replacement, case_sensitive=False, regex=False):
        expanded = {}
        if regex:
            import regex as regex_engine
            compiled = regex_engine.compile(query, 0 if case_sensitive else regex_engine.IGNORECASE)
            for target in {match['target'] for match in matches}:
                obj = model.find_object(self.doc, target)
                for match in compiled.finditer(obj.get('text', ''), timeout=0.1):
                    expanded[(target, match.start(), match.end())] = match.expand(replacement)
        return [{**match, 'replacement': expanded.get((match['target'], match['start'], match['end']), replacement),
                 'explanation': repr(match['text']) + ' → ' + repr(expanded.get((match['target'], match['start'], match['end']), replacement))}
                for match in matches]

    def insert_table(self):
        rows, ok = QInputDialog.getInt(self, 'Insert table', 'Rows', 3, 1, 1000)
        if not ok:
            return
        columns, ok = QInputDialog.getInt(self, 'Insert table', 'Columns', 3, 1, 100)
        if ok:
            self.commit([{'type': 'insert_table', 'after': self.block_target(), 'rows': rows, 'columns': columns}])

    def block_target(self):
        target = self.editor.current_id()
        for block in self.doc['blocks']:
            if block['id'] == target or any(c['id'] == target for r in block.get('rows', []) for c in r['cells']):
                return block['id']
        return self.doc['blocks'][-1]['id'] if self.doc['blocks'] else None

    def table_operation(self, kind):
        target = self.block_target()
        block = next((b for b in self.doc['blocks'] if b['id'] == target and b['type'] == 'table'), None)
        if not block:
            self.error('Place the cursor in a table first.')
            return
        op = {'type': kind, 'target': target}
        if kind.startswith('remove'):
            limit = len(block['rows']) if kind.endswith('row') else len(block['rows'][0]['cells'])
            index, ok = QInputDialog.getInt(self, kind.replace('_', ' ').title(), 'Number (starting at 1)', 1, 1, limit)
            if not ok:
                return
            op['index'] = index - 1
        self.commit([op])

    def reorder_table(self, dimension):
        self.flush_edits()
        selected = self.selected_cell()
        if selected is None:
            self.error('Place the cursor in the table row or column to move.')
            return
        table, row, column, _ = selected
        index = row if dimension == 'row' else column
        count = len(table['rows']) if dimension == 'row' else len(table['rows'][0]['cells'])
        destination, ok = QInputDialog.getInt(self, 'Move ' + dimension,
            f'Move {dimension} {index + 1} to position (1–{count})', index + 1, 1, count)
        if ok and destination - 1 != index:
            self.commit([{'type': 'reorder_' + dimension, 'target': table['id'], 'index': index, 'to_index': destination - 1}])

    def table_properties(self):
        block = next((b for b in self.doc['blocks'] if b['id'] == self.block_target() and b['type'] == 'table'), None)
        if block is None:
            self.error('Place the cursor in a table first.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Table properties')
        form = QFormLayout(dialog)
        header = QCheckBox('First row is a repeating header')
        header.setChecked(bool(block.get('attrs', {}).get('header_rows')))
        padding = QDoubleSpinBox()
        padding.setRange(0, 36)
        padding.setValue(block.get('attrs', {}).get('padding', 4))
        attrs = block.get('attrs', {})
        width = QDoubleSpinBox()
        width.setRange(36, 2000)
        width.setValue(attrs.get('width', self.doc['layout']['width'] - self.doc['layout']['margins']['left'] - self.doc['layout']['margins']['right']))
        columns = QLineEdit(', '.join(str(value) for value in attrs.get('column_widths', [])))
        columns.setPlaceholderText('Blank = automatic; otherwise one width per column')
        border = QDoubleSpinBox()
        border.setRange(0, 12)
        border.setValue(attrs.get('border_width', .5))
        alignment = QComboBox()
        alignment.addItems(['left', 'center', 'right'])
        alignment.setCurrentText(attrs.get('alignment', 'left'))
        colors = {'background': attrs.get('background', '#ffffff'), 'border_color': attrs.get('border_color', '#606060')}
        for key, label in [('background', 'Table shading'), ('border_color', 'Border color')]:
            button = QPushButton(colors[key])
            def choose(checked=False, field=key, control=button):
                color = QColorDialog.getColor(QColor(colors[field]), dialog)
                if color.isValid():
                    colors[field] = color.name()
                    control.setText(color.name())
            button.clicked.connect(choose)
            form.addRow(label, button)
        form.addRow(header)
        form.addRow('Table width (pt)', width)
        form.addRow('Column widths (pt, comma separated)', columns)
        form.addRow('Alignment', alignment)
        form.addRow('Cell padding (pt)', padding)
        form.addRow('Border width (pt)', border)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            try:
                column_widths = [float(value.strip()) for value in columns.text().split(',') if value.strip()]
                if column_widths and (len(column_widths) != len(block['rows'][0]['cells']) or any(value <= 0 for value in column_widths)):
                    raise ValueError('Specify one positive width per column, or leave the field blank.')
                self.commit([{'type': 'set_attributes', 'target': block['id'], 'values': {'header_rows': int(header.isChecked()), 'padding': padding.value(), 'width': width.value(), 'column_widths': column_widths, 'border_width': border.value(), 'alignment': alignment.currentText(), **colors}}])
            except ValueError as exc:
                self.error(exc)

    def selected_cell(self):
        target = self.editor.current_id()
        for block in self.doc['blocks']:
            for ri, row in enumerate(block.get('rows', [])):
                for ci, cell in enumerate(row['cells']):
                    if cell['id'] == target:
                        return block, ri, ci, cell
        return None

    def merge_cells(self):
        self.flush_edits()
        selected = self.selected_cell()
        if selected is None:
            self.error('Place the cursor in the top-left cell of the range to merge.')
            return
        table, row, column, cell = selected
        rows, ok = QInputDialog.getInt(self, 'Merge cells', 'Rows to merge', 1, 1, len(table['rows']) - row)
        if not ok:
            return
        columns, ok = QInputDialog.getInt(self, 'Merge cells', 'Columns to merge', 1, 1, len(table['rows'][row]['cells']) - column)
        if not ok or rows * columns == 1:
            return
        selected_cells = [table['rows'][r]['cells'][c] for r in range(row, row + rows) for c in range(column, column + columns)]
        if any(c.get('attrs', {}).get('rowspan', 1) > 1 or c.get('attrs', {}).get('colspan', 1) > 1 for c in selected_cells):
            self.error('Unmerge existing spans before creating a new merged range.')
            return
        text = '\n'.join(c['text'] for c in selected_cells if c['text'])
        if QMessageBox.question(self, 'Merge selected range?', 'Merge these cells and combine their text into the top-left cell? Unmerging later keeps the combined text in that cell.') != QMessageBox.Yes:
            return
        operations = [{'type': 'update_cell', 'target': cell['id'], 'text': text}]
        operations.extend({'type': 'update_cell', 'target': c['id'], 'text': ''} for c in selected_cells[1:])
        operations.append({'type': 'set_attributes', 'target': cell['id'], 'values': {'rowspan': rows, 'colspan': columns}})
        self.commit(operations)

    def unmerge_cell(self):
        selected = self.selected_cell()
        if selected:
            self.commit([{'type': 'set_attributes', 'target': selected[3]['id'], 'values': {'rowspan': 1, 'colspan': 1}}])

    def import_csv(self):
        from .table_io import read_csv
        path, _ = QFileDialog.getOpenFileName(self, 'Import CSV table', '', 'CSV (*.csv)')
        if not path:
            return
        try:
            rows = read_csv(path)
            columns = max((len(row) for row in rows), default=0)
            if not columns or len(rows) > 1000 or columns > 100:
                raise ValueError('A CSV table must contain 1–1,000 rows and 1–100 columns.')
            block = {'id': 'table_' + uuid.uuid4().hex, 'type': 'table', 'text': '', 'style': 'body', 'marks': [], 'attrs': {'header_rows': 1}, 'rows': []}
            for row in rows:
                block['rows'].append({'id': 'row_' + uuid.uuid4().hex, 'cells': [{'id': 'cell_' + uuid.uuid4().hex, 'text': value, 'attrs': {}} for value in row + [''] * (columns - len(row))]})
            blocks = copy.deepcopy(self.doc['blocks'])
            index = next((i + 1 for i, old in enumerate(blocks) if old['id'] == self.block_target()), len(blocks))
            blocks.insert(index, block)
            self.commit([{'type': 'replace_content', 'blocks': blocks}])
        except Exception as exc:
            self.error(exc)

    def update_table_csv(self):
        from .table_io import import_csv
        self.flush_edits()
        block = next((b for b in self.doc['blocks'] if b['id'] == self.block_target() and b['type'] == 'table'), None)
        if not block:
            self.error('Place the cursor in a table first.')
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Update table values from CSV', '', 'CSV (*.csv)')
        if not path:
            return
        try:
            _, preview = import_csv(self.doc, block['id'], path, grow=True, dry_run=True)
            self.report('Preview CSV table update', preview)
            if QMessageBox.question(self, 'Update table?', 'Apply these CSV values? Existing cell identities and formatting are preserved; larger inputs add rows or columns.') != QMessageBox.Yes:
                return
            revision = self.doc['revision']
            self.doc, _ = import_csv(self.doc, block['id'], path, expected_revision=revision, grow=True)
            self._undo.append(revision)
            self._redo.clear()
            self.dirty = True
            self.refresh()
        except Exception as exc:
            self.error(exc)

    def export_csv(self):
        from .table_io import export_csv
        self.flush_edits()
        block = next((b for b in self.doc['blocks'] if b['id'] == self.block_target() and b['type'] == 'table'), None)
        if not block:
            self.error('Place the cursor in a table first.')
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export table CSV', 'table.csv', 'CSV (*.csv)')
        if path:
            try:
                result = export_csv(self.doc, block['id'], path, overwrite=True, owner=self.owner)
                self.report('CSV export', result)
            except Exception as exc:
                self.error(exc)

    def delete_table(self):
        target = self.block_target()
        if any(b['id'] == target and b['type'] == 'table' for b in self.doc['blocks']):
            self.commit([{'type': 'delete_block', 'target': target}])

    def delete_object(self):
        target = self.block_target()
        if any(b['id'] == target and b['type'] not in ('paragraph', 'heading') for b in self.doc['blocks']):
            self.commit([{'type': 'delete_block', 'target': target}])
        else:
            self.status_label.setText('Place the cursor on the object you want to delete.')

    def insert_image(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Insert embedded image', '', 'Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)')
        if not path:
            return
        alt, ok = QInputDialog.getText(self, 'Image description', 'Describe the image for readers using assistive technology')
        if not ok:
            return
        import mimetypes
        data = Path(path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        from PySide6.QtGui import QImage
        image = QImage(path)
        if image.isNull():
            self.error('This image could not be decoded.')
            return
        width = min(288, image.width())
        height = width * image.height() / max(1, image.width())
        self.commit([{'type': 'add_asset', 'data': base64.b64encode(data).decode(), 'mime': mimetypes.guess_type(path)[0] or 'image/png', 'name': Path(path).name},
                     {'type': 'place_image', 'after': self.block_target(), 'asset': digest, 'alt': alt, 'width': width, 'height': height}])

    def insert_page_break(self):
        self.attributes({'page_break_before': True})

    def insert_structure(self, kind, text='', attrs=None):
        self.flush_edits()
        blocks = copy.deepcopy(self.doc['blocks'])
        target = self.block_target()
        index = next((i + 1 for i, b in enumerate(blocks) if b['id'] == target), len(blocks))
        ident = 'obj_' + uuid.uuid4().hex
        blocks.insert(index, {'id': ident, 'type': kind, 'text': text, 'style': 'body', 'marks': [], 'attrs': attrs or {}})
        return self.commit([{'type': 'replace_content', 'blocks': blocks}])

    def insert_section(self):
        columns, ok = QInputDialog.getInt(self, 'Section layout', 'Number of flowing columns', 2, 1, 6)
        if ok:
            self.insert_structure('section_break', attrs={'columns': columns, 'column_gap': 18})

    def insert_footnote(self):
        target = self.editor.current_id()
        text, ok = QInputDialog.getMultiLineText(self, 'Insert footnote', 'Footnote text')
        if ok and text:
            self.insert_structure('footnote', text, {'reference': target})

    def add_bookmark(self):
        targets = self.editor.selection_targets()
        if not targets:
            return
        name, ok = QInputDialog.getText(self, 'Bookmark', 'Bookmark name')
        if ok and name:
            self.commit([{'type': 'set_bookmark', 'name': name, 'target': targets[0][0], 'offset': targets[0][1]}])

    def cross_reference(self):
        names = list(self.doc.get('settings', {}).get('bookmarks', {}))
        if not names:
            self.message('Cross reference', 'Create a bookmark first, then select the text that should refer to it.')
            return
        name, ok = QInputDialog.getItem(self, 'Cross reference', 'Bookmark', names, 0, False)
        if ok:
            self.format_selection('cross_reference', name)

    def define_style(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Define named style')
        form = QFormLayout(dialog)
        name = QLineEdit()
        based_on = QComboBox()
        based_on.addItems(list(self.doc['styles']))
        font = QFontComboBox()
        size = QDoubleSpinBox()
        size.setRange(6, 144)
        size.setValue(11)
        bold, italic = QCheckBox('Bold'), QCheckBox('Italic')
        for label, control in [('Style name', name), ('Inherit from', based_on), ('Font', font), ('Size (pt)', size), ('Weight', bold), ('Emphasis', italic)]:
            form.addRow(label, control)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() and name.text().strip():
            self.commit([{'type': 'define_style', 'name': name.text().strip(), 'values': {'based_on': based_on.currentText(), 'font_family': font.currentFont().family(), 'font_size': size.value(), 'bold': bold.isChecked(), 'italic': italic.isChecked()}}])
            if name.text().strip() in self.doc['styles'] and self.style_box.findData(name.text().strip()) == -1:
                self.style_box.addItem(name.text().strip(), name.text().strip())

    def add_comment(self):
        text, ok = QInputDialog.getMultiLineText(self, 'Add comment', 'Comment')
        if ok and text:
            targets = self.editor.selection_targets()
            target, start, end = targets[0] if targets else (self.editor.current_id(), 0, 0)
            self.commit([{'type': 'add_comment', 'target': target, 'start': start, 'end': end, 'text': text}])

    def reply_comment(self):
        item = self.review_list.currentItem()
        if item is None or item.data(Qt.UserRole)[0] != 'comments':
            self.status_label.setText('Select a comment in the Review panel before replying.')
            return
        _, comment = item.data(Qt.UserRole)
        text, ok = QInputDialog.getMultiLineText(self, 'Reply to comment', comment.get('text', 'Comment') + '\n\nYour reply')
        if ok and text.strip():
            self.commit([{'type': 'reply_comment', 'target': comment['id'], 'text': text.strip(), 'author': self.doc['metadata'].get('author', '')}])

    def move_section(self):
        self.flush_edits()
        headings = [block for block in self.doc['blocks'] if block['type'] == 'heading']
        if not headings:
            self.message('Move section', 'Add headings to organize the document into movable sections.')
            return
        target = self.editor.current_id()
        current = 0
        for block in self.doc['blocks']:
            if block['type'] == 'heading':
                current = next(i for i, heading in enumerate(headings) if heading['id'] == block['id'])
            if block['id'] == target:
                break
        labels = [f'{index + 1}. {heading.get("text") or "Untitled heading"}' for index, heading in enumerate(headings)]
        source_label, ok = QInputDialog.getItem(self, 'Move section', 'Section to move, including its nested headings and anchored objects', labels, current, False)
        if not ok:
            return
        source = headings[labels.index(source_label)]
        # A heading subtree cannot be its own destination.
        source_index = self.doc['blocks'].index(source)
        source_level = int(source.get('attrs', {}).get('level', 1))
        descendants = {source['id']}
        for block in self.doc['blocks'][source_index + 1:]:
            if block['type'] == 'heading' and int(block.get('attrs', {}).get('level', 1)) <= source_level:
                break
            descendants.add(block['id'])
        destinations = [heading for heading in headings if heading['id'] not in descendants]
        options = ['End of document'] + [f'After section: {heading.get("text") or "Untitled heading"} ({index + 1})' for index, heading in enumerate(destinations)]
        destination, ok = QInputDialog.getItem(self, 'Move section', 'New location', options, 0, False)
        if ok:
            index = options.index(destination)
            operation = {'type': 'move_section', 'target': source['id']}
            if index:
                operation['after'] = destinations[index - 1]['id']
            if self.commit([operation]):
                self.editor.navigate(source['id'])

    def protect(self):
        self.commit([{'type': 'protect', 'target': target, 'kind': 'text'} for target in self.target_ids()])

    def review_item(self, item=None):
        item = item if hasattr(item, 'data') else self.review_list.currentItem()
        if item:
            kind, record = item.data(Qt.UserRole)
            target = record.get('target')
            if target:
                self.editor.navigate(target)
            self.report(kind.replace('_', ' ').title(), record)

    def accept_item(self):
        item = self.review_list.currentItem()
        if not item:
            return
        kind, record = item.data(Qt.UserRole)
        if kind == 'comments':
            self.commit([{'type': 'resolve_comment', 'target': record['id']}])
        elif kind in ('proposals', 'tracked_changes'):
            self.report('Review exact changes before accepting', record)
            if QMessageBox.question(self, 'Apply changes?', 'Apply these exact proposed changes to the document?') == QMessageBox.Yes:
                operation = 'accept_proposal' if kind == 'proposals' else 'accept_change'
                self.commit([{'type': operation, 'target': record['id']}])
        else:
            self.report('Finding details', record)

    def reject_item(self):
        item = self.review_list.currentItem()
        if item:
            kind, record = item.data(Qt.UserRole)
            if kind in ('proposals', 'tracked_changes'):
                self.commit([{'type': 'reject_proposal' if kind == 'proposals' else 'reject_change', 'target': record['id']}])

    def edit_profiles(self):
        from .gui_profiles import ProfileDialog
        ProfileDialog(self).exec()

    def run_checks(self):
        from .checks import run_checks
        self.flush_edits()
        frozen = copy.deepcopy(self.doc)
        def finished(result):
            if frozen['revision'] != self.doc['revision']:
                self.report('Checks completed against an earlier revision', result)
                return
            self._last_report = result
            self.update_sidebar()
            self.status_label.setText('Checks: ' + str(result.get('validation', 'complete')) + ' · See Review for findings')
            self.report('Document health and actual coverage', result)
        profile = getattr(self, 'active_profile', None)
        profile = profile if profile in frozen.get('profiles', {}) else None
        self.background('Checking document…', lambda: run_checks(frozen, profile=profile), finished)

    def add_layer(self):
        name, ok = QInputDialog.getText(self, 'Add watermark / layer', 'Layer name', text='Watermark')
        if not ok:
            return
        text, ok = QInputDialog.getText(self, 'Layer text', 'Text', text='DRAFT')
        if ok:
            self.commit([{'type': 'add_layer', 'values': {'name': name, 'type': 'text', 'text': text, 'visible': True, 'locked': False,
                                                        'x': 160, 'y': 360, 'width': 300, 'height': 100, 'opacity': .15, 'font_size': 52, 'z': -1}}])

    def toggle_layer(self):
        item = self.layer_list.currentItem()
        if item:
            layer = item.data(Qt.UserRole)
            self.commit([{'type': 'update_layer', 'target': layer['id'], 'values': {'visible': not layer.get('visible', True)}}])

    def lock_layer(self):
        item = self.layer_list.currentItem()
        if item:
            layer = item.data(Qt.UserRole)
            self.commit([{'type': 'update_layer', 'target': layer['id'], 'values': {'locked': not layer.get('locked', False)}}])

    def add_reference(self):
        title, ok = QInputDialog.getText(self, 'Attach reference', 'Source title')
        if not ok:
            return
        excerpt, ok = QInputDialog.getMultiLineText(self, 'Reference excerpt', 'Approved evidence excerpt (stored in this document)')
        if ok:
            self.commit([{'type': 'add_reference', 'id': 'ref_' + uuid.uuid4().hex, 'values': {'title': title, 'excerpt': excerpt, 'target': self.editor.current_id()}}])

    def toggle_tracking(self, enabled):
        self.commit([{'type': 'set_settings', 'values': {'track_changes': enabled}}])
        self.track_action.setChecked(bool(self.doc['settings'].get('track_changes')))

    def define_field(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Define template field')
        form = QFormLayout(dialog)
        name, description, default = QLineEdit(), QLineEdit(), QLineEdit()
        kind = QComboBox()
        kind.addItems(['text', 'number', 'date'])
        required = QCheckBox('Required before publication')
        for label, control in [('Field name', name), ('Type', kind), ('Description', description), ('Default', default), ('Requirement', required)]:
            form.addRow(label, control)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() and name.text().strip():
            self.flush_edits()
            targets = self.editor.selection_targets()
            if not targets:
                return
            key = name.text().strip()
            if not all(c.isalnum() or c in '._-' for c in key):
                self.error('Use letters, numbers, dots, underscores, or dashes in field names.')
                return
            value = default.text()
            if kind.currentText() == 'number' and value:
                try:
                    value = float(value)
                except ValueError:
                    self.error('The default must be a number.')
                    return
            field = {'type': kind.currentText(), 'description': description.text(), 'required': required.isChecked()}
            if value != '':
                field['default'] = value
                field['value'] = value
            operations = [{'type': 'insert_text', 'target': targets[0][0], 'offset': targets[0][1], 'text': '{{' + key + '}}'},
                          {'type': 'fill_fields', 'values': {key: field}}]
            if value == '':
                operations.reverse()
            self.commit(operations)

    def fill_fields(self):
        values = {}
        for key, field in self.doc.get('fields', {}).items():
            field = field if isinstance(field, dict) else {'value': field, 'type': 'text'}
            label = field.get('description') or key
            initial = field.get('value', field.get('default', ''))
            if field.get('type') == 'number':
                value, ok = QInputDialog.getDouble(self, 'Fill template fields', label, float(initial or 0), -1e12, 1e12, 6)
            elif field.get('type') in ('enum', 'enumerated'):
                choices = [str(v) for v in field.get('options', field.get('choices', []))]
                value, ok = QInputDialog.getItem(self, 'Fill template fields', label, choices, 0, False)
            else:
                value, ok = QInputDialog.getText(self, 'Fill template fields', label + (' (YYYY-MM-DD)' if field.get('type') == 'date' else ''), text=str(initial))
            if not ok:
                return
            values[key] = value
        if values:
            self.commit([{'type': 'fill_fields', 'values': values}])
        else:
            self.message('Template fields', 'This document does not define any template fields.')

    def checkpoint(self):
        self.flush_edits()
        name, ok = QInputDialog.getText(self, 'Save checkpoint', 'Checkpoint name')
        if ok and name:
            try:
                self.doc, _ = engine.checkpoint(self.doc, name)
                self.dirty = True
                self.update_status()
            except Exception as exc:
                self.error(exc)

    def branch(self):
        self.flush_edits()
        name, ok = QInputDialog.getText(self, 'Alternate draft', 'Branch name')
        if ok and name:
            try:
                self.doc, _ = engine.branch(self.doc, name)
                self.dirty = True
                self.status_label.setText('Draft branch: ' + name)
            except Exception as exc:
                self.error(exc)

    def history(self):
        self.flush_edits()
        dialog = QDialog(self)
        dialog.setWindowTitle('Revision history')
        dialog.resize(700, 430)
        layout = QVBoxLayout(dialog)
        revisions = QListWidget()
        for record in self.doc['history'].get('revisions', []):
            revision = record.get('revision') or record.get('id')
            revisions.addItem(str(record.get('name') or record.get('timestamp') or '') + '  ' + str(revision))
            revisions.item(revisions.count() - 1).setData(Qt.UserRole, revision)
        layout.addWidget(revisions)
        def compare():
            if revisions.currentItem():
                try:
                    self.report('Semantic revision comparison', engine.diff(self.doc, revisions.currentItem().data(Qt.UserRole)))
                except Exception as exc:
                    self.error(exc)
        def restore():
            if revisions.currentItem() and QMessageBox.question(dialog, 'Restore revision?', 'Restore this revision as the current document?') == QMessageBox.Yes:
                try:
                    self.doc, _ = engine.restore(self.doc, revisions.currentItem().data(Qt.UserRole))
                    self.dirty = True
                    self.refresh()
                    dialog.accept()
                except Exception as exc:
                    self.error(exc)
        for label, slot in [('Compare with current', compare), ('Restore selected', restore)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            layout.addWidget(button)
        dialog.exec()

    def page_setup(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Page setup')
        form = QFormLayout(dialog)
        paper = QComboBox()
        paper.addItems(['Letter', 'A4', 'Custom'])
        paper.setCurrentText(self.doc['layout'].get('paper', 'Letter'))
        landscape = QCheckBox('Landscape')
        width, height = QDoubleSpinBox(), QDoubleSpinBox()
        for control, key, default in [(width, 'width', 612), (height, 'height', 792)]:
            control.setRange(72, 5000)
            control.setValue(self.doc['layout'].get(key, default))
        margins = {}
        form.addRow('Paper', paper)
        form.addRow(landscape)
        form.addRow('Width (pt)', width)
        form.addRow('Height (pt)', height)
        for side in ('top', 'right', 'bottom', 'left'):
            spin = QDoubleSpinBox()
            spin.setRange(0, 720)
            spin.setValue(self.doc['layout'].get('margins', {}).get(side, 72))
            margins[side] = spin
            form.addRow(side.title() + ' margin (pt)', spin)
        header, footer = QLineEdit(str(self.doc['layout'].get('header', ''))), QLineEdit(str(self.doc['layout'].get('footer', '')))
        form.addRow('Header', header)
        form.addRow('Footer ({page} / {pages})', footer)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            w, h = {'Letter': (612, 792), 'A4': (595.276, 841.89)}.get(paper.currentText(), (width.value(), height.value()))
            if landscape.isChecked():
                w, h = max(w, h), min(w, h)
            self.commit([{'type': 'set_layout', 'values': {'paper': paper.currentText(), 'width': w, 'height': h,
                                                         'margins': {k: v.value() for k, v in margins.items()},
                                                         'header': header.text(), 'footer': footer.text()}}])

    def toggle_pages(self):
        self.flush_edits()
        if self.stack.currentIndex() == 0:
            self.pages.set_document(self.editor.document(), self.doc)
            self.stack.setCurrentIndex(1)
            self.view_label.setText('Page preview')
            if not self._active_tasks:
                self.status_label.setText('Print layout · Ctrl+Alt+L to return to writing')
        else:
            self.stack.setCurrentIndex(0)
            self.editor.setFocus()
            self.view_label.setText('Writing view')
            if not self._active_tasks:
                self.status_label.setText('Writing view')
        self.motion.fade(self.stack.currentWidget())

    def print_preview(self):
        self.flush_edits()
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintPreviewDialog(printer, self)
        dialog.paintRequested.connect(self.paint_printer)
        dialog.exec()

    def print_document(self):
        self.flush_edits()
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec():
            self.paint_printer(printer)

    def paint_printer(self, printer):
        from .layout import draw_page, resolved_page_count, resolved_page_size
        qdoc = self.editor.document()
        total_pages = resolved_page_count(qdoc, self.doc)
        printer.setFullPage(True)
        printer.setPageSize(QPageSize(QSizeF(self.doc['layout']['width'], self.doc['layout']['height']), QPageSize.Point))
        painter = QPainter(printer)
        if not painter.isActive():
            self.error('The printer could not start the print job.')
            return
        try:
            first = max(1, printer.fromPage())
            last = printer.toPage() or total_pages
            for page in range(first - 1, min(last, total_pages)):
                width, height = resolved_page_size(qdoc, page)
                printer.setPageSize(QPageSize(QSizeF(width, height), QPageSize.Point))
                if page > first - 1:
                    printer.newPage()
                painter.save()
                painter.scale(printer.resolution() / 72, printer.resolution() / 72)
                draw_page(painter, qdoc, self.doc, page)
                painter.restore()
        finally:
            painter.end()

    def redact_copy(self):
        from .publication import save_sanitized
        self.flush_edits()
        targets = self.target_ids()
        if not targets:
            return
        if QMessageBox.question(self, 'Redact selected paragraphs?', 'Create a sanitized text-only copy that removes the selected paragraphs, history, review material, and assets? The private master remains unchanged.') != QMessageBox.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Save sanitized copy', 'Sanitized.byax', 'ByAuthorX (*.byax)')
        if path:
            try:
                self.report('Verified sanitization report', save_sanitized(self.doc, path, targets))
            except Exception as exc:
                self.error(exc)

    def jobs_dialog(self):
        from .jobs import JobManager
        manager = getattr(self, 'job_manager', None) or JobManager()
        self.job_manager = manager
        dialog = QDialog(self)
        dialog.setWindowTitle('Background jobs and recoverable results')
        dialog.resize(720, 440)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        def update():
            listing.clear()
            for job in manager.list():
                listing.addItem(job['kind'] + ' · ' + job['state'] + ' · ' + job['id'])
                listing.item(listing.count() - 1).setData(Qt.UserRole, job['id'])
        layout.addWidget(listing)
        def result():
            if listing.currentItem():
                try:
                    self.report('Recorded job result', manager.result(listing.currentItem().data(Qt.UserRole)))
                except Exception as exc:
                    self.error(exc)
        def cancel():
            if listing.currentItem():
                manager.cancel(listing.currentItem().data(Qt.UserRole))
                update()
        for label, slot in [('Refresh', update), ('View result', result), ('Request cancellation', cancel)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            layout.addWidget(button)
        update()
        dialog.exec()

    def export(self):
        from .publication import export_clean
        self.flush_edits()
        path, _ = QFileDialog.getSaveFileName(self, 'Export publication copy', '', 'PDF (*.pdf);;Word (*.docx);;OpenDocument (*.odt);;Markdown (*.md);;HTML (*.html);;Rich text (*.rtf);;Plain text (*.txt)')
        if not path:
            return
        try:
            report = export_clean(self.doc, path)
            self.report('Export result and compatibility', report)
        except Exception as exc:
            self.error(exc)

    def source_view(self):
        from .exchange import export_document, import_document
        self.flush_edits()
        dialog = QDialog(self)
        dialog.setWindowTitle('Markdown source')
        dialog.resize(850, 680)
        layout = QVBoxLayout(dialog)
        markdown_session = bool(self.path and self.path.suffix.lower() in ('.md', '.markdown'))
        note = QLabel('CommonMark source with tables. Source edits update this Markdown document and preserve unchanged block identities.' if markdown_session else 'CommonMark source with tables. Applying source creates a new document; save it separately to preserve the rich master.')
        note.setWordWrap(True)
        layout.addWidget(note)
        editor = QPlainTextEdit()
        editor.setAccessibleName('Markdown source editor')
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / 'source.md'
            export_document(self.doc, source_path)
            editor.setPlainText(source_path.read_text())
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        initial_source = editor.toPlainText()
        apply = buttons.addButton('Apply source edits' if markdown_session else 'Create document from source', QDialogButtonBox.ActionRole)
        def convert():
            if markdown_session and editor.toPlainText() == initial_source:
                dialog.accept()
                return
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'source.md'
                path.write_text(editor.toPlainText(), encoding='utf-8')
                doc, report = import_document(path)
            if markdown_session:
                if self.apply_markdown_source(doc):
                    dialog.accept()
                return
            if self.confirm_discard():
                self.release_lock()
                self.doc, self.path, self.dirty = doc, None, True
                self.refresh(0)
                dialog.accept()
                if report.get('losses'):
                    self.report('Markdown conversion', report)
        apply.clicked.connect(convert)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def apply_markdown_source(self, imported):
        """Reconcile parsed source with stable IDs before one canonical transaction."""
        import difflib
        original = self.doc['blocks']
        incoming = imported['blocks']
        signature = lambda b: (b['type'], b.get('text', ''), json.dumps(b.get('rows', []), sort_keys=True))
        matcher = difflib.SequenceMatcher(a=[signature(b) for b in original], b=[signature(b) for b in incoming], autojunk=False)
        remapped = {}
        for tag, a, b, c, d in matcher.get_opcodes():
            if tag in ('equal', 'replace'):
                for old, new in zip(original[a:b], incoming[c:d]):
                    if old['type'] == new['type']:
                        remapped[new['id']] = old['id']
                        new['id'] = old['id']
                        if old['type'] == 'table':
                            for old_row, new_row in zip(old.get('rows', []), new.get('rows', [])):
                                new_row['id'] = old_row['id']
                                for old_cell, new_cell in zip(old_row['cells'], new_row['cells']):
                                    new_cell['id'] = old_cell['id']
        provenance = imported['metadata']['import']
        provenance.update(original_path=str(self.path), filename=self.path.name,
            block_order=[b['id'] for b in incoming],
            fingerprints={b['id']: hashlib.sha256(json.dumps(b, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for b in incoming})
        return self.commit([{'type': 'replace_content', 'blocks': incoming, 'assets': imported['assets'], 'import_provenance': provenance}])

    def document_properties(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Document properties')
        form = QFormLayout(dialog)
        controls = {}
        for key in ('title', 'author', 'language', 'description'):
            controls[key] = QLineEdit(self.doc['metadata'].get(key, ''))
            form.addRow(key.title(), controls[key])
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.commit([{'type': 'set_metadata', 'values': {k: v.text() for k, v in controls.items()}}])

    def statistics(self):
        self.flush_edits()
        text = model.text_of(self.doc)
        self.message('Document statistics', f'Authored content\n{len(text.split()):,} words\n{len(text):,} Unicode characters\n{len(self.doc["blocks"]):,} blocks\n{self.editor.document().pageCount():,} laid-out pages\n\nPublication profiles may exclude selected content.')

    def templates(self):
        kind, ok = QInputDialog.getItem(self, 'New from template', 'Template', ['Letter', 'Resume', 'Report', 'Music press kit', 'Open template file…'], 0, False)
        if not ok:
            return
        if kind == 'Open template file…':
            path, _ = QFileDialog.getOpenFileName(self, 'Open template', '', 'ByAuthorX (*.byax)')
            if path and self.open_path(path):
                self.release_lock()
                self.doc['id'] = 'doc_' + uuid.uuid4().hex
                self.path, self.dirty = None, True
            return
        if not self.confirm_discard():
            return
        examples = {
            'Letter': [('title', 'Your name'), ('body', 'Your address · City, State'), ('body', 'October 13, 2026'), ('body', 'Dear recipient,'), ('body', 'Write your letter here.'), ('body', 'Sincerely,\nYour name')],
            'Resume': [('title', 'Your name'), ('subtitle', 'Your profession · email@example.com'), ('heading1', 'Profile'), ('body', 'A concise introduction to your experience and strengths.'), ('heading1', 'Experience'), ('heading2', 'Position · Organization'), ('body', 'Describe the impact of your work.'), ('heading1', 'Education'), ('body', 'Qualification · Institution')],
            'Report': [('title', 'Report title'), ('subtitle', 'Prepared by your name'), ('heading1', 'Executive summary'), ('body', 'Summarize the purpose and findings.'), ('heading1', 'Background'), ('body', 'Explain the context.'), ('heading1', 'Findings'), ('body', 'Present the evidence.'), ('heading1', 'Recommendations'), ('body', 'Set out the next steps.')],
            'Music press kit': [('title', 'ARTIST NAME'), ('subtitle', 'PRESS KIT · New release'), ('heading1', 'Biography'), ('body', 'Introduce the artist, their sound, and the story behind the music.'), ('heading1', 'The release'), ('body', 'Album title · Release date · Genre'), ('heading1', 'Press & booking'), ('body', 'Contact name · email@example.com')],
        }
        document = model.new_document(kind)
        document['styles'].update({'title': {'font_size': 32, 'bold': True}, 'subtitle': {'font_size': 15, 'color': '#677b76'}, 'quotation': {'italic': True, 'indent': 24}, 'caption': {'font_size': 9}, 'code': {'font_family': 'DejaVu Sans Mono', 'font_size': 10}})
        document['blocks'] = []
        for style, text in examples[kind]:
            block = {'id': 'p_' + uuid.uuid4().hex, 'type': 'heading' if style.startswith('heading') else 'paragraph', 'text': text, 'style': style, 'attrs': {}, 'marks': []}
            if style.startswith('heading'):
                block['attrs']['level'] = int(style[-1])
            document['blocks'].append(block)
        self.release_lock()
        self.doc, self.path, self.dirty = document, None, True
        self._undo.clear()
        self._redo.clear()
        self.refresh(0)

    def provider_settings(self):
        from .gui_providers import ProviderDialog
        ProviderDialog(self).exec()

    def model_review(self):
        from .gui_providers import review_document
        review_document(self)

    def import_audio(self):
        from .gui_providers import import_audio
        import_audio(self)

    def dictation(self):
        from .gui_providers import DictationDialog
        existing = getattr(self, '_dictation_dialog', None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        self._dictation_dialog = DictationDialog(self)
        self._dictation_dialog.show()


def main(argv=None):
    app = QApplication.instance() or QApplication(sys.argv if argv is None else ['byauthorx'] + argv)
    app.setApplicationName('ByAuthorX')
    app.setOrganizationName('ByAuthorX')
    app.setStyle('Fusion')
    args = sys.argv[1:] if argv is None else argv
    window = MainWindow(args[0] if args else None)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
