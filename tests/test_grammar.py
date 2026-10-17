"""The grammar tiers: built-in rules, the local LanguageTool adapter, its setup and the findings pipeline."""

import hashlib
import http.server
import importlib.util
import json
import os
import socket
import sys
import threading
import zipfile
from functools import partial
from pathlib import Path

import httpx
import pytest

from byax import engine, grammar
from byax.checks import run_checks
from byax.cli import execute
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document
from byax.transport_policy import check_provider_admin

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'grammar'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark = _load('grammar_benchmark', ROOT / 'scripts' / 'grammar_benchmark.py')
fake = _load('fake_languagetool', FIXTURES / 'fake_languagetool.py')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
def lt_server():
    server = fake.make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()


def enable_languagetool(port, **values):
    config = grammar.load_config()
    config['languagetool'].update(enabled=True, port=port, **values)
    grammar.save_config(config)


def findings(text, suites=('grammar',), language=None, **options):
    doc = new_document(text=text)
    if language:
        doc['metadata']['language'] = language
    return doc, run_checks(doc, list(suites), limit=10**6, **options)


# Accuracy ------------------------------------------------------------------------------------------
def test_published_numbers_match_a_fresh_measurement():
    published = json.loads(
        (ROOT / 'docs' / 'benchmarks' / '0.3-grammar-builtin.json').read_text(encoding='utf-8')
    )
    assert benchmark.measure_builtin() == published, 'run: uv run python scripts/grammar_benchmark.py --write'


def test_fixture_accuracy_meets_the_stored_thresholds_per_rule_group():
    thresholds = json.loads((FIXTURES / 'thresholds.json').read_text(encoding='utf-8'))
    result = benchmark.measure_builtin()
    assert result['corpus']['sentences'] >= 200 and result['corpus']['labelled_errors'] >= 120
    assert set(result['groups']) == set(thresholds['groups'])
    for group, minimum in thresholds['groups'].items():
        measured = result['groups'][group]
        assert measured['precision'] >= minimum['precision'], (group, measured)
        assert measured['recall'] >= minimum['recall'], (group, measured)
    assert result['overall']['precision'] >= thresholds['overall']['precision']
    assert result['overall']['recall'] >= thresholds['overall']['recall']
    assert result['clean_sentence_findings'] <= thresholds['clean_sentence_findings_max']


def test_every_rule_has_a_labelled_example_and_the_rule_file_compiles_cleanly():
    result = benchmark.measure_builtin()
    assert result['rules_without_a_labelled_example'] == []
    assert grammar.rule_set('en')['problems'] == []
    assert set(grammar.RULES) == {r.id for r in grammar.rule_set('en')['rules']}


def test_the_open_fixture_shows_the_built_in_tier_is_narrow():
    open_coverage = benchmark.measure_builtin()['open_coverage']
    assert open_coverage['recall'] < 0.25 and open_coverage['matches_on_clean_sentences'] == 0


# Built-in engine ----------------------------------------------------------------------------------
def matches(text, level='broad'):
    return grammar.BuiltinEngine(grammar.LEVELS[level]).check(text, 'en')


def test_replacements_keep_the_case_of_the_original():
    [match] = matches('Its a nice day.')
    assert (match['rule'], match['text'], match['replacements']) == (
        'grammar.its_for_its_contraction',
        'Its',
        ["It's"],
    )
    [match] = matches('You should of known.')
    assert match['replacements'] == ['have']
    [match] = matches('That is ALOT of work.')
    assert match['replacements'] == ['A LOT']
    [match] = matches('I think between you and I it works.')
    assert match['replacements'] == ['me']


def test_dialogue_is_not_treated_as_a_grammar_error_for_dialect_rules_but_confusions_still_apply():
    assert matches('"He don\'t care," she said.') == []
    assert matches("He don't care.")[0]['rule'] == 'grammar.third_person_dont'
    assert matches('"Its a trap," he said.')[0]['rule'] == 'grammar.its_for_its_contraction'


