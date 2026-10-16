"""Regenerate the interface-string catalogue (src/byax/resources/catalog/strings.json).

    uv run python scripts/extract_strings.py           # check the catalogue is current
    uv run python scripts/extract_strings.py --write   # refresh it

The catalogue lists every literal text passed to ``tr()`` in the GUI modules. Translation coverage in
``docs/translations.md`` and ``byax languages --action coverage`` counts against it. Calls that pass a
variable are counted (``dynamic_call_sites``) but cannot be listed.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
TARGET = ROOT / 'src' / 'byax' / 'resources' / 'catalog' / 'strings.json'


def build():
    from byax.language_packs import extract_sources

    return extract_sources(ROOT / 'src' / 'byax')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    catalog = build()
    text = json.dumps(catalog, ensure_ascii=False, indent=1) + '\n'
    if args.write:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(text, encoding='utf-8')
    elif not TARGET.exists() or TARGET.read_text(encoding='utf-8') != text:
        print('The catalogue is out of date; run with --write.')
        return 1
    print(f'{len(catalog["strings"])} texts, {catalog["dynamic_call_sites"]} dynamic call sites')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
