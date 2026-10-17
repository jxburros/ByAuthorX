"""On-device embedder and rerankers (ONNX Runtime). Optional extra ``byauthorx[local-models]``.

Everything runs on this computer: no request is made and no text leaves the process. Model files
come from :mod:`byax.local_models` after hash verification. Without the extra installed the module
still imports; loading a model then raises ``missing_dependency``.

* :class:`LocalEmbeddingProvider` satisfies :class:`byax.embeddings.EmbeddingProvider`
  (``model_identity``, ``dimension``, ``max_input_chars``, ``embed``) plus ``embed_query``.
* :class:`CrossEncoderReranker` and :class:`ColbertReranker` implement :class:`byax.ranking.Reranker`.
* :class:`EntailmentScorer` scores a claim against the text it cites (cascade stage 2,
  :mod:`byax.grounding_entailment`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Sequence

from . import local_models
from .errors import ByaxError
from .ranking import Hit

PROVIDER_PREFIX = 'local:'
MAX_BATCH = 4
QUERY_CACHE = 256
DOCUMENT_CACHE = 1024

_LOADED: dict[tuple, object] = {}
_LOCK = threading.RLock()


def runtime():
    """(onnxruntime, Tokenizer, numpy), or a ``missing_dependency`` error naming the extra."""
    try:
        import numpy
        import onnxruntime
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise ByaxError(
            'missing_dependency',
            'On-device models need the optional runtime: install byauthorx[local-models] '
            '(onnxruntime and tokenizers). Keyword search works without it.',
            extra='byauthorx[local-models]',
        ) from exc
    return onnxruntime, Tokenizer, numpy


def forget(name=None):
    """Drop loaded sessions (used after a model is removed)."""
    with _LOCK:
        for key in [k for k in _LOADED if name is None or k[0] == name]:
            _LOADED.pop(key, None)


def _session(path, threads=None):
    ort, _, _ = runtime()
    options = ort.SessionOptions()
    if threads:
        options.intra_op_num_threads = int(threads)
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), options, providers=['CPUExecutionProvider'])


def _tokenizer(path):
    _, Tokenizer, _ = runtime()
    return Tokenizer.from_file(str(path))


class _Model:
    """Loaded, verified model files (session, tokenizer, runtime parameters)."""

    def __init__(self, name, *, catalogue=None, directory=None, threads=None):
        self.catalogue = catalogue or local_models.load_catalogue()
        self.entry = local_models.get_model(name, self.catalogue)
        self.name = name
        self.parameters = dict(self.entry.get('runtime', {}))
        runtime()
        self.directory = local_models.ready_path(name, catalogue=self.catalogue, directory=directory)
        self.session = _session(self.directory / 'model.onnx', threads)
        self.tokenizer = _tokenizer(self.directory / 'tokenizer.json')
        # Tokenizer configuration (truncation, padding) is per call, so calls are serialized.
        self.lock = threading.Lock()
        try:
            self.config = json.loads((self.directory / 'config.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.config = {}

    @property
    def identity(self):
        return f'local:{self.name}@{self.entry["version"]}'


def load(name, *, catalogue=None, directory=None, threads=None):
    key = (name, str(directory), threads)
    with _LOCK:
        if key not in _LOADED:
            _LOADED[key] = _Model(name, catalogue=catalogue, directory=directory, threads=threads)
        return _LOADED[key]


def _normalize(array):
    norms = (array**2).sum(axis=-1, keepdims=True) ** 0.5
    norms[norms == 0] = 1.0
    return array / norms


class LocalEmbeddingProvider:
    """Dense embeddings from a catalogue embedder. ``remote`` is always False: nothing is sent anywhere.

    Constructing the provider reads only the catalogue (no model is loaded and nothing is hashed),
    so reporting embedding state and coverage stays cheap. The model loads on the first ``embed``.
    """

    remote = False
    kind = 'local'

    def __init__(
        self, model=local_models.DEFAULT_EMBEDDER, *, dimension=None, catalogue=None, directory=None
    ):
        self.catalogue = catalogue or local_models.load_catalogue()
        entry = local_models.get_model(model, self.catalogue)
        if entry['kind'] != 'embedder':
            raise ByaxError('invalid_input', 'Model is not an embedder', name=model)
        parameters = entry.get('runtime', {})
        native = int(parameters.get('native_dimension', parameters.get('dimension', 0)) or 0)
        self.name = model
        self.directory = directory
        self.dimension = int(dimension or parameters.get('dimension') or native)
        if native and not 1 <= self.dimension <= native:
            raise ByaxError(
                'invalid_input',
                f'Dimension must be between 1 and {native} for {model}',
                dimension=self.dimension,
            )
        self.max_tokens = int(parameters.get('max_tokens', 512))
        self.query_instruction = parameters.get('query_instruction', '')
        self.max_input_chars = 8000
        self.max_batch = MAX_BATCH
        self.model_identity = json.dumps(['local', model, entry['version'], self.dimension, self.max_tokens])
        self.config = type('LocalConfig', (), {'alias': PROVIDER_PREFIX + model, 'kind': 'local'})()
        self._queries = OrderedDict()
        self._lock = threading.Lock()

    def authorize(self):
        """Local inference needs no upload consent."""

    def available(self):
        status = local_models.model_status(self.name, catalogue=self.catalogue, directory=self.directory)
        return status['state'] == 'installed' and local_models.runtime_status()['installed']

    def _encode(self, texts):
        model = load(self.name, catalogue=self.catalogue, directory=self.directory)
        _, _, np = runtime()
        inputs = model.session.get_inputs()
        past = [i for i in inputs if i.name.startswith('past_key_values')]
        names = {i.name for i in inputs}
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        result = [None] * len(texts)
        for start in range(0, len(order), self.max_batch):
            indices = order[start : start + self.max_batch]
            with model.lock:
                model.tokenizer.no_padding()
                model.tokenizer.enable_truncation(max_length=self.max_tokens)
                encodings = [model.tokenizer.encode(texts[i]) for i in indices]
            length = max(len(e.ids) for e in encodings)
            ids = np.zeros((len(indices), length), dtype=np.int64)
            mask = np.zeros((len(indices), length), dtype=np.int64)
            for row, e in enumerate(encodings):
                ids[row, : len(e.ids)] = e.ids
                mask[row, : len(e.ids)] = 1
            feeds = {'input_ids': ids, 'attention_mask': mask}
            if 'position_ids' in names:
                feeds['position_ids'] = np.tile(np.arange(length, dtype=np.int64), (len(indices), 1))
            for tensor in past:
                # Empty key/value cache: [batch, heads, 0, head_dim] straight from the graph's own shapes.
                _, heads, _, head_dim = tensor.shape
                feeds[tensor.name] = np.zeros((len(indices), int(heads), 0, int(head_dim)), dtype=np.float32)
            hidden = model.session.run(['last_hidden_state'], feeds)[0]
            # Right padding with a causal model: the last real token carries the sentence embedding.
            last = mask.sum(axis=1) - 1
            pooled = hidden[np.arange(len(indices)), last][:, : self.dimension].astype(np.float32)
            pooled = _normalize(pooled)
            for row, i in enumerate(indices):
                result[i] = [float(x) for x in pooled[row]]
        return result

    def embed(self, texts):
        """Document vectors (unit length, truncated to ``dimension`` by Matryoshka representation)."""
        if any(not isinstance(text, str) for text in texts):
            raise ByaxError('limit_exceeded', 'Embedding text must be a string')
        if any(len(text) > self.max_input_chars for text in texts):
            raise ByaxError('limit_exceeded', 'Embedding text exceeds configured model input limit')
        if not texts:
            return []
        return self._encode(list(texts))

    def embed_query(self, text):
        """A search query with the model's instruction prefix, cached for repeated searches."""
        if not isinstance(text, str) or len(text) > self.max_input_chars:
            raise ByaxError('limit_exceeded', 'Embedding text exceeds configured model input limit')
        with self._lock:
            if text in self._queries:
                self._queries.move_to_end(text)
                return list(self._queries[text])
        prefixed = f'Instruct: {self.query_instruction}\nQuery:{text}' if self.query_instruction else text
        vector = self._encode([prefixed])[0]
        with self._lock:
            self._queries[text] = vector
            while len(self._queries) > QUERY_CACHE:
                self._queries.popitem(last=False)
        return list(vector)


