"""``route``, ``outline``, ``verify-citations`` and ``summaries`` commands (context engine 8.3–8.5).

All four are dispatched through ``cli.execute``, so they are also MCP tools (``byax_route``,
``byax_outline``, ``byax_verify_citations``, ``byax_summaries``) and REST commands. ``route``,
``outline`` and ``verify-citations`` are read-only and never call a model: ``route`` explains the
route a request would take (and, with ``inspect``, what the assistant would see), ``outline`` exposes
the outline parameters of ``inspect``, and ``verify-citations`` returns grounding records verified
against the indexed source text. ``summaries`` builds chapter summaries only on explicit request with
a provider the author chose; the summaries live in the user cache and never enter the document,
except as a pending synopsis proposal the author accepts.
"""

from __future__ import annotations

import copy
from typing import Any

from .errors import ByaxError

SUMMARY_ACTIONS = ('status', 'show', 'build', 'propose-synopsis', 'clear')
MAX_PAGE = 500


def _page(rows: list[Any], a: dict[str, Any], default: int = 50) -> tuple[list[Any], dict[str, Any]]:
    limit, offset = a.get('limit', default), a.get('offset', 0)
    if limit > MAX_PAGE:
        raise ByaxError('invalid_input', f'limit must be at most {MAX_PAGE}', field='limit')
    page = rows[offset : offset + limit]
    more = offset + len(page) < len(rows)
    return page, {
        'total': len(rows),
        'offset': offset,
        'truncated': more,
        'continuation': offset + len(page) if more else None,
    }


def _selection(doc: dict[str, Any], a: dict[str, Any]) -> list[dict[str, Any]] | None:
    from .model import find_object

    if not a.get('target'):
        return None
    text = find_object(doc, a['target']).get('text', '')
    start, end = a.get('start', 0), a.get('end', len(text))
    if not 0 <= start < end <= len(text):
        raise ByaxError('invalid_input', 'Selection offsets are outside the target text', target=a['target'])
    return [{'target': a['target'], 'start': start, 'end': end, 'text': text[start:end]}]


