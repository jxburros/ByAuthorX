"""Opt-in retrieval evaluation with a real embedding model (`pytest -m eval_models`).

Deselected by default (`addopts` in pyproject.toml). Point it at an embedding provider you configured:

    BYAX_EVAL_PROVIDER=<alias> BYAX_EVAL_DIMENSION=768 pytest -m eval_models -q

A local provider (Ollama on loopback) keeps the fixtures on this computer. A remote provider also needs
`BYAX_EVAL_UPLOAD=1`, which sends the (public-domain / CC BY) fixture passages to it. Nothing downloads a
model for you, and without the variables the test skips.
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
