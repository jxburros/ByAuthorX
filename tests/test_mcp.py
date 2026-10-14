import io
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from byax.errors import ByaxError
from byax.mcp import handle, run
from byax.service import AccessPolicy, make_server


def call(method, params=None, id=1):
    return handle({'jsonrpc': '2.0', 'id': id, 'method': method, 'params': params or {}})


def test_initialization_discovery_and_call(tmp_path):
    initialized = call('initialize')['result']
    assert initialized['capabilities']['tools'] == {'listChanged': False}
    tools = call('tools/list')['result']['tools']
    names = {tool['name']: tool for tool in tools}
    assert {'byax_apply', 'byax_transcribe', 'byax_jobs', 'byax_export'} <= set(names)
    assert names['byax_apply']['inputSchema']['required'] == ['path', 'input']
    path = str(tmp_path / 'mcp.byax')
    result = call('tools/call', {'name': 'byax_new', 'arguments': {'out': path, 'text': 'MCP content'}})[
        'result'
    ]
    assert not result['isError']
    payload = json.loads(result['content'][0]['text'])
    assert payload['outputs'] == [path]
    invalid = call('tools/call', {'name': 'byax_apply', 'arguments': {'path': path}})['result']
    assert invalid['isError']
    assert json.loads(invalid['content'][0]['text'])['error']['code'] == 'invalid_input'


def test_protocol_errors_notifications_and_framing():
    assert handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    assert call('unknown')['error']['code'] == -32601
    assert handle([])['error']['code'] == -32600
    request = json.dumps({'jsonrpc': '2.0', 'id': 4, 'method': 'ping'}).encode()
    output = io.BytesIO()
    assert run(io.BytesIO(request + b'\n'), output) == 0
    assert json.loads(output.getvalue()) == {'jsonrpc': '2.0', 'id': 4, 'result': {}}
    output = io.BytesIO()
    assert run(io.BytesIO(f'Content-Length: {len(request)}\r\n\r\n'.encode() + request), output) == 0
    assert output.getvalue().startswith(b'Content-Length: ')
    assert json.loads(output.getvalue().split(b'\r\n\r\n')[1])['id'] == 4
    output = io.BytesIO()
    run(io.BytesIO(b'not JSON\n'), output)
    assert json.loads(output.getvalue())['error']['code'] == -32700


@pytest.mark.parametrize('nested', [False, True])
def test_mcp_and_rest_refuse_provider_administration_without_writes(tmp_path, nested):
    command = 'providers'
    args = {
        'action': 'configure',
        'config_dir': str(tmp_path / 'config'),
        'input': {'alias': 'w', 'kind': 'whisper.cpp', 'model': '/tmp/model', 'executable': '/usr/bin/touch'},
    }
    if nested:
        command, args = (
            'jobs',
            {
                'action': 'submit',
                'root': str(tmp_path / 'jobs'),
                'input': {'command': command, 'arguments': args},
            },
        )
    result = call('tools/call', {'name': 'byax_' + command, 'arguments': args})['result']
    assert result['isError']
    assert json.loads(result['content'][0]['text'])['error']['code'] == 'unsupported_feature'
    with pytest.raises(ByaxError) as error:
        AccessPolicy([tmp_path]).check(command, args)
    assert error.value.code == 'unsupported_feature'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('transport', ['cli', 'mcp', 'rest'])
def test_transport_settings_cannot_remove_protection_in_a_separate_call(tmp_path, transport):
    from byax import engine, model, storage
    from byax.cli import execute

    doc = model.new_document(text='Protected')
    target = doc['blocks'][0]['id']
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'protect', 'target': target, 'kind': 'text'},
                {'type': 'set_settings', 'values': {'track_changes': True}},
            ]
        },
    )
    path = tmp_path / 'protected.byax'
    storage.save_document(doc, path)
    for op in (
        {'type': 'set_settings', 'values': {'protections': []}},
        {'type': 'set_settings', 'values': {'track_changes': False}},
        {'type': 'unprotect', 'target': target, 'kind': 'text'},
        {'type': 'set_text', 'target': target, 'text': 'Hacked'},
    ):
        args = {'path': str(path), 'input': {'origin': 'gui', 'operations': [op]}}
        if transport == 'mcp':
            result = call('tools/call', {'name': 'byax_apply', 'arguments': args})['result']
            assert result['isError']
            assert json.loads(result['content'][0]['text'])['error']['code'] == 'protected_edit'
        else:
            if transport == 'rest':
                AccessPolicy([tmp_path]).check('apply', args)
            with pytest.raises(ByaxError) as error:
                execute('apply', args)
            assert error.value.code == 'protected_edit'
        assert storage.load_document(path) == doc


