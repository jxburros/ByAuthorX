"""Release Qt test windows promptly so later theme changes do not repolish them."""

import gc

import pytest


@pytest.fixture(autouse=True)
def isolated_language_data(tmp_path_factory, monkeypatch):
    """Keep grammar settings and installed language packs out of the real user folders."""
    import json
    import socket

    from byax import language_packs

    folder = tmp_path_factory.mktemp('grammar')
    monkeypatch.setenv('BYAX_GRAMMAR_DIR', str(folder))
    with socket.socket() as sock:  # a developer's own LanguageTool on the default port must not leak in
        sock.bind(('127.0.0.1', 0))
        (folder / 'grammar.json').write_text(json.dumps({'languagetool': {'port': sock.getsockname()[1]}}))
    monkeypatch.setenv('BYAX_LANGUAGES_DIR', str(tmp_path_factory.mktemp('languages')))
    language_packs.refresh()
    yield
    language_packs.refresh()


@pytest.fixture(autouse=True)
def isolated_bibliography_data(tmp_path_factory, monkeypatch):
    """Lookup caches, the Retraction Watch index and installed CSL styles stay out of the user folders."""
    monkeypatch.setenv('BYAX_BIBLIOGRAPHY_DIR', str(tmp_path_factory.mktemp('bibliography')))


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
def isolated_extensions(tmp_path_factory, monkeypatch):
    """Installed extensions live in a throwaway folder, never the developer's user data directory."""
    monkeypatch.setenv('BYAX_EXTENSIONS_DIR', str(tmp_path_factory.mktemp('extensions')))


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
