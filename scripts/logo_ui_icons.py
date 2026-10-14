"""The ByAuthorX interface icon set, drawn on a 96-unit grid in the logo's own vocabulary.

Ink carries the object, one green pen gesture carries the action, and the red ink blot appears only
where it marks a point of origin (a comment). Strokes are 6 units, so they stay legible at 16 px.
"""

from __future__ import annotations

from logo_ui_art import FINE, INK, PEN, UIArt

GRID = 96
ICONS = {}


def icon(name, group, label):
    def register(function):
        ICONS[name] = dict(draw=function, group=group, label=label)
        return function

    return register


def mirror(points):
    return [(GRID - x, y) for x, y in points]


# -- text formatting ------------------------------------------------------------------------------
@icon('bold', 'format', 'Bold')
def bold(a):
    a.ink_curve('stem', [(30, 14), (30, 46), (30, 78)], sw=8)
    a.ink_curve('upper-bowl', [(30, 15), (50, 14), (61, 24), (51, 42), (30, 45)], sw=8)
    a.ink_curve('lower-bowl', [(30, 45), (54, 44), (67, 58), (55, 77), (30, 78)], sw=8)
    a.pen_curve('underline', [(24, 89), (46, 87.5), (72, 89)], sw=5)


@icon('italic', 'format', 'Italic')
def italic(a):
    a.ink_line('stem', 54, 16, 41, 76)
    a.ink_line('top-serif', 40, 16, 64, 16)
    a.ink_line('base-serif', 29, 76, 53, 76)
    a.pen_curve('underline', [(24, 89), (46, 87.5), (72, 89)], sw=5)


@icon('underline', 'format', 'Underline')
def underline(a):
    a.ink_curve('letter', [(28, 12), (28, 44), (35, 62), (48, 67), (61, 62), (68, 44), (68, 12)])
    a.pen_curve('underline', [(18, 83), (40, 81), (62, 83), (78, 80)], sw=PEN)


@icon('strikethrough', 'format', 'Strikethrough')
def strikethrough(a):
    a.ink_curve(
        'letter',
        [
            (65, 24),
            (59, 14),
            (42, 12),
            (30, 21),
            (34, 35),
            (48, 44),
            (63, 54),
            (67, 66),
            (56, 77),
            (37, 79),
            (27, 69),
        ],
    )
    a.pen_curve('strike', [(14, 47), (46, 45), (82, 47)], sw=PEN)


@icon('text-color', 'format', 'Text color')
def text_color(a):
    a.ink_poly('letter', [(24, 70), (48, 12), (72, 70)], radius=2)
    a.ink_line('bar', 34, 52, 62, 52, sw=FINE + 0.6)
    a.pen_curve('swatch', [(20, 85), (46, 83), (76, 85)], sw=9)


@icon('highlight', 'format', 'Highlight')
def highlight(a):
    a.ink_poly('marker', [(38, 56), (28, 46), (56, 16), (68, 28)], closed=True, radius=2)
    a.ink_poly('nib', [(38, 56), (28, 46), (22, 64)], closed=True, radius=1.5)
    a.pen_curve('stroke', [(24, 82), (46, 79), (74, 82)], sw=9)


@icon('hyperlink', 'format', 'Hyperlink')
def hyperlink(a):
    a.ink_circle('link-ink', 36, 60, 21, 10, angle=-45)
    a.ink_circle('link-pen', 60, 36, 21, 10, angle=-45, color='pen')


@icon('clear-format', 'format', 'Clear text formatting')
def clear_format(a):
    a.ink_line('bar', 22, 18, 62, 18)
    a.ink_line('stem', 42, 18, 42, 62)
    a.pen_curve('wipe', [(40, 84), (54, 72), (66, 60), (80, 62), (78, 74), (70, 74)], sw=PEN)


# -- paragraph ------------------------------------------------------------------------------------
def aligned_lines(a, lengths, align, first_green=True):
    for index, length in enumerate(lengths):
        y = 22 + index * 17
        if align == 'left':
            x = 16
        elif align == 'right':
            x = 80 - length
        else:
            x = 48 - length / 2
        if index == 0 and first_green:
            a.pen_curve(f'line-{index}', [(x, y), (x + length / 2, y - 1), (x + length, y)], sw=5)
        else:
            a.text_line(f'line-{index}', x, y, length)


