"""The Configure tab: API keys, information repositories and the settings behind checks and exports."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class ConfigurePanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
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

        ai = self.section(layout, 'AI providers and API keys')
        # The assistant keeps these controls current; they live here so the AI tab stays a conversation.
        ai.addWidget(window.ai_panel.provider_status)
        ai.addWidget(window.ai_panel.keys_button)

        repositories = self.section(layout, 'Information repositories')
        self.repository_status = QLabel()
        self.repository_status.setObjectName('checkDescription')
        self.repository_status.setWordWrap(True)
        repositories.addWidget(self.repository_status)
        self.buttons(
            repositories,
            (
                ('Libraries…', window.library_panel),
                ('Import sources…', window.import_sources),
                ('Citation style…', window.citation_settings),
            ),
        )

        checking = self.section(layout, 'Checking')
        self.buttons(
            checking,
            (
                ('Checking and voice profiles…', window.edit_profiles),
                ('Section profiles…', window.edit_section_profiles),
            ),
        )

        exports = self.section(layout, 'Export and document')
        self.buttons(
            exports,
            (
                ('Edit export policies…', window.edit_export_policies),
                ('Reusable settings…', window.reusable_settings),
                ('Document properties…', window.document_properties),
                ('Markdown opening preference…', window.markdown_preferences),
            ),
        )

        appearance = self.section(layout, 'Appearance')
        for action in (window.hand_marks_action, window.reduce_motion_action):
            box = QCheckBox(action.text())
            box.setToolTip(action.statusTip() or action.text())
            box.setChecked(action.isChecked())
            box.toggled.connect(lambda on, action=action: action.isChecked() != on and action.trigger())
            action.toggled.connect(box.setChecked)
            appearance.addWidget(box)
        layout.addStretch()
        self.refresh()

    def section(self, layout, title):
        group = QGroupBox(self.tr(title))
        rows = QVBoxLayout(group)
        layout.addWidget(group)
        return rows

    def buttons(self, rows, entries):
        for label, slot in entries:
            button = QPushButton(self.tr(label))
            button.clicked.connect(lambda checked=False, slot=slot: (slot(), self.refresh()))
            rows.addWidget(button)

    def refresh(self):
        settings = self.window.doc.get('settings', {})
        libraries = len(settings.get('libraries', []) or [])
        sources = len(self.window.doc.get('references', {}) or {})
        self.repository_status.setText(
            self.tr(
                'Repository and accuracy checks compare the document with {0} attached '
                'librar{1} and {2} source{3}.'
            ).format(libraries, 'y' if libraries == 1 else 'ies', sources, '' if sources == 1 else 's')
        )
