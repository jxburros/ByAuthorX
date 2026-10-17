"""Saved command macros (CP 7.4): validation, stores, preconditions, dry run, stop on error, admin refusal."""

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from byax import macros
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.mcp import handle
from byax.service import make_server

TOKEN = 'macro-test-token-with-32-characters'


def paragraph_step(text, label='Add paragraph'):
    return {
        'label': label,
        'command': 'apply',
        'arguments': {
            'path': '{document}',
            'input': {'operations': [{'type': 'insert_paragraph', 'text': text}]},
        },
    }


def macro(name='tidy', steps=None, **extra):
    return {
        'schema': 1,
        'name': name,
        'version': 1,
        'description': 'Test macro',
        'steps': steps
        or [
            paragraph_step('Added by a macro.'),
            {
                'label': 'Check',
                'command': 'check',
                'arguments': {'path': '{document}', 'suite': ['structure']},
            },
        ],
        **extra,
    }


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / 'doc.byax'
    execute('new', {'out': str(path), 'text': 'First paragraph.'})
    return str(path)


def revision(path):
    return execute('inspect', {'path': path, 'limit': 1})['revision']


def texts(path):
    return [item['text'] for item in execute('inspect', {'path': path, 'limit': 50})['items']]


# Validation -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ('change', 'message'),
    [
        ({'name': 'Bad Name'}, 'names'),
        ({'version': 0}, 'version'),
        ({'steps': []}, 'steps'),
        ({'steps': [{'command': 'nope', 'arguments': {}}]}, 'Unknown command'),
        ({'steps': [{'command': 'macro', 'arguments': {}}]}, 'another macro'),
        ({'steps': [{'command': 'check', 'arguments': {'bogus': 1}}]}, 'no argument bogus'),
        ({'steps': [{'command': 'check', 'arguments': {'path': '{missing}'}}]}, 'not a declared parameter'),
        ({'steps': [{'command': 'check', 'extra': 1}]}, 'label, command and arguments'),
        ({'steps': [{'command': 'check', 'arguments': []}]}, 'object'),
        ({'extra': 1}, 'object with name'),
        ({'schema': 2}, 'schema'),
        ({'parameters': {'document': {}}}, 'Parameter names'),
        ({'parameters': {'n': {'type': 'integer', 'default': 'x'}}}, 'default'),
        ({'parameters': {'n': {'type': 'float'}}}, 'parameter type'),
        ({'steps': [{'command': 'check', 'arguments': {}}] * 101}, 'between 1 and 100'),
    ],
)
def test_macro_validation_rejects_bad_definitions(change, message):
    with pytest.raises(ByaxError, match=message):
        macros.validate_macro({**macro(), **change})


def test_validated_macros_are_normalized_with_declared_parameters():
    normalized = macros.validate_macro(
        macro(
            parameters={'suite': {'type': 'array', 'default': ['structure']}},
            steps=[{'command': 'check', 'arguments': {'path': '{document}', 'suite': '{suite}'}}],
        )
    )
    assert normalized['steps'][0]['label'] == 'check'
    assert normalized['parameters']['suite'] == {
        'type': 'array',
        'default': ['structure'],
        'required': False,
        'description': '',
    }


def test_read_only_classification_is_conservative():
    assert macros.is_read_only('check', {'path': 'x'}) and macros.is_read_only('inspect', {})
    assert macros.is_read_only('review', {}) and not macros.is_read_only('review', {'action': 'propose'})
    assert macros.is_read_only('project', {'action': 'tree'}) and not macros.is_read_only(
        'project', {'action': 'add'}
    )
    assert not macros.is_read_only('apply', {}) and not macros.is_read_only('export', {})
    assert macros.is_read_only('compile', {'preflight_only': True}) and not macros.is_read_only('compile', {})


