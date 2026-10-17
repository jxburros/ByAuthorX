"""Vector storage behind one interface, kept in the library's derived index file.

The default backend stores little-endian float32 BLOBs in SQLite and searches by
brute-force cosine similarity (vectorised with numpy when it is installed). Each vector
records the model signature that produced it and the fingerprint of the chunk text it
encodes, so callers can skip unchanged chunks, measure coverage and refuse to compare
vectors from different models.

The optional ``sqlite-vec`` backend (``pip install byauthorx[sqlite-vec]``, MIT/Apache-2.0)
keeps the same float32 table as the source of truth and adds a binary-quantized (1 bit per
dimension) index. A query selects candidates by Hamming distance and re-scores them with the
exact float cosine, so ranking quality is that of the float vectors. LanceDB is documented as
an escape hatch only (docs/library-and-citations-guide.md) and is not implemented here.

Backends implement :class:`VectorStore` and register in ``BACKENDS``; callers obtain a store
only through :func:`open_store`.
"""

from __future__ import annotations

import heapq
import math
import sqlite3
import struct
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any, Protocol

# Brute force is measured by scripts/benchmark_vectors.py (512 dimensions, 4-core laptop-class CPU):
# p95 stays under the 300 ms hybrid budget up to about 10,000 vectors (see the library guide).
# Above it a library uses the sqlite-vec backend automatically when that extra is installed.
BRUTE_FORCE_LIMIT = 10_000
DEFAULT_BACKEND = 'sqlite-blob'


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

    def _candidates(self, db, signature, dimension, rowids=None):
        """Current vectors of ``signature`` as (chunk_id, norm, blob), optionally limited to some rowids."""
        query = f"""SELECT v.chunk_id, v.norm, v.vector FROM {self.TABLE} v JOIN chunks c ON c.chunk_id=v.chunk_id
                    WHERE v.model_signature=? AND v.dimension=?
                    AND v.chunk_fingerprint=COALESCE(c.fingerprint, '')"""
        args = [signature, dimension]
        if rowids is not None:
            query += f' AND v.rowid IN ({",".join("?" * len(rowids))})'
            args += list(rowids)
        return db.execute(query, args).fetchall()

    @staticmethod
    def _rank(query, rows, limit):
        """Exact cosine of ``rows`` against ``query``; best first, ties by chunk identifier."""
        query_norm = _norm(query)
        usable = [(chunk_id, norm, blob) for chunk_id, norm, blob in rows if norm]
        if not usable or not query_norm:
            return []
        scored = []
        try:
            import numpy as np
        except ImportError:
            np = None
        if np is not None:
            q = np.asarray(query, dtype=np.float32)
            for start in range(0, len(usable), 8192):
                block = usable[start : start + 8192]
                matrix = np.frombuffer(b''.join(row[2] for row in block), dtype='<f4').reshape(
                    len(block), len(query)
                )
                norms = np.asarray([row[1] for row in block], dtype=np.float64)
                sims = (matrix @ q).astype(np.float64) / (norms * query_norm)
                scored.extend((float(sim), row[0]) for sim, row in zip(sims, block, strict=True))
        else:
            for chunk_id, norm, blob in usable:
                similarity = sum(a * b for a, b in zip(query, decode(blob), strict=True)) / (
                    norm * query_norm
                )
                scored.append((similarity, chunk_id))
        # Stable order for equal similarity: chunk identifier ascending.
        best = heapq.nsmallest(limit, scored, key=lambda item: (-item[0], item[1]))
        return [(chunk_id, similarity) for similarity, chunk_id in best]

    def search(self, signature, vector, *, limit):
        """Brute-force cosine over current vectors; returns (chunk_id, similarity) best first."""
        query = [float(x) for x in vector]
        if not _norm(query) or limit < 1:
            return []
        with self.index._db() as db:
            rows = self._candidates(db, signature, len(query))
        return self._rank(query, rows, limit)


def _load_module():
    """The ``sqlite_vec`` module, or a ``missing_dependency`` error naming the extra."""
    from .errors import ByaxError

    try:
        import sqlite_vec
    except ImportError as exc:
        raise ByaxError(
            'missing_dependency',
            'The sqlite-vec vector backend needs the optional extra: install byauthorx[sqlite-vec]',
            extra='byauthorx[sqlite-vec]',
        ) from exc
    if not hasattr(sqlite3.Connection, 'enable_load_extension'):
        raise ByaxError('unsupported_feature', 'This Python build cannot load SQLite extensions')
    return sqlite_vec


def sqlite_vec_available():
    """True when the optional sqlite-vec extension can be loaded; never raises."""
    from .errors import ByaxError

    try:
        module = _load_module()
        probe = sqlite3.connect(':memory:')
        try:
            probe.enable_load_extension(True)
            module.load(probe)
            probe.enable_load_extension(False)
        finally:
            probe.close()
        return True
    except (ByaxError, sqlite3.Error, OSError, AttributeError):
        return False


