"""Optical spacing and kerning measured on the finished outlines.

Each glyph gets left and right profiles: how far the ink sits from its extreme edge at every height in its
zone, with open shapes capped at a depth. A side's bearing is the nominal bearing minus the white space the
shape already carries (a round or open side needs less). Pair kerning compares the white area between two
positioned glyphs with the white area between two straight stems of the same case.
"""

from __future__ import annotations

import numpy as np
import shapely

ROW = 6.0


class Profile:
    """Left and right ink edges per row for one glyph."""

    def __init__(self, geom, y0, y1, step=ROW):
        self.ys = np.arange(y0 + step / 2, y1, step)
        self.empty = geom.is_empty
        if self.empty:
            self.left = np.full(len(self.ys), np.nan)
            self.right = np.full(len(self.ys), np.nan)
            self.xmin = self.xmax = 0.0
            return
        xmin, _, xmax, _ = geom.bounds
        self.xmin, self.xmax = xmin, xmax
        xs = np.linspace(xmin, xmax, max(8, int((xmax - xmin) / 3)) + 1)
        gx, gy = np.meshgrid(xs, self.ys)
        inside = shapely.contains_xy(geom, gx.ravel(), gy.ravel()).reshape(gx.shape)
        any_ink = inside.any(axis=1)
        first = inside.argmax(axis=1)
        last = inside.shape[1] - 1 - inside[:, ::-1].argmax(axis=1)
        self.left = np.where(any_ink, xs[first], np.nan)
        self.right = np.where(any_ink, xs[last], np.nan)

    def gaps(self, depth):
        """Ink distance from the extreme edge on each side, capped at depth; rows without ink count as depth."""
        left = np.where(np.isnan(self.left), depth, np.minimum(self.left - self.xmin, depth))
        right = np.where(np.isnan(self.right), depth, np.minimum(self.xmax - self.right, depth))
        return left, right


def sidebearings(profile, nominal, depth, share=1.0, minimum=6.0):
    left, right = profile.gaps(depth)
    return (
        max(minimum, nominal - share * float(left.mean())),
        max(minimum, nominal - share * float(right.mean())),
    )


def pair_gap(a, b, adv_a, cap, rows):
    """Mean optical gap between the right of a and the left of b (b set at adv_a), over the first `rows` rows."""
    ra = adv_a - a.right[:rows]
    lb = b.left[:rows]
    gap = np.where(np.isnan(ra) | np.isnan(lb), cap, np.minimum(ra + lb, cap))
    return float(gap.mean())


def kern(a, b, adv_a, cap, rows, reference, strength=0.5, limit=150.0):
    if a.empty or b.empty or rows <= 0:
        return 0.0
    value = -strength * (pair_gap(a, b, adv_a, cap, rows) - reference)
    return max(-limit, min(limit * 0.3, value))
