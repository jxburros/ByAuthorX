"""Temporal codex (facts valid as of a chapter) and the deterministic continuity suite."""

import importlib.util
import json
from pathlib import Path

import pytest

from byax import codex, continuity, engine, series, storage
from byax.checks import DEFAULT_SUITES, run_checks
from byax.cli import execute
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document, validate_document

ROOT = Path(__file__).resolve().parents[1]


def _heading(identity, text):
    return {
        'id': identity,
        'type': 'heading',
        'text': text,
        'style': 'heading1',
        'marks': [],
        'attrs': {'level': 1},
    }


def _para(identity, text):
    return {'id': identity, 'type': 'paragraph', 'text': text, 'style': 'body', 'marks': [], 'attrs': {}}


def crafted():
    """Three chapters; Mara's hair turns grey in chapter 3, Tom is 12 until chapter 2 and 13 after."""
    doc = new_document('Crafted')
    doc['blocks'] = [
        _heading('ch1', 'Chapter 1'),
        _para('a1', "Mara's eyes were grey as the sea. Tom was twelve years old."),
        _para('a2', 'Mara had long black hair. Later, Mara’s hair was grey.'),
        _heading('ch2', 'Chapter 2'),
        _para('b1', "Mara's black hair blew loose. Tom, aged 13, ran ahead."),
        _heading('ch3', 'Chapter 3'),
        _para('c1', 'Mara had grey hair now. Mara’s hair was black once more. Tom was thirteen years old.'),
        _para('c2', 'Mara’s eyes were blue in this light.'),
    ]
    operations = [{'type': 'project_init'}]
    operations.append(
        {
            'type': 'codex_add',
            'id': 'cx_mara',
            'values': {
                'kind': 'character',
                'name': 'Mara Vell',
                'aliases': ['Mara'],
                'traits': [
                    {'name': 'eye colour', 'value': 'grey'},
                    {'name': 'hair', 'value': 'black', 'valid_to': 'ch2'},
                    {'name': 'hair', 'value': 'grey', 'valid_from': 'ch3'},
                ],
            },
        }
    )
    operations.append(
        {
            'type': 'codex_add',
            'id': 'cx_tom',
            'values': {
                'kind': 'character',
                'name': 'Tom',
                'traits': [
                    {'name': 'age', 'value': '12', 'valid_to': 'ch1'},
                    {'name': 'age', 'value': 'thirteen', 'valid_from': 'ch2'},
                ],
            },
        }
    )
    doc, _ = engine.apply(doc, {'operations': operations})
    return doc


def _found(report):
    return sorted((f['rule'], f['affected_ids'][0], f['text']) for f in report['findings'])


def test_trait_validity_is_stored_validated_and_editable():
    doc = crafted()
    hair = [t for t in doc['codex']['entries']['cx_mara']['traits'] if t['name'] == 'hair']
    assert hair[0]['valid_to'] == 'ch2' and hair[1]['valid_from'] == 'ch3'
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'codex_add_trait',
                    'target': 'cx_tom',
                    'name': 'hair',
                    'value': 'red',
                    'valid_from': 'b1',
                },
                {
                    'type': 'codex_update_trait',
                    'target': 'cx_tom',
                    'trait': 'trait_1',
                    'values': {'valid_to': None},
                },
            ]
        },
    )
    tom = doc['codex']['entries']['cx_tom']['traits']
    assert 'valid_to' not in tom[0] and tom[2]['valid_from'] == 'b1'
    broken = json.loads(json.dumps(doc))
    broken['codex']['entries']['cx_tom']['traits'][0]['valid_from'] = ' padded '
    with pytest.raises(ByaxError):
        validate_document(broken)


def test_facts_at_returns_the_facts_in_force_at_a_position():
    doc = crafted()
    early = codex.facts_at(doc, 'a1')
    assert sorted((f['trait'], f['value']) for f in early['facts']) == [
        ('age', '12'),
        ('eye colour', 'grey'),
        ('hair', 'black'),
    ]
    assert sorted((f['trait'], f['value']) for f in early['not_yet']) == [
        ('age', 'thirteen'),
        ('hair', 'grey'),
    ]
    late = codex.facts_at(doc, 'ch3')  # a heading, and binder items resolve to their heading
    assert sorted((f['trait'], f['value']) for f in late['facts']) == [
        ('age', 'thirteen'),
        ('eye colour', 'grey'),
        ('hair', 'grey'),
    ]
    assert sorted(f['value'] for f in late['expired']) == ['12', 'black']
    item = next(k for k, v in doc['project']['items'].items() if v.get('heading') == 'ch2')
    assert codex.facts_at(doc, item)['position']['target'] == 'ch2'
    with pytest.raises(ByaxError):
        codex.facts_at(doc, 'nowhere')
    doc['codex']['entries']['cx_tom']['traits'][0]['valid_to'] = 'deleted_heading'
    assert [f['value'] for f in codex.facts_at(doc, 'a1')['unresolved']] == ['12']


def test_continuity_findings_on_the_crafted_fixture_have_exact_ranges():
    doc = crafted()
    report = run_checks(doc, ['continuity'])
    assert _found(report) == [
        ('continuity.before_established', 'a2', 'grey'),
        ('continuity.expired_fact', 'c1', 'black'),
        ('continuity.trait_mismatch', 'c2', 'blue'),
    ]
    for finding in report['findings']:
        text = next(b['text'] for b in doc['blocks'] if b['id'] == finding['affected_ids'][0])
        assert text[finding['range']['start'] : finding['range']['end']] == finding['text']
        assert finding['family'] == 'continuity' and finding['operations'] == []
    assert 'continuity' not in DEFAULT_SUITES
    assert 'continuity' not in run_checks(doc)['suites']  # off by default


