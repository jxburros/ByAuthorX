# ruff: noqa: BLE001
# GUI event boundaries report errors rather than terminating the application.
"""Explicit provider settings, payload review, and guarded dictation workflows."""

from __future__ import annotations

import copy
import json
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from . import engine, model
from .brand import BrandGraphic
from .errors import ByaxError
from .gui_art import BusyArt, themed_art
from .providers import ProviderConfig, ProviderRegistry
from .transcription import (
    DictationJournal,
    DictationSession,
    LiveDictation,
    QtMicrophoneRecorder,
    RetentionStore,
    apply_direct_options,
    build_context,
    discard_session,
    recover_sessions,
)


class ProviderDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.registry = ProviderRegistry()
        self.setWindowTitle(self.tr('Providers and credentials'))
        self.resize(650, 590)
        form = QFormLayout(self)
        form.addRow(BrandGraphic(themed_art('provider-keys', self), 132), None)
        intro = QLabel(
            self.tr(
                'Choose your own hosted or local providers. Credentials stay in your operating system keyring or environment; documents never contain API keys.'
            )
        )
        intro.setWordWrap(True)
        form.addRow(intro)
        self.saved = QComboBox()
        self.saved.addItem(self.tr('New provider…'), None)
        for config in self.registry.list():
            self.saved.addItem(config['alias'], config['alias'])
        self.saved.currentIndexChanged.connect(lambda index: self.load_config(self.saved.itemData(index)))
        form.addRow(self.tr('Saved providers'), self.saved)
        self.alias, self.model, self.endpoint, self.credential = (
            QLineEdit(),
            QLineEdit(),
            QLineEdit(),
            QLineEdit(),
        )
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText(self.tr('Optional: securely store a new API key'))
        self.kind = QComboBox()
        self.kind.addItems(['openai', 'anthropic', 'gemini', 'ollama', 'elevenlabs', 'whisper.cpp'])
        self.executable = QLineEdit()
        self.executable.setPlaceholderText(self.tr('Absolute path to whisper-cli'))
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 3600)
        self.timeout.setValue(60)
        self.policy = QCheckBox(self.tr('Allow remote calls without repeated authorization'))
        for label, control in [
            (self.tr('Alias'), self.alias),
            (self.tr('Provider'), self.kind),
            (self.tr('Model (or local model path)'), self.model),
            (self.tr('Endpoint (blank = provider default)'), self.endpoint),
            (self.tr('Credential reference (env:NAME)'), self.credential),
            (self.tr('API key'), self.key),
            (self.tr('Local executable'), self.executable),
            (self.tr('Timeout (seconds)'), self.timeout),
        ]:
            control.setAccessibleName(label)
            form.addRow(label, control)
        form.addRow(self.policy)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        save = buttons.addButton(self.tr('Save provider'), QDialogButtonBox.ActionRole)
        test = buttons.addButton(self.tr('Test connection…'), QDialogButtonBox.ActionRole)
        save.clicked.connect(self.save_config)
        test.clicked.connect(self.test_config)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def load_config(self, alias):
        if alias is None:
            return
        config = self.registry.get(alias)
        for control, value in [
            (self.alias, config.alias),
            (self.model, config.model),
            (self.endpoint, config.endpoint),
            (self.credential, config.credential_ref),
            (self.executable, config.executable),
        ]:
            control.setText(value or '')
        self.kind.setCurrentText(config.kind)
        self.timeout.setValue(config.timeout)
        self.policy.setChecked(config.allow_remote)

    def save_config(self):
        try:
            self.registry.save(
                ProviderConfig(
                    alias=self.alias.text().strip(),
                    kind=self.kind.currentText(),
                    model=self.model.text().strip(),
                    endpoint=self.endpoint.text().strip() or None,
                    credential_ref=self.credential.text().strip() or None,
                    executable=self.executable.text().strip() or None,
                    timeout=self.timeout.value(),
                    allow_remote=self.policy.isChecked(),
                )
            )
            if self.key.text():
                self.registry.set_credential(self.alias.text().strip(), self.key.text())
                self.key.clear()
            self.window.status_label.setText(self.tr('Provider configuration saved locally'))
            return True
        except Exception as exc:
            self.window.error(exc)
            return False

    def test_config(self):
        if not self.save_config():
            return
        alias = self.alias.text().strip()
        if (
            QMessageBox.question(
                self,
                self.tr('Test provider connection?'),
                self.tr(
                    'Contact this provider to check its connection and capabilities? No document content will be sent.'
                ),
            )
            == QMessageBox.Yes
        ):
            self.window.background(
                self.tr('Testing provider…'),
                lambda: self.registry.test(alias, authorized=True),
                lambda result: self.window.report(self.tr('Provider capability test'), result),
            )


