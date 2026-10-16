"""Spec 16.2 inventory, cross-interface result/error parity, exit states and the 16.10 workflow."""

import importlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from byax.cli import COMMANDS, execute, main
from byax.errors import ByaxError
from byax.mcp import handle
from byax.service import make_server
from byax.transport_policy import INTERFACE_INVENTORY

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = {
    'Discovery',
    'Files',
    'Inspection',
    'Search and targeting',
    'Editing',
    'Review',
    'History',
    'Checks',
    'Output',
    'Providers',
    'Transcription',
    'Jobs and batches',
}
TOKEN = 'parity-test-token-with-32-characters'
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


def _resolve(dotted):
    parts = dotted.split('.')
    for split in range(len(parts), 0, -1):
        try:
            value = importlib.import_module('.'.join(parts[:split]))
        except ModuleNotFoundError:
            continue
        for attribute in parts[split:]:
            value = getattr(value, attribute)
        return value
    raise AssertionError(dotted)


def test_inventory_covers_every_family_command_and_library_callable():
    assert {row[0] for row in INTERFACE_INVENTORY} == FAMILIES
    used = set()
    for family, operation, cli, library, gui in INTERFACE_INVENTORY:
        assert cli or gui, (family, operation)
        if cli:
            command = cli.split()[0]
            assert command in COMMANDS, (family, operation, command)
            used.add(command)
        if library:
            assert _resolve(library) is not None
    tools = {
        tool['name']
        for tool in handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    }
    assert tools == {'byax_' + name.replace('-', '_') for name in COMMANDS}
    assert used == set(COMMANDS), sorted(set(COMMANDS) ^ used)


def test_gui_labels_and_parity_document_match_inventory():
    # Menu entries may live in any of the focused gui_*.py modules, not only the main window.
    gui = ''.join(p.read_text(encoding='utf-8') for p in sorted((ROOT / 'src' / 'byax').glob('gui*.py')))
    document = (ROOT / 'docs' / 'interface-parity.md').read_text(encoding='utf-8')
    for family, operation, cli, library, label in INTERFACE_INVENTORY:
        assert f'| {family} | {operation} |' in document, operation
        if label and ' > ' in label and not label.startswith('View'):
            assert label.split(' > ', 1)[1] in gui, label


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


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
            'id': 7,
            'method': 'tools/call',
            'params': {'name': 'byax_' + command.replace('-', '_'), 'arguments': arguments},
        }
    )['result']
    assert json.loads(response['content'][0]['text']) == {
        k: v for k, v in response['structuredContent'].items() if k != 'exit_code'
    }
    return response


def test_results_are_equivalent_across_library_cli_mcp_and_rest(tmp_path, rest, capsys):
    path = str(tmp_path / 'parity.byax')
    execute('new', {'out': path, 'text': 'Alpha beta\nGamma'})
    for command, arguments in (
        ('inspect', {'path': path, 'outline': True}),
        ('find', {'path': path, 'query': 'beta'}),
        ('context', {'path': path}),
        ('check', {'path': path}),
        ('diff', {'path': path, 'from_revision': 'rev_x'}),
        ('schema', {'topic': 'operations'}),
        ('version', {}),
    ):
        try:
            library = _normalize(execute(command, arguments))
            library_code = 0
        except ByaxError as exc:
            library, library_code = None, exc.exit_code
        mcp = _mcp(command, arguments)
        status, rest_body = rest(command, arguments)
        flags = []
        for key, value in arguments.items():
            if key == 'path':
                continue
            flag = '--from' if key == 'from_revision' else '--' + key.replace('_', '-')
            flags += [flag] if value is True else [flag, str(value)]
        code = main([command, *([arguments['path']] if 'path' in arguments else []), *flags])
        cli = json.loads(capsys.readouterr().out)
        assert code == library_code == mcp['structuredContent']['exit_code'] == rest_body['exit_code'], (
            command
        )
        if library is not None:
            assert (
                _normalize(cli) == _normalize(mcp['structuredContent']) == _normalize(rest_body) == library
            ), command
            assert status == 200
        else:
            assert (
                cli['error']['code']
                == mcp['structuredContent']['error']['code']
                == rest_body['error']['code']
            )
            assert status in (400, 409, 422, 500) and mcp['isError']


