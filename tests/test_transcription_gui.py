"""Dictation dialog: provisional display, phrase commits, cleanup policy, navigation and recovery."""

import json
import threading
import wave
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from byax import gui_providers
from byax.model import new_document
from byax.transcription import DictationJournal, RetentionStore, recover_sessions


class FakeRegistry:
    def __init__(self, dialog_ref, events, proposed=None):
        self.dialog_ref, self.events, self.proposed = dialog_ref, events, proposed
        self.observed = []

    def resolve(self, role, **kwargs):
        return {'alias': role, 'source': 'default'}

    def preview(self, alias, kind='review', *, text='', context=None, audio_path=None):
        return {
            'provider': alias,
            'kind': kind,
            'endpoint': 'https://example.invalid',
            'remote': True,
            'context': context or {},
            'audio': {'path': str(audio_path)} if audio_path else None,
        }

    def transcribe_stream(self, alias, path, *, on_event, **kwargs):
        dialog = self.dialog_ref()
        for event in self.events:
            on_event(event)
            self.observed.append(
                (
                    event['type'],
                    dialog.provisional.text(),
                    dialog.window.doc['blocks'][0]['text'],
                    len(dialog.window._undo),
                )
            )
        text = ' '.join(event['text'] for event in self.events if event['type'] == 'final')
        return {'text': text, 'segments': [e for e in self.events if e['type'] == 'final'], 'streamed': True}

    def review(self, alias, text, **kwargs):
        return {'proposed_text': self.proposed, 'findings': []}


EVENTS = [
    {'type': 'provisional', 'text': 'delete'},
    {'type': 'provisional', 'text': 'delete this'},
    {'type': 'final', 'index': 0, 'text': 'delete this paragraph', 'start': 0.0, 'end': 1.5},
    {'type': 'provisional', 'text': 'then'},
    {'type': 'final', 'index': 1, 'text': 'then more', 'start': 1.5, 'end': 2.0},
]


@pytest.fixture
def dictation(qtbot, monkeypatch, tmp_path):
    from byax.gui import MainWindow

    monkeypatch.setattr(gui_providers.QtMicrophoneRecorder, 'devices', staticmethod(list))
    window = MainWindow()
    qtbot.addWidget(window)
    window.doc = new_document(text='Start')
    window.doc['references'] = {'src': {'title': 'Source', 'text': 'Fact'}}
    window.refresh()
    cursor = window.editor.textCursor()
    cursor.setPosition(5)
    window.editor.setTextCursor(cursor)
    previews, reports = [], []
    monkeypatch.setattr(
        gui_providers, 'authorize_payload', lambda parent, title, preview: previews.append(preview) or True
    )
    monkeypatch.setattr(window, 'report', lambda title, result: reports.append(result))
    monkeypatch.setattr(
        gui_providers.QMessageBox, 'question', staticmethod(lambda *args, **kwargs: QMessageBox.Yes)
    )
    monkeypatch.setattr(
        window,
        'start_job',
        lambda kind, payload, worker, done: (
            done(worker(payload, threading.Event())) or {'id': 'job_' + '0' * 32}
        ),
    )
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    audio = tmp_path / 'speech.wav'
    with wave.open(str(audio), 'wb') as handle:
        handle.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        handle.writeframes(b'\x00\x00' * 1600)

    def make(events=EVENTS, proposed=None):
        holder = {}
        registry = FakeRegistry(lambda: holder['dialog'], events, proposed)
        dialog = gui_providers.DictationDialog(
            window,
            registry=registry,
            journal_root=tmp_path / 'journal',
            store=RetentionStore(tmp_path / 'store'),
        )
        holder['dialog'] = dialog
        qtbot.addWidget(dialog)
        return dialog, registry

    yield {
        'window': window,
        'make': make,
        'previews': previews,
        'reports': reports,
        'errors': errors,
        'audio': audio,
        'tmp': tmp_path,
    }
    window.dirty = False
    window.close()


def _choose(combo, value):
    combo.setCurrentIndex(combo.findData(value))


