#!/usr/bin/env python3
"""Build the ByAuthorX logo UI library: interface icons, illustrations and motion, authored in Vixl.

The library extends the logo companion shelf with the pieces a native word processor needs every day:
a complete icon set for the toolbar and menus, small illustrations for dialogs and empty states, and
task-owned motion. Everything is drawn from the supplied logo masters (pen line, ink blot, mark) and
the approved palette, so it carries the logo's hand-inked character into the application.

Run in the separate authoring environment (see docs/brand.md). Vixl is not a runtime dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from vixl import Project

import build_creative_library as library
import build_logo_companions as companions
import logo_ui_art as art
import logo_ui_icons as icon_set
import logo_ui_scenes as scenes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/logo-ui-library'
THEMES = ('light', 'dark')
ATLAS_SIZES = (16, 24, 32, 48)  # logical 16/24 px, plus 2x of those
ATLAS_COLUMNS = 11
VIXL_VERSION = '0.27.0'

# Brand colours are exact by construction. Where the green pen crosses ink, anti-aliasing blends the two:
# such a pixel is at most half the largest channel gap from the nearer colour (41 levels for ink and green
# on paper). The palette rule tolerates exactly that blend and almost nothing else, so a stray hue fails.
PALETTE_STRAY_FRACTION = 0.005


def _rgb(value):
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def blend_tolerance(first, second):
    gap = max(abs(a - b) for a, b in zip(_rgb(first), _rgb(second)))
    return math.ceil(gap / 2) + 1


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def brand_suite(tokens, theme):
    colors = tokens['color']
    ink = colors['ink'] if theme == 'light' else colors['paper']
    return dict(
        version=1,
        rules=[
            dict(id='editable-vectors', kind='count', layer_type='shape', minimum=1),
            dict(id='no-baked-copy', kind='count', layer_type='text', maximum=0),
            dict(
                id='brand-palette',
                kind='palette',
                colors=[ink, colors['green'], colors['red']],
                tolerance=blend_tolerance(ink, colors['green']),
                max_fraction=PALETTE_STRAY_FRACTION,
            ),
        ],
    )


def build_vixl(out, category, stem, canvas, ops, suite, title, poster_time=None, exports=('svg', 'png')):
    """Apply an operation list to a fresh document, check it, and write master + exports."""
    directory = out / category
    directory.mkdir(parents=True, exist_ok=True)
    width, height = canvas
    project = Project(width, height, 'transparent', workspace=out)
    operations = [
        dict(type='suite-set', name='asset-contract', suite=suite),
        dict(type='accessibility', lang='en', title=title),
    ] + ops
    project.apply(operations)
    checks = project.check(checks=['bounds', 'motion', 'composition'])
    contract = project.check_suite('asset-contract')
    if not checks['passed'] or not contract['passed']:
        raise RuntimeError(f'{stem}: {json.dumps(checks)} {json.dumps(contract)}')
    project.save(directory / f'{stem}.vixl', overwrite=True)
    write_json(
        directory / f'{stem}.recipe.json',
        dict(canvas=dict(width=width, height=height, background='transparent'), operations=operations),
    )
    return project, checks, contract


def build_icons(out, tokens):
    entries, renders = [], {}
    rows = math.ceil(len(icon_set.ICONS) / ATLAS_COLUMNS)
    atlas = {
        (theme, size): Image.new('RGBA', (ATLAS_COLUMNS * size, rows * size))
        for theme in THEMES
        for size in ATLAS_SIZES
    }
    names = list(icon_set.ICONS)
    for name, spec in icon_set.ICONS.items():
        for theme in THEMES:
            drawing = art.UIArt(icon_set.GRID, icon_set.GRID, theme, seed=name)
            spec['draw'](drawing)
            stem = f'{name}-{theme}'
            project, checks, contract = build_vixl(
                out,
                'icons',
                stem,
                (icon_set.GRID, icon_set.GRID),
                drawing.ops,
                brand_suite(tokens, theme),
                spec['label'],
            )
            directory = out / 'icons'
            project.export(directory / f'{stem}.svg', svg_policy='strict', overwrite=True)
            project.export(directory / f'{stem}.png', alpha='keep', overwrite=True)
            with tempfile.TemporaryDirectory() as scratch:
                renders.setdefault(name, {})
                for size in ATLAS_SIZES:
                    target = Path(scratch) / f'{size}.png'
                    project.export(target, scale=size / icon_set.GRID, alpha='keep', overwrite=True)
                    with Image.open(target) as image:
                        tile = image.convert('RGBA')
                    index = names.index(name)
                    atlas[(theme, size)].paste(
                        tile, ((index % ATLAS_COLUMNS) * size, (index // ATLAS_COLUMNS) * size)
                    )
                    if theme == 'light':
                        renders[name][size] = tile
            entries.append(
                dict(
                    id=stem,
                    design=name,
                    category='icons',
                    group=spec['group'],
                    theme=theme,
                    title=spec['label'],
                    width=icon_set.GRID,
                    height=icon_set.GRID,
                    transparent=True,
                    master=f'icons/{stem}.vixl',
                    recipe=f'icons/{stem}.recipe.json',
                    preview=f'icons/{stem}.png',
                    vector=f'icons/{stem}.svg',
                    exports=['svg', 'png'],
                    checks=checks,
                    contract=contract,
                    use=f'{spec["label"]}: toolbar and menu icon; set beside its text label.',
                    family='logo-ui',
                )
            )
        print(f'Built icon {name}', flush=True)
    for (theme, size), image in atlas.items():
        image.save(out / 'icons' / f'atlas-{theme}-{size}.png', optimize=True)
    # Recipes, not masters: Vixl embeds random layer IDs in a .vixl, so only the recipe hash is reproducible.
    recipes = {name: sha(out / 'icons' / f'{name}-light.recipe.json') for name in names}
    write_json(
        out / 'icons' / 'atlas.json',
        dict(
            generator=f'Vixl {VIXL_VERSION}',
            grid=icon_set.GRID,
            sizes=list(ATLAS_SIZES),
            themes=list(THEMES),
            columns=ATLAS_COLUMNS,
            rows=rows,
            names=names,
            groups={name: spec['group'] for name, spec in icon_set.ICONS.items()},
            labels={name: spec['label'] for name, spec in icon_set.ICONS.items()},
            files={
                f'{theme}-{size}': f'atlas-{theme}-{size}.png' for theme in THEMES for size in ATLAS_SIZES
            },
            recipes_light_sha256=recipes,
            checks={
                'passed': all(e['checks']['passed'] and e['contract']['passed'] for e in entries),
                'icons': len(names),
            },
        ),
    )
    return entries, renders


def scene_suite():
    return dict(
        version=1,
        rules=[
            dict(id='editable-vectors', kind='count', layer_type='shape', minimum=1),
            dict(id='no-baked-copy', kind='count', layer_type='text', maximum=0),
        ],
    )


def build_scenes(out):
    entries = []
    for category, slugs, factory in (
        ('illustrations', scenes.ILLUSTRATIONS, scenes.illustration),
        ('animations', scenes.ANIMATIONS, scenes.animation),
    ):
        for slug, use in slugs.items():
            for theme in THEMES:
                drawing = factory(slug, theme)
                entry = library.build_one(out, category, slug, theme, drawing)
                entry['use'] = use
                entry['family'] = 'logo-ui'
                if category == 'animations':
                    entry['motion_behavior'] = 'write-erase'
                entries.append(entry)
    return entries


def build_rules(out, tokens):
    entries = []
    for slug, use in scenes.RULES.items():
        for theme in THEMES:
            drawing = scenes.rule(slug, theme)
            stem = f'{slug}-{theme}'
            canvas = scenes.canvas_of(drawing)
            project, checks, contract = build_vixl(
                out, 'rules', stem, canvas, drawing.ops, scene_suite(), slug.replace('-', ' ')
            )
            directory = out / 'rules'
            project.export(directory / f'{stem}.svg', svg_policy='strict', overwrite=True)
            project.export(directory / f'{stem}.png', alpha='keep', overwrite=True)
            project.export(directory / f'{stem}@2x.png', scale=2, alpha='keep', overwrite=True)
            entries.append(
                dict(
                    id=stem,
                    design=slug,
                    category='rules',
                    theme=theme,
                    title=slug.replace('-', ' ').title(),
                    width=canvas[0],
                    height=canvas[1],
                    transparent=True,
                    master=f'rules/{stem}.vixl',
                    recipe=f'rules/{stem}.recipe.json',
                    preview=f'rules/{stem}.png',
                    vector=f'rules/{stem}.svg',
                    exports=['svg', 'png', '@2x.png'],
                    checks=checks,
                    contract=contract,
                    use=use,
                    family='logo-ui',
                )
            )
    return entries


def scene_sheet(out, entries, tokens):
    colors = tokens['color']
    selected = [
        e for e in entries if e['category'] in ('illustrations', 'animations') and e['theme'] == 'light'
    ]
    cols, cw, ch = 4, 350, 268
    sheet = Image.new('RGB', (1480, 195 + math.ceil(len(selected) / cols) * ch), colors['paper'])
    draw = ImageDraw.Draw(sheet)
    display = ImageFont.truetype(str(ROOT / 'src/byax/resources/fonts/IM-Fell-English-400.ttf'), 43)
    label = ImageFont.truetype(str(ROOT / 'src/byax/resources/fonts/Manrope-400.ttf'), 16)
    wordmark = Image.open(ROOT / 'assets/logo-companion-library/reference/wordmark-light.png').convert('RGBA')
    wordmark.thumbnail((280, 85), Image.Resampling.LANCZOS)
    sheet.paste(wordmark, (40, 26), wordmark)
    draw.text((363, 33), 'Interface illustrations and motion', font=display, fill=colors['ink'])
    draw.text(
        (42, 95),
        f'{len(selected)} designs · Dialogs, empty states and task-owned loops · Made with Vixl',
        font=label,
        fill=colors['muted'],
    )
    draw.line((40, 141, 1440, 141), fill=colors['rule'], width=1)
    for index, entry in enumerate(selected):
        x, y = 40 + (index % cols) * cw, 173 + (index // cols) * ch
        with Image.open(out / entry['preview']) as image:
            preview = image.convert('RGBA')
        preview.thumbnail((cw - 30, 202), Image.Resampling.LANCZOS)
        sheet.paste(preview, (x + (cw - preview.width) // 2, y + (207 - preview.height) // 2), preview)
        title = entry['design'].replace('-', ' ').title() + (
            ' · loop' if entry['category'] == 'animations' else ''
        )
        draw.text((x + 10, y + 222), title, font=label, fill=colors['ink'])
    sheet.save(out / 'contact-sheet.png', optimize=True)


def checksums(out):
    # Icons ship without SVG (see build_icons), so a stray local export never enters the integrity list.
    write_json(
        out / 'checksums.json',
        {
            str(path.relative_to(out)): sha(path)
            for path in sorted(out.rglob('*'))
            if path.is_file()
            and path.name != 'checksums.json'
            and not (path.parent.name == 'icons' and path.suffix == '.svg')
        },
    )


def icon_sheet(out, entries, renders, tokens):
    """A labelled overview: large icons by group, with real-size 24 and 16 px renders beneath."""
    colors = tokens['color']
    selected = [e for e in entries if e['category'] == 'icons' and e['theme'] == 'light']
    groups = []
    for entry in selected:
        if entry['group'] not in groups:
            groups.append(entry['group'])
    titles = dict(
        format='Text',
        paragraph='Paragraph',
        file='File',
        edit='Edit',
        insert='Insert',
        table='Table',
        review='Review',
        view='View',
        tools='Tools',
    )
    cols, cell, margin = 10, 118, 44
    rows = sum(math.ceil(sum(1 for e in selected if e['group'] == g) / cols) for g in groups)
    height = 190 + rows * (cell + 56) + len(groups) * 54 + 60
    sheet = Image.new('RGB', (cols * cell + margin * 2, height), colors['paper'])
    draw = ImageDraw.Draw(sheet)
    display = ImageFont.truetype(str(ROOT / 'src/byax/resources/fonts/IM-Fell-English-400.ttf'), 43)
    label = ImageFont.truetype(str(ROOT / 'src/byax/resources/fonts/Manrope-400.ttf'), 14)
    small = ImageFont.truetype(str(ROOT / 'src/byax/resources/fonts/Manrope-400.ttf'), 12)
    wordmark = Image.open(ROOT / 'assets/logo-companion-library/reference/wordmark-light.png').convert('RGBA')
    wordmark.thumbnail((280, 85), Image.Resampling.LANCZOS)
    sheet.paste(wordmark, (margin, 26), wordmark)
    draw.text((margin + 322, 33), 'Interface icons', font=display, fill=colors['ink'])
    draw.text(
        (margin + 2, 100),
        f'{len(selected)} icons · Ink for the object, one green pen gesture for the action · Made with Vixl',
        font=label,
        fill=colors['muted'],
    )
    draw.line((margin, 146, sheet.width - margin, 146), fill=colors['rule'], width=1)
    y = 170
    for group in groups:
        draw.text((margin, y), titles[group].upper(), font=small, fill=colors['muted'])
        y += 28
        items = [e for e in selected if e['group'] == group]
        for index, entry in enumerate(items):
            x = margin + (index % cols) * cell
            row_y = y + (index // cols) * (cell + 56)
            with Image.open(out / entry['preview']) as image:
                big = image.convert('RGBA').resize((72, 72), Image.Resampling.LANCZOS)
            sheet.paste(big, (x + 8, row_y), big)
            for slot, size in enumerate((24, 16)):
                tiny = renders[entry['design']][size]
                sheet.paste(tiny, (x + 8 + slot * 34, row_y + 82), tiny)
            draw.text((x + 8, row_y + 112), entry['design'], font=small, fill=colors['ink'])
        y += math.ceil(len(items) / cols) * (cell + 56) + 26
    sheet.save(out / 'icons-contact-sheet.png', optimize=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--only', choices=('icons',), help='Build one category only')
    args = parser.parse_args()
    tokens = art.load_sources()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    entries, renders = build_icons(out, tokens)
    icon_sheet(out, entries, renders, tokens)
    if args.only != 'icons':
        entries += build_scenes(out)
        entries += build_rules(out, tokens)
        scene_sheet(out, entries, tokens)
    designs = sorted({(e['category'], e['design']) for e in entries})
    if args.only:
        # A partial run must not replace the full manifest or integrity list.
        print(json.dumps(dict(partial=args.only, designs=len(designs), variants=len(entries))))
        return
    write_json(
        out / 'manifest.json',
        dict(
            schema_version=1,
            name='ByAuthorX Logo UI Library',
            design_count=len(designs),
            variant_count=len(entries),
            engine=dict(name='Vixl', version=VIXL_VERSION),
            brand_tokens=tokens,
            reference_masters=[
                dict(name=name, source=relative, sha256=sha(ROOT / relative))
                for name, relative in companions.SOURCES.items()
                if (ROOT / relative).exists()
            ],
            assets=entries,
        ),
    )
    checksums(out)
    print(json.dumps(dict(designs=len(designs), variants=len(entries)), indent=2))


if __name__ == '__main__':
    main()
