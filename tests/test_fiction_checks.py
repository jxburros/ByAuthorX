"""Fiction analysis checks (competitiveness plan 6.2): rules, tuning, fixtures, proposals and the CLI."""

import json
from collections import Counter
from pathlib import Path

import pytest

from byax import engine, fiction_checks
from byax.checks import DEFAULT_SUITES, SUITES, rule_catalog, run_checks
from byax.cli import execute
from byax.errors import ByaxError
from byax.exchange import import_document
from byax.model import new_document
from byax.profiles import check_suites, validate_profile

FIXTURES = Path(__file__).parent / 'fixtures' / 'fiction'
BROAD = {'sensitivity': 'broad'}


def fixture_doc(name):
    doc, _ = import_document(str(FIXTURES / name))
    doc['profiles']['fixture'] = json.loads((FIXTURES / 'profile.json').read_text(encoding='utf-8'))
    return doc


def prose(*paragraphs, heading=None, language=None):
    doc = new_document('Story', '\n'.join(paragraphs))
    if heading:
        doc['blocks'].insert(
            0,
            {
                'id': 'h_top',
                'type': 'heading',
                'text': heading,
                'style': 'heading1',
                'marks': [],
                'attrs': {'level': 1},
            },
        )
    if language:
        doc['metadata']['language'] = language
    return doc


def found(doc, profile=None, **options):
    report = run_checks(doc, ['fiction'], limit=10**6, profile=profile or BROAD, **options)
    return [(f['rule'].removeprefix('fiction.'), f['text']) for f in report['findings']]


def broad(**fiction):
    return {**BROAD, 'fiction': fiction} if fiction else BROAD


# Fixtures: the exit criterion -------------------------------------------------------


def test_clean_fixture_produces_no_findings_at_every_sensitivity():
    doc = fixture_doc('clean.md')
    assert found(doc, 'fixture') == []
    for level in ('conservative', 'balanced', 'broad'):
        assert found(doc, {'sensitivity': level}) == []
    report = run_checks(doc, ['fiction'], profile='fixture', limit=10**6)
    # No findings is not a clean pass: craft judgement is declared unmeasurable.
    assert report['validation'] == 'incomplete'
    assert any(c.get('rule') == 'fiction.craft_judgement' for c in report['coverage'])


def test_seeded_fixture_produces_exactly_the_annotated_findings():
    expected = json.loads((FIXTURES / 'seeded-expected.json').read_text(encoding='utf-8'))['findings']
    report = run_checks(fixture_doc('seeded.md'), ['fiction'], profile='fixture', limit=10**6)
    prefixes = {i['rule']: i['text_starts'] for i in expected if 'text_starts' in i}
    observed = Counter(
        (f['rule'], prefixes[f['rule']] if f['rule'] in prefixes else f['text']) for f in report['findings']
    )
    assert observed == Counter((i['rule'], i.get('text') or i['text_starts']) for i in expected)
    for rule, prefix in prefixes.items():
        assert all(f['text'].startswith(prefix) for f in report['findings'] if f['rule'] == rule)


def test_findings_have_exact_ranges_and_are_advisory():
    doc = fixture_doc('seeded.md')
    report = run_checks(doc, ['fiction'], profile='fixture', limit=10**6)
    blocks = {b['id']: b for b in doc['blocks']}
    for finding in report['findings']:
        assert finding['family'] == 'fiction' and finding['suite'] == 'fiction'
        assert finding['severity'] in ('advisory', 'warning') and finding['policy'] == 'advisory'
        target = finding['affected_ids'][0]
        span = finding['range']
        assert blocks[target]['text'][span['start'] : span['end']] == finding['text']
        assert finding['status'] == 'open' and finding['dependencies']['unit']


def test_runs_are_deterministic():
    doc = fixture_doc('seeded.md')
    first = run_checks(doc, ['fiction'], profile='fixture', limit=10**6)
    second = run_checks(doc, ['fiction'], profile='fixture', limit=10**6)
    assert first['findings'] == second['findings']


