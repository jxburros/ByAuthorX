"""Per-family drawing parameters shared by every glyph skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import shapely
from shapely.geometry import Polygon

from .geom import Nib, bezier

# Outer ink width of each letter as a share of the x-height (lowercase) or cap height (the rest).
LOWER_WIDTH = dict(
    a=0.80,
    b=0.84,
    c=0.78,
    d=0.84,
    e=0.82,
    f=0.44,
    g=0.84,
    h=0.80,
    k=0.76,
    m=1.26,
    n=0.80,
    o=0.88,
    p=0.84,
    q=0.84,
    r=0.52,
    s=0.66,
    t=0.46,
    u=0.80,
    v=0.78,
    w=1.12,
    x=0.74,
    y=0.78,
    z=0.68,
    i=0.0,
    j=0.0,
    l=0.0,
)
UPPER_WIDTH = dict(
    A=0.86,
    B=0.68,
    C=0.78,
    D=0.78,
    E=0.60,
    F=0.56,
    G=0.82,
    H=0.76,
    J=0.46,
    K=0.72,
    L=0.56,
    M=0.94,
    N=0.78,
    O=0.90,
    P=0.64,
    Q=0.90,
    R=0.68,
    S=0.64,
    T=0.66,
    U=0.76,
    V=0.84,
    W=1.16,
    X=0.78,
    Y=0.80,
    Z=0.68,
    I=0.0,
)
FIGURE_WIDTH = {
    '0': 0.62,
    '1': 0.40,
    '2': 0.58,
    '3': 0.58,
    '4': 0.64,
    '5': 0.58,
    '6': 0.60,
    '7': 0.56,
    '8': 0.60,
    '9': 0.60,
}


@dataclass
class Serifs:
    """Bracketed or slab serifs: lengths are beyond the stem edge, in font units."""

    length: float = 34.0
    thick: float = 20.0
    bracket: float = 26.0
    flag: float = 36.0  # length of the lowercase top-left flag
    flag_drop: float = 22.0
    tip: float = 0.8  # tip thickness as a share of thick
    arm: float = 46.0  # height of the vertical serif at the end of caps' arms
    caps_length: float = 0.0  # extra length for cap feet; 0 keeps the same as length
    slab: bool = False  # square slab: no bracket, even thickness


@dataclass
class Params:
    xh: float
    cap: float
    asc: float
    desc: float
    nib: Nib
    over: float = 8.0
    wide: float = 1.0
    cap_wide: float | None = None  # caps may be set at a different width factor
    serif: Serifs | None = None
    single_a: bool = False
    single_g: bool = True
    slant: float = 0.0
    straight_y: bool = True
    round_cap: bool = True
    ball: float = 0.0  # radius of terminal balls on curved ends (0 = none)
    dot: float = 0.0  # radius of i/j dots and full stops; 0 derives from the nib
    cross_low: float = 0.0  # lower the e bar (share of the x-height)
    stress: float = 0.0  # tilts ellipse axes (degrees) for old-style stress
    figure_wide: float | None = None
    width_override: dict = field(
        default_factory=dict
    )  # glyph key -> outer width as a share of its zone height
    grid: float = 0.0  # snap outer widths to multiples of this (cross-stitch cells)
    extras: dict = field(default_factory=dict)

    def __post_init__(self):
        self.hv = self.nib.hx
        self.hb = self.nib.hy
        self.cap_wide = self.wide if self.cap_wide is None else self.cap_wide
        self.figure_wide = self.cap_wide if self.figure_wide is None else self.figure_wide
        if not self.dot:
            self.dot = max(self.hv, self.hb) * 1.05

    @property
    def sym(self):
        """Width scale for round symbols (copyright, at, percent ...): narrow families keep them narrower."""
        return min(1.0, 0.45 + 0.5 * self.cap_wide)

    def W(self, key):
        w = self._width(key)
        if self.grid and w:
            w = max(2 * self.grid, round(w / self.grid) * self.grid)
        return w

    def _width(self, key):
        if key in self.width_override:
            return self.width_override[key] * (self.xh if key in LOWER_WIDTH else self.cap)
        if key in LOWER_WIDTH:
            return LOWER_WIDTH[key] * self.xh * self.wide
        if key in UPPER_WIDTH:
            return UPPER_WIDTH[key] * self.cap * self.cap_wide
        return FIGURE_WIDTH[key] * self.cap * self.figure_wide

    def c(self, key, kind='lower'):
        """Local coordinates for one glyph: outer width, centreline insets and zone lines."""
        W = self.W(key) if key else 0.0
        top = self.xh if kind == 'lower' else self.cap
        return SimpleNamespace(
            W=W,
            l=self.hv,
            r=W - self.hv,
            hv=self.hv,
            hb=self.hb,
            xh=self.xh,
            cap=self.cap,
            asc=self.asc,
            desc=self.desc,
            top=top,
            ttop=top - self.hb + self.over,
            tbot=self.hb - self.over,
            mid=top / 2,
            over=self.over,
        )

    # serif shapes -----------------------------------------------------------------------------------------
    def foot(self, x, y=0.0, up=1, side='both', hw=None, length=None):
        """A bracketed foot serif on the zone line y; up=-1 hangs from a top line."""
        s = self.serif
        if not s:
            return None
        hw = self.hv if hw is None else hw
        L = hw + (s.length if length is None else length)
        t = s.thick
        br = 0.0 if s.slab else s.bracket
        tip = t if s.slab else t * s.tip

        def curve(sg):
            if s.slab:
                return [(x + sg * hw, y + up * t)]
            pts = bezier(
                (x + sg * (L - 3), y + up * t),
                (x + sg * (hw + br * 0.45), y + up * t),
                (x + sg * hw, y + up * (t + br * 0.45)),
                (x + sg * hw, y + up * (t + br)),
                step=4,
            )
            return [tuple(p) for p in pts]

        inner = y + up * (t + br + 4)
        if side == 'both':
            pts = (
                [(x - L, y), (x + L, y), (x + L, y + up * tip)]
                + curve(1)
                + [(x + hw * 0.5, inner), (x - hw * 0.5, inner)]
                + curve(-1)[::-1]
                + [(x - L, y + up * tip)]
            )
        elif side == 'right':
            pts = [(x, y), (x + L, y), (x + L, y + up * tip)] + curve(1) + [(x, inner)]
        else:
            pts = [(x, y), (x, inner)] + curve(-1)[::-1] + [(x - L, y + up * tip), (x - L, y)]
        poly = Polygon(pts)
        return poly if poly.is_valid else poly.buffer(0)

    def flag(self, x, y_top, hw=None, side='left'):
        """The lowercase top serif: a slanted wedge reaching out to the left of a stem."""
        s = self.serif
        if not s:
            return None
        hw = self.hv if hw is None else hw
        sg = -1 if side == 'left' else 1
        if s.slab:
            L = hw + s.length
            return Polygon(
                [(x + sg * L, y_top), (x + sg * L, y_top - s.thick), (x, y_top - s.thick), (x, y_top)]
            )
        tip_x = x + sg * (hw + s.flag)
        drop = s.flag_drop
        pts = [
            (x - sg * hw, y_top),
            (tip_x, y_top - drop),
            (tip_x, y_top - drop - s.thick * s.tip),
            (x + sg * (hw + s.flag * 0.35), y_top - drop - s.thick * 0.9 - s.bracket * 0.12),
            (x + sg * hw, y_top - drop - s.thick - s.bracket * 0.7),
            (x - sg * hw * 0.6, y_top - drop - s.thick - s.bracket * 0.7),
        ]
        poly = Polygon(pts)
        return poly if poly.is_valid else poly.buffer(0)

    def arm_serif(self, x, y, up=-1, side='out'):
        """A vertical serif at the end of a horizontal arm (T, E, F, L, Z); up=-1 hangs below y."""
        s = self.serif
        if not s:
            return None
        h = s.arm
        w = max(self.hv * 0.55, s.thick * 0.9)
        poly = Polygon([(x - w / 2, y), (x + w / 2, y), (x + w / 2, y + up * h), (x - w / 2, y + up * h)])
        return poly

    def ball_at(self, point, radius=None):
        radius = self.ball if radius is None else radius
        return shapely.Point(*point).buffer(radius, quad_segs=10) if radius else None
