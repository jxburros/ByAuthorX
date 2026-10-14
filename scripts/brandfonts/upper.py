"""Capital and figure skeletons."""

from __future__ import annotations


from .geom import Glyph, arc, join, line, spline

BIG = 1e4
UPPER = {}


def glyph(*names):
    def deco(fn):
        for n in names:
            UPPER[n] = fn
        return fn

    return deco


def _kind(g, kind='upper'):
    g.kind = kind
    return g


def _round(P, c, a0, a1, W=None, cx=None):
    W = c.W if W is None else W
    cx = W / 2 if cx is None else cx
    return arc(cx, c.cap / 2, W / 2 - c.hv, (c.ttop - c.tbot) / 2, a0, a1)


@glyph('O')
def O(P):
    c = P.c('O', 'upper')
    return _kind(Glyph().stroke(_round(P, c, 0, 360)))


@glyph('C')
def C(P):
    c = P.c('C', 'upper')
    g = Glyph().stroke(_round(P, c, 38, 322), blob=(P.ball, 0))
    return _kind(g)


@glyph('G')
def G(P):
    c = P.c('G', 'upper')
    g = Glyph().stroke(_round(P, c, 40, 360), blob=(P.ball, 0))
    mid = c.cap * 0.47
    g.stroke(line((c.W * 0.54, mid), (c.r, mid)))
    g.stroke(line((c.r, mid), (c.r, c.cap * 0.18)), clip=(0, BIG))
    return _kind(g)


@glyph('Q')
def Q(P):
    c = P.c('Q', 'upper')
    g = Glyph().stroke(_round(P, c, 0, 360))
    g.stroke(line((c.W * 0.55, c.cap * 0.22), (c.r + 0.12 * c.W, -0.08 * c.cap)))
    return _kind(g)


@glyph('D')
def D(P):
    c = P.c('D', 'upper')
    g = Glyph()
    ry = (c.ttop - c.tbot) / 2
    rx = c.r - 0.30 * c.W
    cx = c.r - rx
    g.stroke(line((c.l, c.ttop), (cx, c.ttop)))
    g.stroke(arc(cx, c.cap / 2, rx, ry, 90, -90))
    g.stroke(line((cx, c.tbot), (c.l, c.tbot)))
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.add(P.foot(c.l, 0, side='left')).add(P.foot(c.l, c.cap, up=-1, side='left'))
    return _kind(g)


def _bowls(P, c, split, rx_top, rx_bot, g, joined=True):
    """The two bowls of B and the one of P and R: arcs hanging off a vertical stem."""
    top_mid = (split + c.ttop) / 2
    ry_t = (c.ttop - split) / 2
    g.stroke(
        join(
            line((c.l, c.ttop), (c.l + 0.0 + (c.r - rx_top - c.l), c.ttop)),
            arc(c.l + (c.r - rx_top - c.l), top_mid, rx_top, ry_t, 90, -90),
            line((c.l + (c.r - rx_top - c.l), split), (c.l, split)),
        )
    )


@glyph('B')
def B(P):
    c = P.c('B', 'upper')
    g = Glyph()
    split = 0.52 * c.cap
    # top bowl
    ryt = (c.ttop - split) / 2
    g.stroke(
        join(
            line((c.l, c.ttop), (c.r - 0.06 * c.W - ryt * 0.9, c.ttop)),
            arc(c.r - 0.06 * c.W - ryt * 0.9, split + ryt, ryt * 0.9, ryt, 90, -90),
            line((c.r - 0.06 * c.W - ryt * 0.9, split), (c.l, split)),
        )
    )
    ryb = (split - c.tbot) / 2
    g.stroke(
        join(
            line((c.l, split), (c.r - ryb * 0.95, split)),
            arc(c.r - ryb * 0.95, c.tbot + ryb, ryb * 0.95, ryb, 90, -90),
            line((c.r - ryb * 0.95, c.tbot), (c.l, c.tbot)),
        )
    )
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.add(P.foot(c.l, 0, side='left')).add(P.foot(c.l, c.cap, up=-1, side='left'))
    return _kind(g)


