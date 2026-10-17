"""Support underlines on assistant replies and proposals, and on audited citations (plan Phase 5), offscreen.

The model is a scripted stand-in (``FakeScorer``); the real one is measured in ``test_entailment_fixture.py``.
"""

import copy

import pytest
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QMessageBox

from byax import engine, grounding, model
from byax import grounding_entailment as ge
from byax.citations import source_from_hit
from byax.gui import MainWindow
from byax.gui_ai import SUPPORT, support_glyph, support_span
from byax.libraries import LibraryIndex, LibraryRegistry
from byax.providers import ProviderConfig, ProviderRegistry

PAD = ' Margin note about nothing in particular.' * 25
NOTES = f'Aria is the captain.{PAD}\n\nThe lamp burned all night and the glass held.{PAD}'
PASSAGE = {
    'chunk_id': 'chunk_log_2',
    'library_id': 'lib_fixture',
    'document_id': 'doc_logbook',
    'content_hash': 'hash_logbook',
    'path': 'logbook.txt',
    'title': 'logbook.txt',
    'locator': {'ordinal': 1},
    'text': 'Mrs. Wren rowed the supplies across before the gale.',
}


class FakeScorer:
    identity = 'local:fake-nli@1'

    def __init__(self, table):
        self.table = table

    def score(self, pairs):
        return [next((v for k, v in self.table.items() if k in claim), 0.9) for _, claim in pairs]


def fake_stage():
    return ge.EntailmentStage(FakeScorer({'mainland': 0.05, 'lamp': 0.1, 'glass': 0.95, 'rowed': 0.7}))


@pytest.fixture(autouse=True)
def no_models(tmp_path, monkeypatch):
    monkeypatch.setenv('BYAX_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.delenv('BYAX_BUNDLED_MODELS', raising=False)
    yield
    grounding.register_stage('entailment', None)


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'config')
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.doc = model.new_document(text='The keeper wrote in the log.\nA second line.')
    w.refresh()
    errors = []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    w.test_errors = errors
    return w


def installed(monkeypatch):
    """Pretend the on-device model is installed, with the scripted stage behind it."""
    state = {'available': True, 'model': 'hhem-2.1-open-int8', 'state': 'installed'}
    monkeypatch.setattr(ge, 'availability', lambda *a, **k: state)
    monkeypatch.setattr(ge, 'try_stage', lambda **k: fake_stage())


def chat_like(calls):
    """A stand-in for ``ProviderRegistry.chat`` that grounds the reply the way the real one does."""

    def chat(self, alias, messages, *, mode='chat', context=None, entailment=False, **kw):
        calls.append({'entailment': entailment, 'kw': kw, 'mode': mode})
        reply = messages[-1]['content'] if False else REPLY['text']
        grounded = grounding.ground_reply(reply, passages=[PASSAGE])
        result = {'reply': reply, 'proposals': list(REPLY.get('proposals', []))}
        if entailment:
            grounded = ge.entail_grounding(grounded, passages=[PASSAGE])
            if result['proposals']:
                result['proposal_grounding'] = ge.entail_proposals(result['proposals'], passages=[PASSAGE])
        result['grounding'] = grounded
        return result

    return chat


REPLY = {'text': ''}


def ask(window, qtbot, monkeypatch, tmp_path, text, proposals=()):
    REPLY.update(text=text, proposals=list(proposals))
    ProviderRegistry(tmp_path / 'config').save(ProviderConfig('helper', 'ollama', 'fixture-model'))
    panel = window.ai_panel
    panel.refresh_providers()
    original = panel.context
    monkeypatch.setattr(
        panel, 'context', lambda frozen: dict(original(frozen), library_passages=[dict(PASSAGE)])
    )
    calls = []
    monkeypatch.setattr(ProviderRegistry, 'chat', chat_like(calls))
    panel.input.setPlainText('What does the log say?')
    panel.send()
    qtbot.waitUntil(lambda: panel._pending is None)
    return panel, calls


# --- the control is honest ---------------------------------------------------------------------------------------


def test_the_support_check_is_off_and_says_why_when_the_model_is_not_installed(window):
    box = window.ai_panel.support_check
    assert not box.isEnabled() and not box.isChecked()
    assert 'Not available' in box.toolTip() and 'Model not installed' in box.toolTip()
    assert box.accessibleDescription() == box.toolTip() and box.accessibleName()
    assert window.ai_panel.support_state['available'] is False


def test_the_support_check_is_opt_in_even_when_the_model_is_installed(window, monkeypatch):
    installed(monkeypatch)
    panel = window.ai_panel
    panel.refresh_support_check()
    box = panel.support_check
    assert box.isEnabled() and not box.isChecked()  # nothing runs until the writer turns it on
    assert 'nothing is sent anywhere' in box.toolTip()
    box.setChecked(True)
    assert window.settings.value('ai/support_check', False, type=bool) is True
    panel.refresh_support_check()
    assert box.isChecked()