def test_suite_is_opt_in_and_the_rule_catalog_lists_every_rule():
    assert 'fiction' in SUITES and 'fiction' not in DEFAULT_SUITES
    assert set(fiction_checks.RULES) == set(fiction_checks.GROUPS)
    catalog = rule_catalog()
    for rule in fiction_checks.RULES:
        assert catalog[rule]['family'] == 'fiction' and catalog[rule]['languages'] == 'all'
    assert check_suites({'rule_families': ['fiction']}) == ['fiction', 'requirements', 'structure']
    assert run_checks(fixture_doc('seeded.md'))['summary']['total'] == 0


# Sensitivity and per-rule tuning ---------------------------------------------------------------


def test_sensitivity_gates_rules_and_enabled_disabled_override_it():
    doc = fixture_doc('seeded.md')
    settings = {'thresholds': json.loads((FIXTURES / 'profile.json').read_text())['fiction']['thresholds']}

    def rules(sensitivity, **extra):
        profile = {'sensitivity': sensitivity, 'fiction': {**settings, **extra}}
        return {r for r, _ in found(doc, profile)}

    conservative = rules('conservative')
    assert conservative == {'name_spelling', 'phrase_echo'}
    balanced = rules('balanced')
    assert balanced - conservative == {'word_echo', 'filter_words', 'tag_adverb', 'tag_repetition'}
    assert 'show_tell' not in balanced and 'show_tell' in rules('broad')
    assert 'show_tell' in rules('conservative', enabled=['fiction.show_tell'])
    assert 'name_spelling' not in rules('conservative', disabled=['fiction.name_spelling'])
    assert 'filter_words' not in rules('broad', disabled=['fiction.filter_words'])


def test_thresholds_tune_a_rule():
    doc = prose('Mara saw the lamp. She noticed nothing else.')
    assert [r for r, _ in found(doc)] == ['filter_words', 'filter_words']
    assert found(doc, broad(thresholds={'filter_per_paragraph': 3})) == []
    assert [
        r for r, _ in found(prose('Mara saw the lamp.'), broad(thresholds={'filter_per_paragraph': 1}))
    ] == ['filter_words']


def test_settings_validation_rejects_unknown_or_out_of_range_values():
    for bad in (
        {'thresholds': {'nonsense': 1}},
        {'thresholds': {'echo_window_words': True}},
        {'thresholds': {'echo_window_words': 1}},
        {'thresholds': {'echo_window_words': 30.5}},
        {'enabled': ['fiction.nothing']},
        {'enabled': ['fiction.word_echo'], 'disabled': ['fiction.word_echo']},
        {'other': 1},
        'word_echo',
    ):
        with pytest.raises(ByaxError):
            validate_profile({**BROAD, 'fiction': bad})
    assert validate_profile({**BROAD, 'fiction': {'thresholds': {'adverb_per_100_words': 3.5}}})[
        'fiction'
    ] == {
        'enabled': [],
        'disabled': [],
        'thresholds': {'adverb_per_100_words': 3.5},
    }


def test_section_profiles_tune_fiction_checks_per_section():
    doc = prose('Mara saw the lamp. She noticed nothing else.', heading='Dream', language='en')
    doc['blocks'].extend(
        [
            {
                'id': 'h2',
                'type': 'heading',
                'text': 'Waking',
                'style': 'heading1',
                'marks': [],
                'attrs': {'level': 1},
            },
            {
                'id': 'p_wake',
                'type': 'paragraph',
                'text': 'Mara saw the lamp. She noticed nothing else.',
                'style': 'body',
                'marks': [],
                'attrs': {},
            },
        ]
    )
    doc['settings']['section_profiles'] = {
        'h_top': {'overrides': {'fiction': {'disabled': ['fiction.filter_words']}}}
    }
    report = run_checks(doc, ['fiction'], limit=10**6, profile=BROAD)
    assert {f['affected_ids'][0] for f in report['findings']} == {'p_wake'}


