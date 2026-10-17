import hashlib
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from byax import local_models
from byax.gui import MainWindow
from byax.gui_local_models import plan_text
from byax.libraries import LibraryRegistry


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('BYAX_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.delenv('BYAX_BUNDLED_MODELS', raising=False)
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.recovery_dir = tmp_path
    errors = []
    monkeypatch.setattr(w, 'error', lambda error: errors.append(str(error)))
    w.test_errors = errors
    yield w
    w.dirty = False
    w.editor.blockSignals(True)
    w.close()
    assert app


def open_dialog(window, monkeypatch, interact):
    seen = {}

    def run(dialog):
        seen['title'] = dialog.windowTitle()
        interact(dialog, seen)
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, 'exec', run)
    window.local_models_dialog()
    assert not window.test_errors
    return seen


def button(dialog, text):
    return next(b for b in dialog.findChildren(QPushButton) if b.text() == text)


def test_tools_menu_offers_on_device_search(window):
    tools = next(a.menu() for a in window.menuBar().actions() if a.text().replace('&', '') == 'Tools')
    assert 'On-device search…' in [a.text() for a in tools.actions()]


def test_dialog_names_source_size_and_checksum_and_downloads_only_after_confirmation(window, monkeypatch):
    started, downloaded = [], []
    monkeypatch.setattr(
        local_models, 'download_model', lambda name, **kw: downloaded.append(name) or {'state': 'installed'}
    )

    def start_job(kind, payload, worker, done):
        started.append((kind, payload['name']))
        done(worker(payload, None))

    monkeypatch.setattr(window, 'start_job', start_job)

    def interact(dialog, seen):
        models = dialog.findChild(QListWidget)
        names = [models.item(i).text() for i in range(models.count())]
        seen['names'] = names
        models.setCurrentRow(next(i for i, n in enumerate(names) if 'answerai-colbert' in n))
        seen['details'] = dialog.findChild(QPlainTextEdit).toPlainText()
        seen['runtime'] = dialog.findChild(QLabel, 'localModelsRuntime').text()
        monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.Cancel)
        button(dialog, 'Download…').click()
        seen['after_cancel'] = (list(started), list(downloaded))
        monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.Yes)
        button(dialog, 'Download…').click()

    seen = open_dialog(window, monkeypatch, interact)
    assert seen['title'] == 'On-device search'
    assert any('Qwen3-Embedding' in n and 'Not installed' in n for n in seen['names'])
    assert 'huggingface.co/answerdotai/answerai-colbert-small-v1/resolve/' in seen['details']
    assert (
        'SHA-256 9161e64cab96fe5a' in seen['details']
        and 'Apache-2.0' in seen['details']
        and 'MB' in seen['details']
    )
    assert 'Runtime:' in seen['runtime']
    assert seen['after_cancel'] == ([], []), 'declining the confirmation downloads nothing'
    assert started == [('model_download', 'answerai-colbert-small-v1')]
    assert downloaded == ['answerai-colbert-small-v1']


def test_search_quality_defaults_to_fast_and_the_choice_is_saved(window, monkeypatch):
    def interact(dialog, seen):
        combo = dialog.findChild(QComboBox)
        seen['before'] = combo.currentData()
        combo.setCurrentIndex(1)

    seen = open_dialog(window, monkeypatch, interact)
    assert seen['before'] == 'fast'
    assert local_models.load_settings()['rerank'] == 'accurate'


def test_plan_text_lists_every_file_with_url_and_hash():
    plan = local_models.download_plan('answerai-colbert-small-v1')
    text = plan_text(plan)
    for item in plan['files']:
        assert item['url'] in text and item['sha256'] in text
    assert 'Downloaded only on your request' in text


