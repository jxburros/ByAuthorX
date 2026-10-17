"""``bibliography`` and ``excerpt`` through the library, CLI, MCP and REST (interface parity and policy)."""

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx
import pytest
from bibliography_fixtures import ReplayTransport
from test_zotero import local_api

from byax import cli_bibliography, engine, model
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.mcp import handle
from byax.providers import ProviderConfig, ProviderRegistry
from byax.service import make_server

TOKEN = 'bibliography-test-token-with-32-chars'
IEEE = (Path(__file__).parent / 'fixtures' / 'csl' / 'ieee.csl').read_bytes()
NATURE = '10.1038/nature14539'
LANCET = '10.1016/S0140-6736(97)11096-0'
VOLATILE = {
    'revision',
    'base_revision',
    'document_id',
    'exit_code',
    'outputs',
    'id',
    'created_at',
    'updated_at',
}


class Everything(httpx.BaseTransport):
    """Recorded registry answers, the CSL repository and a fake Zotero, chosen by host."""

    def __init__(self):
        self.registry = ReplayTransport()
        self.zotero = local_api()
        self.requests = []

    def handle_request(self, request):
        self.requests.append((request.method, str(request.url)))
        if request.url.host == 'localhost':
            return self.zotero.handle_request(request)
        if request.url.host == 'raw.githubusercontent.com':
            name = request.url.path.rsplit('/', 1)[-1]
            return httpx.Response(200, content=IEEE) if name == 'ieee.csl' else httpx.Response(404)
        return self.registry.handle_request(request)


@pytest.fixture
def network(monkeypatch):
    transport = Everything()
    monkeypatch.setattr(cli_bibliography, 'TRANSPORT', transport)
    monkeypatch.setattr(cli_bibliography, 'LOOKUP_OPTIONS', {'sleep': lambda _: None})
    return transport


@pytest.fixture
def rest(tmp_path):
    server = make_server([tmp_path], token=TOKEN, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/v1/execute'

    def call(command, arguments):
        request = Request(
            url,
            data=json.dumps({'command': command, 'arguments': arguments}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + TOKEN},
        )
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    yield call
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def mcp(command, arguments):
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_' + command.replace('-', '_'), 'arguments': arguments},
        }
    )['result']
    return response['structuredContent'], response['isError']


def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def cli(capsys, *args):
    code = main(list(args))
    return code, json.loads(capsys.readouterr().out)


def source(ident, title, families, year, **csl):
    return {
        'id': ident,
        'csl': {
            'id': ident,
            'type': 'article-journal',
            'title': title,
            'author': [{'family': f} for f in families],
            'issued': {'date-parts': [[year]]},
            **csl,
        },
        'verification': 'unconfirmed',
    }


@pytest.fixture
def document(tmp_path):
    path = str(tmp_path / 'refs.byax')
    execute('new', {'out': path, 'text': 'Deep nets work (LeCun, 2015). Unlisted claim (Brown, 2018).'})
    operations = [
        {
            'type': 'add_reference',
            'id': 'nature',
            'values': source('nature', 'Deep learning', ['LeCun'], 2015, DOI=NATURE),
        },
        {
            'type': 'add_reference',
            'id': 'lancet',
            'values': source(
                'lancet',
                'Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children',
                ['Wakefield'],
                1998,
                DOI=LANCET,
            ),
        },
        {
            'type': 'add_reference',
            'id': 'bad',
            'values': source('bad', 'Bad DOI', ['Nobody'], 2020, DOI='doi:10.12/x'),
        },
    ]
    execute('apply', {'path': path, 'input': {'operations': operations}})
    return path


# --- parity of the read-only parts ----------------------------------------------------------------------


@pytest.mark.parametrize(
    'arguments',
    [
        {'action': 'health'},
        {'action': 'health', 'checks': ['doi', 'citations']},
        {'action': 'status'},
        {'action': 'ris-export'},
        {'action': 'export-bib'},
    ],
)
def test_read_only_actions_return_the_same_envelope_everywhere(document, rest, capsys, arguments):
    arguments = {'path': document, **arguments}
    library = normalize(execute('bibliography', arguments))
    structured, error = mcp('bibliography', arguments)
    status, body = rest('bibliography', arguments)
    assert not error and status == 200
    assert normalize(structured) == normalize(body) == library
    flags = []
    for key, value in arguments.items():
        if key in ('path', 'checks'):
            continue
        flags += [] if value is True else ['--' + key.replace('_', '-'), str(value)]
    for check in arguments.get('checks', []):
        flags += ['--checks', check]
    code, cli_result = cli(capsys, 'bibliography', document, *flags)
    assert code == 0 and normalize(cli_result) == library


