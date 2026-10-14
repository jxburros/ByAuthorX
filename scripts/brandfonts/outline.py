"""Outlines: ink roughening, polygons to TrueType contours, and SVG path round trips."""

from __future__ import annotations


import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from .geom import polygons


def _periodic_noise(n, knots, rng):
    """Smooth periodic noise sampled at n points using `knots` random values."""
    knots = max(3, int(knots))
    values = rng.uniform(-1, 1, knots)
    x = np.linspace(0, knots, n, endpoint=False)
    i = np.floor(x).astype(int)
    f = x - i
    f = f * f * (3 - 2 * f)
    return values[i % knots] * (1 - f) + values[(i + 1) % knots] * f


def roughen(geom, amp, scale, seed, open_r=0.0, close_r=0.0, step=4.0):
    """Letterpress edges: round the corners, then push the contour in and out with smooth noise."""
    if geom.is_empty:
        return geom
    if close_r:
        geom = geom.buffer(close_r, quad_segs=6).buffer(-close_r, quad_segs=6)
    if open_r:
        geom = geom.buffer(-open_r, quad_segs=6).buffer(open_r, quad_segs=6)
    if not amp:
        return geom
    rng = np.random.default_rng(seed)
    out = []
    for poly in polygons(geom):
        rings = []
        for ring in [poly.exterior, *poly.interiors]:
            ring = shapely.segmentize(ring, step)
            pts = np.array(ring.coords)[:-1]
            n = len(pts)
            if n < 8:
                rings.append(pts)
                continue
            prev, nxt = np.roll(pts, 1, axis=0), np.roll(pts, -1, axis=0)
            tangent = nxt - prev
            norm = np.column_stack([tangent[:, 1], -tangent[:, 0]])
            norm /= np.maximum(np.hypot(norm[:, 0], norm[:, 1]), 1e-9)[:, None]
            length = float(np.hypot(*(pts - prev).T).sum())
            wobble = _periodic_noise(n, length / scale, rng) + 0.45 * _periodic_noise(
                n, length / (scale * 0.37), rng
            )
            swell = 0.65 + 0.35 * _periodic_noise(n, length / (scale * 3.1), rng)
            rings.append(pts + norm * (amp * wobble * swell)[:, None])
        if not rings:
            continue
        shell = Polygon(rings[0], [r for r in rings[1:] if len(r) >= 3])
        out.append(shell if shell.is_valid else shell.buffer(0))
    result = unary_union(out) if out else geom
    return result if result.is_valid else result.buffer(0)


def contours_from_geom(geom, tol=0.6, corner=34.0, long_edge=70.0):
    """TrueType-style contours [(x, y, on_curve), ...]; outer rings clockwise (y up).

    Smooth vertices become off-curve points (a quadratic B-spline through the polygon); corners and the
    ends of long straight edges stay on-curve.
    """
    contours = []
    for poly in polygons(geom):
        poly = orient(poly, sign=-1.0)
        for ring in [poly.exterior, *poly.interiors]:
            ring = ring.simplify(tol, preserve_topology=True)
            pts = np.array(ring.coords)[:-1]
            if len(pts) < 3:
                continue
            contours.append(_classify(pts, corner, long_edge))
    return contours


def _classify(pts, corner, long_edge):
    prev, nxt = np.roll(pts, 1, axis=0), np.roll(pts, -1, axis=0)
    a, b = pts - prev, nxt - pts
    la, lb = np.hypot(*a.T), np.hypot(*b.T)
    cos = (a * b).sum(axis=1) / np.maximum(la * lb, 1e-9)
    turn = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    on = (turn > corner) | (la > long_edge) | (lb > long_edge)
    if not on.any():
        return [(float(x), float(y), False) for x, y in pts]
    # a smooth vertex next to a corner keeps the curve tidy only if its neighbours are on-curve points
    return [(float(x), float(y), bool(o)) for (x, y), o in zip(pts, on)]