def test_levels_filter_the_broad_rules():
    text = 'He was tall, she was short.'
    assert [m['rule'] for m in matches(text, 'broad')] == ['grammar.comma_splice_clauses']
    assert matches(text, 'balanced') == []


def test_a_precise_rule_leaves_correct_neighbours_alone():
    for clean in (
        'Would he have done it?',
        'If he were here, she would leave.',
        'It was a matter of course.',
        'Your welcome speech was warm.',
        'It was better then, and worse than now.',
        'The dog wagged its tail.',
    ):
        assert matches(clean) == [], clean


def test_rule_files_are_validated_for_structure_and_runaway_patterns():
    base = {'rules': [{'id': 'ok_rule', 'group': 'g', 'message': 'm', 'pattern': r'\bx\b'}]}
    grammar.validate_rule_set(base)
    for bad in (
        {'rules': [{'id': 'Bad', 'group': 'g', 'message': 'm', 'pattern': 'x'}]},
        {'rules': [{'id': 'broken', 'group': 'g', 'message': 'm', 'pattern': '('}]},
        {'rules': [{'id': 'slow', 'group': 'g', 'message': 'm', 'pattern': r'(a+)+b'}]},
        {'rules': [{'id': 'dup', 'group': 'g', 'message': 'm', 'pattern': 'x'}] * 2},
        {'rules': [{'id': 'hl', 'group': 'g', 'message': 'm', 'pattern': 'x', 'highlight': 3}]},
        {'rules': [{'id': 'lv', 'group': 'g', 'message': 'm', 'pattern': 'x', 'level': 'wild'}]},
        {'rules': []},
    ):
        with pytest.raises(ByaxError) as caught:
            grammar.validate_rule_set(bad)
        assert caught.value.code == 'invalid_input'


# Findings pipeline ---------------------------------------------------------------------------------
def test_built_in_findings_carry_rule_family_severity_exact_range_and_a_repair():
    doc, report = findings('Intro line.\nI should of gone, its a pity.')
    by_rule = {f['rule']: f for f in report['findings']}
    modal = by_rule['grammar.modal_of']
    block = doc['blocks'][1]
    assert modal['family'] == 'grammar' and modal['suite'] == 'grammar' and modal['severity'] == 'advisory'
    assert modal['affected_ids'] == [block['id']]
    assert block['text'][modal['range']['start'] : modal['range']['end']] == 'of' == modal['text']
    assert modal['measured']['engine'] == 'builtin' and modal['measured']['group'] == 'confusion'
    assert modal['repair'] == {'kind': 'automatic', 'separate_operation': True}
    candidate, _ = engine.apply(doc, {'operations': modal['operations']})
    assert candidate['blocks'][1]['text'] == 'I should have gone, its a pity.'
    assert 'grammar.its_for_its_contraction' in by_rule  # "its a pity" is a second, separate finding
    assert all(f['id'].startswith('finding_') for f in report['findings'])


def test_the_language_comes_from_the_paragraph_or_the_document():
    doc, report = findings('Its a nice day.', language='de')
    assert [f for f in report['findings'] if f['rule'].startswith('grammar.its')] == []
    doc['blocks'][0]['attrs']['language'] = 'en'
    report = run_checks(doc, ['grammar'], limit=100)
    assert any(f['rule'] == 'grammar.its_for_its_contraction' for f in report['findings'])


def test_intentional_passages_are_not_checked():
    doc = new_document(text='I should of known.')
    doc['blocks'][0]['attrs']['quotation'] = True
    assert run_checks(doc, ['grammar'])['findings'] == []


def test_the_built_in_tier_can_be_switched_off_and_rules_disabled():
    text = 'I should of known and its a shame.'
    config = grammar.load_config()
    config['builtin']['disabled_rules'] = ['modal_of']
    grammar.save_config(config)
    assert [f['rule'] for f in findings(text)[1]['findings']] == ['grammar.its_for_its_contraction']
    config['builtin']['enabled'] = False
    grammar.save_config(config)
    assert findings(text)[1]['findings'] == []