def test_documents_free_actions_need_no_path_and_are_listed_over_every_transport(rest, capsys):
    library = normalize(execute('bibliography', {'action': 'styles-list'}))
    structured, error = mcp('bibliography', {'action': 'styles-list'})
    status, body = rest('bibliography', {'action': 'styles-list'})
    assert not error and status == 200 and normalize(structured) == normalize(body) == library
    code, cli_result = cli(capsys, 'bibliography', '--action', 'styles-list')
    assert code == 0 and normalize(cli_result) == library
    assert [s['name'] for s in library['styles']] == ['apa', 'mla', 'chicago']
    assert 'CC BY-SA 3.0' in library['attribution']
    with pytest.raises(ByaxError, match='path'):
        execute('bibliography', {'action': 'health'})


def test_excerpt_where_used_is_the_same_everywhere(document, rest, capsys):
    arguments = {'path': document, 'action': 'where-used', 'include_unused': True}
    library = normalize(execute('excerpt', arguments))
    structured, error = mcp('excerpt', arguments)
    status, body = rest('excerpt', arguments)
    assert not error and status == 200 and normalize(structured) == normalize(body) == library
    code, cli_result = cli(capsys, 'excerpt', document, '--action', 'where-used', '--include-unused')
    assert code == 0 and normalize(cli_result) == library
    assert sorted(library['unused']) == ['bad', 'lancet', 'nature']


# --- policy over MCP and REST -------------------------------------------------------------------------------


REFUSED = [
    ('bibliography', {'action': 'health', 'online': True}),
    ('bibliography', {'action': 'health', 'archive': True}),
    ('bibliography', {'action': 'health', 'allow_network': True}),
    ('bibliography', {'action': 'consent'}),
    ('bibliography', {'action': 'configure', 'input': {'mailto': 'x@example.org'}}),
    ('bibliography', {'action': 'retractions-import', 'source_path': 'rw.csv'}),
    ('bibliography', {'action': 'styles-preview', 'name': 'ieee'}),
    ('bibliography', {'action': 'styles-add', 'name': 'ieee', 'allow_network': True}),
    ('bibliography', {'action': 'styles-remove', 'name': 'ieee'}),
    ('bibliography', {'action': 'zotero-import'}),
    ('bibliography', {'action': 'zotero-collections'}),
    ('bibliography', {'action': 'clear-cache'}),
    ('check', {'citations': True, 'judge': 'someone'}),
]


@pytest.mark.parametrize(('command', 'arguments'), REFUSED)
def test_network_zotero_and_configuration_are_refused_over_mcp_and_rest(
    document, rest, network, command, arguments
):
    arguments = {'path': document, **arguments}
    structured, error = mcp(command, arguments)
    assert error and structured['error']['code'] == 'unsupported_feature'
    status, body = rest(command, arguments)
    assert status >= 400 and body['error']['code'] == 'unsupported_feature'
    assert network.requests == []  # nothing was fetched
    # A job cannot carry the refused request around the policy either.
    job = {'path': document, 'action': 'submit', 'input': {'command': command, 'arguments': arguments}}
    structured, error = mcp('jobs', {k: v for k, v in job.items() if k != 'path'})
    assert error


# --- online health -----------------------------------------------------------------------------------------------


def test_online_health_needs_permission_then_checks_against_recorded_answers(document, network, capsys):
    arguments = {'path': document, 'action': 'health', 'online': True}
    with pytest.raises(ByaxError) as caught:
        execute('bibliography', arguments)
    assert caught.value.code == 'authorization_required' and 'crossref' in caught.value.details['services']
    assert network.requests == []
    result = execute('bibliography', {**arguments, 'allow_network': True})
    report = result['report']
    assert report['network']['mode'] == 'online' and report['network']['requests'] > 0
    assert (
        report['summary']['retracted'] == 1
        and result['validation'] == 'failed'
        and result['review'] == 'required'
    )
    statuses = {(f['source'], f['check'], f['status']) for f in report['findings']}
    assert ('lancet', 'retractions', 'retracted') in statuses and ('bad', 'doi', 'invalid') in statuses
    assert any(line.startswith('3 sources') for line in result['lines'])
    # The exit code reports the failed requirement like `check` does.
    code, _ = cli(capsys, 'bibliography', document, '--action', 'health', '--online', '--allow-network')
    assert code == 4
    # Standing permission per service, granted by the person at this computer.
    execute(
        'bibliography', {'action': 'consent', 'input': {'services': ['crossref', 'datacite', 'openalex']}}
    )
    standing = execute('bibliography', arguments)
    assert standing['report']['summary']['retracted'] == 1
    status = execute('bibliography', {'path': document, 'action': 'status'})
    assert status['consent']['crossref'] is True and status['consent']['wayback'] is False
    # Later runs use the cache and make no new requests.
    count = len(network.requests)
    cached = execute('bibliography', {'path': document, 'action': 'health'})
    assert len(network.requests) == count and cached['report']['summary']['retracted'] == 1
    assert cached['report']['network']['mode'] == 'cache only'
    execute('bibliography', {'action': 'consent', 'input': {'allowed': False}})
    assert execute('bibliography', {'path': document, 'action': 'status'})['consent']['crossref'] is False
    assert execute('bibliography', {'action': 'clear-cache'})['removed'] > 0


