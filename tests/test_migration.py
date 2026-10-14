"""Native schema migration against real historical (schema 1) fixtures."""

import copy
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from byax import engine, migration
from byax.engine import apply
from byax.errors import ByaxError
from byax.model import new_document, validate_document
from byax.storage import load_document, migrate_package, open_document, pack, save_document, unpack

FIXTURES = Path(__file__).parent / 'fixtures'
V1_PACKAGE = FIXTURES / 'schema-v1-letter.byax'


def _v1_copy(tmp_path, name='letter.byax'):
    target = tmp_path / name
    target.write_bytes(V1_PACKAGE.read_bytes())
    return target


def _rewrite(path, mutate):
    """Rewrite a package's document/manifest while keeping its integrity hashes."""
    with zipfile.ZipFile(path) as archive:
        members = {i.filename: archive.read(i) for i in archive.infolist()}
    manifest, document = json.loads(members['manifest.json']), json.loads(members['document.json'])
    mutate(manifest, document)
    members['document.json'] = json.dumps(document).encode()
    manifest['resources']['document.json'] = {
        'sha256': hashlib.sha256(members['document.json']).hexdigest(),
        'bytes': len(members['document.json']),
    }
    members['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def test_schema_v1_fixture_opens_migrated_with_report_and_history(tmp_path):
    path = _v1_copy(tmp_path)
    original = path.read_bytes()
    doc, report = open_document(path)
    assert path.read_bytes() == original
    assert doc['schema_version'] == 2 and report['from_version'] == 1 and report['to_version'] == 2
    assert report['history_revisions_migrated'] == len(doc['history']['revisions']) == 8
    step = report['steps'][0]
    assert {t['kind'] for t in step['transformations']} == {'external_image_dependency', 'added_resource'}
    remote = next(b for b in doc['blocks'] if b['id'] == 'image_remote')
    dependency = doc['dependencies'][remote['attrs']['link']]
    assert dependency['uri'] == 'https://example.com/chart.png' and dependency['status'] == 'unresolved'
    assert remote['attrs']['source'] == 'https://example.com/chart.png'
    # Unknown constructs survive the transition.
    assert doc['x_extension'] == {'vendor': 'future-tool', 'payload': [1, 2, 3]}
    assert doc['metadata']['migrations'][0]['source_document_sha256'] == report['source_document_sha256']
    # Every retained historical state reconstructs as the current schema and restores.
    for record in doc['history']['revisions']:
        state = engine._revision(doc, record['revision'])
        assert state['schema_version'] == 2 and 'dependencies' in state
    historical = engine.revision_document(doc, 'draft one')
    assert historical['blocks'][3]['text'] == 'Body text with a remote picture.'
    assert not historical['settings'].get('track_changes')
    with pytest.raises(ByaxError, match='Tracking'):
        engine.restore(doc, 'draft one')  # restore still cannot weaken retained tracking policy
    assert doc['history']['branches']['alternate'] == doc['revision']
    assert set(doc['requests']) == {'req-asset', 'req-thanks'}
    # Legacy whole-text tracked changes and proposals remain actionable.
    rejected, _ = apply(
        doc, {'operations': [{'type': 'reject_change', 'target': doc['review']['tracked_changes'][-1]['id']}]}
    )
    assert rejected['blocks'][2]['text'] == 'Second item'
    proposal = doc['review']['proposals'][0]['id']
    accepted, _ = apply(doc, {'operations': [{'type': 'accept_proposal', 'target': proposal}]})
    assert accepted['blocks'][1]['text'] == 'Primary item'


def test_saving_migrated_package_preserves_original_once(tmp_path):
    path = _v1_copy(tmp_path)
    original = path.read_bytes()
    doc, _ = open_document(path)
    result = save_document(doc, path)
    backup = Path(result['preserved_original'])
    assert backup.read_bytes() == original and backup.name.startswith('letter.schema1-')
    with zipfile.ZipFile(path) as archive:
        assert json.loads(archive.read('manifest.json'))['schema_versions'] == {'document': 2}
    assert save_document(load_document(path), path)['preserved_original'] is None
    assert len(list(tmp_path.glob('letter.schema1-*.byax'))) == 1
    # The preserved original still opens (migrating again in memory only).
    assert open_document(backup)[1]['from_version'] == 1


def test_migrate_package_writes_validated_copy_and_never_replaces_source(tmp_path):
    path = _v1_copy(tmp_path)
    original = path.read_bytes()
    with pytest.raises(ByaxError) as error:
        migrate_package(path, path)
    assert error.value.code == 'conflict'
    result = migrate_package(path, tmp_path / 'upgraded.byax')
    assert result['migrated'] and result['source_unchanged'] and path.read_bytes() == original
    assert load_document(tmp_path / 'upgraded.byax')['schema_version'] == 2
    with pytest.raises(ByaxError) as error:
        migrate_package(path, tmp_path / 'upgraded.byax')
    assert error.value.code == 'conflict'


def test_future_versions_are_rejected_without_destructive_save(tmp_path):
    path = _v1_copy(tmp_path, 'future.byax')

    def future(manifest, document):
        manifest['schema_versions'] = {'document': 3}
        document['schema_version'] = 3

    _rewrite(path, future)
    before = path.read_bytes()
    with pytest.raises(ByaxError) as error:
        load_document(path)
    assert error.value.code == 'unsupported_version' and path.read_bytes() == before
    with pytest.raises(ByaxError) as error:
        save_document(new_document(text='older writer'), path)
    assert error.value.code == 'unsupported_version' and path.read_bytes() == before
    with pytest.raises(ByaxError) as error:
        validate_document({**new_document(), 'schema_version': 1})
    assert error.value.details['migration'] == 'available'


def test_unmigratable_constructs_are_rejected_explicitly(tmp_path):
    clash = _v1_copy(tmp_path, 'clash.byax')
    _rewrite(clash, lambda manifest, document: document.update(dependencies={'unknown': True}))
    with pytest.raises(ByaxError) as error:
        load_document(clash)
    assert error.value.code == 'unsupported_feature'
    block = _v1_copy(tmp_path, 'block.byax')
    _rewrite(block, lambda manifest, document: document['blocks'].append({'id': 'x', 'type': 'hologram'}))
    with pytest.raises(ByaxError) as error:
        load_document(block)
    assert error.value.code == 'schema_error'


def test_migration_gate_rejects_transitions_that_break_references(monkeypatch):
    with zipfile.ZipFile(V1_PACKAGE) as archive:
        doc = json.loads(archive.read('document.json'))
    original = copy.deepcopy(doc)

    def lossy(state):
        state['assets'] = {}
        return migration._v1_to_v2(state)

    monkeypatch.setitem(migration.TRANSITIONS, 1, (2, 'lossy', lossy))
    with pytest.raises(ByaxError) as error:
        migration.migrate_document(doc)
    assert error.value.code == 'migration_failed' and error.value.details['problems']
    assert doc == original
    monkeypatch.undo()
    migrated, report = migration.migrate_document(doc)
    assert doc == original and report['to_version'] == 2
    assert migration.migrate_document(migrated) == (migrated, None)


def test_v1_source_folders_pack_deterministically_and_unpack_omits_history(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(FIXTURES / 'schema-v1-source', source)
    first, second = tmp_path / 'a.byax', tmp_path / 'b.byax'
    result = pack(source, first)
    pack(source, second)
    assert first.read_bytes() == second.read_bytes()
    assert result['migration']['from_version'] == 1 and not result['source_modified']
    packed = load_document(first)
    assert packed['schema_version'] == 2 and packed['history']['revisions'] == [] and packed['requests'] == {}
    out_a, out_b = tmp_path / 'out-a', tmp_path / 'out-b'
    unpacked = unpack(V1_PACKAGE, out_a)
    unpack(V1_PACKAGE, out_b)
    assert unpacked['migration']['to_version'] == 2 and not unpacked['history_included']
    for name in ('document.json', 'manifest.json'):
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes()
    written = json.loads((out_a / 'document.json').read_bytes())
    assert written['history']['revisions'] == [] and written['requests'] == {}
    assert written['schema_version'] == 2 and written['dependencies']
