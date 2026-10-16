"""User-selectable workspace sidebars and the inspector's Proposed changes tab."""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStyle,
    QTabBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .gui_art import EmptyPanel, themed_art


class InspectorTabBar(QTabBar):
    """Paint the pen underline inside the selected tab, without CSS image bleeding."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.currentIndex() < 0:
            return
        from .brand import BRAND_ROOT
        from .gui_art import art_theme

        rect = self.tabRect(self.currentIndex())
        pixmap = QPixmap(str(BRAND_ROOT / 'ui' / f'tab-underline-{art_theme(self)}.png'))
        painter = QPainter(self)
        painter.setClipRect(rect)
        painter.drawPixmap(QRect(rect.left() + 4, rect.bottom() - 7, rect.width() - 8, 8), pixmap)
        painter.end()


class SidebarTitle(QWidget):
    """A dock title that is itself the panel selector, with the dock's own float and close buttons."""

    def __init__(self, dock, selector):
        super().__init__(dock)
        self.setObjectName('sidebarTitle')
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        selector.setObjectName('sidebarSelector')
        selector.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        layout.addWidget(selector)
        layout.addStretch(1)
        for name, icon, slot in (
            (
                'Detach sidebar',
                QStyle.SP_TitleBarNormalButton,
                lambda: dock.setFloating(not dock.isFloating()),
            ),
            ('Close sidebar', QStyle.SP_TitleBarCloseButton, dock.close),
        ):
            button = QToolButton()
            button.setObjectName('sidebarTitleButton')
            button.setIcon(self.style().standardIcon(icon))
            button.setAutoRaise(True)
            button.setAccessibleName(self.tr(name))
            button.setToolTip(self.tr(name))
            button.clicked.connect(slot)
            layout.addWidget(button)


