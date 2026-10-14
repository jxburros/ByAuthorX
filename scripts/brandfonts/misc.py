"""Punctuation, symbols and special letters."""

from __future__ import annotations

import math

import shapely
from shapely import affinity

from .geom import Glyph, Nib, arc, join, line, spline, transform

BIG = 1e4
MISC = {}


def glyph(*names):
    def deco(fn):
        for n in names:
            MISC[n] = fn
        return fn

    return deco


def dot_at(P, x, y, r=None):
    return shapely.Point(x, y).buffer(P.dot if r is None else r, quad_segs=12)


def shifted(g, dx=0.0, dy=0.0):
    """A copy of a glyph moved by (dx, dy)."""
    out = Glyph(kind=g.kind, role=g.role)
    for s in g.strokes:
        out.strokes.append(
            type(s)(
                s.points + (dx, dy),
                s.nib,
                None if s.clip is None else (s.clip[0] + dy, s.clip[1] + dy),
                None if s.clip_x is None else (s.clip_x[0] + dx, s.clip_x[1] + dx),
                s.taper,
                s.profile,
                s.blob,
            )
        )
    out.shapes = [affinity.translate(sh, dx, dy) for sh in g.shapes]
    out.cuts = [affinity.translate(sh, dx, dy) for sh in g.cuts]
    return out


def merged(*glyphs):
    out = Glyph(kind=glyphs[0].kind)
    for g in glyphs:
        out.strokes += g.strokes
        out.shapes += g.shapes
        out.cuts += g.cuts
    return out


def _punct(kind='punct'):
    return Glyph(kind=kind)


@glyph('period')
def period(P):
    g = _punct()
    g.add(dot_at(P, P.dot, P.dot - P.over * 0.4))
    return g


@glyph('comma')
def comma(P):
    g = _punct()
    d = P.dot
    g.add(dot_at(P, d, d))
    tail = Nib.ellipse(d * 0.92)
    g.stroke(
        spline([(d + 0.3 * d, d * 0.5), (d - 0.05 * d, -d * 0.9), (d - 1.0 * d, -d * 2.1)]),
        nib=tail,
        taper=(1.0, 0.42),
    )
    return g


@glyph('colon')
def colon(P):
    g = _punct()
    g.add(dot_at(P, P.dot, P.dot)).add(dot_at(P, P.dot, P.xh - P.dot))
    return g


@glyph('semicolon')
def semicolon(P):
    g = comma(P)
    g.add(dot_at(P, P.dot, P.xh - P.dot))
    return g


@glyph('exclam')
def exclam(P):
    g = _punct()
    x = max(P.hv, P.dot)
    g.stroke(line((x, P.cap - P.hb), (x, 0.30 * P.cap)), taper=(1.0, 0.55), clip=(0, P.cap))
    g.add(dot_at(P, x, P.dot))
    return g


@glyph('question')
def question(P):
    g = _punct()
    W = 0.50 * P.cap
    h = P.cap
    l, r = P.hv, W - P.hv
    ttop = h - P.hb + P.over
    pts = [
        (l + 0.02 * W, 0.76 * h),
        (0.40 * W, ttop),
        (r - 0.02 * W, 0.76 * h),
        (r - 0.06 * W, 0.58 * h),
        (0.5 * W, 0.44 * h),
        (0.5 * W - 0.02 * W, 0.30 * h),
    ]
    g.stroke(spline(pts), blob=(P.ball, 0))
    g.add(dot_at(P, 0.5 * W - 0.02 * W, P.dot))
    return g


@glyph('quotesingle')
def quotesingle(P):
    g = _punct()
    x = P.hv
    g.stroke(line((x, P.cap - P.hb), (x, 0.66 * P.cap)), taper=(1.0, 0.5), clip=(0, P.cap))
    return g


@glyph('quotedbl')
def quotedbl(P):
    g = _punct()
    d = P.hv * 2.1
    for x in (P.hv, P.hv + d + 0.0):
        g.stroke(line((x, P.cap - P.hb), (x, 0.66 * P.cap)), taper=(1.0, 0.5), clip=(0, P.cap))
    return g