@glyph('P')
def P_(P):
    c = P.c('P', 'upper')
    g = Glyph()
    split = 0.44 * c.cap
    ry = (c.ttop - split) / 2
    rx = ry * 0.95
    g.stroke(
        join(
            line((c.l, c.ttop), (c.r - rx, c.ttop)),
            arc(c.r - rx, split + ry, rx, ry, 90, -90),
            line((c.r - rx, split), (c.l, split)),
        )
    )
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.add(P.foot(c.l, 0)).add(P.foot(c.l, c.cap, up=-1, side='left'))
    return _kind(g)


@glyph('R')
def R(P):
    c = P.c('R', 'upper')
    g = Glyph()
    split = 0.44 * c.cap
    ry = (c.ttop - split) / 2
    rx = ry * 0.95
    g.stroke(
        join(
            line((c.l, c.ttop), (c.r - rx, c.ttop)),
            arc(c.r - rx, split + ry, rx, ry, 90, -90),
            line((c.r - rx, split), (c.l, split)),
        )
    )
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.W * 0.44, split), (c.r, 0)), clip=(0, BIG))
    g.add(P.foot(c.l, 0)).add(P.foot(c.l, c.cap, up=-1, side='left')).add(P.foot(c.r, 0, side='both'))
    return _kind(g)


@glyph('E')
def E(P):
    c = P.c('E', 'upper')
    g = Glyph()
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.l, c.cap - c.hb), (c.r, c.cap - c.hb)))
    g.stroke(line((c.l, c.hb), (c.r, c.hb)))
    g.stroke(line((c.l, c.cap * 0.50), (c.r - 0.14 * c.W, c.cap * 0.50)))
    g.add(P.foot(c.l, 0, side='left')).add(P.foot(c.l, c.cap, up=-1, side='left'))
    g.add(P.arm_serif(c.r, c.cap, -1)).add(P.arm_serif(c.r, 0, 1))
    return _kind(g)


@glyph('F')
def F(P):
    c = P.c('F', 'upper')
    g = Glyph()
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.l, c.cap - c.hb), (c.r, c.cap - c.hb)))
    g.stroke(line((c.l, c.cap * 0.50), (c.r - 0.16 * c.W, c.cap * 0.50)))
    g.add(P.foot(c.l, 0)).add(P.foot(c.l, c.cap, up=-1, side='left')).add(P.arm_serif(c.r, c.cap, -1))
    return _kind(g)


@glyph('L')
def L(P):
    c = P.c('L', 'upper')
    g = Glyph()
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.l, c.hb), (c.r, c.hb)))
    g.add(P.foot(c.l, 0, side='left')).add(P.foot(c.l, c.cap, up=-1, side='left')).add(P.arm_serif(c.r, 0, 1))
    return _kind(g)


@glyph('H')
def H(P):
    c = P.c('H', 'upper')
    g = Glyph()
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.r, 0), (c.r, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.l, c.cap * 0.50), (c.r, c.cap * 0.50)))
    for x in (c.l, c.r):
        g.add(P.foot(x, 0)).add(P.foot(x, c.cap, up=-1))
    return _kind(g)


@glyph('I')
def I(P):
    c = P.c(None, 'upper')
    g = Glyph().stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.add(P.foot(c.l, 0)).add(P.foot(c.l, c.cap, up=-1))
    return _kind(g)


@glyph('J')
def J(P):
    c = P.c('J', 'upper')
    g = Glyph()
    R_ = min(c.W * 0.42, 0.32 * c.cap)
    g.stroke(
        join(line((c.r, c.cap), (c.r, c.tbot + R_)), arc(c.r - R_, c.tbot + R_, R_, R_, 0, -135)),
        clip=(-BIG, c.cap),
        blob=(0, P.ball),
    )
    g.add(P.foot(c.r, c.cap, up=-1))
    return _kind(g)


