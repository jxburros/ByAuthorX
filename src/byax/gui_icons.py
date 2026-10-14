"""Interface icons: the logo's hand-inked set, sliced from Vixl-baked atlases.

Icons are drawn once on a 96-unit grid and rendered from vectors at 16, 24, 32 and 48 px, so every
logical size has an exact pixmap on ordinary and 2x displays. They sit beside a text label or carry an
accessible name; colour never carries meaning alone.
"""

from __future__ import annotations

import json
from functools import cache, lru_cache

from PySide6.QtCore import QRect
from PySide6.QtGui import QIcon, QPixmap

from .brand import BRAND_ROOT
from .gui_art import art_theme


@lru_cache(maxsize=1)
def _manifest():
    return json.loads((BRAND_ROOT / 'ui' / 'icons.json').read_text(encoding='utf-8'))


@cache
def _atlas(theme, size):
    path = BRAND_ROOT / 'ui' / _manifest()['files'][f'{theme}-{size}']
    atlas = QPixmap(str(path))
    if atlas.isNull():
        raise RuntimeError(f'Missing icon atlas: {path.name}')
    return atlas


def icon_names():
    return tuple(_manifest()['names'])


def icon_label(name):
    return _manifest()['labels'][name]


@cache
def _icon(name, theme):
    manifest = _manifest()
    try:
        index = manifest['names'].index(name)
    except ValueError:
        raise KeyError(f'Unknown interface icon: {name}') from None
    columns = manifest['columns']
    icon = QIcon()
    for size in manifest['sizes']:
        cell = QRect((index % columns) * size, (index // columns) * size, size, size)
        icon.addPixmap(_atlas(theme, size).copy(cell))
    return icon


def themed_icon(name, widget=None):
    """The named icon in the variant that matches the effective window palette."""
    return _icon(name, art_theme(widget))
