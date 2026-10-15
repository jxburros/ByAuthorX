"""Semantic document comparison and three-way branch merge planning.

Comparisons are keyed by stable identities, never by rendered position, so
pagination movement cannot appear as wholesale replacement.
"""

import copy
import difflib

import regex as unicode_regex

from .text_merge import conflict_ranges, merge_text

LAYOUT_ATTRS = (
    'page_break_before',
    'column_break_before',
    'keep_with_next',
    'keep_together',
    'widow_control',
    'orphan_control',
    'columns',
    'width',
    'height',
    'wrap',
    'side',
    'anchor',
    'position',
    'layout',
    'column_widths',
    'header_rows',
    'excluded',
    'z_order',
)
NOT_MERGED = ('review', 'layers', 'fields', 'profiles', 'settings')


def _tokens(text):
    return unicode_regex.findall(r'\w+|\s+|[^\w\s]', text)


def word_changes(before, after):
    old, new = _tokens(before), _tokens(after)
    changes, position_old, position_new = [], 0, 0
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        removed, added = ''.join(old[i1:i2]), ''.join(new[j1:j2])
        if tag != 'equal':
            changes.append(
                {
                    'change': {'replace': 'replace', 'delete': 'delete', 'insert': 'insert'}[tag],
                    'before': removed,
                    'after': added,
                    'before_start': position_old,
                    'after_start': position_new,
                }
            )
        position_old += len(removed)
        position_new += len(added)
    return changes


def _moved(before_ids, after_ids):
    common = set(before_ids) & set(after_ids)
    old = [i for i in before_ids if i in common]
    new = [i for i in after_ids if i in common]
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    stable = {old[i] for block in matcher.get_matching_blocks() for i in range(block.a, block.a + block.size)}
    return [i for i in new if i not in stable]


def _cells(block):
    return {
        c['id']: (ri, ci, c) for ri, r in enumerate(block.get('rows', [])) for ci, c in enumerate(r['cells'])
    }


def _mark_key(mark):
    return (mark.get('type'), repr(mark.get('value')), mark.get('start'), mark.get('end'))


