"""Release Qt test windows promptly so later theme changes do not repolish them."""

import gc
import os
import threading

import pytest

try:  # imported once: tests that fake ``resource`` in sys.modules must not blind this guard
    import resource
except ImportError:  # Windows
    resource = None


def _rlimits():
    """The process-wide resource limits that an isolated worker sets on itself (POSIX only)."""
    if resource is None:
        return {}
    names = ('RLIMIT_AS', 'RLIMIT_CPU', 'RLIMIT_FSIZE')
    return {n: resource.getrlimit(getattr(resource, n)) for n in names if hasattr(resource, n)}


@pytest.fixture(autouse=True)
def test_process_keeps_its_resource_limits():
    """Worker launchers cap the process they run in; run in-process they would wedge every later test.

    ``extension_worker`` / ``library_parser`` apply RLIMIT_CPU, RLIMIT_AS and RLIMIT_FSIZE to themselves,
    so a test may only call them in a subprocess or with ``apply_parser_limits`` replaced.
    """
    before = _rlimits()
    yield
    after = _rlimits()
    if after != before:
        for name, value in before.items():
            try:
                resource.setrlimit(getattr(resource, name), value)
            except (ValueError, OSError):
                pass  # a lowered hard limit cannot be raised back; the failure below still names it
        pytest.fail(f'test changed the runner process resource limits: {before} -> {after}', pytrace=False)


def _child_pids():
    """Direct child processes of this process (Linux only; elsewhere the check is skipped)."""
    pids = set()
    try:
        for task in os.listdir('/proc/self/task'):
            with open(f'/proc/self/task/{task}/children', encoding='ascii') as handle:
                pids.update(int(pid) for pid in handle.read().split())
    except (OSError, ValueError):
        return None
    return pids


@pytest.fixture(autouse=True, scope='module')
def no_stray_processes_or_threads_survive_a_module(request):
    """A module must reap every worker process and join every non-daemon thread it started.

    A leaked extension worker waiting on stdin, a parser subprocess or a forgotten thread is what makes a
    whole-suite run hang or crawl far from the test that caused it.
    """
    children_before = _child_pids()
    threads_before = {t.ident for t in threading.enumerate()}
    yield
    gc.collect()
    problems = []
    if children_before is not None:
        import time

        deadline = time.monotonic() + 2.0
        while True:
            leaked = (_child_pids() or set()) - children_before
            if not leaked or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        if leaked:
            problems.append(f'child processes still running: {sorted(leaked)}')
    lingering = [
        t.name
        for t in threading.enumerate()
        if t.ident not in threads_before and t.is_alive() and not t.daemon
    ]
    if lingering:
        problems.append(f'non-daemon threads still running: {lingering}')
    if problems:
        pytest.fail(f'{request.module.__name__} leaked ' + '; '.join(problems), pytrace=False)


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
    released = False
    if app:
        # close() hides widgets; it does not destroy their C++ trees. Stylesheet
        # updates otherwise revisit every closed editor from preceding tests.
        for widget in app.topLevelWidgets():
            if isValid(widget) and isinstance(widget, MainWindow) and widget not in previous:
                widget.deleteLater()
                released = True
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    if released:
        # A full collection walks the whole heap (about 0.2 s once PySide and the suite are loaded), so
        # only pay for it when a window was actually dropped, not after every pure-Python test.
        gc.collect()
