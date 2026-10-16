"""Sync-folder safety: provider detection, risk reports, conflicted copies and reviewable merges."""

import json
import shutil
import threading
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from byax import engine, sync_safety
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.mcp import handle
from byax.service import make_server
from byax.storage import DocumentLock, load_document, save_document
from byax.sync_safety import (
    ARCHIVE_FOLDER,
    archive_conflicted_copy,
    detect_sync_folder,
    find_conflicted_copies,
    placeholder_state,
    prepare_merge,
    sync_risks,
    sync_status,
)

TEXT = 'Chapter one\nThe first section text.\nChapter two\nThe second section text.'
TOKEN = 'sync-safety-token-with-32-characters'


def edit(path, target, text):
    execute(
        'apply',
        {'path': str(path), 'input': {'operations': [{'type': 'set_text', 'target': target, 'text': text}]}},
    )


def make_pair(folder, copy_name="Novel (Jeff's conflicted copy 2026-10-14).byax"):
    """A document and a byte copy, as a sync client leaves them when two computers edit it."""
    folder.mkdir(parents=True, exist_ok=True)
    document = folder / 'Novel.byax'
    execute('new', {'out': str(document), 'text': TEXT, 'overwrite': True})
    copy = folder / copy_name
    shutil.copy(document, copy)
    ids = [b['id'] for b in load_document(document)['blocks']]
    return document, copy, ids


def merge(document, copy):
    return execute('sync', {'path': str(document), 'action': 'merge-conflict', 'copy_path': str(copy)})


def merge_proposal(document):
    return next(p for p in reversed(load_document(document)['review']['proposals']) if p['kind'] == 'merge')


def accept(document, proposal):
    execute('review', {'path': str(document), 'action': 'accept', 'target': proposal})


def texts(document):
    return [b['text'] for b in load_document(document)['blocks']]


# -- Detection --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('folder', 'provider', 'confidence'),
    [
        ('Dropbox', 'dropbox', 'medium'),
        ('Dropbox (Personal)', 'dropbox', 'medium'),
        ('Library/Mobile Documents/com~apple~CloudDocs/Writing', 'icloud', 'high'),
        ('iCloud Drive', 'icloud', 'medium'),
        ('iCloudDrive', 'icloud', 'medium'),
        ('OneDrive', 'onedrive', 'medium'),
        ('OneDrive - Contoso Ltd', 'onedrive', 'medium'),
        ('Library/CloudStorage/OneDrive-Personal', 'onedrive', 'high'),
        ('Library/CloudStorage/GoogleDrive-me@example.com/My Drive', 'google_drive', 'high'),
        ('My Drive', 'google_drive', 'medium'),
        ('Library/CloudStorage/Box-Box', 'box', 'high'),
        ('Box Sync', 'box', 'medium'),
        ('Nextcloud', 'nextcloud', 'medium'),
        ('ownCloud', 'owncloud', 'medium'),
        ('pCloudDrive', 'pcloud', 'medium'),
    ],
)
def test_providers_are_detected_from_folder_patterns(tmp_path, folder, provider, confidence):
    document = tmp_path / 'home' / folder / 'Novels' / 'Novel.byax'
    document.parent.mkdir(parents=True)
    report = detect_sync_folder(document, env={}, home=tmp_path / 'home')
    assert report['synced'] and report['provider'] == provider and report['confidence'] == confidence
    assert report['evidence'] and report['root'] and report['candidates'][0]['provider'] == provider


def test_an_ordinary_folder_is_not_reported_as_synced(tmp_path):
    document = tmp_path / 'Documents' / 'Novel.byax'
    document.parent.mkdir()
    report = detect_sync_folder(document, env={}, home=tmp_path)
    assert report == {
        'synced': False,
        'provider': None,
        'provider_name': None,
        'confidence': 'none',
        'evidence': [],
        'root': None,
        'candidates': [],
    }


