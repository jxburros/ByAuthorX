"""Specimen posters and the overview sheet, built and checked in Vixl with the compiled fonts."""

from __future__ import annotations


from vixl import Project
from vixl.fonts import import_font

PAPER = '#F4EEE3'
INK = '#252927'
MUTED = '#52615B'
GREEN = '#367B6C'
RED = '#AD283E'
RULE = '#D7D0C3'
LABEL_FONT = 'manrope'
LABEL_BOLD = 'manrope-semibold'

WIDTH = 1600
PANGRAM = 'Quick wafting zephyrs vex bold Jim,\nsaid the ink to the page.'
UPPER = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
LOWER = 'abcdefghijklmnopqrstuvwxyz'
FIGURES = '0123456789'
SYMBOLS = '& @ # $ % ( ) [ ] { } ! ? : ; , . “ ” ‘ ’ — – …'
ACCENTS = 'ÀÉÎÕÜ àéîõü ñçß æœøå ÿ'
MARGIN = 80


def text(name, content, font, size, x, y, width, height, color=INK, **extra):
    """A text layer plus its layout box; fit shrinks the text to the box."""
    layout = dict(type='text-layout', target=name, width=width, height=height, fit=extra.pop('fit', True))
    if 'wrap' in extra:
        layout['wrap'] = extra.pop('wrap')
    return [
        dict(type='text', name=name, text=content, font=font, size=size, color=color, x=x, y=y, **extra),
        layout,
    ]


def ink_extent(compiled, content):
    """(width, top, bottom) of the string's ink in font units, from the compiled glyphs (no kerning)."""
    width, top, bottom = 0, -(10**6), 10**6
    for ch in content:
        glyph = compiled.glyphs.get(compiled.cmap.get(ord(ch), ''), None)
        if glyph is None:
            continue
        width += glyph['advance']
        for contour in glyph['contours']:
            for _, y, _ in contour:
                top, bottom = max(top, y), min(bottom, y)
    return width, top, bottom


def fit_size(compiled, content, box_width, ink_height=None, limit=400):
    """The largest size up to `limit` (or the one giving `ink_height` px of ink) that fits the box width."""
    width, top, bottom = ink_extent(compiled, content)
    size = limit if ink_height is None else min(limit, ink_height * 1000 / max(top - bottom, 1))
    size = min(size, box_width * 1000 / max(width, 1) * 0.97)
    return size, (top - bottom) * size / 1000


class Stack:
    """Places text blocks by their ink: every block starts `gap` px under the ink of the one before."""

    def __init__(self, compiled, key, gap=34):
        self.compiled, self.key, self.gap, self.ops = compiled, key, gap, []

    def font_text(self, name, content, size, x, y, width, line_height=1.0, **extra):
        """One line of the family's own type placed with its ink top at y; returns the ink bottom."""
        lines = content.split('\n')
        ink = 0.0
        for i, line in enumerate(lines):
            _, top, bottom = ink_extent(self.compiled, line)
            ink = (i * size * line_height) + (top - bottom) * size / 1000
        box = round(ink + 0.3 * size)
        self.ops += text(
            name, content, self.key, round(size), x, round(y), width, box, line_height=line_height, **extra
        )
        return y + ink

    def label(
        self,
        name,
        content,
        size,
        x,
        y,
        width,
        lines=1,
        font=LABEL_FONT,
        color=MUTED,
        line_height=1.4,
        **extra,
    ):
        ink = 0.78 * size + (lines - 1) * size * line_height
        self.ops += text(
            name,
            content,
            font,
            size,
            x,
            round(y),
            width,
            round(ink + 0.45 * size),
            color=color,
            line_height=line_height,
            **extra,
        )
        return y + ink