def test_grammar_checks_stay_incremental_per_paragraph():
    doc = new_document(text='One clean line.\nI should of gone.\nAnother clean line.')
    first = run_checks(doc, ['grammar'], limit=1000, track=True)
    edited, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'set_text', 'target': doc['blocks'][1]['id'], 'text': 'I should have gone.'}
            ]
        },
    )
    second = run_checks(edited, ['grammar'], limit=1000, previous=first, track=True)
    assert second['evaluation']['rechecked'] == ['block:' + doc['blocks'][1]['id']]
    assert set(second['evaluation']['reused']) == {
        'block:' + doc['blocks'][0]['id'],
        'block:' + doc['blocks'][2]['id'],
    }
    assert not [f for f in second['findings'] if f['rule'] == 'grammar.modal_of']


def test_the_report_says_plainly_that_languagetool_is_not_installed():
    _, report = findings('A clean line.')
    [entry] = [c for c in report['coverage'] if c.get('rule') == 'grammar.languagetool']
    assert entry['status'] == 'unmeasurable' and 'not installed' in entry['reason']
    assert entry['state'] == 'not installed'
    assert report['validation'] == 'incomplete'


def test_rules_that_fail_to_load_are_reported_as_incomplete_coverage(monkeypatch):
    real = grammar.rule_set('en')
    monkeypatch.setattr(
        grammar,
        'rule_set',
        lambda language: {**real, 'problems': ['x: broken']} if language == 'en' else None,
    )
    _, report = findings('A line.')
    [entry] = [c for c in report['coverage'] if c.get('rule') == 'grammar.builtin']
    assert entry['status'] == 'incomplete' and '1 built-in en grammar rules' in entry['reason']


# LanguageTool adapter ---------------------------------------------------------------------------
def test_utf16_offsets_map_to_scalar_indices_around_astral_characters():
    text = 'a😀b😀😀very unique'
    boundaries = grammar.utf16_index(text)
    assert boundaries[-1] == len(text) + 3
    offset = text.encode('utf-16-le').find('very'.encode('utf-16-le')) // 2
    assert grammar.to_scalar_range(boundaries, offset, 11) == (text.index('very'), len(text))
    # an offset that lands inside a surrogate pair snaps outward instead of splitting the character
    assert grammar.to_scalar_range(boundaries, 2, 1) == (1, 2)
    assert grammar.to_scalar_range(grammar.utf16_index('plain'), 1, 3) == (1, 4)


def test_languagetool_findings_use_exact_scalar_ranges_and_become_repair_proposals(lt_server):
    enable_languagetool(lt_server)
    text = 'This 😀 is a very unique idea 😀 with  gaps. Their is a teh problem.'
    doc, report = findings(text)
    by_rule = {f['rule']: f for f in report['findings']}
    unique = by_rule['grammar.languagetool.VERY_UNIQUE']
    assert doc['blocks'][0]['text'][unique['range']['start'] : unique['range']['end']] == 'very unique'
    assert unique['family'] == 'grammar' and unique['measured']['engine'] == 'languagetool'
    assert unique['measured']['category'] == 'STYLE'
    candidate, _ = engine.apply(doc, {'operations': unique['operations']})
    assert 'a unique idea' in candidate['blocks'][0]['text']
    # spelling and duplicates of the deterministic checks are not reported twice; builtin wins an overlap
    assert 'grammar.languagetool.MORFOLOGIK_RULE_EN_US' not in by_rule
    assert 'grammar.languagetool.WHITESPACE_RULE' not in by_rule
    assert 'grammar.languagetool.THEIR_IS' not in by_rule and 'grammar.their_for_there' in by_rule
    [entry] = [c for c in report['coverage'] if c.get('rule') == 'grammar.languagetool']
    assert entry['status'] == 'completed' and entry['state'] == 'running'


def test_languagetool_alternatives_are_kept_and_spelling_is_opt_in(lt_server):
    enable_languagetool(lt_server, include_spelling=True)
    _, report = findings('The cat are here and teh dog too.')
    by_rule = {f['rule']: f for f in report['findings']}
    assert by_rule['grammar.languagetool.SUBJECT_VERB']['measured']['alternatives'] == ['cats are']
    assert 'grammar.languagetool.MORFOLOGIK_RULE_EN_US' in by_rule


