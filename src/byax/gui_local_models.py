# ruff: noqa: BLE001
"""Tools > On-device search…: install, verify and remove local search models; Fast or Accurate search.

A model downloads only when the writer presses Download and confirms a dialog that names the
source, size and SHA-256 of every file. Nothing downloads in the background, and nothing the writer
has written is ever uploaded: only the model files are fetched. The same operations are available
headless as ``byax models`` (see docs/cli-reference.md).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from . import local_models

FAST = 'Fast: keyword and on-device semantic ranking'
ACCURATE = 'Accurate: also reorder the top results with an on-device reranker (slower)'
STATE_TEXT = {
    'installed': 'Installed',
    'not_installed': 'Not installed',
    'partial': 'Partly downloaded',
    'corrupt': 'Damaged: remove and download again',
}


def size_text(size):
    return f'{size / 1024**2:,.0f} MB' if size >= 1024**2 else f'{size / 1024:,.0f} KB'


def plan_text(plan):
    """The confirmation shown before a download: source, size and checksum of every file."""
    lines = [
        f'{plan["title"]} (version {plan["version"]}, licence {plan["licence"]["spdx"]})',
        f'Total download: {size_text(plan["size_bytes"])} from the {plan["source"]} source.',
        f'Stored in: {plan["destination"]}',
        '',
    ]
    for item in plan['files']:
        lines += [
            f'{item["path"]} ({size_text(item["size_bytes"])})',
            f'  {item["url"]}',
            f'  SHA-256 {item["sha256"]}',
        ]
    lines += ['', plan['verification'], plan['privacy']]
    return '\n'.join(lines)


class LocalModelPanels:
    def local_models_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('On-device search'))
        dialog.resize(760, 600)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            self.tr(
                'On-device search models run on this computer; nothing you write leaves it. '
                'A model is downloaded only when you press Download and confirm the source, size and checksum.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        runtime = QLabel()
        runtime.setObjectName('localModelsRuntime')
        runtime.setWordWrap(True)
        layout.addWidget(runtime)
        models = QListWidget()
        models.setAccessibleName(self.tr('On-device models'))
        layout.addWidget(models)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setAccessibleName(self.tr('Model source, size and checksum'))
        layout.addWidget(details)
        quality = QComboBox()
        quality.setAccessibleName(self.tr('Search quality'))
        quality.addItem(self.tr(FAST), 'fast')
        quality.addItem(self.tr(ACCURATE), 'accurate')
        layout.addWidget(QLabel(self.tr('Search quality')))
        layout.addWidget(quality)
        note = QLabel()
        note.setWordWrap(True)
        note.setObjectName('localModelsNote')
        layout.addWidget(note)

        def selected():
            item = models.currentItem()
            return item.data(Qt.UserRole) if item else None

        def refresh():
            current = selected()
            models.clear()
            for status in local_models.list_models():
                where = f' ({status["origin"]})' if status['origin'] else ''
                label = f'{status["title"]} · {size_text(status["size_bytes"])} · {self.tr(STATE_TEXT.get(status["state"], status["state"]))}{where}'
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, status['name'])
                models.addItem(item)
                if status['name'] == current:
                    models.setCurrentItem(item)
            info = local_models.runtime_status()
            runtime.setText(
                self.tr('Runtime: installed (onnxruntime {0}).').format(info['onnxruntime'])
                if info['installed']
                else self.tr('Runtime: {0}. Keyword search still works.').format(info['reason'])
            )
            settings = local_models.load_settings()
            quality.blockSignals(True)
            quality.setCurrentIndex(1 if settings['rerank'] == 'accurate' else 0)
            quality.blockSignals(False)
            note.setText(
                self.tr(
                    'Accurate search needs the reranker "{0}" installed; until then Fast results are shown. '
                    'Measured speed is in the library guide.'
                ).format(settings['reranker'])
            )
            show()

        def show():
            name = selected()
            if not name:
                details.setPlainText(self.tr('Select a model to see its source, size and checksum.'))
                return
            try:
                details.setPlainText(plan_text(local_models.download_plan(name)))
            except Exception as exc:
                details.setPlainText(str(exc))

        def guarded(fn):
            def run():
                try:
                    fn()
                except Exception as exc:
                    self.error(exc)

            return run

        def download():
            name = selected()
            if not name:
                return
            plan = local_models.download_plan(name)
            if plan['state'] == 'installed':
                self.message(self.tr('On-device search'), self.tr('This model is already installed.'))
                return
            box = QMessageBox(dialog)
            box.setWindowTitle(self.tr('Download model?'))
            box.setIcon(QMessageBox.Question)
            box.setText(
                self.tr('Download {0} ({1}) from the {2} source? Files are verified before use.').format(
                    plan['title'], size_text(plan['size_bytes']), plan['source']
                )
            )
            box.setDetailedText(plan_text(plan))
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Cancel)
            if box.exec() != QMessageBox.Yes:
                return

            def finished(result):
                self.status_label.setText(self.tr('Model {0} downloaded and verified.').format(name))
                refresh()

            self.start_job(
                'model_download',
                {'revision': self.doc['revision'], 'name': name},
                lambda payload, cancel_event: local_models.download_model(name, cancel_event=cancel_event),
                finished,
            )
            self.status_label.setText(
                self.tr('Downloading {0}… Progress is in Tools → Background jobs.').format(name)
            )

        def verify():
            name = selected()
            if not name:
                return
            result = local_models.verify_model(name)
            self.message(
                self.tr('Verify model'),
                self.tr('Every file matches its pinned size and SHA-256.')
                if result['verified']
                else self.tr('Verification failed: {0}').format(result['problems']),
            )

        def remove():
            name = selected()
            if not name:
                return
            result = local_models.remove_model(name)
            if result['bundled_copy_kept']:
                self.message(
                    self.tr('Remove model'),
                    self.tr('The copy that came with the installer stays; any extra download was removed.'),
                )
            refresh()

        def choose(index):
            try:
                local_models.save_settings({'rerank': quality.itemData(index)})
            except Exception as exc:
                self.error(exc)

        buttons = QHBoxLayout()
        for title, callback in (('Download…', download), ('Verify', verify), ('Remove download', remove)):
            button = QPushButton(self.tr(title))
            button.clicked.connect(guarded(callback))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        models.currentRowChanged.connect(lambda _row: show())
        quality.currentIndexChanged.connect(choose)
        timer = QTimer(dialog)
        timer.setInterval(1500)
        timer.timeout.connect(lambda: refresh() if not selected() else None)
        timer.start()
        refresh()
        dialog.exec()

    def enable_semantic_search(self, library, done=None):
        """Ask the on-device prompt, then configure the library and index it in a background job."""
        status = local_models.semantic_offer(library['library_id'])
        runtime, model = status['runtime'], status['model']
        if not runtime['installed'] or model['state'] != 'installed':
            reason = (
                runtime['reason']
                if not runtime['installed']
                else self.tr('The model "{0}" is not installed yet.').format(model['name'])
            )
            if (
                QMessageBox.question(
                    self,
                    self.tr('On-device semantic search'),
                    self.tr('{0}\n\nOpen Tools → On-device search to install it?').format(reason),
                )
                == QMessageBox.Yes
            ):
                self.local_models_dialog()
            return None
        answer = QMessageBox.question(
            self,
            self.tr('On-device semantic search'),
            self.tr(local_models.PROMPT),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return None
        from .libraries import LibraryIndex

        updated = local_models.enable_semantic_search(library['library_id'])
        return self.start_job(
            'library_embed',
            {'library_id': updated['library_id'], 'revision': self.doc['revision']},
            lambda payload, cancel_event: LibraryIndex(updated).rebuild_vectors(cancel_event=cancel_event),
            done
            or (lambda result: self.status_label.setText(self.tr('On-device semantic search is ready.'))),
        )

    def disable_semantic_search(self, library):
        """Turn semantic search off for a library and drop its vectors (derived data; keyword search stays)."""
        from .libraries import LibraryIndex, LibraryRegistry
        from .vector_store import open_store

        updated = LibraryRegistry().configure_embedding(library['library_id'], None)
        return open_store(LibraryIndex(updated)).discard_other_signatures('')