def _quote(P, flip, double, base=None):
    g = _punct()
    d = max(P.dot * 0.95, P.hv * 0.9)
    top = P.cap - d * 1.0 if base is None else base
    xs = [d] + ([d + 2.6 * d] if double else [])
    for x in xs:
        piece = Glyph()
        piece.add(dot_at(P, x, top, d))
        piece.stroke(
            spline(
                [(x + 0.3 * d, top - 0.4 * d), (x - 0.05 * d, top - 1.6 * d), (x - 0.95 * d, top - 2.5 * d)]
            ),
            nib=Nib.ellipse(d * 0.92),
            taper=(1.0, 0.42),
        )
        if flip:
            piece = _rotate180(piece, x, top - 0.9 * d)
        g = merged(g, piece)
    g.kind = 'punct'
    return g


def _rotate180(g, cx, cy):
    out = Glyph(kind=g.kind)
    for s in g.strokes:
        out.strokes.append(
            type(s)(
                transform(s.points, sx=-1, sy=-1, dx=2 * cx, dy=2 * cy),
                s.nib,
                None,
                None,
                s.taper,
                s.profile,
                s.blob,
            )
        )
    out.shapes = [affinity.rotate(sh, 180, origin=(cx, cy)) for sh in g.shapes]
    return out


@glyph('quoteright')
def quoteright(P):
    return _quote(P, False, False)


@glyph('quoteleft')
def quoteleft(P):
    return _quote(P, True, False)


@glyph('quotedblright')
def quotedblright(P):
    return _quote(P, False, True)


@glyph('quotedblleft')
def quotedblleft(P):
    return _quote(P, True, True)


@glyph('quotesinglbase')
def quotesinglbase(P):
    g = _quote(P, False, False, base=P.dot)
    return g


@glyph('quotedblbase')
def quotedblbase(P):
    return _quote(P, False, True, base=P.dot)


@glyph('hyphen')
def hyphen(P):
    g = _punct()
    L = 0.26 * P.cap
    y = 0.30 * P.cap
    g.stroke(line((P.hv, y), (P.hv + L, y)))
    return g


@glyph('endash')
def endash(P):
    g = _punct()
    L = 0.50 * P.cap
    y = 0.30 * P.cap
    g.stroke(line((P.hv, y), (P.hv + L, y)))
    return g


@glyph('emdash')
def emdash(P):
    g = _punct()
    L = 0.94 * P.cap
    y = 0.30 * P.cap
    g.stroke(line((P.hv, y), (P.hv + L, y)))
    return g


@glyph('underscore')
def underscore(P):
    g = _punct()
    g.stroke(line((0, -P.hb - 20), (0.56 * P.cap, -P.hb - 20)))
    return g


@glyph('ellipsis')
def ellipsis(P):
    g = _punct()
    for i in range(3):
        g.add(dot_at(P, P.dot + i * 3.0 * P.dot, P.dot))
    return g


@glyph('bullet')
def bullet(P):
    g = _punct()
    r = P.dot * 1.5
    g.add(dot_at(P, r, P.xh * 0.5, r))
    return g


@glyph('periodcentered')
def periodcentered(P):
    g = _punct()
    g.add(dot_at(P, P.dot, P.xh * 0.5))
    return g


def _paren(P, left, top, bottom, depth):
    g = _punct()
    ry = (top - bottom) / 2
    rx = depth * 2.2
    span = 62
    cy = (top + bottom) / 2
    if left:
        pts = arc(
            P.hv + rx * (1 - math.cos(math.radians(span))) + (depth * 0.0), cy, rx, ry, 180 - span, 180 + span
        )
        pts = arc(P.hv + rx, cy, rx, ry, 180 - span, 180 + span)
    else:
        pts = arc(P.hv, cy, rx, ry, span, -span)
    g.stroke(pts, taper=(0.7, 0.7), profile=lambda t: 0.7 + 0.5 * math.sin(math.pi * t))
    return g


@glyph('parenleft')
def parenleft(P):
    return _paren(P, True, P.asc, P.desc * 0.6, 0.075 * P.cap)


