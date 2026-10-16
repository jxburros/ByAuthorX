"""Local library definitions and rebuildable, concurrent SQLite keyword indexes.

No network requests or embedding downloads are performed. All paths originate in
local registry configuration, never in a document attachment or a search request.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import ByaxError
from .providers import _atomic_json, _default_dir
from .search_query import COLUMN_WEIGHTS, Query
from .search_query import parse as parse_query
from .search_query import suggest as suggest_spelling

SUPPORTED = {'.txt', '.md', '.markdown', '.docx', '.byax', '.pdf'}
DEFAULT_LIMITS = {
    'file_bytes': 32 * 1024**2,
    'text_chars': 2_000_000,
    'chunks': 100_000,
    'parse_seconds': 30,
    'results': 100,
    'context_chars': 100_000,
}
CHUNKING = {'version': 2, 'chars': 1800, 'overlap': 160}
# Index schema 2: chunk heading and fingerprint, porter FTS with a heading column and
# prefix indexes, a trigram table for substring matches and an fts5vocab view.
SCHEMA = 2
_SCHEMA_TABLES = (
    'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)',
    'CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL)',
    """CREATE TABLE IF NOT EXISTS files
        (path TEXT PRIMARY KEY, root TEXT, relative_path TEXT, size INTEGER, mtime INTEGER,
         document_id TEXT, status TEXT, error TEXT, lease TEXT, lease_until REAL)""",
    """CREATE TABLE IF NOT EXISTS chunks
        (chunk_id TEXT PRIMARY KEY, document_id TEXT, ordinal INTEGER, locator TEXT,
         heading TEXT, text TEXT, fingerprint TEXT)""",
    'CREATE INDEX IF NOT EXISTS chunk_document ON chunks(document_id)',
    """CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(chunk_id UNINDEXED, heading, text,
        tokenize="porter unicode61 remove_diacritics 2", prefix='2 3')""",
    "CREATE VIRTUAL TABLE IF NOT EXISTS vocab USING fts5vocab(search, 'row')",
)
MAX_SUGGESTED_TERMS = 8
_TRIGRAM_TABLE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_tri USING fts5(chunk_id UNINDEXED, text, tokenize='trigram')"
)


def _identity(prefix, *values):
    return prefix + hashlib.sha256('\0'.join(map(str, values)).encode()).hexdigest()


def _library_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'lib_[0-9a-f]{32}', value):
        raise ByaxError('invalid_input', 'Invalid library ID')
    return value


def _parser_command(snapshot, maximum, result_path):
    mode = ['--byax-library-parser'] if getattr(sys, 'frozen', False) else ['-m', 'byax.library_parser']
    return [sys.executable, *mode, str(snapshot), str(int(maximum)), str(result_path)]


class LibraryRegistry:
    def __init__(self, config_dir=None):
        self.path = Path(config_dir or _default_dir()) / 'libraries.json'

    def list(self):
        if not self.path.exists():
            return []
        try:
            entries = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(entries, list):
                raise TypeError()
            return entries
        except (OSError, ValueError, TypeError) as exc:
            raise ByaxError('invalid_input', 'Library registry cannot be read') from exc

    def get(self, library_id):
        _library_id(library_id)
        for entry in self.list():
            if entry['library_id'] == library_id:
                return entry
        raise ByaxError('not_found', 'Library is unavailable', library_id=library_id)

    def create(self, name, roots, *, include=None, exclude=None, retrieval_policy='ask'):
        if not isinstance(name, str) or not name.strip():
            raise ByaxError('invalid_input', 'Library name is required')
        if retrieval_policy not in {'ask', 'local_only', 'allowed'}:
            raise ByaxError('invalid_input', 'Unknown retrieval policy')
        for patterns in (include, exclude):
            if patterns is not None and (
                not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns)
            ):
                raise ByaxError('invalid_input', 'Library patterns must be lists of strings')
        entry = {
            'library_id': 'lib_' + uuid.uuid4().hex,
            'name': name.strip(),
            'roots': self._roots(roots),
            'include': include or ['*'],
            'exclude': exclude or [],
            'retrieval_policy': retrieval_policy,
            'embedding': None,
        }
        _atomic_json(self.path, self.list() + [entry])
        return entry

    @staticmethod
    def _roots(roots):
        if not isinstance(roots, (list, tuple)) or not roots:
            raise ByaxError('invalid_input', 'Select at least one library folder')
        result = []
        for root in roots:
            path = Path(root).expanduser().resolve()
            if not path.is_dir():
                raise ByaxError('not_found', 'Library folder does not exist', path=str(path))
            result.append(str(path))
        return list(dict.fromkeys(result))

    def relink(self, library_id, roots, *, name=None):
        _library_id(library_id)
        entries = self.list()
        entry = next((e for e in entries if e['library_id'] == library_id), None)
        if entry is None:
            entry = {
                'library_id': library_id,
                'name': name or 'Relinked library',
                'include': ['*'],
                'exclude': [],
                'retrieval_policy': 'ask',
                'embedding': None,
            }
            entries.append(entry)
        entry['roots'] = self._roots(roots)
        _atomic_json(self.path, [entry if e['library_id'] == library_id else e for e in entries])
        return entry

    def configure_embedding(self, library_id, config):
        from .embeddings import validate_config

        entry = self.get(library_id)
        entry['embedding'] = validate_config(config) if config is not None else None
        _atomic_json(self.path, [entry if item['library_id'] == library_id else item for item in self.list()])
        return entry

    def remove(self, library_id):
        self.get(library_id)
        _atomic_json(self.path, [e for e in self.list() if e['library_id'] != library_id])
        return {'removed': library_id, 'user_files_deleted': False}


def chunk_heading(locator):
    """Heading text for the FTS heading column, from the parsed document structure."""
    path = (locator or {}).get('heading_path') or []
    return ' > '.join(str(part) for part in path if str(part).strip())


def chunk_fingerprint(heading, text):
    return hashlib.sha256('\0'.join((heading or '', text)).encode()).hexdigest()


def chunk_passages(passages, document_id):
    """Preserve paragraph/page/block locators, splitting only overlong passages."""
    ordinal = 0
    for passage in passages:
        text = passage['text']
        heading = chunk_heading(passage.get('locator'))
        start = 0
        while start < len(text):
            end = min(start + CHUNKING['chars'], len(text))
            if end < len(text):
                boundary = text.rfind(' ', start + CHUNKING['chars'] // 2, end)
                if boundary > start:
                    end = boundary
            part = text[start:end]
            yield {
                'chunk_id': _identity('chunk_', document_id, ordinal, part),
                'text': part,
                'heading': heading,
                'fingerprint': chunk_fingerprint(heading, part),
                'ordinal': ordinal,
                'locator': {**passage['locator'], 'start': start, 'end': end},
            }
            ordinal += 1
            if end == len(text):
                break
            start = max(start + 1, end - CHUNKING['overlap'])


class LibraryIndex:
    def __init__(self, library, cache_dir=None, *, limits=None):
        self.library = library
        library_id = _library_id(library['library_id'])
        if cache_dir is None:
            from platformdirs import user_cache_path

            cache_dir = user_cache_path('ByAuthorX', appauthor=False) / 'libraries'
        self.directory = Path(cache_dir).resolve()
        # Even an explicit override must not place derived data in a library.
        for root in library['roots']:
            if self.directory.is_relative_to(Path(root).resolve()):
                raise ByaxError('invalid_input', 'Index cache must be outside library folders')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / (library_id + '.sqlite3')
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        if any(not isinstance(v, (int, float)) or v <= 0 for v in self.limits.values()):
            raise ByaxError('invalid_input', 'Library limits must be positive')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(_SCHEMA_TABLES[0])
            recorded = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            upgraded = (recorded is None and 'chunks' in tables) or bool(
                recorded and recorded[0] != str(SCHEMA)
            )
            if upgraded:
                self._upgrade(db, recorded[0] if recorded else None)
            for statement in _SCHEMA_TABLES[1:]:
                db.execute(statement)
            try:
                db.execute(_TRIGRAM_TABLE)
                self.trigram = True
            except sqlite3.OperationalError:
                # SQLite before 3.34 has no trigram tokenizer; substring passes are skipped.
                self.trigram = False
            if upgraded:
                # Old text stays searchable (reported stale) until the refresh job rebuilds it.
                db.execute(
                    'INSERT INTO search(rowid, chunk_id, heading, text) '
                    "SELECT rowid, chunk_id, COALESCE(heading, ''), text FROM chunks"
                )
                if self.trigram:
                    db.execute(
                        'INSERT INTO search_tri(rowid, chunk_id, text) SELECT rowid, chunk_id, text FROM chunks'
                    )
            recorded = db.execute("SELECT value FROM meta WHERE key='chunking'").fetchone()
            if recorded and json.loads(recorded[0]) != CHUNKING:
                db.execute("UPDATE files SET status='stale', size=-1")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('chunking', ?)", (json.dumps(CHUNKING),))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA),))

    @staticmethod
    def _upgrade(db, recorded):
        """Index files are derived data: an upgrade marks files stale for the refresh job."""
        from .vector_store import drop_legacy

        for table in ('search', 'search_tri', 'vocab'):
            db.execute(f'DROP TABLE IF EXISTS {table}')
        drop_legacy(db)
        if recorded is None:
            # Schema 1: extend chunks in place, then mark every file for reparsing.
            columns = {row[1] for row in db.execute('PRAGMA table_info(chunks)')}
            for column in ('heading', 'fingerprint'):
                if column not in columns:
                    db.execute(f'ALTER TABLE chunks ADD COLUMN {column} TEXT')
            db.execute("UPDATE files SET status='stale', size=-1")
        else:
            # An unknown (for example newer) schema is discarded and rebuilt from scratch.
            for table in ('chunks', 'documents', 'files', 'vector_store'):
                db.execute(f'DROP TABLE IF EXISTS {table}')

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

    def _confined(self, root, relative):
        base = Path(root).resolve()
        if str(base) not in self.library['roots']:
            raise ByaxError('not_found', 'Library root has been relinked')
        path = base / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(base) or not resolved.is_file():
            raise ByaxError('not_found', 'Library file is missing or outside its root')
        return resolved

    def _scan(self):
        for root in self.library['roots']:
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(
                    d
                    for d in dirs
                    if not d.startswith('.')
                    and not (Path(directory) / d).is_symlink()
                    and not (Path(directory) / d).resolve().is_relative_to(self.directory)
                )
                for name in sorted(files):
                    if name.startswith('.'):
                        continue
                    path = Path(directory) / name
                    relative = path.relative_to(root).as_posix()
                    if not any(fnmatch.fnmatch(relative, p) for p in self.library.get('include', ['*'])):
                        continue
                    if any(fnmatch.fnmatch(relative, p) for p in self.library.get('exclude', [])):
                        continue
                    try:
                        path = self._confined(root, relative)
                        stat = path.stat()
                    except (OSError, ByaxError):
                        continue
                    yield str(Path(root) / relative), root, relative, stat

    def _source_bytes(self, root, relative):
        # Traverse with directory descriptors: no symlink swap can redirect a read.
        if os.open in os.supports_dir_fd:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                parts = Path(relative).parts
                if not parts or any(p in {'.', '..'} for p in parts):
                    raise ValueError('Invalid library-relative path')
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                handle = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
                with os.fdopen(handle, 'rb') as source:
                    return source.read(int(self.limits['file_bytes']) + 1)
            finally:
                os.close(descriptor)
        path = self._confined(root, relative)
        with path.open('rb') as source:
            raw = source.read(int(self.limits['file_bytes']) + 1)
        if self._confined(root, relative) != path:
            raise ValueError('Library path changed during read')
        return raw

    def _parse(self, path, cancel_event, root=None, relative=None):
        with tempfile.TemporaryDirectory(prefix='byax-library-') as temporary:
            snapshot = Path(temporary) / ('source' + path.suffix.lower())
            raw = self._source_bytes(root, relative)
            if len(raw) > self.limits['file_bytes']:
                raise ValueError('Maximum file size exceeded')
            snapshot.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                result_path = Path(temporary) / 'result.json'
                deadline = time.monotonic() + self.limits['parse_seconds']
                process = subprocess.Popen(
                    _parser_command(snapshot, self.limits['text_chars'], result_path),
                    stdout=output,
                    stderr=errors,
                )
                try:
                    while process.poll() is None:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ByaxError('cancelled', 'Library refresh cancelled')
                        if time.monotonic() > deadline:
                            raise ValueError('Maximum parse time exceeded')
                        try:
                            process.wait(timeout=min(0.05, max(0.000001, deadline - time.monotonic())))
                        except subprocess.TimeoutExpired:
                            pass
                    if time.monotonic() > deadline:
                        raise ValueError('Maximum parse time exceeded')
                    with result_path.open('rb') as result_file:
                        result = json.loads(result_file.read(int(self.limits['text_chars']) * 12 + 1))
                    if 'error' in result:
                        raise ValueError(result['error'])
                    return digest, result['passages']
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()

    def refresh(self, cancel_event=None, progress=None, rebuild=False):
        owner = uuid.uuid4().hex
        scanned = list(self._scan())
        with self._db() as db:
            if rebuild:
                db.execute(
                    "UPDATE files SET size=-1, status='stale' WHERE lease IS NULL OR lease_until < ?",
                    (time.time(),),
                )
            present = {item[0] for item in scanned}
            for row in db.execute('SELECT path FROM files').fetchall():
                if row['path'] not in present:
                    db.execute("UPDATE files SET status='missing' WHERE path=?", (row['path'],))
        processed = 0
        for key, root, relative, stat in scanned:
            if cancel_event is not None and cancel_event.is_set():
                break
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT * FROM files WHERE path=?', (key,)).fetchone()
                if old and old['lease'] and old['lease_until'] > time.time():
                    continue
                if (
                    old
                    and old['size'] == stat.st_size
                    and old['mtime'] == stat.st_mtime_ns
                    and old['status'] in {'ready', 'no_text', 'unsupported'}
                ):
                    continue
                db.execute(
                    """INSERT INTO files VALUES (?,?,?,?,?,?,?,NULL,?,?) ON CONFLICT(path)
                              DO UPDATE SET size=excluded.size,mtime=excluded.mtime,status=excluded.status,
                              error=NULL,lease=excluded.lease,lease_until=excluded.lease_until""",
                    (
                        key,
                        root,
                        relative,
                        stat.st_size,
                        stat.st_mtime_ns,
                        old['document_id'] if old else None,
                        'stale' if old else 'indexing',
                        owner,
                        time.time() + self.limits['parse_seconds'] + 30,
                    ),
                )
            try:
                path = self._confined(root, relative)
                if path.suffix.lower() not in SUPPORTED:
                    self._finish(key, owner, 'unsupported')
                    processed += 1
                    if progress:
                        progress(processed / max(1, len(scanned)))
                    continue
                if stat.st_size > self.limits['file_bytes']:
                    raise ValueError('Maximum file size exceeded')
                digest, passages = self._parse(path, cancel_event, root, relative)
                current = self._confined(root, relative).stat()
                if (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                    raise ValueError('File changed during indexing; refresh again')
                document_id = _identity('doc_', self.library['library_id'], digest)
                chunks = list(chunk_passages(passages, document_id))
                with self._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    lease = db.execute('SELECT lease FROM files WHERE path=?', (key,)).fetchone()
                    if not lease or lease[0] != owner:
                        continue
                    # Replace this path's old identity atomically. If insertion fails,
                    # rollback preserves the usable stale index and its source mapping.
                    db.execute(
                        'UPDATE files SET document_id=? WHERE path=? AND lease=?', (document_id, key, owner)
                    )
                    self._collect_orphans(db)
                    existing = db.execute(
                        'SELECT COUNT(*) FROM chunks WHERE document_id=?', (document_id,)
                    ).fetchone()[0]
                    total = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
                    if total - existing + len(chunks) > self.limits['chunks']:
                        raise ValueError('Maximum library chunk count exceeded')
                    db.execute('INSERT OR IGNORE INTO documents VALUES (?,?)', (document_id, digest))
                    self._delete_search(db, 'SELECT rowid FROM chunks WHERE document_id=?', (document_id,))
                    db.execute('DELETE FROM chunks WHERE document_id=?', (document_id,))
                    for chunk in chunks:
                        rowid = db.execute(
                            'INSERT INTO chunks (chunk_id, document_id, ordinal, locator, heading, text, '
                            'fingerprint) VALUES (?,?,?,?,?,?,?)',
                            (
                                chunk['chunk_id'],
                                document_id,
                                chunk['ordinal'],
                                json.dumps(chunk['locator']),
                                chunk['heading'],
                                chunk['text'],
                                chunk['fingerprint'],
                            ),
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
                    db.execute(
                        'UPDATE files SET document_id=?,status=?,error=NULL,lease=NULL,lease_until=NULL WHERE path=? AND lease=?',
                        (document_id, 'ready' if chunks else 'no_text', key, owner),
                    )
            except Exception as exc:  # noqa: BLE001 - report individual file failures and preserve the index
                self._finish(
                    key,
                    owner,
                    'stale' if getattr(exc, 'code', '') == 'cancelled' else 'error',
                    str(exc)[:400],
                )
            processed += 1
            if progress:
                progress(processed / max(1, len(scanned)))
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._collect_orphans(db)
        result = self.status()
        result.update(processed=processed, cancelled=bool(cancel_event and cancel_event.is_set()))
        return result

    def _delete_search(self, db, rowids, args=()):
        # Full-text rows share their chunk's rowid, so deletes never scan the FTS tables.
        db.execute(f'DELETE FROM search WHERE rowid IN ({rowids})', args)
        if self.trigram:
            db.execute(f'DELETE FROM search_tri WHERE rowid IN ({rowids})', args)

    def _collect_orphans(self, db):
        # Missing files retain status records, but no longer retain their cached text.
        # A live parser's old rows remain available until its replacement commits.
        self._delete_search(
            db,
            """SELECT rowid FROM chunks WHERE document_id NOT IN (
                SELECT document_id FROM files WHERE document_id IS NOT NULL AND
                (status != 'missing' OR (lease IS NOT NULL AND lease_until > ?)))""",
            (time.time(),),
        )
        db.execute(
            """DELETE FROM chunks WHERE document_id NOT IN (
            SELECT document_id FROM files WHERE document_id IS NOT NULL AND
            (status != 'missing' OR (lease IS NOT NULL AND lease_until > ?)))""",
            (time.time(),),
        )
        db.execute('DELETE FROM documents WHERE document_id NOT IN (SELECT document_id FROM chunks)')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_store'").fetchone():
            db.execute('DELETE FROM vector_store WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)')

    def _finish(self, key, owner, status, error=None):
        with self._db() as db:
            db.execute(
                'UPDATE files SET status=?,error=?,lease=NULL,lease_until=NULL WHERE path=? AND lease=?',
                (status, error, key, owner),
            )

    def _paths(self, db, document_id):
        paths = []
        for row in db.execute('SELECT * FROM files WHERE document_id=?', (document_id,)):
            try:
                stat = self._confined(row['root'], row['relative_path']).stat()
            except (OSError, ByaxError):
                continue
            paths.append(
                {
                    'path': row['relative_path'],
                    'stale': row['status'] != 'ready'
                    or stat.st_size != row['size']
                    or stat.st_mtime_ns != row['mtime'],
                }
            )
        return paths

    def status(self):
        from .embeddings import embedding_state

        try:
            embedding = embedding_state(self)
        except (ByaxError, OSError):
            embedding = {'state': 'provider_unavailable'}
        with self._db() as db:
            files = []
            for row in db.execute('SELECT * FROM files ORDER BY relative_path'):
                item = {
                    'path': row['relative_path'],
                    'status': row['status'],
                    'error': row['error'],
                    'document_id': row['document_id'],
                }
                try:
                    stat = self._confined(row['root'], row['relative_path']).stat()
                    if item['status'] == 'ready' and (
                        stat.st_size != row['size'] or stat.st_mtime_ns != row['mtime']
                    ):
                        item['status'] = 'stale'
                except (OSError, ByaxError):
                    item['status'] = 'missing'
                files.append(item)
            documents = db.execute(
                "SELECT COUNT(DISTINCT document_id) FROM files WHERE status='ready'"
            ).fetchone()[0]
            chunks = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        return {
            'library_id': self.library['library_id'],
            'name': self.library['name'],
            'available': any(Path(p).is_dir() for p in self.library['roots']),
            'documents': documents,
            'chunks': chunks,
            'files': files,
            'stale': any(f['status'] in {'stale', 'error', 'missing'} for f in files),
            'embeddings': embedding['state'],
            'embedding_coverage': {k: embedding[k] for k in ('embedded', 'total', 'ratio') if k in embedding},
            'schema': SCHEMA,
            'trigram': self.trigram,
            'retrieval_policy': self.library.get('retrieval_policy', 'ask'),
            'chunking': CHUNKING,
            'limits': self.limits,
        }

    def embedding_preview(self):
        from .embeddings import embedding_preview

        return embedding_preview(self)

    def rebuild_vectors(self, *, authorized=False, provider=None, cancel_event=None, progress=None):
        from .embeddings import rebuild_vectors

        return rebuild_vectors(
            self, authorized=authorized, provider=provider, cancel_event=cancel_event, progress=progress
        )

    def hybrid_search(self, query, *, limit=20, offset=0, authorized=False, provider=None, expand=False):
        from .embeddings import hybrid_search

        return hybrid_search(
            self, query, limit=limit, offset=offset, authorized=authorized, provider=provider, expand=expand
        )

    def _rows(self, db, table, expression):
        weights = ','.join(str(w) for w in COLUMN_WEIGHTS) if table == 'search' else '0.0,1.0'
        try:
            return db.execute(
                f"""SELECT c.*,d.content_hash,bm25({table},{weights}) AS score FROM {table}
                    JOIN chunks c ON c.rowid={table}.rowid
                    JOIN documents d ON d.document_id=c.document_id
                    WHERE {table} MATCH ? ORDER BY score,c.chunk_id""",
                (expression,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # Expressions are built from quoted word tokens only; report rather than leak SQL.
            raise ByaxError('invalid_input', 'Search query cannot be evaluated', reason=str(exc)) from exc

    def suggestions(self, query, *, db=None, limit=3):
        """'Did you mean' spellings for query terms that match nothing, from the index vocabulary."""
        parsed = query if isinstance(query, Query) else parse_query(query)
        if db is None:
            with self._db() as connection:
                return self.suggestions(parsed, db=connection, limit=limit)
        found = {}
        # Bounded work for long prompts: only the first few plain terms are spell-checked.
        for clause in [c for c in parsed.positive if c.kind == 'term' and not c.prefix][:MAX_SUGGESTED_TERMS]:
            token = clause.tokens[0]
            if len(token) < 3 or token.isdigit() or token.casefold() in found:
                continue
            if db.execute('SELECT 1 FROM search WHERE search MATCH ? LIMIT 1', (f'"{token}"',)).fetchone():
                continue
            vocabulary = db.execute(
                'SELECT term, doc FROM vocab WHERE length(term) BETWEEN ? AND ?',
                (len(token) - 3, len(token) + 3),
            )
            options = []
            for option in suggest_spelling(token, ((r[0], r[1]) for r in vocabulary), limit=limit):
                # The vocabulary holds porter stems; show a spelling that occurs in the text.
                row = db.execute(
                    'SELECT text, heading FROM search WHERE search MATCH ? LIMIT 1', (f'"{option["term"]}"',)
                ).fetchone()
                surface = None
                if row:
                    match = re.search(
                        r'\b(' + re.escape(option['term']) + r'\w*)', row[1] + ' ' + row[0], re.IGNORECASE
                    )
                    surface = match.group(1).casefold() if match else None
                term = surface or option['term']
                if all(term != o['term'] for o in options):
                    options.append(dict(option, term=term))
            if options:
                found[token.casefold()] = options
        return found

    def search(self, query, limit=20, offset=0, *, expand=False):
        """Ranked keyword search: porter pass, then a trigram pass when it finds fewer than ``limit``.

        ``query`` uses the shared syntax in :mod:`byax.search_query`. Suggestions are
        reported for unmatched terms; ``expand=True`` also searches the suggested spellings.
        """
        if not isinstance(query, (str, Query)) or (isinstance(query, str) and not query.strip()):
            raise ByaxError('invalid_input', 'A search query is required')
        if type(limit) is not int or type(offset) is not int or limit < 1 or offset < 0:
            raise ByaxError('invalid_input', 'Invalid search pagination')
        if type(expand) is not bool:
            raise ByaxError('invalid_input', 'expand must be a boolean')
        limit = min(limit, int(self.limits['results']))
        parsed = query if isinstance(query, Query) else parse_query(query)
        report = {'method': 'keyword', 'passes': []}
        with self._db() as db:
            suggestions = self.suggestions(parsed, db=db) if not parsed.empty else {}
            if expand and suggestions:
                parsed = parsed.expand(suggestions)
            report['query'] = parsed.to_dict()
            if suggestions:
                report['suggestions'] = suggestions
            expression = parsed.fts()
            if expression is None:
                return {
                    **report,
                    'results': [],
                    'total': 0,
                    'offset': offset,
                    'truncated': False,
                    'continuation': None,
                }
            located, seen, paths_cache = [], set(), {}

            def collect(rows, label):
                for row in rows:
                    if row['chunk_id'] in seen:
                        continue
                    if row['document_id'] not in paths_cache:
                        paths_cache[row['document_id']] = self._paths(db, row['document_id'])
                    paths = paths_cache[row['document_id']]
                    if not paths:
                        continue
                    item = dict(row)
                    item['locator'] = json.loads(item['locator'])
                    item.update(
                        library_id=self.library['library_id'],
                        paths=[p['path'] for p in paths],
                        path=paths[0]['path'],
                        stale=all(p['stale'] for p in paths),
                        match=label,
                    )
                    if parsed.accepts(item):
                        seen.add(row['chunk_id'])
                        located.append(item)

            collect(self._rows(db, 'search', expression), 'porter')
            report['passes'].append('porter')
            # Substring and mid-word matches only when the stemmed pass is short of k hits.
            trigram = parsed.trigram() if self.trigram else None
            if trigram and len(located) < limit:
                collect(self._rows(db, 'search_tri', trigram), 'trigram')
                report['passes'].append('trigram')
        results, chars = [], 0
        for item in located[offset : offset + limit]:
            remaining = int(self.limits['context_chars']) - chars
            if remaining <= 0:
                break
            item['truncated'] = len(item['text']) > remaining
            item['text'] = item['text'][:remaining]
            chars += len(item['text'])
            results.append(item)
        more = offset + len(results) < len(located)
        return {
            **report,
            'results': results,
            'total': len(located),
            'offset': offset,
            'truncated': more,
            'continuation': offset + len(results) if more else None,
        }

    def literal_matches(self, term, *, limit=20):
        """Exhaustive lexical lookup of one term or phrase (porter and trigram, no ranking cut-off).

        Used to verify absence: every chunk is considered, stale chunks are reported as such,
        and ``total`` counts every matching chunk even when only ``limit`` are returned.
        """
        tokens = re.findall(r'\w+', term or '', re.UNICODE)
        literal = (term or '').strip()
        if not tokens:
            return {'term': term, 'matches': [], 'total': 0, 'passes': [], 'chunks_searched': 0}
        found, passes = {}, []
        with self._db() as db:
            searched = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
            expressions = [('search', 'porter', '"' + ' '.join(tokens) + '"')]
            if self.trigram and len(literal) >= 3:
                expressions.append(('search_tri', 'trigram', '"' + literal.replace('"', '""') + '"'))
            paths_cache = {}
            for table, label, expression in expressions:
                passes.append(label)
                for row in self._rows(db, table, expression):
                    if row['chunk_id'] in found:
                        found[row['chunk_id']]['passes'].append(label)
                        continue
                    if row['document_id'] not in paths_cache:
                        paths_cache[row['document_id']] = self._paths(db, row['document_id'])
                    paths = paths_cache[row['document_id']]
                    if not paths:
                        continue
                    text = row['text']
                    position = text.casefold().find(literal.casefold())
                    if position < 0:
                        first = re.search(r'\b' + re.escape(tokens[0][:4]), text, re.IGNORECASE)
                        position = first.start() if first else 0
                    found[row['chunk_id']] = {
                        'chunk_id': row['chunk_id'],
                        'document_id': row['document_id'],
                        'library_id': self.library['library_id'],
                        'content_hash': row['content_hash'],
                        'path': paths[0]['path'],
                        'locator': json.loads(row['locator']),
                        'heading': row['heading'],
                        'excerpt': text[max(0, position - 80) : position + len(literal) + 80],
                        'literal': text.casefold().find(literal.casefold()) >= 0,
                        'stale': all(p['stale'] for p in paths),
                        'passes': [label],
                    }
        ordered = sorted(
            found.values(), key=lambda m: (not m['literal'], m['path'], m['locator'].get('start', 0))
        )
        return {
            'term': term,
            'matches': ordered[:limit],
            'total': len(ordered),
            'truncated': len(ordered) > limit,
            'passes': passes,
            'chunks_searched': searched,
        }

    def read(self, document_id, chunk_id=None, limit=20000, *, offset=0, locator=None):
        if type(limit) is not int or limit < 1 or type(offset) is not int or offset < 0:
            raise ByaxError('invalid_input', 'Read limit must be positive and offset nonnegative')
        if locator is not None and not isinstance(locator, dict):
            raise ByaxError('invalid_input', 'Locator must be an object')
        limit = min(limit, int(self.limits['context_chars']))
        with self._db() as db:
            paths = self._paths(db, document_id)
            if not paths:
                raise ByaxError('not_found', 'Library document is unavailable')
            query = 'SELECT * FROM chunks WHERE document_id=?'
            args = [document_id]
            if chunk_id:
                query += ' AND chunk_id=?'
                args.append(chunk_id)
            rows = db.execute(query + ' ORDER BY ordinal', args).fetchall()
            if locator:
                rows = [
                    row
                    for row in rows
                    if all(json.loads(row['locator']).get(key) == value for key, value in locator.items())
                ]
            if not rows:
                raise ByaxError('not_found', 'Library passage is unavailable')
            digest = db.execute(
                'SELECT content_hash FROM documents WHERE document_id=?', (document_id,)
            ).fetchone()[0]
        results, used = [], 0
        for row in rows[offset:]:
            if used >= limit:
                break
            item = dict(row)
            item['locator'] = json.loads(item['locator'])
            item['truncated'] = len(item['text']) > limit - used
            item['text'] = item['text'][: limit - used]
            used += len(item['text'])
            results.append(item)
        return {
            'library_id': self.library['library_id'],
            'document_id': document_id,
            'content_hash': digest,
            'paths': [p['path'] for p in paths],
            'results': results,
            'stale': all(p['stale'] for p in paths),
            'total': len(rows),
            'offset': offset,
            'truncated': offset + len(results) < len(rows) or any(r['truncated'] for r in results),
            'continuation': offset + len(results) if offset + len(results) < len(rows) else None,
        }
