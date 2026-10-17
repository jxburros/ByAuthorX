"""Fiction checks stay incremental (spec 12.4): an edit re-runs only the units that can depend on it."""

import itertools
import string

import pytest

from byax import engine
from byax.checks import record_checks, run_checks, stored_report
from byax.model import new_document

SUITES = ['fiction']
VOLATILE = ('revision', 'status_history', 'dependencies')
PARAGRAPHS = 12
# Word echoes look back 30 words and nothing else is windowed, so a 25-word paragraph reaches two back.
WINDOW = {
    'sensitivity': 'broad',
    'fiction': {
        'enabled': ['fiction.word_echo'],
        'disabled': [
            'fiction.phrase_echo',
            'fiction.tag_repetition',
            'fiction.sentence_opening',
            'fiction.sentence_rhythm',
            'fiction.paragraph_rhythm',
        ],
    },
}


def words(count, offset):
    """Distinct invented lower-case words, so nothing echoes unless a test makes it."""
    letters = string.ascii_lowercase
    names = (''.join(c) for c in itertools.product(letters, repeat=5))
    return list(itertools.islice(names, offset * count, (offset + 1) * count))


def story(chapters=2):
    doc = new_document('Story', '')
    doc['blocks'] = []
    for chapter in range(chapters):
        doc['blocks'].append(
            {
                'id': f'h{chapter}',
                'type': 'heading',
                'text': f'Chapter {chapter}',
                'style': 'heading1',
                'marks': [],
                'attrs': {'level': 1},
            }
        )
        for number in range(PARAGRAPHS):
            sentence = words(25, chapter * PARAGRAPHS + number)
            doc['blocks'].append(
                {
                    'id': f'p{chapter}_{number}',
                    'type': 'paragraph',
                    'text': ' '.join(sentence).capitalize() + '.',
                    'style': 'body',
                    'marks': [],
                    'attrs': {},
                }
            )
    return doc


def edit(doc, *ops):
    return engine.apply(doc, {'operations': list(ops)})[0]


def normalized(report):
    return sorted(
        ({k: v for k, v in f.items() if k not in VOLATILE} for f in report['findings']), key=lambda f: f['id']
    )


def windows(report, key):
    return sorted(u for u in report['evaluation'][key] if u.startswith('fiction:window:'))


def both(doc, changed, profile=WINDOW):
    first = run_checks(doc, SUITES, profile=profile, limit=10**6, track=True)
    incremental = run_checks(changed, SUITES, profile=profile, limit=10**6, previous=first, track=True)
    full = run_checks(changed, SUITES, profile=profile, limit=10**6, track=True)
    assert normalized(incremental) == normalized(full)
    assert incremental['validation'] == full['validation']
    assert incremental['evaluation']['mode'] == 'incremental'
    return first, incremental, full


def test_editing_one_paragraph_rechecks_only_its_own_unit_and_the_windows_that_reach_it():
    doc = story()
    changed = edit(
        doc, {'type': 'set_text', 'target': 'p0_5', 'text': 'Entirely new text for this paragraph.'}
    )
    _, incremental, _ = both(doc, changed)
    # Paragraph 5, plus the next two whose 30-word look-back (two 25-word paragraphs) reaches it.
    assert windows(incremental, 'rechecked') == [f'fiction:window:p0_{n}' for n in (5, 6, 7)]
    assert 'block:p0_5' in incremental['evaluation']['rechecked']
    assert 'block:p0_4' in incremental['evaluation']['reused']
    assert 'fiction:window:p0_4' in incremental['evaluation']['reused']
    assert 'fiction:window:p0_8' in incremental['evaluation']['reused']
    # The second chapter is untouched.
    assert not [u for u in incremental['evaluation']['rechecked'] if 'p1_' in u or u.endswith('1')]


def test_windows_do_not_reach_across_headings():
    doc = story()
    changed = edit(
        doc, {'type': 'set_text', 'target': f'p0_{PARAGRAPHS - 1}', 'text': 'Changed final paragraph.'}
    )
    _, incremental, _ = both(doc, changed)
    assert 'fiction:window:p1_0' in incremental['evaluation']['reused']
    first = edit(doc, {'type': 'set_text', 'target': 'p1_0', 'text': 'Changed opening paragraph.'})
    _, second, _ = both(doc, first)
    assert windows(second, 'rechecked') == [f'fiction:window:p1_{n}' for n in (0, 1, 2)]


def test_a_new_echo_is_found_incrementally_in_the_later_paragraph_only():
    doc = story()
    shared = doc['blocks'][1 + 3]['text'].rstrip('.').split()[-1]
    later = doc['blocks'][1 + 4]
    changed = edit(
        doc,
        {'type': 'set_text', 'target': later['id'], 'text': 'Then ' + shared + ' returned. ' + later['text']},
    )
    _, incremental, full = both(doc, changed)
    assert [(f['rule'], f['affected_ids']) for f in incremental['findings']] == [
        ('fiction.word_echo', [later['id']])
    ]
    assert incremental['findings'][0]['text'].lower() == shared and full['summary']['total'] == 1


