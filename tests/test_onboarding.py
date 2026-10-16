"""Sample project (regression fixture) and the first-run tour."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest

from byax import checks, epub, project, samples, storage
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.exchange import export_document
from byax.gui_onboarding import (
    NO_TOUR_ENV,
    TOUR_DISABLED_KEY,
    TOUR_SEEN_KEY,
    TOUR_STEPS,
    TOUR_VERSION,
    TourDialog,
    mark_tour_seen,
    step_text,
    tour_due,
)
from byax.mcp import handle
from byax.model import validate_document

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = samples.sample_path()


@pytest.fixture
def sample():
    return storage.load_document(BUNDLED)


# --- the sample project as a fixture ---------------------------------------------------------------


def test_sample_is_bundled_and_regenerates_to_the_same_document():
    assert BUNDLED.is_file() and BUNDLED.parent.name == 'samples'
    # A subprocess keeps the generator's pinned identities and clock out of this test process.
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'build_sample_project.py'), '--check'],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_sample_opens_validates_and_demonstrates_the_semantic_model(sample):
    validate_document(sample)
    assert sample['metadata']['title'] == 'The Lamp at Harrow Point'
    # Styles: a defined style that is actually applied.
    assert 'margin_note' in sample['styles']
    assert any(block['style'] == 'margin_note' for block in sample['blocks'])
    # Outline and binder: chapters, scenes, one note; statuses, synopses and targets are set.
    rows = project.items(sample)
    assert {row['kind'] for row in rows} == {'chapter', 'scene', 'note'}
    assert {row['status'] for row in rows} >= {'revised', 'draft', 'to_do'}
    assert sum(1 for row in rows if row.get('synopsis')) == 3 and sample['project']['target_words'] == 12000
    assert not project.orphaned_ids(sample)
    # A pending proposal, a comment, and a citation that points at a source record.
    proposals = sample['review']['proposals']
    assert len(proposals) == 1 and proposals[0]['status'] == 'pending'
    assert sample['review']['comments'] and not sample['review']['comments'][0].get('resolved')
    cited = [mark for block in sample['blocks'] for mark in block['marks'] if mark['type'] == 'citation']
    assert len(cited) == 1 and cited[0]['value'] in sample['references']
    assert sample['references'][cited[0]['value']]['csl']['title'].startswith('Annual Report')
    # A recorded checking report that is current.
    assert sample['review']['health']['validation'] == 'passed'
    assert not sample['review']['findings']


def test_sample_passes_every_default_check_without_findings(sample):
    report = checks.run_checks(sample)
    assert report['findings'] == [] and report['summary']['total'] == 0
    health = checks.health(sample)
    assert health['validation'] == 'passed' and not health['stale_units'] and not health['unresolved']


@pytest.mark.parametrize('suite', ['spelling', 'grammar', 'editorial', 'consistency', 'factual', 'layout'])
def test_sample_is_also_clean_under_the_prose_suites(sample, suite):
    # The advisory fiction craft flags (word echoes) are not part of "no findings": they are prompts to reread.
    assert checks.run_checks(sample, [suite])['findings'] == []


def test_sample_exports_to_the_formats_that_exist(sample, tmp_path):
    from docx import Document

    docx = tmp_path / 'sample.docx'
    report = export_document(sample, docx)
    assert not [loss for loss in report['losses'] if loss['severity'] == 'error']
    text = '\n'.join(p.text for p in Document(str(docx)).paragraphs)
    assert 'The Lamp at Harrow Point' in text and 'The boat came in on the evening tide' in text

    pdf = tmp_path / 'sample.pdf'
    export_document(sample, pdf)
    assert pdf.read_bytes()[:5] == b'%PDF-' and pdf.stat().st_size > 2000

    book = tmp_path / 'sample.epub'
    export_document(sample, book)
    verdict = epub.validate(book)
    assert verdict['valid'], verdict['errors']
    assert not verdict['warnings']
    with zipfile.ZipFile(book) as archive:
        assert b'Mara' in b''.join(archive.read(n) for n in archive.namelist() if n.endswith('.xhtml'))

    for name in ('sample.md', 'sample.html', 'sample.txt'):
        export_document(sample, tmp_path / name)
        assert (tmp_path / name).stat().st_size > 500


def test_sample_runs_through_the_cli_with_the_binder_commands():
    tree = execute('project', {'path': str(BUNDLED), 'action': 'tree'})
    assert tree['execution'] == 'completed'
    names = json.dumps(tree)
    assert 'Chapter one: Arrival' in names and 'The failing lamp' in names
    assert execute('check', {'path': str(BUNDLED)})['findings'] == []


# --- copying the sample --------------------------------------------------------------------------------


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_install_copies_into_a_folder_without_touching_the_bundled_file(tmp_path):
    before = digest(BUNDLED)
    first = samples.install_sample(tmp_path)
    second = samples.install_sample(tmp_path)
    assert Path(first['path']).name == 'ByAuthorX sample project.byax'
    assert Path(second['path']).name == 'ByAuthorX sample project (2).byax'
    assert digest(first['path']) == digest(second['path']) == first['sha256'] == before
    assert digest(BUNDLED) == before
    assert not list(tmp_path.glob('.*.tmp'))
    # Editing and saving the copy leaves the original alone.
    doc = storage.load_document(first['path'])
    doc['metadata']['title'] = 'Edited'
    storage.save_document(doc, first['path'])
    assert digest(BUNDLED) == before and digest(first['path']) != before


def test_install_to_a_named_file_never_overwrites(tmp_path):
    target = tmp_path / 'mine' / 'practice.byax'
    assert samples.install_sample(target)['path'] == str(target)
    with pytest.raises(ByaxError) as caught:
        samples.install_sample(target)
    assert caught.value.code == 'conflict'


def test_default_destination_is_the_documents_folder(monkeypatch, tmp_path):
    monkeypatch.setattr('platformdirs.user_documents_path', lambda *a, **k: tmp_path)
    assert samples.default_destination() == tmp_path / 'ByAuthorX'
    assert Path(samples.install_sample()['path']).parent == tmp_path / 'ByAuthorX'


def test_sample_command_over_cli_mcp_and_rest_policy(tmp_path, capsys):
    out = tmp_path / 'cli copy.byax'
    assert main(['sample', '--out', str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['sample']['path'] == str(out) and printed['outputs'] == [str(out)]
    assert digest(out) == digest(BUNDLED)
    mcp = handle(
        {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {'name': 'byax_sample', 'arguments': {}},
        }
    )['result']
    assert mcp['isError'] and 'output path' in mcp['content'][0]['text']
    via_mcp = tmp_path / 'mcp copy.byax'
    ok = handle(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {'name': 'byax_sample', 'arguments': {'out': str(via_mcp)}},
        }
    )['result']
    assert not ok.get('isError') and via_mcp.is_file()


# --- the tour ---------------------------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / 'onboarding.ini'), QSettings.IniFormat)


@pytest.fixture
def window(qtbot, monkeypatch, settings, tmp_path):
    from byax.gui import MainWindow

    monkeypatch.delenv(NO_TOUR_ENV, raising=False)
    monkeypatch.delenv('BYAX_REDUCED_MOTION', raising=False)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr(samples, 'default_destination', lambda: tmp_path / 'documents')
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    return win


def test_tour_has_four_to_six_short_steps_with_platform_key_names():
    assert 4 <= len(TOUR_STEPS) <= 6
    assert len({step[0] for step in TOUR_STEPS}) == len(TOUR_STEPS)
    for index in range(len(TOUR_STEPS)):
        title, body = step_text(index)
        assert title and 0 < len(body) < 400 and '%' not in body
    assert 'F7' in step_text(4)[1]


def test_tour_is_due_once_unless_a_newer_version_or_never_again(settings, monkeypatch):
    monkeypatch.delenv(NO_TOUR_ENV, raising=False)
    assert tour_due(settings)
    mark_tour_seen(settings)
    assert not tour_due(settings)
    settings.setValue(TOUR_SEEN_KEY, TOUR_VERSION - 1)  # an older tour was seen before
    assert tour_due(settings)
    mark_tour_seen(settings, never_again=True)
    settings.setValue(TOUR_SEEN_KEY, 0)
    assert not tour_due(settings)  # "don't show again" outranks a newer tour
    settings.setValue(TOUR_DISABLED_KEY, False)
    assert tour_due(settings) and not tour_due(settings, {NO_TOUR_ENV: '1'})


def test_first_launch_shows_the_tour_once(window, settings, qtbot, monkeypatch):
    from byax.gui import MainWindow

    window.maybe_show_tour()
    tour = window._tour
    assert tour.isVisible() and tour.index == 0
    assert settings.value(TOUR_SEEN_KEY, 0, type=int) == TOUR_VERSION
    tour.close()
    again = MainWindow()
    qtbot.addWidget(again)
    again.maybe_show_tour()
    assert getattr(again, '_tour', None) is None


def test_next_and_back_walk_the_steps_and_finish_completes(window, settings):
    tour = window.show_tour()
    seen = []
    tour.stepChanged.connect(seen.append)
    assert tour.back_button.isEnabled() is False and tour.next_button.text() == 'Next'
    for expected in range(1, tour.total):
        tour.next_button.click()
        assert tour.index == expected and tour.back_button.isEnabled()
    assert tour.next_button.text() == 'Finish'
    tour.back_button.click()
    assert tour.index == tour.total - 2
    tour.next_button.click()
    outcomes = []
    tour.closedAs.connect(outcomes.append)
    tour.next_button.click()
    assert outcomes == ['completed'] and not tour.isVisible()
    assert seen[:3] == [1, 2, 3]
    assert settings.value(TOUR_DISABLED_KEY, False, type=bool) is False


def test_skip_button_and_escape_both_skip_and_remember(window, settings, qtbot):
    tour = window.show_tour()
    outcomes = []
    tour.closedAs.connect(outcomes.append)
    tour.skip_button.click()
    assert outcomes == ['skipped'] and not tour.isVisible()
    mark = settings.value(TOUR_SEEN_KEY, 0, type=int)
    assert mark == TOUR_VERSION
    tour = window.show_tour()
    tour.closedAs.connect(outcomes.append)
    QTest.keyClick(tour, Qt.Key_Escape)
    assert outcomes == ['skipped', 'skipped'] and not tour.isVisible()


def test_dont_show_again_persists(window, settings):
    tour = window.show_tour()
    assert tour.never_again.isChecked() is False
    tour.never_again.setChecked(True)
    tour.skip_button.click()
    assert settings.value(TOUR_DISABLED_KEY, False, type=bool) is True
    assert not tour_due(settings)
    reopened = window.show_tour()  # the Help menu still reopens it
    assert reopened.isVisible() and reopened.never_again.isChecked()
    reopened.close()


def test_the_tour_is_keyboard_navigable(window, qtbot):
    tour = window.show_tour()
    tour.activateWindow()
    qtbot.waitUntil(tour.isActiveWindow, timeout=3000)
    QTest.keyClick(tour, Qt.Key_Right)
    assert tour.index == 1
    QTest.keyClick(tour, Qt.Key_Right)
    QTest.keyClick(tour, Qt.Key_Left)
    assert tour.index == 1
    QTest.keyClick(tour, Qt.Key_PageDown)
    assert tour.index == 2
    QTest.keyClick(tour, Qt.Key_PageUp)
    assert tour.index == 1
    # Tab order reaches every control, and Enter on the default button advances.
    assert tour.next_button.isDefault()
    QTest.keyClick(tour.next_button, Qt.Key_Return)
    assert tour.index == 2
    tour.close()


def test_every_control_and_step_has_a_screen_reader_label(window):
    tour = window.show_tour()
    assert tour.accessibleName() == 'ByAuthorX tour' and tour.accessibleDescription()
    for button in (tour.skip_button, tour.back_button, tour.next_button, tour.never_again):
        assert button.accessibleName()
    assert tour.body.focusPolicy() & Qt.TabFocus
    for index in range(tour.total):
        tour.show_step(index)
        assert tour.progress.accessibleName() == f'Step {index + 1} of {tour.total}'
        assert tour.body.accessibleName().startswith(tour.progress.accessibleName())
        assert tour.body.accessibleDescription() == tour.body.text()
        assert tour.title.text() in tour.body.accessibleName()
    assert tour.next_button.accessibleName() == 'Finish tour'
    tour.close()


def test_motion_enabled_fades_between_steps(window):
    window.motion.set_reduced(False)
    tour = window.show_tour()
    assert not window.motion.reduced
    tour.next_button.click()
    assert tour.last_transition == 'fade'
    assert tour.page.graphicsEffect() is not None  # the opacity animation is running
    tour.close()


@pytest.mark.parametrize('how', ['setting', 'environment'])
def test_reduced_motion_swaps_steps_instantly(window, monkeypatch, how):
    if how == 'setting':
        window.motion.set_reduced(True)
    else:
        monkeypatch.setenv('BYAX_REDUCED_MOTION', '1')
    assert window.motion.reduced
    tour = window.show_tour()
    tour.next_button.click()
    assert tour.last_transition == 'instant'
    assert tour.page.graphicsEffect() is None  # no animation object exists at all
    # The welcome pen-line shows its resting pose, not frame zero.
    tour.show_step(0, transition=False)
    assert tour.pen.get_progress() == 1.0
    tour.close()


def test_help_menu_reopens_the_tour_and_opens_a_sample_copy(window, tmp_path):
    mark_tour_seen(window.settings)
    window.tour_action.trigger()
    assert window._tour.isVisible()
    window._tour.close()
    before = digest(BUNDLED)
    window.open_sample()
    copy = Path(window.path)
    assert copy.parent == tmp_path / 'documents' and copy != BUNDLED
    assert window.doc['metadata']['title'] == 'The Lamp at Harrow Point'
    window.doc['metadata']['title'] = 'Changed in the copy'
    window.dirty = True
    window.save(wait=True)
    assert digest(BUNDLED) == before
    window.dirty = False
    window.open_sample()  # a second request makes a second copy instead of replacing the first
    assert Path(window.path) != copy and copy.is_file()


def test_open_sample_reports_a_missing_bundle_instead_of_failing(window, monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(window, 'error', shown.append)
    monkeypatch.setattr(samples, 'sample_path', lambda: tmp_path / 'absent.byax')
    assert window.open_sample() is None and shown


def test_dialog_can_be_built_without_a_window(qapp, settings):
    from byax.gui_motion import MotionController

    motion = MotionController(settings)
    motion.set_reduced(True)
    dialog = TourDialog(motion, settings)
    for _ in range(dialog.total):
        dialog.next()
    assert dialog.outcome == 'completed' and dialog.last_transition == 'instant'
