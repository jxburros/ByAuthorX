"""Compile a family's glyph geometry into a TrueType font with metrics, kerning and features."""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import math

from fontTools import agl
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable

from . import outline, spacing

UPM = 1000
# Fixed so that rebuilding is byte-for-byte reproducible.
TIMESTAMP = 1_791_936_000  # 2026-10-14T00:00:00Z

DEFAULT_LICENSE = (
    'Released under the MIT License, the same terms as the ByAuthorX repository. '
    'See LICENSE at the repository root.'
)
DEFAULT_LICENSE_URL = 'https://opensource.org/licenses/MIT'


@dataclass
class Metrics:
    xh: float
    cap: float
    asc: float
    desc: float
    ascent: int = 0  # hhea / typo / win ascent; 0 means derive from the glyph extremes
    descent: int = 0
    line_gap: int = 0


@dataclass
class Spec:
    key: str
    family: str
    style: str = 'Regular'
    legacy_family: str | None = None  # nameID 1 when it differs from the typographic family
    weight: int = 400
    width: int = 5
    description: str = ''
    metrics: Metrics = None
    nominal: dict = field(
        default_factory=lambda: dict(lower=70.0, upper=80.0, figure=70.0, punct=50.0, symbol=60.0)
    )
    depth: float = 60.0
    min_bearing: float = 6.0
    share: float = 1.0
    kern_strength: float = 0.5
    kern_limit: float = 140.0
    kern_min: int = 14
    kern_cap: float = 360.0
    space: float = 250.0
    mono_advance: float = 0.0  # fixed advance for every glyph (typewriter faces)
    cell: float = (
        0.0  # pixel-grid faces: every glyph gets one empty cell either side and a whole-cell advance
    )
    italic_angle: float = 0.0
    monospace: bool = False
    fea_extra: str = ''
    seed: int = 1
    license: str = DEFAULT_LICENSE
    license_url: str = DEFAULT_LICENSE_URL
    designer: str = 'ByAuthorX contributors'
    url: str = 'https://github.com/jxburros/ByAuthorX'
    version: str = '1.000'


@dataclass
class Compiled:
    spec: Spec
    glyphs: dict  # name -> dict(contours, advance, unicode, kind, geom)
    order: list
    cmap: dict
    kerning: dict
    font: TTFont = None


def ps_name(spec):
    return f'{spec.family}-{spec.style}'.replace(' ', '')


def glyph_name(cp):
    return agl.UV2AGL.get(cp) or (f'uni{cp:04X}' if cp <= 0xFFFF else f'u{cp:X}')


def _ttglyph(contours):
    pen = TTGlyphPen(None)
    for contour in contours:
        pts = list(contour)
        start = next((i for i, p in enumerate(pts) if p[2]), None)
        if start is None:
            pen.qCurveTo(*[(x, y) for x, y, _ in pts], None)
            pen.closePath()
            continue
        pts = pts[start:] + pts[:start]
        pen.moveTo(pts[0][:2])
        pending = []
        for x, y, on in pts[1:]:
            if on:
                if pending:
                    pen.qCurveTo(*pending, (x, y))
                    pending = []
                else:
                    pen.lineTo((x, y))
            else:
                pending.append((x, y))
        if pending:
            pen.qCurveTo(*pending, pts[0][:2])
        pen.closePath()
    return pen.glyph()


def _notdef(metrics):
    w, h = 520, int(metrics.cap)
    outer = [(60, 0, True), (60, h, True), (w - 60, h, True), (w - 60, 0, True)]
    inner = [(110, 50, True), (w - 110, 50, True), (w - 110, h - 50, True), (110, h - 50, True)]
    return [outer, inner], w + 0


