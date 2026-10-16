# ruff: noqa: BLE001
"""The Binder panel: outline and corkboard views of a manuscript project (CP A2).

Both views show the same binder (``byax.project``) and change it only through canonical ``project_*``
operations sent through the window's ``commit``, so every edit is one revision with the usual undo,
history, comment anchors and tracked changes. Reordering has three equivalent routes: dragging,
Alt+Up/Down (earlier or later among siblings, crossing into the neighbouring chapter at the ends) with
Alt+Left/Right (outdent or indent), and the Binder menu, panel menu and context menu. Dragging is a
convenience; nothing can be done only by dragging.

Synopses are authored text or empty. "Draft a synopsis" is an opt-in request to a provider the author
has configured and chosen; its answer is only ever a pending proposal.
"""

from PySide6.QtCore import QEvent, QMimeData, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QDrag, QFont, QFontMetrics, QKeySequence, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import goals, project
from .brand import COLORS, DARK_COLORS
from .errors import ByaxError
from .gui_a11y import describe_action
from .gui_art import EmptyPanel, themed_art

MIME = 'application/x-byax-binder-item'
ROLE_ID = Qt.UserRole
ROLE_HEADING = Qt.UserRole + 1
ROLE_SYNOPSIS = Qt.UserRole + 2
ROLE_ROW = Qt.UserRole + 3
CARD_SIZE = QSize(236, 164)
SYNOPSIS_LINES = 4
LEAVES = ''  # corkboard scope: every item that contains no other item
STATUS_LABELS = {'to_do': 'To do', 'draft': 'Draft', 'revised': 'Revised', 'final': 'Final'}
KIND_LABELS = {
    'part': 'Part',
    'chapter': 'Chapter',
    'scene': 'Scene',
    'note': 'Note',
    'front': 'Front matter',
    'back': 'Back matter',
}


def status_label(status):
    return STATUS_LABELS.get(status, status.replace('_', ' ').capitalize())


def theme_colors(widget):
    return DARK_COLORS if widget.palette().color(QPalette.Window).lightness() < 128 else COLORS


def count_text(row):
    """Words, with the target when the item has one."""
    words = row['total_words']
    if 'target_words' in row:
        return f'{words:,} / {row["target_words"]:,}'
    return f'{words:,}'


def describe_row(row):
    """The sentence a screen reader speaks for a binder item."""
    parts = [
        f'{row["title"] or "Untitled"}, {KIND_LABELS.get(row["kind"], row["kind"]).lower()}',
        f'status {status_label(row["status"]).lower()}',
        f'{row["total_words"]:,} words' + (f' of {row["target_words"]:,}' if 'target_words' in row else ''),
    ]
    if not row['include_in_compile']:
        parts.append('left out of the compiled manuscript')
    text = ', '.join(parts) + '.'
    if row.get('synopsis'):
        text += ' Synopsis: ' + row['synopsis']
    return text


class SynopsisEditor(QPlainTextEdit):
    """Plain synopsis text: Ctrl+Enter or leaving the field saves, Escape restores the saved text.

    A ``transient`` editor (the in-place one on a card) finishes once, so Escape followed by the focus
    change that closes it never saves what was typed.
    """

    committed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None, transient=False):
        super().__init__(parent)
        self.transient = transient
        self._done = False

    def _finish(self, signal, *arguments):
        if self.transient:
            if self._done:
                return
            self._done = True
        signal.emit(*arguments)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self._finish(self.committed, self.toPlainText())
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self._finish(self.cancelled)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if event.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
            self._finish(self.committed, self.toPlainText())