def test_findings_are_paged_with_a_continuation(document):
    page = execute('bibliography', {'path': document, 'action': 'health', 'limit': 1})
    window = page['report']['findings_window']
    assert window['returned'] == 1 and window['truncated'] and window['continuation'] == 1
    rest_of_it = execute('bibliography', {'path': document, 'action': 'health', 'limit': 50, 'offset': 1})
    assert rest_of_it['report']['findings_window']['truncated'] is False
    assert rest_of_it['report']['findings'][0] != page['report']['findings'][0]


def test_the_contact_address_is_stored_only_when_configured(document):
    assert execute('bibliography', {'path': document, 'action': 'status'})['mailto_configured'] is False
    assert execute('bibliography', {'action': 'configure', 'input': {'mailto': 'me@example.org'}})[
        'mailto_configured'
    ]
    status = execute('bibliography', {'path': document, 'action': 'status'})
    assert status['mailto_configured'] is True and 'me@example.org' not in json.dumps(status)
    with pytest.raises(ByaxError):
        execute('bibliography', {'action': 'configure', 'input': {'password': 'x'}})


def test_a_retraction_watch_csv_is_imported_from_the_cli_and_used_offline(document, tmp_path, capsys):
    csv_file = tmp_path / 'rw.csv'
    csv_file.write_text(
        'Record ID,Title,OriginalPaperDOI,RetractionDOI,RetractionDate,RetractionNature,Reason\n'
        f'1,Paper,{NATURE},10.1/notice,1/2/2020,Retraction,+Error;\n',
        encoding='utf-8',
    )
    code, imported = cli(
        capsys, 'bibliography', '--action', 'retractions-import', '--source-path', str(csv_file)
    )
    assert code == 0 and imported['imported'] == 1
    status = execute('bibliography', {'action': 'retractions-status'})
    assert (
        status['retraction_index']['rows'] == '1'
        and 'OriginalPaperDOI' in status['expected_columns']['required']
    )
    report = execute('bibliography', {'path': document, 'action': 'health', 'checks': ['retractions']})[
        'report'
    ]
    assert [f['source'] for f in report['findings'] if f['status'] == 'retracted'] == ['nature']
    with pytest.raises(ByaxError, match='Retraction Watch'):
        bad = tmp_path / 'bad.csv'
        bad.write_text('a,b\n1,2\n')
        execute('bibliography', {'action': 'retractions-import', 'source_path': str(bad)})


# --- exchange ---------------------------------------------------------------------------------------------------------


def test_ris_export_and_import_round_trip_through_files(document, tmp_path, capsys):
    out = tmp_path / 'refs.ris'
    code, exported = cli(capsys, 'bibliography', document, '--action', 'ris-export', '--out', str(out))
    assert code == 0 and exported['count'] == 3 and out.read_text(encoding='utf-8').count('ER  - ') == 3
    with pytest.raises(ByaxError, match='already exists'):
        execute('bibliography', {'path': document, 'action': 'ris-export', 'out': str(out)})
    fresh = str(tmp_path / 'fresh.byax')
    execute('new', {'out': fresh, 'text': 'x'})
    imported = execute('bibliography', {'path': fresh, 'action': 'ris-import', 'source_path': str(out)})
    assert (
        imported['imported'] == 3
        and execute('inspect', {'path': fresh, 'section': 'references'})['total'] == 3
    )
    again = execute('bibliography', {'path': fresh, 'action': 'ris-import', 'source_path': str(out)})
    assert again['imported'] == 3
    assert execute('inspect', {'path': fresh, 'section': 'references'})['total'] == 3  # idempotent
    with pytest.raises(ByaxError):
        execute('bibliography', {'path': fresh, 'action': 'ris-import'})


