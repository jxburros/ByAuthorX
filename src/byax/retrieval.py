"""Bounded retrieval from explicitly attached libraries; no implicit file access.

``retrieve_context`` collects up to ``limit`` candidates from every eligible library,
fuses them globally with reciprocal-rank fusion, removes duplicates and pinned items,
and only then applies the passage and character budget. Each library reports the
method it used (``keyword`` or ``hybrid``) and its embedding state.
"""

from __future__ import annotations

import copy
import json
import re

from .errors import ByaxError

METHODS = ('auto', 'keyword', 'hybrid')
ABSENCE_NOTE = (
    'Exhaustive lexical pass (stemmed words and literal substrings) over every eligible library and '
    'the open document. Paraphrases and synonyms are not checked.'
)


def _embedding_provider(embedding_provider, library):
    if isinstance(embedding_provider, dict):
        return embedding_provider.get(library['library_id'])
    return embedding_provider


def search_library(index, query, *, method='auto', limit=20, offset=0, provider=None, expand=False):
    """One library search with method selection. Never escalates consent.

    ``auto`` uses hybrid search when the library's embeddings are ``ready`` or
    ``partial`` and the query may be embedded under existing consent; otherwise keyword.
    ``hybrid`` asks for it explicitly but still falls back, with a notice, when it cannot
    run. Results always report ``method`` and ``embedding_state``.
    """
    from .embeddings import embedding_state

    if method not in METHODS:
        raise ByaxError('invalid_input', 'Search method must be auto, keyword or hybrid', method=method)
    if provider is None and not index.library.get('embedding'):
        state = {'state': 'disabled'}
    else:
        try:
            state = embedding_state(index, provider)
        except (ByaxError, OSError):
            state = {'state': 'provider_unavailable'}
    notice = None
    if method == 'hybrid' and state['state'] == 'disabled':
        notice = 'Embeddings are not configured for this library; showing keyword results.'
    elif method == 'hybrid' or (method == 'auto' and state['state'] in ('ready', 'partial')):
        try:
            return index.hybrid_search(query, limit=limit, offset=offset, provider=provider, expand=expand)
        except ByaxError as exc:
            if exc.code == 'invalid_input':
                raise
            # Missing consent, a local-only library with a remote embedder, or an
            # unreachable provider: keyword search still answers, with the reason shown.
            notice = f'Semantic search unavailable ({exc.message}); showing keyword results.'
    result = index.search(query, limit=limit, offset=offset, expand=expand)
    result.update(method='keyword', embedding_state=state['state'])
    if 'ratio' in state:
        result['coverage'] = state['ratio']
    if notice:
        result['notice'] = notice
    return result


