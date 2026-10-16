"""Vector storage behind one interface, kept in the library's derived index file.

The only backend today stores little-endian float32 BLOBs in SQLite and searches by
brute-force cosine similarity. Each vector records the model signature that produced
it and the fingerprint of the chunk text it encodes, so callers can skip unchanged
chunks, measure coverage and refuse to compare vectors from different models.

New backends (sqlite-vec, LanceDB) implement :class:`VectorStore` and register in
``BACKENDS``; callers obtain a store only through :func:`open_store`.
"""

from __future__ import annotations

import heapq
import math
import sqlite3
import struct
from collections.abc import Iterable
from typing import Protocol


class VectorStore(Protocol):
    backend: str

    def fingerprints(self, signature: str) -> dict[str, str]:
        """chunk_id -> chunk_fingerprint for vectors made with ``signature``."""
        ...

    def upsert(self, signature: str, items: Iterable[tuple[str, str, list[float]]]) -> int: ...

    def discard_other_signatures(self, signature: str) -> int:
        """Remove stale vectors from any other model; vectors are never mixed."""
        ...

    def prune(self) -> int:
        """Remove vectors whose chunk no longer exists."""
        ...

    def signatures(self) -> dict[str, int]: ...

    def coverage(self, signature: str) -> dict: ...

    def search(self, signature: str, vector: list[float], *, limit: int) -> list[tuple[str, float]]: ...


def encode(vector):
    return struct.pack(f'<{len(vector)}f', *vector)


def decode(blob):
    return struct.unpack(f'<{len(blob) // 4}f', blob)


def _norm(vector):
    return math.sqrt(sum(x * x for x in vector))


class SQLiteBlobStore:
    """float32 BLOB vectors in the index database (table ``vector_store``)."""

    backend = 'sqlite-blob'
    TABLE = 'vector_store'

    def __init__(self, index):
        self.index = index
        with index._db() as db:
            db.execute(
                f"""CREATE TABLE IF NOT EXISTS {self.TABLE}
                    (chunk_id TEXT PRIMARY KEY, model_signature TEXT NOT NULL,
                     chunk_fingerprint TEXT NOT NULL, dimension INTEGER NOT NULL,
                     norm REAL NOT NULL, vector BLOB NOT NULL)"""
            )
            db.execute(f'CREATE INDEX IF NOT EXISTS vector_signature ON {self.TABLE}(model_signature)')

    def fingerprints(self, signature):
        with self.index._db() as db:
            rows = db.execute(
                f'SELECT chunk_id, chunk_fingerprint FROM {self.TABLE} WHERE model_signature=?', (signature,)
            )
            return {row[0]: row[1] for row in rows}

    def upsert(self, signature, items):
        rows = []
        for chunk_id, fingerprint, vector in items:
            values = [float(x) for x in vector]
            rows.append((chunk_id, signature, fingerprint, len(values), _norm(values), encode(values)))
        if rows:
            with self.index._db() as db:
                db.executemany(f'INSERT OR REPLACE INTO {self.TABLE} VALUES (?,?,?,?,?,?)', rows)
        return len(rows)

    def discard_other_signatures(self, signature):
        with self.index._db() as db:
            return db.execute(f'DELETE FROM {self.TABLE} WHERE model_signature != ?', (signature,)).rowcount

    def prune(self):
        with self.index._db() as db:
            return db.execute(
                f'DELETE FROM {self.TABLE} WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)'
            ).rowcount

    def signatures(self):
        with self.index._db() as db:
            rows = db.execute(f'SELECT model_signature, COUNT(*) FROM {self.TABLE} GROUP BY model_signature')
            return {row[0]: row[1] for row in rows}

    def coverage(self, signature):
        """Current vectors match both the model signature and the chunk's fingerprint."""
        with self.index._db() as db:
            total = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
            current = db.execute(
                f"""SELECT COUNT(*) FROM chunks c JOIN {self.TABLE} v ON v.chunk_id=c.chunk_id
                    WHERE v.model_signature=? AND v.chunk_fingerprint=COALESCE(c.fingerprint, '')""",
                (signature,),
            ).fetchone()[0]
            other = db.execute(
                f'SELECT COUNT(*) FROM {self.TABLE} WHERE model_signature != ?', (signature,)
            ).fetchone()[0]
        return {
            'embedded': current,
            'total': total,
            'ratio': round(current / total, 4) if total else 1.0,
            'stale_vectors': other,
        }

    def search(self, signature, vector, *, limit):
        """Brute-force cosine over current vectors; returns (chunk_id, similarity) best first."""
        query = [float(x) for x in vector]
        query_norm = _norm(query)
        if not query_norm or limit < 1:
            return []
        scored = []
        with self.index._db() as db:
            rows = db.execute(
                f"""SELECT v.chunk_id, v.norm, v.vector FROM {self.TABLE} v JOIN chunks c ON c.chunk_id=v.chunk_id
                    WHERE v.model_signature=? AND v.dimension=?
                    AND v.chunk_fingerprint=COALESCE(c.fingerprint, '')""",
                (signature, len(query)),
            )
            for chunk_id, norm, blob in rows:
                if not norm:
                    continue
                similarity = sum(a * b for a, b in zip(query, decode(blob), strict=True)) / (
                    norm * query_norm
                )
                scored.append((similarity, chunk_id))
        # Stable order for equal similarity: chunk identifier ascending.
        best = heapq.nsmallest(limit, scored, key=lambda item: (-item[0], item[1]))
        return [(chunk_id, similarity) for similarity, chunk_id in best]


BACKENDS = {SQLiteBlobStore.backend: SQLiteBlobStore}


def open_store(index, backend=SQLiteBlobStore.backend) -> VectorStore:
    try:
        factory = BACKENDS[backend]
    except KeyError:
        from .errors import ByaxError

        raise ByaxError('unsupported_feature', 'Unknown vector store backend', backend=backend) from None
    return factory(index)


def drop_legacy(db: sqlite3.Connection) -> None:
    """Schema-1 indexes kept JSON vectors in ``vectors``; they are stale by definition."""
    db.execute('DROP TABLE IF EXISTS vectors')
