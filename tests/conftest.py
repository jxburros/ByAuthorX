"""Release Qt test windows promptly so later theme changes do not repolish them."""

import gc

import pytest


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path_factory, monkeypatch):
    """Writing-goal records and other local preferences must never land in the developer's real config."""
    root = tmp_path_factory.mktemp('user-config')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(root))
    monkeypatch.setenv('APPDATA', str(root))
    from PySide6.QtCore import QSettings

    # Qt reads the settings location once, so point it at the same folder for every format.
    for format_ in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        QSettings.setPath(format_, QSettings.Scope.UserScope, str(root))


@pytest.fixture(autouse=True)
def release_test_windows():
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    from byax.gui import MainWindow

    app = QApplication.instance()
    previous = set(app.topLevelWidgets()) if app else set()
    yield
    app = QApplication.instance()
    if app:
        # close() hides widgets; it does not destroy their C++ trees. Stylesheet
        # updates otherwise revisit every closed editor from preceding tests.
        for widget in app.topLevelWidgets():
            if isValid(widget) and isinstance(widget, MainWindow) and widget not in previous:
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
