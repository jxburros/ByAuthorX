#!/usr/bin/env python3
"""Build ByAuthorX.app and a .dmg for Apple silicon; sign and notarize only when asked.

    uv run python scripts/build_macos.py                 # freeze the app (what build.py does on macOS)
    uv run python scripts/build_macos.py --dmg           # also write an unsigned, labelled .dmg
    uv run python scripts/build_macos.py --dmg --sign "Developer ID Application: Name (TEAMID)" --notarize

One windowed app carries the GUI, the CLI and the MCP server: ``Contents/MacOS/byax`` and
``byax-mcp`` are small launchers that re-enter the application executable (see
``packaging/gui_entry.py``), so the three entry points share one copy of Python and Qt.
Signing uses the hardened runtime with ``packaging/macos/entitlements.plist``. Notarization reads
``APPLE_ID``, ``APPLE_APP_PASSWORD`` and ``APPLE_TEAM_ID`` from the environment and needs network access.
The script is written without macOS hardware; the command lists it builds are covered by tests, and the
steps that need a Mac are listed in docs/packaging.md.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

APP_NAME = 'ByAuthorX'
BUNDLE_ID = 'io.github.jxburros.byauthorx'
DOCUMENT_UTI = BUNDLE_ID + '.document'
# The PySide6 6.12 wheels that Python 3.12 resolves are tagged macosx_14_0 (see uv.lock).
MIN_SYSTEM_VERSION = '14.0'
ENTITLEMENTS = ROOT / 'packaging' / 'macos' / 'entitlements.plist'
UNSIGNED_README = ROOT / 'packaging' / 'macos' / 'README-UNSIGNED.txt'
ICON_SIZES = [(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)]
MACH_O_MAGIC = (
    b'\xcf\xfa\xed\xfe',  # 64-bit little endian
    b'\xca\xfe\xba\xbe',  # universal ("fat")
    b'\xbe\xba\xfe\xca',
)


def info_plist(version: str) -> dict:
    """Keys added to the Info.plist PyInstaller generates (bundle id, documents, privacy strings)."""
    return {
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': APP_NAME,
        'CFBundleIdentifier': BUNDLE_ID,
        'CFBundleExecutable': APP_NAME,
        'CFBundlePackageType': 'APPL',
        'CFBundleIconFile': 'byauthorx.icns',
        'CFBundleShortVersionString': version,
        'CFBundleVersion': version,
        'LSMinimumSystemVersion': MIN_SYSTEM_VERSION,
        'LSApplicationCategoryType': 'public.app-category.productivity',
        'LSArchitecturePriority': ['arm64'],
        'LSRequiresNativeExecution': True,
        'NSHighResolutionCapable': True,
        'NSSupportsAutomaticGraphicsSwitching': True,
        'NSHumanReadableCopyright': 'MIT licence. Copyright the ByAuthorX contributors.',
        'NSMicrophoneUsageDescription': (
            'ByAuthorX records audio only when you choose Dictation or record for transcription. '
            'Recordings stay on this computer unless you picked a provider that sends them elsewhere.'
        ),
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'ByAuthorX document',
                'CFBundleTypeRole': 'Editor',
                'LSHandlerRank': 'Owner',
                'LSItemContentTypes': [DOCUMENT_UTI],
                'CFBundleTypeExtensions': ['byax'],
                'CFBundleTypeIconFile': 'byauthorx.icns',
            },
            {
                'CFBundleTypeName': 'Word document',
                'CFBundleTypeRole': 'Editor',
                'LSHandlerRank': 'Alternate',
                'LSItemContentTypes': ['org.openxmlformats.wordprocessingml.document'],
                'CFBundleTypeExtensions': ['docx'],
            },
            {
                'CFBundleTypeName': 'EPUB book',
                'CFBundleTypeRole': 'Editor',
                'LSHandlerRank': 'Alternate',
                'LSItemContentTypes': ['org.idpf.epub-container'],
                'CFBundleTypeExtensions': ['epub'],
            },
        ],
        'UTExportedTypeDeclarations': [
            {
                'UTTypeIdentifier': DOCUMENT_UTI,
                'UTTypeDescription': 'ByAuthorX document',
                'UTTypeConformsTo': ['public.data'],
                'UTTypeIconFiles': ['byauthorx.icns'],
                'UTTypeTagSpecification': {
                    'public.filename-extension': ['byax'],
                    'public.mime-type': ['application/x-byauthorx'],
                },
            }
        ],
    }


def launcher_script(mode: str) -> str:
    """Shell launcher placed in Contents/MacOS; it follows symlinks so /usr/local/bin/byax works."""
    return f"""#!/bin/sh