# --- replies ------------------------------------------------------------------------------------------------------


REPLY_TEXT = (
    'Help came from the mainland in good time [[cite:chunk_log_2]]. '
    'The lamp burned for a week, but the glass held fast through the gale [[cite:chunk_log_2]].'
)


def test_underlines_have_a_glyph_a_hover_card_and_a_plain_text_summary(window, qtbot, monkeypatch, tmp_path):
    installed(monkeypatch)
    window.ai_panel.refresh_support_check()
    window.ai_panel.support_check.setChecked(True)
    panel, calls = ask(window, qtbot, monkeypatch, tmp_path, REPLY_TEXT)
    assert calls[0]['entailment'] is True
    html = panel.history.toHtml()
    transcript = panel.history.toPlainText()
    # Each checked piece is an underlined link; the verdict is also a bracketed glyph after it.
    assert (
        html.count('support:') >= 3
        and 'text-decoration: underline' in html
        or 'text-decoration:underline' in html
    )
    assert '[!]' in transcript and '[✓]' in transcript
    assert [s['result'] for s in panel.supports] == ['unsupported', 'unsupported', 'supported']
    # Colour is never the only signal: the same information is in words in the transcript.
    assert 'Support check (advisory, checked on this computer):' in transcript
    assert '! Not supported: “Help came from the mainland in good time”' in transcript
    assert '✓ Supported: “but the glass held fast through the gale”' in transcript
    # Chips still work beside the underlines.
    assert 'cite:0' in html and 'cite:1' in html and '[[cite:' not in transcript
    panel.history.highlighted.emit(QUrl('support:0'))
    card = panel.last_support_tooltip
    assert 'Not supported: the cited text does not state this' in card
    assert 'Help came from the mainland in good time' in card and 'not proof' in card
    panel.history.highlighted.emit(QUrl('support:2'))
    assert 'Supported: the cited text states this' in panel.last_support_tooltip
    panel.history.highlighted.emit(QUrl('support:99'))  # an unknown link shows nothing and does not fail
    assert not window.test_errors


def test_without_turning_it_on_no_check_runs_and_nothing_is_underlined(window, qtbot, monkeypatch, tmp_path):
    installed(monkeypatch)
    panel, calls = ask(window, qtbot, monkeypatch, tmp_path, REPLY_TEXT)
    assert calls[0]['entailment'] is False
    assert 'support:' not in panel.history.toHtml() and 'Support check' not in panel.history.toPlainText()
    assert panel.supports == []


def test_a_missing_model_is_reported_in_the_transcript_not_hidden(window, qtbot, monkeypatch, tmp_path):
    installed(monkeypatch)
    window.ai_panel.refresh_support_check()
    window.ai_panel.support_check.setChecked(True)
    # The model disappears between turning the check on and sending.
    monkeypatch.setattr(
        ge, 'availability', lambda *a, **k: {'available': False, 'reason': 'Model not installed.'}
    )
    panel, _ = ask(window, qtbot, monkeypatch, tmp_path, REPLY_TEXT)
    assert 'Support check not run: Model not installed.' in panel.history.toPlainText()
    assert 'support:' not in panel.history.toHtml()


def test_a_reply_without_citations_says_there_was_nothing_to_check(window, qtbot, monkeypatch, tmp_path):
    installed(monkeypatch)
    window.ai_panel.refresh_support_check()
    window.ai_panel.support_check.setChecked(True)
    panel, _ = ask(window, qtbot, monkeypatch, tmp_path, 'The keeper is honest and brave.')
    assert 'cites no sources, so there was nothing to check against' in panel.history.toPlainText()


def test_suggested_edits_show_the_support_of_what_they_cite(window, qtbot, monkeypatch, tmp_path):
    installed(monkeypatch)
    window.ai_panel.refresh_support_check()
    window.ai_panel.support_check.setChecked(True)
    block = window.doc['blocks'][0]
    edit = {
        'target': block['id'],
        'start': 0,
        'end': 3,
        'expected_text': 'The',
        'replacement': 'Help came from the mainland [[cite:chunk_log_2]]',
        'explanation': 'adds the rescue',
        'provenance': {},
    }
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: QMessageBox.No)
    window.ai_panel.mode_buttons['work'].setChecked(True)
    panel, _ = ask(window, qtbot, monkeypatch, tmp_path, 'I suggest one edit.', proposals=[edit])
    transcript = panel.history.toPlainText()
    assert 'Suggested edits' in transcript and 'Help came from the mainland' in transcript
    assert '[!]' in transcript and '1. ! Not supported: “Help came from the mainland”' in transcript
    assert [s['result'] for s in panel.supports][-1] == 'unsupported'
    assert 'discarded' in transcript  # the writer declined; nothing was added to Review


