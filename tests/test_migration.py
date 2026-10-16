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
from byax.model import SCHEMA_VERSION, new_document, validate_document
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
    assert doc['schema_version'] == SCHEMA_VERSION and report['from_version'] == 1
    assert report['to_version'] == SCHEMA_VERSION and [s['from'] for s in report['steps']] == [1, 2]
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
        assert state['schema_version'] == SCHEMA_VERSION and 'dependencies' in state
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
        assert json.loads(archive.read('manifest.json'))['schema_versions'] == {'document': SCHEMA_VERSION}
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
    assert load_document(tmp_path / 'upgraded.byax')['schema_version'] == SCHEMA_VERSION
    with pytest.raises(ByaxError) as error:
        migrate_package(path, tmp_path / 'upgraded.byax')
    assert error.value.code == 'conflict'


def test_future_versions_are_rejected_without_destructive_save(tmp_path):
    path = _v1_copy(tmp_path, 'future.byax')

    def future(manifest, document):
        manifest['schema_versions'] = {'document': SCHEMA_VERSION + 1}
        document['schema_version'] = SCHEMA_VERSION + 1

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
    assert doc == original and report['to_version'] == SCHEMA_VERSION
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
    assert (
        packed['schema_version'] == SCHEMA_VERSION
        and packed['history']['revisions'] == []
        and packed['requests'] == {}
    )
    out_a, out_b = tmp_path / 'out-a', tmp_path / 'out-b'
    unpacked = unpack(V1_PACKAGE, out_a)
    unpack(V1_PACKAGE, out_b)
    assert unpacked['migration']['to_version'] == SCHEMA_VERSION and not unpacked['history_included']
    for name in ('document.json', 'manifest.json'):
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes()
    written = json.loads((out_a / 'document.json').read_bytes())
    assert written['history']['revisions'] == [] and written['requests'] == {}
    assert written['schema_version'] == SCHEMA_VERSION and written['dependencies']


# Schema 2 -> 3: optional manuscript binder and citation prefix/suffix text.
V2_PACKAGE = FIXTURES / 'schema-v2-manuscript.byax'


def _v2_copy(tmp_path, name='manuscript.byax'):
    target = tmp_path / name
    target.write_bytes(V2_PACKAGE.read_bytes())
    return target


def test_schema_v2_fixture_opens_migrated_with_history_restorable(tmp_path):
    path = _v2_copy(tmp_path)
    original = path.read_bytes()
    doc, report = open_document(path)
    assert path.read_bytes() == original
    assert doc['schema_version'] == SCHEMA_VERSION == 3
    assert report['from_version'] == 2 and [s['from'] for s in report['steps']] == [2]
    kinds = {t['kind']: t for t in report['steps'][0]['transformations']}
    assert kinds['citation_context_fields']['count'] == 1 and kinds['added_optional_structure']['count'] == 0
    assert 'project' not in doc  # the binder is optional and created explicitly
    assert doc['x_extension'] == {'vendor': 'future-tool', 'payload': [4, 5, 6]}
    assert report['history_revisions_migrated'] == len(doc['history']['revisions']) == 8
    for record in doc['history']['revisions']:
        assert engine._revision(doc, record['revision'])['schema_version'] == SCHEMA_VERSION
    draft = engine.revision_document(doc, 'draft one')
    assert next(b for b in draft['blocks'] if b['id'] == 'p_s2a')['text'] == (
        'The keeper did not look up from his lamp.'
    )
    tracked = next(
        r['revision']
        for r in doc['history']['revisions']
        if [o['type'] for o in r['operations']] == ['insert_text']
    )
    restored, _ = engine.restore(doc, tracked)
    assert restored['settings']['track_changes'] and restored['schema_version'] == SCHEMA_VERSION
    # Review anchors, citation affixes and replay records survive.
    mark = next(m for b in doc['blocks'] for m in b['marks'] if m['type'] == 'citation')
    assert (mark['prefix'], mark['suffix'], mark['quote']) == ('see ', ', ch. 2', 'Fresnel lenses')
    assert doc['review']['comments'][0]['replies'][0]['text'] == 'The ferry.'
    assert doc['settings']['bookmarks'] == {'gulls': {'target': 'p_s1b', 'offset': 16}}
    assert set(doc['requests']) == {'req-review', 'req-cite'}
    change = next(c for c in doc['review']['tracked_changes'] if c['target'] == 'p_s2a')
    rejected, _ = apply(doc, {'operations': [{'type': 'reject_change', 'target': change['id']}]})
    assert next(b for b in rejected['blocks'] if b['id'] == 'p_s2a')['text'] == (
        'The keeper did not look up from his lamp.'
    )
    # The migrated manuscript can become a project; history keeps the pre-binder state.
    binder, _ = apply(doc, {'operations': [{'type': 'project_init'}]})
    assert len(binder['project']['items']) == 7
    assert 'project' not in engine.revision_document(binder, doc['revision'])