def provider_for(settings, *, catalogue=None, directory=None):
    """A :class:`LocalEmbeddingProvider` for a library embedding setting ``{'provider': 'local:<model>'}``."""
    alias = settings['provider']
    return LocalEmbeddingProvider(
        alias[len(PROVIDER_PREFIX) :],
        dimension=settings.get('dimension'),
        catalogue=catalogue,
        directory=directory,
    )


def is_local(settings):
    return isinstance(settings, dict) and str(settings.get('provider', '')).startswith(PROVIDER_PREFIX)


def _retain(hit, rank, score, identity):
    return dataclasses.replace(
        hit,
        ranks={**hit.ranks, 'rerank': rank},
        payload={**hit.payload, 'rerank_score': round(float(score), 6), 'reranker': identity},
    )


class _Reranker:
    def __init__(self, name, *, catalogue=None, directory=None):
        self.name = name
        self.catalogue = catalogue
        self.directory = directory
        self.identity = f'local:{name}'

    def _scores(self, query, texts):  # pragma: no cover - interface
        raise NotImplementedError

    def rerank(self, query: str, hits: Sequence[Hit]) -> list[Hit]:
        """Stable reorder by model score; ties keep the incoming (fused) order."""
        hits = list(hits)
        if len(hits) < 2 or not query.strip():
            return hits
        scores = self._scores(query, [h.text for h in hits])
        order = sorted(range(len(hits)), key=lambda i: (-scores[i], i))
        return [_retain(hits[i], rank, scores[i], self.identity) for rank, i in enumerate(order, 1)]


