"""Writing goals, daily progress, sessions and streaks (CP A3).

Targets that describe the manuscript (the document target and per-item targets) live in the
document's binder and change through ``project_set_target``. Targets and records that describe the
writer's own habits live here, in a local preferences file under the user config directory, keyed by
the document identity:

* the daily goal and the per-session target;
* session records (start, end, words at both ends) and the per-day totals derived from them.

Session records are never part of a document: recording one does not touch the document, create a
revision or enter history. Progress counts authored words through the shared counter
(``statistics.counts``); ``format`` counts the publication projection instead.

A session's ``written`` is the growth in words between its start and end, never negative, so
deleting text neither counts against the writer nor makes up for earlier growth. A session belongs to
the local date it started on.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import ByaxError
from .model import text_of
from .statistics import document_statistics
from .statistics import word_count as count_words

SCHEMA = 1
MAX_GOAL = 10_000_000
MAX_SESSIONS = 100
MAX_DAYS = 1000
WRITE_ACTIONS = ('set-daily', 'set-session')
READ_ACTIONS = ('status', 'history')

Doc = dict[str, Any]


def default_dir() -> Path:
    from platformdirs import user_config_path

    return user_config_path('ByAuthorX', appauthor=False)


def _error(message: str, field: str | None = None, **details: Any):
    raise ByaxError('invalid_input', message, field=field, **details)


def _valid_goal(value: Any) -> bool:
    return type(value) is int and 1 <= value <= MAX_GOAL


def _today(today: date | None) -> date:
    return today or datetime.now().astimezone().date()


class GoalStore:
    """The goals preferences file; every write is a complete atomic replacement."""

    def __init__(self, config_dir: str | Path | None = None):
        self.path = Path(config_dir or default_dir()) / 'goals.json'

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'schema': SCHEMA, 'documents': {}}
        except (OSError, ValueError) as exc:
            raise ByaxError(
                'execution_failure',
                f'The goals file cannot be read; move or repair {self.path}.',
                path=str(self.path),
            ) from exc
        if not isinstance(raw, dict) or not isinstance(raw.get('documents'), dict):
            raise ByaxError(
                'execution_failure', f'The goals file is not recognized: {self.path}', path=str(self.path)
            )
        return raw

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.byax-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def document(self, doc_id: str) -> dict[str, Any]:
        """The stored goals and records for one document (empty defaults when none)."""
        record = self._read()['documents'].get(doc_id)
        record = record if isinstance(record, dict) else {}
        return {
            'daily_words': record.get('daily_words'),
            'session_words': record.get('session_words'),
            'days': dict(record.get('days', {})),
            'sessions': list(record.get('sessions', [])),
        }

    def set_goal(
        self,
        doc_id: str,
        field: str,
        words: int | None,
        *,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Set (or with ``None`` clear) ``daily_words`` or ``session_words``."""
        if field not in ('daily_words', 'session_words'):
            _error('Unknown goal.', 'field')
        if words is not None and not _valid_goal(words):
            _error('Goals are whole numbers of words from 1 to 10,000,000.', 'words')
        data = self._read()
        record = data['documents'].setdefault(doc_id, {})
        if words is None:
            record.pop(field, None)
        else:
            record[field] = words
        if title:
            record['title'] = title
        self._write(data)
        return self.document(doc_id)

    def record_session(self, doc_id: str, session: Session, *, title: str | None = None) -> dict[str, Any]:
        """Insert or update one session and its day total; returns the day's entry."""
        data = self._read()
        record = data['documents'].setdefault(doc_id, {})
        sessions = record.setdefault('sessions', [])
        entry = session.record()
        previous = next((s for s in sessions if s.get('id') == entry['id']), None)
        if previous is None:
            sessions.append(entry)
        else:
            sessions[sessions.index(previous)] = entry
        del sessions[:-MAX_SESSIONS]
        days = record.setdefault('days', {})
        day = days.setdefault(entry['date'], {'written': 0, 'sessions': 0})
        day['written'] = max(0, day['written'] + entry['written'] - (previous['written'] if previous else 0))
        if previous is None:
            day['sessions'] += 1
        for stale in sorted(days)[:-MAX_DAYS]:
            del days[stale]
        if title:
            record['title'] = title
        self._write(data)
        return dict(day, date=entry['date'])


class Session:
    """One writing session: the word count when it began and the count now."""

    def __init__(self, doc_id: str, start_words: int, started: datetime | None = None):
        self.doc_id = doc_id
        self.started = started or datetime.now().astimezone()
        self.start_words = start_words
        self.words = start_words
        self.ended = self.started
        self.saved_written: int | None = None

    @property
    def written(self) -> int:
        return max(0, self.words - self.start_words)

    @property
    def id(self) -> str:
        return self.started.isoformat(timespec='seconds')

    def update(self, words: int, now: datetime | None = None) -> bool:
        """Note the current word count; true when the record would differ from the last save."""
        self.words = words
        self.ended = now or datetime.now().astimezone()
        return self.written != self.saved_written

    def record(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'date': self.started.date().isoformat(),
            'started': self.started.isoformat(timespec='seconds'),
            'ended': self.ended.isoformat(timespec='seconds'),
            'start_words': self.start_words,
            'end_words': self.words,
            'written': self.written,
        }

    def save(self, store: GoalStore, *, title: str | None = None) -> dict[str, Any]:
        """Store the session; nothing is written when it has never written a word."""
        if self.written == 0 and self.saved_written is None:
            return {}
        day = store.record_session(self.doc_id, self, title=title)
        self.saved_written = self.written
        return day


