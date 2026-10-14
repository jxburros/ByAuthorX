"""Cross-module acceptance evidence, distinct from complete product certification."""

import copy
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from byax.errors import ByaxError
from byax.model import canonical_json, new_document, text_of
from byax.storage import DocumentLock, load_document, pack, save_document, unpack

_SPEC = importlib.util.spec_from_file_location(
    'byax_fixtures', Path(__file__).resolve().parents[1] / 'scripts' / 'generate_fixtures.py'
)
_FIXTURES = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURES)


@pytest.mark.parametrize('name', _FIXTURES.NAMES)
def test_representative_fixtures_are_deterministic_and_portable(name, tmp_path):
    doc = _FIXTURES.make_fixture(name)
    assert canonical_json(doc) == canonical_json(_FIXTURES.make_fixture(name))
    path = tmp_path / (name + '.BYAX')
    save_document(doc, path)
    reopened = load_document(path)
    assert reopened == doc
    assert path.name.endswith('.BYAX')


def test_edit_dry_run_commit_retry_reopen_and_source_roundtrip(tmp_path):
    from byax.engine import apply

    doc = _FIXTURES.make_fixture('press_kit')
    original = copy.deepcopy(doc)
    before = copy.deepcopy(next(b for b in doc['blocks'] if b['id'] == 'release_table'))
    envelope = {
        'schema_version': 1,
        'expected_revision': doc['revision'],
        'request_id': 'acceptance-release-date',
        'operations': [
            {
                'type': 'update_cell',
                'target': 'release_table_r2_c1',
                'text': '2026-12-01',
                'expected_text': '2026-11-01',
            }
        ],
    }
    candidate, _preview = apply(doc, envelope, dry_run=True)
    assert doc == original
    assert '2026-12-01' in text_of(candidate)
    assert len(candidate['history']['revisions']) == len(doc['history']['revisions'])
    changed, result = apply(doc, envelope)
    after = next(b for b in changed['blocks'] if b['id'] == 'release_table')
    expected_table = copy.deepcopy(before)
    expected_table['rows'][2]['cells'][1]['text'] = '2026-12-01'
    assert after == expected_table
    retry, retry_result = apply(changed, envelope)
    assert retry == changed
    assert retry_result['revision'] == result['revision']
    path = tmp_path / 'release.byax'
    save_document(changed, path)
    loaded = load_document(path)
    assert loaded == changed
    replayed, replay_result = apply(loaded, envelope)
    assert replayed == loaded
    assert replay_result['revision'] == result['revision']
    source = tmp_path / 'source'
    unpack(path, source)
    packed = tmp_path / 'packed.byax'
    pack(source, packed)
    assert text_of(load_document(packed)) == text_of(changed)
    assert not load_document(packed)['history']['revisions']


