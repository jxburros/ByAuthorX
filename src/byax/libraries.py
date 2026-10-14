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

SUPPORTED = {'.txt', '.md', '.markdown', '.docx', '.byax', '.pdf'}
DEFAULT_LIMITS = {
    'file_bytes': 32 * 1024**2,
    'text_chars': 2_000_000,
    'chunks': 100_000,
    'parse_seconds': 30,
    'results': 100,
    'context_chars': 100_000,
}
CHUNKING = {'version': 1, 'chars': 1800, 'overlap': 160}


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


def chunk_passages(passages, document_id):
    """Preserve paragraph/page/block locators, splitting only overlong passages."""
    ordinal = 0
    for passage in passages:
        text = passage['text']
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
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS documents
                    (document_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS files
                    (path TEXT PRIMARY KEY, root TEXT, relative_path TEXT, size INTEGER, mtime INTEGER,
                     document_id TEXT, status TEXT, error TEXT, lease TEXT, lease_until REAL);
                CREATE TABLE IF NOT EXISTS chunks
                    (chunk_id TEXT PRIMARY KEY, document_id TEXT, ordinal INTEGER, locator TEXT, text TEXT);
                CREATE INDEX IF NOT EXISTS chunk_document ON chunks(document_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(chunk_id UNINDEXED, text);
            """)
            recorded = db.execute("SELECT value FROM meta WHERE key='chunking'").fetchone()
            if recorded and json.loads(recorded[0]) != CHUNKING:
                db.execute("UPDATE files SET status='stale', size=-1")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('chunking', ?)", (json.dumps(CHUNKING),))

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
                    db.execute(
                        'DELETE FROM search WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE document_id=?)',
                        (document_id,),
                    )
                    db.execute('DELETE FROM chunks WHERE document_id=?', (document_id,))
                    for chunk in chunks:
                        db.execute(
                            'INSERT INTO chunks VALUES (?,?,?,?,?)',
                            (
                                chunk['chunk_id'],
                                document_id,
                                chunk['ordinal'],
                                json.dumps(chunk['locator']),
                                chunk['text'],
                            ),
                        )
                        db.execute('INSERT INTO search VALUES (?,?)', (chunk['chunk_id'], chunk['text']))
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

    @staticmethod
    def _collect_orphans(db):
        # Missing files retain status records, but no longer retain their cached text.
        # A live parser's old rows remain available until its replacement commits.
        db.execute(
            """DELETE FROM search WHERE chunk_id IN (
            SELECT chunk_id FROM chunks WHERE document_id NOT IN (
                SELECT document_id FROM files WHERE document_id IS NOT NULL AND
                (status != 'missing' OR (lease IS NOT NULL AND lease_until > ?))))""",
            (time.time(),),
        )
        db.execute(
            """DELETE FROM chunks WHERE document_id NOT IN (
            SELECT document_id FROM files WHERE document_id IS NOT NULL AND
            (status != 'missing' OR (lease IS NOT NULL AND lease_until > ?)))""",
            (time.time(),),
        )
        db.execute('DELETE FROM documents WHERE document_id NOT IN (SELECT document_id FROM chunks)')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vectors'").fetchone():
            db.execute('DELETE FROM vectors WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)')

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
        from .embeddings import index_embedding_state

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
            'embeddings': index_embedding_state(self),
            'retrieval_policy': self.library.get('retrieval_policy', 'ask'),
            'chunking': CHUNKING,
            'limits': self.limits,
        }

    def embedding_preview(self):
        from .embeddings import embedding_preview

        return embedding_preview(self)

    def rebuild_vectors(self, *, authorized=False, provider=None, cancel_event=None):
        from .embeddings import rebuild_vectors

        return rebuild_vectors(self, authorized=authorized, provider=provider, cancel_event=cancel_event)

    def hybrid_search(self, query, *, limit=20, offset=0, authorized=False, provider=None):
        from .embeddings import hybrid_search

        return hybrid_search(
            self, query, limit=limit, offset=offset, authorized=authorized, provider=provider
        )

    def search(self, query, limit=20, offset=0):
        if not isinstance(query, str) or not query.strip():
            raise ByaxError('invalid_input', 'A search query is required')
        if type(limit) is not int or type(offset) is not int or limit < 1 or offset < 0:
            raise ByaxError('invalid_input', 'Invalid search pagination')
        limit = min(limit, int(self.limits['results']))
        terms = re.findall(r'\w+', query[:4096], re.UNICODE)
        if not terms:
            return {'results': [], 'total': 0, 'truncated': False, 'continuation': None}
        expression = ' OR '.join('"' + term + '"' for term in terms[:64])
        with self._db() as db:
            rows = db.execute(
                """SELECT c.*,d.content_hash,bm25(search) AS score FROM search
                                 JOIN chunks c ON c.chunk_id=search.chunk_id
                                 JOIN documents d ON d.document_id=c.document_id
                                 WHERE search MATCH ? ORDER BY score,c.chunk_id""",
                (expression,),
            )
            results, total, chars = [], 0, 0
            for row in rows:
                paths = self._paths(db, row['document_id'])
                if not paths:
                    continue
                total += 1
                if total <= offset or len(results) >= limit:
                    continue
                remaining = int(self.limits['context_chars']) - chars
                if remaining <= 0:
                    continue
                item = dict(row)
                item['locator'] = json.loads(item['locator'])
                item.update(
                    library_id=self.library['library_id'],
                    paths=[p['path'] for p in paths],
                    path=paths[0]['path'],
                    stale=all(p['stale'] for p in paths),
                )
                item['truncated'] = len(item['text']) > remaining
                item['text'] = item['text'][:remaining]
                chars += len(item['text'])
                results.append(item)
        more = offset + len(results) < total
        return {
            'results': results,
            'total': total,
            'offset': offset,
            'truncated': more,
            'continuation': offset + len(results) if more else None,
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
