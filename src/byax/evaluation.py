"""Retrieval evaluation harness (context engine plan, phase 0).

Builds real library indexes from the fixture folders under `tests/fixtures/eval/`, runs a retrieval
configuration over the golden queries and reports recall@5/@20, nDCG@10, MRR, latency percentiles,
index size and build time. It depends only on public entry points (`LibraryRegistry`, `LibraryIndex`
search/read/refresh/hybrid_search, `retrieve_context`), so later engine changes plug in as new
configurations without touching the metrics.

Labels are locators, never chunk IDs: `{path, page}` for PDFs and `{path, start, end}` (character
offsets into the file's text) for text. A hit is relevant when its own span overlaps a locator, so
changing the chunker does not invalidate the labels. Nothing here uses the network or a model.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import platform
import re
import shutil
import sqlite3
import time
import zlib
from collections.abc import Callable, Iterable
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

from .errors import ByaxError

QUERY_TYPES = (
    'known_item_quote',
    'factual_lookup',
    'thematic',
    'citation_locating',
    'continuity',
    'absence_probe',
)
FIXTURE_LIBRARIES = ('novel', 'series', 'humanities', 'webclips', 'papers')
DEPTH = 20  # hits requested per query; every metric is computed from this ranked list
METRICS = ('recall@5', 'recall@20', 'ndcg@10', 'mrr')
# Deterministic, fast subsets used by the pytest suite. Locators beyond the kept prefix are skipped.
SAMPLES = {
    'novel': {'prefix_chars': 70_000},
    'series': {
        'files': ['adventures/01-a-scandal-in-bohemia.txt', 'adventures/02-the-red-headed-league.txt']
    },
    'humanities': {'prefix_chars': 60_000},
    'webclips': {'every': 6},
    'papers': {'files': ['pcbi.1004150.pdf', 'pcbi.1004743.pdf', 'pcbi.1004961.pdf']},  # the committed PDFs
}


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def fixtures_root() -> Path:
    """The fixture folder: `$BYAX_EVAL_FIXTURES`, or `tests/fixtures/eval` of a source checkout."""
    override = os.environ.get('BYAX_EVAL_FIXTURES')
    return Path(override) if override else Path(__file__).resolve().parents[2] / 'tests' / 'fixtures' / 'eval'


def default_papers_cache() -> Path:
    """Where `scripts/fetch_eval_fixtures.py` caches the 27 papers that are not committed."""
    override = os.environ.get('BYAX_EVAL_CACHE')
    if override:
        return Path(override)
    from platformdirs import user_cache_path

    return user_cache_path('ByAuthorX', appauthor=False) / 'eval-fixtures' / 'papers'


# --------------------------------------------------------------------------------------------------
# Queries


def load_queries(library: str, root: Path | None = None) -> list[dict]:
    return load_queries_file((root or fixtures_root()) / library / 'queries.jsonl')


def load_queries_file(path: Path) -> list[dict]:
    queries = [
        json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()
    ]
    for record in queries:
        validate_query(record)
    return queries


def validate_query(record: dict) -> dict:
    """Schema check shared by the loader, the query builder and the tests."""
    for key in ('id', 'type', 'query', 'relevant_chunk_locators'):
        if key not in record:
            raise ValueError(f'Query {record.get("id")!r} lacks {key!r}')
    if record['type'] not in QUERY_TYPES:
        raise ValueError(f'Query {record["id"]!r} has unknown type {record["type"]!r}')
    if not isinstance(record['query'], str):
        raise TypeError(f'Query {record["id"]!r}: query must be text')
    if not record['query'].strip():
        raise ValueError(f'Query {record["id"]!r} is empty')
    locators = record['relevant_chunk_locators']
    if not isinstance(locators, list):
        raise TypeError(f'Query {record["id"]!r}: locators must be a list')
    if bool(record.get('expected_absent')) == bool(locators):
        raise ValueError(
            f'Query {record["id"]!r}: expected_absent must be true exactly when there are no locators'
        )
    for locator in locators:
        if not isinstance(locator.get('path'), str):
            raise TypeError(f'Query {record["id"]!r}: locator without path')
        if 'page' in locator:
            if type(locator['page']) is not int or locator['page'] < 1:
                raise ValueError(f'Query {record["id"]!r}: bad page')
        elif not (
            type(locator.get('start')) is int
            and type(locator.get('end')) is int
            and 0 <= locator['start'] < locator['end']
        ):
            raise ValueError(f'Query {record["id"]!r}: locator needs page or start/end')
    return record


# --------------------------------------------------------------------------------------------------
# Text coordinates


class _TextExtractor(HTMLParser):
    BLOCKS: ClassVar[set[str]] = {
        'p', 'div', 'li', 'tr', 'ul', 'ol', 'table', 'section', 'header', 'footer', 'nav', 'aside', 'article',
        'main', 'figure', 'figcaption', 'blockquote', 'dt', 'dd', 'dl', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'caption', 'pre', 'form', 'label', 'button', 'details', 'summary', 'hr', 'td', 'th',
    }  # fmt: skip
    SKIP: ClassVar[set[str]] = {'script', 'style', 'template', 'noscript', 'head'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.buffer: list[str] = []
        self.skipping = 0
        self.title = ''
        self._in_title = False

    def flush(self):
        text = ' '.join(''.join(self.buffer).split())
        if text:
            self.paragraphs.append(text)
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        if tag == 'title':
            self._in_title = True
        if tag in self.BLOCKS:
            self.flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        if tag == 'title':
            self._in_title = False
        if tag in self.BLOCKS:
            self.flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skipping:
            self.buffer.append(data)


def html_text(html: str) -> str:
    """Visible text of an HTML page, one block-level element per paragraph; page chrome is included.

    The library parser does not read `.html` at 0.3.0, so the harness materialises webclips as text with
    this function and labels offsets in its output. Boilerplate (menus, footers, language lists) is kept on
    purpose: it is what makes web clips a hard retrieval corpus.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.flush()
    return '\n\n'.join(([parser.title.strip()] if parser.title.strip() else []) + parser.paragraphs) + '\n'