def test_styles_are_previewed_downloaded_with_a_hash_and_used_for_rendering(document, network, capsys):
    shown = execute('bibliography', {'action': 'styles-preview', 'name': 'ieee'})
    assert (
        shown['installed'] is False
        and shown['url'].endswith('/master/ieee.csl')
        and len(shown['sha256']) == 64
    )
    assert 'data' not in shown
    with pytest.raises(ByaxError) as caught:
        execute('bibliography', {'action': 'styles-add', 'name': 'ieee'})
    assert (
        caught.value.code == 'authorization_required' and 'raw.githubusercontent.com' in caught.value.message
    )
    added = execute(
        'bibliography',
        {'action': 'styles-add', 'name': 'ieee', 'allow_network': True, 'expect_sha256': shown['sha256']},
    )
    assert added['installed'] and added['verified_against_preview'] and added['sha256'] == shown['sha256']
    names = [s['name'] for s in execute('bibliography', {'action': 'styles-list'})['styles']]
    assert names == ['apa', 'mla', 'chicago', 'ieee']
    rendered = execute('source', {'path': document, 'action': 'bibliography', 'style': 'ieee'})
    assert rendered['bibliography'] and 'LeCun' in ' '.join(rendered['bibliography'])
    with pytest.raises(ByaxError):
        execute(
            'bibliography', {'action': 'styles-add', 'name': 'ieee', 'allow_network': True}
        )  # already there
    assert execute(
        'bibliography', {'action': 'styles-add', 'name': 'ieee', 'allow_network': True, 'replace': True}
    )
    local = Path(document).with_name('My Local.csl')
    local.write_bytes(IEEE)
    code, result = cli(capsys, 'bibliography', '--action', 'styles-add', '--source-path', str(local))
    assert code == 0 and result['name'] == 'my-local'
    assert execute('bibliography', {'action': 'styles-remove', 'name': 'my-local'}) == execute(
        'bibliography', {'action': 'styles-remove', 'name': 'ieee'}
    ) | {'removed': 'my-local'}


def test_zotero_is_read_into_sources_and_exported_with_citation_keys(document, network, tmp_path, capsys):
    fresh = str(tmp_path / 'zot.byax')
    execute('new', {'out': fresh, 'text': 'x'})
    collections = execute('bibliography', {'action': 'zotero-collections'})['collections']
    assert collections == [{'key': 'COLL0001', 'name': 'Reading list', 'parent': None}]
    dry = execute('bibliography', {'path': fresh, 'action': 'zotero-import', 'dry_run': True})
    assert dry['zotero']['items'] == 3 and dry['sent'] is False
    assert execute('inspect', {'path': fresh, 'section': 'references'})['total'] == 0
    result = execute('bibliography', {'path': fresh, 'action': 'zotero-import'})
    assert result['zotero'] == {'items': 3, 'annotations': 3, 'skipped': 2, 'new': 3, 'updated': 0}
    assert execute('inspect', {'path': fresh, 'section': 'references'})['total'] == 3
    again = execute(
        'bibliography',
        {'path': fresh, 'action': 'zotero-import', 'collection': 'COLL0001', 'no_annotations': True},
    )
    assert again['zotero']['updated'] == 2
    out = tmp_path / 'library.bib'
    code, exported = cli(
        capsys, 'bibliography', fresh, '--action', 'export-bib', '--out', str(out), '--only-zotero'
    )
    assert code == 0 and exported['from_zotero'] == 2
    text = out.read_text(encoding='utf-8')
    assert '@article{lecun2015deep,' in text and '@book{kuhn_structure_2012,' in text
    assert all(method == 'GET' for method, _ in network.requests)
    with pytest.raises(ByaxError, match='only on this computer'):
        execute('bibliography', {'path': fresh, 'action': 'zotero-import', 'url': 'http://example.com:23119'})


# --- excerpts ------------------------------------------------------------------------------------------------------------


@pytest.fixture
def library_document(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    (root / 'notes.txt').write_text(
        'Aria is the captain.\n\nBren is the navigator of the long ship.', encoding='utf-8'
    )
    registry = LibraryRegistry(tmp_path / 'config')
    definition = registry.create('Research', [root])
    cache = tmp_path / 'cache'
    LibraryIndex(definition, cache).refresh()
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda *a, **k: registry)
    original = LibraryIndex
    monkeypatch.setattr(
        'byax.libraries.LibraryIndex', lambda library, cache_dir=None, **k: original(library, cache)
    )
    from byax.citations import source_from_hit

    hit = next(iter(LibraryIndex(definition, cache).search('Aria')['results']))
    cited = source_from_hit(hit)
    path = str(tmp_path / 'excerpts.byax')
    doc = model.new_document(text='Opening paragraph.')
    doc['settings']['libraries'] = [{'library_id': definition['library_id'], 'name': 'Research'}]
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'add_reference', 'id': cited['id'], 'values': cited}]}
    )
    from byax.storage import save_document

    save_document(doc, path)
    return path, cited['id'], hit, root, definition, cache


