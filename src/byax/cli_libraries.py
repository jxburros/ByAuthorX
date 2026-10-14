"""Library and citation commands shared by CLI, MCP and REST."""

from __future__ import annotations

from .errors import ByaxError


def attached_library(doc, library_id):
    from .libraries import LibraryRegistry

    attachments = doc.get('settings', {}).get('libraries', [])
    if not any(isinstance(a, dict) and a.get('library_id') == library_id for a in attachments):
        raise ByaxError('invalid_input', 'Library is not attached to this document', target=library_id)
    return LibraryRegistry().get(library_id)


def _attachment(library, role='supplementary'):
    if role not in ('exclusive', 'supplementary', 'open'):
        raise ByaxError('invalid_input', 'Library role must be exclusive, supplementary or open')
    return {'library_id': library['library_id'], 'name': library['name'], 'role': role}


def _set_attachment(a, library, remove=False):
    from .cli import _mutate
    from .engine import apply

    def mutate(doc):
        entries = [
            x for x in doc['settings'].get('libraries', []) if x['library_id'] != library['library_id']
        ]
        if not remove:
            entries.append(_attachment(library, a.get('role', 'supplementary')))
        candidate, result = apply(
            doc,
            {
                'expected_revision': a.get('expect_revision', doc['revision']),
                'operations': [{'type': 'set_settings', 'values': {'libraries': entries}}],
            },
        )
        return candidate, result, True

    return _mutate(a['path'], mutate)


def refresh_worker(payload, cancel):
    from .libraries import LibraryIndex, LibraryRegistry

    library = LibraryRegistry().get(payload['library_id'])
    return LibraryIndex(library).refresh(cancel_event=cancel, rebuild=payload.get('rebuild', False))


def _refresh(library, a, doc):
    from .cli import envelope
    from .jobs import JobManager

    manager = JobManager()
    try:
        job = manager.submit(
            'library-refresh',
            {'library_id': library['library_id'], 'rebuild': a.get('rebuild', False)},
            refresh_worker,
        )
        result = manager.wait(job['id'])
        return envelope({'job': result}, doc, execution=result['state'])
    finally:
        manager.close()


def library_command(a, owner=None):
    from .cli import _load, envelope
    from .libraries import LibraryIndex, LibraryRegistry

    doc = _load(a['path'])
    registry = LibraryRegistry()
    action = a.get('action', 'list')
    if action == 'list':
        rows = []
        for attachment in doc['settings'].get('libraries', []):
            row = dict(attachment)
            try:
                library = registry.get(row['library_id'])
                from pathlib import Path

                row.update(
                    available=all(Path(root).is_dir() for root in library['roots']),
                    index=LibraryIndex(library).status(),
                )
            except (ByaxError, OSError) as exc:
                row.update(available=False, reason=str(exc))
            rows.append(row)
        return envelope({'libraries': rows, 'total': len(rows), 'truncated': False}, doc)
    if action == 'create':
        spec = a.get('input', {})
        library = registry.create(
            spec.get('name', ''),
            spec.get('roots', []),
            **{k: v for k, v in spec.items() if k not in ('name', 'roots')},
        )
        return _set_attachment(a, library)
    if action == 'attach':
        return _set_attachment(a, registry.get(a.get('library_id')))
    if action == 'relink':
        identity = a.get('library_id')
        attachment = next(
            (x for x in doc['settings'].get('libraries', []) if x.get('library_id') == identity), None
        )
        if attachment is None:
            raise ByaxError('invalid_input', 'Library is not attached to this document')
        library = registry.relink(identity, a.get('input', {}).get('roots', []), name=attachment.get('name'))
        return envelope({'library': library}, doc)
    if action in ('detach', 'remove'):
        identity = a.get('library_id')
        attachment = next(
            (x for x in doc['settings'].get('libraries', []) if x.get('library_id') == identity), None
        )
        if attachment is None:
            raise ByaxError('invalid_input', 'Library is not attached to this document')
        result = _set_attachment(a, attachment, remove=True)
        if action == 'remove' and any(x['library_id'] == identity for x in registry.list()):
            registry.remove(identity)
        return result
    library = attached_library(doc, a.get('library_id'))
    if action == 'refresh':
        return _refresh(library, a, doc)
    if action == 'embedding-configure':
        config = a.get('input') or None
        return envelope({'library': registry.configure_embedding(library['library_id'], config)}, doc)
    index = LibraryIndex(library)
    if action == 'embedding-preview':
        return envelope(index.embedding_preview(), doc)
    if action == 'embed':
        return envelope(index.rebuild_vectors(authorized=a.get('allow_remote', False)), doc)
    if action == 'hybrid':
        return envelope(
            index.hybrid_search(
                a.get('query', ''),
                limit=a.get('limit', 20),
                offset=a.get('offset', 0),
                authorized=a.get('allow_remote', False),
            ),
            doc,
        )
    if action == 'status':
        return envelope(index.status(), doc)
    if action == 'search':
        return envelope(
            index.search(a.get('query', ''), limit=a.get('limit', 20), offset=a.get('offset', 0)), doc
        )
    if action == 'read':
        return envelope(
            index.read(
                a.get('document_id'),
                chunk_id=a.get('chunk_id'),
                limit=a.get('limit', 20000),
                offset=a.get('offset', 0),
            ),
            doc,
        )
    raise ByaxError('invalid_input', 'Unknown library action', action=action)


