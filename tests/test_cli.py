import json
from pathlib import Path

import pytest

from byax.cli import error_result, execute, main, result_exit_code
from byax.errors import ByaxError
from byax.storage import DocumentLock, load_document


def test_machine_creation_and_invalid_command(tmp_path, capsys):
    output = tmp_path / 'document.byax'
    assert main(['new', '--out', str(output), '--text', 'A café report', '--json']) == 0
    created = json.loads(capsys.readouterr().out)
    assert created['document_id']
    assert load_document(output)['blocks'][0]['text'] == 'A café report'
    assert main(['unknown-command', '--json']) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed['execution'] == 'failed'
    assert failed['error']['code'] == 'invalid_input'


def test_atomic_edit_dry_run_retry_and_lock(tmp_path):
    path = str(tmp_path / 'document.byax')
    execute('new', {'out': path, 'text': 'Original café'})
    doc = load_document(path)
    target = doc['blocks'][0]['id']
    request = {'expected_revision': doc['revision'], 'request_id': 'cli-test-1',
               'operations': [{'type': 'insert_text', 'target': target, 'offset': 13, 'text': '!'}]}
    original_bytes = Path(path).read_bytes()
    execute('apply', {'path': path, 'input': request, 'dry_run': True})
    assert Path(path).read_bytes() == original_bytes
    applied = execute('apply', {'path': path, 'input': request})
    retried = execute('apply', {'path': path, 'input': request})
    assert applied['revision'] == retried['revision']
    assert load_document(path)['blocks'][0]['text'] == 'Original café!'
    with DocumentLock(path):
        with pytest.raises(ByaxError) as exc:
            execute('apply', {'path': path, 'input': request})
        assert exc.value.exit_code == 3


