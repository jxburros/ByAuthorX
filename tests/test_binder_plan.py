"""Keyboard and drop plans for the binder views (CP A2): pure canonical operation lists."""

import pytest

from byax import engine, project
from byax.errors import ByaxError
from byax.model import new_document


def _h(identity, text, level):
    return {
        'id': identity,
        'type': 'heading',
        'text': text,
        'style': f'heading{level}',
        'marks': [],
        'attrs': {'level': level},
    }


def _p(identity, text):
    return {'id': identity, 'type': 'paragraph', 'text': text, 'style': 'body', 'marks': [], 'attrs': {}}


def manuscript():
    doc = new_document('Novel')
    doc['blocks'] = [
        _h('ch1', 'Chapter one', 1),
        _h('s1', 'Arrival', 2),
        _p('s1a', 'The boat came in on the evening tide.'),
        _h('s2', 'The keeper', 2),
        _p('s2a', 'He did not look up.'),
        _h('ch2', 'Chapter two', 1),
        _h('s3', 'Night', 2),
        _p('s3a', 'The lamp failed at midnight.'),
        _h('s4', 'Morning', 2),
        _p('s4a', 'Gulls again.'),
        _h('ch3', 'Chapter three', 1),
        _p('ch3a', 'Short.'),
    ]
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    return doc


def ids(doc):
    return {row['title']: row['id'] for row in project.items(doc)}


def shape(doc):
    return [(row['title'], row['depth'], row['level']) for row in project.items(doc)]


def run(doc, operations):
    assert operations
    return engine.apply(doc, {'expected_revision': doc['revision'], 'operations': operations})[0]


def step(doc, title, direction):
    return project.plan_step(doc, ids(doc)[title], direction)


def test_up_and_down_swap_siblings_and_keep_the_section_text_together():
    doc = manuscript()
    moved = run(doc, step(doc, 'Chapter two', 'up'))
    assert [t for t, _, _ in shape(moved)][:5] == [
        'Chapter two',
        'Night',
        'Morning',
        'Chapter one',
        'Arrival',
    ]
    back = run(moved, step(moved, 'Chapter two', 'down'))
    assert [b['id'] for b in back['blocks']] == [b['id'] for b in doc['blocks']]
    assert step(doc, 'Chapter one', 'up') is None
    assert step(doc, 'Chapter three', 'down') is None


def test_up_and_down_cross_into_the_neighbouring_container_at_the_ends():
    doc = manuscript()
    up = run(doc, step(doc, 'Night', 'up'))
    assert shape(up)[:4] == [('Chapter one', 0, 1), ('Arrival', 1, 2), ('The keeper', 1, 2), ('Night', 1, 2)]
    down = run(doc, step(doc, 'The keeper', 'down'))
    rows = shape(down)
    assert (
        rows.index(('The keeper', 1, 2)) == rows.index(('Chapter two', 0, 1)) + 1
    )  # first child of the next chapter
    # A scene cannot cross past the first or last container.
    assert step(doc, 'Arrival', 'up') is None
    assert step(doc, 'Morning', 'down') is not None  # Chapter three is a leaf: the scene becomes its child
    both = run(doc, step(doc, 'Morning', 'down'))
    assert ('Morning', 1, 2) in shape(both) and shape(both)[-1] == ('Morning', 1, 2)


def test_indent_nests_under_the_previous_sibling_and_outdent_follows_the_parent():
    doc = manuscript()
    nested = run(doc, step(doc, 'Chapter two', 'in'))
    assert ('Chapter two', 1, 2) in shape(nested) and ('Night', 2, 3) in shape(nested)
    assert next(b for b in nested['blocks'] if b['id'] == 'ch2')['style'] == 'heading2'
    assert next(b for b in nested['blocks'] if b['id'] == 's3')['attrs']['level'] == 3
    assert [b['id'] for b in nested['blocks']] == [b['id'] for b in doc['blocks']]  # order untouched
    restored = run(nested, step(nested, 'Chapter two', 'out'))
    assert shape(restored) == shape(doc)
    out = run(doc, step(doc, 'The keeper', 'out'))
    rows = shape(out)
    assert ('The keeper', 0, 1) in rows
    assert rows.index(('The keeper', 0, 1)) == rows.index(('Chapter two', 0, 1)) - 1
    assert step(doc, 'Chapter one', 'in') is None and step(doc, 'Chapter one', 'out') is None
    assert step(doc, 'Arrival', 'in') is None  # first child has no previous sibling


