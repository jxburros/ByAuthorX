"""Codex panel and project find dialog (context engine 8.1, 8.2).

Both are thin views over :mod:`byax.codex` and :mod:`byax.project_index`: every edit is a canonical
operation committed through the window (so it is undoable), and searches run off the UI thread
through ``window.background``. Everything here works without any model or network.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import codex, project_index, series
from .model import new_id

KIND_LABELS = {'character': 'Character', 'place': 'Place', 'item': 'Item', 'term': 'Term'}
HEATMAP_COLUMNS = 12


def _lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def _cell(text, data=None, tip=None):
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        item.setData(Qt.UserRole, data)
    if tip:
        item.setToolTip(tip)
    return item


class CodexEntryDialog(QDialog):
    """Edit one entry: names, matching rules, description, traits and the hide-from-AI toggles."""

    def __init__(self, parent=None, entry=None):
        super().__init__(parent)
        self.original = copy.deepcopy(entry) if entry else None
        self.setWindowTitle(self.tr('Edit codex entry') if entry else self.tr('New codex entry'))
        entry = entry or {'kind': 'character', 'name': '', 'traits': []}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(entry.get('name', ''))
        self.name.setAccessibleName(self.tr('Entry name'))
        form.addRow(self.tr('Name'), self.name)
        self.kind = QComboBox()
        self.kind.setAccessibleName(self.tr('Entry kind'))
        for key, label in KIND_LABELS.items():
            self.kind.addItem(self.tr(label), key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(entry.get('kind', 'character'))))
        form.addRow(self.tr('Kind'), self.kind)
        self.aliases = QPlainTextEdit('\n'.join(entry.get('aliases', [])))
        self.aliases.setAccessibleName(self.tr('Aliases, one per line'))
        self.aliases.setFixedHeight(70)
        form.addRow(self.tr('Aliases (one per line)'), self.aliases)
        self.excluded = QPlainTextEdit('\n'.join(entry.get('excluded', [])))
        self.excluded.setAccessibleName(self.tr('Excluded phrases, one per line'))
        self.excluded.setToolTip(self.tr('Occurrences inside these phrases are not counted as mentions.'))
        self.excluded.setFixedHeight(50)
        form.addRow(self.tr('Excluded phrases'), self.excluded)
        self.case_sensitive = QCheckBox(self.tr('Match letter case'))
        self.case_sensitive.setChecked(entry.get('case_sensitive', codex.DEFAULT_CASE_SENSITIVE))
        form.addRow('', self.case_sensitive)
        self.hidden = QCheckBox(self.tr('Hide this entry from AI'))
        self.hidden.setToolTip(
            self.tr('Hidden entries and their traits are never sent to a model or shown to MCP clients.')
        )
        self.hidden.setChecked(bool(entry.get('hide_from_ai')))
        form.addRow('', self.hidden)
        self.description = QPlainTextEdit(entry.get('description', ''))
        self.description.setAccessibleName(self.tr('Description'))
        self.description.setFixedHeight(80)
        form.addRow(self.tr('Description'), self.description)
        layout.addLayout(form)
        layout.addWidget(QLabel(self.tr('Traits')))
        self.traits = QTableWidget(0, 3)
        self.traits.setAccessibleName(self.tr('Traits'))
        self.traits.setHorizontalHeaderLabels([self.tr('Trait'), self.tr('Value'), self.tr('Hidden from AI')])
        self.traits.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for trait in entry.get('traits', []):
            self.add_trait(trait)
        layout.addWidget(self.traits)
        row = QHBoxLayout()
        add = QPushButton(self.tr('Add trait'))
        add.setAccessibleName(self.tr('Add trait'))
        add.clicked.connect(lambda: self.add_trait())
        remove = QPushButton(self.tr('Remove trait'))
        remove.setAccessibleName(self.tr('Remove selected trait'))
        remove.clicked.connect(lambda: self.traits.removeRow(self.traits.currentRow()))
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_trait(self, trait=None):
        trait = trait or {'name': '', 'value': ''}
        row = self.traits.rowCount()
        self.traits.insertRow(row)
        name = QTableWidgetItem(trait.get('name', ''))
        name.setData(Qt.UserRole, trait.get('id'))
        self.traits.setItem(row, 0, name)
        self.traits.setItem(row, 1, QTableWidgetItem(trait.get('value', '')))
        hidden = QTableWidgetItem()
        hidden.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        hidden.setCheckState(Qt.Checked if trait.get('hide_from_ai') else Qt.Unchecked)
        hidden.setData(Qt.AccessibleTextRole, self.tr('Hidden from AI'))
        self.traits.setItem(row, 2, hidden)
        return row

    def values(self):
        result = {
            'kind': self.kind.currentData(),
            'name': ' '.join(self.name.text().split()),
            'aliases': list(dict.fromkeys(_lines(self.aliases.toPlainText()))),
            'excluded': list(dict.fromkeys(_lines(self.excluded.toPlainText()))),
            'case_sensitive': self.case_sensitive.isChecked(),
            'hide_from_ai': self.hidden.isChecked(),
            'description': self.description.toPlainText(),
        }
        return result

    def trait_rows(self):
        rows = []
        for row in range(self.traits.rowCount()):
            name = self.traits.item(row, 0)
            value = self.traits.item(row, 1)
            if name is None or not name.text().strip():
                continue
            rows.append(
                {
                    'id': name.data(Qt.UserRole),
                    'name': name.text().strip(),
                    'value': value.text() if value else '',
                    'hide_from_ai': self.traits.item(row, 2).checkState() == Qt.Checked,
                }
            )
        return rows

    def operations(self):
        """Canonical operations that turn the original entry into the edited one."""
        values = self.values()
        traits = self.trait_rows()
        if self.original is None:
            values['traits'] = [
                {
                    'name': t['name'],
                    'value': t['value'],
                    **({'hide_from_ai': True} if t['hide_from_ai'] else {}),
                }
                for t in traits
            ]
            return [{'type': 'codex_add', 'id': new_id('cx'), 'values': values}]
        identity = self.original['id']
        changed = {}
        for key, value in values.items():
            before = self.original.get(
                key,
                codex.DEFAULT_CASE_SENSITIVE
                if key == 'case_sensitive'
                else False
                if key == 'hide_from_ai'
                else ''
                if key == 'description'
                else [],
            )
            if value != before:
                changed[key] = value if value not in ('', []) else None
        operations = [{'type': 'codex_update', 'target': identity, 'values': changed}] if changed else []
        existing = {t['id']: t for t in self.original.get('traits', [])}
        kept = set()
        for trait in traits:
            old = existing.get(trait['id'])
            if old is None:
                operation = {
                    'type': 'codex_add_trait',
                    'target': identity,
                    'name': trait['name'],
                    'value': trait['value'],
                }
                if trait['hide_from_ai']:
                    operation['hide_from_ai'] = True
                operations.append(operation)
                continue
            kept.add(old['id'])
            update = {}
            for key in ('name', 'value'):
                if trait[key] != old[key]:
                    update[key] = trait[key]
            if trait['hide_from_ai'] != bool(old.get('hide_from_ai')):
                update['hide_from_ai'] = trait['hide_from_ai']
            if update:
                operations.append(
                    {'type': 'codex_update_trait', 'target': identity, 'trait': old['id'], 'values': update}
                )
        for trait_id in existing:
            if trait_id not in kept:
                operations.append({'type': 'codex_remove_trait', 'target': identity, 'trait': trait_id})
        return operations


class CodexDialog(QDialog):
    """Codex panel: entries, their mentions, and a mention heatmap per chapter."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(self.tr('Codex'))
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'Characters, places, items and terms of this manuscript. Mentions are found by name and '
                'alias without any AI. Entries and traits marked hidden are never sent to a model.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        filters = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(self.tr('Filter entries'))
        self.filter.setAccessibleName(self.tr('Filter codex entries'))
        self.filter.textChanged.connect(self.populate)
        filters.addWidget(self.filter, 1)
        self.series_label = QLabel()
        self.series_label.setObjectName('codexSeries')
        filters.addWidget(self.series_label)
        layout.addLayout(filters)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.entries = QTableWidget(0, 5)
        self.entries.setAccessibleName(self.tr('Codex entries'))
        self.entries.setHorizontalHeaderLabels(
            [self.tr('Name'), self.tr('Kind'), self.tr('Aliases'), self.tr('Mentions'), self.tr('AI')]
        )
        self.entries.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.entries.setSelectionMode(QAbstractItemView.SingleSelection)
        self.entries.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.entries.itemSelectionChanged.connect(self.show_mentions)
        self.entries.itemActivated.connect(lambda *_: self.edit_entry())
        left_layout.addWidget(self.entries)
        buttons = QHBoxLayout()
        for label, slot, name in (
            ('Add…', self.add_entry, 'Add codex entry'),
            ('Edit…', self.edit_entry, 'Edit selected codex entry'),
            ('Remove', self.remove_entry, 'Remove selected codex entry'),
        ):
            button = QPushButton(self.tr(label))
            button.setAccessibleName(self.tr(name))
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        left_layout.addLayout(buttons)
        splitter.addWidget(left)
        self.tabs = QTabWidget()
        self.mention_list = QListWidget()
        self.mention_list.setAccessibleName(self.tr('Mentions of the selected entry; activate to go there'))
        self.mention_list.itemActivated.connect(self.go_to)
        self.tabs.addTab(self.mention_list, self.tr('Mentions'))
        self.heatmap = QTableWidget(0, 0)
        self.heatmap.setAccessibleName(self.tr('Mention heatmap by chapter'))
        self.heatmap.setToolTip(self.tr('Mentions per chapter and entry; the counts are the cell text.'))
        self.heatmap.itemActivated.connect(self.go_to)
        self.tabs.addTab(self.heatmap, self.tr('Heatmap'))
        splitter.addWidget(self.tabs)
        splitter.setSizes([480, 420])
        layout.addWidget(splitter, 1)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout.addWidget(close)
        self.populate()

    # Data

    def _series(self):
        return series.series_of(self.window.doc, self.window.path)

    def populate(self, *_):
        doc = self.window.doc
        data = self._series()
        info = series.resolve(doc, self.window.path)
        self.series_label.setText(
            self.tr('Series: {}').format(
                info['state'] if info['state'] != 'ok' else data.get('title') or info['path']
            )
            if info
            else ''
        )
        self.map = codex.heatmap(doc, series=data)
        totals = {row['id']: row['mentions'] for row in self.map['entries']}
        needle = self.filter.text().casefold().strip()
        rows = [
            entry
            for entry in codex.effective_entries(doc, data).values()
            if not needle or needle in ' '.join([entry['name'], *entry.get('aliases', [])]).casefold()
        ]
        rows.sort(key=lambda e: (codex.KINDS.index(e['kind']), e['name'].casefold()))
        selected = self.selected_id()
        self.entries.setRowCount(len(rows))
        for index, entry in enumerate(rows):
            hidden = entry.get('hide_from_ai')
            hidden_traits = sum(1 for t in entry.get('traits', []) if t.get('hide_from_ai'))
            ai = (
                self.tr('Hidden')
                if hidden
                else self.tr('{} hidden traits').format(hidden_traits)
                if hidden_traits
                else ''
            )
            name = entry['name'] + (self.tr(' (series)') if entry.get('scope') == 'series' else '')
            self.entries.setItem(index, 0, _cell(name, entry['id']))
            self.entries.setItem(index, 1, _cell(self.tr(KIND_LABELS[entry['kind']])))
            self.entries.setItem(index, 2, _cell(', '.join(entry.get('aliases', []))))
            self.entries.setItem(index, 3, _cell(str(totals.get(entry['id'], 0))))
            self.entries.setItem(index, 4, _cell(ai, tip=self.tr('Hidden from AI') if ai else None))
            if entry['id'] == selected:
                self.entries.selectRow(index)
        self.populate_heatmap()
        self.show_mentions()

    def populate_heatmap(self):
        entries = self.map['entries'][:HEATMAP_COLUMNS]
        sections = self.map['sections']
        self.heatmap.clear()
        self.heatmap.setColumnCount(len(entries))
        self.heatmap.setRowCount(len(sections))
        self.heatmap.setHorizontalHeaderLabels([e['name'] for e in entries])
        self.heatmap.setVerticalHeaderLabels([s['title'] or self.tr('(untitled)') for s in sections])
        peak = max([count for s in sections for count in s['counts'].values()] or [1])
        for row, section in enumerate(sections):
            for column, entry in enumerate(entries):
                count = section['counts'].get(entry['id'], 0)
                item = _cell(
                    str(count),
                    section['heading'],
                    self.tr('{} mentions of {} in {}').format(count, entry['name'], section['title']),
                )
                if count:
                    shade = int(235 - 135 * count / peak)
                    item.setBackground(QColor(shade, shade, 255))
                    item.setForeground(QColor(0, 0, 0))
                self.heatmap.setItem(row, column, item)

    def selected_id(self):
        row = self.entries.currentRow()
        item = self.entries.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

    def show_mentions(self):
        self.mention_list.clear()
        identity = self.selected_id()
        if identity is None:
            return
        entries = codex.effective_entries(self.window.doc, self._series())
        if identity not in entries:
            return
        for object_id, _block_id, text in codex.text_objects(self.window.doc):
            for mention in codex.scan(text, {identity: entries[identity]}):
                start, end = mention['start'], mention['end']
                excerpt = ' '.join(text[max(0, start - 50) : end + 50].split())
                item = QListWidgetItem(f'{mention["surface"]} — …{excerpt}…')
                item.setData(Qt.UserRole, object_id)
                self.mention_list.addItem(item)

    def go_to(self, item):
        target = item.data(Qt.UserRole)
        if target:
            self.window.navigate_target(target)

    # Edits (canonical operations through the window, so they are undoable)

    def _commit(self, operations):
        if operations and self.window.commit(operations):
            self.populate()
            return True
        return False

    def add_entry(self):
        dialog = CodexEntryDialog(self)
        if dialog.exec() != QDialog.Accepted or not dialog.values()['name']:
            return False
        return self._commit(dialog.operations())

    def edit_entry(self):
        identity = self.selected_id()
        if identity is None:
            return False
        entry = codex.find_entry(self.window.doc, identity)
        if entry is None:
            # A series entry: editing it here creates a document entry that overrides it.
            shared = codex.effective_entries(self.window.doc, self._series()).get(identity)
            if shared is None:
                return False
            dialog = CodexEntryDialog(self, {k: v for k, v in shared.items() if k not in ('scope', 'id')})
            dialog.setWindowTitle(self.tr('Override series entry'))
            dialog.original = None
        else:
            dialog = CodexEntryDialog(self, entry)
        if dialog.exec() != QDialog.Accepted:
            return False
        return self._commit(dialog.operations())

    def remove_entry(self):
        identity = self.selected_id()
        if identity is None or codex.find_entry(self.window.doc, identity) is None:
            return False
        name = codex.find_entry(self.window.doc, identity)['name']
        if (
            QMessageBox.question(self, self.tr('Remove codex entry'), self.tr('Remove {}?').format(name))
            != QMessageBox.Yes
        ):
            return False
        return self._commit([{'type': 'codex_remove', 'target': identity}])