def _provider(a: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """The named provider (read only; nothing is configured or sent) and the local text providers."""
    if not a.get('alias'):
        return None, []
    from .providers import ProviderRegistry

    registry = ProviderRegistry(config_dir=a.get('config_dir'))
    config = registry.get(a['alias'])
    local = [p for p in registry.list_with_locality() if p.get('locality') == 'local']
    return config, local


def route_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    """Explain the route for a request without calling any model; ``inspect`` adds what would be sent."""
    from . import context_assembly, router
    from .cli import _load, envelope
    from .series import with_series

    if not a.get('request'):
        raise ByaxError('invalid_input', 'Missing required argument: request', field='request')
    doc = _load(a['path'])
    view = with_series(doc, a['path'])
    selection = _selection(view, a)
    config, local = _provider(a)
    use_libraries = a.get('use_libraries', False)
    options = {
        'selection': selection,
        'cursor': a.get('cursor'),
        'override': a.get('override'),
        'privacy': a.get('privacy', 'exclude'),
        'local_providers': local,
    }
    if not a.get('inspect'):
        decision = router.decide(
            a['request'],
            view,
            provider=config,
            use_libraries=use_libraries,
            libraries=None if use_libraries else [],
            **options,
        )
        return envelope({'route': decision, 'sent': False}, doc)
    assembled = context_assembly.assemble(
        view,
        a['request'],
        document_path=a['path'],
        provider=config,
        use_libraries=use_libraries,
        excluded=a.get('exclude'),
        **options,
    )
    inspector = context_assembly.inspector_payload(assembled, provider=config)
    items, paging = _page(inspector['items'], a)
    inspector['items'] = items
    inspector['pinned'] = [i for i in items if i['tier'] == 'pinned']
    inspector['retrieved'] = [i for i in items if i['tier'] != 'pinned']
    inspector['items_page'] = paging
    result = {
        'route': assembled['route'],
        'inspector': inspector,
        'abstain': assembled['abstain'],
        'provider': assembled['provider'],
        'sent': False,
    }
    return envelope(copy.deepcopy(result), doc)


def outline_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    from .cli import _load, envelope
    from .outline import outline

    doc = _load(a['path'])
    result = outline(
        doc,
        depth=a.get('depth'),
        counts=a.get('counts', False),
        binder=a.get('binder', False),
        limit=a.get('limit', 200),
        offset=a.get('offset', 0),
    )
    return envelope(result, doc)


def _verified_passages(doc, path, specs):
    """Passages with the text the index holds now (or the open document's block text), never caller text."""
    from .cli_libraries import attached_library
    from .libraries import LibraryIndex
    from .model import find_object
    from .series import with_series

    view = with_series(doc, path)
    passages, problems = [], []
    for spec in specs:
        if isinstance(spec, str):
            spec = {'target': spec} if ':' not in spec else {'chunk_id': spec}
        if not isinstance(spec, dict):
            raise ByaxError('invalid_input', 'Each passage is a chunk reference object or a block ID')
        if spec.get('library_id'):
            if not spec.get('document_id') or not spec.get('chunk_id'):
                raise ByaxError(
                    'invalid_input', 'A library passage needs library_id, document_id and chunk_id'
                )
            try:
                library = attached_library(view, spec['library_id'])
                read = LibraryIndex(library).read(spec['document_id'], chunk_id=spec['chunk_id'])
            except ByaxError as exc:
                problems.append({'chunk_id': spec['chunk_id'], 'reason': exc.message})
                continue
            row = read['results'][0]
            passages.append(
                {
                    'chunk_id': spec['chunk_id'],
                    'library_id': spec['library_id'],
                    'library_name': library.get('name'),
                    'document_id': spec['document_id'],
                    'content_hash': read['content_hash'],
                    'path': (read['paths'] or [None])[0],
                    'locator': row.get('locator', {}),
                    'text': row['text'],
                    'title': (read['paths'] or [spec['document_id']])[0],
                }
            )
        else:
            target = spec.get('target') or str(spec.get('chunk_id', '')).split(':')[-1]
            try:
                text = find_object(doc, target).get('text', '')
            except ByaxError as exc:
                problems.append({'target': target, 'reason': exc.message})
                continue
            passages.append(
                {
                    'chunk_id': spec.get('chunk_id') or f'{doc["id"]}:{target}',
                    'document_id': doc['id'],
                    'block_id': target,
                    'locator': {'block_id': target},
                    'text': text,
                    'title': 'this document',
                }
            )
    return passages, problems


def verify_citations_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    """Grounding records for a reply (verified against indexed text), or the document's citation audit."""
    from . import grounding
    from .cli import _load, envelope

    doc = _load(a['path'])
    spec = a.get('input') or {}
    if not isinstance(spec, dict) or set(spec) - {'reply', 'passages'}:
        raise ByaxError('invalid_input', 'verify-citations input accepts {"reply", "passages"}')
    reply = a.get('reply') if a.get('reply') is not None else spec.get('reply')
    if reply is None:
        report = grounding.audit_citations(doc)
        items, paging = _page(report['citations'], a)
        report['citations'] = items
        return envelope(
            dict(report, mode='audit', page=paging, records=[i['record'] for i in items if 'record' in i]),
            doc,
            validation=report['validation'],
            review=report['review'],
        )
    if not isinstance(reply, str):
        raise ByaxError('invalid_input', 'reply must be text', field='reply')
    passages, problems = _verified_passages(doc, a['path'], spec.get('passages') or [])
    grounded = grounding.ground_reply(reply, passages=passages, references=doc.get('references', {}))
    records, paging = _page(grounded['records'], a)
    return envelope(
        {
            'mode': 'reply',
            'text': grounded['text'],
            'records': records,
            'page': paging,
            'summary': grounded['summary'],
            'invalid': grounded['invalid'],
            'stages': grounded['stages'],
            'unavailable_passages': problems,
            'source_text': 'Passage text is read from the attached library index or the open document; '
            'text supplied with the request is never trusted.',
        },
        doc,
    )


def summaries_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    from . import engine, summaries
    from .cli import _load, _mutate, envelope

    action = a.get('action', 'status')
    if action not in SUMMARY_ACTIONS:
        raise ByaxError(
            'invalid_input', f'Unknown summaries action: {action}', supported=list(SUMMARY_ACTIONS)
        )
    doc = _load(a['path'])
    cache = summaries.SummaryCache()
    if action == 'status':
        return envelope(summaries.status(doc, cache), doc)
    if action == 'show':
        gathered = summaries.collected(doc, cache)
        chapters, paging = _page(gathered['chapters'], a)
        return envelope(dict(gathered, chapters=chapters, page=paging), doc)
    if action == 'clear':
        return envelope({'cleared': cache.clear(doc), 'cache': str(cache.path(doc))}, doc)
    if action == 'propose-synopsis':
        if not a.get('target'):
            raise ByaxError(
                'invalid_input', 'propose-synopsis requires --target (a binder item)', field='target'
            )
        values = summaries.synopsis_proposal(doc, a['target'], cache)
        request: dict[str, Any] = {
            'operations': [{'type': 'propose', 'values': values}],
            'expected_revision': a.get('expect_revision') or doc['revision'],
        }
        if a.get('request_id') is not None:
            request['request_id'] = a['request_id']
        dry_run = a.get('dry_run', False)

        def propose(current):
            candidate, result = engine.apply(current, request, dry_run=dry_run)
            result['synopsis'] = values['operations'][0]['values']['synopsis']
            return candidate, result, not dry_run

        return _mutate(a['path'], propose)
    # build: an explicit provider action.
    if not a.get('alias'):
        raise ByaxError(
            'invalid_input', 'build requires --alias (the provider to summarize with)', field='alias'
        )
    from .providers import ProviderRegistry

    registry = ProviderRegistry(config_dir=a.get('config_dir'))
    wanted = [a['target']] if a.get('target') else None
    if a.get('dry_run'):
        return envelope(
            summaries.preview(doc, a['alias'], registry=registry, section_id=a.get('target')), doc
        )
    report = summaries.build(
        doc,
        a['alias'],
        registry=registry,
        cache=cache,
        sections_wanted=wanted,
        rebuild=a.get('rebuild', False),
        authorized=a.get('allow_remote', False),
    )
    return envelope(report, doc)
