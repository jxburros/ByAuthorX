#!/usr/bin/env python3
"""Generate original, deterministic acceptance documents; no downloaded assets.

Page-count workloads use explicit page breaks to stabilize workload size. They do
not establish automatic pagination quality, which needs separate acceptance tests.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from byax.model import new_document, validate_document

PROSE = (
    'The field team records each observation before discussing its meaning. '
    'A clear account separates measured results from interpretation and preserves '
    'the original wording of each contribution. Readers can compare the table '
    'with the narrative and follow the references to their source. '
)
UNICODE = 'Café; Cafe\u0301; Ελληνικά; العربية; עברית; 日本語; नमस्ते; 👩🏽‍💻.'


def block(ident, text='', kind='paragraph', **attrs):
    return {
        'id': ident,
        'type': kind,
        'text': text,
        'style': 'heading1' if kind == 'heading' else 'body',
        'marks': [],
        'attrs': attrs,
    }


def table(ident, values):
    out = block(ident, kind='table', header_rows=1, padding=4)
    out['rows'] = [
        {
            'id': f'{ident}_r{r}',
            'cells': [
                {'id': f'{ident}_r{r}_c{c}', 'text': str(value), 'attrs': {}} for c, value in enumerate(row)
            ],
        }
        for r, row in enumerate(values)
    ]
    return out


def document(name):
    doc = new_document(name.replace('_', ' ').title())
    doc['id'], doc['revision'] = f'doc_fixture_{name}', f'rev_fixture_{name}_0'
    doc['blocks'] = []
    doc['metadata']['author'] = 'ByAuthorX acceptance fixtures (original synthetic content)'
    doc['styles']['body']['font_family'] = 'DejaVu Sans'
    doc['settings']['fixture'] = {'generator_version': 1, 'license': 'MIT', 'name': name}
    return doc


def add_cover(doc, ident='cover', wrap='inline'):
    from PIL import Image, ImageDraw

    image = Image.new('RGB', (600, 600), '#16283c')
    draw = ImageDraw.Draw(image)
    draw.ellipse((90, 90, 510, 510), fill='#faaf58')
    draw.rectangle((0, 380, 600, 420), fill='#f4e8d2')
    output = io.BytesIO()
    image.save(output, format='PNG')
    raw = output.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    doc['assets'][digest] = {
        'data': base64.b64encode(raw).decode(),
        'mime': 'image/png',
        'name': 'original-cover.png',
    }
    return block(
        ident,
        kind='image',
        asset=digest,
        alt='Original geometric orange sun over a pale horizon',
        width=108,
        height=108,
        wrap=wrap,
    )


def flowing_fixture(pages):
    doc = document(f'flowing_{pages}')
    doc['settings']['fixture'].update(requested_pages=pages, pagination='automatic; estimated size')
    for index in range(pages):
        if index % 20 == 0:
            doc['blocks'].append(
                block(
                    f'section_{index}',
                    kind='section_break',
                    columns=1 if index % 40 == 0 else 2,
                    section_start='continuous',
                )
            )
        doc['blocks'].append(
            block(f'heading_{index}', f'Observation {index}', 'heading', level=1, keep_with_next=True)
        )
        doc['blocks'].append(block(f'paragraph_{index}', PROSE * 11, widow_control=2, orphan_control=2))
        if index % 10 == 0:
            doc['blocks'].append(table(f'table_{index}', [['Site', 'Count'], ['North', index]]))
            doc['blocks'].append(
                block(
                    f'note_{index}',
                    'Synthetic note for this observation.',
                    'footnote',
                    reference=f'paragraph_{index}',
                )
            )
        if index % 20 == 0:
            doc['blocks'].append(add_cover(doc, f'image_{index}'))
    return validate_document(doc)


def project_fixture(parts=3, chapters=10, scenes=4, repeat=7):
    """A manuscript project of about 100,000 words: parts, chapters, scenes and two notes."""
    from byax.project import item_id_for

    doc = document('project_100k')
    doc['settings']['fixture'].update(structure=f'{parts} parts x {chapters} chapters x {scenes} scenes')
    items = {}

    def heading(ident, text, level, kind, **meta):
        doc['blocks'].append(dict(block(ident, text, 'heading', level=level), style=f'heading{level}'))
        identity = item_id_for(ident)
        items[identity] = {'id': identity, 'heading': ident, 'kind': kind, 'status': 'draft', **meta}

    doc['blocks'].append(block('dedication', 'For the field team.'))
    for part in range(1, parts + 1):
        heading(f'part_{part}', f'Part {part}', 1, 'part')
        for chapter in range(1, chapters + 1):
            number = (part - 1) * chapters + chapter
            heading(f'chapter_{number}', f'Chapter {number}', 2, 'chapter', target_words=3500)
            for scene in range(1, scenes + 1):
                ident = f'scene_{number}_{scene}'
                heading(
                    ident, f'Scene {number}.{scene}', 3, 'scene', synopsis=f'Observation {number}.{scene}.'
                )
                for paragraph in range(3):
                    doc['blocks'].append(block(f'{ident}_p{paragraph}', PROSE * repeat))
            doc['review']['comments'].append(
                {
                    'id': f'comment_chapter_{number}',
                    'target': f'scene_{number}_1_p0',
                    'start': 4,
                    'end': 9,
                    'text': 'Check this observation.',
                    'author': 'fixture',
                    'status': 'open',
                    'revision': doc['revision'],
                    'replies': [],
                }
            )
    heading('notes_characters', 'Character sheets', 1, 'note')
    doc['blocks'].append(block('notes_characters_p0', 'Alex Rivera leads the field team.'))
    heading('notes_research', 'Research notes', 1, 'note')
    doc['blocks'].append(block('notes_research_p0', PROSE))
    doc['settings']['bookmarks'] = {
        'midpoint': {'target': f'scene_{parts * chapters // 2}_1_p0', 'offset': 4}
    }
    doc['project'] = {'items': items, 'target_words': 100000}
    return validate_document(doc)


def make_fixture(name):
    if name in {'flowing_100', 'flowing_500', 'flowing_2000'}:
        return flowing_fixture(int(name.split('_')[1]))
    if name == 'project_100k':
        return project_fixture()
    doc = document(name)
    if name == 'letter':
        doc['blocks'] = [
            block('letter_title', 'A letter from the field', 'heading', level=1),
            block('letter_date', '13 October 2026'),
            block('salutation', 'Dear Morgan,'),
            block('letter_body', PROSE * 2),
            block('closing', 'Sincerely,\nAlex Rivera'),
        ]
    elif name == 'resume':
        doc['blocks'] = [
            block('resume_name', 'Alex Rivera', 'heading', level=1),
            block('resume_contact', 'alex@example.invalid · Chicago, IL'),
            block('resume_summary', 'Summary', 'heading', level=2),
            block('summary_text', 'Editor and researcher who makes complex reports readable.'),
            block('experience', 'Experience', 'heading', level=2),
            block('role', 'Documentation editor · Example Cooperative · 2022–2026'),
            block(
                'achievement',
                'Edited accessible field guides and coordinated source review.',
                list='bullet',
                level=0,
            ),
            block('skills', 'Skills', 'heading', level=2),
            block('skills_text', 'Editing, research, structured documents, and plain language.'),
        ]
    elif name == 'report':
        doc['layout'].update(header='Field observations', footer='Page {page}')
        doc['blocks'] = [
            block('report_title', 'Field observations', 'heading', level=1),
            block('contents', kind='toc'),
            block('summary', 'Executive summary', 'heading', level=1),
            block('intro', PROSE * 3),
            block('break_report', kind='page_break'),
            block('results', 'Results', 'heading', level=1),
            table('results_table', [['Site', 'Samples'], ['North', '24'], ['South', '31']]),
            block('caption', 'Table 1. Recorded samples.', caption_for='results_table'),
            block('cross_reference', 'See Table 1 for site totals.', reference='results_table'),
            block('multilingual', UNICODE),
            block('note', 'Counts describe this sample only.', 'footnote', reference='cross_reference'),
        ]
    elif name in {'newsletter', 'press_kit'}:
        doc['layout'].update(header='RIVERLIGHT · Independent music', footer='Page {page}')
        doc['layers'] = [
            {
                'id': 'watermark',
                'name': 'Review watermark',
                'type': 'text',
                'text': 'DRAFT',
                'visible': True,
                'locked': True,
                'opacity': 0.1,
            }
        ]
        doc['blocks'] = [
            block('title', 'Riverlight — New horizons', 'heading', level=1),
            block('biography', PROSE * 3),
            block('columns', kind='section_break', columns=2),
            block('release_title', 'Release information', 'heading', level=2),
            table(
                'release_table',
                [
                    ['Field', 'Value'],
                    ['Release', 'New horizons'],
                    ['Date', '2026-11-01'],
                    ['Contact', 'press@example.invalid'],
                ],
            ),
            add_cover(doc, wrap='square'),
            block('image_caption', 'Original cover artwork.', caption_for='cover'),
            block('feature', PROSE * 2),
        ]
        doc['fields']['booking_url'] = {'type': 'text', 'required': True, 'description': 'Public booking URL'}
    elif name in {'mixed_100', 'long_500'}:
        pages = 100 if name == 'mixed_100' else 500
        doc['settings']['fixture'].update(
            requested_pages=pages,
            pagination='explicit page breaks',
            caveat='Measured layout page count is authoritative; this fixture does not test automatic break placement.',
        )
        for page in range(1, pages + 1):
            if page > 1:
                doc['blocks'].append(block(f'break_{page}', kind='page_break'))
            doc['blocks'].append(
                block(f'heading_{page}', f'Chapter {page}: observations', 'heading', level=1)
            )
            doc['blocks'].append(
                block(
                    f'paragraph_{page}',
                    PROSE * (3 if name == 'mixed_100' else 6),
                    section_id=f'section_{page}',
                )
            )
            if name == 'mixed_100':
                if page % 2 == 0:
                    doc['blocks'].append(
                        table(
                            f'table_{page}',
                            [['Site', 'Samples'], ['North', str(page)], ['South', str(page + 1)]],
                        )
                    )
                if page % 5 == 0:
                    doc['blocks'].append(add_cover(doc, f'image_{page}'))
                if page % 10 == 0:
                    doc['blocks'].append(block(f'unicode_{page}', UNICODE))
    else:
        raise ValueError(f'Unknown fixture {name}')
    return validate_document(doc)


NAMES = (
    'letter',
    'resume',
    'report',
    'newsletter',
    'press_kit',
    'mixed_100',
    'long_500',
    'flowing_100',
    'flowing_500',
    'flowing_2000',
    'project_100k',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('artifacts/fixtures'))
    parser.add_argument('--name', choices=NAMES, action='append')
    args = parser.parse_args()
    from byax.storage import save_document

    args.out.mkdir(parents=True, exist_ok=True)
    result = []
    for name in args.name or NAMES:
        doc = make_fixture(name)
        destination = args.out / f'{name}.byax'
        save_document(doc, destination)
        content = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        result.append(
            {
                'name': name,
                'path': str(destination),
                'semantic_sha256': hashlib.sha256(content).hexdigest(),
                'blocks': len(doc['blocks']),
            }
        )
    (args.out / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
