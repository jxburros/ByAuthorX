"""JSON document model; offsets are Unicode scalar values, end exclusive."""

import copy
import hashlib
import json
import math
import uuid

import regex

from .errors import ByaxError

BLOCK_TYPES = {'paragraph', 'heading', 'table', 'image', 'page_break', 'section_break', 'toc', 'footnote'}
# Current document schema. Older supported versions are upgraded by explicit
# transitions in ``migration``; newer versions are rejected without rewriting.
SCHEMA_VERSION = 2
STYLE_KINDS = ('paragraph', 'character')
DEPENDENCY_POLICIES = ('link', 'embedded')
DEPENDENCY_STATUSES = ('unresolved', 'current', 'changed', 'missing', 'embedded')


def new_id(prefix='obj'):
    return f'{prefix}_{uuid.uuid4().hex}'


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def object_hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def new_document(title='Untitled', text=''):
    return {
        'schema_version': SCHEMA_VERSION,
        'id': new_id('doc'),
        'revision': new_id('rev'),
        'metadata': {'title': title, 'author': '', 'language': 'en'},
        'blocks': [
            {'id': new_id('p'), 'type': 'paragraph', 'text': line, 'style': 'body', 'marks': [], 'attrs': {}}
            for line in text.split('\n')
        ],
        'styles': {
            'body': {'font_family': 'DejaVu Sans', 'font_size': 11},
            'heading1': {'based_on': 'body', 'font_size': 24, 'bold': True},
            'heading2': {'based_on': 'body', 'font_size': 18, 'bold': True},
            'heading3': {'based_on': 'body', 'font_size': 14, 'bold': True},
            'heading4': {'based_on': 'body', 'font_size': 12, 'bold': True},
            'heading5': {'based_on': 'body', 'font_size': 11, 'bold': True},
            'heading6': {'based_on': 'body', 'font_size': 11, 'italic': True},
            'title': {'based_on': 'body', 'font_size': 30, 'bold': True},
            'subtitle': {'based_on': 'body', 'font_size': 16},
            'quotation': {'based_on': 'body', 'italic': True, 'left_margin': 24},
            'caption': {'based_on': 'body', 'font_size': 9, 'italic': True},
            'code': {'font_family': 'DejaVu Sans Mono', 'font_size': 10},
            'list': {'based_on': 'body', 'font_size': 11},
        },
        'layout': {
            'paper': 'Letter',
            'width': 612,
            'height': 792,
            'margins': {'top': 72, 'right': 72, 'bottom': 72, 'left': 72},
        },
        'assets': {},
        'dependencies': {},
        'references': {},
        'profiles': {},
        'review': {'comments': [], 'proposals': [], 'tracked_changes': [], 'findings': []},
        'fields': {},
        'layers': [],
        'history': {'revisions': [], 'branches': {}, 'current_branch': 'main'},
        'requests': {},
        'settings': {},
    }


def iter_blocks(doc):
    yield from doc.get('blocks', [])


def text_of(doc):
    return '\n'.join(
        '\n'.join('\t'.join(c.get('text', '') for c in r['cells']) for r in b['rows'])
        if b['type'] == 'table'
        else b.get('text', '')
        for b in iter_blocks(doc)
    )


def find_object(doc, target):
    for b in doc.get('blocks', []):
        if b.get('id') == target:
            return b
        for row in b.get('rows', []):
            if row.get('id') == target:
                return row
            for cell in row.get('cells', []):
                if cell.get('id') == target:
                    return cell
    for layer in doc.get('layers', []):
        if layer.get('id') == target:
            return layer
    raise ByaxError('missing_target', 'The target no longer exists.', target=target)


def dependency_id(uri, salt=''):
    """Deterministic identity for an external dependency record."""
    return 'dep_' + hashlib.sha256((uri + '\x00' + salt).encode()).hexdigest()[:32]


def link_external_image(doc, block, uri):
    """Record an image block's external source as an explicit, unretrieved dependency."""
    identity = dependency_id(uri, block['id'])
    doc.setdefault('dependencies', {})[identity] = {
        'id': identity,
        'uri': uri,
        'kind': 'image',
        'policy': 'link',
        'status': 'unresolved',
        'sha256': None,
        'asset': None,
    }
    block.setdefault('attrs', {})['link'] = identity
    return identity


def snapshot(doc):
    return copy.deepcopy({k: v for k, v in doc.items() if k not in {'history', 'requests'}})