class Shadow:
    """Whitespace-collapsed copy of a text with a map back to original offsets."""

    def __init__(self, text: str):
        self.original_starts: list[int] = []
        self.shadow_starts: list[int] = []
        parts, position = [], 0
        for match in re.finditer(r'\S+', text):
            self.shadow_starts.append(position)
            self.original_starts.append(match.start())
            parts.append(match.group())
            position += len(match.group()) + 1
        self.text = ' '.join(parts)

    def original(self, index: int) -> int:
        slot = bisect.bisect_right(self.shadow_starts, index) - 1
        return self.original_starts[slot] + index - self.shadow_starts[slot]

    def span(self, start: int, end: int) -> tuple[int, int]:
        return self.original(start), self.original(end - 1) + 1

    def find(self, needle: str, begin: int = 0) -> tuple[int, int] | None:
        needle = ' '.join(needle.split())
        position = self.text.find(needle, begin) if needle else -1
        return None if position < 0 else (position, position + len(needle))

    def find_all(self, needle: str, *, casefold: bool = False) -> list[tuple[int, int]]:
        needle = ' '.join(needle.split())
        haystack = self.text
        if casefold:
            haystack, needle = haystack.casefold(), needle.casefold()
        found, position = [], haystack.find(needle) if needle else -1
        while position >= 0:
            found.append(self.span(position, position + len(needle)))
            position = haystack.find(needle, position + len(needle))
        return found


def squash(text: str) -> str:
    """Letters and digits only, NFKC and case folded: robust to hyphenation, ligatures and line breaks."""
    import unicodedata

    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', text).casefold())


def page_texts(path: Path) -> list[str]:
    """Per-page text exactly as the library parser extracts it (pypdf)."""
    import logging

    from pypdf import PdfReader

    logging.getLogger('pypdf').setLevel(logging.ERROR)  # font-encoding warnings are noise here
    return [page.extract_text() or '' for page in PdfReader(path).pages]


# --------------------------------------------------------------------------------------------------
# Corpus


class Corpus:
    """A fixture library materialised in a working folder and indexed through the public API."""

    def __init__(self, name, registry, library, index, root, source_map, extent, workdir):
        self.name = name
        self.registry = registry
        self.library = library
        self.index = index
        self.root = root
        self.cache_dir = index.directory
        self.source_map = source_map  # materialised relative path -> locator path
        self.extent = extent  # locator path -> characters (text) or pages (PDF)
        self.workdir = workdir
        self.build_seconds = 0.0
        self.prepare_seconds = 0.0
        self.refresh = {}
        self._texts: dict[str, str] = {}
        self._reverse: dict[str, str] = {}
        self._shadows: dict[str, Shadow] = {}
        self._chunk_spans: dict[str, dict[str, tuple[int, int] | None]] = {}
        self.alignment = {'hits': 0, 'unmapped': 0}

    # -- source text in locator coordinates -------------------------------------------------------
    def text(self, path: str) -> str:
        if path not in self._texts:
            if len(self._reverse) != len(self.source_map):
                self._reverse = {original: m for m, original in self.source_map.items()}
            self._texts[path] = (self.root / self._reverse[path]).read_text(encoding='utf-8')
        return self._texts[path]

    def shadow(self, path: str) -> Shadow:
        if path not in self._shadows:
            self._shadows[path] = Shadow(self.text(path))
        return self._shadows[path]

    def covers(self, locator: dict) -> bool:
        size = self.extent.get(locator['path'])
        if size is None:
            return False
        return locator['page'] <= size if 'page' in locator else locator['end'] <= size

    # -- hit -> span ------------------------------------------------------------------------------
    def _map_document(self, document_id: str, path: str) -> dict[str, tuple[int, int] | None]:
        """Align each stored chunk of a text document to source offsets, in chunk order."""
        shadow = self.shadow(path)
        spans, cursor, offset = {}, 0, 0
        while True:
            page = self.index.read(document_id, offset=offset)
            for chunk in page['results']:
                text = chunk['text']
                if chunk.get('truncated'):  # the read budget cut the last chunk of the page: fetch it whole
                    text = self.index.read(document_id, chunk['chunk_id'])['results'][0]['text']
                needle = ' '.join(text.split())
                found = shadow.find(needle, cursor) or shadow.find(needle)
                if found is None:
                    spans[chunk['chunk_id']] = None
                else:
                    spans[chunk['chunk_id']] = shadow.span(*found)
                    cursor = found[0] + 1
            if page['continuation'] is None:
                return spans
            offset = page['continuation']

    def hit_spans(self, hit: dict) -> list[tuple]:
        """`('page', path, n)` or `('span', path, start, end)` for every path the hit's document has."""
        paths = [self.source_map.get(p, p) for p in (hit.get('paths') or [hit.get('path')])]
        locator = hit.get('locator') or {}
        self.alignment['hits'] += 1
        if 'page' in locator:
            return [('page', p, locator['page']) for p in paths]
        for path in paths:
            if path not in self.extent or path.lower().endswith('.pdf'):
                continue
            maps = self._chunk_spans.setdefault(hit['document_id'], {})
            if not maps:
                maps.update(self._map_document(hit['document_id'], path))
            span = maps.get(hit['chunk_id'])
            if span is not None:
                return [('span', path, span[0], span[1])]
        self.alignment['unmapped'] += 1
        return []

    def read_text(self, hit: dict) -> str:
        """Text that `LibraryIndex.read` returns for the hit's locator (the jump-fidelity round trip)."""
        result = self.index.read(hit['document_id'], locator=hit['locator'])
        return '\n'.join(item['text'] for item in result['results'])


