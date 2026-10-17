"""The manuscript as a corpus: one block-level index of a project's documents (context engine 8.1).

A *project* is the folder of the open document: the open document and every sibling ``.byax``
package directly in that folder, plus the member documents its series lists (see
:mod:`byax.series`). Migration backups (``*.schemaN-<hash>.byax``), files still syncing and copies
of the same document (sync conflicted copies share the document identity) are skipped and reported.

The index is the one shared with the binder (competitiveness plan decision 6): every text object
of every document is one row whose locator is its stable block identity, with the binder item,
chapter and kind from :func:`byax.project.block_map` / :func:`byax.project.item_for_block`. It uses
the library index conventions (index schema 2): porter FTS5 with a heading column and prefix
indexes, a trigram table for substrings, an ``fts5vocab`` view for "did you mean", the shared
search syntax of :mod:`byax.search_query` and :mod:`byax.ranking` hits. Optional vectors live in
:mod:`byax.vector_store` and are built only when a caller passes an embedding provider.

The index is derived data in the user cache directory, never in the project folder or a
document. It updates incrementally by block content hash on save and on explicit refresh, reads
only local files, and never runs in the background over the network.

Saved searches (Scrivener-style collections) are stored in the document settings
(``settings.saved_searches``) through canonical operations; their results are computed live.
"""

from __future__ import annotations

import copy
import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .errors import ByaxError
from .search_query import COLUMN_WEIGHTS, Query
from .search_query import parse as parse_query
from .search_query import suggest as suggest_spelling

Doc = dict[str, Any]

SCHEMA = 2
GRANULARITY = {'version': 1, 'unit': 'text_object'}
MAX_DOCUMENTS = 200
MAX_RESULT_CHARS = 4000
MAX_CONTEXT_CHARS = 100_000
MAX_RESULTS = 100
MAX_SAVED = 200
SCOPES = ('project', 'document')
_BACKUP = re.compile(r'\.schema\d+-[0-9a-f]+\.byax$', re.IGNORECASE)
_TABLES = (
    'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)',
    """CREATE TABLE IF NOT EXISTS files
        (path TEXT PRIMARY KEY, relative_path TEXT, document_id TEXT, title TEXT, revision TEXT,
         size INTEGER, mtime INTEGER, source TEXT, status TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS chunks
        (chunk_id TEXT PRIMARY KEY, path TEXT, document_id TEXT, target TEXT, block_id TEXT,
         ordinal INTEGER, item TEXT, item_title TEXT, items TEXT, chapter TEXT, chapter_title TEXT,
         kind TEXT, block_type TEXT, heading TEXT, text TEXT, fingerprint TEXT)""",
    'CREATE INDEX IF NOT EXISTS chunk_path ON chunks(path)',
    """CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(chunk_id UNINDEXED, heading, text,
        tokenize="porter unicode61 remove_diacritics 2", prefix='2 3')""",
    "CREATE VIRTUAL TABLE IF NOT EXISTS vocab USING fts5vocab(search, 'row')",
)
_TRIGRAM = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_tri USING fts5(chunk_id UNINDEXED, text, tokenize='trigram')"
)
_COLUMNS = (
    'chunk_id',
    'path',
    'document_id',
    'target',
    'block_id',
    'ordinal',
    'item',
    'item_title',
    'items',
    'chapter',
    'chapter_title',
    'kind',
    'block_type',
    'heading',
    'text',
    'fingerprint',
)


def cache_root() -> Path:
    """Directory of project indexes: the user cache, never a project folder."""
    from platformdirs import user_cache_path

    return user_cache_path('ByAuthorX', appauthor=False) / 'projects'


def _fingerprint(heading: str, text: str) -> str:
    # Same identity as library chunks, so vectors are reused exactly when the text is unchanged.
    return hashlib.sha256('\0'.join((heading or '', text)).encode()).hexdigest()


# Chunks of one document


def _outline_paths(doc: Doc) -> dict[str, list[str]]:
    """Heading path of every block from the semantic outline (used when there is no binder)."""
    stack: list[tuple[int, str]] = []
    paths = {}
    for block in doc.get('blocks', []):
        if block.get('type') == 'heading':
            level = block.get('attrs', {}).get('level', 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block.get('text', '')))
        paths[block['id']] = [title for _, title in stack]
    return paths


def document_chunks(doc: Doc) -> list[dict[str, Any]]:
    """One row per text object (paragraphs, headings, footnotes, table cells) with binder metadata."""
    from . import project
    from .codex import text_objects

    rows = project.items(doc) if isinstance(doc.get('project'), dict) else []
    meta = {row['id']: row for row in rows}
    owner = project.item_for_block(doc) if rows else {}
    outline = _outline_paths(doc)
    types = {block['id']: block.get('type') for block in doc.get('blocks', [])}

    def lineage(identity):
        chain = []
        while identity is not None:
            chain.append(identity)
            identity = meta[identity]['parent']
        return list(reversed(chain))

    chunks = []
    for ordinal, (target, block_id, text) in enumerate(text_objects(doc)):
        item = owner.get(block_id)
        chain = lineage(item) if item else []
        if chain:
            heading_path = [meta[i]['title'] for i in chain]
            chapter = next((i for i in reversed(chain) if meta[i]['kind'] == 'chapter'), chain[0])
        else:
            heading_path, chapter = outline.get(block_id, []), None
        heading = ' > '.join(part for part in heading_path if part.strip())
        chunks.append(
            {
                'chunk_id': f'{doc["id"]}:{target}',
                'target': target,
                'block_id': block_id,
                'ordinal': ordinal,
                'item': item,
                'item_title': meta[item]['title'] if item else None,
                'items': ' '.join(chain),
                'chapter': chapter,
                'chapter_title': meta[chapter]['title'] if chapter else None,
                'kind': meta[item]['kind'] if item else None,
                'block_type': types.get(block_id),
                'heading': heading,
                'heading_path': heading_path,
                'text': text,
                'fingerprint': _fingerprint(heading, text),
            }
        )
    return chunks


