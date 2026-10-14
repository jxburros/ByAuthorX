"""Stroke geometry: sampled skeleton paths swept by a nib into outline polygons.

Glyphs are drawn as centerlines (``Stroke``) in font units, y up. A stroke is swept by a convex nib
(``Nib``): the union of the hulls of the nib at consecutive path points. A rotated rectangle gives a
broad-pen contrast, a circle a monoline, an ellipse a gentle contrast. Everything downstream works on
shapely polygons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np
import shapely
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

STEP = 6.0


def _count(length, step):
    return max(2, int(math.ceil(length / step)) + 1)


def line(*pts, step=STEP):
    """A polyline through corner points, resampled."""
    out = [np.asarray(pts[0], float)]
    for a, b in zip(pts, pts[1:]):
        a, b = np.asarray(a, float), np.asarray(b, float)
        n = _count(float(np.hypot(*(b - a))), step)
        for t in np.linspace(0, 1, n)[1:]:
            out.append(a + (b - a) * t)
    return np.array(out)


def arc(cx, cy, rx, ry, a0, a1, step=STEP):
    """Elliptical arc; angles in degrees, counter-clockwise with y up."""
    length = abs(math.radians(a1 - a0)) * max(rx, ry, 1.0)
    n = _count(length, step)
    t = np.radians(np.linspace(a0, a1, n))
    return np.column_stack([cx + rx * np.cos(t), cy + ry * np.sin(t)])


def bezier(p0, c0, c1, p1, step=STEP):
    p0, c0, c1, p1 = (np.asarray(p, float) for p in (p0, c0, c1, p1))
    length = float(np.hypot(*(c0 - p0)) + np.hypot(*(c1 - c0)) + np.hypot(*(p1 - c1)))
    t = np.linspace(0, 1, _count(length, step))[:, None]
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c0 + 3 * (1 - t) * t**2 * c1 + t**3 * p1


def spline(pts, closed=False, alpha=0.5, step=STEP):
    """A centripetal Catmull-Rom spline through the given points."""
    p = np.asarray(pts, float)
    if closed:
        p = np.vstack([p[-1], p, p[0], p[1]])
    else:
        p = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        t0 = 0.0
        t1 = t0 + max(np.hypot(*(p1 - p0)), 1e-6) ** alpha
        t2 = t1 + max(np.hypot(*(p2 - p1)), 1e-6) ** alpha
        t3 = t2 + max(np.hypot(*(p3 - p2)), 1e-6) ** alpha
        n = _count(float(np.hypot(*(p2 - p1))), step) - 1
        for t in np.linspace(t1, t2, n, endpoint=False):
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    out.append(p[-2] if not closed else out[0])
    return np.array(out)


def join(*parts):
    """Concatenate point lists, dropping the repeated point at each junction."""
    out = [np.asarray(parts[0], float)]
    for part in parts[1:]:
        part = np.asarray(part, float)
        if np.hypot(*(part[0] - out[-1][-1])) < 1e-6:
            part = part[1:]
        out.append(part)
    return np.vstack(out)


def transform(points, sx=1.0, sy=1.0, dx=0.0, dy=0.0, shear=0.0, rotate=0.0, about=(0.0, 0.0)):
    p = np.asarray(points, float) - about
    if rotate:
        c, s = math.cos(math.radians(rotate)), math.sin(math.radians(rotate))
        p = p @ np.array([[c, s], [-s, c]])
    p = np.column_stack([p[:, 0] * sx + p[:, 1] * shear, p[:, 1] * sy])
    return p + about + (dx, dy)


class Nib:
    """A convex pen tip centred on the path."""

    def __init__(self, vertices):
        self.v = np.asarray(vertices, float)

    @classmethod
    def ellipse(cls, rx, ry=None, angle=0.0, n=32):
        ry = rx if ry is None else ry
        t = np.linspace(0, 2 * math.pi, n, endpoint=False)
        pts = np.column_stack([rx * np.cos(t), ry * np.sin(t)])
        return cls(pts @ _rot(angle))

    @classmethod
    def superellipse(cls, rx, ry, power=2.6, n=48, angle=0.0):
        t = np.linspace(0, 2 * math.pi, n, endpoint=False)
        c, s = np.cos(t), np.sin(t)
        pts = np.column_stack(
            [rx * np.sign(c) * np.abs(c) ** (2 / power), ry * np.sign(s) * np.abs(s) ** (2 / power)]
        )
        return cls(pts @ _rot(angle))

    @classmethod
    def rect(cls, w, h, angle=0.0, round_=0.0):
        pts = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
        if round_:
            shape = (
                box(-w / 2, -h / 2, w / 2, h / 2).buffer(-round_, join_style=2).buffer(round_, quad_segs=4)
            )
            pts = np.array(shape.exterior.coords)[:-1]
        return cls(pts @ _rot(angle))

    @property
    def hx(self):
        return float(np.abs(self.v[:, 0]).max())

    @property
    def hy(self):
        return float(np.abs(self.v[:, 1]).max())

    def scaled(self, s):
        return Nib(self.v * s)


def _rot(angle):
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return np.array([[c, s], [-s, c]])


@dataclass
class Stroke:
    points: np.ndarray
    nib: Nib | None = None
    clip: tuple | None = None  # (ymin, ymax): flat terminals at zone lines
    clip_x: tuple | None = None
    taper: tuple = (1.0, 1.0)  # nib scale at the start and the end
    profile: object = None  # optional callable t in 0..1 -> nib scale
    blob: tuple = (0.0, 0.0)  # radius of a terminal ball at the start and the end

    def __post_init__(self):
        self.points = np.asarray(self.points, float)


@dataclass
class Glyph:
    strokes: list = field(default_factory=list)
    shapes: list = field(default_factory=list)  # shapely geometries added as they are
    cuts: list = field(default_factory=list)  # shapely geometries subtracted last
    advance: float | None = None  # fixed advance (monospace); None for automatic spacing
    zone: tuple | None = None  # vertical zone used for spacing, default by case
    kind: str = 'lower'
    role: str = ''  # spacing class hint: round, straight, open, none

    def stroke(self, points, **kw):
        self.strokes.append(Stroke(points, **kw))
        return self

    def add(self, geometry):
        if geometry is not None:
            self.shapes.append(geometry)
        return self


def _hull_union(points, nib_v, scales):
    n = len(points)
    if n == 1:
        return Polygon(points[0] + nib_v * scales[0])
    pieces = []
    for i in range(n - 1):
        a = points[i] + nib_v * scales[i]
        b = points[i + 1] + nib_v * scales[i + 1]
        pieces.append(np.vstack([a, b]))
    stacked = np.stack(pieces)  # (n-1, 2k, 2)
    geoms = shapely.convex_hull(shapely.multipoints(stacked))
    return shapely.union_all(geoms) if len(geoms) > 1 else geoms[0]


def sweep(stroke: Stroke, default_nib: Nib):
    nib = stroke.nib or default_nib
    pts = stroke.points
    n = len(pts)
    if n > 1:
        t = np.linspace(0, 1, n)
        scales = stroke.taper[0] + (stroke.taper[1] - stroke.taper[0]) * t
        if stroke.profile is not None:
            scales = scales * np.array([stroke.profile(x) for x in t])
    else:
        scales = np.array([stroke.taper[0]])
    geom = _hull_union(pts, nib.v, scales)
    for end, radius in ((0, stroke.blob[0]), (-1, stroke.blob[1])):
        if radius:
            x, y = pts[end]
            geom = unary_union([geom, shapely.Point(x, y).buffer(radius, quad_segs=10)])
    if stroke.clip:
        geom = geom.intersection(box(-1e5, stroke.clip[0], 1e5, stroke.clip[1]))
    if stroke.clip_x:
        geom = geom.intersection(box(stroke.clip_x[0], -1e5, stroke.clip_x[1], 1e5))
    return geom


def render(glyph: Glyph, default_nib: Nib):
    """The glyph's outline as a shapely geometry."""
    parts = [sweep(s, default_nib) for s in glyph.strokes] + list(glyph.shapes)
    if not parts:
        return Polygon()
    geom = unary_union(parts)
    for cut in glyph.cuts:
        geom = geom.difference(cut)
    return geom


