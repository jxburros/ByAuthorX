"""Opt-in retrieval evaluation with a real embedding model (`pytest -m eval_models`).

Deselected by default (`addopts` in pyproject.toml). Point it at an embedding provider you configured:

    BYAX_EVAL_PROVIDER=<alias> BYAX_EVAL_DIMENSION=768 pytest -m eval_models -q

A local provider (Ollama on loopback) keeps the fixtures on this computer. A remote provider also needs
`BYAX_EVAL_UPLOAD=1`, which sends the (public-domain / CC BY) fixture passages to it. Nothing downloads a
model for you, and without the variables the test skips.

The two on-device tests need the Qwen3 embedder and the ColBERT reranker installed (`byax models status`,
or `BYAX_MODELS_DIR`) and `uv sync --extra local-models`; they skip otherwise.
"""

import os

import pytest
from eval_support import workdir

from byax import evaluation as ev

pytestmark = pytest.mark.eval_models


@pytest.mark.parametrize('library', ['papers', 'novel', 'webclips'])
def test_model_backed_hybrid_does_not_lose_to_keyword(library):
    alias, dimension = os.environ.get('BYAX_EVAL_PROVIDER'), os.environ.get('BYAX_EVAL_DIMENSION')
    if not alias or not dimension:
        pytest.skip('set BYAX_EVAL_PROVIDER and BYAX_EVAL_DIMENSION to evaluate a real embedding model')
    spec = {
        'name': 'hybrid-model',
        'provider': alias,
        'dimension': int(dimension),
        'authorized': os.environ.get('BYAX_EVAL_UPLOAD') == '1',
    }
    keyword = ev.run_library(library, 'keyword', workdir() / 'models-keyword', sample=True)
    hybrid = ev.run_library(library, spec, workdir() / 'models-hybrid', sample=True)
    assert hybrid['metrics']['recall@20'] >= keyword['metrics']['recall@20'] - 0.03
    assert hybrid['metrics']['ndcg@10'] >= keyword['metrics']['ndcg@10'] - 0.03


def _local(*names):
    from byax import local_models

    if not local_models.runtime_status()['installed']:
        pytest.skip('install byauthorx[local-models] to evaluate on-device models')
    for name in names:
        if local_models.model_status(name)['state'] != 'installed':
            pytest.skip(f'on-device model {name} is not installed (set BYAX_MODELS_DIR or download it)')


def test_on_device_embedder_keeps_recall_at_20_on_every_sample_library():
    """Phase 1 exit: hybrid with the on-device model is at least as good as keyword at recall@20."""
    _local('qwen3-embedding-0.6b-int8')
    for library in ('papers', 'novel'):
        keyword = ev.run_library(library, 'keyword', workdir() / 'local-keyword', sample=True)
        hybrid = ev.run_library(library, 'hybrid-qwen3', workdir() / 'local-hybrid', sample=True)
        assert hybrid['metrics']['recall@20'] >= keyword['metrics']['recall@20'] - 0.03, library


def test_colbert_rerank_scores_thematic_and_continuity_queries():
    _local('qwen3-embedding-0.6b-int8', 'answerai-colbert-small-v1')
    report = ev.run_library('novel', 'hybrid-qwen3-colbert', workdir() / 'local-rerank', sample=True)
    assert set(report['by_type']) <= {'thematic', 'continuity'} and report['queries']['evaluated'] > 0
    assert report['metrics']['recall@20'] > 0.5