@glyph('parenright')
def parenright(P):
    return _paren(P, False, P.asc, P.desc * 0.6, 0.075 * P.cap)


def _bracket(P, left):
    g = _punct()
    top, bot = P.asc - P.hb, P.desc * 0.6 + P.hb
    w = 0.22 * P.cap
    x0, x1 = (P.hv, P.hv + w) if left else (P.hv + w, P.hv)
    g.stroke(line((x1, top), (x0, top), (x0, bot), (x1, bot)))
    return g


@glyph('bracketleft')
def bracketleft(P):
    return _bracket(P, True)


@glyph('bracketright')
def bracketright(P):
    return _bracket(P, False)


def _brace(P, left):
    g = _punct()
    top, bot = P.asc - P.hb, P.desc * 0.6 + P.hb
    mid = (top + bot) / 2
    w = 0.20 * P.cap
    sg = 1 if left else -1
    x_out = P.hv + (0 if left else w)
    x_in = P.hv + (w if left else 0)
    xm = (x_out + x_in) / 2
    q = (top - bot) / 4
    pts = [
        (x_in, top),
        (xm + sg * 0.1 * w, top - 0.12 * q),
        (xm, top - q * 0.9),
        (xm, mid + q * 0.45),
        (x_out + (sg * -0.0), mid),
        (xm, mid - q * 0.45),
        (xm, bot + q * 0.9),
        (xm + sg * 0.1 * w, bot + 0.12 * q),
        (x_in, bot),
    ]
    g.stroke(spline(pts))
    return g


@glyph('braceleft')
def braceleft(P):
    return _brace(P, True)


@glyph('braceright')
def braceright(P):
    return _brace(P, False)


@glyph('slash')
def slash(P):
    g = _punct()
    g.stroke(line((P.hv, P.desc * 0.35), (P.hv + 0.42 * P.cap, P.asc * 0.98)))
    return g


@glyph('backslash')
def backslash(P):
    g = _punct()
    g.stroke(line((P.hv, P.asc * 0.98), (P.hv + 0.42 * P.cap, P.desc * 0.35)))
    return g


@glyph('bar')
def bar(P):
    g = _punct()
    g.stroke(line((P.hv, P.desc * 0.6), (P.hv, P.asc)), clip=(P.desc * 0.6, P.asc))
    return g


@glyph('brokenbar')
def brokenbar(P):
    g = _punct()
    g.stroke(line((P.hv, P.desc * 0.6), (P.hv, 0.22 * P.cap)), clip=(P.desc * 0.6, 0.22 * P.cap))
    g.stroke(line((P.hv, 0.46 * P.cap), (P.hv, P.asc)), clip=(0.46 * P.cap, P.asc))
    return g


def _math_w(P):
    return 0.52 * P.cap * max(P.sym, 0.7)


@glyph('plus')
def plus(P):
    g = _punct('symbol')
    w = _math_w(P)
    y = 0.36 * P.cap
    g.stroke(line((P.hv, y), (P.hv + w, y)))
    g.stroke(line((P.hv + w / 2, y - w / 2), (P.hv + w / 2, y + w / 2)))
    return g


@glyph('minus')
def minus(P):
    g = _punct('symbol')
    g.stroke(line((P.hv, 0.36 * P.cap), (P.hv + _math_w(P), 0.36 * P.cap)))
    return g


@glyph('equal')
def equal(P):
    g = _punct('symbol')
    w = _math_w(P)
    for y in (0.27 * P.cap, 0.47 * P.cap):
        g.stroke(line((P.hv, y), (P.hv + w, y)))
    return g


@glyph('multiply')
def multiply(P):
    g = _punct('symbol')
    w = _math_w(P) * 0.78
    cy = 0.36 * P.cap
    h = w / 2
    g.stroke(line((P.hv, cy - h), (P.hv + w, cy + h)))
    g.stroke(line((P.hv, cy + h), (P.hv + w, cy - h)))
    return g