def word_count(doc: Doc, format: str | None = None) -> int:
    """Authored words by default; the publication projection's words when a format is given."""
    if format:
        return document_statistics(doc, output=format)['publication']['words']
    return count_words(text_of(doc))


def streak(days: dict[str, Any], goal: int | None, today: date) -> dict[str, Any]:
    """Consecutive days meeting the daily goal (or any writing when there is no goal).

    Today does not break a streak before it has been met; the run then ends yesterday.
    """

    def met(day: date) -> bool:
        written = days.get(day.isoformat(), {}).get('written', 0)
        return written >= goal if goal else written > 0

    cursor = today if met(today) else today - timedelta(days=1)
    current = 0
    while met(cursor):
        current += 1
        cursor -= timedelta(days=1)
    best = run = 0
    previous: date | None = None
    for key in sorted(days):
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if not met(day):
            run, previous = 0, None
            continue
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best, previous = max(best, run), day
    return {'current': current, 'best': max(best, current), 'basis': 'goal' if goal else 'any_writing'}


def _ratio(done: int, target: int | None) -> float | None:
    return round(done / target, 4) if target else None


def status(
    doc: Doc,
    store: GoalStore,
    *,
    today: date | None = None,
    format: str | None = None,
    live_written: int = 0,
) -> dict[str, Any]:
    """Progress against every target; ``live_written`` adds an unsaved session's growth to today."""
    from . import project

    today = _today(today)
    record = store.document(doc['id'])
    words = word_count(doc, format)
    stored = doc.get('project')
    target = stored.get('target_words') if isinstance(stored, dict) else None
    written = record['days'].get(today.isoformat(), {}).get('written', 0) + live_written
    daily = record['daily_words']
    days = dict(record['days'])
    if live_written:
        days[today.isoformat()] = {'written': written}
    entries = [
        {
            'id': row['id'],
            'title': row['title'],
            'kind': row['kind'],
            'words': row['total_words'],
            'target': row['target_words'],
            'remaining': max(0, row['target_words'] - row['total_words']),
            'progress': _ratio(row['total_words'], row['target_words']),
            'met': row['total_words'] >= row['target_words'],
        }
        for row in project.items(doc)
        if 'target_words' in row
    ]
    return {
        'basis': format or 'authored',
        'document': {
            'words': words,
            'target': target,
            'remaining': max(0, target - words) if target else None,
            'progress': _ratio(words, target),
            'met': bool(target) and words >= target,
        },
        'items': entries,
        'daily': {
            'goal': daily,
            'date': today.isoformat(),
            'written': written,
            'remaining': max(0, daily - written) if daily else None,
            'progress': _ratio(written, daily),
            'met': bool(daily) and written >= daily,
        },
        'session': {'goal': record['session_words']},
        'streak': streak(days, daily, today),
    }


def history(doc: Doc, store: GoalStore, *, limit: int = 30, today: date | None = None) -> dict[str, Any]:
    """Recorded days (newest first) with whether each met the daily goal, and recent sessions."""
    record = store.document(doc['id'])
    daily = record['daily_words']
    ordered = sorted(record['days'], reverse=True)[:limit]
    return {
        'daily_goal': daily,
        'days': [
            {
                'date': key,
                'written': record['days'][key]['written'],
                'sessions': record['days'][key].get('sessions', 0),
                'met': record['days'][key]['written'] >= daily
                if daily
                else record['days'][key]['written'] > 0,
            }
            for key in ordered
        ],
        'sessions': record['sessions'][-min(limit, 20) :][::-1],
        'streak': streak(record['days'], daily, _today(today)),
    }


def goals_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    """``byax goals``: read progress and history, set the local daily and session goals."""
    from .cli import _load, envelope

    action = a.get('action', 'status')
    if action not in READ_ACTIONS + WRITE_ACTIONS:
        raise ByaxError(
            'invalid_input', f'Unknown goals action: {action}', supported=list(READ_ACTIONS + WRITE_ACTIONS)
        )
    doc = _load(a['path'])
    store = GoalStore(a.get('config_dir'))
    if action == 'status':
        return envelope({'goals': status(doc, store, format=a.get('format'))}, doc)
    if action == 'history':
        return envelope({'history': history(doc, store, limit=a.get('limit', 30))}, doc)
    field = 'daily_words' if action == 'set-daily' else 'session_words'
    if a.get('clear'):
        words = None
    elif 'words' in a:
        words = a['words']
    else:
        _error(f'{action} requires --words or --clear', 'words')
    store.set_goal(doc['id'], field, words, title=doc['metadata'].get('title'))
    return envelope({'goals': status(doc, store, format=a.get('format'))}, doc)