class ReorderMixin:
    """Drag one binder item onto another; every drop becomes a request, never a direct edit."""

    moveRequested = Signal(str, str, str)  # source, target, before | after | into
    stepRequested = Signal(str)  # up | down | out | in

    def _init_reorder(self):
        self._marker = None
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDropIndicatorShown(False)

    def item_id_at(self, point):
        raise NotImplementedError

    def drop_position(self, rect, point):
        raise NotImplementedError

    def startDrag(self, supported):
        identity = self.current_item_id()
        if not identity:
            return
        mime = QMimeData()
        mime.setData(MIME, identity.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)
        self._marker = None
        self.viewport().update()

    def _wanted(self, event):
        return event.mimeData().hasFormat(MIME)

    def dragEnterEvent(self, event):
        if self._wanted(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not self._wanted(event):
            event.ignore()
            return
        event.acceptProposedAction()
        point = event.position().toPoint()
        identity = self.item_id_at(point)
        marker = None
        if identity:
            rect = self.rect_of(identity)
            marker = (rect, self.drop_position(rect, point))
        if marker != self._marker:
            self._marker = marker
            self.viewport().update()

    def dragLeaveEvent(self, event):
        self._marker = None
        self.viewport().update()
        event.accept()

    def dropEvent(self, event):
        # The document is the source of truth: the view is rebuilt from it after the move.
        if not self._wanted(event):
            event.ignore()
            return
        point = event.position().toPoint()
        source = bytes(event.mimeData().data(MIME)).decode()
        target = self.item_id_at(point)
        self._marker = None
        self.viewport().update()
        event.setDropAction(Qt.IgnoreAction)
        event.accept()
        if target:
            self.moveRequested.emit(source, target, self.drop_position(self.rect_of(target), point))

    def paint_marker(self):
        if not self._marker:
            return
        rect, where = self._marker
        painter = QPainter(self.viewport())
        pen = QPen(QColor(theme_colors(self)['greenText']), 3)
        painter.setPen(pen)
        if where == 'into':
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        elif isinstance(self, QTreeWidget):
            y = rect.top() if where == 'before' else rect.bottom()
            painter.drawLine(rect.left(), y, rect.right(), y)
        else:
            x = rect.left() - 3 if where == 'before' else rect.right() + 3
            painter.drawLine(x, rect.top(), x, rect.bottom())
        painter.end()

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.AltModifier and not event.modifiers() & (
            Qt.ControlModifier | Qt.ShiftModifier
        ):
            step = {Qt.Key_Up: 'up', Qt.Key_Down: 'down', Qt.Key_Left: 'out', Qt.Key_Right: 'in'}.get(
                event.key()
            )
            if step:
                self.stepRequested.emit(step)
                event.accept()
                return
        super().keyPressEvent(event)


class OutlineDelegate(QStyledItemDelegate):
    """Two lines per row so the outline fits a narrow sidebar: the title, then kind, status and words."""

    ROW_HEIGHT = 52

    def sizeHint(self, option, index):
        return QSize(160, self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        row = index.data(ROLE_ROW)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        shell = QStyleOptionViewItem(option)
        self.initStyleOption(shell, index)
        shell.text = ''
        style.drawControl(QStyle.CE_ItemViewItem, shell, painter, widget)
        colors = theme_colors(widget)
        selected = bool(option.state & QStyle.State_Selected)
        ink = QColor('#FFFFFF' if selected else colors['ink'])
        muted = QColor('#FFFFFF' if selected else colors['muted'])
        text_rect = option.rect.adjusted(6, 4, -6, -4)
        painter.save()
        title_font = QFont(option.font)
        title_font.setBold(row['kind'] in ('part', 'chapter'))
        painter.setFont(title_font)
        painter.setPen(ink)
        metrics = QFontMetrics(title_font)
        painter.drawText(
            QRect(text_rect.left(), text_rect.top(), text_rect.width(), metrics.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(row['title'] or 'Untitled', Qt.ElideRight, text_rect.width()),
        )
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.85))
        small_metrics = QFontMetrics(small)
        painter.setFont(small)
        painter.setPen(muted)
        meta = f'{KIND_LABELS.get(row["kind"], row["kind"])} · {status_label(row["status"])} · {count_text(row)} words'
        painter.drawText(
            QRect(
                text_rect.left(),
                text_rect.top() + metrics.height() + 1,
                text_rect.width(),
                small_metrics.height(),
            ),
            Qt.AlignLeft | Qt.AlignVCenter,
            small_metrics.elidedText(meta, Qt.ElideRight, text_rect.width()),
        )
        if 'target_words' in row:
            bar = QRect(text_rect.left(), option.rect.bottom() - 5, text_rect.width(), 2)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colors['rule']))
            painter.drawRect(bar)
            painter.setBrush(QColor(colors['green']))
            share = min(1.0, row['total_words'] / row['target_words'])
            painter.drawRect(QRect(bar.left(), bar.top(), int(bar.width() * share), bar.height()))
        painter.restore()


