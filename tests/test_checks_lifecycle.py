"""Checking coverage, deterministic rule families and the finding lifecycle (spec 12.1, 12.3)."""

import copy

import pytest

from byax import engine
from byax.checks import STATUSES, TRANSITIONS, check_batch, health, rule_catalog, run_checks
from byax.errors import ByaxError
from byax.model import new_document


def edit(doc, *ops, **envelope):
    return engine.apply(doc, {'operations': list(ops), **envelope})


def rules(report):
    return {f['rule'] for f in report['findings']}


def test_grammar_rules_are_located_with_exact_text_and_separate_repairs():
    doc = new_document(text='this is a apple.  It is , fine!! (open')
    original = copy.deepcopy(doc)
    report = run_checks(doc, ['grammar'])
    assert doc == original
    found = {f['rule']: f for f in report['findings']}
    assert set(found) >= {
        'grammar.sentence_capital',
        'grammar.article',
        'grammar.double_space',
        'grammar.space_before_punctuation',
        'grammar.repeated_punctuation',
        'grammar.unbalanced',
    }
    target = doc['blocks'][0]['id']
    for finding in report['findings']:
        assert finding['affected_ids'] == [target]
        assert (
            finding['text'] == doc['blocks'][0]['text'][finding['range']['start'] : finding['range']['end']]
        )
        for key in (
            'rule',
            'family',
            'severity',
            'explanation',
            'measured',
            'evidence',
            'status',
            'revision',
            'profile_version',
            'engine',
            'source_versions',
            'scope',
            'repair',
            'policy',
        ):
            assert key in finding
        assert finding['policy'] == 'advisory'
    assert found['grammar.article']['measured'] == {'article': 'a', 'word': 'apple'}
    repaired, _ = edit(doc, *found['grammar.article']['operations'])
    assert repaired['blocks'][0]['text'].startswith('this is an apple.')
    assert (
        not found['grammar.unbalanced']['operations']
        and found['grammar.unbalanced']['repair']['kind'] == 'manual'
    )
    assert any(
        c['rule'] == 'grammar.agreement_structure' and c['status'] == 'unmeasurable'
        for c in report['coverage']
    )
    assert report['validation'] == 'incomplete'


def test_language_coverage_is_explicit_and_language_independent_rules_still_run():
    doc = new_document(text='un  texte (ouvert , ici')
    doc['metadata']['language'] = 'fr'
    report = run_checks(doc, ['grammar'])
    assert 'grammar.double_space' in rules(report) and 'grammar.unbalanced' in rules(report)
    assert 'grammar.space_before_punctuation' not in rules(report)
    unmeasured = [
        c for c in report['coverage'] if c['status'] == 'unmeasurable' and c.get('language') == 'fr'
    ]
    assert {c['rule'] for c in unmeasured} >= {'grammar.article', 'grammar.sentence_capital'}


def test_consistency_terminology_and_acronyms():
    doc = new_document(
        text='Send an e-mail today.\nEmail is fast and email is cheap.\nThe World Health Organization (WHO) met.\n'
        'The Western Hockey Office (WHO) disagreed.\nWe utilise tools.'
    )
    doc['settings']['terminology'] = {'use': ['utilise']}
    report = run_checks(doc, ['consistency', 'spelling'])
    variant = next(f for f in report['findings'] if f['rule'] == 'consistency.term_variant')
    assert variant['text'] == 'e-mail' and variant['measured']['preferred'].lower() == 'email'
    acronym = next(f for f in report['findings'] if f['rule'] == 'consistency.acronym')
    assert acronym['measured']['acronym'] == 'WHO'
    preferred = next(f for f in report['findings'] if f['rule'] == 'terminology.preferred')
    fixed, _ = edit(doc, *preferred['operations'])
    assert fixed['blocks'][-1]['text'] == 'We use tools.'
    assert any(
        c['rule'] == 'consistency.statements' and c['status'] == 'unmeasurable' for c in report['coverage']
    )


