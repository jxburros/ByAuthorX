"""Partial syncs and delayed file appearance: opening and saving stay safe and lose no data."""

import os
import random

import pytest

from byax.cli import execute
from byax.errors import ByaxError
from byax.storage import (
    DocumentLock,
    file_stamp,
    load_document,
    open_document,
    save_document,
)
from byax.sync_safety import sync_status


def make_document(path, text='Alpha\nBeta\nGamma'):
    execute('new', {'out': str(path), 'text': text, 'overwrite': True})
    return path


def staging_files(folder):
    return sorted(p.name for p in folder.iterdir() if p.name.startswith('.') and p.name.endswith('.tmp'))


# -- Truncated and damaged packages -----------------------------------------------------------


def test_a_package_truncated_at_any_point_is_refused_clearly_and_never_modified(tmp_path):
    whole = make_document(tmp_path / 'whole.byax').read_bytes()
    target = tmp_path / 'partial.byax'
    cuts = sorted(
        {0, 1, 2, 4, 22, len(whole) - 22, len(whole) - 1, *range(0, len(whole), max(1, len(whole) // 60))}
    )
    for cut in cuts:
        target.write_bytes(whole[:cut])
        for reader in (load_document, open_document):
            with pytest.raises(ByaxError) as error:
                reader(target)
            assert error.value.code == 'invalid_input', cut
            assert 'Cannot read native package' in error.value.message
        assert target.read_bytes() == whole[:cut]
    target.write_bytes(whole)
    assert load_document(target)['blocks'][0]['text'] == 'Alpha'


def test_zero_byte_and_truncated_files_explain_that_sync_may_be_unfinished(tmp_path):
    whole = make_document(tmp_path / 'whole.byax').read_bytes()
    target = tmp_path / 'chapter.byax'
    target.write_bytes(b'')
    with pytest.raises(ByaxError) as error:
        load_document(target)
    assert error.value.details['reason'] == 'zero_bytes' and 'empty' in error.value.message
    assert 'sync' in error.value.message.lower()
    target.write_bytes(whole[: len(whole) // 2])
    with pytest.raises(ByaxError) as error:
        execute('inspect', {'path': str(target)})
    assert 'incomplete or damaged' in error.value.message and 'not changed' in error.value.message
    # A CLI edit of such a file fails before anything is written.
    with pytest.raises(ByaxError):
        execute(
            'apply',
            {
                'path': str(target),
                'input': {'operations': [{'type': 'set_metadata', 'values': {'title': 'x'}}]},
            },
        )
    assert target.read_bytes() == whole[: len(whole) // 2]
    assert staging_files(tmp_path) == []


def test_mixed_old_and_new_content_and_flipped_bytes_only_ever_raise_structured_errors(tmp_path):
    old = make_document(tmp_path / 'old.byax', 'Old text\nMore old text').read_bytes()
    new = make_document(tmp_path / 'new.byax', 'New text that is a little longer\n' * 30).read_bytes()
    generator = random.Random(20261016)
    target = tmp_path / 'mixed.byax'
    outcomes = {'loaded': 0, 'refused': 0}
    for round_ in range(300):
        if round_ % 3 == 0:
            cut = generator.randrange(1, min(len(old), len(new)))
            data = old[:cut] + new[cut:]
        elif round_ % 3 == 1:
            data = bytearray(new)
            for _ in range(generator.randrange(1, 6)):
                data[generator.randrange(len(data))] ^= 1 << generator.randrange(8)
            data = bytes(data)
        else:
            start = generator.randrange(len(new))
            data = (
                new[:start]
                + bytes(generator.randrange(256) for _ in range(generator.randrange(1, 40)))
                + new[start:]
            )
        target.write_bytes(data)
        try:
            load_document(target)
            outcomes['loaded'] += 1
        except ByaxError:
            outcomes['refused'] += 1
        assert target.read_bytes() == data
    assert outcomes['refused'] > 0


def test_status_flags_an_incomplete_document_instead_of_raising(tmp_path):
    whole = make_document(tmp_path / 'whole.byax').read_bytes()
    target = tmp_path / 'chapter.byax'
    target.write_bytes(whole[: len(whole) - 30])
    status = sync_status(target, env={}, home=tmp_path)
    assert status['attention'] and {r['code'] for r in status['risks']} >= {'document_incomplete'}
    target.write_bytes(b'')
    status = sync_status(target, env={}, home=tmp_path)
    assert {r['code'] for r in status['risks']} >= {'file_not_available'}


# -- Placeholders -----------------------------------------------------------------------------


def test_an_icloud_placeholder_is_not_mistaken_for_a_missing_or_corrupt_file(tmp_path):
    (tmp_path / '.chapter.byax.icloud').write_bytes(b'<plist/>')
    with pytest.raises(ByaxError) as error:
        load_document(tmp_path / 'chapter.byax')
    assert error.value.details['reason'] == 'icloud_placeholder'
    assert 'iCloud' in error.value.message and 'not downloaded' in error.value.message
    with pytest.raises(ByaxError) as error:
        execute('inspect', {'path': str(tmp_path / 'chapter.byax')})
    assert 'iCloud' in error.value.message
    assert not (tmp_path / 'chapter.byax').exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.chapter.byax.icloud']


def test_a_missing_file_without_a_placeholder_is_still_a_plain_error(tmp_path):
    with pytest.raises(ByaxError) as error:
        load_document(tmp_path / 'never.byax')
    assert error.value.code == 'invalid_input' and 'reason' not in error.value.details


# -- Files that appear or change after open ----------------------------------------------------


def test_a_file_replaced_by_sync_after_open_is_never_overwritten(tmp_path):
    path = make_document(tmp_path / 'chapter.byax')
    doc = load_document(path)
    stamp = file_stamp(path)
    # The sync client delivers another computer's newer version while this one holds the old read.
    delivered = make_document(tmp_path / 'delivered.byax', 'Delivered from the other computer')
    os.replace(delivered, path)
    delivered_bytes = path.read_bytes()
    doc['blocks'][0]['text'] = 'Local edit'
    with pytest.raises(ByaxError) as error:
        save_document(doc, path, expected_stamp=stamp)
    assert error.value.code == 'conflict' and 'Nothing was overwritten' in error.value.message
    assert path.read_bytes() == delivered_bytes
    assert staging_files(tmp_path) == []
    # Saving under another name preserves both versions.
    save_document(doc, tmp_path / 'chapter-local.byax')
    assert load_document(tmp_path / 'chapter-local.byax')['blocks'][0]['text'] == 'Local edit'
    assert load_document(path)['blocks'][0]['text'] == 'Delivered from the other computer'


def test_a_matching_stamp_saves_and_a_file_that_appears_late_is_a_conflict(tmp_path):
    path = make_document(tmp_path / 'chapter.byax')
    doc = load_document(path)
    save_document(doc, path, expected_stamp=file_stamp(path))
    assert load_document(path)['id'] == doc['id']
    fresh = tmp_path / 'later.byax'
    assert file_stamp(fresh) is None
    brand_new = load_document(path)
    save_document(brand_new, fresh, expected_stamp=None)
    assert fresh.exists()
    # The same path now exists although the caller expected it absent: it appeared after the check.
    with pytest.raises(ByaxError) as error:
        save_document(brand_new, fresh, expected_stamp=None)
    assert error.value.code == 'conflict'
    assert load_document(fresh)['id'] == doc['id']


def test_a_document_that_arrives_after_a_failed_open_opens_normally_later(tmp_path):
    target = tmp_path / 'chapter.byax'
    with pytest.raises(ByaxError):
        execute('inspect', {'path': str(target)})
    (tmp_path / 'chapter.byax').write_bytes(b'')
    with pytest.raises(ByaxError):
        execute('inspect', {'path': str(target)})
    make_document(target)
    assert execute('inspect', {'path': str(target)})['items'][0]['text'] == 'Alpha'


# -- Interrupted atomic replace ----------------------------------------------------------------


def test_a_failed_replace_leaves_the_original_and_no_staging_file(tmp_path, monkeypatch):
    path = make_document(tmp_path / 'chapter.byax')
    original = path.read_bytes()
    doc = load_document(path)
    doc['blocks'][0]['text'] = 'Never saved'

    def locked_by_sync_client(*args, **kwargs):
        raise PermissionError(13, 'The process cannot access the file because it is being used')

    monkeypatch.setattr(os, 'replace', locked_by_sync_client)
    with pytest.raises(PermissionError):
        save_document(doc, path)
    monkeypatch.undo()
    assert path.read_bytes() == original and staging_files(tmp_path) == []
    with DocumentLock(path) as lock:
        save_document(doc, path, owner=lock.owner)
    assert load_document(path)['blocks'][0]['text'] == 'Never saved'


def test_a_staging_file_left_by_a_crash_is_harmless_and_never_deleted_silently(tmp_path):
    path = make_document(tmp_path / 'chapter.byax')
    whole = path.read_bytes()
    stale = tmp_path / '.chapter.byax.k3j2h1g0.tmp'
    stale.write_bytes(whole[: len(whole) // 3])
    assert load_document(path)['blocks'][0]['text'] == 'Alpha'
    doc = load_document(path)
    doc['blocks'][0]['text'] = 'Saved after the crash'
    save_document(doc, path)
    assert load_document(path)['blocks'][0]['text'] == 'Saved after the crash'
    assert stale.read_bytes() == whole[: len(whole) // 3]
    status = sync_status(path, env={}, home=tmp_path)
    leftovers = [r for r in status['risks'] if r['code'] == 'staging_file_left_behind']
    assert [r['path'] for r in leftovers] == [str(stale)]
    # Another save does not mistake the leftover for a competing document.
    assert staging_files(tmp_path) == [stale.name]


def test_older_schema_upgrade_copy_is_not_confused_with_a_conflicted_copy(tmp_path):
    from byax.sync_safety import find_conflicted_copies

    path = make_document(tmp_path / 'chapter.byax')
    (tmp_path / 'chapter.schema1-0123456789ab.byax').write_bytes(path.read_bytes())
    assert find_conflicted_copies(path)['copies'] == []
