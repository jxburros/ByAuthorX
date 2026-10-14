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
        # The six ByAuthorX brand fonts (assets/brand-fonts), each a single upright style.
        'irongall': 'Irongall-Regular.ttf',
        'scholium': 'Scholium-Regular.ttf',
        'tenuis': 'Tenuis-Regular.ttf',
        'brobdingnag': 'Brobdingnag-Regular.ttf',
        'onionskin': 'Onionskin-Regular.ttf',
        'decussate': 'Decussate-Regular.ttf',
    }
    if family.casefold() in brand_fonts:
        # Only supplied faces are fingerprinted; synthetic styles have no font file.
        if italic or (bold and family.casefold() != 'manrope'):
            return None
        return FONT_ROOT / brand_fonts[family.casefold()]
    families = {
        'DejaVu Sans': ('DejaVuSans', 'Oblique'),
        'DejaVu Sans Mono': ('DejaVuSansMono', 'Oblique'),
        'DejaVu Serif': ('DejaVuSerif', 'Italic'),
    }
    if family not in families:
        return None
    name, italic_name = families[family]
    suffix = ('Bold' if bold else '') + (italic_name if italic else '')
    path = FONT_ROOT / (name + ('-' + suffix if suffix else '') + '.ttf')
    return path if path.is_file() else None


_TABLES = ('head', 'cmap', 'name', 'OS/2', 'hhea', 'hmtx', 'maxp', 'post', 'glyf', 'loca', 'CFF ')
MAX_FONT_BYTES = 64 * 1024 * 1024


def font_file_info(path):
    """Read identity and declared OS/2 embedding permissions from a TrueType/OpenType file."""
    import hashlib
    import struct

    from .errors import ByaxError

    path = Path(path)
    if not path.is_file():
        raise ByaxError('missing_dependency', 'Font file is unavailable', target=str(path))
    if path.stat().st_size > MAX_FONT_BYTES:
        raise ByaxError('limit_exceeded', 'Font file exceeds 64 MiB', target=str(path))
    raw = path.read_bytes()
    offset = struct.unpack('>I', raw[12:16])[0] if raw[:4] == b'ttcf' and len(raw) >= 16 else 0
    tables = {}
    try:
        count = struct.unpack('>H', raw[offset + 4 : offset + 6])[0]
        for index in range(min(count, 512)):
            record = offset + 12 + index * 16
            tag, _checksum, start, length = struct.unpack('>4sIII', raw[record : record + 16])
            tables[tag] = (start, length)
    except struct.error as exc:
        raise ByaxError('invalid_input', 'Font table directory is malformed', target=str(path)) from exc
    fs_type = None
    if b'OS/2' in tables and tables[b'OS/2'][1] >= 10:
        start = tables[b'OS/2'][0]
        fs_type = struct.unpack('>H', raw[start + 8 : start + 10])[0]
    names = {}
    if b'name' in tables:
        start = tables[b'name'][0]
        try:
            _format, total, storage = struct.unpack('>HHH', raw[start : start + 6])
            for index in range(min(total, 2048)):
                platform, _encoding, _language, name_id, size, at = struct.unpack(
                    '>HHHHHH', raw[start + 6 + index * 12 : start + 18 + index * 12]
                )
                if name_id in (1, 2, 4, 16, 17) and name_id not in names:
                    value = raw[start + storage + at : start + storage + at + size]
                    names[name_id] = value.decode('utf-16-be' if platform in (0, 3) else 'latin-1', 'replace')
        except struct.error:
            pass
    if fs_type is None:
        embedding = 'undeclared'
    elif fs_type & 0x0002 and not fs_type & 0x000C:
        embedding = 'restricted'
    elif fs_type & 0x0008:
        embedding = 'editable'
    elif fs_type & 0x0004:
        embedding = 'preview_print'
    else:
        embedding = 'installable'
    return {
        'path': str(path.resolve()),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'family': names.get(16) or names.get(1),
        'style': names.get(17) or names.get(2),
        'full_name': names.get(4),
        'fs_type': fs_type,
        'embedding': embedding,
        'embeddable': embedding in ('installable', 'editable'),
        'subsetting_allowed': fs_type is None or not fs_type & 0x0100,
        'bitmap_only': bool(fs_type and fs_type & 0x0200),
    }


