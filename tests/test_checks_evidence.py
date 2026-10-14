"""Factual evidence outcomes, source-change invalidation and strict publication (spec 13.1-13.2, 23.5)."""

import pytest

from byax import engine
from byax.checks import health, run_checks
from byax.errors import ByaxError
from byax.model import new_document
from byax.profiles import presets, source_hash

CLAIM = 'The bridge opened in 1932 and carries six lanes.'


def edit(doc, *ops, **envelope):
    return engine.apply(doc, {'operations': list(ops), **envelope})


def evidence_doc():
    doc = new_document('Bridge', CLAIM)
    doc, _ = edit(
        doc,
        {
            'type': 'add_reference',
            'id': 'atlas',
            'values': {'title': 'City atlas', 'version': '2024', 'text': 'The bridge opened in 1932.'},
        },
    )
    doc, _ = edit(
        doc,
        {
            'type': 'add_reference',
            'id': 'guide',
            'values': {
                'title': 'Guide',
                'retrieved_at': '2025-01-02',
                'text': 'Opened in 1931, the bridge has four lanes.',
            },
        },
    )
    return doc


def finding(target, outcome, evidence, start=0, end=26, **extra):
    return {
        'family': 'factual',
        'rule': 'factual.claim',
        'explanation': 'Claim review.',
        'target': target,
        'start': start,
        'end': end,
        'outcome': outcome,
        'evidence': evidence,
        **extra,
    }


def test_all_five_outcomes_with_exact_passages_hashes_and_relations():
    doc = evidence_doc()
    target = doc['blocks'][0]['id']
    values = {
        'provenance': {'engine': 'human reviewer'},
        'findings': [
            finding(
                target,
                'supported',
                [{'source_id': 'atlas', 'excerpt': 'The bridge opened in 1932', 'relation': 'quotation'}],
                claim=CLAIM[:26],
            ),
            finding(
                target,
                'contradicted',
                [{'source_id': 'guide', 'excerpt': 'four lanes', 'relation': 'paraphrase'}],
                31,
                48,
            ),
            finding(target, 'not_established', [], 31, 48),
            finding(
                target,
                'conflicting_sources',
                [
                    {'source_id': 'atlas', 'excerpt': 'opened in 1932', 'relation': 'quotation'},
                    {'source_id': 'guide', 'excerpt': 'Opened in 1931', 'relation': 'quotation'},
                ],
            ),
            finding(
                target,
                'needs_interpretation',
                [{'source_id': 'guide', 'excerpt': 'the bridge has four lanes', 'relation': 'inference'}],
                0,
                48,
            ),
        ],
    }
    doc, _ = edit(doc, {'type': 'record_findings', 'values': values})
    recorded = {f['outcome']: f for f in doc['review']['findings']}
    assert set(recorded) == {
        'supported',
        'contradicted',
        'not_established',
        'conflicting_sources',
        'needs_interpretation',
    }
    for item in recorded.values():
        assert item['status'] == 'needs review' and item['calibrated_probability'] is False
        assert item['claim'] == CLAIM[item['range']['start'] : item['range']['end']]
        for citation in item['evidence']:
            assert citation['source_version']['content_hash'] == source_hash(
                doc['references'][citation['source_id']]
            )
    assert {c['relation'] for f in recorded.values() for c in f['evidence']} == {
        'quotation',
        'paraphrase',
        'inference',
    }
    assert (
        recorded['not_established']['evidence'] == []
        and 'not proof of falsehood' in recorded['not_established']['note']
    )
    for bad in (
        [{'source_id': 'atlas', 'excerpt': 'invented sentence', 'relation': 'quotation'}],
        [{'source_id': 'missing', 'excerpt': 'x'}],
    ):
        with pytest.raises(ByaxError):
            edit(
                doc, {'type': 'record_findings', 'values': {'findings': [finding(target, 'supported', bad)]}}
            )
    with pytest.raises(ByaxError):
        edit(doc, {'type': 'record_findings', 'values': {'findings': [finding(target, 'supported', [])]}})
    with pytest.raises(ByaxError):
        edit(
            doc,
            {
                'type': 'record_findings',
                'values': {
                    'findings': [
                        finding(
                            target,
                            'conflicting_sources',
                            [{'source_id': 'atlas', 'excerpt': 'opened in 1932'}],
                        )
                    ]
                },
            },
        )
    with pytest.raises(ByaxError):
        edit(
            doc,
            {
                'type': 'record_findings',
                'values': {
                    'findings': [
                        {
                            **finding(
                                target, 'supported', [{'source_id': 'atlas', 'excerpt': 'opened in 1932'}]
                            ),
                            'claim': 'Something else',
                        }
                    ]
                },
            },
        )


def test_updating_or_removing_a_source_stales_only_dependent_findings():
    doc = evidence_doc()
    target = doc['blocks'][0]['id']
    doc, _ = edit(
        doc,
        {
            'type': 'record_findings',
            'values': {
                'findings': [
                    finding(
                        target, 'supported', [{'source_id': 'atlas', 'excerpt': 'The bridge opened in 1932'}]
                    ),
                    finding(
                        target, 'contradicted', [{'source_id': 'guide', 'excerpt': 'four lanes'}], 31, 48
                    ),
                ]
            },
        },
    )
    doc, _ = edit(
        doc,
        {
            'type': 'add_reference',
            'id': 'atlas',
            'values': {'title': 'City atlas', 'version': '2025', 'text': 'The bridge opened in 1933.'},
        },
    )
    status = {f['outcome']: f['status'] for f in doc['review']['findings']}
    assert status == {'supported': 'stale', 'contradicted': 'needs review'}
    assert any('source' in reason for f in doc['review']['findings'] for reason in f.get('stale_reasons', []))
    doc, _ = edit(doc, {'type': 'remove_reference', 'id': 'guide'})
    assert all(f['status'] == 'stale' for f in doc['review']['findings'])
    assert health(doc)['validation'] == 'stale'