class OutlineTree(ReorderMixin, QTreeWidget):
    """Indented outline: title, kind, status and words with progress toward the target."""

    activatedItem = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('binderOutline')
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setIndentation(16)
        self.setItemDelegate(OutlineDelegate(self))
        self._init_reorder()
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def current_item_id(self):
        item = self.currentItem()
        return item.data(0, ROLE_ID) if item else None

    def item_id_at(self, point):
        item = self.itemAt(point)
        return item.data(0, ROLE_ID) if item else None

    def rect_of(self, identity):
        item = self.find(identity)
        return self.visualItemRect(item) if item else QRect()

    def drop_position(self, rect, point):
        quarter = max(1, rect.height() // 4)
        if point.y() < rect.top() + quarter:
            return 'before'
        return 'after' if point.y() > rect.bottom() - quarter else 'into'

    def items(self):
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def find(self, identity):
        return next((item for item in self.items() if item.data(0, ROLE_ID) == identity), None)

    def expansion(self):
        """Which containers are open, by item identity."""
        return {item.data(0, ROLE_ID): item.isExpanded() for item in self.items() if item.childCount()}

    def paintEvent(self, event):
        super().paintEvent(event)
        self.paint_marker()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
            identity = self.current_item_id()
            if identity:
                self.activatedItem.emit(identity)
                event.accept()
                return
        super().keyPressEvent(event)


class CardDelegate(QStyledItemDelegate):
    """Draws one scene card and edits its synopsis in place."""

    synopsisEdited = Signal(str, str)

    def sizeHint(self, option, index):
        return option.widget.card_size() if option.widget is not None else CARD_SIZE

    def paint(self, painter, option, index):
        row = index.data(ROLE_ROW)
        colors = theme_colors(option.widget)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        card = QRectF(option.rect).adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.State_Selected)
        focused = bool(option.state & QStyle.State_HasFocus)
        painter.setBrush(QColor(colors['surface']))
        painter.setPen(
            QPen(
                QColor(colors['greenText'] if selected or focused else colors['rule']),
                2 if selected or focused else 1,
            )
        )
        painter.drawRoundedRect(card, 8, 8)
        inner = card.adjusted(10, 8, -10, -8).toRect()
        ink, muted = QColor(colors['ink']), QColor(colors['muted'])
        title_font = QFont(option.font)
        title_font.setBold(True)
        title_font.setPointSizeF(option.font.pointSizeF() * 1.05)
        painter.setFont(title_font)
        painter.setPen(ink)
        metrics = QFontMetrics(title_font)
        title = row['title'] or 'Untitled'
        painter.drawText(
            QRect(inner.left(), inner.top(), inner.width(), metrics.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(title, Qt.ElideRight, inner.width()),
        )
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.85))
        small_metrics = QFontMetrics(small)
        painter.setFont(small)
        painter.setPen(muted)
        meta_top = inner.top() + metrics.height() + 2
        meta = f'{KIND_LABELS.get(row["kind"], row["kind"])} · {status_label(row["status"])}'
        if row['children']:
            meta += f' · {len(row["children"])} inside'
        painter.drawText(
            QRect(inner.left(), meta_top, inner.width(), small_metrics.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            small_metrics.elidedText(meta, Qt.ElideRight, inner.width()),
        )
        footer_height = small_metrics.height() + 8
        body = QRect(
            inner.left(),
            meta_top + small_metrics.height() + 6,
            inner.width(),
            inner.bottom() - footer_height - (meta_top + small_metrics.height() + 6),
        )
        painter.setFont(option.font)
        text = row.get('synopsis') or ''
        if text:
            painter.setPen(ink)
            painter.drawText(body, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text)
        else:
            painter.setPen(muted)
            painter.drawText(
                body, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, 'No synopsis yet. Press F2 to write one.'
            )
        painter.setFont(small)
        painter.setPen(muted)
        painter.drawText(
            QRect(
                inner.left(), inner.bottom() - small_metrics.height(), inner.width(), small_metrics.height()
            ),
            Qt.AlignLeft | Qt.AlignVCenter,
            count_text(row) + ' words',
        )
        if 'target_words' in row:
            bar = QRect(inner.left(), inner.bottom() - footer_height + 2, inner.width(), 3)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colors['rule']))
            painter.drawRoundedRect(bar, 1.5, 1.5)
            painter.setBrush(QColor(colors['green']))
            share = min(1.0, row['total_words'] / row['target_words'])
            painter.drawRoundedRect(
                QRect(bar.left(), bar.top(), int(bar.width() * share), bar.height()), 1.5, 1.5
            )
        painter.restore()

    def createEditor(self, parent, option, index):
        editor = SynopsisEditor(parent, transient=True)
        editor.setAccessibleName('Synopsis of ' + (index.data(ROLE_ROW)['title'] or 'Untitled'))
        editor.setToolTip('Synopsis. Ctrl+Enter saves, Escape cancels.')
        editor.committed.connect(lambda text, e=editor, i=index: self._commit(e, i, text))
        editor.cancelled.connect(
            lambda e=editor: self.closeEditor.emit(e, QStyledItemDelegate.RevertModelCache)
        )
        return editor

    def eventFilter(self, editor, event):
        # The delegate closes the editor on Escape before the editor sees the key; note that the
        # focus change this causes is a cancel, not a commit.
        if (
            isinstance(editor, SynopsisEditor)
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Escape
        ):
            editor._done = True
        return super().eventFilter(editor, event)

    def _commit(self, editor, index, text):
        row = index.data(ROLE_ROW)
        if editor.hasFocus():  # saved with the keyboard; losing focus has already closed the editor
            self.closeEditor.emit(editor, QStyledItemDelegate.NoHint)
        if row and text != row.get('synopsis', ''):
            self.synopsisEdited.emit(row['id'], text)

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(ROLE_ROW).get('synopsis', ''))
        editor.selectAll()

    def setModelData(self, editor, model, index):
        # The synopsis is saved through the document; the view is rebuilt from it afterwards.
        return

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(QRectF(option.rect).adjusted(7, 34, -7, -26).toRect())