def library_search(a, owner=None):
    return library_command(dict(a, action='search'), owner)


def library_read(a, owner=None):
    return library_command(dict(a, action='read'), owner)


def cite_command(a, owner=None):
    from .cli import execute

    op = {
        k: a[k] for k in ('target', 'start', 'end', 'source', 'quote', 'kind', 'prefix', 'suffix') if k in a
    }
    op.update(type='cite', **a.get('input', {}))
    if op['type'] != 'cite':
        raise ByaxError('invalid_input', 'Cite input cannot select another operation')
    return execute(
        'apply',
        {
            'path': a['path'],
            'input': {'operations': [op]},
            **{k: a[k] for k in ('expect_revision', 'request_id', 'dry_run') if k in a},
        },
    )


def _suggest(a, doc):
    from .citations import source_from_hit
    from .cli import envelope, execute
    from .model import find_object
    from .retrieval import retrieve_context

    block = find_object(doc, a.get('target'))
    start, end = a.get('start', 0), a.get('end', len(block.get('text', '')))
    if not 0 <= start < end <= len(block.get('text', '')):
        raise ByaxError('invalid_input', 'Select a nonempty claim range')
    claim = block['text'][start:end]
    retrieval = retrieve_context(
        doc, a.get('query') or claim, target=block['id'], limit=min(a.get('limit', 5), 20)
    )
    proposals = []
    for hit in retrieval['included']:
        source = source_from_hit(hit)
        operations = []
        if source['id'] not in doc['references']:
            operations.append({'type': 'add_reference', 'id': source['id'], 'values': source})
        operations.append(
            {
                'type': 'cite',
                'target': block['id'],
                'start': start,
                'end': end,
                'source': source['id'],
                'quote': claim,
                'kind': 'paraphrase',
                'locator': {'chunk': hit['chunk_id']},
            }
        )
        proposals.append(
            {
                'type': 'propose',
                'values': {
                    'operations': operations,
                    'explanation': 'Suggested library source; review the passage before accepting.',
                    'evidence': hit,
                },
            }
        )
    if not proposals:
        return envelope({'proposals': [], 'retrieval': retrieval}, doc)
    result = execute(
        'apply',
        {
            'path': a['path'],
            'expect_revision': a.get('expect_revision', doc['revision']),
            'input': {'operations': proposals},
            **{k: a[k] for k in ('request_id', 'dry_run') if k in a},
        },
    )
    result['retrieval'] = retrieval
    return result


def source_command(a, owner=None):
    from .citations import export_sources, import_sources, render_bibliography, search_sources
    from .cli import _load, envelope, execute

    doc = _load(a['path'])
    action = a.get('action', 'search')
    if action == 'search':
        rows = search_sources(doc['references'], a.get('query', ''))
        offset, limit = a.get('offset', 0), a.get('limit', 20)
        return envelope(
            {
                'sources': rows[offset : offset + limit],
                'total': len(rows),
                'truncated': offset + limit < len(rows),
                'continuation': offset + limit if offset + limit < len(rows) else None,
            },
            doc,
        )
    if action == 'suggest':
        return _suggest(a, doc)
    if action == 'export':
        return envelope({'text': export_sources(doc['references'], a.get('format', 'csl-json'))}, doc)
    if action == 'bibliography':
        return envelope({'bibliography': render_bibliography(doc['references'], a.get('style', 'apa'))}, doc)
    if action == 'import':
        sources = import_sources(a.get('text', ''), a.get('format', 'csl-json'), existing=doc['references'])
        return execute(
            'apply',
            {
                'path': a['path'],
                'input': {
                    'operations': [
                        {'type': 'add_reference', 'id': source['id'], 'values': source} for source in sources
                    ]
                },
                **{k: a[k] for k in ('expect_revision', 'request_id', 'dry_run') if k in a},
            },
        )
    raise ByaxError('invalid_input', 'Unknown source action', action=action)