class ProjectFindDialog(QDialog):
    """Find across the project folder, with saved searches and backlinks."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(self.tr('Find in project'))
        self.resize(900, 560)
        self.last = None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText(self.tr('Words, "exact phrases", -excluded, NEAR(a b)'))
        self.query.setAccessibleName(self.tr('Project search query'))
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        self.scope = QComboBox()
        self.scope.setAccessibleName(self.tr('Search scope'))
        self.scope.addItem(self.tr('Whole project folder'), 'project')
        self.scope.addItem(self.tr('This document'), 'document')
        row.addWidget(self.scope)
        find = QPushButton(self.tr('Find'))
        find.setAccessibleName(self.tr('Find in project'))
        find.clicked.connect(self.search)
        row.addWidget(find)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAccessibleName(self.tr('Project search status'))
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        results = QWidget()
        results_layout = QHBoxLayout(results)
        saved_column = QVBoxLayout()
        saved_column.addWidget(QLabel(self.tr('Saved searches')))
        self.saved = QListWidget()
        self.saved.setAccessibleName(self.tr('Saved searches; activate to run'))
        self.saved.itemActivated.connect(lambda item: self.run_saved(item.data(Qt.UserRole)))
        saved_column.addWidget(self.saved, 1)
        save = QPushButton(self.tr('Save search…'))
        save.setAccessibleName(self.tr('Save the current search'))
        save.clicked.connect(self.save_search)
        remove = QPushButton(self.tr('Remove'))
        remove.setAccessibleName(self.tr('Remove the selected saved search'))
        remove.clicked.connect(self.remove_saved)
        saved_column.addWidget(save)
        saved_column.addWidget(remove)
        results_layout.addLayout(saved_column, 1)
        self.results = QTableWidget(0, 3)
        self.results.setAccessibleName(self.tr('Project search results; activate to go there'))
        self.results.setHorizontalHeaderLabels([self.tr('Document'), self.tr('Where'), self.tr('Text')])
        self.results.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.results.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results.itemActivated.connect(self.go_to)
        results_layout.addWidget(self.results, 3)
        self.tabs.addTab(results, self.tr('Results'))
        links = QWidget()
        links_layout = QVBoxLayout(links)
        pick = QHBoxLayout()
        self.subject = QComboBox()
        self.subject.setAccessibleName(self.tr('Heading, bookmark or codex entry'))
        pick.addWidget(self.subject, 1)
        show = QPushButton(self.tr('Show links'))
        show.setAccessibleName(self.tr('Show backlinks and unlinked mentions'))
        show.clicked.connect(self.show_links)
        pick.addWidget(show)
        links_layout.addLayout(pick)
        self.backlinks = QListWidget()
        self.backlinks.setAccessibleName(self.tr('Backlinks'))
        self.backlinks.itemActivated.connect(self.go_to_link)
        self.unlinked = QListWidget()
        self.unlinked.setAccessibleName(self.tr('Unlinked mentions'))
        self.unlinked.itemActivated.connect(self.go_to_link)
        links_layout.addWidget(QLabel(self.tr('Backlinks')))
        links_layout.addWidget(self.backlinks, 1)
        links_layout.addWidget(QLabel(self.tr('Unlinked mentions')))
        links_layout.addWidget(self.unlinked, 1)
        self.tabs.addTab(links, self.tr('Backlinks'))
        layout.addWidget(self.tabs, 1)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.populate_saved()
        self.populate_subjects()

    def populate_saved(self):
        self.saved.clear()
        for record in project_index.saved_searches(self.window.doc):
            item = QListWidgetItem(f'{record["name"]} — {record["query"]}')
            item.setData(Qt.UserRole, record['name'])
            self.saved.addItem(item)

    def populate_subjects(self):
        self.subject.clear()
        doc = self.window.doc
        for block in doc['blocks']:
            if block.get('type') == 'heading' and block.get('text', '').strip():
                self.subject.addItem(self.tr('Heading: {}').format(block['text']), ('target', block['id']))
        for name in sorted(doc['settings'].get('bookmarks', {})):
            self.subject.addItem(self.tr('Bookmark: {}').format(name), ('bookmark', name))
        for entry in codex.list_entries(
            doc, series=series.series_of(doc, self.window.path), include_hidden=True
        ):
            self.subject.addItem(self.tr('Codex: {}').format(entry['name']), ('target', entry['id']))

    def index(self):
        if self.window.path is None or self.window.path.suffix.lower() != '.byax':
            self.status.setText(
                self.tr('Save the document as a ByAuthorX file to search its project folder.')
            )
            return None
        return project_index.ProjectIndex(self.window.path, doc=self.window.doc)

    def _run(self, label, work, done):
        self.window.background(label, work, done)

    def search(self, *, saved=None):
        query = self.query.text().strip()
        if not query and saved is None:
            return False
        if not self.window.flush_edits():
            return False
        index = self.index()
        if index is None:
            return False
        doc = copy.copy(self.window.doc)
        scope = self.scope.currentData()

        def work():
            index.refresh(doc)
            if saved is not None:
                return project_index.run_saved(index, doc, saved, limit=100)
            return index.search(query, limit=100, documents=['open'] if scope == 'document' else None)

        self.status.setText(self.tr('Searching…'))
        self._run(self.tr('Searching the project…'), work, self.show_results)
        return True

    def show_results(self, result):
        self.last = result
        rows = result['results']
        self.results.setRowCount(len(rows))
        for index, hit in enumerate(rows):
            where = hit.get('item_title') or hit.get('heading') or ''
            self.results.setItem(index, 0, _cell(hit['path'], (hit['open'], hit['target'], hit['path'])))
            self.results.setItem(index, 1, _cell(where))
            self.results.setItem(index, 2, _cell(' '.join(hit['text'].split())[:300]))
        parts = [self.tr('{} matching passages').format(result['total'])]
        if result.get('truncated'):
            parts.append(self.tr('showing the first {}').format(len(rows)))
        for term, options in result.get('suggestions', {}).items():
            parts.append(
                self.tr('Did you mean {} for {}?').format(', '.join(o['term'] for o in options), term)
            )
        if result.get('saved_search'):
            parts.append(self.tr('Saved search “{}”').format(result['saved_search']['name']))
        self.status.setText('; '.join(parts) + '.')

    def go_to(self, item):
        data = self.results.item(item.row(), 0).data(Qt.UserRole)
        opened, target, path = data
        if opened:
            self.window.navigate_target(target)
        else:
            self.status.setText(self.tr('This passage is in {}. Open that document to edit it.').format(path))

    def run_saved(self, name):
        return self.search(saved=name)

    def save_search(self):
        query = self.query.text().strip()
        if not query:
            self.status.setText(self.tr('Type a search first.'))
            return False
        name, ok = QInputDialog.getText(self, self.tr('Save search'), self.tr('Name'), text=query[:64])
        if not ok or not name.strip():
            return False
        operation = {
            'type': 'saved_search_set',
            'name': name.strip()[:64],
            'query': query,
            'scope': self.scope.currentData(),
        }
        if self.window.commit([operation]):
            self.populate_saved()
            return True
        return False

    def remove_saved(self):
        item = self.saved.currentItem()
        if item is None:
            return False
        if self.window.commit([{'type': 'saved_search_remove', 'name': item.data(Qt.UserRole)}]):
            self.populate_saved()
            return True
        return False

    def show_links(self):
        choice = self.subject.currentData()
        if choice is None or not self.window.flush_edits():
            return False
        key, value = choice
        index = self.index()
        doc = copy.copy(self.window.doc)
        data = series.series_of(doc, self.window.path)

        def work():
            if index is not None:
                index.refresh(doc)
            return project_index.links(doc, index=index, series=data, limit=500, **{key: value})

        self._run(self.tr('Finding links…'), work, self.populate_links)
        return True

    def populate_links(self, result):
        for widget, key in ((self.backlinks, 'backlinks'), (self.unlinked, 'unlinked_mentions')):
            widget.clear()
            for row in result[key]['items']:
                where = self.tr('this document') if row.get('open') else row.get('path') or ''
                reason = {
                    'case': self.tr(' (different letter case)'),
                    'excluded': self.tr(' (excluded phrase)'),
                }.get(row.get('reason'), '')
                item = QListWidgetItem(f'{where}: …{" ".join(row["excerpt"].split())}…{reason}')
                item.setData(Qt.UserRole, (row.get('open'), row['target'], row.get('path')))
                widget.addItem(item)
        self.status.setText(
            self.tr('{} backlinks and {} unlinked mentions of {}.').format(
                result['backlinks']['total'], result['unlinked_mentions']['total'], result['subject']['label']
            )
        )

    def go_to_link(self, item):
        opened, target, path = item.data(Qt.UserRole)
        if opened:
            self.window.navigate_target(target)
        else:
            self.status.setText(self.tr('This passage is in {}. Open that document to edit it.').format(path))


class CodexPanels:
    """Main-window behavior for the codex and project find (mixed into ``MainWindow``)."""

    def codex_panel(self):
        if not self.flush_edits():
            return None
        dialog = CodexDialog(self)
        dialog.show()
        return dialog

    def project_find(self):
        if not self.flush_edits():
            return None
        dialog = ProjectFindDialog(self)
        dialog.show()
        return dialog

    def update_project_index(self, path, doc):
        """After a save, refresh this document's rows in an existing project index (never creates one)."""
        try:
            index = project_index.ProjectIndex.existing(path, doc=doc)
        except Exception:  # noqa: BLE001 - the index is derived data; a failure never blocks saving
            return
        if index is None:
            return
        from .gui import Worker  # local import: gui imports this module

        worker = Worker(lambda: index.update_document(doc))
        self._workers.add(worker)
        worker.signals.done.connect(lambda _result: self._workers.discard(worker))
        worker.signals.failed.connect(lambda _error: self._workers.discard(worker))
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(worker)