def _pinned_ids(pinned):
    identities = set()
    for item in pinned or []:
        if isinstance(item, str):
            identities.add(item)
        elif isinstance(item, dict):
            identities.update(str(item[k]) for k in ('chunk_id', 'document_id') if item.get(k))
        else:
            raise ByaxError('invalid_input', 'Pinned items must be chunk or document identifiers')
    return identities


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
    method='auto',
    pinned=None,
    embedding_provider=None,
    expand=False,
):
    from .context_assembly import estimate_tokens
    from .libraries import LibraryIndex, LibraryRegistry
    from .profiles import request_settings
    from .ranking import RRF_K, dedupe, rrf

    if not isinstance(query, str) or not query.strip():
        raise ByaxError('invalid_input', 'Library retrieval requires an explicit query')
    if type(limit) is not int or not 1 <= limit <= 100 or type(max_chars) is not int or max_chars < 0:
        raise ByaxError('invalid_input', 'Invalid retrieval limits')
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens < 0):
        raise ByaxError('invalid_input', 'Invalid retrieval token budget')
    if method not in METHODS:
        raise ByaxError('invalid_input', 'Retrieval method must be auto, keyword or hybrid', method=method)
    pinned_ids = _pinned_ids(pinned)
    registry = registry or LibraryRegistry()
    config = request_settings(doc, target, profile)['profile'] or {}
    attachments = doc.get('settings', {}).get('libraries', [])
    omitted, lists, names, libraries = [], [], [], []
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
            index = LibraryIndex(library, cache_dir=cache_dir)
            hits = search_library(
                index,
                query,
                method=method,
                limit=limit,
                provider=_embedding_provider(embedding_provider, library),
                expand=expand,
            )
        except (ByaxError, OSError) as exc:
            omitted.append({'library_id': identity, 'reason': str(exc)})
            continue
        results = hits.get('results', hits.get('hits', []))
        report = {
            'library_id': identity,
            'name': library.get('name', identity),
            'method': hits['method'],
            'embedding_state': hits['embedding_state'],
            'candidates': len(results),
            'total': hits.get('total', len(results)),
        }
        report.update({k: hits[k] for k in ('coverage', 'notice', 'suggestions') if hits.get(k) is not None})
        libraries.append(report)
        if hits.get('truncated'):
            omitted.append(
                {
                    'library_id': identity,
                    'reason': 'Additional search results omitted by result limit',
                    'continuation': hits.get('continuation'),
                }
            )
        names.append(identity)
        lists.append(
            [
                dict(
                    hit,
                    library_id=identity,
                    library_name=library.get('name', identity),
                    retrieval_policy=policy,
                    remote_bound=remote,
                    method=hits['method'],
                    embedding_state=hits['embedding_state'],
                )
                for hit in results
            ]
        )
    # Global fusion: every library's first hit competes for the first places, so the
    # first attached library can no longer take the whole budget.
    fused, duplicates = dedupe(rrf(lists, names=names))
    included, pinned_tier = [], {}
    remaining = max_chars
    # An optional token budget (context_assembly.library_budget) applies beside the character cap,
    # which stays a hard limit.
    tokens_left = max_tokens
    for rank, hit in enumerate(fused, 1):
        passage = hit.to_dict()
        passage['fused_rank'] = rank
        pinned_by = pinned_ids & {hit.chunk_id, hit.document_id}
        if pinned_by:
            for identity in sorted(pinned_by):
                pinned_tier.setdefault(identity, []).append(hit.chunk_id)
        elif passage.get('stale'):
            omitted.append({'chunk_id': hit.chunk_id, 'reason': 'Indexed passage is stale'})
        elif len(included) >= limit or len(passage.get('text', '')) > remaining:
            omitted.append({'chunk_id': hit.chunk_id, 'reason': 'Retrieval payload limit exceeded'})
        elif tokens_left is not None and estimate_tokens(passage.get('text', ''), provider) > tokens_left:
            omitted.append({'chunk_id': hit.chunk_id, 'reason': 'Retrieval token budget exceeded'})
        else:
            included.append(passage)
            remaining -= len(passage.get('text', ''))
            if tokens_left is not None:
                tokens_left -= estimate_tokens(passage.get('text', ''), provider)
    omitted.extend(duplicates)
    return {
        'included': included,
        'omitted': omitted,
        'pinned': [
            {
                'id': identity,
                'tier': 'pinned',
                'reason': 'Attached in full to this request; excluded from retrieved passages',
                'excluded_chunks': pinned_tier.get(identity, []),
            }
            for identity in sorted(pinned_ids)
        ],
        'libraries': libraries,
        'method': method,
        'fusion': {'method': 'rrf', 'k': RRF_K, 'libraries': len(lists)},
        'remote_bound': remote,
        **(
            {'token_budget': {'maximum': max_tokens, 'used': max_tokens - tokens_left}}
            if max_tokens is not None
            else {}
        ),
        'allowed_citation_ids': [p['chunk_id'] for p in included] + list(doc.get('references', {})),
    }


_ABSENCE = re.compile(
    r"\b(?:not|never|no(?:where)?|n't|cannot|can't|couldn't|unable to)\b[^.!?\n]{0,60}?\b"
    r'(?:found|find|mention(?:ed|s)?|appear(?:s|ed)?|present|contain(?:s|ed)?|reference[sd]?|occur(?:s|red)?)\b'
    r'|\bno (?:mention|reference|record|evidence|instance)s? of\b',
    re.IGNORECASE,
)


def absence_claim(text):
    """True when a reply asserts that something is missing from the writer's sources."""
    return isinstance(text, str) and bool(_ABSENCE.search(text))


def _absence_terms(terms):
    from .search_query import parse

    if isinstance(terms, str):
        values = parse(terms).literal_terms()
    elif isinstance(terms, (list, tuple)) and all(isinstance(t, str) for t in terms):
        values = [t.strip() for t in terms if t.strip()]
    else:
        raise ByaxError('invalid_input', 'Absence terms must be a query string or a list of strings')
    values = list(dict.fromkeys(values))
    if not values:
        raise ByaxError('invalid_input', 'Name at least one term to verify')
    return values[:16]


def _manuscript_matches(doc, term, limit):
    needle = term.casefold()
    tokens = [t.casefold() for t in re.findall(r'\w+', term)]
    pattern = re.compile(r'\b' + r'\W+'.join(re.escape(t) for t in tokens), re.IGNORECASE) if tokens else None
    objects = []
    for block in doc.get('blocks', []):
        if block.get('type') == 'table':
            objects += [cell for row in block.get('rows', []) for cell in row.get('cells', [])]
        else:
            objects.append(block)
    matches, total = [], 0
    for obj in objects:
        text = obj.get('text', '')
        position = text.casefold().find(needle)
        literal = position >= 0
        if not literal and pattern is not None:
            found = pattern.search(text)
            position = found.start() if found else -1
        if position < 0:
            continue
        total += 1
        if len(matches) < limit:
            matches.append(
                {
                    'term': term,
                    'scope': 'manuscript',
                    'target': obj.get('id'),
                    'start': position,
                    'end': position + len(term),
                    'excerpt': text[max(0, position - 80) : position + len(term) + 80],
                    'literal': literal,
                }
            )
    return matches, total, len(objects)


