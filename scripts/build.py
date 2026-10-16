"""Build three self-contained executables and merge their shared runtime.

Two variants (docs/packaging.md):

* ``lean`` (the default) is the frozen application with the on-device runtime but no model files.
  Models are fetched later, only on the user's explicit action (``byax models`` or the GUI button).
* ``standard`` is the lean tree plus the catalogue models marked ``bundled``, downloaded from the
  ``models-v1`` mirror release and verified against ``SHA256SUMS.txt`` and the pinned SHA-256 values.
  It refuses to build until the mirror is populated (``--models-status`` reports it).
"""

import argparse
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

LEAN_DIR = ROOT / 'dist' / 'ByAuthorX'
STANDARD_DIR = ROOT / 'dist' / 'ByAuthorX-standard'


def runtime_options():
    """PyInstaller options for the optional on-device runtime, when the extra is installed."""
    options = []
    for module in ('onnxruntime', 'tokenizers'):
        if importlib.util.find_spec(module):
            options += ['--collect-submodules', module, '--collect-binaries', module]
    return options


def models_status():
    from byax import local_models

    catalogue = local_models.load_catalogue()
    populated = bool(catalogue['mirror'].get('populated'))
    names = [m['name'] for m in catalogue['models'] if m.get('bundled')]
    size = sum(m['size_bytes'] for m in catalogue['models'] if m.get('bundled'))
    return populated, names, size, catalogue['mirror']


def print_models_status():
    populated, names, size, mirror = models_status()
    print(f'populated={str(populated).lower()}')
    print(f'bundled_models={",".join(names)}')
    print(f'bundled_bytes={size}')
    print(f'release={mirror.get("release_tag")}')
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as handle:
            handle.write(f'models_populated={str(populated).lower()}\n')
    return 0


def print_models_help():
    from byax import local_models

    catalogue = local_models.load_catalogue()
    mirror = catalogue['mirror']
    print(f'Create the {mirror["release_tag"]} release in {mirror["repository"]} (maintainer step):')
    print('  1. Download every file listed in src/byax/resources/models.json from its upstream "url".')
    print('  2. Verify each SHA-256 (python scripts/build_model_catalogue.py --check).')
    print('  3. Upload each file under the asset name in "mirror_asset" plus SHA256SUMS.txt:')
    for model in catalogue['models']:
        for item in model['files']:
            print(f'       {item["mirror_asset"]}  {item["size"]} bytes  {item["sha256"]}')
    print('  4. Set "populated": true in models.json, commit, then build with --variant standard.')
    return 0


def write_checksums(models_dir):
    lines = []
    for path in sorted(models_dir.rglob('*')):
        if path.is_file() and path.name != 'SHA256SUMS.txt':
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f'{digest}  {path.relative_to(models_dir).as_posix()}')
    (models_dir / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def build_standard():
    """Copy the lean tree and add the bundled models from the mirror, verified. Fails clearly if unpopulated."""
    from byax import local_models
    from byax.errors import ByaxError

    if not LEAN_DIR.exists():
        raise SystemExit(f'Build the lean variant first (python scripts/build.py): {LEAN_DIR} is missing.')
    try:
        stage = ROOT / 'build' / 'models'
        if stage.exists():
            shutil.rmtree(stage)
        results = local_models.bundle_models(stage)
    except ByaxError as exc:
        raise SystemExit(f'Standard variant not built: {exc.message}') from exc
    if STANDARD_DIR.exists():
        shutil.rmtree(STANDARD_DIR)
    shutil.copytree(LEAN_DIR, STANDARD_DIR)
    target = STANDARD_DIR / 'models'
    shutil.copytree(stage, target)
    licences = ROOT / 'src' / 'byax' / 'resources' / 'licences'
    if licences.exists():
        shutil.copytree(licences, target / 'licences', dirs_exist_ok=True)
    write_checksums(target)
    print(f'Bundled {len(results)} models into {target}')
    return STANDARD_DIR


def build_lean(bundle_only):
    output = LEAN_DIR
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
            *runtime_options(),
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
            # Optional heavy extras never ride along in the frozen app (docs/packaging.md).
            '--exclude-module',
            'docling',
            '--exclude-module',
            'torch',
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
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle-only', action='store_true')
    parser.add_argument(
        '--variant',
        choices=['lean', 'standard'],
        default='lean',
        help='lean: no model files (default); standard: add the bundled catalogue models from the mirror',
    )
    parser.add_argument('--models-status', action='store_true', help='Print whether the mirror is populated')
    parser.add_argument(
        '--models-help', action='store_true', help='Print the maintainer steps for the mirror'
    )
    args = parser.parse_args()
    if args.models_status:
        raise SystemExit(print_models_status())
    if args.models_help:
        raise SystemExit(print_models_help())
    if args.variant == 'standard':
        print(build_standard())
        return
    output = build_lean(args.bundle_only)
    if sys.platform.startswith('linux') and not args.bundle_only:
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'build_deb.py'), '--variant', 'lean'], check=True
        )
    print(output)


if __name__ == '__main__':
    main()