# Project membership


def members(
    document_path: str | Path, *, series: dict[str, Any] | None = None, series_path: str | Path | None = None
) -> dict[str, Any]:
    """The project's documents: the open document, its sibling packages, then series members."""
    from .sync_safety import package_identity

    opened = Path(document_path).resolve()
    folder = opened.parent
    found: list[dict[str, Any]] = [{'path': opened, 'relative': opened.name, 'source': 'open'}]
    skipped: list[dict[str, Any]] = []
    identities: dict[str, str] = {}
    own = package_identity(opened) if opened.is_file() else {'state': 'unsaved'}
    if own.get('document_id'):
        identities[own['document_id']] = opened.name
    candidates: list[tuple[Path, str]] = []
    try:
        names = sorted(p for p in folder.iterdir() if p.suffix.lower() == '.byax')
    except OSError:
        names = []
    candidates += [(p, 'folder') for p in names]
    if series and series_path:
        from .series import documents

        candidates += [(p, 'series') for p in documents(series_path, series)]
    seen = {opened}
    for path, source in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        relative = Path(_relative_label(folder, resolved)).as_posix()
        if path.name.startswith('.') or _BACKUP.search(path.name):
            skipped.append(
                {'path': relative, 'reason': 'migration_backup' if _BACKUP.search(path.name) else 'hidden'}
            )
            continue
        if not resolved.is_file():
            continue
        identity = package_identity(resolved)
        if identity['state'] != 'ok':
            skipped.append({'path': relative, 'reason': identity.get('reason', identity['state'])})
            continue
        if identity['document_id'] in identities:
            skipped.append(
                {
                    'path': relative,
                    'reason': 'duplicate_document',
                    'duplicate_of': identities[identity['document_id']],
                }
            )
            continue
        if len(found) >= MAX_DOCUMENTS:
            skipped.append({'path': relative, 'reason': 'limit_exceeded'})
            continue
        identities[identity['document_id']] = relative
        found.append({'path': resolved, 'relative': relative, 'source': source})
    return {'folder': str(folder), 'documents': found, 'skipped': skipped}


def _relative_label(folder: Path, path: Path) -> str:
    import os

    return os.path.relpath(path, folder)