def test_citation_attachment_and_deterministic_quotation_outcomes():
    doc = new_document('Q', 'She wrote "the sky is blue" and "the sea is red" today.')
    target = doc['blocks'][0]['id']
    doc, _ = edit(
        doc,
        {
            'type': 'add_reference',
            'id': 'diary',
            'values': {'title': 'Diary', 'text': 'Today the sky is blue.', 'target': target},
        },
    )
    doc, _ = edit(
        doc,
        {'type': 'format_text', 'target': target, 'start': 10, 'end': 27, 'marks': {'citation': 'diary'}},
        {'type': 'format_text', 'target': target, 'start': 32, 'end': 48, 'marks': {'citation': 'diary'}},
    )
    report = run_checks(doc, ['factual'])
    assert report['evidence'][0]['outcome'] == 'supported'
    assert report['evidence'][0]['evidence'][0]['relation'] == 'quotation'
    missing = next(f for f in report['findings'] if f['rule'] == 'factual.quotation')
    assert (
        missing['outcome'] == 'not_established'
        and missing['claim'] == '"the sea is red"'
        and missing['evidence'] == []
    )
    assert any(
        c['rule'] == 'factual.uncited_claims' and c['status'] == 'unmeasurable' for c in report['coverage']
    )
    exclusive = presets()['editorial_review'] | {'references': ['other'], 'source_authority': 'exclusive'}
    doc['profiles']['strict'] = exclusive
    filtered = run_checks(doc, ['factual'], profile='strict')
    assert not filtered['evidence'] and not filtered['findings']


def test_source_and_document_corrections_are_separate_and_requirements_cannot_be_loosened():
    doc = evidence_doc()
    target = doc['blocks'][0]['id']
    with pytest.raises(ByaxError) as error:
        edit(
            doc,
            {'type': 'set_text', 'target': target, 'text': 'Changed'},
            {'type': 'add_reference', 'id': 'atlas', 'values': {'text': 'Changed'}},
        )
    assert error.value.code == 'conflict'
    doc['settings']['requirements'] = {'must': {'kind': 'required_text', 'value': '1932'}}
    with pytest.raises(ByaxError) as error:
        edit(
            doc,
            {'type': 'set_text', 'target': target, 'text': 'The bridge opened long ago.'},
            {'type': 'set_requirement', 'id': 'must', 'values': {'kind': 'required_text', 'value': 'ago'}},
            required_suites=['requirements'],
        )
    assert error.value.code == 'failed_requirement'


@pytest.mark.parametrize('authority', ['exclusive', 'supplementary', 'open'])
def test_source_authority_policies_never_retrieve_outside_material(authority):
    from byax.providers import normalize_review_context

    doc = evidence_doc()
    doc['profiles']['p'] = presets()['editorial_review'] | {
        'references': ['atlas'],
        'source_authority': authority,
    }
    package = engine.context(doc, doc['blocks'][0]['id'], profile='p')
    assert set(package['references']) == {'atlas'}
    assert package['inclusion_report']['unselected_references'] == ['guide']
    assert (
        package['provenance']['retrieval'] == 'none'
        and package['provenance']['source_authority'] == authority
    )
    assert package['provenance']['outside_retrieval'] != 'performed'
    normalized = normalize_review_context({'profile': doc['profiles']['p'], 'references': doc['references']})
    assert set(normalized['references']) == {'atlas'} and normalized['retrieval']['performed'] is False


def test_cross_interface_checks_evidence_and_strict_publication(tmp_path):
    from byax.cli import execute
    from byax.storage import save_document

    doc = evidence_doc()
    doc['settings']['requirements'] = {'year': {'kind': 'required_text', 'value': '1932'}}
    path = tmp_path / 'claims.byax'
    save_document(doc, path)
    target = doc['blocks'][0]['id']
    recorded = execute(
        'apply',
        {
            'path': str(path),
            'input': {
                'operations': [
                    {
                        'type': 'record_findings',
                        'values': {
                            'findings': [
                                finding(
                                    target,
                                    'supported',
                                    [{'source_id': 'atlas', 'excerpt': 'The bridge opened in 1932'}],
                                )
                            ]
                        },
                    }
                ]
            },
        },
    )
    assert recorded['execution'] == 'completed'
    checked = execute('check', {'path': str(path), 'suite': ['requirements', 'factual'], 'strict': True})
    assert checked['validation'] == 'incomplete'
    with pytest.raises(ByaxError) as error:
        execute(
            'export',
            {
                'path': str(path),
                'out': str(tmp_path / 'out.txt'),
                'format': 'txt',
                'require_suite': ['factual'],
            },
        )
    assert error.value.code == 'incomplete_coverage'
    exported = execute(
        'export',
        {
            'path': str(path),
            'out': str(tmp_path / 'ok.txt'),
            'format': 'txt',
            'require_suite': ['requirements'],
        },
    )
    assert exported['execution'] == 'completed'
    with pytest.raises(ByaxError) as error:
        execute(
            'apply',
            {
                'path': str(path),
                'input': {'operations': [{'type': 'set_text', 'target': target, 'text': 'Opened later.'}]},
                'require_suite': ['requirements'],
            },
        )
    assert error.value.code == 'failed_requirement'