class SqliteVecStore(SQLiteBlobStore):
    """Binary-quantized candidate search (sqlite-vec) with exact float re-scoring.

    The float32 table of :class:`SQLiteBlobStore` stays authoritative. ``vec_binary_<dim>`` holds
    one bit per dimension (the sign), keyed by the float row's rowid. A search takes the
    ``OVERSAMPLE * limit`` nearest rows by Hamming distance and ranks them by true cosine.
    """

    backend = 'sqlite-vec'
    OVERSAMPLE = 8
    MIN_CANDIDATES = 64
    MAX_CANDIDATES = 4096

    def __init__(self, index):
        _load_module()
        super().__init__(index)

    @contextmanager
    def _vec(self):
        module = _load_module()
        with self.index._db() as db:
            db.enable_load_extension(True)
            try:
                module.load(db)
            finally:
                db.enable_load_extension(False)
            yield db

    @staticmethod
    def _bits(dimension):
        return (dimension + 7) // 8 * 8

    @classmethod
    def _pad(cls, blob, dimension):
        """Zero-pad to a whole number of bytes of bits; padding sets no bit, so distances are unchanged."""
        return blob + b'\0' * (4 * (cls._bits(dimension) - dimension))

    @staticmethod
    def _table(dimension):
        return f'vec_binary_{dimension}'

    def _ensure(self, db, dimension):
        db.execute(
            f'CREATE VIRTUAL TABLE IF NOT EXISTS {self._table(dimension)} '
            f'USING vec0(embedding bit[{self._bits(dimension)}])'
        )

    @staticmethod
    def _tables(db):
        names = (row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        return [n for n in names if n.startswith('vec_binary_') and n[len('vec_binary_') :].isdigit()]

    def _index_rows(self, db, rows):
        """Add binary entries for (rowid, dimension, blob) triples, replacing any earlier entry."""
        for rowid, dimension, blob in rows:
            self._ensure(db, dimension)
            table = self._table(dimension)
            db.execute(f'DELETE FROM {table} WHERE rowid=?', (rowid,))
            db.execute(
                f'INSERT INTO {table}(rowid, embedding) VALUES (?, vec_quantize_binary(?))',
                (rowid, self._pad(blob, dimension)),
            )

    def upsert(self, signature, items):
        items = list(items)
        count = super().upsert(signature, items)
        ids = [item[0] for item in items]
        with self._vec() as db:
            for start in range(0, len(ids), 500):
                part = ids[start : start + 500]
                rows = db.execute(
                    f'SELECT rowid, dimension, vector FROM {self.TABLE} WHERE chunk_id IN ({",".join("?" * len(part))})',
                    part,
                ).fetchall()
                self._index_rows(db, rows)
        return count

    def _drop_orphans(self):
        with self._vec() as db:
            for table in self._tables(db):
                db.execute(f'DELETE FROM {table} WHERE rowid NOT IN (SELECT rowid FROM {self.TABLE})')

    def discard_other_signatures(self, signature):
        removed = super().discard_other_signatures(signature)
        self._drop_orphans()
        return removed

    def prune(self):
        removed = super().prune()
        self._drop_orphans()
        return removed

    def _sync(self, db, signature, dimension):
        """Index float vectors that have no binary entry yet (for example after switching backends)."""
        self._ensure(db, dimension)
        indexed = db.execute(f'SELECT COUNT(*) FROM {self._table(dimension)}').fetchone()[0]
        stored = db.execute(
            f'SELECT COUNT(*) FROM {self.TABLE} WHERE model_signature=? AND dimension=?',
            (signature, dimension),
        ).fetchone()[0]
        if indexed >= stored:
            return 0
        missing = db.execute(
            f"""SELECT v.rowid, v.dimension, v.vector FROM {self.TABLE} v
                WHERE v.model_signature=? AND v.dimension=?
                AND v.rowid NOT IN (SELECT rowid FROM {self._table(dimension)})""",
            (signature, dimension),
        ).fetchall()
        self._index_rows(db, missing)
        return len(missing)

    def search(self, signature, vector, *, limit):
        query = [float(x) for x in vector]
        if not _norm(query) or limit < 1:
            return []
        dimension = len(query)
        module = _load_module()
        wanted = min(max(limit * self.OVERSAMPLE, self.MIN_CANDIDATES), self.MAX_CANDIDATES)
        with self._vec() as db:
            self._sync(db, signature, dimension)
            blob = self._pad(module.serialize_float32(query), dimension)
            rowids = [
                row[0]
                for row in db.execute(
                    f'SELECT rowid FROM {self._table(dimension)} WHERE embedding MATCH vec_quantize_binary(?) AND k = ?',
                    (blob, wanted),
                )
            ]
            rows = []
            for start in range(0, len(rowids), 500):
                rows += self._candidates(db, signature, dimension, rowids[start : start + 500])
        return self._rank(query, rows, limit)


BACKENDS = {SQLiteBlobStore.backend: SQLiteBlobStore, SqliteVecStore.backend: SqliteVecStore}


_AVAILABLE: dict[str, bool] = {}


def _sqlite_vec_cached():
    if 'value' not in _AVAILABLE:
        _AVAILABLE['value'] = sqlite_vec_available()
    return _AVAILABLE['value']


def choose_backend(index):
    """Backend name for ``index``: its configured ``vector_backend``, else brute force until it outgrows it.

    ``auto`` (the default) uses the exact float backend up to :data:`BRUTE_FORCE_LIMIT` chunks and the
    sqlite-vec backend above that, when the optional extra is installed.
    """
    # A project index has no library settings: it always chooses automatically.
    settings = getattr(index, 'library', None) or {}
    configured = (settings.get('embedding') or {}).get('vector_backend') or 'auto'
    if configured != 'auto':
        return configured
    with index._db() as db:
        chunks = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    if chunks > BRUTE_FORCE_LIMIT and _sqlite_vec_cached():
        return SqliteVecStore.backend
    return DEFAULT_BACKEND


def open_store(index: Any, backend: str | None = None) -> VectorStore:
    """The store for ``index``: an explicit ``backend``, else :func:`choose_backend`."""
    backend = backend or choose_backend(index)
    try:
        factory = BACKENDS[backend]
    except KeyError:
        from .errors import ByaxError

        raise ByaxError('unsupported_feature', 'Unknown vector store backend', backend=backend) from None
    return factory(index)


def drop_legacy(db: sqlite3.Connection) -> None:
    """Schema-1 indexes kept JSON vectors in ``vectors``; they are stale by definition."""
    db.execute('DROP TABLE IF EXISTS vectors')
