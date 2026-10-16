"""``byax codex`` and the read-only ``byax entity`` (MCP ``byax_entity``), dispatched by ``cli.execute``.

Reads never show entries or traits hidden from AI unless the local CLI passes ``include_hidden``;
MCP and REST refuse that flag (``transport_policy``), so a model client only ever sees the AI view.
Edits are canonical ``codex_*`` operations applied through ``engine.apply``; ``propose-fact``
records a review proposal instead of editing.
"""

from __future__ import annotations

from typing import Any

from . import codex
from .errors import ByaxError

READ_ACTIONS = ('list', 'get', 'lookup', 'mentions', 'heatmap', 'series')
EDIT_ACTIONS = ('add', 'update', 'remove', 'add-trait', 'update-trait', 'remove-trait', 'propose-fact')
SERIES_ACTIONS = ('series-attach', 'series-detach', 'series-export')
# Attaching a series attaches its libraries: local CLI and desktop only (transport_policy).
LOCAL_ONLY_ACTIONS = ('series-attach', 'series-detach')


def _require(a, *keys):
    for key in keys:
        if a.get(key) in (None, ''):
            raise ByaxError('invalid_input', f'Missing required argument: {key}', field=key)


def _series(doc, path):
    from .series import resolve

    info = resolve(doc, path)
    return (info['series'] if info and info['state'] == 'ok' else None), info


def _read(action: str, a: dict[str, Any], doc: Any, path: Any) -> dict[str, Any]:
    series, info = _series(doc, path)
    hidden = bool(a.get('include_hidden'))
    if action == 'list':
        rows = codex.list_entries(doc, series=series, include_hidden=hidden, kind=a.get('kind'))
        total_hidden = len(codex.hidden_entry_ids(doc, series))
        result: dict[str, Any] = {'entries': rows, 'total': len(rows)}
        if not hidden:
            result['hidden_from_ai'] = total_hidden
        if info:
            result['series'] = {k: v for k, v in info.items() if k != 'series'}
        return result
    if action in ('get', 'lookup'):
        name = a.get('target') or a.get('name')
        if not name:
            raise ByaxError('invalid_input', 'Missing required argument: target or name', field='target')
        found = codex.lookup(doc, name, series=series, include_hidden=hidden)
        if action == 'get' and found['entry'] is None:
            raise ByaxError(
                'missing_target', 'Codex entry does not exist.', target=name, candidates=found['candidates']
            )
        return found
    if action == 'mentions':
        if a.get('target'):
            scope: Any = {'target': a['target']}
            for key in ('start', 'end', 'radius'):
                if a.get(key) is not None:
                    scope[key] = a[key]
        else:
            scope = a.get('text')
        return codex.mentions(
            doc,
            scope,
            series=series,
            for_ai=not hidden,
            limit=a.get('limit', 200),
            offset=a.get('offset', 0),
        )
    if action == 'heatmap':
        return codex.heatmap(doc, series=series, for_ai=not hidden)
    if info is None:
        return {'series': None}
    shown = {k: v for k, v in info.items() if k != 'series'}
    if info['state'] == 'ok':
        data = info['series']
        shown.update(
            id=data['id'],
            title=data.get('title', ''),
            entries=len(data['codex']['entries']),
            libraries=[x['library_id'] for x in data.get('libraries', [])],
            documents=list(data.get('documents', [])),
            overridden=codex.shadowed_series_entries(doc, data),
        )
    return {'series': shown}


def _operation(action: str, a: dict[str, Any], doc: Any, path: Any) -> dict[str, Any]:
    payload = a.get('input') or {}
    if action == 'add':
        values = dict(payload)
        for key in ('name', 'kind'):
            if a.get(key) is not None:
                values[key] = a[key]
        if a.get('hide_from_ai'):
            values['hide_from_ai'] = True
        values.setdefault('kind', 'character')
        op: dict[str, Any] = {'type': 'codex_add', 'values': values}
        if a.get('id'):
            op['id'] = a['id']
        return op
    if action == 'update':
        _require(a, 'target')
        values = dict(payload)
        for key in ('name', 'kind'):
            if a.get(key) is not None:
                values[key] = a[key]
        if a.get('hide_from_ai') is not None:
            values['hide_from_ai'] = a['hide_from_ai']
        return {'type': 'codex_update', 'target': a['target'], 'values': values}
    if action == 'remove':
        _require(a, 'target')
        return {'type': 'codex_remove', 'target': a['target']}
    if action == 'add-trait':
        _require(a, 'target', 'name', 'value')
        op = {'type': 'codex_add_trait', 'target': a['target'], 'name': a['name'], 'value': a['value']}
        if a.get('hide_from_ai'):
            op['hide_from_ai'] = True
        if payload.get('provenance'):
            op['provenance'] = payload['provenance']
        return op
    if action == 'update-trait':
        _require(a, 'target', 'trait')
        values = dict(payload)
        if a.get('value') is not None:
            values['value'] = a['value']
        if a.get('name') is not None:
            values['name'] = a['name']
        if a.get('hide_from_ai') is not None:
            values['hide_from_ai'] = a['hide_from_ai']
        return {'type': 'codex_update_trait', 'target': a['target'], 'trait': a['trait'], 'values': values}
    if action == 'remove-trait':
        _require(a, 'target', 'trait')
        return {'type': 'codex_remove_trait', 'target': a['target'], 'trait': a['trait']}
    # propose-fact
    _require(a, 'trait', 'value')
    entity = a.get('target') or a.get('name')
    if not entity:
        raise ByaxError('invalid_input', 'Missing required argument: target or name', field='target')
    series, _ = _series(doc, path)
    return codex.propose_fact(
        doc,
        entry=entity,
        trait=a['trait'],
        value=a['value'],
        provenance=payload.get('provenance'),
        kind=a.get('kind') or 'character',
        explanation=payload.get('explanation'),
        author=a.get('author') or 'assistant',
        series=series,
    )


