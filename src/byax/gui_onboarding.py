# ruff: noqa: BLE001
"""First-run tour, sample project and manual update check (Help menu).

Nothing here runs by itself except the tour, once, on the first launch. The update check makes one
request only when its menu item is chosen; the tour and the sample never touch the network.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import updates
from .brand import BrandGraphic
from .gui_motion import PenLineAnimation
from .gui_platform import native_keys

TOUR_VERSION = 1
TOUR_SEEN_KEY = 'onboarding/tourVersionSeen'
TOUR_DISABLED_KEY = 'onboarding/tourDisabled'
UPDATE_MENU_KEY = 'updates/menuEnabled'
NO_TOUR_ENV = 'BYAX_NO_TOUR'

# (id, title, body). Plain data so the wording is reviewed in one place; the dialog translates it.
TOUR_STEPS = (
    (
        'welcome',
        'Welcome to ByAuthorX',
        (
            'Your words. Your call.\n\nYour writing stays on this computer. No account, no telemetry, and '
            'no model sees your text unless you set up a provider and ask it to.'
        ),
    ),
    (
        'styles',
        'Styles carry the structure',
        (
            'Headings, body text and quotations are named styles, not hand formatting. Choose a style in '
            'the Format menu or the ribbon and every paragraph that uses it stays consistent. Export to '
            'Word, PDF or EPUB keeps that structure.'
        ),
    ),
    (
        'outline',
        'Outline and binder',
        (
            'The Outline panel lists your headings. In a project, each chapter and scene is a binder item '
            'with a status, a synopsis and a word target, and moving a section moves its text with it.'
        ),
    ),
    (
        'proposals',
        'Proposals before changes',
        (
            'Suggested edits, from a reviewer or an assistant you chose, arrive as proposals. You accept or '
            'reject each edit on its own, and nothing changes in your text until you accept it.'
        ),
    ),
    (
        'checks',
        'Checks and citations',
        (
            'The Check button (%CHECK%) runs the document checks, and Cite (%CITE%) inserts a citation that '
            'points at a source record, so the reference list always matches the text.'
        ),
    ),
    (
        'sample',
        'Try the sample project',
        (
            'Choose Help, then Open sample project, for a small manuscript that uses every feature above. '
            'It opens as a copy, so you can change anything. Help, then Take the tour reopens this tour.'
        ),
    ),
)


def step_text(index):
    """Title and body of a step with this platform's shortcut names filled in."""
    _, title, body = TOUR_STEPS[index]
    body = body.replace('%CHECK%', native_keys('F7')).replace('%CITE%', native_keys('Ctrl+Shift+C'))
    return title, body


def tour_due(settings, environ=None):
    """The tour runs on the first launch, and again only if a newer tour was never seen or declined."""
    env = os.environ if environ is None else environ
    if env.get(NO_TOUR_ENV, '').strip().lower() in ('1', 'true', 'yes'):
        return False
    if settings.value(TOUR_DISABLED_KEY, False, type=bool):
        return False
    return settings.value(TOUR_SEEN_KEY, 0, type=int) < TOUR_VERSION


def mark_tour_seen(settings, *, never_again=None):
    settings.setValue(TOUR_SEEN_KEY, TOUR_VERSION)
    if never_again is not None:
        settings.setValue(TOUR_DISABLED_KEY, bool(never_again))


