"""The "What the AI saw" panel of the AI tab (context engine plan 8.4).

It renders :func:`byax.context_assembly.inspector_payload` for the latest request: the route and its
reason, every pinned and retrieved item with its token count, score source, library and whether it
stays local, the model, cache reads and writes, the cost estimate from the author's own rates, and
everything dropped by budget or policy. Items can be pinned for later requests or excluded; "Exclude
and re-run" is one action. The research log of a multi-step (R3) request is shown below and is
exported with the conversation.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

COLUMNS = ('Item', 'Tier', 'Tokens', 'Score', 'Library', 'Where')


def _cost_text(cost):
    def amount(value):
        if not value:
            return None
        if value.get('amount') is None:
            return value.get('reason') or 'no estimate'
        return f'{value["amount"]:.6f} {value.get("currency", "USD")}'

    before, after = amount(cost.get('before_sending')), amount(cost.get('after_reply'))
    if not before and not after:
        return cost.get('note', '')
    parts = []
    if before:
        parts.append(f'before sending (input only): {before}')
    if after:
        parts.append(f'after the reply: {after}')
    return '; '.join(parts) + ' — estimates from your rates'


class ContextInspector(QGroupBox):
    """Collapsible inspector. ``panel`` is the AIPanel that owns pins, exclusions and re-running."""

    def __init__(self, panel):
        super().__init__(panel.tr('What the AI saw'))
        self.panel = panel
        self.payload = None
        self.setCheckable(True)
        self.setChecked(False)
        self.setAccessibleName(panel.tr('What the AI saw'))
        outer = QVBoxLayout(self)
        self.body = QWidget()
        outer.addWidget(self.body)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        self.note = QLabel(
            panel.tr(
                'AI is opt-in: nothing is sent until you choose a provider and press Send. You choose the '
                'provider; local providers keep everything on this computer.'
            )
        )
        self.note.setWordWrap(True)
        self.note.setObjectName('emptyDescription')
        layout.addWidget(self.note)
        self.route_label = QLabel(panel.tr('No request yet.'))
        self.route_label.setWordWrap(True)
        self.route_label.setAccessibleName(panel.tr('Route and reason'))
        self.route_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.route_label)
        self.model_label = QLabel('')
        self.model_label.setWordWrap(True)
        self.model_label.setAccessibleName(panel.tr('Model and where it runs'))
        layout.addWidget(self.model_label)
        self.cache_label = QLabel('')
        self.cache_label.setWordWrap(True)
        self.cache_label.setAccessibleName(panel.tr('Prompt cache reads and writes'))
        layout.addWidget(self.cache_label)
        self.cost_label = QLabel('')
        self.cost_label.setWordWrap(True)
        self.cost_label.setAccessibleName(panel.tr('Estimated cost'))
        layout.addWidget(self.cost_label)
        self.items = QTreeWidget()
        self.items.setColumnCount(len(COLUMNS))
        self.items.setHeaderLabels([panel.tr(c) for c in COLUMNS])
        self.items.setRootIsDecorated(False)
        self.items.setSelectionMode(QAbstractItemView.SingleSelection)
        self.items.setAccessibleName(panel.tr('Items sent to the model'))
        self.items.setMinimumHeight(120)
        self.items.currentItemChanged.connect(self._sync_buttons)
        layout.addWidget(self.items)
        actions = QHBoxLayout()
        self.pin_button = QPushButton(panel.tr('Pin'))
        self.pin_button.setToolTip(panel.tr('Always include this item in the next requests'))
        self.pin_button.clicked.connect(self.pin_current)
        self.exclude_button = QPushButton(panel.tr('Exclude'))
        self.exclude_button.setToolTip(panel.tr('Leave this item out of the next requests'))
        self.exclude_button.clicked.connect(self.exclude_current)
        self.rerun_button = QPushButton(panel.tr('Exclude and re-run'))
        self.rerun_button.setToolTip(panel.tr('Leave this item out and ask the last question again'))
        self.rerun_button.clicked.connect(self.exclude_and_rerun)
        for button in (self.pin_button, self.exclude_button, self.rerun_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.dropped_label = QLabel(panel.tr('Left out by budget or policy'))
        layout.addWidget(self.dropped_label)
        self.dropped = QListWidget()
        self.dropped.setAccessibleName(panel.tr('Items left out by budget or policy'))
        self.dropped.setWordWrap(True)
        self.dropped.setMaximumHeight(110)
        layout.addWidget(self.dropped)
        self.log_label = QLabel(panel.tr('Research log'))
        layout.addWidget(self.log_label)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setAccessibleName(panel.tr('Research log of the last multi-step search'))
        self.log.setMaximumHeight(140)
        layout.addWidget(self.log)
        self.export_button = QPushButton(panel.tr('Export conversation…'))
        self.export_button.setToolTip(
            panel.tr('Save the conversation with each route, inspector summary and research log')
        )
        self.export_button.clicked.connect(panel.export_conversation)
        layout.addWidget(self.export_button)
        self.toggled.connect(self._toggled)
        self._toggled(False)
        self._sync_buttons()

    def _toggled(self, shown):
        self.body.setVisible(shown)

    def _current_id(self):
        item = self.items.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _sync_buttons(self, *_):
        identity = self._current_id()
        pinned = bool(identity) and identity in self.panel.pinned
        self.pin_button.setText(self.panel.tr('Unpin') if pinned else self.panel.tr('Pin'))
        self.pin_button.setEnabled(bool(identity))
        self.exclude_button.setEnabled(bool(identity))
        self.rerun_button.setEnabled(bool(identity) and self.panel.can_rerun())

    def show_payload(self, payload):
        """Render one inspector payload (before the reply, then again with cache and cost after it)."""
        self.payload = payload
        route = payload['route']
        escalated = ''.join(f' → {e["to"]}' for e in route.get('escalations') or [])
        self.route_label.setText(f'{route["route"]} {route["label"]}{escalated}: {route["reason"]}')
        model = payload.get('model') or {}
        privacy = payload.get('privacy') or {}
        text = (
            f'{model.get("alias")} · {model.get("model")} · {model.get("label")}'
            if model.get('alias')
            else self.panel.tr('No model selected')
        )
        if privacy.get('action') not in (None, 'none'):
            text += ' — ' + privacy.get('reason', '')
        self.model_label.setText(text)
        cache = payload.get('cache') or {}
        if cache.get('read_tokens') is None and cache.get('write_tokens') is None:
            self.cache_label.setText(
                self.panel.tr('Prompt cache: ') + (cache.get('note') or self.panel.tr('not reported'))
            )
        else:
            self.cache_label.setText(
                self.panel.tr('Prompt cache: {0} tokens read, {1} written').format(
                    cache.get('read_tokens') if cache.get('read_tokens') is not None else '—',
                    cache.get('write_tokens') if cache.get('write_tokens') is not None else '—',
                )
            )
        self.cost_label.setText(self.panel.tr('Cost: ') + _cost_text(payload.get('cost') or {}))
        self.items.clear()
        for item in payload['items']:
            score = item.get('score_source') or '—'
            row = QTreeWidgetItem(
                [
                    (self.panel.tr('Pinned · ') if item['id'] in self.panel.pinned else '') + item['label'],
                    item['tier'],
                    str(item['tokens']),
                    score,
                    item.get('library') or '—',
                    item.get('where') or '',
                ]
            )
            row.setData(0, Qt.UserRole, item['id'])
            row.setToolTip(0, item.get('reason') or '')
            self.items.addTopLevelItem(row)
        for column in range(len(COLUMNS)):
            self.items.resizeColumnToContents(column)
        self.dropped.clear()
        for dropped in payload.get('dropped') or []:
            self.dropped.addItem(f'{dropped.get("label") or dropped.get("id")}: {dropped.get("reason")}')
        log = payload.get('research_log')
        self.log.setPlainText(
            _log_text(log) if log else self.panel.tr('No multi-step search in this request.')
        )
        self._sync_buttons()

    def pin_current(self):
        identity = self._current_id()
        if identity:
            self.panel.toggle_pin(identity)
            if self.payload:
                self.show_payload(self.payload)

    def exclude_current(self):
        identity = self._current_id()
        if identity:
            self.panel.excluded.add(identity)
            self.panel.pinned.pop(identity, None)
            self.panel.window.status_label.setText(
                self.panel.tr(
                    'Excluded from the next requests. Choose "Exclude and re-run" to ask again now.'
                )
            )

    def exclude_and_rerun(self):
        identity = self._current_id()
        if identity:
            self.panel.exclude_and_rerun(identity)


def _log_text(log):
    flow = log.get('flow', {})
    lines = [
        (
            f'{log.get("route")}: queries {flow.get("queries", 0)} → candidates {flow.get("candidates", 0)} → '
            f'reranked {flow.get("reranked", 0)} → kept {flow.get("kept", 0)} → cited {flow.get("cited", 0)}'
        )
    ]
    for step in log.get('steps', []):
        note = f' ({step["notice"]})' if step.get('notice') else ''
        arguments = ', '.join(f'{k}={v}' for k, v in sorted(step.get('arguments', {}).items()))
        lines.append(f'{step["step"]}. {step["tool"]}({arguments}): {step["candidates"]}{note}')
    if log.get('outcome'):
        lines.append(f'Outcome: {log["outcome"]}')
    return '\n'.join(lines)
