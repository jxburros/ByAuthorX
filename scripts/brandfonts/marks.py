"""Accent marks and the table of accented letters."""

from __future__ import annotations


import shapely
from shapely.ops import unary_union

from .geom import Nib, arc, line, spline, sweep, Stroke

# (codepoint, glyph name, base glyph name, mark)
ACCENTED = [
    (0xC0, 'Agrave', 'A', 'grave'),
    (0xC1, 'Aacute', 'A', 'acute'),
    (0xC2, 'Acircumflex', 'A', 'circumflex'),
    (0xC3, 'Atilde', 'A', 'tilde'),
    (0xC4, 'Adieresis', 'A', 'dieresis'),
    (0xC5, 'Aring', 'A', 'ring'),
    (0xC7, 'Ccedilla', 'C', 'cedilla'),
    (0xC8, 'Egrave', 'E', 'grave'),
    (0xC9, 'Eacute', 'E', 'acute'),
    (0xCA, 'Ecircumflex', 'E', 'circumflex'),
    (0xCB, 'Edieresis', 'E', 'dieresis'),
    (0xCC, 'Igrave', 'I', 'grave'),
    (0xCD, 'Iacute', 'I', 'acute'),
    (0xCE, 'Icircumflex', 'I', 'circumflex'),
    (0xCF, 'Idieresis', 'I', 'dieresis'),
    (0xD1, 'Ntilde', 'N', 'tilde'),
    (0xD2, 'Ograve', 'O', 'grave'),
    (0xD3, 'Oacute', 'O', 'acute'),
    (0xD4, 'Ocircumflex', 'O', 'circumflex'),
    (0xD5, 'Otilde', 'O', 'tilde'),
    (0xD6, 'Odieresis', 'O', 'dieresis'),
    (0xD9, 'Ugrave', 'U', 'grave'),
    (0xDA, 'Uacute', 'U', 'acute'),
    (0xDB, 'Ucircumflex', 'U', 'circumflex'),
    (0xDC, 'Udieresis', 'U', 'dieresis'),
    (0xDD, 'Yacute', 'Y', 'acute'),
    (0xE0, 'agrave', 'a', 'grave'),
    (0xE1, 'aacute', 'a', 'acute'),
    (0xE2, 'acircumflex', 'a', 'circumflex'),
    (0xE3, 'atilde', 'a', 'tilde'),
    (0xE4, 'adieresis', 'a', 'dieresis'),
    (0xE5, 'aring', 'a', 'ring'),
    (0xE7, 'ccedilla', 'c', 'cedilla'),
    (0xE8, 'egrave', 'e', 'grave'),
    (0xE9, 'eacute', 'e', 'acute'),
    (0xEA, 'ecircumflex', 'e', 'circumflex'),
    (0xEB, 'edieresis', 'e', 'dieresis'),
    (0xEC, 'igrave', 'dotlessi', 'grave'),
    (0xED, 'iacute', 'dotlessi', 'acute'),
    (0xEE, 'icircumflex', 'dotlessi', 'circumflex'),
    (0xEF, 'idieresis', 'dotlessi', 'dieresis'),
    (0xF1, 'ntilde', 'n', 'tilde'),
    (0xF2, 'ograve', 'o', 'grave'),
    (0xF3, 'oacute', 'o', 'acute'),
    (0xF4, 'ocircumflex', 'o', 'circumflex'),
    (0xF5, 'otilde', 'o', 'tilde'),
    (0xF6, 'odieresis', 'o', 'dieresis'),
    (0xF9, 'ugrave', 'u', 'grave'),
    (0xFA, 'uacute', 'u', 'acute'),
    (0xFB, 'ucircumflex', 'u', 'circumflex'),
    (0xFC, 'udieresis', 'u', 'dieresis'),
    (0xFD, 'yacute', 'y', 'acute'),
    (0xFF, 'ydieresis', 'y', 'dieresis'),
    (0x178, 'Ydieresis', 'Y', 'dieresis'),
    # Central European and Turkish letters that need only the marks above
    (0x106, 'Cacute', 'C', 'acute'),
    (0x107, 'cacute', 'c', 'acute'),
    (0x10C, 'Ccaron', 'C', 'caron'),
    (0x10D, 'ccaron', 'c', 'caron'),
    (0x11A, 'Ecaron', 'E', 'caron'),
    (0x11B, 'ecaron', 'e', 'caron'),
    (0x143, 'Nacute', 'N', 'acute'),
    (0x144, 'nacute', 'n', 'acute'),
    (0x147, 'Ncaron', 'N', 'caron'),
    (0x148, 'ncaron', 'n', 'caron'),
    (0x158, 'Rcaron', 'R', 'caron'),
    (0x159, 'rcaron', 'r', 'caron'),
    (0x15A, 'Sacute', 'S', 'acute'),
    (0x15B, 'sacute', 's', 'acute'),
    (0x160, 'Scaron', 'S', 'caron'),
    (0x161, 'scaron', 's', 'caron'),
    (0x179, 'Zacute', 'Z', 'acute'),
    (0x17A, 'zacute', 'z', 'acute'),
    (0x17B, 'Zdotaccent', 'Z', 'dotaccent'),
    (0x17C, 'zdotaccent', 'z', 'dotaccent'),
    (0x17D, 'Zcaron', 'Z', 'caron'),
    (0x17E, 'zcaron', 'z', 'caron'),
    (0x15E, 'Scedilla', 'S', 'cedilla'),
    (0x15F, 'scedilla', 's', 'cedilla'),
    (0x11E, 'Gbreve', 'G', 'breve'),
    (0x11F, 'gbreve', 'g', 'breve'),
    (0x16E, 'Uring', 'U', 'ring'),
    (0x16F, 'uring', 'u', 'ring'),
    (0x150, 'Ohungarumlaut', 'O', 'hungarumlaut'),
    (0x151, 'ohungarumlaut', 'o', 'hungarumlaut'),
    (0x170, 'Uhungarumlaut', 'U', 'hungarumlaut'),
    (0x171, 'uhungarumlaut', 'u', 'hungarumlaut'),
    (0x102, 'Abreve', 'A', 'breve'),
    (0x103, 'abreve', 'a', 'breve'),
    (0x100, 'Amacron', 'A', 'macron'),
    (0x101, 'amacron', 'a', 'macron'),
    (0x112, 'Emacron', 'E', 'macron'),
    (0x113, 'emacron', 'e', 'macron'),
    (0x12A, 'Imacron', 'I', 'macron'),
    (0x12B, 'imacron', 'dotlessi', 'macron'),
    (0x14C, 'Omacron', 'O', 'macron'),
    (0x14D, 'omacron', 'o', 'macron'),
    (0x16A, 'Umacron', 'U', 'macron'),
    (0x16B, 'umacron', 'u', 'macron'),
    (0x130, 'Idotaccent', 'I', 'dotaccent'),
]

