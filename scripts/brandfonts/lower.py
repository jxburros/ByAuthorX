"""Lowercase skeletons. Every function takes Params and returns a Glyph in font units (y up)."""

from __future__ import annotations

import shapely

from .geom import Glyph, arc, join, line, spline

BIG = 1e4
LOWER = {}


def glyph(*names):
    def deco(fn):
        for n in names:
            LOWER[n] = fn
        return fn

    return deco


def _ellipse(c, W, cy, ry, a0=0, a1=360, P=None):
    rx = W / 2 - c.hv
    pts = arc(W / 2, cy, rx, ry, a0, a1)
    return pts


def _tilt(P, pts, about):
    """Old-style stress: tilt the ellipse by P.stress degrees."""
    if not P.stress:
        return pts
    from .geom import transform

    return transform(pts, rotate=P.stress, about=about)


def _dot(P, x, y, r=None):
    return shapely.Point(x, y).buffer(P.dot if r is None else r, quad_segs=12)


def _arch(P, c, left, right, ys):
    """A shoulder from the stem at `left` over to a stem at `right`, then straight down to the baseline."""
    cx = (left + right) / 2
    ry = c.ttop - ys
    pts = arc(cx, ys, (right - left) / 2, ry, 180, 0)
    return join(pts, line((right, ys), (right, 0)))


@glyph('n', 'h')
def n(P, name='n'):
    c = P.c('n')
    top = c.asc if name == 'h' else c.xh
    g = Glyph(kind='lower')
    g.stroke(_arch(P, c, c.l, c.r, 0.52 * c.xh), clip=(0, BIG))
    g.stroke(line((c.l, 0), (c.l, top)), clip=(0, top))
    g.add(P.flag(c.l, top)).add(P.foot(c.l, 0)).add(P.foot(c.r, 0))
    return g


@glyph('m')
def m(P):
    c = P.c('m')
    mid = c.W / 2
    ys = 0.52 * c.xh
    g = Glyph(kind='lower')
    g.stroke(_arch(P, c, c.l, mid, ys), clip=(0, BIG))
    g.stroke(_arch(P, c, mid, c.r, ys), clip=(0, BIG))
    g.stroke(line((c.l, 0), (c.l, c.xh)), clip=(0, c.xh))
    g.add(P.flag(c.l, c.xh)).add(P.foot(c.l, 0)).add(P.foot(mid, 0)).add(P.foot(c.r, 0))
    return g


@glyph('u')
def u(P):
    c = P.c('u')
    yb = 0.48 * c.xh
    cx = (c.l + c.r) / 2
    g = Glyph(kind='lower')
    g.stroke(
        join(line((c.l, c.xh), (c.l, yb)), arc(cx, yb, (c.r - c.l) / 2, yb - c.tbot, 180, 360)),
        clip=(-BIG, c.xh),
    )
    g.stroke(line((c.r, c.xh), (c.r, 0)), clip=(0, c.xh))
    g.add(P.flag(c.l, c.xh)).add(P.flag(c.r, c.xh)).add(P.foot(c.r, 0, side='both'))
    return g


@glyph('o')
def o(P):
    c = P.c('o')
    g = Glyph(kind='lower')
    cy = c.xh / 2
    g.stroke(_tilt(P, arc(c.W / 2, cy, c.W / 2 - c.hv, (c.ttop - c.tbot) / 2, 0, 360), (c.W / 2, cy)))
    return g


@glyph('c')
def cc(P):
    c = P.c('c')
    g = Glyph(kind='lower')
    cy = c.xh / 2
    g.stroke(
        _tilt(
            P, arc(c.W / 2 + 0.02 * c.W, cy, c.W / 2 - c.hv, (c.ttop - c.tbot) / 2, 42, 318), (c.W / 2, cy)
        ),
        blob=(P.ball, 0),
    )
    return g


@glyph('e')
def e(P):
    c = P.c('e')
    g = Glyph(kind='lower')
    cy = c.xh / 2
    rx, ry = c.W / 2 - c.hv, (c.ttop - c.tbot) / 2
    bar = cy - P.cross_low * c.xh
    g.stroke(_tilt(P, arc(c.W / 2, cy, rx, ry, 0, 322), (c.W / 2, cy)))
    g.stroke(line((c.l, bar), (c.r, bar)))
    return g


@glyph('a')
def a(P):
    c = P.c('a')
    g = Glyph(kind='lower')
    if P.single_a:
        cy = c.xh / 2
        rx = (c.r - c.l) / 2
        g.stroke(arc(c.r - rx, cy, rx, (c.ttop - c.tbot) / 2, 0, 360))
        g.stroke(line((c.r, c.xh), (c.r, 0)), clip=(0, c.xh))
        g.add(P.foot(c.r, 0, side='right')).add(P.flag(c.r, c.xh))
        return g
    rx = (c.r - c.l) / 2
    cx = (c.l + c.r) / 2
    ys = 0.60 * c.xh
    top = join(arc(cx, ys, rx * 0.97, c.ttop - ys, 150, 0), line((c.r, ys), (c.r, 0)))
    g.stroke(top, clip=(0, BIG), blob=(P.ball, 0))
    bt = 0.50 * c.xh
    brx = rx * 0.98
    g.stroke(arc(c.r - brx, (bt + c.tbot) / 2, brx, (bt - c.tbot) / 2, 0, 360))
    g.add(P.foot(c.r, 0, side='right', length=P.serif.length * 0.6 if P.serif else 0))
    return g