def test_the_regional_tag_of_the_document_is_passed_and_unsupported_languages_are_reported(lt_server):
    enable_languagetool(lt_server)
    _, report = findings('This is very unique.', language='en-GB')
    assert any(f['rule'] == 'grammar.languagetool.VERY_UNIQUE' for f in report['findings'])
    _, report = findings('This is very unique.', language='fr')
    assert not any(f['rule'].startswith('grammar.languagetool') for f in report['findings'])
    entry = next(c for c in report['coverage'] if c.get('rule') == 'grammar.languagetool' and 'targets' in c)
    assert entry['status'] == 'unmeasurable' and 'fr' in entry['reason']


def test_an_enabled_but_unreachable_server_is_reported_honestly_and_built_in_rules_still_run():
    enable_languagetool(free_port())
    _, report = findings('I should of known.')
    assert any(f['rule'] == 'grammar.modal_of' for f in report['findings'])
    entry = next(c for c in report['coverage'] if c.get('rule') == 'grammar.languagetool')
    assert entry['status'] == 'incomplete' and 'no local server answered' in entry['reason']


def test_the_tier_state_is_part_of_the_dependency_fingerprint(lt_server):
    doc = new_document(text='This is very unique.')
    before = run_checks(doc, ['grammar'], limit=100, track=True)
    assert not any(f['rule'].startswith('grammar.languagetool') for f in before['findings'])
    enable_languagetool(lt_server)
    after = run_checks(doc, ['grammar'], limit=100, previous=before, track=True)
    assert any(f['rule'].startswith('grammar.languagetool') for f in after['findings'])
    assert after['evaluation']['rechecked'] and not after['evaluation']['reused']


def test_text_only_goes_to_a_loopback_address():
    with pytest.raises(ByaxError) as caught:
        grammar.LanguageToolEngine('http://example.com:8081')
    assert caught.value.code == 'invalid_input'
    assert grammar._healthy('http://example.com:8081') is False


# Setup: status, configure, start and stop --------------------------------------------------------------
def make_languagetool_folder(tmp_path):
    folder = tmp_path / 'LanguageTool-6.6'
    folder.mkdir()
    (folder / 'languagetool-server.jar').write_bytes(b'jar')
    return folder


def fake_java(tmp_path):
    script = tmp_path / 'fakejava'
    script.write_text(
        f'#!{sys.executable}\nimport runpy, sys\nsys.argv = ["fake"] + sys.argv[1:]\n'
        f'runpy.run_path({str(FIXTURES / "fake_languagetool.py")!r}, run_name="__main__")\n',
        encoding='utf-8',
    )
    script.chmod(0o755)
    return script


def test_status_moves_from_not_installed_to_stopped_to_running(tmp_path, lt_server):
    assert execute('grammar', {})['grammar']['languagetool']['label'] == 'LanguageTool: not installed'
    folder = make_languagetool_folder(tmp_path)
    port = free_port()
    result = execute('grammar', {'action': 'configure', 'languagetool_dir': str(tmp_path), 'port': port})
    status = result['languagetool']
    assert status['state'] == 'stopped' and status['directory'] == str(folder) and status['version'] == '6.6'
    assert status['enabled'] is False
    running = execute('grammar', {'action': 'configure', 'port': lt_server})['languagetool']
    assert running['state'] == 'running' and running['label'] == 'LanguageTool: running'
    assert execute('grammar', {'action': 'enable'})['languagetool']['enabled'] is True
    assert execute('grammar', {'action': 'disable'})['languagetool']['enabled'] is False


def test_configure_validates_the_folder_and_the_port(tmp_path):
    for arguments in ({'languagetool_dir': str(tmp_path)}, {'port': 80}):
        with pytest.raises(ByaxError) as caught:
            execute('grammar', {'action': 'configure', **arguments})
        assert caught.value.code == 'invalid_input'


