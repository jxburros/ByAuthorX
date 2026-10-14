import os
import re
import tomllib
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from byax import __version__
from byax.cli import execute

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_a_plain_release_triple():
    assert re.fullmatch(r'\d+\.\d+\.\d+', __version__), 'release versions are MAJOR.MINOR.PATCH'


def test_package_metadata_and_lockfile_match_the_engine_version():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    assert project['version'] == __version__
    lock = tomllib.loads((ROOT / 'uv.lock').read_text(encoding='utf-8'))
    (entry,) = [package for package in lock['package'] if package['name'] == project['name']]
    assert entry['version'] == __version__, 'run `uv lock` after changing the version'


def test_windows_installer_carries_the_engine_version():
    script = (ROOT / 'packaging' / 'windows.iss').read_text(encoding='utf-8')
    assert re.search(r'^#define MyAppVersion "([^"]+)"', script, re.MULTILINE).group(1) == __version__


def test_ubuntu_package_script_reads_the_version_from_source():
    script = (ROOT / 'scripts' / 'build_deb.py').read_text(encoding='utf-8')
    assert 'from byax import __version__' in script
    assert __version__ not in script


def test_changelog_has_an_entry_for_this_version():
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert re.search(rf'^## \[{re.escape(__version__)}\]', changelog, re.MULTILINE)


def test_cli_version_command_reports_the_engine_version():
    result = execute('version', {})
    assert result['engine_version'] == __version__


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def test_about_dialog_shows_the_engine_version(app, tmp_path):
    from byax.gui import MainWindow

    window = MainWindow()
    window.recovery_dir = tmp_path
    try:
        dialog = window.create_about_dialog()
        text = ' '.join(label.text() for label in dialog.findChildren(QLabel))
        assert f'Version {__version__}' in text and '%1' not in text
        dialog.close()
    finally:
        window.dirty = False
        window.editor.blockSignals(True)
        window.close()
