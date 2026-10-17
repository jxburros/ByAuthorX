"""Bibliography health panel, Zotero import, "where is this source used" and citation styles.

Context engine plan 9 (B1, Z1, A2). These dialogs are views over :mod:`byax.bibliography`,
:mod:`byax.zotero`, :mod:`byax.excerpts` and :mod:`byax.csl_styles`; every document change is a
canonical operation committed through the window (so it is undoable), and every lookup runs off the UI
thread through ``window.background``.

Nothing here contacts an outside service on its own. The health panel opens with the checks that need
no network. **Check online…** first shows which services would be asked and what each receives, and
only after the person agrees (optionally remembering the choice as the ``citation_lookup``
permission) does it send a request. "Not found" is shown as "Unverified, please check", never as fake.
"""

from __future__ import annotations

import copy
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import bibliography, citation_lookup, cli_bibliography, csl_styles, excerpts, zotero
from .errors import ByaxError

SEVERITY_LABELS = {'error': 'Serious', 'warning': 'To review', 'info': 'For information'}
CHECK_LABELS = {
    'doi': 'DOI',
    'metadata': 'Details',
    'retractions': 'Retraction',
    'citations': 'Reference list',
    'density': 'Citation density',
    'punctuation': 'Punctuation',
    'links': 'Link',
}
STATUS_LABELS = {
    'unverified': 'Unverified, please check',
    'retracted': 'Retracted',
    'concern': 'Expression of concern',
    'invalid': 'Not a valid DOI',
    'mismatch': 'Differs from the registry',
    'minor': 'Small difference',
    'dead': 'Link may be dead',
    'cited_not_listed': 'Cited but not listed',
    'possibly_not_listed': 'Possibly not listed',
    'listed_not_cited': 'Listed but never cited',
    'no_citations': 'No citations',
}
STATE_LABELS = {
    'current': 'Matches the source',
    'edited': 'You edited your copy',
    'source_changed': 'Source changed',
    'unanchored': 'Unanchored',
    'source_removed': 'Source removed',
    'source_unavailable': 'Source unavailable',
    'unchecked': 'Not checked',
    'anchored': 'Anchored',
}