def _series_action(action: str, a: dict[str, Any], owner: Any) -> dict[str, Any]:
    from . import engine
    from .cli import _load, _mutate, envelope
    from .series import new_series, reference_for, save

    if action == 'series-export':
        _require(a, 'out')
        doc = _load(a['path'])
        wanted = (a.get('input') or {}).get('entries')
        entries = {
            key: entry
            for key, entry in (doc.get('codex') or {}).get('entries', {}).items()
            if wanted is None or key in wanted
        }
        libraries = [
            {k: v for k, v in item.items() if k in ('library_id', 'role')}
            for item in doc.get('settings', {}).get('libraries', [])
        ]
        data = new_series(a.get('name') or doc.get('metadata', {}).get('title', ''), entries, libraries)
        written = save(a['out'], data, overwrite=a.get('overwrite', False))
        return envelope({'series': written}, doc, outputs=[written['path']])
    if action == 'series-attach':
        _require(a, 'series_path')
        values: dict[str, Any] = {'series': reference_for(a['path'], a['series_path'])}
    else:
        # A null reference means no series (set_settings merges keys and cannot delete them).
        values = {'series': None}
    request: dict[str, Any] = {'operations': [{'type': 'set_settings', 'values': values}]}
    for option, key in (('expect_revision', 'expected_revision'), ('request_id', 'request_id')):
        if a.get(option) is not None:
            request[key] = a[option]
    dry_run = a.get('dry_run', False)

    def apply(doc):
        if action == 'series-detach' and not doc['settings'].get('series'):
            raise ByaxError('missing_target', 'The document has no series.', target='series')
        candidate, result = engine.apply(doc, request, dry_run=dry_run)
        return candidate, result, not dry_run

    return _mutate(a['path'], apply)


def codex_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    from . import engine
    from .cli import _load, _mutate, envelope

    action = a.get('action', 'list')
    if action in READ_ACTIONS:
        doc = _load(a['path'])
        return envelope(_read(action, a, doc, a['path']), doc)
    if action in SERIES_ACTIONS:
        return _series_action(action, a, owner)
    if action not in EDIT_ACTIONS:
        raise ByaxError(
            'invalid_input',
            f'Unknown codex action: {action}',
            supported=list(READ_ACTIONS + EDIT_ACTIONS + SERIES_ACTIONS),
        )
    request: dict[str, Any] = {}
    for option, key in (('expect_revision', 'expected_revision'), ('request_id', 'request_id')):
        if a.get(option) is not None:
            request[key] = a[option]
    if a.get('author'):
        request['author'] = a['author']
    dry_run = a.get('dry_run', False)

    def apply(doc):
        request['operations'] = [_operation(action, a, doc, a['path'])]
        candidate, result = engine.apply(doc, request, dry_run=dry_run)
        changed = [i for i in result.get('changed_ids', []) if codex.find_entry(candidate, i)]
        result['entries'] = [
            codex.summary(codex.find_entry(candidate, i), for_ai=not a.get('include_hidden')) for i in changed
        ]
        if action == 'propose-fact':
            result['proposal'] = candidate['review']['proposals'][-1]['id'] if not dry_run else None
        return candidate, result, not dry_run

    return _mutate(a['path'], apply)


def entity_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    """Read-only codex lookups for model clients: always the AI-visible view."""
    from .cli import _load, envelope

    action = a.get('action', 'lookup' if a.get('name') or a.get('target') else 'list')
    if action not in ('list', 'get', 'lookup', 'mentions', 'heatmap'):
        raise ByaxError(
            'unsupported_feature',
            'entity is read-only; edit the codex with the codex command (or propose a fact).',
            supported=['list', 'get', 'lookup', 'mentions', 'heatmap'],
        )
    doc = _load(a['path'])
    return envelope(_read(action, dict(a, include_hidden=False), doc, a['path']), doc)
