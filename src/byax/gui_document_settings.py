"""Native editors for publication policy, reusable page geometry and section profiles."""

import copy

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import profiles
from .formats import FORMATS
from .publication import PUBLICATION_CATEGORIES


class DocumentSettingsDialog(QDialog):
    def __init__(self, window, title):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(self.tr(title))
        self.resize(680, 650)
        self.root_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAccessibleName(self.tr(title))
        content = QWidget()
        scroll.setWidget(content)
        self.root_layout.addWidget(scroll)
        self.layout_box = QVBoxLayout(content)
        self.form = QFormLayout()
        self.layout_box.addLayout(self.form)

    def finish(self):
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        self.root_layout.addWidget(buttons)

    def combo(self, label, choices):
        control = QComboBox()
        control.setAccessibleName(self.tr(label))
        for text, value in choices:
            control.addItem(self.tr(text), value)
        self.form.addRow(self.tr(label), control)
        return control

    def submit(self, operations):
        if self.window.commit(operations):
            self.accept()


class ExportPolicyDialog(DocumentSettingsDialog):
    def __init__(self, window):
        super().__init__(window, 'Export policies')
        self.saved = self.combo(
            'Policy',
            [('New policy', None)]
            + [
                (p.get('name', key), key)
                for key, p in window.doc['profiles'].items()
                if p.get('clean') or p.get('format') or 'include_comments' in p
            ],
        )
        self.name = QLineEdit()
        self.name.setAccessibleName(self.tr('Policy name'))
        self.form.addRow(self.tr('Name'), self.name)
        self.format = self.combo(
            'Output format', [(v['label'], k) for k, v in FORMATS.items() if k != 'byax']
        )
        self.tabs = QTabWidget()
        self.layout_box.addWidget(self.tabs)
        inclusion = QWidget()
        inclusion_form = QFormLayout(inclusion)
        self.includes = {}
        for key, description in PUBLICATION_CATEGORIES.items():
            control = QCheckBox(self.tr(description))
            self.includes[key] = control
            inclusion_form.addRow(control)
        self.tabs.addTab(inclusion, self.tr('Include in publication'))
        checks = QWidget()
        check_form = QFormLayout(checks)
        self.suites = {}
        from .checks import SUITES

        for suite in SUITES:
            control = QCheckBox(self.tr(suite.replace('_', ' ').capitalize()))
            self.suites[suite] = control
            check_form.addRow(control)
        self.tabs.addTab(checks, self.tr('Required checks'))
        self.layout_box.addWidget(
            QLabel(self.tr('The master stays unchanged. Unsupported export features are reported.'))
        )
        self.saved.currentIndexChanged.connect(self.load)
        self.load()
        self.finish()

    def load(self):
        self.original = copy.deepcopy(self.window.doc['profiles'].get(self.saved.currentData(), {}))
        self.name.setText(self.original.get('name', 'Publication'))
        self.format.setCurrentIndex(max(0, self.format.findData(self.original.get('format', 'pdf'))))
        for key, control in self.includes.items():
            control.setChecked(self.original.get(key, False))
        for key, control in self.suites.items():
            control.setChecked(key in self.original.get('required_suites', []))

    def save(self):
        from .model import new_id

        config = {
            **self.original,
            'name': self.name.text().strip(),
            'clean': True,
            'format': self.format.currentData(),
            'required_suites': [key for key, box in self.suites.items() if box.isChecked()],
            **{key: box.isChecked() for key, box in self.includes.items()},
        }
        self.submit(
            [{'type': 'set_profile', 'id': self.saved.currentData() or new_id('profile'), 'values': config}]
        )