def _cell(text, data=None, tip=None):
    item = QTableWidgetItem(str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        item.setData(Qt.UserRole, data)
    if tip:
        item.setToolTip(tip)
    return item


class _SessionConsent:
    """Permission for one run only: what the person ticked, never stored."""

    def __init__(self, services):
        self.services = set(services)

    def allows(self, provider, category):
        return (
            category == 'citation_lookup'
            and provider.removeprefix(citation_lookup.CONSENT_PREFIX) in self.services
        )


class LookupPermissionDialog(QDialog):
    """Which outside services may be asked, and what each would receive."""

    def __init__(self, window, *, links=False):
        super().__init__(window)
        self.setWindowTitle(self.tr('Check online'))
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'ByAuthorX will ask the services you tick below about your references. Only the items listed '
                'are sent; your text is never sent. Nothing is requested in the background, and an answer '
                'that is not found is reported as “unverified, please check”, never as fake.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        consent = window_consent()
        self.boxes = {}
        for service in citation_lookup.SERVICE_ORDER:
            label, _base, _interval, sends = citation_lookup.SERVICES[service]
            box = QCheckBox(self.tr('{0} receives {1}').format(label, sends))
            box.setAccessibleName(self.tr('Allow {0}').format(label))
            box.setChecked(
                service in ('crossref', 'datacite', 'openalex')
                or (links and service in ('links', 'wayback'))
                or consent.allows(citation_lookup.CONSENT_PREFIX + service, 'citation_lookup')
            )
            self.boxes[service] = box
            layout.addWidget(box)
        self.remember = QCheckBox(self.tr('Remember this permission for next time'))
        self.remember.setAccessibleName(self.tr('Remember this permission'))
        layout.addWidget(self.remember)
        contact = QLabel(
            self.tr(
                'Crossref and OpenAlex are sent a contact address only if you set one (Contact address…); '
                'every request names ByAuthorX and its project page.'
            )
        )
        contact.setWordWrap(True)
        layout.addWidget(contact)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(self.tr('Check online'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def services(self):
        return [service for service, box in self.boxes.items() if box.isChecked()]


def window_consent():
    from .retrieval import ConsentRegistry

    return ConsentRegistry()


class BibliographyHealthDialog(QDialog):
    """The report: findings first, then the reference list, citation density and reference age."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.report = None
        self.setWindowTitle(self.tr('Bibliography health'))
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        self.summary = QLabel(self.tr('Checking on this computer…'))
        self.summary.setObjectName('bibliographySummary')
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName(self.tr('Bibliography summary'))
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName(self.tr('Bibliography report'))
        self.findings = QTreeWidget()
        self.findings.setObjectName('bibliographyFindings')
        self.findings.setAccessibleName(self.tr('Bibliography findings'))
        self.findings.setHeaderLabels(
            [self.tr('Importance'), self.tr('Reference'), self.tr('Check'), self.tr('What was found')]
        )
        self.findings.setRootIsDecorated(False)
        self.findings.setUniformRowHeights(False)
        self.findings.setWordWrap(True)
        self.findings.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.findings.itemDoubleClicked.connect(self.go_to)
        self.tabs.addTab(self.findings, self.tr('Findings'))
        self.listing = QTextBrowser()
        self.listing.setAccessibleName(self.tr('Citations against the reference list'))
        self.tabs.addTab(self.listing, self.tr('Reference list'))
        self.density = QTableWidget(0, 4)
        self.density.setAccessibleName(self.tr('Citation density per section'))
        self.density.setHorizontalHeaderLabels(
            [self.tr('Section'), self.tr('Words'), self.tr('Citations'), self.tr('Per 1,000 words')]
        )
        self.density.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tabs.addTab(self.density, self.tr('Citation density'))
        self.age = QTableWidget(0, 3)
        self.age.setAccessibleName(self.tr('Age of the references'))
        self.age.setHorizontalHeaderLabels([self.tr('Age'), self.tr('Listed'), self.tr('Cited')])
        self.age.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.age_note = QLabel()
        self.age_note.setWordWrap(True)
        age_box = QVBoxLayout()
        age_box.addWidget(self.age)
        age_box.addWidget(self.age_note)
        from PySide6.QtWidgets import QWidget

        age_page = QWidget()
        age_page.setLayout(age_box)
        self.tabs.addTab(age_page, self.tr('Reference age'))
        layout.addWidget(self.tabs, 1)
        self.network_note = QLabel()
        self.network_note.setObjectName('bibliographyNetwork')
        self.network_note.setWordWrap(True)
        layout.addWidget(self.network_note)
        row = QHBoxLayout()
        self.refresh_button = QPushButton(self.tr('Check again'))
        self.refresh_button.setAccessibleName(self.tr('Check again on this computer'))
        self.refresh_button.clicked.connect(self.run_offline)
        self.online_button = QPushButton(self.tr('Check online…'))
        self.online_button.setAccessibleName(self.tr('Check references online'))
        self.online_button.setToolTip(
            self.tr(
                'Asks Crossref, DataCite, OpenAlex and, if you choose, the websites you link to. You approve this first.'
            )
        )
        self.online_button.clicked.connect(self.ask_online)
        self.retractions_button = QPushButton(self.tr('Import Retraction Watch file…'))
        self.retractions_button.setAccessibleName(self.tr('Import a Retraction Watch CSV file'))
        self.retractions_button.setToolTip(
            self.tr(
                'Optional. A CSV you downloaded from Retraction Watch lets retractions be found without the network.'
            )
        )
        self.retractions_button.clicked.connect(self.import_retractions)
        self.contact_button = QPushButton(self.tr('Contact address…'))
        self.contact_button.setAccessibleName(self.tr('Set the contact address for Crossref and OpenAlex'))
        self.contact_button.clicked.connect(self.set_contact)
        self.styles_button = QPushButton(self.tr('Citation styles…'))
        self.styles_button.clicked.connect(lambda: self.window.citation_styles())
        for button in (
            self.refresh_button,
            self.online_button,
            self.retractions_button,
            self.contact_button,
            self.styles_button,
        ):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # Running ------------------------------------------------------------------------------------

    def _run(self, label, **options):
        self.window.flush_edits()
        frozen = copy.deepcopy(self.window.doc)
        self.revision = frozen['revision']
        checks = options.pop('checks', None)
        online = options.pop('online', False)
        lookup_options = options.pop('lookup', {})
        self.summary.setText(label)

        def work():
            lookup = cli_bibliography.make_lookup(online=online, **lookup_options)
            return bibliography.health(frozen, lookup=lookup, checks=checks, **options)

        self.window.background(label, work, self.show_report, failed=self.failed)

    def failed(self):
        self.summary.setText(self.tr('The check could not be completed.'))

    def run_offline(self):
        self._run(self.tr('Checking the bibliography on this computer…'))

    def ask_online(self):
        has_links = any(
            bibliography.source_fields(s)['url'] for s in self.window.doc.get('references', {}).values()
        )
        dialog = LookupPermissionDialog(self.window, links=has_links)
        if dialog.exec() == QDialog.Accepted:
            self.run_online(dialog.services(), remember=dialog.remember.isChecked())

    def run_online(self, services, *, remember=False):
        """Ask the chosen services; ``remember`` stores the choice as the ``citation_lookup`` permission."""
        services = list(services)
        if not services:
            self.summary.setText(self.tr('No service was chosen, so nothing was sent.'))
            return
        if remember:
            citation_lookup.Lookup(offline=True, consent=window_consent()).grant(services, True)
        checks = [c for c in bibliography.ALL_CHECKS if c != 'links' or 'links' in services]
        self._run(
            self.tr('Checking references online…'),
            online=True,
            checks=checks,
            archive='wayback' in services and 'links' in services,
            lookup={'consent': _SessionConsent(services)},
        )

    def show_report(self, report):
        self.report = report
        self.populate(report)

    # Display --------------------------------------------------------------------------------------

    def populate(self, report):
        stale = self.window.doc.get('revision') != getattr(self, 'revision', None)
        lines = bibliography.describe(report)
        self.summary.setText(
            ' '.join(lines) + (' ' + self.tr('The document changed since this check.') if stale else '')
        )
        mode = report['network']['mode']
        self.network_note.setText(
            self.tr('Asked online: {0} requests, {1} answered from the cache.').format(
                report['network']['requests'], report['network']['cache_hits']
            )
            if mode == 'online'
            else self.tr('Checked on this computer only. Online checks run when you choose Check online…')
            if mode != 'cache only'
            else self.tr(
                'Checked on this computer, using answers kept from earlier online checks. Nothing was sent.'
            )
        )
        titles = {s['id']: s['title'] or s['id'] for s in report['sources']}
        self.findings.clear()
        for finding in report['findings']:
            row = QTreeWidgetItem(
                [
                    self.tr(SEVERITY_LABELS[finding['severity']]),
                    titles.get(finding['source'], '') if finding['source'] else '',
                    self.tr(CHECK_LABELS.get(finding['check'], finding['check'])),
                    '{}: {}'.format(
                        self.tr(
                            STATUS_LABELS.get(
                                finding['status'], finding['status'].replace('_', ' ').capitalize()
                            )
                        ),
                        finding['message'],
                    ),
                ]
            )
            row.setData(0, Qt.UserRole, finding)
            row.setToolTip(3, finding['message'])
            self.findings.addTopLevelItem(row)
        listing = report.get('citation_list')
        self.listing.setHtml(self.listing_html(listing) if listing else '')
        self.fill_density(report.get('density'))
        self.fill_age(report.get('age'))

    def listing_html(self, listing):
        def block(title, items, key):
            if not items:
                return ''
            body = ''.join(f'<li>{html.escape(key(item))}</li>' for item in items)
            return f'<h3>{html.escape(title)}</h3><ul>{body}</ul>'

        text = (
            block(
                self.tr('Cited but not in the reference list'),
                listing['cited_not_listed'],
                lambda i: i['text'] or i['source'],
            )
            + block(
                self.tr('Possibly not in the reference list (author and year in the text)'),
                listing['possibly_not_listed'],
                lambda i: i['text'],
            )
            + block(
                self.tr('In the reference list but never cited'),
                listing['listed_not_cited'],
                lambda i: i['title'],
            )
        )
        summary = self.tr('{0} of {1} listed sources are cited.').format(
            listing['cited_sources'], listing['listed_sources']
        )
        return f'<p>{html.escape(summary)}</p>' + (
            text
            or f'<p>{html.escape(self.tr("Every citation has a reference and every reference is cited."))}</p>'
        )

    def fill_density(self, density):
        rows = (density or {}).get('sections', [])
        self.density.setRowCount(len(rows))
        for index, section in enumerate(rows):
            self.density.setItem(
                index,
                0,
                _cell(section['heading'] or self.tr('(before the first heading)'), section['block_id']),
            )
            self.density.setItem(index, 1, _cell(section['words']))
            self.density.setItem(index, 2, _cell(section['citations']))
            self.density.setItem(
                index, 3, _cell('' if section['per_1000_words'] is None else section['per_1000_words'])
            )

    def fill_age(self, age):
        if not age:
            self.age.setRowCount(0)
            self.age_note.setText('')
            return
        listed, cited = age['listed'], age['cited']
        self.age.setRowCount(len(listed['buckets']))
        for index, (a, b) in enumerate(zip(listed['buckets'], cited['buckets'], strict=True)):
            self.age.setItem(index, 0, _cell(self.tr(a['label'])))
            self.age.setItem(index, 1, _cell(a['count']))
            self.age.setItem(index, 2, _cell(b['count']))
        median = listed['median_age']
        self.age_note.setText(
            self.tr(
                'Median age of the listed references: {0} years; {1} are more than ten years old.'
            ).format(median if median is not None else '—', listed['older_than_10_years'])
        )

    def go_to(self, item, column=0):
        finding = item.data(0, Qt.UserRole) or {}
        block = finding.get('detail', {}).get('block_id')
        if block:
            self.window.navigate_target(block)

    # Settings ---------------------------------------------------------------------------------------

    def import_retractions(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr('Import Retraction Watch file'), '', 'CSV (*.csv);;All files (*)'
        )
        if path:
            self.import_retraction_file(path)

    def import_retraction_file(self, path):
        try:
            result = citation_lookup.import_retraction_watch(path)
        except (ByaxError, OSError) as exc:
            self.window.error(exc)
            return None
        self.window.message(
            self.tr('Retraction Watch'),
            self.tr('{0} records imported. Retractions are now checked on this computer.').format(
                result['imported']
            ),
        )
        self.run_offline()
        return result

    def set_contact(self):
        current = citation_lookup.load_settings().get('mailto', '')
        text, ok = QInputDialog.getText(
            self,
            self.tr('Contact address'),
            self.tr(
                'An email address sent to Crossref and OpenAlex so they can reach you about heavy use. Leave empty to send none.'
            ),
            QLineEdit.Normal,
            current,
        )
        if ok:
            self.save_contact(text)

    def save_contact(self, text):
        try:
            return citation_lookup.save_settings({'mailto': text.strip()})
        except ByaxError as exc:
            self.window.error(exc)
            return None


# --- Zotero ---------------------------------------------------------------------------------------------------


class ZoteroImportDialog(QDialog):
    """Read Zotero's local library (read-only) into this document's sources, with annotations as notes."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.read = None
        self.setWindowTitle(self.tr('Import from Zotero'))
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'ByAuthorX reads your Zotero library on this computer; it never changes Zotero. In Zotero 7, turn '
                'on Settings > Advanced > “Allow other applications on this computer to communicate with Zotero” '
                'and keep Zotero open. Items become sources here, and highlights and notes made in Zotero’s PDF '
                'reader become notes on those sources, with their page.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.collection = QComboBox()
        self.collection.setAccessibleName(self.tr('Zotero collection'))
        self.collection.addItem(self.tr('Whole library'), None)
        form.addRow(self.tr('Collection:'), self.collection)
        self.annotations = QCheckBox(self.tr('Import annotations as notes on their sources'))
        self.annotations.setChecked(True)
        self.annotations.setAccessibleName(self.tr('Import annotations'))
        form.addRow(self.annotations)
        layout.addLayout(form)
        self.status = QLabel(self.tr('Not connected.'))
        self.status.setObjectName('zoteroStatus')
        self.status.setWordWrap(True)
        self.status.setAccessibleName(self.tr('Zotero import status'))
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.connect_button = QPushButton(self.tr('Connect to Zotero'))
        self.connect_button.clicked.connect(self.connect_zotero)
        self.import_button = QPushButton(self.tr('Import'))
        self.import_button.clicked.connect(self.run_import)
        row.addWidget(self.connect_button)
        row.addWidget(self.import_button)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def client(self):
        return zotero.ZoteroClient(zotero.BASE_URL, transport=cli_bibliography.TRANSPORT)

    def connect_zotero(self):
        client = self.client()
        self.status.setText(self.tr('Asking Zotero…'))
        self.window.background(
            self.tr('Connecting to Zotero…'), client.collections, self.show_collections, failed=self.failed
        )

    def failed(self):
        self.status.setText(self.tr('Zotero could not be read. See the message for what to check.'))

    def show_collections(self, collections):
        self.collection.clear()
        self.collection.addItem(self.tr('Whole library'), None)
        for item in collections:
            self.collection.addItem(item['name'] or item['key'], item['key'])
        self.status.setText(self.tr('Connected. {0} collections found.').format(len(collections)))

    def run_import(self):
        self.window.flush_edits()
        existing = copy.deepcopy(self.window.doc.get('references', {}))
        revision = self.window.doc['revision']
        client = self.client()
        collection = self.collection.currentData()
        annotations = self.annotations.isChecked()
        self.status.setText(self.tr('Reading Zotero…'))

        def work():
            return zotero.import_from_zotero(client, existing, collection=collection, annotations=annotations)

        self.window.background(
            self.tr('Importing from Zotero…'),
            work,
            lambda read: self.finish(read, revision),
            failed=self.failed,
        )

    def finish(self, read, revision):
        self.read = read
        counts = read['counts']
        if self.window.doc['revision'] != revision:
            self.status.setText(self.tr('The document changed while Zotero was being read. Import again.'))
            return False
        operations = [{'type': 'add_reference', 'id': s['id'], 'values': s} for s in read['sources']]
        if operations and not self.window.commit(operations):
            return False
        self.status.setText(
            self.tr('Imported {0} items ({1} new, {2} updated) and {3} annotations as notes.').format(
                counts['items'], counts['new'], counts['updated'], counts['annotations']
            )
        )
        return True


# --- where is this source used ---------------------------------------------------------------------------------


class WhereUsedDialog(QDialog):
    """Each source with its citations and excerpts; excerpts can be checked against their sources."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.check_report = None
        self.setWindowTitle(self.tr('Where is this source used'))
        self.resize(780, 560)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setObjectName('whereUsedTree')
        self.tree.setAccessibleName(self.tr('Sources and where they are used'))
        self.tree.setHeaderLabels([self.tr('Source'), self.tr('Section'), self.tr('Text'), self.tr('State')])
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.itemDoubleClicked.connect(self.go_to)
        layout.addWidget(self.tree, 1)
        self.unused = QCheckBox(self.tr('Show sources that are not used'))
        self.unused.setAccessibleName(self.tr('Show unused sources'))
        self.unused.toggled.connect(self.populate)
        layout.addWidget(self.unused)
        row = QHBoxLayout()
        self.check_button = QPushButton(self.tr('Check excerpts'))
        self.check_button.setAccessibleName(self.tr('Check excerpts against their sources'))
        self.check_button.clicked.connect(self.run_check)
        self.apply_button = QPushButton(self.tr('Apply proposed updates'))
        self.apply_button.setEnabled(False)
        self.apply_button.setToolTip(
            self.tr(
                'Re-anchor excerpts that moved and mark lost ones unanchored. No excerpt text is deleted.'
            )
        )
        self.apply_button.clicked.connect(self.apply_updates)
        row.addWidget(self.check_button)
        row.addWidget(self.apply_button)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.populate()

    def populate(self, *_):
        report = excerpts.where_used(self.window.doc, include_unused=self.unused.isChecked())
        self.tree.clear()
        for entry in report['sources']:
            title = entry['title'] + (
                '' if entry['in_references'] else self.tr(' (removed from the reference list)')
            )
            top = QTreeWidgetItem(
                [
                    title,
                    '',
                    self.tr('{0} citations, {1} excerpts').format(
                        entry['counts']['citations'], entry['counts']['excerpts']
                    ),
                    '',
                ]
            )
            top.setData(0, Qt.UserRole, {'source': entry['source']})
            self.tree.addTopLevelItem(top)
            for kind, rows in (('Citation', entry['citations']), ('Excerpt', entry['excerpts'])):
                for use in rows:
                    state = use.get('state') or use.get('anchor_state') or ''
                    child = QTreeWidgetItem(
                        [
                            self.tr(kind),
                            use['heading'] or self.tr('(before the first heading)'),
                            use['text'],
                            self.tr(STATE_LABELS.get(state, state)),
                        ]
                    )
                    child.setData(0, Qt.UserRole, {'block_id': use['block_id'], 'source': entry['source']})
                    top.addChild(child)
            top.setExpanded(True)
        used = len(report['sources']) - sum(
            1 for e in report['sources'] if not e['counts']['citations'] and not e['counts']['excerpts']
        )
        self.summary.setText(
            self.tr('{0} sources are used in this document; {1} reference-list entries are not used.').format(
                used, len(report['unused'])
            )
        )

    def go_to(self, item, column=0):
        data = item.data(0, Qt.UserRole) or {}
        if data.get('block_id'):
            self.window.navigate_target(data['block_id'])

    def run_check(self):
        self.window.flush_edits()
        frozen = copy.deepcopy(self.window.doc)
        self.revision = frozen['revision']
        self.window.background(self.tr('Checking excerpts…'), lambda: excerpts.check(frozen), self.show_check)

    def show_check(self, report):
        self.check_report = report
        states = {(e['block_id'], e['start']): e for e in report['excerpts']}
        for top_index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(top_index)
            for child_index in range(top.childCount()):
                child = top.child(child_index)
                data = child.data(0, Qt.UserRole) or {}
                for (block, _start), found in states.items():
                    if (
                        data.get('block_id') == block
                        and found['source'] == data.get('source')
                        and child.text(0) == self.tr('Excerpt')
                    ):
                        child.setText(3, self.tr(STATE_LABELS.get(found['state'], found['state'])))
                        child.setToolTip(3, found.get('message', ''))
        summary = report['summary']
        attention = sum(
            summary[k]
            for k in ('edited', 'source_changed', 'unanchored', 'source_removed', 'source_unavailable')
        )
        self.summary.setText(
            self.tr(
                '{0} excerpts checked; {1} need attention. Nothing is changed until you apply the updates.'
            ).format(report['total'], attention)
        )
        self.apply_button.setEnabled(bool(report['operations'] or report['source_operations']))

    def apply_updates(self):
        report = self.check_report
        if not report or self.window.doc['revision'] != self.revision:
            self.summary.setText(self.tr('The document changed since the check. Check again.'))
            self.apply_button.setEnabled(False)
            return False
        ok = True
        # Source corrections and document corrections are separate actions.
        if report['source_operations']:
            ok = self.window.commit(copy.deepcopy(report['source_operations']))
        if ok and report['operations']:
            ok = self.window.commit(copy.deepcopy(report['operations']))
        self.apply_button.setEnabled(not ok)
        self.check_report = None if ok else report
        self.populate()
        return ok


# --- citation styles ---------------------------------------------------------------------------------------------


class CitationStylesDialog(QDialog):
    """Built-in and installed CSL styles; add one from the CSL repository (shown first) or a local file."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(self.tr('Citation styles'))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'APA, MLA and Chicago are built in. Other styles come from the Citation Style Language repository '
                '(licensed CC BY-SA 3.0). Nothing is downloaded until you choose a style and confirm its source, size '
                'and checksum.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setObjectName('stylesTable')
        self.table.setAccessibleName(self.tr('Citation styles'))
        self.table.setHorizontalHeaderLabels(
            [self.tr('Name'), self.tr('Title'), self.tr('Kind'), self.tr('Source')]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.download_button = QPushButton(self.tr('Add from the CSL repository…'))
        self.download_button.clicked.connect(self.ask_download)
        self.file_button = QPushButton(self.tr('Import .csl file…'))
        self.file_button.clicked.connect(self.ask_file)
        self.remove_button = QPushButton(self.tr('Remove'))
        self.remove_button.clicked.connect(self.remove_selected)
        for button in (self.download_button, self.file_button, self.remove_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.populate()

    def populate(self):
        rows = csl_styles.list_styles()['styles']
        self.table.setRowCount(len(rows))
        for index, style in enumerate(rows):
            origin = (
                self.tr('Built in')
                if style['built_in']
                else (style.get('origin') or {}).get('url') or self.tr('Local file')
            )
            self.table.setItem(index, 0, _cell(style['name'], style['name']))
            self.table.setItem(index, 1, _cell(style.get('title', '')))
            self.table.setItem(index, 2, _cell(style.get('citation_format', '')))
            self.table.setItem(index, 3, _cell(origin))

    def ask_download(self):
        name, ok = QInputDialog.getText(
            self,
            self.tr('Add a citation style'),
            self.tr('Style name in the CSL repository (for example ieee or nature):'),
        )
        if ok and name.strip():
            self.preview_style(name.strip())

    def preview_style(self, name):
        """Download for display only, then ask; nothing is installed until the person agrees."""
        self.status.setText(self.tr('Fetching {0} to show you what it is…').format(name))
        self.window.background(
            self.tr('Fetching a citation style…'),
            lambda: csl_styles.preview(name, transport=cli_bibliography.TRANSPORT),
            self.confirm,
            failed=lambda: self.status.setText(self.tr('The style could not be fetched.')),
        )

    def confirm(self, shown):
        if not shown['usable']:
            self.status.setText(
                self.tr('{0} is a dependent style; add its parent style instead.').format(shown['name'])
            )
            return False
        answer = QMessageBox.question(
            self,
            self.tr('Add this style?'),
            self.tr('Source: {0}\nSize: {1} bytes\nSHA-256: {2}\nLicence: {3}\n\nAdd “{4}”?').format(
                shown['url'], shown['size'], shown['sha256'], shown['license'] or '—', shown['style']['title']
            ),
        )
        if answer == QMessageBox.Yes:
            self.install_style(shown['name'], shown['sha256'])
            return True
        self.status.setText(self.tr('Nothing was added.'))
        return False

    def install_style(self, name, sha256):
        self.window.background(
            self.tr('Adding a citation style…'),
            lambda: csl_styles.add_from_repository(
                name, expect_sha256=sha256, transport=cli_bibliography.TRANSPORT, replace=True
            ),
            self.installed,
            failed=lambda: self.status.setText(self.tr('The style was not added.')),
        )

    def installed(self, result):
        self.status.setText(
            self.tr('Added {0} ({1} bytes, SHA-256 checked).').format(result['name'], result['size'])
        )
        self.populate()

    def ask_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr('Import a CSL style'), '', 'CSL styles (*.csl)')
        if path:
            self.import_file(path)

    def import_file(self, path):
        try:
            result = csl_styles.add_from_file(path, replace=True)
        except (ByaxError, OSError) as exc:
            self.window.error(exc)
            return None
        self.status.setText(self.tr('Imported {0}.').format(result['name']))
        self.populate()
        return result

    def remove_selected(self):
        row = self.table.currentRow()
        name = self.table.item(row, 0).data(Qt.UserRole) if row >= 0 and self.table.item(row, 0) else None
        if not name:
            return None
        try:
            csl_styles.remove(name)
        except ByaxError as exc:
            self.status.setText(str(exc))
            return None
        self.populate()
        return name


class BibliographyPanels:
    """Main-window behaviour for the dialogs above (mixed into ``MainWindow``)."""

    def bibliography_health(self):
        if not self.flush_edits():
            return None
        dialog = BibliographyHealthDialog(self)
        dialog.show()
        dialog.run_offline()
        return dialog

    def import_from_zotero(self):
        if not self.flush_edits():
            return None
        dialog = ZoteroImportDialog(self)
        dialog.show()
        return dialog

    def where_used(self):
        if not self.flush_edits():
            return None
        dialog = WhereUsedDialog(self)
        dialog.show()
        return dialog

    def citation_styles(self):
        dialog = CitationStylesDialog(self)
        dialog.show()
        return dialog
