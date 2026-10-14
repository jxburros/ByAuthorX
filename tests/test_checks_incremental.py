"""Dependency-sensitive invalidation and incremental rechecks compared with full rechecks (spec 12.4)."""

import pytest

from byax import engine
from byax.checks import health, run_checks
from byax.model import new_document
from byax.profiles import presets

SUITES = [
    'structure',
    'accessibility',
    'grammar',
    'editorial',
    'consistency',
    'spelling',
    'requirements',
    'factual',
]
VOLATILE = ('revision', 'status_history', 'dependencies')


def edit(doc, *ops):
    return engine.apply(doc, {'operations': list(ops)})


def representative():
    doc = new_document(
        'Report', 'Summary the the plan.\nSend an e-mail today.\nEmail is fast.\nA  second paragraph.'
    )
    doc['blocks'].insert(
        1,
        {
            'id': 'h1',
            'type': 'heading',
            'text': 'Findings',
            'style': 'heading1',
            'marks': [],
            'attrs': {'level': 1},
        },
    )
    doc['blocks'].append(
        {
            'id': 'h3',
            'type': 'heading',
            'text': 'Detail',
            'style': 'heading3',
            'marks': [],
            'attrs': {'level': 3},
        }
    )
    doc['blocks'].append(
        {
            'id': 'q1',
            'type': 'paragraph',
            'text': 'He said "the sky is green" here.',
            'style': 'body',
            'marks': [{'type': 'citation', 'value': 'src', 'start': 8, 'end': 25}],
            'attrs': {},
        }
    )
    doc['references']['src'] = {
        'title': 'Almanac',
        'text': 'Observers agree the sky is blue.',
        'version': '1',
    }
    doc['settings']['requirements'] = {'words': {'kind': 'max_words', 'value': 200}}
    return doc


def normalized(report):
    return sorted(
        ({k: v for k, v in f.items() if k not in VOLATILE} for f in report['findings']), key=lambda f: f['id']
    )


def coverage_shape(report):
    return sorted(
        (c['suite'], c.get('rule'), c['status'], tuple(sorted(c.get('targets', []))))
        for c in report['coverage']
    )


EDITS = {
    'text': lambda d: [{'type': 'set_text', 'target': d['blocks'][0]['id'], 'text': 'Summary of the plan.'}],
    'insert': lambda d: [{'type': 'insert_paragraph', 'text': 'New  text an car.', 'after': 'h1'}],
    'structure': lambda d: [{'type': 'set_attributes', 'target': 'h3', 'values': {'level': 2}}],
    'move': lambda d: [{'type': 'move_section', 'target': 'h3', 'after': d['blocks'][0]['id']}],
    'delete': lambda d: [{'type': 'delete_block', 'target': d['blocks'][-2]['id']}],
    'source': lambda d: [
        {
            'type': 'add_reference',
            'id': 'src',
            'values': {'title': 'Almanac', 'text': 'Observers agree the sky is green.', 'version': '2'},
        }
    ],
    'dictionary': lambda d: [{'type': 'set_settings', 'values': {'dictionary': ['Email']}}],
}


@pytest.mark.parametrize('name', sorted(EDITS))
def test_incremental_recheck_matches_full_recheck(name):
    doc = representative()
    first = run_checks(doc, SUITES, limit=10**6, track=True)
    changed, _ = edit(doc, *EDITS[name](doc))
    incremental = run_checks(changed, SUITES, limit=10**6, previous=first, track=True)
    full = run_checks(changed, SUITES, limit=10**6, track=True)
    assert normalized(incremental) == normalized(full)
    assert coverage_shape(incremental) == coverage_shape(full)
    assert incremental['validation'] == full['validation']
    assert incremental['evidence'] == full['evidence']
    assert incremental['evaluation']['mode'] == 'incremental'
    # Terminology changes affect every passage, so nothing may be reused.
    assert bool(incremental['evaluation']['reused']) == (name != 'dictionary')
    if name == 'text':
        assert 'block:' + doc['blocks'][0]['id'] in incremental['evaluation']['rechecked']
        assert 'block:' + doc['blocks'][2]['id'] in incremental['evaluation']['reused']
        assert 'context:structure' in incremental['evaluation']['reused']
        assert 'context:consistency' in incremental['evaluation']['rechecked']
    if name == 'structure':
        assert 'context:structure' in incremental['evaluation']['rechecked']
    if name == 'source':
        assert 'block:q1' in incremental['evaluation']['rechecked']
        assert 'block:' + doc['blocks'][0]['id'] in incremental['evaluation']['reused']


