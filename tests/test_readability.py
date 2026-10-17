"""Readability indices, style statistics, pacing data and the exported report document."""

import json
import math
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from byax import readability
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.exchange import import_document
from byax.mcp import handle
from byax.model import new_document, validate_document
from byax.prose import count_syllables, wordlist
from byax.service import make_server
from byax.storage import load_document, save_document

FIXTURES = Path(__file__).parent / 'fixtures' / 'fiction'
TOKEN = 'readability-test-token-with-32-chars'


def text_doc(*paragraphs, language='en'):
    doc = new_document('Report', '\n'.join(paragraphs))
    doc['metadata']['language'] = language
    return doc


def heading(identity, text, level=1):
    return {
        'id': identity,
        'type': 'heading',
        'text': text,
        'style': 'heading1',
        'marks': [],
        'attrs': {'level': level},
    }


def indices(report):
    return {k: v['value'] for k, v in report['document']['indices'].items()}


@pytest.mark.parametrize(
    'word, expected',
    [
        ('cat', 1),
        ('table', 2),
        ('jumped', 1),
        ('wanted', 2),
        ('because', 2),
        ('beautiful', 3),
        ('syllable', 3),
        ('strength', 1),
        ('quickly', 2),
        ('everything', 3),
        ('idea', 3),
        ('created', 3),
        ('readability', 5),
    ],
)
def test_syllable_heuristic_on_common_words(word, expected):
    exceptions = wordlist('readability', 'en')['syllable_exceptions']
    assert count_syllables(word, exceptions) == expected


def test_standard_indices_match_the_published_formulas_on_a_known_sample():
    # One six-word, six-syllable sentence of 17 letters.
    report = readability.readability_report(text_doc('The cat sat on the mat.'))
    values = indices(report)
    words, sentences, syllables, letters = 6, 1, 6, 17
    assert values['flesch_reading_ease'] == pytest.approx(
        206.835 - 1.015 * words / sentences - 84.6 * syllables / words, abs=0.01
    )
    assert values['flesch_kincaid_grade'] == pytest.approx(
        0.39 * words / sentences + 11.8 * syllables / words - 15.59, abs=0.01
    )
    assert values['ari'] == pytest.approx(4.71 * letters / words + 0.5 * words / sentences - 21.43, abs=0.01)
    assert values['coleman_liau'] == pytest.approx(
        0.0588 * (100 * letters / words) - 0.296 * (100 * sentences / words) - 15.8, abs=0.01
    )
    assert values['gunning_fog'] == pytest.approx(0.4 * (words / sentences + 0), abs=0.01)
    assert values['smog'] == pytest.approx(1.043 * math.sqrt(0 * 30 / sentences) + 3.1291, abs=0.01)


def test_complex_words_exclude_proper_nouns_and_suffix_only_syllables():
    report = readability.readability_report(
        text_doc('Considerably, Constantinople welcomed independence. He jumped, wanted, and walked.')
    )
    style = report['document']['style']
    # considerably (5) and independence (4) count; Constantinople is a proper noun; -ed forms are 1-2 syllables.
    assert style['complex_words'] == 2


def test_sample_size_caveats_are_reported_with_every_short_index():
    report = readability.readability_report(text_doc('The cat sat on the mat. It was warm.'))
    entries = report['document']['indices']
    assert not entries['smog']['reliable'] and '30 sentences' in entries['smog']['caveat']
    assert '100 words' in entries['flesch_reading_ease']['caveat']
    assert report['document']['caveats'] and not any(e['reliable'] for e in entries.values())
    long = ' '.join('The cat sat on the mat today.' for _ in range(40))
    solid = readability.readability_report(text_doc(long))['document']['indices']
    assert all(e['reliable'] and e['caveat'] is None for e in solid.values())


def test_style_statistics_and_the_passive_voice_heuristic():
    report = readability.readability_report(
        text_doc(
            'The window was broken by the storm. She was not seen again. The seed was small.',
            '"Run," he said quickly. They were running. The letters were written and sent.',
        )
    )
    style = report['document']['style']
    assert style['passive_voice_count'] == 3  # broken, seen, written ("sent" follows "and")
    assert (style['sentences'], style['words']) == (6, 29)
    assert style['adverb_count'] == 1 and style['adverbs_per_100_words'] > 0
    assert 0 < style['dialogue_ratio'] < 0.2
    assert style['longest_sentence_words'] >= style['average_sentence_words'] > 0
    assert style['reading_minutes'] == 1


def test_per_chapter_reports_use_top_level_headings_and_expose_pacing():
    doc, _ = import_document(str(FIXTURES / 'seeded.md'))
    report = readability.readability_report(doc)
    assert [c['title'] for c in report['chapters']] == [
        'The Lantern Road',
        'The Tide Tables',
        'The Glass Room',
        'The Quiet Count',
    ]
    last = report['chapters'][-1]
    assert last['style']['paragraphs'] == 6 == len(last['pacing'])
    assert sum(c['style']['words'] for c in report['chapters']) == report['document']['style']['words']
    only = readability.readability_report(doc, target=last['id'])
    assert [c['id'] for c in only['chapters']] == [last['id']] and only['scope'] == last['id']
    with pytest.raises(ByaxError) as error:
        readability.readability_report(doc, target='missing')
    assert error.value.code == 'missing_target'
    json.dumps(report)  # data only
    assert report == readability.readability_report(doc)


