import base64
import hashlib
import zipfile

import pytest

from byax.engine import apply
from byax.errors import ByaxError
from byax.model import new_document
from byax.storage import DocumentLock, load_document, pack, save_document, unpack


def test_roundtrip_history_unknown_fields_and_determinism(tmp_path):
    doc = new_document(text='你好 👩‍💻')
    doc['future_extension'] = {'preserved': [1, 2, 3]}
    doc, _ = apply(doc, {'operations': [{'type': 'set_metadata', 'values': {'title': 'Saved'}}]})
    asset = b'image data'
    digest = hashlib.sha256(asset).hexdigest()
    doc['assets'][digest] = {'data': base64.b64encode(asset).decode(), 'mime': 'image/png', 'name': 'test.png'}
    path, other = tmp_path / 'Doc.BYAX', tmp_path / 'Other.byax'
    save_document(doc, path)
    save_document(doc, other)
    assert path.read_bytes() == other.read_bytes()
    assert load_document(path) == doc
    unpack(path, tmp_path / 'source')
    pack(tmp_path / 'source', tmp_path / 'packed.byax')
    loaded = load_document(tmp_path / 'packed.byax')
    assert not loaded['history']['revisions']
    assert loaded['future_extension'] == doc['future_extension']


def test_save_failure_preserves_original(tmp_path):
    path = tmp_path / 'safe.byax'
    doc = new_document()
    save_document(doc, path)
    original = path.read_bytes()
    doc['assets']['bad'] = {'data': '!!'}
    with pytest.raises(ByaxError):
        save_document(doc, path)
    assert path.read_bytes() == original


def test_lock_owner_and_other_client(tmp_path):
    path = tmp_path / 'locked.byax'
    with DocumentLock(path) as lock:
        save_document(new_document(), path, owner=lock.owner)
        with pytest.raises(ByaxError) as error:
            save_document(new_document(), path)
        assert error.value.code == 'locked'
    save_document(new_document(), path)


def test_tampered_hash_and_unsafe_paths(tmp_path):
    path = tmp_path / 'original.byax'
    save_document(new_document(), path)
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members['document.json'] += b' '
    with zipfile.ZipFile(tmp_path / 'tampered.byax', 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(ByaxError, match='integrity'):
        load_document(tmp_path / 'tampered.byax')
    with zipfile.ZipFile(tmp_path / 'unsafe.byax', 'w') as archive:
        archive.writestr('../escape', 'bad')
    with pytest.raises(ByaxError, match='Unsafe'):
        load_document(tmp_path / 'unsafe.byax')


def test_credentials_never_embedded(tmp_path):
    doc = new_document()
    doc['settings']['api_key'] = 'secret'
    with pytest.raises(ByaxError, match='Credentials'):
        save_document(doc, tmp_path / 'bad.byax')


def test_edited_source_packs_with_fresh_deterministic_revision_and_keeps_asset_hashes(tmp_path):
    import json
    source, original = tmp_path / 'source', tmp_path / 'original.byax'
    doc = new_document(text='Before')
    payload = b'asset bytes'
    digest = hashlib.sha256(payload).hexdigest()
    doc['assets'][digest] = {'data': base64.b64encode(payload).decode(), 'mime': 'application/octet-stream', 'name': 'example'}
    save_document(doc, original)
    unpack(original, source)
    editable = json.loads((source / 'document.json').read_text())
    editable['blocks'][0]['text'] = 'Edited in Git'
    serialized = json.dumps(editable, ensure_ascii=False)
    (source / 'document.json').write_text(serialized)
    report = pack(source, tmp_path / 'edited.byax')
    changed = load_document(tmp_path / 'edited.byax')
    assert changed['blocks'][0]['text'] == 'Edited in Git'
    assert changed['revision'] != doc['revision']
    assert changed['metadata']['source_pack']['base_revision'] == doc['revision']
    assert report['source_modified']
    pack(source, tmp_path / 'repeat.byax')
    assert (tmp_path / 'edited.byax').read_bytes() == (tmp_path / 'repeat.byax').read_bytes()
    assert (source / 'document.json').read_text() == serialized
    (source / 'assets' / digest).write_bytes(b'tampered')
    with pytest.raises(ByaxError, match='asset'):
        pack(source, tmp_path / 'tampered.byax')