@pytest.mark.parametrize(
    ('marker', 'is_dir', 'provider'),
    [
        ('.dropbox', False, 'dropbox'),
        ('.dropbox.cache', True, 'dropbox'),
        ('.stfolder', True, 'syncthing'),
        ('.nextcloudsync.log', False, 'nextcloud'),
        ('._sync_0123abcd.db', False, 'nextcloud'),
        ('.owncloudsync.log', False, 'owncloud'),
        ('.csync_journal.db', False, 'owncloud'),
    ],
)
def test_marker_files_in_an_ancestor_identify_the_provider_with_high_confidence(
    tmp_path, marker, is_dir, provider
):
    root = tmp_path / 'Anywhere'
    deep = root / 'a' / 'b'
    deep.mkdir(parents=True)
    (root / marker).mkdir() if is_dir else (root / marker).write_text('x')
    report = detect_sync_folder(deep / 'Novel.byax', env={}, home=tmp_path)
    assert (report['provider'], report['confidence'], report['root']) == (provider, 'high', str(root))
    assert any(marker in line for line in report['evidence'])


def test_windows_style_environment_variables_and_dropbox_info_json(tmp_path):
    work = tmp_path / 'Users' / 'me' / 'Contoso' / 'Docs'
    work.mkdir(parents=True)
    env = {'OneDriveCommercial': str(tmp_path / 'Users' / 'me' / 'Contoso')}
    report = detect_sync_folder(work / 'Novel.byax', env=env, home=tmp_path / 'nohome')
    assert report['provider'] == 'onedrive' and report['confidence'] == 'high'
    assert 'OneDriveCommercial' in report['evidence'][0]
    elsewhere = tmp_path / 'D' / 'Work'
    elsewhere.mkdir(parents=True)
    info = tmp_path / 'AppData' / 'Dropbox'
    info.mkdir(parents=True)
    (info / 'info.json').write_text(json.dumps({'personal': {'path': str(elsewhere)}}))
    report = detect_sync_folder(
        elsewhere / 'Novel.byax', env={'APPDATA': str(tmp_path / 'AppData')}, home=tmp_path
    )
    assert report['provider'] == 'dropbox' and report['confidence'] == 'high'
    assert 'info.json' in report['evidence'][0]


def test_the_nearest_strongest_candidate_wins_and_all_are_listed(tmp_path):
    root = tmp_path / 'OneDrive' / 'Shared'
    (root / 'Dropbox').mkdir(parents=True)
    (root / 'Dropbox' / '.dropbox').write_text('')
    report = detect_sync_folder(root / 'Dropbox' / 'Novel.byax', env={}, home=tmp_path)
    assert report['provider'] == 'dropbox' and report['confidence'] == 'high'
    assert {c['provider'] for c in report['candidates']} == {'dropbox', 'onedrive'}


def test_detection_is_local_only(tmp_path, monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError('sync detection must not touch the network')

    monkeypatch.setattr(socket, 'socket', refuse)
    (tmp_path / 'Dropbox').mkdir()
    assert detect_sync_folder(tmp_path / 'Dropbox' / 'x.byax', env={}, home=tmp_path)['synced']


# -- Conflicted copies ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('name', 'provider'),
    [
        ("Novel (Jeff's conflicted copy 2026-10-14).byax", 'dropbox'),
        ('Novel (Jeff’s conflicted copy 2026-10-14) (1).byax', 'dropbox'),
        ('Novel (Case Conflict).byax', 'dropbox'),
        ('Novel-DESKTOP-ABC123.byax', 'onedrive'),
        ('Novel-DESKTOP-ABC123-1.byax', 'onedrive'),
        ('Novel-Jeffs-MacBook-Pro.byax', 'onedrive'),
        ('Novel 2.byax', 'icloud'),
        ('Novel (1).byax', 'google_drive'),
        ('Novel (conflicted copy 2026-10-14 101500).byax', 'nextcloud'),
        ('Novel_conflict-20261014-101500.byax', 'owncloud'),
        ('Novel.sync-conflict-20261014-101500-ABCDEFG.byax', 'syncthing'),
        ('NOVEL (2).byax', 'google_drive'),
    ],
)
def test_each_providers_conflict_naming_is_recognised(tmp_path, name, provider):
    document, _copy, _ = make_pair(tmp_path, name)
    found = find_conflicted_copies(document)
    assert [c['name'] for c in found['copies']] == [name]
    entry = found['copies'][0]
    assert entry['provider'] == provider and provider in entry['providers']
    assert entry['identity'] == 'same_document' and entry['confidence'] in ('high', 'medium')
    assert entry['document_id'] == found['document_id'] and not found['pending'] and not found['skipped']


