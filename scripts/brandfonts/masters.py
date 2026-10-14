"""Editable glyph masters: one Vixl document per family, one vector path layer per drawn glyph.

The grid is the coordinate system: a glyph's font coordinates come from where its path sits inside its cell
(x from the cell's left edge, y up from the row's baseline), so edit points with the pen tool and leave the
layers where they are. Spacing and kerning are not stored; the build recomputes them from the outlines.
"""

from __future__ import annotations

import re

from fontTools.pens.recordingPen import RecordingPen
from fontTools.svgLib.path import parse_path
from shapely.geometry import Polygon
from shapely.ops import unary_union

from . import outline

SCALE = 0.13  # pixels per font unit
COLS = 12
CELL_W = 150
CELL_H = 210
MARGIN = 30
BASE = 150  # baseline offset from the top of a cell, in pixels
INK = '#252927'
PAPER = '#F4EEE3'
RULE = '#D7D0C3'
GREEN = '#367B6C'
LAYER = re.compile(r'^g-(\d{3})-(.+)$')


def sheet_size(count):
    rows = -(-count // COLS)
    return MARGIN * 2 + COLS * CELL_W, MARGIN * 2 + rows * CELL_H


def cell_origin(index):
    """(left, baseline) of a grid cell in pixels."""
    col, row = index % COLS, index // COLS
    return MARGIN + col * CELL_W, MARGIN + row * CELL_H + BASE


def glyph_operations(compiled, names):
    """Operations that draw the glyphs `names` (in grid order) as path layers."""
    ops = []
    for index, name in enumerate(names):
        glyph = compiled.glyphs[name]
        contours = glyph['contours']
        box = outline.bounds(contours)
        if not box:
            continue
        left, base = cell_origin(index)
        x0, y0, x1, y1 = box
        d = outline.svg_path(contours, SCALE, -SCALE, -x0 * SCALE, y1 * SCALE, digits=2)
        ops.append(
            dict(
                type='shape',
                name=f'g-{index:03d}-{name}',
                shape='path',
                x=round(left + x0 * SCALE, 2),
                y=round(base - y1 * SCALE, 2),
                width=max(1, round((x1 - x0) * SCALE, 2)),
                height=max(1, round((y1 - y0) * SCALE, 2)),
                fill=INK,
                path=d,
            )
        )
    return ops


def guide_operations(compiled, count, metrics):
    """Thin metric lines (baseline, x-height, cap height) under every row of the grid."""
    rows = -(-count // COLS)
    width = COLS * CELL_W
    ops, names = [], []
    for row in range(rows):
        base = MARGIN + row * CELL_H + BASE
        for label, height, color in (
            ('baseline', 0, GREEN),
            ('xheight', metrics.xh, RULE),
            ('cap', metrics.cap, RULE),
        ):
            name = f'guide-{row:02d}-{label}'
            y = base - height * SCALE
            ops.append(
                dict(
                    type='shape',
                    name=name,
                    shape='rectangle',
                    x=MARGIN,
                    y=round(y - 0.5, 2),
                    width=width,
                    height=1,
                    fill=color,
                )
            )
            names.append(name)
    return ops, names


def read_paths(project):
    """{grid index: (name, geometry in font units)} from a glyph sheet opened as a Vixl Project."""
    out = {}
    for layer in project.state['layers']:
        match = LAYER.match(layer['name'])
        if not match or layer.get('type') != 'shape' or 'path' not in layer:
            continue
        index, name = int(match.group(1)), match.group(2)
        left, base = cell_origin(index)
        out[name] = geometry_from_path(layer['path'], layer, left, base)
    return out


def geometry_from_path(d, layer, left, base):
    """Font-unit geometry of a layer's path (any M/L/Q/C/Z data) given its cell's left edge and baseline."""
    view_w, view_h = layer.get('path_view') or (layer['width'], layer['height'])
    sx = layer['width'] / view_w if view_w else 1.0
    sy = layer['height'] / view_h if view_h else 1.0
    pen = RecordingPen()
    parse_path(d, pen)
    rings = []
    ring, cur = [], None
    for op, args in pen.value:
        if op == 'moveTo':
            ring, cur = [args[0]], args[0]
        elif op == 'lineTo':
            ring.append(args[0])
            cur = args[0]
        elif op == 'qCurveTo':
            for ctrl, end in _quads(cur, args):
                ring += _sample_quad(cur, ctrl, end)
                cur = end
        elif op == 'curveTo':
            ring += _sample_cubic(cur, *args)
            cur = args[-1]
        elif op in ('closePath', 'endPath'):
            if len(ring) >= 3:
                rings.append(ring)
            ring = []
    polys = []
    for ring in rings:
        polys.append(
            Polygon(
                [
                    ((layer['x'] - left + px * sx) / SCALE, (base - (layer['y'] + py * sy)) / SCALE)
                    for px, py in ring
                ]
            )
        )
    return _assemble(polys)


def _quads(start, points):
    """Split a TrueType-style run of off-curve points ending on an on-curve point into quadratic segments."""
    *offs, end = points
    for i, c in enumerate(offs):
        nxt = end if i == len(offs) - 1 else ((c[0] + offs[i + 1][0]) / 2, (c[1] + offs[i + 1][1]) / 2)
        yield c, nxt


def _sample_quad(p0, c, p1, n=8):
    return [
        (
            (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t**2 * p1[0],
            (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t**2 * p1[1],
        )
        for t in [i / n for i in range(1, n + 1)]
    ]


def _sample_cubic(p0, c0, c1, p1, n=12):
    return [
        (
            (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * c0[0] + 3 * (1 - t) * t**2 * c1[0] + t**3 * p1[0],
            (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * c0[1] + 3 * (1 - t) * t**2 * c1[1] + t**3 * p1[1],
        )
        for t in [i / n for i in range(1, n + 1)]
    ]


def _assemble(polys):
    """Even-odd assembly of rings into one geometry: a ring inside an odd number of others is a hole."""
    polys = [p if p.is_valid else p.buffer(0) for p in polys if not p.is_empty]
    polys.sort(key=lambda p: -p.area)
    shells, holes = [], []
    for p in polys:
        depth = sum(
            1 for o in polys if o is not p and o.area > p.area and o.contains(p.representative_point())
        )
        (holes if depth % 2 else shells).append(p)
    geom = unary_union(shells) if shells else Polygon()
    return geom.difference(unary_union(holes)) if holes else geom


def master_names(compiled):
    """The drawn glyphs (accented composites are rebuilt from them), in grid order."""
    return [
        name
        for name in compiled.order
        if name != '.notdef'
        and not compiled.glyphs[name].get('composite')
        and compiled.glyphs[name]['contours']
    ]


def build_sheet(compiled, root, metrics):
    """The editable glyph sheet for a compiled family as an unsaved Vixl Project, and its check result."""
    from vixl import Project

    names = master_names(compiled)
    width, height = sheet_size(len(names))
    project = Project(width, height, PAPER, workspace=root)
    guides, guide_names = guide_operations(compiled, len(names), metrics)
    glyphs = glyph_operations(compiled, names)
    result = project.apply(
        [dict(type='accessibility', lang='en', title=f'{compiled.spec.family} glyph masters')]
        + guides
        + [dict(type='group', name='metrics', targets=guide_names)]
        + glyphs
    )
    check = project.check(checks=['bounds'])
    return project, dict(
        glyph_layers=len(glyphs), warnings=result.get('warnings'), check_passed=check['passed']
    )
