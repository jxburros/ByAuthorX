# ruff: noqa: BLE001
"""Writing goals in the desktop window: a status-bar progress button, session tracking and the Goals dialog.

Targets that describe the manuscript (document and item targets) are edited through ``project_set_target``
and live in the document. The daily and session goals and the session records are local preferences
(``byax.goals``); they never enter the document or its history.
"""

from datetime import date

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from . import goals

SAVE_INTERVAL_MS = 60_000


class GoalsDialog(QDialog):
    """Targets, today's progress, the streak and recent days."""

    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self.setWindowTitle(self.tr('Writing goals'))
        self.resize(480, 560)
        doc = window.doc
        self.state = window.goals_state()
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'Goals count the words you add. Daily and session goals are kept on this computer, outside the '
                'document. The document and item targets are saved in the document’s binder.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.words = QLabel()
        self.words.setAccessibleName(self.tr('Words in the document'))
        form.addRow(self.tr('Words now'), self.words)
        self.target = self._spin(self.tr('Document word target'), self.tr('No target'))
        form.addRow(self.tr('Document target'), self.target)
        self.daily = self._spin(self.tr('Daily word goal'), self.tr('No goal'))
        form.addRow(self.tr('Daily goal'), self.daily)
        self.session = self._spin(self.tr('Session word goal'), self.tr('No goal'))
        form.addRow(self.tr('Session goal'), self.session)
        layout.addLayout(form)
        self.document_bar = self._bar(self.tr('Progress toward the document target'))
        layout.addWidget(self.document_bar)
        self.today = QLabel()
        self.today.setAccessibleName(self.tr('Words written today'))
        layout.addWidget(self.today)
        self.today_bar = self._bar(self.tr('Progress toward the daily goal'))
        layout.addWidget(self.today_bar)
        self.session_label = QLabel()
        self.session_label.setAccessibleName(self.tr('Words written this session'))
        layout.addWidget(self.session_label)
        self.streak = QLabel()
        self.streak.setAccessibleName(self.tr('Writing streak'))
        layout.addWidget(self.streak)
        layout.addWidget(QLabel(self.tr('Item targets')))
        self.items = QListWidget()
        self.items.setAccessibleName(self.tr('Items with word targets'))
        layout.addWidget(self.items, 1)
        layout.addWidget(QLabel(self.tr('Recent days')))
        self.days = QListWidget()
        self.days.setAccessibleName(self.tr('Recent days and whether the daily goal was met'))
        layout.addWidget(self.days, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Save).setToolTip(self.tr('Save the goals'))
        buttons.button(QDialogButtonBox.Close).setToolTip(self.tr('Close without saving changes'))
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.fill(doc)

    def _spin(self, name, none):
        spin = QSpinBox()
        spin.setRange(0, goals.MAX_GOAL)
        spin.setSpecialValueText(none)
        spin.setGroupSeparatorShown(True)
        spin.setAccessibleName(name)
        spin.setToolTip(name + self.tr(' (0 removes it)'))
        return spin

    def _bar(self, name):
        bar = QProgressBar()
        bar.setTextVisible(True)
        bar.setAccessibleName(name)
        bar.setToolTip(name)
        return bar

    def fill(self, doc):
        state = self.state
        document, daily = state['document'], state['daily']
        self.words.setText(f'{document["words"]:,}')
        self.target.setValue(document['target'] or 0)
        self.daily.setValue(daily['goal'] or 0)
        self.session.setValue(state['session']['goal'] or 0)
        self._show_bar(self.document_bar, document['words'], document['target'])
        self.today.setText(self.tr('Written today: {0:,} words').format(daily['written']))
        self._show_bar(self.today_bar, daily['written'], daily['goal'])
        written = self.host.goal_session.written if self.host.goal_session else 0
        goal = state['session']['goal']
        self.session_label.setText(
            self.tr('This session: {0:,} of {1:,} words').format(written, goal)
            if goal
            else self.tr('This session: {0:,} words').format(written)
        )
        streak = state['streak']
        self.streak.setText(
            self.tr('Streak: {0} day(s) in a row (best {1}), counting {2}').format(
                streak['current'],
                streak['best'],
                self.tr('days that met the daily goal')
                if streak['basis'] == 'goal'
                else self.tr('days with any writing'),
            )
        )
        for entry in state['items']:
            self.items.addItem(
                self.tr('{0}: {1:,} of {2:,} words ({3})').format(
                    entry['title'] or self.tr('Untitled'),
                    entry['words'],
                    entry['target'],
                    self.tr('met') if entry['met'] else self.tr('{0:,} to go').format(entry['remaining']),
                )
            )
        if not state['items']:
            self.items.addItem(self.tr('No item has a target yet. Set one with Word target… in the Binder.'))
        for entry in self.host.goals_recent_days():
            self.days.addItem(
                self.tr('{0}: {1:,} words, {2}').format(
                    entry['date'],
                    entry['written'],
                    self.tr('goal met') if entry['met'] else self.tr('goal not met'),
                )
            )
        if not self.days.count():
            self.days.addItem(self.tr('No writing sessions recorded yet.'))

    def _show_bar(self, bar, done, target):
        if target:
            bar.setRange(0, target)
            bar.setValue(min(done, target))
            bar.setFormat(self.tr('{0:,} of {1:,} words').format(done, target))
        else:
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat(self.tr('No target set'))

    def save(self):
        host = self.host
        try:
            host.set_local_goals(self.daily.value() or None, self.session.value() or None)
            wanted = self.target.value() or None
            current = host.doc.get('project', {}).get('target_words') if 'project' in host.doc else None
            if wanted != current:
                operations = [] if 'project' in host.doc else [{'type': 'project_init'}]
                operations.append({'type': 'project_set_target', 'words': wanted})
                if not host.commit(operations):
                    return False
        except Exception as exc:
            host.error(exc)
            return False
        self.accept()
        return True