# Stores -----------------------------------------------------------------------------------
def test_user_store_saves_lists_shows_versions_and_removes(tmp_path):
    config = str(tmp_path / 'config')
    saved = execute('macro', {'action': 'save', 'input': macro(), 'config_dir': config})
    assert saved['macro']['store'] == 'user' and saved['macro']['steps'] == 2
    assert [m['name'] for m in execute('macro', {'config_dir': config})['macros']] == ['tidy']
    shown = execute('macro', {'action': 'show', 'name': 'tidy', 'config_dir': config})
    assert shown['macro']['version'] == 1 and shown['admin_steps'] == []
    with pytest.raises(ByaxError) as error:
        execute('macro', {'action': 'save', 'input': macro(), 'config_dir': config})
    assert error.value.code == 'conflict'
    newer = macro()
    newer['version'] = 2
    assert execute('macro', {'action': 'save', 'input': newer, 'config_dir': config})['macro']['version'] == 2
    execute('macro', {'action': 'save', 'input': macro(), 'config_dir': config, 'replace': True})
    assert execute('macro', {'action': 'remove', 'name': 'tidy', 'config_dir': config})['removed'] == 'tidy'
    assert execute('macro', {'config_dir': config})['macros'] == []
    with pytest.raises(ByaxError) as error:
        execute('macro', {'action': 'show', 'name': 'tidy', 'config_dir': config})
    assert error.value.code == 'missing_target'


def test_a_corrupt_macros_file_is_reported_not_overwritten(tmp_path):
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'macros.json').write_text('{not json', encoding='utf-8')
    with pytest.raises(ByaxError, match='cannot be read'):
        execute('macro', {'config_dir': str(config)})
    assert (config / 'macros.json').read_text(encoding='utf-8') == '{not json'


def test_document_store_is_a_normal_revision_and_travels_with_the_file(tmp_path, doc):
    before = revision(doc)
    result = execute('macro', {'action': 'save', 'input': macro(), 'store': 'document', 'document': doc})
    assert result['macro']['store'] == 'document' and result['revision'] != before
    listed = execute('macro', {'document': doc})['macros']
    assert [(m['name'], m['store']) for m in listed] == [('tidy', 'document')]
    assert (
        execute('macro', {'document': doc, 'store': 'user', 'config_dir': str(tmp_path / 'c')})['macros']
        == []
    )
    copy = tmp_path / 'copy.byax'
    execute('save-as', {'path': doc, 'out': str(copy)})
    assert [m['name'] for m in execute('macro', {'document': str(copy)})['macros']] == ['tidy']
    execute('macro', {'action': 'remove', 'name': 'tidy', 'store': 'document', 'document': doc})
    assert execute('macro', {'document': doc})['macros'] == []
    with pytest.raises(ByaxError, match='--document'):
        execute('macro', {'action': 'save', 'input': macro(), 'store': 'document'})


def test_a_name_in_both_stores_must_be_disambiguated(tmp_path, doc):
    config = str(tmp_path / 'config')
    execute('macro', {'action': 'save', 'input': macro(), 'config_dir': config})
    execute('macro', {'action': 'save', 'input': macro(), 'store': 'document', 'document': doc})
    with pytest.raises(ByaxError) as error:
        execute('macro', {'action': 'show', 'name': 'tidy', 'document': doc, 'config_dir': config})
    assert error.value.code == 'ambiguous_selection'
    assert (
        execute(
            'macro',
            {'action': 'show', 'name': 'tidy', 'document': doc, 'store': 'user', 'config_dir': config},
        )['store']
        == 'user'
    )


# Running ----------------------------------------------------------------------------------
def test_a_macro_runs_steps_through_the_engine_and_keeps_a_transcript(doc, tmp_path):
    config = str(tmp_path / 'config')
    execute('macro', {'action': 'save', 'input': macro(), 'config_dir': config})
    before = revision(doc)
    result = execute('macro', {'action': 'run', 'name': 'tidy', 'document': doc, 'config_dir': config})
    assert result['status'] == 'completed' and result['steps_run'] == 2 and result['dry_run'] is False
    first, second = result['transcript']
    assert (first['command'], first['status'], first['exit_code']) == ('apply', 'completed', 0)
    assert first['summary']['revision'] != before and first['summary']['changed'] >= 1
    assert second['command'] == 'check' and second['summary']['validation'] in ('passed', 'incomplete')
    assert texts(doc) == ['First paragraph.', 'Added by a macro.']
    assert 'result' not in first
    verbose = execute('macro', {'action': 'run', 'input': macro('inline'), 'document': doc, 'verbose': True})
    assert (
        verbose['transcript'][0]['result']['execution'] == 'completed'
        and verbose['macro']['store'] == 'inline'
    )