class MasterDialog(DocumentSettingsDialog):
    def __init__(self, window):
        super().__init__(window, 'Page masters')
        self.saved = self.combo(
            'Master', [('New master', None)] + [(k, k) for k in window.doc['layout'].get('masters', {})]
        )
        self.name = QLineEdit()
        self.name.setAccessibleName(self.tr('Master name'))
        self.form.addRow(self.tr('Name'), self.name)
        self.geometry = {}
        for key in ('width', 'height', 'top', 'bottom', 'left', 'right'):
            box = QDoubleSpinBox()
            box.setRange(1 if key in ('width', 'height') else 0, 10000)
            box.setSuffix(self.tr(' pt'))
            box.setAccessibleName(self.tr(key.capitalize()))
            self.geometry[key] = box
            self.form.addRow(self.tr(key.capitalize()), box)
        self.region_tabs = QTabWidget()
        self.layout_box.addWidget(self.region_tabs)
        self.regions = {}
        for region in ('header', 'footer'):
            tab = QWidget()
            form = QFormLayout(tab)
            for variant in ('first', 'odd', 'even'):
                for side in ('left', 'center', 'right'):
                    label = self.tr('{0} page · {1}').format(self.tr(variant.capitalize()), self.tr(side))
                    field = QLineEdit()
                    field.setAccessibleName(self.tr(region.capitalize()) + ' · ' + label)
                    field.setPlaceholderText(self.tr('Text; {page} and {pages} are supported'))
                    self.regions[(region, variant, side)] = field
                    form.addRow(label, field)
            self.region_tabs.addTab(tab, self.tr(region.capitalize()))
        targets = [('Save without assigning', None), ('Whole document', 'document')]
        targets += [
            (b.get('text') or self.tr('Section break') + ' ' + str(i + 1), b['id'])
            for i, b in enumerate(window.doc['blocks'])
            if b['type'] == 'section_break'
        ]
        self.assignment = self.combo('Assign to', targets)
        self.saved.currentIndexChanged.connect(self.load)
        self.load()
        self.finish()

    def load(self):
        layout = self.window.doc['layout']
        self.original = copy.deepcopy(layout.get('masters', {}).get(self.saved.currentData(), {}))
        self.name.setText(self.saved.currentData() or 'Letterhead')
        self.name.setReadOnly(self.saved.currentData() is not None)
        for key, box in self.geometry.items():
            value = (
                self.original.get(key, layout.get(key, 612 if key == 'width' else 792))
                if key in ('width', 'height')
                else self.original.get('margins', layout.get('margins', {})).get(key, 72)
            )
            box.setValue(value)
        for (region, variant, side), field in self.regions.items():
            value = self.original.get(region, '')
            if isinstance(value, dict) and any(k in value for k in ('first', 'odd', 'even', 'text')):
                value = value.get(variant, value.get('text', ''))
            if not isinstance(value, dict):
                value = {'left' if region == 'header' else 'center': value}
            field.setText(str(value.get(side, '')))

    def save(self):
        name = self.name.text().strip()
        config = copy.deepcopy(self.original)
        for key in ('width', 'height'):
            config[key] = self.geometry[key].value()
        config['margins'] = {k: self.geometry[k].value() for k in ('top', 'bottom', 'left', 'right')}
        for region in ('header', 'footer'):
            config[region] = {
                variant: {
                    side: self.regions[(region, variant, side)].text() for side in ('left', 'center', 'right')
                }
                for variant in ('first', 'odd', 'even')
            }
        operations = [{'type': 'define_master', 'name': name, 'values': config}]
        target = self.assignment.currentData()
        if target == 'document':
            operations.append({'type': 'set_layout', 'values': {'master': name}})
        elif target:
            operations.append({'type': 'set_attributes', 'target': target, 'values': {'master': name}})
        self.submit(operations)