@glyph('T')
def T(P):
    c = P.c('T', 'upper')
    g = Glyph()
    g.stroke(line((c.l, c.cap - c.hb), (c.r, c.cap - c.hb)))
    g.stroke(line((c.W / 2, 0), (c.W / 2, c.cap)), clip=(0, c.cap))
    g.add(P.foot(c.W / 2, 0)).add(P.arm_serif(c.l, c.cap, -1)).add(P.arm_serif(c.r, c.cap, -1))
    return _kind(g)


@glyph('U')
def U(P):
    c = P.c('U', 'upper')
    g = Glyph()
    yb = 0.34 * c.cap
    cx = c.W / 2
    g.stroke(
        join(
            line((c.l, c.cap), (c.l, yb)),
            arc(cx, yb, (c.r - c.l) / 2, yb - c.tbot, 180, 360),
            line((c.r, yb), (c.r, c.cap)),
        ),
        clip=(-BIG, c.cap),
    )
    g.add(P.foot(c.l, c.cap, up=-1)).add(P.foot(c.r, c.cap, up=-1))
    return _kind(g)


@glyph('V')
def V(P):
    c = P.c('V', 'upper')
    g = Glyph().stroke(
        line((c.l + 0.3 * c.hv, c.cap), (c.W / 2, 0), (c.r - 0.3 * c.hv, c.cap)), clip=(0, c.cap)
    )
    g.add(P.foot(c.l + 0.3 * c.hv, c.cap, up=-1)).add(P.foot(c.r - 0.3 * c.hv, c.cap, up=-1))
    return _kind(g)


@glyph('W')
def W(P):
    c = P.c('W', 'upper')
    g = Glyph()
    w = c.W
    g.stroke(
        line(
            (c.l + 0.3 * c.hv, c.cap),
            (0.26 * w, 0),
            (w / 2, c.cap * 0.93),
            (0.74 * w, 0),
            (c.r - 0.3 * c.hv, c.cap),
        ),
        clip=(0, c.cap),
    )
    g.add(P.foot(c.l + 0.3 * c.hv, c.cap, up=-1)).add(P.foot(c.r - 0.3 * c.hv, c.cap, up=-1))
    return _kind(g)


@glyph('A')
def A(P):
    c = P.c('A', 'upper')
    g = Glyph()
    apex = (c.W / 2, c.cap)
    g.stroke(line((c.l - 0.1 * c.hv, 0), apex, (c.r + 0.1 * c.hv, 0)), clip=(0, c.cap))
    yb = c.cap * 0.30
    t = yb / c.cap
    g.stroke(line((c.l + (c.W / 2 - c.l) * t, yb), (c.r - (c.r - c.W / 2) * t, yb)))
    g.add(P.foot(c.l - 0.1 * c.hv, 0)).add(P.foot(c.r + 0.1 * c.hv, 0))
    return _kind(g)


@glyph('X')
def X(P):
    c = P.c('X', 'upper')
    g = Glyph()
    g.stroke(line((c.l, c.cap), (c.r, 0)), clip=(0, c.cap))
    g.stroke(line((c.l, 0), (c.r, c.cap)), clip=(0, c.cap))
    for x in (c.l, c.r):
        g.add(P.foot(x, 0)).add(P.foot(x, c.cap, up=-1))
    return _kind(g)


@glyph('Y')
def Y(P):
    c = P.c('Y', 'upper')
    g = Glyph()
    jy = c.cap * 0.42
    g.stroke(line((c.l, c.cap), (c.W / 2, jy), (c.r, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.W / 2, jy), (c.W / 2, 0)), clip=(0, c.cap))
    g.add(P.foot(c.W / 2, 0)).add(P.foot(c.l, c.cap, up=-1)).add(P.foot(c.r, c.cap, up=-1))
    return _kind(g)


@glyph('Z')
def Z(P):
    c = P.c('Z', 'upper')
    g = Glyph()
    g.stroke(line((c.l, c.cap - c.hb), (c.r, c.cap - c.hb), (c.l, c.hb), (c.r, c.hb)))
    g.add(P.arm_serif(c.l, c.cap, -1)).add(P.arm_serif(c.r, 0, 1))
    return _kind(g)