@glyph('divide')
def divide(P):
    g = _punct('symbol')
    w = _math_w(P)
    cy = 0.36 * P.cap
    g.stroke(line((P.hv, cy), (P.hv + w, cy)))
    g.add(dot_at(P, P.hv + w / 2, cy + 0.17 * P.cap, P.dot * 0.9)).add(
        dot_at(P, P.hv + w / 2, cy - 0.17 * P.cap, P.dot * 0.9)
    )
    return g


@glyph('plusminus')
def plusminus(P):
    g = plus(P)
    g.stroke(line((P.hv, 0.12 * P.cap), (P.hv + _math_w(P), 0.12 * P.cap)))
    return g


@glyph('less')
def less(P):
    g = _punct('symbol')
    w = _math_w(P)
    cy = 0.36 * P.cap
    g.stroke(line((P.hv + w, cy + 0.20 * P.cap), (P.hv, cy), (P.hv + w, cy - 0.20 * P.cap)))
    return g


@glyph('greater')
def greater(P):
    g = _punct('symbol')
    w = _math_w(P)
    cy = 0.36 * P.cap
    g.stroke(line((P.hv, cy + 0.20 * P.cap), (P.hv + w, cy), (P.hv, cy - 0.20 * P.cap)))
    return g


@glyph('asciitilde')
def asciitilde(P):
    g = _punct('symbol')
    w = _math_w(P) * 1.02
    cy = 0.36 * P.cap
    a = 0.07 * P.cap
    g.stroke(
        spline(
            [
                (P.hv, cy - a * 0.2),
                (P.hv + w * 0.25, cy + a),
                (P.hv + w * 0.5, cy),
                (P.hv + w * 0.75, cy - a),
                (P.hv + w, cy + a * 0.2),
            ]
        )
    )
    return g


@glyph('asciicircum')
def asciicircum(P):
    g = _punct()
    w = 0.40 * P.cap
    g.stroke(line((P.hv, 0.62 * P.cap), (P.hv + w / 2, P.cap - P.hb), (P.hv + w, 0.62 * P.cap)))
    return g


@glyph('grave')
def grave_(P):
    g = _punct()
    g.stroke(line((P.hv, P.cap - P.hb), (P.hv + 0.17 * P.cap, 0.78 * P.cap)), taper=(1.0, 0.7))
    return g


@glyph('asterisk')
def asterisk(P):
    g = _punct()
    r = 0.15 * P.cap
    cx, cy = P.hv + r, P.cap - r - P.hb
    for k in range(5):
        a = math.radians(90 + 72 * k)
        g.stroke(line((cx, cy), (cx + r * math.cos(a), cy + r * math.sin(a))), taper=(0.8, 0.8))
    return g


@glyph('numbersign')
def numbersign(P):
    g = _punct('symbol')
    w, h = 0.56 * P.cap, 0.74 * P.cap
    slant = 0.12 * P.cap
    for dx in (0.18, 0.62):
        x = P.hv + dx * w
        g.stroke(line((x, 0.02 * P.cap), (x + slant, 0.02 * P.cap + h)))
    for y in (0.26 * P.cap, 0.52 * P.cap):
        g.stroke(line((P.hv, y), (P.hv + w + slant * 0.6, y)))
    return g


@glyph('dollar')
def dollar(P):
    from .upper import UPPER

    s = UPPER['S'](P)
    g = Glyph(kind='figure')
    g.strokes = list(s.strokes)
    c = P.c('S', 'upper')
    x = c.W / 2
    g.stroke(
        line((x, -0.06 * P.cap), (x, 1.06 * P.cap)), clip=(-0.06 * P.cap, 1.06 * P.cap), taper=(0.7, 0.7)
    )
    return g


@glyph('percent')
def percent(P):
    g = _punct('symbol')
    h = P.cap
    r = 0.13 * h
    W = 0.82 * h * P.sym
    g.stroke(arc(P.hv + r, h - r - P.hb, r, r, 0, 360))
    g.stroke(arc(W - r, r + P.hb, r, r, 0, 360))
    g.stroke(line((W - 0.04 * h, P.cap - P.hb), (P.hv + 0.04 * h, P.hb)), clip=(0, h))
    return g