def test_each_mutating_step_is_guarded_by_the_revision_the_previous_step_produced(doc):
    calls = []
    external = threading.Event()

    def interfering(command, arguments):
        calls.append((command, dict(arguments)))
        result = execute(command, arguments)
        if len(calls) == 1 and not external.is_set():
            external.set()
            # Someone else edits the document between the macro's two steps.
            execute(
                'apply',
                {'path': doc, 'input': {'operations': [{'type': 'insert_paragraph', 'text': 'Interloper.'}]}},
            )
        return result

    steps = [paragraph_step('One.'), paragraph_step('Two.', 'Second'), paragraph_step('Three.', 'Third')]
    outcome = macros.run_macro(macros.validate_macro(macro(steps=steps)), document=doc, execute=interfering)
    assert outcome['status'] == 'failed' and outcome['failed_step'] == 2
    assert outcome['error']['code'] == 'stale_revision' and outcome['exit_code'] == 3
    assert [e['status'] for e in outcome['transcript']] == ['completed', 'failed'], 'step 3 never ran'
    assert calls[0][1]['expect_revision'] != calls[1][1]['expect_revision']
    assert 'Three.' not in texts(doc) and 'Two.' not in texts(doc)


def test_the_run_can_demand_the_revision_it_expects_up_front(doc):
    steps = [paragraph_step('Should not be added.')]
    with pytest.raises(ByaxError) as error:
        execute(
            'macro',
            {'action': 'run', 'input': macro(steps=steps), 'document': doc, 'expect_revision': 'rev_stale'},
        )
    assert error.value.code == 'stale_revision' and error.value.exit_code == 3
    assert error.value.details['transcript'][0]['status'] == 'failed'
    assert texts(doc) == ['First paragraph.']
    execute(
        'macro',
        {'action': 'run', 'input': macro(steps=steps), 'document': doc, 'expect_revision': revision(doc)},
    )
    assert texts(doc)[-1] == 'Should not be added.'


def test_the_first_failing_step_stops_the_macro_with_its_exit_code_and_transcript(doc, capsys):
    steps = [
        paragraph_step('Kept: runs before the failure.'),
        {'label': 'Break', 'command': 'inspect', 'arguments': {'path': '{document}', 'target': 'p_missing'}},
        paragraph_step('Never added.'),
    ]
    with pytest.raises(ByaxError) as error:
        execute('macro', {'action': 'run', 'input': macro(steps=steps), 'document': doc})
    details = error.value.details
    assert details['step'] == 2 and [e['status'] for e in details['transcript']] == ['completed', 'failed']
    assert texts(doc) == ['First paragraph.', 'Kept: runs before the failure.']
    path = Path(doc).parent / 'broken.json'
    path.write_text(json.dumps(macro(steps=steps)), encoding='utf-8')
    code = main(['macro', '--action', 'run', '--document', doc, '--input', str(path)])
    out = json.loads(capsys.readouterr().out)
    assert (
        code == error.value.exit_code and out['error']['step'] == 2 and len(out['error']['transcript']) == 2
    )


def test_a_result_with_a_failing_exit_code_stops_the_macro(doc):
    steps = [
        {'command': 'check', 'arguments': {'path': '{document}', 'suite': ['editorial'], 'strict': True}},
        paragraph_step('Never added.'),
    ]
    with pytest.raises(ByaxError) as error:
        execute('macro', {'action': 'run', 'input': macro(steps=steps), 'document': doc})
    assert error.value.exit_code == 5 and error.value.code == 'incomplete_coverage'
    assert texts(doc) == ['First paragraph.']


def test_dry_run_changes_nothing_runs_read_only_steps_and_says_what_it_skipped(doc):
    steps = [
        paragraph_step('Preview only.'),
        {'label': 'Inspect', 'command': 'inspect', 'arguments': {'path': '{document}', 'outline': True}},
        {
            'label': 'Checkpoint',
            'command': 'history',
            'arguments': {'path': '{document}', 'action': 'checkpoint', 'name': 'x'},
        },
    ]
    before = revision(doc)
    result = execute(
        'macro', {'action': 'run', 'input': macro(steps=steps), 'document': doc, 'dry_run': True}
    )
    assert revision(doc) == before and texts(doc) == ['First paragraph.']
    statuses = [(e['command'], e['status'], e.get('dry_run')) for e in result['transcript']]
    assert statuses == [
        ('apply', 'completed', True),
        ('inspect', 'completed', None),
        ('history', 'skipped', None),
    ]
    assert 'No dry run' in result['transcript'][2]['note'] and result['dry_run'] is True
    assert result['transcript'][0]['summary']['execution'] == 'dry_run'


