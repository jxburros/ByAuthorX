"""Illustrations, task-owned motion and hand-inked rules for the ByAuthorX logo UI library.

They reuse the companion shelf's vocabulary: working pages with imperfect edges, the supplied green pen
line, and the supplied red ink blot used once as a point of origin.
"""

from __future__ import annotations

import math

from logo_ui_art import UIArt

SCENE = (640, 480)
LOOP = (320, 224)
LOOP_MS = 2400

ILLUSTRATIONS = {
    'checks-clear': 'Checks finished with nothing to flag; a lens over the page confirms it.',
    'proofread-marks': 'Checks found items; hand-inked proofreader marks sit under the words that need a look.',
    'jobs-empty': 'Nothing is running in the background; an empty tray and a pen at rest.',
    'dictation-ready': 'Dictation waiting to begin; the pen line becomes the voice.',
    'export-ready': 'A finished copy leaves the working pages; the pen line carries it out.',
    'provider-keys': 'Provider settings; the key bow is the pen loop and the key stays on this computer.',
}
ANIMATIONS = {
    'listening-wave': 'Dictation recording; the voice is written as a green pen line, held, and erased.',
    'checking-page': 'Document checks running; ticks are written beside each line, held, and erased.',
}
RULES = {
    'rule-h': 'A quiet hand-inked horizontal rule that tiles seamlessly (toolbar, status bar, menus).',
    'rule-v': 'A quiet hand-inked vertical rule (toolbar separators).',
    'tab-underline': 'The selected tab: one short green pen stroke.',
}


def _scene(theme, seed, wobble=1.6):
    return UIArt(*SCENE, theme, seed=seed, wobble=wobble)


def signature(a, x=112, y=392, width=420):
    a.gesture('signature', x, y, width)
    a.blot('signature-ink', x - 21, y + 27, 19)


def fanned_page(a, name, cx, cy, w, h, angle, lines=4, fill='soft'):
    """A working sheet turned about its centre; opaque so the sheet in front hides the one behind."""
    cos_a, sin_a = math.cos(math.radians(angle)), math.sin(math.radians(angle))

    def turn(px, py):
        dx, dy = px - cx, py - cy
        return (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)

    left, top, right, bottom, fold = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 26
    outline = [(left, top), (right - fold, top), (right, top + fold), (right, bottom), (left, bottom)]
    a.ink_poly(name, [turn(*p) for p in outline], closed=True, radius=5, sw=3.4, fill=fill)
    a.ink_poly(
        name + '-fold',
        [turn(right - fold, top), turn(right - fold, top + fold), turn(right, top + fold)],
        radius=3,
        sw=2.6,
    )
    for index in range(lines):
        y = top + 52 + index * 30
        length = (w - 56) * (1 if index % 3 != 2 else 0.68)
        start, end = turn(left + 28, y), turn(left + 28 + length, y)
        a.stroke(
            f'{name}-line-{index}',
            f'M{start[0]:.1f} {start[1]:.1f} L{end[0]:.1f} {end[1]:.1f}',
            'muted',
            2.6,
        )


def illustration(slug, theme):
    a = _scene(theme, slug)
    if slug == 'checks-clear':
        a.page('document', 150, 64, 240, 310, 8)
        a.ink_circle('lens', 404, 282, 78, sw=5, fill='soft')
        a.ink_line('lens-handle', 459, 337, 512, 384, sw=10)
        a.pen_poly('lens-check', [(368, 282), (394, 310), (442, 252)], radius=6, sw=10)
        signature(a)
    elif slug == 'proofread-marks':
        a.page('manuscript', 120, 56, 270, 330, 9)
        a.squiggle('spelling-mark', 170, 262, 172, 'red', amp=5.5)
        a.squiggle('grammar-mark', 190, 340, 256, 'pen', amp=5.5)
        a.pen_curve('margin-connector', [(268, 170), (320, 150), (390, 176), (438, 168)], sw=4)
        a.page('margin-note', 440, 120, 132, 116, 3, 'paper')
        a.blot('note-ink', 460, 146, 14)
    elif slug == 'jobs-empty':
        a.ink_poly(
            'tray-front', [(160, 262), (480, 262), (462, 342), (178, 342)], closed=True, radius=8, sw=5
        )
        a.ink_poly('tray-rim', [(160, 262), (194, 226), (446, 226), (480, 262)], radius=8, sw=4)
        a.ink_curve('tray-shade', [(206, 300), (320, 306), (434, 300)], sw=2.6, color='muted')
        a.path(
            'pen',
            'M0 108 L51 0 L64 6 L11 115 L0 129 Z M0 129 L11 115',
            'green',
            'ink',
            2.5,
            x=506,
            y=212,
            w=66,
            h=132,
        )
        signature(a)
    elif slug == 'dictation-ready':
        a.ink_poly('capsule', [(228, 64), (312, 64), (312, 224), (228, 224)], closed=True, radius=42, sw=5)
        a.ink_arc('cradle', 270, 210, 68, 8, 172, sw=5)
        a.ink_line('stand', 270, 278, 270, 344, sw=5)
        a.ink_line('base', 220, 348, 320, 348, sw=5)
        a.pen_curve(
            'voice',
            [
                (360, 150),
                (384, 150),
                (398, 112),
                (412, 186),
                (428, 84),
                (444, 210),
                (460, 120),
                (474, 172),
                (488, 140),
                (502, 156),
                (520, 150),
                (572, 150),
            ],
            sw=6,
        )
        a.blot('voice-ink', 336, 140, 20)
        a.ink_curve('desk', [(150, 396), (320, 392), (500, 397)], sw=2.8, color='rule')
    elif slug == 'export-ready':
        fanned_page(a, 'copy-back', 168, 226, 176, 228, -10)
        fanned_page(a, 'copy-middle', 262, 220, 176, 228, 0)
        fanned_page(a, 'copy-front', 356, 226, 176, 228, 10, lines=3)
        a.gesture('export-line', 250, 372, 330)
        a.blot('export-ink', 229, 399, 17)
    elif slug == 'provider-keys':
        a.page('settings', 130, 74, 220, 290, 6)
        a.ink_circle('bow', 424, 232, 46, sw=7, color='pen')
        a.ink_line('shaft', 470, 232, 594, 232, sw=8)
        a.ink_line('tooth-near', 556, 232, 556, 266, sw=7)
        a.ink_line('tooth-far', 582, 232, 582, 258, sw=7)
        a.blot('key-ink', 413, 221, 22)
        a.ink_curve('desk', [(110, 396), (320, 392), (540, 397)], sw=2.8, color='rule')
    else:
        raise KeyError(slug)
    return a


