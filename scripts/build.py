"""Build three self-contained executables and merge their shared runtime."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pyinstaller_command(name, entry, *, windowed, stage, extra=()):
    """The PyInstaller invocation shared by every platform; ``extra`` adds platform flags."""
    cmd = [
        sys.executable,
        '-m',
        'PyInstaller',
        '--noconfirm',
        '--clean',
        '--onedir',
        '--name',
        name,
        '--distpath',
        str(stage),
        '--workpath',
        str(ROOT / 'build' / name),
        '--specpath',
        str(ROOT / 'build'),
        '--paths',
        str(ROOT / 'src'),
        '--collect-submodules',
        'byax',
        '--collect-data',
        'byax',
        '--collect-data',
        'spellchecker',
        '--collect-data',
        'citeproc',
        '--collect-data',
        'citeproc_styles',
        '--collect-submodules',
        'rapidfuzz',
        '--exclude-module',
        'PySide6.QtWebEngineCore',
        '--exclude-module',
        'PySide6.QtWebEngineWidgets',
        '--exclude-module',
        'PySide6.QtWebEngineQuick',
        '--exclude-module',
        'PySide6.QtQuick',
        '--exclude-module',
        'pytest',
    ]
    if windowed:
        cmd.append('--windowed')
    cmd.extend(extra)
    cmd.append(str(ROOT / 'packaging' / entry))
    return cmd


def copy_notices(output):
    """Licence, readme, documentation and dependency licence texts for redistribution."""
    for file in ['LICENSE', 'README.md']:
        shutil.copy2(ROOT / file, output / file)
    shutil.copytree(ROOT / 'docs', output / 'docs', dirs_exist_ok=True)
    # Preserve dependency license text for redistribution and local inspection.
    from importlib.metadata import distributions

    notices = output / 'third-party-licenses'
    notices.mkdir(exist_ok=True)
    for distribution in distributions():
        for file in distribution.files or []:
            if any(word in file.name.lower() for word in ['license', 'copying']):
                source = distribution.locate_file(file)
                if source.is_file():
                    dest = notices / distribution.metadata['Name'] / str(file).replace('/', '_')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle-only', action='store_true')
    args = parser.parse_args()
    if sys.platform == 'darwin':
        # One windowed app carries the GUI, the CLI and the MCP server; see scripts/build_macos.py.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build_macos

        print(build_macos.build_app())
        return
    output = ROOT / 'dist' / 'ByAuthorX'
    stage = ROOT / 'build' / 'bundles'
    for name, entry, windowed in [
        ('ByAuthorX', 'gui_entry.py', True),
        ('byax', 'cli_entry.py', False),
        ('byax-mcp', 'mcp_entry.py', False),
    ]:
        extra = (
            ['--icon', str(ROOT / 'src' / 'byax' / 'resources' / 'brand' / 'byauthorx.ico')]
            if windowed
            else []
        )
        subprocess.run(
            pyinstaller_command(name, entry, windowed=windowed, stage=stage, extra=extra),
            cwd=ROOT,
            check=True,
        )
        shutil.copytree(stage / name, output, dirs_exist_ok=True)
    copy_notices(output)
    if sys.platform.startswith('linux') and not args.bundle_only:
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_deb.py')], check=True)
    print(output)


if __name__ == '__main__':
    main()
