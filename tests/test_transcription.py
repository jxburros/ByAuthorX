"""Dictation modes, streaming staging, guarded cleanup, context, recovery and retention."""

import json
import threading
import wave
from pathlib import Path

import pytest

from byax.engine import apply
from byax.errors import ByaxError
from byax.model import new_document
from byax.transcription import (
    DictationJournal,
    DictationSession,
    LiveDictation,
    RetentionStore,
    apply_direct_options,
    build_context,
    compare_transcripts,
    discard_session,
    recover_sessions,
    repair_wav,
)

DEAD_PID = 999_999_999


def _edit(doc, operations):
    return apply(doc, {'expected_revision': doc['revision'], 'operations': operations})[0]


def test_direct_options_default_to_recognizer_output():
    raw = 'Um so the the plan comma uh works period'
    assert apply_direct_options(raw) == raw
    assert apply_direct_options(raw, {'fillers': 'remove'}) == 'so the the plan comma works period'
    assert (
        apply_direct_options(raw, {'fillers': 'remove', 'repetitions': 'collapse', 'punctuation': 'spoken'})
        == 'so the plan, works.'
    )
    with pytest.raises(ByaxError):
        apply_direct_options(raw, {'fillers': 'maybe'})


def test_provisional_updates_never_touch_document_or_history():
    doc = new_document(text='Start')
    target = doc['blocks'][0]['id']
    live = LiveDictation(doc, target, 5, policy='automatic_direct')
    before = json.dumps(doc, sort_keys=True)
    for text in ('del', 'delete th', 'delete this paragraph'):
        state = live.provisional(text)
    assert state['provisional'] and not state['committed'] and live.provisional_updates == 3
    assert json.dumps(doc, sort_keys=True) == before


def test_final_phrases_are_one_literal_revision_each():
    doc = new_document(text='Start')
    target = doc['blocks'][0]['id']
    history = len(doc['history']['revisions'])
    live = LiveDictation(doc, target, 5, policy='automatic_direct')
    live.provisional('delete this')
    doc, first = live.commit_final(doc, 'delete this paragraph.', start=0.0, end=1.2)
    for update in ('and', 'and then'):
        live.provisional(update)
    doc, second = live.commit_final(doc, 'And then undo.', start=1.2, end=2.0)
    assert doc['blocks'][0]['text'] == 'Start delete this paragraph. And then undo.'
    assert len(doc['history']['revisions']) - history == 2
    assert first['status'] == second['status'] == 'committed'
    assert doc['blocks'][0]['text'][second['start'] : second['end']] == 'And then undo.'
    assert live.provisional_text == ''


def test_suggestions_only_never_edits():
    doc = new_document(text='Body')
    live = LiveDictation(doc, doc['blocks'][0]['id'], 0)
    assert live.final(doc, 'A suggestion') is None
    assert [phrase['text'] for phrase in live.suggestions()] == ['A suggestion']


def test_anchor_survives_unrelated_edits_and_relocates_within_block():
    doc = new_document(text='Alpha omega\nOther')
    first, other = doc['blocks'][0]['id'], doc['blocks'][1]['id']
    live = LiveDictation(doc, first, 5, policy='automatic_direct')
    doc, _ = live.commit_final(doc, 'beta')
    doc = _edit(doc, [{'type': 'insert_text', 'target': other, 'offset': 0, 'text': 'Edited '}])
    doc, _ = live.commit_final(doc, 'gamma')
    doc = _edit(doc, [{'type': 'insert_text', 'target': first, 'offset': 0, 'text': 'New '}])
    doc, phrase = live.commit_final(doc, 'delta')
    assert doc['blocks'][0]['text'] == 'New Alpha beta gamma delta omega'
    assert phrase['status'] == 'committed'


