"""Bibliography health panel, Zotero import, where-used view and citation styles (offscreen)."""

import copy
from pathlib import Path

import httpx
import pytest
from bibliography_fixtures import ReplayTransport
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QMessageBox
from test_zotero import local_api

from byax import citation_lookup, cli_bibliography, engine, gui_bibliography, model
from byax.gui import MainWindow
from byax.retrieval import ConsentRegistry

IEEE = (Path(__file__).parent / 'fixtures' / 'csl' / 'ieee.csl').read_bytes()
NATURE = '10.1038/nature14539'
LANCET = '10.1016/S0140-6736(97)11096-0'


class Everything(httpx.BaseTransport):
    def __init__(self):
        self.registry = ReplayTransport()
        self.zotero = local_api()
        self.requests = []

    def handle_request(self, request):
        self.requests.append(str(request.url))
        if request.url.host == 'localhost':
            return self.zotero.handle_request(request)
        if request.url.host == 'raw.githubusercontent.com':
            return httpx.Response(200, content=IEEE)
        return self.registry.handle_request(request)


@pytest.fixture
def network(monkeypatch):
    transport = Everything()
    monkeypatch.setattr(cli_bibliography, 'TRANSPORT', transport)
    monkeypatch.setattr(cli_bibliography, 'LOOKUP_OPTIONS', {'sleep': lambda _: None})
    return transport


def reference(ident, title, family, year, **csl):
    return {
        'id': ident,
        'csl': {
            'id': ident,
            'type': 'article-journal',
            'title': title,
            'author': [{'family': family}],
            'issued': {'date-parts': [[year]]},
            **csl,
        },
        'verification': 'unconfirmed',
    }


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'config')
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    doc = model.new_document(text='Deep nets work (Brown, 2018). A claim with a source.\nSecond paragraph.')
    doc['references'] = {
        'nature': reference('nature', 'Deep learning', 'LeCun', 2015, DOI=NATURE),
        'lancet': reference(
            'lancet',
            'Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children',
            'Wakefield',
            1998,
            DOI=LANCET,
        ),
        'bad': reference('bad', 'A bad DOI', 'Nobody', 2020, DOI='doi:10.12/x'),
    }
    block = doc['blocks'][0]
    block['marks'] = [
        {
            'type': 'citation',
            'value': 'nature',
            'start': 30,
            'end': 51,
            'kind': 'paraphrase',
            'quote': 'A claim with a source',
        }
    ]
    w.doc = doc
    w.refresh()
    errors, messages = [], []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    monkeypatch.setattr(w, 'message', lambda title, text: messages.append((title, text)))
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    w.test_errors, w.test_messages = errors, messages
    return w


def texts(tree):
    return [
        ' | '.join(tree.topLevelItem(i).text(c) for c in range(tree.columnCount()))
        for i in range(tree.topLevelItemCount())
    ]


def actions(window):
    return {action.text() for action in window.findChildren(QAction)}


def test_the_tools_menu_offers_the_four_views(window):
    for label in (
        'Bibliography health…',
        'Where is this source used…',
        'Import from Zotero…',
        'Citation styles…',
    ):
        assert label in actions(window)


def test_the_panel_opens_with_the_checks_that_need_no_network(qtbot, window, network):
    dialog = window.bibliography_health()
    qtbot.waitUntil(lambda: dialog.report is not None, timeout=5000)
    assert network.requests == []
    report = dialog.report
    assert (
        report['network']['mode'] == 'cache only' and 'Checked on this computer' in dialog.network_note.text()
    )
    rows = texts(dialog.findings)
    assert any('Not a valid DOI' in row and 'A bad DOI' in row for row in rows)
    assert any('Possibly not listed' in row and 'Brown, 2018' in row for row in rows)
    assert any('Listed but never cited' in row for row in rows)
    assert '3 sources' in dialog.summary.text()
    # The other tabs are filled from the same report.
    assert dialog.density.rowCount() >= 1 and dialog.age.rowCount() == 6
    assert 'never cited' in dialog.listing.toPlainText()
    assert dialog.online_button.text() == 'Check online…' and dialog.online_button.toolTip()
    for widget in (dialog.findings, dialog.listing, dialog.density, dialog.age, dialog.summary):
        assert widget.accessibleName()
    dialog.reject()


