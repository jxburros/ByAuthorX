"""Explicit, deterministic document schema transitions.

Every supported historical version has exactly one registered transition to its
successor. Migration copies its input, upgrades the live document and every
retained history state, reports each transformation, and validates identities
and references before the result can be saved. Unknown versions are rejected
without rewriting anything.
"""

import copy

from .errors import ByaxError
from .model import SCHEMA_VERSION, citation_anchor_problem, dependency_id, validate_document


def _v1_to_v2(state):
    """Schema 2 adds explicit external dependency records (spec 5.2)."""
    transformations = []
    existing = state.get('dependencies')
    if existing is not None:
        raise ByaxError(
            'unsupported_feature',
            'A version 1 document already contains an unknown "dependencies" construct; '
            'it cannot be upgraded without risking loss.',
            field='dependencies',
        )
    dependencies = {}
    for block in state.get('blocks', []):
        attrs = block.get('attrs', {})
        source = attrs.get('source')
        if (
            block.get('type') == 'image'
            and isinstance(source, str)
            and source
            and not source.startswith('data:')
            and 'asset' not in attrs
            and 'link' not in attrs
        ):
            identity = dependency_id(source, block['id'])
            dependencies[identity] = {
                'id': identity,
                'uri': source,
                'kind': 'image',
                'policy': 'link',
                'status': 'unresolved',
                'sha256': None,
                'asset': None,
            }
            attrs['link'] = identity
            transformations.append(
                {
                    'kind': 'external_image_dependency',
                    'target': block['id'],
                    'dependency': identity,
                    'uri': source,
                }
            )
        for index, image in enumerate(attrs.get('external_images', [])):
            uri = image.get('src') if isinstance(image, dict) else None
            if isinstance(uri, str) and uri and 'link' not in image:
                identity = dependency_id(uri, f'{block["id"]}#{index}')
                dependencies[identity] = {
                    'id': identity,
                    'uri': uri,
                    'kind': 'image',
                    'policy': 'link',
                    'status': 'unresolved',
                    'sha256': None,
                    'asset': None,
                }
                image['link'] = identity
                transformations.append(
                    {
                        'kind': 'inline_image_dependency',
                        'target': block['id'],
                        'dependency': identity,
                        'uri': uri,
                    }
                )
    state['dependencies'] = dependencies
    transformations.append({'kind': 'added_resource', 'field': 'dependencies', 'count': len(dependencies)})
    return transformations


def _v2_to_v3(state):
    """Schema 3 adds the optional manuscript ``project`` binder (CP A1), the optional
    ``codex`` (CE 8.2), the optional ``excerpt`` mark (CE 9, A2: a source back-pointer on excerpted
    text) and the optional citation ``anchor`` object (CE 6.5: quote,
    32-character source prefix and suffix, position, content hash and anchored/unanchored
    state). No data changes: the binder, codex and anchors are absent until created.
    Citation ``prefix``/``suffix`` stay what they always were, rendering affixes such as
    ``see ``; they are only checked to be text.
    """
    for field in ('project', 'codex'):
        if field in state:
            raise ByaxError(
                'unsupported_feature',
                f'A version 2 document already contains an unknown "{field}" construct; '
                'it cannot be upgraded without risking loss.',
                field=field,
            )
    affixes = anchors = 0
    for block in state.get('blocks', []):
        for mark in block.get('marks', []):
            if isinstance(mark, dict) and mark.get('type') == 'excerpt':
                raise ByaxError(
                    'unsupported_feature',
                    'A version 2 document already contains an unknown "excerpt" mark; '
                    'it cannot be upgraded without risking loss.',
                    target=block.get('id'),
                    field='marks.excerpt',
                )
            if not isinstance(mark, dict) or mark.get('type') != 'citation':
                continue
            if any(not isinstance(mark.get(key, ''), str) for key in ('prefix', 'suffix')):
                raise ByaxError(
                    'unsupported_feature',
                    'A citation mark has a non-text prefix or suffix; it cannot be upgraded without risking loss.',
                    target=block.get('id'),
                )
            if 'anchor' in mark and citation_anchor_problem(mark['anchor']):
                raise ByaxError(
                    'unsupported_feature',
                    'A version 2 citation mark already uses "anchor" for something else; '
                    'it cannot be upgraded without risking loss.',
                    target=block.get('id'),
                    field='marks.anchor',
                )
            affixes += any(key in mark for key in ('prefix', 'suffix'))
            anchors += 'anchor' in mark
    return [
        {'kind': 'added_optional_structure', 'field': 'project', 'count': 0},
        {'kind': 'added_optional_structure', 'field': 'codex', 'count': 0},
        {'kind': 'citation_anchor_fields', 'field': 'marks.anchor', 'count': anchors},
        {'kind': 'citation_affixes_checked', 'field': 'marks.prefix/suffix', 'count': affixes},
        {'kind': 'added_optional_structure', 'field': 'marks.excerpt', 'count': 0},
    ]


# from_version: (to_version, description, transition)
TRANSITIONS = {
    1: (2, 'Explicit external dependency records for linked images', _v1_to_v2),
    2: (3, 'Optional manuscript project binder, codex and citation anchors', _v2_to_v3),
}
SUPPORTED_VERSIONS = frozenset(TRANSITIONS) | {SCHEMA_VERSION}