class ProjectIndex:
    """Block-level keyword (and optional vector) index of one project folder.

    ``document_path`` is the open document; ``doc`` is its in-memory state (indexed instead of the
    file, so unsaved edits are searchable after :meth:`update_document`). The index file lives in
    ``cache_dir`` (default :func:`cache_root`), keyed by the resolved folder path.
    """

    def __init__(
        self,
        document_path: str | Path,
        *,
        doc: Doc | None = None,
        cache_dir: str | Path | None = None,
        series: dict[str, Any] | None = None,
    ):
        if document_path is None:
            raise ByaxError(
                'invalid_input', 'Save the document first: a project is the folder of a saved document.'
            )
        self.document_path = Path(document_path).resolve()
        self.folder = self.document_path.parent
        self.doc = doc
        self.series_path = None
        self.series = series
        if series is None and doc is not None:
            from .series import resolve

            info = resolve(doc, self.document_path)
            if info and info['state'] == 'ok':
                self.series, self.series_path = info['series'], info['resolved']
        directory = Path(cache_dir or cache_root()).resolve()
        # Derived data never lives inside the project folder (or a library folder).
        if directory.is_relative_to(self.folder):
            raise ByaxError('invalid_input', 'The project index cache must be outside the project folder.')
        directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(str(self.folder).encode()).hexdigest()[:32]
        self.path = directory / f'project_{key}.sqlite3'
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(_TABLES[0])
            recorded = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if recorded and recorded[0] != str(SCHEMA):
                for table in ('search', 'search_tri', 'vocab', 'chunks', 'files', 'vector_store'):
                    db.execute(f'DROP TABLE IF EXISTS {table}')
            for statement in _TABLES[1:]:
                db.execute(statement)
            try:
                db.execute(_TRIGRAM)
                self.trigram = True
            except sqlite3.OperationalError:
                self.trigram = False
            db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('folder', ?)", (str(self.folder),))

    @classmethod
    def existing(cls, document_path: str | Path, *, doc: Doc | None = None, cache_dir: Any = None) -> Any:
        """The project's index when one was already built, else None (saving never creates one)."""
        folder = Path(document_path).resolve().parent
        key = hashlib.sha256(str(folder).encode()).hexdigest()[:32]
        if not (Path(cache_dir or cache_root()) / f'project_{key}.sqlite3').is_file():
            return None
        return cls(document_path, doc=doc, cache_dir=cache_dir)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=15000')
        try:
            with db:
                yield db
        finally:
            db.close()

    # Indexing

    def members(self) -> dict[str, Any]:
        return members(self.document_path, series=self.series, series_path=self.series_path)

    def _store(self, db, key, relative, doc, *, source, size=None, mtime=None):
        """Replace one document's rows by content hash: unchanged rows are not rewritten."""
        chunks = document_chunks(doc)
        existing = {
            row['chunk_id']: row
            for row in db.execute(
                'SELECT rowid, chunk_id, fingerprint, path FROM chunks WHERE path=?', (key,)
            )
        }
        # A document identity seen under another path (moved file) is reindexed under this one.
        db.execute(
            'DELETE FROM search WHERE rowid IN (SELECT rowid FROM chunks WHERE document_id=? AND path!=?)',
            (doc['id'], key),
        )
        if self.trigram:
            db.execute(
                'DELETE FROM search_tri WHERE rowid IN (SELECT rowid FROM chunks WHERE document_id=? AND path!=?)',
                (doc['id'], key),
            )
        db.execute('DELETE FROM chunks WHERE document_id=? AND path!=?', (doc['id'], key))
        added = changed = 0
        keep = set()
        for chunk in chunks:
            keep.add(chunk['chunk_id'])
            values = [
                key if column == 'path' else doc['id'] if column == 'document_id' else chunk[column]
                for column in _COLUMNS
            ]
            old = existing.get(chunk['chunk_id'])
            if old is not None and old['fingerprint'] == chunk['fingerprint']:
                db.execute(
                    'UPDATE chunks SET ordinal=?, item=?, item_title=?, items=?, chapter=?, chapter_title=?, '
                    'kind=?, block_type=? WHERE rowid=?',
                    (
                        chunk['ordinal'],
                        chunk['item'],
                        chunk['item_title'],
                        chunk['items'],
                        chunk['chapter'],
                        chunk['chapter_title'],
                        chunk['kind'],
                        chunk['block_type'],
                        old['rowid'],
                    ),
                )
                continue
            if old is not None:
                self._delete_rows(db, 'SELECT ?', (old['rowid'],))
                db.execute('DELETE FROM chunks WHERE rowid=?', (old['rowid'],))
                changed += 1
            else:
                added += 1
            rowid = db.execute(
                f'INSERT INTO chunks ({",".join(_COLUMNS)}) VALUES ({",".join("?" * len(_COLUMNS))})', values
            ).lastrowid
            db.execute(
                'INSERT INTO search(rowid, chunk_id, heading, text) VALUES (?,?,?,?)',
                (rowid, chunk['chunk_id'], chunk['heading'], chunk['text']),
            )
            if self.trigram:
                db.execute(
                    'INSERT INTO search_tri(rowid, chunk_id, text) VALUES (?,?,?)',
                    (rowid, chunk['chunk_id'], chunk['text']),
                )
        gone = [old['rowid'] for identity, old in existing.items() if identity not in keep]
        for rowid in gone:
            self._delete_rows(db, 'SELECT ?', (rowid,))
            db.execute('DELETE FROM chunks WHERE rowid=?', (rowid,))
        db.execute(
            """INSERT INTO files VALUES (?,?,?,?,?,?,?,?,'ready',NULL) ON CONFLICT(path) DO UPDATE SET
               relative_path=excluded.relative_path, document_id=excluded.document_id, title=excluded.title,
               revision=excluded.revision, size=excluded.size, mtime=excluded.mtime, source=excluded.source,
               status='ready', error=NULL""",
            (
                key,
                relative,
                doc['id'],
                doc.get('metadata', {}).get('title', ''),
                doc.get('revision'),
                size,
                mtime,
                source,
            ),
        )
        return {
            'added': added,
            'changed': changed,
            'removed': len(gone),
            'unchanged': len(chunks) - added - changed,
        }

    def _delete_rows(self, db, rowids, args=()):
        db.execute(f'DELETE FROM search WHERE rowid IN ({rowids})', args)
        if self.trigram:
            db.execute(f'DELETE FROM search_tri WHERE rowid IN ({rowids})', args)

    def update_document(self, doc: Doc, path: str | Path | None = None) -> dict[str, Any]:
        """Index one in-memory document (the open document on save) incrementally."""
        target = Path(path).resolve() if path is not None else self.document_path
        stat = target.stat() if target.is_file() else None
        source = 'open' if target == self.document_path else 'folder'
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            counts = self._store(
                db,
                str(target),
                Path(_relative_label(self.folder, target)).as_posix(),
                doc,
                source=source,
                size=stat.st_size if stat and source != 'open' else None,
                mtime=stat.st_mtime_ns if stat and source != 'open' else None,
            )
        if target == self.document_path:
            self.doc = doc
        return counts

    def refresh(
        self,
        doc: Doc | None = None,
        *,
        rebuild: bool = False,
        cancel_event: Any = None,
        progress: Callable[[float], Any] | None = None,
    ) -> dict[str, Any]:
        """Bring the index up to date with the project folder; only changed blocks are rewritten.

        The open document comes from ``doc`` (or the one given to the constructor, or the file).
        Siblings are reparsed only when their size or modification time changed; documents that
        left the project are dropped. ``rebuild`` discards the index first.
        """
        from .storage import load_document

        if doc is not None:
            self.doc = doc
        listing = self.members()
        totals = {'added': 0, 'changed': 0, 'removed': 0, 'unchanged': 0, 'documents_reindexed': 0}
        errors = []
        with self._db() as db:
            if rebuild:
                db.execute('BEGIN IMMEDIATE')
                self._delete_rows(db, 'SELECT rowid FROM chunks')
                db.execute('DELETE FROM chunks')
                db.execute('DELETE FROM files')
            present = {str(member['path']) for member in listing['documents']}
            for row in db.execute('SELECT path FROM files').fetchall():
                if row['path'] not in present:
                    self._delete_rows(db, 'SELECT rowid FROM chunks WHERE path=?', (row['path'],))
                    totals['removed'] += db.execute(
                        'DELETE FROM chunks WHERE path=?', (row['path'],)
                    ).rowcount
                    db.execute('DELETE FROM files WHERE path=?', (row['path'],))
        for number, member in enumerate(listing['documents']):
            if cancel_event is not None and cancel_event.is_set():
                break
            key, path = str(member['path']), member['path']
            try:
                if member['source'] == 'open' and self.doc is not None:
                    with self._db() as db:
                        current = db.execute(
                            'SELECT revision, source FROM files WHERE path=?', (key,)
                        ).fetchone()
                    if (
                        current
                        and current['revision'] == self.doc.get('revision')
                        and current['source'] == 'open'
                    ):
                        continue
                    counts = self.update_document(self.doc)
                else:
                    stat = path.stat()
                    with self._db() as db:
                        current = db.execute(
                            'SELECT size, mtime, status FROM files WHERE path=?', (key,)
                        ).fetchone()
                    if (
                        current
                        and current['status'] == 'ready'
                        and (current['size'], current['mtime']) == (stat.st_size, stat.st_mtime_ns)
                    ):
                        continue
                    loaded = load_document(path)
                    if member['source'] == 'open':
                        self.doc = loaded
                    with self._db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        counts = self._store(
                            db,
                            key,
                            member['relative'],
                            loaded,
                            source=member['source'],
                            size=stat.st_size,
                            mtime=stat.st_mtime_ns,
                        )
                for name in ('added', 'changed', 'removed', 'unchanged'):
                    totals[name] += counts[name]
                totals['documents_reindexed'] += 1
            except (ByaxError, OSError, ValueError) as exc:
                message = exc.message if isinstance(exc, ByaxError) else str(exc)
                errors.append({'path': member['relative'], 'reason': message[:400]})
                with self._db() as db:
                    db.execute(
                        """INSERT INTO files (path, relative_path, source, status, error) VALUES (?,?,?,'error',?)
                           ON CONFLICT(path) DO UPDATE SET status='error', error=excluded.error, size=NULL""",
                        (key, member['relative'], member['source'], message[:400]),
                    )
            if progress:
                progress((number + 1) / max(1, len(listing['documents'])))
        result = self.status()
        result.update(updated=totals, errors=errors, cancelled=bool(cancel_event and cancel_event.is_set()))
        return result

    def status(self) -> dict[str, Any]:
        with self._db() as db:
            files = [
                dict(row)
                for row in db.execute('SELECT * FROM files ORDER BY source != "open", relative_path')
            ]
            chunks = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        documents = []
        for row in files:
            documents.append(
                {
                    'path': row['relative_path'],
                    'document_id': row['document_id'],
                    'title': row['title'],
                    'source': row['source'],
                    'status': row['status'],
                    'error': row['error'],
                    'stale': self._stale(row),
                }
            )
        listing = self.members()
        return {
            'folder': str(self.folder),
            'documents': documents,
            'skipped': listing['skipped'],
            'chunks': chunks,
            'stale': any(d['stale'] for d in documents) or len(documents) != len(listing['documents']),
            'schema': SCHEMA,
            'trigram': self.trigram,
            'granularity': GRANULARITY,
            'series': bool(self.series),
        }

    def _stale(self, row: Any) -> bool:
        if row['status'] != 'ready':
            return True
        path = Path(row['path'])
        if row['source'] == 'open' and self.doc is not None and str(self.document_path) == row['path']:
            return row['revision'] != self.doc.get('revision')
        try:
            stat = path.stat()
        except OSError:
            return True
        if row['size'] is None:
            # Indexed from memory: compare with the saved revision only when we know it.
            return False
        return (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime'])

    # Search

    def _rows(self, db, table, expression):
        weights = ','.join(str(w) for w in COLUMN_WEIGHTS) if table == 'search' else '0.0,1.0'
        try:
            return db.execute(
                f"""SELECT c.*, f.relative_path, f.title AS document_title, f.source, f.status, f.size, f.mtime,
                    f.revision, bm25({table},{weights}) AS score FROM {table}
                    JOIN chunks c ON c.rowid={table}.rowid JOIN files f ON f.path=c.path
                    WHERE {table} MATCH ? ORDER BY score, c.path, c.ordinal""",
                (expression,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ByaxError('invalid_input', 'Search query cannot be evaluated', reason=str(exc)) from exc

    def suggestions(self, parsed: Query, db: Any, limit: int = 3) -> dict[str, list[dict[str, Any]]]:
        found = {}
        for clause in [c for c in parsed.positive if c.kind == 'term' and not c.prefix][:8]:
            token = clause.tokens[0]
            if len(token) < 3 or token.isdigit() or token.casefold() in found:
                continue
            if db.execute('SELECT 1 FROM search WHERE search MATCH ? LIMIT 1', (f'"{token}"',)).fetchone():
                continue
            vocabulary = db.execute(
                'SELECT term, doc FROM vocab WHERE length(term) BETWEEN ? AND ?',
                (len(token) - 3, len(token) + 3),
            )
            options: list[dict[str, Any]] = []
            for option in suggest_spelling(token, ((r[0], r[1]) for r in vocabulary), limit=limit):
                # The vocabulary holds porter stems; offer a spelling that occurs in the text.
                row = db.execute(
                    'SELECT text, heading FROM search WHERE search MATCH ? LIMIT 1', (f'"{option["term"]}"*',)
                ).fetchone()
                match = (
                    re.search(
                        r'\b(' + re.escape(option['term']) + r'\w*)', row[1] + ' ' + row[0], re.IGNORECASE
                    )
                    if row
                    else None
                )
                term = match.group(1).casefold() if match else option['term']
                if all(term != o['term'] for o in options):
                    options.append(dict(option, term=term))
            if options:
                found[token.casefold()] = options
        return found

    def _hit(self, row, label, stale_cache):
        item = {key: row[key] for key in _COLUMNS if key != 'fingerprint'}
        heading_path = [part for part in (row['heading'] or '').split(' > ') if part]
        key = row['path']
        if key not in stale_cache:
            stale_cache[key] = self._stale(row)
        item.update(
            corpus='manuscript',
            library_id=None,
            path=row['relative_path'],
            paths=[row['relative_path']],
            document_title=row['document_title'],
            source=row['source'],
            open=row['source'] == 'open',
            score=row['score'],
            match=label,
            stale=stale_cache[key],
            locator={
                'document_id': row['document_id'],
                'path': row['relative_path'],
                'block_id': row['block_id'],
                'target': row['target'],
                'ordinal': row['ordinal'],
                'item': row['item'],
                'chapter': row['chapter'],
                'kind': row['kind'],
                'heading_path': heading_path,
            },
        )
        return item

    def search(
        self,
        query: str | Query,
        *,
        limit: int = 20,
        offset: int = 0,
        expand: bool = False,
        documents: list[str] | None = None,
        kind: str | None = None,
        item: str | None = None,
        method: str = 'keyword',
        provider: Any = None,
    ) -> dict[str, Any]:
        """Ranked project-wide find: porter pass, then trigram when it finds fewer than ``limit``.

        Filters: ``documents`` (relative paths, or ``"open"`` for the open document), ``kind``
        (binder kind such as ``scene`` or ``note``) and ``item`` (a binder item and its
        descendants). ``method`` is ``keyword`` (default), ``auto`` or ``hybrid``; vectors are used
        only with an explicit ``provider`` and fall back to keyword with a ``notice``.
        Every result is bounded, with ``continuation`` for the next page.
        """
        if not isinstance(query, (str, Query)) or (isinstance(query, str) and not query.strip()):
            raise ByaxError('invalid_input', 'A search query is required')
        if type(limit) is not int or type(offset) is not int or limit < 1 or offset < 0:
            raise ByaxError('invalid_input', 'Invalid search pagination')
        if method not in ('keyword', 'auto', 'hybrid'):
            raise ByaxError('invalid_input', 'Search method must be auto, keyword or hybrid', method=method)
        limit = min(limit, MAX_RESULTS)
        parsed = query if isinstance(query, Query) else parse_query(query)
        report: dict[str, Any] = {'method': 'keyword', 'passes': [], 'corpus': 'manuscript'}
        with self._db() as db:
            suggestions = self.suggestions(parsed, db) if not parsed.empty else {}
            if expand and suggestions:
                parsed = parsed.expand(suggestions)
            report['query'] = parsed.to_dict()
            if suggestions:
                report['suggestions'] = suggestions
            expression = parsed.fts()
            located: list[dict[str, Any]] = []
            seen: set[str] = set()
            stale: dict[str, bool] = {}

            def admit(hit):
                if documents is not None and not (
                    hit['path'] in documents or ('open' in documents and hit['open'])
                ):
                    return False
                if kind is not None and hit['kind'] != kind:
                    return False
                if item is not None and item not in (hit['items'] or '').split():
                    return False
                return parsed.accepts(hit)

            def collect(rows, label):
                for row in rows:
                    if row['chunk_id'] in seen:
                        continue
                    hit = self._hit(row, label, stale)
                    if admit(hit):
                        seen.add(row['chunk_id'])
                        located.append(hit)

            if expression is not None:
                collect(self._rows(db, 'search', expression), 'porter')
                report['passes'].append('porter')
                trigram = parsed.trigram() if self.trigram else None
                if trigram and len(located) < offset + limit:
                    collect(self._rows(db, 'search_tri', trigram), 'trigram')
                    report['passes'].append('trigram')
        if method != 'keyword' and expression is not None:
            located, dense = self._dense(parsed, located, provider, method, admit, limit + offset)
            report.update(dense)
        results, chars = [], 0
        terms = [t.casefold() for t in parsed.literal_terms()]
        for hit in located[offset : offset + limit]:
            remaining = MAX_CONTEXT_CHARS - chars
            if remaining <= 0:
                break
            span = _first_span(hit['text'], terms)
            if span:
                hit['start'], hit['end'] = span
            cap = min(MAX_RESULT_CHARS, remaining)
            hit['truncated'] = len(hit['text']) > cap
            hit['text'] = hit['text'][:cap]
            chars += len(hit['text'])
            results.append(hit)
        more = offset + len(results) < len(located)
        return {
            **report,
            'results': results,
            'total': len(located),
            'offset': offset,
            'truncated': more,
            'continuation': offset + len(results) if more else None,
        }

    def literal_matches(self, term: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Every case-insensitive, word-bounded occurrence of ``term`` in indexed text, with offsets."""
        tokens = re.findall(r'\w+', term or '', re.UNICODE)
        if not tokens:
            return []
        pattern = _word_pattern(term)
        expression = '"' + ' '.join(tokens) + '"'
        occurrences = []
        with self._db() as db:
            rows = list(self._rows(db, 'search', expression))
            if self.trigram and len(term.strip()) >= 3:
                rows += list(self._rows(db, 'search_tri', '"' + term.strip().replace('"', '""') + '"'))
            seen = set()
            for row in rows:
                if row['chunk_id'] in seen:
                    continue
                seen.add(row['chunk_id'])
                for match in pattern.finditer(row['text']):
                    occurrences.append(
                        {
                            'path': row['relative_path'],
                            'document_id': row['document_id'],
                            'open': row['source'] == 'open',
                            'target': row['target'],
                            'block_id': row['block_id'],
                            'item': row['item'],
                            'chapter': row['chapter'],
                            'start': match.start(),
                            'end': match.end(),
                            'surface': match.group(0),
                            'excerpt': _excerpt(row['text'], match.start(), match.end()),
                        }
                    )
                    if len(occurrences) >= limit:
                        return occurrences
        occurrences.sort(key=lambda o: (not o['open'], o['path'], o['target'], o['start']))
        return occurrences

    def chunks(self, *, exclude_open: bool = False) -> Iterable[dict[str, Any]]:
        """Indexed rows in document order (for whole-project scans such as codex backlinks)."""
        with self._db() as db:
            rows = db.execute(
                'SELECT c.*, f.relative_path, f.source FROM chunks c JOIN files f ON f.path=c.path '
                'ORDER BY f.source != "open", f.relative_path, c.ordinal'
            ).fetchall()
        for row in rows:
            if exclude_open and row['source'] == 'open':
                continue
            yield dict(row)

    # Optional vectors

    def _signature(self, provider):
        from .embeddings import fingerprint

        return fingerprint(provider, {'corpus': 'manuscript', **GRANULARITY})

    def rebuild_vectors(
        self, provider: Any, *, cancel_event: Any = None, batch_size: int | None = None
    ) -> dict[str, Any]:
        """Embed changed rows with an explicitly supplied provider (opt-in; never automatic).

        A provider that needs consent (``authorize``) is asked first; vectors from another model
        signature are discarded, and unchanged rows are skipped by fingerprint.
        """
        from .embeddings import validate_vectors
        from .vector_store import open_store

        if hasattr(provider, 'authorize'):
            provider.authorize()
        signature = self._signature(provider)
        store = open_store(self)
        discarded = store.discard_other_signatures(signature)
        store.prune()
        current = store.fingerprints(signature)
        maximum = int(getattr(provider, 'max_input_chars', 8000))
        size = max(1, int(batch_size or getattr(provider, 'max_batch', None) or 32))
        queue = [
            row
            for row in self.chunks()
            if current.get(row['chunk_id']) != row['fingerprint'] and row['text'].strip()
        ]
        done = 0
        for start in range(0, len(queue), size):
            if cancel_event is not None and cancel_event.is_set():
                break
            batch = queue[start : start + size]
            vectors = provider.embed([row['text'][:maximum] for row in batch])
            validate_vectors(vectors, len(batch), provider.dimension)
            store.upsert(
                signature, [(r['chunk_id'], r['fingerprint'], v) for r, v in zip(batch, vectors, strict=True)]
            )
            done += len(batch)
        coverage = store.coverage(signature)
        return {'done': done, 'discarded_stale': discarded, 'model_fingerprint': signature, **coverage}

    def _dense(self, parsed, located, provider, method, admit, needed):
        from .ranking import Hit, rrf
        from .vector_store import open_store

        if provider is None:
            notice = (
                None if method == 'auto' else 'No embedding provider was supplied; showing keyword results.'
            )
            return located, ({'notice': notice} if notice else {})
        signature = self._signature(provider)
        store = open_store(self)
        coverage = store.coverage(signature)
        if not coverage['embedded']:
            return located, {'notice': 'The project has no current vectors; showing keyword results.'}
        vector = provider.embed([parsed.semantic_text()])[0]
        nearest = store.search(signature, vector, limit=max(needed * 2, 20))
        with self._db() as db:
            placeholders = ','.join('?' * len(nearest))
            rows = {
                row['chunk_id']: row
                for row in db.execute(
                    f"""SELECT c.*, f.relative_path, f.title AS document_title, f.source, f.status, f.size,
                        f.mtime, f.revision, 0.0 AS score FROM chunks c JOIN files f ON f.path=c.path
                        WHERE c.chunk_id IN ({placeholders})""",
                    [chunk for chunk, _ in nearest],
                )
            }
        stale: dict[str, bool] = {}
        dense = []
        for chunk, similarity in nearest:
            if chunk not in rows:
                continue
            hit = self._hit(rows[chunk], 'dense', stale)
            hit['similarity'] = round(similarity, 6)
            if admit(hit) and not parsed.excludes(hit['text']):
                dense.append(hit)
        fused = rrf(
            [[Hit.from_result(h) for h in located], [Hit.from_result(h, method='dense') for h in dense]],
            names=['keyword', 'dense'],
        )
        merged = []
        for hit in fused:
            row = dict(hit.payload, fused_score=round(hit.score, 8), ranks=dict(hit.ranks))
            merged.append(row)
        return merged, {
            'method': 'hybrid',
            'embedding_coverage': coverage['ratio'],
            'fusion': {'method': 'rrf', 'lists': ['keyword', 'dense']},
        }


def _word_pattern(term: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in term.split()]
    body = r'\s+'.join(parts)
    start = r'(?<!\w)' if re.match(r'\w', term.strip()[:1] or ' ') else ''
    end = r'(?!\w)' if re.match(r'\w', term.strip()[-1:] or ' ') else ''
    return re.compile(start + body + end, re.IGNORECASE)


def _first_span(text: str, terms: list[str]) -> tuple[int, int] | None:
    folded = text.casefold()
    best = None
    for term in terms:
        position = folded.find(term)
        if position >= 0 and (best is None or position < best[0]):
            best = (position, position + len(term))
    return best


def _excerpt(text: str, start: int, end: int, width: int = 80) -> str:
    return text[max(0, start - width) : end + width]


# Saved searches: stored in settings, results computed live.


def validate_saved_searches(doc: Doc, fail: Callable[[str, str], Any]) -> None:
    saved = doc.get('settings', {}).get('saved_searches')
    if saved is None:
        return
    if not isinstance(saved, dict) or len(saved) > MAX_SAVED:
        fail('Saved searches must be a mapping of at most 200 searches.', 'settings.saved_searches')
    for name, record in saved.items():
        path = f'settings.saved_searches.{name}'
        if not isinstance(name, str) or not name.strip() or len(name) > 64 or name != name.strip():
            fail('Saved search names must be trimmed text of 1 to 64 characters.', path)
        if not isinstance(record, dict) or set(record) - {
            'query',
            'scope',
            'kind',
            'item',
            'created',
            'updated',
        }:
            fail('Saved searches hold query, scope, kind, item and dates only.', path)
        if (
            not isinstance(record.get('query'), str)
            or not record['query'].strip()
            or len(record['query']) > 4096
        ):
            fail('A saved search needs a query of at most 4,096 characters.', path + '.query')
        if record.get('scope', 'project') not in SCOPES:
            fail('Saved search scope must be project or document.', path + '.scope')
        for key in ('kind', 'item', 'created', 'updated'):
            if key in record and (not isinstance(record[key], str) or len(record[key]) > 128):
                fail(f'{key} must be short text.', f'{path}.{key}')


SAVED_OP_SCHEMAS = {
    'saved_search_set': ('name query', 'scope kind item'),
    'saved_search_remove': ('name', ''),
}


def _op_saved_set(doc, op, base_revision, kind, changed, allow_policy_changes):
    from .codex import _now

    if not isinstance(op['query'], str) or not op['query'].strip():
        raise ByaxError('schema_error', 'A saved search needs a query.', field='query')
    saved = doc['settings'].setdefault('saved_searches', {})
    name = ' '.join(op['name'].split())
    now = _now()
    record = {'query': op['query'], 'scope': op.get('scope', 'project')}
    for key in ('kind', 'item'):
        if op.get(key):
            record[key] = op[key]
    record['created'] = saved.get(name, {}).get('created', now)
    record['updated'] = now
    if name not in saved and len(saved) >= MAX_SAVED:
        raise ByaxError('limit_exceeded', 'A document holds at most 200 saved searches.')
    saved[name] = record
    changed.append('settings')


def _op_saved_remove(doc, op, base_revision, kind, changed, allow_policy_changes):
    saved = doc['settings'].get('saved_searches', {})
    if op['name'] not in saved:
        raise ByaxError('missing_target', 'Saved search does not exist.', target=op['name'])
    del saved[op['name']]
    if not saved:
        doc['settings'].pop('saved_searches')
    changed.append('settings')


SAVED_HANDLERS = {'saved_search_set': _op_saved_set, 'saved_search_remove': _op_saved_remove}


def saved_searches(doc: Doc) -> list[dict[str, Any]]:
    saved = doc.get('settings', {}).get('saved_searches', {}) or {}
    return [
        dict(record, name=name) for name, record in sorted(saved.items(), key=lambda kv: kv[0].casefold())
    ]


def run_saved(
    index: ProjectIndex, doc: Doc, name: str, *, limit: int = 20, offset: int = 0
) -> dict[str, Any]:
    """Results of a saved search, computed now against the current index."""
    record = (doc.get('settings', {}).get('saved_searches') or {}).get(name)
    if record is None:
        raise ByaxError('missing_target', 'Saved search does not exist.', target=name)
    result = index.search(
        record['query'],
        limit=limit,
        offset=offset,
        documents=['open'] if record.get('scope') == 'document' else None,
        kind=record.get('kind'),
        item=record.get('item'),
    )
    result['saved_search'] = dict(record, name=name)
    return result


# Backlinks and unlinked mentions


def _target_info(doc: Doc, target: str | None, bookmark: str | None, series: Any) -> dict[str, Any]:
    from .codex import effective_entries

    if bookmark is not None:
        record = doc.get('settings', {}).get('bookmarks', {}).get(bookmark)
        if record is None:
            raise ByaxError('missing_target', 'Bookmark does not exist.', target=bookmark)
        return {'type': 'bookmark', 'id': bookmark, 'label': bookmark, 'terms': [bookmark.replace('_', ' ')]}
    if not isinstance(target, str) or not target:
        raise ByaxError('invalid_input', 'Name a heading, bookmark or codex entry.', field='target')
    entry = effective_entries(doc, series).get(target)
    if entry is not None:
        return {
            'type': 'codex',
            'id': target,
            'label': entry['name'],
            'terms': [entry['name'], *entry.get('aliases', [])],
            'entry': entry,
        }
    from .model import find_object

    block = find_object(doc, target)
    if block.get('type') != 'heading':
        raise ByaxError(
            'invalid_input',
            'Backlinks are available for headings, bookmarks and codex entries.',
            target=target,
        )
    return {'type': 'heading', 'id': target, 'label': block.get('text', ''), 'terms': [block.get('text', '')]}


def _xrefs(doc: Doc, identities: set[str]) -> list[dict[str, Any]]:
    links = []
    for block in doc.get('blocks', []):
        for mark in block.get('marks', []):
            value = mark.get('value')
            if mark.get('type') == 'xref' and isinstance(value, dict) and value.get('target') in identities:
                links.append(
                    {
                        'kind': 'cross_reference',
                        'target': block['id'],
                        'block_id': block['id'],
                        'start': mark.get('start'),
                        'end': mark.get('end'),
                        'excerpt': _excerpt(block.get('text', ''), mark.get('start', 0), mark.get('end', 0)),
                        'open': True,
                    }
                )
    return links


def links(
    doc: Doc,
    *,
    target: str | None = None,
    bookmark: str | None = None,
    index: ProjectIndex | None = None,
    series: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
    for_ai: bool = False,
) -> dict[str, Any]:
    """Backlinks and unlinked mentions of a heading, bookmark or codex entry across the project.

    * Heading or bookmark: **backlinks** are cross-references to it; **unlinked mentions** are
      plain-text occurrences of its title (or bookmark name) anywhere in the project.
    * Codex entry: **backlinks** are the mentions the codex recognizes (names and aliases);
      **unlinked mentions** are near misses it does not count: a different letter case for a
      case-sensitive entry, or an occurrence inside one of its excluded phrases.

    The open document is read live; sibling documents come from ``index`` (refresh it first).
    """
    from .codex import effective_entries, scan, text_objects

    info = _target_info(doc, target, bookmark, series)
    if for_ai and info.get('entry', {}).get('hide_from_ai'):
        raise ByaxError('missing_target', 'Codex entry does not exist.', target=target)
    backlinks: list[dict[str, Any]] = []
    unlinked: list[dict[str, Any]] = []
    sources: list[tuple[dict[str, Any], str, str, str]] = [
        ({'open': True, 'path': index.document_path.name if index else None, 'item': None}, o, b, t)
        for o, b, t in text_objects(doc)
    ]
    if index is not None:
        sources += [
            (
                {
                    'open': False,
                    'path': row['relative_path'],
                    'item': row['item'],
                    'document_id': row['document_id'],
                },
                row['target'],
                row['block_id'],
                row['text'],
            )
            for row in index.chunks(exclude_open=True)
        ]
    if info['type'] == 'codex':
        entries = {info['id']: effective_entries(doc, series)[info['id']]}
        for where, object_id, block_id, text in sources:
            for mention in scan(text, entries, near_misses=True):
                row = dict(
                    where,
                    target=object_id,
                    block_id=block_id,
                    start=mention['start'],
                    end=mention['end'],
                    surface=text[mention['start'] : mention['end']],
                    excerpt=_excerpt(text, mention['start'], mention['end']),
                )
                if mention.get('rejected'):
                    unlinked.append(dict(row, kind='near_miss', reason=mention['reason']))
                else:
                    backlinks.append(dict(row, kind='mention'))
    else:
        identities = {info['id']}
        backlinks = _xrefs(doc, identities)
        heading_id = info['id'] if info['type'] == 'heading' else None
        for term in info['terms']:
            if len(term.strip()) < 3:
                continue
            pattern = _word_pattern(term)
            for where, object_id, block_id, text in sources:
                if object_id == heading_id:
                    continue
                for match in pattern.finditer(text):
                    unlinked.append(
                        dict(
                            where,
                            kind='text',
                            target=object_id,
                            block_id=block_id,
                            start=match.start(),
                            end=match.end(),
                            surface=match.group(0),
                            excerpt=_excerpt(text, match.start(), match.end()),
                        )
                    )
    if isinstance(doc.get('project'), dict):
        from .project import item_for_block

        owner = item_for_block(doc)
        for row in backlinks + unlinked:
            if row.get('open'):
                row['item'] = owner.get(row['block_id'])

    def page(rows):
        selected = rows[offset : offset + limit]
        more = offset + len(selected) < len(rows)
        return {
            'items': selected,
            'total': len(rows),
            'offset': offset,
            'truncated': more,
            'continuation': offset + len(selected) if more else None,
        }

    subject = {k: v for k, v in info.items() if k not in ('entry', 'terms')}
    subject['terms'] = list(info['terms'])
    return {
        'subject': subject,
        'backlinks': page(backlinks),
        'unlinked_mentions': page(unlinked),
        'project_documents': 1 + (len({r['path'] for r in index.chunks(exclude_open=True)}) if index else 0),
    }


# CLI, MCP and REST: ``byax project-search --action ...``

READ_ACTIONS = ('find', 'status', 'refresh', 'saved-list', 'saved-run', 'backlinks', 'unlinked-mentions')
MUTATING_ACTIONS = ('saved-add', 'saved-remove')


def project_search_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    from . import engine
    from .cli import _load, _mutate, envelope
    from .series import resolve

    action = a.get('action', 'find')
    if action not in READ_ACTIONS + MUTATING_ACTIONS:
        raise ByaxError(
            'invalid_input',
            f'Unknown project-search action: {action}',
            supported=list(READ_ACTIONS + MUTATING_ACTIONS),
        )
    if action in MUTATING_ACTIONS:
        if not a.get('name'):
            raise ByaxError('invalid_input', 'Missing required argument: name', field='name')
        if action == 'saved-add':
            if not a.get('query'):
                raise ByaxError('invalid_input', 'Missing required argument: query', field='query')
            op: dict[str, Any] = {'type': 'saved_search_set', 'name': a['name'], 'query': a['query']}
            for key in ('scope', 'kind', 'item'):
                if a.get(key) is not None:
                    op[key] = a[key]
        else:
            op = {'type': 'saved_search_remove', 'name': a['name']}
        request: dict[str, Any] = {'operations': [op]}
        for option, key in (('expect_revision', 'expected_revision'), ('request_id', 'request_id')):
            if a.get(option) is not None:
                request[key] = a[option]
        dry_run = a.get('dry_run', False)

        def apply(doc):
            candidate, result = engine.apply(doc, request, dry_run=dry_run)
            result['saved_searches'] = saved_searches(candidate)
            return candidate, result, not dry_run

        return _mutate(a['path'], apply)
    doc = _load(a['path'])
    if action == 'saved-list':
        return envelope({'saved_searches': saved_searches(doc)}, doc)
    info = resolve(doc, a['path'])
    series = info['series'] if info and info['state'] == 'ok' else None
    index = ProjectIndex(a['path'], doc=doc)
    if action == 'status':
        return envelope(index.status(), doc)
    # Results are computed live: an incremental refresh reads only changed local files.
    refreshed = index.refresh(doc, rebuild=a.get('rebuild', False) if action == 'refresh' else False)
    if action == 'refresh':
        return envelope(refreshed, doc)
    limit, offset = a.get('limit', 20), a.get('offset', 0)
    if action == 'find':
        if not a.get('query'):
            raise ByaxError('invalid_input', 'Missing required argument: query', field='query')
        result = index.search(
            a['query'],
            limit=limit,
            offset=offset,
            expand=a.get('expand', False),
            documents=['open'] if a.get('scope') == 'document' else a.get('documents'),
            kind=a.get('kind'),
            item=a.get('item'),
        )
    elif action == 'saved-run':
        if not a.get('name'):
            raise ByaxError('invalid_input', 'Missing required argument: name', field='name')
        result = run_saved(index, doc, a['name'], limit=limit, offset=offset)
    else:
        found = links(
            doc,
            target=a.get('target'),
            bookmark=a.get('bookmark'),
            index=index,
            series=series,
            limit=limit,
            offset=offset,
            for_ai=True,
        )
        key = 'backlinks' if action == 'backlinks' else 'unlinked_mentions'
        result = {
            'subject': found['subject'],
            key: found[key],
            'project_documents': found['project_documents'],
        }
    result['index'] = {
        'documents': len(refreshed['documents']),
        'chunks': refreshed['chunks'],
        'skipped': refreshed['skipped'],
        'errors': refreshed['errors'],
    }
    return envelope(copy.deepcopy(result), doc)