def test_online_checks_run_only_after_the_person_chooses_services(qtbot, window, network, monkeypatch):
    dialog = window.bibliography_health()
    qtbot.waitUntil(lambda: dialog.report is not None, timeout=5000)
    monkeypatch.setattr(gui_bibliography.LookupPermissionDialog, 'exec', lambda self: QDialog.Rejected)
    dialog.online_button.click()
    assert network.requests == []  # declining sends nothing
    dialog.report = None
    dialog.run_online(['crossref', 'datacite', 'openalex'])
    qtbot.waitUntil(lambda: dialog.report is not None, timeout=10000)
    rows = texts(dialog.findings)
    assert any(row.startswith('Serious') and 'Retracted' in row for row in rows)
    assert dialog.report['network']['mode'] == 'online' and network.requests
    assert 'Asked online' in dialog.network_note.text()
    # Not remembered: the permission was for this run only.
    assert not ConsentRegistry().allows('bibliography:crossref', 'citation_lookup')
    dialog.reject()


def test_a_remembered_permission_is_stored_and_not_found_reads_as_unverified(qtbot, window, network):
    dialog = window.bibliography_health()
    qtbot.waitUntil(lambda: dialog.report is not None, timeout=5000)
    window.doc['references']['ghost'] = reference(
        'ghost', 'A Study Nobody Has Seen', 'Nobody', 2020, DOI='10.1234/this-doi-does-not-exist-byax'
    )
    dialog.report = None
    dialog.run_online(['crossref', 'datacite', 'openalex'], remember=True)
    qtbot.waitUntil(lambda: dialog.report is not None, timeout=10000)
    assert ConsentRegistry().allows('bibliography:crossref', 'citation_lookup')
    assert not ConsentRegistry().allows('bibliography:wayback', 'citation_lookup')
    rows = texts(dialog.findings)
    ghost = [row for row in rows if 'A Study Nobody Has Seen' in row and 'Unverified' in row]
    assert ghost and 'Unverified, please check' in ghost[0] and 'fake' not in ghost[0].lower()
    dialog.run_online([])
    assert 'nothing was sent' in dialog.summary.text()
    dialog.reject()


def test_the_permission_dialog_lists_what_each_service_receives(qtbot, window):
    dialog = gui_bibliography.LookupPermissionDialog(window, links=True)
    qtbot.addWidget(dialog)
    assert set(dialog.boxes) == set(citation_lookup.SERVICE_ORDER)
    assert 'the DOI of each reference' in dialog.boxes['crossref'].text()
    assert dialog.services() == ['crossref', 'datacite', 'openalex', 'wayback', 'links'] or set(
        dialog.services()
    ) >= {'crossref'}
    plain = gui_bibliography.LookupPermissionDialog(window, links=False)
    qtbot.addWidget(plain)
    assert not plain.boxes['links'].isChecked() and not plain.boxes['wayback'].isChecked()
    assert all(box.accessibleName() for box in dialog.boxes.values())


def test_findings_jump_to_the_block_and_retraction_files_are_imported(qtbot, window, tmp_path, monkeypatch):
    dialog = window.bibliography_health()
    qtbot.waitUntil(lambda: dialog.report is not None, timeout=5000)
    jumps = []
    monkeypatch.setattr(window, 'navigate_target', jumps.append)
    row = next(
        dialog.findings.topLevelItem(i)
        for i in range(dialog.findings.topLevelItemCount())
        if 'Brown' in dialog.findings.topLevelItem(i).text(3)
    )
    dialog.findings.itemDoubleClicked.emit(row, 0)
    assert jumps == [window.doc['blocks'][0]['id']]
    csv_file = tmp_path / 'rw.csv'
    csv_file.write_text(
        'OriginalPaperDOI,RetractionNature,RetractionDate,Reason\n'
        + f'{LANCET},Retraction,2/6/2010,+Misconduct;\n',
        encoding='utf-8',
    )
    dialog.report = None
    assert dialog.import_retraction_file(str(csv_file))['imported'] == 1
    qtbot.waitUntil(lambda: dialog.report is not None, timeout=5000)
    assert any('Retracted' in row for row in texts(dialog.findings)) and window.test_messages
    assert dialog.save_contact('me@example.org')['mailto'] == 'me@example.org'
    assert dialog.save_contact('not an address') is None and window.test_errors
    dialog.reject()


def test_the_checks_tab_lists_the_bibliography_check_and_runs_it(qtbot, window, monkeypatch):
    panel = window.checks_panel
    assert 'bibliography' in panel.toggles and 'bibliography' in panel.run_buttons
    opened = []
    monkeypatch.setattr(window, 'bibliography_health', lambda: opened.append(True))
    panel.run_check('bibliography')
    assert opened == [True]


