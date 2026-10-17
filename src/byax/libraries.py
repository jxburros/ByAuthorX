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
from stat import S_ISREG

from .context_assembly import estimate_tokens, tokens_to_chars
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
    # The optional Docling PDF engine runs a layout model, so a PDF gets far longer than other files.
    'docling_parse_seconds': 900,
    'results': 100,
    'context_chars': 100_000,
}
# Chunker v3 (structure-aware): short paragraphs merge up to ``target_tokens`` (a conservative first
# default; the retrieval harness sweeps 200-1000), a chunk never crosses a heading, and a paragraph
# longer than the target splits at sentence boundaries with ``overlap`` characters of context.
CHUNKING = {'version': 3, 'target_tokens': 400, 'overlap': 160}
MIN_TARGET_TOKENS, MAX_TARGET_TOKENS = 50, 4000
# Weight of the contextual-retrieval note column in bm25 (it is a hint, never source text).
CONTEXT_WEIGHT = 0.5
# Index schema 3: chunk heading and fingerprint, porter FTS with heading, text and a contextual-note
# column and prefix indexes, a trigram table for substring matches, an fts5vocab view and the
# ``contexts`` table that keeps notes apart from the chunk text.
SCHEMA = 3
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
    # Search resolves every matching chunk's document to its files: without this index each lookup scans
    # the whole files table (10,000 documents: 0.8 ms each, 16 s of a 40 s profile).
    'CREATE INDEX IF NOT EXISTS file_document ON files(document_id)',
    """CREATE TABLE IF NOT EXISTS contexts
        (chunk_id TEXT PRIMARY KEY, text_fingerprint TEXT NOT NULL, note TEXT NOT NULL, generator TEXT)""",
    'CREATE INDEX IF NOT EXISTS contexts_fingerprint ON contexts(text_fingerprint)',
    """CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(chunk_id UNINDEXED, heading, text, context,
        tokenize="porter unicode61 remove_diacritics 2", prefix='2 3')""",
    "CREATE VIRTUAL TABLE IF NOT EXISTS vocab USING fts5vocab(search, 'row')",
)
MAX_SUGGESTED_TERMS = 8
# Rows past the requested page that search still checks against the files, so ``total`` is exact
# for result sets up to ``offset + limit + TOTAL_LOOKAHEAD`` chunks and counted from the index beyond.
TOTAL_LOOKAHEAD = 200
_TRIGRAM_TABLE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_tri USING fts5(chunk_id UNINDEXED, text, tokenize='trigram')"
)


def _identity(prefix, *values):
    return prefix + hashlib.sha256('\0'.join(map(str, values)).encode()).hexdigest()


def _library_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'lib_[0-9a-f]{32}', value):
        raise ByaxError('invalid_input', 'Invalid library ID')
    return value


PDF_ENGINES = ('pypdf', 'docling')


def _parser_command(snapshot, maximum, result_path, engine='pypdf', seconds=None):
    mode = ['--byax-library-parser'] if getattr(sys, 'frozen', False) else ['-m', 'byax.library_parser']
    command = [sys.executable, *mode, str(snapshot), str(int(maximum)), str(result_path)]
    # The engine and its CPU budget are added only when they differ from the default parser contract.
    if engine != 'pypdf':
        command += [engine, str(int(seconds or 0))]
    return command


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

    def configure_pdf_engine(self, library_id, engine):
        """Choose how PDFs are read: ``pypdf`` (default) or the optional ``docling`` quality path."""
        if engine not in PDF_ENGINES:
            raise ByaxError('invalid_input', 'PDF engine must be pypdf or docling', engine=engine)
        entry = self.get(library_id)
        entry['pdf_engine'] = None if engine == 'pypdf' else engine
        _atomic_json(self.path, [entry if item['library_id'] == library_id else item for item in self.list()])
        return entry

    def configure_chunking(self, library_id, target_tokens):
        """Set (or clear, with ``None``) the chunk size in tokens; the next refresh re-chunks every file."""
        entry = self.get(library_id)
        if target_tokens is not None and (
            type(target_tokens) is not int or not MIN_TARGET_TOKENS <= target_tokens <= MAX_TARGET_TOKENS
        ):
            raise ByaxError(
                'invalid_input',
                f'Chunk target must be between {MIN_TARGET_TOKENS} and {MAX_TARGET_TOKENS} tokens',
            )
        entry['chunk_tokens'] = target_tokens
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


