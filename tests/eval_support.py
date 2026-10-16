"""Shared, lazily built evaluation corpora for the retrieval-quality and fidelity tests.

Building an index costs seconds, so each sampled fixture library is built once per test process in a
temporary folder that is removed at exit. Nothing here touches the network.
"""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
from functools import cache
from pathlib import Path

from byax import evaluation as ev

_WORK: Path | None = None


def workdir() -> Path:
    global _WORK
    if _WORK is None:
        _WORK = Path(tempfile.mkdtemp(prefix='byax-eval-test-'))
        atexit.register(shutil.rmtree, _WORK, ignore_errors=True)
    return _WORK


@cache
def sample_corpus(library: str, config: str = 'keyword') -> ev.Corpus:
    """The pytest subset of a fixture library, indexed (keyword index; the config only picks the subset)."""
    spec = ev.resolve_config(config).samples.get(library, ev.SAMPLES.get(library, {}))
    return ev.build_fixture_corpus(library, workdir() / config, sample=spec)


@cache
def queries_for(library: str, spec_key: str) -> tuple[dict, ...]:
    spec = json.loads(spec_key)
    return tuple(ev.covered_queries(ev.load_queries(library), ev.sample_extents(library, spec=spec)))


def sample_queries(library: str, config: str = 'keyword') -> list[dict]:
    """Golden queries whose labels all lie inside the sampled corpus (read-only: the list is cached)."""
    spec = ev.resolve_config(config).samples.get(library, ev.SAMPLES.get(library, {}))
    return list(queries_for(library, json.dumps(spec, sort_keys=True)))


def small_queries(library: str, spec: dict) -> list[dict]:
    return list(queries_for(library, json.dumps(spec, sort_keys=True)))
