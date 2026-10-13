import copy
import hashlib
import json
import zipfile

import pytest

from byax.errors import ByaxError
from byax.model import new_document, text_of
from byax.publication import sanitize, save_sanitized
from byax.storage import load_document


def test_redaction_removes_recoverable_copies_and_preserves_original(tmp_path):
    doc = new_document('Secret name', 'Public text. Secret name is here.\nSecret name again.')
    doc['review']['comments'].append({'id': 'comment1', 'text': 'Secret name'})
    doc['references']['source'] = {'text': 'Secret name'}
    doc['history']['revisions'].append({'revision': 'old', 'snapshot': {'text': 'Secret name'}})
    doc['settings']['transcript'] = 'Secret name'
    doc['fields']['private'] = {'value': 'Secret name'}
    import base64
    raw = b'Secret name in embedded file'
    doc['assets'][hashlib.sha256(raw).hexdigest()] = {'data': base64.b64encode(raw).decode(), 'mime': 'audio/wav', 'name': 'Secret name.wav'}
    original = copy.deepcopy(doc)
    output = tmp_path / 'clean.BYAX'
    result = save_sanitized(doc, output, [], terms=['Secret name'])
    assert result['validation'] == 'passed'
    assert result['occurrences_removed'] == 2
    clean = load_document(output)
    assert clean['id'] != doc['id']
    assert 'Secret name' not in json.dumps(clean)
    assert clean['assets'] == {}
    assert clean['history']['revisions'] == []
    assert text_of(clean) == 'Public text. [REDACTED] is here.\n[REDACTED] again.'
    assert original == doc
    with zipfile.ZipFile(output) as z:
        assert all(b'Secret name' not in z.read(member) for member in z.namelist())


def test_sanitize_scalar_range_and_grapheme_validation():
    doc = new_document(text='A e\u0301 B')
    target = doc['blocks'][0]['id']
    with pytest.raises(ByaxError):
        sanitize(doc, [{'target': target, 'start': 2, 'end': 3}])
    clean, _report = sanitize(doc, [{'target': target, 'start': 2, 'end': 4}])
    assert text_of(clean) == 'A [REDACTED] B'


def test_sanitization_never_replaces_original(tmp_path):
    target = tmp_path / 'master.byax'
    target.write_bytes(b'original')
    with pytest.raises(ByaxError):
        save_sanitized(new_document(text='secret'), target, [], terms=['secret'])
    assert target.read_bytes() == b'original'


def test_unknown_content_cannot_claim_sanitization():
    doc = new_document(text='secret')
    doc['blocks'].append({'id': 'opaque', 'type': 'arbitrary', 'payload': 'secret'})
    with pytest.raises(ByaxError, match='Cannot verify'):
        sanitize(doc, [], terms=['secret'])


def test_reproduction_checks_actual_pixels_and_reference_integrity(tmp_path):
    from byax.publication import reproduce, reproduction_record
    doc = new_document(text='Controlled output for comparison.')
    record = reproduction_record(doc, {'reference_dir': str(tmp_path / 'reference'),
                                      'tolerance': {'channel_delta': 0, 'max_changed_fraction': 0}})
    assert record['references']
    assert 'font_dependencies' in record
    matched = reproduce(doc, record, tmp_path / 'same')
    assert matched['reproduction']['reference_verified'] is True
    changed = copy.deepcopy(doc)
    changed['blocks'][0]['text'] = 'Different output.'
    mismatch = reproduce(changed, record, tmp_path / 'changed')
    assert mismatch['validation'] == 'failed'
    assert mismatch['reproduction']['reference_verified'] is False
    from pathlib import Path
    Path(record['references'][0]['path']).write_bytes(b'changed reference')
    with pytest.raises(ByaxError, match='missing or changed'):
        reproduce(doc, record, tmp_path / 'corrupt')


def test_reproduction_refuses_reference_overwrite(tmp_path):
    from byax.publication import reproduce, reproduction_record
    doc = new_document(text='Never overwrite a comparison reference.')
    record = reproduction_record(doc, {'reference_dir': str(tmp_path / 'reference')})
    with pytest.raises(ByaxError, match='overwrite'):
        reproduce(doc, record, tmp_path / 'reference')


def test_clean_native_export_excludes_original_and_hidden_copies(tmp_path):
    from byax.exchange import import_document
    from byax.publication import export_clean
    source = tmp_path / 'source.md'
    source.write_text('Public paragraph.\n\nPrivate source material.\n')
    doc, _ = import_document(source)
    doc['blocks'][1]['attrs']['excluded'] = True
    doc['settings']['transcript'] = 'Private source material.'
    doc['review']['tracked_changes'].append({'id': 'change1', 'before': 'Private source material.'})
    output = tmp_path / 'published.byax'
    report = export_clean(doc, output)
    clean = load_document(output)
    assert 'Private source material.' not in json.dumps(clean)
    assert clean['assets'] == {}
    assert clean['review']['tracked_changes'] == []
    assert report['publication_policy']['include_originals'] is False
    assert 'Private source material.' in source.read_text()


def test_sanitization_verification_checks_values_not_json_syntax(tmp_path, monkeypatch):
    doc = new_document('Report', 'The body of the document mentions Alice.')
    for term in ('body', 'document'):
        assert save_sanitized(doc, tmp_path / f'{term}.byax', [], terms=[term])['validation'] == 'passed'
    from byax import publication
    real = publication.sanitize
    def leaky(source, targets, *, terms=None):
        clean, report = real(source, targets, terms=terms)
        clean['blocks'][0]['text'] = 'She said "hi"\nthere'
        return clean, report
    monkeypatch.setattr(publication, 'sanitize', leaky)
    with pytest.raises(ByaxError, match='retained text'):
        save_sanitized(doc, tmp_path / 'leak.byax', [], terms=['"hi"\nthere', 'Alice'])
    assert not (tmp_path / 'leak.byax').exists()


def test_sanitized_output_falls_back_without_hard_links(tmp_path, monkeypatch):
    import os
    def no_links(*_args):
        raise PermissionError('hard links unsupported')
    monkeypatch.setattr(os, 'link', no_links)
    output = tmp_path / 'clean.byax'
    assert save_sanitized(new_document(text='secret'), output, [], terms=['secret'])['validation'] == 'passed'
    assert text_of(load_document(output)) == '[REDACTED]'
    with pytest.raises(ByaxError) as error:
        save_sanitized(new_document(text='secret'), output, [], terms=['secret'])
    assert error.value.code == 'conflict'