def verify_absence(
    terms,
    libraries=None,
    *,
    manuscript=None,
    registry=None,
    cache_dir=None,
    profile=None,
    target=None,
    limit=20,
):
    """Search exhaustively before anything reports "not found in your sources".

    ``terms`` is a query string (its terms and quoted phrases) or a list of strings.
    ``libraries`` are library IDs or definitions; when omitted, the manuscript's
    attached libraries that its profile permits are used. Every term is looked up with
    the stemmed (porter) and literal (trigram) indexes of every eligible library and in
    the open document's text. The result lists what was searched and every match found.
    Matches are for the writer to see; ``retrieval_policy`` marks local-only sources.
    """
    from .libraries import LibraryIndex, LibraryRegistry

    if type(limit) is not int or not 1 <= limit <= 100:
        raise ByaxError('invalid_input', 'Absence match limit must be between 1 and 100')
    values = _absence_terms(terms)
    registry = registry or LibraryRegistry()
    omitted, entries = [], []
    if libraries is None:
        from .profiles import request_settings

        attachments = (manuscript or {}).get('settings', {}).get('libraries', [])
        config = (request_settings(manuscript, target, profile)['profile'] or {}) if manuscript else {}
        exclusive = config.get('source_authority') == 'exclusive'
        for attachment in attachments:
            if exclusive and attachment.get('role', 'supplementary') == 'open':
                omitted.append(
                    {'library_id': attachment['library_id'], 'reason': 'Active profile excludes open sources'}
                )
                continue
            entries.append(attachment['library_id'])
    else:
        entries = list(libraries)
    searched, matches, per_term = [], [], {t: {'library': 0, 'manuscript': 0} for t in values}
    exhaustive = True
    for entry in entries:
        identity = entry.get('library_id') if isinstance(entry, dict) else entry
        try:
            library = entry if isinstance(entry, dict) else registry.get(entry)
            index = LibraryIndex(library, cache_dir=cache_dir)
            status = index.status()
        except (ByaxError, OSError) as exc:
            omitted.append({'library_id': identity, 'reason': str(exc)})
            exhaustive = False
            continue
        record = {
            'library_id': identity,
            'name': library.get('name', identity),
            'retrieval_policy': library.get('retrieval_policy', 'ask'),
            'chunks': status['chunks'],
            'passes': ['porter', 'trigram'] if index.trigram else ['porter'],
            'stale': status['stale'],
        }
        if status['stale']:
            record['notice'] = (
                'Some files changed or failed to index; refresh the library for a complete check.'
            )
            exhaustive = False
        searched.append(record)
        for term in values:
            found = index.literal_matches(term, limit=limit)
            per_term[term]['library'] += found['total']
            for match in found['matches']:
                matches.append(
                    dict(match, term=term, scope='library', retrieval_policy=record['retrieval_policy'])
                )
    manuscript_report = None
    if manuscript is not None:
        blocks = 0
        for term in values:
            found, total, blocks = _manuscript_matches(manuscript, term, limit)
            per_term[term]['manuscript'] += total
            matches.extend(found)
        manuscript_report = {'document_id': manuscript.get('id'), 'objects': blocks}
    results = [
        {
            'term': term,
            'found': bool(counts['library'] or counts['manuscript']),
            'library_matches': counts['library'],
            'manuscript_matches': counts['manuscript'],
        }
        for term, counts in per_term.items()
    ]
    return {
        'terms': values,
        'searched': {'libraries': searched, 'manuscript': manuscript_report},
        'omitted': omitted,
        'results': results,
        'found': [r['term'] for r in results if r['found']],
        'absent': [r['term'] for r in results if not r['found']],
        'matches': matches,
        'exhaustive': exhaustive,
        'method': 'lexical',
        'note': ABSENCE_NOTE,
    }


def describe_absence(report, limit=5):
    """Plain-language summary of a verify_absence report for notes and findings."""
    libraries = report['searched']['libraries']
    scope = f'{len(libraries)} librar{"y" if len(libraries) == 1 else "ies"}'
    if report['searched']['manuscript'] is not None:
        scope += ' and this document'
    lines = [f'Searched {scope} for: ' + ', '.join(report['terms']) + '.']
    if report['found']:
        lines.append('Literal matches exist for: ' + ', '.join(report['found']) + '.')
        for match in report['matches'][:limit]:
            where = match.get('path') or 'this document'
            locator = match.get('locator') or {}
            place = ', '.join(f'{k} {v}' for k, v in locator.items() if k in ('page', 'ordinal', 'block'))
            excerpt = ' '.join(match.get('excerpt', '').split())
            lines.append(f'• {match["term"]}: {where}{" (" + place + ")" if place else ""} — “{excerpt}”')
    if report['absent']:
        lines.append('No literal match for: ' + ', '.join(report['absent']) + '.')
    if not report['exhaustive']:
        lines.append('Some sources could not be fully searched; see the omitted list.')
    lines.append(report['note'])
    return '\n'.join(lines)


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