def test_unrelated_names_are_not_candidates(tmp_path):
    document, _, _ = make_pair(tmp_path, 'Novel 2.byax')
    for name in ('Novella.byax', 'Novel notes.byax', 'Novel copy.byax', 'Novel-2.byax', 'Other (1).byax'):
        shutil.copy(document, tmp_path / name)
    (tmp_path / 'Novel.byax.bak').write_bytes(b'x')
    (tmp_path / 'Novel (1).txt').write_text('not a document')
    assert [c['name'] for c in find_conflicted_copies(document)['copies']] == ['Novel 2.byax']


def test_a_matching_name_must_share_the_document_identity(tmp_path):
    document, _copy, _ = make_pair(tmp_path, 'Novel (1).byax')
    other = tmp_path / 'Novel (2).byax'
    execute('new', {'out': str(other), 'text': 'A different book'})
    junk = tmp_path / 'Novel (3).byax'
    junk.write_bytes(b'not a zip at all, but sizeable' * 20)
    found = find_conflicted_copies(document)
    assert [c['name'] for c in found['copies']] == ['Novel (1).byax']
    skipped = {s['name']: s for s in found['skipped']}
    assert skipped['Novel (2).byax']['identity'] == 'different_document'
    # Bytes that are not a package look exactly like a download in progress, so they wait rather than merge.
    assert [p['name'] for p in found['pending']] == ['Novel (3).byax']
    with pytest.raises(ByaxError) as error:
        execute('sync', {'path': str(document), 'action': 'merge-conflict', 'copy_path': str(other)})
    assert error.value.code == 'conflict'
    assert 'Novel (2)' not in json.dumps(load_document(document)['review'])


