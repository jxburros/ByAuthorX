"""Named style inheritance, local override reporting and list numbering.

Paragraph and character styles form independent inheritance trees: a style's
``kind`` is ``paragraph`` (default) or ``character`` and ``based_on`` may only
name a style of the same kind. Character styles apply to text through
``character_style`` marks. Direct paragraph attributes and inline marks are
deliberate local overrides and are reported against the inherited values.
"""

import copy
import itertools

from .errors import ByaxError

CHARACTER_MARK = 'character_style'
# Inline formatting carried by marks; semantic marks are never cleared.
INLINE_MARKS = (
    'bold',
    'italic',
    'underline',
    'strike',
    'strikethrough',
    'superscript',
    'subscript',
    'color',
    'highlight',
    'background',
    'font_family',
    'font',
    'font_size',
    'size',
    'code',
    CHARACTER_MARK,
)
SEMANTIC_MARKS = ('field', 'citation', 'reference', 'link', 'href', 'footnote', 'note', 'anchor')
# Paragraph-level direct formatting; structural attributes (lists, levels,
# anchors, locks, assets, captions, sections) are never cleared.
PARAGRAPH_FORMAT = (
    'alignment',
    'indent',
    'first_line_indent',
    'hanging_indent',
    'right_indent',
    'line_spacing',
    'space_before',
    'space_after',
    'tabs',
    'bold',
    'italic',
    'underline',
    'strike',
    'strikethrough',
    'color',
    'highlight',
    'background',
    'font_family',
    'font',
    'font_size',
    'size',
)
LIST_KINDS = {'bullet': 'bullet', 'ordered': 'ordered', 'numbered': 'ordered', 'decimal': 'ordered'}
STYLE_META = ('based_on', 'kind', 'name', 'description')


def style_kind(doc, name):
    return doc['styles'].get(name, {}).get('kind', 'paragraph')


def style_chain(doc, name):
    """Inheritance chain from ``name`` to its root, validating kinds and cycles."""
    chain, kind = [], style_kind(doc, name)
    while name:
        if name in chain:
            raise ByaxError('schema_error', 'Named style inheritance is cyclic.', field='styles.' + name)
        if name not in doc['styles']:
            raise ByaxError('schema_error', 'Named style parent is missing.', field='styles.' + name)
        if style_kind(doc, name) != kind:
            raise ByaxError(
                'schema_error',
                'Character and paragraph styles inherit independently; based_on must name the same kind.',
                field='styles.' + chain[-1] + '.based_on',
            )
        chain.append(name)
        name = doc['styles'][name].get('based_on')
    return chain


def style_values(doc, name):
    values = {}
    for item in reversed(style_chain(doc, name)):
        values.update({k: v for k, v in doc['styles'][item].items() if k not in STYLE_META})
    return values


def _reaches(styles, start, name):
    seen = set()
    while start and start not in seen:
        if start == name:
            return True
        seen.add(start)
        start = styles.get(start, {}).get('based_on')
    return False


def validate_style(doc, name, values):
    """Reject cycles, missing parents, cross-kind inheritance and kind changes that strand uses."""
    if not isinstance(name, str) or not name:
        raise ByaxError('schema_error', 'Style name must be nonempty.', field='name')
    kind = values.get('kind', 'paragraph')
    if kind not in ('paragraph', 'character'):
        raise ByaxError('schema_error', 'Style kind must be paragraph or character.', field='values.kind')
    trial = {'styles': {**doc['styles'], name: values}}
    style_chain(trial, name)
    for other in trial['styles']:
        if other != name and _reaches(trial['styles'], other, name):
            style_chain(trial, other)
    if name in doc['styles'] and doc['styles'][name].get('kind', 'paragraph') != kind:
        stranded = [
            obj['id']
            for obj in _objects(doc)
            if (kind == 'character' and obj.get('style') == name)
            or (
                kind == 'paragraph'
                and any(
                    m.get('type') == CHARACTER_MARK and m.get('value') == name for m in obj.get('marks', [])
                )
            )
        ]
        if stranded:
            raise ByaxError(
                'conflict',
                'Changing the style kind would strand existing uses.',
                target=name,
                affected_ids=stranded,
            )