@icon('align-left', 'paragraph', 'Align left')
def align_left(a):
    aligned_lines(a, [62, 40, 62, 40], 'left')


@icon('align-center', 'paragraph', 'Align center')
def align_center(a):
    aligned_lines(a, [62, 40, 62, 40], 'center')


@icon('align-right', 'paragraph', 'Align right')
def align_right(a):
    aligned_lines(a, [62, 40, 62, 40], 'right')


@icon('justify', 'paragraph', 'Justify')
def justify(a):
    aligned_lines(a, [64, 64, 64, 64], 'left')


@icon('bullet-list', 'paragraph', 'Bulleted list')
def bullet_list(a):
    for index, (y, length) in enumerate([(24, 44), (48, 36), (72, 44)]):
        a.bullet(f'bullet-{index}', 20, y)
        if index == 0:
            a.pen_curve('line-0', [(36, y), (58, y - 1), (36 + length, y)], sw=5)
        else:
            a.text_line(f'line-{index}', 36, y, length)


@icon('numbered-list', 'paragraph', 'Numbered list')
def numbered_list(a):
    a.waive_rhythm('Evenly spaced list rows are the subject of this icon.')
    a.ink_poly('one', [(15, 22), (21, 17), (21, 36)], radius=1.5, sw=FINE)
    a.ink_curve('two', [(15, 47), (19, 42), (26, 44), (26, 50), (15, 61), (27, 61)], sw=FINE)
    a.ink_curve('three', [(15, 70), (25, 69), (22, 75), (26, 79), (25, 85), (15, 85)], sw=FINE)
    a.pen_curve('line-0', [(38, 26), (58, 25), (80, 26)], sw=5)
    a.text_line('line-1', 38, 53, 44)
    a.text_line('line-2', 38, 78, 36)


@icon('indent', 'paragraph', 'Increase list level')
def indent(a):
    a.text_line('line-0', 14, 18, 68)
    a.text_line('line-1', 46, 38, 36)
    a.text_line('line-2', 46, 56, 36)
    a.text_line('line-3', 14, 76, 68)
    a.pen_curve('arrow', [(14, 48), (24, 47), (34, 48)], sw=5)
    a.pen_poly('arrow-head', [(27, 40), (35, 48), (27, 56)], radius=1.5, sw=5)


@icon('outdent', 'paragraph', 'Decrease list level')
def outdent(a):
    a.text_line('line-0', 14, 18, 68)
    a.text_line('line-1', 46, 38, 36)
    a.text_line('line-2', 46, 56, 36)
    a.text_line('line-3', 14, 76, 68)
    a.pen_curve('arrow', [(14, 48), (24, 47), (34, 48)], sw=5)
    a.pen_poly('arrow-head', [(21, 40), (13, 48), (21, 56)], radius=1.5, sw=5)


# -- file -----------------------------------------------------------------------------------------
@icon('new-document', 'file', 'New')
def new_document(a):
    a.page_outline('page', 22, 8, 52, 78)
    a.text_line('line-0', 32, 30, 22)
    a.pen_line('plus-vertical', 60, 54, 60, 74, sw=5.5)
    a.pen_line('plus-horizontal', 50, 64, 70, 64, sw=5.5)


@icon('open-document', 'file', 'Open')
def open_document(a):
    a.ink_poly('folder', [(12, 26), (36, 26), (43, 35), (84, 35), (84, 80), (12, 80)], closed=True, radius=3)
    a.pen_poly('flap', [(14, 80), (24, 55), (92, 55), (82, 80)], radius=3)


@icon('save', 'file', 'Save')
def save(a):
    a.ink_poly('disk', [(14, 14), (66, 14), (82, 30), (82, 82), (14, 82)], closed=True, radius=3)
    a.ink_poly('shutter', [(30, 14), (30, 34), (62, 34), (62, 14)], radius=2, sw=FINE)
    a.ink_poly('label', [(28, 82), (28, 58), (68, 58), (68, 82)], radius=2, sw=FINE)
    a.pen_curve('label-stroke', [(36, 70), (48, 69), (60, 70)], sw=5)


