"""Visible, removable library passages and independent off-site permission."""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
)


def select_passages(window, retrieval, provider, remote):
    dialog = QDialog(window)
    dialog.setWindowTitle('Library passages for ' + provider)
    dialog.resize(720, 600)
    layout = QVBoxLayout(dialog)
    label = (
        'Selected passages will be sent to ' + provider + '. Remove any passage before continuing.'
        if remote
        else 'Retrieval and generation stay on this computer. Verify custom local endpoints yourself.'
    )
    intro = QLabel(label)
    intro.setWordWrap(True)
    layout.addWidget(intro)
    listing = QListWidget()
    listing.setAccessibleName('Library passages to include')
    preview = QPlainTextEdit()
    preview.setReadOnly(True)
    for passage in retrieval['included']:
        title = passage.get('path', passage.get('document_id', ''))
        locator = json.dumps(passage.get('locator', {}), ensure_ascii=False)
        item = QListWidgetItem(
            f'{passage["library_name"]} · {title} · {locator} · {len(passage["text"])} characters'
        )
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setData(Qt.UserRole, passage)
        listing.addItem(item)
    listing.currentItemChanged.connect(
        lambda item, _: preview.setPlainText(item.data(Qt.UserRole)['text'] if item else '')
    )
    layout.addWidget(listing)
    layout.addWidget(preview)
    if listing.count():
        listing.setCurrentRow(0)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return None
    selected = []
    for index in range(listing.count()):
        item = listing.item(index)
        passage = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            selected.append(passage)
        else:
            retrieval['omitted'].append({'chunk_id': passage['chunk_id'], 'reason': 'Removed by user'})
    retrieval['included'] = selected
    retrieval['allowed_citation_ids'] = [p['chunk_id'] for p in selected]
    return retrieval
