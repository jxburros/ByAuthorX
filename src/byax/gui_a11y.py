"""Accessible names, tooltips and descriptions for every actionable control.

Explicit names set in the GUI modules win. Controls without one take the text a
sighted user reads next to them: the button text, the form label or buddy label,
or the placeholder. Tooltips repeat the name and append the keyboard shortcut.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollBar,
    QTabBar,
    QTextEdit,
    QToolButton,
    QWidget,
)

ACTIONABLE = (
    QAbstractButton,
    QComboBox,
    QLineEdit,
    QAbstractSpinBox,
    QAbstractItemView,
    QAbstractSlider,
    QTextEdit,
    QPlainTextEdit,
)
_COMPOSITE = (QComboBox, QAbstractSpinBox, QTabBar)


def plain(text):
    """Menu text without mnemonics or trailing ellipsis, as it should be spoken."""
    return text.replace('&&', '\0').replace('&', '').replace('\0', '&').rstrip('…').rstrip('.').strip()


def shortcut_text(sequence):
    return sequence.toString(QKeySequence.SequenceFormat.NativeText) if sequence else ''


def tooltip(name, sequence=None):
    keys = shortcut_text(sequence)
    return QCoreApplication.translate('Accessibility', '{0} ({1})').format(name, keys) if keys else name


def describe_action(action: QAction, description=None):
    """Tooltip with the shortcut, plus a status/what's-this description."""
    name = plain(action.text())
    action.setToolTip(tooltip(name, action.shortcut()))
    action.setStatusTip(description or name)
    action.setWhatsThis(description or name)
    return action


def describe_menus(menubar):
    """Describe actions Qt creates itself, such as dock visibility toggles."""
    pending = list(menubar.actions())
    while pending:
        action = pending.pop()
        if action.menu():
            pending.extend(action.menu().actions())
        elif not action.isSeparator() and not action.statusTip():
            describe_action(action)


def is_internal(widget):
    if widget.objectName().startswith('qt_') or isinstance(widget, (QScrollBar, QHeaderView)):
        return True
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, _COMPOSITE) or (
            isinstance(parent, QAbstractItemView) and parent.viewport() is not widget
        ):
            return True
        parent = parent.parentWidget()
    return False


def actionable(widget):
    return isinstance(widget, ACTIONABLE) and not is_internal(widget)


def _label_for(widget):
    parent = widget.parentWidget()
    if parent is None:
        return ''
    layouts = [parent.layout()] + parent.findChildren(QFormLayout)
    for layout in layouts:
        if isinstance(layout, QFormLayout):
            label = layout.labelForField(widget)
            if isinstance(label, QLabel) and label.text():
                return plain(label.text())
    for label in parent.findChildren(QLabel):
        if label.buddy() is widget and label.text():
            return plain(label.text())
    return ''


def derived_name(widget):
    if isinstance(widget, QToolButton) and widget.defaultAction() is not None:
        name = plain(widget.defaultAction().text())
        if name:
            return name
    if isinstance(widget, QAbstractButton) and widget.text():
        return plain(widget.text())
    name = _label_for(widget)
    if name:
        return name
    if isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit)) and widget.placeholderText():
        return plain(widget.placeholderText())
    if widget.toolTip():
        return plain(widget.toolTip())
    window = plain(widget.window().windowTitle()) if widget.window() is not widget else ''
    kinds = [
        (QAbstractItemView, QCoreApplication.translate('Accessibility', 'list')),
        (QComboBox, QCoreApplication.translate('Accessibility', 'choice')),
        (QAbstractSpinBox, QCoreApplication.translate('Accessibility', 'number')),
        (QAbstractSlider, QCoreApplication.translate('Accessibility', 'slider')),
        (QAbstractButton, QCoreApplication.translate('Accessibility', 'button')),
    ]
    kind = next(
        (label for cls, label in kinds if isinstance(widget, cls)),
        QCoreApplication.translate('Accessibility', 'text'),
    )
    return QCoreApplication.translate('Accessibility', '{0} {1}').format(window, kind).strip()


def ensure_accessible(root: QWidget):
    """Fill in missing accessible names and tooltips below ``root``."""
    for widget in [root, *root.findChildren(QWidget)]:
        if not actionable(widget):
            continue
        if not widget.accessibleName():
            widget.setAccessibleName(derived_name(widget))
        if not widget.toolTip():
            sequence = widget.shortcut() if isinstance(widget, QAbstractButton) else None
            if isinstance(widget, QToolButton) and widget.defaultAction() is not None:
                sequence = widget.defaultAction().shortcut()
            widget.setToolTip(tooltip(widget.accessibleName(), sequence))
        if not widget.accessibleDescription():
            action = widget.defaultAction() if isinstance(widget, QToolButton) else None
            description = action.statusTip() if action is not None else widget.toolTip()
            if description and description != widget.accessibleName():
                widget.setAccessibleDescription(description)


class AccessibilityFilter(QObject):
    """Labels a watched window's controls each time it is shown."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            ensure_accessible(watched)
        return False


_FILTER = None


def watch(window: QWidget):
    """Watch one window or dialog; cheaper than an application-wide event filter."""
    global _FILTER
    if _FILTER is None:
        _FILTER = AccessibilityFilter(QCoreApplication.instance())
    window.removeEventFilter(_FILTER)
    window.installEventFilter(_FILTER)