class GoalPanels:
    def build_goals(self):
        self.goal_store = goals.GoalStore()
        self.goal_session = None
        self._goal_record = {'daily_words': None, 'session_words': None, 'days': {}, 'sessions': []}
        self._goal_warned = False
        self.goals_button = QPushButton()
        self.goals_button.setObjectName('goalsButton')
        self.goals_button.setFlat(True)
        self.goals_button.setAccessibleName(self.tr('Writing goals'))
        self.goals_button.clicked.connect(self.goals_dialog)
        self.statusBar().addPermanentWidget(self.goals_button)
        self.goals_timer = QTimer(self)
        self.goals_timer.setInterval(SAVE_INTERVAL_MS)
        self.goals_timer.timeout.connect(self.goals_save)
        self.goals_timer.start()
        self.goals_begin()

    # Session lifecycle

    def _goal_words(self):
        return goals.word_count(self.doc)

    def goals_begin(self):
        """Start a session for the open document; the previous one is stored first."""
        self.goals_finish()
        self.goal_session = goals.Session(self.doc['id'], self._goal_words())
        self._goal_load()
        self.update_goals()

    def _goal_load(self):
        try:
            self._goal_record = self.goal_store.document(self.doc['id'])
        except Exception as exc:
            self._goal_record = {'daily_words': None, 'session_words': None, 'days': {}, 'sessions': []}
            self._goal_problem(exc)

    def _goal_problem(self, exc):
        if not self._goal_warned:
            self._goal_warned = True
            self.status_label.setText(self.tr('Writing goals unavailable: {0}').format(exc))

    def goals_finish(self):
        session = getattr(self, 'goal_session', None)
        if session is not None:
            self.goals_save()
        self.goal_session = None

    def goals_save(self):
        """Store the session if it has written anything new; never interrupts writing."""
        session = self.goal_session
        if session is None or session.doc_id != self.doc['id']:
            return
        session.update(self._goal_words())
        if session.written == (session.saved_written or 0):
            return
        try:
            day = session.save(self.goal_store, title=self.doc['metadata'].get('title'))
            if day:
                self._goal_record['days'][day['date']] = {
                    'written': day['written'],
                    'sessions': day['sessions'],
                }
        except Exception as exc:
            self._goal_problem(exc)

    def goals_words_changed(self, words):
        """Called after each edit with the current word count."""
        session = getattr(self, 'goal_session', None)
        if session is not None and session.doc_id == self.doc['id']:
            session.update(words)
            self.update_goals()

    # Reading

    def _live_written(self):
        session = self.goal_session
        if session is None or session.doc_id != self.doc['id']:
            return 0
        return max(0, session.written - (session.saved_written or 0))

    def goals_state(self):
        """Progress now, including the words of the running session not yet stored."""
        store = self.goal_store
        return (
            goals.status(self.doc, store, live_written=self._live_written())
            if self._store_readable()
            else {
                'document': {'words': self._goal_words(), 'target': None},
                'items': [],
                'daily': {'goal': None, 'written': self._live_written()},
                'session': {'goal': None},
                'streak': {'current': 0, 'best': 0, 'basis': 'any_writing'},
            }
        )

    def _store_readable(self):
        try:
            self.goal_store.document(self.doc['id'])
            return True
        except Exception as exc:
            self._goal_problem(exc)
            return False

    def goals_recent_days(self, limit=14):
        try:
            return goals.history(self.doc, self.goal_store, limit=limit)['days']
        except Exception:
            return []

    def update_goals(self):
        """Refresh the status-bar button from cached records (no file access)."""
        session = self.goal_session
        if not hasattr(self, 'goals_button') or session is None:
            return
        record = self._goal_record
        today = date.today().isoformat()
        written_today = record['days'].get(today, {}).get('written', 0) + self._live_written()
        daily, per_session = record['daily_words'], record['session_words']
        if daily:
            text = self.tr('Today {0:,}/{1:,}').format(written_today, daily)
        elif per_session:
            text = self.tr('Session {0:,}/{1:,}').format(session.written, per_session)
        else:
            text = self.tr('Session +{0:,}').format(session.written)
        streak = goals.streak({**record['days'], today: {'written': written_today}}, daily, date.today())[
            'current'
        ]
        description = self.tr(
            'Writing goals. Written today: {0:,} words. Written this session: {1:,} words.'
        ).format(written_today, session.written)
        if daily:
            description += ' ' + self.tr('Daily goal: {0:,} words.').format(daily)
        if streak:
            description += ' ' + self.tr('Streak: {0} day(s).').format(streak)
        description += ' ' + self.tr('Open the Writing goals window.')
        if self.goals_button.text() != text:
            self.goals_button.setText(text)
        self.goals_button.setToolTip(description)
        self.goals_button.setAccessibleDescription(description)

    # Writing preferences and the dialog

    def set_local_goals(self, daily, session):
        record = self.goal_store.document(self.doc['id'])
        title = self.doc['metadata'].get('title')
        if daily != record['daily_words']:
            self.goal_store.set_goal(self.doc['id'], 'daily_words', daily, title=title)
        if session != record['session_words']:
            self.goal_store.set_goal(self.doc['id'], 'session_words', session, title=title)
        self._goal_load()
        self.update_goals()

    def goals_dialog(self):
        if not self.flush_edits():
            return
        self.goals_save()
        GoalsDialog(self).exec()
        self.update_goals()