@icon('save-as', 'file', 'Save as')
def save_as(a):
    a.ink_poly('disk', [(10, 10), (54, 10), (68, 24), (68, 66), (10, 66)], closed=True, radius=3)
    a.ink_poly('shutter', [(22, 10), (22, 26), (50, 26), (50, 10)], radius=2, sw=FINE)
    a.ink_poly('label', [(20, 66), (20, 46), (56, 46), (56, 66)], radius=2, sw=FINE)
    a.pen_curve('pen', [(54, 88), (66, 72), (82, 50)], sw=7)
    a.pen_curve('pen-tip', [(54, 88), (49, 89), (50, 84)], sw=4)


@icon('print', 'file', 'Print')
def print_icon(a):
    a.ink_poly('paper-in', [(26, 34), (26, 12), (70, 12), (70, 34)], radius=2)
    a.ink_poly('body', [(10, 34), (86, 34), (86, 62), (10, 62)], closed=True, radius=4)
    a.ink_poly('paper-out', [(26, 62), (26, 86), (70, 86), (70, 62)], radius=2)
    a.pen_curve('paper-line', [(36, 75), (48, 74), (60, 75)], sw=5)
    a.bullet('lamp', 74, 48, 3.2)


@icon('export', 'file', 'Export')
def export(a):
    a.page_outline('page', 12, 10, 48, 76)
    a.text_line('line-0', 22, 32, 26)
    a.text_line('line-1', 22, 46, 20)
    a.pen_curve('arrow', [(40, 66), (60, 65), (84, 66)], sw=PEN)
    a.pen_poly('arrow-head', [(73, 54), (85, 66), (73, 78)], radius=2, sw=PEN)


# -- edit -----------------------------------------------------------------------------------------
@icon('undo', 'edit', 'Undo')
def undo(a):
    a.ink_curve('arc', [(24, 36), (46, 28), (66, 38), (74, 56), (66, 74)])
    a.ink_poly('arrow-head', [(34, 16), (20, 35), (41, 47)], radius=2)
    a.pen_curve('trail', [(66, 74), (56, 82), (44, 85)], sw=5)


@icon('redo', 'edit', 'Redo')
def redo(a):
    a.ink_curve('arc', mirror([(24, 36), (46, 28), (66, 38), (74, 56), (66, 74)]))
    a.ink_poly('arrow-head', mirror([(34, 16), (20, 35), (41, 47)]), radius=2)
    a.pen_curve('trail', mirror([(66, 74), (56, 82), (44, 85)]), sw=5)


@icon('cut', 'edit', 'Cut')
def cut(a):
    a.pen_line('blade-left', 30, 8, 60, 56)
    a.pen_line('blade-right', 66, 8, 36, 56)
    a.ink_circle('handle-left', 30, 72, 12, 11)
    a.ink_circle('handle-right', 66, 72, 12, 11)


@icon('copy', 'edit', 'Copy')
def copy(a):
    a.ink_poly('back', [(38, 28), (38, 10), (12, 10), (12, 64), (32, 64)], radius=2)
    a.sheet('front', 36, 28, 48, 58)
    a.text_line('line-0', 46, 46, 28)
    a.pen_curve('line-1', [(46, 62), (58, 61), (70, 62)], sw=5)


@icon('paste', 'edit', 'Paste')
def paste(a):
    a.ink_poly('board', [(18, 18), (78, 18), (78, 86), (18, 86)], closed=True, radius=4)
    a.ink_poly('clip', [(36, 10), (60, 10), (60, 26), (36, 26)], closed=True, radius=3)
    a.pen_curve('line-0', [(30, 44), (48, 43), (66, 44)], sw=5)
    a.text_line('line-1', 30, 58, 36)
    a.text_line('line-2', 30, 72, 26)


@icon('find', 'edit', 'Find and replace')
def find(a):
    a.ink_circle('lens', 40, 40, 25)
    a.ink_line('handle', 58, 58, 82, 82, sw=8)
    a.pen_arc('glint', 40, 40, 13, 195, 275, sw=5)