def _comments(items):
    pending = list(items)
    while pending:
        item = pending.pop()
        pending.extend(item.get('replies', []))
        yield item


def reference_problems(doc):
    """Dangling identities, hashes and references, independent of schema version."""
    problems = set()
    ids = set()
    for block in doc.get('blocks', []):
        ids.add(block.get('id'))
        for row in block.get('rows', []):
            ids.add(row.get('id'))
            ids.update(cell.get('id') for cell in row.get('cells', []))
    assets, dependencies = doc.get('assets', {}), doc.get('dependencies', {})
    for block in doc.get('blocks', []):
        attrs = block.get('attrs', {})
        if 'asset' in attrs and attrs['asset'] not in assets:
            problems.add(('asset', block['id'], attrs['asset']))
        if 'link' in attrs and attrs['link'] not in dependencies:
            problems.add(('dependency', block['id'], attrs['link']))
        for mark in block.get('marks', []):
            if mark.get('type') in ('citation', 'reference') and mark.get('value') not in doc.get(
                'references', {}
            ):
                problems.add(('reference', block['id'], mark.get('value')))
    for identity, dependency in dependencies.items():
        if isinstance(dependency, dict) and dependency.get('asset') not in (None, *assets):
            problems.add(('dependency_asset', identity, dependency.get('asset')))
    review = doc.get('review', {})
    for collection, items in (
        ('comment', _comments(review.get('comments', []))),
        ('proposal', review.get('proposals', [])),
        ('tracked_change', review.get('tracked_changes', [])),
    ):
        for item in items:
            if item.get('target') and item['target'] not in ids and item.get('status') != 'orphaned':
                problems.add((collection, item.get('id'), item['target']))
    for name, bookmark in doc.get('settings', {}).get('bookmarks', {}).items():
        if bookmark.get('target') not in ids and bookmark.get('status') != 'orphaned':
            problems.add(('bookmark', name, bookmark.get('target')))
    project = doc.get('project')
    if isinstance(project, dict) and isinstance(project.get('items'), dict):
        for identity, item in project['items'].items():
            if isinstance(item, dict) and item.get('heading') not in ids:
                problems.add(('project_item', identity, item.get('heading')))
    return problems


def _history_states(history):
    from .engine import _patch

    state = None
    for record in history.get('revisions', []):
        if 'snapshot' in record:
            state = copy.deepcopy(record['snapshot'])
        else:
            if state is None:
                raise ByaxError(
                    'invalid_input',
                    'History delta has no reconstructable base.',
                    target=record.get('revision'),
                )
            state = _patch(state, record['delta'])
        yield record, copy.deepcopy(state)


def _version(state):
    version = state.get('schema_version')
    if type(version) is not int or version not in SUPPORTED_VERSIONS:
        raise ByaxError(
            'unsupported_version',
            f'Document schema version {version!r} is not supported; the file was not modified.',
            found=version,
            supported=sorted(SUPPORTED_VERSIONS),
        )
    return version


def _upgrade(state, steps=None):
    while _version(state) != SCHEMA_VERSION:
        source = state['schema_version']
        target, description, transition = TRANSITIONS[source]
        transformations = transition(state)
        state['schema_version'] = target
        if steps is not None:
            steps.append(
                {'from': source, 'to': target, 'description': description, 'transformations': transformations}
            )
    return state


def migrate_document(doc):
    """Return an upgraded copy and a transformation report; ``doc`` is never modified."""
    if not isinstance(doc, dict):
        raise ByaxError('invalid_input', 'Document must be an object.')
    source_version = _version(doc)
    if source_version == SCHEMA_VERSION:
        return doc, None
    from .engine import _delta

    before = reference_problems(doc)
    result = copy.deepcopy(doc)
    steps = []
    live = _upgrade({k: v for k, v in result.items() if k not in ('history', 'requests')}, steps)
    history = result.get('history', {})
    rebuilt, previous = [], None
    for record, state in _history_states(history):
        upgraded = _upgrade(state)
        retained = {k: v for k, v in record.items() if k not in ('snapshot', 'delta')}
        if previous is None or 'snapshot' in record:
            retained['snapshot'] = upgraded
        else:
            retained['delta'] = _delta(previous, upgraded)
        rebuilt.append(retained)
        previous = copy.deepcopy(upgraded)
    migrated = {**live, 'history': {**history, 'revisions': rebuilt}, 'requests': result.get('requests', {})}
    introduced = reference_problems(migrated) - before
    if introduced:
        raise ByaxError(
            'migration_failed',
            'Migration would introduce dangling identities or references; the original is unchanged.',
            problems=sorted(map(list, introduced)),
        )
    validate_document(migrated)
    report = {
        'from_version': source_version,
        'to_version': SCHEMA_VERSION,
        'document_id': migrated['id'],
        'revision': migrated['revision'],
        'steps': steps,
        'history_revisions_migrated': len(rebuilt),
        'preexisting_reference_problems': sorted(map(list, before)),
    }
    return migrated, report