def validate_document(doc, *, include_history=True):
    def fail(message, path):
        raise ByaxError('schema_error', message, field=path)

    if not isinstance(doc, dict) or type(doc.get('schema_version')) is not int:
        raise ByaxError('unsupported_version', 'Document schema version is missing.')
    if doc['schema_version'] != SCHEMA_VERSION:
        from .migration import SUPPORTED_VERSIONS

        raise ByaxError(
            'unsupported_version',
            f'Document schema version {doc["schema_version"]} is not the current version {SCHEMA_VERSION}.',
            found=doc['schema_version'],
            current=SCHEMA_VERSION,
            migration='available' if doc['schema_version'] in SUPPORTED_VERSIONS else 'unavailable',
        )
    for key in ('id', 'revision'):
        if not isinstance(doc.get(key), str) or not doc[key]:
            fail('A nonempty identity is required.', key)
    for key in (
        'metadata',
        'styles',
        'layout',
        'assets',
        'dependencies',
        'references',
        'profiles',
        'review',
        'fields',
        'history',
        'requests',
        'settings',
    ):
        if not isinstance(doc.get(key), dict):
            fail('Expected an object.', key)
    for key in ('blocks', 'layers'):
        if not isinstance(doc.get(key), list):
            fail('Expected an array.', key)
    seen = set()

    def identity(obj, path):
        if not isinstance(obj, dict) or not isinstance(obj.get('id'), str) or not obj['id']:
            fail('An object requires a stable ID.', path)
        if obj['id'] in seen:
            fail('Duplicate object identity.', path + '.id')
        seen.add(obj['id'])

    def text(value, path):
        if not isinstance(value, str) or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            fail('Expected Unicode scalar text.', path)

    for i, b in enumerate(doc['blocks']):
        path = f'blocks[{i}]'
        identity(b, path)
        if b.get('type') not in BLOCK_TYPES:
            fail('Unsupported block type; original file is preserved.', path + '.type')
        text(b.get('text', ''), path + '.text')
        if not isinstance(b.get('attrs', {}), dict) or not isinstance(b.get('marks', []), list):
            fail('Invalid block attributes or marks.', path)
        if b['type'] == 'heading' and (
            type(b.get('attrs', {}).get('level', 1)) is not int
            or not 1 <= b.get('attrs', {}).get('level', 1) <= 6
        ):
            fail('Heading levels must be integers from one through six.', path + '.attrs.level')
        boundaries = None
        for mark in b.get('marks', []):
            if (
                not isinstance(mark, dict)
                or type(mark.get('start')) is not int
                or type(mark.get('end')) is not int
                or not 0 <= mark['start'] <= mark['end'] <= len(b.get('text', ''))
            ):
                fail('Invalid text mark range.', path + '.marks')
            if not isinstance(mark.get('type'), str) or not mark['type']:
                fail('Text mark type must be a nonempty string.', path + '.marks')
            if boundaries is None:
                # Computed once per block; per-mark recomputation was quadratic.
                boundaries = {0} | {m.end() for m in regex.finditer(r'\X', b.get('text', ''))}
            if mark['start'] not in boundaries or mark['end'] not in boundaries:
                fail('Text mark cannot split a grapheme cluster.', path + '.marks')
        if b['type'] == 'table':
            rows = b.get('rows')
            if not isinstance(rows, list) or not rows:
                fail('Tables require at least one row.', path + '.rows')
            columns = None
            for ri, row in enumerate(rows):
                identity(row, path + f'.rows[{ri}]')
                cells = row.get('cells')
                if (
                    not isinstance(cells, list)
                    or not cells
                    or (columns is not None and columns != len(cells))
                ):
                    fail('Tables must be rectangular and nonempty.', path + '.rows')
                columns = len(cells)
                for cell in cells:
                    identity(cell, path + '.cells')
                    text(cell.get('text'), path + '.cells.text')
    for layer in doc['layers']:
        identity(layer, 'layers')
    for digest, asset in doc['assets'].items():
        if (
            not isinstance(digest, str)
            or not isinstance(asset, dict)
            or not isinstance(asset.get('mime'), str)
        ):
            fail('Invalid asset identity or metadata.', 'assets')
    for identity, dependency in doc['dependencies'].items():
        path = 'dependencies.' + str(identity)
        if (
            not isinstance(dependency, dict)
            or dependency.get('id') != identity
            or not isinstance(dependency.get('uri'), str)
            or not dependency['uri']
        ):
            fail('Dependencies require a matching identity and a URI.', path)
        if (
            dependency.get('policy') not in DEPENDENCY_POLICIES
            or dependency.get('status') not in DEPENDENCY_STATUSES
        ):
            fail('Unknown dependency policy or status.', path)
        if dependency.get('asset') is not None and dependency['asset'] not in doc['assets']:
            fail('Dependency cache refers to a missing asset.', path + '.asset')
    for key, style in doc['styles'].items():
        if not isinstance(style, dict):
            fail('Named styles must contain attribute objects.', 'styles.' + key)
        if style.get('kind', 'paragraph') not in STYLE_KINDS:
            fail('Style kind must be paragraph or character.', 'styles.' + key + '.kind')
    for key in ('width', 'height'):
        value = doc['layout'].get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            fail('Paper dimensions must be finite positive numbers.', 'layout.' + key)
    margins = doc['layout'].get('margins')
    if not isinstance(margins, dict) or any(
        type(margins.get(k)) not in (int, float) or not math.isfinite(margins[k]) or margins[k] < 0
        for k in ('top', 'right', 'bottom', 'left')
    ):
        fail('Margins require finite nonnegative measurements.', 'layout.margins')
    validate_layout_extensions(doc, fail)
    history = doc['history']
    if (
        not isinstance(history.get('revisions'), list)
        or not isinstance(history.get('branches'), dict)
        or not isinstance(history.get('current_branch'), str)
    ):
        fail('History requires revisions, branches and a current branch.', 'history')
    if include_history:
        revision_ids = set()
        for record in history['revisions']:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get('revision'), str)
                or record['revision'] in revision_ids
                or not any(isinstance(record.get(k), dict) for k in ('snapshot', 'delta'))
            ):
                fail('History records require unique revisions and snapshots or deltas.', 'history.revisions')
            revision_ids.add(record['revision'])
    settings = doc['settings']
    if 'track_changes' in settings and type(settings['track_changes']) is not bool:
        fail('Track changes must be a boolean.', 'settings.track_changes')
    if 'history_limit' in settings and (
        type(settings['history_limit']) is not int or settings['history_limit'] < 1
    ):
        fail('History limit must be a positive integer.', 'settings.history_limit')
    if 'request_retention' in settings and (
        type(settings['request_retention']) is not int or settings['request_retention'] < 1
    ):
        fail('Request retention must be a positive integer.', 'settings.request_retention')
    for name in ('requirements', 'bookmarks'):
        if name in settings and (
            not isinstance(settings[name], dict)
            or any(not isinstance(v, dict) for v in settings[name].values())
        ):
            fail('Expected a mapping of definition objects.', 'settings.' + name)
    if 'dictionary' in settings and (
        not isinstance(settings['dictionary'], list)
        or any(not isinstance(v, str) for v in settings['dictionary'])
    ):
        fail('Dictionary must be a list of words.', 'settings.dictionary')
    if 'protections' in settings and (
        not isinstance(settings['protections'], list)
        or any(
            not isinstance(v, dict)
            or set(v) != {'target', 'kind'}
            or any(not isinstance(v[k], str) for k in ('target', 'kind'))
            for v in settings['protections']
        )
    ):
        fail('Protections must contain target and kind strings.', 'settings.protections')
    for key in ('comments', 'proposals', 'tracked_changes', 'findings'):
        if not isinstance(doc['review'].get(key), list):
            fail('Expected a review array.', 'review.' + key)
    from .storage import _check_secrets

    _check_secrets(doc if include_history else {k: v for k, v in doc.items() if k != 'history'})
    try:
        canonical_json(doc if include_history else {k: v for k, v in doc.items() if k != 'history'})
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        fail(str(exc), '$')
    return doc


