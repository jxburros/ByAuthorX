"""Opt-in embeddings, with model-identified vectors and independent upload consent.

Vectors live in :mod:`byax.vector_store`, keyed by a model signature (provider, model,
dimension and chunking) and by each chunk's fingerprint. Rebuilds are incremental: an
unchanged chunk under an unchanged signature is skipped. Vectors from another signature
are stale; they are discarded by the next rebuild and never compared with current ones.
Coverage is always reported, so a partial vector set is used visibly, not as ``ready``.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Protocol

from .errors import ByaxError

DEFAULT_BATCH = 32
MAXIMUM_BATCH = 2048
RETRIES = 3


class EmbeddingProvider(Protocol):
    model_identity: str
    dimension: int
    max_input_chars: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def validate_config(config):
    if not isinstance(config, dict) or set(config) - {
        'provider',
        'dimension',
        'max_input_chars',
        'max_batch',
    }:
        raise ByaxError(
            'invalid_input',
            'Embedding settings require provider, dimension and optional max_input_chars and max_batch',
        )
    if not isinstance(config.get('provider'), str) or not config['provider'].strip():
        raise ByaxError('invalid_input', 'Embedding provider alias is required')
    if type(config.get('dimension')) is not int or not 1 <= config['dimension'] <= 65536:
        raise ByaxError('invalid_input', 'Embedding dimension must be between 1 and 65536')
    maximum = config.get('max_input_chars', 8000)
    if type(maximum) is not int or not 1 <= maximum <= 1000000:
        raise ByaxError('invalid_input', 'Invalid embedding input limit')
    batch = config.get('max_batch', DEFAULT_BATCH)
    if type(batch) is not int or not 1 <= batch <= MAXIMUM_BATCH:
        raise ByaxError('invalid_input', f'Embedding batch size must be between 1 and {MAXIMUM_BATCH}')
    return {**config, 'max_input_chars': maximum, 'max_batch': batch}


class ConfiguredEmbeddingProvider:
    def __init__(self, settings, *, registry=None, authorized=False, local_only=False):
        from .providers import _ENDPOINTS, ProviderRegistry, _loopback
        from .retrieval import ConsentRegistry

        settings = validate_config(settings)
        self.registry = registry or ProviderRegistry()
        self.config = self.registry.get(settings['provider'])
        if self.config.kind not in {'openai', 'ollama'}:
            raise ByaxError(
                'unsupported_feature', 'Embeddings support Ollama and OpenAI-compatible providers'
            )
        self.remote = not _loopback(self.config.endpoint or _ENDPOINTS[self.config.kind])
        self.authorized = authorized
        self.local_only = local_only
        self.consent = ConsentRegistry(self.registry.config_dir)
        self.dimension = settings['dimension']
        self.max_input_chars = settings['max_input_chars']
        self.max_batch = settings['max_batch']
        self.model_identity = json.dumps(
            [self.config.kind, self.config.endpoint, self.config.model, self.dimension]
        )

    def authorize(self):
        """Raise unless text may be sent to this embedder under current consent."""
        if self.remote:
            if self.local_only:
                raise ByaxError('authorization_required', 'Local-only library cannot use remote embeddings')
            if not self.authorized and not self.consent.allows(self.config.alias, 'library_embeddings'):
                raise ByaxError('authorization_required', 'Separate embedding upload consent is required')

    def embed(self, texts):
        self.authorize()
        if any(not isinstance(text, str) or len(text) > self.max_input_chars for text in texts):
            raise ByaxError('limit_exceeded', 'Embedding text exceeds configured model input limit')
        if self.config.kind == 'ollama':
            raw = self.registry._request(
                self.config,
                'POST',
                '/api/embed',
                json={'model': self.config.model, 'input': texts, 'truncate': False},
            )
            vectors = raw.get('embeddings')
        else:
            raw = self.registry._request(
                self.config, 'POST', '/embeddings', json={'model': self.config.model, 'input': texts}
            )
            vectors = [
                item.get('embedding') for item in sorted(raw.get('data', []), key=lambda item: item['index'])
            ]
        validate_vectors(vectors, len(texts), self.dimension)
        return vectors


def validate_vectors(vectors, count, dimension):
    if (
        not isinstance(vectors, list)
        or len(vectors) != count
        or any(
            not isinstance(vector, list)
            or len(vector) != dimension
            or any(type(number) not in {int, float} or not math.isfinite(number) for number in vector)
            for vector in vectors
        )
    ):
        raise ByaxError('provider_failure', 'Embedding provider returned invalid vectors')


def fingerprint(provider, chunking):
    """The model signature: vectors with different signatures are never compared."""
    return hashlib.sha256(
        json.dumps([provider.model_identity, provider.dimension, chunking], sort_keys=True).encode()
    ).hexdigest()


model_signature = fingerprint


def cosine(first, second):
    denominator = math.sqrt(sum(x * x for x in first) * sum(x * x for x in second))
    return sum(a * b for a, b in zip(first, second, strict=True)) / denominator if denominator else 0.0


def _library_provider(index, *, authorized=False):
    return ConfiguredEmbeddingProvider(
        index.library['embedding'],
        authorized=authorized,
        local_only=index.library.get('retrieval_policy') == 'local_only',
    )


def _state(coverage):
    if coverage['total'] and not coverage['embedded']:
        return 'stale' if coverage['stale_vectors'] else 'rebuild_required'
    if coverage['embedded'] < coverage['total']:
        return 'partial'
    return 'ready'


def embedding_state(index, provider=None):
    """State and coverage for the library's current model signature, without any request.

    States: ``disabled``, ``provider_unavailable``, ``rebuild_required`` (no vectors),
    ``stale`` (only vectors from another model or chunking), ``partial`` and ``ready``.
    """
    from .libraries import CHUNKING
    from .vector_store import open_store

    if provider is None:
        if not index.library.get('embedding'):
            return {'state': 'disabled'}
        try:
            provider = ConfiguredEmbeddingProvider(index.library['embedding'])
        except (ByaxError, OSError):
            return {'state': 'provider_unavailable'}
    signature = fingerprint(provider, CHUNKING)
    coverage = open_store(index).coverage(signature)
    return {'state': _state(coverage), 'model_signature': signature, **coverage}


def index_embedding_state(index):
    """Read-only model compatibility check; constructing the adapter makes no request."""
    try:
        return embedding_state(index)['state']
    except (ByaxError, OSError):
        return 'provider_unavailable'


def _passages(index):
    with index._db() as db:
        rows = [
            dict(row)
            for row in db.execute('SELECT chunk_id,document_id,text,fingerprint FROM chunks ORDER BY rowid')
            if any(not p['stale'] for p in index._paths(db, row['document_id']))
        ]
    for row in rows:
        row['fingerprint'] = row['fingerprint'] or ''
    return rows


def embedding_preview(index, provider=None):
    """Exactly what the next rebuild would send: changed or new passages only."""
    from .libraries import CHUNKING
    from .vector_store import open_store

    provider = provider or _library_provider(index)
    signature = fingerprint(provider, CHUNKING)
    current = open_store(index).fingerprints(signature)
    rows = _passages(index)
    pending = [r for r in rows if not r['fingerprint'] or current.get(r['chunk_id']) != r['fingerprint']]
    return {
        'provider': getattr(getattr(provider, 'config', None), 'alias', provider.model_identity),
        'remote': getattr(provider, 'remote', False),
        'locality': 'remote' if getattr(provider, 'remote', False) else 'local',
        'documents': len({r['document_id'] for r in pending}),
        'characters': sum(len(r['text']) for r in pending),
        'passages': pending,
        'skipped': len(rows) - len(pending),
        'model_fingerprint': signature,
        'notice': 'One-time upload of these passages, plus explicit later rebuilds after changes.',
    }


def _retryable(exc):
    return exc.code == 'rate_limited' or (
        exc.code == 'provider_failure' and exc.details.get('request_sent') is False
    )


def _wait(seconds, cancel_event, sleep):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            raise ByaxError('cancelled', 'Embedding rebuild cancelled')
        sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _embed_batch(provider, texts, cancel_event, sleep, retries):
    """Retry only failures known not to have reached the provider; uncertain sends are not repeated."""
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            return provider.embed(texts)
        except ByaxError as exc:
            if attempt == retries or not _retryable(exc):
                raise
        _wait(delay, cancel_event, sleep)
        delay *= 2
    raise AssertionError('unreachable')


def rebuild_vectors(
    index,
    *,
    authorized=False,
    provider=None,
    cancel_event=None,
    progress=None,
    sleep=time.sleep,
    retries=RETRIES,
):
    """One incremental rebuild: reports ``done``, ``skipped`` and ``failed`` chunk counts."""
    from .vector_store import open_store

    provider = provider or _library_provider(index, authorized=authorized)
    preview = embedding_preview(index, provider)
    signature = preview['model_fingerprint']
    store = open_store(index)
    discarded = store.discard_other_signatures(signature)
    store.prune()
    limit = int(getattr(provider, 'max_input_chars', 8000))
    batch_size = max(1, min(int(getattr(provider, 'max_batch', DEFAULT_BATCH)), MAXIMUM_BATCH))
    queue = [p for p in preview['passages'] if len(p['text']) <= limit]
    errors = [
        {'chunk_id': p['chunk_id'], 'reason': 'Passage exceeds the model input limit'}
        for p in preview['passages']
        if len(p['text']) > limit
    ]
    failed, done, cancelled = len(errors), 0, False
    total = len(preview['passages']) + preview['skipped']
    for start in range(0, len(queue), batch_size):
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        batch = queue[start : start + batch_size]
        try:
            vectors = _embed_batch(provider, [p['text'] for p in batch], cancel_event, sleep, retries)
            validate_vectors(vectors, len(batch), provider.dimension)
        except ByaxError as exc:
            if exc.code == 'cancelled':
                cancelled = True
                break
            if exc.code == 'authorization_required':
                raise
            failed += len(batch)
            errors.append(
                {'chunk_ids': [p['chunk_id'] for p in batch], 'code': exc.code, 'reason': exc.message}
            )
            continue
        store.upsert(
            signature, [(p['chunk_id'], p['fingerprint'], v) for p, v in zip(batch, vectors, strict=True)]
        )
        done += len(batch)
        if progress:
            progress((done + failed + preview['skipped']) / max(1, total))
    coverage = store.coverage(signature)
    return {
        'done': done,
        'skipped': preview['skipped'],
        'failed': failed,
        'total': total,
        'processed': done,
        'batch_size': batch_size,
        'discarded_stale': discarded,
        'model_fingerprint': signature,
        'cancelled': cancelled,
        'errors': errors[:20],
        'coverage': coverage['ratio'],
        'embedding_state': _state(coverage),
    }


def _notice(info):
    if info['state'] == 'partial':
        return (
            f'Semantic search covers {info["embedded"]} of {info["total"]} passages '
            f'({info["ratio"]:.0%}); rebuild embeddings to include the rest.'
        )
    if info['state'] in ('stale', 'rebuild_required'):
        return 'Embeddings must be rebuilt for the current model; showing keyword results only.'
    return None


def hybrid_search(index, query, *, limit=20, offset=0, authorized=False, provider=None, expand=False):
    """Keyword and dense rankings fused with RRF. Partial vector sets are used and disclosed."""
    from .libraries import CHUNKING
    from .ranking import Hit, rrf
    from .search_query import Query
    from .search_query import parse as parse_query
    from .vector_store import open_store

    if (
        type(limit) is not int
        or not 1 <= limit <= index.limits['results']
        or type(offset) is not int
        or offset < 0
    ):
        raise ByaxError('invalid_input', 'Invalid hybrid search limit or offset')
    provider = provider or _library_provider(index, authorized=authorized)
    info = embedding_state(index, provider)
    if info['state'] not in ('ready', 'partial'):
        page = index.search(query, limit=limit, offset=offset, expand=expand)
        return {
            **page,
            'method': 'keyword',
            'embedding_state': info['state'],
            'coverage': info.get('ratio', 0.0),
            'notice': _notice(info),
        }
    parsed = query if isinstance(query, Query) else parse_query(query)
    keyword = index.search(parsed, limit=int(index.limits['results']), expand=expand)
    text = parsed.semantic_text()[: provider.max_input_chars]
    dense = []
    if text:
        query_vector = provider.embed([text])[0]
        validate_vectors([query_vector], 1, provider.dimension)
        signature = fingerprint(provider, CHUNKING)
        ranked = open_store(index).search(signature, query_vector, limit=int(index.limits['results']))
        similarity = dict(ranked)
        with index._db() as db:
            rows = {}
            for chunk_id, _ in ranked:
                row = db.execute(
                    'SELECT c.*,d.content_hash FROM chunks c JOIN documents d USING(document_id) WHERE chunk_id=?',
                    (chunk_id,),
                ).fetchone()
                if row:
                    rows[chunk_id] = dict(row)
            paths_cache = {}
            for chunk_id, _ in ranked:
                row = rows.get(chunk_id)
                if row is None:
                    continue
                if row['document_id'] not in paths_cache:
                    paths_cache[row['document_id']] = index._paths(db, row['document_id'])
                paths = paths_cache[row['document_id']]
                if not paths or all(p['stale'] for p in paths):
                    continue
                row.update(
                    locator=json.loads(row['locator']),
                    library_id=index.library['library_id'],
                    path=paths[0]['path'],
                    paths=[p['path'] for p in paths],
                    stale=False,
                    truncated=False,
                    match='dense',
                    similarity=round(similarity[chunk_id], 6),
                )
                if parsed.accepts(row) and not parsed.excludes(row['text']):
                    dense.append(Hit.from_result(row, method='dense'))
    fused = rrf([keyword['results'], dense], names=['keyword', 'dense'])
    result = []
    remaining = int(index.limits['context_chars'])
    for hit in fused[offset : offset + limit]:
        if remaining <= 0:
            break
        item = hit.to_dict()
        item['truncated'] = len(item['text']) > remaining
        item['text'] = item['text'][:remaining]
        item['score'] = item['fused_score']
        item['method'] = 'hybrid'
        remaining -= len(item['text'])
        result.append(item)
    more = offset + len(result) < len(fused)
    response = {
        **{k: v for k, v in keyword.items() if k in ('query', 'suggestions', 'passes')},
        'results': result,
        'total': len(fused),
        'offset': offset,
        'truncated': more,
        'continuation': offset + len(result) if more else None,
        'embedding_state': info['state'],
        'coverage': info['ratio'],
        'method': 'hybrid',
        'fusion': {'method': 'rrf', 'k': 60, 'lists': ['keyword', 'dense']},
    }
    notice = _notice(info)
    if notice:
        response['notice'] = notice
    return response


def embedder_locality(library, registry=None):
    """Where a library's embedder sends text: ``None`` when embeddings are off, else a label dict.

    Reads the saved provider configuration only; it makes no request and builds no adapter.
    """
    settings = library.get('embedding')
    if not settings:
        return None
    from .providers import ProviderRegistry, provider_locality

    try:
        info = provider_locality((registry or ProviderRegistry()).get(settings['provider']))
    except (ByaxError, KeyError, OSError):
        return {
            'provider': settings.get('provider'),
            'locality': 'unknown',
            'label': 'Provider not configured',
        }
    return {'provider': settings['provider'], **info}
