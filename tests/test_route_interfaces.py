"""MCP and CLI parity for the context engine 8.5 tools: route, outline, verify-citations, summaries,
and the existing library-search (with method), project-search and entity tools."""

import importlib.util
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from byax.cli import COMMANDS, command_schema, execute, main
from byax.errors import ByaxError
from byax.mcp import handle
from byax.service import make_server
from byax.storage import save_document
from byax.transport_policy import INTERFACE_INVENTORY

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'route-parity-token-with-32-characters!'
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


def _benchmark():
    spec = importlib.util.spec_from_file_location(
        'router_benchmark', ROOT / 'scripts' / 'router_benchmark.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCH = _benchmark()


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))


@pytest.fixture
def path(tmp_path):
    (tmp_path / 'project').mkdir()
    target = tmp_path / 'project' / 'novel.byax'
    save_document(BENCH.fixture_document(), target)
    return str(target)


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


def _mcp(command, arguments):
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {'name': 'byax_' + command.replace('-', '_'), 'arguments': arguments},
        }
    )['result']
    return response


def _tools():
    listed = handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    return {tool['name']: tool for tool in listed}


def test_mcp_lists_the_read_only_context_tools():
    tools = _tools()
    for name in ('byax_route', 'byax_outline', 'byax_verify_citations', 'byax_entity', 'byax_library_search'):
        assert tools[name]['annotations']['readOnlyHint'] is True, name
    assert 'byax_project_search' in tools and 'byax_summaries' in tools
    assert tools['byax_summaries']['annotations']['openWorldHint'] is True
    assert 'method' in tools['byax_library_search']['inputSchema']['properties']
    assert {'depth', 'counts', 'binder', 'limit', 'offset'} <= set(command_schema('outline')['properties'])
    assert command_schema('route')['required'] == ['path', 'request']
    commands = {row[2].split()[0] for row in INTERFACE_INVENTORY if row[2]}
    assert {'route', 'outline', 'verify-citations', 'summaries'} <= commands <= set(COMMANDS)


def test_results_match_across_library_cli_mcp_and_rest(path, rest, capsys):
    for command, arguments in (
        ('outline', {'path': path, 'counts': True, 'binder': True, 'depth': 1}),
        ('route', {'path': path, 'request': 'Where does Tom keep the brass key?'}),
        ('route', {'path': path, 'request': 'Find every place Tom lies to Mara.', 'inspect': True}),
        ('verify-citations', {'path': path}),
        ('verify-citations', {'path': path, 'reply': 'The key hung on a nail.'}),
        ('summaries', {'path': path}),
        ('entity', {'path': path, 'name': 'Tom'}),
        ('project-search', {'path': path, 'query': 'brass key'}),
    ):
        library = _normalize(execute(command, arguments))
        mcp = _mcp(command, arguments)
        status, body = rest(command, arguments)
        flags = []
        for key, value in arguments.items():
            if key == 'path':
                continue
            flag = '--' + key.replace('_', '-')
            flags += [flag] if value is True else [flag, str(value)]
        code = main([command, path, *flags])
        cli = json.loads(capsys.readouterr().out)
        assert code == 0 == mcp['structuredContent']['exit_code'] == body['exit_code'], command
        assert status == 200
        normalized = (_normalize(cli), _normalize(mcp['structuredContent']), _normalize(body))
        if command == 'summaries':
            # The cache path names each process's user cache; everything else matches.
            for value in (library, *normalized):
                value.pop('cache', None)
        assert normalized[0] == normalized[1] == normalized[2] == library, command