def compare(before, after):
    """Readable summary plus exact changes by category."""
    old = {b['id']: b for b in before.get('blocks', [])}
    new = {b['id']: b for b in after.get('blocks', [])}
    result = {
        'wording': [],
        'structure': {'added': [], 'removed': [], 'moved': [], 'type_changes': []},
        'styles': {'definitions': {}, 'applied': [], 'marks': [], 'attributes': []},
        'tables': [],
        'layout': {'document': {}, 'blocks': []},
        'assets': {'added': [], 'removed': []},
        'dependencies': {'added': [], 'removed': [], 'changed': []},
        'references': {'added': [], 'removed': [], 'changed': []},
        'review': {},
        'metadata': {},
    }
    for identity, block in new.items():
        if identity not in old:
            result['structure']['added'].append(
                {'id': identity, 'type': block.get('type'), 'text': block.get('text', '')}
            )
    for identity, block in old.items():
        if identity not in new:
            result['structure']['removed'].append(
                {'id': identity, 'type': block.get('type'), 'text': block.get('text', '')}
            )
    result['structure']['moved'] = _moved(list(old), list(new))
    for identity in [i for i in new if i in old]:
        a, b = old[identity], new[identity]
        if a == b:
            continue
        if a.get('type') != b.get('type') or a.get('attrs', {}).get('level') != b.get('attrs', {}).get(
            'level'
        ):
            result['structure']['type_changes'].append(
                {
                    'id': identity,
                    'before': {'type': a.get('type'), 'level': a.get('attrs', {}).get('level')},
                    'after': {'type': b.get('type'), 'level': b.get('attrs', {}).get('level')},
                }
            )
        if a.get('text', '') != b.get('text', ''):
            result['wording'].append(
                {
                    'id': identity,
                    'before': a.get('text', ''),
                    'after': b.get('text', ''),
                    'changes': word_changes(a.get('text', ''), b.get('text', '')),
                }
            )
        if a.get('style') != b.get('style'):
            result['styles']['applied'].append(
                {'id': identity, 'before': a.get('style'), 'after': b.get('style')}
            )
        old_marks = {_mark_key(m) for m in a.get('marks', [])}
        new_marks = {_mark_key(m) for m in b.get('marks', [])}
        if old_marks != new_marks and a.get('text', '') == b.get('text', ''):
            result['styles']['marks'].append(
                {
                    'id': identity,
                    'added': [m for m in b.get('marks', []) if _mark_key(m) not in old_marks],
                    'removed': [m for m in a.get('marks', []) if _mark_key(m) not in new_marks],
                }
            )
        attrs_a, attrs_b = a.get('attrs', {}), b.get('attrs', {})
        for key in sorted(set(attrs_a) | set(attrs_b)):
            if attrs_a.get(key) != attrs_b.get(key) and key != 'level':
                entry = {'id': identity, 'key': key, 'before': attrs_a.get(key), 'after': attrs_b.get(key)}
                result['layout' if key in LAYOUT_ATTRS else 'styles'][
                    'blocks' if key in LAYOUT_ATTRS else 'attributes'
                ].append(entry)
        if a.get('type') == 'table' or b.get('type') == 'table':
            cells_a, cells_b = _cells(a), _cells(b)
            table = {
                'id': identity,
                'dimensions': {
                    'before': [
                        len(a.get('rows', [])),
                        len(a.get('rows', [{}])[0].get('cells', [])) if a.get('rows') else 0,
                    ],
                    'after': [
                        len(b.get('rows', [])),
                        len(b.get('rows', [{}])[0].get('cells', [])) if b.get('rows') else 0,
                    ],
                },
                'rows_added': [
                    r['id'] for r in b.get('rows', []) if r['id'] not in {x['id'] for x in a.get('rows', [])}
                ],
                'rows_removed': [
                    r['id'] for r in a.get('rows', []) if r['id'] not in {x['id'] for x in b.get('rows', [])}
                ],
                'cells': [
                    {
                        'id': cid,
                        'row': cells_b[cid][0],
                        'column': cells_b[cid][1],
                        'before': cells_a[cid][2].get('text', ''),
                        'after': cells_b[cid][2].get('text', ''),
                    }
                    for cid in cells_b
                    if cid in cells_a and cells_a[cid][2].get('text', '') != cells_b[cid][2].get('text', '')
                ],
                'cells_moved': [
                    cid for cid in cells_b if cid in cells_a and cells_a[cid][:2] != cells_b[cid][:2]
                ],
            }
            if (
                any(table[k] for k in ('rows_added', 'rows_removed', 'cells', 'cells_moved'))
                or table['dimensions']['before'] != table['dimensions']['after']
            ):
                result['tables'].append(table)
    styles_a, styles_b = before.get('styles', {}), after.get('styles', {})
    for name in sorted(set(styles_a) | set(styles_b)):
        if styles_a.get(name) != styles_b.get(name):
            result['styles']['definitions'][name] = {
                'before': styles_a.get(name),
                'after': styles_b.get(name),
            }
    layout_a, layout_b = before.get('layout', {}), after.get('layout', {})
    for key in sorted(set(layout_a) | set(layout_b)):
        if layout_a.get(key) != layout_b.get(key):
            result['layout']['document'][key] = {'before': layout_a.get(key), 'after': layout_b.get(key)}
    for key in ('assets', 'dependencies', 'references'):
        left, right = before.get(key, {}), after.get(key, {})
        result[key]['added'] = sorted(set(right) - set(left))
        result[key]['removed'] = sorted(set(left) - set(right))
        if key != 'assets':
            result[key]['changed'] = sorted(k for k in set(left) & set(right) if left[k] != right[k])
    review_a, review_b = before.get('review', {}), after.get('review', {})
    for key in ('comments', 'proposals', 'tracked_changes', 'findings'):
        left = {i.get('id'): i.get('status') for i in review_a.get(key, [])}
        right = {i.get('id'): i.get('status') for i in review_b.get(key, [])}
        changes = {
            'added': sorted(set(right) - set(left)),
            'removed': sorted(set(left) - set(right)),
            'status': {
                k: {'before': left[k], 'after': right[k]}
                for k in set(left) & set(right)
                if left[k] != right[k]
            },
        }
        if any(changes.values()):
            result['review'][key] = changes
    meta_a, meta_b = before.get('metadata', {}), after.get('metadata', {})
    for key in sorted(set(meta_a) | set(meta_b)):
        if meta_a.get(key) != meta_b.get(key):
            result['metadata'][key] = {'before': meta_a.get(key), 'after': meta_b.get(key)}
    result['summary'] = _summary(result)
    return result


def _plural(count, word):
    return f'{count} {word}{"" if count == 1 else "s"}'


