"""Shared drawing kit for the ByAuthorX logo UI library (icons, illustrations, motion).

Everything is authored as named Vixl vector layers on top of the supplied logo masters. The
hand-drawn character comes from seeded geometry, so every rebuild is byte-for-byte reproducible:
a clean shape goes in, slightly irregular ink comes out, in the spirit of the logo's pen line.
"""

from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path

from vixl import Project

import build_logo_companions as companions

ROOT = Path(__file__).resolve().parents[1]
TOKENS_PATH = ROOT / 'src/byax/resources/brand/tokens.json'

INK = 6.0  # stroke weight on the 96-unit icon grid; the logo's pen line is 5 on a 680-unit master
FINE = 4.6  # interior detail (text lines, hatching)
PEN = 6.0

_NUMBER = re.compile(r'-?\d+(?:\.\d+)?')


def load_sources():
    """Populate the companion module's reference geometry (pen line, ink dot, mark)."""
    companions.TOKENS = json.loads(TOKENS_PATH.read_text(encoding='utf-8'))
    for name, relative in companions.SOURCES.items():
        source = ROOT / relative
        if not source.exists():
            source = ROOT / 'assets/logo-companion-library/reference' / (name + Path(relative).suffix)
        if source.suffix == '.vixl':
            project = Project.load(source, workspace=ROOT)
            companions.PARTS[name] = {
                layer['name']: layer for layer in project.state['layers'] if layer['type'] == 'shape'
            }
            companions.STROKES[name] = {
                op['name']: op
                for node in project.nodes.values()
                for op in node['operations']
                if op.get('type') == 'shape' and op.get('stroke') and op.get('path')
            }
    return companions.TOKENS


def _f(value):
    return f'{value:.2f}'.rstrip('0').rstrip('.') if value != int(value) else str(int(value))


class Hand:
    """Seeded hand-drawn path construction (absolute coordinates, M/L/Q/C/Z only)."""

    def __init__(self, seed, wobble=0.9):
        self.rng = random.Random(seed)
        self.wobble = wobble

    def jitter(self, amplitude=None):
        return self.rng.uniform(-1.0, 1.0) * (self.wobble if amplitude is None else amplitude)

    def points(self, points, amplitude=None):
        return [(x + self.jitter(amplitude), y + self.jitter(amplitude)) for x, y in points]

    def poly(self, points, closed=False, radius=3.0, bow=0.8):
        """Straight-ish edges that bow a little, with softly rounded corners."""
        pts = self.points(points)
        count = len(pts)
        # entry/exit of every vertex: a corner is cut back by `radius` along both edges
        cut = []
        for index, (x, y) in enumerate(pts):
            if not closed and index in (0, count - 1):
                cut.append(((x, y), (x, y)))
                continue
            px, py = pts[index - 1]
            nx, ny = pts[(index + 1) % count]
            back = math.hypot(px - x, py - y) or 1.0
            ahead = math.hypot(nx - x, ny - y) or 1.0
            tb, ta = min(radius / back, 0.45), min(radius / ahead, 0.45)
            cut.append(((x + (px - x) * tb, y + (py - y) * tb), (x + (nx - x) * ta, y + (ny - y) * ta)))

        def point(p):
            return f'{_f(p[0])} {_f(p[1])}'

        def edge(start, end):
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            if not length:
                return f'L{point(end)}'
            nx_, ny_ = -(end[1] - start[1]) / length, (end[0] - start[0]) / length
            offset = self.jitter(bow) if length > 14 else 0.0
            middle = ((start[0] + end[0]) / 2 + nx_ * offset, (start[1] + end[1]) / 2 + ny_ * offset)
            return f'Q{point(middle)} {point(end)}'

        d = [f'M{point(cut[0][1] if closed else cut[0][0])}']
        edges = count if closed else count - 1
        for index in range(edges):
            nxt = (index + 1) % count
            d.append(edge(cut[index][1], cut[nxt][0]))
            if closed or nxt < count - 1:
                d.append(f'Q{point(pts[nxt])} {point(cut[nxt][1])}')
        if closed:
            d.append('Z')
        return ' '.join(d)

    def curve(self, points, closed=False, tension=0.5, amplitude=None):
        """Smooth Catmull-Rom curve through the points."""
        pts = self.points(points, amplitude)
        count = len(pts)
        d = [f'M{_f(pts[0][0])} {_f(pts[0][1])}']
        segments = count if closed else count - 1
        for index in range(segments):
            p0 = pts[(index - 1) % count] if closed or index > 0 else pts[index]
            p1 = pts[index]
            p2 = pts[(index + 1) % count]
            p3 = pts[(index + 2) % count] if closed or index + 2 < count else p2
            c1 = (p1[0] + (p2[0] - p0[0]) * tension / 3 * 2, p1[1] + (p2[1] - p0[1]) * tension / 3 * 2)
            c2 = (p2[0] - (p3[0] - p1[0]) * tension / 3 * 2, p2[1] - (p3[1] - p1[1]) * tension / 3 * 2)
            d.append(f'C{_f(c1[0])} {_f(c1[1])} {_f(c2[0])} {_f(c2[1])} {_f(p2[0])} {_f(p2[1])}')
        if closed:
            d.append('Z')
        return ' '.join(d)

    def ellipse(self, cx, cy, rx, ry=None, count=None, start=-90, angle=0):
        ry = rx if ry is None else ry
        count = count or max(8, min(16, int(max(rx, ry) * 0.55) + 6))
        cos_a, sin_a = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        pts = []
        for index in range(count):
            theta = math.radians(start + 360 * index / count)
            scale = 1 + self.jitter(0.035)
            ex, ey = rx * scale * math.cos(theta), ry * scale * math.sin(theta)
            pts.append((cx + ex * cos_a - ey * sin_a, cy + ex * sin_a + ey * cos_a))
        return self.curve(pts, closed=True, amplitude=0.0)

    def arc(self, cx, cy, radius, start, end, count=None):
        """Open arc, angles in degrees (0 = right, clockwise positive in image space)."""
        count = count or max(3, int(abs(end - start) / 45) + 2)
        pts = []
        for index in range(count):
            angle = math.radians(start + (end - start) * index / (count - 1))
            scale = 1 + self.jitter(0.025)
            pts.append((cx + radius * scale * math.cos(angle), cy + radius * scale * math.sin(angle)))
        return self.curve(pts, amplitude=0.0)

    def line(self, x1, y1, x2, y2, bow=1.0):
        pts = self.points([(x1, y1), (x2, y2)], 0.5)
        length = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]) or 1.0
        nx_, ny_ = -(pts[1][1] - pts[0][1]) / length, (pts[1][0] - pts[0][0]) / length
        offset = self.jitter(bow) if length > 12 else 0.0
        cx, cy = (pts[0][0] + pts[1][0]) / 2 + nx_ * offset, (pts[0][1] + pts[1][1]) / 2 + ny_ * offset
        return f'M{_f(pts[0][0])} {_f(pts[0][1])} Q{_f(cx)} {_f(cy)} {_f(pts[1][0])} {_f(pts[1][1])}'