# Rules ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    'text, expected',
    [
        ('"No," he hissed.', [('tag_bookism', 'hissed')]),
        ('“No,” he hissed.', [('tag_bookism', 'hissed')]),
        ('"No," he said.', []),
        ('"No," he said softly.', [('tag_adverb', 'softly')]),
        ('"No," he softly said.', [('tag_adverb', 'softly')]),
        ('"No," he said, softly.', [('tag_adverb', 'softly')]),
        ('He said softly that he would come.', []),
        ('"Only," he said, "family matters."', []),
        ('He walked slowly to the door.', []),
        ('Mara saw the lamp, and she felt the cold.', [('filter_words', 'saw'), ('filter_words', 'felt')]),
        ('Mara saw the lamp.', []),
        ('"I saw it and felt it," she said.', []),
        (
            'She could see the harbor and began to run.',
            [('filter_words', 'could see'), ('filter_words', 'began to')],
        ),
        (
            'She was furious. He felt very sad.',
            [('show_tell', 'was furious'), ('show_tell', 'felt very sad')],
        ),
        ('"I am furious," she said.', []),
    ],
)
def test_paragraph_rules(text, expected):
    assert found(prose(text)) == expected


def test_tag_repetition_counts_non_plain_tags_in_a_window():
    lines = [
        '"A," she whispered.',
        '"B," he said.',
        '"C," she whispered.',
        '"D," he said.',
        '"E," she whispered.',
    ]
    repeats = [t for r, t in found(prose(*lines)) if r == 'tag_repetition']
    assert repeats == ['whispered']
    assert 'tag_repetition' not in dict(found(prose(*lines[:4])))
    assert 'tag_repetition' not in dict(found(prose(*['"A," he said.'] * 6)))


def test_sentence_openings_ignore_dialogue_and_report_once_per_run():
    assert found(
        prose('He ran. He hid. He waited. He listened.'), broad(disabled=['fiction.sentence_rhythm'])
    ) == [('sentence_opening', 'He')]
    assert found(prose('He ran. He hid. "He waited," she said.')) == []
    assert found(prose('He ran. He hid. She waited. He listened.')) == []


def test_word_echo_respects_window_names_and_stopwords():
    window = broad(thresholds={'echo_window_words': 6})
    assert found(prose('The lantern swung past the dark lantern.'), window) == [('word_echo', 'lantern')]
    assert found(prose('The lantern swung far beyond the doorway and the other lantern.'), window) == []
    # Names, short words and stopwords never echo; plural forms do.
    assert found(prose('Kestrel saw Kestrel again.'), window) == []
    assert found(prose('Lanterns hung beside the lantern.'), window) == [('word_echo', 'lantern')]
    assert found(prose('Another one, another one.'), window) == []


def test_phrase_echo_merges_overlapping_trigrams_into_one_range():
    text = 'She counted the broken stairs. Much later she counted the broken stairs again.'
    assert found(prose(text), broad(thresholds={'echo_window_words': 5})) == [
        ('phrase_echo', 'counted the broken stairs')
    ]


def test_echoes_look_back_across_paragraphs_but_not_across_headings():
    first = 'The silver lantern swung.'
    second = 'Beyond it hung a silver moon.'
    assert found(prose(first, second)) == [('word_echo', 'silver')]
    doc = prose(first, second)
    doc['blocks'].insert(
        1,
        {
            'id': 'h',
            'type': 'heading',
            'text': 'Two',
            'style': 'heading1',
            'marks': [],
            'attrs': {'level': 1},
        },
    )
    assert found(doc) == []


def test_rhythm_and_length_rules():
    even = ' '.join(f'Word{chr(97 + i)} stood by the old gray wall tonight.' for i in range(6))
    assert [r for r, _ in found(prose(even))].count('sentence_rhythm') == 1
    varied = 'Short. A much longer sentence runs on and on for a while. Tiny. Another fairly long sentence follows.'
    assert 'sentence_rhythm' not in dict(found(prose(varied * 3)))
    long_text = ' '.join(['The river kept moving past the mill.'] * 9)
    assert ('paragraph_length', long_text) in found(
        prose(long_text), broad(thresholds={'paragraph_max_words': 50})
    )
    equal = [
        f'Paragraph {chr(97 + i)} has precisely this many words arranged to look just like every other paragraph in the whole story so far.'
        for i in range(6)
    ]
    assert [r for r, _ in found(prose(*equal))].count('paragraph_rhythm') == 1