@glyph('ampersand')
def ampersand(P):
    g = _punct('upper')
    h, W = P.cap, 0.80 * P.cap * P.sym
    pts = [
        (0.86 * W, 0.22 * h),
        (0.62 * W, 0.04 * h),
        (0.32 * W, P.hb),
        (0.10 * W, 0.20 * h),
        (0.14 * W, 0.40 * h),
        (0.44 * W, 0.60 * h),
        (0.64 * W, 0.78 * h),
        (0.54 * W, 0.96 * h),
        (0.34 * W, 0.96 * h),
        (0.24 * W, 0.78 * h),
        (0.44 * W, 0.56 * h),
        (0.74 * W, 0.40 * h),
        (0.92 * W, 0.40 * h),
    ]
    g.stroke(spline(pts), blob=(P.ball, P.ball))
    return g


@glyph('at')
def at(P):
    g = _punct('upper')
    h = P.cap * 0.92
    W = 0.98 * P.cap * P.sym
    cx, cy = W / 2, h / 2 - 0.02 * P.cap
    g.stroke(arc(cx, cy, W / 2 - P.hv, h / 2, 38, 330))
    rx, ry = 0.17 * W, 0.17 * h
    ix = cx + 0.02 * W
    g.stroke(arc(ix, cy, rx, ry, 0, 360))
    g.stroke(
        spline(
            [
                (ix + rx, cy + ry * 0.9),
                (ix + rx, cy - ry * 0.6),
                (ix + rx + 0.12 * W, cy - ry * 0.4),
                (ix + rx + 0.22 * W, cy + 0.02 * h),
                (W - P.hv - 0.01 * W, cy + 0.14 * h),
            ]
        )
    )
    return g


@glyph('section')
def section(P):
    g = _punct('upper')
    h, W = P.cap * 0.96, 0.46 * P.cap
    from .upper import UPPER

    s = UPPER['S'](P)
    g.strokes = [
        type(st)(
            transform(st.points, sx=W / P.c('S', 'upper').W, sy=0.58, dy=h * 0.40),
            st.nib,
            None,
            None,
            st.taper,
            st.profile,
            st.blob,
        )
        for st in s.strokes
    ]
    g.strokes += [
        type(st)(
            transform(st.points, sx=W / P.c('S', 'upper').W, sy=0.58, dy=-0.05 * h),
            st.nib,
            None,
            None,
            st.taper,
            st.profile,
            st.blob,
        )
        for st in s.strokes
    ]
    return g


@glyph('paragraph')
def paragraph(P):
    g = _punct('upper')
    h, W = P.cap, 0.58 * P.cap
    x = W * 0.58
    ry = 0.23 * h
    cy = h - P.hb - ry
    g.stroke(arc(x - 0.22 * W, cy, 0.30 * W, ry, 90, -90))
    g.stroke(line((x - 0.22 * W, h - P.hb), (x + 0.26 * W, h - P.hb)))
    g.stroke(line((x, 0), (x, h)), clip=(0, h))
    g.stroke(line((x + 0.26 * W, 0), (x + 0.26 * W, h)), clip=(0, h))
    return g


@glyph('dagger')
def dagger(P):
    g = _punct('upper')
    h = P.cap
    x = 0.20 * h
    g.stroke(line((x, -0.10 * h), (x, h)), clip=(-0.1 * h, h))
    g.stroke(line((x - 0.16 * h, 0.68 * h), (x + 0.16 * h, 0.68 * h)))
    return g


@glyph('daggerdbl')
def daggerdbl(P):
    g = dagger(P)
    g.stroke(line((0.20 * P.cap - 0.16 * P.cap, 0.30 * P.cap), (0.20 * P.cap + 0.16 * P.cap, 0.30 * P.cap)))
    return g


@glyph('degree')
def degree(P):
    g = _punct()
    r = 0.095 * P.cap
    g.stroke(arc(r + P.hv * 0.4, P.cap - r - P.hb, r, r, 0, 360), nib=Nib.ellipse(P.hv * 0.6, P.hb * 0.9))
    return g