# -- insert ---------------------------------------------------------------------------------------
@icon('table', 'insert', 'Table')
def table(a):
    a.ink_poly('frame', [(12, 16), (84, 16), (84, 80), (12, 80)], closed=True, radius=3)
    a.pen_line('header', 12, 38, 84, 38)
    a.ink_line('row', 12, 59, 84, 59, sw=FINE)
    a.ink_line('column-a', 36, 16, 36, 80, sw=FINE)
    a.ink_line('column-b', 60, 16, 60, 80, sw=FINE)


@icon('image', 'insert', 'Image')
def image(a):
    a.ink_poly('frame', [(12, 16), (84, 16), (84, 80), (12, 80)], closed=True, radius=3)
    a.ink_circle('sun', 35, 37, 8, sw=FINE + 0.6)
    a.pen_poly('hills', [(12, 70), (32, 50), (47, 64), (63, 44), (84, 68)], radius=4)


@icon('page-break', 'insert', 'Page break')
def page_break(a):
    a.ink_poly('page-above', [(22, 38), (22, 10), (74, 10), (74, 38)], radius=3)
    a.ink_poly('page-below', [(22, 58), (22, 86), (74, 86), (74, 58)], radius=3)
    for index, (x1, x2) in enumerate([(10, 26), (36, 58), (68, 86)]):
        a.pen_line(f'break-{index}', x1, 48, x2, 48)


@icon('columns', 'insert', 'Section and columns')
def columns(a):
    a.sheet('page', 12, 10, 72, 76)
    for index, y in enumerate([30, 46, 62]):
        a.text_line(f'left-{index}', 22, y, 16 if index == 2 else 20, sw=4.2)
        a.text_line(f'right-{index}', 57, y, 12 if index == 1 else 17, sw=4.2)
    a.pen_line('divider', 48, 24, 48, 72, sw=5)


@icon('contents', 'insert', 'Table of contents')
def contents(a):
    a.waive_rhythm('Evenly spaced list rows are the subject of this icon.')
    for index, (x, y, length) in enumerate([(14, 22, 44), (26, 44, 32), (14, 66, 44)]):
        a.text_line(f'entry-{index}', x, y, length)
        a.pen_curve(f'page-{index}', [(68, y), (75, y - 1), (82, y)], sw=5)
    a.ink_curve('rule', [(14, 84), (48, 83), (82, 84)], sw=FINE)


@icon('footnote', 'insert', 'Footnote')
def footnote(a):
    a.text_line('line-0', 14, 18, 68)
    a.text_line('line-1', 14, 34, 52)
    a.pen_curve('rule', [(14, 58), (28, 57), (42, 58)], sw=5)
    a.ink_poly('numeral', [(14, 74), (19, 69), (19, 86)], radius=1.5, sw=FINE)
    a.text_line('note', 30, 78, 50)


@icon('bookmark', 'insert', 'Bookmark')
def bookmark(a):
    a.ink_poly('ribbon', [(26, 8), (70, 8), (70, 88), (48, 68), (26, 88)], closed=True, radius=3)
    a.pen_curve('band', [(26, 26), (48, 25), (70, 26)], sw=PEN)


@icon('layers', 'insert', 'Watermark and layers')
def layers(a):
    a.pen_poly('top', [(48, 12), (84, 30), (48, 48), (12, 30)], closed=True, radius=3)
    a.ink_poly('middle', [(12, 48), (48, 66), (84, 48)], radius=3)
    a.ink_poly('bottom', [(12, 64), (48, 82), (84, 64)], radius=3)


# -- table ----------------------------------------------------------------------------------------
@icon('row-add', 'table', 'Add row')
def row_add(a):
    a.ink_poly('frame', [(12, 10), (84, 10), (84, 54), (12, 54)], closed=True, radius=3)
    a.ink_line('row', 12, 32, 84, 32, sw=FINE)
    a.ink_line('column', 48, 10, 48, 54, sw=FINE)
    a.pen_line('plus-vertical', 48, 64, 48, 86, sw=5.5)
    a.pen_line('plus-horizontal', 37, 75, 59, 75, sw=5.5)