def segments(contour):
    """Expand a contour into ('M', p) then ('L', p) / ('Q', c, p) segments with implied points explicit."""
    pts = list(contour)
    n = len(pts)
    if n < 2:
        return []
    start = next((i for i, p in enumerate(pts) if p[2]), None)
    if start is None:
        first = ((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2)
        seq = [(x, y, o) for x, y, o in pts]
        out = [('M', first)]
        for i, (x, y, _) in enumerate(seq):
            nx, ny, _ = seq[(i + 1) % n]
            out.append(('Q', (x, y), ((x + nx) / 2, (y + ny) / 2)))
        return out
    seq = pts[start:] + pts[:start]
    out = [('M', seq[0][:2])]
    pending = []
    for x, y, o in seq[1:] + [seq[0]]:
        if o:
            if not pending:
                out.append(('L', (x, y)))
            else:
                ctrl = pending
                for k, c in enumerate(ctrl[:-1]):
                    mid = ((c[0] + ctrl[k + 1][0]) / 2, (c[1] + ctrl[k + 1][1]) / 2)
                    out.append(('Q', c, mid))
                out.append(('Q', ctrl[-1], (x, y)))
                pending = []
        else:
            pending.append((x, y))
    return out


def svg_path(contours, sx=1.0, sy=1.0, dx=0.0, dy=0.0, digits=3):
    """SVG path data for contours after x' = x*sx + dx, y' = y*sy + dy (use sy < 0 to flip y)."""

    def fmt(p):
        return f'{round(p[0] * sx + dx, digits):g} {round(p[1] * sy + dy, digits):g}'

    parts = []
    for contour in contours:
        for seg in segments(contour):
            if seg[0] == 'M':
                parts.append('M' + fmt(seg[1]))
            elif seg[0] == 'L':
                parts.append('L' + fmt(seg[1]))
            else:
                parts.append('Q' + fmt(seg[1]) + ' ' + fmt(seg[2]))
        parts.append('Z')
    return ' '.join(parts)


def bounds(contours):
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def shift(contours, dx=0.0, dy=0.0):
    return [[(x + dx, y + dy, o) for x, y, o in c] for c in contours]


def quantize(contours):
    """Integer coordinates, dropping contours that collapse; duplicate points are removed."""
    out = []
    for c in contours:
        pts = []
        for x, y, o in c:
            q = (int(round(x)), int(round(y)), o)
            if not pts or (q[0], q[1]) != (pts[-1][0], pts[-1][1]):
                pts.append(q)
        while len(pts) > 1 and (pts[0][0], pts[0][1]) == (pts[-1][0], pts[-1][1]):
            pts.pop()
        if len(pts) >= 3:
            out.append(pts)
    return out


def contours_to_geom(contours, samples=6):
    """Rebuild a shapely geometry from contours (for spacing and kerning measured on the real outline)."""
    rings = []
    for contour in contours:
        pts = []
        cur = None
        for seg in segments(contour):
            if seg[0] == 'M':
                cur = seg[1]
                pts.append(cur)
            elif seg[0] == 'L':
                cur = seg[1]
                pts.append(cur)
            else:
                c, p = seg[1], seg[2]
                for t in np.linspace(0, 1, samples + 1)[1:]:
                    pts.append(
                        (
                            (1 - t) ** 2 * cur[0] + 2 * (1 - t) * t * c[0] + t**2 * p[0],
                            (1 - t) ** 2 * cur[1] + 2 * (1 - t) * t * c[1] + t**2 * p[1],
                        )
                    )
                cur = p
        if len(pts) >= 3:
            rings.append(Polygon(pts))
    if not rings:
        return Polygon()
    rings.sort(key=lambda r: -abs(r.area))
    # even-odd assembly: a ring inside an odd number of others is a hole
    shells, holes = [], []
    for r in rings:
        depth = sum(
            1
            for o in rings
            if o is not r and abs(o.area) > abs(r.area) and o.contains(r.representative_point())
        )
        (holes if depth % 2 else shells).append(r)
    geom = unary_union(shells)
    if holes:
        geom = geom.difference(unary_union(holes))
    return geom
