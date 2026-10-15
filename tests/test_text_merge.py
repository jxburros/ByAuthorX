import copy
import random

import pytest

from byax.compare import plan_merge
from byax.text_merge import merge_text, splices


def block(text, marks=None):
    return {'id': 'p1', 'type': 'paragraph', 'text': text, 'marks': marks or [], 'attrs': {}}


def test_disjoint_sentences_preserve_marks():
    base = block('First sentence. Last sentence.', [{'type': 'bold', 'start': 0, 'end': 5}])
    ours = block('First phrase. Last sentence.', base['marks'])
    theirs = block('First sentence. Last phrase.', base['marks'])
    operations, conflicts, _ = plan_merge(*({'blocks': [b]} for b in (base, ours, theirs)))
    assert not conflicts
    assert operations[0]['values']['text'] == 'First phrase. Last phrase.'
    assert operations[0]['values']['marks'] == base['marks']


def test_overlapping_replacements_report_scalar_ranges():
    base, ours, theirs = map(block, ['Hello world', 'Hello reader', 'Hello writer'])
    _, conflicts, _ = plan_merge(*({'blocks': [b]} for b in (base, ours, theirs)))
    assert conflicts[0]['fields'] == ['text+marks']
    assert conflicts[0]['ranges']
    for item in conflicts[0]['ranges']:
        assert 6 <= item['base_start'] <= item['base_end'] <= 11


@pytest.mark.parametrize('token', ['👩🏽\u200d💻', 'e\u0301', '🇺🇸', '😀', 'العربية'])
def test_unicode_splices_never_split_graphemes(token):
    base = block(f'Alpha {token}. Omega.')
    ours = block(f'Beta {token}. Omega.')
    theirs = block(f'Alpha {token}. End.')
    assert merge_text(base, ours, theirs)['text'] == f'Beta {token}. End.'
    if token != 'العربية':
        assert splices(token, 'X') == [(0, len(token), 'X')]


def test_disjoint_edits_are_commutative_seeded():
    rng = random.Random(69)
    for _ in range(200):
        tokens = [chr(0x4E00 + i) for i in range(30)]
        first, second = sorted(rng.sample(range(30), 2))
        base = block(''.join(tokens))
        left, right = tokens.copy(), tokens.copy()
        left[first] = rng.choice(['', '😀', 'e\u0301'])
        right[second] = rng.choice(['', '👩\u200d💻', 'extra'])
        a, b = block(''.join(left)), block(''.join(right))
        expected = tokens.copy()
        expected[first], expected[second] = left[first], right[second]
        merged = merge_text(base, a, b)
        assert merged is not None
        assert merged == merge_text(base, b, a)
        assert merged['text'] == ''.join(expected)


def test_ambiguous_marks_conflict_without_mutating_inputs():
    base = block('First. Last.')
    ours = block('First! Last.', [{'type': 'bold', 'start': 0, 'end': 6}])
    theirs = block('First. Last!')
    before = copy.deepcopy((base, ours, theirs))
    assert merge_text(base, ours, theirs) is None
    assert (base, ours, theirs) == before


def test_identical_edits_applied_once_and_insert_boundary_is_deterministic():
    base = block('abc')
    assert merge_text(base, block('aXbc'), block('aXbc'))['text'] == 'aXbc'
    assert merge_text(base, block('aXbc'), block('aBc'))['text'] == 'aXBc'
    assert merge_text(base, block('aXbc'), block('aYbc')) is None