class TourDialog(QDialog):
    """Six short steps. Skip (Escape), Back/Next (arrow keys), and a persistent "don't show again"."""

    stepChanged = Signal(int)
    closedAs = Signal(str)

    def __init__(self, motion, settings, parent=None):
        super().__init__(parent)
        self.motion = motion
        self.settings = settings
        self.index = 0
        self.outcome = None
        self.last_transition = 'none'
        self.setObjectName('tourDialog')
        self.setWindowTitle(self.tr('ByAuthorX tour'))
        self.setAccessibleName(self.tr('ByAuthorX tour'))
        self.setAccessibleDescription(
            self.tr('A short introduction. Use Next and Back or the arrow keys; Escape skips it.')
        )
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(14)
        self.art_slot = QWidget()
        art = QVBoxLayout(self.art_slot)
        art.setContentsMargins(0, 0, 0, 0)
        self.wordmark = BrandGraphic('wordmark-light.png', 280)
        self.wordmark.setAccessibleName(self.tr('ByAuthorX'))
        art.addWidget(self.wordmark)
        # The finite pen-line plays once on the welcome step; under reduced motion it shows its resting pose.
        self.pen = PenLineAnimation(motion, width=280)
        art.addWidget(self.pen)
        layout.addWidget(self.art_slot)
        self.progress = QLabel()
        self.progress.setObjectName('tourProgress')
        layout.addWidget(self.progress)
        self.page = QWidget()
        page = QVBoxLayout(self.page)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName('tourTitle')
        self.title.setWordWrap(True)
        self.title.setStyleSheet('font-size: 20px; font-weight: 600;')
        page.addWidget(self.title)
        self.body = QLabel()
        self.body.setObjectName('tourBody')
        self.body.setWordWrap(True)
        self.body.setMinimumHeight(120)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        # Focusable so a screen reader reads the step when focus moves here after Next.
        self.body.setFocusPolicy(Qt.TabFocus)
        page.addWidget(self.body)
        layout.addWidget(self.page, 1)
        self.never_again = QCheckBox(self.tr("Don't show this tour again"))
        self.never_again.setChecked(self.settings.value(TOUR_DISABLED_KEY, False, type=bool))
        self.never_again.setAccessibleName(self.tr("Don't show this tour again"))
        layout.addWidget(self.never_again)
        row = QHBoxLayout()
        self.skip_button = QPushButton(self.tr('Skip tour'))
        self.skip_button.setAccessibleName(self.tr('Skip tour'))
        self.back_button = QPushButton(self.tr('Back'))
        self.back_button.setAccessibleName(self.tr('Back'))
        self.next_button = QPushButton()
        self.next_button.setDefault(True)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        row.addWidget(self.back_button)
        row.addWidget(self.next_button)
        layout.addLayout(row)
        self.skip_button.clicked.connect(self.skip)
        self.back_button.clicked.connect(self.back)
        self.next_button.clicked.connect(self.next)
        # Shortcuts beat the focus-moving arrow handling of buttons inside a dialog.
        for key, slot in (
            (Qt.Key_Right, self.next),
            (Qt.Key_PageDown, self.next),
            (Qt.Key_Left, self.back),
            (Qt.Key_PageUp, self.back),
        ):
            QShortcut(QKeySequence(key), self, slot, context=Qt.WidgetWithChildrenShortcut)
        self.show_step(0, transition=False)

    @property
    def total(self):
        return len(TOUR_STEPS)

    def show_step(self, index, *, transition=True):
        self.index = max(0, min(index, self.total - 1))
        title, body = step_text(self.index)
        last = self.index == self.total - 1
        label = self.tr('Step %1 of %2').replace('%1', str(self.index + 1)).replace('%2', str(self.total))
        self.progress.setText(label)
        self.progress.setAccessibleName(label)
        self.title.setText(self.tr(title))
        self.body.setText(self.tr(body))
        self.body.setAccessibleName(label + '. ' + self.tr(title))
        self.body.setAccessibleDescription(self.tr(body))
        self.next_button.setText(self.tr('Finish') if last else self.tr('Next'))
        self.next_button.setAccessibleName(self.tr('Finish tour') if last else self.tr('Next step'))
        self.back_button.setEnabled(self.index > 0)
        self.art_slot.setVisible(self.index == 0)
        self.last_transition = 'none'
        if transition:
            # Reduced motion: the new step simply appears. Otherwise one short fade, no movement.
            self.last_transition = 'instant' if self.motion.reduced else 'fade'
            self.motion.fade(self.page)
            self.body.setFocus()
        self.stepChanged.emit(self.index)

    def next(self):
        if self.index >= self.total - 1:
            self.finish('completed')
        else:
            self.show_step(self.index + 1)

    def back(self):
        if self.index > 0:
            self.show_step(self.index - 1)

    def skip(self):
        self.finish('skipped')

    def finish(self, outcome):
        if self.outcome is None:
            self.outcome = outcome
            mark_tour_seen(self.settings, never_again=self.never_again.isChecked())
            self.closedAs.emit(outcome)
        if outcome == 'completed':
            self.accept()
        else:
            self.reject()

    def reject(self):
        # Escape and the window's close button count as skipping.
        if self.outcome is None:
            self.finish('skipped')
            return
        super().reject()


