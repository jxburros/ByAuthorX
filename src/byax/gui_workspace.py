"""Two-row writing ribbon and user-selectable workspace sidebars."""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QTabBar,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


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


class WorkspacePanels:
    def build_format_ribbon(self, toolbar, menus):
        toolbar.setObjectName('writingRibbon')
        toolbar.setStyleSheet('QToolBar#writingRibbon QToolButton { padding: 6px; }')
        menus['&Tools'].addAction(self.action(self.tr('Libraries…'), self.library_panel))
        cite = self.action(self.tr('Cite…'), self.cite_selection, 'Ctrl+Shift+C')
        menus['&Insert'].addAction(cite)
        toolbar.addAction(cite)
        self.addToolBarBreak()
        ribbon = QToolBar(self.tr('Formatting tools'), self)
        ribbon.setObjectName('formattingRibbon')
        ribbon.setMovable(False)
        ribbon.setStyleSheet('QToolBar#formattingRibbon QToolButton { padding: 6px; }')
        ribbon.setIconSize(QSize(20, 20))
        ribbon.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(ribbon)
        self.format_ribbon = ribbon
        self.ribbon_font = QFontComboBox()
        self.ribbon_font.setMaximumWidth(190)
        self.ribbon_font.setAccessibleName(self.tr('Paragraph font'))
        self.ribbon_font.setToolTip(self.tr('Set the font for selected paragraphs'))
        self.ribbon_font.activated.connect(
            lambda: self.attributes({'font_family': self.ribbon_font.currentFont().family()})
        )
        ribbon.addWidget(self.ribbon_font)
        self.ribbon_size = QDoubleSpinBox()
        self.ribbon_size.setRange(6, 144)
        self.ribbon_size.setSuffix(' pt')
        self.ribbon_size.setAccessibleName(self.tr('Paragraph font size'))
        self.ribbon_size.setKeyboardTracking(False)
        self.ribbon_size.editingFinished.connect(self.apply_ribbon_size)
        ribbon.addWidget(self.ribbon_size)
        groups = (
            (
                'Strikethrough',
                'Superscript',
                'Subscript',
                'Text color…',
                'Highlight…',
                'Clear text formatting',
            ),
            ('Align left', 'Align center', 'Align right', 'Justify'),
            ('Bulleted list', 'Numbered list', 'Decrease list level', 'Increase list level'),
            ('Hyperlink…', 'Font and paragraph…'),
        )
        actions = {action.text(): action for action in menus['F&ormat'].actions()}
        for group in groups:
            ribbon.addSeparator()
            for label in group:
                action = actions[self.tr(label)]
                ribbon.addAction(action)
                if action.icon().isNull():
                    button = ribbon.widgetForAction(action)
                    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
                    if label == 'Font and paragraph…':
                        button.setText(self.tr('Paragraph…'))
                    if label in ('Superscript', 'Subscript'):
                        button.setText('x²' if label == 'Superscript' else 'x₂')
        self.sync_ribbon_format()

    def apply_ribbon_size(self):
        if self.ribbon_size.value() != self._ribbon_size_value:
            self.attributes({'font_size': self.ribbon_size.value()})

    def sync_ribbon_format(self):
        if not hasattr(self, 'ribbon_font'):
            return
        block = next((b for b in self.doc['blocks'] if b['id'] == self.editor.current_id()), {})
        attrs = {**self.doc['styles'].get(block.get('style', 'body'), {}), **block.get('attrs', {})}
        self.ribbon_font.blockSignals(True)
        self.ribbon_font.setCurrentFont(QFont(attrs.get('font_family', 'Sans Serif')))
        self.ribbon_font.blockSignals(False)
        self.ribbon_size.blockSignals(True)
        self._ribbon_size_value = attrs.get('font_size', 11)
        self.ribbon_size.setValue(self._ribbon_size_value)
        self.ribbon_size.blockSignals(False)

    def build_workspace_sidebars(self, outline_dock, inspector_dock):
        self.sidebar_panels = {}
        self.sidebar_selectors = {}
        self.sidebar_stacks = {}
        self.sidebar_docks = {'left': outline_dock, 'right': inspector_dock}
        for side, dock, original, title in (
            ('left', outline_dock, self.outline_stack, 'Outline'),
            ('right', inspector_dock, self.inspector, 'Inspector'),
        ):
            wrapper = QWidget()
            layout = QVBoxLayout(wrapper)
            layout.setContentsMargins(4, 4, 4, 4)
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
            for label, key, panel in (
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
            }
            self.sidebar_selectors[side] = selector
            self.sidebar_stacks[side] = stack
            layout.addWidget(selector)
            layout.addWidget(stack)
            dock.setWidget(wrapper)
            selector.currentIndexChanged.connect(
                lambda index, side=side: self.select_workspace_sidebar(side, index)
            )
            index = selector.findData(self.settings.value(f'workspace/{side}Sidebar', 'default'))
            selector.setCurrentIndex(max(index, 0))

    def select_workspace_sidebar(self, side, index):
        selector = self.sidebar_selectors[side]
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