def resolve_font_file(family, bold=False, italic=False):
    """Resolve an installed family/style to its font file where the platform exposes one."""
    import os
    import shutil
    import subprocess

    path = bundled_font_path(family, bold, italic)
    if path is None and os.name == 'posix' and shutil.which('fc-match'):
        pattern = f'{family}:weight={"bold" if bold else "regular"}:slant={"italic" if italic else "roman"}'
        result = subprocess.run(
            ['fc-match', '--format=%{file}', pattern], capture_output=True, timeout=5, check=False, text=True
        )
        found = Path(result.stdout.strip())
        if result.returncode == 0 and found.is_file():
            path = found
    elif path is None and os.name == 'nt':
        import winreg

        style_name = family + (' Bold' if bold else '') + (' Italic' if italic else '')
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts') as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, file, _ = winreg.EnumValue(key, index)
                        if name.removesuffix(' (TrueType)').casefold() == style_name.casefold():
                            candidate = Path(file)
                            if not candidate.is_absolute():
                                candidate = (
                                    Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / candidate
                                )
                            if candidate.is_file():
                                return candidate
            except OSError:
                continue
    return path


def inspect_font(family, bold=False, italic=False):
    """Selected family identity, actual substitution and declared embedding permission."""
    from PySide6.QtGui import QFont, QFontInfo

    from .layout import ensure_app

    ensure_app()
    font = QFont(family, 11)
    font.setBold(bold)
    font.setItalic(italic)
    resolved = QFontInfo(font).family()
    substituted = resolved.casefold() != family.casefold()
    result = {
        'requested': family,
        'resolved': resolved,
        'bold': bold,
        'italic': italic,
        'substituted': substituted,
    }
    path = resolve_font_file(resolved, bold, italic)
    if path is None:
        return {
            **result,
            'status': 'unavailable' if substituted else 'file_unresolved',
            'embedding': 'unknown',
            'embeddable': False,
        }
    info = font_file_info(path)
    info['file_family'] = info.pop('family')
    return {**result, **info, 'status': 'substituted' if substituted else 'available'}


def embed_font(doc, family, bold=False, italic=False):
    """Embed a resolved font into a copy of the document only when its declared permission allows it."""
    import base64
    import copy

    from .errors import ByaxError

    info = inspect_font(family, bold, italic)
    public = {k: v for k, v in info.items() if k != 'path'}
    if info['status'] != 'available':
        raise ByaxError(
            'missing_dependency', 'Selected font is unavailable or substituted on this system', font=public
        )
    if not info['embeddable']:
        raise ByaxError(
            'unsupported_feature',
            f'Font embedding is not permitted by its declared license ({info["embedding"]})',
            font=public,
        )
    raw = Path(info['path']).read_bytes()
    updated = copy.deepcopy(doc)
    updated['assets'][info['sha256']] = {
        'data': base64.b64encode(raw).decode('ascii'),
        'mime': 'font/otf' if raw[:4] == b'OTTO' else 'font/ttf',
        'name': Path(info['path']).name,
    }
    face = ('bold' if bold else '') + ('italic' if italic else '') or 'regular'
    updated['layout'].setdefault('fonts', {}).setdefault(family, {})[face] = {
        'font_asset': info['sha256'],
        'embedding': info['embedding'],
        'subsetting_allowed': info['subsetting_allowed'],
    }
    return updated, public


def _clusters(text):
    import regex

    return [cluster for cluster in regex.findall(r'\X', text) if not cluster.isspace()]


def _codepoints(cluster):
    return ' '.join(f'U+{ord(char):04X}' for char in cluster)