def localize(d):
    """Shift an absolute path to its own origin; return the path, x, y, width, height."""
    numbers = [float(n) for n in _NUMBER.findall(d)]
    xs, ys = numbers[0::2], numbers[1::2]
    left, top = math.floor(min(xs)), math.floor(min(ys))
    width, height = max(math.ceil(max(xs)) - left, 1), max(math.ceil(max(ys)) - top, 1)
    values = iter(numbers)
    commands = re.split(r'(-?\d+(?:\.\d+)?)', d)
    out, index = [], 0
    for token in commands:
        if _NUMBER.fullmatch(token or ''):
            value = next(values)
            out.append(_f(value - (left if index % 2 == 0 else top)))
            index += 1
        else:
            out.append(token)
    return ''.join(out), left, top, width, height


class UIArt(companions.BrandArt):
    """BrandArt with seeded hand-drawn helpers; one named layer per drawn part."""

    def __init__(self, width, height, theme='light', seed='ui', wobble=0.9):
        super().__init__(width, height, theme)
        self.hand = Hand(seed, wobble)
        self.tint = {
            'ink': self.c['ink'],
            'pen': self.c['green'],
            'red': self.c['red'],
            'muted': self.c['muted'],
            'paper': self.c['paper'],
            'soft': self.c['soft'],
            'rule': self.c['rule'],
        }

    def waive_rhythm(self, reason):
        """Record a deliberate composition waiver (the finding stays listed as informational)."""
        self.ops.append(dict(type='waiver', check='composition', reason=reason))

    # -- strokes ----------------------------------------------------------------------
    def stroke(self, name, d, color='ink', sw=INK, opacity=None, fill='none'):
        local, x, y, w, h = localize(d)
        paint = self.tint.get(color, color)
        self.shape(
            name,
            'path',
            x,
            y,
            w,
            h,
            'none' if fill == 'none' else self.tint.get(fill, fill),
            paint,
            sw,
            path=local,
        )
        if opacity is not None:
            self.ops.append(dict(type='opacity', target=name, value=opacity))
        return name

    def ink_poly(self, name, points, closed=False, radius=3.0, sw=INK, color='ink', **kwargs):
        return self.stroke(name, self.hand.poly(points, closed, radius), color, sw, **kwargs)

    def ink_curve(self, name, points, closed=False, sw=INK, color='ink', **kwargs):
        return self.stroke(name, self.hand.curve(points, closed), color, sw, **kwargs)

    def ink_circle(self, name, cx, cy, rx, ry=None, sw=INK, color='ink', angle=0, **kwargs):
        return self.stroke(name, self.hand.ellipse(cx, cy, rx, ry, angle=angle), color, sw, **kwargs)

    def ink_arc(self, name, cx, cy, radius, start, end, sw=INK, color='ink', **kwargs):
        return self.stroke(name, self.hand.arc(cx, cy, radius, start, end), color, sw, **kwargs)

    def ink_line(self, name, x1, y1, x2, y2, sw=INK, color='ink', **kwargs):
        return self.stroke(name, self.hand.line(x1, y1, x2, y2), color, sw, **kwargs)

    def text_line(self, name, x, y, length, sw=FINE, color='ink'):
        """A line of 'text': a short wavy rule, as on the companion pages."""
        sag = self.hand.jitter(1.2)
        d = f'M{_f(x)} {_f(y + self.hand.jitter(0.5))} Q{_f(x + length * 0.5)} {_f(y + sag)} {_f(x + length)} {_f(y + self.hand.jitter(0.5))}'
        return self.stroke(name, d, color, sw)

    def pen_poly(self, name, points, closed=False, radius=3.0, sw=PEN, **kwargs):
        return self.ink_poly(name, points, closed, radius, sw, 'pen', **kwargs)

    def pen_curve(self, name, points, closed=False, sw=PEN, **kwargs):
        return self.ink_curve(name, points, closed, sw, 'pen', **kwargs)

    def pen_line(self, name, x1, y1, x2, y2, sw=PEN, **kwargs):
        return self.ink_line(name, x1, y1, x2, y2, sw, 'pen', **kwargs)

    def pen_arc(self, name, cx, cy, radius, start, end, sw=PEN, **kwargs):
        return self.ink_arc(name, cx, cy, radius, start, end, sw, 'pen', **kwargs)

    def squiggle(self, name, x1, x2, y, color='red', amp=5.0, wavelength=15.0, sw=3.4):
        """A hand-inked proofreader's squiggle: uneven crests, softly rounded."""
        count = max(3, int((x2 - x1) / (wavelength / 2)))
        points = []
        for index in range(count + 1):
            sign = 1 if index % 2 == 0 else -1
            points.append((x1 + (x2 - x1) * index / count, y + sign * amp * (1 + self.hand.jitter(0.22))))
        return self.stroke(name, self.hand.curve(points), color, sw)

    def sparkle(self, name, cx, cy, size=12, sw=PEN, color='pen'):
        """A hand-inked four-point star."""
        r, q = size, size * 0.3
        points = [
            (cx, cy - r),
            (cx + q, cy - q),
            (cx + r, cy),
            (cx + q, cy + q),
            (cx, cy + r),
            (cx - q, cy + q),
            (cx - r, cy),
            (cx - q, cy - q),
        ]
        return self.ink_poly(name, points, closed=True, radius=1.2, sw=sw, color=color)

    # -- fills ------------------------------------------------------------------------
    def solid(self, name, d, color='ink'):
        local, x, y, w, h = localize(d)
        self.shape(name, 'path', x, y, w, h, self.tint.get(color, color), None, 0, path=local)
        return name

    def bullet(self, name, cx, cy, r=4.2, color='ink'):
        return self.solid(name, self.hand.ellipse(cx, cy, r, count=7), color)

    # -- the supplied red ink blot, at an exact centre ---------------------------------
    def ink_dot(self, name, cx, cy, size=13):
        scale = size / 17.5
        return self.blot(name, cx - 8.3 * scale, cy - 8.7 * scale, size)

    # -- shared forms ------------------------------------------------------------------
    def page_outline(self, name, x, y, w, h, fold=14, sw=INK):
        """A working sheet with a folded corner, outlined in ink."""
        pts = [(x, y), (x + w - fold, y), (x + w, y + fold), (x + w, y + h), (x, y + h)]
        self.ink_poly(name, pts, closed=True, radius=2.6, sw=sw)
        if fold:
            self.ink_poly(
                name + '-fold',
                [(x + w - fold, y), (x + w - fold, y + fold), (x + w, y + fold)],
                radius=1.6,
                sw=FINE,
            )
        return name

    def sheet(self, name, x, y, w, h, sw=INK):
        """A plain sheet (no fold)."""
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        return self.ink_poly(name, pts, closed=True, radius=2.8, sw=sw)
