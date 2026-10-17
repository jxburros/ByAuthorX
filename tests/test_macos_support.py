"""macOS port: platform branches (exercised by patching ``sys.platform``), packaging recipe and workflows.

Nothing here runs on a Mac. The macOS suite run, a real signature and a notarization ticket stay
pending maintainer verification (docs/acceptance-matrix.md, "Reach").
"""

import importlib.util
import json
import plistlib
import posixpath
import re
import runpy
import shutil
import sys
import types
import unicodedata
from pathlib import Path

import pytest

from byax import fonts, platform_support, storage
from byax.errors import ByaxError

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / 'src' / 'byax' / 'resources' / 'fonts'


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / 'scripts'))
    return module


@pytest.fixture
def macos(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')


# --- fonts --------------------------------------------------------------------------------------


def test_macos_font_folders_are_searched_user_first(tmp_path):
    assert platform_support.system_font_dirs(tmp_path) == [
        tmp_path / 'Library' / 'Fonts',
        Path('/Library/Fonts'),
        Path('/System/Library/Fonts'),
    ]


def test_system_font_scan_matches_family_and_style_by_the_names_inside_the_file(tmp_path):
    folder = tmp_path / 'Fonts'
    folder.mkdir()
    for name in ('DejaVuSans', 'DejaVuSans-Bold', 'DejaVuSans-Oblique', 'DejaVuSansMono'):
        shutil.copy2(FONT_DIR / (name + '.ttf'), folder / (name + '.ttf'))
    (folder / 'notes.txt').write_text('not a font')
    find = platform_support.find_system_font
    assert find('DejaVu Sans', directories=[folder]) == folder / 'DejaVuSans.ttf'
    assert find('DejaVu Sans', bold=True, directories=[folder]) == folder / 'DejaVuSans-Bold.ttf'
    assert find('DejaVu Sans', italic=True, directories=[folder]) == folder / 'DejaVuSans-Oblique.ttf'
    # "DejaVu Sans Mono" shares a file-name prefix but has another family inside.
    assert find('DejaVu Sans Mono', directories=[folder]) == folder / 'DejaVuSansMono.ttf'
    assert find('Georgia', directories=[folder]) is None
    assert find('DejaVu Sans', directories=[tmp_path / 'missing']) is None


def test_resolve_font_file_uses_the_macos_folders_instead_of_fontconfig(macos, monkeypatch, tmp_path):
    folder = tmp_path / 'Fonts'
    folder.mkdir()
    shutil.copy2(FONT_DIR / 'DejaVuSerif.ttf', folder / 'DejaVuSerif.ttf')
    monkeypatch.setattr(fonts, 'bundled_font_path', lambda *args: None)
    monkeypatch.setattr(platform_support, 'system_font_dirs', lambda *_: [folder])
    monkeypatch.setattr('shutil.which', lambda name: pytest.fail('fc-match must not be consulted on macOS'))
    assert fonts.resolve_font_file('DejaVu Serif') == folder / 'DejaVuSerif.ttf'
    assert fonts.resolve_font_file('Nonexistent Family') is None


# --- durability, locks and process limits ------------------------------------------------------


def test_fsync_uses_full_fsync_on_macos_and_plain_fsync_elsewhere(monkeypatch):
    calls = []
    fake = types.SimpleNamespace(
        F_FULLFSYNC=51, fcntl=lambda fd, command: calls.append(('fcntl', fd, command))
    )
    monkeypatch.setitem(sys.modules, 'fcntl', fake)
    monkeypatch.setattr('os.fsync', lambda fd: calls.append(('fsync', fd)))
    monkeypatch.setattr(sys, 'platform', 'darwin')
    platform_support.fsync_file(7)
    assert calls == [('fcntl', 7, 51)]
    calls.clear()
    monkeypatch.setattr(sys, 'platform', 'linux')
    platform_support.fsync_file(7)
    assert calls == [('fsync', 7)]


def test_fsync_falls_back_when_the_volume_rejects_full_fsync(macos, monkeypatch):
    calls = []

    def refuse(fd, command):
        raise OSError(25, 'Inappropriate ioctl')

    monkeypatch.setitem(sys.modules, 'fcntl', types.SimpleNamespace(F_FULLFSYNC=51, fcntl=refuse))
    monkeypatch.setattr('os.fsync', lambda fd: calls.append(fd))
    platform_support.fsync_file(9)
    assert calls == [9]


def test_lock_identity_folds_case_and_unicode_form_only_on_macos(monkeypatch):
    composed = '/Users/a/Documents/Café.byax'
    decomposed = unicodedata.normalize('NFD', composed).replace('Caf', 'CAF')
    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert platform_support.lock_identity(composed) == platform_support.lock_identity(decomposed)
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(platform_support.os.path, 'normcase', posixpath.normcase)
    assert platform_support.lock_identity('/a/B.byax') != platform_support.lock_identity('/a/b.byax')


@pytest.mark.skipif(sys.platform == 'win32', reason='flock')
def test_differently_cased_paths_share_one_lock_on_macos(macos, tmp_path):
    first = tmp_path / 'Draft.byax'
    second = tmp_path / 'draft.byax'
    with storage.DocumentLock(first, owner='one'):
        with pytest.raises(ByaxError) as caught:
            storage.DocumentLock(second, owner='two').acquire()
        assert caught.value.code == 'locked'


def _fake_resource(refuse=()):
    applied = []

    def setrlimit(kind, limits):
        if kind in refuse:
            raise ValueError('current limit exceeds maximum limit')
        applied.append((kind, limits))

    module = types.SimpleNamespace(RLIMIT_AS='AS', RLIMIT_CPU='CPU', setrlimit=setrlimit)
    return module, applied


def test_parser_limits_tolerate_macos_refusing_the_address_space_limit(macos, monkeypatch):
    module, applied = _fake_resource(refuse=('AS',))
    monkeypatch.setitem(sys.modules, 'resource', module)
    assert platform_support.apply_parser_limits(768, 30) == {'memory': False, 'cpu': True}
    assert applied == [('CPU', (30, 30))]


def test_parser_limits_never_claim_memory_enforcement_on_macos(macos, monkeypatch):
    module, applied = _fake_resource()
    monkeypatch.setitem(sys.modules, 'resource', module)
    assert platform_support.apply_parser_limits(768, 30) == {'memory': False, 'cpu': True}
    assert ('AS', (768, 768)) in applied  # still requested where the kernel honours it


def test_parser_limits_apply_both_on_linux_and_none_on_windows(monkeypatch):
    module, applied = _fake_resource()
    monkeypatch.setitem(sys.modules, 'resource', module)
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert platform_support.apply_parser_limits(1, 2) == {'memory': True, 'cpu': True}
    applied.clear()
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert platform_support.apply_parser_limits(1, 2) == {'memory': False, 'cpu': False}
    assert applied == []


def test_library_parser_still_extracts_when_macos_refuses_limits(macos, monkeypatch, tmp_path):
    from byax import library_parser

    module, _ = _fake_resource(refuse=('AS', 'CPU'))
    monkeypatch.setitem(sys.modules, 'resource', module)
    source, output = tmp_path / 'source.txt', tmp_path / 'out.json'
    source.write_text('Aria lighthouse')
    assert library_parser.main([str(source), '100', str(output)]) == 0
    assert json.loads(output.read_text())['passages'][0]['text'] == 'Aria lighthouse'


# --- GUI seams ----------------------------------------------------------------------------------


def test_finder_open_events_reach_the_window_even_before_it_exists(qapp):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFileOpenEvent

    from byax.gui_platform import FileOpenFilter

    opener = FileOpenFilter()
    qapp.installEventFilter(opener)
    try:
        event = QFileOpenEvent('/Users/a/First.byax')
        assert event.type() == QEvent.Type.FileOpen
        qapp.sendEvent(qapp, event)
        assert opener.pending == ['/Users/a/First.byax']
        received = []
        opener.attach(received.append)
        assert received == ['/Users/a/First.byax'] and not opener.pending
        qapp.sendEvent(qapp, QFileOpenEvent('/Users/a/Second.byax'))
        assert received == ['/Users/a/First.byax', '/Users/a/Second.byax']
    finally:
        qapp.removeEventFilter(opener)


def test_menu_roles_move_about_preferences_and_quit_into_the_application_menu(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QAction

    from byax.gui import MainWindow

    settings = QSettings(str(tmp_path / 'roles.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.quit_action.menuRole() == QAction.MenuRole.QuitRole
    assert window.provider_settings_action.menuRole() == QAction.MenuRole.PreferencesRole
    about = next(a for a in window.findChildren(QAction) if a.text() == 'About ByAuthorX')
    assert about.menuRole() == QAction.MenuRole.AboutRole
    # Only one action may hold each role or macOS shows the wrong one.
    roles = [a.menuRole() for a in window.findChildren(QAction)]
    for role in (QAction.MenuRole.AboutRole, QAction.MenuRole.PreferencesRole, QAction.MenuRole.QuitRole):
        assert roles.count(role) == 1


def test_shortcut_names_come_from_the_platform_not_from_text():
    from byax.gui_platform import native_keys

    assert native_keys('F7') == 'F7'
    gui = (ROOT / 'src' / 'byax' / 'gui.py').read_text(encoding='utf-8')
    assert 'Ctrl+Alt+L to return' not in gui


def test_no_source_module_selects_a_keyring_backend_explicitly():
    # keyring picks Keychain on macOS, Credential Locker on Windows and Secret Service on Linux.
    hits = [
        path.name
        for path in (ROOT / 'src' / 'byax').glob('*.py')
        if re.search(r'set_keyring|keyring\.backends|KEYRING_BACKEND', path.read_text(encoding='utf-8'))
    ]
    assert hits == []


# --- packaging ----------------------------------------------------------------------------------


@pytest.fixture(scope='module')
def macos_build():
    return load_script('build_macos')


def test_info_plist_declares_identity_documents_privacy_and_high_resolution(macos_build):
    plist = macos_build.info_plist('1.2.3')
    assert plist['CFBundleIdentifier'] == macos_build.BUNDLE_ID == 'io.github.jxburros.byauthorx'
    assert plist['CFBundleShortVersionString'] == plist['CFBundleVersion'] == '1.2.3'
    assert plist['NSHighResolutionCapable'] is True
    assert plist['LSArchitecturePriority'] == ['arm64'] and plist['LSRequiresNativeExecution'] is True
    assert len(plist['NSMicrophoneUsageDescription']) > 40
    kind = plist['CFBundleDocumentTypes'][0]
    exported = plist['UTExportedTypeDeclarations'][0]
    assert kind['CFBundleTypeExtensions'] == ['byax'] and kind['CFBundleTypeRole'] == 'Editor'
    assert kind['LSItemContentTypes'] == [exported['UTTypeIdentifier']] == [macos_build.DOCUMENT_UTI]
    assert exported['UTTypeTagSpecification']['public.filename-extension'] == ['byax']
    assert plistlib.loads(plistlib.dumps(plist)) == plist


def test_minimum_system_version_is_no_older_than_the_locked_qt_wheels(macos_build):
    lock = (ROOT / 'uv.lock').read_text(encoding='utf-8')
    tags = {int(m) for m in re.findall(r'pyside6_essentials-6\.12\.\d+-[^"]*macosx_(\d+)_0', lock)}
    assert tags, 'uv.lock lists no macOS PySide6 wheel'
    assert float(macos_build.MIN_SYSTEM_VERSION) >= max(tags)


def test_entitlements_allow_microphone_and_network_but_no_jit(macos_build):
    entitlements = plistlib.loads(macos_build.ENTITLEMENTS.read_bytes())
    assert entitlements['com.apple.security.device.audio-input'] is True
    assert entitlements['com.apple.security.network.client'] is True
    forbidden = {
        'com.apple.security.cs.allow-jit',
        'com.apple.security.cs.allow-unsigned-executable-memory',
        'com.apple.security.cs.disable-library-validation',
        'com.apple.security.app-sandbox',
    }
    assert not forbidden & set(entitlements)


def test_icns_is_written_from_the_brand_icon(macos_build, tmp_path):
    path = macos_build.write_icns(tmp_path / 'byauthorx.icns')
    assert path.read_bytes()[:4] == b'icns'


def test_launchers_reenter_the_application_and_follow_symlinks(macos_build, tmp_path):
    for mode in ('cli', 'mcp'):
        script = macos_build.launcher_script(mode)
        assert script.startswith('#!/bin/sh') and f'--byax-{mode}' in script and 'readlink' in script
        assert 'exec "$here/ByAuthorX"' in script


def test_finish_bundle_merges_the_plist_and_adds_icon_and_launchers(macos_build, monkeypatch, tmp_path):
    app = tmp_path / 'ByAuthorX.app'
    (app / 'Contents' / 'MacOS').mkdir(parents=True)
    (app / 'Contents' / 'Info.plist').write_bytes(
        plistlib.dumps({'CFBundleName': 'old', 'NSPrincipalClass': 'NSApplication'})
    )
    build = importlib.import_module('build')
    assert Path(build.__file__).parent == ROOT / 'scripts'
    monkeypatch.setattr(build, 'copy_notices', lambda output: (output / 'LICENSE').write_text('MIT'))
    macos_build.finish_bundle(app, '9.9.9')
    plist = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
    assert plist['NSPrincipalClass'] == 'NSApplication' and plist['CFBundleName'] == 'ByAuthorX'
    assert plist['CFBundleShortVersionString'] == '9.9.9'
    assert (app / 'Contents' / 'Resources' / 'byauthorx.icns').is_file()
    for name in ('byax', 'byax-mcp'):
        launcher = app / 'Contents' / 'MacOS' / name
        assert launcher.read_text(encoding='utf-8').startswith('#!/bin/sh')
        if sys.platform != 'win32':  # Windows has no executable bit to inspect
            assert launcher.stat().st_mode & 0o111


def _fake_app(root):
    app = root / 'ByAuthorX.app'
    macho = b'\xcf\xfa\xed\xfe' + b'\0' * 12
    files = {
        'Contents/MacOS/ByAuthorX': macho,
        'Contents/Frameworks/libpython3.12.dylib': macho,
        'Contents/Frameworks/PySide6/Qt/lib/QtCore.framework/Versions/A/QtCore': macho,
        'Contents/Frameworks/PySide6/QtGui.abi3.so': b'MZ-not-really',
        'Contents/Resources/notices/LICENSE': b'MIT',
    }
    for relative, data in files.items():
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return app


def test_nested_code_is_found_and_signed_inside_out(macos_build, tmp_path):
    app = _fake_app(tmp_path)
    found = macos_build.nested_code(app)
    names = [p.name for p in found]
    assert 'LICENSE' not in names and 'QtGui.abi3.so' in names
    depths = [len(p.parts) for p in found]
    assert depths == sorted(depths, reverse=True)
    framework = next(p for p in found if p.suffix == '.framework')
    assert names.index('QtCore') < names.index(framework.name)


def test_codesign_commands_use_hardened_runtime_timestamps_and_entitlements(macos_build, tmp_path):
    app = _fake_app(tmp_path)
    commands = macos_build.codesign_commands(
        app, 'Developer ID Application: Test (TEAM123456)', keychain='k.db'
    )
    signing = [c for c in commands if c[1] == '--force']
    assert signing and all('runtime' in c and '--timestamp' in c and '--keychain' in c for c in signing)
    assert all(c[c.index('--sign') + 1] == 'Developer ID Application: Test (TEAM123456)' for c in signing)
    assert signing[-1][-1] == str(app) and '--entitlements' in signing[-1]
    assert str(macos_build.ENTITLEMENTS) in signing[-1]
    assert not any('--deep' in c for c in signing)
    assert commands[-1][:2] == ['codesign', '--verify']


def test_notarization_waits_then_staples_and_validates(macos_build, tmp_path):
    dmg = tmp_path / 'ByAuthorX.dmg'
    submit, staple, validate = macos_build.notarize_commands(
        dmg, apple_id='a@example.com', team_id='T', password='p'
    )
    assert submit[:3] == ['xcrun', 'notarytool', 'submit'] and '--wait' in submit
    assert staple == ['xcrun', 'stapler', 'staple', str(dmg)]
    assert validate[:3] == ['xcrun', 'stapler', 'validate']


def test_only_a_signed_and_notarized_image_gets_the_plain_name(macos_build):
    name = macos_build.dmg_name
    assert name('1.0.0', signed=True, notarized=True) == 'ByAuthorX-1.0.0-macos-arm64.dmg'
    assert name('1.0.0', signed=False, notarized=False) == 'ByAuthorX-1.0.0-macos-arm64-UNSIGNED.dmg'
    assert name('1.0.0', signed=True, notarized=False) == 'ByAuthorX-1.0.0-macos-arm64-NOT-NOTARIZED.dmg'


def test_notarize_without_identity_or_credentials_refuses(macos_build, monkeypatch):
    monkeypatch.setattr(macos_build, 'build_app', lambda: Path('/nonexistent/ByAuthorX.app'))
    monkeypatch.delenv('APPLE_SIGNING_IDENTITY', raising=False)
    with pytest.raises(SystemExit, match='signing identity'):
        macos_build.main(['--notarize'])


def test_unsigned_notice_explains_gatekeeper(macos_build):
    text = macos_build.UNSIGNED_README.read_text(encoding='utf-8')
    assert 'Open Anyway' in text and 'xattr -dr com.apple.quarantine' in text and 'SHA-256' in text


def test_pyinstaller_command_is_shared_and_adds_platform_flags():
    build = load_script('build')
    command = build.pyinstaller_command(
        'ByAuthorX', 'gui_entry.py', windowed=True, stage=Path('stage'), extra=['--icon', 'x.icns']
    )
    assert '--windowed' in command and command[command.index('--icon') + 1] == 'x.icns'
    assert command[-1].endswith('gui_entry.py') and '--collect-data' in command
    plain = build.pyinstaller_command('byax', 'cli_entry.py', windowed=False, stage=Path('stage'))
    assert '--windowed' not in plain


def test_application_executable_doubles_as_the_command_line_tool(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['ByAuthorX', '--byax-cli', 'version'])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ROOT / 'packaging' / 'gui_entry.py'), run_name='frozen')
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)['engine_version']


# --- workflows ----------------------------------------------------------------------------------


def _workflow(name):
    return (ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8')


def test_ci_runs_the_focused_subset_and_freezing_on_apple_silicon():
    ci = _workflow('ci.yml')
    assert ci.count('macos-14') == 2  # compatibility matrix and package matrix
    assert 'tests/test_macos_support.py' in ci and 'ByAuthorX.app/Contents/MacOS/byax' in ci
    assert "runner.os != 'macOS'" in ci  # the disk image is built only by the release workflow


def test_release_signs_and_notarizes_only_when_every_secret_exists():
    release = _workflow('release.yml')
    for secret in (
        'APPLE_CERTIFICATE_P12',
        'APPLE_CERTIFICATE_PASSWORD',
        'APPLE_TEAM_ID',
        'APPLE_ID',
        'APPLE_APP_PASSWORD',
    ):
        assert f'secrets.{secret}' in release
    assert 'runs-on: macos-14' in release
    assert release.count("steps.apple.outputs.sign == 'true'") >= 3
    assert 'build_macos.py --dmg --notarize' in release and 'build_macos.py --dmg\n' in release
    # Windows and Linux signing are gated on their own secrets and report their status.
    assert 'WINDOWS_CERTIFICATE_PFX' in release and 'signtool' in release
    assert 'GPG_PRIVATE_KEY' in release and 'detach-sign' in release
    assert 'signing-status-' in release and 'Signing status' in release
    # Secrets reach steps through env, never through an `if` that would leak or misevaluate them.
    assert not re.search(r'if:.*secrets\.', release)