def test_excerpts_are_added_listed_checked_and_refreshed_as_proposals(library_document, capsys):
    path, ident, hit, root, definition, cache = library_document
    # Chunker v3 merges the two short paragraphs into one chunk, so the excerpt names its own span.
    added = execute(
        'excerpt',
        {'path': path, 'action': 'add', 'source': ident, 'chunk_id': hit['chunk_id'], 'start': 0, 'end': 20},
    )
    assert added['excerpt']['source'] == ident and added['excerpt']['characters'] == 20
    listed = execute('excerpt', {'path': path, 'action': 'list'})
    assert [e['state'] for e in listed['excerpts']] == ['anchored'] and listed['excerpts'][0][
        'text'
    ] == 'Aria is the captain.'
    used = execute('excerpt', {'path': path, 'action': 'where-used', 'target': ident})
    assert used['sources'][0]['counts'] == {'citations': 0, 'excerpts': 1}
    assert execute('excerpt', {'path': path, 'action': 'check'})['summary']['current'] == 1
    assert execute('excerpt', {'path': path, 'action': 'refresh'})['proposals'] == []
    (root / 'notes.txt').write_text(
        'Preface.\n\nAria is the captain!\n\nBren is the navigator of the long ship.', encoding='utf-8'
    )
    LibraryIndex(definition, cache).refresh()
    checked = execute('excerpt', {'path': path, 'action': 'check'})
    assert checked['summary']['source_changed'] == 1 and checked['review'] == 'required'
    before = execute('inspect', {'path': path})['revision']
    refreshed = execute('excerpt', {'path': path, 'action': 'refresh'})
    after = execute('inspect', {'path': path})
    assert refreshed['changed_ids'] and after['revision'] != before  # recorded as proposals
    text = [i['text'] for i in after['items']]
    assert (
        'Aria is the captain.' in text and 'Aria is the captain!' not in text
    )  # the text itself is unchanged
    pending = execute('review', {'path': path, 'action': 'list'})
    assert len(pending['items']['proposals']) == 2
    code, result = cli(capsys, 'excerpt', path, '--action', 'check')
    assert code == 0 and result['summary']['source_changed'] == 1
    with pytest.raises(ByaxError):
        execute('excerpt', {'path': path, 'action': 'add', 'chunk_id': hit['chunk_id']})
    with pytest.raises(ByaxError):
        execute('excerpt', {'path': path, 'action': 'bogus'})


def test_the_cli_judge_sends_low_confidence_citations_to_another_provider(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / 'judge.byax')
    doc = model.new_document(text='Help arrived from the mainland in good time.')
    doc['references']['ref'] = {
        'id': 'ref',
        'title': 'Logbook',
        'csl': {'id': 'ref', 'type': 'document', 'title': 'Logbook'},
        'excerpt': 'Mrs. Wren rowed the supplies across before the gale.',
        'verification': 'manual',
    }
    block = doc['blocks'][0]
    block['marks'] = [
        {
            'type': 'citation',
            'value': 'ref',
            'start': 0,
            'end': len(block['text']),
            'kind': 'paraphrase',
            'quote': block['text'],
        }
    ]
    from byax.storage import save_document

    save_document(doc, path)
    registry = ProviderRegistry()
    registry.save(ProviderConfig('judge', 'ollama', 'judge-model'))
    real = httpx.Client
    seen = []

    def handler(request):
        seen.append(json.loads(request.read()))
        verdict = {'verdict': 'partial', 'rationale': 'Supplies, not help.'}
        return httpx.Response(200, json={'message': {'content': json.dumps(verdict)}})

    monkeypatch.setattr(httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    plain = execute('check', {'path': path, 'citations': True})
    assert plain['deterministic'] is True and seen == []
    with pytest.raises(ByaxError, match='apply to --citations'):
        execute('check', {'path': path, 'judge': 'judge'})
    judged = execute('check', {'path': path, 'citations': True, 'judge': 'judge'})
    assert judged['citations'][0]['judge']['result'] == 'partial' and len(seen) == 1
    assert judged['citations'][0]['status'] == 'not_checkable' and judged['network'] is False
    code, via_cli = cli(capsys, 'check', path, '--citations', '--judge', 'judge')
    assert code == 0 and via_cli['citations'][0]['record']['state'] == 'partial'
