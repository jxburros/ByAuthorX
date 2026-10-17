"""The routed assistant and the "What the AI saw" panel in the AI tab, offscreen (plan 8.3, 8.4)."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from byax.gui import MainWindow
from byax.providers import ProviderConfig, ProviderRegistry

ROOT = Path(__file__).resolve().parents[1]


def _benchmark():
    spec = importlib.util.spec_from_file_location(
        'router_benchmark', ROOT / 'scripts' / 'router_benchmark.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCH = _benchmark()


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'config')
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.doc = BENCH.fixture_document()
    w.refresh()
    errors = []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    w.test_errors = errors
    rates = {'input_per_million': 1.0, 'output_per_million': 2.0, 'source': 'my price list'}
    ProviderRegistry(tmp_path / 'config').save(
        ProviderConfig('helper', 'ollama', 'fixture-model', cost_rates=rates)
    )
    w.ai_panel.refresh_providers()
    return w


def mock_chat(monkeypatch, reply='An answer.'):
    calls = []

    def chat(self, alias, messages, *, mode='chat', context=None, authorized=False, cancel_event=None):
        calls.append({'alias': alias, 'messages': copy.deepcopy(messages), 'context': copy.deepcopy(context)})
        return {
            'reply': reply,
            'proposals': [],
            'cache': {'read_tokens': 64, 'write_tokens': 8, 'strategy': 'kv reuse'},
            'cost_estimate': {
                'amount': 0.0002,
                'currency': 'USD',
                'exact': False,
                'rates_source': 'my price list',
            },
        }

    monkeypatch.setattr(ProviderRegistry, 'chat', chat)
    return calls


def ask(window, qtbot, text):
    panel = window.ai_panel
    panel.input.setPlainText(text)
    panel.send()
    qtbot.waitUntil(lambda: panel._pending is None)
    return panel


def rows(panel):
    tree = panel.context_inspector.items
    return [
        [tree.topLevelItem(i).text(c) for c in range(tree.columnCount())]
        + [tree.topLevelItem(i).data(0, Qt.UserRole)]
        for i in range(tree.topLevelItemCount())
    ]


def test_every_request_is_routed_and_the_inspector_shows_what_was_sent(window, qtbot, monkeypatch):
    calls = mock_chat(monkeypatch)
    panel = ask(window, qtbot, 'Where does Tom keep the brass key?')
    assert len(calls) == 1 and not window.test_errors
    inspector = panel.context_inspector
    inspector.setChecked(True)
    assert 'R2' in panel.route_label.text() and 'codex first' in panel.route_label.text()
    assert inspector.route_label.text().startswith('R2 Look it up')
    table = rows(panel)
    assert any(row[0].startswith('Codex · Tom Vane') and row[3] == 'codex' for row in table)
    passages = [row for row in table if row[0].startswith('Manuscript')]
    assert passages and all(row[1] == 'retrieved' and int(row[2]) > 0 for row in passages)
    assert all(row[5] == 'Stays on this computer' for row in table)
    assert 'helper' in inspector.model_label.text() and 'Local' in inspector.model_label.text()
    assert '64 tokens read, 8 written' in inspector.cache_label.text()
    assert (
        'before sending' in inspector.cost_label.text() and 'after the reply' in inspector.cost_label.text()
    )
    assert inspector.dropped.count() > 0  # beyond top-k for an 8K model
    assert 'R2' in inspector.log.toPlainText()
    assert 'opt-in' in inspector.note.text()
    sent = calls[0]['context']
    assert sent['codex_entries'] and sent['manuscript_passages'] and 'blocks' not in sent


def test_exclude_and_rerun_is_one_action(window, qtbot, monkeypatch):
    calls = mock_chat(monkeypatch)
    panel = ask(window, qtbot, 'Where does Tom keep the brass key?')
    panel.context_inspector.setChecked(True)
    table = rows(panel)
    excluded = next(row[-1] for row in table if row[0].startswith('Manuscript'))
    panel.context_inspector.items.setCurrentItem(
        next(
            panel.context_inspector.items.topLevelItem(i)
            for i in range(len(table))
            if panel.context_inspector.items.topLevelItem(i).data(0, Qt.UserRole) == excluded
        )
    )
    assert panel.context_inspector.rerun_button.isEnabled()
    panel.context_inspector.rerun_button.click()
    qtbot.waitUntil(lambda: panel._pending is None and len(calls) == 2)
    assert excluded not in json.dumps(calls[1]['context'])
    assert excluded in json.dumps(calls[0]['context'])
    assert [m['content'] for m in panel.messages] == ['Where does Tom keep the brass key?', 'An answer.']
    assert any(
        'Excluded by you' in panel.context_inspector.dropped.item(i).text()
        for i in range(panel.context_inspector.dropped.count())
    )


def test_pinned_items_are_kept_in_later_requests(window, qtbot, monkeypatch):
    calls = mock_chat(monkeypatch)
    panel = ask(window, qtbot, 'Where does Tom keep the brass key?')
    item = next(i for i in panel._assembled['items'] if i['kind'] == 'manuscript_passage')
    assert panel.toggle_pin(item['id'])
    ask(window, qtbot, "What colour are Mara's eyes?")
    pinned = next(i for i in panel._assembled['items'] if i['id'] == item['id'])
    assert pinned['tier'] == 'pinned' and pinned['pinned_by_you']
    assert item['payload']['text'] in json.dumps(calls[1]['context'])
    assert not panel.toggle_pin(item['id'])


def test_route_override_and_snapshot(window, qtbot, monkeypatch):
    calls = mock_chat(monkeypatch)
    panel = window.ai_panel
    panel.route_box.setCurrentIndex(panel.route_box.findData('R4'))
    ask(window, qtbot, 'Where does Tom keep the brass key?')
    assert 'Chosen by you' in panel.route_label.text()
    assert calls[0]['context']['manuscript_snapshot']['blocks']
    assert panel.route_box.currentData() == 'auto'  # an override applies to one request


def test_abstains_without_calling_the_model_after_verified_absence(window, qtbot, monkeypatch):
    calls = mock_chat(monkeypatch)
    window.doc = BENCH.fixture_document(repeat=BENCH.LARGE_REPEAT)
    window.refresh()
    panel = window.ai_panel
    panel.input.setPlainText('Does Ingrid ever mention the zeppelin?')
    panel.send()
    assert calls == [] and panel._pending is None
    assert 'Not found in your manuscript or attached libraries' in panel.history.toPlainText()
    assert panel.turns[-1]['route']['route'] == 'R3'


def test_export_conversation_writes_routes_and_research_logs(window, qtbot, monkeypatch, tmp_path):
    mock_chat(monkeypatch)
    panel = ask(window, qtbot, 'Find every place Tom lies to Mara.')
    markdown = panel.export_conversation(str(tmp_path / 'conversation.md'))
    text = Path(markdown).read_text(encoding='utf-8')
    assert '**You:** Find every place Tom lies to Mara.' in text and '### Research log (R3)' in text
    data = json.loads(Path(panel.export_conversation(str(tmp_path / 'conversation.json'))).read_text())
    assert data['turns'][0]['route']['route'] == 'R3' and data['turns'][0]['research_log']['steps']


def test_build_chapter_summaries_is_an_explicit_action(window, qtbot, monkeypatch):
    from byax import summaries

    mock_chat(monkeypatch, reply='A chapter summary.')
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: QMessageBox.Yes)
    panel = window.ai_panel
    assert summaries.status(window.doc)['counts']['fresh'] == 0
    original = copy.deepcopy(window.doc)
    assert panel.build_summaries()
    assert 'never written into the document' in panel.last_summary_dialog.text()
    qtbot.waitUntil(lambda: getattr(panel, 'last_summary_report', None) is not None)
    assert len(panel.last_summary_report['built']) == 4
    assert summaries.status(window.doc)['counts']['fresh'] == 4
    assert window.doc == original
    assert 'Chapter summaries: 4 built' in panel.history.toPlainText()
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: QMessageBox.No)
    panel.last_summary_report = None
    assert panel.build_summaries() is None and panel.last_summary_report is None
