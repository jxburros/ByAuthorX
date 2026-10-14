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


def test_spelling_is_in_review_and_uses_single_go_control(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    checks = window.review_checks
    assert window.inspector.widget(0).isAncestorOf(window.ai_panel.spelling_widget)
    assert not window.ai_panel.isAncestorOf(window.ai_panel.spelling_widget)
    assert window.ai_panel.spell_button.isHidden()
    calls = []
    monkeypatch.setattr(window.ai_panel, 'check_spelling', lambda: calls.append('spelling'))
    checks.go.click()
    assert calls == ['spelling']


def test_custom_check_requires_instructions_and_routes_to_review(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    checks = window.review_checks
    calls = []
    monkeypatch.setattr('byax.gui_providers.review_document', lambda window, **kwargs: calls.append(kwargs))
    checks.options.setCurrentIndex(checks.options.findData('custom'))
    assert not checks.go.isEnabled()
    checks.instructions.setPlainText('Check whether each scene has a clear point of view.')
    checks.go.click()
    assert calls == [
        {'check': 'custom', 'instructions': 'Check whether each scene has a clear point of view.'}
    ]
    assert model.text_of(window.doc) == 'Review this claim.'
    assert not window.doc['review']['proposals']


def test_grammar_and_evidence_checks_route_to_distinct_engines(qtbot, monkeypatch, tmp_path):
    window = make_window(qtbot, monkeypatch, tmp_path)
    checks = window.review_checks
    local, remote = [], []
    monkeypatch.setattr(window, 'run_checks', lambda suite: local.append(suite))
    monkeypatch.setattr('byax.gui_providers.review_document', lambda window, **kwargs: remote.append(kwargs))
    checks.options.setCurrentIndex(checks.options.findData('grammar'))
    checks.go.click()
    for kind in ('repository', 'factual', 'voice'):
        checks.options.setCurrentIndex(checks.options.findData(kind))
        checks.go.click()
    assert local == ['grammar']
    assert [item['check'] for item in remote] == ['repository', 'factual', 'voice']
    assert all(item['instructions'] for item in remote)