class Corkboard(ReorderMixin, QListWidget):
    """Cards for the items inside one scope; drag a card before or after another."""

    activatedItem = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('binderCorkboard')
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditKeyPressed)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._card_size = CARD_SIZE
        self.setItemDelegate(CardDelegate(self))
        self._init_reorder()
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    @property
    def delegate(self):
        return self.itemDelegate()

    def current_item_id(self):
        item = self.currentItem()
        return item.data(ROLE_ID) if item else None

    def item_id_at(self, point):
        item = self.itemAt(point)
        return item.data(ROLE_ID) if item else None

    def find(self, identity):
        return next(
            (self.item(i) for i in range(self.count()) if self.item(i).data(ROLE_ID) == identity), None
        )

    def rect_of(self, identity):
        item = self.find(identity)
        return self.visualItemRect(item) if item else QRect()

    def drop_position(self, rect, point):
        return 'before' if point.x() < rect.center().x() else 'after'

    def card_size(self):
        """Cards share the width of the viewport in whole columns, so none is cut off."""
        available = max(0, self.viewport().width() - 4)
        columns = max(1, available // 220)
        width = available // columns - 2 * self.spacing() - 1
        return QSize(max(170, min(320, width)), CARD_SIZE.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        size = self.card_size()
        if size != self._card_size:
            self._card_size = size
            for index in range(self.count()):
                self.item(index).setSizeHint(size)
            self.doItemsLayout()

    def paintEvent(self, event):
        super().paintEvent(event)
        self.paint_marker()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
            identity = self.current_item_id()
            if identity:
                self.activatedItem.emit(identity)
                event.accept()
                return
        super().keyPressEvent(event)


class BinderPanel(QWidget):
    """Outline and corkboard over one binder, with the actions that edit it."""

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.setObjectName('binderPanel')
        self.rows = {}
        self.order = []
        self._key = None
        self._stale = True
        self.view = host.settings.value('binder/view', 'outline')
        if self.view not in ('outline', 'corkboard'):
            self.view = 'outline'
        self.scope = LEAVES
        self._build()
        self.update_actions()

    # Construction

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.outline_button = QPushButton(self.tr('Outline'))
        self.board_button = QPushButton(self.tr('Corkboard'))
        for button, name, tip in (
            (self.outline_button, self.tr('Outline view'), self.tr('Show the binder as an indented outline')),
            (self.board_button, self.tr('Corkboard view'), self.tr('Show the binder as cards with synopses')),
        ):
            button.setCheckable(True)
            button.setAccessibleName(name)
            button.setToolTip(tip)
            top.addWidget(button)
        self.outline_button.clicked.connect(lambda: self.set_view('outline'))
        self.board_button.clicked.connect(lambda: self.set_view('corkboard'))
        top.addStretch(1)
        self.menu_button = QToolButton()
        self.menu_button.setText(self.tr('Binder actions'))
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu_button.setAccessibleName(self.tr('Binder actions'))
        self.menu_button.setToolTip(self.tr('Move, split, merge and annotate the selected binder item'))
        self.menu_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        top.addWidget(self.menu_button)
        layout.addLayout(top)
        self.scope_box = QComboBox()
        self.scope_box.setAccessibleName(self.tr('Corkboard scope'))
        self.scope_box.setToolTip(self.tr('Choose which items the corkboard shows'))
        self.scope_box.activated.connect(self._scope_chosen)
        layout.addWidget(self.scope_box)
        self.summary = QLabel()
        self.summary.setObjectName('binderSummary')
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName(self.tr('Manuscript word count and target'))
        layout.addWidget(self.summary)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        self.progress.setAccessibleName(self.tr('Manuscript progress toward its word target'))
        self.progress.setToolTip(self.tr('Manuscript progress toward its word target'))
        layout.addWidget(self.progress)
        self.stack = QStackedWidget()
        self.empty = EmptyPanel(
            self.tr('No binder yet'),
            self.tr(
                'A binder lists the parts, chapters and scenes built from your headings. Creating one '
                'does not change your text.'
            ),
            graphic=themed_art('outline-empty'),
            action=(self.tr('Create binder'), self.create_binder),
        )
        self.no_items = EmptyPanel(
            self.tr('The binder is empty'),
            self.tr('Add a heading to the document, or add a chapter here.'),
            graphic=themed_art('outline-empty'),
            action=(self.tr('Add chapter'), lambda: self.add_item('chapter', None)),
        )
        self.outline = OutlineTree()
        self.outline.setAccessibleName(self.tr('Binder outline'))
        self.outline.setToolTip(
            self.tr(
                'Binder outline. Alt+Up and Alt+Down move the selected item, Alt+Left and Alt+Right change its level.'
            )
        )
        self.board = Corkboard()
        self.board.setAccessibleName(self.tr('Binder corkboard'))
        self.board.setToolTip(
            self.tr(
                'Corkboard. Alt+arrow keys move the selected card, F2 edits its synopsis, Enter shows it in the editor.'
            )
        )
        for widget in (self.empty, self.no_items, self.outline, self.board):
            self.stack.addWidget(widget)
        layout.addWidget(self.stack, 1)
        self.orphans = QLabel()
        self.orphans.setWordWrap(True)
        self.orphans.setObjectName('emptyDescription')
        self.orphans.hide()
        layout.addWidget(self.orphans)
        heading = QLabel(self.tr('Synopsis'))
        heading.setObjectName('binderSynopsisLabel')
        layout.addWidget(heading)
        self.synopsis = SynopsisEditor()
        self.synopsis.setMaximumHeight(96)
        self.synopsis.setAccessibleName(self.tr('Synopsis of the selected item'))
        self.synopsis.setToolTip(
            self.tr(
                'Synopsis of the selected item. Ctrl+Enter or leaving the field saves, Escape restores it.'
            )
        )
        self.synopsis.setPlaceholderText(self.tr('Write a synopsis, or leave it empty.'))
        self.synopsis.committed.connect(self._synopsis_committed)
        self.synopsis.cancelled.connect(self._load_synopsis)
        layout.addWidget(self.synopsis)
        heading.setBuddy(self.synopsis)
        self._make_actions()
        self.menu_button.setMenu(self.actions_menu(self))
        self.menu_button.menu().aboutToShow.connect(self.update_actions)
        for view in (self.outline, self.board):
            view.moveRequested.connect(self.drop)
            view.stepRequested.connect(self.step)
            view.customContextMenuRequested.connect(lambda point, v=view: self._context_menu(v, point))
            view.activatedItem.connect(self.go_to)
        self.outline.itemSelectionChanged.connect(lambda: self._selection_changed(self.outline))
        self.board.itemSelectionChanged.connect(lambda: self._selection_changed(self.board))
        self.outline.itemClicked.connect(lambda item, column: self.go_to(item.data(0, ROLE_ID)))
        self.board.itemClicked.connect(lambda item: self.go_to(item.data(ROLE_ID)))
        self.board.itemDoubleClicked.connect(lambda item: self.go_to(item.data(ROLE_ID)))
        self.board.delegate.synopsisEdited.connect(self.set_synopsis)

    def _action(self, text, slot, shortcut=None, description=None):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        action.triggered.connect(lambda checked=False: slot())
        describe_action(action, description)
        self.addAction(action)
        return action

    def _make_actions(self):
        t = self.tr
        self.act = {}
        for key, text, slot, shortcut, description in (
            (
                'up',
                t('Move earlier'),
                lambda: self.step('up'),
                'Alt+Up',
                t('Move the selected item before the previous item in its chapter or part'),
            ),
            (
                'down',
                t('Move later'),
                lambda: self.step('down'),
                'Alt+Down',
                t('Move the selected item after the next item in its chapter or part'),
            ),
            (
                'out',
                t('Outdent'),
                lambda: self.step('out'),
                'Alt+Left',
                t('Move the selected item out of its chapter or part, after it'),
            ),
            (
                'in',
                t('Indent'),
                lambda: self.step('in'),
                'Alt+Right',
                t('Move the selected item inside the item before it'),
            ),
            (
                'go',
                t('Show in editor'),
                self.go_to_current,
                None,
                t('Move the editor to the selected heading'),
            ),
            (
                'edit',
                t('Edit synopsis'),
                self.edit_synopsis,
                'F2',
                t('Edit the synopsis of the selected item'),
            ),
            (
                'draft',
                t('Draft a synopsis…'),
                self.draft_synopsis,
                None,
                t('Ask a configured provider for a synopsis. The answer is a proposal you review.'),
            ),
            (
                'split',
                t('Split at editor cursor…'),
                self.split_at_cursor,
                None,
                t('Split the selected item into two at the cursor position in the editor'),
            ),
            (
                'merge',
                t('Merge with next item'),
                self.merge_next,
                None,
                t('Join the selected item and the item that follows it'),
            ),
            (
                'target',
                t('Word target…'),
                self.set_target,
                None,
                t('Set or clear the word target of the selected item'),
            ),
            (
                'scene',
                t('Add scene after…'),
                lambda: self.add_item('scene', 'after'),
                None,
                t('Add a new scene after the selected item'),
            ),
            (
                'chapter',
                t('Add chapter after…'),
                lambda: self.add_item('chapter', 'after'),
                None,
                t('Add a new chapter after the selected item'),
            ),
            (
                'inside',
                t('Open in corkboard'),
                self.open_in_corkboard,
                None,
                t('Show the items inside the selected item on the corkboard'),
            ),
            (
                'create',
                t('Create binder'),
                self.create_binder,
                None,
                t('Bind the headings of this document into a manuscript binder'),
            ),
        ):
            self.act[key] = self._action(text, slot, shortcut, description)
        self.status_actions = []
        for status in ('to_do', 'draft', 'revised', 'final'):
            action = self._action(
                self.tr(STATUS_LABELS[status]),
                lambda s=status: self.set_status(s),
                description=self.tr('Set the status of the selected item to {0}').format(
                    self.tr(STATUS_LABELS[status])
                ),
            )
            self.status_actions.append((status, action))
        self.act['status_other'] = self._action(
            t('Other status…'), self.set_status_other, None, t('Set a custom status for the selected item')
        )

    def actions_menu(self, parent=None):
        """The item actions as one menu; used by the panel button, context menus and the Binder menu."""
        menu = QMenu(parent or self)
        a = self.act
        for key in ('go', 'edit', 'draft'):
            menu.addAction(a[key])
        menu.addSeparator()
        for key in ('up', 'down', 'out', 'in'):
            menu.addAction(a[key])
        menu.addSeparator()
        for key in ('split', 'merge'):
            menu.addAction(a[key])
        status = menu.addMenu(self.tr('Status'))
        for _, action in self.status_actions:
            status.addAction(action)
        status.addAction(a['status_other'])
        menu.addAction(a['target'])
        menu.addSeparator()
        for key in ('scene', 'chapter', 'inside'):
            menu.addAction(a[key])
        return menu

    # Reading the document

    @property
    def doc(self):
        return self.host.doc

    def sync(self):
        """Rebuild after the document changed, but only while the panel is on screen."""
        if self.isVisible():
            self.refresh()
        else:
            self._stale = True

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale:
            self.refresh()

    def current_id(self):
        view = self.outline if self.view == 'outline' else self.board
        return view.current_item_id()

    def refresh(self, force=False):
        doc = self.doc
        key = (doc['revision'], self.view, self.scope, id(doc))
        if key == self._key and not force:
            return
        self._stale = False
        remembered = self.current_id()
        expanded = self.outline.expansion()
        rows = project.items(doc) if 'project' in doc else []
        self.order = [row['id'] for row in rows]
        self.rows = {row['id']: row for row in rows}
        if self.scope and self.scope not in self.rows:
            self.scope = LEAVES
        self._key = key
        self._fill_outline(rows, expanded)
        self._fill_scopes(rows)
        self._fill_board(rows)
        self._fill_header(doc)
        self._show_page()
        self.select(remembered if remembered in self.rows else None, notify=False)
        self.update_actions()
        self._load_synopsis()

    def _show_page(self):
        if 'project' not in self.doc:
            self.stack.setCurrentWidget(self.empty)
        elif not self.rows:
            self.stack.setCurrentWidget(self.no_items)
        else:
            self.stack.setCurrentWidget(self.outline if self.view == 'outline' else self.board)
        self.outline_button.setChecked(self.view == 'outline')
        self.board_button.setChecked(self.view == 'corkboard')
        self.scope_box.setVisible(self.view == 'corkboard' and bool(self.rows))

    def _fill_outline(self, rows, expanded):
        tree = self.outline
        tree.setUpdatesEnabled(False)
        tree.blockSignals(True)
        tree.clear()
        nodes = {}
        for row in rows:
            item = QTreeWidgetItem([row['title'] or self.tr('Untitled')])
            item.setData(0, ROLE_ID, row['id'])
            item.setData(0, ROLE_HEADING, row['heading'])
            item.setData(0, ROLE_ROW, row)
            item.setData(0, Qt.AccessibleTextRole, describe_row(row))
            item.setToolTip(0, describe_row(row))
            nodes[row['id']] = item
            if row['parent'] is None:
                tree.addTopLevelItem(item)
            else:
                nodes[row['parent']].addChild(item)
        # Containers start open; one the author collapsed stays collapsed.
        for identity, item in nodes.items():
            if item.childCount():
                item.setExpanded(expanded.get(identity, True))
        tree.blockSignals(False)
        tree.setUpdatesEnabled(True)

    def _fill_scopes(self, rows):
        self.scope_box.blockSignals(True)
        self.scope_box.clear()
        self.scope_box.addItem(self.tr('All scenes and notes'), LEAVES)
        for row in rows:
            if row['children']:
                self.scope_box.addItem(
                    '    ' * row['depth'] + self.tr('Inside: {0}').format(row['title']), row['id']
                )
        self.scope_box.setCurrentIndex(max(0, self.scope_box.findData(self.scope)))
        self.scope_box.blockSignals(False)

    def _fill_board(self, rows):
        board = self.board
        board.blockSignals(True)
        board.clear()
        shown = (
            [row for row in rows if not row['children']]
            if self.scope == LEAVES
            else [self.rows[i] for i in self.rows[self.scope]['children']]
        )
        for row in shown:
            item = QListWidgetItem(row['title'] or self.tr('Untitled'))
            item.setData(ROLE_ID, row['id'])
            item.setData(ROLE_HEADING, row['heading'])
            item.setData(ROLE_ROW, row)
            item.setData(Qt.AccessibleTextRole, describe_row(row))
            item.setToolTip(describe_row(row))
            item.setSizeHint(board.card_size())
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            board.addItem(item)
        board.blockSignals(False)

    def _fill_header(self, doc):
        words = goals.word_count(doc)
        target = doc.get('project', {}).get('target_words') if 'project' in doc else None
        if target:
            self.summary.setText(
                self.tr('{0:,} of {1:,} words ({2}%)').format(words, target, round(100 * words / target))
            )
            self.progress.setRange(0, target)
            self.progress.setValue(min(words, target))
            self.progress.show()
        else:
            self.summary.setText(self.tr('{0:,} words').format(words))
            self.progress.hide()
        # Items without a heading in the body are the ones ``project.items`` does not list.
        orphans = max(0, len(doc['project']['items']) - len(self.rows)) if 'project' in doc else 0
        if orphans:
            self.orphans.setText(
                self.tr(
                    '{0} binder item(s) lost their heading and are not shown. Nothing was deleted; '
                    'rebind or remove them with "byax project --action reconcile".'
                ).format(orphans)
            )
            self.orphans.show()
        else:
            self.orphans.hide()

    # Views and selection

    def set_view(self, view):
        if view not in ('outline', 'corkboard'):
            return
        current = self.current_id()
        self.view = view
        self.host.settings.setValue('binder/view', view)
        self._key = None
        self.refresh(force=True)
        self.select(current if current in self.rows else None, notify=False)
        self.host.motion.fade(self.stack.currentWidget())

    def _scope_chosen(self, index):
        self.scope = self.scope_box.itemData(index) or LEAVES
        self.refresh(force=True)

    def open_in_corkboard(self):
        identity = self.current_id()
        if identity and self.rows[identity]['children']:
            self.scope = identity
            self.set_view('corkboard')

    def select(self, identity, notify=True):
        view = self.outline if self.view == 'outline' else self.board
        item = view.find(identity) if identity else None
        view.blockSignals(not notify)
        if item is not None:
            view.setCurrentItem(item)
            view.scrollToItem(item)
        else:
            view.setCurrentItem(None)
        view.blockSignals(False)
        self.update_actions()
        self._load_synopsis()

    def _selection_changed(self, view):
        if view is (self.outline if self.view == 'outline' else self.board):
            self.update_actions()
            self._load_synopsis()

    def go_to(self, identity):
        row = self.rows.get(identity)
        if row:
            self.host.navigate_target(row['heading'])

    def go_to_current(self):
        self.go_to(self.current_id())

    def _context_menu(self, view, point):
        item = view.itemAt(point)
        if item is not None:
            view.setCurrentItem(item)
        self.update_actions()
        menu = self.actions_menu(view)
        menu.exec(view.viewport().mapToGlobal(point))

    # Actions

    def _providers(self):
        from .gui_ai import text_providers

        return text_providers()

    def update_actions(self):
        identity = self.current_id()
        row = self.rows.get(identity)
        a = self.act
        have = row is not None
        doc = self.doc
        for key in ('up', 'down', 'out', 'in'):
            a[key].setEnabled(have and project.plan_step(doc, identity, key) is not None)
        for key in ('go', 'edit', 'target'):
            a[key].setEnabled(have)
        a['status_other'].setEnabled(have)
        for status, action in self.status_actions:
            action.setEnabled(have)
            action.setCheckable(True)
            action.setChecked(have and row['status'] == status)
        a['merge'].setEnabled(have and project.next_item(doc, identity) is not None)
        a['split'].setEnabled(have and self._split_point(identity) is not None)
        a['inside'].setEnabled(have and bool(row['children']))
        a['create'].setEnabled('project' not in doc)
        a['scene'].setEnabled('project' in doc)
        a['chapter'].setEnabled('project' in doc)
        providers = self._providers() if have else []
        a['draft'].setEnabled(bool(providers))
        a['draft'].setToolTip(
            self.tr('Ask a configured provider for a synopsis. The answer is a proposal you review.')
            if providers
            else self.tr('Needs a configured text provider. Add one in Tools, Provider settings.')
        )

    def create_binder(self):
        if self.host.commit([{'type': 'project_init'}]):
            self.host.status_label.setText(self.tr('Binder created from the document headings'))

    def apply(self, operations, keep=None, message=None):
        host = self.host
        if not host.flush_edits():
            return False
        if not host.commit(operations):
            return False
        if keep:
            self.refresh(force=True)
            self.select(keep, notify=False)
        if message:
            host.status_label.setText(message)
        return True

    def step(self, direction):
        identity = self.current_id()
        if not identity:
            return False
        operations = project.plan_step(self.doc, identity, direction)
        if not operations:
            self.host.status_label.setText(self.tr('That move is not possible for this item'))
            return False
        title = self.rows[identity]['title']
        return self.apply(operations, identity, self.tr('Moved “{0}”').format(title))

    def drop(self, source, target, where):
        """A drag ended on ``target``; ``where`` is before, after or into."""
        try:
            operations = project.plan_drop(self.doc, source, target, where)
        except ByaxError as exc:
            self.host.error(exc)
            return False
        if not operations:
            self.host.status_label.setText(self.tr('A section cannot be dropped there'))
            return False
        return self.apply(operations, source, self.tr('Moved “{0}”').format(self.rows[source]['title']))

    def set_status(self, status):
        identity = self.current_id()
        if identity:
            self.apply([{'type': 'project_set_status', 'target': identity, 'status': status}], identity)

    def set_status_other(self):
        identity = self.current_id()
        if not identity:
            return
        text, ok = QInputDialog.getText(
            self, self.tr('Status'), self.tr('Status label'), text=self.rows[identity]['status']
        )
        if ok and text.strip():
            self.apply([{'type': 'project_set_status', 'target': identity, 'status': text.strip()}], identity)

    def set_target(self):
        identity = self.current_id()
        if not identity:
            return
        row = self.rows[identity]
        words, ok = QInputDialog.getInt(
            self,
            self.tr('Word target'),
            self.tr('Words for “{0}” (0 removes the target)').format(row['title']),
            row.get('target_words', 0),
            0,
            project.MAX_TARGET,
        )
        if ok:
            self.apply([{'type': 'project_set_target', 'target': identity, 'words': words or None}], identity)

    def set_synopsis(self, identity, text):
        if identity in self.rows:
            self.apply(
                [
                    {
                        'type': 'project_set_meta',
                        'target': identity,
                        'values': {'synopsis': text.strip() or None},
                    }
                ],
                identity,
            )

    def edit_synopsis(self):
        identity = self.current_id()
        if not identity:
            return
        if self.view == 'corkboard' and self.board.hasFocus():
            self.board.edit(self.board.currentIndex())
        else:
            self.synopsis.setFocus()

    def _load_synopsis(self):
        identity = self.current_id()
        row = self.rows.get(identity)
        self.synopsis.blockSignals(True)
        self.synopsis.setPlainText(row.get('synopsis', '') if row else '')
        self.synopsis.blockSignals(False)
        self.synopsis.setEnabled(row is not None)
        self._synopsis_for = identity

    def _synopsis_committed(self, text):
        identity = getattr(self, '_synopsis_for', None)
        row = self.rows.get(identity)
        if row and text.strip() != row.get('synopsis', ''):
            self.set_synopsis(identity, text)

    def add_item(self, kind, placement):
        identity = self.current_id() if placement else None
        title, ok = QInputDialog.getText(
            self,
            self.tr('Add {0}').format(self.tr(KIND_LABELS[kind]).lower()),
            self.tr('Title'),
            text=self.tr('Untitled {0}').format(self.tr(KIND_LABELS[kind]).lower()),
        )
        if not ok or not title.strip():
            return False
        operation = {'type': 'project_add_item', 'kind': kind, 'text': title.strip()}
        if identity:
            operation['after'] = identity
        elif 'project' not in self.doc:
            return False
        before = set(self.rows)
        if not self.apply([operation]):
            return False
        self.refresh(force=True)
        added = [i for i in self.order if i not in before]
        if added:
            self.select(added[0], notify=False)
            self.go_to(added[0])
        return True

    def _split_point(self, identity):
        """The editor cursor as (block, offset) when it lies in the item's own text after its heading."""
        editor = self.host.editor
        try:
            cursor = editor.selection_targets()
        except Exception:
            return None
        if not cursor:
            return None
        target, start, _ = cursor[0]
        if target in project.own_blocks(self.doc, identity)[1:]:
            return target, start
        return None

    def split_at_cursor(self):
        identity = self.current_id()
        if not identity or not self.host.flush_edits():
            return False
        point = self._split_point(identity)
        if point is None:
            QMessageBox.information(
                self,
                self.tr('Split'),
                self.tr(
                    'Place the editor cursor inside this item’s text, after its heading, where the new '
                    'section should begin.'
                ),
            )
            return False
        block, offset = point
        title, ok = QInputDialog.getText(
            self, self.tr('Split'), self.tr('Title of the new section'), text=self.tr('Untitled scene')
        )
        if not ok or not title.strip():
            return False
        text = next(b for b in self.doc['blocks'] if b['id'] == block).get('text', '')
        operation = {
            'type': 'project_split',
            'target': identity,
            'at': block,
            'offset': offset,
            'text': title.strip(),
        }
        if 0 < offset:
            operation['expected_text'] = text
        before = set(self.rows)
        if not self.apply([operation]):
            return False
        self.refresh(force=True)
        created = [i for i in self.order if i not in before]
        if created:
            self.select(created[0], notify=False)
        return True

    def merge_next(self):
        identity = self.current_id()
        following = project.next_item(self.doc, identity) if identity else None
        if not following:
            return False
        keep, gone = self.rows[identity]['title'], self.rows[following]['title']
        answer = QMessageBox.question(
            self,
            self.tr('Merge'),
            self.tr(
                'Merge “{0}” into “{1}”? The text of both stays together under “{1}”. You can undo this.'
            ).format(gone, keep),
        )
        if answer != QMessageBox.Yes:
            return False
        return self.apply([{'type': 'project_merge', 'target': identity, 'next': following}], identity)

    def draft_synopsis(self):
        """Opt-in: ask the chosen provider for a synopsis; the answer is only a pending proposal."""
        from .gui_providers import authorize_payload
        from .providers import ProviderRegistry

        identity = self.current_id()
        host = self.host
        providers = self._providers()
        if not identity:
            return False
        if not providers:
            QMessageBox.information(
                self,
                self.tr('Draft a synopsis'),
                self.tr(
                    'Drafting a synopsis needs a text provider that you configured. Add one in Tools, Provider settings.'
                ),
            )
            return False
        alias = providers[0]['alias']
        if len(providers) > 1:
            aliases = [p['alias'] for p in providers]
            alias, ok = QInputDialog.getItem(
                self, self.tr('Draft a synopsis'), self.tr('Provider'), aliases, 0, False
            )
            if not ok:
                return False
        if not host.flush_edits():
            return False
        import copy

        frozen = copy.deepcopy(host.doc)
        try:
            messages, context = project.synopsis_request(frozen, identity)
            registry = ProviderRegistry()
            config = registry.get(alias)
            preview = registry.preview(
                alias,
                kind='chat',
                text=messages[0]['content'],
                context={'mode': 'chat', 'messages': messages, 'document': context},
            )
        except Exception as exc:
            host.error(exc)
            return False
        authorized = False
        title = self.rows[identity]['title']
        if preview['remote'] and not config.allow_remote:
            if not authorize_payload(host, self.tr('Authorize synopsis request'), preview):
                return False
            authorized = True
        elif (
            QMessageBox.question(
                self,
                self.tr('Draft a synopsis'),
                self.tr(
                    'Ask {0} to draft a synopsis of “{1}”? The answer will be added to Proposed changes for you to review.'
                ).format(alias, title),
            )
            != QMessageBox.Yes
        ):
            return False

        def work():
            return project.draft_synopsis(frozen, identity, alias, registry=registry, authorized=authorized)

        def done(values):
            host.flush_edits()
            if host.doc['revision'] != frozen['revision']:
                host.status_label.setText(
                    self.tr('The document changed while the provider worked; the synopsis was not added.')
                )
                return
            if host.commit([{'type': 'propose', 'values': values}]):
                host.status_label.setText(self.tr('Synopsis proposal added to Proposed changes'))

        host.background(self.tr('Drafting a synopsis with {0}…').format(alias), work, done)
        return True


class BinderPanels:
    """Window integration: the Binder sidebar panel, its menu and its shortcuts."""

    def build_binder(self, menus):
        self.binder_panel = BinderPanel(self)
        menu = menus['&Binder']
        panel = self.binder_panel
        show = self.action(
            self.tr('Show Binder'),
            lambda: self.show_binder(),
            'Ctrl+Alt+B',
            description=self.tr('Show the Binder panel in the left sidebar'),
        )
        outline = self.action(
            self.tr('Binder outline'),
            lambda: self.show_binder('outline'),
            description=self.tr('Show the binder as an indented outline'),
        )
        board = self.action(
            self.tr('Binder corkboard'),
            lambda: self.show_binder('corkboard'),
            description=self.tr('Show the binder as scene cards with synopses'),
        )
        for action in (show, outline, board):
            menu.addAction(action)
        menu.addSeparator()
        for key in ('up', 'down', 'out', 'in'):
            menu.addAction(panel.act[key])
        menu.addSeparator()
        for key in ('go', 'edit', 'draft'):
            menu.addAction(panel.act[key])
        menu.addSeparator()
        for key in ('split', 'merge', 'target'):
            menu.addAction(panel.act[key])
        status = menu.addMenu(self.tr('Status'))
        for _, action in panel.status_actions:
            status.addAction(action)
        status.addAction(panel.act['status_other'])
        menu.addSeparator()
        for key in ('scene', 'chapter', 'inside', 'create'):
            menu.addAction(panel.act[key])
        menu.aboutToShow.connect(panel.update_actions)

    def show_binder(self, view=None):
        """Select the Binder in the left sidebar (or whichever sidebar already shows it)."""
        side = next(
            (s for s, selector in self.sidebar_selectors.items() if selector.currentData() == 'binder'),
            'left',
        )
        selector = self.sidebar_selectors[side]
        selector.setCurrentIndex(selector.findData('binder'))
        self.sidebar_docks[side].show()
        if view:
            self.binder_panel.set_view(view)
        self.binder_panel.refresh()
        return self.binder_panel

    def place_binder_panel(self, side):
        """One Binder panel serves both sidebars; it moves to the side that selects it."""
        slot = self.sidebar_panels[side]['binder']
        if self.binder_panel.parentWidget() is slot:
            return
        for panels in self.sidebar_panels.values():
            panels['binder-note'].setVisible(panels['binder'] is not slot)
        slot.layout().insertWidget(0, self.binder_panel, 1)
        self.binder_panel.show()


__all__ = ['BinderPanel', 'BinderPanels', 'Corkboard', 'OutlineTree']
