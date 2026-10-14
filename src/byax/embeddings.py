"""Opt-in embeddings, with model-identified vectors and independent upload consent."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Protocol

from .errors import ByaxError


class EmbeddingProvider(Protocol):
    model_identity: str
    dimension: int
    max_input_chars: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def validate_config(config):
    if not isinstance(config, dict) or set(config) - {'provider', 'dimension', 'max_input_chars'}:
        raise ByaxError(
            'invalid_input', 'Embedding settings require provider, dimension and optional max_input_chars'
        )
    if not isinstance(config.get('provider'), str) or not config['provider'].strip():
        raise ByaxError('invalid_input', 'Embedding provider alias is required')
    if type(config.get('dimension')) is not int or not 1 <= config['dimension'] <= 65536:
        raise ByaxError('invalid_input', 'Embedding dimension must be between 1 and 65536')
    maximum = config.get('max_input_chars', 8000)
    if type(maximum) is not int or not 1 <= maximum <= 1000000:
        raise ByaxError('invalid_input', 'Invalid embedding input limit')
    return {**config, 'max_input_chars': maximum}


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
        self.model_identity = json.dumps(
            [self.config.kind, self.config.endpoint, self.config.model, self.dimension]
        )

    def embed(self, texts):
        if self.remote:
            if self.local_only:
                raise ByaxError('authorization_required', 'Local-only library cannot use remote embeddings')
            if not self.authorized and not self.consent.allows(self.config.alias, 'library_embeddings'):
                raise ByaxError('authorization_required', 'Separate embedding upload consent is required')
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
    return hashlib.sha256(
        json.dumps([provider.model_identity, provider.dimension, chunking], sort_keys=True).encode()
    ).hexdigest()


def cosine(first, second):
    denominator = math.sqrt(sum(x * x for x in first) * sum(x * x for x in second))
    return sum(a * b for a, b in zip(first, second, strict=True)) / denominator if denominator else 0.0


def embedding_preview(index, provider=None):
    from .libraries import CHUNKING

    provider = provider or ConfiguredEmbeddingProvider(
        index.library['embedding'], local_only=index.library.get('retrieval_policy') == 'local_only'
    )
    with index._db() as db:
        rows = [
            dict(row)
            for row in db.execute('SELECT chunk_id,document_id,text FROM chunks')
            if any(not p['stale'] for p in index._paths(db, row['document_id']))
        ]
    return {
        'provider': getattr(getattr(provider, 'config', None), 'alias', provider.model_identity),
        'remote': getattr(provider, 'remote', False),
        'documents': len({r['document_id'] for r in rows}),
        'characters': sum(len(r['text']) for r in rows),
        'passages': rows,
        'model_fingerprint': fingerprint(provider, CHUNKING),
        'notice': 'One-time upload of these passages, plus explicit later rebuilds after changes.',
    }


def rebuild_vectors(index, *, authorized=False, provider=None, cancel_event=None):
    provider = provider or ConfiguredEmbeddingProvider(
        index.library['embedding'],
        authorized=authorized,
        local_only=index.library.get('retrieval_policy') == 'local_only',
    )
    preview = embedding_preview(index, provider)
    signature = preview['model_fingerprint']
    processed = 0
    with index._db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS vectors (chunk_id TEXT PRIMARY KEY, model TEXT, vector TEXT)')
        db.execute('DELETE FROM vectors WHERE model != ?', (signature,))
    for passage in preview['passages']:
        if cancel_event and cancel_event.is_set():
            break
        vector = provider.embed([passage['text']])[0]
        validate_vectors([vector], 1, provider.dimension)
        with index._db() as db:
            db.execute(
                'INSERT OR REPLACE INTO vectors VALUES (?,?,?)',
                (passage['chunk_id'], signature, json.dumps(vector)),
            )
        processed += 1
    return {
        'processed': processed,
        'total': len(preview['passages']),
        'model_fingerprint': signature,
        'cancelled': processed != len(preview['passages']),
    }


def hybrid_search(index, query, *, limit=20, offset=0, authorized=False, provider=None):
    from .libraries import CHUNKING

    if (
        type(limit) is not int
        or not 1 <= limit <= index.limits['results']
        or type(offset) is not int
        or offset < 0
    ):
        raise ByaxError('invalid_input', 'Invalid hybrid search limit or offset')

    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ByaxError('invalid_input', 'Invalid hybrid pagination')
    provider = provider or ConfiguredEmbeddingProvider(
        index.library['embedding'],
        authorized=authorized,
        local_only=index.library.get('retrieval_policy') == 'local_only',
    )
    signature = fingerprint(provider, CHUNKING)
    keyword = index.search(query, limit=100)
    with index._db() as db:
        if not db.execute("SELECT name FROM sqlite_master WHERE name='vectors'").fetchone():
            return {**index.search(query, limit=limit, offset=offset), 'embedding_state': 'rebuild_required'}
        vectors = list(
            db.execute(
                'SELECT c.*,v.vector,v.model,d.content_hash FROM vectors v JOIN chunks c USING(chunk_id) JOIN documents d USING(document_id) WHERE v.model=?',
                (signature,),
            )
        )
        if not vectors:
            return {**index.search(query, limit=limit, offset=offset), 'embedding_state': 'rebuild_required'}
        query_vector = provider.embed([query])[0]
        validate_vectors([query_vector], 1, provider.dimension)
        ranked = sorted(
            vectors, key=lambda row: cosine(query_vector, json.loads(row['vector'])), reverse=True
        )
        hits = {r['chunk_id']: dict(r) for r in keyword['results']}
        scores = {r['chunk_id']: 1 / (60 + i) for i, r in enumerate(keyword['results'], 1)}
        for position, row in enumerate(ranked, 1):
            paths = index._paths(db, row['document_id'])
            if not paths or all(p['stale'] for p in paths):
                continue
            identity = row['chunk_id']
            scores[identity] = scores.get(identity, 0) + 1 / (60 + position)
            if identity not in hits:
                hit = {key: value for key, value in dict(row).items() if key not in {'vector', 'model'}}
                hit.update(
                    locator=json.loads(hit['locator']),
                    library_id=index.library['library_id'],
                    path=paths[0]['path'],
                    paths=[p['path'] for p in paths],
                    stale=False,
                    truncated=False,
                )
                hits[identity] = hit
    ordered = sorted(hits.values(), key=lambda hit: (-scores[hit['chunk_id']], hit['chunk_id']))
    result = []
    remaining = int(index.limits['context_chars'])
    for hit in ordered[offset : offset + limit]:
        if remaining <= 0:
            break
        hit['truncated'] = len(hit['text']) > remaining
        hit['text'] = hit['text'][:remaining]
        remaining -= len(hit['text'])
        hit['score'] = scores[hit['chunk_id']]
        result.append(hit)
    return {
        'results': result,
        'total': len(ordered),
        'offset': offset,
        'truncated': offset + len(result) < len(ordered),
        'continuation': offset + len(result) if offset + len(result) < len(ordered) else None,
        'embedding_state': 'ready',
        'method': 'hybrid',
    }


def index_embedding_state(index):
    """Read-only model compatibility check; constructing the adapter makes no request."""
    from .libraries import CHUNKING

    if not index.library.get('embedding'):
        return 'disabled'
    try:
        provider = ConfiguredEmbeddingProvider(index.library['embedding'])
        signature = fingerprint(provider, CHUNKING)
        with index._db() as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE name='vectors'").fetchone():
                return 'rebuild_required'
            missing = db.execute(
                'SELECT COUNT(*) FROM chunks c LEFT JOIN vectors v ON c.chunk_id=v.chunk_id AND v.model=? WHERE v.chunk_id IS NULL',
                (signature,),
            ).fetchone()[0]
        return 'rebuild_required' if missing else 'ready'
    except (ByaxError, OSError):
        return 'provider_unavailable'
