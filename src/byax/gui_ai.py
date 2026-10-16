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


# Citation chips (context engine plan 6.2): one label and colour per grounding state.
CHIP_STATES = {
    'supported': ('', '#2f6f4f', 'Quote found in the cited passage'),
    'partial': ('~', '#8a6d1f', 'Close to the cited passage, not exact'),
    'unsupported': ('!', '#a33a2b', 'The quoted words are not in the cited passage'),
    'not_checkable': ('', '#4a5a78', 'A paraphrase: the words were not checked for meaning'),
    'unanchored': ('×', '#a33a2b', 'The cited text could not be found in the source'),
    'unverified': ('?', '#a33a2b', 'This ID was not supplied with the request; the citation was removed'),
}


def chip_html(index, record):
    """One chip: a numbered link (``cite:<index>``) whose colour and suffix give the state."""
    suffix, colour, _ = CHIP_STATES.get(record.get('state'), CHIP_STATES['unverified'])
    label = f'[{index + 1}{suffix}]'
    return (
        f'<a href="cite:{index}" style="color:{colour}; text-decoration:none; font-weight:600;">'
        f'{html.escape(label)}</a>'
    )


def chip_tooltip(record, source_text=''):
    """Hover card: source title, locator and state, then the chunk text with the cited span marked."""
    from .gui_libraries import describe_locator

    _, colour, meaning = CHIP_STATES.get(record.get('state'), CHIP_STATES['unverified'])
    status = f'<span style="color:{colour}">{html.escape(meaning)}</span>'
    if not record.get('valid_id', True):
        cited = html.escape(str(record.get('citation_id')))
        return f'<p><b>Unknown source</b><br>{status}</p><p>Cited ID: {cited}</p>'
    title = html.escape(str(record.get('title') or record.get('citation_id') or 'Source'))
    where = html.escape(describe_locator(record.get('locator')))
    head = f'<p><b>{title}</b><br>{where}<br>{status}</p>'
    text = source_text or record.get('quote') or ''
    span = record.get('source_span') if source_text else None
    if len(text) > 900:
        # Long chunks are cropped around the cited span so the card stays readable.
        low = max(0, (span[0] if span else 0) - 300)
        high = min(len(text), max(low + 600, (span[1] if span else 0) + 300))
        span = [span[0] - low, span[1] - low] if span else None
        text = ('…' if low else '') + text[low:high] + ('…' if high < len(text) else '')
        span = [span[0] + 1, span[1] + 1] if span and low else span
    if span and 0 <= span[0] < span[1] <= len(text):
        body = (
            html.escape(text[: span[0]])
            + '<span style="background-color:#ffe58a; color:#000;">'
            + html.escape(text[span[0] : span[1]])
            + '</span>'
            + html.escape(text[span[1] :])
        )
    else:
        body = html.escape(text)
    return head + '<p>' + body.replace('\n', '<br>') + '</p>'


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
        # Citation chips: hover shows the cited passage, click opens the source at its locator.
        self.citations = []
        self.last_chip_tooltip = None
        self.history.highlighted.connect(self._chip_hovered)
        self.history.anchorClicked.connect(self._chip_clicked)
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

    def _append(self, role, text, records=None, texts=None):
        colors = {'user': 'You', 'assistant': 'Assistant', 'note': 'Note'}
        if records:
            escaped = self._with_chips(text, records, texts or {})
        else:
            escaped = html.escape(text).replace('\n', '<br>')
        self.history.append(f'<p><b>{colors[role]}:</b> {escaped}</p>')
        self.history_stack.setCurrentIndex(1)

    # Citation chips ------------------------------------------------------

    def _append_grounded(self, reply, result, context):
        """Show a reply with a chip per citation (native records, or ``[[cite:ID]]`` tokens)."""
        from .grounding import ground_reply, source_texts

        passages = context.get('library_passages') or []
        references = context.get('references') or {}
        grounded = result.get('grounding')
        if not isinstance(grounded, dict) or not isinstance(grounded.get('records'), list):
            grounded = ground_reply(reply, passages=passages, references=references)
        texts = source_texts(passages, references)
        text = grounded.get('text', reply)
        if not grounded['records']:
            self._append('assistant', text.strip() or reply)
            return
        lead = len(text) - len(text.lstrip())
        records = [dict(r, marker=max(0, int(r.get('marker') or 0) - lead)) for r in grounded['records']]
        self._append('assistant', text.strip(), records=records, texts=texts)
        invalid = grounded.get('invalid') or []
        retry = result.get('citation_retry') or {}
        if retry.get('attempted'):
            self._append(
                'note',
                'The assistant cited sources that were not supplied, so it was asked once to correct the reply.',
            )
        if invalid:
            self._append(
                'note',
                f'{len(invalid)} citation(s) named sources that were not supplied with the request. '
                'They were removed and are marked unverified [?].',
            )

    def _with_chips(self, text, records, texts):
        """Reply HTML with a chip at each record's marker; records join ``self.citations``."""
        placed = sorted(
            (min(max(0, int(r.get('marker') or 0)), len(text)), position, r)
            for position, r in enumerate(records)
        )
        out, cursor = [], 0
        for marker, _, record in placed:
            out.append(html.escape(text[cursor:marker]).replace('\n', '<br>'))
            index = len(self.citations)
            self.citations.append(
                {'record': record, 'text': texts.get(record.get('chunk_id') or record.get('citation_id'), '')}
            )
            out.append(chip_html(index, record))
            cursor = marker
        out.append(html.escape(text[cursor:]).replace('\n', '<br>'))
        return ''.join(out).strip()

    def _chip_index(self, url):
        value = url.toString() if hasattr(url, 'toString') else str(url)
        if not value.startswith('cite:'):
            return None
        try:
            index = int(value.split(':', 1)[1])
        except ValueError:
            return None
        return index if 0 <= index < len(self.citations) else None

    def chip_tooltip(self, index):
        entry = self.citations[index]
        return chip_tooltip(entry['record'], entry['text'])

    def _chip_hovered(self, url):
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QToolTip

        index = self._chip_index(url)
        if index is None:
            QToolTip.hideText()
            return
        self.last_chip_tooltip = self.chip_tooltip(index)
        QToolTip.showText(QCursor.pos(), self.last_chip_tooltip, self.history)

    def _chip_clicked(self, url):
        index = self._chip_index(url)
        if index is not None:
            self.open_citation(index)

    def open_citation(self, index):
        """Open the cited source at its locator in the library reader (PDF page, block or offset)."""
        from .gui_libraries import ExtractedTextDialog

        record = self.citations[index]['record']
        if not record.get('valid_id', True):
            self.window.status_label.setText(self.tr('This citation names a source that was not supplied.'))
            return None
        if record.get('library_id') and record.get('document_id'):
            from .grounding import locate_record
            from .libraries import LibraryIndex

            located = locate_record(record)
            if located['status'] in ('available', 'reanchored'):
                try:
                    files = LibraryIndex(located['library']).status()['files']
                except Exception:
                    files = []
                focus = {
                    'document_id': located['document_id'],
                    'chunk': located['chunk'],
                    'span': located['span'],
                    'title': record.get('title'),
                }
                dialog = ExtractedTextDialog(
                    self.window,
                    located['library'],
                    files,
                    focus=focus,
                    on_insert=lambda: self.insert_citation(index),
                )
                if located['status'] == 'reanchored':
                    dialog.summary.setText(
                        self.tr('The source changed since this answer; the quotation was found again. ')
                        + dialog.summary.text()
                    )
                self.last_citation_dialog = dialog
                dialog.exec()
                return dialog
            self.window.status_label.setText(
                self.tr('The cited source cannot be opened: ') + str(located.get('reason', located['status']))
            )
            return None
        # A supplied reference (not a library chunk): show what the request carried.
        box = QMessageBox(self.window)
        box.setWindowTitle(self.tr('Cited source'))
        box.setTextFormat(Qt.RichText)
        box.setText(self.chip_tooltip(index))
        insert = box.addButton(self.tr('Insert as citation'), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        self.last_citation_dialog = box
        box.exec()
        if box.clickedButton() is insert:
            self.insert_citation(index)
        return box

    def citation_operations(self, index, target, start, end):
        """``add_reference`` (when needed) and ``cite`` operations for a chip, filled from its record."""
        from .citations import source_from_hit
        from .grounding import LOCATOR_KEYS
        from .quote_match import infer_kind

        entry = self.citations[index]
        record = entry['record']
        operations = []
        if record.get('chunk_id') and record.get('library_id') and record.get('origin') != 'user':
            source = source_from_hit(
                {
                    'library_id': record['library_id'],
                    'document_id': record['document_id'],
                    'content_hash': record.get('content_hash') or '',
                    'title': record.get('title'),
                    'path': record.get('path') or '',
                    'chunk_id': record['chunk_id'],
                    'text': entry['text'],
                }
            )
            if source['id'] not in self.window.doc.get('references', {}):
                operations.append({'type': 'add_reference', 'id': source['id'], 'values': source})
            source_id = source['id']
        else:
            source_id = record.get('citation_id')
        selected = model.find_object(self.window.doc, target).get('text', '')[start:end]
        quoted = record.get('claimed_quote') and record.get('state') == 'supported'
        kind = (
            'quotation'
            if quoted or infer_kind(selected, [entry['text']])['kind'] == 'quotation'
            else 'paraphrase'
        )
        op = {
            'type': 'cite',
            'target': target,
            'start': start,
            'end': end,
            'source': source_id,
            'kind': kind,
            'locator': {k: v for k, v in (record.get('locator') or {}).items() if k in LOCATOR_KEYS},
        }
        if record.get('quote'):
            op['quote'] = record['quote']
        span = record.get('source_span')
        if span and record.get('quote'):
            op['anchor'] = {
                'quote': record['quote'],
                'prefix': record.get('prefix', '')[-32:],
                'suffix': record.get('suffix', '')[:32],
                'position': {'start': span[0], 'end': span[1]},
                'content_hash': record.get('content_hash') or '',
                'state': 'anchored',
            }
        operations.append(op)
        return operations

    def insert_citation(self, index):
        """ "Insert as citation": cite the editor selection with the chip's source, locator and quote."""
        window = self.window
        if not window.flush_edits():
            return False
        targets = [(t, s, e) for t, s, e in window.editor.selection_targets() if e > s]
        if not targets:
            window.status_label.setText(
                self.tr('Select the text this source supports, then choose Insert as citation.')
            )
            return False
        target, start, end = targets[0]
        try:
            operations = self.citation_operations(index, target, start, end)
        except Exception as exc:
            window.error(exc)
            return False
        if window.commit(operations):
            window.status_label.setText(
                self.tr('Citation inserted. Checks verify it like any other citation.')
            )
            return True
        return False

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
            lambda result: self._answered(token, mode, frozen, result, context),
        )

    def _answered(self, token, mode, frozen, result, context=None):
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
        self._append_grounded(reply, result, context or {})
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
