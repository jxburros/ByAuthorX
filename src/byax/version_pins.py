"""Version-pinned citations: detect a citation that points at a superseded source version.

Context engine plan section 10. Every citation already records the version it cites: a library
source pins ``library.content_hash`` (and a citation mark's ``anchor.content_hash``), and a source
that is another ``.byax`` draft pins ``byax = {"path", "document_id", "revision", "content_hash"}``
(``content_hash`` is the SHA-256 of the cited draft's body text, see :func:`draft_hash`).

:func:`check_pins` compares each cited source's pinned version with what is there now:

- ``current``: the pinned version is still the current one;
- ``superseded``: the source now has a newer version (a library document re-indexed with
  different content at the same path, or a ``.byax`` draft whose current revision differs; when
  the pinned revision is still in the draft's history the pin is known to be an *earlier* version);
- ``unavailable``: the source cannot be read (library not attached, draft file missing);
- ``unverified``: the library file changed since indexing; refresh the library first;
- ``unpinned``: the source records no version (manual and imported references).

A superseded citation is never hidden: it is reported as a finding ("cites an earlier version of
X"), and two kinds of operation are offered, both unapplied: ``operations`` record the superseded
state on the source (metadata only, so the citation audit and exports can show it), and
``proposals`` hold a review proposal that re-pins every citation of the source to the current
version once the writer has reviewed what changed. Rendered output is unchanged unless the writer
turns on ``settings.citations.annotate_superseded``, which appends a note to recorded superseded
citations (``citation_render``). Reads only local files and attached library indexes; no network.
"""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ByaxError

Doc = dict[str, Any]

STATES = ('current', 'superseded', 'unavailable', 'unverified', 'unpinned')
RULE = 'citations.superseded_version'
ANNOTATION = ' (cites an earlier version)'


def draft_hash(doc: Doc) -> str:
    """The version identity of a ``.byax`` draft used as a source: SHA-256 of its body text."""
    from .model import text_of

    return hashlib.sha256(text_of(doc).encode()).hexdigest()


def pointer_problem(pointer: Any) -> str | None:
    """Why a source's ``byax`` pointer is malformed, or None."""
    if not isinstance(pointer, dict) or set(pointer) - {'path', 'document_id', 'revision', 'content_hash'}:
        return 'Draft sources need a byax pointer with path, document_id, revision and content_hash.'
    if any(not isinstance(pointer.get(k), str) or not pointer[k] for k in ('path', 'content_hash')):
        return 'Draft sources need a relative path and a content hash.'
    path = PurePosixPath(pointer['path'])
    if path.is_absolute() or '\\' in pointer['path'] or path.suffix.lower() != '.byax':
        return 'A draft source path is a relative path to a .byax file.'
    return None


def _resolve_draft(document_path, relative):
    """The cited draft's absolute path; it must stay inside the citing document's parent folder."""
    if document_path is None:
        return None, 'Save the document to resolve the cited draft.'
    folder = Path(document_path).resolve().parent
    target = (folder / relative).resolve()
    if not target.is_relative_to(folder.parent):
        return None, "A cited draft must be in the document's folder or its parent folder."
    return target, None


def source_from_draft(
    document_path: str | os.PathLike[str], draft_path: str | os.PathLike[str]
) -> dict[str, Any]:
    """A reference record citing another ``.byax`` draft, pinned to its current version."""
    from .citations import validate_source
    from .storage import load_document

    target = Path(draft_path).resolve()
    if target.suffix.lower() != '.byax':
        raise ByaxError('invalid_input', 'A draft source must be a .byax document.', field='draft_path')
    folder = Path(document_path).resolve().parent
    if not target.is_relative_to(folder.parent):
        raise ByaxError(
            'invalid_input',
            "A cited draft must be in the document's folder or its parent folder.",
            field='draft_path',
        )
    cited = load_document(target)
    relative = Path(os.path.relpath(target, folder)).as_posix()
    ident = 'source_' + hashlib.sha256(('byax:' + cited['id']).encode()).hexdigest()[:24]
    metadata = cited.get('metadata', {})
    csl: dict[str, Any] = {'id': ident, 'type': 'manuscript', 'title': metadata.get('title') or target.stem}
    if metadata.get('author'):
        csl['author'] = [{'literal': metadata['author']}]
    source = {
        'id': ident,
        'csl': csl,
        'byax': {
            'path': relative,
            'document_id': cited['id'],
            'revision': cited['revision'],
            'content_hash': draft_hash(cited),
        },
        'verification': 'unconfirmed',
    }
    return validate_source(source, ident)