def compile_family(spec: Spec, shapes: dict, extra_features: str = '') -> Compiled:
    """shapes: ordered mapping name -> dict(geom, unicode, kind) with geometry in font units (unpositioned).

    kind is one of lower, upper, figure, mark, fixed (positioned, advance given), accent (copy a base's
    spacing: pass `base`). Returns the compiled font and the data the masters need.
    """
    m = spec.metrics
    nominal = spec.nominal
    placed = {}
    for name, item in shapes.items():
        geom, kind = item['geom'], item.get('kind', 'lower')
        if item.get('advance') is not None and item.get('fixed'):
            placed[name] = dict(item, geom=geom, advance=item['advance'], lsb=0)
            continue
        if kind == 'accent':
            continue
        if geom.is_empty:
            placed[name] = dict(
                item, geom=geom, advance=spec.mono_advance or item.get('advance') or spec.space, lsb=0
            )
            continue
        zone_top = m.cap if kind in ('upper', 'figure') else m.xh
        zone = item.get('zone') or (0.0, zone_top)
        nom = nominal.get(kind, nominal['lower'])
        if kind in ('punct', 'symbol') or spec.cell:
            lsb = rsb = nom
        else:
            profile = spacing.Profile(geom, *zone)
            lsb, rsb = spacing.sidebearings(
                profile, nom, item.get('depth', spec.depth), spec.share, spec.min_bearing
            )
        lsb *= item.get('lsb_scale', 1.0)
        rsb *= item.get('rsb_scale', 1.0)
        lsb += item.get('lsb_add', 0.0)
        rsb += item.get('rsb_add', 0.0)
        xmin, _, xmax, _ = geom.bounds
        from shapely import affinity

        moved = affinity.translate(geom, xoff=lsb - xmin)
        adv = (xmax - xmin) + lsb + rsb
        if spec.cell:
            adv = max(spec.cell, round(((xmax - xmin) + lsb + rsb) / spec.cell) * spec.cell)
            moved = affinity.translate(geom, xoff=(adv - (xmax - xmin)) / 2 - xmin)
        elif spec.mono_advance:
            adv = spec.mono_advance
            room = adv - 2 * spec.min_bearing
            if xmax - xmin > room:
                geom = affinity.scale(geom, xfact=room / (xmax - xmin), yfact=1.0, origin=(xmin, 0))
                xmax = xmin + room
            moved = affinity.translate(geom, xoff=(adv - (xmax - xmin)) / 2 - xmin)
        elif item.get('advance') is not None:
            adv = item['advance']
            moved = affinity.translate(geom, xoff=(adv - (xmax - xmin)) / 2 - xmin)
        placed[name] = dict(item, geom=moved, advance=adv)
    for name, item in shapes.items():
        if item.get('kind') != 'accent':
            continue
        base = placed[item['base']]
        from shapely import affinity
        from shapely.ops import unary_union

        mark = item['mark_geom'](base)
        geom = unary_union([base['geom'], mark])
        placed[name] = dict(item, geom=geom, advance=base['advance'], kind=base.get('kind', 'lower'))
    # contours and TrueType glyphs
    glyphs = {}
    for name, item in placed.items():
        contours = outline.quantize(outline.contours_from_geom(item['geom']))
        glyphs[name] = dict(item, contours=contours, advance=int(round(item['advance'])))
    notdef_contours, notdef_adv = _notdef(m)
    glyphs['.notdef'] = dict(
        contours=notdef_contours,
        advance=notdef_adv,
        unicode=None,
        kind='fixed',
        geom=outline.contours_to_geom(notdef_contours),
    )
    order = ['.notdef'] + [n for n in shapes]
    cmap = {}
    for name in order:
        cp = glyphs[name].get('unicode')
        if cp is not None:
            cmap[cp] = name
    kerning = {} if spec.mono_advance else _kerning(spec, glyphs, cmap)
    font = _assemble(spec, glyphs, order, cmap, kerning, extra_features)
    return Compiled(spec, glyphs, order, cmap, kerning, font)