def _summary(result):
    lines = []
    for item in result['wording']:
        words = sum(1 for c in item['changes'])
        lines.append(f'Wording changed in {item["id"]} ({_plural(words, "edit")}).')
    structure = result['structure']
    for key, verb in (('added', 'Added'), ('removed', 'Removed')):
        for item in structure[key]:
            lines.append(f'{verb} {item["type"]} {item["id"]}.')
    if structure['moved']:
        lines.append(f'Moved {_plural(len(structure["moved"]), "block")} without changing content identity.')
    for item in structure['type_changes']:
        lines.append(f'Changed {item["id"]} from {item["before"]["type"]} to {item["after"]["type"]}.')
    styles = result['styles']
    if styles['definitions']:
        lines.append(f'Changed style definitions: {", ".join(styles["definitions"])}.')
    for item in styles['applied']:
        lines.append(f'Applied style {item["after"]} to {item["id"]} (was {item["before"]}).')
    if styles['marks']:
        lines.append(f'Changed inline formatting in {_plural(len(styles["marks"]), "block")}.')
    if styles['attributes']:
        lines.append(f'Changed {_plural(len(styles["attributes"]), "paragraph formatting attribute")}.')
    for table in result['tables']:
        lines.append(
            f'Table {table["id"]}: {_plural(len(table["cells"]), "cell")} changed, '
            f'{len(table["rows_added"])} rows added, {len(table["rows_removed"])} removed.'
        )
    if result['layout']['document'] or result['layout']['blocks']:
        lines.append('Layout settings changed; pagination may differ.')
    for key in ('assets', 'dependencies', 'references'):
        changes = result[key]
        count = sum(len(v) for v in changes.values())
        if count:
            lines.append(f'{key.title()}: {_plural(count, "change")}.')
    for key, changes in result['review'].items():
        lines.append(
            f'Review {key.replace("_", " ")}: {len(changes["added"])} added, {len(changes["status"])} status changes.'
        )
    if result['metadata']:
        lines.append(f'Metadata changed: {", ".join(result["metadata"])}.')
    return lines or ['No semantic changes.']


def _three_way(base, ours, theirs):
    """Field-level merge of one object; returns (merged, conflicting fields)."""
    merged, conflicts = copy.deepcopy(ours), []
    groups = [('text', 'marks'), ('type',), ('style',), ('rows',)]
    keys = set(base) | set(ours) | set(theirs)
    for group in groups:
        b, o, t = ([x.get(k) for k in group] for x in (base, ours, theirs))
        if t == b or o == t:
            continue
        if o == b:
            for key, value in zip(group, t):
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = copy.deepcopy(value)
        elif group == ('text', 'marks') and (text_result := merge_text(base, ours, theirs)) is not None:
            merged.update(text_result)
        else:
            conflicts.append('+'.join(group))
    attrs_b, attrs_o, attrs_t = (x.get('attrs', {}) for x in (base, ours, theirs))
    merged_attrs = merged.setdefault('attrs', {})
    for key in sorted(set(attrs_b) | set(attrs_o) | set(attrs_t)):
        b, o, t = (x.get(key, KeyError) for x in (attrs_b, attrs_o, attrs_t))
        if t == b or o == t:
            continue
        if o == b:
            if t is KeyError:
                merged_attrs.pop(key, None)
            else:
                merged_attrs[key] = copy.deepcopy(t)
        else:
            conflicts.append('attrs.' + key)
    for key in keys - {'text', 'marks', 'type', 'style', 'rows', 'attrs', 'id'}:
        b, o, t = (x.get(key, KeyError) for x in (base, ours, theirs))
        if t != b and o != t:
            if o == b:
                if t is KeyError:
                    merged.pop(key, None)
                else:
                    merged[key] = copy.deepcopy(t)
            else:
                conflicts.append(key)
    return merged, conflicts


