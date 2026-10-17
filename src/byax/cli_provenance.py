"""``byax provenance``: the local provenance log, AI-use disclosures and the C2PA sidecar.

Dispatched by ``cli.execute``, so it is also the MCP tool ``byax_provenance`` and a REST command.
``report``, ``disclose`` and ``reanchor`` only read. ``propose-disclosure`` records a review
proposal, ``clear`` forgets the log (or the given targets) through ``engine.apply``, and
``export-c2pa`` writes a sidecar file; signing with ``key_file`` is local CLI and desktop only
(``transport_policy``). Nothing here uses the network.
"""

from __future__ import annotations

from typing import Any

from .errors import ByaxError

ACTIONS = ('report', 'disclose', 'propose-disclosure', 'export-c2pa', 'reanchor', 'clear')


def provenance_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    from . import disclosure, engine, provenance
    from .cli import _destination, _load, _mutate, envelope

    action = a.get('action', 'report')
    if action not in ACTIONS:
        raise ByaxError('invalid_input', f'Unknown provenance action: {action}', supported=list(ACTIONS))
    if action == 'report':
        doc = _load(a['path'])
        result = provenance.report(doc, a.get('target'), limit=a.get('limit', 500))
        result['lines'] = provenance.describe(result)
        return envelope(result, doc)
    if action == 'disclose':
        doc = _load(a['path'])
        return envelope(
            {'disclosure': disclosure.disclose(doc, a.get('style', 'apa'), target=a.get('target'))}, doc
        )
    if action == 'reanchor':
        doc = _load(a['path'])
        return envelope(provenance.reanchor_sources(doc), doc)
    if action == 'export-c2pa':
        if not a.get('out'):
            raise ByaxError(
                'invalid_input', 'Name the sidecar file with --out (for example book.c2pa.json)', field='out'
            )
        doc = _load(a['path'])
        _destination(a['out'], a.get('overwrite', False), a['path'])
        written = disclosure.export_c2pa(doc, a['out'], key_file=a.get('key_file'), overwrite=True)
        return envelope({'c2pa': written}, doc, outputs=[written['path']])
    request: dict[str, Any] = {}
    for option, key in (('expect_revision', 'expected_revision'), ('request_id', 'request_id')):
        if a.get(option) is not None:
            request[key] = a[option]
    dry_run = a.get('dry_run', False)

    def apply(doc):
        if action == 'clear':
            operation: dict[str, Any] = {'type': 'clear_provenance'}
            if a.get('target'):
                operation['targets'] = [a['target']]
        else:
            operation = disclosure.disclosure_proposal(doc, a.get('style', 'apa'), after=a.get('after'))
        request['operations'] = [operation]
        candidate, result = engine.apply(doc, request, dry_run=dry_run)
        if action == 'propose-disclosure' and not dry_run:
            result['proposal'] = candidate['review']['proposals'][-1]['id']
        return candidate, result, not dry_run

    return _mutate(a['path'], apply)