class SectionProfileDialog(DocumentSettingsDialog):
    def __init__(self, window):
        super().__init__(window, 'Section checking and writing profile')
        self.heading = self.combo(
            'Section',
            [(b.get('text') or b['id'], b['id']) for b in window.doc['blocks'] if b['type'] == 'heading'],
        )
        self.profile = self.combo(
            'Checking profile',
            [('Use enclosing profile', None)]
            + [(p.get('name', k), k) for k, p in window.doc['profiles'].items() if not p.get('clean')],
        )
        self.inherit = QCheckBox(self.tr('Inherit dictionaries and intentional exceptions'))
        self.form.addRow(self.inherit)
        self.overrides = {}
        for key, choices in (
            ('sensitivity', profiles.SENSITIVITY),
            ('editing_freedom', profiles.EDITING_FREEDOM),
            ('voice_preservation', profiles.VOICE_PRESERVATION),
        ):
            self.overrides[key] = self.combo(
                key.replace('_', ' ').capitalize(),
                [('Inherit', None)] + [(v.capitalize(), v) for v in choices],
            )
        self.remove = QCheckBox(self.tr('Remove this section assignment'))
        self.form.addRow(self.remove)
        self.heading.currentIndexChanged.connect(self.load)
        self.load()
        self.finish()

    def load(self):
        self.original = copy.deepcopy(
            self.window.doc['settings'].get('section_profiles', {}).get(self.heading.currentData(), {})
        )
        self.profile.setCurrentIndex(max(0, self.profile.findData(self.original.get('profile'))))
        self.inherit.setChecked(self.original.get('inherit', True))
        self.remove.setChecked(False)
        for key, box in self.overrides.items():
            box.setCurrentIndex(max(0, box.findData(self.original.get('overrides', {}).get(key))))

    def save(self):
        target = self.heading.currentData()
        if not target:
            return
        value = {
            **self.original,
            'inherit': self.inherit.isChecked(),
            'overrides': dict(self.original.get('overrides', {})),
        }
        if self.profile.currentData():
            value['profile'] = self.profile.currentData()
        else:
            value.pop('profile', None)
        for key, box in self.overrides.items():
            if box.currentData():
                value['overrides'][key] = box.currentData()
            else:
                value['overrides'].pop(key, None)
        if self.remove.isChecked():
            if not self.original:
                self.accept()
                return
            value = {}
        self.submit([{'type': 'set_section_profile', 'target': target, 'values': value}])


class LayerDialog(DocumentSettingsDialog):
    def __init__(self, window, layer):
        super().__init__(window, 'Layer properties')
        self.layer = copy.deepcopy(layer)
        self.name = QLineEdit(layer.get('name', 'Layer'))
        self.name.setAccessibleName(self.tr('Layer name'))
        self.form.addRow(self.tr('Name'), self.name)
        self.text = QLineEdit(layer.get('text', ''))
        self.text.setAccessibleName(self.tr('Layer text'))
        self.form.addRow(self.tr('Text'), self.text)
        self.values = {}
        for key, default, minimum, maximum in (
            ('x', 0, -10000, 10000),
            ('y', 0, -10000, 10000),
            ('width', 100, 1, 10000),
            ('height', 100, 1, 10000),
            ('opacity', 1, 0, 1),
            ('font_size', 12, 1, 1000),
        ):
            box = QDoubleSpinBox()
            box.setRange(minimum, maximum)
            box.setValue(layer.get(key, default))
            box.setAccessibleName(self.tr(key.replace('_', ' ').capitalize()))
            self.form.addRow(box.accessibleName(), box)
            self.values[key] = box
        self.position = self.combo(
            'Anchor policy',
            [
                ('Move with text', 'move_with_text'),
                ('Fix on anchor page', 'fix_on_anchor_page'),
                ('Fixed document page', 'fixed_page'),
            ],
        )
        self.position.setCurrentIndex(max(0, self.position.findData(layer.get('position', 'fixed_page'))))
        self.outputs = {}
        for label, key in (
            ('Include only in formats (empty means all)', 'outputs'),
            ('Exclude from formats', 'exclude_outputs'),
        ):
            field = QLineEdit(', '.join(layer.get(key, [])))
            field.setAccessibleName(self.tr(label))
            field.setPlaceholderText('pdf, docx, odt, rtf, html, md, txt')
            self.form.addRow(self.tr(label), field)
            self.outputs[key] = field
        self.finish()

    def save(self):
        from .errors import ByaxError
        from .formats import canonical_format

        try:
            values = {key: box.value() for key, box in self.values.items()}
            values.update(name=self.name.text(), text=self.text.text(), position=self.position.currentData())
            for key, field in self.outputs.items():
                entries = [canonical_format(v.strip()) for v in field.text().split(',') if v.strip()]
                # Explicitly listing all output formats is equivalent to an unrestricted layer.
                values[key] = entries or (list(FORMATS) if key == 'outputs' else [])
            self.submit([{'type': 'update_layer', 'target': self.layer['id'], 'values': values}])
        except ByaxError as exc:
            self.window.error(exc)