def truncate_text(text: str, prefix_chars: int | None) -> str:
    """Keep a prefix cut at a line break, so every offset inside it still addresses the same characters."""
    if prefix_chars and len(text) > prefix_chars:
        return text[: max(text.rfind('\n', 0, prefix_chars), 1)] + '\n'
    return text


def sample_paths(paths: list[str], sample: dict) -> list[str]:
    """The files a `sample` specification keeps, in a stable order (shared by corpus and query builders)."""
    paths = sorted(paths)
    if sample.get('files'):
        paths = [p for p in paths if p in set(sample['files'])]
    if sample.get('every'):
        paths = paths[:: sample['every']]
    return paths


def _copy_text(source: Path, destination: Path, prefix_chars: int | None) -> int:
    text = source.read_text(encoding='utf-8')
    if source.suffix.lower() in {'.html', '.htm'}:
        text = html_text(text)
    text = truncate_text(text, prefix_chars)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding='utf-8')
    return len(text)


def build_corpus(
    name: str,
    source: Path,
    workdir: Path,
    *,
    sample: dict | None = None,
    limits: dict | None = None,
    chunk_tokens: int | None = None,
    retrieval_policy: str = 'ask',
    refresh: bool = True,
) -> Corpus:
    """Materialise `source` (a folder of fixture files) into `workdir` and index it.

    `sample` selects a deterministic subset: `prefix_chars` truncates every text file (offsets inside the
    prefix stay valid), `files` lists relative paths to keep and `every` keeps each n-th file.
    """
    from .libraries import LibraryIndex, LibraryRegistry

    sample = sample or {}
    workdir = Path(workdir)
    root = workdir / name / 'corpus'
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    everything = {p.relative_to(source).as_posix(): p for p in Path(source).rglob('*') if p.is_file()}
    everything = {
        k: v for k, v in everything.items() if not v.name.startswith('.') and v.suffix.lower() != '.xml'
    }
    files = [everything[k] for k in sample_paths(list(everything), sample)]
    source_map, extent = {}, {}
    for path in files:
        relative = path.relative_to(source).as_posix()
        suffix = path.suffix.lower()
        if suffix in {'.html', '.htm'}:
            materialised = relative + '.txt'
        elif suffix == '.xml':
            continue  # JATS XML is a label source, not library content
        else:
            materialised = relative
        if suffix == '.pdf':
            target = root / materialised
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            extent[relative] = len(page_texts(path))
        else:
            extent[relative] = _copy_text(path, root / materialised, sample.get('prefix_chars'))
        source_map[materialised] = relative
    registry = LibraryRegistry(workdir / name / 'config')
    library = registry.create(name, [root], retrieval_policy=retrieval_policy)
    index = LibraryIndex(library, workdir / name / 'cache', limits=limits, chunk_tokens=chunk_tokens)
    corpus = Corpus(name, registry, library, index, root, source_map, extent, workdir)
    if refresh:
        started = time.perf_counter()
        corpus.refresh = index.refresh()
        corpus.build_seconds = time.perf_counter() - started
    return corpus


def reopen_corpus(name: str, workdir: Path, info: dict) -> Corpus:
    """A corpus whose index was built earlier (`scripts/build_scale_index.py`): nothing is parsed again.

    `info` is that script's `scale-info.json` (library entry, cache folder, corpus root, build time).
    """
    from .libraries import LibraryIndex, LibraryRegistry

    root = Path(info['root'])
    registry = LibraryRegistry(Path(workdir) / name / 'config')
    index = LibraryIndex(info['library'], info['cache'], limits={'chunks': 2_000_000})
    source_map = {
        p.relative_to(root).as_posix(): p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
    }
    extent = {rel: (root / rel).stat().st_size for rel in source_map}  # bytes bound the characters from above
    corpus = Corpus(name, registry, info['library'], index, root, source_map, extent, Path(workdir))
    status = index.status()
    corpus.refresh = {'documents': status['documents'], 'chunks': status['chunks']}
    corpus.build_seconds = float(info.get('build_seconds', 0.0))
    return corpus


def build_fixture_corpus(
    library: str,
    workdir: Path,
    *,
    sample: bool | dict = False,
    root: Path | None = None,
    papers_dir: Path | None = None,
    **kwargs,
) -> Corpus:
    """Index one of the five fixture libraries; `sample=True` uses the pytest subset in `SAMPLES`.

    For `papers` pass `papers_dir` (a folder holding the full verified set, see
    `scripts/fetch_eval_fixtures.py --materialize`); by default only the committed PDFs are indexed.
    """
    root = root or fixtures_root()
    if library == 'papers' and papers_dir is not None:
        source = Path(papers_dir)
    else:
        source = root / library / 'corpus'
    spec = (SAMPLES.get(library, {}) if sample is True else sample) or {}
    return build_corpus(library, source, workdir, sample=spec, **kwargs)


SAMPLE_QUERIES = 24  # per library, in sample mode: keeps the pytest run fast


# --------------------------------------------------------------------------------------------------
# Retrieval configurations