# --- Zotero ---------------------------------------------------------------------------------------------------------


def test_zotero_import_connects_lists_collections_and_adds_sources_with_notes(qtbot, window, network):
    window.doc['references'] = {}
    window.doc['blocks'][0]['marks'] = []
    dialog = window.import_from_zotero()
    assert 'never changes Zotero' in dialog.findChildren(type(dialog.status))[0].text()
    dialog.connect_zotero()
    qtbot.waitUntil(lambda: dialog.collection.count() == 2, timeout=5000)
    assert dialog.collection.itemText(1) == 'Reading list' and 'Connected' in dialog.status.text()
    dialog.import_button.click()
    qtbot.waitUntil(lambda: dialog.read is not None, timeout=5000)
    references = window.doc['references']
    assert (
        len(references) == 3
        and 'Imported 3 items (3 new, 0 updated) and 3 annotations' in dialog.status.text()
    )
    article = next(s for s in references.values() if s['zotero']['key'] == 'ABCD2345')
    assert article['notes'][0]['locator'] == {'page': '436'}
    assert all(request.startswith('http://localhost:23119/') for request in network.requests)
    # Importing again updates rather than duplicating, and is one undoable step.
    dialog.read = None
    dialog.import_button.click()
    qtbot.waitUntil(lambda: dialog.read is not None, timeout=5000)
    assert len(window.doc['references']) == 3 and 'updated' in dialog.status.text()
    dialog.reject()


def test_a_closed_zotero_is_explained_in_the_dialog(qtbot, window, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError('refused', request=request)

    monkeypatch.setattr(cli_bibliography, 'TRANSPORT', httpx.MockTransport(refuse))
    dialog = window.import_from_zotero()
    dialog.connect_zotero()
    qtbot.waitUntil(lambda: bool(window.test_errors), timeout=5000)
    assert 'Zotero is not running' in window.test_errors[0] and 'could not be read' in dialog.status.text()
    dialog.reject()


# --- where is this source used ----------------------------------------------------------------------------------------


def with_excerpt(window, cite=True):
    doc = window.doc
    doc['references']['manual'] = {
        'id': 'manual',
        'title': 'Letters',
        'excerpt': 'x',
        'verification': 'manual',
    }
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'insert_paragraph',
                    'id': 'p_quote',
                    'text': 'Letters say so.',
                    'style': 'quotation',
                    'after': doc['blocks'][0]['id'],
                },
                {
                    'type': 'excerpt',
                    'target': 'p_quote',
                    'start': 0,
                    'end': 15,
                    'source': 'manual',
                    'quote': 'Letters say so.',
                },
                *(
                    [
                        {
                            'type': 'cite',
                            'target': doc['blocks'][0]['id'],
                            'start': 0,
                            'end': 4,
                            'source': 'manual',
                            'kind': 'paraphrase',
                        }
                    ]
                    if cite
                    else []
                ),
            ]
        },
    )
    window.doc = doc
    window.refresh()


def test_where_used_lists_citations_and_excerpts_per_source(qtbot, window, monkeypatch):
    with_excerpt(window)
    dialog = window.where_used()
    rows = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
    letters = next(r for r in rows if r.text(0) == 'Letters')
    assert '1 citations, 1 excerpts' in letters.text(2)
    kinds = {letters.child(i).text(0): letters.child(i).text(3) for i in range(letters.childCount())}
    assert kinds == {'Citation': '', 'Excerpt': 'Anchored'}
    assert 'not used' in dialog.summary.text()
    jumps = []
    monkeypatch.setattr(window, 'navigate_target', jumps.append)
    dialog.tree.itemDoubleClicked.emit(letters.child(0), 0)
    assert jumps
    dialog.unused.setChecked(True)
    assert dialog.tree.topLevelItemCount() > len(rows)
    assert dialog.tree.accessibleName() and dialog.check_button.accessibleName()
    dialog.reject()


def test_deleting_a_source_keeps_the_excerpt_and_the_view_says_so(qtbot, window):
    with_excerpt(window, cite=False)
    assert window.commit([{'type': 'remove_reference', 'id': 'manual'}]), window.test_errors
    block = next(b for b in window.doc['blocks'] if b['id'] == 'p_quote')
    assert block['text'] == 'Letters say so.' and block['marks'][0]['anchor']['state'] == 'unanchored'
    dialog = window.where_used()
    rows = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
    removed = next(r for r in rows if 'removed from the reference list' in r.text(0))
    assert removed.child(0).text(3) == 'Source removed' or removed.child(1).text(3) == 'Source removed'
    dialog.run_check()
    qtbot.waitUntil(lambda: dialog.check_report is not None, timeout=5000)
    assert not dialog.apply_button.isEnabled()  # already unanchored: nothing to apply
    dialog.reject()