def test_parameters_are_typed_defaulted_required_and_substituted_everywhere(doc):
    definition = macro(
        parameters={
            'text': {'type': 'string', 'required': True},
            'suite': {'type': 'array', 'default': ['structure']},
            'limit': {'type': 'integer', 'default': 5},
        },
        steps=[
            {
                'command': 'apply',
                'arguments': {
                    'path': '{document}',
                    'input': {'operations': [{'type': 'insert_paragraph', 'text': 'Say: {text} {{braces}}'}]},
                },
            },
            {'command': 'check', 'arguments': {'path': '{document}', 'suite': '{suite}', 'limit': '{limit}'}},
        ],
    )
    with pytest.raises(ByaxError, match='required'):
        execute('macro', {'action': 'run', 'input': definition, 'document': doc})
    with pytest.raises(ByaxError, match='must be integer'):
        execute(
            'macro',
            {'action': 'run', 'input': definition, 'document': doc, 'params': {'text': 'x', 'limit': 'many'}},
        )
    with pytest.raises(ByaxError, match='Unknown macro parameters'):
        execute(
            'macro',
            {'action': 'run', 'input': definition, 'document': doc, 'params': {'text': 'x', 'nope': 1}},
        )
    result = execute(
        'macro', {'action': 'run', 'input': definition, 'document': doc, 'params': {'text': 'hello'}}
    )
    assert result['status'] == 'completed' and texts(doc)[-1] == 'Say: hello {braces}'


def test_the_cli_accepts_inline_json_parameters_and_the_dry_run_flag(doc, tmp_path, capsys):
    definition = macro(
        parameters={'text': {'type': 'string', 'required': True}},
        steps=[
            {
                'command': 'apply',
                'arguments': {
                    'path': '{document}',
                    'input': {'operations': [{'type': 'insert_paragraph', 'text': '{text}'}]},
                },
            }
        ],
    )
    file = tmp_path / 'm.json'
    file.write_text(json.dumps(definition), encoding='utf-8')
    code = main(
        [
            'macro',
            '--action',
            'run',
            '--document',
            doc,
            '--input',
            str(file),
            '--params',
            '{"text": "from cli"}',
            '--dry-run',
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out['dry_run'] and texts(doc) == ['First paragraph.']
    code = main(
        [
            'macro',
            '--action',
            'run',
            '--document',
            doc,
            '--input',
            str(file),
            '--params',
            '{"text": "from cli"}',
        ]
    )
    capsys.readouterr()
    assert code == 0 and texts(doc)[-1] == 'from cli'


def test_document_macros_run_from_the_document_they_travel_with(doc):
    execute('macro', {'action': 'save', 'input': macro('house-pass'), 'store': 'document', 'document': doc})
    result = execute('macro', {'action': 'run', 'name': 'house-pass', 'document': doc})
    assert result['macro']['store'] == 'document' and texts(doc)[-1] == 'Added by a macro.'


# Administrative steps ---------------------------------------------------------------------
def goal_step(config):
    return {
        'label': 'Set goal',
        'command': 'goals',
        'arguments': {'path': '{document}', 'action': 'set-daily', 'words': 500, 'config_dir': config},
    }


def test_admin_steps_are_refused_unless_the_local_cli_passes_allow_admin(doc, tmp_path):
    config = str(tmp_path / 'goals')
    definition = macro('goal', steps=[goal_step(config)])
    assert execute('macro', {'action': 'save', 'input': definition, 'config_dir': str(tmp_path / 'c')})[
        'macro'
    ]['admin_steps'] == [1]
    with pytest.raises(ByaxError) as error:
        execute('macro', {'action': 'run', 'input': definition, 'document': doc})
    assert error.value.code == 'unsupported_feature' and 'administrative' in error.value.message
    assert error.value.details['transcript'][0]['status'] == 'refused'
    assert not Path(config).exists()
    result = execute('macro', {'action': 'run', 'input': definition, 'document': doc, 'allow_admin': True})
    assert result['status'] == 'completed' and (Path(config) / 'goals.json').is_file()


def test_every_step_gets_the_provider_admin_check(doc, tmp_path):
    for command, arguments in (
        ('providers', {'action': 'configure', 'alias': 'x'}),
        ('languages', {'action': 'install', 'pack_file': 'x.byaxpack'}),
        ('extensions', {'action': 'enable', 'id': 'something'}),
        ('library', {'path': '{document}', 'action': 'attach'}),
    ):
        definition = macro('bad', steps=[{'command': command, 'arguments': arguments}])
        with pytest.raises(ByaxError) as error:
            execute('macro', {'action': 'run', 'input': definition, 'document': doc})
        assert error.value.code == 'unsupported_feature', command


def test_a_macro_cannot_slip_an_admin_step_into_a_job(doc):
    inner = {'command': 'languages', 'arguments': {'action': 'remove', 'id': 'x'}}
    definition = macro('job', steps=[{'command': 'jobs', 'arguments': {'action': 'submit', 'input': inner}}])
    with pytest.raises(ByaxError) as error:
        execute('macro', {'action': 'run', 'input': definition, 'document': doc})
    assert error.value.code == 'unsupported_feature'


def test_mcp_refuses_allow_admin_and_user_store_writes_but_runs_ordinary_macros(doc, tmp_path):
    def call(arguments):
        return handle(
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'byax_macro', 'arguments': arguments},
            }
        )['result']

    refused = call({'action': 'run', 'input': macro(), 'document': doc, 'allow_admin': True})
    assert refused['isError'] and refused['structuredContent']['error']['code'] == 'unsupported_feature'
    assert call({'action': 'save', 'input': macro(), 'config_dir': str(tmp_path / 'c')})['isError']
    assert call({'action': 'remove', 'name': 'tidy', 'config_dir': str(tmp_path / 'c')})['isError']
    saved = call({'action': 'save', 'input': macro(), 'store': 'document', 'document': doc})
    assert not saved['isError']
    ran = call({'action': 'run', 'name': 'tidy', 'document': doc})
    assert not ran['isError'] and ran['structuredContent']['status'] == 'completed'
    admin = call(
        {'action': 'run', 'input': macro('goal', steps=[goal_step(str(tmp_path / 'g'))]), 'document': doc}
    )
    assert admin['isError'] and admin['structuredContent']['error']['transcript'][0]['status'] == 'refused'
    assert not (tmp_path / 'g').exists()