MARK_NAMES = (
    'acute',
    'grave',
    'circumflex',
    'tilde',
    'dieresis',
    'ring',
    'caron',
    'macron',
    'breve',
    'dotaccent',
    'hungarumlaut',
    'cedilla',
)


def _sweep(P, pts, nib=None, taper=(1.0, 1.0)):
    st = P.extras.get('stitch')
    if st:
        from .geom import resample, stitch_x

        half = st['half'] * 0.78
        pts = resample(pts, st['pitch'] * 0.78, 0)
        return unary_union([stitch_x(p, half, st['arm'] * 0.78) for p in pts])
    return sweep(Stroke(pts, nib=nib, taper=taper), nib or P.nib)


def _dots(P, centres, r):
    st = P.extras.get('stitch')
    if st:
        from .geom import stitch_x

        return unary_union([stitch_x(c, st['half'] * 0.78, st['arm'] * 0.78) for c in centres])
    return unary_union([shapely.Point(*c).buffer(r, quad_segs=10) for c in centres])


def mark_geometry(P, mark, cx, base_y, upper):
    """The mark, centred on cx with its lowest point near base_y (its top for cedilla)."""
    h = 0.22 * P.xh
    w = 0.20 * P.xh
    thin = Nib.ellipse(max(P.hv * 0.78, 6), max(P.hb * 0.9, 6))
    d = P.dot * 0.95
    y0 = base_y
    if mark == 'acute':
        return _sweep(P, line((cx - 0.4 * w, y0 + 0.2 * h), (cx + 0.5 * w, y0 + h)), thin, (0.9, 1.0))
    if mark == 'grave':
        return _sweep(P, line((cx - 0.5 * w, y0 + h), (cx + 0.4 * w, y0 + 0.2 * h)), thin, (1.0, 0.9))
    if mark == 'circumflex':
        return _sweep(
            P, line((cx - 0.75 * w, y0 + 0.2 * h), (cx, y0 + h), (cx + 0.75 * w, y0 + 0.2 * h)), thin
        )
    if mark == 'caron':
        return _sweep(P, line((cx - 0.75 * w, y0 + h), (cx, y0 + 0.2 * h), (cx + 0.75 * w, y0 + h)), thin)
    if mark == 'tilde':
        a = 0.28 * h
        pts = spline(
            [
                (cx - 0.85 * w, y0 + 0.45 * h - a * 0.4),
                (cx - 0.4 * w, y0 + 0.5 * h + a),
                (cx + 0.05 * w, y0 + 0.5 * h),
                (cx + 0.5 * w, y0 + 0.5 * h - a),
                (cx + 0.9 * w, y0 + 0.6 * h + a * 0.4),
            ]
        )
        return _sweep(P, pts, thin)
    if mark == 'dieresis':
        return _dots(
            P,
            [
                (cx - 0.55 * w - (0.35 * w if P.extras.get('stitch') else 0), y0 + d),
                (cx + 0.55 * w + (0.35 * w if P.extras.get('stitch') else 0), y0 + d),
            ],
            d,
        )
    if mark == 'dotaccent':
        return _dots(P, [(cx, y0 + d)], d)
    if mark == 'ring':
        r = 0.55 * h
        return _sweep(
            P, arc(cx, y0 + r + 4, r, r, 0, 360), Nib.ellipse(max(P.hv * 0.6, 5), max(P.hb * 0.75, 5))
        )
    if mark == 'macron':
        return _sweep(P, line((cx - 0.85 * w, y0 + 0.55 * h), (cx + 0.85 * w, y0 + 0.55 * h)), thin)
    if mark == 'breve':
        return _sweep(P, arc(cx, y0 + 0.95 * h, 0.8 * w, 0.62 * h, 215, 325), thin)
    if mark == 'hungarumlaut':
        a = _sweep(P, line((cx - 0.75 * w, y0 + 0.2 * h), (cx - 0.1 * w, y0 + h)), thin, (0.9, 1.0))
        b = _sweep(P, line((cx + 0.15 * w, y0 + 0.2 * h), (cx + 0.8 * w, y0 + h)), thin, (0.9, 1.0))
        return a.union(b)
    if mark == 'cedilla':
        pts = spline(
            [
                (cx - 0.1 * w, y0),
                (cx + 0.05 * w, y0 - 0.45 * h),
                (cx + 0.55 * w, y0 - 0.65 * h),
                (cx + 0.15 * w, y0 - 1.1 * h),
                (cx - 0.55 * w, y0 - 1.0 * h),
            ]
        )
        return _sweep(P, pts, thin)
    raise KeyError(mark)


