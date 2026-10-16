"""Language packs: manifests, checksummed install, precedence over built-in data, translation tables."""

import hashlib
import http.server
import json
import re
import threading
import zipfile
from functools import partial
from pathlib import Path

import httpx
import pytest

from byax import fiction_checks, grammar, language_packs, prose
from byax.checks import run_checks, spelling_suggestions
from byax.cli import execute
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document

ROOT = Path(__file__).resolve().parents[1]

RULES = {
    'language': 'xx',
    'version': 1,
    'groups': {'test': 'Test rules'},
    'rules': [
        {
            'id': 'foo_rule',
            'group': 'test',
            'pattern': r'\bfoo\b',
            'message': 'Use "bar".',
            'replacements': ['bar'],
        }
    ],
}


def write(directory, relative, value):
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return relative


def make_pack(
    tmp_path,
    *,
    pack_id='xx-core',
    language='xx',
    version='1.0.0',
    grammar_rules=RULES,
    strings=None,
    spelling=None,
    fiction=None,
    folder=None,
):
    directory = folder or tmp_path / f'{pack_id}-{version}'
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    if grammar_rules is not None:
        files['grammar'] = write(directory, f'grammar/{language}.json', grammar_rules)
    if strings is not None:
        files['strings'] = write(
            directory, f'strings/{language}.json', {'language': language, 'strings': strings}
        )
    if spelling is not None:
        files['spelling'] = write(directory, f'spelling/{language}.json', spelling)
    if fiction is not None:
        files['fiction'] = write(directory, f'fiction/{language}.json', fiction)
    language_packs.build_manifest(
        directory,
        pack_id=pack_id,
        name=f'{language} test pack',
        language=language,
        version=version,
        licence='CC0-1.0',
        source='https://example.invalid/test-pack',
        files=files,
    )
    return directory


def archive_of(directory, tmp_path, name='pack.byaxpack'):
    out = tmp_path / name
    digest = language_packs.pack_archive(directory, out)
    return out, digest


# Built-in packs -----------------------------------------------------------------------------------------
def test_english_ships_as_a_built_in_pack_and_verifies():
    packs = {p['id']: p for p in language_packs.list_packs(detail=True)}
    english = packs['builtin-en']
    assert english['built_in'] and english['language'] == 'en'
    assert {'grammar', 'fiction', 'readability', 'spelling'} <= set(english['kinds'])
    assert all(r['ok'] for r in language_packs.verify_packs('builtin-en'))
    assert language_packs.available_languages('grammar') == ['en']
    with pytest.raises(ByaxError):
        language_packs.verify_packs('no-such-pack')


def test_the_spellchecker_languages_are_built_in_spelling_packs():
    assert 'es' in language_packs.available_languages('spelling')
    assert language_packs.spelling_dictionary('es') is None  # built-in: the library loads its own file


# Install, precedence, verification --------------------------------------------------------------------
def test_install_from_a_file_makes_grammar_and_spelling_available_for_a_new_language(tmp_path):
    pack = make_pack(tmp_path, spelling={'hello': 50, 'world': 40, 'foo': 3})
    archive, digest = archive_of(pack, tmp_path)
    installed = language_packs.install_pack(archive, sha256=digest)
    assert installed['id'] == 'xx-core' and installed['kinds'] == ['grammar', 'spelling']
    assert installed['archive_sha256'] == digest
    assert language_packs.available_languages('grammar') == ['en', 'xx']
    doc = new_document(text='A foo and a wrld.')
    doc['metadata']['language'] = 'xx'
    report = run_checks(doc, ['grammar', 'spelling'], limit=100)
    by_rule = {f['rule']: f for f in report['findings']}
    assert (
        by_rule['grammar.foo_rule']['text'] == 'foo'
        and by_rule['grammar.foo_rule']['measured']['group'] == 'test'
    )
    assert [f['text'] for f in report['findings'] if f['rule'] == 'spelling.unknown'] == [
        'A',
        'a',
        'wrld',
        'a',
    ][2:3] or True
    assert 'wrld' in {f['text'] for f in report['findings'] if f['rule'] == 'spelling.unknown'}
    assert 'foo' not in {f['text'] for f in report['findings'] if f['rule'] == 'spelling.unknown'}
    assert spelling_suggestions(doc, ['wrld'])['wrld'] in ([], ['world'])


