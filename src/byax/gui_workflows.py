# ruff: noqa: BLE001
"""Native access to reusable settings, dependencies, styles and branch workflows."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from . import engine, profiles


class WorkflowPanels:
    def edit_layer_properties(self):
        from .gui_document_settings import LayerDialog

        item = self.layer_list.currentItem()
        if item and self.flush_edits():
            layer = item.data(Qt.UserRole)
            if layer.get('kind') == 'group':
                self.message(
                    self.tr('Layer group'), self.tr('Select a member to edit its geometry and output policy.')
                )
                return
            LayerDialog(self, layer).exec()

    def group_selected_layers(self):
        targets = [item.data(Qt.UserRole)['id'] for item in self.layer_list.selectedItems()]
        if not targets:
            return
        name, ok = QInputDialog.getText(self, self.tr('Group layers'), self.tr('Group name'))
        if ok and name.strip():
            self.commit([{'type': 'group_layers', 'name': name.strip(), 'targets': targets}])

    def ungroup_selected_layers(self):
        item = self.layer_list.currentItem()
        if item:
            layer = item.data(Qt.UserRole)
            target = layer.get('group') or (layer['id'] if layer.get('kind') == 'group' else None)
            if target:
                self.commit([{'type': 'ungroup_layers', 'target': target}])

    def reorder_selected_layer(self, direction):
        item = self.layer_list.currentItem()
        if item:
            index = self.layer_list.row(item) + direction
            if 0 <= index < self.layer_list.count():
                self.commit(
                    [{'type': 'reorder_layer', 'target': item.data(Qt.UserRole)['id'], 'to_index': index}]
                )

    def edit_export_policies(self):
        from .gui_document_settings import ExportPolicyDialog

        if self.flush_edits():
            ExportPolicyDialog(self).exec()

    def edit_page_masters(self):
        from .gui_document_settings import MasterDialog

        if self.flush_edits():
            MasterDialog(self).exec()

    def edit_section_profiles(self):
        from .gui_document_settings import SectionProfileDialog

        if not self.flush_edits():
            return
        if not any(b['type'] == 'heading' for b in self.doc['blocks']):
            self.message(self.tr('Section profiles'), self.tr('Add a heading to define a section first.'))
            return
        SectionProfileDialog(self).exec()

    def linked_assets(self):
        from .dependencies import refresh_operations

        if not self.flush_edits():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Linked assets'))
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        items.setAccessibleName(self.tr('Linked files and retrieval status'))
        for entry in engine.dependency_report(self.doc)['dependencies']:
            items.addItem(f'{entry["uri"]} — {entry["status"]}')
            items.item(items.count() - 1).setData(Qt.UserRole, entry['id'])
        layout.addWidget(items)
        network = QCheckBox(self.tr('Allow network retrieval for the selected asset'))
        layout.addWidget(network)
        row = QHBoxLayout()

        def selected():
            return items.currentItem().data(Qt.UserRole) if items.currentItem() else None

        def refresh():
            target = selected()
            if not target:
                return
            doc = self.doc
            revision = doc['revision']
            base = self.path or Path(doc['metadata'].get('import', {}).get('original_path', '.'))

            def done(result):
                if self.doc['revision'] != revision:
                    self.error(self.tr('The document changed during retrieval. Inspect the asset and retry.'))
                    return
                operations, report = result
                if self.commit(operations):
                    self.report(self.tr('Asset refresh'), {'outcomes': report})

            allow_network = network.isChecked()
            self.background(
                self.tr('Retrieving selected asset…'),
                lambda: refresh_operations(
                    doc, [target], base_dir=base.parent, authorize=True, allow_network=allow_network
                ),
                done,
            )

        def embed():
            if selected() and self.commit([{'type': 'embed_dependency', 'target': selected()}]):
                dialog.accept()

        for title, callback in (('Retrieve selected asset', refresh), ('Embed retrieved asset', embed)):
            button = QPushButton(self.tr(title))
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def reusable_settings(self):
        if not self.flush_edits():
            return
        options = [self.tr('Export settings collection'), self.tr('Import settings collection')]
        choice, ok = QInputDialog.getItem(
            self, self.tr('Reusable settings'), self.tr('Action'), options, 0, False
        )
        if not ok:
            return
        try:
            if choice == options[0]:
                path, _ = QFileDialog.getSaveFileName(
                    self, self.tr('Export reusable settings'), '', self.tr('Settings collections (*.json)')
                )
                if path:
                    profiles.write_collection(profiles.export_settings(self.doc), path)
            else:
                path, _ = QFileDialog.getOpenFileName(
                    self, self.tr('Import reusable settings'), '', self.tr('Settings collections (*.json)')
                )
                if not path:
                    return
                policies = [
                    self.tr('Report conflicts'),
                    self.tr('Keep current settings'),
                    self.tr('Replace conflicting settings'),
                ]
                policy, ok = QInputDialog.getItem(
                    self,
                    self.tr('Import reusable settings'),
                    self.tr('Existing settings'),
                    policies,
                    0,
                    False,
                )
                if not ok:
                    return
                compiled = profiles.compile_collection(
                    self.doc,
                    profiles.read_collection(path),
                    on_conflict=['error', 'keep', 'replace'][policies.index(policy)],
                )
                operations, report = compiled['operations'], compiled['report']
                self.report(self.tr('Settings collection preview'), report)
                if operations:
                    self.commit(operations)
        except Exception as exc:
            self.error(exc)

    def character_style(self):
        names = [name for name, value in self.doc['styles'].items() if value.get('kind') == 'character']
        if not names:
            self.message(self.tr('Character styles'), self.tr('Define a character style first.'))
            return
        name, ok = QInputDialog.getItem(self, self.tr('Character style'), self.tr('Style'), names, 0, False)
        if ok:
            self.commit(
                [
                    {
                        'type': 'apply_character_style',
                        'target': target,
                        'start': start,
                        'end': end,
                        'style': name,
                    }
                    for target, start, end in self.editor.selection_targets()
                    if end > start
                ]
            )

    def formatting_inspector(self):
        from .formatting import element_report

        target = self.editor.current_id()
        if target:
            self.report(
                self.tr('Styles and direct overrides'),
                element_report(
                    self.doc,
                    next(
                        obj
                        for obj in [
                            *self.doc['blocks'],
                            *[
                                cell
                                for block in self.doc['blocks']
                                for row in block.get('rows', [])
                                for cell in row['cells']
                            ],
                        ]
                        if obj['id'] == target
                    ),
                ),
            )

    def layer_inspector(self):
        from .layout import layer_inspector

        self.report(self.tr('Layers and output visibility'), {'layers': layer_inspector(self.doc)})

    def switch_draft(self):
        if not self.flush_edits():
            return
        names = list(self.doc['history']['branches'])
        name, ok = QInputDialog.getItem(
            self, self.tr('Switch draft branch'), self.tr('Branch'), names, 0, False
        )
        if ok:
            try:
                self.doc, _ = engine.switch_branch(self.doc, name)
                self._undo.clear()
                self._redo.clear()
                self.dirty = True
                self.refresh()
            except Exception as exc:
                self.error(exc)

    def merge_draft(self):
        names = list(self.doc['history']['branches'])
        name, ok = QInputDialog.getItem(
            self, self.tr('Compare and merge draft'), self.tr('Source branch'), names, 0, False
        )
        if ok:
            self.commit([{'type': 'propose_merge', 'source': name}])

    def export_profiles(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Export profile'))
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tr('Choose the saved publication policy for this export.')))
        choice = QComboBox()
        choice.setAccessibleName(self.tr('Saved export profile'))
        choice.addItem(self.tr('Default clean publication'), None)
        for name, profile in self.doc.get('profiles', {}).items():
            if profile.get('format') or profile.get('clean') or 'include_comments' in profile:
                choice.addItem(name, name)
        layout.addWidget(choice)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self._selected_export_profile = choice.currentData()
            self.export()

    def review_proposal_edits(self, record, accept=True):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Select proposed changes'))
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                self.tr(
                    'Select changes to accept or reject. Required earlier changes are listed beside each item.'
                )
            )
        )
        items = QListWidget()
        items.setAccessibleName(self.tr('Proposed changes and dependencies'))
        for edit in record.get('edits', []):
            if edit.get('status') != 'pending':
                continue
            operation = record['operations'][edit['index']]
            target = next(
                (b.get('text', '') for b in self.doc['blocks'] if b['id'] == edit.get('target')), ''
            )
            description = operation.get('replacement', operation.get('text', operation.get('style', '')))
            label = self.tr('{0}: {1} → {2}').format(edit['index'] + 1, target[:80], str(description)[:100])
            if edit.get('depends_on'):
                label += self.tr(' · Requires {0}').format(', '.join(edit['depends_on']))
            items.addItem(label)
            item = items.item(items.count() - 1)
            item.setData(Qt.UserRole, edit['id'])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
        layout.addWidget(items)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return False
        selected = [
            items.item(i).data(Qt.UserRole)
            for i in range(items.count())
            if items.item(i).checkState() == Qt.Checked
        ]
        if not selected:
            return False
        return self.commit(
            [
                {
                    'type': 'accept_proposal' if accept else 'reject_proposal',
                    'target': record['id'],
                    'edits': selected,
                }
            ]
        )

    def resolve_merge_conflicts(self):
        item = self.review_list.currentItem()
        if not item:
            return
        kind, record = item.data(Qt.UserRole)
        if kind != 'proposals':
            return
        operations = []
        for conflict in record.get('conflicts', []):
            if conflict.get('status') != 'unresolved':
                continue
            self.report(self.tr('Draft conflict comparison'), conflict)
            options = [self.tr('Keep current draft'), self.tr('Use incoming draft')]
            choice, ok = QInputDialog.getItem(
                self, self.tr('Resolve draft conflict'), self.tr('Resolution'), options, 0, False
            )
            if not ok:
                return
            operations.append(
                {
                    'type': 'resolve_conflict',
                    'target': record['id'],
                    'conflict': conflict['id'],
                    'choice': 'ours' if choice == options[0] else 'theirs',
                }
            )
        if operations:
            self.commit(operations)

    def resolve_selected_changes(self, accept):
        records = [item.data(Qt.UserRole) for item in self.review_list.selectedItems()]
        if len(records) < 2 or any(kind != 'tracked_changes' for kind, _ in records):
            return False
        self.commit(
            [
                {
                    'type': 'accept_changes' if accept else 'reject_changes',
                    'targets': [record['id'] for _, record in records],
                }
            ]
        )
        return True