def test_continuity_is_incremental_and_reuses_unchanged_units():
    doc = crafted()
    first = run_checks(doc, ['continuity'], track=True)
    again = run_checks(doc, ['continuity'], previous=first, track=True)
    assert continuity.UNIT in again['evaluation']['reused'] and not again['evaluation']['rechecked']


def _fixture():
    spec = importlib.util.spec_from_file_location('codex_benchmark', ROOT / 'scripts' / 'codex_benchmark.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = json.loads(
        (ROOT / 'tests' / 'fixtures' / 'codex' / 'pride-and-prejudice-1-6.json').read_text(encoding='utf-8')
    )
    return module.fixture_document(fixture)[0]


PLANTED = [
    # (block, appended sentence, expected rule or None for a consistent statement)
    ('p_1_3', ' Elizabeth’s eyes were blue.', 'continuity.trait_mismatch'),
    ('p_2_1', ' Jane had red hair.', 'continuity.trait_mismatch'),
    ('p_1_4', " Kitty's hair was curly.", 'continuity.before_established'),
    ('p_3_1', ' Lydia was sixteen years old.', 'continuity.before_established'),
    ('p_5_1', ' Lydia was sixteen years old.', None),
    ('p_4_1', " Jane's fair hair shone.", None),
    ('p_4_2', ' Elizabeth had dark eyes.', None),
    ('p_4_3', " Kitty's hair was curly.", None),
]


def test_continuity_precision_on_planted_contradictions_in_the_novel_fixture():
    doc = _fixture()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'codex_add_trait', 'target': 'cx_elizabeth', 'name': 'eyes', 'value': 'dark'},
                {
                    'type': 'codex_add_trait',
                    'target': 'cx_jane',
                    'name': 'hair',
                    'value': 'fair',
                    'valid_from': 'h_0',
                },
                {
                    'type': 'codex_add_trait',
                    'target': 'cx_kitty',
                    'name': 'hair style',
                    'value': 'curly',
                    'valid_from': 'h_3',
                },
                {
                    'type': 'codex_add_trait',
                    'target': 'cx_lydia',
                    'name': 'age',
                    'value': '15',
                    'valid_to': 'h_4',
                },
                {
                    'type': 'codex_add_trait',
                    'target': 'cx_lydia',
                    'name': 'age',
                    'value': 'sixteen',
                    'valid_from': 'h_5',
                },
            ]
        },
    )
    assert run_checks(doc, ['continuity'])['findings'] == []  # Austen's own text raises nothing
    by_id = {b['id']: b for b in doc['blocks']}
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'insert_text', 'target': block, 'offset': len(by_id[block]['text']), 'text': text}
                for block, text, _ in PLANTED
            ]
        },
    )
    found = {(f['affected_ids'][0], f['rule']) for f in run_checks(doc, ['continuity'])['findings']}
    expected = {(block, rule) for block, _, rule in PLANTED if rule}
    assert found == expected  # precision and recall 1.0 on the planted set


def test_series_facts_hold_across_books(tmp_path):
    folder = tmp_path / 'series'
    for name in ('one', 'two'):
        (folder / name).mkdir(parents=True)
    data = series.new_series(
        'Saga',
        {
            'cx_ana': {
                'id': 'cx_ana',
                'kind': 'character',
                'name': 'Ana',
                'traits': [
                    {'id': 'trait_1', 'name': 'hair', 'value': 'brown', 'valid_to': 'book:1'},
                    {'id': 'trait_2', 'name': 'hair', 'value': 'white', 'valid_from': 'book:2'},
                ],
            }
        },
        [],
    )
    data['documents'] = ['one/book.byax', 'two/book.byax']
    series.save(folder / 'saga.byaxseries', data)
    for name, text in (('one', "Ana's hair was brown."), ('two', "Ana's hair was brown again.")):
        doc = new_document(name, text)
        doc['settings']['series'] = {'path': '../saga.byaxseries'}
        storage.save_document(doc, folder / name / 'book.byax')
    first = execute('check', {'path': str(folder / 'one' / 'book.byax'), 'family': ['continuity']})
    assert first['findings'] == []
    second = execute('check', {'path': str(folder / 'two' / 'book.byax'), 'family': ['continuity']})
    (finding,) = second['findings']
    assert finding['rule'] == 'continuity.expired_fact' and finding['measured']['book'] == 2
    facts = execute(
        'codex',
        {
            'path': str(folder / 'two' / 'book.byax'),
            'action': 'facts-at',
            'target': second['findings'][0]['affected_ids'][0],
        },
    )
    assert [f['value'] for f in facts['facts']] == ['white'] and facts['position']['book'] == 2


def test_cli_and_mcp_facts_at_parity(tmp_path):
    path = tmp_path / 'crafted.byax'
    storage.save_document(crafted(), path)
    arguments = {'path': str(path), 'action': 'facts-at', 'target': 'c1'}
    local = execute('codex', arguments)
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {'name': 'byax_entity', 'arguments': arguments},
        }
    )['result']
    assert response['structuredContent']['facts'] == local['facts']
    check = execute('check', {'path': str(path), 'family': ['continuity']})
    assert len(check['findings']) == 3