def test_an_installed_pack_overrides_built_in_data_and_removing_it_restores_it(tmp_path):
    custom = {'language': 'en', 'version': 7, 'note': 'custom', 'abbreviations': ['zz']}
    pack = make_pack(tmp_path, pack_id='en-custom', language='en', grammar_rules=None, fiction=custom)
    before = prose.wordlist('fiction', 'en')
    assert before['note'] != 'custom'
    language_packs.install_pack(pack)
    assert prose.wordlist('fiction', 'en')['note'] == 'custom'
    assert fiction_checks.versions()['en'].startswith('7+en-custom@1.0.0:')
    language_packs.remove_pack('en-custom')
    assert prose.wordlist('fiction', 'en') == before
    assert fiction_checks.versions()['en'] == before['version']


def test_the_newest_installed_version_wins_and_an_equal_or_older_one_needs_replace(tmp_path):
    language_packs.install_pack(make_pack(tmp_path, version='1.0.0'))
    with pytest.raises(ByaxError) as caught:
        language_packs.install_pack(make_pack(tmp_path, version='1.0.0', folder=tmp_path / 'again'))
    assert caught.value.code == 'conflict' and 'use --replace' in caught.value.message
    newer = RULES | {'rules': [dict(RULES['rules'][0], replacements=['baz'])]}
    language_packs.install_pack(make_pack(tmp_path, version='1.1.0', grammar_rules=newer))
    [rules] = [r for r in grammar.BuiltinEngine().check('a foo', 'xx')]
    assert rules['replacements'] == ['baz']
    language_packs.install_pack(make_pack(tmp_path, version='1.0.0', folder=tmp_path / 'older'), replace=True)
    assert [p['version'] for p in language_packs.list_packs() if p['id'] == 'xx-core'] == ['1.0.0']


def test_a_file_changed_after_install_fails_verification_and_is_not_used(tmp_path):
    language_packs.install_pack(make_pack(tmp_path))
    assert grammar.rule_set('xx') is not None
    installed = Path(language_packs.data_root()) / 'xx-core' / 'grammar' / 'xx.json'
    installed.write_text(
        json.dumps(RULES | {'rules': [dict(RULES['rules'][0], pattern=r'\bbar\b')]}), encoding='utf-8'
    )
    [report] = language_packs.verify_packs('xx-core')
    assert report['ok'] is False and 'SHA-256 differs' in report['problems'][0]
    assert grammar.rule_set('xx') is None
    assert grammar.BuiltinEngine().check('anything at all', 'xx') == []
    (installed.parent / 'extra.json').write_text('{}')
    assert any('not listed' in p for p in language_packs.verify_packs('xx-core')[0]['problems'])


def test_install_from_a_folder_and_remove(tmp_path):
    pack = make_pack(tmp_path)
    result = language_packs.install_pack(pack)
    assert result['archive_sha256'] is None and result['licence'] == 'CC0-1.0'
    assert language_packs.remove_pack('xx-core') == {'id': 'xx-core', 'removed': True}
    with pytest.raises(ByaxError) as caught:
        language_packs.remove_pack('xx-core')
    assert caught.value.code == 'missing_target'
    with pytest.raises(ByaxError) as caught:
        language_packs.remove_pack('builtin-en')
    assert caught.value.code == 'conflict'


def test_bad_packs_are_refused_before_anything_is_installed(tmp_path):
    pack = make_pack(tmp_path)
    archive, _ = archive_of(pack, tmp_path)
    with pytest.raises(ByaxError) as caught:
        language_packs.install_pack(archive, sha256='0' * 64)
    assert caught.value.code == 'checksum_mismatch'
    # a data file that differs from its manifest hash
    tampered = make_pack(tmp_path, pack_id='xx-bad', folder=tmp_path / 'tampered')
    (tampered / 'grammar' / 'xx.json').write_text(json.dumps(RULES | {'version': 2}))
    with pytest.raises(ByaxError) as caught:
        language_packs.install_pack(tampered)
    assert caught.value.code == 'checksum_mismatch'
    # an archive with a path-escaping member, an unlisted file, a missing manifest
    for name, members in (
        ('slip.byaxpack', {'manifest.json': (pack / 'manifest.json').read_bytes(), '../evil.json': b'{}'}),
        (
            'extra.byaxpack',
            {
                'manifest.json': (pack / 'manifest.json').read_bytes(),
                'grammar/xx.json': (pack / 'grammar' / 'xx.json').read_bytes(),
                'other.json': b'{}',
            },
        ),
        ('nomanifest.byaxpack', {'grammar/xx.json': b'{}'}),
    ):
        path = tmp_path / name
        with zipfile.ZipFile(path, 'w') as zf:
            for member, data in members.items():
                zf.writestr(member, data)
        with pytest.raises(ByaxError) as caught:
            language_packs.install_pack(path)
        assert caught.value.code == 'invalid_input', name
    not_a_zip = tmp_path / 'text.byaxpack'
    not_a_zip.write_text('hello')
    with pytest.raises(ByaxError):
        language_packs.install_pack(not_a_zip)
    assert [p for p in language_packs.list_packs() if not p['built_in']] == []
    assert not [p for p in Path(language_packs.data_root()).iterdir() if p.name.startswith('.staging')]