def select_provider(window, kinds, label):
    registry = ProviderRegistry()
    aliases = [config['alias'] for config in registry.list() if config['kind'] in kinds]
    if not aliases:
        window.message(
            QCoreApplication.translate('Providers', 'Provider setup needed'),
            QCoreApplication.translate(
                'Providers', 'Add a compatible provider in Tools → Provider settings first.'
            ),
        )
        return None, None
    alias, ok = QInputDialog.getItem(
        window, label, QCoreApplication.translate('Providers', 'Provider'), aliases, 0, False
    )
    return (registry, alias) if ok else (None, None)


def authorize_payload(window, title, preview):
    dialog = QDialog(window)
    dialog.setWindowTitle(title)
    dialog.resize(700, 510)
    layout = QVBoxLayout(dialog)
    label = QLabel(
        QCoreApplication.translate(
            'Providers', 'Review the destination and exact selected content before starting this operation.'
        )
    )
    label.setWordWrap(True)
    layout.addWidget(label)
    editor = QPlainTextEdit()
    editor.setReadOnly(True)
    editor.setPlainText(json.dumps(preview, ensure_ascii=False, indent=2))
    layout.addWidget(editor)
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
    start = buttons.addButton(
        QCoreApplication.translate('Providers', 'Authorize and start'), QDialogButtonBox.AcceptRole
    )
    start.clicked.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return bool(dialog.exec())


def review_document(window):
    window.flush_edits()
    from .profiles import filter_findings, validate_profile

    frozen = copy.deepcopy(window.doc)
    profile_id = getattr(window, 'active_profile', None)
    profile = (
        validate_profile(frozen['profiles'][profile_id], strict=False)
        if profile_id in frozen.get('profiles', {})
        else None
    )
    alias = profile['providers']['review'] if profile else None
    if alias:
        registry = ProviderRegistry()
        try:
            registry.get(alias)
        except Exception as exc:
            window.error(exc)
            return
    else:
        registry, alias = select_provider(
            window,
            {'openai', 'anthropic', 'gemini', 'ollama'},
            QCoreApplication.translate('Providers', 'Review with model'),
        )
    if not alias:
        return
    selected = window.editor.textCursor().selectedText().replace('\u2029', '\n')
    text = selected or model.text_of(frozen)
    selected_sources = profile['references'] if profile else []
    blocks = copy.deepcopy(frozen['blocks'])
    if selected:
        blocks = []
        for target, start, end in window.editor.selection_targets():
            if start == end:
                continue
            original = model.find_object(frozen, target)
            boundaries = engine.grapheme_boundaries(original.get('text', ''))
            if start not in boundaries or end not in boundaries:
                window.error(
                    QCoreApplication.translate(
                        'Providers', 'Select complete characters before requesting a model edit.'
                    )
                )
                return
            blocks.append(
                {
                    'id': target,
                    'text': original.get('text', '')[start:end],
                    'source_offset': start,
                    'attrs': copy.deepcopy(original.get('attrs', {})),
                }
            )
    context = {
        'revision': frozen['revision'],
        'scope': 'selection' if selected else 'document',
        'profile': profile or {},
        'profile_version': profile.get('version') if profile else None,
        'references': {
            identity: frozen['references'][identity]
            for identity in selected_sources
            if identity in frozen['references']
        },
        'source_authority': profile['source_authority'] if profile else 'exclusive',
        'blocks': blocks,
        'protections': copy.deepcopy(frozen.get('settings', {}).get('protections', [])),
    }
    try:
        preview = registry.preview(alias, text=text, context=context)
    except Exception as exc:
        window.error(exc)
        return
    if not authorize_payload(
        window, QCoreApplication.translate('Providers', 'Authorize model review'), preview
    ):
        return

    def finished(result):
        if profile:
            result = filter_findings(result, profile)
        window.flush_edits()
        if window.doc['revision'] != frozen['revision']:
            result['stale'] = True
        else:
            proposals = result.get('proposals', [])
            if proposals:
                # The adapter compiled strict range edits; model-supplied arbitrary
                # operations never enter this path. Registration adds review records
                # only; each actual edit requires explicit acceptance in the dock.
                registered = window.commit(
                    [{'type': 'propose', 'values': proposal} for proposal in proposals]
                )
                result['proposals_registered'] = bool(registered)
            window._last_report = result
            window.update_sidebar()
        window.report(
            QCoreApplication.translate('Providers', 'Model review · advisory findings and pending proposals'),
            result,
        )

    window.background(
        QCoreApplication.translate('Providers', 'Reviewing with ') + alias + '…',
        lambda: registry.review(alias, text, context=context, authorized=True),
        finished,
    )


