"""Lexical tie-breaks for the best BM25 candidates of a keyword search.

The porter index stems every word, so a search for "respected" ranks "respect", "respective" and
"respectively" alongside it, and BM25 over merged chunks of about 400 tokens cannot tell a passage that
contains the query's own words, in order, from one that only shares stems and a title. This re-orders the
top ``RERANK_WINDOW`` candidates by three signals that need no new index:

* BM25, normalised to the best candidate (what the index already knows);
* the share of the query's words that occur *unstemmed* in the passage, each weighted by how rare it is
  among the candidates (``EXACT_WEIGHT``);
* the share of the query's consecutive word pairs (not two function words in a row) that occur
  consecutively in the passage, which favours
  the passage that quotes the query over one that merely contains the same words (``PHRASE_WEIGHT``).

The weights were chosen on the golden queries of ``byax.evaluation`` (see docs/benchmarks/context-engine.md);
ties keep BM25 order, so the result is deterministic. Nothing past the window moves.
"""

from __future__ import annotations

import itertools
import math
import re
import unicodedata

from .search_query import STOPWORDS

RERANK_WINDOW = 100
EXACT_WEIGHT = 1.5
PHRASE_WEIGHT = 1.0
_WORD = re.compile(r'\w+', re.UNICODE)
_FILTER = re.compile(r'\b(?:title|path):(?:"[^"]*"|\S+)', re.IGNORECASE)


def words(text: str) -> list[str]:
    """Lower-case words with accents removed (the unstemmed form the passage was written in)."""
    folded = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    return _WORD.findall(folded.casefold())


def query_words(raw: str) -> list[str]:
    """The words a person typed, without field filters; operators contribute their words only."""
    return words(_FILTER.sub(' ', raw))


def _heading_stems(term: str, heading: list[str]) -> bool:
    """A heading word that shares the term's stem ("Lanterns" for "lantern"): headings are stemmed matches."""
    return len(term) >= 4 and any(
        word.startswith(term) or term.startswith(word) for word in heading if len(word) >= 4
    )


def order(raw_query: str, candidates: list[tuple[float, str, str]]) -> list[int]:
    """Indices of ``candidates`` (BM25 score, heading, passage text; best first) in re-ranked order.

    A stemmed match in a heading counts as exact, so the index's rule that headings outrank body text holds.
    """
    count = len(candidates)
    query = query_words(raw_query)
    if count < 2 or not query:
        return list(range(count))
    distinct = list(dict.fromkeys(query))
    passages = [words(f'{heading}\n{text}') for _, heading, text in candidates]
    present = []
    for (_, heading, _), passage in zip(candidates, passages, strict=True):
        found = set(passage)
        if heading:
            heading_words = words(heading)
            found.update(term for term in distinct if _heading_stems(term, heading_words))
        present.append(found)
    weight = {
        term: math.log((count + 1) / (sum(1 for found in present if term in found) + 0.5))
        for term in distinct
    }
    total = sum(weight.values()) or 1.0
    pairs = [
        pair for pair in itertools.pairwise(query) if not (pair[0] in STOPWORDS and pair[1] in STOPWORDS)
    ]
    top = max(-score for score, _, _ in candidates) or 1.0
    keyed = []
    for position, ((score, _, _), passage, found) in enumerate(
        zip(candidates, passages, present, strict=True)
    ):
        exact = sum(weight[term] for term in distinct if term in found) / total
        phrase = 0.0
        if pairs:
            adjacent = set(itertools.pairwise(passage))
            phrase = sum(1 for pair in pairs if pair in adjacent) / len(pairs)
        keyed.append((-(-score / top + EXACT_WEIGHT * exact + PHRASE_WEIGHT * phrase), position))
    return [position for _, position in sorted(keyed)]