@icon('column-add', 'table', 'Add column')
def column_add(a):
    a.ink_poly('frame', [(10, 12), (56, 12), (56, 84), (10, 84)], closed=True, radius=3)
    a.ink_line('row', 10, 48, 56, 48, sw=FINE)
    a.ink_line('column', 33, 12, 33, 84, sw=FINE)
    a.pen_line('plus-vertical', 75, 37, 75, 59, sw=5.5)
    a.pen_line('plus-horizontal', 64, 48, 86, 48, sw=5.5)


# -- review ---------------------------------------------------------------------------------------
@icon('comment', 'review', 'Comment')
def comment(a):
    a.ink_poly(
        'bubble',
        [(12, 14), (84, 14), (84, 62), (52, 62), (30, 82), (32, 62), (12, 62)],
        closed=True,
        radius=4,
    )
    a.ink_dot('origin', 25, 31, 12)
    a.text_line('line-0', 38, 31, 32)
    a.pen_curve('line-1', [(25, 47), (40, 46), (62, 47)], sw=5)


@icon('reply', 'review', 'Reply')
def reply(a):
    a.ink_poly(
        'bubble',
        [(12, 14), (84, 14), (84, 62), (52, 62), (30, 82), (32, 62), (12, 62)],
        closed=True,
        radius=4,
    )
    a.pen_poly('arrow-head', [(36, 26), (24, 38), (36, 50)], radius=2, sw=5)
    a.pen_curve('arrow', [(26, 38), (50, 36), (66, 44), (68, 52)], sw=5)


@icon('checks', 'review', 'Check')
def checks(a):
    a.page_outline('page', 12, 8, 54, 78)
    a.text_line('line-0', 22, 30, 28)
    a.text_line('line-1', 22, 44, 34)
    a.pen_poly('check', [(40, 64), (54, 78), (86, 42)], radius=3, sw=8)


@icon('protect', 'review', 'Protect')
def protect(a):
    a.ink_poly('body', [(22, 42), (74, 42), (74, 86), (22, 86)], closed=True, radius=5)
    a.ink_curve('shackle', [(33, 42), (33, 28), (41, 14), (55, 14), (63, 28), (63, 42)])
    a.ink_circle('keyhole', 48, 61, 6, sw=5, color='pen')
    a.pen_line('keyhole-slot', 48, 66, 48, 75, sw=5)


@icon('checkpoint', 'review', 'Checkpoint')
def checkpoint(a):
    a.ink_line('pole', 24, 10, 24, 88)
    a.pen_curve(
        'flag', [(27, 16), (46, 11), (65, 21), (80, 17), (78, 50), (62, 56), (44, 45), (27, 50)], closed=True
    )


@icon('branch', 'review', 'Alternate draft')
def branch(a):
    a.ink_circle('node-top', 30, 18, 7)
    a.ink_circle('node-bottom', 30, 78, 7)
    a.ink_line('trunk', 30, 26, 30, 70)
    a.pen_curve('branch', [(30, 62), (32, 46), (48, 40), (66, 33)])
    a.ink_circle('node-branch', 68, 26, 7)


@icon('history', 'review', 'Revision history')
def history(a):
    a.ink_circle('face', 56, 50, 28)
    a.ink_poly('hands', [(56, 32), (56, 50), (68, 58)], radius=2, sw=5)
    a.pen_curve('rewind', [(26, 24), (16, 36), (14, 52)], sw=PEN)
    a.pen_poly('rewind-head', [(6, 46), (14, 56), (22, 46)], radius=2, sw=5)


@icon('track-changes', 'review', 'Track changes')
def track_changes(a):
    a.page_outline('page', 12, 8, 54, 78)
    a.text_line('line-0', 22, 30, 32)
    a.pen_curve('strike', [(22, 44), (38, 43), (54, 44)], sw=4.6)
    a.text_line('line-2', 22, 58, 24)
    a.pen_curve('pen', [(50, 86), (66, 68), (84, 46)], sw=7)
    a.pen_curve('pen-tip', [(50, 86), (45, 87), (46, 82)], sw=4)