def test_every_exit_state_is_reachable_with_structured_errors(tmp_path, capsys, monkeypatch):
    path = str(tmp_path / 'exit.byax')
    assert main(['new', '--out', path, '--text', 'Exit states']) == 0
    states = {}
    states[1] = main(['export', path, '--out', str(tmp_path / 'x.exe')])
    states[2] = main(['inspect', path, '--limit', 'zero'])
    states[3] = main(['new', '--out', path])
    block = execute('inspect', {'path': path})['items'][0]['id']
    rules = tmp_path / 'protect.json'
    rules.write_text(
        json.dumps(
            {
                'operations': [{'type': 'set_text', 'target': block, 'text': 'changed'}],
                'protect': [{'target': block, 'kind': 'wording'}],
            }
        )
    )
    states[4] = main(['apply', path, '--input', str(rules)])
    # Editorial grammar/voice coverage is declared unmeasurable, so strict policy cannot pass.
    states[5] = main(['check', path, '--suite', 'editorial', '--strict'])
    from byax.jobs import JobManager

    root = tmp_path / 'jobs'
    manager = JobManager(root)
    gate = threading.Event()
    started = threading.Event()

    def remote_work(payload, cancel):
        started.set()
        gate.wait(5)
        return {}

    job = manager.submit('review', {'remote': True}, remote_work)
    assert started.wait(5)
    manager.cancel(job['id'])
    gate.set()
    manager.wait(job['id'], 5)
    local = manager.submit('local', {'remote': False}, lambda payload, cancel: gate.wait(5) and {})
    manager.wait(local['id'], 5)
    manager.close()
    states[7] = main(['jobs', '--action', 'status', '--id', job['id'], '--root', str(root)])

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr('byax.cli.execute', interrupted)
    states[6] = main(['version'])
    outputs = capsys.readouterr().out.strip().splitlines()
    assert all(json.loads(line)['schema_version'] == 1 for line in outputs)
    for code, observed in states.items():
        assert observed == code, (code, observed)


def _cli(*args, stdin=None):
    return subprocess.run(
        [sys.executable, '-m', 'byax.cli', *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'PYTHONIOENCODING': 'utf-8'},
    )


def test_spec_16_10_workflow_through_the_cli_process(tmp_path):
    report = str(tmp_path / 'report.byax')
    assert _cli('new', '--out', report, '--title', 'Report', '--text', 'The old wording').returncode == 0
    steps = {}
    for name, args in (
        ('capabilities', ('capabilities', '--topic', 'editing', '--json')),
        ('outline', ('inspect', report, '--outline', '--json')),
    ):
        result = _cli(*args)
        assert (
            result.returncode == 0
            and result.stderr == ''
            and json.loads(result.stdout)['schema_version'] == 1
        )
        steps[name] = json.loads(result.stdout)
    inspected = json.loads(_cli('inspect', report).stdout)
    paragraph, revision = inspected['items'][0]['id'], inspected['revision']
    context = json.loads(_cli('context', report, '--target', paragraph, '--json').stdout)
    assert context['revision'] == revision
    edits = json.dumps(
        {
            'schema_version': 1,
            'operations': [
                {
                    'type': 'replace_text',
                    'target': paragraph,
                    'match': 'The old wording',
                    'occurrence': 1,
                    'expected_text': 'The old wording',
                    'replacement': 'The revised wording',
                }
            ],
        }
    )
    dry = _cli(
        'apply', report, '--input', '-', '--expect-revision', revision, '--dry-run', '--json', stdin=edits
    )
    assert dry.returncode == 0 and json.loads(_cli('inspect', report).stdout)['revision'] == revision
    applied = _cli(
        'apply',
        report,
        '--input',
        '-',
        '--expect-revision',
        revision,
        '--request-id',
        'edit_001',
        '--json',
        stdin=edits,
    )
    assert applied.returncode == 0, applied.stdout
    stale = _cli('apply', report, '--input', '-', '--expect-revision', revision, '--json', stdin=edits)
    assert stale.returncode == 3 and json.loads(stale.stdout)['error']['code'] == 'stale_revision'
    diff = json.loads(_cli('diff', report, '--from', revision, '--json').stdout)
    assert diff['execution'] == 'completed'
    rendered = _cli('render', report, '--pages', 'affected', '--out', str(tmp_path / 'proof-images'))
    assert rendered.returncode == 0 and json.loads(rendered.stdout)['outputs']
    exported = _cli('export', report, '--out', str(tmp_path / 'report.pdf'), '--json')
    assert exported.returncode == 0 and Path(tmp_path / 'report.pdf').is_file()
    preview = _cli('convert', report, '--out', str(tmp_path / 'preview.md'), '--dry-run')
    assert (
        preview.returncode == 0
        and json.loads(preview.stdout)['preview']
        and not (tmp_path / 'preview.md').exists()
    )
    piped = subprocess.run(
        f'"{sys.executable}" -m byax.cli inspect "{report}" | "{sys.executable}" -c '
        '"import json,sys; print(json.load(sys.stdin)[\'execution\'])"',
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
    )
    assert piped.stdout.strip() == 'completed'
