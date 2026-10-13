"""Permitted, bundled baseline fonts; other installed fonts remain selectable."""
from pathlib import Path

FONT_ROOT = Path(__file__).parent / 'resources' / 'fonts'
_REGISTERED = False


def register_bundled_fonts():
    global _REGISTERED
    if _REGISTERED:
        return
    from PySide6.QtGui import QFontDatabase, QGuiApplication
    if QGuiApplication.instance() is None:
        raise RuntimeError('Create the Qt application before registering fonts.')
    for path in sorted(FONT_ROOT.glob('*.ttf')):
        if QFontDatabase.addApplicationFont(str(path)) == -1:
            raise RuntimeError(f'Cannot register bundled font: {path.name}')
    _REGISTERED = True


def bundled_font_path(family, bold=False, italic=False):
    brand_fonts = {
        'manrope': 'Manrope-600.ttf' if bold else 'Manrope-400.ttf',
        'im fell english': 'IM-Fell-English-400.ttf',
        'source serif 4': 'Source-Serif-4-400.ttf',
    }
    if family.casefold() in brand_fonts:
        # Only supplied faces are fingerprinted; synthetic styles have no font file.
        if italic or (bold and family.casefold() != 'manrope'):
            return None
        return FONT_ROOT / brand_fonts[family.casefold()]
    families = {'DejaVu Sans': ('DejaVuSans', 'Oblique'), 'DejaVu Sans Mono': ('DejaVuSansMono', 'Oblique'),
                'DejaVu Serif': ('DejaVuSerif', 'Italic')}
    if family not in families:
        return None
    name, italic_name = families[family]
    suffix = ('Bold' if bold else '') + (italic_name if italic else '')
    path = FONT_ROOT / (name + ('-' + suffix if suffix else '') + '.ttf')
    return path if path.is_file() else None
