#!/usr/bin/env python3
"""Build (once) and keep the synthetic scale index so latency work can re-run queries without re-parsing.

    uv run python scripts/build_scale_index.py artifacts/scale-work --documents 10000

Writes the generated corpus, its golden queries and the indexed library under the given folder (all
git-ignored under artifacts/). `scripts/profile_search.py` and `scripts/eval_retrieval.py --scale-work`
reuse it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import generate_fixtures  # noqa: E402

from byax import evaluation as ev  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    parser.add_argument('--documents', type=int, default=10_000)
    parser.add_argument('--queries', type=int, default=50)
    args = parser.parse_args(argv)
    source = args.work / 'scale-source'
    info = generate_fixtures.make_eval_scale(source, args.documents, queries=args.queries)
    started = time.perf_counter()
    corpus = ev.build_corpus('scale', source / 'corpus', args.work, limits={'chunks': 2_000_000})
    info.update(build_seconds=round(time.perf_counter() - started, 1), chunks=corpus.refresh['chunks'],
                library=corpus.library, cache=str(corpus.cache_dir), root=str(corpus.root))  # fmt: skip
    (args.work / 'scale-info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in info.items() if k != 'library'}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