def _kerning(spec, glyphs, cmap):
    m = spec.metrics
    letters = {
        cp: n
        for cp, n in cmap.items()
        if (0x41 <= cp <= 0x5A)
        or (0x61 <= cp <= 0x7A)
        or cp in (0x2C, 0x2E, 0x2D, 0x27, 0x22, 0x3A, 0x3B)
        or cp in (0x2018, 0x2019, 0x201C, 0x201D, 0x2014, 0x2013, 0x30, 0x31, 0x34, 0x37)
    }
    ref_rows = int(m.cap // spacing.ROW)
    profiles = {n: spacing.Profile(glyphs[n]['geom'], 0, m.cap) for n in set(letters.values())}
    # row index of x-height for lowercase pairs
    xrows = int(m.xh // spacing.ROW)

    def is_low(cp):
        return 0x61 <= cp <= 0x7A or cp in (
            0x2C,
            0x2E,
            0x2D,
            0x3A,
            0x3B,
            0x27,
            0x22,
            0x2018,
            0x2019,
            0x201C,
            0x201D,
        )

    def ref(rows, a, b):
        if a not in cmap or b not in cmap:
            return None
        pa, pb = profiles[cmap[a]], profiles[cmap[b]]
        return spacing.pair_gap(pa, pb, glyphs[cmap[a]]['advance'], spec.kern_cap, rows)

    ref_low = ref(xrows, ord('n'), ord('n'))
    ref_up = ref(ref_rows, ord('H'), ord('H'))
    if ref_low is None:
        return {}
    if ref_up is None:
        ref_up = ref_low
    pairs = {}
    for ca, na in letters.items():
        for cb, nb in letters.items():
            both_low = (0x61 <= ca <= 0x7A or ca in (0x2C, 0x2E, 0x2D)) and (0x61 <= cb <= 0x7A)
            upper_first = 0x41 <= ca <= 0x5A
            if both_low:
                rows, reference = xrows, ref_low
            elif upper_first and 0x41 <= cb <= 0x5A:
                rows, reference = ref_rows, ref_up
            elif upper_first:
                rows, reference = xrows, (ref_low + ref_up) / 2
            elif 0x41 <= cb <= 0x5A:
                rows, reference = ref_rows, (ref_low + ref_up) / 2
            else:
                rows, reference = xrows, ref_low
            k = spacing.kern(
                profiles[na],
                profiles[nb],
                glyphs[na]['advance'],
                spec.kern_cap,
                rows,
                reference,
                spec.kern_strength,
                spec.kern_limit,
            )
            k = int(round(k / 2.0) * 2)
            if abs(k) >= spec.kern_min:
                pairs[(na, nb)] = k
    return pairs


def _feature_text(spec, kerning, extra):
    lines = ['languagesystem DFLT dflt;', 'languagesystem latn dflt;', '']
    if extra:
        lines.append(extra)
    if kerning:
        lines.append('feature kern {')
        for (a, b), v in sorted(kerning.items()):
            lines.append(f'    pos {a} {b} {v};')
        lines.append('} kern;')
    return '\n'.join(lines) + '\n'


def _assemble(spec, glyphs, order, cmap, kerning, extra):
    m = spec.metrics
    fb = FontBuilder(UPM, isTTF=True)
    stamp = TIMESTAMP + 2_082_844_800  # head dates count seconds from 1904-01-01
    fb.setupHead(unitsPerEm=UPM, created=stamp, modified=stamp, flags=0x0003)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    ttglyphs, hmtx = {}, {}
    ymin_all, ymax_all = 0, 0
    for name in order:
        g = glyphs[name]
        tt = _ttglyph(g['contours'])
        ttglyphs[name] = tt
        if g['contours']:
            xmin = min(p[0] for c in g['contours'] for p in c)
            ymin = min(p[1] for c in g['contours'] for p in c)
            ymax = max(p[1] for c in g['contours'] for p in c)
            ymin_all, ymax_all = min(ymin_all, ymin), max(ymax_all, ymax)
        else:
            xmin = 0
        hmtx[name] = (g['advance'], int(xmin))
    fb.setupGlyf(ttglyphs)
    fb.setupHorizontalMetrics(hmtx)
    # the line box never clips a glyph: Windows cuts ink above usWinAscent and below usWinDescent
    ascent = max(m.ascent, int(math.ceil(max(ymax_all, m.asc))))
    descent = max(m.descent, int(math.ceil(max(-ymin_all, -m.desc))))
    fb.setupHorizontalHeader(ascent=ascent, descent=-descent, lineGap=m.line_gap)
    family = spec.family
    legacy = spec.legacy_family or (family if spec.style == 'Regular' else f'{family} {spec.style}')
    legacy_style = 'Regular' if legacy != family or spec.style == 'Regular' else spec.style
    full = family if spec.style == 'Regular' else f'{family} {spec.style}'
    ps = ps_name(spec)
    names = {
        'copyright': f'Copyright (c) 2026 {spec.designer}. {spec.license}',
        'familyName': legacy,
        'styleName': legacy_style,
        'uniqueFontIdentifier': f'{spec.version};BYAX;{ps}',
        'fullName': full,
        'version': f'Version {spec.version}',
        'psName': ps,
        'designer': spec.designer,
        'description': spec.description,
        'vendorURL': spec.url,
        'designerURL': spec.url,
        'licenseDescription': spec.license,
        'licenseInfoURL': spec.license_url,
    }
    if legacy != family or legacy_style != spec.style:
        names['typographicFamily'] = family
        names['typographicSubfamily'] = spec.style
    fb.setupNameTable(names, mac=False)
    widths = [g['advance'] for g in glyphs.values() if g['advance']]
    fs_selection = 0x40 | 0x80  # REGULAR | USE_TYPO_METRICS
    fb.setupOS2(
        sTypoAscender=ascent,
        sTypoDescender=-descent,
        sTypoLineGap=m.line_gap,
        usWinAscent=ascent,
        usWinDescent=descent,
        sxHeight=int(m.xh),
        sCapHeight=int(m.cap),
        version=4,
        usWeightClass=spec.weight,
        usWidthClass=spec.width,
        fsType=0,
        fsSelection=fs_selection,
        achVendID='BYAX',
        xAvgCharWidth=int(sum(widths) / max(1, len(widths))),
        ySubscriptXSize=650,
        ySubscriptYSize=600,
        ySubscriptYOffset=75,
        ySuperscriptXSize=650,
        ySuperscriptYSize=600,
        ySuperscriptYOffset=350,
        yStrikeoutSize=int(m.cap * 0.06) or 40,
        yStrikeoutPosition=int(m.xh * 0.5),
    )
    post_kw = dict(
        isFixedPitch=1 if spec.monospace else 0,
        italicAngle=spec.italic_angle,
        underlinePosition=-int(0.08 * UPM),
        underlineThickness=int(0.05 * UPM),
    )
    fb.setupPost(**post_kw)
    features = _feature_text(spec, kerning, extra)
    fb.addOpenTypeFeatures(features)
    gasp = newTable('gasp')
    gasp.version = 1
    gasp.gaspRange = {0xFFFF: 0x000F}
    fb.font['gasp'] = gasp
    font = fb.font
    font['OS/2'].recalcUnicodeRanges(font)
    return font


def save(compiled: Compiled, path, woff2=True):
    font = compiled.font
    font.flavor = None
    font.save(path)
    out = [path]
    if woff2:
        try:
            import brotli  # noqa: F401
        except ImportError:
            return out
        buf = io.BytesIO()
        font.save(buf)
        buf.seek(0)
        w = TTFont(buf)
        w.flavor = 'woff2'
        target = str(path).rsplit('.', 1)[0] + '.woff2'
        w.save(target)
        out.append(target)
    return out