def test_provisional_text_is_staged_and_final_phrases_commit_once_each(dictation):
    window = dictation['window']
    dialog, registry = dictation['make']()
    _choose(dialog.policy, 'automatic_direct')
    undo = len(window._undo)
    dialog.transcribe(str(dictation['audio']), owned=False)
    kinds = [(kind, label, text, depth - undo) for kind, label, text, depth in registry.observed]
    assert kinds[0] == ('provisional', 'delete', 'Start', 0)
    assert kinds[1] == ('provisional', 'delete this', 'Start', 0)
    assert kinds[2] == ('final', '', 'Start delete this paragraph', 1)
    assert kinds[3] == ('provisional', 'then', 'Start delete this paragraph', 1)
    assert kinds[4] == ('final', '', 'Start delete this paragraph then more', 2)
    assert window.doc['blocks'][0]['text'] == 'Start delete this paragraph then more'
    assert len(window._undo) - undo == 2 and dialog.segments.count() == 2
    assert dialog.segments.item(0).text().startswith('[00:00.0–00:01.5]')
    location = dialog.navigate_segment(dialog.segments.item(1))
    assert location['status'] == 'unchanged' and window.editor.textCursor().selectedText() == 'then more'
    assert dialog.provisional.accessibleName().startswith('Provisional')
    assert dictation['errors'] == []


def test_suggestions_only_inserts_once_after_review(dictation):
    window = dictation['window']
    dialog, _ = dictation['make']()
    undo = len(window._undo)
    dialog.transcribe(str(dictation['audio']), owned=False)
    assert dictation['reports'] and window.doc['blocks'][0]['text'] == 'Startdelete this paragraph then more'
    assert len(window._undo) - undo == 1
    assert '(suggestion)' in dialog.segments.item(0).text()


def test_authorized_cleanup_replaces_only_the_committed_dictation(dictation):
    window = dictation['window']
    dialog, _ = dictation['make'](proposed='Delete this paragraph, then more.')
    _choose(dialog.mode, 'cleanup')
    _choose(dialog.policy, 'automatic_cleanup')
    dialog.nearby.setValue(1)
    dialog.dictionary.setChecked(True)
    dialog.references.item(0).setCheckState(gui_providers.Qt.Checked)
    undo = len(window._undo)
    dialog.transcribe(str(dictation['audio']), owned=False)
    assert window.doc['blocks'][0]['text'] == 'Start Delete this paragraph, then more.'
    assert len(window._undo) - undo == 3
    editorial = dictation['previews'][0]['editorial_processing']
    kinds = {part['kind'] for part in editorial['selection']['parts']}
    assert {'nearby_paragraphs', 'dictionary', 'selected_references'} <= kinds
    assert 'blocks' not in editorial['context']  # Cleanup mode never sends document text.
    assert dictation['previews'][0]['application_policy'] == 'automatic_cleanup'


def test_retention_controls_show_storage_impact_and_link_records(dictation):
    dialog, _ = dictation['make']()
    _choose(dialog.policy, 'automatic_direct')
    assert 'not kept' in dialog.impact.text()
    dialog.retain_raw.setChecked(True)
    assert 'Raw transcript: kept' in dialog.impact.text()
    dialog.transcribe(str(dictation['audio']), owned=False)
    assert 'Retained: ' in dialog.state.text()
    record = json.loads(Path(dialog.session.retained['record']).read_text(encoding='utf-8'))
    assert record['raw_transcript'] == 'delete this paragraph then more'
    assert '1 session(s)' in dialog.impact.text() and dictation['audio'].exists()


def test_recovery_inserts_completed_segments_and_discards_journal(dictation):
    window = dictation['window']
    root = dictation['tmp'] / 'journal'
    journal = DictationJournal('dictation_old', root)
    journal.segment({'index': 0, 'text': 'Recovered words', 'start': 0.0, 'end': 1.0})
    data = json.loads(journal.path.read_text())
    data['owner_pid'] = 999_999_999
    journal.path.write_text(json.dumps(data))
    dialog, _ = dictation['make']()
    assert '1 interrupted' in dialog.state.text()
    sessions = dialog.recover()
    assert sessions[0]['resubmitted'] is False
    assert window.doc['blocks'][0]['text'] == 'StartRecovered words'
    assert recover_sessions(root) == []