def import_audio(window, path=None):
    window.flush_edits()
    if path is None:
        path, _ = QFileDialog.getOpenFileName(
            window,
            QCoreApplication.translate('Providers', 'Import audio for transcription'),
            '',
            QCoreApplication.translate(
                'Providers', 'Audio (*.wav *.mp3 *.m4a *.ogg *.flac *.webm);;All files (*)'
            ),
        )
    if not path:
        return
    registry, recognizer = select_provider(
        window,
        {'openai', 'elevenlabs', 'whisper.cpp'},
        QCoreApplication.translate('Providers', 'Speech recognition'),
    )
    if not recognizer:
        return
    modes = {
        QCoreApplication.translate('Providers', 'Direct transcript'): 'direct',
        QCoreApplication.translate('Providers', 'Cleaned up'): 'cleanup',
        QCoreApplication.translate('Providers', 'Document-aware'): 'document_aware',
    }
    mode, ok = QInputDialog.getItem(
        window,
        QCoreApplication.translate('Providers', 'Transcription mode'),
        QCoreApplication.translate('Providers', 'Processing'),
        list(modes),
        0,
        False,
    )
    if not ok:
        return
    mode = modes[mode]
    editorial = None
    if mode != 'direct':
        _, editorial = select_provider(
            window,
            {'openai', 'anthropic', 'gemini', 'ollama'},
            QCoreApplication.translate('Providers', 'Editorial processing'),
        )
        if not editorial:
            return
    targets = window.editor.selection_targets()
    if not targets:
        window.error(
            QCoreApplication.translate(
                'Providers', 'Place the cursor in a text paragraph before transcribing.'
            )
        )
        return
    target, offset, _ = targets[0]
    try:
        session = DictationSession(window.doc, target, offset, mode)
        context = (
            {
                'revision': window.doc['revision'],
                'text': model.text_of(window.doc),
                'references': window.doc.get('references', {}),
            }
            if mode == 'document_aware'
            else {}
        )
        preview = registry.preview(recognizer, kind='transcribe', audio_path=path)
        if editorial:
            preview['editorial_processing'] = {
                'provider': editorial,
                'destination': registry.get(editorial).endpoint,
                'content': 'The recognized transcript will be sent for cleanup.',
                'context': context,
            }
        if not authorize_payload(
            window, QCoreApplication.translate('Providers', 'Authorize transcription'), preview
        ):
            return
    except Exception as exc:
        window.error(exc)
        return
    progress = QDialog(window)
    progress.setWindowTitle(QCoreApplication.translate('Providers', 'Transcription job'))
    layout = QVBoxLayout(progress)
    layout.addWidget(
        QLabel(
            QCoreApplication.translate(
                'Providers',
                'Transcribing audio. You can continue editing; changed insertion targets will require review.',
            )
        )
    )
    cancel = QPushButton(QCoreApplication.translate('Providers', 'Cancel job'))
    cancel.clicked.connect(
        lambda: cancel.setText(
            QCoreApplication.translate('Providers', 'Cancellation requested; remote work may continue')
        )
    )
    layout.addWidget(cancel)
    progress.show()

    def finished(result):
        progress.close()
        window.report(
            QCoreApplication.translate('Providers', 'Compare recognized and processed transcript'), result
        )
        if (
            QMessageBox.question(
                window,
                QCoreApplication.translate('Providers', 'Insert transcript?'),
                QCoreApplication.translate(
                    'Providers', 'Insert the reviewed transcript at the original cursor position?'
                ),
            )
            == QMessageBox.Yes
        ):
            try:
                window.flush_edits()
                envelope = session.proposal(window.doc)
                # Canonical request verifies the frozen revision again.
                old = window.doc['revision']
                window.doc, _ = engine.apply(window.doc, envelope)
                window._undo.append(old)
                window._redo.clear()
                window.dirty = True
                window.refresh()
            except Exception as exc:
                window.error(
                    str(exc)
                    + QCoreApplication.translate(
                        'Providers',
                        '\nThe transcript remains available in the job result; it was not inserted.',
                    )
                )

    job = window.start_job(
        'transcription',
        {
            'revision': session.revision,
            'recognizer': recognizer,
            'editor': editorial,
            'audio_path': str(path),
            'mode': mode,
        },
        lambda payload, cancel_event: session.process(
            registry,
            recognizer,
            path,
            editor=editorial,
            context=context,
            authorized=True,
            cancel_event=cancel_event,
        ),
        finished,
    )
    cancel.clicked.connect(lambda: window.job_manager.cancel(job['id']))