# --- helpers shared with the audit ----------------------------------------------------------------------------------


def test_every_category_has_a_distinct_glyph_and_words():
    assert len({SUPPORT[r][0] for r in SUPPORT}) == 3 and len({SUPPORT[r][1] for r in SUPPORT}) == 3
    for result in SUPPORT:
        span = support_span('claim', result, 4)
        assert 'href="support:4"' in span and 'title="' in span and 'underline' in span
        assert SUPPORT[result][0] in support_glyph(result)
    assert 'href' not in support_span('claim', 'partial')  # the audit uses a plain span with a title


# --- the audit of the writer's own citations ---------------------------------------------------------------------


@pytest.fixture
def library(qtbot, tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    (root / 'notes.txt').write_text(NOTES, encoding='utf-8')
    registry = LibraryRegistry(tmp_path / 'libraries')
    definition = registry.create('Research', [root])
    cache = tmp_path / 'cache'
    LibraryIndex(definition, cache).refresh()
    real = LibraryIndex
    monkeypatch.setattr('byax.libraries.LibraryRegistry', lambda *a, **k: registry)
    monkeypatch.setattr(
        'byax.libraries.LibraryIndex', lambda library, cache_dir=None, **k: real(library, cache)
    )
    return definition, real(definition, cache)


def audited_document(window, library):
    definition, index = library
    lamp = index.search('burned')['results'][0]
    source = source_from_hit(lamp)
    doc = model.new_document(text='“The lamp burned all night”\nThe lamp lasted a whole week.')
    doc['settings']['libraries'] = [{'library_id': definition['library_id'], 'name': 'Research'}]
    first, second = doc['blocks']
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'add_reference', 'id': source['id'], 'values': source},
                {
                    'type': 'cite',
                    'target': first['id'],
                    'start': 0,
                    'end': len(first['text']),
                    'source': source['id'],
                    'kind': 'quotation',
                    'locator': {'chunk': lamp['chunk_id']},
                },
                {
                    'type': 'cite',
                    'target': second['id'],
                    'start': 0,
                    'end': len(second['text']),
                    'source': source['id'],
                    'kind': 'paraphrase',
                    'locator': {'chunk': lamp['chunk_id']},
                },
            ]
        },
    )
    window.doc = doc
    window.refresh()
    return doc


def test_the_checks_tab_offers_the_paraphrase_check_and_underlines_audited_citations(
    window, qtbot, monkeypatch, library
):
    doc = audited_document(window, library)
    checks = window.checks_panel
    assert 'support' in checks.toggles and not checks.toggles['support'].isChecked()
    assert '(on-device)' in checks.toggles['support'].text()
    installed(monkeypatch)
    checks.run_buttons['support'].click()
    qtbot.waitUntil(lambda: getattr(checks, 'last_audit', None) is not None)
    report = checks.last_audit
    assert [i['status'] for i in report['citations']] == ['quote_found', 'not_checkable']
    assert report['citations'][1]['entailment']['result'] == 'unsupported' and report['advisory'] is True
    html = checks.audit_dialog.browser.toHtml()
    text = checks.audit_dialog.browser.toPlainText()
    assert 'underline' in html and '[!]' in text
    assert 'Support check (advisory, on this computer):' in text and 'Not supported' in text
    assert 'Quote found' in text  # stage 1 is unchanged and still shown
    assert report['citations'][0].get('entailment') is None  # the quotation was settled by stage 1
    checks.audit_dialog.close()
    assert (
        grounding.audit_citations(doc)['citations'][1]['status'] == 'not_checkable'
    )  # the plain audit is as before


def test_the_audit_says_so_when_the_model_is_missing_and_still_runs_stage_one(window, qtbot, library):
    audited_document(window, library)
    checks = window.checks_panel
    checks.run_buttons['support'].click()
    qtbot.waitUntil(lambda: getattr(checks, 'last_audit', None) is not None)
    text = checks.audit_dialog.browser.toPlainText()
    assert 'Support check not run' in text and 'Model not installed' in text
    assert 'Quote found' in text and 'Not checkable' in text
    checks.audit_dialog.close()


def test_the_plain_citation_audit_is_unchanged(window, qtbot, library):
    doc = audited_document(window, library)
    checks = window.checks_panel
    checks.run_buttons['citations'].click()
    qtbot.waitUntil(lambda: getattr(checks, 'last_audit', None) is not None)
    assert checks.last_audit == grounding.audit_citations(copy.deepcopy(doc))
    assert 'Support check' not in checks.audit_dialog.browser.toPlainText()
    checks.audit_dialog.close()