def animation(slug, theme):
    a = UIArt(*LOOP, theme, seed=slug, wobble=1.0)
    a.timeline(LOOP_MS, True)
    if slug == 'listening-wave':
        a.ink_poly('mic-capsule', [(54, 62), (86, 62), (86, 122), (54, 122)], closed=True, radius=16, sw=4)
        a.ink_arc('mic-cradle', 70, 116, 27, 8, 172, sw=4)
        a.ink_line('mic-stand', 70, 142, 70, 168, sw=4)
        a.ink_line('mic-base', 54, 171, 86, 171, sw=4)
        a.pen_curve(
            'listening-wave',
            [
                (136, 100),
                (152, 100),
                (162, 78),
                (172, 124),
                (184, 60),
                (196, 140),
                (208, 82),
                (220, 116),
                (232, 94),
                (244, 106),
                (256, 100),
                (296, 100),
            ],
            sw=4.5,
        )
        a.write_erase('listening-wave')
        a.blot('listening-ink', 112, 90, 17)
    elif slug == 'checking-page':
        a.page('checking-page', 84, 22, 152, 178, 0)
        for index in range(4):
            y = 64 + index * 33
            a.text_line(f'checking-line-{index}', 104, y, 70 if index % 2 == 0 else 54, sw=3)
            a.pen_poly(f'checking-tick-{index}', [(194, y), (201, y + 8), (216, y - 9)], radius=2, sw=4.4)
            start = 120 + index * 240
            a.write_erase(
                f'checking-tick-{index}',
                draw_start=start,
                draw_end=start + 220,
                erase_start=1440 + index * 70,
                erase_end=1720 + index * 70,
            )
        a.blot('checking-ink', 97, 36, 10)
    else:
        raise KeyError(slug)
    return a


def rule(slug, theme):
    """Quiet hand-inked rules; the horizontal tile starts and ends level so it repeats unseen."""
    if slug == 'rule-h':
        a = UIArt(1200, 6, theme, seed=slug, wobble=0.5)
        points = [(0, 3.0)] + [(x, 3 + a.hand.jitter(0.8)) for x in range(120, 1200, 120)] + [(1200, 3.0)]
        a.stroke('rule', a.hand.curve(points, amplitude=0.0), 'rule', 1.8)
    elif slug == 'rule-v':
        a = UIArt(6, 96, theme, seed=slug, wobble=0.5)
        points = [(3.0, 0)] + [(3 + a.hand.jitter(0.8), y) for y in range(16, 96, 16)] + [(3.0, 96)]
        a.stroke('rule', a.hand.curve(points, amplitude=0.0), 'rule', 1.8)
    elif slug == 'tab-underline':
        a = UIArt(480, 8, theme, seed=slug, wobble=0.5)
        points = [
            (2, 5),
            (110, 4 + a.hand.jitter(0.7)),
            (240, 4 + a.hand.jitter(0.7)),
            (370, 4 + a.hand.jitter(0.7)),
            (478, 5),
        ]
        a.stroke('underline', a.hand.curve(points, amplitude=0.0), 'pen', 3.2)
    else:
        raise KeyError(slug)
    return a


def canvas_of(art):
    return (art.width, art.height)
