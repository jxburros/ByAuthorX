"""Assemble a native Ubuntu 24.04 x64 package from the frozen runtime.

--variant lean packages dist/ByAuthorX (no model files) as ``byauthorx-lean``; --variant standard packages
dist/ByAuthorX-standard (bundled on-device models, see scripts/build.py) as ``byauthorx``.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--variant', choices=['lean', 'standard'], default='lean')
variant = parser.parse_args().variant
os_release = platform.freedesktop_os_release()
if os_release.get('ID') != 'ubuntu' or os_release.get('VERSION_ID') != '24.04':
    raise SystemExit('Build the Ubuntu 24.04 package on Ubuntu 24.04 to preserve the required glibc ABI.')
if platform.machine() not in ('x86_64', 'AMD64'):
    raise SystemExit('This package recipe targets x64.')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from byax import __version__  # noqa: E402

package = 'byauthorx' if variant == 'standard' else 'byauthorx-lean'
frozen = ROOT / 'dist' / ('ByAuthorX-standard' if variant == 'standard' else 'ByAuthorX')
if not frozen.exists():
    raise SystemExit(f'{frozen} is missing; build it first with scripts/build.py (--variant {variant}).')
root = ROOT / 'build' / ('deb' if variant == 'standard' else 'deb-lean')
if root.exists():
    shutil.rmtree(root)
(root / 'DEBIAN').mkdir(parents=True)
(root / 'DEBIAN' / 'control').write_text(f"""Package: {package}
Version: {__version__}
Section: editors
Priority: optional
Architecture: amd64
Maintainer: ByAuthorX contributors
Conflicts: {'byauthorx-lean' if variant == 'standard' else 'byauthorx'}
Replaces: {'byauthorx-lean' if variant == 'standard' else 'byauthorx'}
Depends: libc6 (>= 2.39), libgl1, libegl1, libxkbcommon0, libdbus-1-3, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1, libxcb-render-util0, libxkbcommon-x11-0, libasound2t64, libfontconfig1, libfreetype6
Description: Native programmable word processor
 ByAuthorX desktop editor, headless CLI, and MCP server.
 {'Includes the on-device search models.' if variant == 'standard' else 'Lean build: on-device search models are downloaded only on request.'}
""")
shutil.copytree(frozen, root / 'opt' / 'byauthorx')
for dest, source in [
    ('usr/share/applications/byauthorx.desktop', 'byauthorx.desktop'),
    ('usr/share/mime/packages/byauthorx.xml', 'byauthorx.xml'),
    ('usr/share/icons/hicolor/scalable/apps/byauthorx.svg', 'byauthorx.svg'),
]:
    path = root / dest
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'packaging' / source, path)
for size in (16, 24, 32, 48, 64, 96, 128, 256, 512):
    path = root / 'usr' / 'share' / 'icons' / 'hicolor' / f'{size}x{size}' / 'apps' / 'byauthorx.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'src' / 'byax' / 'resources' / 'brand' / f'app-{size}.png', path)
(root / 'usr' / 'bin').mkdir(parents=True)
for name in ['byax', 'byax-mcp']:
    os.symlink('/opt/byauthorx/' + name, root / 'usr' / 'bin' / name)
os.symlink('/opt/byauthorx/ByAuthorX', root / 'usr' / 'bin' / 'byauthorx')
subprocess.run(
    [
        'dpkg-deb',
        '--root-owner-group',
        '--build',
        str(root),
        str(ROOT / 'dist' / f'{package}_{__version__}_amd64.deb'),
    ],
    check=True,
)
