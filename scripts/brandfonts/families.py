"""The five families: drawing parameters, finishing and font metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

import numpy as np
from shapely import affinity

from . import glyphset
from .compile import Metrics, Spec, compile_family
from .geom import Glyph, Nib, Stroke, jitter, render, stitch_grid
from .outline import roughen
from .params import Params, Serifs


@dataclass
class Family:
    key: str
    spec: Spec
    params: Params
    post: object = None  # callable(geom, glyph_name) -> geom
    overrides: dict = field(default_factory=dict)
    features: str = ''
    sample: str = ''
    accents: bool = True
    render_fn: object = None  # callable(glyph, glyph_name) -> geometry, replacing the plain nib sweep
    specimen: dict = field(default_factory=dict)  # layout hints for the specimen sheet
    variants: int = 0

    def shapes(self):
        return glyphset.assemble(
            self.params,
            post=self.post,
            accents=self.accents,
            overrides=self.overrides,
            render_fn=self.render_fn,
            variants=self.variants,
        )

    def compile(self):
        return compile_family(self.spec, self.shapes(), self.features)


def seed_of(key, name):
    return int.from_bytes(hashlib.sha256(f'{key}:{name}'.encode()).digest()[:4], 'big')


def irongall():
    nib = Nib.rect(106, 10, 19, round_=2.5)
    P = Params(
        xh=480,
        cap=690,
        asc=752,
        desc=-240,
        nib=nib,
        serif=Serifs(length=40, thick=36, bracket=44, flag=46, flag_drop=24, tip=0.85),
        wide=1.10,
        ball=21,
        over=11,
        dot=44,
        stress=-5,
    )
    m = Metrics(xh=480, cap=690, asc=752, desc=-240, ascent=960, descent=300)
    spec = Spec(
        key='irongall',
        family='Irongall',
        weight=600,
        metrics=m,
        nominal=dict(lower=70, upper=94, figure=72, punct=62, symbol=80),
        depth=64,
        space=250,
        kern_cap=380,
        kern_strength=0.5,
        description='A rough, inked old-style serif drawn from the ByAuthorX wordmark: broad-pen contrast, '
        'bracketed serifs, ball terminals and letterpress edges.',
    )

    def post(geom, name):
        return roughen(geom, amp=5.8, scale=40, seed=seed_of('irongall', name), open_r=10.0, close_r=6.5)

    return Family('irongall', spec, P, post=post)


def tenuis():
    nib = Nib.ellipse(13, 10.5, n=36)
    P = Params(
        xh=610,
        cap=830,
        asc=900,
        desc=-170,
        nib=nib,
        wide=0.46,
        cap_wide=0.50,
        over=7,
        single_a=True,
        dot=19,
        figure_wide=0.52,
    )
    m = Metrics(xh=610, cap=830, asc=900, desc=-170, ascent=1000, descent=230)
    spec = Spec(
        key='tenuis',
        family='Tenuis',
        weight=200,
        width=3,
        metrics=m,
        nominal=dict(lower=96, upper=116, figure=100, punct=84, symbol=110),
        depth=90,
        space=250,
        kern_cap=420,
        kern_strength=0.5,
        description='A tall, thin, minimal display face: hairline strokes on narrow letters more than twice '
        'as tall as they are wide.',
    )
    return Family('tenuis', spec, P)


def brobdingnag():
    nib = Nib.superellipse(75, 61, power=2.7)
    P = Params(
        xh=565,
        cap=745,
        asc=790,
        desc=-205,
        nib=nib,
        wide=1.12,
        cap_wide=1.08,
        over=12,
        single_a=True,
        serif=Serifs(length=40, thick=70, bracket=0, flag=44, flag_drop=0, tip=1.0, slab=True),
        dot=84,
        ball=0,
    )
    m = Metrics(xh=565, cap=745, asc=790, desc=-205, ascent=950, descent=290)
    spec = Spec(
        key='brobdingnag',
        family='Brobdingnag',
        weight=900,
        width=7,
        metrics=m,
        nominal=dict(lower=50, upper=74, figure=54, punct=52, symbol=60),
        depth=56,
        space=260,
        kern_cap=360,
        kern_strength=0.5,
        description='A huge, heavy slab display face for headlines: black strokes, short square serifs and '
        'tight counters.',
    )
    return Family('brobdingnag', spec, P)


def _calt_cycle(letters, variants):
    """A calt rule set that cycles each letter through its alternates: base, alt1, alt2, base ..."""
    base = ' '.join(letters)
    lines = [f'@base = [{base}];']
    for k in range(1, variants + 1):
        lines.append(f'@alt{k} = [{" ".join(f"{n}.alt{k}" for n in letters)}];')
    lines.append('feature calt {')
    lines.append("    sub @base @base' by @alt1;")
    for k in range(1, variants):
        lines.append(f"    sub @alt{k} @base' by @alt{k + 1};")
    lines.append('} calt;')
    return '\n'.join(lines)


def scholium():
    nib = Nib.ellipse(21, 18, n=28)
    P = Params(
        xh=450,
        cap=650,
        asc=720,
        desc=-250,
        nib=nib,
        wide=0.98,
        cap_wide=0.94,
        over=7,
        single_a=True,
        dot=27,
        ball=0,
    )
    m = Metrics(xh=450, cap=650, asc=720, desc=-250, ascent=900, descent=330)
    spec = Spec(
        key='scholium',
        family='Scholium',
        weight=400,
        metrics=m,
        italic_angle=-9.0,
        nominal=dict(lower=56, upper=66, figure=58, punct=54, symbol=70),
        depth=60,
        space=210,
        kern_cap=380,
        kern_strength=0.45,
        description='A hand-drawn monoline face from the ByAuthorX illustrations: slightly uneven pen lines '
        'with three alternates per letter that cycle as you type.',
    )
    letters = [glyph_of(c) for c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ']
    variants = 2

    def render_fn(g, name):
        rng = np.random.default_rng(seed_of('scholium', name))
        sx, sy = rng.uniform(0.96, 1.05), rng.uniform(0.96, 1.04)
        press = rng.uniform(0.55, 0.8)
        wobbly = Glyph(kind=g.kind, role=g.role)
        for st in g.strokes:
            pts = jitter(st.points, 6.5, 85, rng)
            pts = pts * (sx, sy)
            clip = None if st.clip is None else (st.clip[0] * sy, st.clip[1] * sy)
            wobbly.strokes.append(
                Stroke(
                    pts,
                    st.nib,
                    clip,
                    None,
                    st.taper,
                    lambda t, p=press: p + (1.18 - p) * np.sin(np.pi * t) ** 0.8,
                    st.blob,
                )
            )
        for sh in g.shapes:
            blob = roughen(
                affinity.translate(sh, rng.uniform(-3, 3), rng.uniform(-3, 3)),
                amp=5.5,
                scale=26,
                seed=int(rng.integers(1 << 30)),
                open_r=2.0,
                close_r=2.0,
            )
            wobbly.shapes.append(blob)
        geom = render(wobbly, P.nib)
        if geom.is_empty:
            return geom
        x0, y0, x1, y1 = geom.bounds
        geom = affinity.rotate(geom, rng.uniform(-2.2, 2.2), origin=((x0 + x1) / 2, 0))
        geom = affinity.translate(geom, 0, rng.uniform(-6, 6))
        return affinity.skew(geom, xs=9.0, origin=(0, 0))

    return Family(
        'scholium', spec, P, render_fn=render_fn, variants=variants, features=_calt_cycle(letters, variants)
    )


def glyph_of(ch):
    from .compile import glyph_name

    return glyph_name(ord(ch))


def onionskin():
    nib = Nib.ellipse(29, 25, n=32)
    wo = dict(
        m=0.88,
        w=0.88,
        M=0.64,
        W=0.68,
        A=0.66,
        O=0.70,
        C=0.66,
        G=0.68,
        D=0.66,
        Q=0.70,
        N=0.64,
        U=0.64,
        V=0.68,
        X=0.66,
        Y=0.68,
        K=0.64,
        H=0.64,
        B=0.62,
        R=0.64,
        S=0.58,
        T=0.64,
        Z=0.60,
        E=0.56,
        F=0.54,
        L=0.54,
        P=0.58,
    )
    P = Params(
        xh=500,
        cap=690,
        asc=745,
        desc=-205,
        nib=nib,
        wide=1.0,
        cap_wide=0.84,
        over=8,
        single_a=False,
        serif=Serifs(length=46, thick=36, bracket=0, flag=46, flag_drop=0, tip=1.0, slab=True),
        dot=36,
        width_override=wo,
    )
    m = Metrics(xh=500, cap=690, asc=745, desc=-205, ascent=900, descent=260)
    spec = Spec(
        key='onionskin',
        family='Onionskin',
        weight=400,
        metrics=m,
        monospace=True,
        mono_advance=600,
        nominal=dict(lower=60, upper=60, figure=60, punct=60, symbol=60),
        depth=60,
        space=600,
        description='A typewriter face for drafts: fixed-pitch slab letters with the slight uneven ink of a '
        'carbon copy.',
    )

    def post(geom, name):
        return roughen(geom, amp=1.8, scale=26, seed=seed_of('onionskin', name), open_r=3.0, close_r=2.5)

    from .lower import LOWER
    from .upper import UPPER

    def widen(table, key, feet):
        """The skeleton `key` with extra long slab feet, so narrow letters fill the typewriter's fixed cell."""
        base = table[key]

        def build(P_):
            g = base(P_)
            for x, y, up in feet(P_):
                g.add(P_.foot(x, y, up=up, length=110))
            return g

        return build

    overrides = {
        'i': widen(LOWER, 'i', lambda P_: [(P_.hv, 0, 1)]),
        'l': widen(LOWER, 'l', lambda P_: [(P_.hv, 0, 1)]),
        'j': widen(LOWER, 'j', lambda P_: []),
        'I': widen(UPPER, 'I', lambda P_: [(P_.hv, 0, 1), (P_.hv, P_.cap, -1)]),
        '1': widen(UPPER, '1', lambda P_: [(P_.W('1') * 0.62, 0, 1)]),
        'f': widen(LOWER, 'f', lambda P_: [(P_.hv + 0.05 * P_.xh, 0, 1)]),
        'r': widen(LOWER, 'r', lambda P_: [(P_.hv, 0, 1)]),
    }
    return Family('onionskin', spec, P, post=post, overrides=overrides)


