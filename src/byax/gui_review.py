"""The Checks tab: every kind of check, each marked active or inactive, and suggestion-only runs.

Local checks run on this computer. AI checks go through a configured provider with the usual explicit
authorization, one request per check. Results land in Proposed changes; nothing is applied automatically.
"""

from __future__ import annotations

import copy
import html
import json
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

AUDIT_LABELS = {
    'quote_found': 'Quote found',
    'near_match': 'Near match',
    'quote_not_found': 'Quote not found',
    'source_changed': 'Source changed since citation',
    'source_unavailable': 'Source unavailable',
    'not_checkable': 'Not checkable (paraphrase without a quote)',
    'unanchored': 'Unanchored',
}


def audit_html(report):
    """The audit as rich text; an unanchored citation's quote is struck through, never hidden."""
    from .grounding import describe_audit

    rows = [f'<p><b>{html.escape(describe_audit(report)[0])}</b></p>']
    for item in report['citations']:
        quote = html.escape(item.get('quote') or item['text'])
        if item['status'] == 'unanchored':
            quote = f'<s>{quote}</s>'
        message = f'<br>{html.escape(item["message"])}' if item.get('message') else ''
        rows.append(
            f'<p><b>{html.escape(AUDIT_LABELS[item["status"]])}</b> · {html.escape(str(item["title"]))}'
            f'<br>“{quote}”{message}</p>'
        )
    rows.append(f'<p><i>{html.escape(report["note"])}</i></p>')
    return ''.join(rows)


class CitationAuditDialog(QDialog):
    """Read-only audit report; proposed re-anchoring is applied only when the author asks."""

    def __init__(self, window, report, revision):
        super().__init__(window)
        self.window = window
        self.report = report
        self.revision = revision
        self.setWindowTitle(self.tr('Citation audit'))
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setAccessibleName(self.tr('Citation audit report'))
        self.browser.setHtml(audit_html(report))
        layout.addWidget(self.browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.apply_button = buttons.addButton(
            self.tr('Apply proposed anchor updates'), QDialogButtonBox.ActionRole
        )
        self.apply_button.setEnabled(bool(report['operations']))
        self.apply_button.setToolTip(
            self.tr('Record re-anchored locators and mark citations that could not be found as unanchored')
        )
        self.apply_button.clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self):
        window = self.window
        window.flush_edits()
        if window.doc['revision'] != self.revision:
            window.status_label.setText(self.tr('The document changed since the audit. Run it again.'))
            return False
        if window.commit(copy.deepcopy(self.report['operations'])):
            self.apply_button.setEnabled(False)
            window.status_label.setText(self.tr('Citation anchors updated.'))
            return True
        return False


MODEL_CHECKS = {
    'repository': 'Check claims against the supplied information repository and source excerpts. '
    'Locate discrepancies and missing evidence; never treat missing evidence as proof of falsehood.',
    'factual': 'Review factual claims, dates, names and quantities against supplied evidence. '
    'Clearly distinguish supported, contradicted and not established claims. Do not browse or invent sources.',
    'voice': 'Check voice consistency against the active voice profile and the supplied document. '
    'Identify specific departures with explanations and optional exact suggested edits. Preserve intentional variation.',
}
# (kind, label, description, active by default)
LOCAL_CHECKS = (
    ('spelling', 'Spelling', 'Local dictionary. Nothing leaves this computer.', True),
    ('grammar', 'Grammar', 'Local grammar and usage rules; the optional LanguageTool tier adds more.', True),
    (
        'health',
        'Document health',
        'Structure, accessibility, requirements and print readiness.',
        True,
    ),
    (
        'citations',
        'Citation audit',
        'Checks every citation’s quotation against its source, and whether the source changed. No AI.',
        False,
    ),
    (
        'bibliography',
        'Bibliography health',
        'Reference list, DOIs, retractions, citation placement. Online lookups run only when you ask.',
        False,
    ),
)
AI_CHECKS = (
    (
        'repository',
        'Information repository',
        'Compares claims with the libraries and sources set up in Configure.',
        False,
    ),
    ('factual', 'Factual accuracy', 'Dates, names and quantities against supplied evidence.', False),
    ('voice', 'Voice consistency', 'Departures from the active voice profile.', False),
)
HEALTH_SUITES = ['structure', 'accessibility', 'requirements', 'print']
# Deterministic fiction analysis (competitiveness plan 6.2): advisory, local, inactive until chosen.
FICTION_CHECKS = (
    (
        'fiction:dialogue',
        'Dialogue tags',
        'Ornate speech tags, adverbs on tags and a tag repeated across nearby lines of dialogue.',
    ),
    (
        'fiction:style',
        'Filter words and telling',
        'Filter words (saw, felt, noticed), adverb density and emotions named outright.',
    ),
    (
        'fiction:repetition',
        'Repeated words and openings',
        'Words and phrases echoed within a short window and runs of sentences with the same first word.',
    ),
    (
        'fiction:rhythm',
        'Sentence and paragraph rhythm',
        'Runs of sentences or paragraphs of nearly equal length, and overlong paragraphs.',
    ),
    ('fiction:sensory', 'Sensory balance', 'Chapters that lean almost entirely on one sense.'),
    (
        'fiction:names',
        'Invented-name spelling',
        'Near-duplicate spellings of invented names, with a proposal to use the common one.',
    ),
)


class ChecksPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.settings = window.settings
        self.toggles = {}
        self.run_buttons = {}
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
        intro = QLabel(
            self.tr('Active checks run together from Proposed changes. Run any one on its own here.')
        )
        intro.setObjectName('emptyDescription')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        for title, checks in (('On this computer', LOCAL_CHECKS), ('With an AI provider', AI_CHECKS)):
            group = QGroupBox(self.tr(title))
            rows = QVBoxLayout(group)
            for kind, label, description, default in checks:
                self._add_row(rows, kind, self.tr(label), self.tr(description), default)
            layout.addWidget(group)
            if title == 'On this computer':
                layout.addWidget(self._grammar_group())
        fiction = QGroupBox(self.tr('Fiction analysis (advisory, off by default)'))
        fiction.setAccessibleName(self.tr('Fiction analysis checks'))
        rows = QVBoxLayout(fiction)
        for kind, label, description in FICTION_CHECKS:
            self._add_row(rows, kind, self.tr(label), self.tr(description), False)
            self.toggles[kind].setToolTip(
                self.tr(
                    'Advisory. Active: included in “Run all active checks”. Nothing is changed automatically.'
                )
            )
        layout.addWidget(fiction)
        self.custom_group = QGroupBox(self.tr('Custom checks'))
        self.custom_rows = QVBoxLayout(self.custom_group)
        self.custom_list = QVBoxLayout()
        self.custom_rows.addLayout(self.custom_list)
        self.custom_name = QLineEdit()
        self.custom_name.setAccessibleName(self.tr('Custom check name'))
        self.custom_name.setPlaceholderText(self.tr('Name, for example “Point of view”'))
        self.custom_rows.addWidget(self.custom_name)
        self.instructions = QPlainTextEdit()
        self.instructions.setAccessibleName(self.tr('Custom check instructions'))
        self.instructions.setPlaceholderText(self.tr('Explain what AI should check…'))
        self.instructions.setMaximumHeight(90)
        self.instructions.textChanged.connect(self.sync)
        self.custom_rows.addWidget(self.instructions)
        self.add_button = QPushButton(self.tr('Add custom check'))
        self.add_button.clicked.connect(self.add_from_editor)
        self.custom_rows.addWidget(self.add_button)
        layout.addWidget(self.custom_group)
        profiles = QPushButton(self.tr('Checking and voice profiles…'))
        profiles.clicked.connect(window.edit_profiles)
        layout.addWidget(profiles)
        layout.addStretch()
        self.custom = self._load_custom()
        self._render_custom()
        self.sync()

    # Grammar tiers -----------------------------------------------------------

    def _grammar_group(self):
        group = QGroupBox(self.tr('Grammar tiers'))
        group.setAccessibleName(self.tr('Grammar tiers'))
        rows = QVBoxLayout(group)
        self.grammar_builtin_label = QLabel()
        self.grammar_builtin_label.setAccessibleName(self.tr('Built-in grammar rules status'))
        self.grammar_lt_label = QLabel()
        self.grammar_lt_label.setAccessibleName(self.tr('LanguageTool status'))
        self.grammar_lt_label.setWordWrap(True)
        rows.addWidget(self.grammar_builtin_label)
        rows.addWidget(self.grammar_lt_label)
        setup = QPushButton(self.tr('Set up grammar tiers…'))
        setup.setAccessibleName(self.tr('Set up grammar tiers'))
        setup.setToolTip(
            self.tr(
                'Install or start the optional LanguageTool tier. Nothing is downloaded unless you confirm.'
            )
        )
        setup.clicked.connect(lambda: self.window.show_languages(tab='grammar'))
        rows.addWidget(setup)
        self.refresh_grammar_status()
        return group

    def refresh_grammar_status(self):
        from .gui_languages import tier_summary

        summary = tier_summary()
        self.grammar_builtin_label.setText(summary['builtin'])
        self.grammar_lt_label.setText(summary['languagetool'])

    # Rows ------------------------------------------------------------------

    def _add_row(self, rows, kind, label, description, default, target=None):
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        toggle = QCheckBox(label)
        toggle.setToolTip(self.tr('Active: included in “Run all active checks”'))
        toggle.setChecked(self._active(kind, default))
        toggle.toggled.connect(lambda on, kind=kind: self.set_active(kind, on))
        line.addWidget(toggle, 1)
        run = QPushButton(self.tr('Run'))
        run.setAccessibleName(self.tr('Run {0}').format(label))
        run.setToolTip(self.tr('Run {0} now').format(label))
        run.clicked.connect(lambda checked=False, kind=kind: self.run_check(kind))
        line.addWidget(run)
        (target or rows).addWidget(row)
        if description:
            note = QLabel(description)
            note.setObjectName('checkDescription')
            note.setWordWrap(True)
            note.setIndent(26)
            (target or rows).addWidget(note)
        self.toggles[kind] = toggle
        self.run_buttons[kind] = run
        return row

    def _active(self, kind, default):
        if kind.startswith('custom:'):
            return next((c['active'] for c in self.custom if 'custom:' + c['id'] == kind), default)
        return self.settings.value(f'checks/{kind}/active', default, type=bool)

    def set_active(self, kind, active):
        if kind.startswith('custom:'):
            for check in self.custom:
                if 'custom:' + check['id'] == kind:
                    check['active'] = bool(active)
            self._save_custom()
        else:
            self.settings.setValue(f'checks/{kind}/active', bool(active))
        self.sync()

    def active_checks(self):
        return [kind for kind, toggle in self.toggles.items() if toggle.isChecked()]

    # Custom checks -----------------------------------------------------------

    def _load_custom(self):
        try:
            stored = json.loads(self.settings.value('checks/custom', '[]', type=str) or '[]')
        except (TypeError, ValueError):
            return []
        return [c for c in stored if isinstance(c, dict) and c.get('id') and c.get('instructions')]

    def _save_custom(self):
        self.settings.setValue('checks/custom', json.dumps(self.custom))

    def _render_custom(self):
        while self.custom_list.count():
            widget = self.custom_list.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for kind in [k for k in self.toggles if k.startswith('custom:')]:
            self.toggles.pop(kind)
            self.run_buttons.pop(kind)
        for check in self.custom:
            kind = 'custom:' + check['id']
            row = self._add_row(None, kind, check['name'], '', check['active'], target=self.custom_list)
            remove = QPushButton(self.tr('Remove'))
            remove.setAccessibleName(self.tr('Remove {0}').format(check['name']))
            remove.clicked.connect(lambda checked=False, ident=check['id']: self.remove_custom(ident))
            row.layout().addWidget(remove)

    def add_custom(self, name, instructions, active=True):
        instructions = instructions.strip()
        if not instructions:
            return None
        check = {
            'id': uuid.uuid4().hex[:12],
            'name': name.strip() or instructions.splitlines()[0][:40],
            'instructions': instructions,
            'active': bool(active),
        }
        self.custom.append(check)
        self._save_custom()
        self._render_custom()
        self.sync()
        return 'custom:' + check['id']

    def add_from_editor(self):
        if self.add_custom(self.custom_name.text(), self.instructions.toPlainText()):
            self.custom_name.clear()
            self.instructions.clear()

    def remove_custom(self, ident):
        self.custom = [c for c in self.custom if c['id'] != ident]
        self._save_custom()
        self._render_custom()
        self.sync()

    # Running -----------------------------------------------------------------

    def sync(self, *_):
        self.add_button.setEnabled(bool(self.instructions.toPlainText().strip()))
        active = self.active_checks()
        label = getattr(self.window, 'active_checks_label', None)
        if label is not None:
            names = [self.toggles[kind].text() for kind in active]
            label.setText(
                self.tr('Active: {0}. Change these in the Checks tab.').format(', '.join(names))
                if names
                else self.tr('No checks are active. Choose some in the Checks tab.')
            )
            self.window.run_all_button.setEnabled(bool(names))

    def run_check(self, kind):
        self.run([kind])

    def run_all(self):
        self.run(self.active_checks())

    def run_citation_audit(self):
        """Stage 1 over every citation mark (plan 6.4): same report as ``byax check --citations``."""
        from .grounding import audit_citations

        window = self.window
        window.flush_edits()
        frozen = copy.deepcopy(window.doc)

        def done(report):
            self.last_audit = report
            self.audit_dialog = CitationAuditDialog(window, report, frozen['revision'])
            self.audit_dialog.show()

        window.background(self.tr('Auditing citations…'), lambda: audit_citations(frozen), done)

    def run(self, kinds):
        """Run local checks in the background, then each AI check with its own authorization."""
        if 'spelling' in kinds:
            self.window.ai_panel.check_spelling()
        if 'citations' in kinds:
            self.run_citation_audit()
        if 'bibliography' in kinds:
            self.window.bibliography_health()
        suites = (['grammar'] if 'grammar' in kinds else []) + (HEALTH_SUITES if 'health' in kinds else [])
        groups = [kind.split(':', 1)[1] for kind in kinds if kind.startswith('fiction:')]
        if groups:
            self.window.run_checks(suites + ['fiction'], fiction_groups=groups)
        elif suites:
            self.window.run_checks(suites)
        remote = [kind for kind in kinds if kind in MODEL_CHECKS or kind.startswith('custom:')]
        if not remote:
            return
        from .gui_ai import text_providers

        if not text_providers():
            self.window.status_label.setText(
                self.tr('AI checks skipped: add a provider in the Configure tab.')
            )
            return
        from .gui_providers import review_document

        custom = {'custom:' + c['id']: c['instructions'] for c in self.custom}
        for kind in remote:
            if kind in MODEL_CHECKS:
                review_document(self.window, check=kind, instructions=MODEL_CHECKS[kind])
            else:
                review_document(self.window, check='custom', instructions=custom[kind])
