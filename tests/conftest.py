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