@glyph('exclamdown')
def exclamdown(P):
    g = _punct()
    x = max(P.hv, P.dot)
    g.stroke(line((x, 0.0), (x, 0.62 * P.cap)), taper=(0.55, 1.0), clip=(0, P.cap))
    g.add(dot_at(P, x, 0.62 * P.cap + P.dot * 2.0 + P.hb))
    g.shapes[-1] = dot_at(P, x, P.cap - P.dot - 8)
    return g


@glyph('questiondown')
def questiondown(P):
    g = question(P)
    return _rotate_glyph(P, g, 0.5 * P.cap / 2, P.cap / 2 - P.desc * 0.0)


def _rotate_glyph(P, g, cx, cy):
    out = Glyph(kind=g.kind)
    for s in g.strokes:
        out.strokes.append(
            type(s)(
                transform(s.points, sx=-1, sy=-1, dx=2 * cx, dy=2 * cy),
                s.nib,
                None,
                None,
                s.taper,
                s.profile,
                s.blob,
            )
        )
    out.shapes = [affinity.rotate(sh, 180, origin=(cx, cy)) for sh in g.shapes]
    return out


@glyph('guilsinglleft')
def guilsinglleft(P):
    g = _punct()
    w = 0.20 * P.cap
    cy = 0.30 * P.cap
    g.stroke(line((P.hv + w, cy + 0.15 * P.cap), (P.hv, cy), (P.hv + w, cy - 0.15 * P.cap)))
    return g


@glyph('guilsinglright')
def guilsinglright(P):
    g = _punct()
    w = 0.20 * P.cap
    cy = 0.30 * P.cap
    g.stroke(line((P.hv, cy + 0.15 * P.cap), (P.hv + w, cy), (P.hv, cy - 0.15 * P.cap)))
    return g


@glyph('guillemotleft')
def guillemotleft(P):
    g = guilsinglleft(P)
    return merged(g, shifted(guilsinglleft(P), 0.22 * P.cap))


@glyph('guillemotright')
def guillemotright(P):
    g = guilsinglright(P)
    return merged(g, shifted(guilsinglright(P), 0.22 * P.cap))


@glyph('cent')
def cent(P):
    from .lower import LOWER

    c = LOWER['c'](P)
    g = Glyph(kind='figure')
    cc = P.c('c')
    s = c.strokes[0]
    g.strokes = [
        type(s)(
            transform(s.points, sy=P.cap * 0.78 / P.xh, dy=0.1 * P.cap),
            s.nib,
            None,
            None,
            s.taper,
            s.profile,
            s.blob,
        )
    ]
    x = cc.W / 2
    g.stroke(line((x, 0.0), (x, 0.9 * P.cap)), taper=(0.7, 0.7), clip=(0, 0.9 * P.cap))
    return g


@glyph('sterling')
def sterling(P):
    g = _punct('symbol')
    W, h = 0.56 * P.cap, P.cap
    l, r = P.hv, W - P.hv
    g.stroke(
        join(
            spline(
                [
                    (r - 0.02 * W, 0.78 * h),
                    (0.5 * W, h - P.hb + P.over),
                    (0.28 * W, 0.78 * h),
                    (0.30 * W, 0.50 * h),
                    (0.22 * W, 0.2 * h),
                    (l, P.hb),
                ]
            ),
            line((l, P.hb), (r, P.hb)),
        )
    )
    g.stroke(line((l - 0.02 * W, 0.46 * h), (0.60 * W, 0.46 * h)))
    return g


@glyph('yen')
def yen(P):
    g = _punct('symbol')
    W, h = 0.66 * P.cap, P.cap
    cx = W / 2
    g.stroke(line((P.hv, h), (cx, 0.46 * h), (W - P.hv, h)), clip=(0, h))
    g.stroke(line((cx, 0.46 * h), (cx, 0)), clip=(0, h))
    g.stroke(line((cx - 0.20 * W, 0.34 * h), (cx + 0.20 * W, 0.34 * h)))
    g.stroke(line((cx - 0.20 * W, 0.18 * h), (cx + 0.20 * W, 0.18 * h)))
    return g