class WorkspacePanels:
    def build_proposed_changes(self):
        """The Proposed changes tab: everything waiting on the author, and one button to look for more."""
        page = QWidget()
        rv = QVBoxLayout(page)
        rv.setContentsMargins(8, 12, 8, 12)
        rv.setSpacing(8)
        self.run_all_button = QPushButton(self.tr('Run all active checks'))
        self.run_all_button.setProperty('primary', True)
        self.run_all_button.setToolTip(self.tr('Run every check marked active in the Checks tab'))
        self.run_all_button.clicked.connect(self.run_all_checks)
        rv.addWidget(self.run_all_button)
        self.active_checks_label = QLabel()
        self.active_checks_label.setObjectName('emptyDescription')
        self.active_checks_label.setWordWrap(True)
        rv.addWidget(self.active_checks_label)
        # Spelling results keep their own list, stale-range guards and explicit apply; they show once checked.
        rv.addWidget(self.ai_panel.spelling_widget)
        self.review_list = QListWidget()
        self.review_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.review_list.setWordWrap(True)
        # Items wrap to the panel, so they never need sideways scrolling, whatever the face's widths.
        self.review_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.review_list.setResizeMode(QListWidget.Adjust)
        self.review_list.setObjectName('proposedChanges')
        self.review_list.setAccessibleName(self.tr('Proposed changes, comments, and findings'))
        self.review_list.itemActivated.connect(self.review_item)
        self.review_stack = QStackedWidget()
        self.review_stack.addWidget(self.review_list)
        self.review_empty = EmptyPanel(
            self.tr('No proposed changes'),
            self.tr('Suggested edits, comments, and check results appear here.'),
            graphic='ui/review-empty.png',
            busy=('checking-page', 94, self.motion),
        )
        self.review_stack.addWidget(self.review_empty)
        rv.addWidget(self.review_stack, 1)
        self.review_buttons = []
        for label, slot in [
            (self.tr('Open selected item'), self.review_item),
            (self.tr('Reply to comment…'), self.reply_comment),
            (self.tr('Resolve / accept…'), self.accept_item),
            (self.tr('Reject proposal'), self.reject_item),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            rv.addWidget(btn)
            self.review_buttons.append(btn)
        self.review_list.currentRowChanged.connect(self._sync_review_actions)
        return page

    def build_layout_panel(self):
        """The Layout sidebar: document layers, then the page-layout commands."""
        panel = QWidget()
        ll = QVBoxLayout(panel)
        ll.setContentsMargins(8, 12, 8, 12)
        ll.setSpacing(8)
        heading = QLabel(self.tr('Layers'))
        heading.setObjectName('emptyHeading')
        ll.addWidget(heading)
        self.layer_list = QListWidget()
        self.layer_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.layer_list.setAccessibleName(self.tr('Document layers'))
        self.layer_stack = QStackedWidget()
        self.layer_stack.addWidget(self.layer_list)
        self.layer_stack.addWidget(
            EmptyPanel(
                self.tr('No document layers'),
                self.tr('Add a watermark or a layer from the Insert menu.'),
                graphic=themed_art('layers-empty'),
            )
        )
        ll.addWidget(self.layer_stack, 1)
        for label, slot in [
            (self.tr('Add layer…'), self.add_layer),
            (self.tr('Show / hide'), self.toggle_layer),
            (self.tr('Lock / unlock'), self.lock_layer),
            (self.tr('Layer inspector…'), self.layer_inspector),
        ]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            ll.addWidget(button)
        heading = QLabel(self.tr('Page layout'))
        heading.setObjectName('emptyHeading')
        ll.addWidget(heading)
        for label, slot in [
            (self.tr('Page setup…'), self.page_setup),
            (self.tr('Page masters…'), self.edit_page_masters),
            (self.tr('Section / columns…'), self.insert_section),
            (self.tr('Section profiles…'), self.edit_section_profiles),
        ]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            ll.addWidget(button)
        return panel

    def place_layout_panel(self, side):
        """One Layout panel serves both sidebars; it moves to the side that selects it."""
        slot = self.sidebar_panels[side]['layout']
        if self.layout_panel.parentWidget() is slot:
            return
        for panels in self.sidebar_panels.values():
            panels['layout-note'].setVisible(panels['layout'] is not slot)
        slot.layout().insertWidget(0, self.layout_panel, 1)
        self.layout_panel.show()

    def build_workspace_sidebars(self, outline_dock, inspector_dock):
        self.sidebar_panels = {}
        self.sidebar_selectors = {}
        self.sidebar_stacks = {}
        self.sidebar_docks = {'left': outline_dock, 'right': inspector_dock}
        for side, dock, original, title in (
            ('left', outline_dock, self.outline_stack, 'Outline'),
            ('right', inspector_dock, self.inspector, 'Inspector'),
        ):
            selector = QComboBox()
            selector.setAccessibleName(self.tr('{0} sidebar').format(side.title()))
            selector.setToolTip(self.tr('Choose the panel displayed in this sidebar'))
            stack = QStackedWidget()
            stack.addWidget(original)
            selector.addItem(self.tr(title), 'default')
            bookmarks = QTreeWidget()
            bookmarks.setHeaderHidden(True)
            bookmarks.setAccessibleName(self.tr('Bookmarks'))
            bookmarks.itemActivated.connect(
                lambda item, column: self.navigate_target(item.data(0, Qt.UserRole))
            )
            bookmarks.itemClicked.connect(
                lambda item, column: self.navigate_target(item.data(0, Qt.UserRole))
            )
            statistics = QLabel()
            statistics.setWordWrap(True)
            statistics.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            statistics.setMargin(12)
            statistics.setAccessibleName(self.tr('Writing statistics'))
            notes = QPlainTextEdit()
            notes.setAccessibleName(self.tr('Personal scratchpad'))
            notes.setPlaceholderText(
                self.tr('Personal notes for your workspace. Saved on this computer, outside the document.')
            )
            notes.setPlainText(self.settings.value('workspace/scratchpad', '', type=str))
            notes.textChanged.connect(lambda notes=notes: self.save_workspace_notes(notes))
            layout_slot = QWidget()
            slot_layout = QVBoxLayout(layout_slot)
            slot_layout.setContentsMargins(0, 0, 0, 0)
            elsewhere = QLabel(self.tr('Layout is open in the other sidebar.'))
            elsewhere.setObjectName('emptyDescription')
            elsewhere.setWordWrap(True)
            elsewhere.setAlignment(Qt.AlignCenter)
            elsewhere.setMargin(12)
            slot_layout.addWidget(elsewhere)
            for label, key, panel in (
                ('Layout', 'layout', layout_slot),
                ('Bookmarks', 'bookmarks', bookmarks),
                ('Writing statistics', 'statistics', statistics),
                ('Personal scratchpad', 'scratchpad', notes),
                ('Library', 'library', self.library_inspector_widget()),
            ):
                selector.addItem(self.tr(label), key)
                stack.addWidget(panel)
            self.sidebar_panels[side] = {
                'bookmarks': bookmarks,
                'statistics': statistics,
                'scratchpad': notes,
                'layout': layout_slot,
                'layout-note': elsewhere,
            }
            self.sidebar_selectors[side] = selector
            self.sidebar_stacks[side] = stack
            dock.setTitleBarWidget(SidebarTitle(dock, selector))
            dock.setWidget(stack)
            selector.currentIndexChanged.connect(
                lambda index, side=side: self.select_workspace_sidebar(side, index)
            )
            index = selector.findData(self.settings.value(f'workspace/{side}Sidebar', 'default'))
            selector.setCurrentIndex(max(index, 0))
        if self.layout_panel.parentWidget() is None:
            self.place_layout_panel('left')

    def select_workspace_sidebar(self, side, index):
        selector = self.sidebar_selectors[side]
        if selector.currentData() == 'layout':
            self.place_layout_panel(side)
        self.sidebar_stacks[side].setCurrentIndex(index)
        self.sidebar_docks[side].setWindowTitle(selector.currentText())
        self.settings.setValue(f'workspace/{side}Sidebar', selector.currentData())

    def save_workspace_notes(self, source):
        text = source.toPlainText()
        self.settings.setValue('workspace/scratchpad', text)
        for panels in self.sidebar_panels.values():
            notes = panels['scratchpad']
            if notes is not source and notes.toPlainText() != text:
                notes.blockSignals(True)
                notes.setPlainText(text)
                notes.blockSignals(False)

    def update_workspace_sidebars(self):
        if not hasattr(self, 'sidebar_panels'):
            return
        texts = []
        for block in self.doc['blocks']:
            texts.append(block.get('text', ''))
            texts.extend(cell.get('text', '') for row in block.get('rows', []) for cell in row['cells'])
        text = '\n'.join(texts)
        for panels in self.sidebar_panels.values():
            bookmarks = panels['bookmarks']
            bookmarks.clear()
            for name, bookmark in self.doc.get('settings', {}).get('bookmarks', {}).items():
                item = QTreeWidgetItem([name])
                item.setData(0, Qt.UserRole, bookmark.get('target'))
                bookmarks.addTopLevelItem(item)
            if not bookmarks.topLevelItemCount():
                item = QTreeWidgetItem([self.tr('Add bookmarks from the Insert menu.')])
                item.setFlags(Qt.NoItemFlags)
                bookmarks.addTopLevelItem(item)
            panels['statistics'].setText(
                self.tr('{0} words\n{1} characters\n{2} document blocks').format(
                    len(text.split()), len(text), len(self.doc['blocks'])
                )
            )