def test_accessibility_and_layout_diagnosis():
    doc = new_document('', 'Body')
    doc['metadata']['language'] = ''
    doc, _ = edit(
        doc,
        {'type': 'insert_paragraph', 'text': 'Go here', 'id': 'p_link'},
        {
            'type': 'format_text',
            'target': 'p_link',
            'start': 3,
            'end': 7,
            'marks': {'link': 'https://example.com'},
        },
        {'type': 'format_text', 'target': 'p_link', 'start': 0, 'end': 2, 'marks': {'color': '#dddddd'}},
        {'type': 'insert_table', 'rows': 2, 'columns': 2},
    )
    table = doc['blocks'][-1]['id']
    doc, _ = edit(doc, {'type': 'set_attributes', 'target': table, 'values': {'column_widths': [400, 400]}})
    report = run_checks(doc, ['accessibility', 'layout'])
    assert {
        'document.language',
        'document.title',
        'link.text',
        'color.contrast',
        'table.header_empty',
        'layout.overflow',
    } <= rules(report)
    contrast = next(f for f in report['findings'] if f['rule'] == 'color.contrast')
    assert contrast['measured']['ratio'] < 4.5
    breaks = new_document(text='Body')
    breaks, _ = edit(breaks, {'type': 'insert_paragraph', 'text': ''})
    breaks['blocks'][-1].update(type='page_break')
    assert 'layout.blank_page' in rules(run_checks(breaks, ['layout']))
    assert any(
        c.get('rule') == 'resolved_geometry' and c['status'] == 'completed'
        for c in run_checks(breaks, ['layout'])['coverage']
    )


def test_intentional_passages_are_skipped_by_policy_and_reported():
    doc = new_document(text='the the lyric  line')
    doc['blocks'][0]['attrs']['lyric'] = True
    report = run_checks(doc, ['grammar', 'editorial', 'spelling'])
    assert report['findings'] == []
    skipped = [c for c in report['coverage'] if c['status'] == 'skipped']
    assert skipped and all(c['by_policy'] for c in skipped)


def test_scopes_selection_section_block_and_batch():
    doc = new_document(text='Intro the the\nmore  space')
    doc['blocks'].insert(
        1,
        {
            'id': 'h1',
            'type': 'heading',
            'text': 'Part',
            'style': 'heading1',
            'marks': [],
            'attrs': {'level': 1},
        },
    )
    first, last = doc['blocks'][0]['id'], doc['blocks'][-1]['id']
    whole = run_checks(doc, ['grammar', 'editorial'])
    section = run_checks(doc, ['grammar', 'editorial'], scope='h1')
    assert {f['affected_ids'][0] for f in section['findings']} == {last}
    block = run_checks(doc, ['editorial'], scope=first)
    assert rules(block) == {'word.repeated'} and block['scope'] == first
    selection = run_checks(doc, ['editorial'], scope={'target': first, 'start': 0, 'end': 5})
    assert selection['findings'] == []
    batch = run_checks(doc, ['grammar', 'editorial'], scope=[first, last])
    assert len(batch['findings']) == len(whole['findings'])
    scoped_requirements = run_checks(doc, ['requirements', 'editorial'], scope=first)
    assert scoped_requirements['validation'] == 'incomplete'
    assert any(
        c['rule'] == 'document_rules' and c['status'] == 'skipped' for c in scoped_requirements['coverage']
    )
    with pytest.raises(ByaxError):
        run_checks(doc, ['grammar'], scope='missing')
    outcome = check_batch([doc, {'id': 'bad'}], ['structure'])
    assert outcome['results'][1]['execution'] == 'failed' and outcome['validation'] == 'failed'


def test_limit_and_unavailable_checks_never_become_clean_passes(monkeypatch):
    doc = new_document(text='Hello wrld')
    doc['settings']['requirements'] = {'a': {'kind': 'required_text', 'value': 'missing'}}
    report = run_checks(doc, ['requirements', 'spelling'], limit=0)
    assert report['findings'] == [] and report['summary']['total'] >= 2 and report['validation'] == 'failed'
    from byax import checks

    monkeypatch.setattr(checks, '_SPELLERS', {'en': None})
    unavailable = run_checks(new_document(text='Hello'), ['spelling'])
    assert unavailable['validation'] == 'incomplete'
    assert any(
        c['rule'] == 'spelling.unknown' and c['status'] == 'unmeasurable' for c in unavailable['coverage']
    )
    catalog = rule_catalog()
    assert catalog['grammar.article']['languages'] == ['en']