def _text_groups(doc):
    styles = doc.get('styles', {})
    body = styles.get('body', {}) if isinstance(styles.get('body'), dict) else {}
    groups = {}
    for block in doc.get('blocks', []):
        style = styles.get(block.get('style'), {})
        style = style if isinstance(style, dict) else {}
        family = style.get('font_family') or body.get('font_family') or 'DejaVu Sans'
        texts = [block.get('text', '')] + [
            cell.get('text', '') for row in block.get('rows', []) for cell in row.get('cells', [])
        ]
        key = (family, bool(style.get('bold') or block.get('type') == 'heading'), bool(style.get('italic')))
        groups.setdefault(key, set()).update(cluster for text in texts for cluster in _clusters(text))
        for mark in block.get('marks', []):
            if mark.get('type') == 'font_family' and isinstance(mark.get('value'), str):
                text = block.get('text', '')[mark.get('start', 0) : mark.get('end', 0)]
                groups.setdefault((mark['value'], False, False), set()).update(_clusters(text))
    return groups


def glyph_fallbacks(doc, *, sample_limit=32, max_clusters=5000):
    """Fonts that actually shape the document's characters, including glyph-level fallbacks.

    Every font in the shaping result is hashed from its raw tables (platform independent)
    and from its file where resolvable, so fallback substitution drift is detectable.
    """
    import hashlib

    from PySide6.QtGui import QFont, QTextLayout

    from .layout import ensure_app

    ensure_app()
    fonts, missing, truncated = {}, {}, False
    for (family, bold, italic), characters in sorted(_text_groups(doc).items()):
        if not characters:
            continue
        font = QFont(family, 11)
        font.setBold(bold)
        font.setItalic(italic)
        covered = set()
        truncated = truncated or len(characters) > max_clusters
        characters = set(sorted(characters)[:max_clusters])
        # Shape clusters separately: Qt's run string indexes are unreliable across fallback runs.
        for cluster in sorted(characters):
            layout = QTextLayout(cluster, font)
            layout.beginLayout()
            layout.createLine().setLineWidth(1e7)
            layout.endLayout()
            length = len(cluster.encode('utf-16-le')) // 2
            for run in layout.glyphRuns(0, length, QTextLayout.GlyphRunRetrievalFlag.RetrieveAll):
                raw = run.rawFont()
                key = (raw.familyName(), raw.styleName())
                if key not in fonts:
                    digest = hashlib.sha256()
                    for tag in _TABLES:
                        digest.update(tag.encode() + bytes(raw.fontTable(tag)))
                    slanted = any(word in key[1].lower() for word in ('italic', 'oblique'))
                    path = resolve_font_file(key[0], raw.weight() >= 600, slanted)
                    fonts[key] = {
                        'tables_sha256': digest.hexdigest(),
                        'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path else None,
                        'requested': set(),
                        'characters': set(),
                    }
                if any(run.glyphIndexes()):
                    fonts[key]['requested'].add(family)
                    fonts[key]['characters'].add(cluster)
                    covered.add(cluster)
        for cluster in characters - covered:
            missing.setdefault(cluster, set()).add(family)
    result = []
    for (name, style_name), entry in sorted(fonts.items()):
        chars = sorted(entry['characters'])
        result.append(
            {
                'family': name,
                'style': style_name,
                'tables_sha256': entry['tables_sha256'],
                'file_sha256': entry['file_sha256'],
                'requested_families': sorted(entry['requested']),
                'fallback': name not in entry['requested'],
                'character_count': len(chars),
                'characters_sha256': hashlib.sha256(''.join(chars).encode()).hexdigest(),
                'sample': [_codepoints(c) for c in chars[:sample_limit]],
            }
        )
    return {
        'fonts': [entry for entry in result if entry['character_count']],
        'truncated': truncated,
        'missing_glyphs': [
            {'codepoints': _codepoints(c), 'requested_families': sorted(families)}
            for c, families in sorted(missing.items())
        ],
    }