def test_stdin_json_and_no_gui_import(tmp_path, capsys, monkeypatch):
    import io
    import sys
    path = str(tmp_path / 'document.byax')
    execute('new', {'out': path, 'text': 'Before'})
    doc = load_document(path)
    request = {'operations': [{'type': 'set_text', 'target': doc['blocks'][0]['id'], 'text': 'After'}]}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request)))
    assert main(['apply', path, '--input', '-', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['execution'] == 'completed'
    assert load_document(path)['blocks'][0]['text'] == 'After'


def test_overwrite_is_explicit_and_source_preserved(tmp_path):
    path = str(tmp_path / 'document.byax')
    execute('new', {'out': path, 'text': 'Private original'})
    with pytest.raises(ByaxError) as exc:
        execute('new', {'out': path, 'text': 'Replacement'})
    assert exc.value.exit_code == 3
    with pytest.raises(ByaxError):
        execute('save-as', {'path': path, 'out': path, 'overwrite': True})
    assert load_document(path)['blocks'][0]['text'] == 'Private original'


def test_batch_partial_failure_retains_per_input_outcomes(tmp_path):
    good = tmp_path / 'good.txt'
    good.write_text('Text', encoding='utf-8')
    result = execute('batch', {'input': {'items': [
        {'path': str(good), 'out': str(tmp_path / 'good.byax')},
        {'path': str(tmp_path / 'missing.txt'), 'out': str(tmp_path / 'missing.byax')},
    ]}})
    assert result['execution'] == 'failed'
    assert result['failed'] == 1
    assert len(result['items']) == 2
    assert result_exit_code(result) == 1
    assert (tmp_path / 'good.byax').exists()
    assert not (tmp_path / 'missing.byax').exists()


def test_exit_states_and_input_validation():
    assert result_exit_code({'execution': 'pending'}) == 7
    assert result_exit_code({'execution': 'cancelled'}) == 6
    assert result_exit_code({'validation': 'incomplete', 'strict': True}) == 5
    assert result_exit_code({'validation': 'incomplete'}) == 0
    assert result_exit_code({'validation': 'failed'}) == 4
    with pytest.raises(ByaxError):
        execute('inspect', {'path': 'unused', 'limit': 0})
    with pytest.raises(ByaxError):
        execute('version', {'unknown': True})
    result, status = error_result(ByaxError('stale_revision', 'Changed'))
    assert status == 3
    assert result['error']['code'] == 'stale_revision'


def test_export_respects_editor_lock_and_required_coverage(tmp_path):
    source = str(tmp_path / 'source.byax')
    destination = str(tmp_path / 'destination.byax')
    execute('new', {'out': source, 'text': 'Publication text.'})
    execute('new', {'out': destination, 'text': 'Unsaved editor base.'})
    with DocumentLock(destination):
        with pytest.raises(ByaxError) as exc:
            execute('export', {'path': source, 'out': destination, 'overwrite': True})
        assert exc.value.exit_code == 3
    output = str(tmp_path / 'gated.txt')
    with pytest.raises(ByaxError) as exc:
        execute('export', {'path': source, 'out': output, 'require_suite': ['publication']})
    assert exc.value.exit_code in (4, 5)
    assert not Path(output).exists()


def test_transcription_cleanup_job_and_revision_checked_insertion(tmp_path, monkeypatch):
    from byax.providers import ProviderRegistry
    path = str(tmp_path / 'dictation.byax')
    execute('new', {'out': path, 'text': 'Hello '})
    doc = load_document(path)
    def recognize(self, alias, audio_path, **kwargs):
        assert kwargs['authorized'] is True
        return {'text': 'worlld', 'segments': []}
    def review(self, alias, text, **kwargs):
        assert text == 'worlld'
        return {'proposed_text': 'world!'}
    monkeypatch.setattr(ProviderRegistry, 'preview', lambda *args, **kwargs: {'remote': True})
    monkeypatch.setattr(ProviderRegistry, 'transcribe', recognize)
    monkeypatch.setattr(ProviderRegistry, 'review', review)
    result = execute('transcribe', {'path': str(tmp_path / 'voice.wav'), 'alias': 'recognizer',
        'mode': 'cleaned', 'cleanup_alias': 'editor', 'document': path,
        'target': doc['blocks'][0]['id'], 'offset': 6, 'expect_revision': doc['revision'],
        'insert': True, 'allow_remote': True, 'job_root': str(tmp_path / 'jobs')})
    assert result['execution'] == 'completed'
    assert load_document(path)['blocks'][0]['text'] == 'Hello world!'
    job = result['transcription']
    assert job['state'] == 'completed'
    assert job['input_revision'] == doc['revision']
    assert execute('jobs', {'action': 'result', 'id': job['id'], 'root': str(tmp_path / 'jobs')})['job']['result']['raw_transcript'] == 'worlld'


def test_transcription_does_not_overwrite_manual_edits(tmp_path, monkeypatch):
    from byax.providers import ProviderRegistry
    path = str(tmp_path / 'dictation.byax')
    execute('new', {'out': path, 'text': 'Original'})
    doc = load_document(path)
    def recognize(self, alias, audio_path, **kwargs):
        execute('apply', {'path': path, 'input': {'operations': [
            {'type': 'set_text', 'target': doc['blocks'][0]['id'], 'text': 'Manual edit'}]}})
        return {'text': 'New transcript'}
    monkeypatch.setattr(ProviderRegistry, 'preview', lambda *args, **kwargs: {'remote': True})
    monkeypatch.setattr(ProviderRegistry, 'transcribe', recognize)
    with pytest.raises(ByaxError) as exc:
        execute('transcribe', {'path': str(tmp_path / 'voice.wav'), 'alias': 'recognizer',
            'document': path, 'target': doc['blocks'][0]['id'], 'expect_revision': doc['revision'],
            'insert': True, 'job_root': str(tmp_path / 'jobs')})
    assert exc.value.code == 'stale_revision'
    assert load_document(path)['blocks'][0]['text'] == 'Manual edit'
    jobs = execute('jobs', {'root': str(tmp_path / 'jobs')})['jobs']
    assert len(jobs) == 1
    assert jobs[0]['state'] == 'completed'


def test_missing_input_file_and_job_policy_failures_keep_exit_codes(tmp_path, capsys):
    path = str(tmp_path / 'document.byax')
    execute('new', {'out': path, 'text': 'Plain text'})
    assert main(['apply', path, '--input', str(tmp_path / 'missing.json'), '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'invalid_input'
    requirement = tmp_path / 'requirement.json'
    requirement.write_text(json.dumps({'operations': [{'type': 'set_requirement', 'id': 'r', 'values': {'kind': 'required_text', 'value': 'zzz'}}]}))
    assert main(['apply', path, '--input', str(requirement), '--json']) == 0
    capsys.readouterr()
    assert main(['check', path, '--suite', 'requirements', '--json']) == 4
    capsys.readouterr()
    job = tmp_path / 'job.json'
    job.write_text(json.dumps({'command': 'check', 'arguments': {'path': path, 'suite': ['requirements']}}))
    assert main(['jobs', '--action', 'submit', '--input', str(job), '--root', str(tmp_path / 'jobs'), '--json']) == 4
    failed = json.loads(capsys.readouterr().out)
    assert failed['error']['code'] == 'failed_requirement'
    assert failed['error']['job']['state'] == 'failed'
