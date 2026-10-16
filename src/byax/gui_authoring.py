"""Guided research, readable evidence and review, and deliberate publication."""

import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .authoring import citation_evidence, create_research_sample
from .publication import PUBLICATION_CATEGORIES, export_clean, publication_preview


def readable_review(record):
    """Human-readable explanation followed by inspectable supporting evidence."""
    parts = []
    if 'expected_text' in record:
        parts += ['Before', record['expected_text'], '\nProposed wording', record.get('replacement', '')]
    if record.get('claim'):
        parts += ['Claim', record['claim']]
    parts += ['\nReason', record.get('explanation') or record.get('message') or 'No explanation supplied.']
    parts += ['\nReview status', str(record.get('status', 'needs review'))]
    constraints = record.get('author_constraints')
    if constraints:
        parts += ['\nAuthor instructions', json.dumps(constraints, ensure_ascii=False, indent=2)]
    if record.get('outcome'):
        parts += ['\nAssessment (requires review)', str(record['outcome'])]
    if record.get('evidence'):
        parts += ['\nEvidence', json.dumps(record['evidence'], ensure_ascii=False, indent=2)]
    parts += ['\nLocation', str(record.get('target') or record.get('affected_ids') or 'Document')]
    if record.get('range'):
        parts.append(str(record['range']))
    return '\n'.join(parts)


def readable_evidence(row, translate):
    if 'finding' in row:
        return readable_review(row['finding'])
    passages = '\n\n'.join(
        json.dumps(p.get('locator', {}), ensure_ascii=False) + '\n' + p['text']
        for p in row.get('passages', [])
    )
    return '\n'.join(
        [
            translate('Claim'),
            row.get('claim', ''),
            '\n' + row.get('title', ''),
            json.dumps(row.get('locator', {}), ensure_ascii=False),
            row.get('status', ''),
            row.get('method', ''),
            row.get('outcome', ''),
            row.get('reason', ''),
            '\n' + translate('Current indexed passages'),
            passages or translate('No current passage available.'),
        ]
    )


