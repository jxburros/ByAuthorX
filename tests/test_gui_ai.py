"""Inspector AI tab: local spelling, provider entry point, and guarded assistant modes."""

import copy
import json

import pytest
from PySide6.QtCore import QAbstractAnimation, QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from byax import model
from byax.gui import MainWindow
from byax.providers import ProviderConfig, ProviderRegistry


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'config')
    monkeypatch.delenv('BYAX_REDUCED_MOTION', raising=False)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.doc = model.new_document(text='The quick brwn fox jumps.\nA second line.')
    w.refresh()
    errors = []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    w.test_errors = errors
    return w


def configure(tmp_path, alias='helper', kind='ollama', **kwargs):
    registry = ProviderRegistry(tmp_path / 'config')
    registry.save(ProviderConfig(alias, kind, 'fixture-model', **kwargs))
    return registry


def mock_chat(monkeypatch, reply):
    calls = []

    def chat(self, alias, messages, *, mode='chat', context=None, authorized=False, cancel_event=None):
        calls.append({'alias': alias, 'messages': messages, 'mode': mode, 'context': context})
        return reply(mode, context) if callable(reply) else copy.deepcopy(reply)

    monkeypatch.setattr(ProviderRegistry, 'chat', chat)
    return calls


def test_ai_tab_exists_with_keys_button_and_empty_state(window):
    tabs = [window.inspector.tabText(i) for i in range(window.inspector.count())]
    assert tabs == ['Review', 'Layers', 'AI']
    panel = window.ai_panel
    assert panel.keys_button.text() == 'Add or manage API keys…'
    assert 'No AI provider configured' in panel.provider_status.text()
    assert panel.chat_stack.currentIndex() == 1
    assert not panel.send_button.isEnabled()
    assert panel.mode_switch.accessibleName() == 'Assistant mode'


def test_api_keys_button_opens_provider_dialog_and_refreshes(window, monkeypatch, tmp_path):
    from byax.gui_providers import ProviderDialog

    opened = []

    def fake_exec(dialog):
        opened.append(dialog)
        configure(tmp_path)
        return 0

    monkeypatch.setattr(ProviderDialog, 'exec', fake_exec)
    window.ai_panel.keys_button.click()
    assert len(opened) == 1
    assert 'helper' in window.ai_panel.provider_status.text()
    assert 'fixture-model' in window.ai_panel.provider_status.text()
    assert window.ai_panel.chat_stack.currentIndex() == 0


def test_spell_check_lists_misspelling_and_replacement_commits(window, qtbot):
    panel = window.ai_panel
    panel.check_spelling()
    qtbot.waitUntil(lambda: panel.spell_list.count() > 0)
    finding = panel.spell_list.item(0).data(Qt.UserRole)
    assert finding['word'] == 'brwn'
    assert 'brown' in finding['suggestions']
    navigated = []
    window.editor.navigate = lambda target: navigated.append(target)
    panel.spell_list.itemClicked.emit(panel.spell_list.item(0))
    assert navigated == [finding['target']]
    committed = []
    original = window.commit
    window.commit = lambda operations: committed.append(operations) or original(operations)
    panel.suggestions.setCurrentText('brown')
    panel.replace_selected()
    assert committed[0] == [
        {
            'type': 'replace_text',
            'target': finding['target'],
            'match': 'brwn',
            'replacement': 'brown',
            'occurrence': 1,
            'expected_text': 'brwn',
        }
    ]
    assert window.doc['blocks'][0]['text'] == 'The quick brown fox jumps.'
    qtbot.waitUntil(lambda: panel.spell_button.isEnabled())
    assert panel.spell_stack.currentIndex() == 1
    assert 'No misspellings' in panel.spell_status.text()
    assert not window.test_errors


def test_spell_dictionary_and_ignore(window, qtbot):
    window.doc = model.new_document(text='Zorblat met Quixxa and Zorblat again.')
    window.refresh()
    panel = window.ai_panel
    panel.check_spelling()
    qtbot.waitUntil(lambda: panel.spell_list.count() == 3)
    panel.spell_list.setCurrentRow(0)
    panel.add_to_dictionary()
    assert window.doc['settings']['dictionary'] == ['Zorblat']
    assert panel.spell_list.count() == 1
    panel.ignore_selected()
    assert panel.spell_list.count() == 0
    panel.check_spelling()
    qtbot.waitUntil(lambda: panel.spell_button.isEnabled())
    assert panel.spell_list.count() == 0


def test_stale_spelling_finding_is_never_applied(window, qtbot):
    panel = window.ai_panel
    panel.check_spelling()
    qtbot.waitUntil(lambda: panel.spell_list.count() > 0)
    finding = panel.spell_list.item(0).data(Qt.UserRole)
    window._commit([{'type': 'set_text', 'target': finding['target'], 'text': 'Completely different.'}])
    assert panel.replacement_operation(finding, 'brown') is None


