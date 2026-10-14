"""Explicit, suggestion-only review tasks in one keyboard-accessible control."""

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

MODEL_CHECKS = {
    'repository': 'Check claims against the supplied information repository and source excerpts. '
    'Locate discrepancies and missing evidence; never treat missing evidence as proof of falsehood.',
    'factual': 'Review factual claims, dates, names and quantities against supplied evidence. '
    'Clearly distinguish supported, contradicted and not established claims. Do not browse or invent sources.',
    'voice': 'Check voice consistency against the active voice profile and the supplied document. '
    'Identify specific departures with explanations and optional exact suggested edits. Preserve intentional variation.',
}


class ReviewChecks(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.options = QComboBox()
        self.options.setAccessibleName(self.tr('Check to run'))
        for label, kind in (
            ('Spelling', 'spelling'),
            ('Grammar', 'grammar'),
            ('Document health', 'health'),
            ('Information repository', 'repository'),
            ('Factual data', 'factual'),
            ('Voice consistency', 'voice'),
            ('Custom AI check', 'custom'),
        ):
            self.options.addItem(self.tr(label), kind)
        row.addWidget(self.options, 1)
        self.go = QPushButton(self.tr('Go'))
        self.go.setAccessibleName(self.tr('Run selected check'))
        self.go.clicked.connect(self.run)
        row.addWidget(self.go)
        layout.addLayout(row)
        self.instructions = QPlainTextEdit()
        self.instructions.setAccessibleName(self.tr('Custom check instructions'))
        self.instructions.setPlaceholderText(self.tr('Explain what AI should check…'))
        self.instructions.setMaximumHeight(90)
        self.instructions.textChanged.connect(self.sync)
        layout.addWidget(self.instructions)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.spelling = window.ai_panel.spelling_widget
        # The existing controller retains its stale-range and explicit-apply guards.
        window.ai_panel.spell_button.hide()
        layout.addWidget(self.spelling)
        self.options.currentIndexChanged.connect(self.sync)
        self.sync()

    def sync(self, *_):
        kind = self.options.currentData()
        self.instructions.setVisible(kind == 'custom')
        self.spelling.setVisible(kind == 'spelling')
        self.go.setEnabled(kind != 'custom' or bool(self.instructions.toPlainText().strip()))
        self.hint.setText(
            self.tr('Local check. Suggestions only; apply each change yourself.')
            if kind in ('spelling', 'grammar', 'health')
            else self.tr(
                'Uses a configured AI reviewer and supplied evidence. Suggestions appear below for review; '
                'nothing is changed automatically.'
            )
        )

    def run(self):
        kind = self.options.currentData()
        if kind == 'spelling':
            self.window.ai_panel.check_spelling()
        elif kind in ('grammar', 'health'):
            self.window.run_checks('grammar' if kind == 'grammar' else None)
        else:
            from .gui_providers import review_document

            instructions = self.instructions.toPlainText().strip() if kind == 'custom' else MODEL_CHECKS[kind]
            if instructions:
                review_document(self.window, check=kind, instructions=instructions)
