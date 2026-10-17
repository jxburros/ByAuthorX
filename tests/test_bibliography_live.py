"""Live bibliography checks: real requests to the public registries. Deselected by default.

Run with ``uv run python -m pytest -m live tests/test_bibliography_live.py``. These exist to notice
when a registry changes the shape of its answers, which the recorded fixtures cannot; CI replays
recordings instead (see ``tests/bibliography_fixtures.py``).
"""

from datetime import date

import pytest

from byax import bibliography
from byax.citation_lookup import Lookup
from byax.model import new_document

pytestmark = pytest.mark.live


def test_live_registries_still_answer_in_the_shapes_the_checks_read(tmp_path):
    doc = new_document(text='Live check')
    doc['references'] = {
        'nature': {
            'id': 'nature',
            'csl': {
                'id': 'nature',
                'type': 'article-journal',
                'title': 'Deep learning',
                'author': [{'family': 'LeCun'}, {'family': 'Bengio'}, {'family': 'Hinton'}],
                'issued': {'date-parts': [[2015]]},
                'DOI': '10.1038/nature14539',
            },
        },
        'lancet': {
            'id': 'lancet',
            'csl': {
                'id': 'lancet',
                'type': 'article-journal',
                'title': 'Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children',
                'author': [{'family': 'Wakefield'}],
                'issued': {'date-parts': [[1998]]},
                'DOI': '10.1016/S0140-6736(97)11096-0',
            },
        },
    }
    lookup = Lookup(authorized=True, cache=tmp_path / 'cache')
    report = bibliography.health(
        doc, lookup=lookup, checks=['doi', 'metadata', 'retractions'], today=date.today()
    )
    assert report['sources'][0]['metadata']['fields']['title']['status'] == 'match'
    assert report['summary']['retracted'] == 1
