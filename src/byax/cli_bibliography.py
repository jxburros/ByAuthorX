"""``byax bibliography`` and ``byax excerpt`` (context engine plan 9), dispatched by ``cli.execute``.

``bibliography`` is the bibliography health report, bibliography exchange (RIS), additional CSL
styles, the offline Retraction Watch index and the Zotero connection. ``excerpt`` adds, lists, checks
and refreshes live excerpts and shows where each source is used.

Over MCP and REST the read-only parts work (health from the cache, RIS and BibTeX export, listing
styles, where-used, excerpt checks). Anything that would fetch from the network, read the person's
Zotero, change the user's configuration or write to the user's data folders is refused there
(:func:`byax.transport_policy.check_provider_admin`): the person at this computer asks for it
through the CLI or the desktop.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import ByaxError

# Actions that reach the network, Zotero or the user's own configuration and data folders.
LOCAL_ACTIONS = (
    'consent',
    'configure',
    'retractions-import',
    'styles-preview',
    'styles-add',
    'styles-remove',
    'zotero-collections',
    'zotero-import',
    'clear-cache',
)
READ_ACTIONS = ('health', 'status', 'ris-export', 'export-bib', 'styles-list', 'retractions-status')
WRITE_ACTIONS = ('ris-import',)
ACTIONS = READ_ACTIONS + WRITE_ACTIONS + LOCAL_ACTIONS
EXCERPT_ACTIONS = ('list', 'where-used', 'check', 'add', 'refresh')
# Document-free actions need no ``path``.
DOCUMENT_FREE = (
    'consent',
    'configure',
    'retractions-import',
    'retractions-status',
    'styles-list',
    'styles-preview',
    'styles-add',
    'styles-remove',
    'zotero-collections',
    'clear-cache',
)
DEFAULT_LIMIT = 200
# Tests (and nothing else) replace these to serve recorded answers instead of reaching the network.
TRANSPORT: Any = None
LOOKUP_OPTIONS: dict[str, Any] = {}


def _invalid(message, **details):
    raise ByaxError('invalid_input', message, **details)


def _need(a, *keys):
    for key in keys:
        if a.get(key) in (None, ''):
            _invalid(f'Missing required argument: {key}', field=key)


def make_lookup(*, online=False, authorized=False, consent=None):
    """The lookup client for a run: the cache alone, or online under ``authorized`` / stored permission."""
    from .citation_lookup import Lookup

    if not online:
        return Lookup(offline=True)
    return Lookup(authorized=authorized, consent=consent, transport=TRANSPORT, **LOOKUP_OPTIONS)


def refuse_over_transports(arguments):
    """Raise for the parts of ``bibliography`` that only the local CLI and desktop may run."""
    action = arguments.get('action', 'health')
    if action in LOCAL_ACTIONS:
        raise ByaxError(
            'unsupported_feature',
            'Fetching from outside services, reading Zotero and changing bibliography settings or '
            'installed styles are done through the local CLI or desktop',
        )
    if action == 'health' and any(arguments.get(k) for k in ('online', 'archive', 'allow_network')):
        raise ByaxError(
            'unsupported_feature',
            'Online bibliography checks are run through the local CLI or desktop; automation can read the cached report',
        )


def _slice(items, a):
    offset, limit = a.get('offset', 0), a.get('limit', DEFAULT_LIMIT)
    page = items[offset : offset + limit]
    more = offset + limit < len(items)
    return page, {
        'total': len(items),
        'offset': offset,
        'returned': len(page),
        'truncated': more,
        'continuation': offset + limit if more else None,
    }


def _write(path, text, overwrite):
    from .cli import _destination

    _destination(path, overwrite)
    target = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.byax-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)


def _apply(a, operations):
    from .cli import execute

    return execute(
        'apply',
        {
            'path': a['path'],
            'input': {'operations': operations},
            **{k: a[k] for k in ('expect_revision', 'request_id', 'dry_run') if k in a},
        },
    )


# --- bibliography -----------------------------------------------------------------------------------------


def _health(a, doc):
    from . import bibliography
    from .cli import envelope

    online = bool(a.get('online') or a.get('archive'))
    lookup = make_lookup(online=online, authorized=bool(a.get('allow_network')))
    report = bibliography.health(
        doc, lookup=lookup, checks=a.get('checks') or None, archive=bool(a.get('archive')), today=None
    )
    findings, window = _slice(report['findings'], a)
    sources, source_window = _slice(report['sources'], {'limit': a.get('limit', DEFAULT_LIMIT), 'offset': 0})
    lines = bibliography.describe(report)
    serious = report['summary']['by_severity']['error']
    attention = serious or report['summary']['by_severity']['warning']
    return envelope(
        {
            'report': {
                **report,
                'findings': findings,
                'findings_window': window,
                'sources': sources,
                'sources_window': source_window,
            },
            'lines': lines,
            'validation': 'failed' if serious else 'passed',
            'review': 'required' if attention else 'not_required',
        },
        doc,
    )


def _status(a, doc):
    from . import citation_lookup, csl_styles
    from .cli import envelope
    from .retrieval import ConsentRegistry

    consent = ConsentRegistry()
    return envelope(
        {
            'consent': {
                s: consent.allows(citation_lookup.CONSENT_PREFIX + s, 'citation_lookup')
                for s in citation_lookup.SERVICE_ORDER
            },
            'services': {s: {'name': v[0], 'sends': v[3]} for s, v in citation_lookup.SERVICES.items()},
            'mailto_configured': bool(citation_lookup.load_settings().get('mailto')),
            'retraction_index': citation_lookup.RetractionIndex().info(),
            'installed_styles': [s['name'] for s in csl_styles.list_styles()['styles'] if not s['built_in']],
            'references': len(doc.get('references', {})),
        },
        doc,
    )


def _ris_export(a, doc):
    from .citations import export_sources
    from .cli import envelope

    text = export_sources(doc.get('references', {}), 'ris')
    result: dict[str, Any] = {'format': 'ris', 'count': len(doc.get('references', {}))}
    if a.get('out'):
        result['outputs'] = [{'path': _write(a['out'], text, a.get('overwrite', False)), 'format': 'ris'}]
    else:
        result['text'] = text
    return envelope(result, doc)


def _ris_import(a, doc):
    from .citations import import_sources

    text = a.get('text')
    if text is None and a.get('source_path'):
        file = Path(a['source_path'])
        if not file.is_file() or file.stat().st_size > 8 * 1024 * 1024:
            _invalid('Name a RIS file of at most 8 MiB', field='source_path')
        text = file.read_text(encoding='utf-8-sig')
    if text is None:
        _invalid('Give the RIS text or a RIS file (source_path)', field='source_path')
    sources = import_sources(text, 'ris', existing=doc['references'])
    result = _apply(a, [{'type': 'add_reference', 'id': s['id'], 'values': s} for s in sources])
    result['imported'] = len(sources)
    return result


def _export_bib(a, doc):
    from . import zotero
    from .cli import envelope

    exported = zotero.export_bib(doc.get('references', {}), only_zotero=bool(a.get('only_zotero')))
    result: dict[str, Any] = {
        'format': 'bibtex',
        'count': exported['count'],
        'keys': exported['keys'],
        'from_zotero': exported['from_zotero'],
    }
    if a.get('out'):
        result['outputs'] = [
            {'path': _write(a['out'], exported['text'], a.get('overwrite', False)), 'format': 'bibtex'}
        ]
    else:
        result['text'] = exported['text']
    return envelope(result, doc)


def _zotero_import(a, doc):
    from . import zotero
    from .cli import envelope

    client = zotero.ZoteroClient(a.get('url') or zotero.BASE_URL, transport=TRANSPORT)
    read = zotero.import_from_zotero(
        client, doc['references'], collection=a.get('collection'), annotations=not a.get('no_annotations')
    )
    if a.get('dry_run'):
        return envelope(
            {'zotero': read['counts'], 'sources': [s['id'] for s in read['sources']], 'sent': False}, doc
        )
    result = _apply(a, [{'type': 'add_reference', 'id': s['id'], 'values': s} for s in read['sources']])
    result['zotero'] = read['counts']
    return result


def _document_free(a, action):
    from . import citation_lookup, csl_styles
    from .cli import envelope

    if action == 'styles-list':
        return envelope(csl_styles.list_styles())
    if action == 'styles-preview':
        _need(a, 'name')
        return envelope(_strip(csl_styles.preview(a['name'], transport=TRANSPORT)))
    if action == 'styles-add':
        if a.get('source_path'):
            return envelope(
                _strip(
                    csl_styles.add_from_file(
                        a['source_path'], name=a.get('name'), replace=bool(a.get('replace'))
                    )
                )
            )
        _need(a, 'name')
        if not a.get('allow_network'):
            raise ByaxError(
                'authorization_required',
                'Downloading a style contacts raw.githubusercontent.com. Preview it first (styles-preview), '
                'then repeat with --allow-network (and --expect-sha256 from the preview).',
                url=csl_styles.STYLE_URL.format(name=a['name']),
            )
        return envelope(
            _strip(
                csl_styles.add_from_repository(
                    a['name'],
                    expect_sha256=a.get('expect_sha256'),
                    replace=bool(a.get('replace')),
                    transport=TRANSPORT,
                )
            )
        )
    if action == 'styles-remove':
        _need(a, 'name')
        return envelope(csl_styles.remove(a['name']))
    if action == 'retractions-status':
        return envelope(
            {
                'retraction_index': citation_lookup.RetractionIndex().info(),
                'expected_columns': citation_lookup.RETRACTION_COLUMNS,
            }
        )
    if action == 'retractions-import':
        _need(a, 'source_path')
        return envelope(citation_lookup.import_retraction_watch(a['source_path']))
    if action == 'configure':
        values = a.get('input') or {}
        if not isinstance(values, dict):
            _invalid('configure takes an object such as {"mailto": "you@example.org"}', field='input')
        saved = citation_lookup.save_settings(values)
        return envelope({'mailto_configured': bool(saved.get('mailto'))})
    if action == 'consent':
        spec = a.get('input') or {}
        services = spec.get('services') or list(citation_lookup.SERVICE_ORDER)
        allowed = spec.get('allowed', True)
        if not isinstance(services, list) or type(allowed) is not bool:
            _invalid('consent takes {"services": [...], "allowed": true|false}', field='input')
        citation_lookup.Lookup(offline=True).grant(services, allowed)
        return envelope({'services': services, 'allowed': allowed})
    if action == 'clear-cache':
        return envelope({'removed': citation_lookup.Lookup(offline=True).clear_cache()})
    if action == 'zotero-collections':
        from . import zotero

        return envelope(
            {
                'collections': zotero.ZoteroClient(
                    a.get('url') or zotero.BASE_URL, transport=TRANSPORT
                ).collections()
            }
        )
    _invalid('Unknown bibliography action', action=action)


def _strip(result):
    """Drop the file bytes from a style result (they stay on disk or in the repository)."""
    return {k: v for k, v in result.items() if k != 'data'}


def bibliography_command(a, owner=None):
    from .cli import _load

    action = a.get('action', 'health')
    if action not in ACTIONS:
        _invalid('Unknown bibliography action', action=action, supported=list(ACTIONS))
    if action in DOCUMENT_FREE:
        return _document_free(a, action)
    _need(a, 'path')
    doc = _load(a['path'])
    if action == 'health':
        return _health(a, doc)
    if action == 'status':
        return _status(a, doc)
    if action == 'ris-export':
        return _ris_export(a, doc)
    if action == 'export-bib':
        return _export_bib(a, doc)
    if action == 'ris-import':
        return _ris_import(a, doc)
    return _zotero_import(a, doc)


# --- excerpt ------------------------------------------------------------------------------------------------


def excerpt_command(a, owner=None):
    from . import excerpts
    from .cli import _load, envelope

    action = a.get('action', 'where-used')
    if action not in EXCERPT_ACTIONS:
        _invalid('Unknown excerpt action', action=action, supported=list(EXCERPT_ACTIONS))
    doc = _load(a['path'])
    if action == 'where-used':
        report = excerpts.where_used(
            doc, a.get('source') or a.get('target'), include_unused=bool(a.get('include_unused'))
        )
        rows, window = _slice(report['sources'], a)
        return envelope({**report, 'sources': rows, 'window': window}, doc)
    if action == 'list':
        rows = [
            {
                'block_id': obj['id'],
                'start': mark['start'],
                'end': mark['end'],
                'source': mark.get('value'),
                'state': excerpts.excerpt_state(doc, mark),
                'locator': mark.get('locator', {}),
                'text': (obj.get('text') or '')[mark['start'] : mark['end']][: excerpts.SNIPPET],
            }
            for obj, mark in excerpts.excerpt_marks(doc)
        ]
        page, window = _slice(rows, a)
        return envelope({'excerpts': page, 'window': window}, doc)
    if action == 'check':
        return envelope(excerpts.check(doc), doc)
    if action == 'refresh':
        report = excerpts.check(doc)
        operations = []
        if report['source_operations']:
            operations.append(
                _proposal(
                    report['source_operations'], 'Update the source pointer after the library file changed.'
                )
            )
        if report['operations']:
            operations.append(
                _proposal(
                    report['operations'], 'Bring excerpts in line with their sources; no text is removed.'
                )
            )
        if not operations:
            return envelope({'proposals': [], **report}, doc)
        result = _apply({**a, 'expect_revision': a.get('expect_revision', doc['revision'])}, operations)
        result['excerpts_checked'] = report['summary']
        return result
    _need(a, 'source')
    planned = excerpts.add_operations(
        doc,
        source=a['source'],
        after=a.get('target'),
        chunk_id=a.get('chunk_id'),
        text=a.get('text'),
        start=a.get('start'),
        end=a.get('end'),
        style=a.get('style', 'quotation'),
    )
    result = _apply(
        {**a, 'expect_revision': a.get('expect_revision', doc['revision'])}, planned['operations']
    )
    result['excerpt'] = planned['excerpt']
    return result


def _proposal(operations, explanation):
    return {'type': 'propose', 'values': {'operations': operations, 'explanation': explanation}}