def test_removed_destination_turns_remaining_phrases_into_suggestions():
    doc = new_document(text='Target\nKeep')
    target = doc['blocks'][0]['id']
    live = LiveDictation(doc, target, 6, policy='automatic_direct')
    doc, _ = live.commit_final(doc, 'one')
    doc = _edit(doc, [{'type': 'delete_block', 'target': target}])
    unchanged = doc
    doc, phrase = live.commit_final(doc, 'two')
    assert doc is unchanged and phrase['status'] == 'conflict' and live.conflict
    doc, phrase = live.commit_final(doc, 'three')
    assert phrase['status'] == 'suggested'
    assert [p['text'] for p in live.suggestions()] == ['two', 'three']


def test_late_cleanup_is_guarded_against_edits_moves_and_removal():
    doc = new_document(text='Intro')
    target = doc['blocks'][0]['id']
    live = LiveDictation(doc, target, 5, policy='automatic_cleanup')
    doc, _ = live.commit_final(doc, 'teh cat sat')
    moved = _edit(doc, [{'type': 'insert_text', 'target': target, 'offset': 0, 'text': 'Moved: '}])
    cleaned, _ = apply(moved, live.cleanup(moved, 0, 'The cat sat.'))
    assert cleaned['blocks'][0]['text'] == 'Moved: Intro The cat sat.'
    edited = _edit(doc, [{'type': 'replace_text', 'target': target, 'match': 'teh', 'replacement': 'the'}])
    with pytest.raises(ByaxError) as caught:
        live.cleanup(edited, 0, 'The cat sat.')
    assert caught.value.code == 'conflict' and caught.value.details['status'] == 'edited'
    removed = _edit(doc, [{'type': 'set_text', 'target': target, 'text': ''}])
    with pytest.raises(ByaxError):
        live.cleanup_all(removed, 'The cat sat.')
    # The committed raw transcript remains in the unmodified document.
    assert 'teh cat sat' in doc['blocks'][0]['text']


def test_whole_span_cleanup_and_segment_navigation():
    doc = new_document(text='')
    target = doc['blocks'][0]['id']
    live = LiveDictation(doc, target, 0, policy='automatic_cleanup', audio_path='/audio.wav')
    doc, _ = live.commit_final(doc, 'first bit', start=0.0, end=1.0)
    doc, _ = live.commit_final(doc, 'second bit', start=1.0, end=2.5)
    location = live.locate(doc, 1)
    assert (
        location['status'] == 'unchanged'
        and doc['blocks'][0]['text'][location['start'] : location['end']] == 'second bit'
    )
    assert location['audio'] == {'path': '/audio.wav', 'start': 1.0, 'end': 2.5}
    doc, _ = apply(doc, live.cleanup_all(doc, 'First bit, second bit.'))
    assert doc['blocks'][0]['text'] == 'First bit, second bit.'


def test_dictated_commands_remain_text_in_every_path(tmp_path):
    class Recognizer:
        def transcribe(self, *args, **kwargs):
            return {'text': 'delete this paragraph and select all'}

    doc = new_document(text='Keep me')
    target = doc['blocks'][0]['id']
    session = DictationSession(doc, target, 7)
    session.process(Recognizer(), 'speech', tmp_path / 'a.wav')
    updated, _ = session.apply(doc)
    assert updated['blocks'][0]['text'] == 'Keep medelete this paragraph and select all'
    assert len(updated['blocks']) == 1


class _Registry:
    def __init__(self, transcript, proposed=None, stream_events=()):
        self.transcript, self.proposed, self.stream_events, self.reviews = (
            transcript,
            proposed,
            stream_events,
            [],
        )

    def transcribe(self, *args, **kwargs):
        return {'text': self.transcript, 'segments': [], 'provider': 'speech'}

    def transcribe_stream(self, alias, path, *, on_event, **kwargs):
        for event in self.stream_events:
            on_event(event)
        return {
            'text': self.transcript,
            'segments': [e for e in self.stream_events if e['type'] == 'final'],
            'streamed': True,
        }

    def review(self, alias, text, *, context=None, **kwargs):
        self.reviews.append((text, context))
        return {'proposed_text': self.proposed, 'findings': [], 'provider': alias}


