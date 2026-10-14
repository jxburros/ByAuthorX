#!/usr/bin/env python3
"""Draw, compile and document the six ByAuthorX brand fonts.

Glyphs are drawn from parametric skeletons (scripts/brandfonts), compiled to TrueType with fontTools, kept as
editable vector masters in Vixl, and shown in Vixl specimen sheets that are checked before they are saved.
Use --from-masters to compile the fonts from the glyph masters instead, after editing them in Vixl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fontTools import version as fonttools_version  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402
from vixl import Project, __version__ as vixl_version  # noqa: E402

from brandfonts import compile as comp  # noqa: E402
from brandfonts import families, masters, specimens  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/brand-fonts'
APP_FONTS = ROOT / 'src/byax/resources/fonts'
LABELS = ROOT / 'src/byax/resources/fonts'
ROLES = {
    'irongall': 'Logo: a rough inked serif',
    'scholium': 'Illustrations: a hand-drawn pen face',
    'tenuis': 'Tall, thin and elegant',
    'brobdingnag': 'Big and bold',
    'onionskin': 'My pick: a typewriter',
    'decussate': 'Bonus: every letter is made of X marks',
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_entry(path, base):
    path = Path(path)
    return dict(path=path.relative_to(base).as_posix(), bytes=path.stat().st_size, sha256=sha256(path))


def font_features(path):
    font = TTFont(path)
    tags = set()
    for table in ('GSUB', 'GPOS'):
        if table in font:
            tags |= {record.FeatureTag for record in font[table].table.FeatureList.FeatureRecord}
    return sorted(tags)


def install(out):
    """Copy every built TTF listed in the manifest into the application's bundled fonts (and report how many)."""
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    count = 0
    for entry in manifest['families']:
        source = out / entry['files']['ttf']['path']
        shutil.copy2(source, APP_FONTS / source.name)
        count += 1
    return count


def apply_masters(shapes, sheet_path):
    """Replace drawn glyph outlines with the ones stored in a Vixl glyph sheet."""
    project = Project.load(sheet_path)
    edited = masters.read_paths(project)
    replaced = 0
    for name, item in shapes.items():
        if item.get('composite') or name not in edited:
            continue
        item['geom'] = edited[name]
        replaced += 1
    return replaced


def build_family(key, args, labels, out):
    family = families.get(key)
    shapes = family.shapes()
    sheet = out / 'masters' / f'{key}-glyphs.vixl'
    if args.from_masters:
        print(
            f'{key}: compiling from {sheet.name}: {apply_masters(shapes, sheet)} glyphs replaced', flush=True
        )
    compiled = comp.compile_family(family.spec, shapes, family.features)
    stem = comp.ps_name(family.spec)
    font_dir = out / 'fonts' / key
    font_dir.mkdir(parents=True, exist_ok=True)
    written = comp.save(compiled, font_dir / f'{stem}.ttf')
    entry = dict(
        key=key,
        family=family.spec.family,
        style=family.spec.style,
        postscript_name=stem,
        role=ROLES[key],
        weight=family.spec.weight,
        characters=len(compiled.cmap),
        glyphs=len(compiled.order),
        kerning_pairs=len(compiled.kerning),
        features=font_features(written[0]),
        files={Path(p).suffix[1:]: file_entry(p, out) for p in written},
    )
    print(f'{key}: {stem}.ttf, {entry["characters"]} characters, {entry["glyphs"]} glyphs', flush=True)
    if not args.skip_masters and not args.from_masters:
        project, report = masters.build_sheet(compiled, out, family.spec.metrics)
        (out / 'masters').mkdir(exist_ok=True)
        project.save(sheet, overwrite=True)
        entry['masters'] = dict(path=sheet.relative_to(out).as_posix(), **report)
        print(f'{key}: glyph sheet with {report["glyph_layers"]} path layers', flush=True)
    if not args.skip_specimens:
        spec_dir = out / 'specimens'
        spec_dir.mkdir(exist_ok=True)
        index = list(families.FAMILIES).index(key) + 1
        project, result = specimens.build_specimen(
            out, key, compiled, written[0], index, len(families.FAMILIES), labels, family.specimen
        )
        check = project.check()
        if not check['passed']:
            raise RuntimeError(f'{key} specimen failed its checks: {json.dumps(check.get("issues"))[:1500]}')
        project.save(spec_dir / f'{key}-specimen.vixl', overwrite=True)
        project.export(spec_dir / f'{key}-specimen.png', overwrite=True)
        entry['specimen'] = dict(
            vixl=f'specimens/{key}-specimen.vixl',
            png=f'specimens/{key}-specimen.png',
            check=dict(passed=check['passed'], by_action={k: len(v) for k, v in check['by_action'].items()}),
        )
    return entry, compiled, written[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--only', nargs='+', choices=list(families.FAMILIES), help='build only these families'
    )
    parser.add_argument('--out', type=Path, default=OUT, help='output directory (default assets/brand-fonts)')
    parser.add_argument('--from-masters', action='store_true', help='compile from the glyph masters in Vixl')
    parser.add_argument('--skip-masters', action='store_true')
    parser.add_argument('--skip-specimens', action='store_true')
    parser.add_argument('--no-install', action='store_true', help='do not copy the TTFs into the application')
    parser.add_argument(
        '--install-only', action='store_true', help='copy the built TTFs into the application and stop'
    )
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.install_only:
        print(f'installed {install(out)} fonts into {APP_FONTS.relative_to(ROOT)}')
        return
    labels = {'manrope': LABELS / 'Manrope-400.ttf', 'manrope-semibold': LABELS / 'Manrope-600.ttf'}
    keys = args.only or list(families.FAMILIES)
    entries, compiled_all, paths = [], {}, {}
    for key in keys:
        entry, compiled, path = build_family(key, args, labels, out)
        entries.append(entry)
        compiled_all[key], paths[key] = compiled, path
    manifest_path = out / 'manifest.json'
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() and args.only else {}
    merged = {e['key']: e for e in previous.get('families', [])}
    merged.update({e['key']: e for e in entries})
    ordered = [merged[k] for k in families.FAMILIES if k in merged]
    if not args.skip_specimens and len(keys) == len(families.FAMILIES):
        items = [(k, compiled_all[k], ROLES[k]) for k in keys]
        project, _ = specimens.overview(out, items, labels, paths)
        check = project.check()
        if not check['passed']:
            raise RuntimeError(f'overview failed its checks: {json.dumps(check.get("issues"))[:1500]}')
        project.save(out / 'overview.vixl', overwrite=True)
        project.export(out / 'overview.png', overwrite=True)
        print('overview.png', flush=True)
    manifest = dict(
        vixl=vixl_version,
        fonttools=fonttools_version,
        license='MIT (see the repository LICENSE)',
        families=ordered,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {manifest_path}')
    if out == OUT and not args.no_install:
        print(f'installed {install(out)} fonts into {APP_FONTS.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
