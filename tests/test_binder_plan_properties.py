"""Randomized (seeded) properties of the binder's keyboard and drop plans (CP A2, exit criterion 3.5).

Every plan the Binder panel can build, applied as one envelope, must keep the manuscript intact:

- no block is lost, duplicated or edited (heading level and style aside) and no comment, reply,
  bookmark or tracked change disappears, moves to another block or loses its range;
- the binder stays consistent (no orphans) and every heading level stays within 1 to 6;
- a step or drop has the nesting it names: before and after make siblings, into makes a child.
"""

import copy
import random

import pytest
from test_project_properties import _anchors, _check_present, _manuscript

from byax import engine, project

STEPS = ('up', 'down', 'out', 'in')


def _content(doc):
    """Every block except the heading fields a level change is allowed to touch."""
    result = {}
    for block in doc['blocks']:
        record = copy.deepcopy(block)
        if block['type'] == 'heading':
            record['attrs'].pop('level', None)
            record.pop('style', None)
        result[block['id']] = record
    return result


def _apply(doc, operations):
    return engine.apply(doc, {'expected_revision': doc['revision'], 'operations': operations})[0]


def _check(before, after, previous):
    assert sorted(b['id'] for b in before['blocks']) == sorted(b['id'] for b in after['blocks'])
    assert _content(before) == _content(after)
    levels = [b['attrs']['level'] for b in after['blocks'] if b['type'] == 'heading']
    assert all(1 <= level <= 6 for level in levels)
    assert project.reconcile(after)['consistent']
    _check_present(previous, _anchors(after), after)
    for kind in ('comments', 'bookmarks'):
        for key, record in previous[kind].items():
            kept = _anchors(after)[kind][key]
            for field in ('target', 'start', 'end', 'offset', 'text', 'quote'):
                assert kept.get(field) == record.get(field), (kind, key, field)


@pytest.mark.parametrize('seed', range(30))
def test_random_steps_and_drops_keep_every_block_and_anchor(seed):
    rng = random.Random(seed)
    doc = _manuscript(rng)
    for _ in range(25):
        rows = project.items(doc)
        row = rng.choice(rows)
        before, anchors = doc, _anchors(doc)
        if rng.random() < 0.5:
            step = rng.choice(STEPS)
            operations = project.plan_step(doc, row['id'], step)
            if not operations:
                continue
            doc = _apply(doc, operations)
            after = {r['id']: r for r in project.items(doc)}
            source = after[row['id']]
            if step in ('out', 'in'):
                assert source['level'] != row['level']
            if step == 'in':
                assert source['parent'] is not None and source['parent'] != row['parent']
            if step == 'out':
                assert source['depth'] == after[row['parent']]['depth']
        else:
            target = rng.choice(rows)
            where = rng.choice(('before', 'after', 'into'))
            operations = project.plan_drop(doc, row['id'], target['id'], where)
            if not operations:
                continue
            doc = _apply(doc, operations)
            after = {r['id']: r for r in project.items(doc)}
            source, anchor = after[row['id']], after[target['id']]
            if where == 'into':
                assert source['parent'] == target['id'] and source['level'] == anchor['level'] + 1
            else:
                assert source['parent'] == anchor['parent'] and source['level'] == anchor['level']
        _check(before, doc, anchors)


def test_the_property_loop_exercises_every_plan_kind():
    seen = set()
    for seed in range(30):
        rng = random.Random(seed)
        doc = _manuscript(rng)
        for row in project.items(doc):
            for step in STEPS:
                if project.plan_step(doc, row['id'], step):
                    seen.add(step)
            for other in project.items(doc)[:6]:
                for where in ('before', 'after', 'into'):
                    if project.plan_drop(doc, row['id'], other['id'], where):
                        seen.add(where)
    assert seen == {'up', 'down', 'out', 'in', 'before', 'after', 'into'}
