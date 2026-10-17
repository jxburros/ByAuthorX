"""Quote normalization, alignment and anchors (context engine plan 5.3, 6.5)."""

import pytest

from byax import quote_match
from byax.quote_match import describe_differences, infer_kind, locate, normalize, reanchor, selectors

SOFT = chr(0xAD)
NBSP = chr(0xA0)
ZWSP = chr(0x200B)

# (name, original, normalized)
NORMALIZATION = [
    ('plain text is unchanged', 'a plain sentence.', 'a plain sentence.'),
    ('NFC composes combining marks', 'café', 'café'),
    ('whitespace collapses', 'a  b\t\tc\n\nd', 'a b c d'),
    ('no-break space is whitespace', f'a{NBSP}{NBSP}b', 'a b'),
    ('curly double quotes', '“quoted”', '"quoted"'),
    ('curly single quotes and apostrophes', '‘it’s’', "'it's'"),
    ('low and guillemet quotes', '„text‟ «text»', '"text" "text"'),
    ('soft hyphen is removed', f'inter{SOFT}national', 'international'),
    ('zero-width space is removed', f'a{ZWSP}b', 'ab'),
    ('line-break hyphenation joins', 'an exam-\nple of it', 'an example of it'),
    ('hyphenation with trailing spaces and CRLF', 'an exam- \r\nple', 'an example'),
    ('hyphen before a capital is kept', 'pre-\nWar years', 'pre- War years'),
    ('hyphen before a digit is kept', '1990-\n2000', '1990- 2000'),
    ('hyphen inside a word is kept', 'well-known', 'well-known'),
    ('ligatures expand', 'the ﬁrst oﬃce ﬂoor', 'the first office floor'),
    ('dashes fold', 'a–b—c−d', 'a-b-c-d'),
    ('three dots become an ellipsis', 'wait... then', 'wait… then'),
    ('spaced dots become an ellipsis', 'wait . . . then', 'wait … then'),
    ('bracketed dots', 'one [...] two', 'one … two'),
    ('bracketed ellipsis character', 'one […] two', 'one … two'),
    ('bracketed with spaces', 'one [ ... ] two', 'one … two'),
    ('four dots keep the full stop', 'end....', 'end….'),
    ('em dash at a line end is not a hyphen', 'word—\nnext', 'word- next'),
]


@pytest.mark.parametrize('name,original,expected', NORMALIZATION, ids=[row[0] for row in NORMALIZATION])
def test_normalization_table(name, original, expected):
    normalized, offsets = normalize(original)
    assert normalized == expected
    assert len(offsets) == len(normalized)
    for start, end in offsets:
        assert 0 <= start < end <= len(original)


def test_offset_map_points_back_into_the_original():
    original = 'It’s an exam-\nple  of\n\nit.'
    normalized, offsets = normalize(original)
    assert normalized == "It's an example of it."
    # The apostrophe maps to the curly one, the joined word to both of its halves, spaces to runs.
    assert original[slice(*offsets[2])] == '’'
    at = normalized.index('example')
    assert original[offsets[at][0] : offsets[at + 6][1]] == 'exam-\nple'
    space = normalized.index(' of') + 3
    assert original[slice(*offsets[space])] == '\n\n' or original[slice(*offsets[space])] == '  '
    assert quote_match.map_span(offsets, at, at + 7) == (original.index('exam'), original.index('ple') + 3)


def test_normalize_can_keep_the_hyphen_for_compound_words():
    assert normalize('well-\nknown', join_hyphenation=False)[0] == 'well-known'
    assert normalize('well-\nknown')[0] == 'wellknown'


# Text as pdf extractors really deliver it: line breaks mid-word, ligatures, soft hyphens, curly quotes.
PDF = (
    'The committee’s report found that “the ﬁnancial ben-\n'
    f'e{SOFT}fits of the pro-\ngramme were negligible” and that the\n'
    'review [...] should continue.  Further, the oﬃcer wrote that costs\n'
    'rose by 4.5 per cent—more than forecast.'
)


def test_locate_exact_reports_a_raw_pass():
    result = locate('Further, the', PDF)
    assert result['match'] == 'exact' and result['raw_pass'] and result['normalized_pass']
    assert PDF[slice(*result['span'])] == 'Further, the'
    assert result['score'] == 100.0


@pytest.mark.parametrize(
    'quote,expected_text',
    [
        ('"the financial benefits of the programme were negligible"', 'the ﬁnancial ben-\ne'),
        ('the financial benefits of the programme were negligible', 'the ﬁnancial ben-\ne'),
        ("committee's report found that", 'committee’s report found that'),
        ('the officer wrote that costs rose by 4.5 per cent-more than forecast', 'oﬃcer'),
        ('the review ... should continue', 'review [...] should continue'),
    ],
)
def test_locate_normalized_maps_the_span_back_to_the_original(quote, expected_text):
    result = locate(quote, PDF)
    assert result['match'] == 'normalized', result
    assert not result['raw_pass'] and result['normalized_pass']
    start, end = result['span']
    assert expected_text in PDF[start:end]
    assert 0 <= start < end <= len(PDF)


def test_locate_elision_requires_segments_in_order():
    assert locate('should continue ... the review', PDF)['match'] != 'normalized'
    found = locate('The committee ... should continue', PDF)
    assert found['match'] == 'normalized' and found.get('elided')
    assert PDF[found['span'][0] :].startswith('The committee')