@pytest.mark.skipif(os.name == 'nt', reason='uses a POSIX launcher script')
def test_start_and_stop_a_server_from_a_user_installed_folder(tmp_path):
    folder = make_languagetool_folder(tmp_path)
    port = free_port()
    execute(
        'grammar',
        {
            'action': 'configure',
            'languagetool_dir': str(folder),
            'port': port,
            'java': str(fake_java(tmp_path)),
        },
    )
    started = execute('grammar', {'action': 'start'})['languagetool']
    try:
        assert started['started'] is True and started['state'] == 'running' and started['managed_by_byax']
        again = execute('grammar', {'action': 'start'})['languagetool']
        assert again['started'] is False
        checked = execute(
            'grammar', {'action': 'check', 'text': 'This is very unique.', 'engine': 'languagetool'}
        )
        assert [m['text'] for m in checked['matches']] == ['very unique']
    finally:
        stopped = execute('grammar', {'action': 'stop'})['languagetool']
    assert stopped['stopped'] is True and stopped['state'] == 'stopped'
    assert execute('grammar', {'action': 'stop'})['languagetool']['stopped'] is False


def test_start_refuses_without_a_folder_or_java(tmp_path):
    with pytest.raises(ByaxError) as caught:
        execute('grammar', {'action': 'start'})
    assert caught.value.code == 'missing_dependency' and 'not installed' in caught.value.message
    folder = make_languagetool_folder(tmp_path)
    execute(
        'grammar', {'action': 'configure', 'languagetool_dir': str(folder), 'java': str(tmp_path / 'nojava')}
    )
    with pytest.raises(ByaxError) as caught:
        execute('grammar', {'action': 'start'})
    assert 'Java was not found' in caught.value.message


# Installing LanguageTool ------------------------------------------------------------------------------
def serve_directory(directory):
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def make_archive(directory, members):
    path = directory / 'lt.zip'
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_install_shows_the_plan_and_downloads_nothing_without_confirmation():
    plan = grammar.installation_plan()
    assert plan['url'].startswith('https://languagetool.org/download/LanguageTool-')
    assert len(plan['sha256']) == 64 and plan['size'] > 100_000_000 and plan['licence'] == 'LGPL-2.1-or-later'
    result = execute('grammar', {'action': 'install-languagetool'})
    assert result['downloaded'] is False and result['confirmation_required'] is True
    assert result['plan'] == plan
    wrong = execute('grammar', {'action': 'install-languagetool', 'sha256': '0' * 64})
    assert wrong['downloaded'] is False
    assert not grammar.data_dir().exists()


def test_install_verifies_size_and_sha256_before_unpacking(tmp_path):
    served = tmp_path / 'served'
    served.mkdir()
    archive = make_archive(
        served, {'LanguageTool-6.6/languagetool-server.jar': b'jar', 'LanguageTool-6.6/x.txt': b'x'}
    )
    data = archive.read_bytes()
    server = serve_directory(served)
    try:
        plan = {
            'version': '6.6',
            'url': f'http://127.0.0.1:{server.server_port}/lt.zip',
            'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'licence': 'LGPL-2.1-or-later',
            'archive_root': 'LanguageTool-6.6',
            'requires': 'Java 17 or later',
        }
        client = httpx.Client(trust_env=False)
        refused = grammar.install_languagetool(plan=plan, client=client)
        assert refused['downloaded'] is False
        tampered = {**plan, 'sha256': '1' * 64}
        with pytest.raises(ByaxError) as caught:
            grammar.install_languagetool(confirm_sha256=tampered['sha256'], plan=tampered, client=client)
        assert caught.value.code == 'checksum_mismatch'
        assert not (grammar.data_dir() / '6.6').exists()
        result = grammar.install_languagetool(confirm_sha256=plan['sha256'], plan=plan, client=client)
    finally:
        server.shutdown()
        server.server_close()
    assert result['downloaded'] is True
    assert (Path(result['directory']) / 'languagetool-server.jar').is_file()
    status = grammar.languagetool_status()
    assert status['installed'] and status['state'] == 'stopped' and status['enabled'] is True
    assert not [p for p in grammar.data_dir().iterdir() if p.name.startswith('.install-')]


