"""Rank fusion, de-duplication, diversity and the reranker seam for retrieval hits.

Fusion uses reciprocal ranks only, so lists whose scores are not comparable (BM25 from
different libraries, cosine similarity, trigram passes) combine without calibration.
Every fused hit records the ranks it received, so the ordering stays inspectable.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

RRF_K = 60
_WORD = re.compile(r'\w+', re.UNICODE)


@dataclass
class Hit:
    """One retrievable passage. ``payload`` keeps the source result row unchanged."""

    chunk_id: str
    library_id: str | None = None
    document_id: str | None = None
    text: str = ''
    locator: dict = field(default_factory=dict)
    score: float = 0.0
    method: str = 'keyword'
    content_hash: str | None = None
    ranks: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)

    @property
    def key(self):
        return (self.library_id or '', self.chunk_id)

    @classmethod
    def from_result(cls, row, *, method='keyword', library_id=None):
        if isinstance(row, Hit):
            return row
        return cls(
            chunk_id=row['chunk_id'],
            library_id=row.get('library_id', library_id),
            document_id=row.get('document_id'),
            text=row.get('text', ''),
            locator=row.get('locator') or {},
            score=float(row.get('score') or 0.0),
            method=row.get('method', method),
            content_hash=row.get('content_hash'),
            payload=dict(row),
        )

    def to_dict(self):
        value = dict(self.payload)
        value.update(
            chunk_id=self.chunk_id,
            library_id=self.library_id,
            document_id=self.document_id,
            text=self.text,
            locator=self.locator,
        )
        if self.ranks:
            value['fused_score'] = round(self.score, 8)
            value['ranks'] = dict(self.ranks)
        return value


def rrf(lists: Sequence[Iterable[Any]], k: int = RRF_K, *, names: Sequence[str] | None = None) -> list[Hit]:
    """Reciprocal-rank fusion: score = sum(1 / (k + rank)) over every list a hit appears in.

    Ties break on the best single rank, then the earliest list, then the stable key, so
    equal first-place hits from different libraries interleave instead of the first
    attached library filling the budget.
    """
    if type(k) is not int or k < 1:
        raise ValueError('RRF k must be a positive integer')
    fused: dict[tuple[str, str], Hit] = {}
    order: dict[tuple[str, str], tuple[int, int]] = {}
    for position, ranked in enumerate(lists):
        name = names[position] if names else str(position)
        seen = set()
        for rank, item in enumerate(ranked, 1):
            hit = Hit.from_result(item)
            if hit.key in seen:
                continue
            seen.add(hit.key)
            current = fused.get(hit.key)
            if current is None:
                current = Hit(**{**hit.__dict__, 'ranks': {}, 'score': 0.0, 'payload': dict(hit.payload)})
                fused[hit.key] = current
                order[hit.key] = (rank, position)
            current.ranks[name] = rank
            current.score += 1.0 / (k + rank)
            order[hit.key] = min(order[hit.key], (rank, position))
    return sorted(fused.values(), key=lambda h: (-h.score, order[h.key], h.key))


def _span(locator):
    try:
        return int(locator['start']), int(locator['end'])
    except (KeyError, TypeError, ValueError):
        return None


def _place(locator):
    # ``parent`` identifies a section within one library's index, so it is not part of the place.
    return tuple(
        sorted((k, str(v)) for k, v in (locator or {}).items() if k not in ('start', 'end', 'parent'))
    )


def overlapping(first: Hit, second: Hit, threshold: float = 0.5) -> bool:
    """Same source content at the same place, sharing more than ``threshold`` of the shorter span."""
    same_source = (first.content_hash and first.content_hash == second.content_hash) or (
        first.document_id is not None and first.document_id == second.document_id
    )
    if not same_source or _place(first.locator) != _place(second.locator):
        return False
    a, b = _span(first.locator), _span(second.locator)
    if a is None or b is None:
        return a == b
    shared = min(a[1], b[1]) - max(a[0], b[0])
    shortest = max(1, min(a[1] - a[0], b[1] - b[0]))
    return shared / shortest > threshold


def dedupe(hits: Iterable[Hit]) -> tuple[list[Hit], list[dict]]:
    """Keep the best-ranked of hits from the same document and overlapping locator.

    Identical files attached through two libraries share a content hash and count as
    the same document. Returns kept hits and a report of each dropped duplicate.
    """
    kept: list[Hit] = []
    dropped: list[dict] = []
    for hit in hits:
        original = next((k for k in kept if k.key == hit.key or overlapping(k, hit)), None)
        if original is None:
            kept.append(hit)
        else:
            dropped.append(
                {
                    'chunk_id': hit.chunk_id,
                    'library_id': hit.library_id,
                    'duplicate_of': original.chunk_id,
                    'reason': 'Duplicate passage from the same document and location',
                }
            )
    return kept, dropped


def token_similarity(first: Hit, second: Hit) -> float:
    a = {w.casefold() for w in _WORD.findall(first.text)}
    b = {w.casefold() for w in _WORD.findall(second.text)}
    return len(a & b) / len(a | b) if a and b else 0.0


def mmr(
    hits: Sequence[Hit],
    limit: int,
    *,
    lambda_: float = 0.7,
    similarity: Callable[[Hit, Hit], float] = token_similarity,
) -> list[Hit]:
    """Maximal marginal relevance over an already ranked list.

    Relevance is taken from rank position (1 for the first hit, falling linearly), so the
    helper works on fused lists whose raw scores are not comparable.
    """
    if not 0.0 <= lambda_ <= 1.0:
        raise ValueError('MMR lambda must be between 0 and 1')
    remaining = list(hits)
    count = max(1, len(remaining))
    relevance = {h.key: 1.0 - i / count for i, h in enumerate(remaining)}
    selected: list[Hit] = []
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda h: (
                lambda_ * relevance[h.key]
                - (1 - lambda_) * max((similarity(h, s) for s in selected), default=0.0),
                relevance[h.key],
            ),
        )
        selected.append(best)
        remaining.remove(best)
    return selected


class Reranker(Protocol):
    """Reorders candidates for a query. Implementations must be local and deterministic."""

    identity: str

    def rerank(self, query: str, hits: Sequence[Hit]) -> list[Hit]: ...


class IdentityReranker:
    """No-op reranker: keeps fused order. Model-backed rerankers arrive in a later phase."""

    identity = 'identity'

    def rerank(self, query: str, hits: Sequence[Hit]) -> list[Hit]:
        return list(hits)