COLUMN_KEYS = {'count', 'gap', 'gaps', 'widths', 'ratios', 'balance'}
ROW_SPLIT_POLICIES = {'auto', 'allow', 'never'}
SECTION_STARTS = {'next_page', 'continuous', 'odd_page', 'even_page'}
XREF_SHOW = {'page', 'text', 'number', 'label'}


def _number(value, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def validate_columns(value, path, fail):
    """Columns are a count or {count, gap|gaps, widths|ratios, balance} in points."""
    if type(value) is int:
        if not 1 <= value <= 12:
            fail('Column count must be 1..12.', path)
        return
    if not isinstance(value, dict) or set(value) - COLUMN_KEYS:
        fail('Columns must be a count or an object with count, gap, gaps, widths, ratios and balance.', path)
    count = value.get('count', len(value.get('widths', [])) or 1)
    if type(count) is not int or not 1 <= count <= 12:
        fail('Column count must be 1..12.', path + '.count')
    if 'gap' in value and not _number(value['gap']):
        fail('Column gap must be a nonnegative number of points.', path + '.gap')
    for key, size in (('gaps', count - 1), ('widths', count), ('ratios', count)):
        if key in value and (
            not isinstance(value[key], list)
            or len(value[key]) != size
            or not all(_number(v, positive=key != 'gaps') for v in value[key])
        ):
            fail(f'Column {key} must list {size} numbers.', path + '.' + key)
    if 'balance' in value and type(value['balance']) is not bool:
        fail('Column balance must be a boolean.', path + '.balance')


def validate_master(name, master, fail, path='layout.masters'):
    if not isinstance(name, str) or not name or not isinstance(master, dict):
        fail('Page masters must be named objects.', path)
    if 'margins' in master and (
        not isinstance(master['margins'], dict) or not all(_number(v) for v in master['margins'].values())
    ):
        fail('Master margins require nonnegative numbers.', f'{path}.{name}.margins')
    for key in ('width', 'height'):
        if key in master and not _number(master[key], positive=True):
            fail('Master paper dimensions must be positive numbers.', f'{path}.{name}.{key}')
    if 'columns' in master:
        validate_columns(master['columns'], f'{path}.{name}.columns', fail)
    artwork = master.get('artwork', [])
    if not isinstance(artwork, list) or any(not isinstance(item, dict) for item in artwork):
        fail('Master artwork must be a list of objects.', f'{path}.{name}.artwork')


def validate_layout_extensions(doc, fail):
    """Additive checks for columns, masters, table pagination, layer groups and cross-references."""
    layout = doc['layout']
    masters = layout.get('masters', {})
    if not isinstance(masters, dict):
        fail('Page masters must be a mapping.', 'layout.masters')
    for name, master in masters.items():
        validate_master(name, master, fail)
    if 'columns' in layout:
        validate_columns(layout['columns'], 'layout.columns', fail)
    if layout.get('master') is not None and layout['master'] not in masters:
        fail('Assigned page master is not defined.', 'layout.master')
    for index, block in enumerate(doc['blocks']):
        attrs, path = block.get('attrs', {}), f'blocks[{index}].attrs'
        if block.get('type') == 'section_break':
            section = attrs.get('layout', {})
            if not isinstance(section, dict):
                fail('Section layout must be an object.', path + '.layout')
            for source, prefix in ((section, path + '.layout'), (attrs, path)):
                if 'columns' in source:
                    validate_columns(source['columns'], prefix + '.columns', fail)
                if source.get('master') is not None and source['master'] not in masters:
                    fail('Assigned page master is not defined.', prefix + '.master')
            if 'start' in attrs and attrs['start'] not in SECTION_STARTS:
                fail('Section start must be next_page, continuous, odd_page or even_page.', path + '.start')
        if block.get('type') == 'table':
            if 'row_split' in attrs and attrs['row_split'] not in ROW_SPLIT_POLICIES:
                fail('Table row_split must be auto, allow or never.', path + '.row_split')
            if 'repeat_header' in attrs and type(attrs['repeat_header']) is not bool:
                fail('Table repeat_header must be a boolean.', path + '.repeat_header')
            if 'continued_label' in attrs and not isinstance(attrs['continued_label'], str):
                fail('Table continued_label must be text.', path + '.continued_label')
        for key in ('outputs', 'exclude_outputs'):
            if key in attrs and (
                not isinstance(attrs[key], list) or not all(isinstance(v, str) for v in attrs[key])
            ):
                fail('Output policies must list output names.', path + '.' + key)
        for mark in block.get('marks', []):
            if mark.get('type') == 'xref':
                value = mark.get('value')
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get('target'), str)
                    or value.get('show', 'page') not in XREF_SHOW
                ):
                    fail('Cross-references require a target and a show mode.', f'blocks[{index}].marks')
    groups = {
        layer.get('id')
        for layer in doc['layers']
        if layer.get('kind') == 'group' or layer.get('type') == 'group'
    }
    for index, layer in enumerate(doc['layers']):
        if layer.get('group') is not None and (layer['group'] not in groups or layer.get('id') in groups):
            fail('Layer group must reference an existing, non-nested group layer.', f'layers[{index}].group')
        if 'outputs' in layer and (
            not isinstance(layer['outputs'], list) or not all(isinstance(v, str) for v in layer['outputs'])
        ):
            fail('Output policies must list output names.', f'layers[{index}].outputs')