class RetrievalConfig:
    """A way of retrieving hits from a built corpus. Subclass, or register a factory, to add one."""

    name = 'config'
    description = ''
    # Slow configurations narrow the fast subset: libraries to run and sample overrides per library.
    sample_libraries: tuple[str, ...] | None = None
    # Score only these query types (a reranker is judged on the hard ones). None scores everything.
    types: tuple[str, ...] | None = None
    samples: ClassVar[dict[str, dict]] = {}  # read-only class data

    def prepare(self, corpus: Corpus) -> dict:
        """One-time setup after the keyword index exists (for example embedding every chunk)."""
        return {}

    def search(self, corpus: Corpus, query: str, limit: int) -> list[dict]:
        raise NotImplementedError

    def describe(self) -> dict:
        return {'name': self.name, 'description': self.description}


class KeywordConfig(RetrievalConfig):
    name = 'keyword'
    description = 'LibraryIndex.search: FTS5 BM25 over line/paragraph chunks (the 0.2.0 engine).'

    def search(self, corpus, query, limit):
        return corpus.index.search(query, limit=min(limit, int(corpus.index.limits['results'])))['results']


class ContextConfig(RetrievalConfig):
    """The route the AI features use: `retrieve_context` over an attached library."""

    name = 'context'
    description = 'retrieval.retrieve_context with the library attached to a blank document (local route).'

    def search(self, corpus, query, limit):
        from .model import new_document
        from .retrieval import retrieve_context

        doc = new_document()
        doc['settings']['libraries'] = [{'library_id': corpus.library['library_id'], 'role': 'supplementary'}]
        result = retrieve_context(
            doc,
            query,
            registry=corpus.registry,
            cache_dir=corpus.cache_dir,
            limit=min(limit, 100),
            max_chars=10**7,
        )
        return result['included']


