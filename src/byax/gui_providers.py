# ruff: noqa: BLE001
# GUI event boundaries report errors rather than terminating the application.
"""Explicit provider settings, payload review, and guarded dictation workflows."""

from __future__ import annotations

import copy
import json
import tempfile

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from . import engine, model
from .providers import ProviderConfig, ProviderRegistry
from .transcription import DictationSession, QtMicrophoneRecorder


class ProviderDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.registry = ProviderRegistry()
        self.setWindowTitle(self.tr('Providers and credentials'))
        self.resize(650, 590)
        form = QFormLayout(self)
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


class DictationDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(self.tr('Microphone dictation'))
        self.resize(510, 240)
        self.recorder = None
        self.audio_path = None
        layout = QVBoxLayout(self)
        self.state = QLabel(
            self.tr(
                'Choose a microphone, then start recording. Audio is recorded locally until you explicitly authorize transcription.'
            )
        )
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.devices = QComboBox()
        self.devices.setAccessibleName(self.tr('Microphone'))
        try:
            for device in QtMicrophoneRecorder.devices():
                self.devices.addItem(device['name'], device['id'])
            self.recorder = QtMicrophoneRecorder()
        except Exception as exc:
            self.state.setText(self.tr('Microphone unavailable: ') + str(exc))
        layout.addWidget(self.devices)
        controls = QHBoxLayout()
        for name, slot, key in [
            ('Start', self.start, 'Alt+S'),
            ('Pause', self.pause, 'Alt+P'),
            ('Resume', self.resume, 'Alt+R'),
            ('Stop and transcribe', self.stop, 'Alt+T'),
        ]:
            button = QPushButton(name)
            button.setShortcut(key)
            button.clicked.connect(slot)
            button.setEnabled(self.recorder is not None)
            controls.addWidget(button)
        layout.addLayout(controls)

    def start(self):
        if self.audio_path:
            return
        try:
            with tempfile.NamedTemporaryFile(prefix='byax-recording-', suffix='.wav', delete=False) as handle:
                self.audio_path = handle.name
            self.recorder.start(self.audio_path, self.devices.currentData())
            self.state.setText(self.tr('Recording ● · Alt+P to pause · Alt+T to stop'))
        except Exception as exc:
            self.audio_path = None
            self.window.error(exc)

    def pause(self):
        if self.audio_path:
            self.recorder.pause()
            self.state.setText(self.tr('Recording paused · Alt+R to resume'))

    def resume(self):
        if self.audio_path:
            self.recorder.resume()
            self.state.setText(self.tr('Recording ●'))

    def stop(self):
        if not self.audio_path:
            return
        self.recorder.stop()
        path = self.audio_path
        self.audio_path = None
        self.state.setText(self.tr('Recording stopped. Audio is retained locally at ') + path)
        import_audio(self.window, path)

    def reject(self):
        if self.audio_path:
            self.recorder.stop()
            self.window.status_label.setText(self.tr('Unsubmitted recording retained at ') + self.audio_path)
        super().reject()