def test_mode_toggle_persists_in_settings(window):
    panel = window.ai_panel
    assert panel.mode == 'chat'
    panel.mode_buttons['work'].click()
    assert window.settings.value('ai/mode') == 'work'
    assert panel.mode_buttons['work'].accessibleName() == 'Work mode'


EDIT_REPLY = {
    'reply': 'I fixed the typo.',
    'proposed_edits': [
        {'target': None, 'start': 10, 'end': 14, 'expected_text': 'brwn', 'replacement': 'brown'}
    ],
}


def model_edit_output(context):
    edit = dict(EDIT_REPLY['proposed_edits'][0], target=context['blocks'][0]['id'])
    return json.dumps({**EDIT_REPLY, 'proposed_edits': [edit]})


def test_chat_mode_never_commits_even_if_model_returns_edits(window, qtbot, monkeypatch, tmp_path):
    from byax.providers import validate_chat_output

    configure(tmp_path)
    window.ai_panel.refresh_providers()

    def reply(mode, context):
        # A misbehaving model/adapter that returns edits regardless of mode.
        return {**validate_chat_output(model_edit_output(context), context), 'reply': 'Here is a thought.'}

    calls = mock_chat(monkeypatch, reply)
    committed = []
    monkeypatch.setattr(window, 'commit', lambda operations: committed.append(operations) or True)
    original = copy.deepcopy(window.doc)
    panel = window.ai_panel
    panel.input.setPlainText('Fix the typo please.')
    panel.send()
    qtbot.waitUntil(lambda: panel._pending is None)
    assert calls[0]['mode'] == 'chat'
    assert calls[0]['context']['blocks'][0]['text'].startswith('The quick')
    assert committed == []
    assert window.doc == original
    assert panel.messages[-1] == {'role': 'assistant', 'content': 'Here is a thought.'}
    assert 'Here is a thought.' in panel.history.toPlainText()


def test_work_mode_offers_exact_edits_and_registers_review_proposals(window, qtbot, monkeypatch, tmp_path):
    from byax.providers import validate_chat_output

    configure(tmp_path)
    panel = window.ai_panel
    panel.refresh_providers()
    panel.mode_buttons['work'].click()
    mock_chat(monkeypatch, lambda mode, context: validate_chat_output(model_edit_output(context), context))
    shown = []

    def answer(box):
        shown.append(box.detailedText())
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, 'exec', answer)
    panel.input.setPlainText('Fix the typo please.')
    panel.send()
    qtbot.waitUntil(lambda: panel._pending is None)
    assert '“brwn” → “brown”' in shown[0]
    proposals = window.doc['review']['proposals']
    assert len(proposals) == 1 and proposals[0]['status'] == 'pending'
    # Registering a proposal does not change the text; acceptance in Review does.
    assert window.doc['blocks'][0]['text'] == 'The quick brwn fox jumps.'
    assert window.review_stack.currentIndex() == 0
    assert not window.test_errors


def test_work_mode_declined_edits_change_nothing(window, qtbot, monkeypatch, tmp_path):
    from byax.providers import validate_chat_output

    configure(tmp_path)
    panel = window.ai_panel
    panel.refresh_providers()
    panel.mode_buttons['work'].click()
    mock_chat(monkeypatch, lambda mode, context: validate_chat_output(model_edit_output(context), context))
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: QMessageBox.No)
    original = copy.deepcopy(window.doc)
    panel.input.setPlainText('Fix the typo please.')
    panel.send()
    qtbot.waitUntil(lambda: panel._pending is None)
    assert window.doc == original


