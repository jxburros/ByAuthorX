"""JSON document model; offsets are Unicode scalar values, end exclusive."""
import copy
import hashlib
import json
import math
import uuid

import regex

from .errors import ByaxError

BLOCK_TYPES = {'paragraph', 'heading', 'table', 'image', 'page_break', 'section_break', 'toc', 'footnote'}

def new_id(prefix='obj'):
    return f'{prefix}_{uuid.uuid4().hex}'

def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def object_hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()

def new_document(title='Untitled', text=''):
    return {'schema_version': 1, 'id': new_id('doc'), 'revision': new_id('rev'),
            'metadata': {'title': title, 'author': '', 'language': 'en'},
            'blocks': [{'id': new_id('p'), 'type': 'paragraph', 'text': line, 'style': 'body', 'marks': [], 'attrs': {}}
                       for line in text.split('\n')],
            'styles': {'body': {'font_family': 'DejaVu Sans', 'font_size': 11},
                       'heading1': {'font_size': 24, 'bold': True},
                       'heading2': {'font_size': 18, 'bold': True},
                       'heading3': {'font_size': 14, 'bold': True},
                       'heading4': {'font_size': 12, 'bold': True}, 'heading5': {'font_size': 11, 'bold': True},
                       'heading6': {'font_size': 11, 'italic': True}, 'title': {'font_size': 30, 'bold': True},
                       'subtitle': {'font_size': 16}, 'quotation': {'italic': True, 'left_margin': 24},
                       'caption': {'font_size': 9, 'italic': True}, 'code': {'font_family': 'DejaVu Sans Mono', 'font_size': 10},
                       'list': {'font_size': 11}},
            'layout': {'paper': 'Letter', 'width': 612, 'height': 792,
                       'margins': {'top': 72, 'right': 72, 'bottom': 72, 'left': 72}},
            'assets': {}, 'references': {}, 'profiles': {},
            'review': {'comments': [], 'proposals': [], 'tracked_changes': [], 'findings': []},
            'fields': {}, 'layers': [], 'history': {'revisions': [], 'branches': {}, 'current_branch': 'main'},
            'requests': {}, 'settings': {}}

def iter_blocks(doc):
    yield from doc.get('blocks', [])

def text_of(doc):
    return '\n'.join('\n'.join('\t'.join(c.get('text', '') for c in r['cells']) for r in b['rows'])
                     if b['type'] == 'table' else b.get('text', '') for b in iter_blocks(doc))

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

def snapshot(doc):
    return copy.deepcopy({k: v for k, v in doc.items() if k not in {'history', 'requests'}})

def validate_document(doc, *, include_history=True):
    def fail(message, path):
        raise ByaxError('schema_error', message, field=path)
    if not isinstance(doc, dict) or type(doc.get('schema_version')) is not int or doc['schema_version'] != 1:
        raise ByaxError('unsupported_version', 'Only document schema version 1 is supported.')
    for key in ('id', 'revision'):
        if not isinstance(doc.get(key), str) or not doc[key]:
            fail('A nonempty identity is required.', key)
    for key in ('metadata', 'styles', 'layout', 'assets', 'references', 'profiles', 'review', 'fields', 'history', 'requests', 'settings'):
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
        if b['type'] == 'heading' and (type(b.get('attrs', {}).get('level', 1)) is not int or not 1 <= b.get('attrs', {}).get('level', 1) <= 6):
            fail('Heading levels must be integers from one through six.', path + '.attrs.level')
        for mark in b.get('marks', []):
            if not isinstance(mark, dict) or type(mark.get('start')) is not int or type(mark.get('end')) is not int or not 0 <= mark['start'] <= mark['end'] <= len(b.get('text', '')):
                fail('Invalid text mark range.', path + '.marks')
            if not isinstance(mark.get('type'), str) or not mark['type']:
                fail('Text mark type must be a nonempty string.', path + '.marks')
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
                if not isinstance(cells, list) or not cells or (columns is not None and columns != len(cells)):
                    fail('Tables must be rectangular and nonempty.', path + '.rows')
                columns = len(cells)
                for cell in cells:
                    identity(cell, path + '.cells')
                    text(cell.get('text'), path + '.cells.text')
    for layer in doc['layers']:
        identity(layer, 'layers')
    for digest, asset in doc['assets'].items():
        if not isinstance(digest, str) or not isinstance(asset, dict) or not isinstance(asset.get('mime'), str):
            fail('Invalid asset identity or metadata.', 'assets')
    for key, style in doc['styles'].items():
        if not isinstance(style, dict):
            fail('Named styles must contain attribute objects.', 'styles.' + key)
    for key in ('width', 'height'):
        value = doc['layout'].get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            fail('Paper dimensions must be finite positive numbers.', 'layout.' + key)
    margins = doc['layout'].get('margins')
    if not isinstance(margins, dict) or any(type(margins.get(k)) not in (int, float) or not math.isfinite(margins[k]) or margins[k] < 0 for k in ('top', 'right', 'bottom', 'left')):
        fail('Margins require finite nonnegative measurements.', 'layout.margins')
    history = doc['history']
    if not isinstance(history.get('revisions'), list) or not isinstance(history.get('branches'), dict) or not isinstance(history.get('current_branch'), str):
        fail('History requires revisions, branches and a current branch.', 'history')
    if include_history:
        revision_ids = set()
        for record in history['revisions']:
            if not isinstance(record, dict) or not isinstance(record.get('revision'), str) or record['revision'] in revision_ids or not any(isinstance(record.get(k), dict) for k in ('snapshot', 'delta')):
                fail('History records require unique revisions and snapshots or deltas.', 'history.revisions')
            revision_ids.add(record['revision'])
    settings = doc['settings']
    if 'track_changes' in settings and type(settings['track_changes']) is not bool:
        fail('Track changes must be a boolean.', 'settings.track_changes')
    if 'history_limit' in settings and (type(settings['history_limit']) is not int or settings['history_limit'] < 1):
        fail('History limit must be a positive integer.', 'settings.history_limit')
    for name in ('requirements', 'bookmarks'):
        if name in settings and (not isinstance(settings[name], dict) or any(not isinstance(v, dict) for v in settings[name].values())):
            fail('Expected a mapping of definition objects.', 'settings.' + name)
    if 'dictionary' in settings and (not isinstance(settings['dictionary'], list) or any(not isinstance(v, str) for v in settings['dictionary'])):
        fail('Dictionary must be a list of words.', 'settings.dictionary')
    if 'protections' in settings and (not isinstance(settings['protections'], list) or any(not isinstance(v, dict) or set(v) != {'target', 'kind'} or any(not isinstance(v[k], str) for k in ('target', 'kind')) for v in settings['protections'])):
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
