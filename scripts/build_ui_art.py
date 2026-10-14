"""Bake selected Vixl assets for native Qt; run in the separate authoring environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / 'assets/logo-companion-library'
UI_LIBRARY = ROOT / 'assets/logo-ui-library'
OUTPUT = ROOT / 'src/byax/resources/brand/ui'


# Inspector empty states and waiting loops taken from the logo companion shelf.
ILLUSTRATIONS = {
    'ai-empty': 'idea-on-paper',
    'ai-setup': 'local-copy',
    'spelling-clean': 'signature-manuscript',
    'layers-empty': 'revision-pages',
    'outline-empty': 'loop-bound-book',
}
LOOPS = {'ai-thinking': 'ink-blot-loader', 'spelling-review': 'margin-review'}
THEMES = ('light', 'dark')
# The logo UI library: dialog illustrations, task-owned loops, hand-inked chrome rules and the icon atlas.
UI_ILLUSTRATIONS = (
    'checks-clear',
    'proofread-marks',
    'jobs-empty',
    'dictation-ready',
    'export-ready',
    'provider-keys',
)
UI_LOOPS = ('listening-wave', 'checking-page')
UI_RULES = ('rule-h', 'rule-v', 'tab-underline')


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bake(name, design, *, loop=False, isolate=()):
    from vixl import Project
    from vixl.timeline import project_at

    source = LIBRARY / 'animations' / f'{design}-light.vixl'
    project = Project.load(source, workspace=ROOT)
    hidden = []
    if isolate:
        keep = {project.layer(ref)['id'] for ref in isolate}
        for layer in project.state['layers']:
            if layer.get('parent') in keep:
                keep.add(layer['id'])
        hidden = [layer['name'] for layer in project.state['layers'] if layer['id'] not in keep]
        project.apply({'type': 'hide', 'targets': hidden})
    duration = project.state['timeline']['duration']
    fps = 24 if loop else 60
    times = sorted({*[round(i * 1000 / fps) for i in range(math.ceil(duration * fps / 1000))], duration})
    frames = [project_at(project, time).render().convert('RGBA') for time in times]
    boxes = [frame.getbbox() for frame in frames if frame.getbbox()]
    canvas = project.state['canvas']
    crop = (
        max(0, min(b[0] for b in boxes) - 6),
        max(0, min(b[1] for b in boxes) - 6),
        min(canvas['width'], max(b[2] for b in boxes) + 6),
        min(canvas['height'], max(b[3] for b in boxes) + 6),
    )
    frames = [frame.crop(crop) for frame in frames]
    width, height = frames[0].size
    sprite = Image.new('RGBA', (width * len(frames), height))
    for index, frame in enumerate(frames):
        sprite.paste(frame, (index * width, 0))
    sprite.save(OUTPUT / f'{name}-sprite.png')
    poster_time = 1200 if loop else duration
    project_at(project, poster_time).render().crop(crop).save(OUTPUT / f'{name}-poster.png')
    checks = project.check(checks=['bounds', 'motion'])
    if not checks['passed']:
        raise RuntimeError(json.dumps(checks))
    manifest = {
        'generator': 'Vixl 0.26.0',
        'source': str(source.relative_to(ROOT)),
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'durationMs': duration,
        'frameWidth': width,
        'frameHeight': height,
        'frameTimesMs': times,
        'loop': loop,
        'posterTimeMs': poster_time,
        'crop': list(crop),
        'hiddenLayers': hidden,
        'checks': checks,
    }
    (OUTPUT / f'{name}.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'{name}: {len(frames)} transparent native frames, {width} × {height}')


def bake_companion_loop(name, design, theme, library=LIBRARY):
    """Unpack the delivered transparent APNG exactly; no Vixl runtime is required."""
    variant = f'{design}-{theme}'
    shelf = json.loads((library / 'manifest.json').read_text(encoding='utf-8'))
    entry = next(item for item in shelf['assets'] if item['id'] == variant)
    engine = shelf['engine']
    source = library / entry['apng']
    poster_source = library / entry['reduced_motion']
    frames, times, elapsed = [], [], 0
    with Image.open(source) as animation:
        for index in range(animation.n_frames):
            animation.seek(index)
            frames.append(animation.convert('RGBA'))
            times.append(elapsed)
            elapsed += round(animation.info['duration'])
    duration = elapsed
    if duration != entry['duration_ms']:
        raise RuntimeError(f'{variant}: APNG timing does not match the manifest')
    # The loop boundary is invisible: close the cycle on its opening (erased) pose.
    frames.append(frames[0])
    times.append(duration)
    with Image.open(poster_source) as still:
        poster = still.convert('RGBA')
    boxes = [image.getbbox() for image in (*frames, poster) if image.getbbox()]
    width, height = frames[0].size
    crop = (
        max(0, min(b[0] for b in boxes) - 6),
        max(0, min(b[1] for b in boxes) - 6),
        min(width, max(b[2] for b in boxes) + 6),
        min(height, max(b[3] for b in boxes) + 6),
    )
    frames = [frame.crop(crop) for frame in frames]
    width, height = frames[0].size
    sprite = Image.new('RGBA', (width * len(frames), height))
    for index, frame in enumerate(frames):
        sprite.paste(frame, (index * width, 0))
    stem = f'{name}-{theme}'
    sprite.save(OUTPUT / f'{stem}-sprite.png', optimize=True)
    poster.crop(crop).save(OUTPUT / f'{stem}-poster.png', optimize=True)
    manifest = {
        'generator': f'{engine["name"]} {engine["version"]} (delivered APNG frames, unpacked without resampling)',
        'source': str(source.relative_to(ROOT)),
        'source_sha256': _sha(source),
        'poster_source': str(poster_source.relative_to(ROOT)),
        'poster_source_sha256': _sha(poster_source),
        'durationMs': duration,
        'frameWidth': width,
        'frameHeight': height,
        'frameTimesMs': times,
        'loop': True,
        'posterTimeMs': entry.get('poster_time_ms'),
        'crop': list(crop),
        'theme': theme,
        'checks': entry['checks'],
    }
    (OUTPUT / f'{stem}.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'{stem}: {len(frames)} transparent native frames, {width} × {height}')


def _copy(source, target, provenance):
    shutil.copy2(source, target)
    provenance[target.name] = {'source': str(source.relative_to(ROOT)), 'source_sha256': _sha(source)}


def ui_library(provenance):
    """Copy the logo UI library's runtime pieces; pure file operations, no Vixl needed."""
    for theme in THEMES:
        for name in UI_ILLUSTRATIONS:
            _copy(
                UI_LIBRARY / 'illustrations' / f'{name}-{theme}.png',
                OUTPUT / f'{name}-{theme}.png',
                provenance,
            )
        for name in UI_LOOPS:
            bake_companion_loop(name, name, theme, UI_LIBRARY)
        for name in UI_RULES:
            for suffix in ('', '@2x'):
                _copy(
                    UI_LIBRARY / 'rules' / f'{name}-{theme}{suffix}.png',
                    OUTPUT / f'{name}-{theme}{suffix}.png',
                    provenance,
                )
    atlas = json.loads((UI_LIBRARY / 'icons/atlas.json').read_text(encoding='utf-8'))
    for key, filename in atlas['files'].items():
        theme, size = key.split('-')
        _copy(UI_LIBRARY / 'icons' / filename, OUTPUT / f'icons-{theme}-{size}.png', provenance)
    atlas['files'] = {key: f'icons-{key}.png' for key in atlas['files']}
    atlas['source'] = 'assets/logo-ui-library/icons/atlas.json'
    (OUTPUT / 'icons.json').write_text(json.dumps(atlas, indent=2) + '\n', encoding='utf-8')
    provenance['icons.json'] = {
        'source': 'assets/logo-ui-library/icons/atlas.json',
        'source_sha256': _sha(UI_LIBRARY / 'icons/atlas.json'),
    }