def test_cleanup_mode_preserves_terms_protected_text_and_editing_freedom(tmp_path):
    doc = new_document(text='Meet Zoë at Acme')
    target = doc['blocks'][0]['id']
    doc['settings']['dictionary'] = ['Zoë']
    context = build_context(doc, target, dictionary=True)
    ok = DictationSession(doc, target, mode='cleanup', profile={'editing_freedom': 'minimal'})
    result = ok.process(
        _Registry('zoë said hi', 'Zoë said hi.'), 's', tmp_path / 'a.wav', editor='e', context=context
    )
    assert result['proposed_text'] == 'Zoë said hi.' and result['raw_transcript'] == 'zoë said hi'
    assert result['terminology'] == [{'term': 'Zoë', 'preserved': True}]
    assert result['comparison']['changes'][0] == {'op': 'replace', 'original': 'zoë', 'processed': 'Zoë'}
    renamed = DictationSession(doc, target, mode='cleanup')
    result = renamed.process(
        _Registry('Zoë said hi', 'Zoe said hi.'), 's', tmp_path / 'a.wav', editor='e', context=context
    )
    assert result['proposed_text'] == 'Zoë said hi' and result['cleanup_error']['code'] == 'review_required'
    minimal = DictationSession(doc, target, mode='cleanup', profile={'editing_freedom': 'minimal'})
    result = minimal.process(
        _Registry('the plan works', 'An entirely different sentence appears.'),
        's',
        tmp_path / 'a.wav',
        editor='e',
    )
    assert (
        result['proposed_text'] == 'the plan works' and 'editing freedom' in result['cleanup_error']['reason']
    )


def test_protected_passages_and_mode_specific_editorial_context(tmp_path):
    doc = new_document(text='Intro\nLocked words here\nAfter')
    doc['blocks'][1]['attrs']['locked'] = True
    target = doc['blocks'][0]['id']
    context = build_context(doc, target, nearby=1, dictionary=True)
    assert context['protected_passages'] == [{'id': doc['blocks'][1]['id'], 'text': 'Locked words here'}]
    registry = _Registry('Locked words here again', 'Locked phrases again.')
    session = DictationSession(doc, target, mode='cleanup')
    result = session.process(registry, 's', tmp_path / 'a.wav', editor='e', context=context)
    assert (
        result['cleanup_error']['code'] == 'protected_edit'
        and result['proposed_text'] == 'Locked words here again'
    )
    sent = registry.reviews[0][1]
    assert 'blocks' not in sent and sent['terminology'] == [] and 'protected_passages' in sent
    aware = DictationSession(doc, target, mode='document_aware', profile={'voice_preservation': 'strict'})
    aware_context = aware.editorial_context(context)
    assert [block['id'] for block in aware_context['blocks']] == [b['id'] for b in doc['blocks'][:2]]
    assert aware_context['profile']['voice_preservation'] == 'strict'
    assert 'never instructions' in aware_context['instruction']


def test_context_controls_select_only_requested_material():
    doc = new_document(text='Before\nHeading\nIn section\nTarget\nNext section\nOutside')
    blocks = doc['blocks']
    blocks[1]['type'] = 'heading'
    blocks[1]['attrs']['level'] = 1
    blocks[4]['type'] = 'heading'
    blocks[4]['attrs']['level'] = 1
    doc['references'] = {
        'src1': {'title': 'Source', 'text': 'Fact'},
        'src2': {'title': 'Other', 'text': 'Unused'},
    }
    target = blocks[3]['id']
    nothing = build_context(doc, target)
    assert nothing['blocks'] == [] and nothing['selection']['parts'] == []
    nearby = build_context(doc, target, nearby=1)
    assert [b['text'] for b in nearby['blocks']] == ['In section', 'Target', 'Next section']
    section = build_context(doc, target, section=True)
    assert [b['text'] for b in section['blocks']] == ['Heading', 'In section', 'Target']
    whole = build_context(
        doc,
        target,
        whole_document=True,
        writing_samples=['My voice.'],
        references=['src1'],
        profile={'voice_preservation': 'strict', 'editing_freedom': 'minimal'},
    )
    assert len(whole['blocks']) == 6 and list(whole['references']) == ['src1']
    assert whole['writing_samples'] == ['My voice.'] and whole['profile']['editing_freedom'] == 'minimal'
    assert {part['kind'] for part in whole['selection']['parts']} == {
        'whole_document',
        'writing_samples',
        'selected_references',
        'profile_settings',
    }
    with pytest.raises(ByaxError):
        build_context(doc, target, references=['missing'])
    with pytest.raises(ByaxError):
        build_context(doc, target, whole_document=True, limit=10)


