"""Exercise motion cancellation, document preservation, and installed identity resources."""

import copy
from types import SimpleNamespace

from PIL import Image
from PySide6.QtCore import QAbstractAnimation, QSettings, QSize, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from byax import model
from byax.brand import BRAND_ROOT, TOKENS, app_icon
from byax.fonts import bundled_font_path
from byax.gui import MainWindow
from byax.gui_motion import PenLineAnimation


def make_window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'appearance.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.delenv('BYAX_REDUCED_MOTION', raising=False)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def test_fonts_and_icon_sizes_available(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    assert QApplication.instance().font().family() == 'Manrope'
    for family in (
        'Manrope',
        'IM Fell English',
        'Source Serif 4',
        'Irongall',
        'Scholium',
        'Tenuis',
        'Brobdingnag',
        'Onionskin',
        'Decussate',
    ):
        assert family.casefold() in {name.casefold() for name in QFontDatabase.families()}
        assert bundled_font_path(family).is_file()
    assert bundled_font_path('Manrope', bold=True).name == 'Manrope-600.ttf'
    icon = app_icon()
    for size in (16, 20, 24, 32, 64, 256):
        assert icon.pixmap(QSize(size, size)).size() == QSize(size, size)
    assert not window.windowIcon().isNull()
    assert window.brand_mark.width() >= 240
    for file in ('wordmark-light.png', 'byauthorx.ico', 'tokens.json'):
        assert (BRAND_ROOT / file).is_file()


def test_interface_text_uses_the_brand_fonts_by_role(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDockWidget, QLabel

    window = make_window(qtbot, monkeypatch, tmp_path)
    # headings and brand moments: Irongall
    assert window.document_title.font().family() == 'Irongall'
    about = window.create_about_dialog()
    qtbot.addWidget(about)
    about.show()
    assert about.findChild(QLabel, 'brandTagline').font().family() == 'Irongall'
    assert all(dock.styleSheet() == '' for dock in window.findChildren(QDockWidget))
    assert (
        'font-family: "Irongall"'
        in QApplication.instance().styleSheet().split('QDockWidget {')[1].split('}')[0]
    )
    heading = next(label for label in window.findChildren(QLabel, 'emptyHeading'))
    assert heading.font().family() == 'Irongall'
    # helper text: Scholium; status counters: Onionskin; controls stay in Manrope
    helper = next(label for label in window.findChildren(QLabel, 'emptyDescription'))
    assert helper.font().family() == 'Scholium'
    assert window.word_label.font().family() == 'Onionskin'
    assert window.menuBar().font().family() == 'Manrope'
    assert window.status_label.font().family() == 'Manrope'
    assert window.document_state.font().family() == 'Manrope'


def test_view_motion_completes_without_changing_document(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    window.doc = model.new_document(text='A passage worth keeping.')
    window.refresh()
    original = copy.deepcopy(window.doc)
    window.toggle_pages()
    assert window.stack.currentIndex() == 1
    assert window.stack.currentWidget().graphicsEffect() is not None
    qtbot.waitUntil(lambda: not window.motion._fades, timeout=1500)
    assert window.stack.currentWidget().graphicsEffect() is None
    window.toggle_pages()
    qtbot.waitUntil(lambda: not window.motion._fades, timeout=1500)
    assert window.doc == original
    assert not window.dirty


def test_reduce_motion_cancels_active_fades_and_pen(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = window.create_about_dialog()
    # Explicit lifetime for inspecting after close.
    dialog.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(dialog)
    dialog.show()
    pen = dialog.findChild(PenLineAnimation)
    assert pen.animation.state() == QAbstractAnimation.Running
    window.toggle_pages()
    window.reduce_motion_action.trigger()
    assert window.motion.reduced
    assert not window.motion._fades
    assert window.stack.currentWidget().graphicsEffect() is None
    assert pen.animation.state() == QAbstractAnimation.Stopped
    assert pen.progress == 1.0
    window.toggle_pages()
    assert window.stack.currentWidget().graphicsEffect() is None
    assert QSettings(str(tmp_path / 'appearance.ini'), QSettings.IniFormat).value(
        'appearance/reducedMotion', False, type=bool
    )


def test_vixl_frames_end_in_exact_resting_pose(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    dialog = window.create_about_dialog()
    dialog.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(dialog)
    dialog.show()
    pen = dialog.findChild(PenLineAnimation)
    assert pen.manifest['durationMs'] == TOKENS['motion']['durationMs']
    assert pen.frame_times[0] == 0
    assert pen.frame_times[-1] == pen.animation.duration()
    qtbot.waitUntil(lambda: pen.animation.state() == QAbstractAnimation.Stopped, timeout=1500)
    assert pen.progress == 1.0
    with (
        Image.open(BRAND_ROOT / 'ui/about-write-sprite.png') as sprite,
        Image.open(BRAND_ROOT / 'ui/about-write-poster.png') as rest,
    ):
        width, height = rest.size
        assert sprite.size == (width * len(pen.frame_times), height)
        assert sprite.crop((sprite.width - width, 0, sprite.width, height)).tobytes() == rest.tobytes()
        first = sprite.crop((0, 0, width, height))
        assert sum(first.getchannel('A').tobytes()) < sum(rest.getchannel('A').tobytes())


def test_environment_and_system_preferences_take_precedence(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    monkeypatch.setenv('BYAX_REDUCED_MOTION', '1')
    window.motion.set_reduced(False)
    window.toggle_pages()
    assert window.motion.reduced
    assert window.stack.currentWidget().graphicsEffect() is None
    monkeypatch.delenv('BYAX_REDUCED_MOTION')
    window.motion._accessibility = SimpleNamespace(motionPreference=lambda: Qt.MotionPreference.ReducedMotion)
    window.motion.set_reduced(False)
    assert window.motion.reduced
    window.toggle_pages()
    assert window.stack.currentWidget().graphicsEffect() is None


def test_rapid_view_changes_settle_and_typing_does_not_animate(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    for _ in range(8):
        window.toggle_pages()
    qtbot.waitUntil(lambda: not window.motion._fades, timeout=1500)
    window.editor.insertPlainText('Keep my voice.')
    assert window.flush_edits()
    assert not window.motion._fades
    assert window.doc['blocks'][0]['text'] == 'Keep my voice.'
    window.dirty = False