def test_locate_near_reports_score_span_and_differences():
    # One misspelt word: close, not identical.
    quote = 'the financial benefits of the programme were negligable'
    result = locate(quote, PDF)
    assert result['match'] == 'near'
    assert not result['normalized_pass'] and not result['raw_pass']
    assert 90 <= result['score'] < 100
    assert result['differences'] == [
        {'op': 'replace', 'quote': 'a', 'source': 'i', 'quote_offset': 51, 'source_offset': 51}
    ]
    assert describe_differences(result['differences']) == "quote 'a' vs source 'i'"
    start, end = result['span']
    assert PDF[start:end].replace(SOFT, '').endswith('negligible')


def test_locate_threshold_is_adjustable_and_none_below_it():
    quote = 'the financial benefits of the programme were negligable'
    assert locate(quote, PDF, threshold=99.9)['match'] == 'none'
    assert locate(quote, PDF, threshold=70)['match'] == 'near'
    heavy = 'the financial benefits of the programme were considerable'
    assert locate(heavy, PDF)['match'] == 'none'
    assert locate(heavy, PDF, threshold=70)['match'] == 'near'
    assert locate('an entirely unrelated sentence about sailing boats', PDF)['match'] == 'none'
    assert quote_match.NEAR_THRESHOLD == 94.0


def test_short_quotes_are_never_matched_fuzzily():
    assert locate('benefits', 'The benefits were large.')['match'] == 'exact'
    assert locate('benefitz', 'The benefits were large.')['match'] == 'none'


def test_locate_handles_empty_and_non_text():
    assert locate('', PDF)['match'] == 'none'
    assert locate('   ', PDF)['match'] == 'none'
    assert locate(None, PDF)['match'] == 'none'
    assert locate('something', '')['match'] == 'none'


def test_hyphenated_compound_split_across_lines_matches_with_the_hyphen_kept():
    source = 'A well-\nknown author wrote it.'
    assert locate('A well-known author', source)['match'] == 'normalized'
    assert locate('A wellknown author', source)['match'] == 'normalized'


def test_selectors_have_position_quote_and_32_char_context():
    text = 'x' * 50 + 'the quoted words' + 'y' * 50
    anchor = selectors(text, (50, 66))
    assert anchor['position'] == {'start': 50, 'end': 66}
    assert anchor['quote'] == 'the quoted words'
    assert anchor['prefix'] == 'x' * 32 and anchor['suffix'] == 'y' * 32
    near_start = selectors('abcdef', (2, 4))
    assert near_start['prefix'] == 'ab' and near_start['suffix'] == 'ef'
    with pytest.raises(ValueError):
        selectors('abc', (2, 2))


QUOTE = 'the committee rejected the second proposal outright'
BASE = f'Intro paragraph. Then {QUOTE} after a long debate. Closing paragraph.'


def test_reanchor_follows_the_plan_order():
    start = BASE.index(QUOTE)
    anchor = selectors(BASE, (start, start + len(QUOTE)))
    # 1. Unchanged text: the recorded position.
    assert reanchor(anchor, BASE).method == 'position'
    # 2. Text inserted before it: found near the old position.
    shifted = 'New opening sentence added here. ' + BASE
    found = reanchor(anchor, shifted)
    assert found.method == 'nearby'
    assert shifted[found.start : found.end] == QUOTE
    # 3. Moved far away: found anywhere.
    far = 'z' * 5000 + BASE
    found = reanchor(anchor, far)
    assert found.method == 'anywhere'
    assert far[found.span[0] : found.span[1]] == QUOTE
    # 4. Lightly edited: the fuzzy match is still found; heavily rewritten: None.
    edited = BASE.replace('rejected', 'rejects')
    assert reanchor(anchor, edited).method in {'nearby', 'anywhere'}
    assert reanchor(anchor, 'Nothing here resembles it at all.') is None


def test_reanchor_uses_prefix_and_suffix_to_pick_between_repeats():
    text = 'First chapter: it was a dark night. Second chapter: it was a dark night. Third.'
    second = text.rindex('it was a dark night')
    anchor = selectors(text, (second, second + len('it was a dark night')))
    moved = 'Preface added. ' + text
    found = reanchor(anchor, moved)
    assert moved[found.start - 20 : found.start].endswith('Second chapter: ')


def test_infer_kind():
    assert infer_kind('“any quoted words”', [])['kind'] == 'quotation'
    assert infer_kind('"straight quoted"', [])['kind'] == 'quotation'
    source = 'Mars has two moons: Phobos and Deimos, both small.'
    assert infer_kind('Mars has two moons', [source])['kind'] == 'quotation'
    assert infer_kind('Mars has two\nmoons: Phobos', [source])['kind'] == 'quotation'
    assert infer_kind('The red planet has a pair of satellites', [source])['kind'] == 'paraphrase'
    # A very short exact hit is not evidence of quotation; neither is a near match.
    assert infer_kind('moons', [source])['kind'] == 'paraphrase'
    assert infer_kind('Mars has two moonz: Phobos and Deimoz', [source])['kind'] == 'paraphrase'
    assert 'reason' in infer_kind('anything goes here', [])