def test_chapter_rules_need_enough_text_and_report_on_the_heading():
    adverbs = 'He walked slowly and spoke quietly, smiling gently, then waited patiently. ' * 5
    doc = prose(adverbs, heading='Chapter One')
    report = run_checks(doc, ['fiction'], profile=broad(thresholds={'adverb_min_words': 50}), limit=10**6)
    [finding] = [f for f in report['findings'] if f['rule'] == 'fiction.adverb_density']
    assert finding['affected_ids'] == ['h_top'] and finding['text'] == 'Chapter One'
    assert finding['measured']['per_100_words'] > 20
    assert 'adverb_density' not in dict(found(doc))  # below the default 300-word minimum
    sight = 'Bright gleaming crimson shadows and pale golden light. ' * 8
    result = found(
        prose(sight, heading='Glass'), broad(thresholds={'sensory_min_words': 50, 'sensory_min_hits': 4})
    )
    assert ('sensory_balance', 'Glass') in result


def test_intentional_passages_and_unsupported_languages_are_not_analysed():
    doc = prose('Mara saw the lamp. She noticed nothing else.')
    doc['blocks'][0]['attrs']['intentional'] = True
    assert found(doc) == []
    doc = prose('Mara saw the lamp. She noticed nothing else.', language='fr')
    report = run_checks(doc, ['fiction'], profile=BROAD, limit=10**6)
    assert report['findings'] == []
    assert any(
        c.get('rule') == 'fiction.language' and c['status'] == 'unmeasurable' for c in report['coverage']
    )


def test_word_lists_are_data_files_keyed_by_language():
    from byax.prose import languages, wordlist

    assert 'en' in languages('fiction') and 'en' in languages('readability')
    lists = wordlist('fiction', 'en')
    assert {'filter_words', 'speech_verbs', 'sensory', 'stopwords'} <= set(lists)
    assert wordlist('fiction', 'xx') is None and wordlist('fiction', '../en') is None


# Names and proposals ---------------------------------------------------------------------------


NAME_TEXT = (
    'Aldric led the way. Aldric did not look back. Aldric knocked twice. Aldrick opened the gate. '
    'The cart of Aldric waited.'
)


def test_name_spelling_proposes_the_common_spelling_without_changing_text():
    doc = prose(NAME_TEXT)
    report = run_checks(doc, ['fiction'], profile=BROAD, limit=10**6)
    [finding] = [f for f in report['findings'] if f['rule'] == 'fiction.name_spelling']
    assert finding['text'] == 'Aldrick' and finding['severity'] == 'warning'
    assert finding['measured']['suggested'] == 'Aldric'
    assert finding['repair'] == {'kind': 'automatic', 'separate_operation': True}
    assert doc['blocks'][0]['text'] == NAME_TEXT
    repaired, _ = engine.apply(doc, {'operations': finding['operations']})
    assert (
        'Aldrick' not in repaired['blocks'][0]['text'] and repaired['blocks'][0]['text'].count('Aldric') == 5
    )


@pytest.mark.parametrize(
    'text',
    [
        'Aldric came. Aldric left. Aldrick came. Aldrick left.',  # both frequent: two characters
        'Aldric came. Aldric left. Aldric sat. Aldrick came.' + ' Aldrick sat.' * 3,
        'Aldric came once and Orrin wandered.',
        'Aldric came. Aldric left. Brenwick sat.',  # different first letters are different names
        'Aldric came. Aldric left. Aldrxxxx sat.',  # too far apart
    ],
)
def test_name_spelling_stays_quiet_when_names_are_distinct(text):
    assert [r for r, _ in found(prose(text)) if r == 'name_spelling'] == []


def test_name_source_seam_flags_variants_of_known_names_whatever_the_counts():
    doc = prose('Quillan arrived. Quillan left. Quilan stayed.')
    registered = list(fiction_checks.NAME_SOURCES)  # the codex source is registered at import
    fiction_checks.NAME_SOURCES.clear()
    try:
        fiction_checks.register_name_source(lambda document: ['Quillan'])
        report = run_checks(doc, ['fiction'], profile=BROAD, limit=10**6)
        [finding] = [f for f in report['findings'] if f['rule'] == 'fiction.name_spelling']
        assert finding['text'] == 'Quilan' and 'known name' in finding['explanation']
        assert finding['measured']['canonical_occurrences'] is None
    finally:
        fiction_checks.NAME_SOURCES[:] = registered


