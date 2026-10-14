# ruff: noqa: BLE001
"""Local library management and keyboard-accessible citation picking."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LibraryPanels:
    def library_inspector_widget(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        explanation = QLabel(self.tr('Search local research and manage citation sources.'))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        for label, slot in (
            ('Libraries…', self.library_panel),
            ('Cite selected text…', self.cite_selection),
            ('Citation style…', self.citation_settings),
            ('Import sources…', self.import_sources),
            ('Export sources…', self.export_sources),
        ):
            button = QPushButton(self.tr(label))
            button.clicked.connect(slot)
            layout.addWidget(button)
        layout.addStretch()
        return panel

    def citation_settings(self):
        current = self.doc.get('settings', {}).get('citations', {})
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Citation style'))
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        style = QComboBox()
        style.setObjectName('citationStyle')
        for label, value in (('APA', 'apa'), ('MLA', 'mla'), ('Chicago', 'chicago')):
            style.addItem(self.tr(label), value)
        style.setCurrentIndex(max(0, style.findData(current.get('style', 'apa'))))
        form.addRow(self.tr('Style'), style)
        presentation = QComboBox()
        presentation.setObjectName('citationPresentation')
        for label, value in (
            ('In-text', 'in-text'),
            ('Footnotes', 'footnotes'),
            ('Endnotes', 'endnotes'),
            ('Numbered references', 'numbered'),
        ):
            presentation.addItem(self.tr(label), value)
        presentation.setCurrentIndex(max(0, presentation.findData(current.get('presentation', 'in-text'))))
        form.addRow(self.tr('Presentation'), presentation)
        bibliography = QCheckBox(self.tr('Include bibliography'))
        bibliography.setObjectName('citationBibliography')
        bibliography.setChecked(current.get('bibliography', True))
        form.addRow(bibliography)
        layout.addLayout(form)
        note = QLabel(
            self.tr('Applies to page preview and exports. Citation sources and selected text stay unchanged.')
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.commit(
                [
                    {
                        'type': 'set_settings',
                        'values': {
                            'citations': {
                                **current,
                                'style': style.currentData(),
                                'presentation': presentation.currentData(),
                                'bibliography': bibliography.isChecked(),
                            }
                        },
                    }
                ]
            )

    def refresh_attached_libraries(self):
        """Opening a document checks local definitions, never its advisory path hints."""
        from .libraries import LibraryRegistry

        registry = LibraryRegistry()
        missing = []
        for attachment in self.doc.get('settings', {}).get('libraries', []):
            try:
                library = registry.get(attachment['library_id'])
                if not library or not all(Path(root).is_dir() for root in library['roots']):
                    missing.append(attachment.get('name', attachment['library_id']))
                    continue
                self._index_library(library)
            except Exception:
                missing.append(attachment.get('name', attachment.get('library_id', 'Library')))
        if missing:
            self.status_label.setText(
                self.tr('Unavailable libraries: {0}. Relink in Tools → Libraries.').format(', '.join(missing))
            )

    def _index_library(self, library, rebuild=False, done=None):
        from .libraries import LibraryIndex
        from .library_checks import invalidate_library_findings

        document_id = self.doc['id']

        def completed(result):
            if self.doc['id'] == document_id:
                invalidate_library_findings(self.doc, library['library_id'])
                self.update_sidebar()
            if done:
                done(result)

        return self.start_job(
            'library_index',
            {'library_id': library['library_id'], 'revision': self.doc['revision']},
            lambda payload, cancel_event: LibraryIndex(library).refresh(
                cancel_event=cancel_event, rebuild=rebuild
            ),
            completed,
        )

    def library_panel(self):
        from .libraries import LibraryIndex, LibraryRegistry

        registry = LibraryRegistry()
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Libraries'))
        dialog.resize(760, 540)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                self.tr(
                    'Local keyword search · No data leaves this computer. Removing a library never deletes its files.'
                )
            )
        )
        libraries = QListWidget()
        libraries.setAccessibleName(self.tr('Libraries and availability'))
        layout.addWidget(libraries)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setAccessibleName(self.tr('Index status and file results'))
        layout.addWidget(details)
        role = QComboBox()
        role.addItems(['supplementary', 'exclusive', 'open'])
        role.setAccessibleName(self.tr('Reference authority when attaching'))
        layout.addWidget(role)

        def reload():
            libraries.clear()
            attached = {a['library_id']: a for a in self.doc.get('settings', {}).get('libraries', [])}
            local = {lib['library_id']: lib for lib in registry.list()}
            for library_id in dict.fromkeys([*local, *attached]):
                entry = local.get(library_id) or attached[library_id]
                label = entry.get('name', library_id)
                label += ' · attached' if library_id in attached else ''
                if library_id not in local:
                    label += ' · unavailable'
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, library_id)
                libraries.addItem(item)

        def selected():
            return libraries.currentItem().data(Qt.UserRole) if libraries.currentItem() else None

        def show_status():
            if not selected():
                return
            try:
                library = registry.get(selected())
                status = LibraryIndex(library).status() if library else {'state': 'unavailable'}
                details.setPlainText(json.dumps(status, indent=2, ensure_ascii=False))
            except Exception as exc:
                details.setPlainText(str(exc))

        def attach(library):
            records = copy.deepcopy(self.doc.get('settings', {}).get('libraries', []))
            records = [r for r in records if r['library_id'] != library['library_id']]
            records.append(
                {
                    'library_id': library['library_id'],
                    'name': library['name'],
                    'path_hint': Path(library['roots'][0]).name,
                    'role': role.currentText(),
                }
            )
            self.commit([{'type': 'set_settings', 'values': {'libraries': records}}])
            reload()

        def add():
            folder = QFileDialog.getExistingDirectory(dialog, self.tr('Choose library folder'))
            if not folder:
                return
            name, ok = QInputDialog.getText(
                dialog, self.tr('Library name'), self.tr('Name'), text=Path(folder).name
            )
            if ok and name.strip():
                library = registry.create(name.strip(), [folder])
                attach(library)
                self._index_library(library)

        def relink():
            if not selected():
                return
            folder = QFileDialog.getExistingDirectory(dialog, self.tr('Relink library folder'))
            if folder:
                attachment = next(
                    (
                        a
                        for a in self.doc.get('settings', {}).get('libraries', [])
                        if a['library_id'] == selected()
                    ),
                    {},
                )
                registry.relink(selected(), [folder], name=attachment.get('name'))
                self._index_library(registry.get(selected()))
                reload()

        def remove():
            if not selected():
                return
            library_id = selected()
            records = [
                r for r in self.doc.get('settings', {}).get('libraries', []) if r['library_id'] != library_id
            ]
            if self.commit([{'type': 'set_settings', 'values': {'libraries': records}}]):
                if any(lib['library_id'] == library_id for lib in registry.list()):
                    registry.remove(library_id)
                reload()

        def guarded(fn):
            def run():
                try:
                    fn()
                except Exception as exc:
                    self.error(exc)

            return run

        buttons = QHBoxLayout()
        for title, callback in (
            ('Add…', add),
            ('Attach', lambda: attach(registry.get(selected())) if selected() else None),
            ('Relink…', relink),
            ('Refresh', lambda: self._index_library(registry.get(selected())) if selected() else None),
            (
                'Rebuild',
                lambda: self._index_library(registry.get(selected()), rebuild=True) if selected() else None,
            ),
            ('Remove', remove),
        ):
            button = QPushButton(self.tr(title))
            button.clicked.connect(guarded(callback))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(
            QLabel(self.tr('Progress and cancellation are available in Tools → Background jobs.'))
        )
        citation_style = QPushButton(self.tr('Citation style…'))
        citation_style.clicked.connect(self.citation_settings)
        layout.addWidget(citation_style)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        libraries.currentRowChanged.connect(show_status)
        timer = QTimer(dialog)
        timer.setInterval(1500)
        timer.timeout.connect(show_status)
        timer.start()
        reload()
        dialog.exec()

    def import_sources(self):
        from .citations import import_sources

        path, _ = QFileDialog.getOpenFileName(
            self, self.tr('Import sources'), '', 'Sources (*.json *.bib *.ris);;All files (*)'
        )
        if not path:
            return
        try:
            format_name = {'.bib': 'bibtex', '.ris': 'ris'}.get(Path(path).suffix.lower(), 'csl-json')
            if Path(path).stat().st_size > 8 * 1024 * 1024:
                raise ValueError('Source import exceeds the 8 MiB limit.')
            records = import_sources(
                Path(path).read_text(encoding='utf-8-sig'), format_name, self.doc.get('references', {})
            )
            self.commit(
                [{'type': 'add_reference', 'id': record['id'], 'values': record} for record in records]
            )
        except Exception as exc:
            self.error(exc)

    def export_sources(self):
        from .citations import export_sources

        path, selected = QFileDialog.getSaveFileName(
            self, self.tr('Export sources'), '', 'CSL-JSON (*.json);;BibTeX (*.bib)'
        )
        if path:
            try:
                format_name = 'bibtex' if 'BibTeX' in selected else 'csl-json'
                Path(path).write_text(
                    export_sources(self.doc.get('references', {}), format_name), encoding='utf-8'
                )
            except Exception as exc:
                self.error(exc)

    def cite_selection(self):
        from .citations import source_from_hit
        from .libraries import LibraryIndex, LibraryRegistry

        if not self.flush_edits():
            return
        targets = [(t, start, end) for t, start, end in self.editor.selection_targets() if end > start]
        if not targets:
            self.status_label.setText(self.tr('Select text to cite first.'))
            return
        revision = self.doc['revision']
        document_id = self.doc['id']
        existing = copy.deepcopy(self.doc.get('references', {}))
        attachments = copy.deepcopy(self.doc.get('settings', {}).get('libraries', []))
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Cite selected text'))
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        query = QLineEdit()
        query.setPlaceholderText(self.tr('Search sources and attached libraries'))
        query.setAccessibleName(self.tr('Citation search'))
        layout.addWidget(query)
        search = QPushButton(self.tr('Search'))
        layout.addWidget(search)
        results = QListWidget()
        results.setAccessibleName(self.tr('Citation matches'))
        layout.addWidget(results)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setAccessibleName(self.tr('Source passage and locator'))
        layout.addWidget(preview)
        manual = QPushButton(self.tr('Enter source manually / paste DOI or URL…'))
        layout.addWidget(manual)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(self.tr('Insert citation'))
        buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        layout.addWidget(buttons)
        active = {'open': True, 'generation': 0}

        def context_current():
            selection = [(t, start, end) for t, start, end in self.editor.selection_targets() if end > start]
            return self.doc['id'] == document_id and self.doc['revision'] == revision and selection == targets

        def add_result(source, locator=None):
            title = source.get('csl', {}).get('title') or source.get('title') or source['id']
            stale = source.get('verification') == 'stale'
            item = QListWidgetItem(
                str(title) + (self.tr(' · stale — refresh library before citing') if stale else '')
            )
            item.setData(Qt.UserRole, (source, locator or {}))
            results.addItem(item)

        def search_now():
            active['generation'] += 1
            generation = active['generation']
            phrase = query.text().strip()
            results.clear()
            for source_id, record in existing.items():
                if not phrase or phrase.casefold() in json.dumps(record, ensure_ascii=False).casefold():
                    add_result(dict(record, id=source_id))
            if not phrase or not context_current():
                return

            def work():
                hits = []
                registry = LibraryRegistry()
                for attachment in attachments:
                    try:
                        library = registry.get(attachment['library_id'])
                        if library:
                            response = LibraryIndex(library).search(phrase, limit=20)
                            hits.extend(
                                response.get('results', response.get('hits', []))
                                if isinstance(response, dict)
                                else response
                            )
                    except Exception as exc:
                        logging.getLogger(__name__).info('Library search unavailable: %s', exc)
                return hits

            def finished(hits):
                if not active['open'] or active['generation'] != generation:
                    return
                if not context_current():
                    results.clear()
                    preview.setPlainText(
                        self.tr('The document or selection changed. Close this picker and cite again.')
                    )
                    buttons.button(QDialogButtonBox.Ok).setEnabled(False)
                    return
                for hit in hits:
                    source = source_from_hit(hit)
                    if hit.get('stale'):
                        source['verification'] = 'stale'
                    add_result(source, {**hit.get('locator', {}), 'chunk': hit['chunk_id']})

            self.background(self.tr('Searching local libraries…'), work, finished)

        def show_preview():
            item = results.currentItem()
            buttons.button(QDialogButtonBox.Ok).setEnabled(
                item is not None
                and context_current()
                and item.data(Qt.UserRole)[0].get('verification') != 'stale'
            )
            if item:
                source, locator = item.data(Qt.UserRole)
                preview.setPlainText(
                    str(source.get('excerpt', source.get('text', '')))
                    + '\n\n'
                    + json.dumps(
                        {
                            'source': source.get('csl', {}),
                            'locator': locator,
                            'verification': source.get('verification', 'manual'),
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )

        def manual_source():
            import uuid

            text, ok = QInputDialog.getText(
                dialog, self.tr('Manual source'), self.tr('Title, DOI, or URL (no online lookup)')
            )
            if ok and text.strip():
                value = text.strip()
                csl = {'type': 'document', 'title': value}
                if value.startswith(('https://', 'http://')):
                    csl['URL'] = value
                elif value.startswith('10.'):
                    csl['DOI'] = value
                add_result(
                    {
                        'id': 'src_' + uuid.uuid4().hex,
                        'csl': csl,
                        'verification': 'manual',
                        'import': {'origin': 'manual'},
                    }
                )
                results.setCurrentRow(results.count() - 1)

        search.clicked.connect(search_now)
        query.returnPressed.connect(search_now)
        results.currentRowChanged.connect(show_preview)
        manual.clicked.connect(manual_source)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        search_now()
        query.setFocus()
        accepted = dialog.exec() == QDialog.Accepted
        active['open'] = False
        if not accepted or not results.currentItem():
            return
        if not context_current():
            self.error(
                self.tr(
                    'The document or selection changed while choosing a source. Select the text and cite again.'
                )
            )
            return
        source, locator = results.currentItem().data(Qt.UserRole)
        if source.get('verification') == 'stale':
            self.error(
                self.tr(
                    'This source is stale. Refresh the library and choose a current passage before citing.'
                )
            )
            return
        operations = []
        if existing.get(source['id']) != source:
            operations.append({'type': 'add_reference', 'id': source['id'], 'values': source})
        operations.extend(
            {
                'type': 'cite',
                'target': target,
                'start': start,
                'end': end,
                'source': source['id'],
                'locator': locator,
                'kind': 'paraphrase',
            }
            for target, start, end in targets
        )
        self.commit(operations)
