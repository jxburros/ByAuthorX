# ruff: noqa: BLE001
# GUI event boundaries report errors rather than terminating the application.
"""Assistant panel and the local spelling controller used by Review.

Chat mode only answers. Work mode may suggest exact range edits, which are shown to
the author and, after confirmation, registered as pending Review proposals; nothing
is applied to the document until the author accepts a proposal in Review.
"""

from __future__ import annotations

import copy
import html
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from . import engine, model
from .gui_art import EmptyPanel, TaskLoop, themed_art
from .gui_editor import ProofMark

TEXT_KINDS = {'openai', 'anthropic', 'gemini', 'ollama'}
CONTEXT_LIMIT = 40_000
MODES = ('chat', 'work')


def text_providers():
    """Configured conversation-capable providers; unreadable config reads as none."""
    from .providers import ProviderRegistry

    try:
        return [
            config for config in ProviderRegistry().list_with_locality() if config.get('kind') in TEXT_KINDS
        ]
    except Exception:
        return []


def describe_edit(proposal):
    return f'“{proposal["expected_text"]}” → “{proposal["replacement"]}”  ({proposal.get("explanation", "")})'


class AIPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.ignored = set()
        self.messages = []
        self._spelling_token = None
        self._pending = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(10)

        self.provider_status = QLabel()
        self.provider_status.setObjectName('emptyDescription')
        self.provider_status.setWordWrap(True)
        self.keys_button = QPushButton(self.tr('Add or manage API keys…'))
        self.keys_button.setAccessibleName(self.tr('Add or manage API keys'))
        self.keys_button.setToolTip(self.tr('Open provider settings to add a hosted or local model'))
        self.keys_button.clicked.connect(self.manage_keys)
        # Provider status and keys are shown in the Configure tab.

        self.spelling_widget = self._build_spelling()
        # Proposed changes shows spelling results once a spelling check runs; checks start it.
        self.spelling_widget.hide()
        self.spell_button.hide()
        layout.addWidget(self._build_assistant(), 1)
        self.refresh_providers()

    # Spelling ------------------------------------------------------------

    def _build_spelling(self):
        box = QGroupBox(self.tr('Spelling'))
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.spell_button = QPushButton(self.tr('Check spelling'))
        self.spell_button.setToolTip(
            self.tr('Check the selection, or the whole document when nothing is selected')
        )
        self.spell_button.clicked.connect(self.check_spelling)
        row.addWidget(self.spell_button, 1)
        self.spell_loop = TaskLoop(self.window.motion, 'spelling-review', 40, self)
        row.addWidget(self.spell_loop)
        layout.addLayout(row)
        self.spell_status = QLabel(self.tr('Checks use the local dictionary. Nothing leaves this computer.'))
        self.spell_status.setWordWrap(True)
        self.spell_status.setObjectName('emptyDescription')
        layout.addWidget(self.spell_status)
        self.spell_list = QListWidget()
        self.spell_list.setAccessibleName(self.tr('Possible misspellings'))
        self.spell_list.setMinimumHeight(90)
        self.spell_list.setWordWrap(True)
        self.spell_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.spell_list.currentItemChanged.connect(self._spelling_selected)
        # Marks in the text follow this list; coalesce the burst of row changes into one update.
        self._marks_pending = False
        rows = self.spell_list.model()
        for signal in (rows.rowsInserted, rows.rowsRemoved, rows.modelReset):
            signal.connect(self._marks_changed)
        self.spell_list.itemClicked.connect(self._navigate)
        self.spell_list.itemActivated.connect(self._navigate)
        self.spell_stack = QStackedWidget()
        self.spell_stack.addWidget(self.spell_list)
        self.spell_stack.addWidget(
            EmptyPanel(
                self.tr('No misspellings found'),
                self.tr('Every checked word is in the local or document dictionary.'),
                graphic=themed_art('spelling-clean', self),
            )
        )
        self.spell_stack.setVisible(False)
        layout.addWidget(self.spell_stack)
        self.suggestions = QComboBox()
        self.suggestions.setAccessibleName(self.tr('Spelling suggestion'))
        layout.addWidget(self.suggestions)
        self.replace_button = QPushButton(self.tr('Replace with suggestion'))
        self.replace_button.clicked.connect(self.replace_selected)
        self.dictionary_button = QPushButton(self.tr('Add to dictionary'))
        self.dictionary_button.setToolTip(self.tr('Store this word in the document dictionary'))
        self.dictionary_button.clicked.connect(self.add_to_dictionary)
        self.ignore_button = QPushButton(self.tr('Ignore'))
        self.ignore_button.setToolTip(self.tr('Ignore this word until the document is closed'))
        self.ignore_button.clicked.connect(self.ignore_selected)
        layout.addWidget(self.replace_button)
        actions = QHBoxLayout()
        actions.addWidget(self.dictionary_button)
        actions.addWidget(self.ignore_button)
        layout.addLayout(actions)
        self._spelling_selected()
        return box

    def check_spelling(self):
        from .checks import run_checks, spelling_suggestions

        window = self.window
        if not window.flush_edits():
            return
        self.spelling_widget.show()
        frozen = copy.deepcopy(window.doc)
        scope = [
            (target, start, end) for target, start, end in window.editor.selection_targets() if start != end
        ]
        ignored = {word.lower() for word in self.ignored}
        token = object()
        self._spelling_token = token
        self.spell_loop.start()
        self.spell_button.setEnabled(False)

        def work():
            try:
                report = run_checks(frozen, 'spelling', limit=1000)
                findings = []
                for finding in report['findings']:
                    target = (finding.get('affected_ids') or [None])[0]
                    span = finding.get('range') or {}
                    word = finding.get('measured', {}).get('word')
                    if not target or not word or word.lower() in ignored:
                        continue
                    if scope and not any(
                        t == target and s <= span['start'] and span['end'] <= e for t, s, e in scope
                    ):
                        continue
                    findings.append(
                        {'target': target, 'start': span['start'], 'end': span['end'], 'word': word}
                    )
                suggestions = spelling_suggestions(frozen, sorted({f['word'] for f in findings}))
                for finding in findings:
                    finding['suggestions'] = suggestions.get(finding['word'], [])
                coverage = [c for c in report.get('coverage', []) if c.get('suite') == 'spelling']
                return {'findings': findings, 'coverage': coverage, 'revision': frozen['revision']}
            except Exception as exc:
                return {'error': str(exc)}

        window.background(
            self.tr('Checking spelling…'),
            work,
            lambda result: self._spelling_done(token, result, bool(scope)),
        )

    def _spelling_done(self, token, result, selection):
        if token is not self._spelling_token:
            return
        self.spell_loop.stop()
        self.spell_button.setEnabled(True)
        if 'error' in result:
            self.spell_status.setText(self.tr('Spelling check failed: ') + result['error'])
            return
        self.spell_list.clear()
        current = result['revision'] == self.window.doc['revision']
        for finding in result['findings']:
            # Marks need the text the finding was measured on; skip them if the document moved on.
            block = model.find_object(self.window.doc, finding['target']) if current else None
            finding['text'] = (block or {}).get('text')
            hint = ', '.join(finding['suggestions'][:3]) or 'no suggestions'
            item = QListWidgetItem(f'{finding["word"]}  →  {hint}')
            item.setData(Qt.UserRole, finding)
            self.spell_list.addItem(item)
        count = self.spell_list.count()
        unmeasured = [c['reason'] for c in result['coverage'] if c.get('status') == 'unmeasurable']
        where = 'selection' if selection else 'document'
        self.spell_status.setText(
            ' '.join(unmeasured)
            or (
                self.tr('{0} possible misspelling{1} in the {2}.').format(count, 's' * (count != 1), where)
                if count
                else ''
            )
            or self.tr('No misspellings in the {0}.').format(where)
        )
        self.spell_stack.setVisible(True)
        self._show_spelling_page(0 if count else 1)
        if count:
            self.spell_list.setCurrentRow(0)
        self._spelling_selected()
        self.window.motion.fade(self.spell_stack)

    def _marks_changed(self, *_):
        if not isValid(self.spell_list):
            return
        if not self._marks_pending:
            self._marks_pending = True
            QTimer.singleShot(0, self.spell_list, self._flush_marks)

    def _flush_marks(self):
        self._marks_pending = False
        if isValid(self.spell_list) and isValid(self.window.editor):
            self.window.sync_proof_marks()

    def spelling_marks(self):
        """Red-pen marks for the listed misspellings, measured on the text the check actually saw."""
        marks = []
        for index in range(self.spell_list.count()):
            finding = self.spell_list.item(index).data(Qt.UserRole)
            if not finding or not finding.get('text'):
                continue
            hint = ', '.join(finding['suggestions'][:3])
            label = self.tr('Possible misspelling: {0}').format(finding['word'])
            marks.append(
                ProofMark(
                    finding['target'],
                    finding['start'],
                    finding['end'],
                    'spelling',
                    f'{label} → {hint}' if hint else label,
                    finding['text'],
                )
            )
        return marks

    def _current_finding(self):
        item = self.spell_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _spelling_selected(self, *_):
        finding = self._current_finding()
        self.suggestions.clear()
        if finding:
            self.suggestions.addItems(finding['suggestions'])
        self.suggestions.setEnabled(bool(finding and finding['suggestions']))
        self.replace_button.setEnabled(bool(finding and finding['suggestions']))
        self.dictionary_button.setEnabled(bool(finding))
        self.ignore_button.setEnabled(bool(finding))

    def _navigate(self, item):
        finding = item.data(Qt.UserRole)
        if finding:
            self.window.editor.navigate(finding['target'])

    def replacement_operation(self, finding, replacement):
        """Exact canonical edit for one finding, or None when the text changed since the check."""
        try:
            text = model.find_object(self.window.doc, finding['target']).get('text', '')
        except Exception:
            return None
        word = finding['word']
        locations = [match.start() for match in re.finditer(re.escape(word), text)]
        if finding['start'] not in locations:
            return None
        return {
            'type': 'replace_text',
            'target': finding['target'],
            'match': word,
            'replacement': replacement,
            'occurrence': locations.index(finding['start']) + 1,
            'expected_text': word,
        }

    def replace_selected(self):
        finding = self._current_finding()
        replacement = self.suggestions.currentText()
        if not finding or not replacement:
            return
        if not self.window.flush_edits():
            return
        operation = self.replacement_operation(finding, replacement)
        if operation is None:
            self.spell_status.setText(self.tr('The text changed since the check. Checking again…'))
            self.check_spelling()
            return
        if self.window.commit([operation]):
            self._drop_current(lambda other: other['target'] != finding['target'])
            self.check_spelling()

    def add_to_dictionary(self):
        finding = self._current_finding()
        if not finding:
            return
        words = list(self.window.doc.get('settings', {}).get('dictionary', []))
        if finding['word'] not in words:
            words.append(finding['word'])
            if not self.window.commit([{'type': 'set_settings', 'values': {'dictionary': sorted(words)}}]):
                return
        self._drop_current(lambda other: other['word'].lower() != finding['word'].lower())

    def ignore_selected(self):
        finding = self._current_finding()
        if finding:
            self.ignored.add(finding['word'])
            self._drop_current(lambda other: other['word'].lower() != finding['word'].lower())

    def _drop_current(self, keep):
        row = self.spell_list.currentRow()
        for index in reversed(range(self.spell_list.count())):
            if not keep(self.spell_list.item(index).data(Qt.UserRole)):
                self.spell_list.takeItem(index)
        if self.spell_list.count():
            self.spell_list.setCurrentRow(min(row, self.spell_list.count() - 1))
        else:
            self._show_spelling_page(1)
        self._spelling_selected()

    def _show_spelling_page(self, index):
        self.spell_stack.setCurrentIndex(index)
        # The list stays compact; the clean state takes the room its illustration needs.
        self.spell_stack.setMaximumHeight(150 if index == 0 else 16777215)

    # Providers -----------------------------------------------------------

    def manage_keys(self):
        self.window.provider_settings()
        self.refresh_providers()

    def refresh_providers(self):
        configs = text_providers()
        current = self.provider_box.currentData() if hasattr(self, 'provider_box') else None
        current = current or self.window.settings.value('ai/provider', '', type=str)
        self.provider_box.blockSignals(True)
        self.provider_box.clear()
        for config in configs:
            label = f'{config["alias"]} · {config["model"]}'
            if config.get('locality'):
                label += ' · ' + config['locality'].capitalize()
            self.provider_box.addItem(label, config['alias'])
        index = self.provider_box.findData(current)
        self.provider_box.setCurrentIndex(max(0, index))
        self.provider_box.blockSignals(False)
        self._provider_changed()

    def _provider_changed(self, *_):
        configs = {config['alias']: config for config in text_providers()}
        alias = self.provider_box.currentData()
        if alias in configs:
            config = configs[alias]
            self.window.settings.setValue('ai/provider', alias)
            self.provider_status.setText(
                self.tr('AI provider: {0} · {1} · {2}').format(alias, config['kind'], config['model'])
            )
        else:
            self.provider_status.setText(
                self.tr('No AI provider configured. Writing and spelling work without one.')
            )
        self.chat_stack.setCurrentIndex(0 if configs else 1)
        self.provider_box.setVisible(bool(configs))
        self._sync_send()

    # Assistant -----------------------------------------------------------

    def _build_assistant(self):
        box = QGroupBox(self.tr('Assistant'))
        layout = QVBoxLayout(box)
        controls = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_switch = QWidget()
        self.mode_switch.setAccessibleName(self.tr('Assistant mode'))
        switch = QHBoxLayout(self.mode_switch)
        switch.setContentsMargins(0, 0, 0, 0)
        switch.setSpacing(0)
        self.mode_buttons = {}
        for mode, label, tip in (
            ('work', self.tr('Work'), 'Work: the assistant may suggest exact edits for you to review'),
            ('chat', self.tr('Chat'), 'Chat: the assistant only answers and never changes the document'),
        ):
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setAccessibleName(label + self.tr(' mode'))
            button.setAccessibleDescription(tip)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            switch.addWidget(button)
        saved = self.window.settings.value('ai/mode', 'chat', type=str)
        self.mode_buttons[saved if saved in MODES else 'chat'].setChecked(True)
        self.mode_group.buttonToggled.connect(self._mode_changed)
        controls.addWidget(self.mode_switch)
        controls.addStretch()
        layout.addLayout(controls)
        self.provider_box = QComboBox()
        self.provider_box.setAccessibleName(self.tr('Assistant provider and model'))
        self.provider_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.provider_box.setMinimumContentsLength(12)
        self.provider_box.currentIndexChanged.connect(self._provider_changed)
        layout.addWidget(self.provider_box)
        self.mode_hint = QLabel()
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setObjectName('emptyDescription')
        layout.addWidget(self.mode_hint)

        self.chat_stack = QStackedWidget()
        conversation = QWidget()
        cv = QVBoxLayout(conversation)
        cv.setContentsMargins(0, 0, 0, 0)
        self.history_stack = QStackedWidget()
        self.history = QTextBrowser()
        self.history.setAccessibleName(self.tr('Assistant conversation'))
        self.history.setOpenLinks(False)
        self.history.setMinimumHeight(180)
        self.history_stack.addWidget(
            EmptyPanel(
                self.tr('Ask about your writing'),
                self.tr(
                    'Chat answers questions. Switch to Work to get exact edits you review before anything changes.'
                ),
                graphic=themed_art('ai-empty', self),
            )
        )
        self.history_stack.addWidget(self.history)
        cv.addWidget(self.history_stack, 1)
        thinking = QHBoxLayout()
        self.thinking = TaskLoop(self.window.motion, 'ai-thinking', 22, self)
        thinking.addWidget(self.thinking)
        self.thinking_label = QLabel('')
        self.thinking_label.setObjectName('emptyDescription')
        thinking.addWidget(self.thinking_label, 1)
        cv.addLayout(thinking)
        self.input = QPlainTextEdit()
        self.input.setAccessibleName(self.tr('Message to the assistant'))
        self.input.setPlaceholderText(self.tr('Ask about the document or selection…'))
        self.input.setFixedHeight(72)
        self.input.textChanged.connect(self._sync_send)
        cv.addWidget(self.input)
        self.use_libraries = QCheckBox(self.tr('Use attached libraries'))
        self.use_libraries.setToolTip(
            self.tr('Search attached libraries and choose passages before sending.')
        )
        cv.addWidget(self.use_libraries)
        self.send_button = QPushButton(self.tr('Send'))
        self.send_button.setProperty('primary', True)
        self.send_button.setShortcut('Ctrl+Return')
        self.send_button.clicked.connect(self.send)
        cv.addWidget(self.send_button)
        self.chat_stack.addWidget(conversation)
        self.chat_stack.addWidget(
            EmptyPanel(
                self.tr('Connect an AI provider to chat'),
                self.tr(
                    'Add your own API key or a local model. Writing, saving, and spelling never need one.'
                ),
                graphic=themed_art('ai-setup', self),
                action=(self.tr('Add or manage API keys…'), self.manage_keys),
            )
        )
        layout.addWidget(self.chat_stack, 1)
        self._mode_changed()
        return box

    @property
    def mode(self):
        return 'work' if self.mode_buttons['work'].isChecked() else 'chat'

    def _mode_changed(self, *_):
        self.window.settings.setValue('ai/mode', self.mode)
        self.mode_hint.setText(
            self.tr('Chat mode: answers only. The document is never changed.')
            if self.mode == 'chat'
            else self.tr(
                'Work mode: suggested edits are shown exactly and added to Review only after you confirm.'
            )
        )

    def _sync_send(self, *_):
        ready = bool(self.provider_box.currentData()) and self._pending is None
        self.send_button.setEnabled(ready and bool(self.input.toPlainText().strip()))

    def _append(self, role, text):
        colors = {'user': 'You', 'assistant': 'Assistant', 'note': 'Note'}
        escaped = html.escape(text).replace('\n', '<br>')
        self.history.append(f'<p><b>{colors[role]}:</b> {escaped}</p>')
        self.history_stack.setCurrentIndex(1)

    def context(self, frozen):
        selected = [(t, s, e) for t, s, e in self.window.editor.selection_targets() if s != e]
        target = selected[0][0] if selected else None
        context = engine.context(
            frozen, target=target, limit=CONTEXT_LIMIT, profile=getattr(self.window, 'active_profile', None)
        )
        context['scope'] = 'selection' if selected else 'document'
        if selected:
            context['selection'] = [
                {'target': t, 'start': s, 'end': e, 'text': model.find_object(frozen, t).get('text', '')[s:e]}
                for t, s, e in selected
            ]
        # Codex entries and traits hidden from AI never reach a model (context engine 8.2).
        from .codex import redact_for_ai

        return redact_for_ai(frozen, context)

    def send(self):
        from .gui_providers import authorize_payload
        from .providers import ProviderRegistry

        alias = self.provider_box.currentData()
        prompt = self.input.toPlainText().strip()
        if not alias or not prompt or self._pending is not None:
            return
        window = self.window
        if not window.flush_edits():
            return
        mode = self.mode
        frozen = copy.deepcopy(window.doc)
        messages = [*self.messages, {'role': 'user', 'content': prompt}]
        try:
            registry = ProviderRegistry()
            config = registry.get(alias)
            context = self.context(frozen)
            library_authorized = False
            if self.use_libraries.isChecked():
                from .context_assembly import library_budget
                from .gui_retrieval import select_passages
                from .retrieval import augment_context, retrieve_context

                remote = registry.preview(alias, kind='chat')['remote']
                from .series import with_series

                retrieval = retrieve_context(
                    with_series(frozen, getattr(window, 'path', None)),
                    prompt,
                    method='auto',
                    remote=remote,
                    profile=getattr(window, 'active_profile', None),
                    target=context.get('target'),
                    max_tokens=library_budget(config).tokens,
                    provider=config,
                )
                if retrieval['included']:
                    retrieval = select_passages(window, retrieval, alias, remote)
                    if retrieval is None:
                        return
                    library_authorized = True
                context = augment_context(context, retrieval)
                count = len(retrieval['included'])
                methods = ', '.join(
                    f'{lib["name"]}: {lib["method"]}'
                    + (
                        f' ({lib["embedding_state"]})'
                        if lib['embedding_state'] not in ('disabled', 'ready')
                        else ''
                    )
                    for lib in retrieval.get('libraries', [])
                )
                self._append(
                    'note',
                    f'Library retrieval: {count} selected passage(s)'
                    + (f' · {methods}.' if methods else '.'),
                )
                for library in retrieval.get('libraries', []):
                    if library.get('notice'):
                        self._append('note', f'{library["name"]}: {library["notice"]}')
                for omission in retrieval['omitted']:
                    self._append('note', 'Library passage omitted: ' + omission['reason'])
            preview = registry.preview(
                alias,
                kind='chat',
                text=prompt,
                context={'mode': mode, 'messages': messages, 'document': context},
            )
        except Exception as exc:
            window.error(exc)
            return
        # Same consent rule as model review: remote payloads are shown exactly and
        # authorized by the author unless a saved provider policy already allows it.
        authorized = False
        if preview['remote'] and not config.allow_remote:
            if not authorize_payload(window, f'Authorize assistant message ({mode} mode)', preview):
                return
            authorized = True
        self.input.clear()
        self.messages = messages
        self._append('user', prompt)
        token = object()
        self._pending = token
        self._sync_send()
        self.thinking_label.setText(self.tr('{0} is thinking…').format(alias))
        self.thinking.start()

        def work():
            try:
                return registry.chat(
                    alias,
                    messages,
                    mode=mode,
                    context=context,
                    authorized=authorized,
                    **({'library_authorized': True} if library_authorized else {}),
                )
            except Exception as exc:
                return {'error': str(exc)}

        window.background(
            self.tr('Asking {0}…').format(alias),
            work,
            lambda result: self._answered(token, mode, frozen, result),
        )

    def _answered(self, token, mode, frozen, result):
        if token is not self._pending:
            return
        self._pending = None
        self.thinking.stop()
        self.thinking_label.setText('')
        self._sync_send()
        if 'error' in result:
            self.messages.pop()
            self._append('note', 'The assistant could not answer: ' + result['error'])
            return
        reply = result.get('reply') or '(No reply text.)'
        self.messages.append({'role': 'assistant', 'content': reply})
        self._append('assistant', reply)
        self.verify_absence(reply, frozen)
        if mode != 'work':
            # Chat mode never edits, whatever the model returned.
            return
        for key in ('note', 'rejected_edits'):
            if result.get(key):
                self._append('note', result[key])
        proposals = result.get('proposals') or []
        if proposals:
            self.offer_proposals(proposals, frozen)

    def verify_absence(self, reply, frozen):
        """Q14: before a "not in your sources" answer stands, search every eligible source literally."""
        from .retrieval import absence_claim, describe_absence, verify_absence
        from .search_query import key_terms

        if not absence_claim(reply):
            return
        prompt = next((m['content'] for m in reversed(self.messages) if m['role'] == 'user'), '')
        quoted = re.findall(r'["“]([^"”]{2,200})["”]', reply)
        terms = list(dict.fromkeys([q.strip() for q in quoted] + key_terms(prompt)))[:8]
        if not terms:
            return
        profile = getattr(self.window, 'active_profile', None)

        def work():
            return verify_absence(terms, manuscript=frozen, profile=profile)

        def done(report):
            if not isValid(self):
                return
            self.last_absence_report = report
            if report['found']:
                self._append(
                    'note',
                    'The reply says something is missing, but literal matches exist. Check these before '
                    'relying on the answer:\n' + describe_absence(report),
                )
            else:
                self._append('note', describe_absence(report))

        self.window.background(self.tr('Checking sources for literal matches…'), work, done)

    def offer_proposals(self, proposals, frozen):
        window = self.window
        listing = '\n'.join(describe_edit(proposal) for proposal in proposals)
        self._append('note', f'Suggested edits ({len(proposals)}):\n{listing}')
        window.flush_edits()
        if window.doc['revision'] != frozen['revision']:
            self._append(
                'note', 'The document changed while the assistant worked; these edits were not added.'
            )
            return False
        box = QMessageBox(window)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(self.tr('Add suggested edits to Review?'))
        box.setText(
            self.tr(
                'The assistant suggested {0} exact edit(s). Add them to Review as pending proposals? Nothing changes until you accept each one in Review.'
            ).format(len(proposals))
        )
        box.setDetailedText(listing)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            self._append('note', 'Suggested edits were discarded.')
            return False
        if window.commit([{'type': 'propose', 'values': proposal} for proposal in proposals]):
            self._append('note', 'Added to Review. Accept or reject each proposal there.')
            return True
        return False