def test_only_name_findings_propose_edits_and_protected_text_blocks_them():
    doc = fixture_doc('seeded.md')
    report = run_checks(doc, ['fiction'], profile='fixture', limit=10**6)
    for finding in report['findings']:
        assert bool(finding['operations']) == (finding['rule'] == 'fiction.name_spelling'), finding['rule']
        if not finding['operations']:
            assert finding['repair'] == {'kind': 'manual'}
    paragraph = prose(NAME_TEXT)
    paragraph['settings']['protections'] = [{'target': paragraph['blocks'][0]['id'], 'kind': 'wording'}]
    [name] = [
        f
        for f in run_checks(paragraph, ['fiction'], profile=BROAD, limit=10**6)['findings']
        if f['rule'] == 'fiction.name_spelling'
    ]
    assert name['operations'] == [] and name['repair']['kind'] == 'blocked'


def test_edit_distance_counts_adjacent_transpositions_once():
    assert fiction_checks._distance('aldric', 'aldirc', 2) == 1
    assert fiction_checks._distance('aldric', 'aldrick', 2) == 1
    assert fiction_checks._distance('aldric', 'bellmere', 1) == 2


# Recording, scopes and pacing data --------------------------------------------------------------------


def test_fiction_findings_record_and_survive_rechecks_with_statuses():
    from byax.checks import health, record_checks

    doc = fixture_doc('seeded.md')
    record_checks(doc, {'suites': ['fiction'], 'profile': 'fixture'})
    assert len(doc['review']['findings']) == 18
    assert health(doc)['validation'] == 'incomplete'
    first = doc['review']['findings'][0]
    assert first['dependencies']['unit'].startswith(('block:', 'fiction:', 'context:fiction'))


def test_scoped_run_limits_findings_to_the_selection():
    doc = fixture_doc('seeded.md')
    heading = next(b['id'] for b in doc['blocks'] if b.get('text') == 'The Tide Tables')
    report = run_checks(doc, ['fiction'], profile='fixture', scope=heading, limit=10**6)
    assert [f['rule'] for f in report['findings']] == ['fiction.adverb_density']


def test_pacing_series_is_exposed_per_chapter_as_data():
    doc = fixture_doc('seeded.md')
    chapters = fiction_checks.pacing_series(doc)
    assert [c['title'] for c in chapters] == [
        'The Lantern Road',
        'The Tide Tables',
        'The Glass Room',
        'The Quiet Count',
    ]
    last = chapters[-1]
    assert [p['words'] for p in last['series']] == [23, 23, 22, 23, 22, 23]
    assert last['series'][-1]['cumulative_words'] == sum(p['words'] for p in last['series'])
    assert last['summary']['paragraphs'] == 6 and 0 <= last['summary']['dialogue_ratio'] <= 1
    assert chapters[0]['series'][1]['dialogue_ratio'] == 0.5
    only = fiction_checks.pacing_series(doc, target=chapters[1]['id'])
    assert [c['title'] for c in only] == ['The Tide Tables']


# CLI -------------------------------------------------------------------------------------------------------


def saved(tmp_path, name='story.byax', source='seeded.md'):
    from byax.storage import save_document

    doc = fixture_doc(source)
    path = tmp_path / name
    save_document(doc, path)
    return str(path), doc


def test_check_family_fiction_runs_the_fiction_suite_with_a_profile(tmp_path):
    path, _ = saved(tmp_path)
    plain = execute('check', {'path': path, 'family': ['fiction']})
    assert plain['suites'] == ['fiction'] and plain['summary']['total'] > 0
    assert {f['rule'] for f in plain['findings']} <= set(fiction_checks.RULES)
    tuned = execute('check', {'path': path, 'suite': ['fiction'], 'profile': 'fixture', 'limit': 1000})
    assert tuned['summary']['total'] == 18


def test_check_family_rejects_unknown_families(tmp_path):
    path, _ = saved(tmp_path)
    with pytest.raises(ByaxError) as error:
        execute('check', {'path': path, 'family': ['nonsense']})
    assert error.value.code == 'invalid_input' and 'fiction' in error.value.details['supported']
    mixed = execute('check', {'path': path, 'family': ['fiction', 'spelling'], 'suite': ['grammar']})
    assert mixed['suites'] == ['fiction', 'grammar', 'spelling']