@glyph('Euro')
def euro(P):
    from .upper import UPPER

    c = UPPER['C'](P)
    g = Glyph(kind='figure')
    g.strokes = list(c.strokes)
    cc = P.c('C', 'upper')
    for y in (0.40 * P.cap, 0.58 * P.cap):
        g.stroke(line((-0.04 * cc.W, y), (0.62 * cc.W, y)))
    return g


@glyph('copyright', 'registered')
def circled(P, name='copyright'):
    g = _punct('upper')
    R = 0.40 * P.cap * P.sym
    g.stroke(arc(R, R, R - P.hv * 0.4, R - P.hb * 0.4, 0, 360), nib=Nib.ellipse(P.hv * 0.45, P.hb * 0.45))
    from .upper import UPPER

    letter = UPPER['C' if name == 'copyright' else 'R'](P)
    sc = 0.46 * P.cap / P.cap
    cw = P.c('C' if name == 'copyright' else 'R', 'upper').W
    for st in letter.strokes:
        g.strokes.append(
            type(st)(
                transform(st.points, sx=sc, sy=sc, dx=R - cw * sc / 2, dy=R - P.cap * sc / 2),
                Nib.ellipse(P.hv * 0.45, P.hb * 0.45),
                None,
                None,
                st.taper,
                st.profile,
                (0, 0),
            )
        )
    return g


@glyph('trademark')
def trademark(P):
    from .upper import UPPER

    g = _punct('upper')
    sc = 0.36
    cw = P.c('T', 'upper').W
    t, m = UPPER['T'](P), UPPER['M'](P)
    thin = Nib.ellipse(P.hv * 0.5, P.hb * 0.55)
    for st in t.strokes:
        g.strokes.append(
            type(st)(
                transform(st.points, sx=sc, sy=sc, dy=P.cap * (1 - sc)),
                thin,
                None,
                None,
                st.taper,
                st.profile,
                (0, 0),
            )
        )
    for st in m.strokes:
        g.strokes.append(
            type(st)(
                transform(st.points, sx=sc, sy=sc, dx=cw * sc * 1.1, dy=P.cap * (1 - sc)),
                thin,
                None,
                None,
                st.taper,
                st.profile,
                (0, 0),
            )
        )
    return g


# special letters -------------------------------------------------------------------------------------------


@glyph('germandbls')
def germandbls(P):
    c = P.c('n')
    g = Glyph(kind='lower')
    W = 0.86 * P.xh * P.wide
    l, r = P.hv, W - P.hv
    top = P.asc - P.hb + P.over
    g.stroke(line((l, 0), (l, top - 0.28 * P.xh)), clip=(0, BIG))
    g.stroke(
        spline(
            [
                (l, top - 0.3 * P.xh),
                (l + 0.06 * W, top - 0.02 * P.xh),
                (0.55 * W, top),
                (r - 0.03 * W, top - 0.2 * P.xh),
                (0.62 * W, 0.58 * P.xh),
                (0.38 * W, 0.50 * P.xh),
            ]
        )
    )
    g.stroke(
        spline(
            [
                (0.38 * W, 0.50 * P.xh),
                (r + 0.0 * W, 0.40 * P.xh),
                (r - 0.02 * W, 0.16 * P.xh),
                (0.5 * W, c.tbot),
                (l + 0.14 * W, 0.16 * P.xh),
            ]
        ),
        blob=(0, P.ball),
    )
    g.add(P.foot(l, 0))
    return g


def _compose_halves(P, left, right, overlap):
    wl = left[1]
    return merged(left[0], shifted(right[0], wl - overlap))


@glyph('ae')
def ae(P):
    from .lower import LOWER

    a_ = LOWER['a'](P)
    e_ = LOWER['e'](P)
    wa = P.W('a')
    ov = P.hv * 1.3
    out = merged(a_, shifted(e_, wa - ov))
    out.kind = 'lower'
    return out


@glyph('oe')
def oe(P):
    from .lower import LOWER

    o_ = LOWER['o'](P)
    e_ = LOWER['e'](P)
    wo = P.W('o')
    ov = P.hv * 1.3
    out = merged(o_, shifted(e_, wo - ov))
    out.kind = 'lower'
    return out