class AuthorWorkflows:
    def build_author_start(self, workspace_layout):
        self.author_start = QWidget()
        row = QHBoxLayout(self.author_start)
        label = QLabel(self.tr('Write with AI. Keep your voice, your sources, and the final say.'))
        label.setWordWrap(True)
        row.addWidget(label, 1)
        start = QPushButton(self.tr('Research to publication…'))
        start.clicked.connect(self.research_walkthrough)
        row.addWidget(start)
        dismiss = QPushButton(self.tr('Dismiss introduction'))

        def hide():
            self.settings.setValue('onboarding/dismissed', True)
            self.author_start.hide()

        dismiss.clicked.connect(hide)
        row.addWidget(dismiss)
        workspace_layout.insertWidget(0, self.author_start)

    def research_walkthrough(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Research to publication'))
        dialog.resize(590, 460)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            self.tr(
                'Write with AI. Keep your voice, your sources, and the final say. '
                'Use your own document or create an offline sample with fictional research. '
                'The sample is created locally and needs no model.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        steps = (
            ('Create sample project…', self.create_research_walkthrough),
            ('1. Attach your research folder…', self.library_panel),
            ('2. Cite selected text…', self.cite_selection),
            ('3. Inspect claims and evidence…', self.inspect_evidence),
            ('4. Review wording and voice', self.focus_author_review),
            ('5. Prepare to share…', self.prepare_to_share),
        )
        for label, callback in steps:
            button = QPushButton(self.tr(label))
            button.clicked.connect(callback)
            layout.addWidget(button)
        note = QLabel(
            self.tr(
                'Select text before citing. Local evidence inspection needs no model. '
                'AI review needs a configured provider; inspect and accept proposals in Review. '
                'Close this guide to write, then reopen it from Help at any time.'
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        # A modeless guide allows selecting and writing in the document between steps.
        self._research_guide = dialog
        dialog.show()

    def create_research_walkthrough(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr('Choose a folder for the sample project'))
        if not folder:
            return

        def finished(path):
            if self.open_path(path):
                self.status_label.setText(
                    self.tr(
                        'Sample ready. Inspect its quotation, revise the uncited conclusion, then prepare to share.'
                    )
                )

        self.background(self.tr('Creating offline sample…'), lambda: create_research_sample(folder), finished)

    def focus_author_review(self):
        self.inspector.setCurrentIndex(0)
        self.inspector.parentWidget().show()
        self.review_checks.options.setCurrentIndex(self.review_checks.options.findData('voice'))
        self.review_checks.brief.setFocus()

    def inspect_evidence(self, *, offset=0):
        if not self.flush_edits():
            return
        frozen = copy.deepcopy(self.doc)
        profile = frozen.get('profiles', {}).get(getattr(self, 'active_profile', None))

        def finished(report):
            if (self.doc['id'], self.doc['revision']) != (frozen['id'], frozen['revision']):
                self.message(
                    self.tr('Evidence changed'), self.tr('The document changed. Inspect evidence again.')
                )
                return
            self.show_evidence(report)

        self.background(
            self.tr('Inspecting local evidence…'),
            lambda: citation_evidence(frozen, profile=profile, offset=offset),
            finished,
        )

    def show_evidence(self, report):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Claims and evidence'))
        dialog.resize(800, 650)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            self.tr(
                'Quotation matches establish a text match, not the truth of a claim. '
                'Paraphrases and AI assessments need interpretation. Changed or missing sources are not verified.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if report.get('total'):
            layout.addWidget(
                QLabel(
                    self.tr('Citations {0}–{1} of {2}').format(
                        report.get('offset', 0) + 1,
                        report.get('offset', 0) + len(report['citations']),
                        report['total'],
                    )
                )
            )
        listing = QListWidget()
        listing.setAccessibleName(self.tr('Claims, source status and assessment method'))
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setAccessibleName(self.tr('Claim, exact source passages and locators'))
        rows = report['citations']
        for row in rows:
            listing.addItem(f'{row["status"]} · {row["method"]} · {row["claim"][:85]}')
            listing.item(listing.count() - 1).setData(Qt.UserRole, row)
        findings = [
            *self.doc['review'].get('findings', []),
            *[
                finding
                for finding in (self._last_report or {}).get('findings', [])
                if finding.get('document_id') == self.doc['id']
            ],
        ]
        seen = set()
        for finding in findings:
            key = finding.get('id') or json.dumps(finding, sort_keys=True)
            if key in seen or not (finding.get('evidence') or finding.get('outcome')):
                continue
            seen.add(key)
            listing.addItem(
                self.tr('AI / review assessment · ')
                + str(finding.get('status', 'needs review'))
                + ' · '
                + str(finding.get('claim') or finding.get('explanation', ''))[:85]
            )
            listing.item(listing.count() - 1).setData(Qt.UserRole, {'finding': finding})
        if not listing.count():
            details.setPlainText(
                self.tr('No citations or evidence assessments yet. Select a passage and choose Cite.')
            )

        def show(item, _=None):
            details.setPlainText(readable_evidence(item.data(Qt.UserRole) if item else {}, self.tr))

        def locate(item):
            if self.doc['revision'] != report['revision']:
                return
            row = item.data(Qt.UserRole)
            target = row.get('target') or row.get('finding', {}).get('target')
            if target:
                self.set_file_view('byax')
                self.navigate_target(target)
                dialog.accept()

        listing.currentItemChanged.connect(show)
        listing.itemActivated.connect(locate)
        layout.addWidget(listing)
        layout.addWidget(details, 1)
        if listing.count():
            listing.setCurrentRow(0)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        for label, offset in (
            ('Previous citations', max(0, report.get('offset', 0) - 100) if report.get('offset') else None),
            ('Next citations', report.get('next_offset')),
        ):
            if offset is not None:
                page = buttons.addButton(self.tr(label), QDialogButtonBox.ActionRole)

                def change_page(checked=False, offset=offset):
                    dialog.accept()
                    self.inspect_evidence(offset=offset)

                page.clicked.connect(change_page)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def show_readable_review(self, record):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Review wording and evidence'))
        dialog.resize(740, 570)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setAccessibleName(self.tr('Before and proposed wording, reason and author instructions'))
        text.setPlainText(readable_review(record))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def prepare_to_share(self):
        if self.flush_edits():
            ShareDialog(self).exec()


class ShareDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.frozen = copy.deepcopy(window.doc)
        self.generation = 0
        self.setWindowTitle(self.tr('Prepare to share'))
        self.resize(790, 720)
        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr(
                'Create a separate publication copy. Your master keeps its history and research. '
                'Choose any private material to include, then inspect format limitations before exporting.'
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.format = QComboBox()
        self.format.setAccessibleName(self.tr('Publication format'))
        from .formats import FORMATS

        for key in ('pdf', 'docx', 'odt', 'md', 'html', 'rtf', 'txt'):
            self.format.addItem(FORMATS[key]['label'], key)
        layout.addWidget(self.format)
        self.categories = {}
        options = QWidget()
        options_layout = QVBoxLayout(options)
        for key, label in PUBLICATION_CATEGORIES.items():
            box = QCheckBox(self.tr('Include: ') + self.tr(label))
            box.toggled.connect(self.refresh_preview)
            self.categories[key] = box
            options_layout.addWidget(box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAccessibleName(self.tr('Private material inclusion options'))
        scroll.setWidget(options)
        layout.addWidget(scroll, 1)
        checks = QHBoxLayout()
        self.required = {}
        for suite in ('structure', 'requirements', 'print'):
            box = QCheckBox(self.tr('Require check: ') + self.tr(suite))
            box.toggled.connect(self.refresh_preview)
            self.required[suite] = box
            checks.addWidget(box)
        layout.addLayout(checks)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.losses = QListWidget()
        self.losses.setAccessibleName(self.tr('Publication limitations; activate to locate in master'))
        self.losses.itemActivated.connect(self.locate_loss)
        layout.addWidget(self.losses, 1)
        self.publish = QPushButton(self.tr('Export publication copy…'))
        self.publish.clicked.connect(self.export_copy)
        layout.addWidget(self.publish)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.format.currentIndexChanged.connect(self.refresh_preview)
        self.refresh_preview()

    def profile(self):
        return {
            **{key: box.isChecked() for key, box in self.categories.items()},
            'required_suites': [key for key, box in self.required.items() if box.isChecked()],
            'allow_markdown_losses': True,
        }

    def refresh_preview(self, *_):
        self.generation += 1
        generation = self.generation
        self.publish.setEnabled(False)
        self.summary.setText(self.tr('Inspecting publication copy…'))
        fmt, profile = self.format.currentData(), self.profile()

        def done(report):
            if generation != self.generation:
                return
            self.losses.clear()
            for loss in report.get('losses', []):
                self.losses.addItem(loss['message'])
                self.losses.item(self.losses.count() - 1).setData(Qt.UserRole, loss.get('target'))
            check = report.get('checks')
            for finding in (check or {}).get('findings', []):
                self.losses.addItem(
                    self.tr('Check: ') + (finding.get('message') or finding.get('explanation', ''))
                )
                target = finding.get('target') or next(iter(finding.get('affected_ids', [])), None)
                self.losses.item(self.losses.count() - 1).setData(Qt.UserRole, target)
            ready = not check or check.get('validation') == 'passed'
            self.publish.setEnabled(ready)
            inclusion = report['inclusion']
            self.summary.setText(
                self.tr(
                    '{0} private categories included; {1} excluded. {2} format limitations. Checks: {3}. '
                    'Word, OpenDocument and RTF layouts may differ in other applications.'
                ).format(
                    len(inclusion['included']),
                    len(inclusion['excluded']),
                    self.losses.count(),
                    check['validation'] if check else self.tr('not requested'),
                )
            )

        self.window.background(
            self.tr('Preparing publication preview…'),
            lambda: publication_preview(self.frozen, fmt, profile),
            done,
            failed=lambda: self.summary.setText(
                self.tr('Preview failed. Change the options or reopen Prepare to share.')
            ),
        )

    def locate_loss(self, item):
        if not self.current():
            return
        target = item.data(Qt.UserRole)
        if target:
            self.window.set_file_view('byax')
            self.window.navigate_target(target)
            self.reject()

    def current(self):
        if not self.window.flush_edits():
            return False
        if (self.window.doc['id'], self.window.doc['revision']) != (
            self.frozen['id'],
            self.frozen['revision'],
        ):
            self.window.message(
                self.tr('Publication preview expired'),
                self.tr('The document changed. Prepare to share again.'),
            )
            self.reject()
            return False
        return True

    def export_copy(self):
        if not self.publish.isEnabled() or not self.current():
            return
        fmt, profile = self.format.currentData(), self.profile()
        suggestion = str(Path(self.window.export_suggestion()).with_suffix('.' + fmt))
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr('Export publication copy'), suggestion, '*.' + fmt
        )
        if not path or not self.current():
            return
        destination = Path(path)
        if destination.suffix.lower() != '.' + fmt:
            self.window.message(
                self.tr('Filename and format differ'),
                self.tr(
                    'Choose a filename ending in .{0}, or select the matching publication format.'
                ).format(fmt),
            )
            return
        path = str(destination)
        if self.window.path and Path(path).resolve() == self.window.path.resolve():
            self.window.message(
                self.tr('Choose another path'),
                self.tr('Keep the master and publication copy in separate files.'),
            )
            return
        self.window.background(
            self.tr('Exporting publication copy…'),
            lambda: export_clean(self.frozen, path, format=fmt, profile=profile),
            lambda report: self.window.report(self.tr('Publication result and included material'), report),
        )
        self.accept()
