"""Native profile controls; document changes use canonical operations."""

from __future__ import annotations

import copy
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .errors import ByaxError
from .profiles import (
    APPLICATION_POLICY,
    EDITING_FREEDOM,
    PROVIDER_ROLES,
    RULE_FAMILIES,
    SENSITIVITY,
    SOURCE_AUTHORITY,
    VOICE_PRESERVATION,
    export_collection,
    import_collection,
    presets,
    validate_profile,
)
from .providers import ProviderRegistry


class ProfileDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.profile_id = None
        self.original = presets()['conservative_proofreading']
        self.setWindowTitle(self.tr('Checking and voice profiles'))
        self.resize(690, 620)
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'Control what gets flagged, what rewrites may change, and how closely they preserve your voice. These are independent choices.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.saved = QComboBox()
        self.saved.setAccessibleName(self.tr('Saved profile or preset'))
        layout.addWidget(self.saved)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        review = QWidget()
        form = QFormLayout(review)
        self.name = QLineEdit()
        self.name.setAccessibleName(self.tr('Profile name'))
        form.addRow(self.tr('Name'), self.name)
        self.sensitivity = self._combo(SENSITIVITY, 'Sensitivity')
        self.editing = self._combo(EDITING_FREEDOM, 'Editing freedom')
        self.voice = self._combo(VOICE_PRESERVATION, 'Voice preservation')
        self.policy = self._combo(APPLICATION_POLICY, 'Application policy')
        form.addRow(self.tr('Sensitivity · concerns to raise'), self.sensitivity)
        form.addRow(self.tr('Editing freedom · permitted changes'), self.editing)
        form.addRow(self.tr('Voice preservation'), self.voice)
        form.addRow(self.tr('Application policy'), self.policy)
        note = QLabel(
            self.tr(
                'Automatic policies never authorize remote sharing. Transcript insertion still revalidates its destination. Structural errors and publication requirements cannot be dismissed by a profile.'
            )
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.families = QListWidget()
        self.families.setAccessibleName(self.tr('Enabled rule families'))
        for family in RULE_FAMILIES:
            self._check_item(self.families, family, family)
        form.addRow(self.tr('Rule families'), self.families)
        self.dictionary = QLineEdit()
        self.dictionary.setPlaceholderText(self.tr('Accepted words separated by commas'))
        self.dictionary.setAccessibleName(self.tr('Accepted spelling words'))
        form.addRow(self.tr('Accepted spelling words'), self.dictionary)
        tabs.addTab(review, self.tr('Review controls'))
        sources = QWidget()
        source_form = QFormLayout(sources)
        self.authority = self._combo(SOURCE_AUTHORITY, 'Source authority')
        source_form.addRow(self.tr('Source authority'), self.authority)
        source_note = QLabel(
            self.tr(
                'Exclusive: judge against selected sources only. Supplementary/open: selected evidence remains identified; outside retrieval requires its own explicit workflow.'
            )
        )
        source_note.setWordWrap(True)
        source_form.addRow(source_note)
        self.references = QListWidget()
        self.references.setAccessibleName(self.tr('Selected reference sources'))
        source_form.addRow(self.tr('Selected references'), self.references)
        self.providers = {}
        configured = ProviderRegistry().list()
        for role in PROVIDER_ROLES:
            box = QComboBox()
            box.setAccessibleName(role.title() + self.tr(' provider alias'))
            box.addItem(self.tr('Choose at use time'), None)
            kinds = (
                {'openai', 'elevenlabs', 'whisper.cpp'}
                if role == 'recognition'
                else {'openai', 'anthropic', 'gemini', 'ollama'}
            )
            for provider in configured:
                if provider['kind'] in kinds:
                    box.addItem(provider['alias'], provider['alias'])
            self.providers[role] = box
            source_form.addRow(role.title() + self.tr(' provider'), box)
        tabs.addTab(sources, self.tr('Sources and models'))
        exception_widget = QWidget()
        exception_layout = QVBoxLayout(exception_widget)
        help_text = QLabel(
            self.tr(
                'Keep intentional language, repetition, dialect, or spelling visible as accepted exceptions. Supply a rule (or *), optional target/text, and a reason. Exceptions never hide structural or requirement errors.'
            )
        )
        help_text.setWordWrap(True)
        exception_layout.addWidget(help_text)
        self.exceptions = QTableWidget(0, 4)
        self.exceptions.setAccessibleName(self.tr('Intentional exceptions'))
        self.exceptions.setHorizontalHeaderLabels(
            [self.tr('Rule'), self.tr('Target (optional)'), self.tr('Text (optional)'), self.tr('Reason')]
        )
        self.exceptions.horizontalHeader().setStretchLastSection(True)
        exception_layout.addWidget(self.exceptions)
        row_buttons = QHBoxLayout()
        add = QPushButton(self.tr('Add exception'))
        remove = QPushButton(self.tr('Remove selected exception'))
        add.clicked.connect(lambda: self.exceptions.insertRow(self.exceptions.rowCount()))
        remove.clicked.connect(
            lambda: (
                self.exceptions.removeRow(self.exceptions.currentRow())
                if self.exceptions.currentRow() >= 0
                else None
            )
        )
        row_buttons.addWidget(add)
        row_buttons.addWidget(remove)
        exception_layout.addLayout(row_buttons)
        tabs.addTab(exception_widget, self.tr('Intentional exceptions'))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        save = buttons.addButton(self.tr('Save and use profile'), QDialogButtonBox.AcceptRole)
        copy_button = buttons.addButton(self.tr('Save as new profile'), QDialogButtonBox.ActionRole)
        load = buttons.addButton(self.tr('Import…'), QDialogButtonBox.ActionRole)
        export = buttons.addButton(self.tr('Export…'), QDialogButtonBox.ActionRole)
        save.clicked.connect(self.save_profile)
        copy_button.clicked.connect(lambda: self.save_profile(as_new=True))
        load.clicked.connect(self.import_profiles)
        export.clicked.connect(self.export_profiles)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.saved.currentIndexChanged.connect(self.load_selected)
        self.refresh_choices()

    @staticmethod
    def _combo(values, label):
        box = QComboBox()
        box.setAccessibleName(label)
        for value in values:
            box.addItem(value.replace('_', ' ').capitalize(), value)
        return box

    @staticmethod
    def _check_item(widget, label, identity, checked=False):
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, identity)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        widget.addItem(item)

    def refresh_choices(self, selected=None):
        self.saved.blockSignals(True)
        self.saved.clear()
        for identity, profile in self.window.doc.get('profiles', {}).items():
            self.saved.addItem(profile.get('name', identity), ('document', identity))
        for identity, profile in presets().items():
            self.saved.addItem(self.tr('Preset: ') + profile['name'], ('preset', identity))
        desired = selected or getattr(self.window, 'active_profile', None)
        if desired:
            index = self.saved.findData(('document', desired))
            if index >= 0:
                self.saved.setCurrentIndex(index)
        self.saved.blockSignals(False)
        self.load_selected()

    def load_selected(self):
        origin, identity = self.saved.currentData()
        self.profile_id = identity if origin == 'document' else None
        raw = self.window.doc['profiles'][identity] if origin == 'document' else presets()[identity]
        self.original = validate_profile(raw, strict=False)
        config = self.original
        self.name.setText(config['name'])
        for control, key in (
            (self.sensitivity, 'sensitivity'),
            (self.editing, 'editing_freedom'),
            (self.voice, 'voice_preservation'),
            (self.policy, 'application_policy'),
            (self.authority, 'source_authority'),
        ):
            control.setCurrentIndex(control.findData(config[key]))
        for index in range(self.families.count()):
            item = self.families.item(index)
            item.setCheckState(
                Qt.Checked if item.data(Qt.UserRole) in config['rule_families'] else Qt.Unchecked
            )
        self.dictionary.setText(', '.join(config['dictionary']))
        self.references.clear()
        all_sources = {
            identity: source.get('title', identity)
            for identity, source in self.window.doc.get('references', {}).items()
        }
        for identity in config['references']:
            all_sources.setdefault(identity, identity + ' (unresolved)')
        for identity, title in all_sources.items():
            self._check_item(self.references, title, identity, identity in config['references'])
        for role, control in self.providers.items():
            alias = config['providers'][role]
            index = control.findData(alias)
            if index < 0:
                control.addItem(str(alias) + self.tr(' (unresolved)'), alias)
                index = control.count() - 1
            control.setCurrentIndex(index)
        self.exceptions.setRowCount(len(config['intentional_exceptions']))
        for row, exception in enumerate(config['intentional_exceptions']):
            for column, key in enumerate(('rule', 'target', 'text', 'reason')):
                self.exceptions.setItem(row, column, QTableWidgetItem(exception.get(key, '')))

    @staticmethod
    def _checked(widget):
        return [
            widget.item(index).data(Qt.UserRole)
            for index in range(widget.count())
            if widget.item(index).checkState() == Qt.Checked
        ]

    def current_profile(self):
        config = copy.deepcopy(self.original)
        config.update(
            name=self.name.text().strip(),
            sensitivity=self.sensitivity.currentData(),
            editing_freedom=self.editing.currentData(),
            voice_preservation=self.voice.currentData(),
            application_policy=self.policy.currentData(),
            source_authority=self.authority.currentData(),
            rule_families=self._checked(self.families),
            references=self._checked(self.references),
            dictionary=[word.strip() for word in self.dictionary.text().split(',') if word.strip()],
            providers={role: control.currentData() for role, control in self.providers.items()},
            intentional_exceptions=[],
        )
        for row in range(self.exceptions.rowCount()):
            values = {
                key: self.exceptions.item(row, column).text().strip()
                if self.exceptions.item(row, column)
                else ''
                for column, key in enumerate(('rule', 'target', 'text', 'reason'))
            }
            config['intentional_exceptions'].append({key: value for key, value in values.items() if value})
        return validate_profile(config)

    def save_profile(self, *, as_new=False):
        try:
            config = self.current_profile()
            identity = self.profile_id if self.profile_id and not as_new else 'profile_' + uuid.uuid4().hex
            config['version'] = self.original['version'] + 1 if identity == self.profile_id else 1
            if self.window.commit([{'type': 'set_profile', 'id': identity, 'values': config}]):
                self.window.active_profile = identity
                self.window.status_label.setText(self.tr('Active profile: ') + config['name'])
                self.accept()
        except (ByaxError, OSError, ValueError) as exc:
            self.window.error(exc)

    def import_profiles(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr('Import profile collection'), '', self.tr('Profile collections (*.json)')
        )
        if not path:
            return
        try:
            profiles = import_collection(path)
            overlap = set(profiles) & set(self.window.doc.get('profiles', {}))
            if (
                overlap
                and QMessageBox.question(
                    self,
                    self.tr('Replace matching profiles?'),
                    self.tr('This collection replaces ')
                    + str(len(overlap))
                    + self.tr(' existing profile(s).'),
                )
                != QMessageBox.Yes
            ):
                return
            if profiles and self.window.commit(
                [
                    {'type': 'set_profile', 'id': identity, 'values': profile}
                    for identity, profile in profiles.items()
                ]
            ):
                self.refresh_choices()
        except (ByaxError, OSError, ValueError) as exc:
            self.window.error(exc)

    def export_profiles(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr('Export saved profile collection'),
            'profiles.json',
            self.tr('Profile collections (*.json)'),
        )
        if not path:
            return
        try:
            export_collection(self.window.doc.get('profiles', {}), path)
            self.window.status_label.setText(
                self.tr('Saved profile collection exported without provider credentials')
            )
        except (ByaxError, OSError, ValueError) as exc:
            self.window.error(exc)