def test_saving_migrated_v2_package_preserves_original_once(tmp_path):
    path = _v2_copy(tmp_path)
    original = path.read_bytes()
    doc, _ = open_document(path)
    doc, _ = apply(doc, {'operations': [{'type': 'project_init'}]})
    backup = Path(save_document(doc, path)['preserved_original'])
    assert backup.read_bytes() == original and backup.name.startswith('manuscript.schema2-')
    reopened = load_document(path)
    assert reopened['schema_version'] == 3 and reopened['project'] == doc['project']
    assert save_document(reopened, path)['preserved_original'] is None
    assert open_document(backup)[1]['from_version'] == 2


def test_v2_unmigratable_constructs_are_rejected_without_writes(tmp_path):
    clash = _v2_copy(tmp_path, 'clash.byax')
    _rewrite(clash, lambda manifest, document: document.update(project={'chapters': ['unknown']}))
    before = clash.read_bytes()
    with pytest.raises(ByaxError) as error:
        load_document(clash)
    assert error.value.code == 'unsupported_feature' and error.value.details['field'] == 'project'
    with pytest.raises(ByaxError):
        migrate_package(clash, tmp_path / 'never.byax')
    assert clash.read_bytes() == before and not (tmp_path / 'never.byax').exists()

    def bad_affix(manifest, document):
        mark = next(m for b in document['blocks'] for m in b['marks'] if m['type'] == 'citation')
        mark['prefix'] = {'not': 'text'}

    affix = _v2_copy(tmp_path, 'affix.byax')
    _rewrite(affix, bad_affix)
    with pytest.raises(ByaxError) as error:
        load_document(affix)
    assert error.value.code == 'unsupported_feature'
    # A current-schema document with a non-text affix is a schema error, not a silent fix.
    current = load_document(_v2_copy(tmp_path, 'current.byax'))
    mark = next(m for b in current['blocks'] for m in b['marks'] if m['type'] == 'citation')
    mark['suffix'] = 7
    with pytest.raises(ByaxError) as error:
        validate_document(current)
    assert error.value.code == 'schema_error'


def test_newer_schema_is_refused_and_older_build_cannot_overwrite(tmp_path):
    path = _v2_copy(tmp_path, 'newer.byax')

    def newer(manifest, document):
        manifest['schema_versions'] = {'document': SCHEMA_VERSION + 1}
        document['schema_version'] = SCHEMA_VERSION + 1

    _rewrite(path, newer)
    before = path.read_bytes()
    with pytest.raises(ByaxError) as error:
        open_document(path)
    assert error.value.code == 'unsupported_version'
    with pytest.raises(ByaxError):
        save_document(new_document(text='older writer'), path)
    assert path.read_bytes() == before


def test_v2_source_folder_packs_deterministically(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(FIXTURES / 'schema-v2-source', source)
    first, second = tmp_path / 'a.byax', tmp_path / 'b.byax'
    result = pack(source, first)
    pack(source, second)
    assert first.read_bytes() == second.read_bytes()
    assert result['migration']['from_version'] == 2 and not result['source_modified']
    packed = load_document(first)
    assert packed['schema_version'] == SCHEMA_VERSION and packed['x_extension']['vendor'] == 'future-tool'


def test_migration_gate_rejects_v2_transition_that_breaks_references(monkeypatch):
    with zipfile.ZipFile(V2_PACKAGE) as archive:
        doc = json.loads(archive.read('document.json'))

    def lossy(state):
        state['references'] = {}
        return migration._v2_to_v3(state)

    monkeypatch.setitem(migration.TRANSITIONS, 2, (3, 'lossy', lossy))
    with pytest.raises(ByaxError) as error:
        migration.migrate_document(doc)
    assert error.value.code == 'migration_failed'
    assert ['reference', 'p_notes', 'stevenson'] in error.value.details['problems']