def dependents(doc, name):
    """Styles inheriting from ``name`` (directly or indirectly)."""
    result = []
    for other in doc['styles']:
        if other == name:
            continue
        try:
            chain = style_chain(doc, other)
        except ByaxError:
            continue
        if name in chain:
            result.append(other)
    return sorted(result)


def _objects(doc):
    for block in doc['blocks']:
        yield block
        for row in block.get('rows', []):
            yield from row.get('cells', [])


def affected_ids(doc, name):
    """Element IDs whose effective formatting depends on style ``name``."""
    names = {name, *dependents(doc, name)}
    result = []
    for obj in _objects(doc):
        if obj.get('style') in names or any(
            m.get('type') == CHARACTER_MARK and m.get('value') in names for m in obj.get('marks', [])
        ):
            result.append(obj['id'])
    return result


def _run_boundaries(obj):
    points = {0, len(obj.get('text', ''))}
    for mark in obj.get('marks', []):
        points.update((mark['start'], mark['end']))
    return sorted(p for p in points if 0 <= p <= len(obj.get('text', '')))


def element_report(doc, obj):
    """Inherited, local and effective formatting with explicit overrides."""
    name = obj.get('style')
    chain = style_chain(doc, name) if name else []
    inherited = style_values(doc, name) if name else {}
    local = {k: v for k, v in obj.get('attrs', {}).items() if k in PARAGRAPH_FORMAT}
    overrides = [
        {'property': key, 'style_value': inherited.get(key), 'local_value': value, 'source': 'paragraph'}
        for key, value in sorted(local.items())
        if inherited.get(key) != value
    ]
    effective = {**inherited, **local}
    runs = []
    points = _run_boundaries(obj)
    for start, end in itertools.pairwise(points):
        if start == end:
            continue
        active = [m for m in obj.get('marks', []) if m['start'] <= start and m['end'] >= end]
        character = [m['value'] for m in active if m.get('type') == CHARACTER_MARK]
        character_values, character_chain = {}, []
        if character:
            character_chain = style_chain(doc, character[-1])
            character_values = style_values(doc, character[-1])
        direct = {
            m['type']: m.get('value', True)
            for m in active
            if m['type'] in INLINE_MARKS and m['type'] != CHARACTER_MARK
        }
        base = {**effective, **character_values}
        run_overrides = [
            {'property': key, 'style_value': base.get(key), 'local_value': value, 'source': 'text'}
            for key, value in sorted(direct.items())
            if base.get(key) != value
        ]
        if character or direct:
            runs.append(
                {
                    'start': start,
                    'end': end,
                    'character_style': character[-1] if character else None,
                    'character_chain': character_chain,
                    'direct': direct,
                    'effective': {**base, **direct},
                    'overrides': run_overrides,
                }
            )
    return {
        'id': obj['id'],
        'style': name,
        'chain': chain,
        'inherited': inherited,
        'local': local,
        'effective': effective,
        'overrides': overrides,
        'runs': runs,
        'has_overrides': bool(overrides or any(r['overrides'] for r in runs)),
    }


def style_report(doc, name=None):
    names = [name] if name else sorted(doc['styles'])
    result = []
    for item in names:
        if item not in doc['styles']:
            raise ByaxError('missing_target', 'Named style does not exist.', target=item)
        result.append(
            {
                'name': item,
                'kind': style_kind(doc, item),
                'based_on': doc['styles'][item].get('based_on'),
                'chain': style_chain(doc, item),
                'values': style_values(doc, item),
                'dependents': dependents(doc, item),
                'affected_ids': affected_ids(doc, item),
            }
        )
    return result


def clear_marks(obj, start, end):
    """Remove inline formatting in [start, end) while keeping semantic marks."""
    kept = []
    for mark in obj.get('marks', []):
        if mark.get('type') in SEMANTIC_MARKS or mark['type'] not in INLINE_MARKS:
            kept.append(mark)
            continue
        if mark['end'] <= start or mark['start'] >= end:
            kept.append(mark)
            continue
        if mark['start'] < start:
            kept.append(dict(mark, end=start))
        if mark['end'] > end:
            kept.append(dict(mark, start=end))
    obj['marks'] = kept


