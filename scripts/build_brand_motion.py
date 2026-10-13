"""Author motion with installed Vixl, then bake portable frames for native Qt playback.

Run: uv run python scripts/build_brand_motion.py
Install authoring tool: uv tool install https://github.com/jxburros/Vixl/archive/refs/tags/v0.26.0.tar.gz
"""
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'design' / 'brand' / 'kit' / 'graphics' / 'pen-line.vixl'
MOTION = ROOT / 'design' / 'brand' / 'motion'
RUNTIME = ROOT / 'src' / 'byax' / 'resources' / 'brand'


def run(*args):
    result = subprocess.run(['vixl', *map(str, args), '--json'], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def main():
    if shutil.which('vixl') is None:
        raise SystemExit('Install Vixl 0.26.0 first; see docs/brand.md.')
    version = subprocess.run(['vixl', '--version'], check=True, capture_output=True, text=True).stdout.strip()
    if version != '0.26.0':
        raise SystemExit(f'This recipe is verified with Vixl 0.26.0; installed version is {version}.')
    tokens = json.loads((RUNTIME / 'tokens.json').read_text(encoding='utf-8'))
    duration = tokens['motion']['durationMs']
    MOTION.mkdir(parents=True, exist_ok=True)
    master = MOTION / 'pen-settle.vixl'
    recipe = MOTION / 'pen-settle.operations.json'
    operations = json.loads(recipe.read_text(encoding='utf-8'))
    if any(op.get('duration', duration) != duration for op in operations):
        raise SystemExit('Keep recipe durations aligned with the canonical motion token.')
    shutil.copy2(SOURCE, master)
    run('-p', master, 'apply', recipe)
    checks = run('-p', master, 'check', '--checks', 'bounds', 'motion')
    (MOTION / 'checks.json').write_text(json.dumps(checks, indent=2) + '\n', encoding='utf-8')
    if not checks.get('passed'):
        raise SystemExit('Vixl bounds/motion checks failed; inspect design/brand/motion/checks.json.')
    sheet = MOTION / 'contact-sheet.png'
    sheet.unlink(missing_ok=True)
    run('-p', master, 'timeline-sheet', '--out', sheet, '--times', '0ms', '90ms', f'{duration}ms',
        '--thumbnail', '360')
    preview = ROOT / 'docs' / 'images' / 'pen-settle.gif'
    run('-p', master, 'export-timeline', '--out', preview, '--fps', '60', '--scale', '0.53',
        '--background', tokens['color']['paper'], '--dither', 'none', '--overwrite')
    # Include the exact resting endpoint as well as the 60 Hz samples.
    times = sorted({*range(0, duration, 17), duration})
    with tempfile.TemporaryDirectory(prefix='byax-vixl-frames-') as directory:
        frames = []
        for index, time in enumerate(times):
            path = Path(directory) / f'{index}.png'
            run('-p', master, 'render', '--time', f'{time}ms', '--out', path, '--no-check')
            with Image.open(path) as image:
                frames.append(image.convert('RGBA'))
        width, height = frames[0].size
        sprite = Image.new('RGBA', (width * len(frames), height))
        for index, frame in enumerate(frames):
            sprite.paste(frame, (width * index, 0))
        sprite.save(RUNTIME / 'pen-settle.png')
        frames[-1].save(RUNTIME / 'pen-settle-rest.png')
    manifest = {
        'generator': f'Vixl {version}', 'source': 'design/brand/motion/pen-settle.vixl',
        'source_sha256': hashlib.sha256(master.read_bytes()).hexdigest(),
        'durationMs': duration, 'frameWidth': width, 'frameHeight': height,
        'frameTimesMs': times, 'loop': False, 'reducedMotion': 'pen-settle-rest.png',
    }
    (RUNTIME / 'pen-settle.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'Vixl master, checked preview, and {len(times)} native frames written.')


if __name__ == '__main__':
    main()
