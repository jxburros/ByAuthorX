#!/usr/bin/env python3
"""Export every acceptance fixture as EPUB and validate it, optionally with EPUBCheck.

The built-in structural check (``byax.epub.validate``) always runs. EPUBCheck is the
authoritative validator and needs Java; pass ``--epubcheck path/to/epubcheck.jar`` (the CI job
downloads a pinned, checksum-verified release). Exits non-zero when any fixture has an error.
Nothing is downloaded by this script.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from byax.epub import export_epub, validate  # noqa: E402
from byax.model import new_document, validate_document  # noqa: E402
from generate_fixtures import NAMES, make_fixture  # noqa: E402

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=='
)


def _image_asset(doc, data, mime, name):
    digest = hashlib.sha256(data).hexdigest()
    doc['assets'][digest] = {'data': base64.b64encode(data).decode(), 'mime': mime, 'name': name}
    return digest


def rich_document():
    """One deterministic document that exercises every EPUB construct the exporter writes."""

    def block(ident, text='', kind='paragraph', style=None, marks=(), **attrs):
        return {
            'id': ident,
            'type': kind,
            'text': text,
            'style': style or ('heading1' if kind == 'heading' else 'body'),
            'marks': list(marks),
            'attrs': attrs,
        }

    def mark(kind, start, end, value=True):
        return {'type': kind, 'start': start, 'end': end, 'value': value}

    doc = new_document('The Lighthouse Keeper')
    doc['id'], doc['revision'] = 'doc_fixture_epub_rich', 'rev_fixture_epub_rich_0'
    doc['metadata'].update(
        author='Ada Example',
        language='en-GB',
        description='A synthetic book that exercises every EPUB construct.',
        publisher='Example Press',
        rights='Public domain test content',
        subjects=['Fixtures', 'Accessibility'],
        modified='2026-10-01T12:00:00+02:00',
    )
    doc['styles']['emphasis'] = {'kind': 'character', 'italic': True, 'color': '#336699'}
    doc['styles']['lead'] = {'based_on': 'body', 'font_size': 13, 'alignment': 'center', 'space_after': 12}
    cover = _image_asset(doc, PNG, 'image/png', 'cover.png')
    doc['metadata']['cover'] = cover
    from PIL import Image

    jpeg = io.BytesIO()
    Image.new('RGB', (8, 8), '#336699').save(jpeg, format='JPEG')
    photo = _image_asset(doc, jpeg.getvalue(), 'image/jpeg', 'photo.jpg')
    bmp = _image_asset(doc, b'BM' + bytes(60), 'image/bmp', 'plate.bmp')
    text = 'Bold italic underline strike code super link colour highlight serif sized style internal relative'
    words = text.split()
    marks, position = [], 0
    spans = {}
    for word in words:
        spans[word] = (position, position + len(word))
        position += len(word) + 1

    def span(word, kind, value=True):
        return mark(kind, *spans[word], value)

    doc['blocks'] = [
        block('h_part1', 'Part One: The Light', 'heading', level=1),
        block('p_lead', 'A lead paragraph, centred through its named style.', style='lead'),
        block(
            'p_marks',
            text,
            marks=[
                span('Bold', 'bold'),
                span('italic', 'italic'),
                span('underline', 'underline'),
                span('strike', 'strikethrough'),
                span('code', 'code'),
                span('super', 'superscript'),
                span('link', 'link', 'https://example.com/a?b=1&c=2'),
                span('colour', 'color', '#cc3300'),
                span('highlight', 'highlight', '#ffee88'),
                span('serif', 'font_family', 'DejaVu Serif'),
                span('sized', 'font_size', 16),
                span('style', 'character_style', 'emphasis'),
                span('internal', 'link', '#p_target'),
                span('relative', 'link', 'other.html'),
            ],
            alignment='justify',
            first_line_indent=18,
        ),
        block('h_sec', 'The tower', 'heading', level=2),
        block('p_target', 'Target of an internal link, with a note.'),
        block('note_1', 'First note, with a *marked* word.', 'footnote', reference='p_target', offset=6),
        block('note_2', 'Second note on the same paragraph.', 'footnote', reference='p_target', offset=14),
        block('h_sub', 'Stairs', 'heading', level=3),
        block('l_1', 'Bullet one', list='bullet', level=1),
        block('l_2', 'Nested bullet', list='bullet', level=2),
        block('l_3', 'Deeply nested ordered', list={'kind': 'ordered', 'level': 3}),
        block('l_4', 'Bullet two', list='bullet', level=1),
        block('l_5', 'Ordered from seven', list='ordered', level=1, restart=True, start=7),
        block('l_6', 'Ordered eight', list='ordered', level=1),
        block('note_3', 'A note on a list item.', 'footnote', reference='l_6', offset=3),
        block('p_quote', 'A quotation set as a block quote.', style='quotation'),
        block('p_code', 'first line\n    indented second line', style='code'),
        block('p_french', 'Un paragraphe en français.', language='fr'),
        block('p_control', 'Control\x01character'),
        block('p_empty', ''),
        block('h_empty', '', 'heading', level=2),
        {
            'id': 't_1',
            'type': 'table',
            'text': '',
            'style': 'body',
            'marks': [],
            'attrs': {'header_rows': 1},
            'rows': [
                {
                    'id': 't_1_r0',
                    'cells': [
                        {'id': 't_1_r0_c0', 'text': 'Site', 'attrs': {}, 'marks': [mark('bold', 0, 4)]},
                        {'id': 't_1_r0_c1', 'text': 'Count', 'attrs': {}},
                        {'id': 't_1_r0_c2', 'text': 'Note', 'attrs': {}},
                    ],
                },
                {
                    'id': 't_1_r1',
                    'cells': [
                        {'id': 't_1_r1_c0', 'text': 'North', 'attrs': {'rowspan': 2}},
                        {'id': 't_1_r1_c1', 'text': 'Spans two columns', 'attrs': {'colspan': 2}},
                        {'id': 't_1_r1_c2', 'text': '', 'attrs': {}},
                    ],
                },
                {
                    'id': 't_1_r2',
                    'cells': [
                        {'id': 't_1_r2_c0', 'text': '', 'attrs': {}},
                        {'id': 't_1_r2_c1', 'text': '12', 'attrs': {}},
                        {'id': 't_1_r2_c2', 'text': 'ok', 'attrs': {}},
                    ],
                },
            ],
        },
        block(
            'i_alt', kind='image', asset=photo, alt='A blue square standing in for a photograph', width=100
        ),
        block('i_none', kind='image', asset=cover, alt=''),
        block('i_decor', kind='image', asset=cover, alt='', decorative=True),
        block('i_bmp', kind='image', asset=bmp, alt='A plate in a format EPUB does not accept'),
        block('b_page', kind='page_break'),
        block('b_section', kind='section_break'),
        block('b_toc', kind='toc'),
        block('h_part2', 'Part Two: Ελληνικά, עברית, 日本語', 'heading', level=1),
        block('p_unicode', 'Café; Cafe\u0301; Ελληνικά; العربية; עברית; 日本語; 👩🏽\u200d💻.'),
        block('note_loose', 'A footnote with no reference.', 'footnote'),
    ]
    return validate_document(doc)


def run_epubcheck(jar, epub):
    """EPUBCheck's own JSON report, reduced to its messages."""
    report = Path(str(epub) + '.epubcheck.json')
    result = subprocess.run(
        ['java', '-jar', str(jar), '--quiet', '--json', str(report), str(epub)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if not report.is_file():
        return [{'severity': 'FATAL', 'message': (result.stdout + result.stderr).strip()[-2000:]}]
    messages = json.loads(report.read_text()).get('messages', [])
    return [
        {
            'severity': m.get('severity'),
            'id': m.get('ID'),
            'message': m.get('message'),
            'location': (m.get('locations') or [{}])[0].get('path'),
        }
        for m in messages
        if m.get('severity') in ('ERROR', 'FATAL', 'WARNING')
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('artifacts/epub'))
    parser.add_argument(
        '--name', choices=[*NAMES, 'epub_rich'], action='append', help='fixture to export (default: all)'
    )
    parser.add_argument('--epubcheck', type=Path, help='path to epubcheck.jar; skipped when omitted')
    parser.add_argument('--warnings-fail', action='store_true', help='treat EPUBCheck warnings as failures')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    failed, rows = False, []
    for name in args.name or [*NAMES, 'epub_rich']:
        target = args.out / f'{name}.epub'
        report = export_epub(rich_document() if name == 'epub_rich' else make_fixture(name), target)
        structure = validate(target)
        row = {
            'fixture': name,
            'bytes': target.stat().st_size,
            'losses': len(report['losses']),
            'structure': 'passed' if structure['valid'] else 'failed',
            'structure_errors': structure['errors'],
        }
        failed |= not structure['valid']
        if args.epubcheck:
            messages = run_epubcheck(args.epubcheck, target)
            bad = [m for m in messages if m['severity'] in ('ERROR', 'FATAL') or args.warnings_fail]
            row.update(epubcheck='failed' if bad else 'passed', epubcheck_messages=messages)
            failed |= bool(bad)
        rows.append(row)
        print(
            f'{name}: structure {row["structure"]}'
            + (f', EPUBCheck {row["epubcheck"]}' if args.epubcheck else '')
        )
    (args.out / 'epubcheck-report.json').write_text(json.dumps(rows, indent=2, sort_keys=True) + '\n')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