def chunk_fingerprint(heading, text, note=''):
    """Identity of what is embedded: heading, text and (when present) the contextual note."""
    parts = (heading or '', text) if not note else (heading or '', text, note)
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()


def _sections(passages):
    """Consecutive passages sharing a heading path (and PDF page) form one section."""
    group, key = [], None
    for passage in passages:
        locator = passage.get('locator') or {}
        # PDF annotation notes (locator ``note``) stay one passage each.
        current = (
            tuple(str(part) for part in locator.get('heading_path') or ()),
            locator.get('page'),
            locator.get('note') and locator.get('ordinal'),
        )
        if group and current != key:
            yield group
            group = []
        key = current
        group.append(passage)
    if group:
        yield group


def _bare_heading(group, following):
    """A group of only heading text that introduces the next section adds nothing as a chunk of its own.

    Its words stay searchable: every chunk below it carries the heading path in its heading column.
    """
    if following is None:
        return False
    path = [str(part) for part in (group[0].get('locator') or {}).get('heading_path') or ()]
    nxt = [str(part) for part in (following[0].get('locator') or {}).get('heading_path') or ()]
    if not path or nxt[: len(path)] != path or len(nxt) <= len(path):
        return False
    return all(p['text'].strip() == path[-1].strip() for p in group)


def _split_spans(text, target, overlap):
    """(start, end) spans of an overlong passage: at most ``target`` tokens, sentence boundaries preferred."""
    limit = max(1, tokens_to_chars(target))
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        while end - start > 1 and estimate_tokens(text[start:end]) > target:
            end = start + max(1, int((end - start) * 0.9))
        if end < len(text):
            floor = start + (end - start) // 2
            window = text[floor:end]
            cut = max(window.rfind('. '), window.rfind('? '), window.rfind('! '), window.rfind('\n'))
            if cut >= 0:
                end = floor + cut + 1
            else:
                boundary = text.rfind(' ', floor, end)
                if boundary > start:
                    end = boundary
        yield start, end
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)


def chunk_passages(passages, document_id, *, target_tokens=None):
    """Structure-aware chunks (v3): merge short paragraphs, never cross a heading, keep a parent pointer.

    * Passages merge (joined by a blank line) while the estimated token count stays within
      ``target_tokens``; a PDF page is never merged with another page.
    * A passage longer than the target splits at sentence, then word, boundaries with overlap.
    * ``text`` is the source text exactly as extracted, so quotes verify against it. The heading path
      lives in the ``heading`` field and is added only to the indexed (full-text and embedded) form.
    * ``locator`` keeps the first passage's coordinates (page, ordinal, block, heading_path), adds
      ``start``/``end`` offsets within the section text and ``parent``, the section's identity, which
      expands a hit to its whole section (``LibraryIndex.read_section``).
    """
    target = int(target_tokens or CHUNKING['target_tokens'])
    if not MIN_TARGET_TOKENS <= target <= MAX_TARGET_TOKENS:
        raise ByaxError(
            'invalid_input',
            f'Chunk target must be between {MIN_TARGET_TOKENS} and {MAX_TARGET_TOKENS} tokens',
        )
    groups = list(_sections(passages))
    raw = []
    for index, group in enumerate(groups):
        if _bare_heading(group, groups[index + 1] if index + 1 < len(groups) else None):
            continue
        heading = chunk_heading(group[0].get('locator'))
        parent = _identity('sec_', document_id, index, heading)
        pending = []  # [(passage, offset of the passage within the section text)]
        position = 0

        def emit(parts, start, end, text, heading=heading, parent=parent):
            first = parts[0][0].get('locator') or {}
            locator = {**first, 'start': start, 'end': end, 'parent': parent}
            last = (parts[-1][0].get('locator') or {}).get('ordinal')
            if len(parts) > 1 and last is not None:
                locator['ordinal_end'] = last
            boxes = [(q[0].get('locator') or {}).get('bbox') for q in parts]
            if len(parts) > 1 and all(boxes):
                # Merged paragraphs on one page: the box that covers them all.
                locator['bbox'] = [
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                ]
            raw.append({'text': text, 'heading': heading, 'locator': locator})

        def flush(pending=pending, emit=emit):
            if pending:
                text = '\n\n'.join(p['text'] for p, _ in pending)
                emit(pending, pending[0][1], pending[0][1] + len(text), text)
                pending.clear()

        for passage in group:
            text, offset = passage['text'], position
            position += len(text) + 2
            if estimate_tokens(text) > target:
                flush()
                for start, end in _split_spans(text, target, CHUNKING['overlap']):
                    emit([(passage, offset)], offset + start, offset + end, text[start:end])
                continue
            if pending and estimate_tokens('\n\n'.join([p['text'] for p, _ in pending] + [text])) > target:
                flush()
            pending.append((passage, offset))
        flush()
    for ordinal, chunk in enumerate(raw):
        yield {
            **chunk,
            'chunk_id': _identity('chunk_', document_id, ordinal, chunk['text']),
            'fingerprint': chunk_fingerprint(chunk['heading'], chunk['text']),
            'ordinal': ordinal,
        }