# REST: the service's path policy is applied to every macro step ---------------------------
@pytest.fixture
def rest(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    server = make_server([root], token=TOKEN, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/v1/execute'

    def call(command, arguments, token=TOKEN):
        request = Request(
            url,
            data=json.dumps({'command': command, 'arguments': arguments}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + token},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    yield root, call
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_over_rest_macro_steps_cannot_reach_outside_the_authorized_roots(rest, tmp_path):
    root, call = rest
    document = str(root / 'doc.byax')
    execute('new', {'out': document, 'text': 'Inside.'})
    escape = tmp_path / 'escape.md'
    sneaky = macro(
        'sneaky',
        steps=[{'command': 'export', 'arguments': {'path': '{document}', 'out': str(escape)}}],
    )
    status, body = call('macro', {'action': 'run', 'input': sneaky, 'document': document})
    assert status == 400 and body['error']['code'] == 'invalid_input' and not escape.exists()
    # A stored macro is not visible in the request, so the policy is applied to each step as it runs.
    execute('macro', {'action': 'save', 'input': sneaky, 'store': 'document', 'document': document})
    status, body = call('macro', {'action': 'run', 'name': 'sneaky', 'document': document})
    assert status == 400 and body['error']['code'] == 'invalid_input' and not escape.exists()
    assert body['error']['transcript'][0]['status'] == 'failed' and 'outside' in body['error']['message']
    fine = macro(
        'fine',
        steps=[{'command': 'export', 'arguments': {'path': '{document}', 'out': str(root / 'out.md')}}],
    )
    status, body = call('macro', {'action': 'run', 'input': fine, 'document': document})
    assert status == 200 and (root / 'out.md').is_file(), body['error']
    status, body = call('macro', {'action': 'run', 'input': fine, 'document': document, 'allow_admin': True})
    assert status == 422 or status == 500 or body['error']['code'] == 'unsupported_feature'
    status, body = call('macro', {'action': 'save', 'input': fine})
    assert body['error']['code'] == 'unsupported_feature'
    status, body = call(
        'macro',
        {
            'action': 'run',
            'input': macro(
                'p', steps=[{'command': 'check', 'arguments': {'path': str(tmp_path / 'outside.byax')}}]
            ),
            'document': document,
        },
    )
    assert status == 400 and body['error']['code'] == 'invalid_input'