@glyph('N')
def N(P):
    c = P.c('N', 'upper')
    g = Glyph()
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.r, 0), (c.r, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.l + 0.2 * c.hv, c.cap), (c.r - 0.2 * c.hv, 0)), clip=(0, c.cap))
    g.add(P.foot(c.l, 0)).add(P.foot(c.r, 0, side='both')).add(P.foot(c.l, c.cap, up=-1)).add(
        P.foot(c.r, c.cap, up=-1, side='both')
    )
    return _kind(g)


@glyph('M')
def M(P):
    c = P.c('M', 'upper')
    g = Glyph()
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.r, 0), (c.r, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.l, c.cap), (c.W / 2, 0), (c.r, c.cap)), clip=(0, c.cap))
    g.add(P.foot(c.l, 0)).add(P.foot(c.r, 0)).add(P.foot(c.l, c.cap, up=-1)).add(P.foot(c.r, c.cap, up=-1))
    return _kind(g)


@glyph('K')
def K(P):
    c = P.c('K', 'upper')
    g = Glyph()
    g.stroke(line((c.l, 0), (c.l, c.cap)), clip=(0, c.cap))
    g.stroke(line((c.r, c.cap), (c.l, c.cap * 0.40)), clip=(0, c.cap))
    g.stroke(line((c.W * 0.28, c.cap * 0.50), (c.r, 0)), clip=(0, c.cap))
    g.add(P.foot(c.l, 0)).add(P.foot(c.l, c.cap, up=-1, side='left')).add(P.foot(c.r, 0)).add(
        P.foot(c.r, c.cap, up=-1)
    )
    return _kind(g)


@glyph('S')
def S(P):
    c = P.c('S', 'upper')
    g = Glyph()
    W, h = c.W, c.cap
    pts = [
        (c.r - 0.02 * W, 0.80 * h),
        (0.60 * W, c.ttop),
        (0.28 * W, c.ttop - 0.01 * h),
        (c.l + 0.03 * W, 0.75 * h),
        (0.30 * W, 0.58 * h),
        (0.70 * W, 0.42 * h),
        (c.r - 0.01 * W, 0.25 * h),
        (0.58 * W, c.tbot),
        (0.28 * W, c.tbot + 0.01 * h),
        (c.l + 0.02 * W, 0.20 * h),
    ]
    g.stroke(spline(pts), blob=(P.ball, P.ball))
    return _kind(g)


# figures ---------------------------------------------------------------------------------------------------


def _fig(g):
    return _kind(g, 'figure')


@glyph('0')
def zero(P):
    c = P.c('0', 'upper')
    return _fig(Glyph().stroke(arc(c.W / 2, c.cap / 2, c.W / 2 - c.hv, (c.ttop - c.tbot) / 2, 0, 360)))


@glyph('1')
def one(P):
    c = P.c('1', 'upper')
    g = Glyph()
    x = c.W * 0.62
    g.stroke(line((x, 0), (x, c.cap)), clip=(0, c.cap))
    g.stroke(line((x, c.cap), (c.l, c.cap * 0.78)), clip=(0, c.cap))
    g.add(P.foot(x, 0))
    return _fig(g)


@glyph('2')
def two(P):
    c = P.c('2', 'upper')
    g = Glyph()
    W, h = c.W, c.cap
    pts = [
        (c.l + 0.02 * W, 0.76 * h),
        (0.38 * W, c.ttop),
        (c.r - 0.04 * W, 0.74 * h),
        (c.r - 0.02 * W, 0.58 * h),
        (0.55 * W, 0.34 * h),
        (c.l + 0.02 * W, c.hb + 0.04 * h),
    ]
    g.stroke(join(spline(pts), line((c.l + 0.02 * W, c.hb), (c.r, c.hb))), blob=(P.ball, 0))
    return _fig(g)