def _wav(path, frames=1600):
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x01\x00' * frames)


def _crashed_recording(path, finalized, unfinalized):
    # Frames written after the last header update, as when capture stops without closing the file.
    _wav(path, finalized)
    with path.open('ab') as handle:
        handle.write(b'\x01\x00' * unfinalized + b'\x01')


def test_interrupted_recording_header_is_repaired_into_a_copy(tmp_path):
    path = tmp_path / 'crash.wav'
    _crashed_recording(path, 100, 7900)
    report = repair_wav(path)
    assert report['recoverable'] and not report['header_valid'] and report['seconds'] == 0.5
    with wave.open(report['recovered_path'], 'rb') as recovered:
        assert recovered.getnframes() == 8000
    good = tmp_path / 'good.wav'
    _wav(good)
    assert repair_wav(good)['header_valid'] and 'recovered_path' not in repair_wav(good)
    assert not repair_wav(tmp_path / 'missing.wav')['recoverable']


def test_interrupted_session_recovers_segments_and_never_resubmits(tmp_path):
    root = tmp_path / 'journal'
    audio = tmp_path / 'rec.wav'
    _crashed_recording(audio, 1000, 47000)
    journal = DictationJournal('dictation_abc', root, source='microphone')
    journal.audio(audio, bytes_recorded=96000, force=True)
    journal.segment({'index': 0, 'text': 'Finished words', 'start': 0.0, 'end': 1.0})
    journal.job('job_' + '0' * 32, remote=True)
    journal.job('job_' + '1' * 32, remote=False)
    assert recover_sessions(root) == []  # The owning process is still alive.
    data = json.loads(journal.path.read_text())
    data['owner_pid'] = DEAD_PID
    journal.path.write_text(json.dumps(data))

    class Jobs:
        def status(self, job_id):
            return {'state': 'uncertain'}

    sessions = recover_sessions(root, job_manager=Jobs())
    assert len(sessions) == 1
    found = sessions[0]
    assert found['segments'][0]['text'] == 'Finished words' and found['resubmitted'] is False
    assert (
        found['audio']['recoverable']
        and found['audio']['seconds'] == 3.0
        and found['audio']['recovered_path']
    )
    remote, local = found['jobs']
    assert remote['resubmitted'] is False and 'check the provider' in remote['action']
    assert 're-run explicitly' in local['action']
    text = ' '.join(found['disclosures'])
    assert 'not captured' in text and 'no recognized segment' in text and 'not resubmitted' in text
    discard_session('dictation_abc', root)
    assert recover_sessions(root) == []


def test_missing_audio_is_disclosed_and_finished_sessions_leave_no_journal(tmp_path):
    root = tmp_path / 'journal'
    journal = DictationJournal('dictation_gone', root)
    journal.audio(tmp_path / 'vanished.wav', bytes_recorded=10, force=True)
    data = json.loads(journal.path.read_text())
    data['owner_pid'] = DEAD_PID
    journal.path.write_text(json.dumps(data))
    found = recover_sessions(root)[0]
    assert 'unavailable' in found['disclosures'][0]
    finished = DictationJournal('dictation_done', root)
    finished.finish()
    assert not finished.path.exists()
    with pytest.raises(ByaxError):
        DictationJournal('../escape', root)