def plan_merge(base, ours, theirs):
    """Operations that bring ``theirs`` changes into ``ours`` plus explicit conflicts."""
    operations, conflicts = [], []

    def conflict(kind, target, **values):
        conflicts.append(
            {
                'id': f'conflict_{len(conflicts) + 1}',
                'kind': kind,
                'target': target,
                'status': 'unresolved',
                **copy.deepcopy(values),
            }
        )

    for digest, asset in theirs.get('assets', {}).items():
        if digest not in ours.get('assets', {}):
            operations.append(
                {
                    'type': 'add_asset',
                    'data': asset['data'],
                    'mime': asset['mime'],
                    'name': asset.get('name', ''),
                }
            )
    for identity, dependency in theirs.get('dependencies', {}).items():
        if identity not in ours.get('dependencies', {}):
            operations.append(
                {
                    'type': 'link_asset',
                    'uri': dependency['uri'],
                    'id': identity,
                    'kind': dependency.get('kind', 'image'),
                }
            )
    for section, op_type in (('styles', 'define_style'), ('references', 'add_reference')):
        b, o, t = (x.get(section, {}) for x in (base, ours, theirs))
        for name in sorted(set(t) | set(b)):
            if t.get(name) == b.get(name) or o.get(name) == t.get(name):
                continue
            if name not in t:
                if o.get(name) == b.get(name):
                    conflict(
                        section[:-1] + '_removed',
                        name,
                        base=b.get(name),
                        ours=o.get(name),
                        theirs=None,
                        reason='Removing definitions is not merged automatically.',
                    )
                continue
            if o.get(name) == b.get(name):
                key = 'name' if op_type == 'define_style' else 'id'
                operations.append({'type': op_type, key: name, 'values': copy.deepcopy(t[name])})
            else:
                conflict(section[:-1], name, base=b.get(name), ours=o.get(name), theirs=t.get(name))
    for section, op_type in (('layout', 'set_layout'), ('metadata', 'set_metadata')):
        b, o, t = (x.get(section, {}) for x in (base, ours, theirs))
        values = {}
        for key in sorted(set(t) | set(b)):
            if key == 'migrations' or t.get(key) == b.get(key) or o.get(key) == t.get(key):
                continue
            if o.get(key) == b.get(key) and key in t:
                values[key] = copy.deepcopy(t[key])
            else:
                conflict(section, key, base=b.get(key), ours=o.get(key), theirs=t.get(key))
        if values:
            operations.append({'type': op_type, 'values': values})
    blocks_b = {x['id']: x for x in base.get('blocks', [])}
    blocks_o = {x['id']: x for x in ours.get('blocks', [])}
    blocks_t = {x['id']: x for x in theirs.get('blocks', [])}
    present = set(blocks_o)
    previous = None
    for block in theirs.get('blocks', []):
        identity = block['id']
        b, o = blocks_b.get(identity), blocks_o.get(identity)
        if b is None and o is None:
            operations.append(
                {
                    'type': 'insert_block',
                    'block': copy.deepcopy(block),
                    'after': previous if previous in present or previous is None else None,
                }
            )
            present.add(identity)
        elif o is None:
            if block != b:
                conflict('block_deleted_in_ours', identity, base=b, ours=None, theirs=block, after=previous)
        elif b is None:
            if o != block:
                conflict('block_added_in_both', identity, base=None, ours=o, theirs=block)
        elif block != b and o != block:
            merged, fields = _three_way(b, o, block)
            if fields:
                conflict(
                    'block',
                    identity,
                    fields=fields,
                    base=b,
                    ours=o,
                    theirs=block,
                    ranges=conflict_ranges(b.get('text', ''), o.get('text', ''), block.get('text', ''))
                    if 'text+marks' in fields
                    else [],
                )
            elif merged != o:
                operations.append({'type': 'set_block', 'target': identity, 'values': merged})
        if identity in present:
            previous = identity
    for identity, b in blocks_b.items():
        if identity in blocks_t or identity not in blocks_o:
            continue
        if blocks_o[identity] == b:
            operations.append({'type': 'delete_block', 'target': identity})
            present.discard(identity)
        else:
            conflict('block_deleted_in_theirs', identity, base=b, ours=blocks_o[identity], theirs=None)
    order_b = [i for i in blocks_b if i in blocks_o and i in blocks_t]
    order_o = [x['id'] for x in ours.get('blocks', []) if x['id'] in blocks_b and x['id'] in blocks_t]
    order_t = [x['id'] for x in theirs.get('blocks', []) if x['id'] in blocks_b and x['id'] in blocks_o]
    if order_t != order_b:
        if order_o == order_b:
            for identity in _moved(order_b, order_t):
                position = order_t.index(identity)
                if position:
                    operations.append(
                        {'type': 'move_block', 'target': identity, 'after': order_t[position - 1]}
                    )
                else:
                    operations.append({'type': 'move_block', 'target': identity, 'before': order_t[1]})
        elif order_o != order_t:
            conflict('order', 'blocks', base=order_b, ours=order_o, theirs=order_t)
    not_merged = [key for key in NOT_MERGED if theirs.get(key) != base.get(key)]
    return operations, conflicts, not_merged