def _bowl_letter(P, key, stem_x, stem_top, stem_bot, side):
    c = P.c(key)
    g = Glyph(kind='lower')
    cy = c.xh / 2
    g.stroke(arc(c.W / 2, cy, c.W / 2 - c.hv, (c.ttop - c.tbot) / 2, 0, 360))
    g.stroke(
        line((stem_x, stem_top), (stem_x, stem_bot)), clip=(min(stem_top, stem_bot), max(stem_top, stem_bot))
    )
    return g, c


@glyph('b')
def b(P):
    g, c = _bowl_letter(P, 'b', P.hv, P.asc, 0, 'l')
    g.add(P.flag(c.l, c.asc)).add(P.foot(c.l, 0, side='left'))
    return g


@glyph('d')
def d(P):
    c = P.c('d')
    g, c = _bowl_letter(P, 'd', c.r, c.asc, 0, 'r')
    g.add(P.flag(c.r, c.asc)).add(P.foot(c.r, 0, side='right'))
    return g


@glyph('p')
def p(P):
    c = P.c('p')
    g, c = _bowl_letter(P, 'p', c.l, c.xh, c.desc, 'l')
    g.add(P.flag(c.l, c.xh)).add(P.foot(c.l, c.desc))
    return g


@glyph('q')
def q(P):
    c = P.c('q')
    g, c = _bowl_letter(P, 'q', c.r, c.xh, c.desc, 'r')
    g.add(P.flag(c.r, c.xh)).add(P.foot(c.r, c.desc))
    return g


@glyph('l')
def l_(P):
    c = P.c(None)
    g = Glyph(kind='lower')
    g.stroke(line((c.l, 0), (c.l, c.asc)), clip=(0, c.asc))
    g.add(P.flag(c.l, c.asc)).add(P.foot(c.l, 0))
    return g


@glyph('i')
def i(P):
    c = P.c(None)
    g = Glyph(kind='lower')
    g.stroke(line((c.l, 0), (c.l, c.xh)), clip=(0, c.xh))
    g.add(P.flag(c.l, c.xh)).add(P.foot(c.l, 0))
    g.add(_dot(P, c.l, c.xh + P.extras.get('dot_gap', 0.22 * c.xh) + P.dot))
    return g


@glyph('dotlessi')
def dotlessi(P):
    g = i(P)
    g.shapes = g.shapes[:-1]
    return g


@glyph('j')
def j(P):
    c = P.c(None)
    g = Glyph(kind='lower')
    R = max(P.xh * 0.2, 3 * c.hv)
    x = c.l
    g.stroke(
        join(line((x, c.xh), (x, c.desc + c.hb + R)), arc(x - R, c.desc + c.hb + R, R, R, 0, -95)),
        clip=(c.desc, c.xh),
        blob=(0, P.ball),
    )
    g.add(P.flag(x, c.xh))
    g.add(_dot(P, x, c.xh + P.extras.get('dot_gap', 0.22 * c.xh) + P.dot))
    return g


@glyph('dotlessj')
def dotlessj(P):
    g = j(P)
    g.shapes = g.shapes[:-1]
    return g


@glyph('f')
def f(P):
    c = P.c('f')
    g = Glyph(kind='lower')
    xs = c.l + 0.05 * P.xh
    R = max(0.22 * P.xh, 3.2 * c.hv)
    top = c.asc - c.hb + c.over
    g.stroke(
        join(line((xs, 0), (xs, top - R)), arc(xs + R, top - R, R, R, 180, 62)),
        clip=(0, BIG),
        blob=(0, P.ball),
    )
    bar = c.xh - c.hb
    g.stroke(line((xs - 0.20 * P.xh, bar), (xs + 0.24 * P.xh, bar)))
    g.add(P.foot(xs, 0))
    return g


@glyph('t')
def t(P):
    c = P.c('t')
    g = Glyph(kind='lower')
    xs = c.l + 0.08 * P.xh
    R = max(0.17 * P.xh, 2.6 * c.hv)
    top = c.xh + 0.5 * (c.asc - c.xh)
    g.stroke(
        join(line((xs, top), (xs, c.tbot + R)), arc(xs + R, c.tbot + R, R, R, 180, 275)),
        clip=(-BIG, top),
        blob=(0, P.ball),
    )
    bar = c.xh - c.hb
    g.stroke(line((xs - 0.20 * P.xh, bar), (xs + 0.30 * P.xh, bar)))
    return g


@glyph('r')
def r(P):
    c = P.c('r')
    g = Glyph(kind='lower')
    g.stroke(line((c.l, 0), (c.l, c.xh)), clip=(0, c.xh))
    ry = 0.40 * c.xh
    rx = (c.r - c.l) * 0.95
    g.stroke(arc(c.l + rx, c.ttop - ry, rx, ry, 180, 52), blob=(0, P.ball))
    g.add(P.flag(c.l, c.xh)).add(P.foot(c.l, 0))
    return g


