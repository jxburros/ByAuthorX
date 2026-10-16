# ruff: noqa: BLE001
"""File > Compile…: choose a preset, a target, a trim size and the binder items, preflight, compile.

The dialog drives the same library calls as ``byax compile`` (``compile.resolve_profile``,
``compile.compile_document``); it never changes the open document except when the writer saves a
preset, which is one canonical ``set_settings`` operation through ``commit``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from . import compile as book

TARGET_LABELS = (
    ('pdf', 'Print-ready PDF'),
    ('epub', 'EPUB e-book'),
    ('docx', 'Word document (.docx)'),
    ('md', 'Markdown'),
)
TARGET_FILTERS = {
    'pdf': 'PDF (*.pdf)',
    'epub': 'EPUB (*.epub)',
    'docx': 'Word document (*.docx)',
    'md': 'Markdown (*.md)',
}


def _name(widget, name, tip):
    widget.setAccessibleName(name)
    widget.setToolTip(tip)
    return widget


class CompileDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window_ref = window
        self.report = None
        self.setWindowTitle(self.tr('Compile'))
        self.resize(720, 640)
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'Turn this manuscript into a finished book. The document itself is not changed; '
                'preflight lists anything to fix first.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.preset = _name(
            QComboBox(), self.tr('Compile preset'), self.tr('Book design and structure to use')
        )
        for entry in book.list_presets(window.doc):
            suffix = self.tr(' (saved in this document)') if entry['source'] == 'document' else ''
            self.preset.addItem(entry['label'] + suffix, entry['name'])
        self.target = _name(QComboBox(), self.tr('Output format'), self.tr('What to compile the book into'))
        for key, label in TARGET_LABELS:
            self.target.addItem(self.tr(label), key)
        self.trim = _name(
            QComboBox(), self.tr('Trim size'), self.tr('Page size of the printed book (PDF only)')
        )
        self.trim.addItem(self.tr('As the preset says'), None)
        for key in book.TRIM_SIZES:
            self.trim.addItem(key, key)
        form.addRow(self.tr('Preset'), self.preset)
        form.addRow(self.tr('Format'), self.target)
        form.addRow(self.tr('Trim size'), self.trim)
        layout.addLayout(form)
        layout.addWidget(QLabel(self.tr('Binder items to include')))
        self.items = _name(
            QListWidget(),
            self.tr('Binder items to include'),
            self.tr('Checked items are compiled; notes are left out unless you check them'),
        )
        layout.addWidget(self.items, 2)
        layout.addWidget(QLabel(self.tr('Preflight')))
        self.findings = _name(
            QListWidget(),
            self.tr('Preflight results'),
            self.tr('Problems found before compiling: fonts, images, metadata, empty chapters and headings'),
        )
        layout.addWidget(self.findings, 1)
        self.status = QLabel(self.tr('Run preflight to check the book before compiling.'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.preflight_button = _name(
            QPushButton(self.tr('Run preflight')),
            self.tr('Run preflight'),
            self.tr('Check the compiled book without writing a file'),
        )
        self.save_button = _name(
            QPushButton(self.tr('Save preset…')),
            self.tr('Save preset'),
            self.tr('Save these choices as a compile preset in this document'),
        )
        self.compile_button = _name(
            QPushButton(self.tr('Compile…')),
            self.tr('Compile'),
            self.tr('Choose where to save the book and compile it'),
        )
        for button in (self.preflight_button, self.save_button, self.compile_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close = buttons.button(QDialogButtonBox.Close)
        _name(close, self.tr('Close'), self.tr('Close without compiling'))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.preflight_button.clicked.connect(self.run_preflight)
        self.save_button.clicked.connect(self.save_preset)
        self.compile_button.clicked.connect(self.run_compile)
        self.preset.currentIndexChanged.connect(self.preset_changed)
        self.fill_items()
        self.preset_changed()

    # --- choices -----------------------------------------------------------------------------
    def fill_items(self):
        from . import project

        self.items.clear()
        doc = self.window_ref.doc
        if isinstance(doc.get('project'), dict) and doc['project'].get('items'):
            rows = project.items(doc)
        else:
            rows = book._rows(doc)[0]
        for row in rows:
            words = self.tr('{0} words').format(f'{row["total_words"]:,}')
            label = (
                '    ' * row['depth'] + f'{row["title"] or self.tr("(untitled)")} · {row["kind"]} · {words}'
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, row['id'])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if row['include_in_compile'] else Qt.Unchecked)
            item.setToolTip(self.tr('Include "{0}" in the book').format(row['title']))
            self.items.addItem(item)
        self.default_included = [
            self.items.item(i).data(Qt.UserRole)
            for i in range(self.items.count())
            if self.items.item(i).checkState() == Qt.Checked
        ]

    def preset_changed(self, *_):
        try:
            profile, _source = book.resolve_profile(self.window_ref.doc, self.preset.currentData())
        except Exception as exc:
            self.status.setText(str(exc))
            return
        index = self.target.findData(profile.get('target', 'pdf'))
        if index >= 0:
            self.target.setCurrentIndex(index)

    def included(self):
        return [
            self.items.item(i).data(Qt.UserRole)
            for i in range(self.items.count())
            if self.items.item(i).checkState() == Qt.Checked
        ]

    def overrides(self):
        values = {}
        included = self.included()
        if included != self.default_included:
            values['include'] = included
        if self.trim.currentData():
            values['design'] = {'trim': self.trim.currentData()}
        values['target'] = self.target.currentData()
        return values

    def profile(self):
        return book.resolve_profile(
            self.window_ref.doc, self.preset.currentData(), overrides=self.overrides()
        )

    # --- actions -----------------------------------------------------------------------------
    def show_report(self, report):
        self.report = report
        self.window_ref.last_compile_report = report
        self.findings.clear()
        for finding in report.get('findings', []):
            target = ', '.join(finding.get('affected_ids', [])) or finding.get('scope', '')
            item = QListWidgetItem(f'{finding["severity"].capitalize()}: {finding["explanation"]} ({target})')
            item.setToolTip(finding['rule'])
            self.findings.addItem(item)
        if not report.get('findings'):
            self.findings.addItem(self.tr('No preflight findings.'))
        self.status.setText(book.describe(report))

    def run_preflight(self):
        self.window_ref.flush_edits()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            profile, source = self.profile()
            report = book.compile_document(
                self.window_ref.doc,
                profile=profile,
                source=source,
                target=self.target.currentData(),
                preflight_only=True,
            )
        except Exception as exc:
            self.window_ref.error(exc)
            return None
        finally:
            QApplication.restoreOverrideCursor()
        self.show_report(report)
        return report

    def run_compile(self, path=None):
        target = self.target.currentData()
        if not path:
            stem = Path(self.window_ref.path).stem if self.window_ref.path else 'book'
            path, _ = QFileDialog.getSaveFileName(
                self, self.tr('Compile book'), f'{stem}.{target}', self.tr(TARGET_FILTERS[target])
            )
        if not path:
            return None
        self.window_ref.flush_edits()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            profile, source = self.profile()
            report = book.compile_document(
                self.window_ref.doc, path, profile=profile, source=source, target=target, overwrite=True
            )
        except Exception as exc:
            self.window_ref.error(exc)
            return None
        finally:
            QApplication.restoreOverrideCursor()
        self.show_report(report)
        self.status.setText(self.tr('Saved {0}. ').format(Path(path).name) + book.describe(report))
        return report

    def save_preset(self, name=None):
        if not name:
            name, ok = QInputDialog.getText(self, self.tr('Save compile preset'), self.tr('Preset name'))
            if not ok or not name.strip():
                return False
        try:
            profile, _source = self.profile()
            operations = book.save_preset_operations(self.window_ref.doc, name.strip(), profile)
        except Exception as exc:
            self.window_ref.error(exc)
            return False
        if not self.window_ref.commit(operations):
            return False
        current = self.preset.findData(name.strip())
        if current < 0:
            self.preset.addItem(
                profile.get('label', name.strip()) + self.tr(' (saved in this document)'), name.strip()
            )
        self.status.setText(self.tr('Saved the compile preset "{0}" in this document.').format(name.strip()))
        return True


class CompilePanels:
    """MainWindow mixin: File > Compile…"""

    def compile_book(self):
        dialog = CompileDialog(self)
        self._compile_dialog = dialog
        dialog.exec()
        return dialog