def test_service_policy_rejects_escaping_symlink_and_nested_paths(tmp_path):
    root = tmp_path / 'authorized'
    root.mkdir()
    outside = tmp_path / 'private'
    outside.mkdir()
    (root / 'link').symlink_to(outside, target_is_directory=True)
    policy = AccessPolicy([root])
    policy.check('new', {'out': str(root / 'okay.byax')})
    for path in (outside / 'bad.byax', root / 'link' / 'bad.byax'):
        with pytest.raises(ByaxError):
            policy.check('new', {'out': str(path)})
    with pytest.raises(ByaxError):
        policy.check('batch', {'input': {'items': [{'path': str(outside / 'secret.byax')}]}})
    with pytest.raises(ByaxError):
        policy.check(
            'jobs',
            {'action': 'submit', 'input': {'command': 'providers', 'arguments': {'action': 'configure'}}},
        )


def test_service_owns_provider_and_job_state_folders(tmp_path):
    root = tmp_path / 'authorized'
    (root / 'planted').mkdir(parents=True)
    policy = AccessPolicy([root])
    state = (root / '.byax').resolve()
    args = {'action': 'list'}
    policy.check('providers', args)
    assert args['config_dir'] == str(state / 'config')
    # A client-written providers.json elsewhere in the root cannot be selected.
    for command, key in (
        ('providers', 'config_dir'),
        ('transcribe', 'config_dir'),
        ('transcribe', 'job_root'),
        ('jobs', 'root'),
    ):
        with pytest.raises(ByaxError):
            policy.check(command, {'action': 'list', key: str(root / 'planted')})
    with pytest.raises(ByaxError):
        policy.check(
            'jobs',
            {
                'action': 'submit',
                'input': {
                    'command': 'providers',
                    'arguments': {'action': 'test', 'alias': 'x', 'config_dir': str(root / 'planted')},
                },
            },
        )
    # Clients cannot write into the reserved state folder either.
    for out in (state / 'config' / 'providers.json', state, state / 'jobs' / 'x' / 'job.json'):
        with pytest.raises(ByaxError):
            policy.check('export', {'path': str(root / 'd.byax'), 'out': str(out)})
    nested = {'action': 'submit', 'input': {'command': 'providers', 'arguments': {'action': 'list'}}}
    policy.check('jobs', nested)
    assert nested['root'] == str(state / 'jobs')
    assert nested['input']['arguments']['config_dir'] == str(state / 'config')


def test_opt_in_rest_auth_and_actual_request(tmp_path):
    token = 'test-access-token-with-32-characters'
    with pytest.raises(ByaxError):
        make_server([tmp_path], token=token, host='0.0.0.0')
    with pytest.raises(ByaxError):
        make_server([tmp_path], token='short')
    server = make_server([tmp_path], token=token, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}/v1/execute'
    try:
        body = json.dumps(
            {'command': 'new', 'arguments': {'out': str(tmp_path / 'rest.byax'), 'text': 'REST'}}
        ).encode()
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url, data=body, method='POST'), timeout=5)
        assert exc.value.code == 401
        req = Request(url, data=body, headers={'Authorization': 'Bearer ' + token}, method='POST')
        with urlopen(req, timeout=5) as response:
            assert json.load(response)['exit_code'] == 0
        assert (tmp_path / 'rest.byax').is_file()
        render_body = json.dumps(
            {
                'command': 'render',
                'arguments': {'path': str(tmp_path / 'rest.byax'), 'out': str(tmp_path / 'http-render')},
            }
        ).encode()
        render_request = Request(
            url, data=render_body, headers={'Authorization': 'Bearer ' + token}, method='POST'
        )
        with urlopen(render_request, timeout=10) as response:
            rendered = json.load(response)
            assert rendered['exit_code'] == 0
            assert len(rendered['outputs']) == 1
        assert (tmp_path / 'http-render' / 'page-0001.png').is_file()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_mcp_skips_blank_lines_and_keeps_framing_for_parse_errors():
    import io

    from byax.mcp import run

    ping = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}).encode()
    out = io.BytesIO()
    run(io.BytesIO(b'\n' + ping + b'\n'), out)
    assert [json.loads(line)['id'] for line in out.getvalue().splitlines()] == [1]
    out = io.BytesIO()
    run(io.BytesIO(b'Content-Length: 3\r\n\r\n{x}'), out)
    header, body = out.getvalue().split(b'\r\n\r\n', 1)
    assert header.startswith(b'Content-Length: ')
    assert json.loads(body)['error']['code'] == -32700