def tiny_embedder_catalogue(monkeypatch, tmp_path, *, installed):
    body = b'12345'
    digest = hashlib.sha256(body).hexdigest()
    entry = {
        'name': 'tiny',
        'title': 'Tiny',
        'kind': 'embedder',
        'version': '1',
        'licence': {'spdx': 'Apache-2.0'},
        'runtime': {'native_dimension': 8, 'dimension': 6},
        'files': [
            {
                'path': 'model.onnx',
                'size': 5,
                'sha256': digest,
                'url': 'https://example.invalid/m',
                'mirror_asset': 'tiny__m',
            }
        ],
    }
    catalogue = local_models.validate_catalogue(
        {
            'schema': 1,
            'mirror': {'base_url': 'https://example.invalid', 'populated': False},
            'models': [entry],
        }
    )
    monkeypatch.setattr(local_models, 'load_catalogue', lambda path=None: catalogue)
    settings = tmp_path / 'config' / 'ByAuthorX'
    settings.mkdir(parents=True, exist_ok=True)
    (settings / 'local-models.json').write_text('{"embedder": "tiny", "reranker": "tiny"}')
    if installed:
        # The flow under test only needs the runtime to be reported present (the embedding job itself is
        # replaced). Without the local-models extra the real check would open the modal install dialog,
        # whose exec() never returns in a test run.
        monkeypatch.setattr(
            local_models, 'runtime_status', lambda: {'installed': True, 'extra': 'byauthorx[local-models]'}
        )
        target = tmp_path / 'models' / 'tiny'
        target.mkdir(parents=True)
        (target / 'model.onnx').write_bytes(body)
        local_models._write_manifest(entry, target, 'upstream')
    return catalogue


@pytest.fixture
def library(tmp_path, monkeypatch):
    from byax import libraries

    monkeypatch.setattr(libraries, '_default_dir', lambda: tmp_path / 'libconfig')
    root = tmp_path / 'research'
    root.mkdir()
    (root / 'a.txt').write_text('Mars has two moons.', encoding='utf-8')
    return LibraryRegistry().create('Research', [str(root)])


def test_enable_prompt_wording_and_no_is_the_default(window, monkeypatch, tmp_path, library):
    tiny_embedder_catalogue(monkeypatch, tmp_path, installed=True)
    asked = {}

    def question(parent, title, text, buttons=None, default=None):
        asked.update(text=text, default=default)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, 'question', question)
    jobs = []
    monkeypatch.setattr(window, 'start_job', lambda *a, **k: jobs.append(a))
    assert window.enable_semantic_search(library) is None
    assert asked['text'] == 'Enable on-device semantic search? Nothing leaves this computer.'
    assert asked['default'] == QMessageBox.No
    assert jobs == [] and LibraryRegistry().get(library['library_id'])['embedding'] is None


def test_confirming_configures_the_library_and_starts_a_cancellable_job(
    window, monkeypatch, tmp_path, library
):
    tiny_embedder_catalogue(monkeypatch, tmp_path, installed=True)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    jobs = []
    monkeypatch.setattr(
        window, 'start_job', lambda kind, payload, worker, done: jobs.append((kind, payload)) or {'id': 'j'}
    )
    assert window.enable_semantic_search(library) == {'id': 'j'}
    assert jobs[0][0] == 'library_embed' and jobs[0][1]['library_id'] == library['library_id']
    assert LibraryRegistry().get(library['library_id'])['embedding']['provider'] == 'local:tiny'
    removed = window.disable_semantic_search(library)
    assert removed == 0 and LibraryRegistry().get(library['library_id'])['embedding'] is None


def test_a_missing_model_offers_the_install_dialog_instead_of_downloading(
    window, monkeypatch, tmp_path, library
):
    tiny_embedder_catalogue(monkeypatch, tmp_path, installed=False)
    prompts, opened = [], []
    monkeypatch.setattr(
        QMessageBox, 'question', lambda parent, title, text, *a: prompts.append(text) or QMessageBox.Yes
    )
    monkeypatch.setattr(window, 'local_models_dialog', lambda: opened.append(True))
    monkeypatch.setattr(
        window, 'start_job', lambda *a, **k: pytest.fail('nothing may start without the model')
    )
    assert window.enable_semantic_search(library) is None
    assert 'not installed yet' in prompts[0] or 'not installed' in prompts[0]
    assert opened == [True]
    assert LibraryRegistry().get(library['library_id'])['embedding'] is None


def test_library_panel_has_the_semantic_buttons(window, monkeypatch, tmp_path, library):
    window.doc['settings']['libraries'] = [
        {'library_id': library['library_id'], 'name': 'Research', 'role': 'supplementary'}
    ]
    seen = {}

    def run(dialog):
        seen['buttons'] = [b.text() for b in dialog.findChildren(QPushButton)]
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, 'exec', run)
    window.library_panel()
    assert (
        'Enable on-device semantic search…' in seen['buttons']
        and 'Turn off semantic search' in seen['buttons']
    )