@pytest.mark.parametrize(
    'name, operations',
    [
        ('insert', lambda d: [{'type': 'insert_paragraph', 'text': 'Fresh words only.', 'after': 'p0_3'}]),
        ('delete', lambda d: [{'type': 'delete_block', 'target': 'p0_3'}]),
        ('heading level', lambda d: [{'type': 'set_attributes', 'target': 'h1', 'values': {'level': 2}}]),
        ('move', lambda d: [{'type': 'move_section', 'target': 'h1', 'after': 'p0_1'}]),
    ],
)
def test_structural_edits_match_a_full_recheck(name, operations):
    doc = story()
    both(doc, edit(doc, *operations(doc)))


def test_all_rules_incremental_matches_full_on_the_seeded_fixture():
    import json
    from pathlib import Path

    from byax.exchange import import_document

    fixtures = Path(__file__).parent / 'fixtures' / 'fiction'
    doc, _ = import_document(str(fixtures / 'seeded.md'))
    profile = json.loads((fixtures / 'profile.json').read_text(encoding='utf-8'))
    target = next(b['id'] for b in doc['blocks'] if b.get('text', '').startswith('The silver lantern'))
    changed = edit(
        doc, {'type': 'set_text', 'target': target, 'text': 'The silver lantern swung above the gate.'}
    )
    first, incremental, full = both(doc, changed, profile)
    assert full['summary']['total'] == first['summary']['total'] - 1
    assert incremental['evaluation']['reused'], 'a one-paragraph edit must reuse other units'
    # The silver echo disappears; the chapter and names units that read this paragraph re-run.
    assert 'fiction.word_echo' not in {f['rule'] for f in incremental['findings'] if f['text'] == 'silver'}
    assert any(u.startswith('context:fiction_chapter:') for u in incremental['evaluation']['reused'])


def test_names_and_chapter_units_depend_on_their_own_text_only():
    doc = story()
    profile = {'sensitivity': 'broad', 'fiction': {'thresholds': {'adverb_min_words': 50}}}
    changed = edit(doc, {'type': 'set_text', 'target': 'p1_2', 'text': 'Changed text in chapter one.'})
    _, incremental, _ = both(doc, changed, profile)
    assert 'context:fiction_chapter:h1' in incremental['evaluation']['rechecked']
    assert 'context:fiction_chapter:h0' in incremental['evaluation']['reused']
    # Invented names are compared across the whole document, so that unit always re-runs.
    assert 'context:fiction_names' in incremental['evaluation']['rechecked']


def test_profile_and_threshold_changes_recheck_what_they_affect():
    doc = story()
    first = run_checks(doc, SUITES, profile=WINDOW, track=True)
    tuned = {**WINDOW, 'fiction': {**WINDOW['fiction'], 'thresholds': {'echo_window_words': 60}}}
    other = run_checks(doc, SUITES, profile=tuned, previous=first, track=True)
    assert other['evaluation']['mode'] == 'full'
    wide = run_checks(doc, SUITES, profile=tuned, track=True)
    # A wider look-back reaches further back, so each window unit depends on more paragraphs.
    a = {k: v['fingerprint'] for k, v in first['units'].items() if k.startswith('fiction:window:')}
    b = {k: v['fingerprint'] for k, v in wide['units'].items() if k.startswith('fiction:window:')}
    assert a.keys() == b.keys() and all(a[k] != b[k] for k in a)


def test_section_profile_change_rechecks_only_that_section():
    doc = story()
    first = run_checks(doc, SUITES, profile=WINDOW, limit=10**6, track=True)
    changed = edit(
        doc,
        {
            'type': 'set_section_profile',
            'target': 'h1',
            'values': {'overrides': {'fiction': {'thresholds': {'echo_window_words': 60}}}},
        },
    )
    incremental = run_checks(changed, SUITES, profile=WINDOW, limit=10**6, previous=first, track=True)
    full = run_checks(changed, SUITES, profile=WINDOW, limit=10**6, track=True)
    assert normalized(incremental) == normalized(full)
    chapter_one = [u for u in incremental['evaluation']['rechecked'] if u.startswith('fiction:window:p1_')]
    assert len(chapter_one) == PARAGRAPHS
    assert not [u for u in incremental['evaluation']['rechecked'] if 'p0_' in u]


def test_recorded_fiction_units_support_incremental_rechecks_after_commit():
    doc = story()
    record_checks(doc, {'suites': SUITES, 'profile': None})
    previous = stored_report(doc)
    assert previous is not None
    assert any(key.startswith('fiction:window:') for key in previous['units'])
    changed = edit(doc, {'type': 'set_text', 'target': 'p0_2', 'text': 'Brand new words here.'})
    again = record_checks(changed, {'suites': SUITES, 'incremental': True})
    assert again == ['review.findings', 'review.health']
    reused = changed['review']['health']['evaluation']['reused']
    assert (
        'fiction:window:p0_9' in reused
        and 'fiction:window:p0_2' in changed['review']['health']['evaluation']['rechecked']
    )
