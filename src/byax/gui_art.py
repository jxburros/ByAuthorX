"""Restrained native artwork and task-driven playback of Vixl-baked frames."""

from __future__ import annotations

import bisect
import json

from PySide6.QtCore import QAbstractAnimation, QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from .brand import BRAND_ROOT, BrandGraphic
from .gui_motion import PenLineAnimation


def art_theme(widget=None):
    """Pick the companion variant that matches the effective window palette."""
    palette = widget.palette() if widget is not None else QApplication.palette()
    return 'dark' if palette.color(QPalette.Window).lightness() < 128 else 'light'


def themed_art(name, widget=None):
    """Path, relative to the brand root, of a light/dark companion still."""
    return f'ui/{name}-{art_theme(widget)}.png'


class EmptyPanel(QWidget):
    """Explain an empty inspector without placing artwork in authored content."""

    def __init__(self, title, description, *, graphic=None, action=None, parent=None):
        super().__init__(parent)
        self.setObjectName('emptyPanel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(12)
        layout.addStretch()
        if graphic:
            art = BrandGraphic(graphic, 144, self)
            layout.addWidget(art, alignment=Qt.AlignHCenter)
        heading = QLabel(title)
        heading.setObjectName('emptyHeading')
        heading.setWordWrap(True)
        heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading)
        body = QLabel(description)
        body.setObjectName('emptyDescription')
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        layout.addWidget(body)
        if action:
            label, callback = action
            button = QPushButton(label)
            button.setProperty('primary', True)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addStretch()


class ActivityGraphic(PenLineAnimation):
    """Run only for an active task or a short explicit-save confirmation."""

    def __init__(self, motion, parent=None):
        super().__init__(motion, parent, profile='busy-write', width=48)
        self.mode = None
        self.pending = QTimer(self)
        self.pending.setSingleShot(True)
        self.pending.setInterval(200)
        self.pending.timeout.connect(self._reveal_busy)
        self.confirmation = QTimer(self)
        self.confirmation.setSingleShot(True)
        self.confirmation.setInterval(1200)
        self.confirmation.timeout.connect(self.clear)
        self.window().installEventFilter(self)
        self.hide()

    def _load(self, profile, width):
        self.manifest = json.loads((BRAND_ROOT / 'ui' / f'{profile}.json').read_text(encoding='utf-8'))
        self.frame_times = self.manifest['frameTimesMs']
        self.sprite = QPixmap(str(BRAND_ROOT / 'ui' / f'{profile}-sprite.png'))
        self.pixmap = QPixmap(str(BRAND_ROOT / 'ui' / f'{profile}-poster.png'))
        if self.sprite.isNull() or self.pixmap.isNull():
            raise RuntimeError(f'Missing UI motion resource: {profile}')
        height = round(width * self.pixmap.height() / self.pixmap.width())
        self.art_rect = QRectF(0, 0, width, height)
        self.setFixedSize(width, height)
        self.animation.setDuration(self.manifest['durationMs'])
        self.animation.setLoopCount(-1 if self.manifest['loop'] else 1)
        self.set_progress(1.0)

    def show_busy(self):
        if self.mode == 'busy':
            return
        self.animation.stop()
        self.confirmation.stop()
        self.mode = 'busy'
        self._load('busy-write', 48)
        self.hide()
        self.pending.start()

    def _reveal_busy(self):
        if self.mode == 'busy':
            self.show()
            self._sync_playback()

    def show_saved(self):
        if self.mode == 'busy':
            return
        self.animation.stop()
        self.pending.stop()
        self.confirmation.stop()
        self.mode = 'saved'
        self._load('save-check', 36)
        self.show()
        self._sync_playback()
        self.confirmation.start()

    def clear(self):
        self.mode = None
        self.animation.stop()
        self.pending.stop()
        self.confirmation.stop()
        self.hide()

    def _sync_playback(self):
        if not self.mode or not self.isVisible() or self.window().isMinimized() or self.motion.reduced:
            self.animation.stop()
            self.set_progress(1.0)
        elif self.animation.state() != QAbstractAnimation.Running and (
            self.mode == 'busy' or self.mode == 'saved' and not self.confirmation.isActive()
        ):
            self.animation.start()

    def _preference_changed(self, reduced):
        self._sync_playback()

    def showEvent(self, event):
        # Avoid PenLineAnimation's automatic one-shot reveal; task state owns playback.
        BrandGraphic.showEvent(self, event)
        self._sync_playback()

    def hideEvent(self, event):
        self.animation.stop()
        self.set_progress(1.0)
        BrandGraphic.hideEvent(self, event)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.WindowStateChange, QEvent.Show):
            self._sync_playback()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.motion.reduced or self.animation.state() == QAbstractAnimation.Stopped:
            painter.drawPixmap(self.art_rect, self.pixmap, QRectF(self.pixmap.rect()))
            return
        time = self.progress * self.manifest['durationMs']
        index = max(0, bisect.bisect_right(self.frame_times, time) - 1)
        width, height = self.manifest['frameWidth'], self.manifest['frameHeight']
        painter.drawPixmap(self.art_rect, self.sprite, QRectF(index * width, 0, width, height))


class TaskLoop(PenLineAnimation):
    """A companion waiting loop owned by one task: it never runs as ambient decoration.

    Playback happens only between start() and stop(), while visible and not minimized;
    reduced motion shows the delivered still (fully written hold pose) instead.
    """

    def __init__(self, motion, name, width, parent=None):
        super().__init__(motion, parent, profile=f'{name}-{art_theme(parent)}', width=width)
        self.name = name
        self.running = False
        self.animation.setLoopCount(-1)
        self.setAccessibleName('Working')
        self.hide()

    def start(self):
        self.running = True
        self.show()
        self._sync_playback()

    def stop(self):
        self.running = False
        self.animation.stop()
        self.set_progress(1.0)
        self.hide()

    def _sync_playback(self):
        window = self.window()
        if not self.running or not self.isVisible() or window.isMinimized() or self.motion.reduced:
            self.animation.stop()
            self.set_progress(1.0)
        elif self.animation.state() != QAbstractAnimation.Running:
            self.animation.start()

    def _preference_changed(self, reduced):
        self._sync_playback()

    def showEvent(self, event):
        BrandGraphic.showEvent(self, event)
        self.window().installEventFilter(self)
        self._sync_playback()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.WindowStateChange, QEvent.Show, QEvent.Hide):
            QTimer.singleShot(0, self, self._sync_playback)
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        if self.motion.reduced or self.animation.state() == QAbstractAnimation.Stopped:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(self.art_rect, self.pixmap, QRectF(self.pixmap.rect()))
            return
        super().paintEvent(event)