def test_ambiguous_stale_and_atomic_failure_do_not_change_original():
    from byax.engine import apply

    doc = new_document(text='echo echo')
    target = doc['blocks'][0]['id']
    original = copy.deepcopy(doc)
    with pytest.raises(ByaxError) as ambiguous:
        apply(
            doc,
            {
                'operations': [
                    {'type': 'replace_text', 'target': target, 'match': 'echo', 'replacement': 'reply'}
                ]
            },
        )
    assert ambiguous.value.code == 'ambiguous_selection'
    assert ambiguous.value.details.get('candidates')
    with pytest.raises(ByaxError) as stale:
        apply(
            doc,
            {
                'expected_revision': 'rev_obsolete',
                'operations': [{'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'x'}],
            },
        )
    assert stale.value.code == 'stale_revision'
    with pytest.raises(ByaxError):
        apply(
            doc,
            {
                'operations': [
                    {'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'x'},
                    {'type': 'delete_block', 'target': 'missing'},
                ]
            },
        )
    assert doc == original


def test_grapheme_boundaries_reject_combining_mark_split():
    from byax.engine import apply

    doc = new_document(text='Cafe\u0301 👩🏽‍💻')
    with pytest.raises(ByaxError):
        apply(
            doc,
            {'operations': [{'type': 'delete_text', 'target': doc['blocks'][0]['id'], 'start': 4, 'end': 5}]},
        )


def test_unsaved_session_lock_blocks_independent_process(tmp_path):
    doc = new_document(text='Committed state')
    path = tmp_path / 'working.byax'
    save_document(doc, path)
    code = """
import sys
from byax.storage import load_document, save_document
from byax.errors import ByaxError
try:
    save_document(load_document(sys.argv[1]), sys.argv[1])
except ByaxError as exc:
    print(exc.code)
    sys.exit(exc.exit_code)
"""
    with DocumentLock(path):
        result = subprocess.run(
            [sys.executable, '-c', code, str(path)], check=False, capture_output=True, text=True, timeout=15
        )
    assert result.returncode == 3, result.stderr
    assert result.stdout.strip() == 'locked'
    assert load_document(path) == doc


def test_failed_atomic_replace_preserves_last_committed_archive(tmp_path, monkeypatch):
    from byax import storage

    path = tmp_path / 'stable.byax'
    doc = new_document(text='Last committed wording')
    save_document(doc, path)
    original = path.read_bytes()
    changed = copy.deepcopy(doc)
    changed['blocks'][0]['text'] = 'Unsaved wording'

    def fail(*args, **kwargs):
        raise OSError('Simulated interrupted replacement')

    monkeypatch.setattr(storage.os, 'replace', fail)
    with pytest.raises((OSError, ByaxError)):
        save_document(changed, path)
    assert path.read_bytes() == original
    assert load_document(path) == doc


def test_findings_limit_preserves_failure_and_missing_coverage():
    from byax.checks import run_checks

    doc = new_document(text='An ordinary paragraph.')
    doc['settings']['requirements'] = {
        'one': {'kind': 'required_text', 'value': 'MISSING A'},
        'two': {'kind': 'required_text', 'value': 'MISSING B'},
    }
    report = run_checks(doc, ['requirements'], limit=0)
    assert report['validation'] == 'failed'
    assert report['summary']['errors'] == 2
    assert report['findings'] == []
    assert report['summary']['truncated']
    unavailable = run_checks(doc, ['editorial'])
    assert unavailable['validation'] == 'incomplete'
    assert any(item['status'] == 'unmeasurable' for item in unavailable['coverage'])


def test_true_redaction_checks_serialized_members_and_preserves_original(tmp_path):
    from byax.publication import save_sanitized

    secret = 'CONFIDENTIAL-CASE-4927'
    doc = new_document(text='Public statement. ' + secret)
    doc['metadata']['description'] = secret
    doc['review']['comments'].append(
        {'id': 'comment_secret', 'target': doc['blocks'][0]['id'], 'text': secret}
    )
    doc['transcripts'] = [{'text': secret}]
    doc['references']['secret_ref'] = {'excerpt': secret}
    doc['history']['revisions'].append({'revision': 'old', 'snapshot': {'text': secret}})
    doc['custom_extension'] = {'recoverable_copy': secret}
    original = copy.deepcopy(doc)
    output = tmp_path / 'sanitized.byax'
    report = save_sanitized(doc, output, [], terms=[secret])
    assert doc == original
    assert report['coverage']['serialized_output'] == 'passed'
    with zipfile.ZipFile(output) as archive:
        for member in archive.namelist():
            assert secret not in json.dumps(json.loads(archive.read(member)), ensure_ascii=False)
    clean = load_document(output)
    assert 'Public statement.' in text_of(clean)
    assert not clean['history']['revisions']
    assert not clean['review']['comments']
    assert not clean['assets']


@pytest.mark.parametrize('format', ['txt', 'md', 'html', 'docx', 'odt', 'rtf'])
def test_exchange_roundtrip_keeps_selected_text_and_original(format, tmp_path):
    from byax.exchange import export_document, import_document

    doc = new_document(text='Original multilingual wording: Café Ελληνικά العربية.\nA second paragraph.')
    original = copy.deepcopy(doc)
    out = tmp_path / ('exchange.' + format)
    report = export_document(doc, out, format=format)
    before = out.read_bytes()
    imported, imported_report = import_document(out)
    assert 'Original multilingual wording' in text_of(imported)
    assert 'Café' in text_of(imported)
    assert 'A second paragraph.' in text_of(imported)
    assert doc == original
    assert out.read_bytes() == before
    assert isinstance(report['losses'], list)
    assert isinstance(imported_report['losses'], list)


def test_shared_render_and_pdf_create_actual_outputs(tmp_path):
    from byax.layout import build_document, export_pdf, render

    doc = _FIXTURES.make_fixture('letter')
    qdoc = build_document(doc)
    rendered = render(doc, tmp_path / 'proof')
    pdf = tmp_path / 'letter.pdf'
    exported = export_pdf(doc, pdf)
    assert qdoc.pageCount() == rendered['page_count'] == exported['page_count']
    assert rendered['outputs']
    for output in rendered['outputs']:
        assert Path(output).read_bytes().startswith(b'\x89PNG')
    assert pdf.read_bytes().startswith(b'%PDF')
    assert pdf.stat().st_size > 1000
    assert exported['validation'] != 'passed'


def test_required_typed_template_field_does_not_pass_without_a_value():
    from byax.checks import run_checks

    doc = _FIXTURES.make_fixture('press_kit')
    report = run_checks(doc, ['requirements'])
    assert report['validation'] == 'failed'
    assert any(f['rule'] == 'field.required' for f in report['findings'])
    doc['fields']['booking_url']['value'] = 'https://example.invalid/book'
    assert run_checks(doc, ['requirements'])['validation'] == 'passed'
