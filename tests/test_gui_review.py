"""Review checks are explicit, scoped tasks; running one never accepts edits."""

from PySide6.QtCore import QSettings

from byax import model
from byax.gui import MainWindow


def make_window(qtbot, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / 'review.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    monkeypatch.setattr('byax.providers._default_dir', lambda: tmp_path / 'providers')
    window = MainWindow()
    qtbot.addWidget(window)
    window.doc = model.new_document(text='Review this claim.')
    window.refresh()
    return window


def test_spelling_results_belong_to_proposed_changes_and_run_from_checks(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    tabs = [window.inspector.tabText(i) for i in range(window.inspector.count())]
    assert tabs == ['Proposed changes', 'Checks', 'AI', 'Configure']
    assert window.inspector.widget(0).isAncestorOf(window.ai_panel.spelling_widget)
    assert not window.ai_panel.isAncestorOf(window.ai_panel.spelling_widget)
    assert window.ai_panel.spell_button.isHidden()
    calls = []
    monkeypatch.setattr(window.ai_panel, 'check_spelling', lambda: calls.append('spelling'))
    window.checks_panel.run_buttons['spelling'].click()
    assert calls == ['spelling']


def test_checks_are_marked_active_and_run_all_runs_only_those(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    checks = window.checks_panel
    assert checks.active_checks() == ['spelling', 'grammar', 'health']
    local, remote, spelling = [], [], []
    monkeypatch.setattr(window, 'run_checks', lambda suites: local.append(suites))
    monkeypatch.setattr(window.ai_panel, 'check_spelling', lambda: spelling.append(True))
    monkeypatch.setattr(
        'byax.gui_ai.text_providers', lambda: [{'alias': 'helper', 'kind': 'openai', 'model': 'fixture'}]
    )
    monkeypatch.setattr('byax.gui_providers.review_document', lambda window, **kwargs: remote.append(kwargs))
    checks.toggles['spelling'].setChecked(False)
    checks.toggles['factual'].setChecked(True)
    assert 'Factual accuracy' in window.active_checks_label.text()
    window.run_all_button.click()
    assert spelling == [] and local == [['grammar', 'structure', 'accessibility', 'requirements', 'print']]
    assert [item['check'] for item in remote] == ['factual']
    reopened = MainWindow()
    qtbot.addWidget(reopened)
    assert reopened.checks_panel.active_checks() == ['grammar', 'health', 'factual']
    for kind in list(checks.toggles):
        checks.toggles[kind].setChecked(False)
    assert not window.run_all_button.isEnabled()


def test_ai_checks_are_skipped_without_a_provider(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    remote = []
    monkeypatch.setattr('byax.gui_providers.review_document', lambda window, **kwargs: remote.append(kwargs))
    window.checks_panel.run_check('voice')
    assert remote == [] and 'Configure' in window.status_label.text()


def test_custom_checks_need_instructions_persist_and_route_to_review(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    checks = window.checks_panel
    calls = []
    monkeypatch.setattr(
        'byax.gui_ai.text_providers', lambda: [{'alias': 'helper', 'kind': 'openai', 'model': 'fixture'}]
    )
    monkeypatch.setattr('byax.gui_providers.review_document', lambda window, **kwargs: calls.append(kwargs))
    assert not checks.add_button.isEnabled()
    checks.custom_name.setText('Point of view')
    checks.instructions.setPlainText('Check whether each scene has a clear point of view.')
    checks.add_button.click()
    kind = next(k for k in checks.toggles if k.startswith('custom:'))
    assert checks.toggles[kind].text() == 'Point of view' and checks.toggles[kind].isChecked()
    checks.run_buttons[kind].click()
    assert calls == [
        {'check': 'custom', 'instructions': 'Check whether each scene has a clear point of view.'}
    ]
    assert model.text_of(window.doc) == 'Review this claim.'
    assert not window.doc['review']['proposals']
    reopened = MainWindow()
    qtbot.addWidget(reopened)
    assert kind in reopened.checks_panel.toggles
    reopened.checks_panel.remove_custom(kind.split(':', 1)[1])
    assert kind not in reopened.checks_panel.toggles


def test_grammar_and_evidence_checks_route_to_distinct_engines(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    checks = window.checks_panel
    local, remote = [], []
    monkeypatch.setattr(window, 'run_checks', lambda suites: local.append(suites))
    monkeypatch.setattr(
        'byax.gui_ai.text_providers', lambda: [{'alias': 'helper', 'kind': 'openai', 'model': 'fixture'}]
    )
    monkeypatch.setattr('byax.gui_providers.review_document', lambda window, **kwargs: remote.append(kwargs))
    for kind in ('grammar', 'repository', 'factual', 'voice'):
        checks.run_buttons[kind].click()
    assert local == [['grammar']]
    assert [item['check'] for item in remote] == ['repository', 'factual', 'voice']
    assert all(item['instructions'] for item in remote)
