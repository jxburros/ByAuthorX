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
from PySide6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    QSettings,
    QSizeF,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, engine, gui_a11y, model, storage
from .brand import COLORS, BrandGraphic, app_icon, configure_application
from .formats import FORMATS, project
from .gui_ai import AIPanel
from .gui_art import ActivityGraphic, EmptyPanel, themed_art
from .gui_editor import PageCanvas, ProofMark, SemanticEditor
from .gui_icons import themed_icon
from .gui_libraries import LibraryPanels
from .gui_motion import MotionController, PenLineAnimation
from .gui_ribbon import RibbonPanels
from .gui_views import FileViews
from .gui_workflows import WorkflowPanels
from .gui_workspace import InspectorTabBar, WorkspacePanels

TEMPLATE_CHOICES = ['Letter', 'Resume', 'Report', 'Music press kit', 'Open template file…']
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


class MainWindow(FileViews, WorkflowPanels, WorkspacePanels, RibbonPanels, LibraryPanels, QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setWindowTitle(self.tr('ByAuthorX'))
        self.resize(1320, 900)
        self.settings = QSettings('ByAuthorX', 'ByAuthorX')
        configure_application(QApplication.instance())
        self.setWindowIcon(app_icon())
        self.motion = MotionController(self.settings, self)
        QApplication.instance().styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.hand_marks = self.settings.value('appearance/handDrawnMarks', True, type=bool)
        self.setDockOptions(self.dockOptions() & ~QMainWindow.AnimatedDocks)
        self.doc = model.new_document()
        self.path = None
        self.lock = None
        self.owner = 'gui_' + uuid.uuid4().hex
        self.dirty = False
        self._refreshing = False
        self._disk_stamp = None
        self._save_task = None
        self._save_again = False
        self._pages_generation = 0
        self._last_save_ok = None
        self._undo = []
        self._redo = []
        self._workers = set()
        self._active_tasks = {}
        self._last_report = None
        self._recording = None
        self.recovery_dir = user_data_path('ByAuthorX') / 'recovery'
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        self._build_ui()
        gui_a11y.describe_menus(self.menuBar())
        if not self.inspector.accessibleName():
            self.inspector.setAccessibleName(self.tr('Inspector panels'))
        gui_a11y.ensure_accessible(self)
        gui_a11y.watch(self)
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

    def action(self, title, slot, shortcut=None, checkable=False, description=None, icon=None):
        action = QAction(title, self)
        if icon:
            action.setProperty('byaxIcon', icon)
            action.setIcon(themed_icon(icon, self))
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        return gui_a11y.describe_action(action, description)

    def childEvent(self, event):
        # Dialogs parented to the window get accessible names and tooltips on show.
        if event.type() == QEvent.Type.ChildPolished:
            child = event.child()
            if isinstance(child, QWidget) and child.isWindow():
                gui_a11y.watch(child)
        super().childEvent(event)

    def _build_ui(self):
        menus = {
            name: self.menuBar().addMenu(self.tr(name))
            for name in (
                '&File',
                '&Edit',
                'F&ormat',
                '&Insert',
                '&Table',
                '&Review',
                '&View',
                '&Tools',
                '&Help',
            )
        }
        file_actions = [
            (self.tr('New'), self.new_file, QKeySequence.New, 'new-document'),
            (self.tr('New from template…'), self.templates, None, None),
            (self.tr('Open…'), self.open_file, QKeySequence.Open, 'open-document'),
            (self.tr('Save'), self.save, QKeySequence.Save, 'save'),
            (self.tr('Save as…'), self.save_as, QKeySequence.SaveAs, 'save-as'),
            (self.tr('Export…'), self.export, 'Ctrl+Shift+E', 'export'),
            (self.tr('Page setup…'), self.page_setup, None, None),
            (self.tr('Print preview…'), self.print_preview, None, None),
            (self.tr('Print…'), self.print_document, QKeySequence.Print, 'print'),
            (self.tr('Recover autosave…'), self.recover, None, None),
        ]
        for title, slot, shortcut, icon in file_actions:
            menus['&File'].addAction(self.action(title, slot, shortcut, icon=icon))
            if slot == self.export:
                self.export_as_menu = menus['&File'].addMenu(self.tr('Export as'))
                self.export_options_menu = menus['&File'].addMenu(self.tr('Export options'))
                self.export_options_menu.addAction(
                    self.action(self.tr('Redact a sanitized copy…'), self.redact_copy)
                )
        self.recent_menu = menus['&File'].addMenu(self.tr('Recent documents'))
        self.update_recents()
        menus['&File'].addSeparator()
        menus['&File'].addAction(self.action(self.tr('Quit'), self.close, QKeySequence.Quit))
        self.editor = SemanticEditor()
        self.editor.motion = self.motion
        self.editor.set_hand_marks(self.hand_marks)
        self._check_marks = ()
        self.editor.fileDropped.connect(self.open_path)
        self.editor.viewEditRequested.connect(self.commit)
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
        self.view_label = QLabel(self.tr('Writing view'))
        self.view_label.setObjectName('workspaceView')
        document_bar.addWidget(self.view_label)
        workspace_layout.addLayout(document_bar)
        workspace_layout.addWidget(self.stack, 1)
        self.setCentralWidget(workspace)
        for title, slot, key, icon in [
            (self.tr('Undo'), self.undo, QKeySequence.Undo, 'undo'),
            (self.tr('Redo'), self.redo, QKeySequence.Redo, 'redo'),
            (self.tr('Cut'), self.editor.cut, QKeySequence.Cut, 'cut'),
            (self.tr('Copy'), self.editor.copy, QKeySequence.Copy, 'copy'),
            (self.tr('Paste'), self.editor.paste, QKeySequence.Paste, 'paste'),
            (self.tr('Select all'), self.editor.selectAll, QKeySequence.SelectAll, None),
            (self.tr('Find and replace…'), self.find_replace, QKeySequence.Find, 'find'),
        ]:
            menus['&Edit'].addAction(self.action(title, slot, key, icon=icon))
        self.ribbon_actions = {}
        self.style_box = QComboBox()
        self.style_box.setAccessibleName(self.tr('Paragraph style'))
        self.style_box.setToolTip(self.tr('Paragraph style of the current paragraph'))
        for key, label in [
            ('body', self.tr('Body')),
            ('title', self.tr('Title')),
            ('subtitle', self.tr('Subtitle')),
            ('heading1', self.tr('Heading 1')),
            ('heading2', self.tr('Heading 2')),
            ('heading3', self.tr('Heading 3')),
            ('heading4', self.tr('Heading 4')),
            ('heading5', self.tr('Heading 5')),
            ('heading6', self.tr('Heading 6')),
            ('list', self.tr('List')),
            ('quotation', self.tr('Quotation')),
            ('caption', self.tr('Caption')),
            ('code', self.tr('Code')),
        ]:
            self.style_box.addItem(label, key)
        self.style_box.activated.connect(
            lambda: self.apply_style(self.style_box.currentData() or self.style_box.currentText())
        )
        format_menu = menus['F&ormat']
        self.submenus = {
            key: format_menu.addMenu(self.tr(title))
            for key, title in (
                ('text', 'Text'),
                ('paragraph', 'Paragraph'),
                ('lists', 'Lists'),
                ('styles', 'Styles'),
                ('layers', 'Layers'),
            )
        }
        format_menu.addSeparator()
        for label, mark, shortcut in [
            (self.tr('Bold'), 'bold', 'Ctrl+B'),
            (self.tr('Italic'), 'italic', 'Ctrl+I'),
            (self.tr('Underline'), 'underline', 'Ctrl+U'),
        ]:
            action = self.action(
                label, lambda checked=False, m=mark: self.format_selection(m, True), shortcut, icon=mark
            )
            self.ribbon_actions[mark] = action
            format_menu.insertAction(self.submenus['text'].menuAction(), action)
        text_menu = self.submenus['text']
        for title, mark, icon in [
            (self.tr('Strikethrough'), 'strikethrough', 'strikethrough'),
            (self.tr('Superscript'), 'superscript', 'superscript'),
            (self.tr('Subscript'), 'subscript', 'subscript'),
        ]:
            text_menu.addAction(
                self.action(title, lambda checked=False, m=mark: self.format_selection(m, True), icon=icon)
            )
        text_menu.addAction(
            self.action(self.tr('Text color…'), lambda: self.choose_color('color'), icon='text-color')
        )
        text_menu.addAction(
            self.action(self.tr('Highlight…'), lambda: self.choose_color('highlight'), icon='highlight')
        )
        text_menu.addAction(self.action(self.tr('Hyperlink…'), self.hyperlink, icon='hyperlink'))
        text_menu.addAction(
            self.action(self.tr('Clear text formatting'), self.clear_formatting, icon='clear-format')
        )
        self.submenus['paragraph'].addAction(
            self.action(self.tr('Font and paragraph…'), self.paragraph_format, icon='paragraph')
        )
        self.submenus['styles'].addAction(self.action(self.tr('Define named style…'), self.define_style))
        for alignment, title in [
            ('left', self.tr('Align left')),
            ('center', self.tr('Align center')),
            ('right', self.tr('Align right')),
            ('justify', self.tr('Justify')),
        ]:
            self.submenus['paragraph'].addAction(
                self.action(
                    title,
                    lambda checked=False, a=alignment: self.attributes({'alignment': a}),
                    icon=f'align-{alignment}' if alignment != 'justify' else 'justify',
                )
            )
        self.submenus['lists'].addAction(
            self.action(
                self.tr('Bulleted list'),
                lambda: self.set_list({'kind': 'bullet', 'level': 1}),
                icon='bullet-list',
            )
        )
        self.submenus['lists'].addAction(
            self.action(
                self.tr('Numbered list'),
                lambda: self.set_list({'kind': 'ordered', 'level': 1}),
                icon='numbered-list',
            )
        )
        self.submenus['lists'].addAction(
            self.action(
                self.tr('Increase list level'), lambda: self.list_indent(1), 'Alt+Shift+Right', icon='indent'
            )
        )
        self.submenus['lists'].addAction(
            self.action(
                self.tr('Decrease list level'), lambda: self.list_indent(-1), 'Alt+Shift+Left', icon='outdent'
            )
        )
        self.submenus['lists'].addAction(
            self.action(
                self.tr('Restart list numbering'), lambda: self.set_list({'restart': True, 'start': 1})
            )
        )
        self.submenus['lists'].addAction(
            self.action(self.tr('Continue list numbering'), lambda: self.set_list({'restart': False}))
        )
        self.submenus['lists'].addAction(
            self.action(self.tr('Remove list'), lambda: self.set_list({'kind': None}))
        )
        for key, title, slot, shortcut, description, icon in (
            (
                'comment',
                self.tr('Comment'),
                self.add_comment,
                'Ctrl+Alt+M',
                self.tr('Add a comment to the selected text'),
                'comment',
            ),
            ('check', self.tr('Check'), self.run_checks, 'F7', self.tr('Run document checks'), 'checks'),
            (
                'pages',
                self.tr('Pages'),
                self.toggle_pages,
                'Ctrl+Alt+P',
                self.tr('Switch between the writing view and the page preview'),
                'pages',
            ),
        ):
            self.ribbon_actions[key] = self.action(title, slot, shortcut, description=description, icon=icon)
        insert_menu = menus['&Insert']
        self.submenus['references'] = QMenu(self.tr('References'), self)
        self.submenus['fields'] = QMenu(self.tr('Template fields'), self)
        for title, slot, icon, menu in [
            (self.tr('Table…'), self.insert_table, 'table', insert_menu),
            (self.tr('Image…'), self.insert_image, 'image', insert_menu),
            (self.tr('Page break'), self.insert_page_break, 'page-break', insert_menu),
            (self.tr('Section / columns…'), self.insert_section, 'columns', insert_menu),
            (self.tr('Table of contents'), lambda: self.insert_structure('toc'), 'contents', insert_menu),
            (self.tr('Footnote…'), self.insert_footnote, 'footnote', insert_menu),
            (self.tr('Bookmark…'), self.add_bookmark, 'bookmark', insert_menu),
            (self.tr('Watermark / layer…'), self.add_layer, 'layers', insert_menu),
            (self.tr('Cross reference…'), self.cross_reference, 'hyperlink', self.submenus['references']),
            (self.tr('Source reference…'), self.add_reference, None, self.submenus['references']),
            (self.tr('Define template field…'), self.define_field, None, self.submenus['fields']),
            (self.tr('Template fields…'), self.fill_fields, None, self.submenus['fields']),
        ]:
            menu.addAction(self.action(title, slot, icon=icon))
        insert_menu.addSeparator()
        insert_menu.addMenu(self.submenus['references'])
        insert_menu.addMenu(self.submenus['fields'])
        insert_menu.addSeparator()
        insert_menu.addAction(self.action(self.tr('Delete selected object'), self.delete_object))
        table_menu = menus['&Table']
        for title, kind, icon in [
            (self.tr('Add row'), 'add_row', 'row-add'),
            (self.tr('Add column'), 'add_column', 'column-add'),
        ]:
            table_menu.addAction(
                self.action(title, lambda checked=False, k=kind: self.table_operation(k), icon=icon)
            )
        rows = table_menu.addMenu(self.tr('Rows and columns'))
        for title, kind in [
            (self.tr('Remove row…'), 'remove_row'),
            (self.tr('Remove column…'), 'remove_column'),
        ]:
            rows.addAction(self.action(title, lambda checked=False, k=kind: self.table_operation(k)))
        rows.addAction(self.action(self.tr('Move row…'), lambda: self.reorder_table('row')))
        rows.addAction(self.action(self.tr('Move column…'), lambda: self.reorder_table('column')))
        cells = table_menu.addMenu(self.tr('Cells'))
        cells.addAction(self.action(self.tr('Merge cells…'), self.merge_cells))
        cells.addAction(self.action(self.tr('Unmerge selected cell'), self.unmerge_cell))
        csv = table_menu.addMenu(self.tr('CSV'))
        csv.addAction(self.action(self.tr('Import CSV as table…'), self.import_csv))
        csv.addAction(self.action(self.tr('Update selected table from CSV…'), self.update_table_csv))
        csv.addAction(self.action(self.tr('Export selected table as CSV…'), self.export_csv))
        table_menu.addSeparator()
        table_menu.addAction(self.action(self.tr('Table properties…'), self.table_properties))
        table_menu.addAction(self.action(self.tr('Delete selected table'), self.delete_table))
        review_menu = menus['&Review']
        self.submenus['protection'] = QMenu(self.tr('Protection'), self)
        self.submenus['versions'] = QMenu(self.tr('Versions and drafts'), self)
        for title, slot, icon, menu in [
            (self.tr('Run document checks'), self.run_checks, 'checks', review_menu),
            (self.tr('Checking and voice profiles…'), self.edit_profiles, None, review_menu),
            (self.tr('Review with a model…'), self.model_review, 'model-review', review_menu),
            (self.tr('Add comment…'), self.add_comment, 'comment', review_menu),
            (self.tr('Reply to selected comment…'), self.reply_comment, 'reply', review_menu),
            (self.tr('Move section…'), self.move_section, None, review_menu),
            (self.tr('Protect selected paragraph'), self.protect, 'protect', self.submenus['protection']),
            (self.tr('Remove selected protection…'), self.unprotect, None, self.submenus['protection']),
            (self.tr('Checkpoint…'), self.checkpoint, 'checkpoint', self.submenus['versions']),
            (self.tr('Create branch…'), self.branch, 'branch', self.submenus['versions']),
            (self.tr('Revision history…'), self.history, 'history', self.submenus['versions']),
        ]:
            menu.addAction(self.action(title, slot, icon=icon))
        self.track_action = self.action(
            self.tr('Track changes'), self.toggle_tracking, checkable=True, icon='track-changes'
        )
        review_menu.addAction(self.track_action)
        review_menu.addSeparator()
        review_menu.addMenu(self.submenus['protection'])
        review_menu.addMenu(self.submenus['versions'])
        menus['&View'].addAction(
            self.action(self.tr('Writing / pages'), self.toggle_pages, 'Ctrl+Alt+L', icon='pages')
        )
        menus['&View'].addAction(self.action(self.tr('Markdown source…'), self.source_view, icon='source'))
        self.hand_marks_action = self.action(
            self.tr('Hand-drawn marks'),
            self.set_hand_marks,
            checkable=True,
            description=self.tr(
                'Draw spelling and grammar marks, underline and strikethrough as pen strokes in the editor. '
                'Pages, print and exports always use exact lines.'
            ),
        )
        self.hand_marks_action.setChecked(self.hand_marks)
        menus['&View'].addAction(self.hand_marks_action)
        self.reduce_motion_action = self.action(
            self.tr('Reduce motion'), self.motion.set_reduced, checkable=True
        )
        self.reduce_motion_action.setChecked(self.motion.reduced)
        self.motion.reducedChanged.connect(self.reduce_motion_action.setChecked)
        menus['&View'].addAction(self.reduce_motion_action)
        for title, slot, icon in [
            (self.tr('Provider settings…'), self.provider_settings, 'provider-settings'),
            (self.tr('Import audio…'), self.import_audio, 'import-audio'),
            (self.tr('Dictation…'), self.dictation, 'dictation'),
            (self.tr('Background jobs…'), self.jobs_dialog, 'jobs'),
            (self.tr('Document properties…'), self.document_properties, 'properties'),
            (self.tr('Document statistics'), self.statistics, 'statistics'),
        ]:
            menus['&Tools'].addAction(self.action(title, slot, icon=icon))
        about = self.action(self.tr('About ByAuthorX'), self.about)
        about.setIcon(app_icon())  # the logo's own micro mark, never a redraw of it
        menus['&Help'].addAction(about)
        self.outline = QTreeWidget()
        self.outline.setHeaderHidden(True)
        self.outline.setAccessibleName(self.tr('Document outline'))
        self.outline.itemActivated.connect(
            lambda item, column: self.navigate_target(item.data(0, Qt.UserRole))
        )
        self.outline.itemClicked.connect(lambda item, column: self.navigate_target(item.data(0, Qt.UserRole)))
        self.outline_stack = QStackedWidget()
        self.outline_stack.addWidget(self.outline)
        self.outline_stack.addWidget(
            EmptyPanel(
                self.tr('Your document outline'),
                self.tr('Headings, tables, images, and bookmarks appear here.'),
                graphic=themed_art('outline-empty'),
            )
        )
        outline_dock = QDockWidget(self.tr('Outline'), self)
        outline_dock.setWidget(self.outline_stack)
        outline_dock.setMinimumWidth(195)
        self.addDockWidget(Qt.LeftDockWidgetArea, outline_dock)
        self.inspector = QTabWidget()
        self.inspector.setObjectName('inspectorTabs')
        self.inspector.setTabBar(InspectorTabBar(self.inspector))
        # The assistant owns the spelling controller and the provider controls that other tabs display.
        self.ai_panel = AIPanel(self)
        self.inspector.addTab(self.build_proposed_changes(), self.tr('Proposed changes'))
        from .gui_configure import ConfigurePanel
        from .gui_review import ChecksPanel

        self.checks_panel = ChecksPanel(self)
        self.inspector.addTab(self.checks_panel, self.tr('Checks'))
        self.inspector.addTab(self.ai_panel, self.tr('AI'))
        self.configure_panel = ConfigurePanel(self)
        self.inspector.addTab(self.configure_panel, self.tr('Configure'))
        self.layout_panel = self.build_layout_panel()
        inspector_dock = QDockWidget(self.tr('Inspector'), self)
        inspector_dock.setWidget(self.inspector)
        inspector_dock.setMinimumWidth(300)
        self.addDockWidget(Qt.RightDockWidgetArea, inspector_dock)
        self.build_workspace_sidebars(outline_dock, inspector_dock)
        self.resizeDocks([outline_dock, inspector_dock], [220, 340], Qt.Horizontal)
        menus['&View'].addAction(outline_dock.toggleViewAction())
        menus['&View'].addAction(inspector_dock.toggleViewAction())
        for dock in (outline_dock, inspector_dock):
            widget = dock.widget()
            dock.visibilityChanged.connect(
                lambda visible, widget=widget: (
                    self.motion.fade(widget) if visible else self.motion.finish(widget)
                )
            )
        self.inspector.currentChanged.connect(lambda index: self.motion.fade(self.inspector.widget(index)))
        self.status_label = QLabel(self.tr('Ready'))
        self.activity_graphic = ActivityGraphic(self.motion, self)
        self.activity_slot = QWidget()
        self.activity_slot.setFixedSize(56, 28)
        activity_layout = QHBoxLayout(self.activity_slot)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        activity_layout.addWidget(self.activity_graphic, alignment=Qt.AlignCenter)
        self.statusBar().addWidget(self.activity_slot)
        self.statusBar().addWidget(self.status_label, 1)
        self.word_label = QLabel()
        self.word_label.setObjectName('statusCounter')
        self.statusBar().addPermanentWidget(self.word_label)
        for label, slot in (
            ('Linked assets…', self.linked_assets),
            ('Reusable settings…', self.reusable_settings),
        ):
            menus['&Tools'].addAction(self.action(self.tr(label), slot))
        self.submenus['text'].addAction(self.action(self.tr('Apply character style…'), self.character_style))
        self.submenus['styles'].addAction(
            self.action(self.tr('Styles and overrides…'), self.formatting_inspector)
        )
        menus['&View'].addAction(self.action(self.tr('Layer inspector…'), self.layer_inspector))
        for label, slot in (
            ('Edit selected layer…', self.edit_layer_properties),
            ('Group selected layers…', self.group_selected_layers),
            ('Ungroup selected layers', self.ungroup_selected_layers),
            ('Move layer up', lambda: self.reorder_selected_layer(-1)),
            ('Move layer down', lambda: self.reorder_selected_layer(1)),
        ):
            self.submenus['layers'].addAction(self.action(self.tr(label), slot))
        for label, slot in (
            ('Switch draft branch…', self.switch_draft),
            ('Compare and merge draft…', self.merge_draft),
            ('Resolve selected merge conflicts…', self.resolve_merge_conflicts),
        ):
            self.submenus['versions'].addAction(self.action(self.tr(label), slot))
        self.export_options_menu.addAction(
            self.action(self.tr('Export with saved profile…'), self.export_profiles)
        )
        self.export_options_menu.addAction(
            self.action(self.tr('Edit export policies…'), self.edit_export_policies)
        )
        menus['F&ormat'].addAction(self.action(self.tr('Page masters…'), self.edit_page_masters))
        menus['&Review'].addAction(self.action(self.tr('Section profiles…'), self.edit_section_profiles))
        menus['&Help'].addAction(self.action(self.tr('Download updates…'), self.open_releases))
        self.build_file_views(menus, workspace_layout)
        self.build_ribbon(menus)
        theme_menu = menus['&View'].addMenu(self.tr('Theme'))
        for key, label in (('system', 'System'), ('light', 'Light'), ('dark', 'Dark')):
            theme_menu.addAction(self.action(self.tr(label), lambda checked=False, t=key: self.set_theme(t)))

    def open_releases(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl('https://github.com/jxburros/ByAuthorX/releases'))

    def set_theme(self, theme):
        self.settings.setValue('appearance/theme', theme)
        self.apply_theme()

    def apply_theme(self, *_):
        configure_application(QApplication.instance())
        from PySide6.QtGui import QPixmap

        from .brand import BRAND_ROOT
        from .gui_art import art_theme

        if hasattr(self, 'brand_mark'):
            self.brand_mark.pixmap = QPixmap(str(BRAND_ROOT / ('wordmark-' + art_theme(self) + '.png')))
            self.brand_mark.update()
        for action in self.findChildren(QAction):
            name = action.property('byaxIcon')
            if name:
                action.setIcon(themed_icon(name, self))

    def create_about_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('About ByAuthorX'))
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(40, 40, 40, 24)
        layout.setSpacing(24)
        wordmark = BrandGraphic('wordmark-light.png', 360)
        wordmark.setAccessibleName(self.tr('ByAuthorX'))
        layout.addWidget(wordmark)
        tagline = QLabel(self.tr('Your words. Your call.'))
        tagline.setObjectName('brandTagline')
        layout.addWidget(tagline)
        layout.addWidget(PenLineAnimation(self.motion))
        description = QLabel(
            self.tr(
                'A native, local-first writing workspace.\n\n'
                'Write. Review. Keep what works.\n'
                'Provider workflows run only when requested.\n'
                'Version %1 · MIT license'
            ).replace('%1', __version__)
        )
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
        QMessageBox.warning(self, self.tr('ByAuthorX'), str(text))

    def report(self, title, result, art=None):
        """Show a result. ``art`` is ``(still, headline, detail)``: an illustrated summary above the text."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        if art:
            layout.addLayout(self.art_header(*art))
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setAccessibleName(title + self.tr(' summary'))
        view.setPlainText(self.readable_report(result))
        layout.addWidget(view)
        if not isinstance(result, str):
            advanced = QCheckBox(self.tr('Advanced technical details'))
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

    def art_header(self, still, headline, detail):
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(BrandGraphic(themed_art(still, self), 120), alignment=Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(4)
        title = QLabel(headline)
        title.setObjectName('emptyHeading')
        title.setWordWrap(True)
        words.addWidget(title)
        body = QLabel(detail)
        body.setObjectName('emptyDescription')
        body.setWordWrap(True)
        words.addWidget(body)
        words.addStretch()
        header.addLayout(words, 1)
        return header

    def readable_report(self, result):
        if isinstance(result, str):
            return result
        labels = {
            b['id']: (b.get('text') or b.get('attrs', {}).get('alt') or b['type'].replace('_', ' ').title())[
                :85
            ]
            for b in self.doc['blocks']
        }
        lines = []

        def describe(record):
            if isinstance(record, str):
                return record
            if not isinstance(record, dict):
                return str(record)
            message = (
                record.get('explanation')
                or record.get('message')
                or record.get('text')
                or record.get('title')
                or record.get('reason')
                or record.get('path')
                or record.get('file')
            )
            if not message:
                message = str(
                    record.get('rule')
                    or record.get('name')
                    or record.get('family')
                    or record.get('type')
                    or record.get('status')
                    or self.tr('Recorded item')
                ).replace('_', ' ')
            severity = record.get('severity', record.get('status', ''))
            location = record.get('target')
            suffix = ('\n  At: ' + labels.get(location, 'Document')) if location else ''
            return (
                ((str(severity).replace('_', ' ').capitalize() + ': ') if severity else '')
                + str(message)
                + suffix
            )

        if isinstance(result, list):
            return '\n\n'.join(describe(item) for item in result) or self.tr('No items.')
        for key in ('execution', 'validation', 'review'):
            if key in result and not isinstance(result[key], (dict, list)):
                lines.append(key.title() + ': ' + str(result[key]).replace('_', ' ').capitalize())
        if result.get('stale'):
            lines.append('This result was produced for an earlier document revision and needs review.')
        for key, label in [
            ('summary', self.tr('Summary')),
            ('explanation', self.tr('Explanation')),
            ('message', self.tr('Result')),
            ('raw_transcript', self.tr('Recognized transcript')),
            ('proposed_text', self.tr('Proposed text')),
            ('text_diff', self.tr('Exact text comparison')),
        ]:
            if result.get(key):
                value = result[key]
                if isinstance(value, dict):
                    value = ' · '.join(
                        str(k).replace('_', ' ').title() + ': ' + str(v)
                        for k, v in value.items()
                        if not isinstance(v, (dict, list))
                    )
                lines += ['', label, str(value)]
        for key, label in [
            ('findings', self.tr('Findings')),
            ('losses', self.tr('Compatibility notes')),
            ('outputs', self.tr('Saved outputs')),
            ('changes', self.tr('Changes')),
            ('operations', self.tr('Exact edits')),
        ]:
            values = result.get(key)
            if not values:
                continue
            lines += ['', label]
            if key == 'changes' and isinstance(values, dict):
                for change_kind in ('added', 'removed'):
                    for block in values.get(change_kind, []):
                        lines.append(
                            change_kind.capitalize()
                            + ': '
                            + (block.get('text') or block.get('type', 'Object'))
                        )
                for change in values.get('modified', []):
                    before, after = change.get('before', {}), change.get('after', {})
                    if before.get('text') != after.get('text'):
                        lines.extend(['Before: ' + before.get('text', ''), 'After: ' + after.get('text', '')])
                    else:
                        lines.append(
                            'Formatting or structure changed: '
                            + labels.get(change.get('id'), before.get('type', 'Object'))
                        )
                if values.get('order_changed'):
                    lines.append('Document reading order changed.')
                if values.get('properties'):
                    lines.append(
                        'Updated document properties: '
                        + ', '.join(key.replace('_', ' ') for key in values['properties'])
                    )
            elif isinstance(values, dict):
                lines.extend(str(k).replace('_', ' ').title() + ': ' + describe(v) for k, v in values.items())
            elif key == 'operations':
                for op in values:
                    target = labels.get(op.get('target'), 'Document')
                    lines.append(
                        op.get('type', op.get('op', 'Edit')).replace('_', ' ').capitalize() + ' — ' + target
                    )
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

    def background(self, label, fn, done, failed=None):
        worker = Worker(fn)
        self._workers.add(worker)
        self._begin_activity(worker, label)

        def finish(result):
            self._workers.discard(worker)
            self._end_activity(worker)
            done(result)

        def fail(error):
            self._workers.discard(worker)
            self._end_activity(worker, self.tr('Task failed'))
            if failed is not None:
                failed()
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
            self.status_label.setText(
                label if count == 1 else self.tr('{0} tasks in progress · {1}').format(count, label)
            )
            self.activity_graphic.show_busy()
        else:
            self.activity_graphic.clear()
            self.status_label.setText(message)

    def _saved_feedback(self):
        if not self._active_tasks:
            self.status_label.setText(self.tr('Saved locally'))
            self.activity_graphic.show_saved()
            self.motion.fade(self.status_label)

    def start_job(self, kind, payload, worker, done):
        from .jobs import JobManager

        if not hasattr(self, 'job_manager'):
            self.job_manager = JobManager()
        job = self.job_manager.submit(kind, payload, worker)
        self._begin_activity(job['id'], kind.replace('_', ' ').title() + self.tr(' in progress…'))
        timer = QTimer(self)
        timer.setInterval(350)

        def poll():
            try:
                status = self.job_manager.status(job['id'], current_revision=self.doc['revision'])
                if status['state'] not in ('completed', 'failed', 'cancelled', 'uncertain'):
                    return
                timer.stop()
                timer.deleteLater()
                self._end_activity(
                    job['id'],
                    kind.title()
                    + ': '
                    + status['state']
                    + self.tr(' · Results available in Tools → Background jobs'),
                )
                recorded = self.job_manager.result(job['id'], current_revision=self.doc['revision'])
                if status['state'] == 'completed':
                    done(recorded['result'])
                elif status['state'] == 'failed':
                    self.error((status.get('error') or {}).get('message', 'Background job failed.'))
            except Exception as exc:
                timer.stop()
                timer.deleteLater()
                self._end_activity(job['id'], self.tr('Task failed'))
                self.error(str(exc))

        timer.timeout.connect(poll)
        timer.start()
        return job

    def refresh(self, position=None, previous=None):
        from .layout import build_document, hand_drawn_marks, update_document

        self._refreshing = True
        try:
            position = self.editor.textCursor().position() if position is None else position
            document = self.editor.document()
            # Structural edits confined to plain text blocks rewrite only the
            # affected blocks; anything else rebuilds the shared document. The editor draws its own
            # underline and strikethrough as pen strokes; pages, print and export are built apart.
            projected = self.projection()
            previous_view = (
                previous
                if self.file_view == 'byax'
                else project({**previous, 'history': {}, 'requests': {}}, self.file_view)[0]
                if previous is not None
                else None
            )
            with hand_drawn_marks(self.hand_marks and self.file_view == 'byax'):
                if previous is None or not update_document(document, previous_view, projected):
                    document = build_document(projected)
                    document.setParent(self.editor)
                    self.editor.setDocument(document)
                    self.editor.setUndoRedoEnabled(False)
            self.editor.capture_baseline()
            self.sync_file_view()
            cursor = self.editor.textCursor()
            cursor.setPosition(min(position, max(0, document.characterCount() - 1)))
            self.editor.setTextCursor(cursor)
            if self.stack.currentIndex() == 1:
                self.update_pages()
            self.update_sidebar()
            self.update_status()
            self.track_action.setChecked(bool(self.doc['settings'].get('track_changes')))
        finally:
            self._refreshing = False
        if getattr(self, '_restore_source', False):
            self._restore_source = False
            self.set_source_mode(1)

    def update_sidebar(self):
        self.update_workspace_sidebars()
        self.outline.clear()
        for block in self.doc['blocks']:
            if block['type'] in ('heading', 'table', 'image', 'footnote', 'section_break') or block.get(
                'style', ''
            ).startswith('heading'):
                label = block.get('text') or block.get('attrs', {}).get('alt') or block['type'].title()
                item = QTreeWidgetItem([label[:65]])
                item.setData(0, Qt.UserRole, block['id'])
                self.outline.addTopLevelItem(item)
        for name, bookmark in self.doc.get('settings', {}).get('bookmarks', {}).items():
            item = QTreeWidgetItem([self.tr('Bookmark: {0}').format(name)])
            item.setData(0, Qt.UserRole, bookmark.get('target'))
            self.outline.addTopLevelItem(item)
        self.review_list.clear()
        for kind in ('comments', 'proposals', 'tracked_changes', 'findings'):
            for record in self.doc['review'].get(kind, []):
                if record.get('status') in ('resolved', 'accepted', 'rejected'):
                    continue
                self.review_list.addItem(
                    record.get('text')
                    or record.get('explanation')
                    or record.get('message')
                    or record.get('title')
                    or kind.replace('_', ' ').title()
                )
                if self.file_view != 'byax' and kind not in FORMATS[self.file_view]['review']:
                    item = self.review_list.item(self.review_list.count() - 1)
                    item.setText(item.text() + self.tr(' (not in this format)'))
                self.review_list.item(self.review_list.count() - 1).setData(Qt.UserRole, (kind, record))
                if kind == 'comments':

                    def show_replies(comment, depth=1):
                        for reply in comment.get('replies', []):
                            if reply.get('status') != 'resolved':
                                self.review_list.addItem(
                                    '    ' * min(depth, 4) + '↳ ' + reply.get('text', self.tr('Reply'))
                                )
                                self.review_list.item(self.review_list.count() - 1).setData(
                                    Qt.UserRole, ('comments', reply)
                                )
                            show_replies(reply, depth + 1)

                    show_replies(record)
        if self._last_report:
            for finding in self._last_report.get('findings', []):
                if finding in self.doc['review'].get('findings', []):
                    continue
                self.review_list.addItem(
                    finding.get('message')
                    or finding.get('explanation')
                    or str(finding.get('rule', 'Finding'))
                )
                self.review_list.item(self.review_list.count() - 1).setData(
                    Qt.UserRole, ('findings', finding)
                )
        self.layer_list.clear()
        for layer in self.doc.get('layers', []):
            self.layer_list.addItem(
                ('● ' if layer.get('visible', True) else '○ ')
                + layer.get('name', 'Layer')
                + ('  🔒' if layer.get('locked') else '')
            )
            self.layer_list.item(self.layer_list.count() - 1).setData(Qt.UserRole, layer)
        self.outline_stack.setCurrentIndex(0 if self.outline.topLevelItemCount() else 1)
        self.review_stack.setCurrentIndex(0 if self.review_list.count() else 1)
        self.layer_stack.setCurrentIndex(0 if self.layer_list.count() else 1)
        self._sync_review_actions()

    def _sync_review_actions(self, *_):
        item = self.review_list.currentItem()
        kind = item.data(Qt.UserRole)[0] if item else None
        enabled = (
            bool(item),
            kind == 'comments',
            kind in ('comments', 'proposals', 'tracked_changes'),
            kind in ('proposals', 'tracked_changes'),
        )
        for button, available in zip(self.review_buttons, enabled):
            button.setVisible(bool(self.review_list.count()))
            button.setEnabled(available)

    def update_status(self):
        if hasattr(self, 'file_view_box'):
            self.projection()
            self.sync_file_view()
        text = model.text_of(self.doc)
        words = len(text.split())
        self.word_label.setText(
            self.tr('{0:,} words   ·   {1:,} characters   ·   {2} min read').format(
                words, len(text), max(1, round(words / 225))
            )
        )
        self.setWindowTitle(
            self.tr('{0}{1} — ByAuthorX').format(
                self.doc['metadata'].get('title', 'Untitled'), ' •' if self.dirty else ''
            )
        )
        title = self.doc['metadata'].get('title', self.tr('Untitled'))
        self.document_title.setText(title)
        self.document_title.setToolTip(title)
        self.document_state.setText(
            self.tr('Saving…')
            if self._save_task is not None and self._save_task['path'] == self.path
            else self.tr('Unsaved changes')
            if self.dirty
            else self.tr('Saved locally')
            if self.path
            else self.tr('New document')
        )

    def selection_changed(self):
        self.sync_ribbon_format()
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
            self.status_label.setText(
                self.tr('Selection: {0} words · {1} characters').format(
                    len(selection.split()), len(selection)
                )
            )

    def _text_changed(self):
        if not self._refreshing:
            self.dirty = True
            self.edit_timer.start()

    def flush_edits(self):
        self.edit_timer.stop()
        if self._refreshing:
            return True
        try:
            if self.stack.currentIndex() == 2:
                return True
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
            if self.file_view != 'byax':
                self.refresh()
            # Keep pending text and the baseline so the user can correct or
            # retry an edit after a conflict or a resource-limit failure.
            return False

    def _commit(self, operations, refresh=True):
        if not operations:
            return None
        old_revision = self.doc['revision']
        candidate, result = engine.apply(
            self.doc,
            {
                'schema_version': 1,
                'expected_revision': old_revision,
                'origin': 'gui',
                'operations': operations,
            },
            allow_policy_changes=True,
            auto_compact_history=True,
        )
        self._undo.append(old_revision)
        self._redo.clear()
        previous, self.doc = self.doc, candidate
        self._prune_history_stacks()
        self.dirty = True
        self._last_report = None
        if refresh:
            self.refresh(previous=previous)
        else:
            from .layout import invalidate_resolved

            invalidate_resolved(self.editor.document())
            self.update_sidebar()
            self.update_status()
        return result

    def commit(self, operations):
        if self.path and self.path.suffix.lower() in ('.md', '.markdown'):
            simple = {
                'insert_text',
                'replace_text',
                'delete_text',
                'set_text',
                'insert_paragraph',
                'delete_block',
                'move_block',
                'split_block',
                'join_blocks',
                'insert_table',
                'update_cell',
                'add_row',
                'remove_row',
                'add_column',
                'remove_column',
                'move_section',
                'reorder_row',
                'reorder_column',
            }
            compatible = all(
                op['type'] in simple
                or (op['type'] == 'replace_content' and 'import_provenance' in op)
                or (op['type'] == 'format_text' and set(op['marks']) <= {'bold', 'italic', 'code', 'link'})
                or (
                    op['type'] == 'apply_style'
                    and op['style'] in {'body', 'heading1', 'heading2', 'heading3', 'code', 'quotation'}
                )
                or (op['type'] == 'set_attributes' and set(op['values']) <= {'list', 'level'})
                for op in operations
            )
            if not compatible and (
                QMessageBox.question(
                    self,
                    self.tr('Convert to ByAuthorX?'),
                    self.tr(
                        'This feature cannot be represented in standard Markdown. Save a native .byax copy before applying it? Your Markdown original is preserved.'
                    ),
                )
                != QMessageBox.Yes
                or not self.save_as()
            ):
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
        self.wait_for_save()
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            self.tr('Save changes?'),
            self.tr('Save your changes before closing this document?'),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Save:
            return self.save(wait=True)
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
        path, _ = QFileDialog.getOpenFileName(self, self.tr('Open document'), '', FILE_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path):
        from .exchange import import_document

        if not self.confirm_discard():
            return False
        selected = Path(path).resolve()
        if self.path == selected:
            return True
        markdown_mode = (
            self.choose_markdown_mode() if selected.suffix.lower() in ('.md', '.markdown') else 'import'
        )
        if markdown_mode is None:
            return False
        backed = selected.suffix.lower() == '.byax' or markdown_mode == 'inplace'
        candidate_lock = None
        try:
            if backed:
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
            self.path = selected if backed else None
            self.dirty = False
            self.restore_file_view()
            self._disk_stamp = self.file_stamp(self.path)
            self._undo.clear()
            self._redo.clear()
            self.refresh(0)
            self.add_recent(selected)
            self.refresh_attached_libraries()
            if report and report.get('losses'):
                self.report(self.tr('Import compatibility report'), report)
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

    def save(self, *, wait=False):
        if not self.flush_edits():
            return False
        if not self.path:
            return self.save_as(wait=wait)
        if self._save_task is not None:
            # One write at a time: the newest model is saved once the current write ends.
            self._save_again = True
            return self.wait_for_save() if wait else True
        if self.file_stamp(self.path) != self._disk_stamp:
            self.error(
                self.tr(
                    'The file changed outside this session. Save as a new file to preserve both versions.'
                )
            )
            return False
        if self.path.suffix.lower() in ('.md', '.markdown'):
            return self._save_markdown()
        path = self.path

        def finished(task, error):
            if error is not None:
                self.error(error)
                return
            if self.path == path:
                self._disk_stamp = self.file_stamp(path)
                self._mark_saved(task['revision'])

        self._start_save(path, finished)
        return self.wait_for_save() if wait else True

    def _save_markdown(self):
        from .exchange import export_document

        try:
            with tempfile.TemporaryDirectory(prefix='.byax-save-', dir=self.path.parent) as temporary:
                stage = Path(temporary) / 'document.md'
                result = export_document(self.doc, stage)
                normalized = [
                    loss
                    for loss in result.get('losses', [])
                    if loss.get('code', loss.get('type')) == 'markdown_normalized'
                ]
                if (
                    normalized
                    and QMessageBox.question(
                        self,
                        self.tr('Normalize Markdown source?'),
                        self.tr(
                            'The structural changes require regenerating Markdown whitespace and syntax. Save these changes?'
                        ),
                    )
                    != QMessageBox.Yes
                ):
                    return False
                os.replace(stage, self.path)
            self._disk_stamp = self.file_stamp(self.path)
            self._mark_saved(self.doc['revision'])
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def _mark_saved(self, revision):
        # Edits made while the write was running keep the document dirty.
        if self.doc['revision'] == revision and not self.edit_timer.isActive():
            self.dirty = False
            self.recovery_path().unlink(missing_ok=True)
        self.update_status()
        self._saved_feedback()
        self.offer_export_after_save()

    def _start_save(self, path, finished, *, label='Saving…', quiet=False):
        # Committed documents are replaced rather than mutated, so a shallow copy
        # is a stable model for the worker while editing continues.
        doc = copy.copy(self.doc)
        task = {'path': path, 'revision': doc['revision'], 'ok': None}
        worker = Worker(lambda: storage.save_document(doc, path, owner=self.owner))
        self._save_task = task
        self._workers.add(worker)
        if not quiet:
            self._begin_activity(worker, label)
        self.update_status()

        def complete(error=None):
            self._workers.discard(worker)
            self._save_task = None
            task['ok'] = error is None
            self._last_save_ok = task['ok']
            if not quiet:
                self._end_activity(worker, self.tr('Ready') if error is None else self.tr('Save failed'))
            try:
                finished(task, error)
            finally:
                if self._save_again:
                    self._save_again = False
                    if self.dirty:
                        self.save()

        worker.signals.done.connect(lambda _result: complete())
        worker.signals.failed.connect(complete)
        QThreadPool.globalInstance().start(worker)
        return task

    def wait_for_save(self):
        """Process events until queued writes finish; returns whether the last write succeeded."""
        from PySide6.QtCore import QEventLoop

        while self._save_task is not None or self._save_again:
            if self._save_task is None:
                self._save_again = False
                if not (self.dirty and self.save()):
                    break
                continue
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents | QEventLoop.WaitForMoreEvents, 50)
        return bool(self._last_save_ok)

    def save_as(self, *, wait=False):
        if not self.flush_edits():
            return False
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr('Save editable master'),
            str(
                self.path
                or Path(
                    self.doc['metadata']
                    .get('import', {})
                    .get('original_path', self.doc['metadata'].get('title', 'Untitled'))
                ).with_suffix('.byax')
            ),
            self.tr('ByAuthorX (*.byax)'),
        )
        if not path:
            return False
        target = Path(path)
        if target.suffix.lower() != '.byax':
            target = target.with_suffix('.byax')
        target = target.resolve()
        if target == self.path:
            return self.save(wait=wait)
        self.wait_for_save()
        candidate_lock = storage.DocumentLock(target, owner=self.owner)
        try:
            candidate_lock.acquire()
        except Exception as exc:
            self.error(exc)
            return False

        def finished(task, error):
            if error is not None:
                candidate_lock.release()
                self.error(error)
                return
            self.release_lock()
            self.lock, self.path = candidate_lock, target
            self.sync_file_view()
            self._disk_stamp = self.file_stamp(target)
            self.add_recent(target)
            self._mark_saved(task['revision'])

        self._start_save(target, finished)
        return self.wait_for_save() if wait else True

    def recovery_path(self):
        return self.recovery_dir / (self.doc['id'] + '.byax')

    def autosave(self):
        if self._save_task is not None or not self.dirty or not self.flush_edits():
            return

        def finished(task, error):
            if error is not None:
                self.status_label.setText(self.tr('Recovery copy failed: ') + str(error))
            elif not self._active_tasks:
                self.status_label.setText(self.tr('Recovery copy saved'))

        self._start_save(self.recovery_path(), finished, quiet=True)

    def recover(self):
        files = sorted(self.recovery_dir.glob('*.byax'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            self.message(self.tr('Recovery'), self.tr('No recovery copies are available.'))
            return
        labels = [f'{p.name} · {p.stat().st_size:,} bytes' for p in files]
        chosen, ok = QInputDialog.getItem(
            self, self.tr('Recover document'), self.tr('Recovery copy'), labels, 0, False
        )
        if ok and self.confirm_discard():
            try:
                document = storage.load_document(files[labels.index(chosen)])
                self.release_lock()
                self.doc, self.path, self.dirty = document, None, True
                self.refresh(0)
            except Exception as exc:
                self.error(exc)

    def check_external_change(self):
        if self._save_task is None and self.path and self.file_stamp(self.path) != self._disk_stamp:
            self.status_label.setText(
                self.tr('File changed outside ByAuthorX. Save as a new file to preserve both versions.')
            )

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

    def _prune_history_stacks(self):
        retained = {r['revision'] for r in self.doc['history']['revisions']} | {self.doc['revision']}
        self._undo = [r for r in self._undo if r in retained]
        self._redo = [r for r in self._redo if r in retained]

    def undo(self):
        if not self.flush_edits():
            return
        if self._undo:
            revision = self._undo[-1]
            try:
                candidate, _ = engine.restore(self.doc, revision, auto_compact_history=True)
                self._undo.pop()
                self._redo.append(self.doc['revision'])
                previous, self.doc = self.doc, candidate
                self._prune_history_stacks()
                self.dirty = True
                self.refresh(previous=previous)
            except Exception as exc:
                self.error(exc)

    def redo(self):
        if not self.flush_edits():
            return
        if self._redo:
            revision = self._redo[-1]
            try:
                candidate, _ = engine.restore(self.doc, revision, auto_compact_history=True)
                self._redo.pop()
                self._undo.append(self.doc['revision'])
                previous, self.doc = self.doc, candidate
                self._prune_history_stacks()
                self.dirty = True
                self.refresh(previous=previous)
            except Exception as exc:
                self.error(exc)

    def target_ids(self):
        self.flush_edits()
        return list(dict.fromkeys(target for target, _, _ in self.editor.selection_targets()))

    def apply_style(self, style):
        operations = []
        if style not in self.doc['styles']:
            defaults = {
                'title': {'font_size': 32, 'bold': True},
                'subtitle': {'font_size': 15, 'color': '#677b76'},
                'quotation': {'italic': True, 'indent': 24},
                'caption': {'font_size': 9},
                'code': {'font_family': 'DejaVu Sans Mono', 'font_size': 10},
            }
            operations.append({'type': 'define_style', 'name': style, 'values': defaults.get(style, {})})
        operations.extend(
            {'type': 'apply_style', 'target': target, 'style': style} for target in self.target_ids()
        )
        self.commit(operations)

    def attributes(self, values):
        self.commit(
            [{'type': 'set_attributes', 'target': target, 'values': values} for target in self.target_ids()]
        )

    def format_selection(self, mark, value):
        self.flush_edits()
        targets = self.editor.selection_targets()
        if not self.editor.textCursor().hasSelection():
            self.status_label.setText(self.tr('Select the text you want to format.'))
            return
        self.commit(
            [
                {'type': 'format_text', 'target': target, 'start': start, 'end': end, 'marks': {mark: value}}
                for target, start, end in targets
                if end > start
            ]
        )

    def clear_formatting(self):
        self.flush_edits()
        self.commit(
            [
                {
                    'type': 'format_text',
                    'target': target,
                    'start': start,
                    'end': end,
                    'marks': {
                        key: False
                        for key in (
                            'bold',
                            'italic',
                            'underline',
                            'strike',
                            'superscript',
                            'subscript',
                            'color',
                            'highlight',
                            'link',
                            'font_family',
                            'font_size',
                        )
                    },
                }
                for target, start, end in self.editor.selection_targets()
                if end > start
            ]
        )

    def choose_color(self, mark):
        color = QColorDialog.getColor(QColor(COLORS['ink']), self)
        if color.isValid():
            self.format_selection(mark, color.name())

    def hyperlink(self):
        value, ok = QInputDialog.getText(self, self.tr('Hyperlink'), self.tr('URL (https://…)'))
        if ok:
            self.format_selection('link', value)

    def paragraph_format(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Font and paragraph'))
        form = QFormLayout(dialog)
        block = next((b for b in self.doc['blocks'] if b['id'] == self.editor.current_id()), {})
        values = {**self.doc['styles'].get(block.get('style', 'body'), {}), **block.get('attrs', {})}
        font = QFontComboBox()
        size = QDoubleSpinBox()
        size.setRange(6, 144)
        size.setValue(11)
        spacing = QDoubleSpinBox()
        spacing.setRange(0.5, 4)
        spacing.setSingleStep(0.1)
        spacing.setValue(1.15)
        indent = QDoubleSpinBox()
        indent.setRange(-144, 432)
        left_indent = QDoubleSpinBox()
        left_indent.setRange(0, 432)
        left_indent.setValue(values.get('indent', 0))
        indent.setValue(values.get('first_line_indent', 0))
        tabs = QLineEdit(', '.join(str(value) for value in values.get('tabs', [])))
        tabs.setPlaceholderText(self.tr('For example: 36, 72, 144'))
        before, after = QDoubleSpinBox(), QDoubleSpinBox()
        before.setRange(0, 144)
        after.setRange(0, 144)
        for label, control in [
            (self.tr('Font'), font),
            (self.tr('Size (pt)'), size),
            (self.tr('Line spacing'), spacing),
            (self.tr('First line indent (negative = hanging, pt)'), indent),
            (self.tr('Left indent (pt)'), left_indent),
            (self.tr('Tab stops (pt, comma separated)'), tabs),
            (self.tr('Space before (pt)'), before),
            (self.tr('Space after (pt)'), after),
        ]:
            form.addRow(label, control)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            try:
                tab_values = sorted(
                    {float(value.strip()) for value in tabs.text().split(',') if value.strip()}
                )
                if any(value <= 0 or value > 2000 for value in tab_values):
                    raise ValueError('Tab stops must be greater than 0 and at most 2,000 points.')
            except ValueError as exc:
                self.error(exc)
                return
            self.attributes(
                {
                    'font_family': font.currentFont().family(),
                    'font_size': size.value(),
                    'indent': left_indent.value(),
                    'tabs': tab_values,
                    'line_spacing': spacing.value(),
                    'first_line_indent': indent.value(),
                    'space_before': before.value(),
                    'space_after': after.value(),
                }
            )

    def set_list(self, values):
        self.commit(
            [{'type': 'set_list', 'target': target, 'values': values} for target in self.target_ids()]
        )

    def list_indent(self, delta):
        operations = []
        for target in self.target_ids():
            block = next((b for b in self.doc['blocks'] if b['id'] == target), None)
            if not block:
                continue
            attrs = block.get('attrs', {})
            listing = attrs.get('list', {'kind': 'bullet', 'level': 1})
            listing = (
                dict(listing)
                if isinstance(listing, dict)
                else {'kind': listing, 'level': max(1, attrs.get('level', 1))}
            )
            listing['level'] = max(1, min(9, listing.get('level', 1) + delta))
            operations.append({'type': 'set_list', 'target': target, 'values': listing})
        self.commit(operations)

    def section_targets(self, target):
        blocks = self.doc['blocks']
        index = next(
            (
                i
                for i, block in enumerate(blocks)
                if block['id'] == target
                or any(cell['id'] == target for row in block.get('rows', []) for cell in row['cells'])
            ),
            0,
        )
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
        dialog.setWindowTitle(self.tr('Find and replace'))
        dialog.resize(540, 240)
        form = QFormLayout(dialog)
        query, replacement = QLineEdit(), QLineEdit()
        case, whole, regex = (
            QCheckBox(self.tr('Match case')),
            QCheckBox(self.tr('Whole words')),
            QCheckBox(self.tr('Regular expression')),
        )
        selected_ranges = {target: (start, end) for target, start, end in self.editor.selection_targets()}
        current_target = self.editor.current_id()
        scope = QComboBox()
        for key, label in [
            ('document', self.tr('Document')),
            ('selection', self.tr('Selection')),
            ('paragraph', self.tr('Current paragraph')),
            ('section', self.tr('Current section')),
        ]:
            scope.addItem(label, key)
        scope.setAccessibleName(self.tr('Search scope'))
        form.addRow(self.tr('Scope'), scope)
        form.addRow(self.tr('Find'), query)
        form.addRow(self.tr('Replace with'), replacement)
        for control in (case, whole, regex):
            form.addRow(control)
        count = QLabel(
            self.tr('Find text before replacing. Replacement always previews the affected matches.')
        )
        count.setWordWrap(True)
        form.addRow(count)
        matches = []
        match_index = 0

        def navigate_match():
            if matches:
                from PySide6.QtGui import QTextCursor

                from .gui_editor import qt_offset

                match = matches[match_index]
                if self.navigate_target(match['target']):
                    cursor = self.editor.textCursor()
                    text = cursor.block().text()
                    base = cursor.block().position()
                    cursor.setPosition(base + qt_offset(text, match['start']))
                    cursor.setPosition(base + qt_offset(text, match['end']), QTextCursor.KeepAnchor)
                    self.editor.setTextCursor(cursor)
                count.setText(self.tr('Match {0} of {1}').format(match_index + 1, len(matches)))

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
                result = engine.find(
                    self.doc,
                    query.text(),
                    case_sensitive=case.isChecked(),
                    whole_word=whole.isChecked(),
                    regex=regex.isChecked(),
                )
                matches = result.get('matches', result.get('results', []))
                if scope.currentData() == 'paragraph':
                    matches = [match for match in matches if match['target'] == current_target]
                elif scope.currentData() == 'section':
                    selected_blocks = self.section_targets(current_target)
                    matches = [match for match in matches if match['block'] in selected_blocks]
                elif scope.currentData() == 'selection':
                    matches = [
                        match
                        for match in matches
                        if match['target'] in selected_ranges
                        and selected_ranges[match['target']][0] <= match['start']
                        and match['end'] <= selected_ranges[match['target']][1]
                    ]
                count.setText(self.tr('{0} matches').format(len(matches)))
                navigate_match()
            except Exception as exc:
                count.setText(str(exc))

        def replace_all():
            search()
            if not matches:
                return
            try:
                replacements = self.replacement_preview(
                    matches, query.text(), replacement.text(), case.isChecked(), regex.isChecked()
                )
            except Exception as exc:
                self.error(exc)
                return
            self.report(self.tr('Replacement preview'), replacements)
            if (
                QMessageBox.question(
                    dialog,
                    self.tr('Replace matches?'),
                    self.tr('Replace {0} matches with {1!r}?').format(len(matches), replacement.text()),
                )
                != QMessageBox.Yes
            ):
                return
            operations = []
            for match in reversed(replacements):
                target = match.get('target') or match.get('id')
                start, end = match['start'], match['end']
                operations += [
                    {'type': 'delete_text', 'target': target, 'start': start, 'end': end},
                    {'type': 'insert_text', 'target': target, 'offset': start, 'text': match['replacement']},
                ]
            if self.commit(operations):
                dialog.accept()

        find_btn, replace_btn = QPushButton(self.tr('Find')), QPushButton(self.tr('Preview replace all…'))
        next_btn = QPushButton(self.tr('Next match'))
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
        return [
            {
                **match,
                'replacement': expanded.get((match['target'], match['start'], match['end']), replacement),
                'explanation': repr(match['text'])
                + ' → '
                + repr(expanded.get((match['target'], match['start'], match['end']), replacement)),
            }
            for match in matches
        ]

    def insert_table(self):
        rows, ok = QInputDialog.getInt(self, self.tr('Insert table'), self.tr('Rows'), 3, 1, 1000)
        if not ok:
            return
        columns, ok = QInputDialog.getInt(self, self.tr('Insert table'), self.tr('Columns'), 3, 1, 100)
        if ok:
            self.commit(
                [{'type': 'insert_table', 'after': self.block_target(), 'rows': rows, 'columns': columns}]
            )

    def block_target(self):
        target = self.editor.current_id()
        for block in self.doc['blocks']:
            if block['id'] == target or any(
                c['id'] == target for r in block.get('rows', []) for c in r['cells']
            ):
                return block['id']
        return self.doc['blocks'][-1]['id'] if self.doc['blocks'] else None

    def table_operation(self, kind):
        target = self.block_target()
        block = next((b for b in self.doc['blocks'] if b['id'] == target and b['type'] == 'table'), None)
        if not block:
            self.error(self.tr('Place the cursor in a table first.'))
            return
        op = {'type': kind, 'target': target}
        if kind.startswith('remove'):
            limit = len(block['rows']) if kind.endswith('row') else len(block['rows'][0]['cells'])
            index, ok = QInputDialog.getInt(
                self, kind.replace('_', ' ').title(), self.tr('Number (starting at 1)'), 1, 1, limit
            )
            if not ok:
                return
            op['index'] = index - 1
        self.commit([op])

    def reorder_table(self, dimension):
        self.flush_edits()
        selected = self.selected_cell()
        if selected is None:
            self.error(self.tr('Place the cursor in the table row or column to move.'))
            return
        table, row, column, _ = selected
        index = row if dimension == 'row' else column
        count = len(table['rows']) if dimension == 'row' else len(table['rows'][0]['cells'])
        destination, ok = QInputDialog.getInt(
            self,
            self.tr('Move ') + dimension,
            self.tr('Move {0} {1} to position (1–{2})').format(dimension, index + 1, count),
            index + 1,
            1,
            count,
        )
        if ok and destination - 1 != index:
            self.commit(
                [
                    {
                        'type': 'reorder_' + dimension,
                        'target': table['id'],
                        'index': index,
                        'to_index': destination - 1,
                    }
                ]
            )

    def table_properties(self):
        block = next(
            (b for b in self.doc['blocks'] if b['id'] == self.block_target() and b['type'] == 'table'), None
        )
        if block is None:
            self.error(self.tr('Place the cursor in a table first.'))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Table properties'))
        form = QFormLayout(dialog)
        header = QCheckBox(self.tr('First row is a repeating header'))
        header.setChecked(bool(block.get('attrs', {}).get('header_rows')))
        padding = QDoubleSpinBox()
        padding.setRange(0, 36)
        padding.setValue(block.get('attrs', {}).get('padding', 4))
        attrs = block.get('attrs', {})
        width = QDoubleSpinBox()
        width.setRange(36, 2000)
        width.setValue(
            attrs.get(
                'width',
                self.doc['layout']['width']
                - self.doc['layout']['margins']['left']
                - self.doc['layout']['margins']['right'],
            )
        )
        columns = QLineEdit(', '.join(str(value) for value in attrs.get('column_widths', [])))
        columns.setPlaceholderText(self.tr('Blank = automatic; otherwise one width per column'))
        border = QDoubleSpinBox()
        border.setRange(0, 12)
        border.setValue(attrs.get('border_width', 0.5))
        alignment = QComboBox()
        for key, label in [
            ('left', self.tr('Left')),
            ('center', self.tr('Center')),
            ('right', self.tr('Right')),
        ]:
            alignment.addItem(label, key)
        alignment.setCurrentIndex(max(0, alignment.findData(attrs.get('alignment', 'left'))))
        colors = {
            'background': attrs.get('background', '#ffffff'),
            'border_color': attrs.get('border_color', '#606060'),
        }
        for key, label in [
            ('background', self.tr('Table shading')),
            ('border_color', self.tr('Border color')),
        ]:
            button = QPushButton(colors[key])

            def choose(checked=False, field=key, control=button):
                color = QColorDialog.getColor(QColor(colors[field]), dialog)
                if color.isValid():
                    colors[field] = color.name()
                    control.setText(color.name())

            button.clicked.connect(choose)
            form.addRow(label, button)
        form.addRow(header)
        form.addRow(self.tr('Table width (pt)'), width)
        form.addRow(self.tr('Column widths (pt, comma separated)'), columns)
        form.addRow(self.tr('Alignment'), alignment)
        form.addRow(self.tr('Cell padding (pt)'), padding)
        form.addRow(self.tr('Border width (pt)'), border)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            try:
                column_widths = [float(value.strip()) for value in columns.text().split(',') if value.strip()]
                if column_widths and (
                    len(column_widths) != len(block['rows'][0]['cells'])
                    or any(value <= 0 for value in column_widths)
                ):
                    raise ValueError('Specify one positive width per column, or leave the field blank.')
                self.commit(
                    [
                        {
                            'type': 'set_attributes',
                            'target': block['id'],
                            'values': {
                                'header_rows': int(header.isChecked()),
                                'padding': padding.value(),
                                'width': width.value(),
                                'column_widths': column_widths,
                                'border_width': border.value(),
                                'alignment': alignment.currentData(),
                                **colors,
                            },
                        }
                    ]
                )
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
            self.error(self.tr('Place the cursor in the top-left cell of the range to merge.'))
            return
        table, row, column, cell = selected
        rows, ok = QInputDialog.getInt(
            self, self.tr('Merge cells'), self.tr('Rows to merge'), 1, 1, len(table['rows']) - row
        )
        if not ok:
            return
        columns, ok = QInputDialog.getInt(
            self,
            self.tr('Merge cells'),
            self.tr('Columns to merge'),
            1,
            1,
            len(table['rows'][row]['cells']) - column,
        )
        if not ok or rows * columns == 1:
            return
        selected_cells = [
            table['rows'][r]['cells'][c]
            for r in range(row, row + rows)
            for c in range(column, column + columns)
        ]
        if any(
            c.get('attrs', {}).get('rowspan', 1) > 1 or c.get('attrs', {}).get('colspan', 1) > 1
            for c in selected_cells
        ):
            self.error(self.tr('Unmerge existing spans before creating a new merged range.'))
            return
        text = '\n'.join(c['text'] for c in selected_cells if c['text'])
        if (
            QMessageBox.question(
                self,
                self.tr('Merge selected range?'),
                self.tr(
                    'Merge these cells and combine their text into the top-left cell? Unmerging later keeps the combined text in that cell.'
                ),
            )
            != QMessageBox.Yes
        ):
            return
        operations = [{'type': 'update_cell', 'target': cell['id'], 'text': text}]
        operations.extend({'type': 'update_cell', 'target': c['id'], 'text': ''} for c in selected_cells[1:])
        operations.append(
            {'type': 'set_attributes', 'target': cell['id'], 'values': {'rowspan': rows, 'colspan': columns}}
        )
        self.commit(operations)

    def unmerge_cell(self):
        selected = self.selected_cell()
        if selected:
            self.commit(
                [
                    {
                        'type': 'set_attributes',
                        'target': selected[3]['id'],
                        'values': {'rowspan': 1, 'colspan': 1},
                    }
                ]
            )

    def import_csv(self):
        from .table_io import read_csv

        path, _ = QFileDialog.getOpenFileName(self, self.tr('Import CSV table'), '', self.tr('CSV (*.csv)'))
        if not path:
            return
        try:
            rows = read_csv(path)
            columns = max((len(row) for row in rows), default=0)
            if not columns or len(rows) > 1000 or columns > 100:
                raise ValueError('A CSV table must contain 1–1,000 rows and 1–100 columns.')
            block = {
                'id': 'table_' + uuid.uuid4().hex,
                'type': 'table',
                'text': '',
                'style': 'body',
                'marks': [],
                'attrs': {'header_rows': 1},
                'rows': [],
            }
            for row in rows:
                block['rows'].append(
                    {
                        'id': 'row_' + uuid.uuid4().hex,
                        'cells': [
                            {'id': 'cell_' + uuid.uuid4().hex, 'text': value, 'attrs': {}}
                            for value in row + [''] * (columns - len(row))
                        ],
                    }
                )
            blocks = copy.deepcopy(self.doc['blocks'])
            index = next(
                (i + 1 for i, old in enumerate(blocks) if old['id'] == self.block_target()), len(blocks)
            )
            blocks.insert(index, block)
            self.commit([{'type': 'replace_content', 'blocks': blocks}])
        except Exception as exc:
            self.error(exc)

    def update_table_csv(self):
        from .table_io import import_csv

        self.flush_edits()
        block = next(
            (b for b in self.doc['blocks'] if b['id'] == self.block_target() and b['type'] == 'table'), None
        )
        if not block:
            self.error(self.tr('Place the cursor in a table first.'))
            return
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr('Update table values from CSV'), '', self.tr('CSV (*.csv)')
        )
        if not path:
            return
        try:
            _, preview = import_csv(self.doc, block['id'], path, grow=True, dry_run=True)
            self.report(self.tr('Preview CSV table update'), preview)
            if (
                QMessageBox.question(
                    self,
                    self.tr('Update table?'),
                    self.tr(
                        'Apply these CSV values? Existing cell identities and formatting are preserved; larger inputs add rows or columns.'
                    ),
                )
                != QMessageBox.Yes
            ):
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
        block = next(
            (b for b in self.doc['blocks'] if b['id'] == self.block_target() and b['type'] == 'table'), None
        )
        if not block:
            self.error(self.tr('Place the cursor in a table first.'))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr('Export table CSV'), 'table.csv', self.tr('CSV (*.csv)')
        )
        if path:
            try:
                result = export_csv(self.doc, block['id'], path, overwrite=True, owner=self.owner)
                self.report(self.tr('CSV export'), result)
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
            self.status_label.setText(self.tr('Place the cursor on the object you want to delete.'))

    def insert_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr('Insert embedded image'),
            '',
            self.tr('Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)'),
        )
        if not path:
            return
        alt, ok = QInputDialog.getText(
            self,
            self.tr('Image description'),
            self.tr('Describe the image for readers using assistive technology'),
        )
        if not ok:
            return
        import mimetypes

        data = Path(path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        from PySide6.QtGui import QImage

        image = QImage(path)
        if image.isNull():
            self.error(self.tr('This image could not be decoded.'))
            return
        width = min(288, image.width())
        height = width * image.height() / max(1, image.width())
        self.commit(
            [
                {
                    'type': 'add_asset',
                    'data': base64.b64encode(data).decode(),
                    'mime': mimetypes.guess_type(path)[0] or 'image/png',
                    'name': Path(path).name,
                },
                {
                    'type': 'place_image',
                    'after': self.block_target(),
                    'asset': digest,
                    'alt': alt,
                    'width': width,
                    'height': height,
                },
            ]
        )

    def insert_page_break(self):
        self.attributes({'page_break_before': True})

    def insert_structure(self, kind, text='', attrs=None):
        self.flush_edits()
        blocks = copy.deepcopy(self.doc['blocks'])
        target = self.block_target()
        index = next((i + 1 for i, b in enumerate(blocks) if b['id'] == target), len(blocks))
        ident = 'obj_' + uuid.uuid4().hex
        blocks.insert(
            index,
            {'id': ident, 'type': kind, 'text': text, 'style': 'body', 'marks': [], 'attrs': attrs or {}},
        )
        return self.commit([{'type': 'replace_content', 'blocks': blocks}])

    def insert_section(self):
        columns, ok = QInputDialog.getInt(
            self, self.tr('Section layout'), self.tr('Number of flowing columns'), 2, 1, 6
        )
        if ok:
            self.insert_structure('section_break', attrs={'columns': columns, 'column_gap': 18})

    def insert_footnote(self):
        target = self.editor.current_id()
        text, ok = QInputDialog.getMultiLineText(self, self.tr('Insert footnote'), self.tr('Footnote text'))
        if ok and text:
            self.insert_structure('footnote', text, {'reference': target})

    def add_bookmark(self):
        targets = self.editor.selection_targets()
        if not targets:
            return
        name, ok = QInputDialog.getText(self, self.tr('Bookmark'), self.tr('Bookmark name'))
        if ok and name:
            self.commit(
                [{'type': 'set_bookmark', 'name': name, 'target': targets[0][0], 'offset': targets[0][1]}]
            )

    def cross_reference(self):
        names = list(self.doc.get('settings', {}).get('bookmarks', {}))
        if not names:
            self.message(
                self.tr('Cross reference'),
                self.tr('Create a bookmark first, then select the text that should refer to it.'),
            )
            return
        name, ok = QInputDialog.getItem(
            self, self.tr('Cross reference'), self.tr('Bookmark'), names, 0, False
        )
        if ok:
            self.format_selection('cross_reference', name)

    def define_style(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Define named style'))
        form = QFormLayout(dialog)
        name = QLineEdit()
        kind = QComboBox()
        kind.addItem(self.tr('Paragraph'), 'paragraph')
        kind.addItem(self.tr('Character'), 'character')
        based_on = QComboBox()

        def parents():
            based_on.clear()
            based_on.addItem(self.tr('None'), None)
            for key, values in self.doc['styles'].items():
                if values.get('kind', 'paragraph') == kind.currentData():
                    based_on.addItem(key, key)

        kind.currentIndexChanged.connect(parents)
        parents()
        font = QFontComboBox()
        size = QDoubleSpinBox()
        size.setRange(6, 144)
        size.setValue(11)
        bold, italic = QCheckBox(self.tr('Bold')), QCheckBox(self.tr('Italic'))
        for label, control in [
            (self.tr('Style name'), name),
            (self.tr('Style kind'), kind),
            (self.tr('Inherit from'), based_on),
            (self.tr('Font'), font),
            (self.tr('Size (pt)'), size),
            (self.tr('Weight'), bold),
            (self.tr('Emphasis'), italic),
        ]:
            form.addRow(label, control)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() and name.text().strip():
            self.commit(
                [
                    {
                        'type': 'define_style',
                        'name': name.text().strip(),
                        'values': {
                            'based_on': based_on.currentData(),
                            'kind': kind.currentData(),
                            'font_family': font.currentFont().family(),
                            'font_size': size.value(),
                            'bold': bold.isChecked(),
                            'italic': italic.isChecked(),
                        },
                    }
                ]
            )
            if (
                kind.currentData() == 'paragraph'
                and name.text().strip() in self.doc['styles']
                and self.style_box.findData(name.text().strip()) == -1
            ):
                self.style_box.addItem(name.text().strip(), name.text().strip())

    def add_comment(self):
        text, ok = QInputDialog.getMultiLineText(self, self.tr('Add comment'), self.tr('Comment'))
        if ok and text:
            targets = self.editor.selection_targets()
            target, start, end = targets[0] if targets else (self.editor.current_id(), 0, 0)
            self.commit([{'type': 'add_comment', 'target': target, 'start': start, 'end': end, 'text': text}])

    def reply_comment(self):
        item = self.review_list.currentItem()
        if item is None or item.data(Qt.UserRole)[0] != 'comments':
            self.status_label.setText(self.tr('Select a comment in the Review panel before replying.'))
            return
        _, comment = item.data(Qt.UserRole)
        text, ok = QInputDialog.getMultiLineText(
            self, self.tr('Reply to comment'), comment.get('text', 'Comment') + self.tr('\n\nYour reply')
        )
        if ok and text.strip():
            self.commit(
                [
                    {
                        'type': 'reply_comment',
                        'target': comment['id'],
                        'text': text.strip(),
                        'author': self.doc['metadata'].get('author', ''),
                    }
                ]
            )

    def move_section(self):
        self.flush_edits()
        headings = [block for block in self.doc['blocks'] if block['type'] == 'heading']
        if not headings:
            self.message(
                self.tr('Move section'),
                self.tr('Add headings to organize the document into movable sections.'),
            )
            return
        target = self.editor.current_id()
        current = 0
        for block in self.doc['blocks']:
            if block['type'] == 'heading':
                current = next(i for i, heading in enumerate(headings) if heading['id'] == block['id'])
            if block['id'] == target:
                break
        labels = [
            f'{index + 1}. {heading.get("text") or "Untitled heading"}'
            for index, heading in enumerate(headings)
        ]
        source_label, ok = QInputDialog.getItem(
            self,
            self.tr('Move section'),
            self.tr('Section to move, including its nested headings and anchored objects'),
            labels,
            current,
            False,
        )
        if not ok:
            return
        source = headings[labels.index(source_label)]
        # A heading subtree cannot be its own destination.
        source_index = self.doc['blocks'].index(source)
        source_level = int(source.get('attrs', {}).get('level', 1))
        descendants = {source['id']}
        for block in self.doc['blocks'][source_index + 1 :]:
            if block['type'] == 'heading' and int(block.get('attrs', {}).get('level', 1)) <= source_level:
                break
            descendants.add(block['id'])
        destinations = [heading for heading in headings if heading['id'] not in descendants]
        options = ['End of document'] + [
            f'After section: {heading.get("text") or "Untitled heading"} ({index + 1})'
            for index, heading in enumerate(destinations)
        ]
        destination, ok = QInputDialog.getItem(
            self, self.tr('Move section'), self.tr('New location'), options, 0, False
        )
        if ok:
            index = options.index(destination)
            operation = {'type': 'move_section', 'target': source['id']}
            if index:
                operation['after'] = destinations[index - 1]['id']
            if self.commit([operation]):
                self.editor.navigate(source['id'])

    def protect(self):
        self.commit([{'type': 'protect', 'target': target, 'kind': 'text'} for target in self.target_ids()])

    def unprotect(self):
        targets = self.target_ids()
        rules = [rule for rule in self.doc['settings'].get('protections', []) if rule['target'] in targets]
        if (
            rules
            and QMessageBox.question(
                self,
                self.tr('Remove protection?'),
                self.tr(
                    'Allow future edits to the selected protected content? This policy change is recorded in history.'
                ),
            )
            == QMessageBox.Yes
        ):
            self.commit([{'type': 'unprotect', **rule} for rule in rules])

    def review_item(self, item=None):
        item = item if hasattr(item, 'data') else self.review_list.currentItem()
        if item:
            kind, record = item.data(Qt.UserRole)
            target = record.get('target')
            if target:
                self.navigate_target(target)
            self.report(kind.replace('_', ' ').title(), record)

    def accept_item(self):
        if self.resolve_selected_changes(True):
            return
        item = self.review_list.currentItem()
        if not item:
            return
        kind, record = item.data(Qt.UserRole)
        if kind == 'proposals' and record.get('edits'):
            self.review_proposal_edits(record, True)
            return
        if kind == 'comments':
            self.commit([{'type': 'resolve_comment', 'target': record['id']}])
        elif kind in ('proposals', 'tracked_changes'):
            self.report(self.tr('Review exact changes before accepting'), record)
            if (
                QMessageBox.question(
                    self,
                    self.tr('Apply changes?'),
                    self.tr('Apply these exact proposed changes to the document?'),
                )
                == QMessageBox.Yes
            ):
                operation = 'accept_proposal' if kind == 'proposals' else 'accept_change'
                self.commit([{'type': operation, 'target': record['id']}])
        else:
            self.report(self.tr('Finding details'), record)

    def reject_item(self):
        if self.resolve_selected_changes(False):
            return
        item = self.review_list.currentItem()
        if item:
            kind, record = item.data(Qt.UserRole)
            if kind == 'proposals' and record.get('edits'):
                self.review_proposal_edits(record, False)
                return
            if kind in ('proposals', 'tracked_changes'):
                self.commit(
                    [
                        {
                            'type': 'reject_proposal' if kind == 'proposals' else 'reject_change',
                            'target': record['id'],
                        }
                    ]
                )

    def edit_profiles(self):
        from .gui_profiles import ProfileDialog

        ProfileDialog(self).exec()

    def run_all_checks(self):
        self.checks_panel.run_all()

    def run_checks(self, suites=None):
        from .checks import run_checks

        if isinstance(suites, bool):  # QAction.triggered supplies its checked state.
            suites = None

        self.flush_edits()
        frozen = copy.deepcopy(self.doc)

        def finished(result):
            self.review_empty.set_busy(False)
            if frozen['revision'] != self.doc['revision']:
                self.report(self.tr('Checks completed against an earlier revision'), result)
                return
            self._last_report = result
            self._check_marks = tuple(self.marks_from_findings(result.get('findings') or [], self.doc))
            self.sync_proof_marks()
            self.update_sidebar()
            self.status_label.setText(
                self.tr('Checks: ')
                + str(result.get('validation', 'complete'))
                + self.tr(' · See Review for findings')
            )
            self.report(self.tr('Document health and actual coverage'), result, art=self.checks_art(result))

        profile = getattr(self, 'active_profile', None)
        profile = profile if profile in frozen.get('profiles', {}) else None
        self.review_empty.set_busy(True)
        self.background(
            self.tr('Checking document…'),
            lambda: run_checks(frozen, suites=suites, profile=profile),
            finished,
            failed=lambda: self.review_empty.set_busy(False),
        )

    def set_hand_marks(self, enabled):
        self.hand_marks = bool(enabled)
        self.settings.setValue('appearance/handDrawnMarks', self.hand_marks)
        self.editor.set_hand_marks(self.hand_marks)
        self.hand_marks_action.setChecked(self.hand_marks)
        self.refresh(previous=None)  # rebuild the editing document with native or pen-stroke decorations

    def finding_label(self, finding):
        text = finding.get('message') or finding.get('explanation')
        if text:
            return str(text)
        rule = str(finding.get('rule', ''))
        word = (finding.get('measured') or {}).get('word')
        if rule == 'spelling.unknown' and word:
            return self.tr('Possible misspelling: {0}').format(word)
        return rule.replace('.', ': ').replace('_', ' ').capitalize()

    def marks_from_findings(self, findings, doc):
        """Proofreader's marks for spelling and grammar findings that locate a span in a block."""
        marks = []
        for finding in findings:
            family = finding.get('family')
            target = (finding.get('affected_ids') or [None])[0]
            span = finding.get('range') or {}
            if family not in ('spelling', 'grammar') or not target or 'start' not in span:
                continue
            block = model.find_object(doc, target)
            if not block or not block.get('text'):
                continue
            marks.append(
                ProofMark(
                    target,
                    span['start'],
                    span['end'],
                    family,
                    self.finding_label(finding),
                    block['text'],
                )
            )
        return marks

    def sync_proof_marks(self):
        """Show the latest check and spelling results as hand-inked marks in the text."""
        merged = {}
        for mark in (*self._check_marks, *self.ai_panel.spelling_marks()):
            merged.setdefault(mark.key, mark)
        self.editor.set_proof_marks(merged.values())

    def checks_art(self, result):
        """Illustration and plain-language summary for a finished check; none when it proves nothing."""
        findings = result.get('findings') or []
        if findings:
            count = len(findings)
            return (
                'proofread-marks',
                self.tr('{0} item{1} need a look').format(count, '' if count == 1 else 's'),
                self.tr('Each one is listed in Review. The coverage notes below say what was measured.'),
            )
        if result.get('validation') == 'passed':
            return (
                'checks-clear',
                self.tr('Nothing to flag'),
                self.tr(
                    'Every check that ran came back clean. The coverage notes below say what was measured.'
                ),
            )
        return None

    def add_layer(self):
        name, ok = QInputDialog.getText(
            self, self.tr('Add watermark / layer'), self.tr('Layer name'), text=self.tr('Watermark')
        )
        if not ok:
            return
        text, ok = QInputDialog.getText(self, self.tr('Layer text'), self.tr('Text'), text=self.tr('DRAFT'))
        if ok:
            self.commit(
                [
                    {
                        'type': 'add_layer',
                        'values': {
                            'name': name,
                            'type': 'text',
                            'text': text,
                            'visible': True,
                            'locked': False,
                            'x': 160,
                            'y': 360,
                            'width': 300,
                            'height': 100,
                            'opacity': 0.15,
                            'font_size': 52,
                            'z': -1,
                        },
                    }
                ]
            )

    def toggle_layer(self):
        item = self.layer_list.currentItem()
        if item:
            layer = item.data(Qt.UserRole)
            self.commit(
                [
                    {
                        'type': 'update_layer',
                        'target': layer['id'],
                        'values': {'visible': not layer.get('visible', True)},
                    }
                ]
            )

    def lock_layer(self):
        item = self.layer_list.currentItem()
        if item:
            layer = item.data(Qt.UserRole)
            self.commit(
                [
                    {
                        'type': 'update_layer',
                        'target': layer['id'],
                        'values': {'locked': not layer.get('locked', False)},
                    }
                ]
            )

    def add_reference(self):
        title, ok = QInputDialog.getText(self, self.tr('Attach reference'), self.tr('Source title'))
        if not ok:
            return
        excerpt, ok = QInputDialog.getMultiLineText(
            self, self.tr('Reference excerpt'), self.tr('Approved evidence excerpt (stored in this document)')
        )
        if ok:
            self.commit(
                [
                    {
                        'type': 'add_reference',
                        'id': 'ref_' + uuid.uuid4().hex,
                        'values': {'title': title, 'excerpt': excerpt, 'target': self.editor.current_id()},
                    }
                ]
            )

    def toggle_tracking(self, enabled):
        if (
            enabled
            or QMessageBox.question(
                self,
                self.tr('Disable tracking?'),
                self.tr(
                    'Stop recording tracked changes for future edits? Existing tracked changes are retained.'
                ),
            )
            == QMessageBox.Yes
        ):
            self.commit([{'type': 'set_tracking', 'enabled': enabled}])
        self.track_action.setChecked(bool(self.doc['settings'].get('track_changes')))

    def define_field(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Define template field'))
        form = QFormLayout(dialog)
        name, description, default = QLineEdit(), QLineEdit(), QLineEdit()
        kind = QComboBox()
        for key, label in [
            ('text', self.tr('Text')),
            ('number', self.tr('Number')),
            ('date', self.tr('Date')),
        ]:
            kind.addItem(label, key)
        required = QCheckBox(self.tr('Required before publication'))
        for label, control in [
            (self.tr('Field name'), name),
            (self.tr('Type'), kind),
            (self.tr('Description'), description),
            (self.tr('Default'), default),
            (self.tr('Requirement'), required),
        ]:
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
                self.error(self.tr('Use letters, numbers, dots, underscores, or dashes in field names.'))
                return
            value = default.text()
            if kind.currentData() == 'number' and value:
                try:
                    value = float(value)
                except ValueError:
                    self.error(self.tr('The default must be a number.'))
                    return
            field = {
                'type': kind.currentData(),
                'description': description.text(),
                'required': required.isChecked(),
            }
            if value != '':
                field['default'] = value
                field['value'] = value
            operations = [
                {
                    'type': 'insert_text',
                    'target': targets[0][0],
                    'offset': targets[0][1],
                    'text': '{{' + key + '}}',
                },
                {'type': 'fill_fields', 'values': {key: field}},
            ]
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
                value, ok = QInputDialog.getDouble(
                    self, self.tr('Fill template fields'), label, float(initial or 0), -1e12, 1e12, 6
                )
            elif field.get('type') in ('enum', 'enumerated'):
                choices = [str(v) for v in field.get('options', field.get('choices', []))]
                value, ok = QInputDialog.getItem(
                    self, self.tr('Fill template fields'), label, choices, 0, False
                )
            else:
                value, ok = QInputDialog.getText(
                    self,
                    self.tr('Fill template fields'),
                    label + (self.tr(' (YYYY-MM-DD)') if field.get('type') == 'date' else ''),
                    text=str(initial),
                )
            if not ok:
                return
            values[key] = value
        if values:
            self.commit([{'type': 'fill_fields', 'values': values}])
        else:
            self.message(
                self.tr('Template fields'), self.tr('This document does not define any template fields.')
            )

    def checkpoint(self):
        self.flush_edits()
        name, ok = QInputDialog.getText(self, self.tr('Save checkpoint'), self.tr('Checkpoint name'))
        if ok and name:
            try:
                self.doc, _ = engine.checkpoint(self.doc, name)
                self.dirty = True
                self.update_status()
            except Exception as exc:
                self.error(exc)

    def branch(self):
        self.flush_edits()
        name, ok = QInputDialog.getText(self, self.tr('Alternate draft'), self.tr('Branch name'))
        if ok and name:
            try:
                self.doc, _ = engine.branch(self.doc, name)
                self.dirty = True
                self.status_label.setText(self.tr('Draft branch: ') + name)
            except Exception as exc:
                self.error(exc)

    def history(self):
        self.flush_edits()
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Revision history'))
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
                    self.report(
                        self.tr('Semantic revision comparison'),
                        engine.diff(self.doc, revisions.currentItem().data(Qt.UserRole)),
                    )
                except Exception as exc:
                    self.error(exc)

        def restore():
            if (
                revisions.currentItem()
                and QMessageBox.question(
                    dialog,
                    self.tr('Restore revision?'),
                    self.tr('Restore this revision as the current document?'),
                )
                == QMessageBox.Yes
            ):
                try:
                    self.doc, _ = engine.restore(
                        self.doc, revisions.currentItem().data(Qt.UserRole), auto_compact_history=True
                    )
                    self._prune_history_stacks()
                    self.dirty = True
                    self.refresh()
                    dialog.accept()
                except Exception as exc:
                    self.error(exc)

        for label, slot in [
            (self.tr('Compare with current'), compare),
            (self.tr('Restore selected'), restore),
        ]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            layout.addWidget(button)
        dialog.exec()

    def page_setup(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Page setup'))
        form = QFormLayout(dialog)
        paper = QComboBox()
        for key, label in [
            ('Letter', self.tr('Letter')),
            ('A4', self.tr('A4')),
            ('Custom', self.tr('Custom')),
        ]:
            paper.addItem(label, key)
        paper.setCurrentIndex(max(0, paper.findData(self.doc['layout'].get('paper', 'Letter'))))
        landscape = QCheckBox(self.tr('Landscape'))
        width, height = QDoubleSpinBox(), QDoubleSpinBox()
        for control, key, default in [(width, 'width', 612), (height, 'height', 792)]:
            control.setRange(72, 5000)
            control.setValue(self.doc['layout'].get(key, default))
        margins = {}
        form.addRow(self.tr('Paper'), paper)
        form.addRow(landscape)
        form.addRow(self.tr('Width (pt)'), width)
        form.addRow(self.tr('Height (pt)'), height)
        for side, label in [
            ('top', self.tr('Top margin (pt)')),
            ('right', self.tr('Right margin (pt)')),
            ('bottom', self.tr('Bottom margin (pt)')),
            ('left', self.tr('Left margin (pt)')),
        ]:
            spin = QDoubleSpinBox()
            spin.setRange(0, 720)
            spin.setValue(self.doc['layout'].get('margins', {}).get(side, 72))
            margins[side] = spin
            form.addRow(label, spin)
        header, footer = (
            QLineEdit(str(self.doc['layout'].get('header', ''))),
            QLineEdit(str(self.doc['layout'].get('footer', ''))),
        )
        form.addRow(self.tr('Header'), header)
        form.addRow(self.tr('Footer ({page} / {pages})'), footer)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            w, h = {'Letter': (612, 792), 'A4': (595.276, 841.89)}.get(
                paper.currentData(), (width.value(), height.value())
            )
            if landscape.isChecked():
                w, h = max(w, h), min(w, h)
            self.commit(
                [
                    {
                        'type': 'set_layout',
                        'values': {
                            'paper': paper.currentData(),
                            'width': w,
                            'height': h,
                            'margins': {k: v.value() for k, v in margins.items()},
                            'header': header.text(),
                            'footer': footer.text(),
                        },
                    }
                ]
            )

    def toggle_pages(self):
        if not FORMATS[self.file_view]['paged']:
            self.status_label.setText(self.tr('This format has no pages'))
            return
        if not self.flush_edits():
            return
        if self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.update_pages()
            self.view_label.setText(self.tr('Page preview'))
            if not self._active_tasks:
                self.status_label.setText(self.tr('Print layout · Ctrl+Alt+L to return to writing'))
        else:
            self.stack.setCurrentIndex(0)
            self.editor.setFocus()
            self.view_label.setText(self.tr('Writing view'))
            if not self._active_tasks:
                self.status_label.setText(self.tr('Writing view'))
        self.motion.fade(self.stack.currentWidget())

    def update_pages(self):
        """Resolve physical pages on a worker and show them when they match the model."""
        from .layout import adopt_resolved, resolve_pages, resolved_documents

        document, doc = self.editor.document(), self.projection()
        if None in getattr(document, 'byax_resolved_outputs', {}):
            self.pages.set_document(document, doc)
            return
        self._pages_generation += 1
        generation = self._pages_generation
        main_thread = QApplication.instance().thread()

        def work():
            # Only unparented documents are built here; they are handed to the
            # GUI thread before any widget paints them.
            resolved = resolve_pages(copy.copy(doc))
            for part in resolved_documents(resolved):
                part.moveToThread(main_thread)
            return resolved

        def done(resolved):
            if generation != self._pages_generation:
                return
            if self.projection() is not doc or self.editor.document() is not document:
                if self.stack.currentIndex() == 1:
                    self.update_pages()
                return
            adopt_resolved(document, resolved)
            self.pages.set_document(document, doc)

        self.background(self.tr('Laying out pages…'), work, done)

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
        printer.setPageSize(
            QPageSize(QSizeF(self.doc['layout']['width'], self.doc['layout']['height']), QPageSize.Point)
        )
        painter = QPainter(printer)
        if not painter.isActive():
            self.error(self.tr('The printer could not start the print job.'))
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
        if (
            QMessageBox.question(
                self,
                self.tr('Redact selected paragraphs?'),
                self.tr(
                    'Create a sanitized text-only copy that removes the selected paragraphs, history, review material, and assets? The private master remains unchanged.'
                ),
            )
            != QMessageBox.Yes
        ):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr('Save sanitized copy'), 'Sanitized.byax', self.tr('ByAuthorX (*.byax)')
        )
        if path:
            try:
                self.report(self.tr('Verified sanitization report'), save_sanitized(self.doc, path, targets))
            except Exception as exc:
                self.error(exc)

    def jobs_dialog(self):
        from .jobs import JobManager

        manager = getattr(self, 'job_manager', None) or JobManager()
        self.job_manager = manager
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Background jobs and recoverable results'))
        dialog.resize(720, 440)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        listing.setAccessibleName(self.tr('Background jobs'))
        jobs_view = QStackedWidget()
        jobs_view.addWidget(listing)
        jobs_view.addWidget(
            EmptyPanel(
                self.tr('Nothing running in the background'),
                self.tr('Long tasks, such as transcription, are listed here so a result can be recovered.'),
                graphic=themed_art('jobs-empty', self),
            )
        )

        def update():
            listing.clear()
            for job in manager.list():
                listing.addItem(job['kind'] + ' · ' + job['state'] + ' · ' + job['id'])
                listing.item(listing.count() - 1).setData(Qt.UserRole, job['id'])
            jobs_view.setCurrentIndex(0 if listing.count() else 1)

        layout.addWidget(jobs_view)

        def result():
            if listing.currentItem():
                try:
                    self.report(
                        self.tr('Recorded job result'),
                        manager.result(listing.currentItem().data(Qt.UserRole)),
                    )
                except Exception as exc:
                    self.error(exc)

        def cancel():
            if listing.currentItem():
                manager.cancel(listing.currentItem().data(Qt.UserRole))
                update()

        for label, slot in [
            (self.tr('Refresh'), update),
            (self.tr('View result'), result),
            (self.tr('Request cancellation'), cancel),
        ]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            layout.addWidget(button)
        update()
        dialog.exec()

    def export(self, fmt=None):
        """Export a copy; ``fmt`` (a format id) preselects that format in the save dialog."""
        from .exchange import export_document
        from .publication import export_clean

        if isinstance(fmt, bool):  # QAction.triggered supplies its checked state.
            fmt = None
        if not self.flush_edits():
            return
        filters = [
            ('pdf', 'PDF (*.pdf)'),
            ('docx', 'Word (*.docx)'),
            ('odt', 'OpenDocument (*.odt)'),
            ('md', 'Markdown (*.md)'),
            ('html', 'HTML (*.html)'),
            ('rtf', 'Rich text (*.rtf)'),
            ('txt', 'Plain text (*.txt)'),
        ]
        if fmt:
            suggestion = str(Path(self.export_suggestion()).with_suffix('.' + fmt))
        elif getattr(self, '_selected_export_profile', None) in self.doc['profiles'] and self.doc['profiles'][
            self._selected_export_profile
        ].get('format'):
            suggestion = str(
                Path(self.export_suggestion()).with_suffix(
                    '.' + self.doc['profiles'][self._selected_export_profile]['format']
                )
            )
        else:
            suggestion = self.export_suggestion()
        chosen = next((self.tr(label) for ident, label in filters if ident == fmt), '')
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr('Export publication copy'),
            suggestion,
            ';;'.join(self.tr(label) for _, label in filters),
            chosen,
        )
        if not path:
            return
        doc = copy.copy(self.doc)
        profile = copy.deepcopy(doc.get('profiles', {}).get(getattr(self, '_selected_export_profile', None)))
        writer = (
            export_clean
            if (profile and profile.get('clean'))
            or (self.file_view == 'byax' and self.export_offer.isHidden())
            else export_document
        )
        if writer is export_document:
            profile = {**(profile or {}), 'allow_markdown_losses': True}
        self.background(
            self.tr('Exporting…'),
            lambda: writer(doc, path, profile=profile),
            lambda report: self.report(
                self.tr('Export result and compatibility'),
                report,
                art=(
                    'export-ready',
                    self.tr('Export finished'),
                    self.tr('Your document is unchanged. Compatibility notes are listed below.'),
                ),
            ),
        )

    def source_view(self):
        from .exchange import export_document, import_document

        self.flush_edits()
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Markdown source'))
        dialog.resize(850, 680)
        layout = QVBoxLayout(dialog)
        markdown_session = bool(self.path and self.path.suffix.lower() in ('.md', '.markdown'))
        note = QLabel(
            self.tr(
                'CommonMark source with tables. Source edits update this Markdown document and preserve unchanged block identities.'
            )
            if markdown_session
            else self.tr(
                'CommonMark source with tables. Applying source creates a new document; save it separately to preserve the rich master.'
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        editor = QPlainTextEdit()
        editor.setAccessibleName(self.tr('Markdown source editor'))
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / 'source.md'
            export_document(self.doc, source_path)
            editor.setPlainText(source_path.read_text())
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        initial_source = editor.toPlainText()
        apply = buttons.addButton(
            'Apply source edits' if markdown_session else 'Create document from source',
            QDialogButtonBox.ActionRole,
        )

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
                    self.report(self.tr('Markdown conversion'), report)

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
        matcher = difflib.SequenceMatcher(
            a=[signature(b) for b in original], b=[signature(b) for b in incoming], autojunk=False
        )
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
        provenance.update(
            original_path=str(self.path),
            filename=self.path.name,
            block_order=[b['id'] for b in incoming],
            fingerprints={
                b['id']: hashlib.sha256(
                    json.dumps(b, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                for b in incoming
            },
        )
        return self.commit(
            [
                {
                    'type': 'replace_content',
                    'blocks': incoming,
                    'assets': imported['assets'],
                    'import_provenance': provenance,
                }
            ]
        )

    def document_properties(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Document properties'))
        form = QFormLayout(dialog)
        controls = {}
        for key, label in [
            ('title', self.tr('Title')),
            ('author', self.tr('Author')),
            ('language', self.tr('Language')),
            ('description', self.tr('Description')),
        ]:
            controls[key] = QLineEdit(self.doc['metadata'].get(key, ''))
            form.addRow(label, controls[key])
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.commit([{'type': 'set_metadata', 'values': {k: v.text() for k, v in controls.items()}}])

    def statistics(self):
        from .statistics import counts, document_statistics

        if not self.flush_edits():
            return
        report = document_statistics(self.doc, output=self.file_view)
        report['selection'] = counts(self.editor.textCursor().selectedText())
        self.report(self.tr('Document statistics'), report)

    def templates(self):
        kind, ok = QInputDialog.getItem(
            self,
            self.tr('New from template'),
            self.tr('Template'),
            [self.tr(label) for label in TEMPLATE_CHOICES],
            0,
            False,
        )
        if not ok:
            return
        kind = TEMPLATE_CHOICES[[self.tr(label) for label in TEMPLATE_CHOICES].index(kind)]
        if kind == 'Open template file…':
            path, _ = QFileDialog.getOpenFileName(
                self, self.tr('Open template'), '', self.tr('ByAuthorX (*.byax)')
            )
            if path and self.open_path(path):
                self.release_lock()
                self.doc = {**self.doc, 'id': 'doc_' + uuid.uuid4().hex}
                self.path, self.dirty = None, True
            return
        if not self.confirm_discard():
            return
        examples = {
            'Letter': [
                ('title', 'Your name'),
                ('body', 'Your address · City, State'),
                ('body', 'October 13, 2026'),
                ('body', 'Dear recipient,'),
                ('body', 'Write your letter here.'),
                ('body', 'Sincerely,\nYour name'),
            ],
            'Resume': [
                ('title', 'Your name'),
                ('subtitle', 'Your profession · email@example.com'),
                ('heading1', 'Profile'),
                ('body', 'A concise introduction to your experience and strengths.'),
                ('heading1', 'Experience'),
                ('heading2', 'Position · Organization'),
                ('body', 'Describe the impact of your work.'),
                ('heading1', 'Education'),
                ('body', 'Qualification · Institution'),
            ],
            'Report': [
                ('title', 'Report title'),
                ('subtitle', 'Prepared by your name'),
                ('heading1', 'Executive summary'),
                ('body', 'Summarize the purpose and findings.'),
                ('heading1', 'Background'),
                ('body', 'Explain the context.'),
                ('heading1', 'Findings'),
                ('body', 'Present the evidence.'),
                ('heading1', 'Recommendations'),
                ('body', 'Set out the next steps.'),
            ],
            'Music press kit': [
                ('title', 'ARTIST NAME'),
                ('subtitle', 'PRESS KIT · New release'),
                ('heading1', 'Biography'),
                ('body', 'Introduce the artist, their sound, and the story behind the music.'),
                ('heading1', 'The release'),
                ('body', 'Album title · Release date · Genre'),
                ('heading1', 'Press & booking'),
                ('body', 'Contact name · email@example.com'),
            ],
        }
        document = model.new_document(kind)
        document['styles'].update(
            {
                'title': {'font_size': 32, 'bold': True},
                'subtitle': {'font_size': 15, 'color': '#677b76'},
                'quotation': {'italic': True, 'indent': 24},
                'caption': {'font_size': 9},
                'code': {'font_family': 'DejaVu Sans Mono', 'font_size': 10},
            }
        )
        document['blocks'] = []
        for style, text in examples[kind]:
            block = {
                'id': 'p_' + uuid.uuid4().hex,
                'type': 'heading' if style.startswith('heading') else 'paragraph',
                'text': text,
                'style': style,
                'attrs': {},
                'marks': [],
            }
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
