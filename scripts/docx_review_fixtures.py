"""Regenerate the Word review round-trip fixtures in ``tests/fixtures/docx-review``.

    uv run python scripts/docx_review_fixtures.py --out tests/fixtures/docx-review
    uv run python scripts/docx_review_fixtures.py --out tests/fixtures/docx-review --libreoffice

Writes ``sent.byax`` (the manuscript after "Send for review", holding the pinned snapshot and the review
record), ``review-copy.docx`` (what the editor receives), ``word-returned.docx`` and
``word-unsupported.docx`` (the copy edited the way Microsoft Word saves tracked changes, comments,
replies and moves, emulated in ``tests/docx_word.py``). With ``--libreoffice`` it also writes two real
third-party files: ``libreoffice-converted.docx`` (``word-returned.docx`` opened and saved again by
LibreOffice) and ``libreoffice-edited.docx`` (the review copy edited by LibreOffice itself with change
recording on, through ``scripts/libreoffice_review_edit.py`` and the system UNO bridge).

The review copy is regenerated from the snapshot exactly; ``sent.byax`` gets new random identities on
every run, so regenerate all files together and commit them as a set.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'src'))

from docx_word import sample_document, word_session, word_unsupported_session  # noqa: E402

from byax import docx_review, storage  # noqa: E402

CREATED = '2026-10-01T08:00:00Z'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=ROOT / 'tests' / 'fixtures' / 'docx-review')
    parser.add_argument(
        '--libreoffice', action='store_true', help='Also produce LibreOffice-written fixtures'
    )
    parser.add_argument('--soffice', default='soffice')
    parser.add_argument('--uno-python', default='/usr/bin/python3')
    args = parser.parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    doc = sample_document()
    doc, result, data = docx_review.create_review_package(
        doc, out / 'review-copy.docx', reviewer='Jane Editor', created=CREATED, overwrite=True
    )
    storage.save_document(doc, out / 'sent.byax')
    docx_review.publish_review_copy(data, out / 'review-copy.docx', overwrite=True)
    (out / 'word-returned.docx').write_bytes(word_session(data))
    (out / 'word-unsupported.docx').write_bytes(word_unsupported_session(data))
    print('review package', result['review_package']['id'], 'losses', len(result['export_report']['losses']))
    if args.libreoffice:
        with tempfile.TemporaryDirectory(prefix='byax-lo-') as temporary:
            profile = Path(temporary) / 'profile'
            converted = Path(temporary) / 'converted'
            subprocess.run(
                [
                    args.soffice,
                    '--headless',
                    '--norestore',
                    f'-env:UserInstallation={profile.as_uri()}',
                    '--convert-to',
                    'docx:MS Word 2007 XML',
                    '--outdir',
                    str(converted),
                    str(out / 'word-returned.docx'),
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
            shutil.copyfile(converted / 'word-returned.docx', out / 'libreoffice-converted.docx')
        subprocess.run(
            [
                args.uno_python,
                str(ROOT / 'scripts' / 'libreoffice_review_edit.py'),
                str(out / 'review-copy.docx'),
                str(out / 'libreoffice-edited.docx'),
            ],
            check=True,
            timeout=300,
        )
        print('LibreOffice fixtures written')


if __name__ == '__main__':
    main()
