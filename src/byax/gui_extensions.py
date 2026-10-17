# ruff: noqa: BLE001
"""Tools > Extensions and Tools > Macros.

Extensions run out of process with limits and are not sandboxed, so installing one is always an explicit
local action that shows the requested scopes and the SHA-256 first. Everything here is also available
from the CLI (`extensions`, `macro`) and, for the read-only parts, over MCP and REST.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import extensions, macros, storage
from .errors import ByaxError

HOOKS_SETTING = 'extensions/runExportHooks'


class _Task(QObject):
    done = Signal(object)
    failed = Signal(str)

    def start(self, fn):
        def work():
            try:
                self.done.emit(fn())
            except Exception as exc:
                self.failed.emit(exc.message if isinstance(exc, ByaxError) else str(exc))

        threading.Thread(target=work, daemon=True).start()


def _table(headers, name, parent):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAccessibleName(name)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _selected(table):
    rows = table.selectionModel().selectedRows() if table.selectionModel() else []
    return table.item(rows[0].row(), 0).data(Qt.UserRole) if rows else None


def confirmation_text(plan):
    """The words of the install confirmation: who, what it may do, and the hash that was verified."""
    info = plan['extension']
    scopes = '\n'.join(f'  - {name}: {text}' for name, text in plan['scope_descriptions'].items())
    return (
        f'{info["name"]} {info["version"]} by {info["author"]} (licence {info["licence"]})\n\n'
        f'{info["description"]}\n\n'
        f'It asks for these permissions:\n{scopes}\n\n'
        f'SHA-256 ({plan["sha256_of"]}): {plan["sha256"]}\n\n'
        'Extensions run as separate programs with time, memory and output limits; they are not sandboxed. '
        'Install only extensions whose author you trust. It is installed switched off.'
    )


class ExtensionsDialog(QDialog):
    def __init__(self, window=None):
        super().__init__(window)
        self.window_ref = window
        self.setWindowTitle(self.tr('Extensions'))
        self.setAccessibleName(self.tr('Extensions'))
        self.resize(900, 640)
        self.task = _Task()
        self.task.done.connect(self._task_done)
        self.task.failed.connect(self._failed)
        layout = QVBoxLayout(self)
        note = QLabel(
            self.tr(
                'Extensions add checks and export hooks. They run in their own process with time, memory and '
                'output limits, see only what the permissions below allow, and can only suggest changes. '
                'They are not sandboxed: install only extensions whose author you trust.'
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = _table(
            [
                self.tr('Extension'),
                self.tr('Version'),
                self.tr('Provides'),
                self.tr('Permissions'),
                self.tr('Status'),
                self.tr('Enabled'),
            ],
            self.tr('Installed extensions'),
            self,
        )
        self.table.itemSelectionChanged.connect(self._show_details)
        layout.addWidget(self.table, 2)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName(self.tr('Extension details'))
        layout.addWidget(self.details, 2)
        row = QHBoxLayout()
        self.install_folder_button = self._button(
            self.tr('Install from folder…'),
            self.tr('Install an extension from a folder'),
            self.install_from_folder,
        )
        self.install_archive_button = self._button(
            self.tr('Install from archive…'),
            self.tr('Install an extension from a ZIP file'),
            self.install_from_archive,
        )
        self.toggle_button = self._button(
            self.tr('Enable'), self.tr('Enable or disable the selected extension'), self.toggle_enabled
        )
        self.remove_button = self._button(
            self.tr('Remove…'), self.tr('Remove the selected extension'), self.remove_selected
        )
        self.verify_button = self._button(
            self.tr('Verify'), self.tr('Verify installed extension files'), self.verify_all
        )
        for button in (
            self.install_folder_button,
            self.install_archive_button,
            self.toggle_button,
            self.remove_button,
            self.verify_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.check_button = self._button(
            self.tr('Run its checks on this document'),
            self.tr('Run the selected extension checks on the open document'),
            self.run_checks_now,
        )
        self.hook_button = self._button(
            self.tr('Run an export hook on a file…'),
            self.tr('Run the selected extension export hook on an exported file'),
            self.run_hook_on_file,
        )
        row.addWidget(self.check_button)
        row.addWidget(self.hook_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.hooks_box = QCheckBox(self.tr('Run enabled export hooks after each export'))
        self.hooks_box.setAccessibleName(self.tr('Run enabled export hooks after each export'))
        settings = getattr(window, 'settings', None)
        if settings is not None:
            self.hooks_box.setChecked(settings.value(HOOKS_SETTING, False, type=bool))
        self.hooks_box.toggled.connect(self._save_hooks_setting)
        layout.addWidget(self.hooks_box)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setAccessibleName(self.tr('Extension status'))
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    def _button(self, text, accessible, slot):
        button = QPushButton(text)
        button.setAccessibleName(accessible)
        button.clicked.connect(lambda checked=False: slot())
        return button

    def _failed(self, message):
        self.status.setText(str(message))
        for button in (self.check_button, self.hook_button):
            button.setEnabled(True)

    def _save_hooks_setting(self, on):
        settings = getattr(self.window_ref, 'settings', None)
        if settings is not None:
            settings.setValue(HOOKS_SETTING, bool(on))

    # List ------------------------------------------------------------------------------------
    def refresh(self, select=None):
        selected = select or _selected(self.table)
        self.table.setRowCount(0)
        try:
            records = extensions.summaries()
        except Exception as exc:
            self._failed(str(exc))
            records = []
        for record in records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = [
                record['name'] or record['id'],
                record['version'] or '',
                ', '.join(record['kinds']),
                ', '.join(record['granted_scopes']),
                record['status'],
                self.tr('Yes') if record['enabled'] else self.tr('No'),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, record['id'])
                self.table.setItem(row, column, item)
            if record['id'] == selected:
                self.table.selectRow(row)
        if not records:
            self.details.setPlainText(self.tr('No extensions are installed.'))
        self._buttons()

    def _buttons(self):
        current = self.current()
        self.toggle_button.setText(
            self.tr('Disable') if current and current['enabled'] else self.tr('Enable')
        )
        has = current is not None
        for button in (self.toggle_button, self.remove_button, self.check_button, self.hook_button):
            button.setEnabled(has)
        if current:
            self.check_button.setEnabled(bool(current['checks']) and current['enabled'])
            self.hook_button.setEnabled(bool(current['export_hooks']) and current['enabled'])

    def current(self):
        identifier = _selected(self.table)
        if not identifier:
            return None
        return next((r for r in extensions.summaries() if r['id'] == identifier), None)

    def _show_details(self):
        identifier = _selected(self.table)
        if not identifier:
            self._buttons()
            return
        try:
            info = extensions.describe(identifier)
        except ByaxError as exc:
            self.details.setPlainText(exc.message)
            return
        lines = [
            f'{info["name"]} {info["version"]}  ({info["id"]})',
            info.get('description', ''),
            '',
            self.tr('Author: {0}').format(info.get('author', '')),
            self.tr('Licence: {0}').format(info.get('licence', '')),
        ]
        if info.get('homepage'):
            lines.append(self.tr('Homepage (not opened by ByAuthorX): {0}').format(info['homepage']))
        lines += ['', self.tr('Permissions granted:')]
        lines += [f'  {name}: {info["scope_descriptions"].get(name, "")}' for name in info['granted_scopes']]
        points = info['entry_points']
        if points['checks']:
            lines += ['', self.tr('Checks:')]
            lines += [f'  {c["title"]} ({c["type"]})' for c in points['checks']]
        if points['export_hooks']:
            lines += ['', self.tr('Export hooks:')]
            lines += [
                f'  {h["title"]} ({", ".join(h["formats"]) if h["formats"] else self.tr("any format")})'
                for h in points['export_hooks']
            ]
        lines += ['', self.tr('SHA-256: {0}').format(info.get('archive_sha256') or info.get('tree_sha256'))]
        if info['problems']:
            lines += ['', self.tr('Problems:')] + [f'  {p}' for p in info['problems']]
        self.details.setPlainText('\n'.join(lines))
        self._buttons()

    # Install, enable, remove -----------------------------------------------------------------
    def install_from_folder(self, path=None):
        if not path:
            path = QFileDialog.getExistingDirectory(self, self.tr('Choose the extension folder'))
        return self.install_from(path) if path else None

    def install_from_archive(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, self.tr('Choose the extension archive'), '', self.tr('Extensions (*.zip)')
            )
        return self.install_from(path) if path else None

    def install_from(self, path):
        """Show the scopes and SHA-256, and install only if the person confirms them."""
        try:
            plan = extensions.inspect_package(path)
            if not plan['compatible']:
                raise ByaxError('unsupported_version', plan['incompatible_reason'])
            answer = QMessageBox.question(
                self,
                self.tr('Install extension'),
                confirmation_text(plan),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.status.setText(self.tr('Nothing was installed.'))
                return None
            result = extensions.install(
                path,
                sha256=plan['sha256'],
                scopes=plan['requested_scopes'],
                replace=plan['installed_version'] is not None,
            )
        except ByaxError as exc:
            self._failed(exc.message)
            return None
        self.status.setText(
            self.tr('Installed {0} {1}. It is switched off until you enable it.').format(
                result['name'], result['version']
            )
        )
        self.refresh(result['id'])
        return result

    def toggle_enabled(self):
        current = self.current()
        if not current:
            return None
        try:
            result = extensions.set_enabled(current['id'], not current['enabled'])
        except ByaxError as exc:
            self._failed(exc.message)
            return None
        self.status.setText(
            self.tr('{0} is now {1}.').format(
                result['name'], self.tr('enabled') if result['enabled'] else self.tr('disabled')
            )
        )
        self.refresh(result['id'])
        return result

    def remove_selected(self):
        current = self.current()
        if not current:
            return None
        answer = QMessageBox.question(
            self,
            self.tr('Remove extension'),
            self.tr('Remove {0}? Its files are deleted from your user data folder.').format(current['name']),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return None
        try:
            result = extensions.remove(current['id'])
        except ByaxError as exc:
            self._failed(exc.message)
            return None
        self.status.setText(self.tr('Removed {0}.').format(current['name']))
        self.refresh()
        return result

    def verify_all(self):
        results = extensions.verify()
        bad = [r for r in results if not r['ok']]
        self.status.setText(
            self.tr('All {0} installed extensions match what was installed.').format(len(results))
            if not bad
            else self.tr('Problems: {0}').format('; '.join(f'{r["id"]}: {r["problems"][0]}' for r in bad))
        )
        self.refresh()
        return results

    # Running ---------------------------------------------------------------------------------
    def run_checks_now(self):
        current = self.current()
        window = self.window_ref
        if not current or window is None:
            return None
        from .checks import run_checks

        doc = window.doc
        self.check_button.setEnabled(False)
        self.status.setText(self.tr('Running the checks…'))

        def work():
            with extensions.only(current['id']):
                return run_checks(doc, ['extension'], limit=1000)

        self._pending = 'checks'
        self.task.start(work)
        return True

    def run_hook_on_file(self, path=None, hook=None):
        current = self.current()
        if not current or not current['export_hooks']:
            return None
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, self.tr('Choose the exported file'))
        if not path:
            return None
        if hook is None:
            hook = current['export_hooks'][0]
            if len(current['export_hooks']) > 1:
                hook, ok = QInputDialog.getItem(
                    self, self.tr('Export hook'), self.tr('Hook'), current['export_hooks'], 0, False
                )
                if not ok:
                    return None
        record = extensions.get(current['id'])
        entry = next(h for h in record['entry_points']['export_hooks'] if h['id'] == hook)
        fmt = Path(path).suffix.lstrip('.').lower()
        output_dir = str(Path(path).with_name(Path(path).name + '.hooks'))
        self.hook_button.setEnabled(False)
        self.status.setText(self.tr('Running the export hook…'))
        self._pending = 'hook'
        self.task.start(
            lambda: extensions.run_hook(
                record, entry, path, fmt=fmt, loss_report=None, output_dir=output_dir, overwrite=True
            )
        )
        return output_dir

    def _task_done(self, result):
        self.check_button.setEnabled(True)
        self.hook_button.setEnabled(True)
        self.last_result = result
        if getattr(self, '_pending', '') == 'checks':
            failed = [c for c in result['coverage'] if c.get('status') == 'failed']
            text = self.tr('{0} findings from the extension.').format(result['summary']['total'])
            if failed:
                text += ' ' + failed[0]['reason']
            self.status.setText(text)
        else:
            if result['status'] == 'completed':
                names = ', '.join(Path(p).name for p in result['outputs']) or self.tr('no files')
                self.status.setText(self.tr('The export hook finished and wrote {0}.').format(names))
            else:
                self.status.setText(
                    self.tr('The export hook did not finish: {0}').format(result.get('error', ''))
                )


class MacrosDialog(QDialog):
    def __init__(self, window=None):
        super().__init__(window)
        self.window_ref = window
        self.setWindowTitle(self.tr('Macros'))
        self.setAccessibleName(self.tr('Macros'))
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        note = QLabel(
            self.tr(
                'A macro is a saved sequence of ByAuthorX commands. It runs on the saved copy of this document '
                'with the same checks as the same commands elsewhere, stops at the first error, and keeps a '
                'transcript. A dry run changes nothing. Steps that need the local command line are refused here.'
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = _table(
            [
                self.tr('Macro'),
                self.tr('Version'),
                self.tr('Saved in'),
                self.tr('Steps'),
                self.tr('Needs command line'),
            ],
            self.tr('Saved macros'),
            self,
        )
        self.table.itemSelectionChanged.connect(self._show_steps)
        layout.addWidget(self.table, 2)
        self.steps_view = QPlainTextEdit()
        self.steps_view.setReadOnly(True)
        self.steps_view.setMaximumHeight(120)
        self.steps_view.setAccessibleName(self.tr('Macro steps'))
        layout.addWidget(self.steps_view)
        params_row = QHBoxLayout()
        params_row.addWidget(QLabel(self.tr('Parameters (JSON, optional)')))
        self.params = QLineEdit()
        self.params.setAccessibleName(self.tr('Macro parameters as a JSON object'))
        params_row.addWidget(self.params, 1)
        layout.addLayout(params_row)
        row = QHBoxLayout()
        self.run_button = self._button(
            self.tr('Run'), self.tr('Run the selected macro'), lambda: self.run(False)
        )
        self.dry_button = self._button(
            self.tr('Dry run'),
            self.tr('Run the selected macro without changing anything'),
            lambda: self.run(True),
        )
        self.import_button = self._button(
            self.tr('Import from file…'), self.tr('Import a macro from a JSON file'), self.import_from_file
        )
        self.remove_button = self._button(
            self.tr('Remove…'), self.tr('Remove the selected macro'), self.remove_selected
        )
        for button in (self.run_button, self.dry_button, self.import_button, self.remove_button):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.transcript = _table(
            [self.tr('Step'), self.tr('Command'), self.tr('Result'), self.tr('Detail')],
            self.tr('Macro transcript'),
            self,
        )
        self.transcript.itemSelectionChanged.connect(self._show_entry)
        layout.addWidget(self.transcript, 2)
        self.entry_view = QPlainTextEdit()
        self.entry_view.setReadOnly(True)
        self.entry_view.setAccessibleName(self.tr('Transcript step details'))
        layout.addWidget(self.entry_view, 1)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setAccessibleName(self.tr('Macro status'))
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
        self.outcome = None
        self.refresh()

    def _button(self, text, accessible, slot):
        button = QPushButton(text)
        button.setAccessibleName(accessible)
        button.clicked.connect(lambda checked=False: slot())
        return button

    def _doc(self):
        return getattr(self.window_ref, 'doc', None)

    def refresh(self, select=None):
        selected = select or _selected(self.table)
        self.table.setRowCount(0)
        rows = []
        try:
            rows += [macros.describe(m, 'user') for m in macros.user_macros().values()]
        except ByaxError as exc:
            self.status.setText(exc.message)
        if self._doc() is not None:
            rows += [macros.describe(m, 'document') for m in macros.document_macros(self._doc()).values()]
        for record in sorted(rows, key=lambda r: (r['name'], r['store'])):
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = [
                record['name'],
                str(record['version']),
                self.tr('This document') if record['store'] == 'document' else self.tr('My macros'),
                str(record['steps']),
                ', '.join(map(str, record['admin_steps'])) or self.tr('No'),
            ]
            key = (record['name'], record['store'])
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, key)
                self.table.setItem(row, column, item)
            if key == selected:
                self.table.selectRow(row)
        if not rows:
            self.steps_view.setPlainText(self.tr('No macros are saved. Import one from a JSON file.'))
        self._buttons()

    def _buttons(self):
        has = _selected(self.table) is not None
        for button in (self.run_button, self.dry_button, self.remove_button):
            button.setEnabled(has)

    def selected_macro(self):
        key = _selected(self.table)
        if not key:
            return None
        name, store = key
        macro, _ = macros.find(name, doc=self._doc(), config_dir=None, store=store)
        return macro, store

    def _show_steps(self):
        found = None
        try:
            found = self.selected_macro()
        except ByaxError as exc:
            self.steps_view.setPlainText(exc.message)
        if found:
            macro = found[0]
            self.steps_view.setPlainText(
                '\n'.join(
                    f'{i}. {step["label"]}: {step["command"]} {json.dumps(step["arguments"], ensure_ascii=False)}'
                    for i, step in enumerate(macro['steps'], 1)
                )
            )
        self._buttons()

    def _show_entry(self):
        rows = self.transcript.selectionModel().selectedRows()
        if not rows or not self.outcome:
            return
        entry = self.outcome['transcript'][rows[0].row()]
        self.entry_view.setPlainText(json.dumps(entry, ensure_ascii=False, indent=2, default=str))

    # Running ---------------------------------------------------------------------------------
    def run(self, dry_run, *, params=None):
        found = self.selected_macro()
        if not found:
            return None
        macro, _ = found
        try:
            supplied = params if params is not None else self._parameters()
        except (TypeError, ValueError) as exc:
            self.status.setText(str(exc))
            return None
        runner = getattr(self.window_ref, 'run_macro_on_open_document', None)
        if runner is None:
            self.status.setText(self.tr('Open a saved ByAuthorX document first.'))
            return None
        try:
            outcome = runner(macro, supplied, dry_run)
        except ByaxError as exc:
            self.status.setText(exc.message)
            return None
        self.show_outcome(outcome)
        return outcome

    def _parameters(self):
        text = self.params.text().strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise ValueError(self.tr('Parameters must be a JSON object: {0}').format(exc)) from exc
        if not isinstance(value, dict):
            raise TypeError(self.tr('Parameters must be a JSON object.'))
        return value

    def show_outcome(self, outcome):
        self.outcome = outcome
        self.transcript.setRowCount(0)
        for entry in outcome['transcript']:
            row = self.transcript.rowCount()
            self.transcript.insertRow(row)
            detail = (
                entry.get('error', {}).get('message')
                or entry.get('note')
                or (entry.get('summary') or {}).get('validation', '')
                or ''
            )
            for column, text in enumerate(
                [str(entry['step']), entry['command'], entry['status'], str(detail)]
            ):
                self.transcript.setItem(row, column, QTableWidgetItem(text))
        if outcome['status'] == 'completed':
            text = self.tr('{0} of {1} steps finished.').format(outcome['steps_run'], outcome['steps_total'])
            if outcome['dry_run']:
                text = self.tr('Dry run: nothing was changed. ') + text
        else:
            text = self.tr('Stopped at step {0}: {1}').format(
                outcome['failed_step'], outcome['error']['message']
            )
        self.status.setText(text)
        if self.transcript.rowCount():
            self.transcript.selectRow(0)

    # Import and remove -----------------------------------------------------------------------
    def import_from_file(self, path=None, store=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, self.tr('Import macro'), '', self.tr('Macros (*.json)')
            )
        if not path:
            return None
        try:
            macro = macros.validate_macro(json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, ValueError, ByaxError) as exc:
            self.status.setText(exc.message if isinstance(exc, ByaxError) else str(exc))
            return None
        if store is None:
            box = QMessageBox(self)
            box.setWindowTitle(self.tr('Save macro'))
            box.setText(self.tr('Where should {0} be saved?').format(macro['name']))
            mine = box.addButton(self.tr('My macros'), QMessageBox.AcceptRole)
            here = box.addButton(self.tr('This document'), QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            here.setEnabled(self._doc() is not None and self.window_ref.path is not None)
            box.exec()
            store = (
                'user' if box.clickedButton() is mine else 'document' if box.clickedButton() is here else None
            )
        if store is None:
            return None
        try:
            self._save(macro, store)
        except ByaxError as exc:
            self.status.setText(exc.message)
            return None
        self.status.setText(self.tr('Saved macro {0}.').format(macro['name']))
        self.refresh((macro['name'], store))
        return macro

    def _save(self, macro, store):
        if store == 'user':
            saved = macros.user_macros()
            saved[macro['name']] = macro
            macros._write_user(saved, None)
            return
        window = self.window_ref
        stored = macros.document_macros(window.doc)
        stored[macro['name']] = macro
        window.commit([{'type': 'set_settings', 'values': {'macros': stored}}])

    def remove_selected(self):
        key = _selected(self.table)
        if not key:
            return None
        name, store = key
        answer = QMessageBox.question(
            self,
            self.tr('Remove macro'),
            self.tr('Remove the macro {0}?').format(name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return None
        try:
            if store == 'user':
                saved = macros.user_macros()
                saved.pop(name, None)
                macros._write_user(saved, None)
            else:
                remaining = macros.document_macros(self.window_ref.doc)
                remaining.pop(name, None)
                self.window_ref.commit([{'type': 'set_settings', 'values': {'macros': remaining}}])
        except ByaxError as exc:
            self.status.setText(exc.message)
            return None
        self.status.setText(self.tr('Removed {0}.').format(name))
        self.refresh()
        return name


class ExtensionPanels:
    """Main-window entry points (mixed into ``MainWindow``)."""

    def show_extensions(self):
        dialog = ExtensionsDialog(self)
        dialog.exec()
        return dialog

    def show_macros(self):
        dialog = MacrosDialog(self)
        dialog.exec()
        return dialog

    def run_macro_on_open_document(self, macro, params=None, dry_run=False):
        """Run a macro through ``cli.execute`` on the saved file, then reload what it changed.

        The window's own file lock is released for the run (each mutating step takes the lock itself) and
        taken again afterwards, so no other process can slip in; a run that changed the file reloads it.
        """
        if not self.path or self.path.suffix.lower() != '.byax':
            raise ByaxError('invalid_input', self.tr('Save this document as a ByAuthorX file first.'))
        if not self.flush_edits() or not self.save(wait=True) or self.dirty:
            raise ByaxError(
                'invalid_input', self.tr('The document could not be saved, so the macro did not run.')
            )
        path = self.path
        before = self.file_stamp(path)
        self.release_lock()
        try:
            outcome = macros.run_macro(macro, params=params, document=str(path), dry_run=dry_run)
        finally:
            lock = storage.DocumentLock(path, owner=self.owner)
            lock.acquire()
            self.lock = lock
        if self.file_stamp(path) != before:
            self.doc = storage.load_document(path)
            self.dirty = False
            self._disk_stamp = self.file_stamp(path)
            self._undo.clear()
            self._redo.clear()
            self.refresh()
        return outcome

    def run_export_hooks_after(self, path, fmt, report):
        """Run enabled export hooks on a finished export when the person turned that on; returns results."""
        if not self.settings.value(HOOKS_SETTING, False, type=bool) or not extensions.hooks_for(fmt):
            return []
        loss_report = {'losses': report.get('losses', []), 'fidelity': report.get('fidelity')}
        return extensions.run_export_hooks(
            path, fmt=fmt, loss_report=loss_report, output_dir=str(path) + '.hooks'
        )