@glyph('3')
def three(P):
    c = P.c('3', 'upper')
    g = Glyph()
    W, h = c.W, c.cap
    upper = [
        (c.l + 0.04 * W, 0.78 * h),
        (0.40 * W, c.ttop),
        (c.r - 0.06 * W, 0.77 * h),
        (0.60 * W, 0.57 * h),
        (0.36 * W, 0.53 * h),
    ]
    lower = [
        (0.36 * W, 0.53 * h),
        (c.r - 0.02 * W, 0.40 * h),
        (c.r - 0.02 * W, 0.20 * h),
        (0.40 * W, c.tbot),
        (c.l + 0.02 * W, 0.20 * h),
    ]
    g.stroke(spline(upper), blob=(P.ball, 0))
    g.stroke(spline(lower), blob=(0, P.ball))
    return _fig(g)


@glyph('4')
def four(P):
    c = P.c('4', 'upper')
    g = Glyph()
    x = c.W * 0.70
    g.stroke(line((x, 0), (x, c.cap)), clip=(0, c.cap))
    g.stroke(line((x, c.cap), (c.l, c.cap * 0.30), (c.r, c.cap * 0.30)), clip=(0, c.cap))
    g.add(P.foot(x, 0))
    return _fig(g)


@glyph('5')
def five(P):
    c = P.c('5', 'upper')
    g = Glyph()
    W, h = c.W, c.cap
    g.stroke(line((c.r - 0.02 * W, c.cap - c.hb), (c.l + 0.10 * W, c.cap - c.hb), (c.l + 0.04 * W, 0.50 * h)))
    bowl = [
        (c.l + 0.04 * W, 0.50 * h),
        (0.50 * W, 0.58 * h),
        (c.r - 0.02 * W, 0.37 * h),
        (c.r - 0.06 * W, 0.17 * h),
        (0.42 * W, c.tbot),
        (c.l + 0.02 * W, 0.17 * h),
    ]
    g.stroke(spline(bowl), blob=(0, P.ball))
    return _fig(g)


@glyph('6')
def six(P):
    c = P.c('6', 'upper')
    g = Glyph()
    W, h = c.W, c.cap
    cy = 0.31 * h
    g.stroke(arc(W / 2, cy, W / 2 - c.hv, cy - c.tbot, 0, 360))
    g.stroke(
        spline(
            [(c.l + 0.01 * W, cy), (c.l + 0.06 * W, 0.62 * h), (0.42 * W, 0.93 * h), (c.r - 0.06 * W, c.ttop)]
        ),
        blob=(0, P.ball),
    )
    return _fig(g)


@glyph('7')
def seven(P):
    c = P.c('7', 'upper')
    g = Glyph()
    g.stroke(line((c.l, c.cap - c.hb), (c.r, c.cap - c.hb)))
    g.stroke(line((c.r, c.cap - c.hb), (c.W * 0.34, 0)), clip=(0, c.cap))
    return _fig(g)


@glyph('8')
def eight(P):
    c = P.c('8', 'upper')
    g = Glyph()
    W, h = c.W, c.cap
    split = 0.55 * h
    g.stroke(arc(W / 2, (split + c.ttop) / 2, (W - 2 * c.hv) * 0.40, (c.ttop - split) / 2 + 0.0, 0, 360))
    g.stroke(arc(W / 2, split / 2 + c.tbot / 2, W / 2 - c.hv, (split - c.tbot) / 2, 0, 360))
    return _fig(g)


@glyph('9')
def nine(P):
    c = P.c('9', 'upper')
    g = Glyph()
    W, h = c.W, c.cap
    cy = 0.69 * h
    g.stroke(arc(W / 2, cy, W / 2 - c.hv, c.ttop - cy, 0, 360))
    g.stroke(
        spline(
            [(c.r - 0.01 * W, cy), (c.r - 0.06 * W, 0.38 * h), (0.58 * W, 0.07 * h), (c.l + 0.06 * W, c.tbot)]
        ),
        blob=(0, P.ball),
    )
    return _fig(g)