_STOPWORD_TEXT = (
    'a an and are as at be but by for from had has have he her his i in is it its me my not of on or she so '
    'that the their them they this to was we were what when which who will with you your'
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


class FakeEmbedder:
    """Deterministic stand-in for a model: hashed content-word features, L2-normalised.

    It has no semantics, but it is stable, offline and good enough to exercise the hybrid path (vector
    storage, fusion, invalidation) in CI. `model_identity` changes with `dimension` so vectors invalidate.
    """

    max_input_chars = 1_000_000

    def __init__(self, dimension: int = 64):
        self.dimension = dimension
        self.model_identity = f'eval-hashing-embedder-v1-{dimension}'

    @staticmethod
    def _stem(token: str) -> str:
        for suffix in ('ing', 'ed', 'es', 'ly', 's'):
            if token.endswith(suffix) and len(token) - len(suffix) >= 3:
                return token[: -len(suffix)]
        return token

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in re.findall(r'\w+', text.lower()):
                if token in _STOPWORDS:
                    continue
                digest = zlib.crc32(self._stem(token).encode())
                vector[digest % self.dimension] += -1.0 if (digest >> 16) & 1 else 1.0
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            vectors.append([round(x / norm, 6) for x in vector])
        return vectors


class HybridConfig(RetrievalConfig):
    """`LibraryIndex.hybrid_search` with an injected embedder (keyword + vector, reciprocal-rank fusion)."""

    def __init__(self, embedder=None, name='hybrid-fake'):
        self.embedder = embedder or FakeEmbedder()
        self.name = name
        offline = isinstance(self.embedder, FakeEmbedder)
        self.description = f'hybrid_search with {self.embedder.model_identity}' + (
            ' (hashing function: no model, no network).' if offline else ' (configured embedding provider).'
        )

    def prepare(self, corpus):
        started = time.perf_counter()
        result = corpus.index.rebuild_vectors(provider=self.embedder)
        return {'vectors': result['done'], 'seconds': time.perf_counter() - started}

    def search(self, corpus, query, limit):
        result = corpus.index.hybrid_search(query, limit=min(limit, 100), provider=self.embedder)
        if result.get('embedding_state') != 'ready':
            raise ByaxError('invalid_input', 'Hybrid evaluation requires prepared vectors')
        return result['results']


class LocalModelConfig(HybridConfig):
    """Hybrid search with an on-device embedder from the model catalogue (needs the `local-models` extra
    and the model installed, for example through `BYAX_MODELS_DIR`). Nothing is downloaded here."""

    def __init__(self, model='qwen3-embedding-0.6b-int8', name='hybrid-qwen3', dimension=None):
        from .local_inference import LocalEmbeddingProvider

        super().__init__(LocalEmbeddingProvider(model, dimension=dimension), name=name)
        self.description = f'hybrid_search with the on-device embedder {model} (local ONNX, no network).'

    def prepare(self, corpus):
        if not self.embedder.available():
            raise ByaxError('unsupported_feature', f'On-device model {self.embedder.name} is not installed')
        return super().prepare(corpus)


class RerankedConfig(LocalModelConfig):
    """On-device hybrid search followed by a catalogue reranker (late-interaction ColBERT by default) over the
    top candidates, scored on the query types where ranking order matters most."""

    types = ('thematic', 'continuity')  # noqa: RUF012 - read-only class data

    def __init__(self, reranker='answerai-colbert-small-v1', name='hybrid-qwen3-colbert', depth=40, **kwargs):
        super().__init__(name=name, **kwargs)
        self.reranker_name, self.depth, self._reranker = reranker, depth, None
        self.description += f' Top {depth} candidates reordered by {reranker}.'

    def search(self, corpus, query, limit):
        from .local_inference import load_reranker
        from .retrieval import search_library

        if self._reranker is None:
            self._reranker = load_reranker(self.reranker_name)
        result = search_library(
            corpus.index,
            query,
            method='hybrid',
            limit=min(max(limit, self.depth), 100),
            provider=self.embedder,
            reranker=self._reranker,
        )
        return result['results'][:limit]


def model_hybrid_config(spec: dict, registry=None) -> HybridConfig:
    """Hybrid retrieval with a configured embedding provider (`{'name': 'hybrid-model', 'provider': alias,
    'dimension': n}`); used by the opt-in `eval_models` tests. A remote provider needs `authorized: true`,
    because the fixture passages are uploaded to it."""
    from .embeddings import ConfiguredEmbeddingProvider

    if not spec.get('provider') or not spec.get('dimension'):
        raise ByaxError('invalid_input', "'hybrid-model' needs 'provider' (an alias) and 'dimension'")
    provider = ConfiguredEmbeddingProvider(
        {'provider': spec['provider'], 'dimension': int(spec['dimension'])},
        registry=registry,
        authorized=bool(spec.get('authorized')),
    )
    return HybridConfig(provider, name=spec.get('label', 'hybrid-model'))


class CallableConfig(RetrievalConfig):
    def __init__(self, function: Callable, name: str = 'callable'):
        self.function, self.name = function, name
        self.description = 'caller-supplied function(corpus, query, limit) -> list of hits'

    def search(self, corpus, query, limit):
        return self.function(corpus, query, limit)


CONFIGS: dict[str, Callable[[], RetrievalConfig]] = {
    'keyword': KeywordConfig,
    'context': ContextConfig,
    'hybrid-fake': HybridConfig,
    'hybrid-model': lambda: model_hybrid_config(
        {}
    ),  # needs provider/dimension: pass a dict to resolve_config
}


def register_config(name: str, factory: Callable[[], RetrievalConfig]) -> None:
    """Make a configuration selectable by name (later phases register theirs here)."""
    CONFIGS[name] = factory


def resolve_config(spec) -> RetrievalConfig:
    """Accept a name, a `{'name': ...}` / `{'kind': ...}` dict, a RetrievalConfig, or a callable."""
    if isinstance(spec, RetrievalConfig):
        return spec
    if isinstance(spec, str):
        spec = {'name': spec}
    if isinstance(spec, dict):
        name = spec.get('name') or spec.get('kind')
        if name not in CONFIGS:
            raise ByaxError(
                'invalid_input', f'Unknown retrieval configuration {name!r}', known=sorted(CONFIGS)
            )
        if name == 'hybrid-model':
            return model_hybrid_config(spec)
        config = CONFIGS[name]()
        if name == 'hybrid-fake' and spec.get('dimension'):
            config = HybridConfig(FakeEmbedder(int(spec['dimension'])), name=spec.get('label', name))
        return config
    if callable(spec):
        return CallableConfig(spec, getattr(spec, '__name__', 'callable'))
    raise ByaxError(
        'invalid_input', 'Retrieval configuration must be a name, dict, config object or callable'
    )


def sample_extents(library: str, *, spec: dict | None = None, root: Path | None = None) -> dict[str, int]:
    """Locator extents (characters or pages) a (sampled) library build would hold, without indexing it."""
    root = root or fixtures_root()
    source = root / library / 'corpus'
    spec = SAMPLES.get(library, {}) if spec is None else spec
    files = {
        p.relative_to(source).as_posix(): p for p in source.rglob('*') if p.is_file() and p.suffix != '.xml'
    }
    extents = {}
    for relative in sample_paths(list(files), spec):
        path = files[relative]
        if path.suffix.lower() == '.pdf':
            extents[relative] = len(page_texts(path))
            continue
        text = path.read_text(encoding='utf-8')
        if path.suffix.lower() in {'.html', '.htm'}:
            text = html_text(text)
        extents[relative] = len(truncate_text(text, spec.get('prefix_chars')))
    return extents


def covered_queries(queries: list[dict], extents: dict[str, int]) -> list[dict]:
    """Queries whose every locator lies inside `extents` (expected-absent queries always qualify)."""

    def inside(locator):
        size = extents.get(locator['path'])
        return size is not None and (locator['page'] <= size if 'page' in locator else locator['end'] <= size)

    return [q for q in queries if all(inside(x) for x in q['relevant_chunk_locators'])]


def run_library(
    library: str,
    config,
    workdir: Path,
    *,
    sample: bool = False,
    papers_dir: Path | None = None,
    root: Path | None = None,
    chunk_tokens: int | None = None,
    thin: bool = True,
) -> dict:
    """Build one fixture library and score `config` on its golden queries (the harness in one call)."""
    config = resolve_config(config)
    spec: bool | dict = False
    if sample:
        spec = config.samples.get(library, SAMPLES.get(library, {}))
    corpus = build_fixture_corpus(
        library, workdir, sample=spec, root=root, papers_dir=papers_dir, chunk_tokens=chunk_tokens
    )
    return evaluate(
        corpus, load_queries(library, root), config, max_queries=SAMPLE_QUERIES if sample and thin else None
    )


def libraries_for(config, *, sample: bool) -> tuple[str, ...]:
    config = resolve_config(config)
    return (config.sample_libraries if sample and config.sample_libraries else None) or FIXTURE_LIBRARIES


# --------------------------------------------------------------------------------------------------
# Metrics


def overlaps(span: tuple, locator: dict) -> bool:
    if span[1] != locator['path']:
        return False
    if span[0] == 'page':
        return locator.get('page') == span[2]
    if 'start' not in locator:
        return False
    return span[2] < locator['end'] and locator['start'] < span[3]


def score_hits(hit_spans: list[list[tuple]], locators: list[dict]) -> dict[str, float]:
    """Metrics for one ranked list. `hit_spans[i]` is the span set of the hit at rank i+1."""
    first = [None] * len(locators)  # best (lowest) rank at which each locator is covered
    new_gain_ranks = []
    for rank, spans in enumerate(hit_spans, 1):
        gained = False
        for slot, locator in enumerate(locators):
            if first[slot] is None and any(overlaps(span, locator) for span in spans):
                first[slot] = rank
                gained = True
        if gained:
            new_gain_ranks.append(rank)
    total = len(locators)

    def recall(k):
        return sum(1 for r in first if r is not None and r <= k) / total

    dcg = sum(1 / math.log2(rank + 1) for rank in new_gain_ranks if rank <= 10)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(total, 10) + 1))
    reciprocal = 1 / min(r for r in first if r is not None) if any(r is not None for r in first) else 0.0
    return {'recall@5': recall(5), 'recall@20': recall(20), 'ndcg@10': dcg / ideal, 'mrr': reciprocal}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)] if ordered else 0.0


