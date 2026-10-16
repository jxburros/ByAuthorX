"""The ribbon: the logo beside two rows of tools, rebuilt for each file view.

Each file view has its own ribbon (``formats.RIBBONS``); items inside a group are filtered by what the
view supports, so a Markdown ribbon never offers underline. Long runs of related commands sit behind one
dropdown button, keeping each row short enough to sit beside the logo.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFontComboBox,
    QMenu,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .brand import BrandGraphic
from .formats import COMMAND_CAPABILITIES, FORMATS, RIBBONS, supports
from .gui_icons import themed_icon

EXPORT_FORMATS = ('pdf', 'docx', 'odt', 'epub', 'md', 'html', 'rtf', 'txt')
ALIGNMENTS = ('Align left', 'Align center', 'Align right', 'Justify')
# Commands a dropdown offers, by English label. Capabilities default to COMMAND_CAPABILITIES; a command
# with no entry there exists only in the full ByAuthorX view, and None means every view that shows the menu.
DROPDOWNS = {
    'text': (
        'Strikethrough',
        'Superscript',
        'Subscript',
        'Text color…',
        'Highlight…',
        'Apply character style…',
        'Clear text formatting',
    ),
    'align': ALIGNMENTS,
    'lists': (
        'Bulleted list',
        'Numbered list',
        'Increase list level',
        'Decrease list level',
        'Restart list numbering',
        'Continue list numbering',
        'Remove list',
    ),
    'insert': (
        'Table…',
        'Image…',
        'Page break',
        'Section / columns…',
        'Table of contents',
        'Footnote…',
        'Bookmark…',
        'Watermark / layer…',
        'Cross reference…',
        'Source reference…',
        'Template fields…',
    ),
    'versions': (
        'Checkpoint…',
        'Create branch…',
        'Revision history…',
        'Switch draft branch…',
        'Compare and merge draft…',
    ),
}
CAPABILITY_OVERRIDES = {
    'Clear text formatting': None,
    'Table…': 'blocks.table',
    **{label: 'block_attrs.alignment' for label in ALIGNMENTS},
}
# Single commands placed directly on a row: (English label, capability).
DIRECT = {
    'link': ('Hyperlink…', 'marks.link'),
    'paragraph': ('Font and paragraph…', 'marks.font_family'),
    'track': ('Track changes', 'full'),
    'protect': ('Protect selected paragraph', 'full'),
    'find': ('Find and replace…', None),
}
LABELLED = {'comment', 'check', 'pages', 'text', 'insert', 'export'}


def walk_menu(menu):
    """Every command in a menu and its submenus."""
    for action in menu.actions():
        if action.menu():
            yield from walk_menu(action.menu())
        elif not action.isSeparator():
            yield action


class RibbonPanels:
    def find_action(self, label):
        text = self.tr(label)
        for menu in self._menus.values():
            for action in walk_menu(menu):
                if action.text() == text:
                    return action
        raise KeyError(label)

    def build_ribbon(self, menus):
        self._menus = menus
        menus['&Tools'].addAction(self.action(self.tr('Libraries…'), self.library_panel))
        cite = self.action(self.tr('Cite…'), self.cite_selection, 'Ctrl+Shift+C', icon='cite')
        self.submenus['references'].addAction(cite)
        self.ribbon_actions['cite'] = cite
        self.ribbon_font = QFontComboBox()
        self.ribbon_font.setMaximumWidth(170)
        self.ribbon_font.setAccessibleName(self.tr('Paragraph font'))
        self.ribbon_font.setToolTip(self.tr('Set the font for selected paragraphs'))
        self.ribbon_font.activated.connect(
            lambda: self.attributes({'font_family': self.ribbon_font.currentFont().family()})
        )
        self.ribbon_size = QDoubleSpinBox()
        self.ribbon_size.setRange(6, 144)
        self.ribbon_size.setDecimals(1)
        self.ribbon_size.setSuffix(' pt')
        self.ribbon_size.setAccessibleName(self.tr('Paragraph font size'))
        self.ribbon_size.setToolTip(self.tr('Set the font size for selected paragraphs'))
        self.ribbon_size.setKeyboardTracking(False)
        self.ribbon_size.editingFinished.connect(self.apply_ribbon_size)
        self.ribbon_widgets = {}
        for key, widget in (
            ('style', self.style_box),
            ('font', self.ribbon_font),
            ('size', self.ribbon_size),
            ('view', self.file_view_box),
            ('source', self.source_mode),
        ):
            holder = QWidgetAction(self)
            holder.setDefaultWidget(widget)
            self.ribbon_widgets[key] = holder
        self.ribbon_actions['source-view'] = self.find_action('Markdown source…')
        for key, title, icon, description in (
            ('text', 'Text', 'font', 'More text formatting: strikethrough, scripts, color and highlight'),
            ('align', 'Alignment', 'align-left', 'Paragraph alignment'),
            ('lists', 'Lists', 'bullet-list', 'Bulleted and numbered lists and list levels'),
            ('insert', 'Insert', 'insert', 'Insert a table, image, break, note or reference'),
            ('versions', 'Versions', 'history', 'Checkpoints, alternate drafts and revision history'),
        ):
            self.ribbon_actions[key] = self.ribbon_dropdown(title, icon, description)
        self.ribbon_actions['export'] = self.ribbon_dropdown(
            'Export', 'export', 'Export a copy in another format', self.export_current
        )
        for ident in EXPORT_FORMATS:
            action = self.action(
                self.tr('{0}…').format(FORMATS[ident]['label']),
                lambda checked=False, f=ident: self.export(f),
                description=self.tr('Export a copy as {0}').format(FORMATS[ident]['label']),
            )
            action.setData(ident)
            self.ribbon_actions['export'].menu().addAction(action)
            self.export_as_menu.addAction(action)

        bar = QToolBar(self.tr('Ribbon'), self)
        bar.setObjectName('ribbon')
        bar.setMovable(False)
        bar.setFloatable(False)
        self.addToolBar(bar)
        self.ribbon = bar
        rows = QWidget()
        rows.setObjectName('ribbonRows')
        stack = QVBoxLayout(rows)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(2)
        self.ribbon_rows = []
        for name, title in (('writingRibbon', 'Writing tools'), ('formattingRibbon', 'Formatting tools')):
            row = QToolBar(self.tr(title), rows)
            row.setObjectName(name)
            row.setMovable(False)
            row.setFloatable(False)
            row.setIconSize(QSize(20, 20))
            row.setToolButtonStyle(Qt.ToolButtonIconOnly)
            stack.addWidget(row)
            self.ribbon_rows.append(row)
        self.populate_ribbon('byax')
        # Rows keep the height of the fullest ribbon, so the logo beside them never jumps between views.
        for row in self.ribbon_rows:
            row.setFixedHeight(row.sizeHint().height())
        height = sum(row.height() for row in self.ribbon_rows) + stack.spacing()
        self.brand_mark = BrandGraphic('wordmark-light.png', None, clear_space=14, height=height)
        self.brand_mark.setAccessibleName(self.tr('ByAuthorX'))
        bar.addWidget(self.brand_mark)
        bar.addSeparator()
        bar.addWidget(rows)
        self.sync_ribbon_format()

    def ribbon_dropdown(self, title, icon, description, slot=None):
        action = self.action(
            self.tr(title), slot or (lambda: None), description=self.tr(description), icon=icon
        )
        action.setMenu(QMenu(self.tr(title), self))
        return action

    def ribbon_command(self, label, capability, view):
        if capability is not None and not supports(view, capability):
            return None
        return self.find_action(label)

    def ribbon_item(self, key, view):
        """The actions ribbon item ``key`` places on a row for ``view``; empty when it has none there."""
        entry = FORMATS[view]
        if key in ('style', 'font', 'size', 'view'):
            return [self.ribbon_widgets[key]]
        if key == 'source':
            return [self.ribbon_widgets['source'] if entry['source'] else self.ribbon_actions['source-view']]
        if key == 'marks':
            return [
                self.ribbon_actions[m]
                for m in ('bold', 'italic', 'underline')
                if supports(view, f'marks.{m}')
            ]
        if key in ('comment', 'check'):
            return [self.ribbon_actions[key]] if key == 'check' or 'comments' in entry['review'] else []
        if key == 'pages':
            return [self.ribbon_actions[key]] if entry['paged'] else []
        if key == 'cite':
            return [self.ribbon_actions['cite']]
        if key in DIRECT:
            action = self.ribbon_command(*DIRECT[key], view)
            return [action] if action else []
        if key == 'export':
            self.sync_export_action(view)
            return [self.ribbon_actions['export']]
        dropdown = self.ribbon_actions[key]
        menu = dropdown.menu()
        # removeAction, not clear(): clear() may delete a shared command the menu bar still shows.
        for action in menu.actions():
            menu.removeAction(action)
        for label in DROPDOWNS[key]:
            capability = CAPABILITY_OVERRIDES.get(label, COMMAND_CAPABILITIES.get(label, 'full'))
            action = self.ribbon_command(label, capability, view)
            if action is not None:
                menu.addAction(action)
        return [dropdown] if menu.actions() else []

    def populate_ribbon(self, view):
        if getattr(self, '_ribbon_view', None) == view:
            return
        self._ribbon_view = view
        for row, groups in zip(self.ribbon_rows, RIBBONS[view]):
            for action in row.actions():
                row.removeAction(action)
                if action.isSeparator():
                    action.deleteLater()
            first = True
            for group in groups:
                actions = [action for key in group for action in self.ribbon_item(key, view)]
                if not actions:
                    continue
                if not first:
                    row.addSeparator()
                first = False
                for action in actions:
                    row.addAction(action)
                    self.style_ribbon_button(row, action)

    def style_ribbon_button(self, row, action):
        button = row.widgetForAction(action)
        if not isinstance(button, QToolButton) or isinstance(action, QWidgetAction):
            return
        key = next((k for k, a in self.ribbon_actions.items() if a is action), None)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if key in LABELLED else Qt.ToolButtonIconOnly)
        if action.menu():
            split = key == 'export' and self._ribbon_view != 'byax'
            button.setPopupMode(QToolButton.MenuButtonPopup if split else QToolButton.InstantPopup)
            button.setAccessibleName(action.toolTip())

    def sync_export_action(self, view):
        """ByAuthorX asks for a format; any other view exports its own format, with the others one click away."""
        action = self.ribbon_actions['export']
        menu = action.menu()
        current = next((a for a in menu.actions() if a.data() == view), None)
        menu.setDefaultAction(current)
        if current is None:
            action.setToolTip(self.tr('Export: choose a format'))
        else:
            label = FORMATS[view]['label']
            action.setToolTip(self.tr('Export as {0}; choose another format from the arrow').format(label))
        action.setStatusTip(action.toolTip())

    def export_current(self):
        if self.file_view in EXPORT_FORMATS:
            self.export(self.file_view)
        else:
            self.export()

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
        # The alignment dropdown wears the current paragraph's alignment.
        alignment = attrs.get('alignment', 'left')
        icon = 'justify' if alignment == 'justify' else f'align-{alignment}'
        align = self.ribbon_actions['align']
        if align.property('byaxIcon') != icon and alignment in ('left', 'center', 'right', 'justify'):
            align.setProperty('byaxIcon', icon)
            align.setIcon(themed_icon(icon, self))
