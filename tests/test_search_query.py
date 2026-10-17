"""Search syntax parsing, safe FTS5 compilation, stopwords and typo suggestions."""

import random
import sqlite3

import pytest

from byax.search_query import COLUMN_WEIGHTS, key_terms, parse, suggest


def test_terms_phrases_fields_near_negation_and_prefix():
    query = parse('copper "silver moon" heading:Rosings NEAR(Elizabeth Darcy, 5) -Wickham run* title:notes')
    kinds = [(c.kind, c.text(), c.column, c.negated, c.prefix) for c in query.clauses]
    assert kinds == [
        ('term', 'copper', None, False, False),
        ('phrase', 'silver moon', None, False, False),
        ('term', 'Rosings', 'heading', False, False),
        ('near', 'Elizabeth Darcy', None, False, False),
        ('term', 'Wickham', None, True, False),
        ('term', 'run', None, False, True),
    ]
    assert query.clauses[3].distance == 5
    assert query.filters == (('title', 'notes'),)
    assert query.fts() == (
        '("copper" OR "silver moon" OR heading : "Rosings" OR NEAR("Elizabeth" "Darcy", 5) OR "run"*) '
        'NOT ("Wickham")'
    )
    assert query.to_dict()['filters'] == [{'field': 'title', 'value': 'notes'}]


def test_stopwords_are_dropped_only_when_other_terms_remain():
    query = parse('the copper of the moon')
    assert [c.text() for c in query.clauses] == ['copper', 'moon']
    assert query.dropped_stopwords == ('the', 'of', 'the')
    only = parse('to be or not')
    # Lower-case words are words; only upper-case AND/OR/NOT act as connectors.
    assert [c.text() for c in only.clauses] == ['to', 'be', 'or', 'not'] and not only.dropped_stopwords
    # A quoted stopword is a phrase the writer asked for.
    assert [c.text() for c in parse('"the" moon').clauses] == ['the', 'moon']


def test_negation_only_and_filter_only_queries_match_nothing_with_a_warning():
    assert parse('-moon').fts() is None
    assert 'Negated terms need at least one positive term' in parse('-moon').warnings
    assert parse('path:notes/*').fts() is None
    assert parse('path:notes/*').warnings


def test_trigram_expression_uses_literal_substrings_of_three_or_more_characters():
    assert parse('opper "copper moon" ab').trigram() == '"opper" OR "copper moon"'
    assert parse('NEAR(copper moon)').trigram() == '("copper" AND "moon")'
    assert parse('heading:Rosings').trigram() is None
    assert parse("Darcy's -xyz").trigram() == '("Darcy\'s") NOT ("xyz")'


def test_post_filters_and_dense_exclusion():
    query = parse('moon path:notes/*.md title:journal -jupiter')
    assert query.accepts({'paths': ['notes/journal.md']})
    assert not query.accepts({'paths': ['drafts/journal.md']})
    assert not query.accepts({'paths': ['notes/other.md']})
    assert query.excludes('The moons of Jupiter')
    assert not query.excludes('A copper moon')
    assert parse('moon -jup*').excludes('Jupiter rises')


def test_semantic_text_strips_syntax_only_when_present():
    assert parse('what does the copper moon mean?').semantic_text() == 'what does the copper moon mean?'
    assert parse('copper -moon heading:sky').semantic_text() == 'copper sky'


HOSTILE = [
    '"',
    '""',
    '"unterminated phrase',
    'NEAR(',
    'NEAR(a',
    'NEAR(a b, x)',
    'NEAR(a b, 99999)',
    'a OR',
    'OR AND NOT',
    'chunk_id:secret',
    'text:"x" OR chunk_id : "y"',
    '{heading text}: moon',
    'moon) OR (1=1',
    "'; DROP TABLE chunks; --",
    'bm25(search)',
    'col*:moon ^moon',
    '***',
    '- - -',
    'a:b:c:d',
    'heading:',
    'heading:"',
    '\x00\x01 moon',
    'móon ÉLAN naïve 東京',
    'x' * 5000,
    ' '.join(['w'] * 200),
]


def _fts():
    db = sqlite3.connect(':memory:')
    db.execute(
        'CREATE VIRTUAL TABLE search USING fts5(chunk_id UNINDEXED, heading, text, '
        'tokenize="porter unicode61 remove_diacritics 2", prefix=\'2 3\')'
    )
    db.execute("CREATE VIRTUAL TABLE search_tri USING fts5(chunk_id UNINDEXED, text, tokenize='trigram')")
    db.execute("INSERT INTO search VALUES ('secret', 'Sky', 'A copper moon over Tokyo 東京')")
    db.execute("INSERT INTO search_tri VALUES ('secret', 'A copper moon over Tokyo 東京')")
    return db


@pytest.mark.parametrize('text', HOSTILE)
def test_hostile_input_always_compiles_to_a_valid_quoted_expression(text):
    db = _fts()
    query = parse(text)
    for table, expression in (('search', query.fts()), ('search_tri', query.trigram())):
        if expression is None:
            continue
        weights = ','.join(map(str, COLUMN_WEIGHTS)) if table == 'search' else '0.0,1.0'
        db.execute(
            f'SELECT bm25({table},{weights}) FROM {table} WHERE {table} MATCH ?', (expression,)
        ).fetchall()
    # The unindexed identifier column is never addressable from user syntax.
    assert 'chunk_id :' not in (query.fts() or '')


def test_random_input_never_breaks_compilation():
    rng = random.Random(1342)
    alphabet = 'ab moon"():-*^{},.NEARORNOT\'\\ \t'
    db = _fts()
    for _ in range(400):
        text = ''.join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
        query = parse(text)
        if query.fts():
            db.execute('SELECT * FROM search WHERE search MATCH ?', (query.fts(),)).fetchall()
        if query.trigram():
            db.execute('SELECT * FROM search_tri WHERE search_tri MATCH ?', (query.trigram(),)).fetchall()


def test_unknown_fields_are_searched_as_words():
    query = parse('chunk_id:secret')
    assert [c.text() for c in query.clauses] == ['chunk_id secret']
    assert query.clauses[0].column is None
    assert any('Unknown field' in w for w in query.warnings)


def test_rapidfuzz_suggestions_rank_by_similarity_then_document_frequency():
    vocabulary = [('elizabeth', 40), ('elisabeth', 2), ('pemberlei', 9), ('moon', 3)]
    assert [s['term'] for s in suggest('elizabth', vocabulary)] == ['elizabeth', 'elisabeth']
    assert suggest('zz', vocabulary) == []
    assert suggest('moon', vocabulary) == []


def test_expansion_is_explicit_and_reported():
    query = parse('elizabth moon')
    expanded = query.expand({'elizabth': [{'term': 'elizabeth', 'score': 94.1, 'documents': 1}]})
    assert query.fts() == '"elizabth" OR "moon"'
    assert expanded.fts() == '"elizabth" OR "moon" OR "elizabeth"'
    assert expanded.to_dict()['expanded'] == {'elizabth': ['elizabeth']}


def test_key_terms_prefer_quoted_phrases_and_names():
    assert key_terms('Does "Lady Catherine" ever visit Pemberley?') == ['Lady Catherine', 'Pemberley']
    assert key_terms('is the copper lantern mentioned anywhere?') == ['copper', 'lantern']
