#!/usr/bin/env python3
"""Capture the documentation screenshots from Qt's offscreen renderer with a sample document.

Run it after any change to the interface typography or branding, then review the six PNGs it writes to
docs/images. The sample document is set in Source Serif 4 as an explicit specimen choice; the busy capture
holds a local task open, and the saved and review captures show the save confirmation.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import byax.gui as gui  # noqa: E402
from byax import model  # noqa: E402
from byax.gui_editor import ProofMark  # noqa: E402

TEXT = (
    'The space to write\n'
    'A useful draft begins with a clear page and room to think. I keep the first pass simple: one thought, '
    'then the next.\n'
    'A second look\n'
    'Review adds perspective without taking over the page. Keep the words that sound like you; refine the '
    'ones that get in the way.'
)


def sample_document():
    doc = model.new_document(title='Notes on the first draft', text=TEXT)
    doc['blocks'][0]['style'] = 'heading1'
    doc['blocks'][2]['style'] = 'heading1'
    doc['styles']['body'].update(font_family='Source Serif 4', font_size=16)
    return doc


MARKED = (
    'The space to write\n'
    'A useful draft begins with a clear page and room to think. I keep teh first pass simple: one thought, '
    'then the next. The old opening was cut and replaced; the new one is underlined here.\n'
    'A second look\n'
    'Review adds perspective without taking over the page. Keep the words that sound like you; refine the '
    'ones that get in the way, an the sytem works.'
)


def marked_document():
    """The sample document with spelling and grammar marks, an underline and a strikethrough."""
    doc = model.new_document(title='Notes on the first draft', text=MARKED)
    for index in (0, 2):
        doc['blocks'][index]['style'] = 'heading1'
    doc['styles']['body'].update(font_family='Source Serif 4', font_size=16)
    first, second = doc['blocks'][1], doc['blocks'][3]
    for block, phrase, kind in (
        (first, 'The old opening was cut', 'strike'),
        (first, 'underlined here', 'underline'),
    ):
        start = block['text'].index(phrase)
        block['marks'].append({'type': kind, 'start': start, 'end': start + len(phrase)})
    return doc


def proof_marks(doc):
    first, second = doc['blocks'][1], doc['blocks'][3]
    found = []
    for block, word, kind, label in (
        (first, 'teh', 'spelling', 'Possible misspelling: teh'),
        (second, 'an the', 'grammar', 'Check the grammar here'),
        (second, 'sytem', 'spelling', 'Possible misspelling: sytem'),
    ):
        start = block['text'].index(word)
        found.append(ProofMark(block['id'], start, start + len(word), kind, label, block['text']))
    return found


def make_window(app, settings_dir):
    settings = QSettings(str(Path(settings_dir) / 'appearance.ini'), QSettings.IniFormat)
    gui.QSettings = lambda *_: settings
    window = gui.MainWindow()
    window.resize(1320, 900)
    window.show()
    window.doc = sample_document()
    window.path = Path(settings_dir) / 'notes.byax'
    window.dirty = False
    window.refresh()
    window.activateWindow()
    app.setActiveWindow(window)
    QTest.qWait(300)
    return window


def save(widget, path):
    QApplication.processEvents()
    widget.grab().save(str(path))
    print('wrote', path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'docs' / 'images')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as settings_dir:
        window = make_window(app, settings_dir)
        save(window, args.out / 'editor.png')

        window._saved_feedback()
        QTest.qWait(400)
        save(window, args.out / 'editor-saved.png')

        window.doc['review']['comments'].append(
            {'id': 'comment-one', 'text': 'Keep the opening. This sounds like your voice.', 'status': 'open'}
        )
        window.dirty = True
        window.update_sidebar()
        window.update_status()
        window.review_list.setCurrentRow(0)
        save(window, args.out / 'editor-review.png')

        window.doc['review']['comments'].clear()
        window.dirty = False
        window.update_sidebar()
        window.update_status()
        window._begin_activity('capture', 'Checking document…')
        QTest.qWait(1400)  # the loop holds its fully written pose from about 1.1 s to 1.4 s
        save(window, args.out / 'editor-busy.png')
        window._end_activity('capture')

        window.doc = marked_document()
        window.dirty = False
        window.refresh()
        window.editor.set_proof_marks(proof_marks(window.doc))
        window.editor._finish_reveal()
        save(window, args.out / 'editor-marks.png')

        dialog = window.create_about_dialog()
        dialog.show()
        QTest.qWait(500)
        save(dialog, args.out / 'about.png')
        dialog.close()
        window.close()


if __name__ == '__main__':
    main()