@glyph('s')
def s(P):
    c = P.c('s')
    g = Glyph(kind='lower')
    W, xh = c.W, c.xh
    pts = [
        (c.r - 0.02 * W, 0.79 * xh),
        (0.60 * W, c.ttop),
        (0.27 * W, c.ttop - 0.01 * xh),
        (c.l + 0.02 * W, 0.745 * xh),
        (0.30 * W, 0.57 * xh),
        (0.70 * W, 0.43 * xh),
        (c.r - 0.01 * W, 0.255 * xh),
        (0.58 * W, c.tbot),
        (0.30 * W, c.tbot + 0.01 * xh),
        (c.l + 0.03 * W, 0.215 * xh),
    ]
    g.stroke(spline(pts), blob=(P.ball, P.ball))
    return g


@glyph('v')
def v(P):
    c = P.c('v')
    g = Glyph(kind='lower')
    g.stroke(line((c.l + 0.2 * c.hv, c.xh), (c.W / 2, 0), (c.r - 0.2 * c.hv, c.xh)), clip=(0, c.xh))
    g.add(P.foot(c.l + 0.2 * c.hv, c.xh, up=-1)).add(P.foot(c.r - 0.2 * c.hv, c.xh, up=-1))
    return g


@glyph('w')
def w(P):
    c = P.c('w')
    g = Glyph(kind='lower')
    W = c.W
    g.stroke(
        line(
            (c.l + 0.2 * c.hv, c.xh),
            (0.27 * W, 0),
            (W / 2, c.xh * 0.92),
            (0.73 * W, 0),
            (c.r - 0.2 * c.hv, c.xh),
        ),
        clip=(0, c.xh),
    )
    g.add(P.foot(c.l + 0.2 * c.hv, c.xh, up=-1)).add(P.foot(c.r - 0.2 * c.hv, c.xh, up=-1))
    return g


@glyph('x')
def x(P):
    c = P.c('x')
    g = Glyph(kind='lower')
    g.stroke(line((c.l, c.xh), (c.r, 0)), clip=(0, c.xh))
    g.stroke(line((c.l, 0), (c.r, c.xh)), clip=(0, c.xh))
    g.add(P.foot(c.l, 0)).add(P.foot(c.r, 0)).add(P.foot(c.l, c.xh, up=-1)).add(P.foot(c.r, c.xh, up=-1))
    return g


@glyph('y')
def y(P):
    c = P.c('y')
    g = Glyph(kind='lower')
    x2 = c.l + 0.12 * c.W
    yj = 0.0
    xj = c.r + (x2 - c.r) * (c.xh - yj) / (c.xh - c.desc)
    g.stroke(line((c.l + 0.2 * c.hv, c.xh), (xj, yj)), clip=(yj - 1, c.xh))
    g.stroke(line((c.r - 0.2 * c.hv, c.xh), (x2, c.desc)), clip=(c.desc, c.xh), blob=(0, P.ball))
    g.add(P.foot(c.l + 0.2 * c.hv, c.xh, up=-1)).add(P.foot(c.r - 0.2 * c.hv, c.xh, up=-1))
    return g


@glyph('z')
def z(P):
    c = P.c('z')
    g = Glyph(kind='lower')
    g.stroke(line((c.l, c.xh - c.hb), (c.r, c.xh - c.hb), (c.l, c.hb), (c.r, c.hb)))
    return g


@glyph('k')
def k(P):
    c = P.c('k')
    g = Glyph(kind='lower')
    g.stroke(line((c.l, 0), (c.l, c.asc)), clip=(0, c.asc))
    tip = (c.r - 0.02 * c.W, c.xh)
    knee = (c.l, 0.38 * c.xh)
    g.stroke(line(tip, knee), clip=(0, c.xh))
    t0 = 0.55
    jx, jy = tip[0] + (knee[0] - tip[0]) * t0, tip[1] + (knee[1] - tip[1]) * t0
    g.stroke(line((jx, jy), (c.r, 0)), clip=(0, c.xh))
    g.add(P.flag(c.l, c.asc)).add(P.foot(c.l, 0)).add(P.foot(c.r, 0, side='both')).add(
        P.foot(tip[0], c.xh, up=-1)
    )
    return g


@glyph('g')
def g_(P):
    c = P.c('g')
    g = Glyph(kind='lower')
    cy = c.xh / 2
    rx = (c.r - c.l) / 2
    g.stroke(arc(c.r - rx, cy, rx, (c.ttop - c.tbot) / 2, 0, 360))
    R = max(0.24 * c.W, 3 * c.hv)
    ytail = c.desc + c.hb + 0.0
    g.stroke(
        join(line((c.r, c.xh), (c.r, ytail + R)), arc(c.r - R, ytail + R, R, R, 0, -100)),
        clip=(c.desc, c.xh),
        blob=(0, P.ball),
    )
    g.add(P.flag(c.r, c.xh))
    return g
