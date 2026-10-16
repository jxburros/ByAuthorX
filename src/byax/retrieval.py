"""Bounded retrieval from explicitly attached libraries; no implicit file access."""

from __future__ import annotations

import copy
import json
import re

from .errors import ByaxError


def retrieve_context(
    doc,
    query,
    *,
    profile=None,
    target=None,
    registry=None,
    cache_dir=None,
    limit=12,
    max_chars=12000,
    max_tokens=None,
    provider=None,
    remote=False,
):
    from .context_assembly import estimate_tokens
    from .libraries import LibraryIndex, LibraryRegistry
    from .profiles import request_settings

    if not isinstance(query, str) or not query.strip():
        raise ByaxError('invalid_input', 'Library retrieval requires an explicit query')
    if type(limit) is not int or not 1 <= limit <= 100 or type(max_chars) is not int or max_chars < 0:
        raise ByaxError('invalid_input', 'Invalid retrieval limits')
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens < 0):
        raise ByaxError('invalid_input', 'Invalid retrieval token budget')
    registry = registry or LibraryRegistry()
    config = request_settings(doc, target, profile)['profile'] or {}
    attachments = doc.get('settings', {}).get('libraries', [])
    included, omitted = [], []
    remaining = max_chars
    # An optional token budget (context_assembly.library_budget) applies beside the character cap,
    # which stays a hard limit.
    tokens_left = max_tokens
    # An exclusive profile permits only supplied authoritative library attachments.
    exclusive = config.get('source_authority') == 'exclusive'
    for attachment in attachments:
        identity = attachment['library_id']
        if exclusive and attachment.get('role', 'supplementary') == 'open':
            omitted.append({'library_id': identity, 'reason': 'Active profile excludes open sources'})
            continue
        try:
            library = registry.get(identity)
            policy = library.get('retrieval_policy', 'ask')
            if remote and policy == 'local_only':
                omitted.append({'library_id': identity, 'reason': 'Library is local_only'})
                continue
            hits = LibraryIndex(library, cache_dir=cache_dir).search(query, limit=limit)
        except (ByaxError, OSError) as exc:
            omitted.append({'library_id': identity, 'reason': str(exc)})
            continue
        if hits.get('truncated'):
            omitted.append(
                {
                    'library_id': identity,
                    'reason': 'Additional search results omitted by result limit',
                    'continuation': hits.get('continuation'),
                }
            )
        for hit in hits.get('results', hits.get('hits', [])):
            passage = dict(
                hit,
                library_id=identity,
                library_name=library.get('name', identity),
                retrieval_policy=policy,
                remote_bound=remote,
            )
            if hit.get('stale'):
                omitted.append({'chunk_id': hit['chunk_id'], 'reason': 'Indexed passage is stale'})
            elif len(included) >= limit or len(hit.get('text', '')) > remaining:
                omitted.append({'chunk_id': hit['chunk_id'], 'reason': 'Retrieval payload limit exceeded'})
            elif tokens_left is not None and estimate_tokens(hit.get('text', ''), provider) > tokens_left:
                omitted.append({'chunk_id': hit['chunk_id'], 'reason': 'Retrieval token budget exceeded'})
            else:
                included.append(passage)
                remaining -= len(hit.get('text', ''))
                if tokens_left is not None:
                    tokens_left -= estimate_tokens(hit.get('text', ''), provider)
    return {
        'included': included,
        'omitted': omitted,
        'remote_bound': remote,
        **(
            {'token_budget': {'maximum': max_tokens, 'used': max_tokens - tokens_left}}
            if max_tokens is not None
            else {}
        ),
        'allowed_citation_ids': [p['chunk_id'] for p in included] + list(doc.get('references', {})),
    }


def augment_context(context, retrieval):
    result = copy.deepcopy(context)
    result['library_passages'] = copy.deepcopy(retrieval['included'])
    result['library_retrieval'] = {k: copy.deepcopy(v) for k, v in retrieval.items() if k != 'included'}
    return result


def validate_citations(text, context):
    """Reject machine citation tokens that were not supplied in this request."""
    allowed = set(context.get('references', {}))
    allowed.update(p['chunk_id'] for p in context.get('library_passages', []))
    for identity in re.findall(r'\[\[cite:([^\]]+)\]\]', text):
        if identity not in allowed:
            raise ByaxError(
                'invalid_input', 'Assistant cited an unavailable source or chunk', target=identity
            )


class ConsentRegistry:
    """Local provider-specific permissions, deliberately separate from documents."""

    CATEGORIES = ('library_passages', 'library_embeddings', 'citation_lookup')

    def __init__(self, config_dir=None):
        from pathlib import Path

        from .providers import _default_dir

        self.path = (Path(config_dir) if config_dir else _default_dir()) / 'data-consent.json'

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def allows(self, provider, category):
        return self._load().get(provider, {}).get(category) is True

    def set(self, provider, category, allowed):
        from .providers import _atomic_json

        if category not in self.CATEGORIES or type(allowed) is not bool:
            raise ByaxError('invalid_input', 'Unknown consent category or invalid permission')
        data = self._load()
        data.setdefault(provider, {})[category] = allowed
        _atomic_json(self.path, data)


def authorize_library_context(context, *, remote, provider, config_dir=None, authorized=False):
    passages = context.get('library_passages', [])
    if not passages or not remote:
        return
    if any(p.get('retrieval_policy') == 'local_only' for p in passages):
        raise ByaxError('authorization_required', 'Local-only library passages cannot leave this computer')
    if not authorized and not ConsentRegistry(config_dir).allows(provider, 'library_passages'):
        raise ByaxError('authorization_required', 'Separate authorization is required for library passages')