def test_route_explains_without_calling_any_model(path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('route must never call a model')

    monkeypatch.setattr('byax.providers.ProviderRegistry.chat', refuse)
    monkeypatch.setattr('byax.providers.ProviderRegistry._request', refuse)
    explained = execute('route', {'path': path, 'request': 'Summarize the whole book.'})
    assert explained['route']['route'] == 'R4' and explained['sent'] is False
    inspected = execute(
        'route', {'path': path, 'request': 'Tighten this.', 'target': 'c1s1p2', 'inspect': True}
    )
    inspector = inspected['inspector']
    assert inspector['route']['route'] == 'R0' and inspector['items'] and not inspector['sent']
    forced = execute(
        'route', {'path': path, 'request': 'Tighten this.', 'target': 'c1s1p2', 'override': 'R4'}
    )
    assert forced['route']['route'] == 'R4' and forced['route']['suggested']['route'] == 'R0'
    paged = execute(
        'route', {'path': path, 'request': 'Find every place Tom lies.', 'inspect': True, 'limit': 2}
    )
    page = paged['inspector']['items_page']
    assert len(paged['inspector']['items']) == 2 and page['truncated'] and page['continuation'] == 2
    with pytest.raises(ByaxError):
        execute('route', {'path': path, 'request': 'x', 'override': 'R7'})


def test_outline_and_citations_are_bounded_with_continuations(path):
    first = execute('outline', {'path': path, 'limit': 5})
    assert len(first['items']) == 5 and first['truncated'] and first['continuation'] == 5
    rest_page = execute('outline', {'path': path, 'limit': 50, 'offset': first['continuation']})
    assert rest_page['offset'] == 5 and not rest_page['truncated']
    assert first['total'] == 12 == len(first['items']) + len(rest_page['items'])
    deep = execute('outline', {'path': path, 'depth': 1, 'counts': True})
    assert [row['text'] for row in deep['items']] == [
        'Chapter One',
        'Chapter Two',
        'Chapter Three',
        'Chapter Four',
    ]
    assert sum(row['total_words'] for row in deep['items']) == deep['words']
    reply = 'He hid it [[cite:A:c1s2p2]] and again [[cite:A:c3s1p2]] and "a silver key" [[cite:A:c1s2p2]].'
    passages = [{'target': 'c1s2p2', 'chunk_id': 'A:c1s2p2'}, {'target': 'c3s1p2', 'chunk_id': 'A:c3s1p2'}]
    verified = execute(
        'verify-citations', {'path': path, 'input': {'reply': reply, 'passages': passages}, 'limit': 2}
    )
    assert verified['page']['total'] == 3 and verified['page']['continuation'] == 2
    states = [r['state'] for r in verified['records']]
    assert states == ['not_checkable', 'not_checkable']
    last = execute(
        'verify-citations', {'path': path, 'input': {'reply': reply, 'passages': passages}, 'offset': 2}
    )
    assert last['records'][0]['state'] == 'unsupported'  # "a silver key" is not in the cited block


def test_verify_citations_reads_source_text_from_the_index_not_the_request(path):
    fake = [{'target': 'c1s2p2', 'chunk_id': 'A:c1s2p2', 'text': 'a silver key'}]
    reply = '"a silver key" [[cite:A:c1s2p2]]'
    result = execute('verify-citations', {'path': path, 'input': {'reply': reply, 'passages': fake}})
    assert result['records'][0]['state'] == 'unsupported'
    with pytest.raises(ByaxError):
        execute('verify-citations', {'path': path, 'input': {'reply': reply, 'other': 1}})


def test_library_search_keeps_its_method_parameter(path, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config-home'))
    folder = tmp_path / 'research'
    folder.mkdir()
    (folder / 'notes.txt').write_text('The lighthouse lens was ground in Paris.', encoding='utf-8')
    created = execute(
        'library', {'path': path, 'action': 'create', 'input': {'name': 'Research', 'roots': [str(folder)]}}
    )
    library_id = created['changed_ids'][0] if created['changed_ids'] else None
    from byax.storage import load_document

    library_id = load_document(path)['settings']['libraries'][0]['library_id']
    execute('library', {'path': path, 'action': 'refresh', 'library_id': library_id})
    found = execute(
        'library-search', {'path': path, 'library_id': library_id, 'query': 'lens', 'method': 'keyword'}
    )
    assert found['method'] == 'keyword' and found['results']
    mcp = _mcp(
        'library-search', {'path': path, 'library_id': library_id, 'query': 'lens', 'method': 'hybrid'}
    )
    assert mcp['structuredContent']['method'] == 'keyword' and 'notice' in mcp['structuredContent']
    refused = _mcp('library', {'path': path, 'action': 'embedding-configure', 'library_id': library_id})
    assert refused['isError'] and refused['structuredContent']['error']['code'] == 'unsupported_feature'