def test_files_still_syncing_are_pending_not_offered(tmp_path):
    document, copy, _ = make_pair(tmp_path, 'Novel (1).byax')
    (tmp_path / 'Novel 2.byax').write_bytes(b'')
    (tmp_path / 'Novel 3.byax').write_bytes(copy.read_bytes()[: copy.stat().st_size // 2])
    (tmp_path / '.Novel 4.byax.icloud').write_bytes(b'placeholder')
    found = find_conflicted_copies(document)
    assert [c['name'] for c in found['copies']] == ['Novel (1).byax']
    pending = {p['name']: p for p in found['pending']}
    assert pending['Novel 2.byax']['reason'] == 'zero_bytes'
    assert pending['Novel 3.byax']['reason'] == 'incomplete'
    assert pending['Novel 4.byax']['reason'] == 'icloud_placeholder'
    status = sync_status(document)
    assert any(r['code'] == 'conflicted_copy_still_syncing' for r in status['risks'])
    for name in ('Novel 2.byax', 'Novel 3.byax'):
        with pytest.raises(ByaxError) as error:
            execute(
                'sync', {'path': str(document), 'action': 'merge-conflict', 'copy_path': str(tmp_path / name)}
            )
        assert error.value.code == 'pending'


def test_nothing_is_offered_when_the_document_itself_is_not_readable(tmp_path):
    document, _copy, _ = make_pair(tmp_path, 'Novel (1).byax')
    document.write_bytes(b'')
    found = find_conflicted_copies(document)
    assert found['copies'] == [] and found['document_state']['state'] == 'pending'


# -- Merging ----------------------------------------------------------------------------------


def test_edits_to_different_sections_merge_with_no_loss_through_review(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[1], 'The first section text, revised on the laptop.')
    edit(copy, ids[3], 'The second section text, revised on the desktop.')
    copy_bytes = copy.read_bytes()
    found = execute('sync', {'path': str(document), 'action': 'conflicts'})
    assert found['copies'][0]['relation'] == 'diverged' and found['copies'][0]['merge_base']
    result = merge(document, copy)
    assert result['sync']['conflicts'] == 0 and result['sync']['already_proposed'] is False
    assert result['review'] == 'required'
    # The merge is only a proposal: the document still holds just the laptop edit, the copy is untouched.
    assert texts(document)[1:] == [
        'The first section text, revised on the laptop.',
        'Chapter two',
        TEXT.split('\n')[3],
    ]
    assert copy.read_bytes() == copy_bytes
    proposal = merge_proposal(document)
    assert proposal['merge']['source_revision'] == found['copies'][0]['revision']
    assert proposal['merge']['base_revision'] == found['copies'][0]['merge_base']
    accept(document, proposal['id'])
    assert texts(document) == [
        'Chapter one',
        'The first section text, revised on the laptop.',
        'Chapter two',
        'The second section text, revised on the desktop.',
    ]
    assert copy.read_bytes() == copy_bytes and copy.exists()
    merged = load_document(document)
    assert merged['history']['parents'][merged['revision']][1] == proposal['merge']['source_revision']


def test_structural_edits_on_both_sides_are_kept(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    execute(
        'apply',
        {
            'path': str(copy),
            'input': {
                'operations': [{'type': 'insert_paragraph', 'text': 'Added on the desktop.', 'after': ids[3]}]
            },
        },
    )
    edit(document, ids[0], 'Chapter one, retitled')
    merge(document, copy)
    accept(document, merge_proposal(document)['id'])
    assert texts(document) == [
        'Chapter one, retitled',
        TEXT.split('\n')[1],
        'Chapter two',
        TEXT.split('\n')[3],
        'Added on the desktop.',
    ]


def test_edits_to_the_same_section_conflict_and_resolve_explicitly(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[3], 'The second part text.')
    edit(copy, ids[3], 'The second chapter text.')
    result = merge(document, copy)
    assert result['sync']['conflicts'] == 1
    proposal = merge_proposal(document)
    conflict = proposal['conflicts'][0]
    assert conflict['status'] == 'unresolved' and conflict['target'] == ids[3] and conflict['fields']
    assert texts(document)[3] == 'The second part text.'
    execute(
        'apply',
        {
            'path': str(document),
            'input': {
                'operations': [
                    {
                        'type': 'resolve_conflict',
                        'target': proposal['id'],
                        'conflict': conflict['id'],
                        'choice': 'theirs',
                    }
                ]
            },
        },
    )
    accept(document, proposal['id'])
    assert texts(document)[3] == 'The second chapter text.'
    assert copy.exists()


def test_keeping_ours_resolves_a_conflict_without_changing_the_text(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[3], 'The second part text.')
    edit(copy, ids[3], 'The second chapter text.')
    edit(copy, ids[1], 'The first section text, revised on the desktop.')
    merge(document, copy)
    proposal = merge_proposal(document)
    execute(
        'apply',
        {
            'path': str(document),
            'input': {
                'operations': [
                    {
                        'type': 'resolve_conflict',
                        'target': proposal['id'],
                        'conflict': 'conflict_1',
                        'choice': 'ours',
                    }
                ]
            },
        },
    )
    accept(document, proposal['id'])
    assert texts(document)[1:4:2] == [
        'The first section text, revised on the desktop.',
        'The second part text.',
    ]


def test_a_copy_that_is_simply_ahead_merges_cleanly(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(copy, ids[1], 'Only the desktop edited this.')
    assert find_conflicted_copies(document, analyze=True)['copies'][0]['relation'] == 'ahead'
    merge(document, copy)
    accept(document, merge_proposal(document)['id'])
    assert texts(document)[1] == 'Only the desktop edited this.'


def test_a_copy_with_nothing_new_is_refused_without_changes(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    for expected in ('identical', 'behind'):
        before = document.read_bytes()
        assert find_conflicted_copies(document, analyze=True)['copies'][0]['relation'] == expected
        with pytest.raises(ByaxError) as error:
            merge(document, copy)
        assert error.value.code == 'conflict' and document.read_bytes() == before
        edit(document, ids[1], 'The laptop moved on.')


def test_a_copy_without_shared_history_is_refused_clearly(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    doc = load_document(copy)
    doc['history'] = {'revisions': [], 'branches': {}, 'current_branch': 'main'}
    doc['revision'] = 'rev_unrelated'
    doc['blocks'][1]['text'] = 'History is gone.'
    save_document(doc, copy)
    edit(document, ids[3], 'The laptop moved on.')
    assert find_conflicted_copies(document, analyze=True)['copies'][0]['relation'] == 'unrelated_history'
    before = document.read_bytes()
    with pytest.raises(ByaxError) as error:
        merge(document, copy)
    assert error.value.code == 'missing_target' and 'manually' in error.value.message
    assert document.read_bytes() == before


def test_repeating_a_merge_reuses_the_pending_proposal(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[1], 'Laptop.')
    edit(copy, ids[3], 'Desktop.')
    first = merge(document, copy)
    before = document.read_bytes()
    second = merge(document, copy)
    assert (
        second['sync']['already_proposed'] is True and second['sync']['proposal'] == first['sync']['proposal']
    )
    assert document.read_bytes() == before


def test_dry_run_and_stale_revision_change_nothing(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[1], 'Laptop.')
    edit(copy, ids[3], 'Desktop.')
    before = document.read_bytes()
    result = execute(
        'sync', {'path': str(document), 'action': 'merge-conflict', 'copy_path': str(copy), 'dry_run': True}
    )
    assert result['execution'] == 'dry_run' and result['sync']['operations'] == 1
    assert document.read_bytes() == before
    with pytest.raises(ByaxError) as error:
        execute(
            'sync',
            {
                'path': str(document),
                'action': 'merge-conflict',
                'copy_path': str(copy),
                'expect_revision': 'rev_old',
            },
        )
    assert error.value.code == 'stale_revision' and document.read_bytes() == before


def test_imported_intermediate_revisions_are_reconstructable(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    for n in range(5):
        edit(copy, ids[1], f'Desktop revision {n}.')
    edit(document, ids[3], 'Laptop.')
    ours, theirs = load_document(document), load_document(copy)
    candidate, info = prepare_merge(ours, copy)
    assert info['imported_revisions'] >= 5 and candidate['revision'] == ours['revision']
    assert candidate['history']['branches'][info['branch']] == theirs['revision']
    assert candidate['history']['forks'][info['branch']] == info['merge_base']
    for record in theirs['history']['revisions']:
        assert engine._revision(candidate, record['revision']) == engine._revision(theirs, record['revision'])
    assert engine._revision(candidate, info['branch']) == engine._revision(theirs, theirs['revision'])
    # The inputs are not modified.
    assert 'conflicted' not in json.dumps(list(ours['history']['branches']))
    assert info['branch'] not in ours['history']['branches']


def test_a_small_history_limit_imports_only_what_the_merge_needs(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    for n in range(6):
        edit(copy, ids[1], f'Desktop revision {n}.')
    execute(
        'apply',
        {
            'path': str(document),
            'input': {'operations': [{'type': 'set_settings', 'values': {'history_limit': 4}}]},
        },
    )
    candidate, info = prepare_merge(load_document(document), copy)
    assert info['imported_revisions'] == 1
    assert len(candidate['history']['revisions']) <= 4


def test_import_branch_rejects_other_documents_and_duplicate_names(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(copy, ids[1], 'Desktop.')
    ours, theirs = load_document(document), load_document(copy)
    stranger = load_document(document)
    stranger['id'] = 'doc_other'
    with pytest.raises(ByaxError, match='different document'):
        engine.import_branch(ours, stranger, 'x')
    candidate, _ = engine.import_branch(ours, theirs, 'desk')
    with pytest.raises(ByaxError, match='already exists'):
        engine.import_branch(candidate, theirs, 'desk')
    with pytest.raises(ByaxError, match='empty'):
        engine.import_branch(ours, theirs, ' ')


# -- Archive ----------------------------------------------------------------------------------


def test_a_merged_copy_can_be_moved_but_never_before_the_merge_is_accepted(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[1], 'Laptop.')
    edit(copy, ids[3], 'Desktop.')
    copy_bytes = copy.read_bytes()
    with pytest.raises(ByaxError) as error:
        archive_conflicted_copy(document, copy)
    assert error.value.code == 'conflict' and copy.exists()
    merge(document, copy)
    with pytest.raises(ByaxError):
        execute('sync', {'path': str(document), 'action': 'archive-conflict', 'copy_path': str(copy)})
    assert copy.exists()
    accept(document, merge_proposal(document)['id'])
    moved = execute('sync', {'path': str(document), 'action': 'archive-conflict', 'copy_path': str(copy)})
    archived = Path(moved['moved_to'])
    assert archived.parent == tmp_path / ARCHIVE_FOLDER and archived.read_bytes() == copy_bytes
    assert not copy.exists()
    assert find_conflicted_copies(document)['copies'] == []
    assert sync_status(document)['attention'] is False


def test_archiving_never_overwrites_an_earlier_archived_file(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    archive = tmp_path / ARCHIVE_FOLDER
    archive.mkdir()
    (archive / copy.name).write_bytes(b'earlier')
    edit(copy, ids[3], 'Desktop.')
    merge(document, copy)
    accept(document, merge_proposal(document)['id'])
    moved = archive_conflicted_copy(document, copy)
    assert (archive / copy.name).read_bytes() == b'earlier'
    assert Path(moved['moved_to']).read_bytes() != b'earlier' and Path(moved['moved_to']).exists()


# -- Risk report and status -------------------------------------------------------------------


def test_status_reports_provider_conflicts_and_attention(tmp_path):
    folder = tmp_path / 'home' / 'Dropbox' / 'Books'
    document, copy, _ids = make_pair(folder)
    status = sync_status(document, env={}, home=tmp_path / 'home')
    assert status['kind'] == 'document' and status['sync']['provider'] == 'dropbox'
    assert status['attention'] and len(status['conflicts']['copies']) == 1
    codes = {r['code'] for r in status['risks']}
    assert 'lock_is_per_computer' in codes
    copy.unlink()
    quiet = sync_status(document, env={}, home=tmp_path / 'home')
    assert not quiet['attention'] and quiet['conflicts']['copies'] == []


def test_leftover_staging_backup_and_other_application_files_are_flagged(tmp_path):
    folder = tmp_path / 'Dropbox' / 'Books'
    document, copy, _ids = make_pair(folder)
    copy.unlink()
    (folder / '.Novel.byax.abc123.tmp').write_bytes(b'partial')
    (folder / 'Novel.byax.bak').write_bytes(b'old')
    (folder / '~$Novel.byax').write_bytes(b'')
    (folder / 'Novel.schema1-0123456789ab.byax').write_bytes(b'kept')
    risks = {r['code']: r for r in sync_risks(document, env={}, home=tmp_path)}
    assert risks['staging_file_left_behind']['severity'] == 'warning'
    assert risks['staging_file_left_behind']['size'] == 7
    assert risks['backup_in_synced_folder']['path'].endswith('Novel.byax.bak')
    assert risks['other_application_lock']['path'].endswith('~$Novel.byax')
    assert risks['preserved_original']['severity'] == 'info'
    plain = tmp_path / 'plain'
    plain.mkdir()
    shutil.copy(document, plain / 'Novel.byax')
    (plain / '.Novel.byax.abc123.tmp').write_bytes(b'partial')
    plain_risks = {r['code']: r for r in sync_risks(plain / 'Novel.byax', env={}, home=tmp_path)}
    assert plain_risks['staging_file_left_behind']['severity'] == 'info'


def test_lock_and_recovery_folders_inside_a_synced_folder_are_called_out(tmp_path, monkeypatch):
    synced = tmp_path / 'home' / 'Dropbox'
    synced.mkdir(parents=True)
    monkeypatch.setattr(sync_safety, 'user_cache_path', lambda *_: synced / 'cache')
    monkeypatch.setattr(sync_safety, 'user_data_path', lambda *_: synced / 'data')
    document, _, _ = make_pair(tmp_path / 'docs')
    codes = {r['code']: r for r in sync_risks(document, env={}, home=tmp_path / 'home')}
    assert codes['lock_folder_in_synced_folder']['severity'] == 'warning'
    assert codes['recovery_folder_in_synced_folder']['severity'] == 'warning'
    assert 'cache' in codes['lock_folder_in_synced_folder']['path']


def test_this_machines_lock_lives_outside_the_document_folder(tmp_path):
    folder = tmp_path / 'Dropbox'
    document, _, _ = make_pair(folder)
    with DocumentLock(document) as lock:
        save_document(load_document(document), document, owner=lock.owner)
        assert not [p for p in folder.rglob('*') if p.suffix == '.lock']
    assert not [p for p in folder.rglob('*') if p.suffix == '.lock']


def test_an_open_document_and_large_files_are_reported(tmp_path, monkeypatch):
    folder = tmp_path / 'Dropbox'
    document, copy, ids = make_pair(folder)
    copy.unlink()
    with DocumentLock(document):
        held = {r['code'] for r in sync_risks(document, env={}, home=tmp_path)}
    assert 'open_in_another_session' in held
    monkeypatch.setattr(sync_safety, 'LARGE_PACKAGE_BYTES', 100)
    monkeypatch.setattr(sync_safety, 'LARGE_HISTORY_REVISIONS', 2)
    for n in range(3):
        edit(document, ids[1], f'Revision {n}')
    risks = {r['code']: r for r in sync_risks(document, env={}, home=tmp_path)}
    assert risks['large_package']['severity'] == 'warning'
    assert risks['large_history']['revisions'] >= 3
    assert 'compact' in risks['large_history']['advice'].lower()


def test_light_status_skips_the_lock_probe_and_history_load(tmp_path, monkeypatch):
    document, copy, _ = make_pair(tmp_path / 'Dropbox')
    copy.unlink()
    monkeypatch.setattr(sync_safety, 'LARGE_HISTORY_REVISIONS', 0)
    with DocumentLock(document):
        full = {r['code'] for r in sync_status(document, env={}, home=tmp_path)['risks']}
        light = {r['code'] for r in sync_status(document, env={}, home=tmp_path, light=True)['risks']}
    assert {'open_in_another_session', 'large_history'} <= full
    assert not {'open_in_another_session', 'large_history'} & light


def test_a_missing_document_with_an_icloud_placeholder_is_reported_not_raised(tmp_path):
    (tmp_path / '.Novel.byax.icloud').write_bytes(b'plist')
    status = sync_status(tmp_path / 'Novel.byax', env={}, home=tmp_path)
    assert status['attention'] and status['risks'][0]['code'] == 'file_not_available'
    assert status['risks'][0]['kind'] == 'icloud_placeholder'
    with pytest.raises(ByaxError) as error:
        sync_status(tmp_path / 'Absent.byax')
    assert error.value.code == 'missing_target'


def test_online_only_attributes_are_recognised(tmp_path, monkeypatch):
    document, _, _ = make_pair(tmp_path)
    real = Path.stat

    class Marked:
        def __init__(self, status, **extra):
            self._status, self._extra = status, extra

        def __getattr__(self, name):
            return self._extra[name] if name in self._extra else getattr(self._status, name)

    def windows_recall(self, *args, **kwargs):
        status = real(self, *args, **kwargs)
        return Marked(status, st_file_attributes=0x400000) if self == document else status

    monkeypatch.setattr(Path, 'stat', windows_recall)
    assert placeholder_state(document)['kind'] == 'online_only'

    def mac_dataless(self, *args, **kwargs):
        status = real(self, *args, **kwargs)
        return Marked(status, st_flags=0x40000000) if self == document else status

    monkeypatch.setattr(Path, 'stat', mac_dataless)
    assert placeholder_state(document)['kind'] == 'online_only'
    monkeypatch.setattr(Path, 'stat', real)
    assert placeholder_state(document) is None


def test_folder_status_lists_documents_that_have_conflicted_copies(tmp_path):
    folder = tmp_path / 'OneDrive' / 'Books'
    document, _copy, _ids = make_pair(folder, 'Novel-DESKTOP-ABC123.byax')
    execute('new', {'out': str(folder / 'Quiet.byax'), 'text': 'Nothing wrong here'})
    status = sync_status(folder, env={}, home=tmp_path)
    assert status['kind'] == 'folder' and status['sync']['provider'] == 'onedrive' and status['attention']
    assert [d['path'] for d in status['documents_with_conflicts']] == [str(document)]
    assert status['documents'] == 2


# -- CLI, MCP and REST ------------------------------------------------------------------------


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


def mcp(command, arguments):
    reply = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_' + command, 'arguments': arguments},
        }
    )['result']
    return reply['structuredContent'], reply['isError']


def test_status_and_conflicts_are_identical_across_library_cli_mcp_and_rest(tmp_path, rest, capsys):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[1], 'Laptop.')
    edit(copy, ids[3], 'Desktop.')
    for arguments in (
        {'path': str(document), 'action': 'status'},
        {'path': str(document), 'action': 'conflicts'},
        {'path': str(tmp_path), 'action': 'status'},
    ):
        expected = execute('sync', arguments)
        structured, is_error = mcp('sync', arguments)
        status, body = rest('sync', arguments)
        code = main(['sync', arguments['path'], '--action', arguments['action']])
        cli = json.loads(capsys.readouterr().out)
        assert code == 0 and status == 200 and not is_error
        for other in (structured, body, cli):
            other = {k: v for k, v in other.items() if k != 'exit_code'}
            assert other == expected


def test_merge_works_through_mcp_rest_and_the_cli_process(tmp_path, rest, capsys):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[1], 'Laptop.')
    edit(copy, ids[3], 'Desktop.')
    status, body = rest(
        'sync', {'path': str(document), 'action': 'merge-conflict', 'copy_path': str(copy), 'dry_run': True}
    )
    assert status == 200 and body['execution'] == 'dry_run' and body['sync']['operations'] == 1
    structured, is_error = mcp(
        'sync', {'path': str(document), 'action': 'merge-conflict', 'copy_path': str(copy), 'author': 'MCP'}
    )
    assert not is_error and structured['sync']['proposal']
    assert main(['sync', str(document), '--action', 'merge-conflict', '--copy-path', str(copy)]) == 0
    assert json.loads(capsys.readouterr().out)['sync']['already_proposed'] is True
    assert merge_proposal(document)['author'] == 'MCP'


def test_rest_scopes_both_the_document_and_the_copy_path(tmp_path, rest):
    inside = tmp_path / 'inside.byax'
    outside_root = tmp_path.parent / (tmp_path.name + '-outside')
    outside_root.mkdir()
    execute('new', {'out': str(inside), 'text': 'x'})
    outsider = outside_root / 'copy.byax'
    execute('new', {'out': str(outsider), 'text': 'x'})
    for arguments in (
        {'path': str(outsider), 'action': 'status'},
        {'path': str(inside), 'action': 'merge-conflict', 'copy_path': str(outsider)},
        {'path': str(inside), 'action': 'archive-conflict', 'copy_path': str(outsider)},
    ):
        status, body = rest('sync', arguments)
        assert status == 400 and body['error']['code'] == 'invalid_input'
        assert 'outside authorized' in body['error']['message']


def test_argument_validation_and_unknown_actions(tmp_path):
    document, _, _ = make_pair(tmp_path)
    with pytest.raises(ByaxError, match='requires --copy-path'):
        execute('sync', {'path': str(document), 'action': 'merge-conflict'})
    with pytest.raises(ByaxError, match='Unknown sync action'):
        execute('sync', {'path': str(document), 'action': 'delete-everything'})
    with pytest.raises(ByaxError, match='Unknown argument'):
        execute('sync', {'path': str(document), 'force': True})


def test_the_merged_document_validates_and_round_trips(tmp_path):
    document, copy, ids = make_pair(tmp_path)
    edit(document, ids[1], 'Laptop.')
    edit(copy, ids[3], 'Desktop.')
    merge(document, copy)
    accept(document, merge_proposal(document)['id'])
    merged = load_document(document)
    save_document(merged, tmp_path / 'again.byax')
    assert load_document(tmp_path / 'again.byax') == merged
    assert zipfile.is_zipfile(copy)