class LibraryIndex:
    def __init__(self, library, cache_dir=None, *, limits=None, chunk_tokens=None):
        self.library = library
        # Chunk size is configurable per library (``chunk_tokens``) or per index (harness sweeps).
        tokens = chunk_tokens or library.get('chunk_tokens') or CHUNKING['target_tokens']
        if type(tokens) is not int or not MIN_TARGET_TOKENS <= tokens <= MAX_TARGET_TOKENS:
            raise ByaxError(
                'invalid_input',
                f'Chunk target must be between {MIN_TARGET_TOKENS} and {MAX_TARGET_TOKENS} tokens',
            )
        engine = library.get('pdf_engine') or 'pypdf'
        if engine not in PDF_ENGINES:
            raise ByaxError('invalid_input', 'PDF engine must be pypdf or docling', engine=engine)
        self.pdf_engine = engine
        # A non-default PDF engine is part of the chunking identity: switching re-parses PDFs and
        # makes vectors made from the other engine's text stale.
        self.chunking = {
            **CHUNKING,
            'target_tokens': tokens,
            **({'pdf_engine': engine} if engine != 'pypdf' else {}),
        }
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
        self._bases = {}
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
                    'INSERT INTO search(rowid, chunk_id, heading, text, context) '
                    "SELECT c.rowid, c.chunk_id, COALESCE(c.heading, ''), c.text, COALESCE(x.note, '') "
                    'FROM chunks c LEFT JOIN contexts x ON x.chunk_id=c.chunk_id'
                )
                if self.trigram:
                    db.execute(
                        'INSERT INTO search_tri(rowid, chunk_id, text) SELECT rowid, chunk_id, text FROM chunks'
                    )
            recorded = db.execute("SELECT value FROM meta WHERE key='chunking'").fetchone()
            if recorded and json.loads(recorded[0]) != self.chunking:
                db.execute("UPDATE files SET status='stale', size=-1")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('chunking', ?)", (json.dumps(self.chunking),))
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
        elif recorded == '2':
            # Schema 2 -> 3 adds the contextual-note column and the chunker changed: keep the old text
            # searchable (reported stale) and let the refresh job re-chunk every file.
            db.execute("UPDATE files SET status='stale', size=-1")
        else:
            # An unknown (for example newer) schema is discarded and rebuilt from scratch.
            for table in ('chunks', 'documents', 'files', 'vector_store', 'contexts'):
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
                docling = self.pdf_engine == 'docling' and path.suffix.lower() == '.pdf'
                seconds = self.limits['docling_parse_seconds'] if docling else self.limits['parse_seconds']
                deadline = time.monotonic() + seconds
                process = subprocess.Popen(
                    _parser_command(
                        snapshot,
                        self.limits['text_chars'],
                        result_path,
                        'docling' if docling else 'pypdf',
                        seconds,
                    ),
                    stdout=output,
                    stderr=errors,
                    # Docling must run offline on the installed layout model, never fetch one.
                    env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
                    if docling
                    else None,
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
                        time.time()
                        + (
                            self.limits['docling_parse_seconds']
                            if self.pdf_engine == 'docling' and key.lower().endswith('.pdf')
                            else self.limits['parse_seconds']
                        )
                        + 30,
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
                chunks = list(
                    chunk_passages(passages, document_id, target_tokens=self.chunking['target_tokens'])
                )
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
                        note = self._note(db, chunk)
                        if note:
                            chunk['fingerprint'] = chunk_fingerprint(chunk['heading'], chunk['text'], note)
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
                            'INSERT INTO search(rowid, chunk_id, heading, text, context) VALUES (?,?,?,?,?)',
                            (rowid, chunk['chunk_id'], chunk['heading'], chunk['text'], note),
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
            # Notes survive re-chunking of an edited file (looked up by text) and go once nothing uses them.
            db.execute('DELETE FROM contexts WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)')
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

    def _live_stat(self, root, relative):
        """``os.stat_result`` of a library file confined to its root, else None.

        Same decision as ``_confined(...).stat()`` (the root must still be a registered root, the resolved
        path must stay inside it and be a regular file) in one resolve and one stat instead of several.
        """
        base = self._bases.get(root)
        if base is None:
            base = self._bases[root] = str(Path(root).resolve())
        if base not in self.library['roots']:
            return None
        resolved = os.path.realpath(os.path.join(base, relative))
        if not resolved.startswith(base + os.sep):
            return None
        try:
            stat = os.stat(resolved)
        except OSError:
            return None
        return stat if S_ISREG(stat.st_mode) else None

    def _paths(self, db, document_id):
        paths = []
        for row in db.execute('SELECT * FROM files WHERE document_id=?', (document_id,)):
            stat = self._live_stat(row['root'], row['relative_path'])
            if stat is None:
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
            'chunking': self.chunking,
            'contexts': self.context_counts(),
            'limits': self.limits,
        }

    @staticmethod
    def _note(db, chunk):
        """The contextual note written for a chunk with exactly this heading and text, else an empty string."""
        row = db.execute(
            'SELECT note, generator FROM contexts WHERE text_fingerprint=? ORDER BY chunk_id LIMIT 1',
            (chunk['fingerprint'],),
        ).fetchone()
        if row is None:
            return ''
        # Chunk identities include the document, so an edited file renumbers its chunks; a note is
        # reused for any chunk whose heading and text are unchanged.
        db.execute(
            'INSERT OR REPLACE INTO contexts VALUES (?,?,?,?)',
            (chunk['chunk_id'], chunk['fingerprint'], row[0], row[1]),
        )
        return row[0]

    def pending_contexts(self, limit=None):
        """Current chunks that have no contextual note yet, in index order."""
        with self._db() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """SELECT c.chunk_id, c.document_id, c.heading, c.text FROM chunks c
                       LEFT JOIN contexts x ON x.chunk_id=c.chunk_id WHERE x.chunk_id IS NULL ORDER BY c.rowid"""
                )
                if any(not p['stale'] for p in self._paths(db, row['document_id']))
            ]
            for row in rows:
                row['path'] = self._paths(db, row['document_id'])[0]['path']
        return rows[:limit] if limit else rows

    def set_contexts(self, notes, generator=''):
        """Store ``[(chunk_id, note)]`` in the separate notes table and the full-text context column.

        A note changes what is indexed and embedded for the chunk (so its vector becomes stale) but is
        never part of ``chunks.text``: it is not returned as a passage, not quoted and not cited.
        """
        stored = 0
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            for chunk_id, note in notes:
                row = db.execute(
                    'SELECT rowid, heading, text FROM chunks WHERE chunk_id=?', (chunk_id,)
                ).fetchone()
                note = ' '.join(str(note).split())
                if row is None or not note:
                    continue
                base = chunk_fingerprint(row['heading'], row['text'])
                db.execute(
                    'INSERT OR REPLACE INTO contexts VALUES (?,?,?,?)',
                    (chunk_id, base, note, generator or ''),
                )
                db.execute(
                    'UPDATE chunks SET fingerprint=? WHERE chunk_id=?',
                    (chunk_fingerprint(row['heading'], row['text'], note), chunk_id),
                )
                db.execute('DELETE FROM search WHERE rowid=?', (row['rowid'],))
                db.execute(
                    'INSERT INTO search(rowid, chunk_id, heading, text, context) VALUES (?,?,?,?,?)',
                    (row['rowid'], chunk_id, row['heading'] or '', row['text'], note),
                )
                stored += 1
        return stored

    def clear_contexts(self):
        """Remove every note and restore the plain chunk fingerprints and full-text rows."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute(
                'SELECT rowid, chunk_id, heading, text FROM chunks WHERE chunk_id IN (SELECT chunk_id FROM contexts)'
            ).fetchall()
            for row in rows:
                db.execute(
                    'UPDATE chunks SET fingerprint=? WHERE chunk_id=?',
                    (chunk_fingerprint(row['heading'], row['text']), row['chunk_id']),
                )
                db.execute('DELETE FROM search WHERE rowid=?', (row['rowid'],))
                db.execute(
                    'INSERT INTO search(rowid, chunk_id, heading, text, context) VALUES (?,?,?,?,?)',
                    (row['rowid'], row['chunk_id'], row['heading'] or '', row['text'], ''),
                )
            db.execute('DELETE FROM contexts')
        return len(rows)

    def context_counts(self):
        with self._db() as db:
            total = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
            notes = db.execute(
                'SELECT COUNT(*) FROM contexts WHERE chunk_id IN (SELECT chunk_id FROM chunks)'
            ).fetchone()[0]
        return {'notes': notes, 'chunks': total}

    def read_section(self, document_id, chunk_id, limit=20000, *, offset=0):
        """Expand a hit to its whole section: every chunk sharing its ``parent`` pointer, in order."""
        with self._db() as db:
            row = db.execute(
                'SELECT locator FROM chunks WHERE chunk_id=? AND document_id=?', (chunk_id, document_id)
            ).fetchone()
        if row is None:
            raise ByaxError('not_found', 'Library passage is unavailable')
        parent = json.loads(row['locator']).get('parent')
        if not parent:
            # Chunks from an older chunker have no section: the passage is its own section.
            result = self.read(document_id, chunk_id=chunk_id, limit=limit, offset=offset)
        else:
            result = self.read(document_id, limit=limit, offset=offset, locator={'parent': parent})
        return {**result, 'section': parent, 'anchor_chunk_id': chunk_id}

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
        weights = (
            ','.join(str(w) for w in (*COLUMN_WEIGHTS, CONTEXT_WEIGHT)) if table == 'search' else '0.0,1.0'
        )
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

    def _ranked(self, db, table, expression, limit=None):
        """Matching chunks in rank order straight from the FTS table: id, rowid and score only.

        No join: the document and content hash of a row are looked up (``_documents``) only for the rows
        a search actually examines, which is a small part of a result set that matches most of a library.
        """
        weights = (
            ','.join(str(w) for w in (*COLUMN_WEIGHTS, CONTEXT_WEIGHT)) if table == 'search' else '0.0,1.0'
        )
        try:
            return db.execute(
                f"""SELECT chunk_id,rowid,bm25({table},{weights}) AS score FROM {table}
                    WHERE {table} MATCH ? ORDER BY score,chunk_id{' LIMIT ' + str(int(limit)) if limit else ''}""",
                (expression,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ByaxError('invalid_input', 'Search query cannot be evaluated', reason=str(exc)) from exc

    @staticmethod
    def _count(db, table, expression):
        return db.execute(f'SELECT count(*) FROM {table} WHERE {table} MATCH ?', (expression,)).fetchone()[0]

    @staticmethod
    def _documents(db, rowids):
        """``{rowid: (document_id, content_hash)}`` for chunks that still have a document."""
        found = {}
        for start in range(0, len(rowids), 400):
            batch = rowids[start : start + 400]
            marks = ','.join('?' * len(batch))
            for row in db.execute(
                f"""SELECT c.rowid AS rid,c.document_id,d.content_hash FROM chunks c
                    JOIN documents d ON d.document_id=c.document_id WHERE c.rowid IN ({marks})""",
                batch,
            ):
                found[row['rid']] = (row['document_id'], row['content_hash'])
        return found

    @staticmethod
    def _chunks(db, chunk_ids):
        """Full chunk rows by id (the page of results is the only part of a search that needs text)."""
        rows = {}
        for start in range(0, len(chunk_ids), 400):
            batch = chunk_ids[start : start + 400]
            marks = ','.join('?' * len(batch))
            for row in db.execute(f'SELECT * FROM chunks WHERE chunk_id IN ({marks})', batch):
                rows[row['chunk_id']] = row
        return rows

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
            # Rank first, check files and read text only where needed. Rows are examined in rank order until
            # the requested page plus ``TOTAL_LOOKAHEAD`` more are confirmed against the files; matches
            # beyond that are only counted (``total_estimated``), so a query that matches most of a
            # 10,000-document library costs the page, not the library.
            located, seen, paths_cache, unchecked = [], set(), {}, 0
            needed = offset + limit + TOTAL_LOOKAHEAD
            filtered = bool(parsed.filters)

            def collect(rows, label):
                nonlocal unchecked
                documents = {}
                for position, row in enumerate(rows):
                    if row['chunk_id'] in seen:
                        continue
                    if len(located) >= needed and not filtered:
                        unchecked += 1
                        continue
                    if row['rowid'] not in documents:
                        ahead = [
                            r['rowid'] for r in rows[position : position + 64] if r['rowid'] not in documents
                        ]
                        documents.update(self._documents(db, ahead))
                        documents.update((r, None) for r in ahead if r not in documents)
                    if documents[row['rowid']] is None:
                        continue
                    document_id, content_hash = documents[row['rowid']]
                    if document_id not in paths_cache:
                        paths_cache[document_id] = self._paths(db, document_id)
                    paths = paths_cache[document_id]
                    if not paths:
                        continue
                    if filtered:
                        probe = {'paths': [p['path'] for p in paths], 'path': paths[0]['path']}
                        probe['locator'] = json.loads(
                            self._chunks(db, [row['chunk_id']])[row['chunk_id']]['locator']
                        )
                        if not parsed.accepts(probe):
                            continue
                    seen.add(row['chunk_id'])
                    located.append((row, paths, label, content_hash))

            # Only the head of a large ranking is needed: take twice the rows the page examines, count the
            # rest, and fall back to the full ranking if too many of the head turn out to be unusable.
            cap = max(2 * needed, 400)
            head = self._ranked(db, 'search', expression, limit=None if filtered else cap + 1)
            if len(head) > cap:
                matches, head = self._count(db, 'search', expression), head[:cap]
                collect(head, 'porter')
                if len(located) < needed:
                    located.clear()
                    seen.clear()
                    unchecked = 0
                    collect(self._ranked(db, 'search', expression), 'porter')
                else:
                    unchecked += matches - len(head)
            else:
                collect(head, 'porter')
            report['passes'].append('porter')
            # Substring and mid-word matches only when the stemmed pass is short of k hits.
            trigram = parsed.trigram() if self.trigram else None
            if trigram and len(located) < limit:
                collect(self._ranked(db, 'search_tri', trigram), 'trigram')
                report['passes'].append('trigram')
            page = located[offset : offset + limit]
            full = self._chunks(db, [row['chunk_id'] for row, _, _, _ in page])
        results, chars = [], 0
        for row, paths, label, content_hash in page:
            remaining = int(self.limits['context_chars']) - chars
            if remaining <= 0:
                break
            item = dict(full[row['chunk_id']])
            item.update(content_hash=content_hash, score=row['score'])
            item['locator'] = json.loads(item['locator'])
            item.update(
                library_id=self.library['library_id'],
                paths=[p['path'] for p in paths],
                path=paths[0]['path'],
                stale=all(p['stale'] for p in paths),
                match=label,
            )
            item['truncated'] = len(item['text']) > remaining
            item['text'] = item['text'][:remaining]
            chars += len(item['text'])
            results.append(item)
        total = len(located) + unchecked
        more = offset + len(results) < total
        return {
            **report,
            'results': results,
            'total': total,
            **({'total_estimated': True} if unchecked else {}),
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
