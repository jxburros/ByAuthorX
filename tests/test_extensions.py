"""Extension layer (CP 7.4): manifests, install flow, scopes, data and script checks, hostile output,
resource limits, export hooks and the CLI/MCP/REST surfaces."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest
from extension_support import (
    EXAMPLES,
    GOOD_SCRIPT,
    install,
    install_example,
    make,
    make_hook,
    manifest,
)

from byax import checks, engine, extensions, model
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.mcp import handle


def doc_with(text='Hello there world. Second sentence here.'):
    return model.new_document(text=text)


def extension_report(doc, **options):
    return checks.run_checks(doc, ['extension'], **options)


def failed_coverage(report):
    return [c for c in report['coverage'] if c['status'] == 'failed']


# Manifest ---------------------------------------------------------------------------------
def test_the_example_manifests_are_valid_and_declare_what_they_do():
    found = {}
    for folder in sorted(EXAMPLES.iterdir()):
        found[folder.name] = extensions.validate_manifest(
            json.loads((folder / extensions.MANIFEST_NAME).read_text(encoding='utf-8'))
        )
    assert set(found) == {'house-style-words', 'long-sentences', 'wordcount-csv'}
    assert found['house-style-words']['entry_points']['checks'][0]['type'] == 'data'
    assert found['long-sentences']['entry_points']['checks'][0]['type'] == 'script'
    assert found['wordcount-csv']['kinds'] == ['export-hook']
    assert all(m['api_version'] == extensions.API_VERSION for m in found.values())


@pytest.mark.parametrize(
    ('change', 'field'),
    [
        ({'id': 'Bad_ID'}, 'id'),
        ({'id': 'x'}, 'id'),
        ({'version': 'one'}, 'version'),
        ({'name': ''}, 'name'),
        ({'description': 5}, 'description'),
        ({'author': None}, 'author'),
        ({'licence': ''}, 'licence'),
        ({'homepage': 'ftp://example.invalid'}, 'homepage'),
        ({'kinds': ['plugin']}, 'kinds'),
        ({'kinds': []}, 'kinds'),
        ({'kinds': ['check', 'export-hook']}, 'kinds'),
        ({'scopes': ['everything']}, 'scopes'),
        ({'scopes': []}, 'scopes'),
        ({'scopes': ['checks']}, 'scopes'),  # a script check needs read
        ({'scopes': ['read']}, 'scopes'),  # checks need checks
        ({'surprise': True}, 'surprise'),
        ({'entry_points': {'checks': [], 'plugins': []}}, 'entry_points'),
        ({'min_byax_version': 'later'}, 'min_byax_version'),
    ],
)
def test_manifest_validation_rejects_each_malformed_field(change, field):
    with pytest.raises(ByaxError) as error:
        extensions.validate_manifest({**manifest(), **change})
    assert error.value.code == 'invalid_input'
    assert error.value.details.get('field') == field or field in error.value.details.get('keys', [])


def test_manifest_api_version_is_checked_and_reported_as_unsupported():
    for value in (2, 0):
        with pytest.raises(ByaxError) as error:
            extensions.validate_manifest({**manifest(), 'api_version': value})
        assert error.value.code == 'unsupported_version'
        assert error.value.details['supported'] == [1]
    with pytest.raises(ByaxError) as error:
        extensions.validate_manifest({k: v for k, v in manifest().items() if k != 'api_version'})
    assert error.value.code == 'invalid_input'


@pytest.mark.parametrize(
    'entry',
    [
        {'id': 'c1', 'type': 'script', 'path': '../escape.py'},
        {'id': 'c1', 'type': 'script', 'path': '/etc/passwd'},
        {'id': 'c1', 'type': 'script', 'path': 'a b.py'},
        {'id': 'c1', 'type': 'script', 'path': 'check.sh'},  # python runtime needs .py
        {'id': 'c1', 'type': 'script', 'path': 'check.py', 'timeout': 0},
        {'id': 'c1', 'type': 'script', 'path': 'check.py', 'timeout': 9999},
        {'id': 'c1', 'type': 'script', 'path': 'check.py', 'runtime': 'node'},
        {'id': 'c1', 'type': 'script', 'path': 'check.py', 'extra': 1},
        {'id': 'C1', 'type': 'script', 'path': 'check.py'},
        {'id': 'c1', 'type': 'plugin', 'path': 'check.py'},
        {'id': 'c1', 'type': 'data', 'path': 'check.py'},
        {'id': 'c1', 'type': 'data', 'path': 'rules.json', 'timeout': 5},
        {'id': 'c1', 'type': 'script', 'path': 'byax-extension.json'},
        {'id': 'c1', 'type': 'script', 'path': 'check.py', 'options': 'big'},
        {'id': 'c1', 'type': 'script', 'path': 'check.py', 'rules': ['Bad Rule']},
    ],
)
def test_entry_points_must_be_safe_and_declared_strictly(entry):
    data = manifest()
    data['entry_points'] = {'checks': [entry]}
    with pytest.raises(ByaxError) as error:
        extensions.validate_manifest(data)
    assert error.value.code == 'invalid_input'


def test_duplicate_entry_points_and_mismatched_kinds_are_refused():
    data = manifest()
    data['entry_points']['checks'].append(dict(data['entry_points']['checks'][0]))
    with pytest.raises(ByaxError, match='Duplicate'):
        extensions.validate_manifest(data)
    hook = {'id': 'h1', 'path': 'hook.py'}
    data = manifest(scopes=('checks', 'read', 'export-hook'))
    data['entry_points']['export_hooks'] = [hook]
    with pytest.raises(ByaxError, match='kinds'):
        extensions.validate_manifest(data)  # a hook entry without the export-hook kind
    data['kinds'] = ['check', 'export-hook']
    assert extensions.validate_manifest(data)['kinds'] == ['check', 'export-hook']


def test_data_check_validation_rejects_bad_rules_and_runaway_patterns():
    good = {'rules': [{'id': 'a', 'kind': 'words', 'words': ['very'], 'message': 'cut it'}]}
    assert extensions.validate_data_check(good, propose=False)[0]['id'] == 'a'
    bad = [
        {'rules': []},
        {'rules': [{'id': 'a', 'kind': 'script', 'message': 'm'}]},
        {'rules': [{'id': 'a', 'kind': 'words', 'words': [], 'message': 'm'}]},
        {'rules': [{'id': 'a', 'kind': 'words', 'words': ['x'], 'message': ''}]},
        {'rules': [{'id': 'a', 'kind': 'words', 'words': ['x'], 'message': 'm', 'severity': 'error'}]},
        {'rules': [{'id': 'a', 'kind': 'words', 'words': ['x'], 'message': 'm', 'code': 'x'}]},
        {'rules': [{'id': 'a', 'kind': 'regex', 'pattern': '(', 'message': 'm'}]},
        {'rules': [{'id': 'a', 'kind': 'regex', 'pattern': '(a+)+$', 'message': 'm'}]},  # backtracking
        {'rules': [{'id': 'a', 'kind': 'words', 'words': ['x'], 'message': 'm'}] * 2},
        {'rules': [{'id': 'a', 'kind': 'regex', 'pattern': 'x', 'flags': 'z', 'message': 'm'}]},
        {'rules': [{'id': 'a', 'kind': 'regex', 'pattern': 'x', 'replacement': 'y', 'message': 'm'}]},
    ]
    for data in bad:
        with pytest.raises(ByaxError):
            extensions.validate_data_check(data, propose=False)


# Install flow -----------------------------------------------------------------------------
def test_inspect_shows_hash_and_scopes_and_installs_nothing(tmp_path):
    folder = make(tmp_path)
    plan = extensions.inspect_package(folder)
    assert plan['requested_scopes'] == ['checks', 'read']
    assert plan['scope_descriptions']['read'] and len(plan['sha256']) == 64
    assert plan['sha256_of'] == 'folder contents' and plan['installed_version'] is None
    assert extensions.summaries() == []


def test_install_requires_the_confirmed_hash_and_exact_scopes(tmp_path):
    folder = make(tmp_path)
    plan = extensions.inspect_package(folder)
    with pytest.raises(ByaxError, match='SHA-256 that was shown'):
        extensions.install(folder, sha256=None, scopes=plan['requested_scopes'])
    with pytest.raises(ByaxError) as error:
        extensions.install(folder, sha256='0' * 64, scopes=plan['requested_scopes'])
    assert error.value.code == 'checksum_mismatch'
    with pytest.raises(ByaxError) as error:
        extensions.install(folder, sha256=plan['sha256'], scopes=['checks'])
    assert error.value.code == 'conflict' and error.value.details['requested'] == ['checks', 'read']
    assert extensions.summaries() == []
    record = extensions.install(folder, sha256=plan['sha256'], scopes=['read', 'checks'])
    assert record['enabled'] is False and record['status'] == 'ok'
    assert record['granted_scopes'] == ['checks', 'read']


def test_a_changed_package_must_be_reconfirmed(tmp_path):
    folder = make(tmp_path)
    plan = extensions.inspect_package(folder)
    (folder / 'check.py').write_text('# changed after the hash was shown\n', encoding='utf-8')
    with pytest.raises(ByaxError) as error:
        extensions.install(folder, sha256=plan['sha256'], scopes=plan['requested_scopes'])
    assert error.value.code == 'checksum_mismatch'


def test_upgrading_with_more_scopes_is_refused_until_the_new_scopes_are_accepted(tmp_path):
    install(make(tmp_path), enable=True)
    wider = make(tmp_path / 'v2', scopes=('checks', 'read', 'propose'))
    plan = extensions.inspect_package(wider)
    assert plan['installed_version'] == '1.0.0'
    with pytest.raises(ByaxError, match='exists|installed'):
        extensions.install(wider, sha256=plan['sha256'], scopes=plan['requested_scopes'])
    with pytest.raises(ByaxError, match='scopes'):
        extensions.install(wider, sha256=plan['sha256'], scopes=['checks', 'read'], replace=True)
    record = extensions.install(wider, sha256=plan['sha256'], scopes=plan['requested_scopes'], replace=True)
    assert record['granted_scopes'] == ['checks', 'propose', 'read']
    assert record['enabled'] is False, 'an upgrade is switched off until the person enables it again'


def test_install_from_a_zip_reports_the_archive_hash_and_strips_one_wrapping_folder(tmp_path):
    folder = make(tmp_path)
    archive = tmp_path / 'ext.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                zf.write(path, 'wrapped/' + path.relative_to(folder).as_posix())
    plan = extensions.inspect_package(archive)
    assert plan['sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert plan['sha256_of'] == 'archive' and plan['tree_sha256'] != plan['sha256']
    record = extensions.install(archive, sha256=plan['sha256'], scopes=plan['requested_scopes'])
    assert record['id'] == 'ext-one' and record['archive_sha256'] == plan['sha256']


@pytest.mark.parametrize(
    'name',
    ['../evil.py', '/abs.py', 'a/../../b.py', 'dir\\file.py', '.hidden/x.py', 'sp ace.py'],
)
def test_archives_with_unsafe_member_names_are_refused(tmp_path, name):
    folder = make(tmp_path)
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.write(folder / extensions.MANIFEST_NAME, extensions.MANIFEST_NAME)
        zf.write(folder / 'check.py', 'check.py')
        member = zipfile.ZipInfo('placeholder')
        member.filename = name  # ZipInfo would turn a backslash into '/' on Windows
        zf.writestr(member, 'x = 1\n')
    with pytest.raises(ByaxError):
        extensions.inspect_package(archive)


def test_archives_with_links_bombs_or_no_manifest_are_refused(tmp_path):
    folder = make(tmp_path)
    link = tmp_path / 'link.zip'
    with zipfile.ZipFile(link, 'w') as zf:
        zf.write(folder / extensions.MANIFEST_NAME, extensions.MANIFEST_NAME)
        info = zipfile.ZipInfo('check.py')
        info.external_attr = 0o120777 << 16
        zf.writestr(info, 'check.py')
    with pytest.raises(ByaxError, match='links'):
        extensions.inspect_package(link)
    bomb = tmp_path / 'bomb.zip'
    with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(folder / extensions.MANIFEST_NAME, extensions.MANIFEST_NAME)
        zf.writestr('check.py', b'0' * (extensions.MAX_FILE_BYTES + 1))
    with pytest.raises(ByaxError) as error:
        extensions.inspect_package(bomb)
    assert error.value.code == 'limit_exceeded'
    bare = tmp_path / 'bare.zip'
    with zipfile.ZipFile(bare, 'w') as zf:
        zf.writestr('check.py', 'pass\n')
    with pytest.raises(ByaxError, match='byax-extension.json'):
        extensions.inspect_package(bare)
    with pytest.raises(ByaxError, match='not a valid ZIP'):
        notzip = tmp_path / 'junk.zip'
        notzip.write_bytes(b'not a zip')
        extensions.inspect_package(notzip)


def test_folders_with_symlinks_or_missing_entry_points_are_refused(tmp_path):
    folder = make(tmp_path)
    (folder / 'link.py').symlink_to(folder / 'check.py')
    with pytest.raises(ByaxError, match='links'):
        extensions.inspect_package(folder)
    (folder / 'link.py').unlink()
    (folder / 'check.py').unlink()
    with pytest.raises(ByaxError, match='missing'):
        extensions.inspect_package(folder)
    with pytest.raises(ByaxError) as error:
        extensions.inspect_package(tmp_path / 'nowhere')
    assert error.value.code == 'missing_target'


def test_extensions_install_disabled_and_enable_disable_remove_round_trip(tmp_path):
    record = install(make(tmp_path), enable=False)
    assert [e['enabled'] for e in extensions.summaries()] == [False]
    assert extensions.set_enabled('ext-one', True)['enabled'] is True
    assert extensions.set_enabled('ext-one', False)['enabled'] is False
    assert extensions.describe('ext-one')['entry_points']['checks'][0]['id'] == 'c1'
    assert extensions.remove('ext-one') == {'removed': 'ext-one', 'version': '1.0.0'}
    assert extensions.summaries() == [] and record['id'] == 'ext-one'
    with pytest.raises(ByaxError) as error:
        extensions.get('ext-one')
    assert error.value.code == 'missing_target'
    with pytest.raises(ByaxError):
        extensions.get('../etc')


def test_a_file_changed_after_installation_makes_the_extension_unusable(tmp_path):
    install(make(tmp_path), enable=True)
    assert extensions.verify('ext-one')[0]['ok']
    installed = Path(extensions.get('ext-one')['directory'])
    (installed / 'check.py').write_text("print('tampered')\n", encoding='utf-8')
    status = extensions.verify('ext-one')[0]
    assert not status['ok'] and 'differs from what was installed' in status['problems'][0]
    assert extensions.get('ext-one')['status'] == 'modified'
    assert extensions.enabled_checks() == []
    report = extension_report(doc_with())
    assert report['findings'] == []
    assert report['coverage'][-1]['rule'] == 'extension.none_enabled'
    with pytest.raises(ByaxError, match='intact'):
        extensions.set_enabled('ext-one', True)
    (installed / 'extra.py').write_text('x = 1\n', encoding='utf-8')
    assert any('added after installation' in p for p in extensions.verify('ext-one')[0]['problems'])


def test_usable_enforces_enabled_intact_and_granted_scope(tmp_path):
    install(make(tmp_path), enable=False)
    with pytest.raises(ByaxError, match='not enabled'):
        extensions.usable('ext-one', 'checks')
    extensions.set_enabled('ext-one', True)
    assert extensions.usable('ext-one', 'checks')['id'] == 'ext-one'
    with pytest.raises(ByaxError) as error:
        extensions.usable('ext-one', 'export-hook')
    assert error.value.code == 'forbidden'
    assert extensions.token_scopes('ext-one') == {'checks', 'read'}
    assert extensions.token_scopes('missing-one') is None


def test_min_byax_version_marks_an_extension_incompatible(tmp_path):
    folder = make(tmp_path, min_byax_version='99.0')
    plan = extensions.inspect_package(folder)
    assert not plan['compatible'] and '99.0' in plan['incompatible_reason']
    with pytest.raises(ByaxError) as error:
        extensions.install(folder, sha256=plan['sha256'], scopes=plan['requested_scopes'])
    assert error.value.code == 'unsupported_version'


# Data checks ------------------------------------------------------------------------------
def test_a_data_check_reports_words_regexes_and_offers_replacements_as_proposals():
    install_example('house-style-words')
    doc = doc_with('This is very good. We utilize whilst running - fine. Literally no filler\u00a0here.')
    report = extension_report(doc)
    rules = sorted({f['rule'] for f in report['findings']})
    assert rules == [
        'extension.house-style-words.double-hyphen',
        'extension.house-style-words.filler',
        'extension.house-style-words.preferred-spelling',
    ]
    for finding in report['findings']:
        assert finding['family'] == 'extension:house-style-words' and finding['suite'] == 'extension'
        assert finding['severity'] in ('advisory', 'warning')
        assert (
            finding['text'] == doc['blocks'][0]['text'][finding['range']['start'] : finding['range']['end']]
        )
    utilize = next(f for f in report['findings'] if f['text'] == 'utilize')
    assert utilize['repair']['separate_operation'] is True
    assert {op['type'] for op in utilize['operations']} == {'delete_text', 'insert_text'}
    assert next(op for op in utilize['operations'] if op['type'] == 'insert_text')['text'] == 'use'
    assert next(f for f in report['findings'] if f['text'] == 'very')['operations'] == []
    assert report['validation'] == 'passed' and report['summary']['errors'] == 0
    assert any(c['status'] == 'completed' and c.get('extension') for c in report['coverage'])


def test_data_check_replacements_are_proposals_only_the_document_does_not_change():
    install_example('house-style-words')
    doc = doc_with('We utilize tools.')
    before = json.dumps(doc, sort_keys=True)
    extension_report(doc)
    assert json.dumps(doc, sort_keys=True) == before
    committed, _ = engine.apply(
        doc,
        {'operations': [{'type': 'record_checks', 'values': {'suites': ['extension']}}]},
    )
    finding = next(f for f in committed['review']['findings'] if f['text'] == 'utilize')
    assert finding['status'] == 'open' and committed['blocks'][0]['text'] == 'We utilize tools.'
    repaired, _ = engine.apply(committed, {'operations': [{'type': 'apply_repair', 'target': finding['id']}]})
    assert repaired['blocks'][0]['text'] == 'We use tools.'


def test_disabled_extensions_contribute_nothing_and_enabled_ones_are_fingerprinted():
    install_example('house-style-words', enable=False)
    doc = doc_with('This is very good.')
    assert extension_report(doc)['findings'] == []
    extensions.set_enabled('house-style-words', True)
    assert len(extension_report(doc)['findings']) == 1
    committed, _ = engine.apply(
        doc, {'operations': [{'type': 'record_checks', 'values': {'suites': ['extension']}}]}
    )
    assert committed['review']['findings'][0]['status'] == 'open'
    extensions.set_enabled('house-style-words', False)
    health = checks.health(committed)
    assert health['validation'] == 'stale', 'turning an extension off makes its recorded findings stale'


def test_intentional_passages_are_never_checked():
    install_example('house-style-words')
    doc = doc_with('Plain very word.')
    doc['blocks'][0]['attrs']['quotation'] = True
    report = extension_report(doc)
    assert report['findings'] == []
    assert any(c['status'] == 'skipped' and c.get('by_policy') for c in report['coverage'])


# Script checks ----------------------------------------------------------------------------
def test_a_script_check_runs_out_of_process_and_enters_the_findings_pipeline(tmp_path):
    install_example('long-sentences')
    words = ' '.join(f'w{i}' for i in range(60))
    doc = doc_with(f'Short one. {words}. Short two.')
    report = extension_report(doc)
    assert len(report['findings']) == 1
    finding = report['findings'][0]
    assert finding['rule'] == 'extension.long-sentences.too-long'
    assert finding['family'] == 'extension:long-sentences'
    assert finding['measured']['words'] == 60 and finding['measured']['extension'] == 'long-sentences'
    text = doc['blocks'][0]['text']
    assert text[finding['range']['start'] : finding['range']['end']].startswith('w0 ')
    assert finding['status'] == 'open' and finding['operations'] == []
    committed, _ = engine.apply(
        doc, {'operations': [{'type': 'record_checks', 'values': {'suites': ['extension']}}]}
    )
    assert committed['review']['findings'][0]['family'] == 'extension:long-sentences'
    assert checks.health(committed)['validation'] != 'stale'


def test_a_script_check_receives_only_the_text_the_read_scope_grants(tmp_path):
    echo = """
        import json, os, sys
        request = json.load(sys.stdin)
        target = request['targets'][0]
        shape = {
            'top': sorted(request),
            'target': sorted(target),
            'count': len(request['targets']),
            'env': sorted(k for k in os.environ if 'SECRET' in k or 'TOKEN' in k or k.startswith('BYAX_SERVICE')),
        }
        json.dump({'findings': [{'rule': 'r1', 'target': target['id'], 'start': 0, 'end': 1,
                                 'message': json.dumps(shape)}]}, sys.stdout)
    """
    install(make(tmp_path, script=echo))
    os.environ['BYAX_SERVICE_TOKEN'] = 'must-not-leak-into-extensions-0000'
    os.environ['SECRET_FOR_TEST'] = 'nope'
    try:
        doc = doc_with('Visible text.')
        doc['metadata']['title'] = 'Private title'
        doc['blocks'].append(
            {
                'id': 'q1',
                'type': 'paragraph',
                'text': 'Quoted words.',
                'style': 'body',
                'marks': [],
                'attrs': {'quotation': True},
            }
        )
        doc['review']['comments'] = []
        report = extension_report(doc)
    finally:
        del os.environ['BYAX_SERVICE_TOKEN'], os.environ['SECRET_FOR_TEST']
    shape = json.loads(report['findings'][0]['explanation'])
    assert shape['top'] == ['api_version', 'check', 'extension', 'language', 'options', 'targets']
    assert shape['target'] == ['block', 'id', 'language', 'text', 'type']
    assert shape['count'] == 1, 'the intentional (quotation) passage is not sent'
    assert shape['env'] == [], 'the child process does not inherit service tokens or secrets'
    assert 'Private title' not in json.dumps(shape)


def finding_script(count=1, **fields):
    """A script that reports ``count`` copies of a finding naming the first target, with ``fields`` changed."""
    finding = {'rule': 'r1', 'start': 0, 'end': 1, 'message': 'm', **fields}
    return (
        'import json, sys\n'
        'request = json.load(sys.stdin)\n'
        f'finding = {finding!r}\n'
        "finding.setdefault('target', request['targets'][0]['id'])\n"
        f"print(json.dumps({{'findings': [finding] * {count}}}))\n"
    )


HOSTILE = {
    'oversized': ("import sys\nsys.stdout.write('x' * (5 * 1024 * 1024))\n", 'oversized'),
    'malformed': ("print('this is not json')\n", 'malformed'),
    'empty': ('pass\n', 'malformed'),
    'garbage_bytes': ("import sys\nsys.stdout.buffer.write(b'\\xff\\xfe\\x00')\n", 'malformed'),
    'nan': ('print(\'{"findings": NaN}\')\n', 'malformed'),
    'not_an_object': ("print('[]')\n", 'invalid'),
    'no_findings_list': ('print(\'{"findings": 5}\')\n', 'invalid'),
    'extra_top_key': ('print(\'{"findings": [], "operations": []}\')\n', 'invalid'),
    'wrong_api': ('print(\'{"api_version": 7, "findings": []}\')\n', 'invalid'),
    'range_past_end': (finding_script(end=99999), 'invalid'),
    'negative_start': (finding_script(start=-1, end=2), 'invalid'),
    'reversed_range': (finding_script(start=5, end=2), 'invalid'),
    'boolean_offsets': (finding_script(start=True, end=True), 'invalid'),
    'unknown_target': (finding_script(target='p_nope'), 'invalid'),
    'error_severity': (finding_script(severity='error'), 'invalid'),
    'unknown_finding_key': (finding_script(operations=[{'type': 'delete_block'}]), 'invalid'),
    'bad_rule_id': (finding_script(rule='Bad Rule'), 'invalid'),
    'huge_message': (finding_script(message='m' * 501), 'invalid'),
    'replacement_without_propose': (finding_script(replacement='x'), 'invalid'),
    'too_many_findings': (finding_script(count=1001), 'invalid'),
    'crash': ("raise RuntimeError('boom')\n", 'crash'),
    'exit_status': ('import sys\nsys.exit(3)\n', 'crash'),
    'syntax_error': ('def broken(:\n', 'crash'),
    'timeout': ('import time\ntime.sleep(60)\n', 'timeout'),
    'busy_loop': ('while True:\n    pass\n', 'timeout'),
}


@pytest.mark.parametrize('name', sorted(HOSTILE))
def test_hostile_script_output_is_rejected_whole_and_reported_as_failed_coverage(tmp_path, name):
    script, kind = HOSTILE[name]
    install(make(tmp_path, script=script, timeout=1 if kind == 'timeout' else 5))
    report = extension_report(doc_with())
    failed = failed_coverage(report)
    assert report['findings'] == [], 'no part of a rejected output is used'
    assert len(failed) == 1 and failed[0]['failure'] == kind, failed
    assert failed[0]['extension'] == 'ext-one'
    assert report['validation'] == 'incomplete', 'a failed extension is never a clean pass'


def test_declared_rules_restrict_what_a_script_may_report(tmp_path):
    install(make(tmp_path, rules=['other-rule']))
    report = extension_report(doc_with())
    assert report['findings'] == [] and failed_coverage(report)[0]['failure'] == 'invalid'
    assert 'not declared' in failed_coverage(report)[0]['reason']


def test_a_good_script_still_works_and_proposes_with_the_propose_scope(tmp_path):
    script = GOOD_SCRIPT.replace(
        "'message': 'hello there'", "'message': 'hello there', 'replacement': 'HELLO'"
    )
    install(make(tmp_path, script=script, scopes=('checks', 'read', 'propose')))
    doc = doc_with('Hello world.')
    finding = extension_report(doc)['findings'][0]
    assert finding['rule'] == 'extension.ext-one.r1'
    assert next(op for op in finding['operations'] if op['type'] == 'insert_text')['text'] == 'HELLO'


def test_timeouts_and_crashes_do_not_hang_and_a_failed_unit_is_retried_incrementally(tmp_path):
    marker = tmp_path / 'ran-once'
    script = f"""
        import json, os, sys, time
        request = json.load(sys.stdin)
        marker = {str(marker)!r}
        if not os.path.exists(marker):
            open(marker, 'w').close()
            sys.exit(1)
        target = request['targets'][0]
        json.dump({{'findings': [{{'rule': 'r1', 'target': target['id'], 'start': 0, 'end': 2, 'message': 'second time'}}]}}, sys.stdout)
    """
    install(make(tmp_path, script=script))
    doc = doc_with('Retry me.')
    first, _ = engine.apply(
        doc, {'operations': [{'type': 'record_checks', 'values': {'suites': ['extension']}}]}
    )
    assert first['review']['findings'] == []
    assert any(c.get('status') == 'failed' for c in first['review']['health']['coverage'])
    second, _ = engine.apply(
        first,
        {'operations': [{'type': 'record_checks', 'values': {'suites': ['extension'], 'incremental': True}}]},
    )
    assert [f['explanation'] for f in second['review']['findings']] == ['second time']


def test_a_script_cannot_run_when_the_installed_file_was_replaced_between_listing_and_running(tmp_path):
    install(make(tmp_path))
    record = extensions.get('ext-one')
    (Path(record['directory']) / 'check.py').write_text("print('{}')\n", encoding='utf-8')
    with pytest.raises(extensions.ExtensionFailure) as error:
        extensions._entry_file(record, 'check.py')
    assert error.value.kind == 'tampered'


# Resource limits --------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != 'linux', reason='address-space limits are enforced on Linux')
def test_a_memory_hog_is_stopped_by_the_address_space_limit(tmp_path):
    script = 'x = bytearray(3 * 1024 ** 3)\nprint(\'{"findings": []}\')\n'
    install(make(tmp_path, script=script))
    report = extension_report(doc_with())
    failed = failed_coverage(report)
    assert len(failed) == 1 and failed[0]['failure'] == 'crash', failed


@pytest.mark.skipif(sys.platform == 'win32', reason='no resource module on Windows')
def test_the_worker_applies_memory_cpu_and_file_size_limits_before_the_extension_runs(tmp_path):
    code = (
        'import resource, sys\n'
        'from byax.platform_support import apply_parser_limits\n'
        'applied = apply_parser_limits(1 << 30, 7, 1 << 20)\n'
        'print(applied, resource.getrlimit(resource.RLIMIT_CPU)[0], resource.getrlimit(resource.RLIMIT_FSIZE)[0])\n'
    )
    out = subprocess.run(
        [sys.executable, '-I', '-c', code], capture_output=True, text=True, check=True
    ).stdout
    assert "'cpu': True" in out and "'file': True" in out and out.strip().endswith('7 1048576')
    from byax.platform_support import apply_parser_limits

    assert callable(apply_parser_limits)


def test_the_launcher_runs_a_python_entry_point_with_its_sibling_modules_and_returns_its_status(tmp_path):
    folder = tmp_path / 'lib'
    folder.mkdir()
    (folder / 'helper.py').write_text('VALUE = 41\n', encoding='utf-8')
    (folder / 'main.py').write_text(
        'import helper, sys\nprint(helper.VALUE + 1)\nsys.exit(4)\n', encoding='utf-8'
    )
    done = subprocess.run(
        [
            sys.executable,
            '-I',
            '-m',
            'byax.extension_worker',
            'python',
            str(folder / 'main.py'),
            str(1 << 30),
            '10',
            str(1 << 20),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.stdout.strip() == '42' and done.returncode == 4
    unknown = subprocess.run(
        [sys.executable, '-I', '-m', 'byax.extension_worker', 'perl', 'x', '1', '1', '1'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert unknown.returncode == 2
    assert (
        subprocess.run(
            [sys.executable, '-I', '-m', 'byax.extension_worker'], capture_output=True, check=False
        ).returncode
        == 2
    )


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX executables')
def test_an_executable_entry_point_runs_through_the_same_limited_launcher(tmp_path):
    folder = make(tmp_path)
    program = folder / 'check.sh'
    program.write_text('#!/bin/sh\ncat >/dev/null\necho \'{"findings": []}\'\n', encoding='utf-8')
    program.chmod(0o755)
    data = manifest()
    data['entry_points']['checks'][0].update(path='check.sh', runtime='executable')
    (folder / extensions.MANIFEST_NAME).write_text(json.dumps(data), encoding='utf-8')
    (folder / 'check.py').unlink()
    install(folder)
    report = extension_report(doc_with())
    assert report['findings'] == [] and not failed_coverage(report)
    program.chmod(0o644)
    with pytest.raises(ByaxError, match='execute permission'):
        extensions.inspect_package(folder)


# Export hooks -----------------------------------------------------------------------------
def export_markdown(tmp_path, text='One two three four five.'):
    source = tmp_path / 'book.byax'
    execute('new', {'out': str(source), 'text': text})
    return source


def test_the_wordcount_hook_writes_a_csv_beside_the_export_and_never_touches_the_master(tmp_path):
    install_example('wordcount-csv')
    source = export_markdown(tmp_path)
    master = source.read_bytes()
    out = tmp_path / 'book.md'
    result = execute('export', {'path': str(source), 'out': str(out), 'hooks': True})
    assert source.read_bytes() == master
    (hook,) = result['hooks']
    assert hook['status'] == 'completed' and hook['extension'] == 'wordcount-csv'
    (csv_path,) = hook['outputs']
    assert Path(csv_path).name == 'book.md.wordcount.csv' and Path(csv_path).parent == Path(
        str(out) + '.hooks'
    )
    rows = Path(csv_path).read_text(encoding='utf-8').splitlines()
    assert rows[0] == 'file,format,bytes,words,reported_losses'
    assert rows[1].startswith('book.md,md,') and ',5,' in rows[1]
    assert hook['message'] == '5 words in book.md'


def test_hooks_run_only_when_asked_and_a_failing_hook_never_fails_the_export(tmp_path):
    install(make_hook(tmp_path, script="raise RuntimeError('hook crashed')\n"))
    source = export_markdown(tmp_path)
    plain = execute('export', {'path': str(source), 'out': str(tmp_path / 'a.md')})
    assert 'hooks' not in plain
    result = execute('export', {'path': str(source), 'out': str(tmp_path / 'b.md'), 'hooks': True})
    assert (tmp_path / 'b.md').is_file() and result['execution'] == 'completed'
    (hook,) = result['hooks']
    assert hook['status'] == 'failed' and hook['failure'] == 'crash' and 'hook crashed' in hook['stderr']


def test_a_hook_gets_a_copy_and_the_loss_report_and_its_outputs_are_collected_safely(tmp_path):
    script = """
        import json, os, sys
        request = json.load(sys.stdin)
        assert sorted(request) == ['api_version', 'extension', 'file', 'format', 'hook', 'loss_report', 'output_dir']
        report = request['loss_report']
        with open(os.path.join(request['output_dir'], 'seen.json'), 'w') as handle:
            json.dump({'file': request['file'], 'losses': report.get('losses'), 'format': request['format'],
                       'cwd': os.getcwd(), 'output_dir': request['output_dir']}, handle)
        try:
            with open(request['file'], 'a') as handle:
                handle.write('tampered copy')
        except OSError:
            pass
        print(json.dumps({'message': 'done'}))
    """
    install(make_hook(tmp_path, script=script))
    source = export_markdown(tmp_path)
    out = tmp_path / 'chapter.html'
    result = execute(
        'export', {'path': str(source), 'out': str(out), 'hooks': True, 'hooks_dir': str(tmp_path / 'extra')}
    )
    exported = out.read_bytes()
    assert b'tampered copy' not in exported, 'the hook only ever saw a copy'
    (hook,) = result['hooks']
    assert hook['status'] == 'completed' and hook['message'] == 'done'
    seen = json.loads((tmp_path / 'extra' / 'seen.json').read_text(encoding='utf-8'))
    assert seen['file'] != str(out) and seen['format'] == 'html' and 'byax-ext-hook-' in seen['file']
    assert seen['cwd'] == seen['output_dir'] != str(tmp_path / 'extra')
    assert isinstance(seen['losses'], list)
    assert not Path(seen['file']).exists(), 'the temporary copy is removed afterwards'


def test_hook_outputs_never_overwrite_unless_asked(tmp_path):
    script = "import json, os, sys\nr = json.load(sys.stdin)\nopen(os.path.join(r['output_dir'], 'report.txt'), 'w').write('new')\n"
    install(make_hook(tmp_path, script=script))
    source = export_markdown(tmp_path)
    target = tmp_path / 'hooks-out'
    target.mkdir()
    (target / 'report.txt').write_text('old', encoding='utf-8')
    result = execute(
        'export',
        {'path': str(source), 'out': str(tmp_path / 'c.md'), 'hooks': True, 'hooks_dir': str(target)},
    )
    (hook,) = result['hooks']
    assert hook['outputs'] == [] and hook['skipped'][0]['file'] == 'report.txt'
    assert (target / 'report.txt').read_text(encoding='utf-8') == 'old'
    result = execute(
        'export',
        {
            'path': str(source),
            'out': str(tmp_path / 'c.md'),
            'hooks': True,
            'hooks_dir': str(target),
            'overwrite': True,
        },
    )
    assert (target / 'report.txt').read_text(encoding='utf-8') == 'new' and result['hooks'][0]['outputs']


HOSTILE_HOOKS = {
    'too_many_files': (
        (
            'import json, os, sys\nr = json.load(sys.stdin)\n'
            "for i in range(25):\n    open(os.path.join(r['output_dir'], f'f{i}.txt'), 'w').write('x')\n"
        ),
        'invalid',
    ),
    'subdirectory': (
        "import json, os, sys\nr = json.load(sys.stdin)\nos.mkdir(os.path.join(r['output_dir'], 'sub'))\n",
        'invalid',
    ),
    'symlink': (
        "import json, os, sys\nr = json.load(sys.stdin)\nos.symlink('/etc/passwd', os.path.join(r['output_dir'], 'link.txt'))\n",
        'invalid',
    ),
    'bad_name': (
        "import json, os, sys\nr = json.load(sys.stdin)\nopen(os.path.join(r['output_dir'], '.hidden'), 'w').write('x')\n",
        'invalid',
    ),
    'file_too_big': (
        (
            'import json, os, sys\nr = json.load(sys.stdin)\n'
            "with open(os.path.join(r['output_dir'], 'big.bin'), 'wb') as h:\n"
            "    h.write(b'0' * (40 * 1024 * 1024))\n"
        ),
        'crash',
    ),
    'stdout_not_json': ("print('plain text')\n", 'malformed'),
    'stdout_wrong_keys': ('print(\'{"outputs": ["/etc/passwd"]}\')\n', 'malformed'),
    'stdout_flood': ("import sys\nsys.stdout.write('x' * (200 * 1024))\n", 'oversized'),
    'timeout': ('import time\ntime.sleep(60)\n', 'timeout'),
    'crash': ('import sys\nsys.exit(9)\n', 'crash'),
}


@pytest.mark.parametrize('name', sorted(HOSTILE_HOOKS))
def test_hostile_hooks_are_contained_and_report_failure_without_leaving_outputs(tmp_path, name):
    script, kind = HOSTILE_HOOKS[name]
    install(make_hook(tmp_path, script=script, timeout=1 if kind == 'timeout' else 5))
    source = export_markdown(tmp_path)
    target = tmp_path / 'collected'
    result = execute(
        'export',
        {'path': str(source), 'out': str(tmp_path / 'd.md'), 'hooks': True, 'hooks_dir': str(target)},
    )
    (hook,) = result['hooks']
    if name == 'file_too_big' and importlib.util.find_spec('resource') is None:
        # Without a file-size limit (Windows) the oversized output is written, then refused when collected.
        kind = 'invalid'
    assert hook['status'] in ('failed', 'timeout') and hook['failure'] == kind, hook
    assert hook['outputs'] == [] and not target.exists()
    assert (tmp_path / 'd.md').is_file()


def test_hooks_respect_the_formats_they_declare(tmp_path):
    install(make_hook(tmp_path, script="print('{}')\n", formats=['pdf']))
    source = export_markdown(tmp_path)
    result = execute('export', {'path': str(source), 'out': str(tmp_path / 'e.md'), 'hooks': True})
    assert result['hooks'] == []
    assert extensions.hooks_for('pdf') and not extensions.hooks_for('md')


def test_run_hook_command_checks_enabled_scope_and_format(tmp_path):
    install(make_hook(tmp_path, script="print('{}')\n", formats=['md']), enable=False)
    exported = tmp_path / 'x.md'
    exported.write_text('text', encoding='utf-8')
    with pytest.raises(ByaxError, match='not enabled'):
        execute(
            'extensions',
            {'action': 'run-hook', 'id': 'hook-one', 'hook': 'h1', 'exported_file': str(exported)},
        )
    extensions.set_enabled('hook-one', True)
    with pytest.raises(ByaxError, match='No such export hook'):
        execute(
            'extensions',
            {'action': 'run-hook', 'id': 'hook-one', 'hook': 'nope', 'exported_file': str(exported)},
        )
    with pytest.raises(ByaxError, match='does not apply'):
        execute(
            'extensions',
            {
                'action': 'run-hook',
                'id': 'hook-one',
                'hook': 'h1',
                'exported_file': str(exported),
                'format': 'pdf',
            },
        )
    result = execute(
        'extensions', {'action': 'run-hook', 'id': 'hook-one', 'hook': 'h1', 'exported_file': str(exported)}
    )
    assert result['hook']['status'] == 'completed' and result['execution'] == 'completed'


# CLI, MCP and REST parity -----------------------------------------------------------------
def run_cli(capsys, *args):
    code = main(list(args))
    return code, json.loads(capsys.readouterr().out)


def test_cli_install_is_a_two_step_confirmation_with_the_shown_hash(tmp_path, capsys):
    folder = str(EXAMPLES / 'long-sentences')
    code, preview = run_cli(capsys, 'extensions', '--action', 'install', '--source-path', folder)
    assert code == 0 and preview['installed'] is False and preview['review'] == 'required'
    shown = preview['package']['sha256']
    assert shown in preview['next'] and extensions.summaries() == []
    code, wrong = run_cli(
        capsys,
        'extensions',
        '--action',
        'install',
        '--source-path',
        folder,
        '--sha256',
        '0' * 64,
        '--scopes',
        'checks',
        '--scopes',
        'read',
    )
    assert code == 1 and wrong['error']['code'] == 'checksum_mismatch'
    code, partial = run_cli(
        capsys,
        'extensions',
        '--action',
        'install',
        '--source-path',
        folder,
        '--sha256',
        shown,
        '--scopes',
        'checks',
    )
    assert code == 3 and partial['error']['code'] == 'conflict'
    code, done = run_cli(
        capsys,
        'extensions',
        '--action',
        'install',
        '--source-path',
        folder,
        '--sha256',
        shown,
        '--scopes',
        'checks',
        '--scopes',
        'read',
    )
    assert code == 0 and done['installed'] and done['extension']['enabled'] is False
    code, listing = run_cli(capsys, 'extensions')
    assert [e['id'] for e in listing['extensions']] == ['long-sentences']
    assert run_cli(capsys, 'extensions', '--action', 'enable', '--id', 'long-sentences')[1]['extension'][
        'enabled'
    ]
    code, info = run_cli(capsys, 'extensions', '--action', 'info', '--id', 'long-sentences')
    assert info['extension']['granted_scopes'] == ['checks', 'read']
    code, verified = run_cli(capsys, 'extensions', '--action', 'verify')
    assert code == 0 and verified['validation'] == 'passed'
    assert run_cli(capsys, 'extensions', '--action', 'scopes')[1]['scopes'].keys() == extensions.SCOPES.keys()
    assert (
        run_cli(capsys, 'extensions', '--action', 'disable', '--id', 'long-sentences')[1]['extension'][
            'enabled'
        ]
        is False
    )
    assert (
        run_cli(capsys, 'extensions', '--action', 'remove', '--id', 'long-sentences')[1]['removed']
        == 'long-sentences'
    )
    code, _ = run_cli(capsys, 'extensions', '--action', 'frobnicate')
    assert code == 2


def test_run_check_runs_only_the_named_enabled_extension_on_a_document(tmp_path, capsys):
    install_example('long-sentences')
    install_example('house-style-words')
    path = tmp_path / 'doc.byax'
    execute('new', {'out': str(path), 'text': 'This is very plain. ' + ' '.join(['word'] * 50) + '.'})
    code, report = run_cli(
        capsys, 'extensions', '--action', 'run-check', '--id', 'long-sentences', '--document', str(path)
    )
    assert code == 0 and {f['family'] for f in report['findings']} == {'extension:long-sentences'}
    both = execute('check', {'path': str(path), 'suite': ['extension']})
    assert {f['family'] for f in both['findings']} == {
        'extension:long-sentences',
        'extension:house-style-words',
    }
    via_family = execute('check', {'path': str(path), 'family': ['extension']})
    assert len(via_family['findings']) == len(both['findings'])
    extensions.set_enabled('long-sentences', False)
    with pytest.raises(ByaxError, match='not enabled'):
        execute('extensions', {'action': 'run-check', 'id': 'long-sentences', 'document': str(path)})


ADMIN = ('install', 'inspect-package', 'enable', 'disable', 'remove', 'token')


def mcp_call(arguments, name='byax_extensions'):
    response = handle(
        {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}
    )
    return response['result']


def test_administrative_actions_are_refused_over_mcp_and_nothing_changes(tmp_path):
    install_example('long-sentences', enable=False)
    folder = str(EXAMPLES / 'house-style-words')
    for action in ADMIN:
        result = mcp_call({'action': action, 'id': 'long-sentences', 'source_path': folder})
        assert result['isError'], action
        assert result['structuredContent']['error']['code'] == 'unsupported_feature', action
    assert [(e['id'], e['enabled']) for e in extensions.summaries()] == [('long-sentences', False)]
    listing = mcp_call({'action': 'list'})
    assert not listing['isError'] and listing['structuredContent']['extensions'][0]['id'] == 'long-sentences'
    assert not mcp_call({'action': 'info', 'id': 'long-sentences'})['isError']


def test_extension_checks_work_over_mcp_like_the_cli(tmp_path):
    install_example('long-sentences')
    path = tmp_path / 'doc.byax'
    execute('new', {'out': str(path), 'text': ' '.join(['word'] * 50) + '.'})
    over_mcp = mcp_call({'path': str(path), 'suite': ['extension']}, 'byax_check')
    assert not over_mcp['isError']
    assert over_mcp['structuredContent']['findings'][0]['family'] == 'extension:long-sentences'


def test_export_hooks_flag_is_in_the_export_schema_and_reports_through_mcp(tmp_path):
    install_example('wordcount-csv')
    source = export_markdown(tmp_path)
    result = mcp_call({'path': str(source), 'out': str(tmp_path / 'm.md'), 'hooks': True}, 'byax_export')
    assert not result['isError'] and result['structuredContent']['hooks'][0]['status'] == 'completed'


def test_script_check_findings_are_deterministic_across_runs(tmp_path):
    install_example('long-sentences')
    doc = doc_with(' '.join(['word'] * 60) + '.')
    first = [f['id'] for f in extension_report(doc)['findings']]
    second = [f['id'] for f in extension_report(doc)['findings']]
    assert first == second and len(first) == 1


def test_the_launcher_dispatch_flag_is_wired_for_frozen_builds(tmp_path, monkeypatch):
    from byax import extension_worker, library_parser, platform_support

    # The worker applies RLIMIT_CPU/AS/FSIZE to *its own process*. Run in-process here that would cap
    # the whole test runner (a 10 s CPU limit kills or wedges every later test), so record the call.
    limits = []
    monkeypatch.setattr(platform_support, 'apply_parser_limits', lambda *args: limits.append(args) or {})
    folder = tmp_path / 'w'
    folder.mkdir()
    (folder / 's.py').write_text("print('frozen ok')\n", encoding='utf-8')
    with pytest.raises(SystemExit) as stop:
        library_parser.dispatch_worker(
            ['--byax-extension-worker', 'python', str(folder / 's.py'), str(1 << 30), '10', str(1 << 20)]
        )
    assert stop.value.code == 0
    assert limits == [(1 << 30, 10, 1 << 20)]
    assert extension_worker.dispatch_worker(['--other']) is None
    cmd = extensions._worker_command('python', Path('s.py'), 5, 100)
    assert cmd[1:4] == ['-I', '-m', 'byax.extension_worker']
    sys.frozen = True  # type: ignore[attr-defined]
    try:
        assert extensions._worker_command('python', Path('s.py'), 5, 100)[1] == '--byax-extension-worker'
    finally:
        del sys.frozen  # type: ignore[attr-defined]


def test_hook_directory_names_are_the_only_files_collected(tmp_path):
    assert textwrap.dedent('x') == 'x'
    for name in ('report.csv', 'My Report 1.txt', 'a_b-c.tar.gz'):
        assert extensions._OUTPUT_NAME.fullmatch(name)
    for name in ('.hidden', '../up', 'a/b', '', 'x' * 130, ' lead'):
        assert not extensions._OUTPUT_NAME.fullmatch(name)


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process groups')
def test_a_timed_out_script_takes_its_child_processes_with_it(tmp_path):
    import time

    marker = tmp_path / 'child-alive'
    child = f"import time; time.sleep(2); open({str(marker)!r}, 'w').close()"
    script = (
        f'import subprocess, sys, time\nsubprocess.Popen([sys.executable, "-c", {child!r}])\ntime.sleep(60)\n'
    )
    install(make(tmp_path, script=script, timeout=1))
    report = extension_report(doc_with())
    assert failed_coverage(report)[0]['failure'] == 'timeout'
    time.sleep(3)
    assert not marker.exists(), 'the whole process group is killed, not only the launcher'