def _cited(doc):
    """``{source_id: [citation locations]}`` for every citation mark, in document order."""
    result: dict[str, list[dict[str, Any]]] = {}
    for block in doc.get('blocks', []):
        objects = (
            [c for r in block.get('rows', []) for c in r.get('cells', [])]
            if block.get('type') == 'table'
            else [block]
        )
        for obj in objects:
            for mark in obj.get('marks', []):
                if (
                    isinstance(mark, dict)
                    and mark.get('type') == 'citation'
                    and isinstance(mark.get('value'), str)
                ):
                    result.setdefault(mark['value'], []).append(
                        {
                            'target': obj['id'],
                            'start': mark['start'],
                            'end': mark['end'],
                            'text': obj.get('text', '')[mark['start'] : mark['end']],
                            'anchor_hash': (mark.get('anchor') or {}).get('content_hash'),
                        }
                    )
    return result


def _title(source, ident):
    return (source.get('csl') or {}).get('title') or source.get('title') or ident


def _library_state(doc, source, registry, cache_dir):
    from .grounding import _current_document, _library_index

    pointer = source['library']
    attached = {a.get('library_id') for a in doc.get('settings', {}).get('libraries', [])}
    if pointer.get('library_id') not in attached:
        return {'state': 'unavailable', 'reason': 'The cited library is not attached to this document.'}
    try:
        _, index = _library_index(pointer['library_id'], registry, cache_dir)
        current = _current_document(index, pointer)
    except (ByaxError, OSError, KeyError) as exc:
        return {'state': 'unavailable', 'reason': 'The cited library cannot be read: ' + str(exc)}
    if current is None:
        return {'state': 'unavailable', 'reason': 'The cited library document is no longer indexed.'}
    document_id, _, page = current
    if page.get('stale'):
        return {
            'state': 'unverified',
            'reason': 'The source file changed since it was indexed; refresh the library.',
        }
    now = {'document_id': document_id, 'content_hash': page['content_hash']}
    if page['content_hash'] == pointer['content_hash']:
        return {'state': 'current', 'current': now}
    return {'state': 'superseded', 'current': now, 'earlier': True}


def _draft_state(source, document_path):
    from .storage import load_document

    pointer = source['byax']
    problem = pointer_problem(pointer)
    if problem:
        return {'state': 'unavailable', 'reason': problem}
    target, reason = _resolve_draft(document_path, pointer['path'])
    if target is None:
        return {'state': 'unavailable', 'reason': reason}
    try:
        cited = load_document(target)
    except (ByaxError, OSError) as exc:
        return {'state': 'unavailable', 'reason': 'The cited draft cannot be read: ' + str(exc)}
    now = {'document_id': cited['id'], 'revision': cited['revision'], 'content_hash': draft_hash(cited)}
    if now['content_hash'] == pointer['content_hash']:
        return {'state': 'current', 'current': now}
    history = {r.get('revision') for r in cited.get('history', {}).get('revisions', [])}
    return {'state': 'superseded', 'current': now, 'earlier': pointer.get('revision') in history}


def _repin_operations(ident, source, citations, current):
    updated = copy.deepcopy(source)
    updated.pop('superseded_by', None)
    if 'library' in updated:
        updated['library'].update({k: v for k, v in current.items() if k in ('document_id', 'content_hash')})
    else:
        updated['byax'].update(current)
    updated['verification'] = 'unconfirmed'
    operations: list[dict[str, Any]] = [{'type': 'add_reference', 'id': ident, 'values': updated}]
    for citation in citations:
        if citation['anchor_hash'] and citation['anchor_hash'] != current['content_hash']:
            operations.append(
                {
                    'type': 'reanchor_citation',
                    'target': citation['target'],
                    'start': citation['start'],
                    'end': citation['end'],
                    'source': ident,
                    'anchor': {'_from_mark': True, 'content_hash': current['content_hash']},
                }
            )
    return operations


