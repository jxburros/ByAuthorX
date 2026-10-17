"""Flowcharts, guides and a booklet that explain ByAuthorX, authored and checked in Vixl.

Every sheet is a Vixl document (`docs/visual-guides/vixl/*.vixl`) with exports beside it, and the booklet
is one multi-page document exported as a PDF. The look follows docs/brand.md: paper, ink and pen green,
Irongall for text, Scholium for helper lines, Onionskin for counters, and the approved wordmark, icons,
illustrations and hand-inked rules, never redrawn.

Boxes, connectors, arrowheads and badges are hand-drawn the way the app's own artwork is
(scripts/logo_ui_art.py): seeded, slightly irregular pen strokes, so a rebuild is byte-for-byte
reproducible. Everything sits on an explicit grid; there is no automatic layout.

    python -m venv .venv-assets
    .venv-assets/bin/python -m pip install -r assets/brand-fonts/requirements.txt
    .venv-assets/bin/python scripts/build_visual_guides.py [--only SHEET] [--no-booklet]
"""

from __future__ import annotations

import argparse
import math
import random
import re
import sys
from pathlib import Path

from PIL import Image, ImageFont
from vixl import Project
from vixl.fonts import import_font

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'visual-guides'
FONTS = ROOT / 'assets' / 'brand-fonts' / 'fonts'
UI = ROOT / 'assets' / 'logo-ui-library'
BRAND = ROOT / 'design' / 'brand' / 'kit'
LOGO = BRAND / 'logos' / 'primary.png'
INK_DOT = BRAND / 'graphics' / 'ink-dot.png'
RULE_H = UI / 'rules' / 'rule-h-light@2x.png'
UNDERLINE = UI / 'rules' / 'tab-underline-light@2x.png'

PAPER, INK, GREEN, RED = '#F4EEE3', '#252927', '#367B6C', '#AD283E'
SURFACE, RULE, MUTED, DEEP = '#FFFCF7', '#D7D0C3', '#52615B', '#246052'
W, H = 1600, 1000
M = 80  # page margin; every sheet's content sits between M and W - M
FACES = {
    'irongall': 'irongall/Irongall-Regular.ttf',
    'scholium': 'scholium/Scholium-Regular.ttf',
    'onionskin': 'onionskin/Onionskin-Regular.ttf',
}
LINE_HEIGHT = 1.2
PEN = 3.0  # outline weight on the 1600 px page; the app's icons use 6 on a 96 grid
FINE = 2.4

_NUMBER = re.compile(r'-?\d+(?:\.\d+)?')
_FONT_CACHE = {}


def _f(value):
    return f'{value:.2f}'.rstrip('0').rstrip('.') if value != int(value) else str(int(value))


class Hand:
    """Seeded hand-drawn path construction (absolute coordinates, M/L/Q/C/Z only), after logo_ui_art.Hand."""

    def __init__(self, seed, wobble=0.9):
        self.rng = random.Random(seed)
        self.wobble = wobble

    def jitter(self, amplitude=None):
        return self.rng.uniform(-1.0, 1.0) * (self.wobble if amplitude is None else amplitude)

    def points(self, points, amplitude=None):
        return [(x + self.jitter(amplitude), y + self.jitter(amplitude)) for x, y in points]

    def poly(self, points, closed=False, radius=3.0, bow=0.8, amplitude=None):
        """Straight-ish edges that bow a little, with softly rounded corners."""
        pts = self.points(points, amplitude)
        count = len(pts)
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
        for index in range(count if closed else count - 1):
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
        for index in range(count if closed else count - 1):
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

    def ellipse(self, cx, cy, rx, ry=None, count=None):
        ry = rx if ry is None else ry
        count = count or max(10, min(18, int(max(rx, ry) * 0.5) + 6))
        pts = []
        for index in range(count):
            theta = math.radians(-90 + 360 * index / count)
            scale = 1 + self.jitter(0.03)
            pts.append((cx + rx * scale * math.cos(theta), cy + ry * scale * math.sin(theta)))
        return self.curve(pts, closed=True, amplitude=0.0)

    def line(self, x1, y1, x2, y2, bow=1.2):
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
    out, index = [], 0
    for token in re.split(r'(-?\d+(?:\.\d+)?)', d):
        if _NUMBER.fullmatch(token or ''):
            out.append(_f(float(token) - (left if index % 2 == 0 else top)))
            index += 1
        else:
            out.append(token)
    return ''.join(out), left, top, width, height


