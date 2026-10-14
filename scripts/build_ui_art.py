"""Bake selected Vixl assets for native Qt; run in the separate authoring environment."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

from PIL import Image
from vixl import Project
from vixl.timeline import project_at

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / 'assets/logo-companion-library'
OUTPUT = ROOT / 'src/byax/resources/brand/ui'


def bake(name, design, *, loop=False, isolate=()):
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


def main():
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


if __name__ == '__main__':
    main()
