#!/usr/bin/env python3
"""Build ByAuthorX's original, editable illustration and motion library with Vixl.

Install the pinned authoring dependency from assets/creative-library/requirements.txt.
This is an offline authoring tool; Vixl is not a ByAuthorX runtime dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import struct
import zlib

from PIL import Image, ImageDraw, ImageFont
from vixl import Project
from vixl.timeline import export_timeline

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'assets' / 'creative-library'
VIXL_COMMIT = 'afaee339bfc2fa7959c8b6399dc0740f6023bc86'
PALETTES = {
    'light': dict(
        ink='#183e3d',
        paper='#fffdf7',
        sage='#b5d5ce',
        soft='#e8ede9',
        gold='#e5c890',
        muted='#677b76',
        clay='#bb7661',
        shade='#c7d3cc',
    ),
    'dark': dict(
        ink='#f4f1e7',
        paper='#244b48',
        sage='#80b4a7',
        soft='#1d4240',
        gold='#e5c890',
        muted='#acc3ba',
        clay='#db9c87',
        shade='#102f30',
    ),
}


class Art:
    """Named vector layers; no flattened illustration imports or stock artwork."""

    def __init__(self, width=640, height=480, theme='light', background='transparent'):
        self.width, self.height, self.theme = width, height, theme
        self.c = PALETTES[theme]
        self.background = background
        self.ops = []
        self.duration = 0
        self.loop = True

    def color(self, value):
        return self.c.get(value, value)

    def shape(self, name, kind, x, y, w, h, fill='ink', stroke=None, sw=3, **kwargs):
        op = dict(
            type='shape', name=name, shape=kind, x=x, y=y, width=w, height=h, fill=self.color(fill), **kwargs
        )
        if stroke:
            op.update(stroke=self.color(stroke), stroke_width=sw, line_cap='round', line_join='round')
        self.ops.append(op)
        return name

    def rect(self, name, x, y, w, h, fill='paper', radius=12, **kwargs):
        return self.shape(name, 'rounded-rectangle', x, y, w, h, fill, radius=radius, **kwargs)

    def ellipse(self, name, x, y, w, h=None, fill='sage', **kwargs):
        return self.shape(name, 'ellipse', x, y, w, h or w, fill, **kwargs)

    def path(self, name, d, fill='none', stroke='ink', sw=4, x=0, y=0, w=None, h=None):
        return self.shape(name, 'path', x, y, w or self.width, h or self.height, fill, stroke, sw, path=d)

    def line(self, name, x1, y1, x2, y2, color='ink', sw=4):
        # Literal path coordinates live in a tight positive box for usable pivots.
        x, y = min(x1, x2), min(y1, y2)
        return self.path(
            name,
            f'M{x1 - x} {y1 - y} L{x2 - x} {y2 - y}',
            stroke=color,
            sw=sw,
            x=x,
            y=y,
            w=max(abs(x2 - x1), 1),
            h=max(abs(y2 - y1), 1),
        )

    def sparkle(self, name, x, y, size=30, fill='gold'):
        return self.shape(name, 'sparkle', x, y, size, size, fill)

    def group(self, name, targets):
        self.ops.append(dict(type='group', name=name, targets=targets))
        return name

    def sheet(self, name, x, y, w=170, h=224, lines=4, fill='paper', shadow=True):
        if shadow:
            self.rect(name + '-shadow', x + 7, y + 9, w, h, 'shade', 14)
        self.rect(name, x, y, w, h, fill, 14, stroke='ink', sw=3)
        self.rect(name + '-heading', x + 22, y + 30, w * 0.5, 9, 'ink', 4)
        for i in range(lines):
            self.rect(
                f'{name}-line-{i}',
                x + 22,
                y + 64 + i * 26,
                (w - 44) * (1 if i % 3 != 2 else 0.64),
                5,
                'muted',
                2,
            )

    def pen(self, name, x, y, size=150):
        # A fountain-pen nib, drawn as a long tapered body with a split tip.
        self.path(name, 'M25 0 L44 20 L37 115 L25 148 L13 115 L6 20 Z', 'ink', None, x=x, y=y, w=50, h=150)
        self.path(
            name + '-inlay',
            'M25 10 L32 23 L28 107 L25 127 L22 107 L18 23 Z',
            'gold',
            None,
            x=x,
            y=y,
            w=50,
            h=150,
        )
        self.line(name + '-slit', x + 25, y + 119, x + 25, y + 142, 'paper', 2)
        self.ellipse(name + '-breather', x + 22, y + 112, 6, fill='paper')

    def book(self, name, x=130, y=235):
        self.path(
            name + '-shadow',
            'M0 8 Q82 -2 166 35 Q248 -2 332 8 L332 144 Q252 126 166 164 Q82 126 0 144 Z',
            'shade',
            None,
            x=x + 5,
            y=y + 8,
            w=332,
            h=165,
        )
        self.path(
            name + '-pages',
            'M0 8 Q82 -2 166 35 Q248 -2 332 8 L332 144 Q252 126 166 164 Q82 126 0 144 Z',
            'paper',
            'ink',
            3,
            x=x,
            y=y,
            w=332,
            h=165,
        )
        self.line(name + '-spine', x + 166, y + 35, x + 166, y + 159, 'ink', 3)
        for side in range(2):
            for i in range(4):
                start = x + 22 + side * 170
                self.path(
                    f'{name}-ink-{side}-{i}',
                    'M0 0 Q55 -7 112 12',
                    stroke='muted',
                    sw=3,
                    x=start,
                    y=y + 46 + i * 23,
                    w=112,
                    h=15,
                )
        self.path(
            name + '-bookmark',
            'M0 0 L18 2 L18 100 L9 91 L0 98 Z',
            'gold',
            None,
            x=x + 270,
            y=y + 17,
            w=18,
            h=100,
        )

    def sprig(self, name, x, y, s=1, color='sage'):
        # Each sprig is one original contour with alternating leaf silhouettes.
        d = 'M45 150 C38 110 44 68 70 5 M44 115 C5 110 4 81 10 70 C41 77 47 95 44 115 Z M45 94 C80 88 94 63 88 50 C61 52 48 68 45 94 Z M52 60 C25 52 24 26 30 16 C53 25 59 40 52 60 Z M63 30 C81 29 93 10 90 0 C70 5 64 18 63 30 Z'
        self.path(name, d, color, color, 2, x=x, y=y, w=100, h=150)
        if s != 1:
            self.ops.append(dict(type='scale', target=name, factor=s))

    def backdrop(self):
        self.ellipse('paper-halo', 94, 54, 430, 355, 'soft')
        self.ellipse('sage-halo', 348, 240, 180, 140, 'sage')
        self.sparkle('accent-star', 505, 113, 29)
        self.ellipse('accent-dot', 95, 295, 10, fill='gold')

    def timeline(self, duration=1600, loop=True):
        self.duration, self.loop = duration, loop
        self.ops.append(
            dict(
                type='timeline-set',
                duration=duration,
                fps=24,
                loop=0 if loop else 1,
                loop_mode='seamless' if loop else 'off',
            )
        )

    def wave(self, target, prop, amplitude, offset=0, phase=0, period=None):
        self.ops.append(
            dict(
                type='keyframes',
                target=target,
                property=prop,
                sample=dict(
                    fn='sin',
                    period=period or self.duration,
                    amplitude=amplitude,
                    offset=offset,
                    phase=phase,
                    samples=48,
                    duration=self.duration,
                ),
            )
        )

    def keys(self, target, prop, keys):
        self.ops.append(
            dict(
                type='keyframes',
                target=target,
                property=prop,
                keys=[dict(time=t, value=v, easing=e) for t, v, e in keys],
            )
        )


def illustration(slug, theme):
    a = Art(theme=theme)
    a.backdrop()
    if slug == 'writing-desk':
        a.rect('desk', 84, 368, 470, 16, 'ink', 8)
        a.sheet('manuscript', 210, 105, 185, 239, 5)
        a.pen('fountain-pen', 414, 162)
        a.shape('tea', 'cup', 106, 263, 100, 94, 'sage', 'ink', 3)
        a.shape('steam', 'steam', 130, 211, 43, 38, 'muted')
        a.sprig('desk-sprig', 445, 258, 0.67, 'muted')
    elif slug == 'blank-page':
        a.sheet('new-document', 225, 99, 188, 271, lines=0)
        a.line('insertion-caret', 250, 171, 250, 201, 'ink', 4)
        a.sparkle('first-idea', 357, 151, 43)
        a.sprig('new-growth', 137, 232, 0.85)
        a.path('small-arc', 'M0 21 Q40 0 75 20', stroke='muted', sw=3, x=431, y=281, w=75, h=25)
    elif slug == 'story-garden':
        a.sprig('tall-stem', 285, 68, 1.2)
        a.sprig('left-stem', 182, 140, 0.86, 'muted')
        a.sprig('right-stem', 384, 127, 0.78)
        a.ellipse('flower-heart', 319, 83, 24, fill='gold')
        a.book('open-story', 136, 238)
        a.sparkle('garden-spark', 207, 98, 20)
    elif slug == 'document-library':
        a.sheet('back-draft', 142, 115, 150, 218, 4, 'soft')
        a.sheet('front-draft', 284, 82, 155, 226, 5)
        a.path(
            'folder',
            'M0 18 L110 18 L138 44 L320 44 L300 185 L10 185 Z',
            'sage',
            'ink',
            3,
            x=135,
            y=210,
            w=320,
            h=185,
        )
        a.rect('folder-label', 223, 291, 137, 37, 'paper', 8)
        a.line('label-line', 251, 310, 331, 310, 'muted', 4)
        a.shape('library-bookmark', 'banner', 399, 285, 40, 73, 'gold')
    elif slug == 'review-and-refine':
        a.sheet('draft', 178, 101, 235, 273, 6)
        a.rect('highlight', 200, 191, 145, 16, 'gold', 4)
        a.line('highlighted-words', 207, 199, 333, 199, 'ink', 3)
        a.path(
            'revision-circle',
            'M0 15 C-2 -3 66 -4 79 9 C91 28 12 34 0 15',
            stroke='clay',
            sw=3,
            x=202,
            y=243,
            w=85,
            h=32,
        )
        a.line('review-leader', 405, 181, 441, 181, 'ink', 3)
        a.rect('review-note', 427, 126, 103, 88, 'sage', 20, stroke='ink', sw=3)
        a.shape('review-check', 'checkmark', 459, 152, 42, 33, 'ink')
        a.pen('review-pen', 108, 201)
    elif slug == 'voice-to-page':
        a.sheet('transcript', 335, 111, 170, 258, 6)
        a.rect('mic-capsule', 159, 136, 60, 124, 'sage', 30, stroke='ink', sw=4)
        a.path(
            'mic-cradle',
            'M0 0 L0 36 C0 88 100 88 100 36 L100 0 M50 75 L50 117 M24 117 L76 117',
            stroke='ink',
            sw=5,
            x=139,
            y=201,
            w=100,
            h=120,
        )
        for i, h in enumerate([15, 32, 52, 33, 16]):
            a.rect(f'voice-bar-{i}', 262 + i * 12, 236 - h / 2, 5, h, 'gold', 2)
    elif slug == 'local-and-safe':
        a.sheet('private-draft', 165, 104, 183, 259, 5)
        a.shape('shield', 'shield', 298, 186, 168, 191, 'sage', 'ink', 3)
        a.path(
            'lock-shackle',
            'M0 31 L0 16 C0 -5 39 -5 39 16 L39 31',
            stroke='ink',
            sw=5,
            x=363,
            y=235,
            w=39,
            h=35,
        )
        a.rect('lock-body', 347, 264, 72, 58, 'paper', 12, stroke='ink', sw=3)
        a.ellipse('keyhole', 378, 280, 10, fill='gold')
        a.line('keyhole-stem', 383, 289, 383, 303, 'gold', 5)
        a.sprig('safety-sprig', 447, 248, 0.6)
    elif slug == 'export-ready':
        a.sheet('editable-master', 105, 117, 165, 245, 5)
        a.sheet('publication', 369, 92, 165, 245, 5)
        a.shape('export-arrow', 'arrow', 283, 192, 73, 45, 'gold', shaft_width=13, head_width=38)
        a.rect('master-mark', 144, 303, 76, 27, 'sage', 6)
        a.rect('publication-mark', 409, 277, 76, 27, 'ink', 6)
        a.shape('ready-tick', 'checkmark', 431, 281, 33, 19, 'paper')
    elif slug == 'focus-time':
        a.path(
            'quiet-window',
            'M0 197 L0 71 C0 -25 166 -25 166 71 L166 197 Z',
            'paper',
            'ink',
            3,
            x=161,
            y=99,
            w=166,
            h=198,
        )
        a.line('window-crossbar', 164, 193, 324, 193, 'muted', 3)
        a.line('window-mullion', 244, 105, 244, 293, 'muted', 3)
        a.ellipse('sun', 262, 135, 28, fill='gold')
        a.rect('desk', 108, 351, 436, 14, 'ink', 6)
        a.shape('focus-cup', 'cup', 359, 271, 94, 77, 'sage', 'ink', 3)
        a.shape('quiet-steam', 'steam', 383, 223, 37, 34, 'muted')
        a.ellipse('clock', 128, 266, 82, fill='paper', stroke='ink', sw=3)
        a.path('clock-hands', 'M18 0 L18 21 L0 21', stroke='ink', sw=4, x=151, y=284, w=20, h=23)
    elif slug == 'ideas-in-orbit':
        a.ellipse('idea-orbit', 108, 83, 423, 316, 'none', stroke='sage', sw=3)
        a.path(
            'bulb',
            'M48 0 C-3 0 -12 61 21 89 L27 108 L69 108 L75 89 C108 61 99 0 48 0 Z',
            'paper',
            'ink',
            4,
            x=272,
            y=146,
            w=100,
            h=112,
        )
        a.rect('bulb-base', 299, 256, 43, 12, 'ink', 5)
        a.rect('bulb-tip', 307, 274, 27, 8, 'gold', 4)
        a.path('filament', 'M0 0 L16 24 L32 0 M16 24 L16 50', stroke='gold', sw=4, x=305, y=190, w=34, h=52)
        a.sparkle('satellite-star', 141, 116, 38)
        a.rect('satellite-note', 449, 287, 55, 70, 'sage', 8, stroke='ink', sw=2)
        a.line('note-ink', 461, 310, 489, 310, 'ink', 3)
        a.ellipse('orbit-dot', 263, 378, 15, fill='gold')
    elif slug == 'chapters-and-outline':
        a.path(
            'outline-tree',
            'M0 0 L0 154 M0 21 L77 21 M0 84 L77 84 M0 147 L77 147',
            stroke='muted',
            sw=4,
            x=164,
            y=148,
            w=80,
            h=156,
        )
        for i, width in enumerate([258, 213, 235]):
            a.rect(f'chapter-{i}-shadow', 245, 138 + i * 67, width, 49, 'shade', 12)
            a.rect(f'chapter-{i}', 240, 132 + i * 67, width, 49, 'paper', 12, stroke='ink', sw=2)
            a.ellipse(f'chapter-{i}-number', 256, 147 + i * 67, 18, fill='gold' if i == 0 else 'sage')
            a.line(f'chapter-{i}-title', 290, 157 + i * 67, 240 + width - 22, 157 + i * 67, 'ink', 4)
        a.ellipse('outline-root', 155, 133, 19, fill='ink')
        a.sprig('outline-sprig', 440, 307, 0.49)
    elif slug == 'celebration':
        a.book('finished-story', 139, 222)
        a.ellipse('achievement', 259, 102, 113, fill='gold', stroke='ink', sw=3)
        a.shape('achievement-check', 'checkmark', 288, 138, 56, 39, 'ink')
        for i, (x, y, rot) in enumerate([(128, 155, -25), (428, 114, 30), (493, 220, -20), (181, 90, 18)]):
            a.rect(f'confetti-{i}', x, y, 9, 23, 'sage' if i % 2 else 'clay', 4, rotation=rot)
        a.sparkle('celebration-star', 400, 173, 33)
    return a


ICONS = [
    'document',
    'pen',
    'book',
    'folder',
    'search',
    'review',
    'layers',
    'history',
    'microphone',
    'export',
    'save',
    'lock',
    'spark',
    'success',
    'warning',
    'offline',
    'outline',
    'focus',
]


def icon(slug, theme):
    a = Art(128, 128, theme)
    if slug == 'document':
        a.path(
            'document',
            'M0 0 L45 0 L66 23 L66 84 L0 84 Z M45 0 L45 23 L66 23',
            'none',
            'ink',
            5,
            x=31,
            y=22,
            w=66,
            h=84,
        )
        a.line('first-line', 45, 64, 80, 64, sw=5)
        a.line('second-line', 45, 80, 70, 80, 'muted', 5)
    elif slug == 'pen':
        a.path(
            'nib', 'M0 45 L25 0 L62 0 L86 45 L43 86 Z M43 41 L43 86', 'none', 'ink', 5, x=21, y=22, w=86, h=86
        )
        a.ellipse('breather', 58, 52, 12, fill='gold')
    elif slug == 'book':
        a.path(
            'open-book',
            'M0 0 Q26 -5 46 8 Q69 -5 92 0 L92 67 Q68 61 46 78 Q24 61 0 67 Z M46 8 L46 78',
            'none',
            'ink',
            5,
            x=18,
            y=29,
            w=92,
            h=80,
        )
        for i in range(2):
            a.line(f'book-line-{i}', 31 + i * 46, 52, 51 + i * 46, 56, 'muted', 4)
    elif slug == 'folder':
        a.path(
            'folder', 'M0 11 L32 11 L45 24 L93 24 L93 75 L0 75 Z', 'none', 'ink', 5, x=18, y=19, w=93, h=77
        )
        a.line('folder-line', 21, 58, 108, 58, 'muted', 4)
    elif slug == 'search':
        a.ellipse('lens', 24, 22, 58, fill='none', stroke='ink', sw=5)
        a.line('handle', 74, 74, 103, 103, sw=7)
    elif slug == 'review':
        a.rect('comment', 20, 25, 88, 65, 'none', 14, stroke='ink', sw=5)
        a.path('tail', 'M0 0 L0 17 L22 0', stroke='ink', sw=5, x=34, y=91, w=24, h=19)
        a.shape('check', 'checkmark', 42, 44, 43, 29, 'ink')
    elif slug == 'layers':
        for i in range(3):
            a.path(
                f'layer-{i}',
                'M0 20 L42 0 L84 20 L42 40 Z',
                'paper' if i == 2 else 'none',
                'ink' if i == 2 else 'muted',
                4,
                x=22,
                y=62 - i * 21,
                w=84,
                h=40,
            )
    elif slug in ('history', 'focus'):
        a.ellipse('clock', 23, 23, 82, fill='none', stroke='ink', sw=5)
        a.path('hands', 'M20 0 L20 25 L0 25', stroke='ink', sw=5, x=44, y=39, w=23, h=28)
        if slug == 'history':
            a.path('rewind', 'M0 0 L0 17 L17 17', stroke='ink', sw=5, x=17, y=24, w=19, h=19)
    elif slug == 'microphone':
        a.rect('capsule', 49, 19, 30, 59, 'none', 15, stroke='ink', sw=5)
        a.path(
            'cradle',
            'M0 0 L0 14 C0 49 62 49 62 14 L62 0 M31 41 L31 65 M13 65 L49 65',
            stroke='ink',
            sw=5,
            x=33,
            y=43,
            w=62,
            h=67,
        )
    elif slug == 'export':
        a.path('tray', 'M0 0 L0 38 L80 38 L80 0', stroke='ink', sw=5, x=24, y=66, w=80, h=40)
        a.path('arrow', 'M0 21 L21 0 L42 21 M21 0 L21 61', stroke='ink', sw=5, x=43, y=18, w=44, h=64)
    elif slug == 'save':
        a.path(
            'disk',
            'M0 0 L63 0 L82 19 L82 82 L0 82 Z M18 0 L18 27 L57 27 L57 0 M18 82 L18 51 L64 51 L64 82',
            stroke='ink',
            sw=5,
            x=23,
            y=23,
            w=82,
            h=84,
        )
        a.line('disk-notch', 65, 31, 65, 42, 'gold', 5)
    elif slug == 'lock':
        a.rect('body', 28, 55, 72, 52, 'none', 11, stroke='ink', sw=5)
        a.path('shackle', 'M0 32 L0 17 C0 -6 44 -6 44 17 L44 32', stroke='ink', sw=5, x=42, y=23, w=46, h=35)
        a.ellipse('keyhole', 59, 74, 10, fill='gold')
        a.line('key-stem', 64, 82, 64, 93, 'gold', 4)
    elif slug == 'spark':
        a.sparkle('large-spark', 29, 32, 65, 'ink')
        a.sparkle('small-spark', 89, 21, 19)
    elif slug == 'success':
        a.ellipse('ring', 23, 23, 82, fill='none', stroke='ink', sw=5)
        a.path('check', 'M0 18 L16 34 L48 0', stroke='ink', sw=6, x=41, y=47, w=50, h=36)
    elif slug == 'warning':
        a.path('triangle', 'M45 0 L91 80 L0 80 Z', stroke='ink', sw=5, x=18, y=22, w=91, h=82)
        a.line('exclamation', 64, 51, 64, 73, 'clay', 6)
        a.ellipse('dot', 61, 84, 6, fill='clay')
    elif slug == 'offline':
        a.path(
            'cloud',
            'M15 61 C-9 59 -5 30 15 28 C13 0 51 -7 65 15 C94 2 110 49 84 61 Z',
            stroke='ink',
            sw=5,
            x=15,
            y=26,
            w=100,
            h=63,
        )
        a.line('offline-stroke', 27, 102, 101, 23, 'clay', 5)
    elif slug == 'outline':
        a.path(
            'tree',
            'M0 0 L0 69 M0 13 L25 13 M0 38 L25 38 M0 63 L25 63',
            stroke='muted',
            sw=5,
            x=29,
            y=28,
            w=26,
            h=72,
        )
        for i in range(3):
            a.rect(f'branch-{i}', 58, 32 + i * 25, 40 if i != 1 else 30, 16, 'none', 4, stroke='ink', sw=4)
    return a


ANIMATIONS = [
    'ink-dots',
    'quill-writing',
    'page-breathe',
    'orbit-spark',
    'voice-wave',
    'review-scan',
    'book-idle',
    'thinking-spark',
    'success-bloom',
    'saved-stamp',
]


def animation(slug, theme):
    a = Art(128, 48, theme) if slug == 'ink-dots' else Art(256, 256, theme)
    one_shot = slug in ('success-bloom', 'saved-stamp')
    duration = 1200 if one_shot else (2400 if slug in ('book-idle', 'thinking-spark') else 1600)
    a.timeline(duration, not one_shot)
    if slug == 'ink-dots':
        for i in range(3):
            name = a.ellipse(f'dot-{i}', 26 + i * 31, 17, 14, fill='ink')
            a.wave(name, 'translate-y', 5, phase=-i / 3)
    elif slug == 'quill-writing':
        a.rect('writing-line-track', 54, 165, 149, 5, 'soft', 2)
        a.line('written-line', 54, 166, 196, 166, sw=4)
        a.keys(
            'written-line',
            'trim_end',
            [
                (0, 5, 'ease-in-out'),
                (900, 100, 'ease-in-out'),
                (1200, 100, 'ease-in-out'),
                (1600, 5, 'ease-in-out'),
            ],
        )
        a.path(
            'nib', 'M0 45 L25 0 L44 14 L26 59 L5 67 Z M5 67 L20 40', 'gold', 'ink', 3, x=106, y=87, w=48, h=70
        )
        a.wave('nib', 'translate-x', 28, phase=-0.25)
        a.wave('nib', 'translate-y', 2, period=800)
    elif slug == 'page-breathe':
        a.sheet('page', 83, 58, 91, 135, lines=2)
        targets = [op['name'] for op in a.ops if op['type'] == 'shape']
        a.group('floating-page', targets)
        a.wave('floating-page', 'translate-y', 5)
        a.sparkle('page-star', 177, 71, 22)
        a.wave('page-star', 'scale-x', 0.12, 1)
        a.wave('page-star', 'scale-y', 0.12, 1)
    elif slug == 'orbit-spark':
        a.ellipse('orbit', 64, 64, 128, fill='none', stroke='sage', sw=3)
        a.sparkle('center-spark', 105, 105, 46, 'ink')
        a.ellipse('orbiting-ink', 180, 116, 24, fill='gold')
        a.ops.append(
            dict(
                type='motion',
                recipe='orbit',
                target='orbiting-ink',
                center=[128, 128],
                radius=64,
                period=duration,
                duration=duration,
                samples=48,
            )
        )
    elif slug == 'voice-wave':
        for i, height in enumerate([32, 54, 81, 98, 81, 54, 32]):
            name = a.rect(
                f'wave-bar-{i}', 58 + i * 22, 128 - height / 2, 9, height, 'gold' if i == 3 else 'ink', 4
            )
            a.wave(name, 'scale-y', 0.3, 0.78, phase=-i / 7, period=800)
    elif slug == 'review-scan':
        a.rect('review-page', 70, 51, 116, 154, 'paper', 12, stroke='ink', sw=3)
        for i in range(4):
            a.line(f'review-line-{i}', 92, 86 + i * 27, 164, 86 + i * 27, 'muted', 4)
        a.rect('scan-highlight', 83, 72, 91, 17, 'gold', 4)
        a.line('scan-ink', 91, 81, 164, 81, 'ink', 3)
        a.group('scan', ['scan-highlight', 'scan-ink'])
        a.wave('scan', 'translate-y', 33, 36, phase=-0.25)
    elif slug == 'book-idle':
        a.path(
            'book',
            'M0 0 Q40 -6 78 12 Q118 -6 156 0 L156 73 Q112 66 78 90 Q40 66 0 73 Z M78 12 L78 90',
            'paper',
            'ink',
            3,
            x=50,
            y=105,
            w=156,
            h=92,
        )
        a.sparkle('book-star', 116, 63, 25)
        a.wave('book-star', 'translate-y', 7)
        a.wave('book-star', 'scale-x', 0.12, 1)
        a.wave('book-star', 'scale-y', 0.12, 1)
        for i in range(2):
            a.line(f'book-ink-{i}', 66 + i * 81, 136, 106 + i * 81, 140, 'muted', 3)
    elif slug == 'thinking-spark':
        a.sparkle('thought', 101, 101, 54, 'ink')
        a.wave('thought', 'scale-x', 0.12, 0.95)
        a.wave('thought', 'scale-y', 0.12, 0.95)
        for i, (x, y, s) in enumerate([(173, 75, 16), (62, 90, 12), (169, 173, 10)]):
            a.sparkle(f'little-thought-{i}', x, y, s)
            a.wave(f'little-thought-{i}', 'opacity', 0.32, 0.62, phase=-i / 3)
    elif slug == 'success-bloom':
        a.ellipse('success-disc', 70, 70, 116, fill='sage')
        a.path('success-check', 'M0 24 L23 47 L67 0', stroke='ink', sw=7, x=95, y=104, w=70, h=50)
        a.keys(
            'success-disc',
            'scale-x',
            [(0, 0.76, 'ease-out'), (350, 1.06, 'ease-in-out'), (550, 1, 'ease-in-out'), (1200, 1, 'linear')],
        )
        a.keys(
            'success-disc',
            'scale-y',
            [(0, 0.76, 'ease-out'), (350, 1.06, 'ease-in-out'), (550, 1, 'ease-in-out'), (1200, 1, 'linear')],
        )
        a.keys(
            'success-check', 'trim_end', [(0, 5, 'ease-out'), (460, 100, 'ease-out'), (1200, 100, 'linear')]
        )
        for i, (x, y) in enumerate([(49, 62), (191, 54), (194, 186)]):
            name = a.sparkle(f'success-spark-{i}', x, y, 16)
            a.keys(
                name,
                'opacity',
                [(0, 0, 'ease-out'), (250 + i * 80, 1, 'ease-out'), (900, 0, 'ease-in'), (1200, 0, 'linear')],
            )
    elif slug == 'saved-stamp':
        a.rect('saved-page', 80, 50, 100, 148, 'paper', 12, stroke='ink', sw=3)
        for i in range(3):
            a.line(f'saved-line-{i}', 99, 83 + i * 24, 159, 83 + i * 24, 'muted', 3)
        a.ellipse('saved-seal', 145, 151, 53, fill='sage', stroke='ink', sw=3)
        a.path('saved-tick', 'M0 9 L9 18 L25 0', stroke='ink', sw=4, x=158, y=168, w=27, h=20)
        a.group('stamp', ['saved-seal', 'saved-tick'])
        a.keys(
            'stamp',
            'scale-x',
            [(0, 0.2, 'ease-out'), (300, 1.12, 'ease-in-out'), (550, 1, 'ease-in-out'), (1200, 1, 'linear')],
        )
        a.keys(
            'stamp',
            'scale-y',
            [(0, 0.2, 'ease-out'), (300, 1.12, 'ease-in-out'), (550, 1, 'ease-in-out'), (1200, 1, 'linear')],
        )
    return a


DECORATIONS = [
    'botanical-sprig',
    'editorial-flourish',
    'spark-cluster',
    'ink-swoosh',
    'laurel-pair',
    'chapter-divider',
    'paper-tab',
    'confetti-scatter',
]
BACKGROUNDS = [
    'paper-orbits',
    'dark-orbits',
    'botanical-margin',
    'chapter-lines',
    'sage-arch',
    'ink-contours',
]
ILLUSTRATIONS = [
    'writing-desk',
    'blank-page',
    'story-garden',
    'document-library',
    'review-and-refine',
    'voice-to-page',
    'local-and-safe',
    'export-ready',
    'focus-time',
    'ideas-in-orbit',
    'chapters-and-outline',
    'celebration',
]


def decoration(slug):
    a = Art(640, 320)
    if slug == 'botanical-sprig':
        a.sprig('sprig', 243, 66, 1.25, 'ink')
    elif slug == 'editorial-flourish':
        a.path(
            'flourish',
            'M0 55 C40 95 145 16 99 3 C48 -14 79 71 146 51 C192 37 201 14 242 29 C274 44 277 74 321 55 C350 42 330 12 303 33 C280 62 367 77 419 45',
            stroke='ink',
            sw=4,
            x=108,
            y=120,
            w=424,
            h=97,
        )
    elif slug == 'spark-cluster':
        for i, (x, y, s) in enumerate([(258, 110, 70), (350, 78, 29), (203, 190, 18), (390, 180, 16)]):
            a.sparkle(f'spark-{i}', x, y, s, 'ink' if i == 0 else 'gold')
    elif slug == 'ink-swoosh':
        a.path(
            'swoosh',
            'M0 38 C131 4 237 16 403 0 M46 61 C162 27 299 38 379 14',
            stroke='ink',
            sw=5,
            x=118,
            y=137,
            w=407,
            h=65,
        )
    elif slug == 'laurel-pair':
        a.sprig('left-laurel', 165, 77, 1.05, 'ink')
        a.sprig('right-laurel', 367, 77, 1.05, 'ink')
        a.ops.append(dict(type='flip', target='right-laurel', direction='horizontal'))
    elif slug == 'chapter-divider':
        a.line('left-rule', 94, 160, 273, 160, 'muted', 2)
        a.line('right-rule', 367, 160, 546, 160, 'muted', 2)
        a.sparkle('divider-spark', 305, 145, 30)
        a.ellipse('left-dot', 289, 158, 5, fill='ink')
        a.ellipse('right-dot', 346, 158, 5, fill='ink')
    elif slug == 'paper-tab':
        a.path(
            'bookmark-tab', 'M0 0 L82 0 L82 150 L41 119 L0 150 Z', 'sage', 'ink', 3, x=279, y=76, w=82, h=153
        )
        a.sparkle('tab-spark', 306, 101, 28, 'ink')
    elif slug == 'confetti-scatter':
        points = [
            (130, 119, 25),
            (210, 70, -24),
            (325, 195, 12),
            (432, 101, 32),
            (488, 203, -35),
            (179, 214, -12),
        ]
        for i, (x, y, rot) in enumerate(points):
            a.rect(f'slip-{i}', x, y, 8, 24, ['sage', 'gold', 'clay'][i % 3], 3, rotation=rot)
        a.sparkle('spark-one', 296, 94, 27)
        a.sparkle('spark-two', 401, 206, 15, 'ink')
    return a


def background(slug):
    theme = 'dark' if slug in ('dark-orbits', 'ink-contours') else 'light'
    a = Art(1600, 900, theme, PALETTES[theme]['shade'] if theme == 'dark' else PALETTES[theme]['paper'])
    if slug in ('paper-orbits', 'dark-orbits'):
        # Deliberate open area at the left for future copy.
        for i in range(4):
            a.ellipse(
                f'orbit-{i}',
                950 + i * 25,
                120 + i * 28,
                600 - i * 50,
                600 - i * 50,
                'none',
                stroke='sage',
                sw=2,
            )
        a.sparkle('orbit-star', 1246, 371, 80, 'gold')
        a.ellipse('orbit-dot', 1050, 170, 16, fill='gold')
        a.line('small-rule', 110, 790, 230, 790, 'muted', 2)
    elif slug == 'botanical-margin':
        for i, (x, y, size) in enumerate([(1220, 290, 2), (1403, 501, 1.7), (1100, 555, 1.8)]):
            a.sprig(f'border-sprig-{i}', x, y, size, 'sage' if i != 1 else 'muted')
        a.line('editorial-rule', 120, 784, 1060, 784, 'sage', 2)
        a.sparkle('border-star', 1340, 187, 32)
    elif slug == 'chapter-lines':
        for i in range(7):
            a.path(
                f'chapter-contour-{i}',
                f'M0 {110 + i * 43} C110 {110 + i * 43} 150 {i * 43} 300 {i * 43} L535 {i * 43}',
                stroke='sage',
                sw=2,
                x=1010,
                y=238,
                w=538,
                h=420,
            )
        a.rect('chapter-marker', 1320, 138, 18, 70, 'gold', 7)
    elif slug == 'sage-arch':
        a.path(
            'large-arch',
            'M0 650 L0 300 C0 -100 470 -100 470 300 L470 650 Z',
            'soft',
            None,
            x=1030,
            y=173,
            w=470,
            h=650,
        )
        a.path(
            'inner-arch',
            'M0 490 L0 240 C0 -80 320 -80 320 240 L320 490',
            stroke='sage',
            sw=3,
            x=1105,
            y=298,
            w=320,
            h=490,
        )
        a.sparkle('arch-star', 1240, 363, 50)
    elif slug == 'ink-contours':
        for i in range(8):
            a.path(
                f'ink-ripple-{i}',
                f'M0 {20 + i * 37} C130 {220 + i * 37} 385 {i * 37} 576 {140 + i * 37}',
                stroke='sage',
                sw=2,
                x=950,
                y=233,
                w=580,
                h=444,
            )
        a.sparkle('ink-star', 1300, 195, 30)
    return a


USES = {
    'ink-dots': 'Compact indeterminate loading; use at 48–96 px wide.',
    'quill-writing': 'Writing, drafting, or text processing in progress.',
    'page-breathe': 'Document opening, fetching, or general waiting.',
    'orbit-spark': 'Background processing or optional AI work in progress.',
    'voice-wave': 'Dictation in progress; illustrative, not a live audio meter.',
    'review-scan': 'Checks or document review in progress.',
    'book-idle': 'Gentle waiting illustration for larger panels.',
    'thinking-spark': 'Idea generation or optional assistance in progress.',
    'success-bloom': 'One-shot completion; hold the last frame after playback.',
    'saved-stamp': 'One-shot save confirmation; hold the last frame after playback.',
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def one_play(path):
    """Normalize container playback counts without decoding or recompressing pixels.

    Vixl 0.26's timeline loop=1 disables seamless-loop checks, but its encoders
    interpret it as one additional repetition. Our completion assets play once.
    """
    data = bytearray(path.read_bytes())
    if path.suffix == '.webp':
        offset = 12
        while offset < len(data):
            length = struct.unpack_from('<I', data, offset + 4)[0]
            if data[offset : offset + 4] == b'ANIM':
                struct.pack_into('<H', data, offset + 12, 1)
                break
            offset += 8 + length + (length % 2)
        else:
            raise ValueError(f'Missing WebP animation header: {path}')
    elif path.suffix == '.apng':
        offset = 8
        while offset < len(data):
            length = struct.unpack_from('>I', data, offset)[0]
            if data[offset + 4 : offset + 8] == b'acTL':
                struct.pack_into('>I', data, offset + 12, 1)
                crc = zlib.crc32(data[offset + 4 : offset + 8 + length])
                struct.pack_into('>I', data, offset + 8 + length, crc)
                break
            offset += 12 + length
        else:
            raise ValueError(f'Missing APNG animation header: {path}')
    elif path.suffix == '.gif':
        extension = b'\x21\xff\x0bNETSCAPE2.0\x03\x01\x01\x00\x00'
        if data.count(extension) != 1:
            raise ValueError(f'Unexpected GIF loop metadata: {path}')
        data = data.replace(extension, b'')
    path.write_bytes(data)


def build_one(out, category, slug, theme, art):
    stem = f'{slug}-{theme}' if theme else slug
    directory = out / category
    directory.mkdir(parents=True, exist_ok=True)
    p = Project(art.width, art.height, art.background, workspace=out)
    suite = dict(
        version=1,
        rules=[
            dict(id='editable-vectors', kind='count', layer_type='shape', minimum=1),
            dict(id='no-baked-copy', kind='count', layer_type='text', maximum=0),
        ],
    )
    operations = [
        dict(type='suite-set', name='asset-contract', suite=suite),
        dict(type='accessibility', lang='en', title=slug.replace('-', ' ')),
    ] + art.ops
    p.apply(operations)
    checks = p.check(checks=['bounds', 'motion', 'composition'])
    contract = p.check_suite('asset-contract')
    if not checks['passed'] or not contract['passed']:
        raise RuntimeError(f'{stem}: {json.dumps(checks)} {json.dumps(contract)}')
    master = directory / f'{stem}.vixl'
    p.save(master, overwrite=True)
    write_json(
        directory / f'{stem}.recipe.json',
        dict(
            canvas=dict(width=art.width, height=art.height, background=art.background), operations=operations
        ),
    )
    poster_time = getattr(art, 'poster_time', art.duration if art.duration and not art.loop else 0)
    p.export(directory / f'{stem}.svg', svg_policy='strict', time=poster_time, overwrite=True)
    p.export(directory / f'{stem}.png', time=poster_time, alpha='keep', overwrite=True)
    exports = ['svg', 'png']
    if category in ('illustrations', 'decorations'):
        p.export(directory / f'{stem}@2x.png', scale=2, alpha='keep', overwrite=True)
        exports.append('@2x.png')
    if category == 'icons':
        for size in (32, 64, 256):
            p.export(directory / f'{stem}-{size}.png', scale=size / 128, alpha='keep', overwrite=True)
            exports.append(f'{size}.png')
    export_reports = []
    if art.duration:
        for suffix, options in [
            ('webp', dict(quality=92)),
            ('apng', {}),
            ('gif', dict(colors=64, dither='none')),
        ]:
            # GIF uses an opaque theme matte because binary GIF alpha cannot preserve edges.
            if suffix == 'gif':
                options['background'] = art.c['paper'] if art.theme == 'light' else art.c['shade']
            output = directory / f'{stem}.{suffix}'
            report = export_timeline(p, output, fps=24, overwrite=True, **options)
            if not art.loop:
                one_play(output)
            export_reports.append(report)
            exports.append(suffix)
        sheet_fps = 4 if art.duration >= 1000 else 24
        sheet_columns = min(4, max(1, math.ceil(art.duration * sheet_fps / 1000)))
        export_timeline(
            p,
            directory / f'{stem}-sheet.png',
            format='sheet',
            fps=sheet_fps,
            columns=sheet_columns,
            overwrite=True,
        )
        exports.append('sheet.png')
        if slug in ('ink-dots', 'quill-writing', 'orbit-spark'):
            export_timeline(p, directory / f'{stem}-frames.zip', format='frames', fps=24, overwrite=True)
            exports.append('frames.zip')
    warnings = [warning for report in export_reports for warning in report.get('warnings', [])]
    entry = dict(
        id=stem,
        design=slug,
        category=category,
        theme=theme or 'universal',
        title=slug.replace('-', ' ').title(),
        width=art.width,
        height=art.height,
        transparent=art.background == 'transparent',
        use=USES.get(slug, f'Reusable {category[:-1]} for app or marketing.'),
        master=f'{category}/{stem}.vixl',
        recipe=f'{category}/{stem}.recipe.json',
        preview=f'{category}/{stem}.png',
        vector=f'{category}/{stem}.svg',
        exports=exports,
        checks=checks,
        contract=contract,
        export_warnings=warnings,
    )
    if art.duration:
        entry.update(
            duration_ms=art.duration,
            fps=24,
            loop=art.loop,
            poster_time_ms=poster_time,
            animation=f'{category}/{stem}.webp',
            reduced_motion=f'{category}/{stem}.png',
            apng=f'{category}/{stem}.apng',
            gif=f'{category}/{stem}.gif',
            on_complete='hold-last-frame' if not art.loop else None,
        )
    print(f'Built {stem}', flush=True)
    return entry


def contact_sheet(out, entries):
    font_path = ROOT / 'src/byax/resources/fonts/DejaVuSans.ttf'
    title = ImageFont.truetype(str(font_path), 34)
    label = ImageFont.truetype(str(font_path), 15)
    columns, cellw, cellh = 6, 250, 210
    # One preview per design; dark-only backgrounds retain their actual theme.
    selected = [e for e in entries if e['theme'] != 'dark']
    width = columns * cellw + 80
    im = Image.new('RGB', (width, 145 + math.ceil(len(selected) / columns) * cellh), '#fffdf7')
    d = ImageDraw.Draw(im)
    d.text((40, 29), 'ByAuthorX  /  The creative library', font=title, fill='#183e3d')
    d.text(
        (42, 85),
        '54 original designs · Editable vectors · Gentle motion · Made with Vixl',
        font=label,
        fill='#677b76',
    )
    for i, entry in enumerate(selected):
        x, y = 40 + i % columns * cellw, 133 + i // columns * cellh
        d.rounded_rectangle((x + 3, y + 3, x + cellw - 9, y + cellh - 10), 14, fill='#edf0e9')
        image = Image.open(out / entry['preview']).convert('RGBA')
        image.thumbnail((cellw - 36, 145), Image.Resampling.LANCZOS)
        im.paste(image, (x + (cellw - image.width) // 2, y + 13 + (143 - image.height) // 2), image)
        d.text((x + 16, y + 167), entry['title'], font=label, fill='#183e3d')
        d.text(
            (x + 16, y + 188), entry['category'], font=ImageFont.truetype(str(font_path), 11), fill='#677b76'
        )
    im.save(out / 'contact-sheet.jpg', quality=92)


def gallery(out, entries):
    cards = []
    for e in entries:
        moving = bool(e.get('animation'))
        links = [
            ('SVG', e['vector']),
            ('PNG', e['preview']),
            ('Master', e['master']),
            ('Recipe', e['recipe']),
        ]
        if moving:
            links += [('WebP', e['animation']), ('APNG', e['apng']), ('GIF', e['gif'])]
        cls = 'dark' if e['theme'] == 'dark' else 'light'
        image = e.get('animation', e['preview'])
        attrs = f'data-static="{e["preview"]}" data-motion="{image}"' if moving else ''
        cards.append(f'''<article data-category="{e['category']}" data-theme="{e['theme']}" data-search="{html.escape(e['title'].lower())}">
<div class="art {cls}"><img src="{image}" {attrs} alt="{html.escape(e['title'])}" loading="lazy"></div>
<div class="details"><span class="eyebrow">{e['category']} / {e['theme']}</span><h2>{e['title']}</h2><p>{e['use']}</p>
<nav>{''.join(f'<a href="{link}" target="_blank" rel="noopener" download>{name}</a>' for name, link in links)}</nav></div></article>''')
    document = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ByAuthorX Creative Library</title><style>
:root{--ink:#183e3d;--paper:#fffdf7;--sage:#b5d5ce;--gold:#e5c890}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,sans-serif}
header{padding:64px 6vw 42px;border-bottom:1px solid #d8e1d9;background:#e8ede9}.brand{font-size:13px;font-weight:700;letter-spacing:.17em;text-transform:uppercase}
h1{font-family:Georgia,serif;font-weight:400;font-size:clamp(38px,6vw,70px);letter-spacing:-.04em;margin:20px 0}header p{max-width:660px;line-height:1.65;color:#526b62}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin:26px 0 0}input,select,button{font:inherit;padding:12px 16px;border:1px solid #bdcdc4;border-radius:9px;background:var(--paper);color:var(--ink)}input{min-width:240px}button{cursor:pointer}button[aria-pressed=true]{background:var(--ink);color:var(--paper)}a{color:inherit;text-underline-offset:3px}a:focus-visible,input:focus-visible,select:focus-visible,button:focus-visible{outline:3px solid #bb7661;outline-offset:3px}
.count{padding:25px 6vw 0;font-size:13px;color:#677b76}.grid{padding:24px 6vw 60px;display:grid;grid-template-columns:repeat(auto-fill,minmax(265px,1fr));gap:23px}
article{border:1px solid #dce4dc;border-radius:16px;overflow:hidden}article[hidden]{display:none}.art{height:220px;display:flex;align-items:center;justify-content:center;background:#edf0e9}.art.dark{background:#102f30}.art img{max-width:100%;max-height:210px;width:100%;object-fit:contain;padding:12px}.details{padding:22px}.eyebrow{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#677b76}h2{font-size:19px;margin:10px 0}article p{font-size:13px;line-height:1.5;min-height:39px;color:#526b62}nav{display:flex;gap:11px;flex-wrap:wrap;font-size:12px;font-weight:650}footer{padding:24px 6vw;border-top:1px solid #d8e1d9;font-size:12px;color:#677b76}
</style><header><div class="brand">ByAuthorX / Creative resources</div><h1>Room for what comes next.</h1><p>A shelf of original illustrations, small animations, and editorial details, ready for the app or the next campaign. Choose a theme, explore a collection, and download what you need.</p>
<div class="controls"><input id="search" type="search" placeholder="Find an asset…" aria-label="Search assets"><select id="category" aria-label="Filter collection"><option value="all">All collections</option><option>illustrations</option><option>animations</option><option>icons</option><option>decorations</option><option>backgrounds</option></select><select id="theme" aria-label="Filter theme"><option value="light">Light + universal</option><option value="dark">Dark + universal</option><option value="all">Every variant</option></select><button id="motion" aria-pressed="false">Pause motion</button></div></header>
<div class="count" id="count" aria-live="polite"></div><main class="grid">CARDS</main><footer>Made with Vixl · 54 original designs · SVG + PNG + editable masters · Animation posters serve as reduced-motion alternatives.</footer>
<script>
const media=matchMedia('(prefers-reduced-motion: reduce)');let paused=media.matches;
const button=document.querySelector('#motion');
function setMotion(){button.setAttribute('aria-pressed',String(paused));button.textContent=paused?'Play motion':'Pause motion';document.querySelectorAll('[data-motion]').forEach(img=>img.src=paused?img.dataset.static:img.dataset.motion)}
button.addEventListener('click',()=>{paused=!paused;setMotion()});media.addEventListener('change',event=>{paused=event.matches;setMotion()});setMotion();
function filter(){let n=0;document.querySelectorAll('article').forEach(card=>{const category=document.querySelector('#category').value,theme=document.querySelector('#theme').value,q=document.querySelector('#search').value.toLowerCase();card.hidden=!(category==='all'||category===card.dataset.category)||!(theme==='all'||theme===card.dataset.theme||card.dataset.theme==='universal')||!card.dataset.search.includes(q);if(!card.hidden)n++});document.querySelector('#count').textContent=n+' assets shown'}
['search','category','theme'].forEach(id=>document.getElementById(id).addEventListener('input',filter));filter();
</script></html>""".replace('CARDS', '\n'.join(cards))
    (out / 'index.html').write_text(document)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--only', help='Build one named design for iteration')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    entries = []
    for category, slugs, factory, themed in [
        ('illustrations', ILLUSTRATIONS, illustration, True),
        ('icons', ICONS, icon, True),
        ('animations', ANIMATIONS, animation, True),
        ('decorations', DECORATIONS, decoration, False),
        ('backgrounds', BACKGROUNDS, background, False),
    ]:
        for slug in slugs:
            if args.only and slug != args.only:
                continue
            for theme in ('light', 'dark') if themed else (None,):
                art = factory(slug, theme) if themed else factory(slug)
                entries.append(build_one(args.out, category, slug, theme, art))
    if args.only:
        write_json(args.out / f'{args.only}-review.json', entries)
        return
    manifest = dict(
        schema_version=1,
        name='ByAuthorX Creative Library',
        design_count=54,
        variant_count=len(entries),
        generator='scripts/build_creative_library.py',
        engine=dict(
            name='Vixl', version='0.26.0', commit=VIXL_COMMIT, repository='https://github.com/jxburros/Vixl'
        ),
        palettes=PALETTES,
        assets=entries,
    )
    write_json(args.out / 'manifest.json', manifest)
    gallery(args.out, entries)
    contact_sheet(args.out, entries)
    files = {
        str(path.relative_to(args.out)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(args.out.rglob('*'))
        if path.is_file() and path.name != 'checksums.json'
    }
    write_json(args.out / 'checksums.json', files)
    reviews = [
        dict(id=e['id'], findings=e['checks'].get('issues', []), warnings=e['export_warnings'])
        for e in entries
        if e['checks'].get('issues') or e['export_warnings']
    ]
    print(json.dumps(dict(designs=54, variants=len(entries), files=len(files), reviews=reviews), indent=2))


if __name__ == '__main__':
    main()