def test_indent_is_refused_when_it_would_pass_heading_level_six():
    doc = new_document('Deep')
    doc['blocks'] = [_h(f'h{n}', f'Level {n}', n) for n in range(1, 7)] + [_h('last', 'Last', 6)]
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    assert step(doc, 'Last', 'in') is None
    assert step(doc, 'Level 6', 'in') is None


def test_drop_before_after_and_into_normalizes_heading_levels():
    doc = manuscript()
    before = project.plan_drop(doc, ids(doc)['Chapter three'], ids(doc)['Chapter one'], 'before')
    assert shape(run(doc, before))[0] == ('Chapter three', 0, 1)
    after = project.plan_drop(doc, ids(doc)['Night'], ids(doc)['Arrival'], 'after')
    rows = shape(run(doc, after))
    assert rows[:4] == [('Chapter one', 0, 1), ('Arrival', 1, 2), ('Night', 1, 2), ('The keeper', 1, 2)]
    into = project.plan_drop(doc, ids(doc)['Chapter three'], ids(doc)['Chapter one'], 'into')
    rows = shape(run(doc, into))
    assert ('Chapter three', 1, 2) in rows and rows.index(('Chapter three', 1, 2)) == rows.index(
        ('The keeper', 1, 2)
    ) + 1
    promoted = project.plan_drop(doc, ids(doc)['Night'], ids(doc)['Chapter three'], 'after')
    assert ('Night', 0, 1) in shape(run(doc, promoted))
    deepened = project.plan_drop(doc, ids(doc)['Chapter three'], ids(doc)['Night'], 'into')
    rows = shape(run(doc, deepened))
    assert ('Chapter three', 2, 3) in rows


def test_drop_refuses_impossible_targets():
    doc = manuscript()
    lookup = ids(doc)
    assert project.plan_drop(doc, lookup['Chapter one'], lookup['Chapter one'], 'before') is None
    assert project.plan_drop(doc, lookup['Chapter one'], lookup['Arrival'], 'after') is None  # inside itself
    assert project.plan_drop(doc, 'missing', lookup['Arrival'], 'after') is None
    with pytest.raises(ByaxError):
        project.plan_drop(doc, lookup['Chapter one'], lookup['Chapter two'], 'sideways')
    with pytest.raises(ByaxError):
        project.plan_step(doc, lookup['Chapter one'], 'sideways')
    assert project.plan_step(doc, 'missing', 'up') is None


def test_every_plan_moves_comments_with_their_text():
    doc = manuscript()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'add_comment', 'target': 's3a', 'start': 4, 'end': 8, 'text': 'Which lamp?'},
            ]
        },
    )
    moved = run(doc, step(doc, 'Chapter two', 'up'))
    comment = moved['review']['comments'][0]
    block = next(b for b in moved['blocks'] if b['id'] == comment['target'])
    assert block['text'][comment['start'] : comment['end']] == 'lamp'
    indented = run(moved, step(moved, 'Chapter one', 'in'))
    comment = indented['review']['comments'][0]
    block = next(b for b in indented['blocks'] if b['id'] == comment['target'])
    assert block['text'][comment['start'] : comment['end']] == 'lamp'


def test_next_item_and_own_blocks_helpers():
    doc = manuscript()
    lookup = ids(doc)
    assert project.next_item(doc, lookup['Chapter one']) == lookup['Arrival']
    assert project.next_item(doc, lookup['Chapter three']) is None
    assert project.next_item(doc, 'missing') is None
    assert project.own_blocks(doc, lookup['Arrival']) == ['s1', 's1a']
    assert project.own_blocks(doc, 'missing') == []