class CrossEncoderReranker(_Reranker):
    """Query-passage pairs scored jointly by an int8 cross-encoder (bge-reranker-v2-m3)."""

    batch = 4

    def _scores(self, query, texts):
        model = load(self.name, catalogue=self.catalogue, directory=self.directory)
        _, _, np = runtime()
        limit = int(model.parameters.get('max_tokens', 512))
        pad = int(model.parameters.get('pad_token_id', 0))
        names = {i.name for i in model.session.get_inputs()}
        scores = []
        for start in range(0, len(texts), self.batch):
            with model.lock:
                model.tokenizer.no_padding()
                model.tokenizer.enable_truncation(max_length=limit, strategy='only_second')
                encodings = [
                    model.tokenizer.encode(query, text) for text in texts[start : start + self.batch]
                ]
            length = max(len(e.ids) for e in encodings)
            ids = np.full((len(encodings), length), pad, dtype=np.int64)
            mask = np.zeros((len(encodings), length), dtype=np.int64)
            for row, e in enumerate(encodings):
                ids[row, : len(e.ids)] = e.ids
                mask[row, : len(e.ids)] = 1
            feeds = {'input_ids': ids, 'attention_mask': mask}
            if 'token_type_ids' in names:
                types = np.zeros_like(ids)
                for row, e in enumerate(encodings):
                    types[row, : len(e.type_ids)] = e.type_ids
                feeds['token_type_ids'] = types
            logits = model.session.run(None, feeds)[0]
            scores.extend(float(x) for x in logits.reshape(len(encodings), -1)[:, 0])
        return scores


class ColbertReranker(_Reranker):
    """Late interaction: sum over query tokens of the best-matching passage token (MaxSim)."""

    batch = 8

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        self._documents = OrderedDict()
        self._lock = threading.Lock()

    def _encode(self, model, token_lists):
        _, _, np = runtime()
        ids = np.zeros((len(token_lists), max(len(t) for t in token_lists)), dtype=np.int64)
        mask = np.zeros_like(ids)
        for row, tokens in enumerate(token_lists):
            ids[row, : len(tokens)] = tokens
            mask[row, : len(tokens)] = 1
        names = {i.name for i in model.session.get_inputs()}
        feeds = {'input_ids': ids, 'attention_mask': mask}
        if 'token_type_ids' in names:
            feeds['token_type_ids'] = np.zeros_like(ids)
        output = model.session.run(None, feeds)[0].astype(np.float32)
        output = _normalize(output)
        return [output[row, : len(tokens)] for row, tokens in enumerate(token_lists)]

    def _tokens(self, model, text, prefix_id, limit):
        with model.lock:
            model.tokenizer.no_padding()
            model.tokenizer.enable_truncation(max_length=limit - 1)
            ids = model.tokenizer.encode(text).ids
        # [CLS] [Q]/[D] tokens... [SEP]: the marker token goes right after [CLS].
        return [ids[0], prefix_id, *ids[1:]]

    def _skip(self, model):
        cached = model.parameters.get('_skip')
        if cached is None:
            words = model.config.get('skiplist_words', [])
            cached = {i for i in (model.tokenizer.token_to_id(w) for w in words) if i is not None}
            model.parameters['_skip'] = cached
        return cached

    def _document_vectors(self, model, texts):
        _, _, np = runtime()
        prefix = int(model.parameters.get('document_prefix_id', 2))
        limit = int(model.parameters.get('max_document_tokens', 300))
        skip = self._skip(model)
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self._documents]
        for start in range(0, len(missing), self.batch):
            chunk = missing[start : start + self.batch]
            token_lists = [self._tokens(model, texts[i], prefix, limit) for i in chunk]
            vectors = self._encode(model, token_lists)
            for row, i in enumerate(chunk):
                keep = np.array([t not in skip for t in token_lists[row]])
                with self._lock:
                    self._documents[keys[i]] = vectors[row][keep]
                    while len(self._documents) > DOCUMENT_CACHE:
                        self._documents.popitem(last=False)
        with self._lock:
            return [self._documents[k] for k in keys]

    def _scores(self, query, texts):
        model = load(self.name, catalogue=self.catalogue, directory=self.directory)
        tokens = self._tokens(
            model,
            query,
            int(model.parameters.get('query_prefix_id', 1)),
            int(model.parameters.get('max_query_tokens', 32)),
        )
        (query_vectors,) = self._encode(model, [tokens])
        scores = []
        for vectors in self._document_vectors(model, texts):
            if not len(vectors):
                scores.append(0.0)
                continue
            scores.append(float((query_vectors @ vectors.T).max(axis=1).sum()))
        return scores