# Command-line entry point of ByAuthorX; runs the application executable in {mode} mode.
source="$0"
while [ -L "$source" ]; do
  dir="$(cd -P "$(dirname "$source")" && pwd)"
  source="$(readlink "$source")"
  case "$source" in /*) ;; *) source="$dir/$source" ;; esac
done
here="$(cd -P "$(dirname "$source")" && pwd)"
exec "$here/{APP_NAME}" --byax-{mode} "$@"
"""


def write_icns(destination: Path) -> Path:
    from PIL import Image

    source = ROOT / 'src' / 'byax' / 'resources' / 'brand' / 'app-1024.png'
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.open(source).save(destination, format='ICNS', sizes=ICON_SIZES)
    return destination


def finish_bundle(app: Path, version: str) -> None:
    """Merge our Info.plist keys, add the icon and the CLI/MCP launchers, and notices."""
    contents = app / 'Contents'
    plist_path = contents / 'Info.plist'
    plist = plistlib.loads(plist_path.read_bytes()) if plist_path.exists() else {}
    plist.update(info_plist(version))
    plist_path.write_bytes(plistlib.dumps(plist, sort_keys=True))
    write_icns(contents / 'Resources' / 'byauthorx.icns')
    for name, mode in (('byax', 'cli'), ('byax-mcp', 'mcp')):
        launcher = contents / 'MacOS' / name
        launcher.write_text(launcher_script(mode), encoding='utf-8')
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    import build

    notices = contents / 'Resources' / 'notices'
    notices.mkdir(parents=True, exist_ok=True)
    build.copy_notices(notices)


def build_app() -> Path:
    """Freeze the GUI as ByAuthorX.app (arm64) and complete the bundle; returns the app path."""
    import build
    from byax import __version__

    if sys.platform != 'darwin':
        raise SystemExit('Build the macOS app on macOS (Apple silicon).')
    stage = ROOT / 'build' / 'bundles'
    icon = write_icns(ROOT / 'build' / 'byauthorx.icns')
    extra = [
        '--osx-bundle-identifier',
        BUNDLE_ID,
        '--target-architecture',
        'arm64',
        '--icon',
        str(icon),
        # The OS keychain backend is discovered by entry point, which PyInstaller cannot see.
        '--hidden-import',
        'keyring.backends.macOS',
    ]
    subprocess.run(
        build.pyinstaller_command(APP_NAME, 'gui_entry.py', windowed=True, stage=stage, extra=extra),
        cwd=ROOT,
        check=True,
    )
    app = ROOT / 'dist' / f'{APP_NAME}.app'
    if app.exists():
        shutil.rmtree(app)
    app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(stage / f'{APP_NAME}.app', app, symlinks=True)
    finish_bundle(app, __version__)
    return app


# Signing and notarization


def is_mach_o(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with path.open('rb') as handle:
            return handle.read(4) in MACH_O_MAGIC
    except OSError:
        return False


def nested_code(app: Path) -> list[Path]:
    """Everything inside the bundle that needs its own signature, deepest first (inside-out)."""
    found: list[Path] = []
    for path in app.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_dir() and path.suffix in ('.framework', '.app', '.xpc'):
            found.append(path)
        elif path.is_file() and (path.suffix in ('.so', '.dylib') or is_mach_o(path)):
            found.append(path)
    return sorted(found, key=lambda p: (-len(p.parts), str(p)))


def codesign_commands(
    app: Path, identity: str, *, keychain: str | None = None, nested=None
) -> list[list[str]]:
    """The ``codesign`` calls to sign a bundle for notarization: hardened runtime and a timestamp."""
    base = ['codesign', '--force', '--timestamp', '--options', 'runtime', '--sign', identity]
    if keychain:
        base += ['--keychain', keychain]
    inner = nested if nested is not None else nested_code(app)
    commands = [[*base, str(path)] for path in inner]
    # The main executable and the app itself get the entitlements, signed last.
    commands.append([*base, '--entitlements', str(ENTITLEMENTS), str(app)])
    commands.append(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)])
    return commands


def notarize_commands(dmg: Path, *, apple_id: str, team_id: str, password: str) -> list[list[str]]:
    return [
        [
            'xcrun',
            'notarytool',
            'submit',
            str(dmg),
            '--apple-id',
            apple_id,
            '--team-id',
            team_id,
            '--password',
            password,
            '--wait',
        ],
        ['xcrun', 'stapler', 'staple', str(dmg)],
        ['xcrun', 'stapler', 'validate', str(dmg)],
    ]


def dmg_name(version: str, *, signed: bool, notarized: bool) -> str:
    """Only a signed and notarized image carries the plain name; anything else says what it lacks."""
    base = f'{APP_NAME}-{version}-macos-arm64'
    if signed and notarized:
        return base + '.dmg'
    return base + ('-NOT-NOTARIZED.dmg' if signed else '-UNSIGNED.dmg')


def make_dmg(app: Path, destination: Path, *, unsigned_notice: bool) -> Path:
    stage = ROOT / 'build' / 'dmg'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(app, stage / app.name, symlinks=True)
    os.symlink('/Applications', stage / 'Applications')
    if unsigned_notice:
        shutil.copy2(UNSIGNED_README, stage / 'READ ME FIRST - unsigned build.txt')
    if destination.exists():
        destination.unlink()
    subprocess.run(
        [
            'hdiutil',
            'create',
            '-volname',
            APP_NAME,
            '-srcfolder',
            str(stage),
            '-ov',
            '-format',
            'UDZO',
            str(destination),
        ],
        check=True,
    )
    return destination


def run_all(commands: list[list[str]]) -> None:
    for command in commands:
        subprocess.run(command, check=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dmg', action='store_true', help='Also create the disk image')
    parser.add_argument('--no-freeze', action='store_true', help='Reuse dist/ByAuthorX.app')
    parser.add_argument('--sign', metavar='IDENTITY', default=os.environ.get('APPLE_SIGNING_IDENTITY'))
    parser.add_argument('--keychain', default=os.environ.get('APPLE_KEYCHAIN'))
    parser.add_argument('--notarize', action='store_true')
    args = parser.parse_args(argv)
    from byax import __version__

    app = ROOT / 'dist' / f'{APP_NAME}.app' if args.no_freeze else build_app()
    if args.notarize and not args.sign:
        raise SystemExit('--notarize needs a signing identity (--sign or APPLE_SIGNING_IDENTITY).')
    if args.sign:
        run_all(codesign_commands(app, args.sign, keychain=args.keychain))
    if not args.dmg:
        print(app)
        return 0
    dmg = ROOT / 'dist' / dmg_name(__version__, signed=bool(args.sign), notarized=args.notarize)
    make_dmg(app, dmg, unsigned_notice=not args.sign)
    if args.sign:
        run_all([['codesign', '--force', '--timestamp', '--sign', args.sign, str(dmg)]])
    if args.notarize:
        try:
            credentials = {
                'apple_id': os.environ['APPLE_ID'],
                'team_id': os.environ['APPLE_TEAM_ID'],
                'password': os.environ['APPLE_APP_PASSWORD'],
            }
        except KeyError as exc:
            raise SystemExit(f'--notarize needs {exc.args[0]} in the environment.') from exc
        run_all(notarize_commands(dmg, **credentials))
    print(dmg)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