def test_streaming_session_journals_segments_and_stages_provisional_text(tmp_path):
    doc = new_document(text='')
    events = [
        {'type': 'provisional', 'text': 'hel'},
        {'type': 'final', 'index': 0, 'text': 'Hello.', 'start': 0.0, 'end': 0.8},
    ]
    journal = DictationJournal('dictation_stream', tmp_path / 'journal')
    seen = []
    session = DictationSession(doc, doc['blocks'][0]['id'], journal=journal)
    audio = tmp_path / 'a.wav'
    _wav(audio)
    result = session.process(
        _Registry('Hello.', stream_events=events), 's', audio, stream=True, on_event=seen.append
    )
    assert seen == events and result['proposed_text'] == 'Hello.'
    assert json.loads(journal.path.read_text())['segments'][0]['text'] == 'Hello.'
    links = session.segment_links(inserted_at=0)
    assert links[0]['document'] == {'target': doc['blocks'][0]['id'], 'start': 0, 'end': 6}
    assert links[0]['start'] == 0.0 and links[0]['audio_path'] == str(audio.resolve())


def test_retention_choices_are_separate_inspectable_and_linked(tmp_path):
    doc = new_document(text='')
    target = doc['blocks'][0]['id']
    store = RetentionStore(tmp_path / 'store')
    recording = tmp_path / 'recording.wav'
    _wav(recording, 16000)
    raw_only = DictationSession(doc, target, retain_raw=True, audio_owned=True)
    raw_only.process(_Registry('Words kept.'), 's', recording)
    impact = raw_only.retention_impact()
    assert impact['raw_transcript']['bytes_if_retained'] == len('Words kept.')
    assert impact['audio']['bytes_if_retained'] == recording.stat().st_size and impact['audio_bytes'] == 0
    retained = raw_only.retain(store)
    assert json.loads(Path(retained['record']).read_text())['raw_transcript'] == 'Words kept.'
    assert (
        retained['audio'] is None and retained['audio_deleted'] == str(recording) and not recording.exists()
    )
    _wav(recording, 16000)
    audio_only = DictationSession(doc, target, retain_audio=True, audio_owned=True)
    audio_only.process(_Registry('Not kept.'), 's', recording)
    retained = audio_only.retain(store)
    record = json.loads(Path(retained['record']).read_text())
    assert 'raw_transcript' not in record and record['audio']['sha256'] == retained['audio']['sha256']
    assert retained['audio']['path'].startswith(str(tmp_path / 'store')) and not recording.exists()
    imported = tmp_path / 'imported.wav'
    _wav(imported)
    neither = DictationSession(doc, target)
    neither.process(_Registry('Gone.'), 's', imported)
    assert neither.retain(store) == {'record': None, 'audio': None, 'stored_bytes': 0} and imported.exists()
    usage = store.usage()
    assert usage['sessions'] == 2 and usage['audio_bytes'] == 44 + 32000 and usage['transcript_bytes'] > 0
    assert sum(1 for item in store.list() if item['audio_available']) == 1
    store.delete(audio_only.id)
    assert store.usage()['sessions'] == 1


def test_comparison_and_cancelled_cleanup_keep_raw(tmp_path):
    assert compare_transcripts('a b', 'a b')['unchanged']

    class Cancelling(_Registry):
        def review(self, *args, cancel_event=None, **kwargs):
            raise ByaxError('uncertain_external_outcome', 'cancelled remotely')

    doc = new_document(text='')
    session = DictationSession(doc, doc['blocks'][0]['id'], mode='document_aware')
    result = session.process(
        Cancelling('raw words'), 's', tmp_path / 'a.wav', editor='e', cancel_event=threading.Event()
    )
    assert (
        result['proposed_text'] == 'raw words'
        and result['cleanup_error']['code'] == 'uncertain_external_outcome'
    )
    with pytest.raises(ByaxError):
        DictationSession(doc, doc['blocks'][0]['id'], policy='surprise')