def load_reranker(name=None, *, catalogue=None, directory=None):
    """The reranker for a catalogue model; loads (and verifies) it on first use."""
    name = name or local_models.load_settings()['reranker']
    catalogue = catalogue or local_models.load_catalogue()
    entry = local_models.get_model(name, catalogue)
    if entry['kind'] != 'reranker':
        raise ByaxError('invalid_input', 'Model is not a reranker', name=name)
    family = entry.get('runtime', {}).get('family')
    cls = ColbertReranker if family == 'colbert' else CrossEncoderReranker
    return cls(name, catalogue=catalogue, directory=directory)


class EntailmentScorer:
    """How consistent is a claim with a premise? A T5 classifier (HHEM-2.1-Open) run through ONNX.

    ``score`` returns, for each ``(premise, claim)`` pair, the model's probability that the claim is
    consistent with the premise (0 to 1). The number is an internal ranking signal: it is never shown
    to the writer, who sees only the categories :mod:`byax.grounding_entailment` derives from it.
    The prompt is the one the model was trained with; the premise is trimmed from both ends (never the
    claim) until the pair fits ``max_tokens``.
    """

    kind = 'entailment'
    batch = 4
    MIN_PREMISE_CHARS = 200

    def __init__(self, name=None, *, catalogue=None, directory=None):
        self.name = name or local_models.load_settings()['entailer']
        self.catalogue = catalogue or local_models.load_catalogue()
        entry = local_models.get_model(self.name, self.catalogue)
        if entry['kind'] != 'entailment':
            raise ByaxError('invalid_input', 'Model is not an entailment checker', name=self.name)
        self.directory = directory
        self.identity = f'local:{self.name}@{entry["version"]}'
        self.parameters = dict(entry.get('runtime', {}))

    def _tokens(self, model, text):
        with model.lock:
            model.tokenizer.no_padding()
            model.tokenizer.no_truncation()
            return model.tokenizer.encode(text).ids

    def _fit(self, model, premise, claim):
        """Token ids of the prompt, shortening the premise around its middle until it fits."""
        template = self.parameters['prompt']
        limit = int(self.parameters.get('max_tokens', 1024))
        ids = self._tokens(model, template.format(text1=premise, text2=claim))
        while len(ids) > limit and len(premise) > self.MIN_PREMISE_CHARS:
            keep = max(self.MIN_PREMISE_CHARS, int(len(premise) * min(0.9, limit / len(ids) * 0.97)))
            cut = (len(premise) - keep) // 2
            premise = premise[cut : cut + keep]
            ids = self._tokens(model, template.format(text1=premise, text2=claim))
        return ids[:limit] if len(ids) > limit else ids

    def score(self, pairs):
        pairs = list(pairs)
        if any(not isinstance(p, str) or not isinstance(c, str) for p, c in pairs):
            raise ByaxError('invalid_input', 'Entailment text must be a string')
        if not pairs:
            return []
        model = load(self.name, catalogue=self.catalogue, directory=self.directory)
        _, _, np = runtime()
        pad = int(self.parameters.get('pad_token_id', 0))
        encoded = [self._fit(model, premise, claim) for premise, claim in pairs]
        order = sorted(range(len(encoded)), key=lambda i: len(encoded[i]))
        result = [0.0] * len(encoded)
        for start in range(0, len(order), self.batch):
            indices = order[start : start + self.batch]
            length = max(len(encoded[i]) for i in indices)
            ids = np.full((len(indices), length), pad, dtype=np.int64)
            mask = np.zeros((len(indices), length), dtype=np.int64)
            for row, i in enumerate(indices):
                ids[row, : len(encoded[i])] = encoded[i]
                mask[row, : len(encoded[i])] = 1
            logits = model.session.run(None, {'input_ids': ids, 'attention_mask': mask})[0]
            logits = np.asarray(logits, dtype=np.float64).reshape(len(indices), -1)[:, :2]
            logits = logits - logits.max(axis=1, keepdims=True)
            probabilities = np.exp(logits)
            probabilities = probabilities[:, 1] / probabilities.sum(axis=1)
            for row, i in enumerate(indices):
                result[i] = float(probabilities[row])
        return result


def load_entailer(name=None, *, catalogue=None, directory=None):
    """The entailment scorer for a catalogue model; the model loads (and is verified) on the first score."""
    return EntailmentScorer(name, catalogue=catalogue, directory=directory)