def test_every_status_transition_through_canonical_operations():
    doc = new_document(text='The the cat.\nA a dog.\nOne one fish.\nTwo two birds.')
    doc, _ = edit(doc, {'type': 'record_checks', 'values': {'suites': ['editorial']}})
    findings = [f for f in doc['review']['findings'] if f['rule'] == 'word.repeated']
    assert len(findings) == 4 and all(f['status'] == 'open' for f in findings)
    assert health(doc)['validation'] == 'incomplete'
    first, second, third, fourth = (f['id'] for f in findings)
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'set_finding_status', 'target': first, 'status': 'dismissed'})
    assert error.value.code == 'schema_error'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'set_finding_status', 'target': first, 'status': 'accepted edit'})
    assert error.value.code == 'conflict'
    doc, _ = edit(
        doc, {'type': 'set_finding_status', 'target': first, 'status': 'dismissed', 'reason': 'Deliberate'}
    )
    doc, _ = edit(doc, {'type': 'set_finding_status', 'target': first, 'status': 'open'})
    doc, _ = edit(
        doc,
        {
            'type': 'set_finding_status',
            'target': second,
            'status': 'intentional exception',
            'reason': 'Voice',
        },
    )
    doc, _ = edit(doc, {'type': 'set_finding_status', 'target': third, 'status': 'needs review'})
    before_repair = doc['revision']
    doc, _result = edit(doc, {'type': 'apply_repair', 'target': fourth})
    statuses = {f['id']: f['status'] for f in doc['review']['findings']}
    assert statuses[fourth] == 'accepted edit'
    assert doc['blocks'][3]['text'] == 'Two birds.'
    assert (
        statuses[first] == 'open'
        and statuses[second] == 'intentional exception'
        and statuses[third] == 'needs review'
    )
    history = next(f for f in doc['review']['findings'] if f['id'] == first)['status_history']
    assert [h['to'] for h in history] == ['dismissed', 'open']
    # Repairs are separate, undoable revisions.
    restored, _ = engine.restore(doc, before_repair)
    assert restored['blocks'][3]['text'] == 'Two two birds.'
    # Editing the passage stales only its own findings.
    doc, _ = edit(doc, {'type': 'set_text', 'target': doc['blocks'][0]['id'], 'text': 'The cat the cat.'})
    statuses = {f['id']: f['status'] for f in doc['review']['findings']}
    assert statuses[first] == 'stale' and statuses[second] == 'intentional exception'
    assert health(doc)['validation'] == 'stale'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'set_finding_status', 'target': first, 'status': 'open'})
    assert error.value.code == 'conflict'
    assert set(STATUSES) == set(TRANSITIONS)


def test_strict_failures_cannot_be_dismissed_and_repairs_respect_protection():
    doc = new_document(text='Body  text')
    doc['settings']['requirements'] = {'title': {'kind': 'required_text', 'value': 'Summary'}}
    target = doc['blocks'][0]['id']
    doc, _ = edit(doc, {'type': 'protect', 'target': target, 'kind': 'text'})
    doc, _ = edit(doc, {'type': 'record_checks', 'values': {'suites': ['requirements', 'grammar']}})
    strict = next(f for f in doc['review']['findings'] if f['family'] == 'requirements')
    assert strict['policy'] == 'strict'
    with pytest.raises(ByaxError) as error:
        edit(
            doc,
            {'type': 'set_finding_status', 'target': strict['id'], 'status': 'dismissed', 'reason': 'Later'},
        )
    assert error.value.code == 'conflict'
    spacing = next(f for f in doc['review']['findings'] if f['rule'] == 'grammar.double_space')
    assert spacing['operations'] == [] and spacing['repair']['kind'] == 'blocked'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'apply_repair', 'target': spacing['id']})
    assert error.value.code == 'unsupported_feature'


def test_recording_checks_never_changes_content():
    doc = new_document(text='The the end')
    recorded, result = edit(doc, {'type': 'record_checks', 'values': {'suites': ['editorial']}})
    assert recorded['blocks'] == doc['blocks']
    assert set(result['changed_ids']) == {'review.findings', 'review.health'}
    assert health(new_document())['validation'] == 'not_run'