def measure(face, size, text):
    key = (face, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(str(FONTS / FACES[face]), size)
    return _FONT_CACHE[key].getlength(text) * 1.05  # a margin for Vixl's kerning and rounding


def wrap(text, face, size, width):
    lines = []
    for paragraph in text.split('\n'):
        current = ''
        for word in paragraph.split(' '):
            trial = f'{current} {word}'.strip()
            if current and measure(face, size, trial) > width:
                lines.append(current)
                current = word
            else:
                current = trial
        lines.append(current)
    return lines


class Sheet:
    """One page of ops: hand-drawn shapes, text and images, with names kept unique."""

    def __init__(self, key, ref, kicker, title, lede, alt, prefix='', wobble=1.0):
        self.prefix = prefix
        self.wobble = wobble
        self.meta = dict(key=key, ref=ref, kicker=kicker, title=title, lede=lede, alt=alt)
        self.hand = Hand(key, 0.9 * wobble)
        self.ops = []
        self.count = 0
        self.nodes = {}

    def name(self, kind):
        self.count += 1
        return f'{self.prefix}{kind}-{self.count}'

    # -- strokes --------------------------------------------------------------------
    def path(self, name, d, stroke=INK, sw=PEN, fill='none'):
        local, x, y, w, h = localize(d)
        op = dict(type='shape', name=name, shape='path', x=x, y=y, width=w, height=h, fill=fill, path=local)
        if stroke:
            op.update(stroke=stroke, stroke_width=sw, line_cap='round', line_join='round')
        self.ops.append(op)

    def box(self, x, y, w, h, fill=SURFACE, stroke=INK, sw=PEN, radius=10):
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        self.path(
            self.name('box'),
            self.hand.poly(pts, True, radius, bow=1.0 * self.wobble, amplitude=0.7 * self.wobble),
            stroke,
            sw,
            fill,
        )

    def pill(self, x, y, w, h, fill=SURFACE, stroke=INK, sw=PEN):
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        self.path(
            self.name('pill'),
            self.hand.poly(pts, True, h / 2, bow=1.0 * self.wobble, amplitude=0.6 * self.wobble),
            stroke,
            sw,
            fill,
        )

    def diamond(self, cx, cy, w, h, fill=SURFACE, stroke=INK, sw=PEN):
        pts = [(cx, cy - h / 2), (cx + w / 2, cy), (cx, cy + h / 2), (cx - w / 2, cy)]
        self.path(
            self.name('diamond'),
            self.hand.poly(pts, True, 7, bow=1.0 * self.wobble, amplitude=0.7 * self.wobble),
            stroke,
            sw,
            fill,
        )

    def circle(self, cx, cy, r, fill=GREEN, stroke=None, sw=PEN):
        self.path(self.name('disc'), self.hand.ellipse(cx, cy, r), stroke, sw, fill)

    def stroke(self, points, color=INK, sw=PEN, radius=14, bow=1.0):
        """An open pen line through the points, corners softly rounded."""
        self.path(
            self.name('line'),
            self.hand.poly(points, False, radius, bow=bow * self.wobble, amplitude=0.6 * self.wobble),
            color,
            sw,
        )

    def arrow(self, points, color=INK, sw=PEN, radius=14, label=None):
        """A pen line ending in an open chevron arrowhead; the line stops at the last point."""
        self.stroke(points, color, sw, radius)
        (x1, y1), (x2, y2) = points[-2], points[-1]
        angle = math.atan2(y2 - y1, x2 - x1)
        head = 15
        wings = [
            (
                x2 - head * math.cos(angle + s * math.radians(28)),
                y2 - head * math.sin(angle + s * math.radians(28)),
            )
            for s in (1, -1)
        ]
        self.path(
            self.name('head'),
            self.hand.poly([wings[0], (x2, y2), wings[1]], False, 1.5, amplitude=0.4 * self.wobble),
            color,
            sw,
        )

    def wave(self, x1, x2, y, color=RULE, sw=FINE, bow=1.6):
        self.path(self.name('rule'), self.hand.line(x1, y, x2, y, bow * self.wobble), color, sw)

    # -- text -----------------------------------------------------------------------
    def text(
        self,
        label,
        x,
        cy,
        w,
        size=22,
        color=INK,
        face='irongall',
        align='left',
        lh=LINE_HEIGHT,
        max_h=None,
        top=False,
    ):
        """Wrapped text whose block is centred on cy (or starts at cy when top); returns its height. x is the left edge, the centre for 'center' or the right edge for 'right'."""
        lines = wrap(label, face, size, w)
        h = len(lines) * size * lh
        if max_h and h > max_h:
            raise ValueError(f'{label!r} needs {len(lines)} lines ({h:.0f}px) but the box allows {max_h}px')
        name = self.name('text')
        left = x - w / 2 if align == 'center' else (x - w if align == 'right' else x)
        y0 = cy if top else cy - h / 2
        self.ops += [
            dict(
                type='text',
                name=name,
                text='\n'.join(lines),
                font=face,
                size=size,
                color=color,
                x=left,
                y=y0,
                align=align,
                line_height=lh,
            ),
            dict(type='text-layout', target=name, width=w, height=round(h + 4), fit=False),
        ]
        return h

    def image(self, path, x, y, w):
        with Image.open(path) as picture:
            ratio = picture.height / picture.width
        self.ops.append(
            dict(
                type='add',
                name=self.name('image'),
                path=str(path),
                x=x,
                y=y,
                width=w,
                height=round(w * ratio, 2),
            )
        )

    def icon(self, key, x, y, size=64):
        self.image(UI / 'icons' / f'{key}-light.png', x, y, size)

    def badge(self, n, cx, cy, r=22):
        self.circle(cx, cy, r, GREEN)
        self.text(str(n), cx, cy, 2 * r, size=r * 1.15, color='#FFFFFF', align='center', lh=1.0)

    # -- flowchart nodes --------------------------------------------------------------
    def node(self, nid, kind, cx, cy, w, h, label, key=False, size=22):
        fill, stroke, color = (GREEN, DEEP, '#FFFFFF') if key else (SURFACE, INK, INK)
        if kind == 'diamond':
            self.diamond(cx, cy, w, h, fill, stroke)
            inner, room = w * 0.58, h * 0.7
        elif kind == 'pill':
            self.pill(cx - w / 2, cy - h / 2, w, h, fill, stroke)
            inner, room = w - 2 * (h / 2) + 14, h - 10
        else:
            self.box(cx - w / 2, cy - h / 2, w, h, fill, stroke)
            inner, room = w - 28, h - 12
        self.text(label, cx, cy, inner, size, color, align='center', max_h=room)
        self.nodes[nid] = dict(cx=cx, cy=cy, w=w, h=h)
        return self.nodes[nid]

    def port(self, nid, side, gap=3):
        n = self.nodes[nid]
        return {
            'l': (n['cx'] - n['w'] / 2 - gap, n['cy']),
            'r': (n['cx'] + n['w'] / 2 + gap, n['cy']),
            't': (n['cx'], n['cy'] - n['h'] / 2 - gap),
            'b': (n['cx'], n['cy'] + n['h'] / 2 + gap),
        }[side]

    def link(self, a, b, sides='rl', via=(), label=None, at=None, color=INK):
        """Arrow from node a's side to node b's side through the waypoints in `via`."""
        points = [self.port(a, sides[0], 3), *via, self.port(b, sides[1], 4)]
        self.arrow(points, color)
        if label:
            self.text(label, at[0], at[1], 120, 19, DEEP, align='center')

    def label(self, label, x, cy, face='onionskin', color=DEEP, size=17, w=520):
        self.text(label.upper(), x, cy, w, size, color, face)


# --------------------------------------------------------------------------- page chrome


def chrome(sheet, page, total):
    meta = sheet.meta
    s = Sheet('chrome-' + meta['key'], '', '', '', '', '', prefix='page-')
    s.ops.append(dict(type='accessibility', lang='en', title=f'ByAuthorX: {meta["title"]}'))
    s.image(LOGO, M, 44, 240)
    s.text(meta['kicker'].upper(), W - M, 76, 700, 17, MUTED, 'onionskin', 'right')
    s.text(meta['title'], M, 166, W - 2 * M, 56, INK, lh=1.0)
    s.image(INK_DOT, M, 212, 18)
    s.image(UNDERLINE, M + 14, 217, 340)
    s.text(meta['lede'], M, 262, W - 2 * M, 24, INK, 'scholium')
    s.image(RULE_H, M, 928, W - 2 * M)
    s.text('Your words. Your call.', M, 966, 600, 20, MUTED)
    s.text(f'{meta["ref"]}   {page} / {total}', W - M, 966, 700, 17, MUTED, 'onionskin', 'right')
    return s.ops


# --------------------------------------------------------------------------- flowcharts


def proposals():
    s = Sheet(
        'proposals',
        'Flowchart 1',
        'Flowchart',
        'Nothing changes your text until you accept it',
        'Everything that wants to change your writing arrives as a proposal first.',
        'Six sources of suggestions flow into Proposed changes. You accept, reject or postpone each one.',
    )
    sources = [
        'Spelling and grammar checks',
        'Fiction and document checks',
        "An editor's changes from Word",
        "A beta reader's suggestion",
        'The assistant in Work mode',
        'A saved draft or synced copy',
    ]
    pitch, top = 94, 366
    s.label('Something suggests', M, 320)
    s.text('PROPOSED', 670, 320, 300, 17, DEEP, 'onionskin', 'center')
    s.text('YOU DECIDE', 965, 320, 300, 17, DEEP, 'onionskin', 'center')
    s.label('What happens', 1230, 320, w=290)
    ys = [top + i * pitch for i in range(6)]
    mid = (ys[0] + ys[-1]) / 2
    bus = 485
    for i, (label, cy) in enumerate(zip(sources, ys)):
        s.node(f's{i}', 'box', M + 175, cy, 350, 70, label)
        s.stroke([(M + 350 + 3, cy), (bus, cy)], bow=0.6)
    s.stroke([(bus, ys[0]), (bus, ys[-1])], radius=1)
    s.node('p', 'box', 670, mid, 220, 110, 'Proposed changes', key=True)
    s.arrow([(bus, mid), (s.port('p', 'l', 4)[0], mid)])
    s.node('d', 'diamond', 965, mid, 250, 150, 'You decide, one by one')
    s.link('p', 'd', 'rl')
    bus2 = 1130
    outs = [
        ('accept', 'Your text changes. One revision, which you can undo.', mid - 190),
        ('not yet', 'Reply, or leave it pending for later.', mid),
        ('reject', 'Your text stays exactly as it is.', mid + 190),
    ]
    s.stroke([s.port('d', 'r', 3), (bus2, mid)], radius=1)
    s.stroke([(bus2, outs[0][2]), (bus2, outs[2][2])], radius=1)
    for i, (label, text, cy) in enumerate(outs):
        s.node(f'o{i}', 'box', 1375, cy, 290, 100, text, key=(i == 0))
        s.arrow([(bus2, cy), s.port(f'o{i}', 'l', 4)])
        s.text(label, (bus2 + 1230) / 2, cy - 20, 90, 19, DEEP, align='center')
    return s


def share():
    s = Sheet(
        'share',
        'Flowchart 2',
        'Flowchart',
        'How do I share this?',
        'Your master never leaves your computer unless you send it. Every route makes a copy.',
        'A decision tree from "Who is it for?" to Word review, beta readers, compile for publishing or a plain export.',
    )
    chains = [
        [
            'An editor who uses Word',
            'File → Send for review (Word)…',
            'File → Merge reviewed copy…',
            "The editor's changes arrive as proposals",
        ],
        [
            'Readers who read and comment',
            'File → Send to beta readers…',
            'File → Import reader comments…',
            'Comments and proposals, each named',
        ],
        [
            'A printer, shop or store',
            'File → Compile…',
            'Choose a preset and run preflight',
            'A print PDF, EPUB or Word file',
        ],
        [
            'Someone who needs a copy',
            'File → Export',
            'PDF, Word, EPUB or Markdown',
            'Check what it left out',
        ],
    ]
    ys = [372 + i * 140 for i in range(4)]
    mid = (ys[0] + ys[-1]) / 2
    s.node('start', 'pill', M + 80, mid, 160, 100, 'I want to share my work', key=True, size=19)
    s.node('who', 'diamond', 405, mid, 210, 140, 'Who is it for?', size=21)
    s.link('start', 'who', 'rl')
    bus = 545
    s.stroke([s.port('who', 'r', 3), (bus, mid)], radius=1)
    s.stroke([(bus, ys[0]), (bus, ys[-1])], radius=1)
    col_w, pitch, x0 = 190, 240, 600
    for row, (chain, cy) in enumerate(zip(chains, ys)):
        for col, label in enumerate(chain):
            s.node(
                f'c{row}{col}',
                'box',
                x0 + col_w / 2 + col * pitch,
                cy,
                col_w,
                100,
                label,
                key=(col == 3),
                size=20,
            )
        s.arrow([(bus, cy), s.port(f'c{row}0', 'l', 4)])
        for col in range(3):
            s.link(f'c{row}{col}', f'c{row}{col + 1}', 'rl')
    return s


def book():
    s = Sheet(
        'manuscript-to-book',
        'Flowchart 3',
        'Flowchart',
        'From manuscript to book',
        'Your master is never changed by a compile. Each format tells you what it could not carry.',
        'Two flows: planning and writing with drafts, then compiling a book with preflight.',
    )
    x = [M + 100 + i * 248 for i in range(6)]
    top, bottom = 420, 750
    s.label('Plan and write', M, 308)
    plan = [
        ('box', 'Binder → Create binder', True),
        ('box', 'Write. Move scenes in the outline or corkboard', False),
        ('box', 'Tools → Writing goals…', False),
        ('box', 'Review → Versions and drafts → Save draft as…', False),
        ('diamond', 'Rework in a new draft?', False),
        ('box', 'Compare drafts… then Bring changes from draft…', False),
    ]
    for i, (kind, label, key) in enumerate(plan):
        s.node(f'p{i}', kind, x[i], top, 200, 130 if kind == 'diamond' else 112, label, key, 20)
    for i in range(5):
        s.link(f'p{i}', f'p{i + 1}', 'rl', label='yes' if i == 4 else None, at=(x[i] + 124, top - 24))
    loop_y = top - 100
    s.arrow([s.port('p5', 't'), (x[5], loop_y), (x[1], loop_y), s.port('p1', 't', 4)], radius=18)
    s.text('Back to writing', (x[1] + x[5]) / 2, loop_y - 20, 300, 19, DEEP, align='center')
    s.label('Compile', M, 600)
    comp = [
        ('box', 'File → Compile…', True),
        ('box', 'Pick a preset: Novel, Non-fiction or Manuscript', False),
        ('box', 'Choose trim size and what goes in', False),
        ('box', 'Run preflight', False),
        ('diamond', 'Anything to fix?', False),
        ('box', 'Compile… and choose where to save', True),
    ]
    for i, (kind, label, key) in enumerate(comp):
        s.node(f'c{i}', kind, x[i], bottom, 200, 130 if kind == 'diamond' else 112, label, key, 20)
    for i in range(5):
        s.link(f'c{i}', f'c{i + 1}', 'rl', label='no' if i == 4 else None, at=(x[i] + 124, bottom - 24))
    back_y = bottom + 100
    s.arrow([s.port('c4', 'b'), (x[4], back_y), (x[1], back_y), s.port('c1', 'b', 4)], radius=18)
    s.text(
        'Yes: fix it, then run preflight again', (x[1] + x[4]) / 2, back_y + 24, 480, 19, DEEP, align='center'
    )
    s.arrow([s.port('p4', 'b'), (x[4], 546), (x[0], 546), s.port('c0', 't', 4)], radius=18)
    s.text('No: ready to compile', (x[0] + x[4]) / 2 + 100, 524, 300, 19, DEEP, align='center')
    return s


def ai():
    s = Sheet(
        'ai-opt-in',
        'Flowchart 4',
        'Flowchart',
        'AI is opt-in, not opt-out',
        'No account, no telemetry, no background network traffic. You choose the provider and the moment.',
        'Two flows: deciding whether to use AI and choosing a provider, then Chat or Work ending in proposals you accept.',
    )
    s.label('Set up', M, 308)
    s.node('q', 'diamond', 210, 485, 240, 130, 'Do you want AI help?', size=21)
    s.node('no', 'box', 580, 372, 360, 80, 'Write, save, print and check locally. Nothing is sent.')
    s.arrow([s.port('q', 't'), (210, 372), (s.port('no', 'l', 4)[0], 372)], radius=14)
    s.text('no', 255, 352, 60, 19, DEEP, align='center')
    s.node('prov', 'box', 540, 485, 220, 84, 'Tools → Provider settings')
    s.link('q', 'prov', 'rl', label='yes', at=(375, 463))
    s.node('where', 'diamond', 860, 485, 250, 140, 'Where does the model run?', size=21)
    s.link('prov', 'where', 'rl')
    s.node('host', 'box', 1325, 420, 390, 84, 'Hosted, with your own key in the OS keyring')
    s.node('loc', 'box', 1325, 550, 390, 84, 'On your computer: Ollama, whisper.cpp or a local server')
    bus = 1030
    s.stroke([s.port('where', 'r', 3), (bus, 485)], radius=1)
    s.stroke([(bus, 420), (bus, 550)], radius=1)
    s.arrow([(bus, 420), s.port('host', 'l', 4)])
    s.arrow([(bus, 550), s.port('loc', 'l', 4)])
    s.text('hosted', 1080, 398, 90, 19, DEEP, align='center')
    s.text('local', 1080, 528, 90, 19, DEEP, align='center')

    s.label('Use it', M, 640)
    s.node('ready', 'pill', M + 110, 775, 220, 80, 'A provider is set up', key=True)
    s.node('mode', 'diamond', 460, 775, 220, 130, 'Chat or Work?', size=21)
    s.link('ready', 'mode', 'rl')
    bus = 610
    s.stroke([s.port('mode', 'r', 3), (bus, 775)], radius=1)
    s.stroke([(bus, 715), (bus, 835)], radius=1)
    s.node('chat', 'box', 920, 715, 420, 80, 'Chat answers. It never touches your document.')
    s.node('work', 'box', 805, 835, 190, 80, 'Work suggests exact edits')
    s.arrow([(bus, 715), s.port('chat', 'l', 4)])
    s.arrow([(bus, 835), s.port('work', 'l', 4)])
    s.text('chat', 662, 693, 70, 19, DEEP, align='center')
    s.text('work', 662, 813, 70, 19, DEEP, align='center')
    s.node(
        'pay',
        'box',
        1075,
        835,
        250,
        100,
        'A hosted provider shows you the exact text first. You authorize it.',
        size=20,
    )
    s.node(
        'prop',
        'box',
        1385,
        835,
        270,
        100,
        'Edits appear in Proposed changes. You accept each one.',
        key=True,
        size=20,
    )
    s.link('work', 'pay', 'rl')
    s.link('pay', 'prop', 'rl')
    return s


def conflict():
    s = Sheet(
        'synced-folders',
        'Flowchart 5',
        'Flowchart',
        'Two computers, one manuscript',
        'Work on one computer at a time. If both edited, you review the merge. Nothing is deleted.',
        'What to do when a save is refused in a synced folder: save as, review the conflicted copy, resolve and accept.',
    )
    s.label('When a save is refused', M, 308)
    labels = [
        ('pill', 'Save says the file changed on disk', True),
        ('box', 'Nothing was overwritten', False),
        ('box', 'Save as, to keep both versions', False),
        ('box', 'Tools → Review conflicted copies…', False),
    ]
    xs = [M + 150 + i * 380 for i in range(4)]
    for i, (kind, label, key) in enumerate(labels):
        s.node(f't{i}', kind, xs[i], 400, 300, 100, label, key, 24)
    for i in range(3):
        s.link(f't{i}', f't{i + 1}', 'rl')
    s.label('Review the other copy', M, 490)
    s.node('q', 'diamond', 235, 700, 270, 150, 'Same section edited on both?', size=21)
    s.node('ok', 'box', 640, 620, 340, 90, 'Different sections merge with nothing lost', size=21)
    s.node(
        'cf',
        'box',
        640,
        780,
        340,
        120,
        'Review → Versions and drafts → Resolve selected merge conflicts…',
        size=21,
    )
    bus = 410
    s.stroke([s.port('q', 'r', 3), (bus, 700)], radius=1)
    s.stroke([(bus, 620), (bus, 780)], radius=1)
    s.arrow([(bus, 620), s.port('ok', 'l', 4)])
    s.arrow([(bus, 780), s.port('cf', 'l', 4)])
    s.text('no', 440, 598, 50, 19, DEEP, align='center')
    s.text('yes', 440, 758, 50, 19, DEEP, align='center')
    bus2 = 850
    s.stroke([s.port('ok', 'r', 3), (bus2, 620)], radius=1)
    s.stroke([s.port('cf', 'r', 3), (bus2, 780)], radius=1)
    s.stroke([(bus2, 620), (bus2, 780)], radius=1)
    s.node('acc', 'box', 1040, 700, 280, 100, 'Accept the merge and save', key=True, size=22)
    s.arrow([(bus2, 700), s.port('acc', 'l', 4)])
    s.node(
        'mv', 'box', 1375, 700, 290, 120, 'Optionally move the copy to a conflicted copies folder', size=21
    )
    s.link('acc', 'mv', 'rl')
    return s


# --------------------------------------------------------------------------- guides


def window():
    s = Sheet(
        'the-window',
        'Guide 1',
        'Guide',
        'Find your way around the window',
        'A schematic of the editor. The writing page stays white inside warm paper margins.',
        'A schematic of the ByAuthorX window with six numbered parts explained beside it.',
    )
    fx, fy, fw, fh = M, 316, 900, 580
    s.box(fx, fy, fw, fh, SURFACE, INK, 3.4, 12)
    s.box(fx + 14, fy + 14, fw - 28, 72, PAPER, RULE, FINE)
    s.image(LOGO, fx + 30, fy + 28, 130)
    for i, key in enumerate(['save', 'undo', 'find', 'checks', 'comment', 'export']):
        s.icon(key, fx + 190 + i * 58, fy + 30, 44)
    s.box(fx + 14, fy + 100, 190, fh - 154, PAPER, RULE, FINE)
    s.text('Outline', fx + 30, fy + 124, 150, 19, DEEP)
    for i, length in enumerate([120, 90, 110, 70, 100, 80]):
        s.wave(fx + 32, fx + 32 + length, fy + 166 + i * 36, INK, FINE)
    s.box(fx + 218, fy + 100, 470, fh - 154, '#FFFFFF', RULE, FINE)
    for i in range(11):
        s.wave(
            fx + 252,
            fx + 252 + [400, 360, 300, 380, 400, 340, 390, 280, 400, 350, 250][i],
            fy + 150 + i * 32,
            RULE,
            FINE,
        )
    s.wave(fx + 300, fx + 420, fy + 214, RED, 3.4, 2.2)
    s.box(fx + 702, fy + 100, 184, fh - 154, PAPER, RULE, FINE)
    for i in range(4):
        s.wave(
            fx + 716 + i * 40,
            fx + 746 + i * 40,
            fy + 124,
            GREEN if i == 0 else MUTED,
            3.6 if i == 0 else FINE,
            0.8,
        )
    for i in range(4):
        s.box(fx + 716, fy + 146 + i * 76, 156, 60, SURFACE, RULE, FINE, 6)
    s.box(fx + 14, fy + fh - 52, fw - 28, 38, PAPER, RULE, FINE, 8)
    s.text('Saved locally       1,240 words', fx + 32, fy + fh - 33, 420, 15, MUTED, 'onionskin')
    for n, (cx, cy, r) in enumerate(
        [
            (fx + 620, fy + 50, 20),
            (fx + 150, fy + 124, 20),
            (fx + 640, fy + 128, 20),
            (fx + 790, fy + 100 + (fh - 154) - 36, 20),
            (fx + 840, fy + 176, 20),
            (fx + fw - 56, fy + fh - 33, 15),
        ],
        1,
    ):
        s.badge(n, cx, cy, r)
    items = [
        ('Ribbon', 'Two rows of tools. Each file view shows only what its format supports.'),
        ('Side panel', 'Choose Outline, Binder, Layout, Bookmarks, Statistics, Scratchpad or Library.'),
        ('Writing view', 'Continuous editing. Switch to Pages for the layout print and PDF use.'),
        ('Inspector', 'Proposed changes, Checks, AI and Configure.'),
        ('Proposed changes', 'Every suggestion waits here. Run all active checks from this tab.'),
        ('Status bar', 'Save state, counters and the limits of the current file format.'),
    ]
    lx, top, pitch = 1040, 334, 98
    for n, (head, body) in enumerate(items, 1):
        cy = top + (n - 1) * pitch
        s.badge(n, lx + 20, cy, 20)
        s.text(head, lx + 56, cy, 440, 24, INK)
        s.text(body, lx + 56, cy + 24, 424, 18, MUTED, lh=1.25, top=True)
    return s


def first_minutes():
    s = Sheet(
        'first-minutes',
        'Guide 2',
        'Guide',
        'Your first minutes',
        'Six steps from a fresh install to a first export, all on your own computer.',
        'Six numbered steps: tour, sample project, write and save, run checks, accept or reject, export.',
    )
    steps = [
        ('pages', 'Take the tour', 'Six short steps. Reopen any time with Help → Take the tour…'),
        (
            'open-document',
            'Open the sample project',
            'Help → Open sample project makes a copy of The Lamp at Harrow Point to practise on.',
        ),
        (
            'save',
            'Write, then save',
            'A .byax file keeps your text, images, review notes and history together.',
        ),
        (
            'checks',
            'Run the local checks',
            'In Proposed changes, choose Run all active checks. Nothing leaves your computer.',
        ),
        ('comment', 'Accept or reject', 'Each proposal waits for you. Keep what works.'),
        (
            'export',
            'Export a copy',
            'File → Export. The master is never replaced, and you see what a format left out.',
        ),
    ]
    cw, ch, gx, gy = 460, 248, 30, 24
    for i, (key, head, body) in enumerate(steps):
        x, y = M + (i % 3) * (cw + gx), 322 + (i // 3) * (ch + gy)
        s.box(x, y, cw, ch, SURFACE, INK, PEN, 12)
        s.badge(i + 1, x + 46, y + 46, 22)
        s.icon(key, x + cw - 24 - 72, y + 10, 72)
        s.text(head, x + 24, y + 112, cw - 48, 30, INK)
        s.text(body, x + 24, y + 144, cw - 48, 22, MUTED, lh=1.3, top=True)
    s.box(M, 868, W - 2 * M, 48, PAPER, RULE, FINE, 8)
    s.text(
        'Install: Windows 11 x64 setup, Ubuntu 24.04 x64 .deb, or macOS 14+ .dmg for Apple silicon. Installers may be unsigned; see troubleshooting.',
        M + 20,
        892,
        W - 2 * M - 40,
        17,
        MUTED,
    )
    return s


def checks():
    s = Sheet(
        'checks',
        'Guide 3',
        'Guide',
        'Checks at a glance',
        'Four kinds of check, what each starts as, and where it runs.',
        'A table of the four kinds of check with their default state and where they run.',
    )
    rows = [
        (
            'On this computer',
            'Spelling, grammar, document health.',
            'Active by default',
            'Stays on your computer',
        ),
        (
            'Fiction analysis',
            'Dialogue tags, filter words, repeated openings, rhythm, sensory balance, invented-name spelling.',
            'Inactive by default',
            'Stays on your computer. Advisory.',
        ),
        (
            'Extension checks',
            'Added by extensions you install and enable under Tools → Extensions…',
            'Inactive by default',
            'Depends on the extension',
        ),
        (
            'With an AI provider',
            'Information repository, factual accuracy, voice consistency and checks you write.',
            'Inactive by default',
            'Asks for your authorization each time',
        ),
    ]
    tw, c1, c2, c3 = 1180, M + 26, 790, 1010
    s.label('Check', c1, 322, 'onionskin', MUTED, 16)
    s.label('Starts', c2, 322, 'onionskin', MUTED, 16)
    s.label('Where it runs', c3, 322, 'onionskin', MUTED, 16)
    for i, (head, body, state, where) in enumerate(rows):
        y = 346 + i * 124
        s.box(M, y, tw, 112, SURFACE, INK, PEN, 10)
        s.text(head, c1, y + 32, 640, 26, INK)
        s.text(body, c1, y + 60, 680, 18, MUTED, lh=1.2, top=True)
        s.text(state, c2, y + 56, 200, 20, DEEP if i == 0 else INK, lh=1.2)
        s.text(where, c3, y + 56, 230, 20, INK, lh=1.2)
    s.image(UI / 'illustrations' / 'proofread-marks-light.png', 1296, 340, 224)
    s.text(
        'Red pen marks spelling and green pen marks grammar. They are interface only and never appear in print or exports.',
        1296,
        530,
        224,
        18,
        INK,
        'scholium',
        lh=1.3,
    )
    s.text(
        'Results are proposals. Text changes only when you accept. Find the list in Inspector → Checks.',
        M,
        866,
        1180,
        21,
        INK,
    )
    return s


def formats():
    s = Sheet(
        'files-and-formats',
        'Guide 4',
        'Guide',
        'Your file, your copies',
        'The master file holds everything. Every other format is a copy that says what it left out.',
        'A table matching six goals to the File command that does them.',
    )
    rows = [
        ('save', 'Keep a master you can edit later', 'Save as .byax'),
        (
            'open-document',
            'Open Word, ODT, RTF, HTML, EPUB or text',
            'File → Open. A report lists anything lost.',
        ),
        ('export', 'Share a PDF or Word copy', 'File → Export. The master is never replaced.'),
        ('pages', 'See how a format will look', 'Use the File view selector in the toolbar.'),
        ('contents', 'Publish an e-book', 'File → Export → EPUB.'),
        ('print', 'Make a print book or a submission', 'File → Compile…'),
    ]
    s.label('You want to', M + 104, 322, 'onionskin', MUTED, 16)
    s.label('Do this', 800, 322, 'onionskin', MUTED, 16)
    for i, (key, want, do) in enumerate(rows):
        y = 346 + i * 88
        s.box(M, y, W - 2 * M, 76, SURFACE, INK, PEN, 10)
        s.icon(key, M + 18, y + 6, 64)
        s.text(want, M + 104, y + 38, 640, 24, INK)
        s.text(do, 800, y + 38, 680, 22, DEEP)
    s.text(
        'Hidden or unsupported content stays in the master. Word, ODT and RTF previews are approximate.',
        M,
        894,
        W - 2 * M,
        20,
        INK,
        'scholium',
    )
    return s


def privacy():
    s = Sheet(
        'private-by-default',
        'Guide 5',
        'Guide',
        'Private by default',
        'Four claims, said the same way everywhere ByAuthorX speaks.',
        'Four privacy claims: no account, no telemetry, no background network traffic, nothing leaves until you choose.',
    )
    claims = [
        ('No account', 'Nothing to sign up for. Write the day you install.'),
        ('No telemetry', 'ByAuthorX does not report how you use it.'),
        ('No background traffic', 'No update pings. Help → Check for updates asks once, when you click.'),
        ('Nothing leaves until you choose', 'AI sends nothing until you add a provider and press Send.'),
    ]
    for i, (head, body) in enumerate(claims):
        y = 330 + i * 134
        s.box(M, y, 880, 114, SURFACE, INK, PEN, 12)
        s.badge(i + 1, M + 52, y + 57, 24)
        s.text(head, M + 100, y + 34, 740, 28, INK)
        s.text(body, M + 100, y + 58, 740, 20, MUTED, lh=1.2, top=True)
    s.image(
        ROOT / 'assets' / 'logo-companion-library' / 'illustrations' / 'local-copy-light.png', 1010, 340, 510
    )
    s.text(
        'Hosted or local, you choose the provider. Keys stay in your OS keyring or a named environment variable.',
        1010,
        770,
        510,
        22,
        INK,
        'scholium',
        lh=1.3,
    )
    return s


def context_flow():
    s = Sheet(
        'context-flow',
        '',
        'Flowchart',
        'What the assistant sees for each request',
        'The context engine sends only what a question needs, says why, and lets you look.',
        'The context engine flow: privacy first, then a route from your selection to the whole manuscript, then the What the AI saw panel.',
    )
    s.node('ask', 'box', 155, 560, 150, 110, 'You ask in the AI tab', key=True, size=20)
    s.node(
        'priv',
        'box',
        375,
        560,
        210,
        150,
        'Privacy first. Local-only sources stay out of remote requests.',
        size=18,
    )
    s.node('rt', 'box', 625, 560, 210, 120, 'The router picks a route and says why', size=19)
    s.link('ask', 'priv', 'rl')
    s.link('priv', 'rt', 'rl')
    routes = [
        ('Just your selection', 'Tighten this'),
        ('Your selection and the text around it', 'Continue this scene'),
        ('Codex, manuscript and libraries', 'What did I name the innkeeper?'),
        ('A step-by-step search', 'Every place Mara lies to Tom'),
        ('The whole manuscript, or chapter summaries', 'Assess act 2 pacing'),
    ]
    ys = [368 + i * 98 for i in range(5)]
    bus = 790
    s.stroke([s.port('rt', 'r', 3), (bus, 560)], radius=1)
    s.stroke([(bus, ys[0]), (bus, ys[-1])], radius=1)
    s.text('ROUTE', 1045, 312, 300, 15, DEEP, 'onionskin', 'center')
    s.text('A TYPICAL ASK', 1390, 312, 260, 15, DEEP, 'onionskin', 'center')
    for i, ((label, ask), cy) in enumerate(zip(routes, ys)):
        s.node(f'r{i}', 'box', 1045, cy, 390, 78, label, size=20)
        s.arrow([(bus, cy), s.port(f'r{i}', 'l', 4)])
        s.text(ask, 1390, cy, 240, 19, MUTED, 'scholium', 'center', lh=1.15)
    s.box(M, 818, W - 2 * M, 84, PAPER, INK, PEN, 12)
    s.text(
        'Open What the AI saw to read the route, the reason and every item. Pin it, exclude it, or exclude it and ask again.',
        M + 30,
        860,
        W - 2 * M - 60,
        22,
        INK,
    )
    return s


def context_meet():
    s = Sheet(
        'context-engine',
        '',
        'Guide',
        'Meet the context engine',
        'Answers about your own work that you can find, check and trust.',
        'Three promises of the context engine, found, checked and shown, with a private-first strip beneath.',
    )
    cards = [
        (
            'find',
            'Found',
            'It searches your manuscript, codex and research on this computer. Exact phrases, excluded words and near words all work.',
        ),
        (
            'cite',
            'Checked',
            'Citations open at the page you recorded. Quotations are checked against their source. "Not in your sources" is only said after a literal search.',
        ),
        (
            'properties',
            'Shown',
            'For every request you see the route, the reason and each passage the assistant saw. Pin it, leave it out, or ask again without it.',
        ),
    ]
    cw, gap = 460, 30
    for i, (key, head, body) in enumerate(cards):
        x = M + i * (cw + gap)
        s.box(x, 320, cw, 370, SURFACE, INK, PEN, 12)
        s.icon(key, x + 28, 344, 80)
        s.text(head, x + 130, 384, cw - 150, 38, INK)
        s.wave(x + 28, x + cw - 28, 440, GREEN, 3.4, 1.4)
        s.text(body, x + 28, 468, cw - 56, 25, MUTED, lh=1.35, top=True)
    s.box(M, 722, W - 2 * M, 134, PAPER, INK, PEN, 12)
    s.icon('protect', M + 24, 756, 66)
    s.text('Private first', M + 120, 762, 600, 30, INK)
    s.text(
        'Local-only libraries never reach a remote model. Codex entries marked Hide from AI are never sent. Nothing is sent until you choose a provider.',
        M + 120,
        800,
        W - 2 * M - 150,
        20,
        MUTED,
        lh=1.3,
        top=True,
    )
    s.text(
        'Search, codex and the citation audit work with no AI at all.', M, 890, W - 2 * M, 20, INK, 'scholium'
    )
    return s


def context_use():
    s = Sheet(
        'context-use',
        '',
        'Guide',
        'Use the context engine',
        'Four steps. Search, codex and the citation audit need no AI; the assistant needs a provider you choose.',
        'Four steps for using the context engine beside a sketch of the What the AI saw panel.',
    )
    steps = [
        (
            'Add your sources',
            'Tools → Libraries attaches research folders. Tools → Codex… keeps characters and places. Mark a library local-only, or an entry Hide from AI.',
        ),
        (
            'Ask in the AI tab',
            'Choose a provider and Chat or Work, then Send. The route and the reason appear under the message box.',
        ),
        (
            'Open What the AI saw',
            'Read every item with its size and where it went. Pin it, Exclude it, or Exclude and re-run.',
        ),
        (
            'Check the citations',
            'Hover a chip for the passage, click it to open the source at its page, then Insert as citation. Checks → Citation audit re-checks them all.',
        ),
    ]
    for i, (head, body) in enumerate(steps):
        y = 336 + i * 138
        s.badge(i + 1, M + 24, y + 4, 22)
        s.text(head, M + 64, y + 4, 600, 28, INK)
        s.text(body, M + 64, y + 28, 640, 19, MUTED, lh=1.3, top=True)
    s.text(
        'From the command line:  byax route draft.byax --request "…" --inspect',
        M,
        896,
        760,
        17,
        DEEP,
        'onionskin',
    )
    px, py, pw, ph = 880, 320, 640, 560
    s.box(px, py, pw, ph, SURFACE, INK, 3.4, 12)
    s.text('What the AI saw', px + 28, py + 36, 360, 26, INK)
    s.text('EXAMPLE', px + pw - 28, py + 36, 160, 15, MUTED, 'onionskin', 'right')
    s.wave(px + 24, px + pw - 24, py + 64, RULE, FINE, 1.0)
    s.pill(px + 28, py + 84, 250, 40, GREEN, DEEP, PEN)
    s.text('Route: lookup', px + 153, py + 104, 220, 19, '#FFFFFF', align='center')
    s.text(
        'The request names someone who is not in the nearby text.',
        px + 28,
        py + 150,
        pw - 56,
        18,
        INK,
        'scholium',
    )
    rows = [
        ('Codex: the innkeeper', 'stays on this computer'),
        ('Chapter 3, paragraph 12', 'stays on this computer'),
        ('Research notes, page 41', 'left out: local-only source'),
    ]
    for i, (name, note) in enumerate(rows):
        y = py + 190 + i * 76
        s.box(px + 28, y, pw - 56, 62, PAPER if i < 2 else SURFACE, RULE if i < 2 else INK, FINE, 8)
        s.text(name, px + 48, y + 22, 360, 19, INK)
        s.text(note, px + 48, y + 46, 360, 15, MUTED, 'onionskin')
        if i < 2:
            s.pill(px + pw - 176, y + 16, 60, 30, SURFACE, INK, FINE)
            s.text('Pin', px + pw - 146, y + 31, 56, 16, INK, align='center')
            s.pill(px + pw - 108, y + 16, 66, 30, SURFACE, INK, FINE)
            s.text('Exclude', px + pw - 75, y + 31, 64, 15, INK, align='center')
    s.pill(px + 28, py + 450, 300, 46, GREEN, DEEP, PEN)
    s.text('Exclude and re-run', px + 178, py + 473, 280, 20, '#FFFFFF', align='center')
    s.text(
        'Passages and payloads still ask for your approval.',
        px + 28,
        py + 524,
        pw - 56,
        16,
        MUTED,
        'scholium',
    )
    return s


SHEETS = [
    proposals,
    context_flow,
    share,
    book,
    ai,
    conflict,
    context_meet,
    context_use,
    window,
    first_minutes,
    checks,
    formats,
    privacy,
]


# --------------------------------------------------------------------------- build


def new_project():
    project = Project(W, H, PAPER, workspace=ROOT)
    for name, rel in FACES.items():
        import_font(project, FONTS / rel, name)
    return project


def report(label, project):
    result = project.check(checks=['bounds', 'contrast', 'composition'])
    findings = [i for i in result.get('issues', []) if i.get('severity') in ('error', 'fix')]
    print(f'{label}: {"ok" if result["passed"] else "FAILED"}  ({len(findings)} to fix)')
    for issue in findings[:8]:
        print('   ', issue.get('rule') or issue.get('code'), issue.get('message', '')[:150])
    return result['passed']


def build_sheet(sheet, index, total):
    key = sheet.meta['key']
    project = new_project()
    result = project.apply(chrome(sheet, index, total) + sheet.ops)
    for warning in result.get('warnings', []):
        print('  warn', key, str(warning)[:160])
    for sub in ('vixl', 'png', 'svg'):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    project.save(OUT / 'vixl' / f'{key}.vixl', overwrite=True)
    project.export(OUT / 'png' / f'{key}.png', overwrite=True)
    project.export(OUT / 'svg' / f'{key}.svg', overwrite=True)
    return report(key, project)


def cover_and_contents(sheets):
    cover = Sheet(
        'cover',
        'Booklet',
        'Visual guides',
        'ByAuthorX at a glance',
        'Flowcharts and guides for writers. Your words. Your call.',
        '',
    )
    cover.image(UI / 'illustrations' / 'checks-clear-light.png', 520, 320, 560)
    cover.text('Write. Review. Make it yours.', W / 2, 870, 1000, 34, DEEP, align='center')
    contents = Sheet(
        'contents',
        'Contents',
        'Contents',
        'What is inside',
        'Five flowcharts and five guides. Each page also stands alone as a PNG.',
        '',
    )
    for i, sheet in enumerate(sheets):
        cy = 350 + i * 54
        contents.text(f'{i + 3}', M, cy, 60, 20, DEEP, 'onionskin')
        contents.text(sheet.meta['title'], M + 70, cy, 900, 28, INK)
        contents.text(sheet.meta['kicker'], W - M, cy, 300, 17, MUTED, 'onionskin', 'right')
        contents.wave(M, W - M, cy + 28, RULE, FINE, 1.2)
    return [cover, contents]


def build_booklet(sheets):
    project = new_project()
    pages = cover_and_contents(sheets) + sheets
    total = len(pages)
    ops = []
    for n, sheet in enumerate(pages, 1):
        name = f'p{n}'
        ops.append(dict(type='page', action='add', name=name))
        for op in chrome(sheet, n, total) + sheet.ops:
            ops.append(op if op['type'] == 'accessibility' else {**op, 'page': name})
    project.apply(ops)
    project.save(OUT / 'vixl' / 'byauthorx-at-a-glance.vixl', overwrite=True)
    project.export(OUT / 'ByAuthorX-at-a-glance.pdf', overwrite=True)
    print('booklet: pages', total)
    return report('booklet', project)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--only')
    parser.add_argument('--no-booklet', action='store_true')
    args = parser.parse_args(argv)
    sheets = [make() for make in SHEETS]
    counts = {}
    for sheet in sheets:
        kind = sheet.meta['kicker']
        counts[kind] = counts.get(kind, 0) + 1
        sheet.meta['ref'] = f'{kind} {counts[kind]}'
    total = len(sheets) + 2
    ok = True
    for index, sheet in enumerate(sheets, 3):
        if args.only and args.only != sheet.meta['key']:
            continue
        ok &= build_sheet(sheet, index, total)
    if not args.only and not args.no_booklet:
        ok &= build_booklet(sheets)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
