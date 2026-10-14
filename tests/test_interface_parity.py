"""CLI, library and MCP expose the same commands, schemas, envelopes and error classes."""

import json

import pytest

from byax import cli
from byax.cli import ARGUMENTS, COMMANDS, REQUIRED, build_parser, command_schema, error_result, execute, main
from byax.errors import ByaxError
from byax.mcp import handle

ENVELOPE_KEYS = {
    'schema_version',
    'execution',
    'validation',
    'review',
    'document_id',
    'base_revision',
    'revision',
    'changed_ids',
    'findings',
    'coverage',
    'outputs',
}


def _mcp(name, arguments):
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_' + name.replace('-', '_'), 'arguments': arguments},
        }
    )
    result = response['result']
    return json.loads(result['content'][0]['text']), result['isError']


def _cli(capsys, argv):
    code = main(argv)
    return json.loads(capsys.readouterr().out), code


def _library(name, arguments):
    try:
        return execute(name, arguments)
    except Exception as exc:  # noqa: BLE001 - mirror the transport boundary
        return error_result(exc)[0]


def test_every_command_has_handler_schema_parser_and_tool():
    assert set(cli._HANDLERS) | {'transact'} == set(COMMANDS) == set(ARGUMENTS)
    assert set(REQUIRED) <= set(COMMANDS)
    parser_commands = set(build_parser()._subparsers._group_actions[0].choices)
    assert set(COMMANDS) <= parser_commands
    tools = handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    by_name = {tool['name']: tool for tool in tools}
    for name in COMMANDS:
        tool = by_name['byax_' + name.replace('-', '_')]
        assert tool['inputSchema'] == command_schema(name)
        assert set(tool['inputSchema']['properties']) == set(ARGUMENTS[name])


def test_transact_alias_and_unknown_dispatch(tmp_path):
    path = str(tmp_path / 'alias.byax')
    execute('new', {'out': path, 'text': 'Alias'})
    request = {'operations': [{'type': 'set_metadata', 'values': {'title': 'Alias'}}]}
    dry_apply = execute('apply', {'path': path, 'input': request, 'dry_run': True})
    dry_transact = execute('transact', {'path': path, 'input': request, 'dry_run': True})
    assert dry_apply['execution'] == dry_transact['execution'] == 'dry_run'
    assert dry_apply['revision'] == dry_transact['revision']
    with pytest.raises(ByaxError) as caught:
        cli._execute('not-a-command', {})
    assert caught.value.code == 'invalid_input'


def test_envelopes_and_errors_match_across_cli_library_and_mcp(tmp_path, capsys):
    path = str(tmp_path / 'parity.byax')
    created, code = _cli(capsys, ['new', '--out', path, '--text', 'Parity café'])
    assert code == 0 and ENVELOPE_KEYS <= set(created)
    cases = [
        ('version', {}, ['version']),
        ('inspect', {'path': path, 'limit': 5}, ['inspect', path, '--limit', '5']),
        ('find', {'path': path, 'query': 'café'}, ['find', path, '--query', 'café']),
        ('history', {'path': path}, ['history', path]),
        ('diff', {'path': path, 'from_revision': 'rev_missing'}, ['diff', path, '--from', 'rev_missing']),
        (
            'history',
            {'path': path, 'action': 'checkpoint', 'expect_revision': 'rev_stale', 'name': 'x'},
            ['history', path, '--action', 'checkpoint', '--expect-revision', 'rev_stale', '--name', 'x'],
        ),
        ('history', {'path': path, 'action': 'bogus'}, ['history', path, '--action', 'bogus']),
        ('inspect', {'path': str(tmp_path / 'missing.byax')}, ['inspect', str(tmp_path / 'missing.byax')]),
    ]
    for name, arguments, argv in cases:
        library = _library(name, arguments)
        over_mcp, is_error = _mcp(name, arguments)
        over_cli, exit_code = _cli(capsys, argv)
        for payload in (library, over_mcp, over_cli):
            assert ENVELOPE_KEYS <= set(payload), (name, payload)
        assert library == over_mcp == over_cli, name
        assert is_error == bool(exit_code), name
        if 'error' in library:
            assert library['execution'] == 'failed'
            assert exit_code == library['error'].get('exit_code', exit_code)
            assert exit_code in (1, 2, 3, 4, 5)


def test_validation_errors_are_identical_across_transports(capsys):
    library = _library('find', {'query': 'x'})
    over_mcp, is_error = _mcp('find', {'query': 'x'})
    assert library == over_mcp and is_error
    assert library['error']['code'] == 'invalid_input'
    assert library['error']['field'] == 'path'
    unknown = _library('version', {'surprise': True})
    assert unknown['error']['code'] == 'invalid_input' and unknown['error']['field'] == 'surprise'
    over_cli, code = _cli(capsys, ['find', '--query', 'x'])
    assert code == 2 and over_cli['error']['code'] == 'invalid_input'