def polygons(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == 'Polygon':
        return [geom]
    return [g for g in getattr(geom, 'geoms', []) if g.geom_type == 'Polygon' and not g.is_empty]


def jitter(points, amp, scale, rng):
    """Displace a sampled path with smooth noise (hand wobble); the ends move as much as the middle."""
    pts = np.asarray(points, float)
    if len(pts) < 3 or not amp:
        return pts
    seg = np.hypot(*np.diff(pts, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    knots = max(3, int(s[-1] / scale) + 2)
    out = pts.copy()
    for axis in (0, 1):
        values = rng.uniform(-1, 1, knots + 1)
        f = np.clip(s / max(scale, 1e-6), 0, knots - 1e-9)
        i = np.floor(f).astype(int)
        t = f - i
        t = t * t * (3 - 2 * t)
        out[:, axis] += amp * (values[i] * (1 - t) + values[i + 1] * t)
    return out


def resample(points, pitch, inset=0.0):
    """Points spaced `pitch` apart along the path, from `inset` to the end minus `inset`."""
    pts = np.asarray(points, float)
    if len(pts) < 2:
        return pts
    seg = np.hypot(*np.diff(pts, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    total = s[-1]
    lo, hi = inset, total - inset
    if hi <= lo:
        mid = total / 2
        return np.array([[np.interp(mid, s, pts[:, 0]), np.interp(mid, s, pts[:, 1])]])
    n = max(1, int(round((hi - lo) / pitch)))
    ts = np.linspace(lo, hi, n + 1)
    return np.column_stack([np.interp(ts, s, pts[:, 0]), np.interp(ts, s, pts[:, 1])])


def stitch_x(centre, half, arm):
    """One X stitch: two diagonals of half-extent `half` swept with a round nib of radius `arm`."""
    cx, cy = centre
    nib = Nib.ellipse(arm, arm, n=14)
    a = _hull_union(
        np.array([[cx - half + arm, cy - half + arm], [cx + half - arm, cy + half - arm]]), nib.v, np.ones(2)
    )
    b = _hull_union(
        np.array([[cx - half + arm, cy + half - arm], [cx + half - arm, cy - half + arm]]), nib.v, np.ones(2)
    )
    return unary_union([a, b])


def stitch_grid(geom, pitch, threshold=0.4, half=None, arm=None, origin=None, rows=(-4, 14)):
    """Rasterise a geometry onto a cell grid (columns from its left edge, rows up from the baseline) and put one
    X stitch in every cell that is at least `threshold` covered."""
    if geom.is_empty:
        return geom
    half = pitch * 0.47 if half is None else half
    arm = pitch * 0.15 if arm is None else arm
    x0 = geom.bounds[0] if origin is None else origin
    cols = int(math.ceil((geom.bounds[2] - x0) / pitch)) + 1
    stamps = []
    for j in range(rows[0], rows[1]):
        for i in range(cols):
            cell = box(x0 + i * pitch, j * pitch, x0 + (i + 1) * pitch, (j + 1) * pitch)
            if not geom.intersects(cell):
                continue
            if geom.intersection(cell).area / (pitch * pitch) >= threshold:
                stamps.append(stitch_x((x0 + (i + 0.5) * pitch, (j + 0.5) * pitch), half, arm))
    return unary_union(stamps) if stamps else Polygon()