def test_remote_provider_requires_payload_authorization(window, qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv('BYAX_FIXTURE_KEY', 'secret')
    configure(tmp_path, kind='openai', credential_ref='env:BYAX_FIXTURE_KEY')
    panel = window.ai_panel
    panel.refresh_providers()
    calls = mock_chat(monkeypatch, {'reply': 'ok', 'proposals': []})
    previews = []
    monkeypatch.setattr(
        'byax.gui_providers.authorize_payload', lambda w, title, preview: previews.append(preview) or False
    )
    panel.input.setPlainText('Hello')
    panel.send()
    assert previews and previews[0]['context']['mode'] == 'chat'
    assert calls == [] and panel.messages == []
    monkeypatch.setattr('byax.gui_providers.authorize_payload', lambda *args: True)
    panel.send()
    qtbot.waitUntil(lambda: panel._pending is None)
    assert len(calls) == 1


def test_thinking_loop_runs_only_while_waiting(window, qtbot, monkeypatch, tmp_path):
    import threading

    configure(tmp_path)
    panel = window.ai_panel
    panel.refresh_providers()
    window.inspector.setCurrentWidget(panel)
    release = threading.Event()

    def reply(mode, context):
        assert release.wait(3)
        return {'reply': 'done', 'proposals': []}

    mock_chat(monkeypatch, reply)
    panel.input.setPlainText('Hello')
    panel.send()
    try:
        assert panel.thinking.running and not panel.send_button.isEnabled()
        window.motion.set_reduced(True)
        assert panel.thinking.animation.state() == QAbstractAnimation.Stopped
        window.motion.set_reduced(False)
    finally:
        release.set()
    qtbot.waitUntil(lambda: panel._pending is None)
    assert not panel.thinking.running and panel.thinking.isHidden()


def test_provider_failure_is_reported_in_conversation(window, qtbot, monkeypatch, tmp_path):
    configure(tmp_path)
    panel = window.ai_panel
    panel.refresh_providers()

    def reply(mode, context):
        raise RuntimeError('fixture outage')

    mock_chat(monkeypatch, reply)
    panel.input.setPlainText('Hello')
    panel.send()
    qtbot.waitUntil(lambda: panel._pending is None)
    assert panel.messages == []
    assert 'fixture outage' in panel.history.toPlainText()
    assert panel.thinking.isHidden()


CHAT_RESPONSES = {
    'openai': ('/chat/completions', lambda text: {'choices': [{'message': {'content': text}}]}),
    'anthropic': ('/messages', lambda text: {'content': [{'type': 'text', 'text': text}]}),
    'gemini': (
        '/models/fixture-model:generateContent',
        lambda text: {'candidates': [{'content': {'parts': [{'text': text}]}}]},
    ),
    'ollama': ('/api/chat', lambda text: {'message': {'content': text}}),
}


@pytest.mark.parametrize('kind', list(CHAT_RESPONSES))
@pytest.mark.parametrize('mode', ['chat', 'work'])
def test_provider_chat_wire_protocols_state_mode(tmp_path, monkeypatch, kind, mode):
    import httpx

    from byax import engine

    doc = model.new_document(text='The quick brwn fox.')
    context = engine.context(doc, limit=20000)
    block = doc['blocks'][0]['id']
    output = json.dumps(
        {
            'reply': 'Fixed.',
            'proposed_edits': [
                {'target': block, 'start': 10, 'end': 14, 'expected_text': 'brwn', 'replacement': 'brown'}
            ],
        }
    )
    suffix, wrap = CHAT_RESPONSES[kind]
    monkeypatch.setenv('BYAX_TEST_SECRET', 'fixture-key')
    registry = ProviderRegistry(tmp_path)
    registry.save(ProviderConfig('test', kind, 'fixture-model', credential_ref='env:BYAX_TEST_SECRET'))
    requests = []

    def handler(request):
        requests.append(json.loads(request.read()))
        assert request.url.path.endswith(suffix)
        return httpx.Response(200, json=wrap(output))

    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    messages = [
        {'role': 'user', 'content': 'Hello'},
        {'role': 'assistant', 'content': 'Hi.'},
        {'role': 'user', 'content': 'Fix it.'},
    ]
    if kind != 'ollama':
        with pytest.raises(Exception, match='authorization'):
            registry.chat('test', messages, mode=mode, context=context)
        assert requests == []
    result = registry.chat('test', messages, mode=mode, context=context, authorized=True)
    body = requests[0]
    system = (
        body['systemInstruction']['parts'][0]['text']
        if kind == 'gemini'
        else body['system']
        if kind == 'anthropic'
        else body['messages'][0]['content']
    )
    assert f'Current mode: {mode.upper()}' in system
    assert 'brwn' in system
    turns = body['contents'] if kind == 'gemini' else body['messages'][-3:]
    assert [turn['role'] for turn in turns] == (
        ['user', 'model', 'user'] if kind == 'gemini' else ['user', 'assistant', 'user']
    )
    if mode == 'chat':
        assert result['proposals'] == [] and result['reply'] == output
    else:
        assert result['reply'] == 'Fixed.'
        assert result['proposals'][0]['operations'][0]['type'] == 'replace_text'
        assert result['proposals'][0]['expected_revision'] == doc['revision']


def test_work_output_rejects_unsafe_edits_and_tolerates_plain_text():
    from byax import engine
    from byax.providers import validate_chat_output

    doc = model.new_document(text='Alpha beta.')
    context = engine.context(doc)
    plain = validate_chat_output('Just an answer.', context)
    assert plain['proposals'] == [] and plain['reply'] == 'Just an answer.'
    target = doc['blocks'][0]['id']
    unsafe = validate_chat_output(
        json.dumps({'reply': 'x', 'proposed_edits': [{'type': 'delete_block', 'target': target}]}), context
    )
    assert unsafe['proposals'] == [] and 'rejected' in unsafe['rejected_edits']
    edit = {'target': target, 'start': 0, 'end': 5, 'expected_text': 'Gamma', 'replacement': 'Delta'}
    mismatch = validate_chat_output(json.dumps({'reply': 'x', 'proposed_edits': [edit]}), context)
    assert mismatch['proposals'] == []


def test_provider_chat_rejects_malformed_conversation(tmp_path):
    from byax.errors import ByaxError

    registry = configure(tmp_path)
    for messages in ([], [{'role': 'system', 'content': 'x'}], [{'role': 'assistant', 'content': 'x'}]):
        with pytest.raises(ByaxError):
            registry.chat('helper', messages)
    with pytest.raises(ByaxError):
        registry.chat('helper', [{'role': 'user', 'content': 'x'}], mode='edit-anything')
