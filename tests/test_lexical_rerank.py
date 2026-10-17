"""The lexical re-rank of the best BM25 candidates (`byax.lexical_rerank`, applied by `LibraryIndex.search`)."""

import pytest

from byax import lexical_rerank as rerank
from byax.libraries import LibraryIndex, LibraryRegistry


def candidates(*passages, scores=None):
    scores = scores or [-5.0 + 0.1 * n for n in range(len(passages))]
    return [(score, '', text) for score, text in zip(scores, passages, strict=True)]


def test_the_literal_word_outranks_stemmed_variants_with_similar_scores():
    ranked = candidates(
        'They were respectively delighted by the result.',
        'A respective share was set aside for each.',
        'He was respected by everyone in the town.',
    )
    assert rerank.order('respected', ranked)[0] == 2


def test_a_passage_that_quotes_the_query_outranks_one_with_the_same_words_scattered():
    ranked = candidates(
        'The river was wide and the bridge was old, but the mill stood far away from them.',
        'Beyond the bridge, the old mill stood by the wide river in the evening light.',
        scores=[-5.2, -5.0],
    )
    assert rerank.order('the old mill stood by the wide river', ranked) == [1, 0]


def test_a_much_better_bm25_score_is_not_overturned_by_one_extra_word():
    ranked = [(-12.0, '', 'lanterns lanterns lanterns hung in the harbour'), (-3.0, '', 'a lantern')]
    assert rerank.order('lantern harbour', ranked) == [0, 1]


def test_a_stemmed_heading_match_counts_as_exact():
    ranked = [(-5.0, 'Lanterns', 'The hall was dark.'), (-4.9, '', 'She lit a lantern by the stove.')]
    assert rerank.order('lantern', ranked) == [0, 1]


def test_ties_keep_bm25_order_and_degenerate_inputs_are_left_alone():
    same = candidates('alpha beta', 'alpha beta', scores=[-1.0, -1.0])
    assert rerank.order('alpha beta', same) == [0, 1]
    assert rerank.order('alpha', same[:1]) == [0]
    assert rerank.order('', same) == [0, 1]
    assert rerank.order('title:notes', same) == [0, 1]


def test_accents_and_case_are_ignored_in_exact_matches():
    ranked = candidates('The Cafés were closed.', 'The cafe was open.', scores=[-5.0, -5.0])
    assert rerank.order('CAFE', ranked)[0] == 1


@pytest.fixture
def make_library(tmp_path):
    registry = LibraryRegistry(tmp_path / 'config')

    def make(name, files):
        root = tmp_path / name
        root.mkdir()
        for relative, text in files.items():
            (root / relative).write_text(text, encoding='utf-8')
        library = registry.create(name, [str(root)])
        index = LibraryIndex(library, cache_dir=tmp_path / 'cache')
        index.refresh()
        return library, index, root

    return make


def test_search_puts_the_literal_word_first_and_keeps_pagination_consistent(make_library):
    files = {f'{n}.txt': f'Chapter {n}. The respective share of ledger {n} was small.' for n in range(30)}
    files['literal.txt'] = 'He was respected in the town, and the ledger said so.'
    _, index, _ = make_library('Words', files)
    first = index.search('respected', limit=5)
    assert first['results'][0]['path'] == 'literal.txt'
    pages = [
        h['chunk_id']
        for offset in (0, 5, 10)
        for h in index.search('respected', limit=5, offset=offset)['results']
    ]
    assert pages == [h['chunk_id'] for h in index.search('respected', limit=15)['results']]
    assert len(set(pages)) == len(pages)