@icon('model-review', 'review', 'Review with a model')
def model_review(a):
    a.ink_poly('nib', [(38, 14), (56, 42), (48, 68), (38, 78), (28, 68), (20, 42)], closed=True, radius=3)
    a.ink_line('slit', 38, 46, 38, 72, sw=FINE)
    a.sparkle('spark', 72, 24, 14, sw=5)
    a.sparkle('spark-small', 72, 62, 8, sw=4.2)


# -- view -----------------------------------------------------------------------------------------
@icon('pages', 'view', 'Pages')
def pages(a):
    a.ink_poly(
        'book',
        [(48, 26), (26, 17), (8, 21), (8, 72), (26, 68), (48, 79), (70, 68), (88, 72), (88, 21), (70, 17)],
        closed=True,
        radius=3,
    )
    a.ink_line('spine', 48, 26, 48, 79, sw=FINE)
    a.text_line('left-0', 16, 34, 24, sw=4)
    a.text_line('left-1', 16, 47, 24, sw=4)
    a.pen_curve('right-0', [(56, 36), (68, 34), (80, 36)], sw=4.6)
    a.text_line('right-1', 56, 49, 24, sw=4)


@icon('source', 'view', 'Markdown source')
def source(a):
    a.ink_poly('open', [(34, 24), (14, 48), (34, 72)], radius=2)
    a.ink_poly('close', [(62, 24), (82, 48), (62, 72)], radius=2)
    a.pen_line('slash', 55, 18, 41, 78)


# -- tools ----------------------------------------------------------------------------------------
@icon('provider-settings', 'tools', 'Provider settings')
def provider_settings(a):
    a.ink_circle('bow', 28, 34, 15, color='pen')
    a.ink_line('shaft', 39, 45, 82, 84)
    a.ink_line('tooth-a', 64, 66, 72, 58, sw=5)
    a.ink_line('tooth-b', 76, 78, 84, 70, sw=5)


@icon('dictation', 'tools', 'Dictation')
def dictation(a):
    a.ink_poly('capsule', [(34, 8), (58, 8), (58, 46), (34, 46)], closed=True, radius=11)
    a.ink_arc('cradle', 46, 42, 20, 5, 175)
    a.ink_line('stand', 46, 64, 46, 82)
    a.ink_line('base', 32, 86, 60, 86)
    a.pen_arc('wave-near', 48, 30, 25, -40, 40, sw=5)
    a.pen_arc('wave-far', 48, 30, 36, -36, 36, sw=5)


@icon('import-audio', 'tools', 'Import audio')
def import_audio(a):
    a.page_outline('page', 14, 8, 56, 80)
    a.pen_curve(
        'wave', [(22, 58), (29, 58), (34, 44), (40, 72), (46, 36), (52, 74), (58, 50), (62, 58)], sw=5
    )
    a.text_line('line-0', 24, 26, 24)


@icon('jobs', 'tools', 'Background jobs')
def jobs(a):
    a.ink_poly(
        'glass',
        [(26, 12), (70, 12), (70, 26), (54, 48), (70, 70), (70, 84), (26, 84), (26, 70), (42, 48), (26, 26)],
        closed=True,
        radius=3,
    )
    a.pen_curve('sand', [(36, 76), (48, 72), (60, 76)], sw=6)
    a.pen_line('stream', 48, 50, 48, 68, sw=4.4)


@icon('properties', 'tools', 'Document properties')
def properties(a):
    a.page_outline('page', 8, 8, 44, 78)
    a.text_line('line-0', 17, 30, 24)
    a.text_line('line-1', 17, 44, 16)
    a.ink_circle('badge', 70, 66, 17, color='pen')
    a.ink_line('badge-stem', 70, 65, 70, 78, sw=5.5)
    a.bullet('badge-dot', 70, 54, 3.4)


@icon('statistics', 'tools', 'Document statistics')
def statistics(a):
    a.ink_poly('axes', [(12, 12), (12, 84), (86, 84)], radius=2)
    for index, (x, top) in enumerate([(24, 60), (44, 46), (64, 34)]):
        a.ink_poly(f'bar-{index}', [(x, 84), (x, top), (x + 12, top), (x + 12, 84)], radius=1.5, sw=FINE)
    a.pen_curve('trend', [(20, 46), (40, 32), (58, 36), (82, 14)], sw=5.5)