def test_install_refuses_unsafe_archive_members(tmp_path):
    served = tmp_path / 'served'
    served.mkdir()
    archive = make_archive(served, {'LanguageTool-6.6/languagetool-server.jar': b'jar', '../evil.txt': b'no'})
    data = archive.read_bytes()
    server = serve_directory(served)
    try:
        plan = {
            'version': '6.6',
            'url': f'http://127.0.0.1:{server.server_port}/lt.zip',
            'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'licence': 'LGPL-2.1-or-later',
            'archive_root': 'LanguageTool-6.6',
            'requires': 'Java 17 or later',
        }
        with pytest.raises(ByaxError) as caught:
            grammar.install_languagetool(
                confirm_sha256=plan['sha256'], plan=plan, client=httpx.Client(trust_env=False)
            )
    finally:
        server.shutdown()
        server.server_close()
    assert caught.value.code == 'invalid_input' and 'unsafe' in caught.value.message
    assert not (grammar.data_dir() / '6.6').exists() and not (tmp_path / 'evil.txt').exists()


def test_the_pinned_download_is_the_official_https_location():
    assert grammar.LANGUAGETOOL['url'] == 'https://languagetool.org/download/LanguageTool-6.6.zip'
    assert grammar.LANGUAGETOOL['archive_root'] == 'LanguageTool-6.6'


# Interfaces ---------------------------------------------------------------------------------------------
def test_cli_status_rules_and_check_actions():
    status = execute('grammar', {})['grammar']
    assert status['builtin']['label'] == 'Built-in rules' and status['builtin']['rules'] >= 80
    assert (
        status['languagetool']['state'] == 'not installed' and status['languagetool_plan']['version'] == '6.6'
    )
    rules = execute('grammar', {'action': 'rules', 'group': 'idiom'})['rule_sets']
    assert rules[0]['language'] == 'en' and {r['group'] for r in rules[0]['rules']} == {'idiom'}
    checked = execute('grammar', {'action': 'check', 'text': 'We could of gone, alot.'})
    assert [m['rule'] for m in checked['matches']] == ['grammar.modal_of', 'grammar.run_together_words']
    assert checked['matches'][0]['replacements'] == ['have'] and checked['count'] == 2
    with pytest.raises(ByaxError):
        execute('grammar', {'action': 'check'})
    with pytest.raises(ByaxError):
        execute('grammar', {'action': 'check', 'text': 'x', 'engine': 'languagetool'})


def test_administrative_actions_are_refused_over_mcp_and_rest_but_reading_is_not():
    for action in ('configure', 'enable', 'disable', 'install-languagetool', 'start', 'stop'):
        with pytest.raises(ByaxError) as caught:
            check_provider_admin('grammar', {'action': action})
        assert caught.value.code == 'unsupported_feature'
        response = handle(
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'byax_grammar', 'arguments': {'action': action}},
            }
        )['result']
        assert response['isError'] and response['structuredContent']['error']['code'] == 'unsupported_feature'
    for action in ('status', 'rules', 'check'):
        check_provider_admin('grammar', {'action': action})
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {'name': 'byax_grammar', 'arguments': {'action': 'check', 'text': 'We could of gone.'}},
        }
    )['result']
    assert not response['isError'] and response['structuredContent']['count'] == 1


# A real server, only when one is configured ----------------------------------------------------------------
@pytest.mark.languagetool
@pytest.mark.skipif(
    not os.environ.get('BYAX_LANGUAGETOOL_URL'), reason='set BYAX_LANGUAGETOOL_URL to a local server'
)
def test_a_real_languagetool_server_returns_scalar_ranges_for_astral_text():
    url = os.environ['BYAX_LANGUAGETOOL_URL']
    tier = grammar.LanguageToolEngine(url)
    text = "Here is a 😀 emoji. He don't like it. I should of gone."
    found = tier.check(text, 'en-US')
    assert found, 'LanguageTool reported nothing'
    for item in found:
        assert text[item['start'] : item['end']] == item['text']
    assert any(item['text'] == 'of' or 'of' in item['text'] for item in found)
    assert tier.version