def test_text_before_the_first_heading_and_documents_without_headings_still_report():
    doc = text_doc('Opening words before any heading.')
    assert [c['id'] for c in readability.readability_report(doc)['chapters']] == ['_document']
    doc['blocks'].append(heading('h1', 'One'))
    doc['blocks'].append({**doc['blocks'][0], 'id': 'p2', 'text': 'Words under the heading.'})
    assert [c['id'] for c in readability.readability_report(doc)['chapters']] == ['_front', 'h1']


def test_intentional_passages_headings_and_tables_are_excluded():
    doc = text_doc('Counted words here.', 'Skipped quote text that is long.')
    doc['blocks'][1]['attrs']['intentional'] = True
    doc['blocks'].append(heading('h', 'Heading words that are ignored'))
    assert readability.readability_report(doc)['document']['style']['words'] == 3


def test_other_languages_report_counts_without_english_indices():
    report = readability.readability_report(text_doc('Le chat dort sur le tapis.', language='fr'))
    entry = report['document']['indices']['flesch_reading_ease']
    assert entry['value'] is None and 'No readability data' in entry['caveat']
    assert report['document']['style']['words'] == 6 and report['document']['caveats']


def test_report_document_is_a_valid_native_document_with_tables():
    doc, _ = import_document(str(FIXTURES / 'seeded.md'))
    report_doc = readability.readability_document(readability.readability_report(doc), 'Report')
    validate_document(report_doc)
    kinds = [b['type'] for b in report_doc['blocks']]
    assert kinds.count('heading') == 6 and kinds.count('table') == 10
    first = next(b for b in report_doc['blocks'] if b['type'] == 'table')
    assert [c['text'] for c in first['rows'][0]['cells']] == ['Index', 'Value', 'Note']
    assert len(first['rows']) == 7


# CLI, export and transport parity --------------------------------------------------------------


@pytest.fixture
def story(tmp_path):
    doc, _ = import_document(str(FIXTURES / 'seeded.md'))
    path = tmp_path / 'story.byax'
    save_document(doc, path)
    return str(path), doc


def test_readability_command_returns_the_report_without_touching_the_document(story):
    path, doc = story
    result = execute('readability', {'path': path})
    assert result['execution'] == 'completed' and result['revision'] == doc['revision']
    assert result['readability']['chapters'][0]['title'] == 'The Lantern Road'
    assert load_document(path)['revision'] == doc['revision']
    scoped = execute('readability', {'path': path, 'target': result['readability']['chapters'][1]['id']})
    assert [c['title'] for c in scoped['readability']['chapters']] == ['The Tide Tables']


@pytest.mark.parametrize('suffix', ['byax', 'md', 'docx', 'html', 'txt'])
def test_report_exports_through_existing_exporters_with_located_losses(story, tmp_path, suffix):
    path, _ = story
    out = tmp_path / f'report.{suffix}'
    result = execute('readability', {'path': path, 'out': str(out)})
    assert out.is_file() and result['outputs'] == [str(out.resolve())]
    assert isinstance(result['losses'], list) and result['fidelity'] in ('supported_subset', 'partial')
    for loss in result['losses']:
        assert {'code', 'message'} <= set(loss)
    if suffix == 'byax':
        exported = load_document(out)
        assert exported['metadata']['title'].startswith('Readability report: ')
        assert sum(b['type'] == 'table' for b in exported['blocks']) == 10
    if suffix == 'md':
        assert '| Index | Value | Note |' in out.read_text(encoding='utf-8')
    with pytest.raises(ByaxError) as error:
        execute('readability', {'path': path, 'out': str(out)})
    assert error.value.code == 'conflict'
    execute('readability', {'path': path, 'out': str(out), 'overwrite': True})


def test_report_export_refuses_the_source_path_and_unknown_formats(story, tmp_path):
    path, _ = story
    with pytest.raises(ByaxError):
        execute('readability', {'path': path, 'out': path, 'overwrite': True})
    with pytest.raises(ByaxError) as error:
        execute('readability', {'path': path, 'out': str(tmp_path / 'report.exe')})
    assert error.value.code == 'unsupported_format'


def rest_call(port, command, arguments):
    request = Request(
        f'http://127.0.0.1:{port}/v1/execute',
        data=json.dumps({'command': command, 'arguments': arguments}).encode(),
        method='POST',
        headers={'Authorization': 'Bearer ' + TOKEN},
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def test_library_cli_mcp_and_rest_return_the_same_readability_and_fiction_results(story, tmp_path, capsys):
    path, _ = story
    server = make_server([tmp_path], token=TOKEN, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for command, arguments, flags in (
            ('readability', {'path': path}, []),
            (
                'check',
                {'path': path, 'family': ['fiction'], 'limit': 1000},
                ['--family', 'fiction', '--limit', '1000'],
            ),
        ):
            library = execute(command, arguments)
            via_mcp = handle(
                {
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': 'tools/call',
                    'params': {'name': 'byax_' + command, 'arguments': arguments},
                }
            )['result']['structuredContent']
            via_rest = rest_call(server.server_port, command, arguments)
            assert main([command, path, *flags]) == 0
            via_cli = json.loads(capsys.readouterr().out)
            for other in (via_mcp, via_rest, via_cli):
                other.pop('exit_code', None)
            assert library == via_mcp == via_rest == via_cli
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
