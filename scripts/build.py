"""Build three self-contained executables and merge their shared runtime."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle-only', action='store_true')
    args = parser.parse_args()
    output = ROOT / 'dist' / 'ByAuthorX'
    stage = ROOT / 'build' / 'bundles'
    for name, entry, windowed in [
        ('ByAuthorX', 'gui_entry.py', True),
        ('byax', 'cli_entry.py', False),
        ('byax-mcp', 'mcp_entry.py', False),
    ]:
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
            cmd.extend(['--icon', str(ROOT / 'src' / 'byax' / 'resources' / 'brand' / 'byauthorx.ico')])
        cmd.append(str(ROOT / 'packaging' / entry))
        subprocess.run(cmd, cwd=ROOT, check=True)
        shutil.copytree(stage / name, output, dirs_exist_ok=True)
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
    if sys.platform.startswith('linux') and not args.bundle_only:
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_deb.py')], check=True)
    print(output)


if __name__ == '__main__':
    main()
