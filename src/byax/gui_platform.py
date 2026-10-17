"""Desktop-platform behaviour in the GUI: Finder document events, macOS menu roles, native key names."""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtGui import QAction, QKeySequence


class FileOpenFilter(QObject):
    """Catch ``QFileOpenEvent`` so a document opened from Finder or the Dock reaches the window.

    macOS delivers "Open With" and double-clicks as application events, not command-line
    arguments, and a file opened before the window exists would otherwise be lost. Paths received
    early are kept and handed over by :meth:`attach`.
    """

    opened = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pending: list[str] = []
        self._attached = False

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file()
            if path:
                if self._attached:
                    self.opened.emit(path)
                else:
                    self.pending.append(path)
                return True
        return False

    def attach(self, open_path):
        """Route future events to ``open_path`` and replay the ones that arrived earlier."""
        self.opened.connect(open_path)
        self._attached = True
        queued, self.pending = self.pending, []
        for path in queued:
            open_path(path)


def apply_menu_roles(about: QAction, preferences: QAction, quit_action: QAction) -> None:
    """Pin the roles macOS uses to move actions into the application menu.

    Other platforms ignore roles, so this is always safe. Explicit roles avoid depending on Qt's
    text heuristics, which fail for translated or renamed items.
    """
    about.setMenuRole(QAction.MenuRole.AboutRole)
    preferences.setMenuRole(QAction.MenuRole.PreferencesRole)
    quit_action.setMenuRole(QAction.MenuRole.QuitRole)


def native_keys(portable: str) -> str:
    """A portable shortcut such as ``Ctrl+Alt+L`` as the user sees it (⌘⌥L on macOS)."""
    return QKeySequence(portable).toString(QKeySequence.SequenceFormat.NativeText)


def uses_native_menu_bar() -> bool:
    return sys.platform == 'darwin'