@glyph('oslash')
def oslash(P):
    from .lower import LOWER

    g = LOWER['o'](P)
    c = P.c('o')
    g.stroke(line((c.l * 0.4, 0.02 * P.xh), (c.r + c.hv * 0.6, 0.98 * P.xh)), taper=(0.7, 0.7))
    return g


@glyph('Oslash')
def Oslash(P):
    from .upper import UPPER

    g = UPPER['O'](P)
    c = P.c('O', 'upper')
    g.stroke(line((c.l * 0.6, 0.02 * P.cap), (c.r + c.hv * 0.4, 0.98 * P.cap)), taper=(0.7, 0.7))
    return g


@glyph('AE')
def AE(P):
    from .upper import UPPER

    a_, e_ = UPPER['A'](P), UPPER['E'](P)
    wa = P.W('A')
    out = merged(a_, shifted(e_, wa * 0.62))
    out.kind = 'upper'
    return out


@glyph('OE')
def OE(P):
    from .upper import UPPER

    o_, e_ = UPPER['O'](P), UPPER['E'](P)
    wo = P.W('O')
    out = merged(o_, shifted(e_, wo - P.hv * 1.3))
    out.kind = 'upper'
    return out


@glyph('eth')
def eth(P):
    from .lower import LOWER

    g = LOWER['d'](P)
    c = P.c('d')
    g.strokes = [s for s in g.strokes]
    g.stroke(line((c.r - 0.30 * c.W, 0.80 * P.asc), (c.r + 0.18 * c.W, 0.86 * P.asc)))
    return g


@glyph('thorn')
def thorn(P):

    c = P.c('p')
    g = Glyph(kind='lower')
    cy = P.xh / 2
    g.stroke(arc(c.W / 2, cy, c.W / 2 - c.hv, (c.ttop - c.tbot) / 2, 0, 360))
    g.stroke(line((c.l, P.asc), (c.l, P.desc)), clip=(P.desc, P.asc))
    g.add(P.flag(c.l, P.asc)).add(P.foot(c.l, P.desc))
    return g


@glyph('Thorn')
def Thorn(P):
    c = P.c('P', 'upper')
    g = Glyph(kind='upper')
    ry = 0.30 * P.cap
    rx = ry * 0.95
    cy = P.cap / 2
    g.stroke(
        join(
            line((c.l, cy + ry), (c.r - rx, cy + ry)),
            arc(c.r - rx, cy, rx, ry, 90, -90),
            line((c.r - rx, cy - ry), (c.l, cy - ry)),
        )
    )
    g.stroke(line((c.l, 0), (c.l, P.cap)), clip=(0, P.cap))
    g.add(P.foot(c.l, 0)).add(P.foot(c.l, P.cap, up=-1, side='left'))
    return g


@glyph('Eth')
def Eth(P):
    from .upper import UPPER

    g = UPPER['D'](P)
    c = P.c('D', 'upper')
    g.stroke(line((-0.06 * c.W, P.cap * 0.50), (0.42 * c.W, P.cap * 0.50)))
    return g


@glyph('lslash')
def lslash(P):
    from .lower import LOWER

    g = LOWER['l'](P)
    c = P.c(None)
    g.stroke(
        line((c.l - 0.28 * P.xh * P.wide * 0.5, 0.28 * P.xh), (c.l + 0.28 * P.xh * P.wide * 0.5, 0.64 * P.xh))
    )
    return g


@glyph('Lslash')
def Lslash(P):
    from .upper import UPPER

    g = UPPER['L'](P)
    c = P.c('L', 'upper')
    g.stroke(line((c.l - 0.12 * c.W, 0.32 * P.cap), (c.l + 0.30 * c.W, 0.60 * P.cap)))
    return g


@glyph('dcroat')
def dcroat(P):
    from .lower import LOWER

    g = LOWER['d'](P)
    c = P.c('d')
    g.stroke(line((c.r - 0.30 * c.W, 0.80 * P.asc), (c.r + 0.30 * c.W, 0.80 * P.asc)))
    return g