def _finalize_anchor_ops(doc, operations):
    """Fill re-pin anchor updates from the marks themselves (only the content hash changes)."""
    result = []
    for operation in operations:
        anchor = operation.get('anchor')
        if isinstance(anchor, dict) and anchor.pop('_from_mark', False):
            from .model import find_object

            obj = find_object(doc, operation['target'])
            mark = next(
                m
                for m in obj.get('marks', [])
                if m.get('type') == 'citation'
                and m.get('value') == operation['source']
                and (m['start'], m['end']) == (operation['start'], operation['end'])
            )
            operation['anchor'] = {**copy.deepcopy(mark['anchor']), 'content_hash': anchor['content_hash']}
        result.append(operation)
    return result


def check_pins(
    doc: Doc,
    *,
    document_path: str | os.PathLike[str] | None = None,
    registry: Any = None,
    cache_dir: Any = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Compare every cited source's pinned version with its current version (see module docs)."""
    sources, findings, operations, proposals = [], [], [], []
    summary = dict.fromkeys(STATES, 0)
    references = doc.get('references', {})
    for ident, citations in _cited(doc).items():
        source = references.get(ident)
        if not isinstance(source, dict):
            continue
        if isinstance(source.get('library'), dict) and source['library'].get('content_hash'):
            kind = 'library'
            pinned = {k: source['library'].get(k) for k in ('document_id', 'content_hash')}
            outcome = _library_state(doc, source, registry, cache_dir)
        elif isinstance(source.get('byax'), dict):
            kind = 'draft'
            pinned = {k: source['byax'].get(k) for k in ('document_id', 'revision', 'content_hash')}
            outcome = _draft_state(source, document_path)
        else:
            kind, pinned, outcome = 'other', {}, {'state': 'unpinned'}
        title = _title(source, ident)
        row = {
            'source_id': ident,
            'title': title,
            'kind': kind,
            'state': outcome['state'],
            'pinned': pinned,
            'citations': [{k: c[k] for k in ('target', 'start', 'end', 'text')} for c in citations],
            **{k: outcome[k] for k in ('current', 'reason') if k in outcome},
        }
        summary[row['state']] += 1
        if row['state'] == 'superseded':
            which = 'an earlier version' if outcome.get('earlier', True) else 'a different version'
            row['message'] = (
                f'Cites {which} of “{title}”. The citation is kept; review what changed, then re-pin it '
                'to the current version.'
            )
            record = {
                'content_hash': outcome['current']['content_hash'],
                **({'revision': outcome['current']['revision']} if 'revision' in outcome['current'] else {}),
            }
            if source.get('superseded_by') != record:
                operations.append(
                    {
                        'type': 'add_reference',
                        'id': ident,
                        'values': {**copy.deepcopy(source), 'superseded_by': record},
                    }
                )
            repin = _finalize_anchor_ops(doc, _repin_operations(ident, source, citations, outcome['current']))
            proposals.append(
                {
                    'type': 'propose',
                    'values': {
                        'operations': repin,
                        'explanation': f'Re-pin the citations of “{title}” to its current version after reviewing '
                        'what changed.',
                        'author': 'ByAuthorX',
                        'origin': 'version_pin',
                    },
                }
            )
            for citation in citations:
                findings.append(
                    {
                        'rule': RULE,
                        'family': 'factual',
                        'severity': 'warning',
                        'explanation': row['message'],
                        'affected_ids': [citation['target']],
                        'range': {'start': citation['start'], 'end': citation['end']},
                        'text': citation['text'],
                        'measured': {'pinned': pinned.get('content_hash'), 'current': record['content_hash']},
                        'source_versions': {ident: record['content_hash']},
                        'status': 'open',
                        'revision': revision or doc.get('revision'),
                    }
                )
        sources.append(row)
    return {
        'audit': 'version_pins',
        'sources': sources,
        'summary': summary,
        'findings': findings,
        'operations': operations,
        'proposals': proposals,
        'network': False,
        'note': 'Superseded citations are reported, never hidden. Operations record the superseded state; '
        'proposals re-pin after review. Nothing is applied.',
    }


def annotation_for(source: dict[str, Any], settings: dict[str, Any]) -> str:
    """The rendered note for a recorded superseded citation, when the writer turned annotation on."""
    if settings.get('annotate_superseded') is True and isinstance(source.get('superseded_by'), dict):
        return ANNOTATION
    return ''