def test_checking_excerpts_reports_an_unavailable_source_and_changes_nothing(qtbot, window):
    with_excerpt(window)
    doc = copy.deepcopy(window.doc)
    mark = next(m for b in doc['blocks'] for m in b['marks'] if m['type'] == 'excerpt')
    # An excerpt that claims a library pointer which is not there: unavailable, nothing to apply.
    doc['references']['manual']['library'] = {'library_id': 'gone', 'document_id': 'd', 'content_hash': 'h'}
    window.doc = doc
    window.refresh()
    dialog = window.where_used()
    dialog.run_check()
    qtbot.waitUntil(lambda: dialog.check_report is not None, timeout=5000)
    letters = next(
        dialog.tree.topLevelItem(i)
        for i in range(dialog.tree.topLevelItemCount())
        if dialog.tree.topLevelItem(i).text(0) == 'Letters'
    )
    states = [letters.child(i).text(3) for i in range(letters.childCount())]
    assert 'Source unavailable' in states and mark['quote'] in window.doc['blocks'][1]['text']
    assert not dialog.apply_button.isEnabled()
    dialog.reject()


def test_proposed_excerpt_updates_are_applied_only_when_the_person_asks(qtbot, window):
    with_excerpt(window, cite=False)
    # An excerpt whose source vanished without the removal being recorded (an older file, a merge).
    window.doc['blocks'][1]['marks'][0]['value'] = 'ghost'
    window.refresh()
    dialog = window.where_used()
    dialog.run_check()
    qtbot.waitUntil(lambda: dialog.check_report is not None, timeout=5000)
    assert dialog.apply_button.isEnabled() and '1 need attention' in dialog.summary.text()
    assert window.doc['blocks'][1]['marks'][0].get('anchor') is None  # nothing changed yet
    dialog.apply_button.click()
    mark = window.doc['blocks'][1]['marks'][0]
    assert mark['anchor']['state'] == 'unanchored' and window.doc['blocks'][1]['text'] == 'Letters say so.'
    assert not dialog.apply_button.isEnabled()
    dialog.reject()


# --- citation styles ----------------------------------------------------------------------------------------------------


def test_a_style_is_previewed_confirmed_and_then_offered_in_the_citation_settings(
    qtbot, window, network, monkeypatch
):
    dialog = window.citation_styles()
    assert (
        dialog.table.rowCount() == 3 and 'CC BY-SA 3.0' in dialog.findChildren(type(dialog.status))[0].text()
    )
    asked = []

    def question(parent, title, text, *args):
        asked.append(text)
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, 'question', staticmethod(question))
    dialog.preview_style('ieee')
    qtbot.waitUntil(lambda: dialog.table.rowCount() == 4, timeout=5000)
    assert 'raw.githubusercontent.com' in asked[0] and 'SHA-256' in asked[0] and str(len(IEEE)) in asked[0]
    assert 'SHA-256 checked' in dialog.status.text()
    assert dialog.table.item(3, 0).text() == 'ieee' and dialog.table.item(3, 2).text() == 'numeric'
    dialog.table.selectRow(3)
    assert dialog.remove_selected() == 'ieee' and dialog.table.rowCount() == 3
    # Declining installs nothing.
    monkeypatch.setattr(QMessageBox, 'question', staticmethod(lambda *a: QMessageBox.No))
    dialog.preview_style('ieee')
    qtbot.waitUntil(lambda: 'Nothing was added' in dialog.status.text(), timeout=5000)
    assert dialog.table.rowCount() == 3
    dialog.reject()


def test_a_local_style_file_can_be_imported(qtbot, window, tmp_path):
    path = tmp_path / 'house.csl'
    path.write_bytes(IEEE)
    dialog = window.citation_styles()
    assert dialog.import_file(str(path))['name'] == 'house' and dialog.table.rowCount() == 4
    bad = tmp_path / 'bad.csl'
    bad.write_text('<nope/>')
    assert dialog.import_file(str(bad)) is None and window.test_errors
    assert Qt  # keep the import used for the flags above
    dialog.reject()