class OnboardingPanels:
    """Mixed into the main window: Help menu entries and their handlers."""

    def build_onboarding_menu(self, help_menu):
        self.tour_action = self.action(self.tr('Take the tour…'), self.show_tour)
        help_menu.addAction(self.tour_action)
        help_menu.addAction(self.action(self.tr('Open sample project'), self.open_sample))
        self.update_action = self.action(self.tr('Check for updates…'), self.check_for_updates)
        self.update_menu_action = self.action(
            self.tr('Show "Check for updates" in this menu'), self.set_update_menu, checkable=True
        )
        help_menu.addAction(self.update_action)
        help_menu.addAction(self.update_menu_action)
        self.update_menu_action.setChecked(self.update_menu_enabled())
        self.apply_update_menu()

    # Tour

    def maybe_show_tour(self):
        """Called once by ``main`` after the window shows; a no-op on later launches."""
        if tour_due(self.settings):
            self.show_tour()

    def show_tour(self):
        existing = getattr(self, '_tour', None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return existing
        self._tour = TourDialog(self.motion, self.settings, self)
        mark_tour_seen(self.settings)  # first run only: shown once even if the window is closed abruptly
        self._tour.open()
        return self._tour

    # Sample project

    def open_sample(self):
        from .samples import install_sample

        try:
            copied = install_sample()
        except Exception as exc:
            self.error(exc)
            return None
        if self.open_path(copied['path']):
            self.status_label.setText(self.tr('Opened a copy of the sample project'))
        return copied['path']

    # Update check (manual only)

    def update_menu_enabled(self):
        return not updates.update_checks_disabled() and self.settings.value(UPDATE_MENU_KEY, True, type=bool)

    def apply_update_menu(self):
        disabled_by_policy = updates.update_checks_disabled()
        self.update_action.setVisible(self.update_menu_enabled())
        self.update_menu_action.setVisible(not disabled_by_policy)

    def set_update_menu(self, enabled):
        self.settings.setValue(UPDATE_MENU_KEY, bool(enabled))
        self.apply_update_menu()

    def check_for_updates(self):
        """One request, on this click only; the result appears in a dialog and nothing is installed."""
        self.background(
            self.tr('Checking for updates…'),
            updates.check_for_updates,
            self.show_update_result,
        )

    def update_result_dialog(self, result):
        box = QMessageBox(self)
        box.setWindowTitle(self.tr('Check for updates'))
        box.setIcon(QMessageBox.Information)
        current, latest = result['current_version'], result['latest_version']
        if result['status'] == 'update_available':
            box.setText(
                self.tr('ByAuthorX %1 is available. You have %2.')
                .replace('%1', latest)
                .replace('%2', current)
            )
            box.setInformativeText(
                self.tr(
                    'The release page has the installers and notes. ByAuthorX does not download or install updates.'
                )
            )
            open_button = box.addButton(self.tr('Open release page'), QMessageBox.AcceptRole)
            open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(result['release_url'])))
        elif result['status'] == 'up_to_date':
            box.setText(self.tr('You have the latest version (%1).').replace('%1', current))
        else:
            box.setText(
                self.tr('You have %1, which is newer than the latest release (%2).')
                .replace('%1', current)
                .replace('%2', latest)
            )
        box.addButton(QMessageBox.Close)
        box.setDetailedText(self.tr('Checked once: %1').replace('%1', result['source']))
        return box

    def show_update_result(self, result):
        self.update_result_dialog(result).exec()
