"""Run the real Docling engine on one PDF and print what the library parser would index.

    pip install "byauthorx[docling]"
    byax models --action download --name docling-layout-heron --confirm
    python scripts/check_docling_pdf.py book.pdf

The automated tests use a stand-in ``docling`` package, so run this after upgrading Docling to see that
passages still come back with a page, a top-left bounding box and a heading path. It runs offline on the
installed layout model (``HF_HUB_OFFLINE=1``), exactly as the parser subprocess does.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from byax import library_parser  # noqa: E402


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    start = time.perf_counter()
    passages = library_parser.extract(sys.argv[1], 2_000_000, 'docling')
    print(json.dumps({'seconds': round(time.perf_counter() - start, 1), 'passages': passages}, indent=2))
    problems = [p['locator'] for p in passages if 'page' not in p['locator'] or 'bbox' not in p['locator']]
    if problems and not all('note' in locator for locator in problems):
        raise SystemExit(f'{len(problems)} passages lack page or bbox provenance')


if __name__ == '__main__':
    main()