def accent_for(P, base, mark, gap=None):
    """A function(placed_base_item) -> mark geometry, positioned over (or under) the placed base glyph."""
    upper = base[:1].isupper() or base in ('Aring',)

    def place(item):
        geom = item['geom']
        if P.extras.get('stitch'):
            return stitch_mark(P, mark, item, upper)
        xmin, ymin, xmax, ymax = geom.bounds
        top = P.cap if upper else P.xh
        cx = (xmin + xmax) / 2
        if base == 'dotlessi':
            cx = (xmin + xmax) / 2
        if mark == 'cedilla':
            return mark_geometry(P, mark, cx, 0 + P.extras.get('cedilla_y', 0.0), upper)
        g = P.extras.get('accent_gap', 0.07 * P.xh)
        # lowercase tall bowls (b d h k l) never take marks here; round letters overshoot a little
        y = max(ymax, top) + g if upper else (top + g)
        if mark in ('dieresis', 'dotaccent'):
            y -= P.dot * 0.0
        return mark_geometry(P, mark, cx, y, upper)

    return place


# Accents for the cross-stitch family, as (column, row) offsets from the anchor cell.
STITCH_MARKS = {
    'acute': [(0, 0), (1, 1)],
    'grave': [(0, 1), (1, 0)],
    'circumflex': [(-1, 0), (0, 1), (1, 0)],
    'caron': [(-1, 1), (0, 0), (1, 1)],
    'tilde': [(-1, 0), (0, 1), (1, 1), (2, 0)],
    'dieresis': [(-1, 0), (1, 0)],
    'dotaccent': [(0, 0)],
    'ring': [(0, 0), (1, 0), (0, 1), (1, 1)],
    'macron': [(-1, 0), (0, 0), (1, 0)],
    'breve': [(-1, 1), (0, 0), (1, 0), (2, 1)],
    'hungarumlaut': [(-2, 0), (-1, 1), (1, 0), (2, 1)],
    'cedilla': [(0, -1), (1, -2)],
}


def stitch_mark(P, mark, item, upper):
    """Cell-aligned accent stitches over (or under) a placed base glyph of the cross-stitch family."""
    from .geom import stitch_x

    st = P.extras['stitch']
    pitch = st['pitch']
    xmin, _, xmax, ymax = item['geom'].bounds
    cx = (xmin + xmax) / 2
    col = int((cx - xmin) // pitch)
    if mark == 'cedilla':
        row = 0
    else:
        top_row = int(round((P.cap if upper else P.xh) / pitch))
        row = top_row + 1
    out = []
    for dc, dr in STITCH_MARKS[mark]:
        x = xmin + (col + dc + 0.5) * pitch
        y = (row + dr + 0.5) * pitch if mark != 'cedilla' else (dr + 0.5) * pitch
        out.append(stitch_x((x, y), st['half'], st['arm']))
    return unary_union(out)