def clear_paragraph(obj):
    attrs = obj.get('attrs', {})
    removed = sorted(k for k in attrs if k in PARAGRAPH_FORMAT)
    for key in removed:
        del attrs[key]
    return removed


def list_info(attrs):
    value = attrs.get('list')
    if not value:
        return None
    if isinstance(value, dict):
        kind = value.get('kind', 'bullet')
        level = value.get('level', attrs.get('level', 1))
        identity = value.get('list_id') or attrs.get('list_id')
    else:
        kind, level, identity = value, attrs.get('level', 1), attrs.get('list_id')
    kind = LIST_KINDS.get(kind, kind)
    try:
        level = max(1, min(9, int(level)))
    except (TypeError, ValueError):
        level = 1
    return {'kind': kind, 'level': level, 'list_id': identity}


def _label(kind, number):
    return '•' if kind == 'bullet' else f'{number}.'


def resolve_lists(doc):
    """Resolve list identity, nesting and numbering for every list paragraph.

    Paragraphs carrying an explicit ``list_id`` number within that list. Older
    paragraphs without one continue the implicit list of the same kind. A
    ``restart`` attribute starts a new instance at ``start`` (default 1). A
    shallower item resets the counters of deeper levels.
    """
    counters, instance, result = {}, {}, []
    for block in doc['blocks']:
        attrs = block.get('attrs', {})
        info = list_info(attrs)
        if info is None or block.get('type') not in ('paragraph', 'heading'):
            continue
        identity = info['list_id'] or 'implicit:' + info['kind']
        levels = counters.setdefault(identity, {})
        if attrs.get('restart'):
            instance[identity] = instance.get(identity, 0) + 1
            levels.clear()
            levels[info['level']] = int(attrs.get('start', 1)) - 1
        for deeper in [lvl for lvl in levels if lvl > info['level']]:
            del levels[deeper]
        if info['level'] not in levels and 'start' in attrs and not attrs.get('restart'):
            levels[info['level']] = int(attrs['start']) - 1
        levels[info['level']] = levels.get(info['level'], 0) + 1
        number = levels[info['level']]
        path = [levels.get(lvl, 0) for lvl in range(1, info['level'] + 1)]
        result.append(
            {
                'id': block['id'],
                'list_id': identity,
                'instance': instance.get(identity, 0),
                'kind': info['kind'],
                'level': info['level'],
                'number': number,
                'path': path,
                'label': _label(info['kind'], number),
            }
        )
    return result


def list_attrs(attrs, values, preceding):
    """Apply ``set_list`` values to paragraph attributes."""
    attrs = copy.deepcopy(attrs)
    if values.get('kind') is None and 'kind' in values:
        for key in ('list', 'list_id', 'restart', 'start'):
            attrs.pop(key, None)
        return attrs
    current = list_info(attrs) or {}
    kind = LIST_KINDS.get(values.get('kind', current.get('kind', 'bullet')))
    if kind is None:
        raise ByaxError('schema_error', 'List kind must be bullet or ordered.', field='values.kind')
    level = values.get('level', current.get('level', 1))
    if type(level) is not int or not 1 <= level <= 9:
        raise ByaxError(
            'schema_error', 'List level must be an integer from 1 through 9.', field='values.level'
        )
    identity = values.get('list_id', current.get('list_id'))
    if identity is None:
        identity = next((p['list_id'] for p in reversed(preceding) if p['kind'] == kind), None)
    if identity is not None and not isinstance(identity, str):
        raise ByaxError('schema_error', 'List identity must be a string.', field='values.list_id')
    attrs['list'] = {'kind': kind, 'level': level, **({'list_id': identity} if identity else {})}
    for key in ('restart', 'start'):
        if key in values:
            if key == 'restart' and type(values[key]) is not bool:
                raise ByaxError('schema_error', 'Restart must be a boolean.', field='values.restart')
            if key == 'start' and (type(values[key]) is not int or values[key] < 0):
                raise ByaxError(
                    'schema_error', 'List start must be a nonnegative integer.', field='values.start'
                )
            if values[key] is False:
                attrs.pop(key, None)
            else:
                attrs[key] = values[key]
    return attrs
