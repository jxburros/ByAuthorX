"""Finite, non-blocking native motion with an immediate reduced-motion path."""

from __future__ import annotations

import bisect
import json
import os

from PySide6.QtCore import Property, QEasingCurve, QObject, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect

from .brand import BRAND_ROOT, TOKENS, BrandGraphic


class MotionController(QObject):
    reducedChanged = Signal(bool)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._fades = {}
        self._style_hints = QApplication.instance().styleHints()
        self._accessibility = (
            self._style_hints.accessibility() if hasattr(self._style_hints, 'accessibility') else None
        )
        if self._accessibility is not None:
            self._accessibility.motionPreferenceChanged.connect(self._preference_changed)

    @property
    def reduced(self):
        forced = os.getenv('BYAX_REDUCED_MOTION', '').lower() in ('1', 'true', 'yes')
        selected = self.settings.value('appearance/reducedMotion', False, type=bool)
        system = (
            self._accessibility is not None
            and self._accessibility.motionPreference() == Qt.MotionPreference.ReducedMotion
        )
        return forced or selected or system

    def set_reduced(self, selected):
        self.settings.setValue('appearance/reducedMotion', bool(selected))
        self._preference_changed()

    def _preference_changed(self, *_):
        if self.reduced:
            for widget in list(self._fades):
                self.finish(widget)
        self.reducedChanged.emit(self.reduced)

    def finish(self, widget):
        animation = self._fades.pop(widget, None)
        if animation is not None:
            animation.stop()
            widget.setGraphicsEffect(None)
            animation.deleteLater()

    def fade(self, widget):
        self.finish(widget)
        if self.reduced or not widget.isVisible():
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b'opacity', self)
        animation.setDuration(TOKENS['motion']['durationMs'])
        animation.setStartValue(0.35)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(lambda: self.finish(widget))
        self._fades[widget] = animation
        animation.start()


class PenLineAnimation(BrandGraphic):
    """Play Vixl-authored transparent frames once, then retain the exact resting pose."""

    def __init__(self, motion, parent=None, *, profile='about-write', width=360):
        super().__init__(f'ui/{profile}-poster.png', width, parent)
        self.motion = motion
        self.manifest = json.loads((BRAND_ROOT / 'ui' / f'{profile}.json').read_text(encoding='utf-8'))
        self.sprite = QPixmap(str(BRAND_ROOT / 'ui' / f'{profile}-sprite.png'))
        self.frame_times = self.manifest['frameTimesMs']
        if self.sprite.isNull():
            raise RuntimeError(f'Missing Vixl motion frames: {profile}')
        self._progress = 1.0
        self.animation = QPropertyAnimation(self, b'progress', self)
        self.animation.setDuration(self.manifest['durationMs'])
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        # Vixl already baked the ease-out curve; the playback clock stays linear.
        self.animation.setEasingCurve(QEasingCurve.Linear)
        self.motion.reducedChanged.connect(self._preference_changed)

    def get_progress(self):
        return self._progress

    def set_progress(self, value):
        self._progress = value
        self.update()

    progress = Property(float, get_progress, set_progress)

    def _preference_changed(self, reduced):
        if reduced:
            self.animation.stop()
            self.set_progress(1.0)

    def showEvent(self, event):
        super().showEvent(event)
        if self.motion.reduced:
            self.set_progress(1.0)
        else:
            self.animation.start()

    def hideEvent(self, event):
        self.animation.stop()
        self.set_progress(1.0)
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        time = self._progress * self.manifest['durationMs']
        index = max(0, bisect.bisect_right(self.frame_times, time) - 1)
        width, height = self.manifest['frameWidth'], self.manifest['frameHeight']
        painter.drawPixmap(self.art_rect, self.sprite, QRectF(index * width, 0, width, height))
