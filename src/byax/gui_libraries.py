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


def format_extracted(page):
    """Text and locators of one ``LibraryIndex.read`` page, exactly as indexed."""
    blocks = []
    for passage in page['results']:
        header = json.dumps(passage['locator'], ensure_ascii=False, sort_keys=True)
        note = '  [shown truncated by the read limit]' if passage.get('truncated') else ''
        blocks.append(f'[{passage["ordinal"]}] {header}{note}\n{passage["text"]}')
    return '\n\n'.join(blocks)


class ExtractedTextDialog(QDialog):
    """Read-only view of what the parser extracted from each file and how it was chunked.

    Nothing here is parsed again: it reads the index, so it shows what search, citation and
    retrieval will actually see (including text a parser could not extract, which is simply absent).
    """

    PAGE_CHARS = 20000

    def __init__(self, parent, library, files, cache_dir=None, *, focus=None, on_insert=None):
        """``focus`` opens the reader at a citation: ``{document_id, chunk, span}`` from
        ``grounding.locate_record``. ``on_insert`` adds an "Insert as citation" button."""
        from .libraries import LibraryIndex

        super().__init__(parent)
        self.setWindowTitle(self.tr('Extracted text') if focus is None else self.tr('Cited source'))
        self.resize(760, 560)
        self.index = LibraryIndex(library, cache_dir=cache_dir)
        self.offset = 0
        self.focus = focus
        self.focused = None
        layout = QVBoxLayout(self)
        note = QLabel(
            self.tr(
                'This is the text and the locators exactly as indexed from the file. Search, citations and AI passages use only this text. It is read-only.'
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.files = QComboBox()
        self.files.setObjectName('extractedFile')
        self.files.setAccessibleName(self.tr('File to preview'))
        for item in files:
            self.files.addItem(f'{item["path"]} · {item["status"]}', item)
        layout.addWidget(self.files)
        self.summary = QLabel()
        self.summary.setObjectName('extractedSummary')
        layout.addWidget(self.summary)
        self.view = QPlainTextEdit()
        self.view.setObjectName('extractedText')
        self.view.setReadOnly(True)
        self.view.setAccessibleName(self.tr('Extracted text and locators'))
        layout.addWidget(self.view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.more = buttons.addButton(self.tr('Load more'), QDialogButtonBox.ActionRole)
        self.more.clicked.connect(lambda: self.load(append=True))
        self.insert_button = None
        if on_insert is not None:
            self.insert_button = buttons.addButton(self.tr('Insert as citation'), QDialogButtonBox.ActionRole)
            self.insert_button.setObjectName('insertCitation')
            self.insert_button.setToolTip(
                self.tr('Cite the selected document text with this source, locator and quotation')
            )
            self.insert_button.clicked.connect(on_insert)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if focus is not None:
            index = next(
                (
                    i
                    for i in range(self.files.count())
                    if self.files.itemData(i).get('document_id') == focus['document_id']
                ),
                -1,
            )
            if index < 0:
                self.files.addItem(
                    focus.get('title') or focus['document_id'], {**focus, 'path': '', 'status': ''}
                )
                index = self.files.count() - 1
            self.files.setCurrentIndex(index)
        self.files.currentIndexChanged.connect(lambda _: self.load())
        self.load()

    def load(self, append=False):
        from .errors import ByaxError

        item = self.files.currentData()
        focus = (
            self.focus
            if self.focus and item and item.get('document_id') == self.focus['document_id']
            else None
        )
        if not append:
            # A citation opens at its chunk: reading starts there and "Load more" continues after it.
            self.offset = int(focus['chunk'].get('ordinal', 0)) if focus else 0
            self.view.clear()
        self.more.setEnabled(False)
        if not item or not item.get('document_id'):
            self.summary.setText(self.tr('This file has no indexed text. Refresh the library to index it.'))
            return
        try:
            page = self.index.read(item['document_id'], limit=self.PAGE_CHARS, offset=self.offset)
        except ByaxError as exc:
            self.summary.setText(str(exc))
            return
        text = format_extracted(page)
        self.view.setPlainText((self.view.toPlainText() + '\n\n' if append else '') + text)
        self.offset += len(page['results'])
        self.more.setEnabled(bool(page['continuation']))
        self.summary.setText(
            self.tr('{0} of {1} passages shown{2}').format(
                self.offset, page['total'], self.tr(' · file changed since indexing') if page['stale'] else ''
            )
        )
        if focus and not append:
            self._show_focus(page, focus)

    def _show_focus(self, page, focus):
        """Select the cited span inside the first shown passage (the cited chunk)."""
        from PySide6.QtGui import QTextCursor

        first = page['results'][0] if page['results'] else None
        if not first or first['chunk_id'] != focus['chunk']['chunk_id']:
            return
        header = json.dumps(first['locator'], ensure_ascii=False, sort_keys=True)
        note = '  [shown truncated by the read limit]' if first.get('truncated') else ''
        base = len(f'[{first["ordinal"]}] {header}{note}\n')
        start, end = focus.get('span') or [0, 0]
        start, end = max(0, min(start, len(first['text']))), max(0, min(end, len(first['text'])))
        cursor = QTextCursor(self.view.document())
        cursor.setPosition(base + start)
        cursor.setPosition(base + (end if end > start else len(first['text'])), QTextCursor.KeepAnchor)
        self.view.setTextCursor(cursor)
        self.view.centerCursor()
        locator = {**first['locator'], 'chunk': first['chunk_id']}
        self.focused = {
            'document_id': page['document_id'],
            'chunk_id': first['chunk_id'],
            'locator': locator,
            'selected': cursor.selectedText().replace(' ', '\n'),
        }
        where = describe_locator(locator)
        self.summary.setText(self.tr('Opened at {0}').format(where) + ' · ' + self.summary.text())


def describe_locator(locator):
    """Short human description of a library locator: page, block, paragraph or offset."""
    locator = locator or {}
    parts = []
    if locator.get('page') is not None:
        parts.append(f'page {locator["page"]}')
    if locator.get('heading_path'):
        parts.append(' > '.join(str(part) for part in locator['heading_path']))
    if locator.get('block'):
        parts.append(f'block {locator["block"]}')
    elif locator.get('ordinal') is not None and locator.get('page') is None:
        parts.append(f'paragraph {int(locator["ordinal"]) + 1}')
    if locator.get('start') is not None and locator.get('end') is not None:
        parts.append(f'characters {locator["start"]}–{locator["end"]}')
    return ', '.join(parts) or 'the cited passage'


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
        from .csl_styles import list_styles

        for installed in list_styles()['styles']:
            if not installed['built_in']:
                style.addItem(installed.get('title') or installed['name'], installed['name'])
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
        from .embeddings import embedder_locality
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
        embedder = QLabel()
        embedder.setObjectName('libraryEmbedder')
        embedder.setWordWrap(True)
        layout.addWidget(embedder)
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
                info = embedder_locality(library) if library else None
                embedder.setText(
                    self.tr('Semantic search: off. Keyword search runs on this computer.')
                    if info is None
                    else self.tr('Embedder: {0} · {1}').format(info['provider'], info['label'])
                )
            except Exception as exc:
                details.setPlainText(str(exc))

        def preview_text():
            library = registry.get(selected()) if selected() else None
            if not library:
                return
            files = [f for f in LibraryIndex(library).status()['files'] if f.get('document_id')]
            if not files:
                self.status_label.setText(self.tr('No indexed files to preview yet.'))
                return
            ExtractedTextDialog(dialog, library, files).exec()

        def enable_semantic():
            library = registry.get(selected()) if selected() else None
            if library:
                self.enable_semantic_search(library, lambda result: show_status())

        def disable_semantic():
            library = registry.get(selected()) if selected() else None
            if library and library.get('embedding'):
                self.disable_semantic_search(library)
                show_status()

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
            ('Preview extracted text…', preview_text),
            ('Enable on-device semantic search…', enable_semantic),
            ('Turn off semantic search', disable_semantic),
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
            self, self.tr('Export sources'), '', 'CSL-JSON (*.json);;BibTeX (*.bib);;RIS (*.ris)'
        )
        if path:
            try:
                format_name = 'bibtex' if 'BibTeX' in selected else 'ris' if 'RIS' in selected else 'csl-json'
                Path(path).write_text(
                    export_sources(self.doc.get('references', {}), format_name), encoding='utf-8'
                )
            except Exception as exc:
                self.error(exc)

    def cite_selection(self):
        from .citations import source_from_hit
        from .libraries import LibraryIndex, LibraryRegistry
        from .library_checks import source_texts
        from .model import find_object
        from .quote_match import infer_kind

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
        kind_row = QHBoxLayout()
        kind_row.addWidget(QLabel(self.tr('This citation is a')))
        kind = QComboBox()
        kind.setObjectName('citationKind')
        kind.setAccessibleName(self.tr('Evidence kind: quotation, paraphrase or inference'))
        for label, value in (
            (self.tr('Quotation'), 'quotation'),
            (self.tr('Paraphrase'), 'paraphrase'),
            (self.tr('Inference'), 'inference'),
        ):
            kind.addItem(label, value)
        kind_row.addWidget(kind)
        kind_reason = QLabel()
        kind_reason.setObjectName('citationKindReason')
        kind_reason.setWordWrap(True)
        kind_row.addWidget(kind_reason, 1)
        layout.addLayout(kind_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(self.tr('Insert citation'))
        buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        layout.addWidget(buttons)
        active = {'open': True, 'generation': 0, 'kind_overridden': False}
        selected_texts = [find_object(self.doc, target)['text'][start:end] for target, start, end in targets]

        def suggest_kind(source=None):
            """Preselect quotation when the text is quoted or found in the source, unless the user chose."""
            if active['kind_overridden']:
                return
            texts = source_texts(source) if source else []
            guesses = [infer_kind(text, texts) for text in selected_texts]
            guess = next((g for g in guesses if g['kind'] == 'paraphrase'), guesses[0])
            kind.setCurrentIndex(kind.findData(guess['kind']))
            kind_reason.setText(self.tr(guess['reason']))

        kind.activated.connect(lambda _: active.update(kind_overridden=True))
        suggest_kind()

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
                from .retrieval import search_library

                hits, suggestions = [], {}
                registry = LibraryRegistry()
                for attachment in attachments:
                    try:
                        library = registry.get(attachment['library_id'])
                        if library:
                            # Same query syntax and method selection as `byax library-search`.
                            response = search_library(LibraryIndex(library), phrase, limit=20)
                            hits.extend(
                                response.get('results', response.get('hits', []))
                                if isinstance(response, dict)
                                else response
                            )
                            for term, options in (response.get('suggestions') or {}).items():
                                suggestions.setdefault(term, []).extend(o['term'] for o in options)
                    except Exception as exc:
                        logging.getLogger(__name__).info('Library search unavailable: %s', exc)
                return {'hits': hits, 'suggestions': suggestions}

            def finished(found):
                hits = found['hits'] if isinstance(found, dict) else found
                suggestions = found.get('suggestions', {}) if isinstance(found, dict) else {}
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
                if not hits and suggestions:
                    preview.setPlainText(
                        self.tr('Did you mean: ')
                        + '; '.join(
                            f'{term} → {", ".join(dict.fromkeys(options))}'
                            for term, options in suggestions.items()
                        )
                    )

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
                suggest_kind(source)
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
        kind_choice = kind.currentData()
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
                'kind': kind_choice,
            }
            for target, start, end in targets
        )
        self.commit(operations)
