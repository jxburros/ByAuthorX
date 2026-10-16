# ruff: noqa: BLE001
"""Tools > Languages: language packs, interface language and the grammar tiers.

Everything here is a local, explicit action. Packs are installed from a file the person chooses;
LanguageTool is downloaded only after the dialog has shown the source URL, size and SHA-256 and the
person confirms. The same operations are available from the CLI (`languages`, `grammar`).
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import grammar, language_packs
from .errors import ByaxError


class _Progress(QObject):
    step = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)


def tier_summary():
    """Plain-language state of both grammar tiers (also shown in the Checks tab)."""
    config, status = grammar.tier_state()
    rules = (grammar.BuiltinEngine().describe('en') or {'rules': []})['rules']
    builtin = (
        f'Built-in rules: on ({len(rules)} English rules)'
        if config['builtin']['enabled']
        else 'Built-in rules: off'
    )
    languagetool = status['label']
    if status['state'] != 'not installed' and not config['languagetool']['enabled']:
        languagetool += ' (off)'
    return {'builtin': builtin, 'languagetool': languagetool, 'status': status, 'config': config}


class LanguagesDialog(QDialog):
    def __init__(self, window=None, tab=None):
        super().__init__(window)
        self.window_ref = window
        self.setWindowTitle(self.tr('Languages and grammar'))
        self.setAccessibleName(self.tr('Languages and grammar'))
        self.resize(820, 560)
        self.progress = _Progress()
        self.progress.step.connect(self._download_step)
        self.progress.done.connect(self._download_done)
        self.progress.failed.connect(self._failed)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName(self.tr('Languages sections'))
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._packs_tab(), self.tr('Language packs'))
        self.tabs.addTab(self._interface_tab(), self.tr('Interface language'))
        self.tabs.addTab(self._grammar_tab(), self.tr('Grammar'))
        if tab == 'grammar':
            self.tabs.setCurrentIndex(2)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    # Packs ----------------------------------------------------------------------------------
    def _packs_tab(self):
        page = QWidget()
        column = QVBoxLayout(page)
        note = QLabel(
            self.tr(
                'Dictionary, grammar-rule, word-list and interface-string packs are data. Each file is '
                'checked against its SHA-256 before use. Installing reads a file from this computer; '
                'nothing is downloaded.'
            )
        )
        note.setWordWrap(True)
        column.addWidget(note)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [self.tr('Pack'), self.tr('Language'), self.tr('Version'), self.tr('Provides'), self.tr('Status')]
        )
        self.table.setAccessibleName(self.tr('Installed language packs'))
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        column.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.install_button = QPushButton(self.tr('Install from file…'))
        self.install_button.setAccessibleName(self.tr('Install a language pack from a file'))
        self.install_button.clicked.connect(self.install_from_file)
        self.verify_button = QPushButton(self.tr('Verify all'))
        self.verify_button.setAccessibleName(self.tr('Verify language pack checksums'))
        self.verify_button.clicked.connect(self.verify_all)
        self.remove_button = QPushButton(self.tr('Remove selected'))
        self.remove_button.setAccessibleName(self.tr('Remove the selected language pack'))
        self.remove_button.clicked.connect(self.remove_selected)
        for button in (self.install_button, self.verify_button, self.remove_button):
            row.addWidget(button)
        row.addStretch(1)
        column.addLayout(row)
        self.pack_status = QLabel('')
        self.pack_status.setWordWrap(True)
        self.pack_status.setAccessibleName(self.tr('Language pack status'))
        column.addWidget(self.pack_status)
        return page

    def selected_pack(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.table.item(rows[0].row(), 0).data(Qt.UserRole) if rows else None

    def install_from_file(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, self.tr('Install language pack'), '', self.tr('Language packs (*.byaxpack *.zip)')
            )
        if not path:
            return None
        try:
            digest = language_packs._sha256_file(path)
            answer = QMessageBox.question(
                self,
                self.tr('Install language pack'),
                self.tr(
                    'Install the pack in {0}?\n\nSHA-256 of the file: {1}\n\nThe pack is checked file by file before it is used.'
                ).format(Path(path).name, digest),
            )
            if answer != QMessageBox.Yes:
                return None
            result = language_packs.install_pack(path, sha256=digest, replace=True)
        except ByaxError as exc:
            self._failed(exc.message)
            return None
        self.pack_status.setText(
            self.tr('Installed {0} {1} ({2}).').format(result['id'], result['version'], result['licence'])
        )
        self.refresh()
        return result

    def verify_all(self):
        results = language_packs.verify_packs()
        bad = [r for r in results if not r['ok']]
        self.pack_status.setText(
            self.tr('All {0} packs match their checksums.').format(len(results))
            if not bad
            else self.tr('Problems: {0}').format('; '.join(f'{r["id"]}: {r["problems"][0]}' for r in bad))
        )
        return results

    def remove_selected(self):
        pack_id = self.selected_pack()
        if not pack_id:
            return None
        try:
            if any(p['id'] == pack_id and p['built_in'] for p in language_packs.list_packs()):
                raise ByaxError(
                    'conflict', self.tr('Built-in packs ship with the application and cannot be removed.')
                )
            result = language_packs.remove_pack(pack_id)
        except ByaxError as exc:
            self._failed(exc.message)
            return None
        self.pack_status.setText(self.tr('Removed {0}.').format(pack_id))
        self.refresh()
        return result

    # Interface ---------------------------------------------------------------------------------
    def _interface_tab(self):
        page = QWidget()
        column = QVBoxLayout(page)
        note = QLabel(
            self.tr(
                'The interface is written in English. A language is available here once a strings pack for it '
                'is installed. Coverage counts how many interface texts have a usable translation; missing '
                'texts stay in English.'
            )
        )
        note.setWordWrap(True)
        column.addWidget(note)
        self.language_box = QComboBox()
        self.language_box.setAccessibleName(self.tr('Interface language'))
        self.language_box.currentIndexChanged.connect(self._language_changed)
        column.addWidget(self.language_box)
        self.coverage_label = QLabel('')
        self.coverage_label.setWordWrap(True)
        self.coverage_label.setAccessibleName(self.tr('Translation coverage'))
        column.addWidget(self.coverage_label)
        self.apply_language = QPushButton(self.tr('Use this language after restart'))
        self.apply_language.setAccessibleName(self.tr('Save the interface language'))
        self.apply_language.clicked.connect(self.save_language)
        column.addWidget(self.apply_language)
        column.addStretch(1)
        return page

    def _language_changed(self, *_):
        code = self.language_box.currentData()
        if code is None:
            return
        info = language_packs.translation_coverage(code)
        self.coverage_label.setText(
            self.tr('English is the source language: {0} texts.').format(info['total'])
            if code == 'en'
            else self.tr('{0}: {1} of {2} texts translated ({3}%).').format(
                code, info['translated'], info['total'], info['percent']
            )
        )

    def save_language(self):
        code = self.language_box.currentData() or 'en'
        settings = getattr(self.window_ref, 'settings', None)
        if settings is not None:
            settings.setValue('interface/language', '' if code == 'en' else code)
        self.coverage_label.setText(self.coverage_label.text() + ' ' + self.tr('Restart ByAuthorX to apply.'))
        return code

    # Grammar -----------------------------------------------------------------------------------
    def _grammar_tab(self):
        page = QWidget()
        column = QVBoxLayout(page)
        intro = QLabel(
            self.tr(
                'Two tiers check grammar on this computer. The built-in rules are small and always '
                'available. LanguageTool is optional: it needs Java 17 or newer and runs as a separate local '
                'program (LGPL-2.1). Text is only ever sent to it on this computer.'
            )
        )
        intro.setWordWrap(True)
        column.addWidget(intro)
        self.builtin_label = QLabel('')
        self.builtin_label.setAccessibleName(self.tr('Built-in rules status'))
        column.addWidget(self.builtin_label)
        self.lt_label = QLabel('')
        self.lt_label.setAccessibleName(self.tr('LanguageTool status'))
        self.lt_label.setWordWrap(True)
        column.addWidget(self.lt_label)
        self.lt_enabled = QCheckBox(self.tr('Use LanguageTool in grammar checks'))
        self.lt_enabled.setAccessibleName(self.tr('Use LanguageTool in grammar checks'))
        self.lt_enabled.toggled.connect(self._toggle_languagetool)
        column.addWidget(self.lt_enabled)
        row = QHBoxLayout()
        self.lt_install = QPushButton(self.tr('Install LanguageTool…'))
        self.lt_install.setAccessibleName(self.tr('Install LanguageTool'))
        self.lt_install.clicked.connect(self.install_languagetool)
        self.lt_folder = QPushButton(self.tr('Choose LanguageTool folder…'))
        self.lt_folder.setAccessibleName(self.tr('Choose an existing LanguageTool folder'))
        self.lt_folder.clicked.connect(self.choose_folder)
        self.lt_start = QPushButton(self.tr('Start'))
        self.lt_start.setAccessibleName(self.tr('Start the local LanguageTool server'))
        self.lt_start.clicked.connect(self.start_server)
        self.lt_stop = QPushButton(self.tr('Stop'))
        self.lt_stop.setAccessibleName(self.tr('Stop the local LanguageTool server'))
        self.lt_stop.clicked.connect(self.stop_server)
        for button in (self.lt_install, self.lt_folder, self.lt_start, self.lt_stop):
            row.addWidget(button)
        row.addStretch(1)
        column.addLayout(row)
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel(self.tr('Local port')))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setAccessibleName(self.tr('LanguageTool local port'))
        self.port.editingFinished.connect(self._save_port)
        port_row.addWidget(self.port)
        port_row.addStretch(1)
        column.addLayout(port_row)
        self.lt_output = QPlainTextEdit()
        self.lt_output.setReadOnly(True)
        self.lt_output.setMaximumHeight(120)
        self.lt_output.setAccessibleName(self.tr('LanguageTool messages'))
        column.addWidget(self.lt_output)
        column.addStretch(1)
        return page

    def _toggle_languagetool(self, on):
        if self._refreshing:
            return
        config = grammar.load_config()
        config['languagetool']['enabled'] = bool(on)
        grammar.save_config(config)
        self.refresh()

    def _save_port(self):
        config = grammar.load_config()
        if config['languagetool']['port'] != self.port.value():
            config['languagetool']['port'] = self.port.value()
            grammar.save_config(config)
            self.refresh()

    def choose_folder(self, path=None):
        if not path:
            path = QFileDialog.getExistingDirectory(self, self.tr('Choose the LanguageTool folder'))
        if not path:
            return None
        if grammar.find_languagetool(path) is None:
            self._failed(self.tr('No languagetool-server.jar was found in that folder.'))
            return None
        config = grammar.load_config()
        config['languagetool']['directory'] = path
        config['languagetool']['enabled'] = True
        grammar.save_config(config)
        self.refresh()
        return path

    def start_server(self):
        try:
            status = grammar.start_languagetool()
        except ByaxError as exc:
            self._failed(exc.message)
            return None
        self.lt_output.setPlainText(status.get('message') or self.tr('LanguageTool is running.'))
        self.refresh()
        return status

    def stop_server(self):
        status = grammar.stop_languagetool()
        self.lt_output.setPlainText(status.get('message') or self.tr('LanguageTool was stopped.'))
        self.refresh()
        return status

    def install_plan_text(self):
        plan = grammar.installation_plan()
        return self.tr(
            'Download LanguageTool {0}?\n\nSource: {1}\nSize: {2:,} bytes\nSHA-256: {3}\nLicence: {4}\nNeeds: {5}\n\n'
            'The file is checked against this size and SHA-256 before anything is unpacked. It is stored in '
            'your ByAuthorX data folder and is not part of ByAuthorX.'
        ).format(
            plan['version'], plan['url'], plan['size'], plan['sha256'], plan['licence'], plan['requires']
        )

    def install_languagetool(self, confirmed=None):
        if confirmed is None:
            confirmed = (
                QMessageBox.question(self, self.tr('Install LanguageTool'), self.install_plan_text())
                == QMessageBox.Yes
            )
        if not confirmed:
            return False
        self.lt_install.setEnabled(False)
        self.lt_output.setPlainText(self.tr('Downloading…'))

        def work():
            try:
                self.progress.done.emit(
                    grammar.install_languagetool(
                        confirm_sha256=grammar.LANGUAGETOOL['sha256'],
                        progress=lambda done, total: self.progress.step.emit(done, total),
                    )
                )
            except Exception as exc:
                self.progress.failed.emit(exc.message if isinstance(exc, ByaxError) else str(exc))

        self._download_thread = threading.Thread(target=work, daemon=True)
        self._download_thread.start()
        return True

    def _download_step(self, done, total):
        self.lt_output.setPlainText(
            self.tr('Downloading… {0} of {1} MB').format(done // 1_000_000, total // 1_000_000)
        )

    def _download_done(self, result):
        self.lt_output.setPlainText(self.tr('Installed to {0}.').format(result['directory']))
        self.lt_install.setEnabled(True)
        self.refresh()

    # Shared ---------------------------------------------------------------------------------------
    def _failed(self, message):
        self.lt_install.setEnabled(True)
        self.pack_status.setText(str(message))
        self.lt_output.setPlainText(str(message))

    _refreshing = False

    def refresh(self):
        self._refreshing = True
        try:
            language_packs.refresh()
            packs = language_packs.list_packs()
            self.table.setRowCount(len(packs))
            for row, pack in enumerate(packs):
                cells = [
                    pack['id'],
                    pack['language'] or '',
                    pack['version'] or '',
                    ', '.join(pack['kinds']),
                    self.tr('built in') if pack['built_in'] else pack['status'],
                ]
                for column, text in enumerate(cells):
                    item = QTableWidgetItem(str(text))
                    item.setData(Qt.UserRole, pack['id'])
                    self.table.setItem(row, column, item)
            self.language_box.blockSignals(True)
            self.language_box.clear()
            for report in language_packs.coverage_report():
                self.language_box.addItem(report['language'], report['language'])
            current = ''
            settings = getattr(self.window_ref, 'settings', None)
            if settings is not None:
                current = str(settings.value('interface/language', '') or '')
            index = self.language_box.findData(current or 'en')
            self.language_box.setCurrentIndex(max(index, 0))
            self.language_box.blockSignals(False)
            self._language_changed()
            summary = tier_summary()
            self.builtin_label.setText(summary['builtin'])
            self.lt_label.setText(summary['languagetool'])
            status = summary['status']
            self.lt_enabled.setChecked(summary['config']['languagetool']['enabled'])
            self.lt_enabled.setEnabled(status['installed'] or status['running'])
            self.lt_start.setEnabled(status['installed'] and not status['running'])
            self.lt_stop.setEnabled(status['running'] and status['managed_by_byax'])
            self.port.setValue(status['port'])
            notice = ''
            if status['state'] == 'not installed':
                notice = self.tr(
                    'Not installed. Install the official release or choose a folder you already have.'
                )
            elif not status['java']:
                notice = self.tr('Java was not found; LanguageTool needs Java 17 or newer.')
            if notice and not self.lt_output.toPlainText():
                self.lt_output.setPlainText(notice)
        finally:
            self._refreshing = False
        parent = self.window_ref
        refresh_status = getattr(getattr(parent, 'checks_panel', None), 'refresh_grammar_status', None)
        if refresh_status is not None:
            refresh_status()


class LanguagePanels:
    """Main-window entry point (mixed into ``MainWindow``)."""

    def show_languages(self, tab=None):
        dialog = LanguagesDialog(self, tab=tab)
        dialog.exec()
        return dialog
