"""Hand-inked strokes for interface marks, generated live so they fit any word.

The logo's underline is a pen line with a slight wave. These helpers draw proofreader's marks and
authored strikethrough or underline the same way: uneven crests, a little drift, round ends. Every
stroke is deterministic for its seed, so a mark never shimmers between repaints. With ``hand=False``
the same calls return plain regular lines, for anyone who prefers them.
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainterPath, QPen

SQUIGGLE_AMPLITUDE = 1.9
SQUIGGLE_WAVELENGTH = 7.0


def _through(points):
    """A smooth Catmull-Rom curve through the points."""
    path = QPainterPath(QPointF(*points[0]))
    count = len(points)
    for index in range(count - 1):
        p0 = points[index - 1] if index else points[index]
        p1, p2 = points[index], points[index + 1]
        p3 = points[index + 2] if index + 2 < count else p2
        path.cubicTo(
            QPointF(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6),
            QPointF(p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6),
            QPointF(*p2),
        )
    return path


def squiggle(x1, x2, y, seed, *, hand=True, amplitude=SQUIGGLE_AMPLITUDE, wavelength=SQUIGGLE_WAVELENGTH):
    """A wavy proofreader's line under [x1, x2] at baseline offset ``y``."""
    width = max(x2 - x1, 2.0)
    half = wavelength / 2
    if not hand:
        path = QPainterPath(QPointF(x1, y))
        steps = max(2, round(width / half))
        for step in range(1, steps + 1):
            path.lineTo(x1 + width * step / steps, y + (amplitude if step % 2 else -amplitude))
        return path
    rng = random.Random(seed)
    count = max(3, round(width / half))
    points = []
    for index in range(count + 1):
        taper = 0.55 if index in (0, count) else 1.0
        sign = 1 if index % 2 == 0 else -1
        crest = amplitude * taper * (0.72 + 0.56 * rng.random())
        drift = rng.uniform(-0.3, 0.3)
        x = x1 + width * index / count + rng.uniform(-0.12, 0.12) * half * (0 < index < count)
        points.append((x, y + sign * crest + drift))
    return _through(points)


def rule(x1, x2, y, seed, *, hand=True, drift=0.5):
    """A nearly straight pen line (authored underline or strikethrough) from x1 to x2 at ``y``."""
    if not hand or x2 - x1 < 6:
        path = QPainterPath(QPointF(x1, y))
        path.lineTo(x2, y)
        return path
    rng = random.Random(seed)
    count = max(2, round((x2 - x1) / 16))
    points = []
    for index in range(count + 1):
        end = index in (0, count)
        x = x1 + (x2 - x1) * index / count + (rng.uniform(-0.5, 0.5) if end else 0)
        points.append((x, y + rng.uniform(-drift, drift) * (0.5 if end else 1.0)))
    # The pen leaves the page a touch lower than it landed, as a quick stroke does.
    points[-1] = (points[-1][0], points[-1][1] + rng.uniform(0.0, 0.45))
    return _through(points)


def pen(color, width):
    stroke = QPen(color, width)
    stroke.setCapStyle(Qt.RoundCap)
    stroke.setJoinStyle(Qt.RoundJoin)
    return stroke


def line_weight(font_pixel_size):
    """Stroke weight that sits comfortably against the text it marks."""
    return max(1.1, round(font_pixel_size / 15.0, 2))


def reveal_fraction(elapsed_ms, duration_ms):
    """Ease-out progress of a stroke being written, 0..1."""
    if duration_ms <= 0:
        return 1.0
    t = min(max(elapsed_ms / duration_ms, 0.0), 1.0)
    return 1 - math.pow(1 - t, 3)
