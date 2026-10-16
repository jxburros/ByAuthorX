"""Manuscript projects: a binder of sections inside one ``.byax`` master (CP A1).

The optional top-level ``project`` object binds heading blocks to binder items.
Each item has a stable ID, a kind, a status and optional authored metadata; the
text stays in the document body. Binder order is the body order of the bound
headings and nesting follows heading levels (the semantic outline), so checks,
exports, history and proposals keep working unchanged. A block belongs to the
innermost item whose section contains it.

Items whose heading disappears (deleted, joined or demoted outside binder
operations) are reported as orphaned by :func:`reconcile` and in ``engine.apply``
results; they are never dropped silently. Mutations are canonical operations
(``project_*``) dispatched through ``engine.apply``.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from typing import Any

from . import anchors
from .errors import ByaxError
from .model import new_id, object_hash, text_of
from .statistics import counts

KINDS = ('part', 'chapter', 'scene', 'note', 'front', 'back')
# Suggested workflow statuses. Any short label is accepted so writers can use their own.
DEFAULT_STATUSES = ('to_do', 'draft', 'revised', 'final')
DEFAULT_STATUS = 'to_do'
ITEM_FIELDS = frozenset(
    ('id', 'heading', 'kind', 'status', 'synopsis', 'target_words', 'label', 'include_in_compile')
)
META_FIELDS = ('kind', 'status', 'synopsis', 'target_words', 'label', 'include_in_compile', 'heading')
PROJECT_FIELDS = frozenset(('items', 'target_words'))
MAX_SYNOPSIS = 20000
MAX_LABEL = 64
MAX_TARGET = 10_000_000

Doc = dict[str, Any]


def item_id_for(heading_id: str) -> str:
    """Deterministic binder item identity for a heading, so binding is reproducible."""
    return 'item_' + hashlib.sha256(heading_id.encode()).hexdigest()[:24]


def default_include(kind: str) -> bool:
    """Notes (research, character sheets) are excluded from compile unless chosen."""
    return kind != 'note'


# Validation (called from model.validate_document for schema 3)


def _scalar_text(value):
    return isinstance(value, str) and not any(0xD800 <= ord(c) <= 0xDFFF for c in value)


def _valid_target(value):
    return type(value) is int and 1 <= value <= MAX_TARGET


def _valid_status(value):
    return _scalar_text(value) and 0 < len(value) <= MAX_LABEL and value.strip() == value


def validate_project(doc: Doc, fail: Callable[[str, str], Any]) -> None:
    """Structure and types only; dangling headings are orphans, reported rather than rejected."""
    if 'project' not in doc:
        return
    project = doc['project']
    if (
        not isinstance(project, dict)
        or set(project) - PROJECT_FIELDS
        or not isinstance(project.get('items'), dict)
    ):
        fail('A project requires an items mapping and only known fields.', 'project')
    if 'target_words' in project and not _valid_target(project['target_words']):
        fail('Word targets must be integers from 1 to 10,000,000.', 'project.target_words')
    objects = set()
    for block in doc['blocks']:
        objects.add(block.get('id'))
        for row in block.get('rows', []):
            objects.add(row.get('id'))
            objects.update(cell.get('id') for cell in row.get('cells', []))
    headings = set()
    for key, item in project['items'].items():
        path = f'project.items.{key}'
        if not isinstance(key, str) or not key or not isinstance(item, dict) or item.get('id') != key:
            fail('Binder items require a nonempty identity matching their key.', path)
        if set(item) - ITEM_FIELDS:
            fail('Unknown binder item field: ' + ', '.join(sorted(set(item) - ITEM_FIELDS)), path)
        if key in objects:
            fail('Binder item identity collides with a document object.', path + '.id')
        heading = item.get('heading')
        if not isinstance(heading, str) or not heading:
            fail('Binder items require a heading identity.', path + '.heading')
        if heading in headings:
            fail('Two binder items bind the same heading.', path + '.heading')
        headings.add(heading)
        if item.get('kind') not in KINDS:
            fail('Binder item kind must be part, chapter, scene, note, front or back.', path + '.kind')
        if not _valid_status(item.get('status')):
            fail('Binder status must be a trimmed label of 1 to 64 characters.', path + '.status')
        if 'synopsis' in item and (
            not _scalar_text(item['synopsis']) or len(item['synopsis']) > MAX_SYNOPSIS
        ):
            fail('Synopsis must be text of at most 20,000 characters.', path + '.synopsis')
        if 'label' in item and (not _scalar_text(item['label']) or len(item['label']) > MAX_LABEL):
            fail('Label must be text of at most 64 characters.', path + '.label')
        if 'target_words' in item and not _valid_target(item['target_words']):
            fail('Word targets must be integers from 1 to 10,000,000.', path + '.target_words')
        if 'include_in_compile' in item and type(item['include_in_compile']) is not bool:
            fail('include_in_compile must be a boolean.', path + '.include_in_compile')


# Queries


def _project_items(doc):
    project = doc.get('project')
    return project.get('items', {}) if isinstance(project, dict) else {}


def _level(block):
    return block.get('attrs', {}).get('level', 1)


def _outline(doc: Doc) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """One pass over the body: binder rows in order and the owning item of each block."""
    by_heading = {item['heading']: item for item in _project_items(doc).values()}
    blocks = doc['blocks']
    rows: list[dict[str, Any]] = []
    owner: dict[str, str] = {}
    stack: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if block.get('type') == 'heading':
            level = _level(block)
            while stack and stack[-1]['level'] >= level:
                stack.pop()['end'] = index
            item = by_heading.get(block['id'])
            if item is not None:
                row = {
                    'id': item['id'],
                    'heading': block['id'],
                    'title': block.get('text', ''),
                    'level': level,
                    'depth': len(stack),
                    'parent': stack[-1]['id'] if stack else None,
                    'children': [],
                    'index': index,
                    'end': len(blocks),
                    'block_ids': [],
                }
                if stack:
                    stack[-1]['children'].append(item['id'])
                rows.append(row)
                stack.append(row)
        if stack:
            stack[-1]['block_ids'].append(block['id'])
            owner[block['id']] = stack[-1]['id']
    return rows, owner


def _orphans(doc, rows=None):
    rows = _outline(doc)[0] if rows is None else rows
    bound = {row['id'] for row in rows}
    kinds = {block['id']: block.get('type') for block in doc['blocks']}
    return [
        {
            'item': item['id'],
            'heading': item['heading'],
            'kind': item['kind'],
            'reason': 'missing' if item['heading'] not in kinds else 'not_a_heading',
        }
        for item in sorted(_project_items(doc).values(), key=lambda value: value['id'])
        if item['id'] not in bound
    ]


def find_item(doc: Doc, identity: str) -> dict[str, Any] | None:
    """The binder item record with this identity, if any."""
    item = _project_items(doc).get(identity)
    return item if isinstance(item, dict) else None


def orphaned_ids(doc: Doc) -> set[str]:
    return {entry['item'] for entry in _orphans(doc)}


def _effective(doc: Doc, rows: list[dict[str, Any]]) -> dict[str, bool]:
    """Effective compile inclusion; an excluded ancestor excludes its descendants."""
    items = _project_items(doc)
    included: dict[str, bool] = {}
    for row in rows:
        item = items[row['id']]
        own = item.get('include_in_compile', default_include(item['kind']))
        included[row['id']] = own and (row['parent'] is None or included[row['parent']])
    return included


def _word_counts(doc, rows):
    """Own and total (with descendants) authored words per row; counts are additive per block."""
    index = {block['id']: block for block in doc['blocks']}
    own = {
        row['id']: counts(text_of({'blocks': [index[i] for i in row['block_ids']]}))['words'] for row in rows
    }
    total = dict(own)
    for row in reversed(rows):
        if row['parent'] is not None:
            total[row['parent']] += total[row['id']]
    return own, total


def _section_ids(doc, row):
    return [block['id'] for block in doc['blocks'][row['index'] : row['end']]]


def items(doc: Doc) -> list[dict[str, Any]]:
    """Binder items in order with metadata, nesting and authored word counts.

    ``words`` counts the item's own blocks; ``total_words`` includes descendants.
    Counts use the shared authored counter (``statistics.counts``).
    """
    rows, _ = _outline(doc)
    project = _project_items(doc)
    included = _effective(doc, rows)
    own, total = _word_counts(doc, rows)
    result = []
    for row in rows:
        item = project[row['id']]
        entry = {
            'id': row['id'],
            'heading': row['heading'],
            'title': row['title'],
            'kind': item['kind'],
            'status': item['status'],
            'level': row['level'],
            'depth': row['depth'],
            'parent': row['parent'],
            'children': list(row['children']),
            'include_in_compile': included[row['id']],
            'words': own[row['id']],
            'total_words': total[row['id']],
        }
        for key in ('synopsis', 'target_words', 'label'):
            if key in item:
                entry[key] = item[key]
        result.append(entry)
    return result


def tree(doc: Doc) -> dict[str, Any]:
    """Nested binder plus orphaned items and document totals."""
    flat = items(doc)
    nodes = {entry['id']: dict(entry, children=[]) for entry in flat}
    roots = []
    for entry in flat:
        node = nodes[entry['id']]
        if entry['parent'] is None:
            roots.append(node)
        else:
            nodes[entry['parent']]['children'].append(node)
    stored = doc.get('project')
    project: dict[str, Any] = stored if isinstance(stored, dict) else {}
    result: dict[str, Any] = {
        'enabled': 'project' in doc,
        'items': roots,
        'orphaned': _orphans(doc),
        'words': counts(text_of(doc))['words'],
    }
    if 'target_words' in project:
        result['target_words'] = project['target_words']
    return result


def block_map(doc: Doc) -> list[dict[str, Any]]:
    """Per binder item, in binder order: heading ID and ordered block IDs.

    ``block_ids`` are the item's own blocks (heading first); ``section_block_ids``
    also include descendants. This is the mapping the shared project index uses.
    """
    rows, _ = _outline(doc)
    return [
        {
            'item': row['id'],
            'heading': row['heading'],
            'parent': row['parent'],
            'block_ids': list(row['block_ids']),
            'section_block_ids': _section_ids(doc, row),
        }
        for row in rows
    ]


def item_for_block(doc: Doc) -> dict[str, str]:
    """Owning binder item of every body block inside a bound section."""
    return _outline(doc)[1]


def compile_plan(doc: Doc) -> dict[str, Any]:
    """Body blocks in manuscript order with excluded binder items (notes by default) left out.

    Blocks outside every binder item (for example text before the first heading)
    are kept and listed as ``unassigned``.
    """
    rows, owner = _outline(doc)
    included = _effective(doc, rows)
    return {
        'items': [row['id'] for row in rows if included[row['id']]],
        'excluded': [row['id'] for row in rows if not included[row['id']]],
        'block_ids': [
            block['id'] for block in doc['blocks'] if block['id'] not in owner or included[owner[block['id']]]
        ],
        'unassigned': [block['id'] for block in doc['blocks'] if block['id'] not in owner],
    }


def reconcile(doc: Doc) -> dict[str, Any]:
    """Consistency report between the binder and the body; never changes the document."""
    rows, owner = _outline(doc)
    bound = {row['heading'] for row in rows}
    unbound = [
        {'id': block['id'], 'text': block.get('text', ''), 'level': _level(block)}
        for block in doc['blocks']
        if block.get('type') == 'heading' and block['id'] not in bound
    ]
    orphaned = _orphans(doc, rows)
    return {
        'enabled': 'project' in doc,
        'consistent': not orphaned,
        'orphaned': orphaned,
        'unbound_headings': unbound if 'project' in doc else [],
        'unassigned_blocks': [block['id'] for block in doc['blocks'] if block['id'] not in owner],
    }


def export_losses(doc: Doc, fmt: str) -> list[dict[str, Any]]:
    """Located losses for exchange formats: binder order survives as headings, metadata does not."""
    if fmt == 'byax' or not isinstance(doc.get('project'), dict):
        return []
    project = _project_items(doc)
    losses = []
    for row in _outline(doc)[0]:
        item = project[row['id']]
        fields = ['kind', 'status'] + [
            key for key in ('target_words', 'synopsis', 'label', 'include_in_compile') if key in item
        ]
        losses.append(
            {
                'code': 'binder_metadata',
                'severity': 'warning',
                'message': 'Binder order is exported as headings; item '
                + ', '.join(fields)
                + ' are not represented in this format.',
                'target': row['heading'],
                'item': row['id'],
                'fields': fields,
            }
        )
    for entry in _orphans(doc):
        losses.append(
            {
                'code': 'binder_orphaned_item',
                'severity': 'warning',
                'message': 'Orphaned binder item has no heading in the body and is not exported.',
                'target': entry['item'],
            }
        )
    if 'target_words' in doc['project']:
        losses.append(
            {
                'code': 'binder_metadata',
                'severity': 'warning',
                'message': 'The document word target is not represented in this format.',
                'target': 'project',
                'fields': ['target_words'],
            }
        )
    return losses


def new_orphans(before: Doc, after: Doc) -> list[dict[str, Any]]:
    """Items orphaned by an edit, for reporting in ``engine.apply`` results."""
    if not _project_items(after):
        return []
    previous = orphaned_ids(before) if _project_items(before) else set()
    return [entry for entry in _orphans(after) if entry['item'] not in previous]


# Canonical operations. Handlers use engine internals lazily (engine imports this module).

# Required fields, optional fields (same convention as engine.OP_SCHEMAS).
OP_SCHEMAS = {
    'project_init': ('', 'kinds'),
    'project_add_item': ('kind', 'target after before parent text level id heading values'),
    'project_remove_item': ('target', 'delete_content'),
    'project_move': ('target', 'after before parent'),
    'project_split': ('target at offset', 'text id heading kind values expected_text'),
    'project_merge': ('target next', ''),
    'project_set_status': ('target status', ''),
    'project_set_target': ('words', 'target'),
    'project_set_meta': ('target values', ''),
}
# Operations that change body content (kept apart from source corrections).
CONTENT_OPERATIONS = frozenset(
    ('project_add_item', 'project_remove_item', 'project_move', 'project_split', 'project_merge')
)


def _error(message, field=None, **details):
    raise ByaxError('schema_error', message, field=field, **details)


def _require_project(doc):
    project = doc.get('project')
    if not isinstance(project, dict):
        raise ByaxError(
            'missing_target', 'The document has no project binder; run project_init first.', target='project'
        )
    return project['items']


def _item(doc, op, key='target'):
    project = _require_project(doc)
    identity = op.get(key)
    if not isinstance(identity, str) or identity not in project:
        raise ByaxError('missing_target', 'Binder item does not exist.', target=identity, field=key)
    item = project[identity]
    if key == 'target' and op.get('expected_hash') and object_hash(item) != op['expected_hash']:
        raise ByaxError('precondition_failed', 'Binder item changed.', target=identity)
    return item


def _row(doc, item):
    rows, _ = _outline(doc)
    row = next((r for r in rows if r['id'] == item['id']), None)
    if row is None:
        raise ByaxError(
            'conflict',
            'Binder item is orphaned: its heading is not in the body. Rebind it with project_set_meta or remove it.',
            target=item['id'],
        )
    return row


def _check_identity(doc, identity, field):
    from .model import find_object

    if not isinstance(identity, str) or not identity:
        _error('Expected a nonempty identity.', field)
    if identity in _project_items(doc):
        raise ByaxError('conflict', 'Binder item identity already exists.', target=identity)
    try:
        find_object(doc, identity)
    except ByaxError:
        return identity
    raise ByaxError('conflict', 'Identity is already used by a document object.', target=identity)


def _check_heading_id(doc, identity):
    from .model import find_object

    if not isinstance(identity, str) or not identity:
        _error('Expected a nonempty heading identity.', 'heading')
    try:
        find_object(doc, identity)
    except ByaxError:
        if identity in _project_items(doc):
            raise ByaxError('conflict', 'Identity is used by a binder item.', target=identity) from None
        return identity
    raise ByaxError('conflict', 'Heading identity already exists.', target=identity)


def _set_meta(doc, item, values, *, allow_heading=False):
    if not isinstance(values, dict):
        _error('Expected an object of binder metadata.', 'values')
    unknown = set(values) - set(META_FIELDS if allow_heading else META_FIELDS[:-1])
    if unknown:
        _error('Unknown binder metadata fields.', 'values', unknown=sorted(unknown))
    for key, value in values.items():
        if value is None and key in ('synopsis', 'target_words', 'label', 'include_in_compile'):
            item.pop(key, None)
            continue
        if key == 'kind' and value not in KINDS:
            _error('Binder item kind must be part, chapter, scene, note, front or back.', 'values.kind')
        if key == 'status' and not _valid_status(value):
            _error('Binder status must be a trimmed label of 1 to 64 characters.', 'values.status')
        if key == 'synopsis' and (not _scalar_text(value) or len(value) > MAX_SYNOPSIS):
            _error('Synopsis must be text of at most 20,000 characters.', 'values.synopsis')
        if key == 'label' and (not _scalar_text(value) or len(value) > MAX_LABEL):
            _error('Label must be text of at most 64 characters.', 'values.label')
        if key == 'target_words' and not _valid_target(value):
            _error('Word targets must be integers from 1 to 10,000,000.', 'values.target_words')
        if key == 'include_in_compile' and type(value) is not bool:
            _error('include_in_compile must be a boolean.', 'values.include_in_compile')
        if key == 'heading':
            block = _heading_block(doc, value)
            if any(
                other['heading'] == block['id'] for other in _project_items(doc).values() if other is not item
            ):
                raise ByaxError('conflict', 'Heading is already bound to another binder item.', target=value)
        if key == 'synopsis' and value == '':
            item.pop('synopsis', None)
            continue
        item[key] = value


def _heading_block(doc, identity):
    from . import engine

    if not isinstance(identity, str):
        _error('Expected a heading identity.', 'target')
    block = engine._target(doc, {'target': identity})
    if block.get('type') != 'heading' or block not in doc['blocks']:
        _error('Binder items bind top-level heading blocks.', 'target', target=identity)
    return block


def _default_kinds(levels):
    sequence = ['chapter'] if len(levels) == 1 else ['chapter', 'scene'] if len(levels) == 2 else KINDS[:3]
    return {level: sequence[min(rank, len(sequence) - 1)] for rank, level in enumerate(levels)}


def _op_init(doc, op, base_revision, kind, changed, allow_policy_changes):
    project = doc.setdefault('project', {'items': {}})
    headings = [block for block in doc['blocks'] if block.get('type') == 'heading']
    kinds = _default_kinds(sorted({_level(block) for block in headings}))
    if 'kinds' in op:
        if not isinstance(op['kinds'], dict) or any(
            key not in {str(n) for n in range(1, 7)} or value not in KINDS
            for key, value in op['kinds'].items()
        ):
            _error('kinds maps heading levels "1".."6" to binder kinds.', 'kinds')
        kinds.update({int(key): value for key, value in op['kinds'].items()})
    bound = {item['heading'] for item in project['items'].values()}
    for block in headings:
        if block['id'] in bound:
            continue
        identity = _check_identity(doc, item_id_for(block['id']), 'id')
        project['items'][identity] = {
            'id': identity,
            'heading': block['id'],
            'kind': kinds[_level(block)],
            'status': DEFAULT_STATUS,
        }
        changed.append(identity)
    if not changed:
        changed.append('project')


def _op_add_item(doc, op, base_revision, kind, changed, allow_policy_changes):
    if op['kind'] not in KINDS:
        _error('Binder item kind must be part, chapter, scene, note, front or back.', 'kind')
    doc.setdefault('project', {'items': {}})
    placement = [key for key in ('after', 'before', 'parent') if key in op]
    if 'target' in op:
        if placement or {'text', 'level', 'heading'} & op.keys():
            _error('Bind an existing heading with target alone, or create one with text.', 'target')
        heading = _heading_block(doc, op['target'])
        if any(item['heading'] == heading['id'] for item in _project_items(doc).values()):
            raise ByaxError('conflict', 'Heading is already bound to a binder item.', target=heading['id'])
    else:
        if not isinstance(op.get('text'), str) or not op['text'].strip():
            _error('A new binder item needs heading text, or target an existing heading.', 'text')
        if len(placement) > 1:
            _error('Use one of after, before or parent.', placement[1])
        level, index = op.get('level'), len(doc['blocks'])
        if placement and op[placement[0]] is not None:
            anchor = _row(doc, _item(doc, op, placement[0]))
            if placement[0] == 'parent':
                level = anchor['level'] + 1 if level is None else level
                if type(level) is int and level <= anchor['level']:
                    raise ByaxError(
                        'conflict', 'A child heading must be deeper than its parent.', target=anchor['id']
                    )
                index = anchor['end']
            else:
                level = anchor['level'] if level is None else level
                index = anchor['index'] if placement[0] == 'before' else anchor['end']
        level = 1 if level is None else level
        if type(level) is not int or not 1 <= level <= 6:
            _error('Heading levels must be integers from one through six.', 'level')
        identity = _check_heading_id(doc, op['heading']) if 'heading' in op else new_id('h')
        style = f'heading{level}'
        heading = {
            'id': identity,
            'type': 'heading',
            'text': op['text'],
            'style': style if style in doc['styles'] else 'body',
            'marks': [],
            'attrs': {'level': level},
        }
        doc['blocks'].insert(index, heading)
        changed.append(identity)
    identity = _check_identity(doc, op.get('id', item_id_for(heading['id'])), 'id')
    item = {'id': identity, 'heading': heading['id'], 'kind': op['kind'], 'status': DEFAULT_STATUS}
    _set_meta(doc, item, op.get('values', {}))
    doc['project']['items'][identity] = item
    changed.append(identity)


def _op_remove_item(doc, op, base_revision, kind, changed, allow_policy_changes):
    from . import engine

    item = _item(doc, op)
    delete = op.get('delete_content', False)
    if type(delete) is not bool:
        _error('Expected a boolean.', 'delete_content')
    project = doc['project']['items']
    if delete:
        row = _row(doc, item)
        heading = engine._target(doc, {'target': row['heading']})
        members = engine._section_members(doc, heading)
        removed = set()
        for block in [b for b in doc['blocks'] if b['id'] in members]:
            engine._target(doc, {'target': block['id']})
            removed.add(block['id'])
            for table_row in block.get('rows', []):
                removed.add(table_row['id'])
                removed.update(cell['id'] for cell in table_row['cells'])
        rows, _ = _outline(doc)
        for other in rows:
            if other['heading'] in members and other['id'] != item['id']:
                del project[other['id']]
                changed.append(other['id'])
        doc['blocks'] = [block for block in doc['blocks'] if block['id'] not in members]
        anchors.orphan(doc, removed)
        changed.extend(sorted(members))
    del project[item['id']]
    changed.append(item['id'])


def _op_move(doc, op, base_revision, kind, changed, allow_policy_changes):
    from . import engine

    item = _item(doc, op)
    row = _row(doc, item)
    placement = [key for key in ('after', 'before', 'parent') if key in op]
    if len(placement) != 1:
        _error('Use exactly one of after, before or parent.', 'after')
    key = placement[0]
    if key == 'after' and op['after'] is None:
        moved = engine._move_section(doc, {'target': row['heading']})
    else:
        anchor = _row(doc, _item(doc, op, key))
        if anchor['id'] == item['id']:
            raise ByaxError(
                'conflict', 'A binder item cannot be moved relative to itself.', target=item['id']
            )
        if key == 'parent' and anchor['level'] >= row['level']:
            raise ByaxError(
                'conflict',
                'A section can only be placed inside a shallower heading; change heading levels first.',
                target=anchor['id'],
                level=row['level'],
                parent_level=anchor['level'],
            )
        moved = engine._move_section(
            doc,
            {'target': row['heading'], 'after': anchor['heading'] if key == 'after' else None},
            before=anchor['heading'] if key == 'before' else None,
            inside=anchor['heading'] if key == 'parent' else None,
        )
    anchors.report_moved(doc, moved)
    changed.append(item['id'])
    changed.extend(moved)


def _op_split(doc, op, base_revision, kind, changed, allow_policy_changes):
    from . import engine

    item = _item(doc, op)
    row = _row(doc, item)
    if not isinstance(op['at'], str) or op['at'] not in row['block_ids'][1:]:
        _error("The split block must be one of the item's own blocks after its heading.", 'at')
    block = engine._target(doc, {'target': op['at']})
    text = block.get('text', '')
    if 'expected_text' in op and text != op['expected_text']:
        raise ByaxError(
            'precondition_failed', 'Expected text does not match the split block.', target=block['id']
        )
    offset = op['offset']
    if offset and (block.get('type') not in ('paragraph', 'footnote') or offset > len(text)):
        _error(
            'Only paragraphs and footnotes can be split inside their text; use offset 0 to split before.',
            'offset',
        )
    engine._boundaries(text, offset, offset)
    source = engine.find_object(doc, row['heading'])
    title = op.get('text', source.get('text', ''))
    if not isinstance(title, str):
        _error('Expected a string.', 'text')
    if 0 < offset < len(text):
        engine._op_split_block(
            doc,
            {'target': block['id'], 'offset': offset},
            base_revision,
            'split_block',
            changed,
            allow_policy_changes,
        )
        position = doc['blocks'].index(block) + 1
    else:
        position = doc['blocks'].index(block) + (1 if offset else 0)
    identity = _check_heading_id(doc, op['heading']) if 'heading' in op else new_id('h')
    heading = {
        'id': identity,
        'type': 'heading',
        'text': title,
        'style': source.get('style', 'body'),
        'marks': [],
        'attrs': {'level': row['level']},
    }
    doc['blocks'].insert(position, heading)
    new_item = {
        'id': _check_identity(doc, op.get('id', item_id_for(identity)), 'id'),
        'heading': identity,
        'kind': item['kind'],
        'status': item['status'],
    }
    if 'include_in_compile' in item:
        new_item['include_in_compile'] = item['include_in_compile']
    if 'kind' in op:
        _set_meta(doc, new_item, {'kind': op['kind']})
    _set_meta(doc, new_item, op.get('values', {}))
    doc['project']['items'][new_item['id']] = new_item
    anchors.note(
        'identity_changes',
        {'operation': 'project_split', 'source': item['id'], 'created': new_item['id'], 'heading': identity},
    )
    changed.extend((item['id'], new_item['id'], identity))


def _op_merge(doc, op, base_revision, kind, changed, allow_policy_changes):
    from . import engine

    first, second = _item(doc, op), _item(doc, op, 'next')
    rows, _ = _outline(doc)
    position = next((i for i, r in enumerate(rows) if r['id'] == first['id']), None)
    if position is None:
        _row(doc, first)
    if position is None or position + 1 >= len(rows) or rows[position + 1]['id'] != second['id']:
        raise ByaxError('conflict', 'Only an item and the next binder item can be merged.', target=op['next'])
    keep, gone = rows[position], rows[position + 1]
    if gone['level'] < keep['level']:
        raise ByaxError(
            'conflict',
            'Merging a shallower item into a deeper one would re-parent its siblings.',
            target=gone['id'],
        )
    heading = engine._target(doc, {'target': gone['heading']})
    pending = [
        change['id']
        for change in doc['review']['tracked_changes']
        if change.get('target') == heading['id'] and change.get('status') == 'pending'
    ]
    if pending:
        raise ByaxError(
            'conflict',
            'Accept or reject the tracked changes in the merged heading first.',
            target=heading['id'],
            changes=pending,
        )
    # Anchors on the removed heading move to the seam: the merged item's first own
    # content block, or the surviving heading when the merged item has no content.
    seam = gone['block_ids'][1] if len(gone['block_ids']) > 1 else keep['heading']
    for name, bookmark in doc['settings'].get('bookmarks', {}).items():
        if bookmark.get('target') == heading['id']:
            before = {'target': heading['id'], 'offset': bookmark.get('offset', 0)}
            bookmark['target'], bookmark['offset'] = seam, 0
            anchors.note(
                'anchor_mappings',
                {
                    'kind': 'bookmark',
                    'id': name,
                    'from': before,
                    'to': {'target': seam, 'offset': 0},
                    'status': 'moved',
                },
            )
    for comment in anchors.comments(doc):
        if comment.get('target') != heading['id'] or comment.get('parent_id'):
            continue
        before = {'target': heading['id'], 'start': comment.get('start'), 'end': comment.get('end')}
        if comment.get('start') is not None:
            comment.setdefault('quote', heading['text'][comment['start'] : comment['end']])
        comment.pop('start', None)
        comment.pop('end', None)
        comment.update(target=seam, anchor_status='needs_review', merged_from=heading['id'])
        anchors._sync_replies(comment)
        anchors.note(
            'anchor_mappings',
            {
                'kind': 'comment',
                'id': comment['id'],
                'from': before,
                'to': {'target': seam},
                'status': 'needs_review',
            },
        )
    doc['blocks'].remove(heading)
    anchors.orphan(doc, {heading['id']})
    synopses = [value for value in (first.get('synopsis'), second.get('synopsis')) if value]
    if len(synopses) == 2:
        combined = '\n\n'.join(synopses)
        if len(combined) > MAX_SYNOPSIS:
            raise ByaxError(
                'limit_exceeded', 'The merged synopsis would exceed 20,000 characters.', target=first['id']
            )
        first['synopsis'] = combined
    elif synopses:
        first['synopsis'] = synopses[0]
    targets = [value for value in (first.get('target_words'), second.get('target_words')) if value]
    if targets:
        first['target_words'] = min(MAX_TARGET, sum(targets))
    del doc['project']['items'][second['id']]
    anchors.note(
        'identity_changes',
        {
            'operation': 'project_merge',
            'target': first['id'],
            'removed': second['id'],
            'heading': heading['id'],
        },
    )
    changed.extend((first['id'], second['id'], heading['id']))


def _op_set_status(doc, op, base_revision, kind, changed, allow_policy_changes):
    item = _item(doc, op)
    if not _valid_status(op['status']):
        _error('Binder status must be a trimmed label of 1 to 64 characters.', 'status')
    item['status'] = op['status']
    changed.append(item['id'])


def _op_set_target(doc, op, base_revision, kind, changed, allow_policy_changes):
    words = op['words']
    if words is not None and not _valid_target(words):
        _error('Word targets must be integers from 1 to 10,000,000, or null to clear.', 'words')
    if 'target' in op:
        holder = _item(doc, op)
        changed.append(holder['id'])
    else:
        _require_project(doc)
        holder = doc['project']
        changed.append('project')
    if words is None:
        holder.pop('target_words', None)
    else:
        holder['target_words'] = words


def _op_set_meta(doc, op, base_revision, kind, changed, allow_policy_changes):
    item = _item(doc, op)
    _set_meta(doc, item, copy.deepcopy(op['values']), allow_heading=True)
    changed.append(item['id'])


HANDLERS = {
    'project_init': _op_init,
    'project_add_item': _op_add_item,
    'project_remove_item': _op_remove_item,
    'project_move': _op_move,
    'project_split': _op_split,
    'project_merge': _op_merge,
    'project_set_status': _op_set_status,
    'project_set_target': _op_set_target,
    'project_set_meta': _op_set_meta,
}


# CLI, MCP and REST: ``byax project --action ...`` (dispatched through cli.execute).

READ_ACTIONS = {
    'list': lambda doc: {'items': items(doc), 'orphaned': _orphans(doc), 'enabled': 'project' in doc},
    'tree': tree,
    'reconcile': reconcile,
    'blocks': lambda doc: {'items': block_map(doc)},
    'compile': compile_plan,
}
# action: (operation type, {cli argument: operation field})
MUTATING_ACTIONS = {
    'init': ('project_init', {}),
    'add': (
        'project_add_item',
        {k: k for k in ('kind', 'target', 'after', 'before', 'parent', 'text', 'level', 'id', 'heading')},
    ),
    'remove': ('project_remove_item', {'target': 'target', 'delete_content': 'delete_content'}),
    'move': ('project_move', {'target': 'target', 'after': 'after', 'before': 'before', 'parent': 'parent'}),
    'split': (
        'project_split',
        {k: k for k in ('target', 'at', 'offset', 'text', 'id', 'heading', 'kind', 'expected_text')},
    ),
    'merge': ('project_merge', {'target': 'target', 'next': 'next'}),
    'set-status': ('project_set_status', {'target': 'target', 'status': 'status'}),
    'set-target': ('project_set_target', {'target': 'target', 'words': 'words'}),
    'set-meta': ('project_set_meta', {'target': 'target'}),
}


def operation_for(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Translate a ``project`` CLI action into one canonical operation."""
    if action not in MUTATING_ACTIONS:
        raise ByaxError(
            'invalid_input',
            f'Unknown project action: {action}',
            supported=sorted(READ_ACTIONS) + sorted(MUTATING_ACTIONS),
        )
    kind, fields = MUTATING_ACTIONS[action]
    op: dict[str, Any] = {'type': kind}
    for argument, field in fields.items():
        if argument in arguments:
            op[field] = arguments[argument]
    payload = arguments.get('input')
    if action == 'init' and payload:
        op['kinds'] = payload.get('kinds', payload)
    elif action == 'set-meta':
        op['values'] = payload or {}
    elif action in ('add', 'split') and payload:
        op['values'] = payload
    if action == 'move' and not {'after', 'before', 'parent'} & op.keys():
        op['after'] = None  # no placement: move to the end of the manuscript
    if action == 'set-target':
        if arguments.get('clear'):
            op['words'] = None
        elif 'words' not in op:
            raise ByaxError('invalid_input', 'set-target requires --words or --clear', field='words')
    return op


def project_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    from . import engine
    from .cli import _load, _mutate, envelope

    action = a.get('action', 'list')
    if action in READ_ACTIONS:
        doc = _load(a['path'])
        return envelope(READ_ACTIONS[action](doc), doc)
    request: dict[str, Any] = {'operations': [operation_for(action, a)]}
    for option, key in (('expect_revision', 'expected_revision'), ('request_id', 'request_id')):
        if a.get(option) is not None:
            request[key] = a[option]
    dry_run = a.get('dry_run', False)

    def apply(doc):
        candidate, result = engine.apply(doc, request, dry_run=dry_run)
        result['project'] = {'items': items(candidate), 'orphaned': _orphans(candidate)}
        return candidate, result, not dry_run

    return _mutate(a['path'], apply)