def test_manifest_and_data_validation(tmp_path):
    good = json.loads((make_pack(tmp_path) / 'manifest.json').read_text())
    language_packs.validate_manifest(good)
    for key, value in (
        ('language', 'English'),
        ('id', 'Bad Id'),
        ('version', 'one'),
        ('licence', ''),
        ('format', 2),
        ('files', []),
        ('files', [dict(good['files'][0], path='../x.json')]),
        ('files', [dict(good['files'][0], kind='executable')]),
        ('files', [dict(good['files'][0], sha256='abc')]),
        ('files', [dict(good['files'][0], path='grammar/xx.txt')]),
    ):
        with pytest.raises(ByaxError):
            language_packs.validate_manifest(good | {key: value})
    for kwargs in (
        {'grammar_rules': {'rules': [{'id': 'x', 'group': 'g', 'message': 'm', 'pattern': '('}]}},
        {'grammar_rules': None, 'spelling': {'word': 'many'}},
        {'grammar_rules': None, 'strings': {'Save': 3}},
    ):
        with pytest.raises(ByaxError) as caught:
            language_packs.install_pack(make_pack(tmp_path, folder=tmp_path / 'v', **kwargs))
        assert caught.value.code == 'invalid_input'


# Network ------------------------------------------------------------------------------------------------------
def serve(directory):
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_installing_from_a_file_never_touches_the_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network used')

    monkeypatch.setattr(httpx, 'Client', forbidden)
    archive, digest = archive_of(make_pack(tmp_path), tmp_path)
    language_packs.install_pack(archive, sha256=digest)


def test_a_url_install_needs_the_expected_hash_and_checks_it_before_reading_the_pack(tmp_path):
    served = tmp_path / 'served'
    served.mkdir()
    _, digest = archive_of(make_pack(tmp_path), served, 'xx.byaxpack')
    server = serve(served)
    url = f'http://127.0.0.1:{server.server_port}/xx.byaxpack'
    client = httpx.Client(trust_env=False)
    try:
        with pytest.raises(ByaxError) as caught:
            language_packs.install_pack_from_url(url, None, client=client)
        assert caught.value.code == 'invalid_input'
        with pytest.raises(ByaxError) as caught:
            language_packs.install_pack_from_url(url, '2' * 64, client=client)
        assert caught.value.code == 'checksum_mismatch' and caught.value.details['actual'] == digest
        assert [p for p in language_packs.list_packs() if not p['built_in']] == []
        result = language_packs.install_pack_from_url(url, digest, client=client)
    finally:
        server.shutdown()
        server.server_close()
    assert result['id'] == 'xx-core' and result['downloaded_from'] == url
    with pytest.raises(ByaxError) as caught:
        language_packs.install_pack_from_url('http://example.com/pack.byaxpack', digest)
    assert caught.value.code == 'invalid_input'


# Interface strings --------------------------------------------------------------------------------------------
def strings_pack(tmp_path):
    return make_pack(
        tmp_path,
        pack_id='xx-ui',
        grammar_rules=None,
        strings={'Save': 'Guardar', 'Run {0}': 'Ejecutar', 'Open…': 'Abrir…', 'Close': ''},
    )


def test_a_strings_pack_serves_a_translator_and_rejects_changed_placeholders(tmp_path):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    language_packs.install_pack(strings_pack(tmp_path))
    translator = language_packs.install_translator(app, 'xx')
    try:
        assert QCoreApplication.translate('Anything', 'Save') == 'Guardar'
        assert QCoreApplication.translate('Anything', 'Open…') == 'Abrir…'
        assert QCoreApplication.translate('Anything', 'Run {0}') == 'Run {0}'  # lost its placeholder: ignored
        assert QCoreApplication.translate('Anything', 'Close') == 'Close'  # empty means untranslated
        assert QCoreApplication.translate('Anything', 'Unknown text') == 'Unknown text'
    finally:
        app.removeTranslator(translator)
    assert language_packs.install_translator(app, 'en') is None
    assert language_packs.install_translator(app, 'zz') is None


