"""The logo UI library at runtime: icons, dialog art, task-owned loops and hand-drawn marks."""

import copy
import hashlib
import json
import threading
import warnings
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QAbstractAnimation, QRectF, QSettings
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QToolBar

from byax import model
from byax.brand import BRAND_ROOT
from byax.gui import MainWindow
from byax.gui_ink import rule, squiggle
from byax.layout import (
    INK_STRIKE_PROPERTY,
    INK_UNDERLINE_PROPERTY,
    build_document,
    hand_drawn_marks,
    resolve_pages,
    resolved_documents,
)

ROOT = BRAND_ROOT.parents[3]
LIBRARY = ROOT / 'assets/logo-ui-library'
UI = BRAND_ROOT / 'ui'
NEW_STILLS = (
    'checks-clear',
    'proofread-marks',
    'jobs-empty',
    'dictation-ready',
    'export-ready',
    'provider-keys',
)
NEW_LOOPS = ('listening-wave', 'checking-page')
RULES = ('rule-h', 'rule-v', 'tab-underline')


def make_window(qtbot, monkeypatch, tmp_path, **env):
    settings = QSettings(str(tmp_path / 'appearance.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.delenv('BYAX_REDUCED_MOTION', raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def marked_document(text='A teh sample sentence', marks=None):
    doc = model.new_document(title='Marks', text=text)
    doc['blocks'][0]['marks'] = marks or []
    return doc


# -- the library ships as built ------------------------------------------------------------------
def test_library_files_match_their_checksums():
    checksums = json.loads((LIBRARY / 'checksums.json').read_text())
    assert checksums
    for relative, digest in checksums.items():
        assert sha(LIBRARY / relative) == digest, relative
    manifest = json.loads((LIBRARY / 'manifest.json').read_text())
    for asset in manifest['assets']:
        assert asset['checks']['passed'] and asset['contract']['passed'], asset['id']
        for key in ('master', 'recipe', 'preview'):
            assert (LIBRARY / asset[key]).exists(), asset[key]


def test_icon_atlas_covers_every_designed_icon_in_both_themes():
    atlas = json.loads((UI / 'icons.json').read_text())
    manifest = json.loads((LIBRARY / 'manifest.json').read_text())
    designed = {a['design'] for a in manifest['assets'] if a['category'] == 'icons'}
    assert set(atlas['names']) == designed and len(atlas['names']) == len(set(atlas['names']))
    assert atlas['checks']['passed']
    for theme in atlas['themes']:
        for size in atlas['sizes']:
            with Image.open(UI / atlas['files'][f'{theme}-{size}']) as image:
                assert image.size == (atlas['columns'] * size, atlas['rows'] * size)
                assert image.mode == 'RGBA' and image.getchannel('A').getbbox()
    provenance = json.loads((UI / 'provenance.json').read_text())
    assert provenance['icons.json']['source_sha256'] == sha(LIBRARY / 'icons/atlas.json')


def test_every_icon_resolves_with_exact_pixmaps_and_is_distinct(qtbot):
    from byax.gui_icons import icon_names, themed_icon

    rendered = []
    for name in icon_names():
        icon = themed_icon(name)
        assert not icon.isNull(), name
        assert {size.width() for size in icon.availableSizes()} == {16, 24, 32, 48}
        rendered.append(icon.pixmap(24, 24).toImage().constBits().tobytes())
    assert len(set(rendered)) == len(rendered), 'two icons draw identically'
    with pytest.raises(KeyError):
        themed_icon('not-an-icon')


def test_icons_use_only_the_brand_colours():
    """Ink for the object, pen green for the action, the ink blot red once: nothing else is drawn."""
    palette = json.loads((BRAND_ROOT / 'tokens.json').read_text())['color']
    allowed = [tuple(int(palette[k][i : i + 2], 16) for i in (1, 3, 5)) for k in ('ink', 'green', 'red')]
    with Image.open(UI / 'icons-light-48.png') as atlas:
        pixels = atlas.convert('RGBA')
    data = pixels.tobytes()  # RGBA, valid on every Pillow the project allows
    solid = [tuple(data[i : i + 3]) for i in range(0, len(data), 4) if data[i + 3] == 255]
    assert solid

    def near_brand(color):
        return min(max(abs(a - b) for a, b in zip(color, ref)) for ref in allowed) <= 41

    off = sum(1 for color in solid if not near_brand(color))
    assert off / len(solid) < 0.005


def test_new_stills_loops_and_rules_have_provenance():
    provenance = json.loads((UI / 'provenance.json').read_text())
    for theme in ('light', 'dark'):
        for name in NEW_STILLS:
            path = UI / f'{name}-{theme}.png'
            record = provenance[path.name]
            assert record['source'] == f'assets/logo-ui-library/illustrations/{name}-{theme}.png'
            assert sha(path) == record['source_sha256'] == sha(ROOT / record['source'])
            with Image.open(path) as image:
                assert image.size == (640, 480)
        for name in RULES:
            with (
                Image.open(UI / f'{name}-{theme}.png') as one,
                Image.open(UI / f'{name}-{theme}@2x.png') as two,
            ):
                assert two.size == (one.width * 2, one.height * 2)
        for name in NEW_LOOPS:
            manifest = json.loads((UI / f'{name}-{theme}.json').read_text())
            assert manifest['loop'] and manifest['checks']['passed']
            assert (
                manifest['frameTimesMs'][0] == 0
                and manifest['frameTimesMs'][-1] == manifest['durationMs'] == 2400
            )
            with (
                Image.open(UI / f'{name}-{theme}-sprite.png') as sprite,
                Image.open(UI / f'{name}-{theme}-poster.png') as poster,
            ):
                width, height = poster.size
                assert sprite.size == (width * len(manifest['frameTimesMs']), height)
                first = sprite.crop((0, 0, width, height)).tobytes()
                assert first == sprite.crop((sprite.width - width, 0, sprite.width, height)).tobytes()


# -- toolbar and menus -----------------------------------------------------------------------------
def test_toolbar_and_menu_actions_carry_icons(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    bar = window.findChild(QToolBar)
    by_text = {a.text(): a for a in bar.actions() if a.text()}
    for text in ('Bold', 'Italic', 'Underline', 'Comment', 'Check', 'Pages'):
        assert not by_text[text].icon().isNull(), text
    # B / I / U are the letters themselves: icon only, with names and shortcuts kept for assistive tech.
    for text in ('Bold', 'Italic', 'Underline'):
        button = bar.widgetForAction(by_text[text])
        assert button.toolButtonStyle().name == 'ToolButtonIconOnly'
        assert by_text[text].text() == text and by_text[text].toolTip()
    for text in ('Comment', 'Check', 'Pages'):
        assert bar.widgetForAction(by_text[text]).toolButtonStyle().name == 'ToolButtonTextBesideIcon'
    iconed = {}
    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        iconed[menu_action.text().replace('&', '')] = [
            a.text() for a in menu.actions() if not a.icon().isNull()
        ]
    assert {'New', 'Open…', 'Save', 'Export…', 'Print…'} <= set(iconed['File'])
    assert {'Undo', 'Redo', 'Cut', 'Copy', 'Paste', 'Find and replace…'} <= set(iconed['Edit'])
    assert {'Run document checks', 'Add comment…', 'Checkpoint…', 'Revision history…'} <= set(
        iconed['Review']
    )
    assert {'Dictation…', 'Provider settings…'} <= set(iconed['Tools'])
    assert (
        not window.menuBar().actions()[-1].menu().actions()[0].icon().isNull()
    )  # About: the logo's own mark
    window.dirty = False


# -- dialogs and loops ------------------------------------------------------------------------------
def capture_dialogs(monkeypatch):
    shown = []

    def fake_exec(self):
        shown.append(self)
        return 0

    monkeypatch.setattr(QDialog, 'exec', fake_exec)
    return shown


def test_checks_art_matches_what_the_result_proves(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    still, headline, _ = window.checks_art({'validation': 'passed', 'findings': []})
    assert still == 'checks-clear' and 'Nothing' in headline
    still, headline, _ = window.checks_art(
        {'validation': 'failed', 'findings': [{'rule': 'x'}, {'rule': 'y'}]}
    )
    assert still == 'proofread-marks' and headline.startswith('2 items')
    # A check that could not measure everything must not claim the document is clean.
    assert window.checks_art({'validation': 'incomplete', 'findings': []}) is None
    window.dirty = False


def test_result_dialogs_show_the_illustrated_header_only_when_given(qtbot, monkeypatch, tmp_path):
    from byax.brand import BrandGraphic

    window = make_window(qtbot, monkeypatch, tmp_path)
    shown = capture_dialogs(monkeypatch)
    window.report('Plain', {'message': 'x'})
    window.report('Illustrated', {'message': 'x'}, art=('export-ready', 'Export finished', 'Unchanged.'))
    assert not shown[0].findChildren(BrandGraphic)
    graphics = shown[1].findChildren(BrandGraphic)
    assert len(graphics) == 1 and not graphics[0].pixmap.isNull()
    window.dirty = False


def test_jobs_dialog_explains_an_empty_queue(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QListWidget, QStackedWidget

    window = make_window(qtbot, monkeypatch, tmp_path)
    window.job_manager = type('Jobs', (), {'list': lambda self: []})()
    shown = capture_dialogs(monkeypatch)
    window.jobs_dialog()
    stack = shown[0].findChild(QStackedWidget)
    assert stack.currentIndex() == 1 and stack.widget(0).findChild(QListWidget) is None
    window.job_manager = type(
        'Jobs', (), {'list': lambda self: [{'kind': 'k', 'state': 's', 'id': 'job-1'}]}
    )()
    shown.clear()
    window.jobs_dialog()
    assert shown[0].findChild(QStackedWidget).currentIndex() == 0
    window.dirty = False


def test_checking_loop_runs_only_while_checks_run(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    capture_dialogs(monkeypatch)
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    release = threading.Event()
    loop = window.review_empty.art.loop
    assert not loop.running and loop.isHidden()

    def slow_checks(doc, profile=None, suites=None):
        release.wait(10)
        return {'validation': 'passed', 'findings': [], 'revision': doc['revision']}

    monkeypatch.setattr('byax.checks.run_checks', slow_checks)
    window.run_checks()
    assert loop.running and window.review_empty.art.still.isHidden()
    qtbot.waitUntil(lambda: loop.animation.state() == QAbstractAnimation.Running)
    release.set()
    qtbot.waitUntil(lambda: not loop.running)
    assert loop.animation.state() == QAbstractAnimation.Stopped and window.review_empty.art.still.isVisible()
    assert not errors
    window.dirty = False


def test_checking_loop_stops_when_the_checks_fail(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)

    def broken(doc, profile=None, suites=None):
        raise RuntimeError('boom')

    monkeypatch.setattr('byax.checks.run_checks', broken)
    window.run_checks()
    qtbot.waitUntil(lambda: bool(errors))
    assert not window.review_empty.art.loop.running and window.review_empty.art.still.isVisible()
    window.dirty = False


def test_dictation_voice_loop_is_task_owned(qtbot, monkeypatch, tmp_path):
    from byax.gui_providers import DictationDialog
    from byax.transcription import QtMicrophoneRecorder

    monkeypatch.setattr(QtMicrophoneRecorder, 'devices', staticmethod(list))
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = DictationDialog(window, journal_root=tmp_path / 'journal')
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.voice.busy and dialog.voice.loop.isHidden() and dialog.voice.still.isVisible()
    dialog.voice.set_busy(True)
    qtbot.waitUntil(lambda: dialog.voice.loop.animation.state() == QAbstractAnimation.Running)
    window.motion.set_reduced(True)
    assert dialog.voice.loop.animation.state() == QAbstractAnimation.Stopped and dialog.voice.loop.isVisible()
    dialog.voice.loop.grab()  # reduced motion paints the delivered still pose
    window.motion.set_reduced(False)
    dialog.reject()
    assert not dialog.voice.busy and dialog.voice.loop.animation.state() == QAbstractAnimation.Stopped
    window.dirty = False


# -- hand-drawn strokes ------------------------------------------------------------------------------
def test_strokes_are_deterministic_bounded_and_fall_back_to_plain_lines(qtbot):
    first = squiggle(10, 90, 40, 'seed')
    assert first == squiggle(10, 90, 40, 'seed') and first != squiggle(10, 90, 40, 'other')
    box = first.boundingRect()
    assert box.left() >= 8 and box.right() <= 92 and box.height() <= 2 * 1.9 * 1.3 + 1.2
    plain = squiggle(10, 90, 40, 'seed', hand=False)
    assert plain == squiggle(10, 90, 40, 'other', hand=False)
    assert rule(5, 80, 20, 's') == rule(5, 80, 20, 's')
    assert rule(5, 80, 20, 's', hand=False).boundingRect().height() == 0
    assert rule(5, 80, 20, 's').boundingRect().height() <= 2.2


def decorations(document, position=1):
    cursor = QTextCursor(document)
    cursor.setPosition(position)
    fmt = cursor.charFormat()
    return (
        fmt.fontUnderline(),
        fmt.fontStrikeOut(),
        fmt.boolProperty(INK_UNDERLINE_PROPERTY),
        fmt.boolProperty(INK_STRIKE_PROPERTY),
    )


def test_pen_stroke_decorations_are_editing_view_only(qtbot):
    doc = marked_document('Struck and underlined', [{'type': 'strikethrough', 'start': 0, 'end': 6}])
    doc['blocks'][0]['marks'].append({'type': 'underline', 'start': 0, 'end': 6})
    assert decorations(build_document(doc)) == (True, True, False, False)
    with hand_drawn_marks(True):
        assert decorations(build_document(doc)) == (False, False, True, True)
    assert decorations(build_document(doc)) == (True, True, False, False)

    # Pages, print and export resolve their own documents from the model: always exact lines.
    with hand_drawn_marks(True):
        resolved = resolve_pages(copy.deepcopy(doc))
    parts = resolved_documents(resolved)
    assert parts and all(decorations(part) == (True, True, False, False) for part in parts[:1])


def test_pen_stroke_mode_does_not_leak_between_threads(qtbot):
    seen = []
    with hand_drawn_marks(True):
        worker = threading.Thread(
            target=lambda: seen.append(
                decorations(
                    build_document(
                        marked_document('Struck', [{'type': 'strikethrough', 'start': 0, 'end': 6}])
                    )
                )
            )
        )
        worker.start()
        worker.join()
    assert seen == [(False, True, False, False)]


def test_window_toggle_rebuilds_the_editing_view_without_touching_the_document(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = marked_document('Struck words', [{'type': 'strikethrough', 'start': 0, 'end': 6}])
    window.dirty = False
    window.refresh(0)
    before = json.dumps(window.doc, sort_keys=True)
    assert window.hand_marks and decorations(window.editor.document()) == (False, False, False, True)
    window.set_hand_marks(False)
    assert decorations(window.editor.document()) == (False, True, False, False)
    assert window.settings.value('appearance/handDrawnMarks', type=bool) is False
    window.set_hand_marks(True)
    assert decorations(window.editor.document()) == (False, False, False, True)
    assert json.dumps(window.doc, sort_keys=True) == before
    window.dirty = False


def test_preference_persists_across_windows(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    window.set_hand_marks(False)
    again = make_window(qtbot, monkeypatch, tmp_path)
    assert again.hand_marks is False and not again.hand_marks_action.isChecked()
    window.dirty = again.dirty = False


# -- proofreader's marks -----------------------------------------------------------------------------
def spelling_mark(window, start=2, end=5, text='A teh sample sentence', kind='spelling'):
    from byax.gui_editor import ProofMark

    return ProofMark(window.doc['blocks'][0]['id'], start, end, kind, 'Possible misspelling: teh', text)


def open_marked_window(qtbot, monkeypatch, tmp_path, **env):
    window = make_window(qtbot, monkeypatch, tmp_path, **env)
    window.doc = marked_document()
    window.dirty = False
    window.refresh(0)
    window.resize(900, 500)
    window.editor.setCursorWidth(0)  # a blinking caret would change pixels between snapshots
    qtbot.wait(50)
    return window


def snapshot(editor):
    return editor.viewport().grab().toImage()


def changed_pixels(first, second):
    return sum(
        first.pixel(x, y) != second.pixel(x, y) for x in range(first.width()) for y in range(first.height())
    )


MARK_PIXEL_ATTEMPTS = 3  # the first run plus two retries


def test_marks_sit_under_the_word_they_measure(qtbot, monkeypatch, tmp_path):
    # The check compares rendered pixels of a pen stroke whose shape is seeded by a random block id, so
    # an unlucky draw or a slow runner can fail one snapshot. Retry a failed assertion with a fresh
    # window and stroke; a real regression fails every attempt. Retries are reported as warnings.
    for attempt in range(1, MARK_PIXEL_ATTEMPTS + 1):
        try:
            return check_marks_sit_under_the_word(qtbot, monkeypatch, tmp_path)
        except AssertionError as error:
            if attempt == MARK_PIXEL_ATTEMPTS:
                raise
            warnings.warn(
                f'pen-mark pixel check failed on attempt {attempt} of {MARK_PIXEL_ATTEMPTS}, retrying: {error}',
                RuntimeWarning,
                stacklevel=1,
            )


def check_marks_sit_under_the_word(qtbot, monkeypatch, tmp_path):
    window = open_marked_window(qtbot, monkeypatch, tmp_path)
    try:
        mark_sits_under_the_word(qtbot, window)
    finally:
        window.dirty = False


def mark_sits_under_the_word(qtbot, window):
    editor = window.editor
    mark = spelling_mark(window)
    block = editor._block_for(mark.target)
    ((x1, x2, top, ascent, _descent),) = editor._spans(block, 2, 5)
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + 2)
    assert abs(editor.cursorRect(cursor).x() - x1) <= 1.5
    cursor.setPosition(block.position() + 5)
    assert abs(editor.cursorRect(cursor).x() - x2) <= 1.5
    before = snapshot(editor)
    editor.set_proof_marks([mark])
    editor._finish_reveal()
    qtbot.wait(20)
    after = snapshot(editor)
    assert changed_pixels(before, after) > 20
    # Every changed pixel lies in the word's own column, below its baseline. The pen stroke is seeded
    # by the (random) block id, so its crests differ per run. Pixel (x, y) covers [x, x + 1) x [y, y + 1),
    # so it belongs to the allowed region when any part of it does: comparing the integer row to the
    # fractional bound flagged faint antialiasing in a row the stroke legitimately reaches.
    diff = [
        (x, y)
        for x in range(after.width())
        for y in range(after.height())
        if before.pixel(x, y) != after.pixel(x, y)
    ]
    outside = [(x, y) for x, y in diff if not (x + 1 > x1 - 3 and x < x2 + 3 and y + 1 > top + ascent - 1)]
    assert not outside, f'pixels outside the word column or above its baseline: {outside[:8]}'


def test_marks_use_the_pen_colours_and_names(qtbot, monkeypatch, tmp_path):
    window = open_marked_window(qtbot, monkeypatch, tmp_path)
    editor = window.editor
    editor.set_proof_marks([spelling_mark(window), spelling_mark(window, 8, 14, kind='grammar')])
    editor._finish_reveal()
    qtbot.wait(20)
    hits = {label for _rect, label in editor._hits}
    assert hits == {'Possible misspelling: teh'}
    image = snapshot(editor)
    palette = json.loads((BRAND_ROOT / 'tokens.json').read_text())['color']
    reds = greens = 0
    for x in range(image.width()):
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            reds += (
                abs(color.red() - 0xAD) < 24
                and abs(color.green() - 0x28) < 24
                and abs(color.blue() - 0x3E) < 24
            )
            greens += (
                abs(color.red() - 0x36) < 24
                and abs(color.green() - 0x7B) < 24
                and abs(color.blue() - 0x6C) < 24
            )
    assert palette['red'] == '#AD283E' and reds > 5 and greens > 5
    window.dirty = False


def test_a_mark_disappears_the_moment_its_block_changes(qtbot, monkeypatch, tmp_path):
    window = open_marked_window(qtbot, monkeypatch, tmp_path)
    editor = window.editor
    editor.set_proof_marks([spelling_mark(window)])
    editor._finish_reveal()
    qtbot.wait(20)
    assert editor._hits
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.insertText('X')
    qtbot.wait(20)
    editor.viewport().repaint()
    assert not editor._hits
    # The typed edit is committed before the window closes, so teardown never waits on a save prompt.
    assert window.flush_edits()
    window.dirty = False


def test_new_marks_are_written_over_180ms_and_instantly_under_reduced_motion(qtbot, monkeypatch, tmp_path):
    window = open_marked_window(qtbot, monkeypatch, tmp_path)
    editor = window.editor
    assert editor._reveal.duration() == 180
    editor.set_proof_marks([spelling_mark(window)])
    assert editor._reveal.state() == QAbstractAnimation.Running and editor._writing
    qtbot.waitUntil(lambda: editor._reveal.state() == QAbstractAnimation.Stopped)
    assert not editor._writing
    editor.set_proof_marks([spelling_mark(window)])  # the same mark is not written again
    assert editor._reveal.state() == QAbstractAnimation.Stopped
    window.motion.set_reduced(True)
    editor.set_proof_marks([spelling_mark(window, 8, 14)])
    assert editor._reveal.state() == QAbstractAnimation.Stopped and not editor._writing
    window.dirty = False


def test_marks_are_view_only_and_never_enter_the_document(qtbot, monkeypatch, tmp_path):
    window = open_marked_window(qtbot, monkeypatch, tmp_path)
    before = json.dumps(window.doc, sort_keys=True)
    window.editor.set_proof_marks([spelling_mark(window)])
    window.sync_proof_marks()
    window.set_hand_marks(False)
    window.set_hand_marks(True)
    assert json.dumps(window.doc, sort_keys=True) == before
    assert not any('mark' in key for key in window.doc.get('settings', {}))
    window.dirty = False


def test_checks_findings_become_marks_only_for_spelling_and_grammar(qtbot, monkeypatch, tmp_path):
    from byax.checks import run_checks

    window = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = marked_document('This is a  test of teh sytem , a apple here')
    window.dirty = False
    window.refresh(0)
    report = run_checks(window.doc, ['spelling', 'grammar'], limit=50)
    marks = window.marks_from_findings(report['findings'], window.doc)
    assert {m.kind for m in marks} == {'spelling', 'grammar'}
    assert all(m.text == window.doc['blocks'][0]['text'] for m in marks)
    assert all(m.label and not m.label.startswith('None') for m in marks)
    structure = [
        {
            'family': 'structure',
            'affected_ids': [window.doc['blocks'][0]['id']],
            'range': {'start': 0, 'end': 2},
        }
    ]
    assert window.marks_from_findings(structure, window.doc) == []
    window.dirty = False


def test_spelling_check_marks_follow_the_misspelling_list(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = marked_document('A teh sample sentence')
    window.dirty = False
    window.refresh(0)
    panel = window.ai_panel
    block = window.doc['blocks'][0]
    finding = {'target': block['id'], 'start': 2, 'end': 5, 'word': 'teh', 'suggestions': ['the', 'tech']}
    panel._spelling_done(
        panel._spelling_token,
        {'findings': [finding], 'coverage': [], 'revision': window.doc['revision']},
        False,
    )
    qtbot.waitUntil(lambda: bool(window.editor.proof_marks()))
    (mark,) = window.editor.proof_marks()
    assert (mark.kind, mark.start, mark.end, mark.text) == ('spelling', 2, 5, block['text'])
    assert 'teh' in mark.label and 'the' in mark.label
    panel.spell_list.takeItem(0)
    qtbot.waitUntil(lambda: not window.editor.proof_marks())
    # A result for an earlier revision is shown in the list but never drawn over different text.
    panel._spelling_done(
        panel._spelling_token, {'findings': [dict(finding)], 'coverage': [], 'revision': 'old'}, False
    )
    qtbot.wait(30)
    assert panel.spell_list.count() == 1 and not window.editor.proof_marks()
    window.dirty = False


def test_high_contrast_and_plain_mode_still_draw_marks(qtbot, monkeypatch, tmp_path):
    window = open_marked_window(qtbot, monkeypatch, tmp_path)
    editor = window.editor
    window.set_hand_marks(False)
    before = snapshot(editor)
    editor.set_proof_marks([spelling_mark(window)])
    editor._finish_reveal()
    qtbot.wait(20)
    assert changed_pixels(before, snapshot(editor)) > 20 and editor._hits
    monkeypatch.setattr('byax.gui_editor.prefers_high_contrast', lambda app: True)
    editor.viewport().repaint()
    assert editor._hits
    window.dirty = False


def test_hovering_a_mark_shows_its_label(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication, QToolTip

    window = open_marked_window(qtbot, monkeypatch, tmp_path)
    editor = window.editor
    editor.set_proof_marks([spelling_mark(window)])
    editor._finish_reveal()
    qtbot.wait(20)
    ((rect, label),) = editor._hits
    assert isinstance(rect, QRectF) and rect.width() > 10 and 'misspelling' in label
    # Qt delivers hover tooltips to the viewport, in viewport coordinates.
    inside = rect.center().toPoint()
    shown = []
    monkeypatch.setattr(QToolTip, 'showText', lambda position, text, widget=None, *a: shown.append(text))
    QApplication.sendEvent(
        editor.viewport(), QHelpEvent(QEvent.ToolTip, inside, editor.viewport().mapToGlobal(inside))
    )
    assert shown == [label]
    shown.clear()
    elsewhere = QPoint(inside.x(), inside.y() + 120)
    QApplication.sendEvent(
        editor.viewport(), QHelpEvent(QEvent.ToolTip, elsewhere, editor.viewport().mapToGlobal(elsewhere))
    )
    assert shown == []
    window.dirty = False