def specimen_ops(compiled, key, index, total, opts):
    spec = compiled.spec
    lh = opts.get('line_height', 1.12)
    right = WIDTH - MARGIN
    column = 1000  # where the left column ends
    name = opts.get('title', spec.family)
    st = Stack(compiled, key)
    gap = st.gap
    ops = st.ops
    st.label(
        'kicker',
        f'BYAUTHORX BRAND FONTS  /  {index:02d} OF {total:02d}',
        17,
        MARGIN,
        64,
        600,
        font=LABEL_BOLD,
        tracking=1.5,
    )
    st.label(
        'kicker-right',
        f'TrueType  /  {len(compiled.cmap)} characters',
        17,
        right - 360,
        64,
        360,
        align='right',
    )
    ops.append(
        dict(
            type='shape',
            name='top-rule',
            shape='rectangle',
            x=MARGIN,
            y=104,
            width=WIDTH - 2 * MARGIN,
            height=1,
            fill=RULE,
        )
    )
    top = 132
    # left column: title, pen signature, tagline, pangram
    size, _ = fit_size(compiled, name, column - MARGIN, opts.get('ink_height', 220))
    y = st.font_text('title', name, size, MARGIN, top, column - MARGIN, line_height=lh)
    y += gap
    ops += [
        dict(
            type='shape',
            name='ink-dot',
            shape='ellipse',
            x=MARGIN,
            y=round(y) - 7,
            width=18,
            height=18,
            fill=RED,
        ),
        dict(
            type='shape',
            name='pen-line',
            shape='rectangle',
            x=MARGIN,
            y=round(y),
            width=350,
            height=4,
            fill=GREEN,
            radius=2,
        ),
    ]
    y += 4 + gap
    lines = -(-len(spec.description) * 11.4 // 700)
    y = st.label('tagline', spec.description, 23, MARGIN, y, 700, lines=int(lines), wrap='pretty') + gap
    pangram = opts.get('pangram', PANGRAM)
    longest = max(pangram.split('\n'), key=lambda line: ink_extent(compiled, line)[0])
    psize, _ = fit_size(compiled, longest, column - MARGIN, limit=opts.get('pangram_size', 60))
    left_end = st.font_text('pangram', pangram, psize, MARGIN, y, column - MARGIN, line_height=lh)
    # right column: the Aa tile, figures, symbols
    asize, ah = fit_size(compiled, 'Aa', right - column - 40, 330)
    y = st.font_text('specimen-aa', 'Aa', asize, column + 40, top, right - column - 40)
    y += gap
    y = (
        st.font_text(
            'figures',
            FIGURES,
            fit_size(compiled, FIGURES, right - column - 40, limit=68)[0],
            column + 40,
            y,
            right - column - 40,
        )
        + gap
    )
    right_end = st.font_text(
        'symbols-side',
        '& @ # $ % ? !',
        fit_size(compiled, '& @ # $ % ? !', right - column - 40, limit=62)[0],
        column + 40,
        y,
        right - column - 40,
    )
    y = max(left_end, right_end) + gap
    ops.append(
        dict(
            type='shape',
            name='mid-rule',
            shape='rectangle',
            x=MARGIN,
            y=round(y),
            width=WIDTH - 2 * MARGIN,
            height=1,
            fill=RULE,
        )
    )
    y += 1 + gap
    full = WIDTH - 2 * MARGIN
    for label, content, limit in (
        ('alphabet-upper', UPPER, 56),
        ('alphabet-lower', LOWER, 56),
        ('symbols', SYMBOLS, 44),
        ('accents', ACCENTS, 44),
    ):
        s, _ = fit_size(compiled, content, full, limit=opts.get('set_size', limit) if limit == 56 else limit)
        y = st.font_text(label, content, s, MARGIN, y, full) + gap * 0.7
    st.label(
        'footer',
        f'{spec.family} {spec.style}  /  version {spec.version}  /  drawn and compiled with Vixl',
        15,
        MARGIN,
        y + gap * 0.5,
        800,
    )
    return ops, y + gap * 0.5 + 15 + 90


def build_specimen(root, key, compiled, font_path, index, total, label_fonts, opts=None):
    opts = opts or {}
    layout, height = specimen_ops(compiled, key, index, total, opts)
    project = Project(WIDTH, round(height), PAPER, workspace=root)
    for name, path in {**label_fonts, key: font_path}.items():
        import_font(project, path, name)
    ops = [dict(type='accessibility', lang='en', title=f'{compiled.spec.family} type specimen')]
    result = project.apply(ops + layout)
    return project, result


def overview(root, items, label_fonts, font_paths):
    """One sheet showing all families: name, sample line and role, each in its own face."""
    row_h = 252
    top = 280
    height = top + 60 + row_h * (len(items) - 1) + 230 + 96
    project = Project(WIDTH, height, PAPER, workspace=root)
    for name, path in {**label_fonts, **font_paths}.items():
        import_font(project, path, name)
    first_key, first = items[0][0], items[0][1]
    ops = [dict(type='accessibility', lang='en', title='ByAuthorX brand fonts overview')]
    st = Stack(first, first_key)
    st.label('kicker', 'BYAUTHORX BRAND FONTS', 17, MARGIN, 88, 600, font=LABEL_BOLD, tracking=1.5)
    st.label(
        'kicker-right',
        f'{len(items)} families  /  TrueType  /  drawn and compiled with Vixl',
        17,
        WIDTH - MARGIN - 560,
        88,
        560,
        align='right',
    )
    st.ops.append(
        dict(
            type='shape',
            name='top-rule',
            shape='rectangle',
            x=MARGIN,
            y=132,
            width=WIDTH - 2 * MARGIN,
            height=1,
            fill=RULE,
        )
    )
    size, _ = fit_size(first, 'Your words. Your call.', WIDTH - 2 * MARGIN, 96)
    st.font_text('headline', 'Your words. Your call.', size, MARGIN, 168, WIDTH - 2 * MARGIN)
    st.ops += [
        dict(type='shape', name='ink-dot', shape='ellipse', x=MARGIN, y=292, width=18, height=18, fill=RED),
        dict(
            type='shape',
            name='pen-line',
            shape='rectangle',
            x=MARGIN,
            y=299,
            width=350,
            height=4,
            fill=GREEN,
            radius=2,
        ),
    ]
    y = top + 60
    for number, (key, compiled, role) in enumerate(items, 1):
        spec = compiled.spec
        sub = Stack(compiled, key)
        sub.label(
            f'role-{number}',
            f'{number:02d}  /  {role.upper()}',
            15,
            MARGIN,
            y,
            700,
            font=LABEL_BOLD,
            tracking=1.2,
        )
        size, ink = fit_size(compiled, spec.family, 700, 104)
        sub.font_text(f'name-{number}', spec.family, size, MARGIN, y + 34, 700)
        sub.label(
            f'about-{number}', spec.description, 17, MARGIN, y + 34 + ink + 20, 700, lines=2, wrap='pretty'
        )
        sample = 'Handbags & quartz 0123' if spec.mono_advance else 'Your words, your call.'
        ssize, _ = fit_size(compiled, sample, 640, limit=62)
        sub.font_text(f'sample-{number}', sample, ssize, 860, y + 34, 640)
        st.ops += sub.ops
        if number < len(items):
            st.ops.append(
                dict(
                    type='shape',
                    name=f'rule-{number}',
                    shape='rectangle',
                    x=MARGIN,
                    y=y + row_h - 36,
                    width=WIDTH - 2 * MARGIN,
                    height=1,
                    fill=RULE,
                )
            )
        y += row_h
    result = project.apply(ops + st.ops)
    return project, result
