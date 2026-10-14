"""The shared character set and the assembly of a family's shapes from its skeleton functions."""

from __future__ import annotations

import inspect

from shapely.geometry import Polygon

from . import marks
from .compile import glyph_name
from .geom import render
from .lower import LOWER
from .misc import MISC
from .upper import UPPER

# codepoint -> skeleton name (a key of LOWER, UPPER or MISC)
BASIC = {}
for ch in 'abcdefghijklmnopqrstuvwxyz':
    BASIC[ord(ch)] = ch
for ch in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
    BASIC[ord(ch)] = ch

MISC_CODEPOINTS = {
    0x21: 'exclam',
    0x22: 'quotedbl',
    0x23: 'numbersign',
    0x24: 'dollar',
    0x25: 'percent',
    0x26: 'ampersand',
    0x27: 'quotesingle',
    0x28: 'parenleft',
    0x29: 'parenright',
    0x2A: 'asterisk',
    0x2B: 'plus',
    0x2C: 'comma',
    0x2D: 'hyphen',
    0x2E: 'period',
    0x2F: 'slash',
    0x3A: 'colon',
    0x3B: 'semicolon',
    0x3C: 'less',
    0x3D: 'equal',
    0x3E: 'greater',
    0x3F: 'question',
    0x40: 'at',
    0x5B: 'bracketleft',
    0x5C: 'backslash',
    0x5D: 'bracketright',
    0x5E: 'asciicircum',
    0x5F: 'underscore',
    0x60: 'grave',
    0x7B: 'braceleft',
    0x7C: 'bar',
    0x7D: 'braceright',
    0x7E: 'asciitilde',
    0xA1: 'exclamdown',
    0xA2: 'cent',
    0xA3: 'sterling',
    0xA5: 'yen',
    0xA6: 'brokenbar',
    0xA7: 'section',
    0xA9: 'copyright',
    0xAB: 'guillemotleft',
    0xAE: 'registered',
    0xB0: 'degree',
    0xB1: 'plusminus',
    0xB6: 'paragraph',
    0xB7: 'periodcentered',
    0xBB: 'guillemotright',
    0xBF: 'questiondown',
    0xD7: 'multiply',
    0xF7: 'divide',
    0xDF: 'germandbls',
    0xC6: 'AE',
    0xE6: 'ae',
    0x152: 'OE',
    0x153: 'oe',
    0xD8: 'Oslash',
    0xF8: 'oslash',
    0xD0: 'Eth',
    0xF0: 'eth',
    0xDE: 'Thorn',
    0xFE: 'thorn',
    0x131: 'dotlessi',
    0x141: 'Lslash',
    0x142: 'lslash',
    0x111: 'dcroat',
    0x2013: 'endash',
    0x2014: 'emdash',
    0x2018: 'quoteleft',
    0x2019: 'quoteright',
    0x201A: 'quotesinglbase',
    0x201C: 'quotedblleft',
    0x201D: 'quotedblright',
    0x201E: 'quotedblbase',
    0x2020: 'dagger',
    0x2021: 'daggerdbl',
    0x2022: 'bullet',
    0x2026: 'ellipsis',
    0x2039: 'guilsinglleft',
    0x203A: 'guilsinglright',
    0x20AC: 'Euro',
    0x2122: 'trademark',
    0x2212: 'minus',
}

KIND_OF = {}


def skeleton(name):
    for table in (LOWER, UPPER, MISC):
        if name in table:
            return table[name]
    raise KeyError(name)


def make_glyph(P, name):
    fn = skeleton(name)
    return fn(P, name) if len(inspect.signature(fn).parameters) > 1 else fn(P)


def codepoints(extra=()):
    table = dict(BASIC)
    table.update(MISC_CODEPOINTS)
    for item in extra:
        table[item[0]] = item[1]
    return table


def assemble(P, post=None, accents=True, overrides=None, render_fn=None, variants=0):
    """Render every glyph of the character set. Returns an ordered dict name -> shape item for compile_family."""
    overrides = overrides or {}
    shapes = {}
    shapes['space'] = dict(geom=Polygon(), kind='fixed', unicode=0x20, advance=None, fixed=True, space=True)
    shapes['nbspace'] = dict(geom=Polygon(), kind='fixed', unicode=0xA0, advance=None, fixed=True, space=True)
    cmap = codepoints()
    done = {}
    for cp in sorted(cmap):
        skel = cmap[cp]
        gname = glyph_name(cp)
        g = overrides.get(skel)
        g = g(P) if g else make_glyph(P, skel)
        geom = render_fn(g, gname) if render_fn else render(g, P.nib)
        if post:
            geom = post(geom, gname)
        item = dict(
            geom=geom, kind=g.kind, unicode=cp, skeleton=skel, advance=g.advance, fixed=g.advance is not None
        )
        shapes[gname] = item
        done[skel] = gname
        if variants and (skel.isalpha() and len(skel) == 1):
            for k in range(1, variants + 1):
                alt = f'{gname}.alt{k}'
                geom = render_fn(g, alt) if render_fn else render(g, P.nib)
                if post:
                    geom = post(geom, alt)
                shapes[alt] = dict(
                    geom=geom,
                    kind=g.kind,
                    unicode=None,
                    skeleton=skel,
                    advance=None,
                    fixed=False,
                    alt=k,
                    base=gname,
                )
    if accents:
        for cp, gname, base, mark in marks.ACCENTED:
            if base == 'dotlessi':
                base_name = 'dotlessi'
            else:
                base_name = glyph_name(ord(base)) if len(base) == 1 else base
            if base_name not in shapes:
                continue
            shapes[gname] = dict(
                kind='accent',
                composite=True,
                unicode=cp,
                base=base_name,
                geom=None,
                skeleton=f'{base}+{mark}',
                mark_geom=marks.accent_for(P, base, mark),
            )
    return shapes