def companions():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    provenance = json.loads((OUTPUT / 'provenance.json').read_text(encoding='utf-8'))
    for name, design in LOOPS.items():
        for theme in THEMES:
            bake_companion_loop(name, design, theme)
    for name, design in ILLUSTRATIONS.items():
        for theme in THEMES:
            source = LIBRARY / 'illustrations' / f'{design}-{theme}.png'
            shutil.copy2(source, OUTPUT / f'{name}-{theme}.png')
            provenance[f'{name}-{theme}.png'] = {
                'source': str(source.relative_to(ROOT)),
                'source_sha256': _sha(source),
            }
    ui_library(provenance)
    (OUTPUT / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--companions-only',
        action='store_true',
        help='Refresh only inspector loops and empty states (Pillow only; no Vixl needed)',
    )
    if parser.parse_args().companions_only:
        companions()
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bake('busy-write', 'pen-loop-wait', loop=True)
    bake('save-check', 'saved-ink', isolate=('save-check', 'saved-ink'))
    bake('about-write', 'signature-settle')
    source = LIBRARY / 'illustrations/margin-notes-light.png'
    shutil.copy2(source, OUTPUT / 'review-empty.png')
    (OUTPUT / 'provenance.json').write_text(
        json.dumps(
            {
                'review-empty.png': {
                    'source': str(source.relative_to(ROOT)),
                    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                },
                'note': 'Made with Vixl. Native playback uses only committed PNG/JSON files.',
            },
            indent=2,
        )
        + '\n',
        encoding='utf-8',
    )
    companions()


if __name__ == '__main__':
    main()