def _mean(rows: Iterable[dict], key: str) -> float:
    rows = list(rows)
    return round(sum(r[key] for r in rows) / len(rows), 4) if rows else 0.0


def index_size_bytes(corpus: Corpus) -> int:
    """Bytes of the derived SQLite index (checkpointed), including embeddings stored in it."""
    path = Path(corpus.index.path)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return sum(
        p.stat().st_size for p in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm')) if p.exists()
    )


def run_queries(
    corpus: Corpus, queries: list[dict], config: RetrievalConfig, *, depth: int = DEPTH
) -> list[dict]:
    """Run each query once (after a warm-up) and return per-query rows with hits, spans and timing."""
    if queries:
        config.search(corpus, queries[0]['query'], depth)  # warm caches; not timed
    rows = []
    for record in queries:
        started, cpu_started = time.perf_counter(), time.process_time()
        hits = config.search(corpus, record['query'], depth)[:depth]
        elapsed = (time.perf_counter() - started) * 1000
        cpu = (time.process_time() - cpu_started) * 1000
        rows.append({'record': record, 'hits': hits, 'ms': elapsed, 'cpu_ms': cpu})
    return rows


def thin_queries(queries: list[dict], limit: int) -> list[dict]:
    """Deterministic, type-balanced subset of at most `limit` queries (evenly spaced within each type)."""
    if len(queries) <= limit:
        return list(queries)
    groups: dict[str, list[dict]] = {}
    for record in queries:
        groups.setdefault(record['type'], []).append(record)
    quota = max(1, limit // len(groups))
    chosen: list[dict] = []
    for kind in sorted(groups):
        items = groups[kind]
        take = min(quota, len(items))
        chosen += [items[int(i * len(items) / take)] for i in range(take)]
    chosen += [q for q in queries if q not in chosen][: limit - len(chosen)]
    return sorted(chosen, key=lambda q: q['id'])


def evaluate(
    corpus: Corpus,
    queries: list[dict],
    config,
    *,
    prepared: dict | None = None,
    depth: int = DEPTH,
    max_queries: int | None = None,
) -> dict:
    """Score `config` on the queries that this corpus can answer; returns a JSON-serialisable report.

    `max_queries` thins the applicable queries (see `thin_queries`) so CI stays fast.
    """
    config = resolve_config(config)
    covered = [
        q
        for q in queries
        if all(corpus.covers(loc) for loc in q['relevant_chunk_locators'])
        and (config.types is None or q['type'] in config.types)
    ]
    applicable = thin_queries(covered, max_queries) if max_queries else covered
    if prepared is None:
        started = time.perf_counter()
        prepared = config.prepare(corpus)
        corpus.prepare_seconds = time.perf_counter() - started
    rows = run_queries(corpus, applicable, config, depth=depth)
    scored, absent, per_type = [], [], {}
    for row in rows:
        record = row['record']
        if record.get('expected_absent'):
            absent.append({'type': record['type'], 'returned': len(row['hits']), 'id': record['id']})
            continue
        spans = [corpus.hit_spans(hit) for hit in row['hits']]
        metrics = score_hits(spans, record['relevant_chunk_locators'])
        entry = {'id': record['id'], 'type': record['type'], **metrics}
        scored.append(entry)
        per_type.setdefault(record['type'], []).append(entry)
    latencies = [row['ms'] for row in rows]
    cpu_times = [row['cpu_ms'] for row in rows]
    literal = [r for r in scored if r['type'] == 'absence_probe']
    report = {
        'library': corpus.name,
        'config': config.describe(),
        'queries': {
            'total': len(queries),
            'covered': len(covered),
            'evaluated': len(applicable),
            'skipped_not_in_corpus': len(queries) - len(covered),
            'scored': len(scored),
            'expected_absent': len(absent),
        },
        'metrics': {key: _mean(scored, key) for key in METRICS},
        'by_type': {
            kind: {'queries': len(entries), **{key: _mean(entries, key) for key in METRICS}}
            for kind, entries in sorted(per_type.items())
        },
        'absence': {
            'literal_probes': len(literal),
            'literal_found_at_20': sum(1 for r in literal if r['recall@20'] > 0),
            'absent_probes': len(absent),
            'absent_returned_nothing': sum(1 for r in absent if r['returned'] == 0),
            'absent_mean_results': round(sum(r['returned'] for r in absent) / len(absent), 2)
            if absent
            else 0.0,
        },
        'latency_ms': {
            'p50': round(percentile(latencies, 0.5), 3),
            'p95': round(percentile(latencies, 0.95), 3),
            'mean': round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            'max': round(max(latencies), 3) if latencies else 0.0,
            'samples': len(latencies),
        },
        # Process CPU time of the same calls: stays meaningful when other jobs share the host.
        'latency_cpu_ms': {
            'p50': round(percentile(cpu_times, 0.5), 3),
            'p95': round(percentile(cpu_times, 0.95), 3),
            'mean': round(sum(cpu_times) / len(cpu_times), 3) if cpu_times else 0.0,
        },
        'index': {
            'documents': corpus.refresh.get('documents'),
            'chunks': corpus.refresh.get('chunks'),
            'bytes': index_size_bytes(corpus),
            'build_seconds': round(corpus.build_seconds, 3),
            'prepare_seconds': round(corpus.prepare_seconds, 3),
        },
        'alignment': dict(corpus.alignment),
        'worst': sorted(scored, key=lambda r: (r['recall@20'], r['mrr'], r['id']))[:5],
        'per_query': [{**entry, **{k: round(entry[k], 4) for k in METRICS}} for entry in scored]
        + [
            {'id': a['id'], 'type': a['type'], 'expected_absent': True, 'returned': a['returned']}
            for a in absent
        ],
    }
    report['alignment']['unmapped_rate'] = round(
        report['alignment']['unmapped'] / max(1, report['alignment']['hits']), 4
    )
    return report


def environment() -> dict:
    """Hardware and software identity, in the shape of the layout benchmarks in `docs/benchmarks/`."""
    from . import __version__

    cpu = platform.processor()
    memory = None
    if Path('/proc/cpuinfo').exists():
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                cpu = line.partition(':')[2].strip()
                break
    if Path('/proc/meminfo').exists():
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal'):
                memory = round(int(line.split()[1]) / 1024**2, 1)
                break
    return {
        'platform': platform.platform(),
        'machine': platform.machine(),
        'processor': cpu or 'Unavailable',
        'logical_cpus': os.cpu_count(),
        'memory_gib': memory,
        'python': platform.python_version(),
        'sqlite': sqlite3.sqlite_version,
        'engine': __version__,
        'load_average_1m_5m_15m': [round(x, 2) for x in os.getloadavg()]
        if hasattr(os, 'getloadavg')
        else None,
        'hardware_qualification': 'Unqualified execution host; CPU/SSD target hardware not verified.',
    }


def macro_average(reports: list[dict]) -> dict:
    return {key: _mean((r['metrics'] for r in reports), key) for key in METRICS} if reports else {}


# --------------------------------------------------------------------------------------------------
# Label verification (used by the query builder and by tests/test_context_fidelity.py)


def locator_text(
    corpus_dir: Path,
    locator: dict,
    *,
    pages: dict[str, list[str]] | None = None,
    texts: dict[str, str] | None = None,
) -> str:
    """The source text a locator points at, in the coordinates the labels use.

    `pages` and `texts` are optional caches (path to page list / full text) that this function fills.
    """
    path = corpus_dir / locator['path']
    if 'page' in locator:
        pages = {} if pages is None else pages
        if locator['path'] not in pages:
            pages[locator['path']] = page_texts(path)
        return pages[locator['path']][locator['page'] - 1]
    texts = {} if texts is None else texts
    if locator['path'] not in texts:
        raw = path.read_text(encoding='utf-8')
        texts[locator['path']] = html_text(raw) if path.suffix.lower() in {'.html', '.htm'} else raw
    return texts[locator['path']][locator['start'] : locator['end']]


def label_problems(
    record: dict,
    corpus_dir: Path,
    *,
    pages: dict[str, list[str]] | None = None,
    texts: dict[str, str] | None = None,
) -> list[str]:
    """Check that every locator exists and still contains its recorded `quote` (whitespace-insensitive)."""
    problems = []
    for locator in record['relevant_chunk_locators']:
        try:
            text = locator_text(corpus_dir, locator, pages=pages, texts=texts)
        except (OSError, IndexError, KeyError, ValueError) as exc:
            problems.append(f'{record["id"]}: {locator["path"]} unreadable ({exc})')
            continue
        quote = locator.get('quote')
        if quote is None:
            continue
        exact = ' '.join(quote.split()).casefold() in ' '.join(text.split()).casefold()
        # Page labels derived from JATS text only match the extracted PDF text once hyphenation is undone.
        if not exact and not ('page' in locator and squash(quote) in squash(text)):
            where = locator.get('page', (locator.get('start'), locator.get('end')))
            problems.append(f'{record["id"]}: quote not in {locator["path"]} {where}')
    return problems


# --------------------------------------------------------------------------------------------------
# Quote verification (context engine plan 5.3): pass rates and the near-match threshold


def genuine_quote_cases(
    corpus_dir: Path, queries: list[dict], pages: dict[str, list[str]] | None = None
) -> list[dict]:
    """Genuine quotes whose wording comes from outside the extracted text (JATS) with the page they cite.

    Each case is ``{quote, source}``: ``source`` is the text of the cited PDF page, the setting where raw
    comparison fails (line-break hyphenation, ligatures) and normalisation has to rescue the quote.
    """
    cases, cache = [], {} if pages is None else pages
    for record in queries:
        for locator in record['relevant_chunk_locators']:
            if 'page' not in locator or 'quote' not in locator:
                continue
            try:
                source = locator_text(corpus_dir, locator, pages=cache)
            except (OSError, IndexError):
                continue
            cases.append({'id': record['id'], 'quote': locator['quote'], 'source': source})
    return cases


def quote_pass_rates(cases: list[dict]) -> dict:
    """Raw versus normalised pass rate of ``quote_match.locate`` over genuine quotes (the Phase 1 exit figure)."""
    from . import quote_match

    counts = {'exact': 0, 'normalized': 0, 'near': 0, 'none': 0}
    for case in cases:
        counts[quote_match.locate(case['quote'], case['source'])['match']] += 1
    total = max(1, len(cases))
    return {
        'quotes': len(cases),
        'by_match': counts,
        'raw_pass_rate': round(counts['exact'] / total, 4),
        'normalized_pass_rate': round((counts['exact'] + counts['normalized']) / total, 4),
        'unverified': counts['near'] + counts['none'],
    }


def _typo(rng, text: str) -> str:
    """One to three keyboard-level slips: substitution, deletion or a swapped pair, away from the edges."""
    letters = list(text)
    for _ in range(rng.choice((1, 1, 2, 3))):
        spots = [i for i in range(3, len(letters) - 4) if letters[i].isalpha() and letters[i + 1].isalpha()]
        i = rng.choice(spots)
        kind = rng.choice(('sub', 'del', 'swap'))
        if kind == 'sub':
            letters[i] = rng.choice('abcdefghijklmnopqrstuvwxyz'.replace(letters[i].lower(), ''))
        elif kind == 'del':
            del letters[i]
        else:
            letters[i], letters[i + 1] = letters[i + 1], letters[i]
    return ''.join(letters)


QUOTE_KINDS = ('noisy', 'reworded', 'spliced', 'shuffled', 'neighbour')


def altered_quote_cases(sources: list[str], seed: int = 20261016, per_kind: int = 120) -> list[dict]:
    """Quotes labelled by what the verifier should say about them against their source passage.

    ``noisy`` quotes are the passage's own words with a few typing or OCR slips (a near match is right);
    the other kinds are not quotations of that passage and must never be accepted: ``reworded`` (two content
    words changed), ``spliced`` (halves of two sentences), ``shuffled`` (same words, new order) and
    ``neighbour`` (a real sentence from a different passage).
    """
    import random

    rng = random.Random(seed)
    sentence = re.compile(r'[^.!?\n]{50,160}[.!?]')

    def pick(text):
        found = [m.group().strip() for m in sentence.finditer(' '.join(text.split()))]
        return [s for s in found if len(s.split()) >= 9]

    pools = [(source, pick(source)) for source in sources]
    pools = [(s, p) for s, p in pools if len(p) >= 2]
    vocabulary = sorted({w for _, options in pools for s in options for w in s.split() if len(w) > 5})
    cases = []
    for kind in QUOTE_KINDS:
        made = 0
        while made < per_kind:
            source, options = rng.choice(pools)
            base = rng.choice(options)
            words = base.split()
            if kind == 'noisy':
                quote = _typo(rng, base)
            elif kind == 'reworded':
                slots = rng.sample([i for i, w in enumerate(words) if len(w) > 4], 2)
                for slot in slots:
                    words[slot] = rng.choice(vocabulary)
                quote = ' '.join(words)
            elif kind == 'spliced':
                other = rng.choice(options).split()
                quote = ' '.join(words[: len(words) // 2] + other[len(other) // 2 :])
            elif kind == 'shuffled':
                head, tail = words[: len(words) // 2], words[len(words) // 2 :]
                rng.shuffle(head)
                quote = ' '.join(head + tail)
            else:
                foreign = [o for s, p in pools if s is not source for o in p]
                quote = rng.choice(foreign)
            if quote == base or (kind != 'noisy' and quote in ' '.join(source.split())):
                continue
            cases.append({'kind': kind, 'quote': quote, 'source': source, 'base': base})
            made += 1
    return cases


def sweep_near_threshold(cases: list[dict], thresholds: list[float]) -> list[dict]:
    """Precision and recall of a verdict at each ``near`` threshold.

    Positives are ``noisy`` quotes (the span must overlap the sentence they were made from); anything else
    that is reported as a match is a false positive. Precision comes first: a wrong "this is the quote" is
    the misattribution failure the plan exists to prevent.
    """
    from . import quote_match

    rows = []
    for threshold in thresholds:
        found = dict.fromkeys(QUOTE_KINDS, 0)
        totals = dict.fromkeys(QUOTE_KINDS, 0)
        for case in cases:
            result = quote_match.locate(case['quote'], case['source'], threshold=threshold)
            totals[case['kind']] += 1
            if result['match'] == 'none':
                continue
            if case['kind'] != 'noisy':
                found[case['kind']] += 1
                continue
            start = ' '.join(case['source'].split()).find(case['base'][:25])
            span = result['span']
            # Spans index the original source: accept any overlap with the sentence it was made from.
            at = case['source'].find(case['base'][:25])
            lo = at if at >= 0 else max(0, start)
            if span and span[0] < lo + len(case['base']) + 20 and lo - 20 < span[1]:
                found['noisy'] += 1
        false_positives = sum(v for k, v in found.items() if k != 'noisy')
        negatives = sum(v for k, v in totals.items() if k != 'noisy')
        reported = found['noisy'] + false_positives
        rows.append(
            {
                'threshold': threshold,
                'precision': round(found['noisy'] / reported, 4) if reported else 1.0,
                'recall': round(found['noisy'] / max(1, totals['noisy']), 4),
                'false_positive_rate': round(false_positives / max(1, negatives), 4),
                'false_positives_by_kind': {k: v for k, v in found.items() if k != 'noisy'},
            }
        )
    return rows