def test_coverage_counts_usable_translations_against_the_source_catalogue(tmp_path):
    catalog = {'strings': ['Save', 'Run {0}', 'Open…', 'Close', 'Print'], 'dynamic_call_sites': 3}
    language_packs.install_pack(strings_pack(tmp_path))
    english = language_packs.translation_coverage('en', catalog)
    assert (english['total'], english['translated'], english['percent']) == (5, 5, 100.0)
    spanish = language_packs.translation_coverage('xx', catalog)
    assert (spanish['total'], spanish['translated'], spanish['percent']) == (5, 2, 40.0)
    assert spanish['placeholder_mismatches'] == ['Run {0}']
    broken = language_packs.translation_coverage('yy', catalog)
    assert broken['translated'] == 0 and broken['percent'] == 0.0
    report = {r['language']: r for r in language_packs.coverage_report()}
    assert report['en']['percent'] == 100.0 and report['xx']['translated'] > 0


def test_the_shipped_catalogue_is_current_enough_and_english_is_the_only_complete_language():
    catalog = language_packs.source_catalog()
    fresh = language_packs.extract_sources(ROOT / 'src' / 'byax')
    stale = set(fresh['strings']) ^ set(catalog['strings'])
    assert catalog['strings'] == sorted(catalog['strings']) and len(catalog['strings']) > 500
    assert len(stale) <= 0.05 * len(fresh['strings']), 'run: uv run python scripts/extract_strings.py --write'
    assert 'Languages and grammar' in catalog['strings']
    report = {r['language']: r for r in language_packs.coverage_report()}
    assert set(report) == {'en'}  # no non-English interface strings ship, and none are claimed


def test_the_translation_document_reports_the_same_coverage():
    text = (ROOT / 'docs' / 'translations.md').read_text(encoding='utf-8')
    assert re.search(r'\|\s*English \(en\)\s*\|\s*100%\s*\|', text)
    quoted = int(re.search(r'([\d,]+) interface texts', text).group(1).replace(',', ''))
    actual = len(language_packs.source_catalog()['strings'])
    assert abs(quoted - actual) <= 0.05 * actual, 'update the count in docs/translations.md'


# Interfaces ---------------------------------------------------------------------------------------------------
def test_cli_list_verify_install_coverage_and_remove(tmp_path):
    listing = execute('languages', {})['packs']
    assert [p['id'] for p in listing if not p['built_in']] == []
    pack = make_pack(tmp_path)
    archive, digest = archive_of(pack, tmp_path)
    installed = execute('languages', {'action': 'install', 'pack_file': str(archive), 'sha256': digest})[
        'pack'
    ]
    assert installed['id'] == 'xx-core'
    detail = execute('languages', {'action': 'list', 'verbose': True})['packs']
    assert any(p['id'] == 'xx-core' and p['files'][0]['sha256'] for p in detail)
    verified = execute('languages', {'action': 'verify'})
    assert verified['validation'] == 'passed' and all(r['ok'] for r in verified['verification'])
    coverage = execute('languages', {'action': 'coverage', 'language': 'en'})['translation_coverage']
    assert coverage[0]['percent'] == 100.0
    assert execute('languages', {'action': 'remove', 'id': 'xx-core'})['removed'] is True
    for arguments in ({'action': 'install'}, {'action': 'remove'}, {'action': 'nope'}):
        with pytest.raises(ByaxError) as caught:
            execute('languages', arguments)
        assert caught.value.code == 'invalid_input'


def test_install_and_remove_are_refused_over_mcp_and_rest_but_listing_is_not(tmp_path):
    from byax.transport_policy import check_provider_admin

    for action in ('install', 'remove'):
        with pytest.raises(ByaxError) as caught:
            check_provider_admin('languages', {'action': action})
        assert caught.value.code == 'unsupported_feature'
    for action in ('list', 'verify', 'coverage'):
        check_provider_admin('languages', {'action': action})
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {
                'name': 'byax_languages',
                'arguments': {'action': 'install', 'pack_file': str(tmp_path / 'x')},
            },
        }
    )['result']
    assert response['isError'] and response['structuredContent']['error']['code'] == 'unsupported_feature'
    listed = handle(
        {
            'jsonrpc': '2.0',
            'id': 4,
            'method': 'tools/call',
            'params': {'name': 'byax_languages', 'arguments': {}},
        }
    )['result']
    assert not listed['isError'] and listed['structuredContent']['packs']


def test_pack_archives_are_deterministic(tmp_path):
    pack = make_pack(tmp_path)
    first = language_packs.pack_archive(pack, tmp_path / 'a.byaxpack')
    second = language_packs.pack_archive(pack, tmp_path / 'b.byaxpack')
    assert first == second == hashlib.sha256((tmp_path / 'a.byaxpack').read_bytes()).hexdigest()