def decussate():
    pitch = 72.0
    s = pitch / 2
    nib = Nib.ellipse(s, s, n=24)
    P = Params(
        xh=7 * pitch,
        cap=11 * pitch,
        asc=12 * pitch,
        desc=-3 * pitch,
        nib=nib,
        wide=1.0,
        cap_wide=1.0,
        over=0.0,
        single_a=True,
        dot=s,
        grid=pitch,
        extras=dict(stitch=dict(half=pitch * 0.47, pitch=pitch, arm=pitch * 0.15)),
    )
    m = Metrics(xh=7 * pitch, cap=11 * pitch, asc=12 * pitch, desc=-3 * pitch, ascent=1000, descent=300)
    spec = Spec(
        key='decussate',
        family='Decussate',
        weight=700,
        metrics=m,
        cell=pitch,
        nominal=dict(lower=pitch, upper=pitch, figure=pitch, punct=pitch, symbol=pitch),
        depth=2 * pitch,
        space=pitch * 4,
        kern_strength=0.0,
        description='A novelty face where every letter is stitched from little X marks, like a cross-stitch '
        'sampler.',
    )

    def render_fn(g, name):
        return stitch_grid(render(g, P.nib), pitch, 0.42, pitch * 0.47, pitch * 0.15)

    return Family('decussate', spec, P, render_fn=render_fn)


FAMILIES = {
    'irongall': irongall,
    'scholium': scholium,
    'tenuis': tenuis,
    'brobdingnag': brobdingnag,
    'onionskin': onionskin,
    'decussate': decussate,
}


def get(key):
    return FAMILIES[key]()