class _EventBridge(QObject):
    """Delivers recognizer events from the job thread to the GUI thread."""

    event = Signal(dict)


def _clock(seconds):
    if seconds is None:
        return '--:--'
    minutes, rest = divmod(float(seconds), 60)
    return f'{int(minutes):02d}:{rest:04.1f}'


def _size(count):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if count < 1024 or unit == 'GB':
            return f'{count:.0f} {unit}' if unit == 'bytes' else f'{count:.1f} {unit}'
        count /= 1024


class DictationDialog(QDialog):
    """Microphone or imported audio through one pipeline: explicit context, retention,
    application policy, provisional display, phrase commits and segment navigation."""

    def __init__(self, window, *, registry=None, journal_root=None, store=None):
        super().__init__(window)
        self.window = window
        self.registry = registry or ProviderRegistry()
        self.journal_root = journal_root
        self.store = store or RetentionStore()
        self.setWindowTitle(self.tr('Dictation'))
        self.resize(620, 720)
        self.recorder = None
        self.audio_path = None
        self.journal = None
        self.session = None
        self.live = None
        self.bridge = _EventBridge(self)
        self.bridge.event.connect(self.on_event)
        layout = QVBoxLayout(self)
        # The pen line becomes the voice: the loop plays only while audio is actually being recorded.
        self.voice = BusyArt(
            themed_art('dictation-ready', window), 144, window.motion, 'listening-wave', 130, self
        )
        layout.addWidget(self.voice, alignment=Qt.AlignHCenter)
        self.state = QLabel(
            self.tr(
                'Choose a microphone, then start recording. Audio is recorded locally until you explicitly authorize transcription.'
            )
        )
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        form = QFormLayout()
        layout.addLayout(form)
        self.devices = QComboBox()
        try:
            for device in QtMicrophoneRecorder.devices():
                self.devices.addItem(device['name'], device['id'])
            self.recorder = QtMicrophoneRecorder()
        except Exception as exc:
            self.state.setText(self.tr('Microphone unavailable: ') + str(exc))
        self.destination = QComboBox()
        self.destination.addItem(self.tr('Cursor position'), ('cursor', None))
        self.destination.addItem(self.tr('Start of selected text'), ('selection', None))
        for name in sorted(window.doc.get('settings', {}).get('bookmarks', {})):
            self.destination.addItem(self.tr('Bookmark: ') + name, ('bookmark', name))
        self.mode = QComboBox()
        for label, value in (
            (self.tr('Direct transcript'), 'direct'),
            (self.tr('Spelling and grammar cleanup'), 'cleanup'),
            (self.tr('Document aware'), 'document_aware'),
        ):
            self.mode.addItem(label, value)
        self.policy = QComboBox()
        for label, value in (
            (self.tr('Suggestions only (review before inserting)'), 'suggestions_only'),
            (self.tr('Insert final phrases automatically'), 'automatic_direct'),
            (self.tr('Insert phrases, then apply authorized cleanup'), 'automatic_cleanup'),
        ):
            self.policy.addItem(label, value)
        for label, control in (
            (self.tr('Microphone'), self.devices),
            (self.tr('Insert at'), self.destination),
            (self.tr('Processing'), self.mode),
            (self.tr('Application'), self.policy),
        ):
            control.setAccessibleName(label)
            form.addRow(label, control)
        self.fillers = QCheckBox(self.tr('Remove filler words (um, uh)'))
        self.repetitions = QCheckBox(self.tr('Collapse repeated words'))
        self.spoken = QCheckBox(self.tr('Convert spoken punctuation (“comma”, “period”)'))
        for control in (self.fillers, self.repetitions, self.spoken):
            form.addRow(control)
        context = QGroupBox(self.tr('Context sent for cleanup (nothing else is sent)'))
        context_form = QFormLayout(context)
        self.nearby = QSpinBox()
        self.nearby.setRange(0, 10)
        self.nearby.setAccessibleName(self.tr('Nearby paragraphs'))
        context_form.addRow(self.tr('Nearby paragraphs'), self.nearby)
        self.section = QCheckBox(self.tr('Current section'))
        self.whole = QCheckBox(self.tr('Whole document'))
        self.dictionary = QCheckBox(self.tr('Document and profile dictionary'))
        for control in (self.section, self.whole, self.dictionary):
            context_form.addRow(control)
        self.samples = QPlainTextEdit()
        self.samples.setPlaceholderText(self.tr('Optional writing samples, separated by blank lines'))
        self.samples.setAccessibleName(self.tr('Writing samples'))
        self.samples.setMaximumHeight(70)
        context_form.addRow(self.tr('Writing samples'), self.samples)
        self.references = QListWidget()
        self.references.setAccessibleName(self.tr('Selected references'))
        self.references.setMaximumHeight(80)
        for identity, reference in window.doc.get('references', {}).items():
            item = QListWidgetItem(str(reference.get('title', identity)))
            item.setData(Qt.UserRole, identity)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.references.addItem(item)
        context_form.addRow(self.tr('References'), self.references)
        layout.addWidget(context)
        retention = QGroupBox(self.tr('Retention'))
        retention_form = QFormLayout(retention)
        self.retain_raw = QCheckBox(self.tr('Keep raw transcript and timestamps'))
        self.retain_audio = QCheckBox(self.tr('Keep audio recording'))
        self.impact = QLabel()
        self.impact.setWordWrap(True)
        self.impact.setAccessibleName(self.tr('Storage impact'))
        for control in (self.retain_raw, self.retain_audio, self.impact):
            retention_form.addRow(control)
        self.retain_raw.toggled.connect(self.update_impact)
        self.retain_audio.toggled.connect(self.update_impact)
        layout.addWidget(retention)
        self.provisional = QLabel()
        self.provisional.setWordWrap(True)
        self.provisional.setStyleSheet('color: palette(mid); font-style: italic;')
        self.provisional.setAccessibleName(self.tr('Provisional recognition, not yet in the document'))
        layout.addWidget(self.provisional)
        self.segments = QListWidget()
        self.segments.setAccessibleName(self.tr('Recognized segments; activate to show in document'))
        self.segments.itemActivated.connect(self.navigate_segment)
        layout.addWidget(self.segments)
        controls = QHBoxLayout()
        self.buttons = []
        for name, slot, key in [
            (self.tr('Start'), self.start, 'Alt+S'),
            (self.tr('Pause'), self.pause, 'Alt+P'),
            (self.tr('Resume'), self.resume, 'Alt+R'),
            (self.tr('Stop and transcribe'), self.stop, 'Alt+T'),
        ]:
            button = QPushButton(name)
            button.setShortcut(key)
            button.clicked.connect(slot)
            button.setEnabled(self.recorder is not None)
            controls.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(controls)
        extra = QHBoxLayout()
        for name, slot in [
            (self.tr('Import audio file…'), self.import_file),
            (self.tr('Recover interrupted sessions…'), self.recover),
        ]:
            button = QPushButton(name)
            button.clicked.connect(slot)
            extra.addWidget(button)
        layout.addLayout(extra)
        self.update_impact()
        try:
            pending = recover_sessions(self.journal_root)
            if pending:
                self.state.setText(
                    self.state.text()
                    + self.tr('\n{0} interrupted dictation session(s) can be recovered.').format(len(pending))
                )
        except Exception as exc:
            self.state.setText(
                self.state.text() + self.tr(' Interrupted sessions could not be checked: ') + str(exc)
            )

    def options(self):
        return {
            'fillers': 'remove' if self.fillers.isChecked() else 'keep',
            'repetitions': 'collapse' if self.repetitions.isChecked() else 'keep',
            'punctuation': 'spoken' if self.spoken.isChecked() else 'recognizer',
        }

    def selected_references(self):
        return [
            self.references.item(i).data(Qt.UserRole)
            for i in range(self.references.count())
            if self.references.item(i).checkState() == Qt.Checked
        ]

    def insertion_point(self):
        kind, name = self.destination.currentData()
        if kind == 'bookmark':
            mark = self.window.doc['settings']['bookmarks'][name]
            return mark['target'], mark.get('offset', 0)
        targets = self.window.editor.selection_targets()
        if not targets:
            raise ByaxError('invalid_input', 'Place the cursor in a text paragraph before dictating')
        target, start, end = targets[0]
        return target, start if kind == 'selection' else end

    def profile(self):
        profile_id = getattr(self.window, 'active_profile', None)
        return copy.deepcopy(self.window.doc.get('profiles', {}).get(profile_id, {})) if profile_id else {}

    def build_context(self, target):
        samples = [part.strip() for part in self.samples.toPlainText().split('\n\n') if part.strip()]
        return build_context(
            self.window.doc,
            target,
            nearby=self.nearby.value(),
            section=self.section.isChecked(),
            whole_document=self.whole.isChecked(),
            dictionary=self.dictionary.isChecked(),
            writing_samples=samples,
            references=self.selected_references(),
            profile=self.profile() or None,
        )

    def update_impact(self, *_):
        path = self.audio_path or (self.session.audio_path if self.session else None)
        audio = Path(path).stat().st_size if path and Path(path).is_file() else 0
        raw = len(self.session.raw_transcript.encode('utf-8')) if self.session else 0
        lines = [
            (
                'Raw transcript: kept, about ' + _size(raw)
                if self.session
                else 'Raw transcript: kept (size known after recognition)'
            )
            if self.retain_raw.isChecked()
            else 'Raw transcript: not kept after this session',
            ('Audio: kept, ' + _size(audio) if audio else 'Audio: kept')
            if self.retain_audio.isChecked()
            else 'Audio: recordings are deleted after this session; imported files are never deleted',
        ]
        try:
            usage = self.store.usage()
            lines.append(
                f'Retained so far: {usage["sessions"]} session(s), {_size(usage["total_bytes"])} in {usage["location"]}'
            )
        except Exception as exc:
            lines.append('Retained storage could not be measured: ' + str(exc))
        self.impact.setText('\n'.join(lines))

    def start(self):
        if self.audio_path or self.recorder is None:
            return
        try:
            with tempfile.NamedTemporaryFile(prefix='byax-recording-', suffix='.wav', delete=False) as handle:
                self.audio_path = handle.name
            Path(self.audio_path).unlink()
            self.journal = DictationJournal(
                'dictation_' + uuid.uuid4().hex,
                self.journal_root,
                source='microphone',
                revision=self.window.doc['revision'],
            )
            self.recorder.journal = self.journal
            self.journal.audio(self.audio_path, bytes_recorded=0, force=True)
            self.recorder.start(self.audio_path, self.devices.currentData())
            self.state.setText(self.tr('Recording ● · Alt+P to pause · Alt+T to stop'))
            self.voice.set_busy(True)
        except Exception as exc:
            if self.journal is not None:
                self.journal.finish('failed')
                self.journal = None
            self.audio_path = None
            self.voice.set_busy(False)
            self.window.error(exc)

    def pause(self):
        if self.audio_path:
            self.recorder.pause()
            self.state.setText(self.tr('Recording paused · Alt+R to resume'))
            self.voice.set_busy(False)

    def resume(self):
        if self.audio_path:
            self.recorder.resume()
            self.state.setText(self.tr('Recording ●'))
            self.voice.set_busy(True)

    def stop(self):
        if not self.audio_path:
            return
        result = self.recorder.stop()
        self.voice.set_busy(False)
        path = self.audio_path
        self.audio_path = None
        note = (' · ' + result['error']) if result.get('error') else ''
        self.state.setText(
            self.tr('Recording stopped') + note + self.tr('. Audio is held locally at ') + path
        )
        self.transcribe(path, owned=True)

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr('Import audio for transcription'),
            '',
            self.tr('Audio (*.wav *.mp3 *.m4a *.ogg *.flac *.webm);;All files (*)'),
        )
        if path:
            self.journal = DictationJournal(
                'dictation_' + uuid.uuid4().hex, self.journal_root, source='import'
            )
            self.transcribe(path, owned=False)

    def choose(self, role, kinds, label, workflow):
        try:
            return self.registry.resolve(role, workflow=workflow, profile=self.profile() or None)['alias']
        except ByaxError:
            _, alias = select_provider(self.window, kinds, label)
            return alias

    def transcribe(self, path, *, owned):
        self.window.flush_edits()
        mode, policy = self.mode.currentData(), self.policy.currentData()
        try:
            target, offset = self.insertion_point()
            recognizer = self.choose(
                'recognition', {'openai', 'elevenlabs', 'whisper.cpp'}, 'Speech recognition', 'dictation'
            )
            editor = None
            if recognizer and mode != 'direct':
                editor = self.choose(
                    'cleanup', {'openai', 'anthropic', 'gemini', 'ollama'}, 'Editorial processing', mode
                )
            if not recognizer or (mode != 'direct' and not editor):
                self.abandon(owned)
                return
            context = self.build_context(target)
            self.session = DictationSession(
                self.window.doc,
                target,
                offset,
                mode,
                retain_raw=self.retain_raw.isChecked(),
                retain_audio=self.retain_audio.isChecked(),
                policy=policy,
                direct_options=self.options(),
                profile=self.profile(),
                audio_owned=owned,
                journal=self.journal,
            )
            preview = self.registry.preview(recognizer, kind='transcribe', audio_path=path)
            preview['application_policy'] = policy
            preview['retention'] = self.session.retention_impact()
            if editor:
                editorial = self.registry.preview(
                    editor, text='(recognized transcript)', context=self.session.editorial_context(context)
                )
                preview['editorial_processing'] = {
                    'provider': editor,
                    'endpoint': editorial['endpoint'],
                    'remote': editorial['remote'],
                    'context': editorial['context'],
                    'selection': context['selection'],
                }
            if not authorize_payload(self.window, 'Authorize transcription', preview):
                self.abandon(owned)
                return
        except Exception as exc:
            self.abandon(owned)
            self.window.error(exc)
            return
        self.live = LiveDictation(
            self.window.doc,
            target,
            offset,
            journal=self.journal,
            audio_path=str(Path(path).resolve()),
            policy='suggestions_only' if policy == 'suggestions_only' else 'automatic_direct',
        )
        self.segments.clear()
        self.provisional.clear()
        if self.journal is not None:
            self.journal.update(
                meta={
                    'mode': mode,
                    'policy': policy,
                    'recognizer': recognizer,
                    'editor': editor,
                    'target': target,
                    'offset': offset,
                    'revision': self.window.doc['revision'],
                }
            )
        remote = preview['remote'] or bool(editor and preview['editorial_processing']['remote'])
        session, bridge = self.session, self.bridge

        def worker(payload, cancel_event):
            return session.process(
                self.registry,
                recognizer,
                path,
                editor=editor,
                context=context,
                authorized=True,
                cancel_event=cancel_event,
                stream=True,
                on_event=bridge.event.emit,
            )

        self.state.setText(
            self.tr('Transcribing… provisional words appear in italics and are not part of the document yet.')
        )
        job = self.window.start_job(
            'transcription',
            {
                'revision': session.revision,
                'recognizer': recognizer,
                'editor': editor,
                'audio_path': str(path),
                'mode': mode,
                'remote': remote,
            },
            worker,
            self.finished,
        )
        if self.journal is not None:
            self.journal.job(job['id'], remote=remote)

    def abandon(self, owned):
        # An unsubmitted microphone recording stays journaled so it can be recovered later.
        if self.journal is not None and not owned:
            self.journal.finish('cancelled')
            self.journal = None

    def on_event(self, event):
        if self.live is None:
            return
        if event.get('type') == 'provisional':
            self.live.provisional(event.get('text', ''))
            self.provisional.setText(self.live.provisional_text)
            return
        if event.get('type') != 'final':
            return
        self.provisional.clear()
        text = apply_direct_options(event.get('text', ''), self.options())
        self.window.flush_edits()
        envelope = self.live.final(self.window.doc, text, start=event.get('start'), end=event.get('end'))
        if envelope is not None:
            try:
                self.window._commit(envelope['operations'])
                self.live.committed(self.window.doc)
            except Exception as exc:
                self.live.conflict = {'code': getattr(exc, 'code', 'conflict'), 'message': str(exc)}
                self.live.phrases[-1]['status'] = 'conflict'
        phrase = self.live.phrases[-1]
        status = {
            'committed': '',
            'suggested': ' (suggestion)',
            'conflict': ' (not inserted: destination changed)',
        }[phrase['status']]
        item = QListWidgetItem(
            f'[{_clock(phrase["audio_start"])}–{_clock(phrase["audio_end"])}] {phrase["text"]}{status}'
        )
        item.setData(Qt.UserRole, phrase['index'])
        self.segments.addItem(item)

    def navigate_segment(self, item):
        location = self.live.locate(self.window.doc, item.data(Qt.UserRole))
        audio = location['audio']
        if location['status'] in {'unchanged', 'moved'} and self.window.editor.navigate(location['target']):
            from PySide6.QtGui import QTextCursor

            from .gui_editor import qt_offset

            cursor = self.window.editor.textCursor()
            text, base = cursor.block().text(), cursor.block().position()
            cursor.setPosition(base + qt_offset(text, location['start']))
            cursor.setPosition(base + qt_offset(text, location['end']), QTextCursor.KeepAnchor)
            self.window.editor.setTextCursor(cursor)
        self.state.setText(
            self.tr('Segment {0}: {1} · audio {2}–{3}').format(
                location['index'] + 1, location['status'], _clock(audio['start']), _clock(audio['end'])
            )
            + (self.tr(' in {0}').format(audio['path']) if audio['path'] else '')
        )
        return location

    def finished(self, result):
        session, live = self.session, self.live
        self.provisional.clear()
        if not live.phrases and result.get('proposed_text'):
            # Adapters that report no phrase events still yield one final transcript.
            self.on_event({'type': 'final', 'text': result['raw_transcript']})
        span = live.committed_span()
        processed = result.get('proposed_text', '')
        cleanup_ready = (
            span
            and session.mode != 'direct'
            and not result.get('cleanup_error')
            and processed != span['text']
        )
        if cleanup_ready and session.policy != 'automatic_cleanup':
            self.window.report(self.tr('Compare recognized and processed transcript'), result)
        if cleanup_ready and (
            session.policy == 'automatic_cleanup'
            or QMessageBox.question(
                self,
                self.tr('Apply cleanup?'),
                self.tr(
                    'Replace the inserted dictation with the processed text? Edited text is never overwritten.'
                ),
            )
            == QMessageBox.Yes
        ):
            try:
                self.window.flush_edits()
                envelope = live.cleanup_all(self.window.doc, processed)
                self.window._commit(envelope['operations'])
            except Exception as exc:
                self.window.error(
                    str(exc)
                    + self.tr('\nThe recognized transcript remains in the document and in the job result.')
                )
        elif not span:
            self.window.report(self.tr('Compare recognized and processed transcript'), result)
            if (
                processed
                and QMessageBox.question(
                    self,
                    self.tr('Insert transcript?'),
                    self.tr('Insert the reviewed transcript at the chosen destination?'),
                )
                == QMessageBox.Yes
            ):
                try:
                    self.window.flush_edits()
                    operations = session.proposal(self.window.doc)['operations']
                    self.window._commit(
                        [
                            {('type' if key == 'op' else key): value for key, value in op.items()}
                            for op in operations
                        ]
                    )
                except Exception as exc:
                    self.window.error(
                        str(exc)
                        + self.tr(
                            '\nThe transcript remains available in the job result; it was not inserted.'
                        )
                    )
        try:
            retained = session.retain(self.store)
            self.journal = None
            if retained.get('record'):
                self.state.setText(
                    self.tr('Retained: ')
                    + retained['record']
                    + (self.tr(' · audio ') + retained['audio']['path'] if retained.get('audio') else '')
                )
            else:
                self.state.setText(
                    self.tr('Transcription finished. Nothing was retained beyond the document.')
                )
        except Exception as exc:
            self.window.error(exc)
        self.update_impact()

    def recover(self):
        manager = getattr(self.window, 'job_manager', None)
        sessions = recover_sessions(self.journal_root, job_manager=manager)
        if not sessions:
            self.window.message(
                self.tr('Dictation recovery'), self.tr('No interrupted dictation sessions were found.')
            )
            return sessions
        self.window.report(
            self.tr('Interrupted dictation sessions'),
            {
                'sessions': sessions,
                'note': 'Nothing was resubmitted. Hosted work with an unknown outcome stays uncertain.',
            },
        )
        for found in sessions:
            text = ' '.join(segment['text'] for segment in found.get('segments', []) if segment.get('text'))
            if (
                text
                and QMessageBox.question(
                    self,
                    self.tr('Insert recovered segments?'),
                    self.tr('Insert {0} recovered segment(s) at the cursor?').format(len(found['segments'])),
                )
                == QMessageBox.Yes
            ):
                try:
                    target, offset = self.insertion_point()
                    self.window._commit(
                        [{'type': 'insert_text', 'target': target, 'offset': offset, 'text': text}]
                    )
                except Exception as exc:
                    self.window.error(exc)
                    continue
            if found.get('session_id'):
                discard_session(found['session_id'], self.journal_root)
        return sessions

    def reject(self):
        self.voice.set_busy(False)
        if self.audio_path:
            self.recorder.stop()
            self.window.status_label.setText(self.tr('Unsubmitted recording retained at ') + self.audio_path)
        super().reject()