def test_profile_change_rechecks_everything_and_incompatible_previous_is_full():
    doc = representative()
    doc['profiles']['p'] = presets()['editorial_review']
    first = run_checks(doc, SUITES, profile='p', track=True)
    changed, _ = edit(
        doc,
        {
            'type': 'set_profile',
            'id': 'p',
            'values': {**presets()['editorial_review'], 'sensitivity': 'conservative'},
        },
    )
    incremental = run_checks(changed, SUITES, profile='p', previous=first, track=True, limit=10**6)
    full = run_checks(changed, SUITES, profile='p', track=True, limit=10**6)
    assert normalized(incremental) == normalized(full)
    assert not [u for u in incremental['evaluation']['reused'] if u.startswith('block:')]
    other = run_checks(changed, ['grammar'], previous=first)
    assert other['evaluation']['mode'] == 'full' and 'reason' in other['evaluation']


def test_section_scope_expands_context_rules_and_reports_it():
    doc = representative()
    report = run_checks(doc, ['accessibility', 'consistency'], scope='h3')
    assert {e['unit'] for e in report['evaluation']['expanded']} == {
        'context:structure',
        'context:consistency',
    }
    assert all(f['affected_ids'][0] in ('h3', 'q1') for f in report['findings'])


def test_persisted_findings_invalidate_only_affected_and_health_never_stays_clean():
    doc = new_document(text='Alpha the the.\nBeta clean text.')
    doc, _ = edit(doc, {'type': 'record_checks', 'values': {'suites': ['editorial']}})
    record = doc['review']['health']
    assert health(doc)['validation'] == 'incomplete' and not health(doc)['stale_units']
    second = doc['blocks'][1]['id']
    doc, _ = edit(doc, {'type': 'set_text', 'target': second, 'text': 'Beta changed text.'})
    report = health(doc)
    assert report['stale_units'] == ['block:' + second]
    assert report['validation'] == 'stale'
    assert all(
        f['status'] == 'open' for f in doc['review']['findings']
    )  # the finding in the first block is unaffected
    # Clean recorded pass also becomes stale rather than staying passed.
    clean = new_document(text='Clean text.')
    clean, _ = edit(clean, {'type': 'record_checks', 'values': {'suites': ['structure', 'requirements']}})
    assert health(clean)['validation'] == 'passed'
    clean, _ = edit(
        clean, {'type': 'set_requirement', 'id': 'r', 'values': {'kind': 'required_text', 'value': 'Missing'}}
    )
    assert health(clean)['validation'] == 'stale'
    assert any(c['status'] == 'stale' for c in health(clean)['coverage'])
    clean, _ = edit(
        clean,
        {'type': 'record_checks', 'values': {'suites': ['structure', 'requirements'], 'incremental': True}},
    )
    assert health(clean)['validation'] == 'failed'
    # Incremental persisted recheck equals a full persisted recheck and keeps statuses of reused findings.
    first_finding = doc['review']['findings'][0]['id']
    doc, _ = edit(
        doc,
        {'type': 'set_finding_status', 'target': first_finding, 'status': 'dismissed', 'reason': 'Rhythm'},
    )
    doc, _ = edit(doc, {'type': 'set_text', 'target': second, 'text': 'Beta the the again.'})
    incremental, _ = edit(
        doc, {'type': 'record_checks', 'values': {'suites': ['editorial'], 'incremental': True}}
    )
    full, _ = edit(doc, {'type': 'record_checks', 'values': {'suites': ['editorial']}})
    shape = lambda d: sorted((f['id'], f['status']) for f in d['review']['findings'])
    assert shape(incremental) == shape(full)
    assert dict(shape(incremental))[first_finding] == 'dismissed'
    assert incremental['review']['health']['evaluation']['reused'] == ['block:' + doc['blocks'][0]['id']]
    assert record['parameters']['suites'] == ['editorial']
