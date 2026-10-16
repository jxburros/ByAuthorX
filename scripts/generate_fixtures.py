#!/usr/bin/env python3
"""Generate original, deterministic acceptance documents; no downloaded assets (`--eval-scale N` writes a synthetic text corpus for the retrieval harness).

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


def make_fixture(name):
    if name in {'flowing_100', 'flowing_500', 'flowing_2000'}:
        return flowing_fixture(int(name.split('_')[1]))
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


def make_eval_scale(out: Path, documents: int = 10_000, seed: int = 20261016, queries: int = 100) -> dict:
    """Synthetic retrieval-scale corpus: `documents` short text files plus golden queries.

    Output (never committed): `out/corpus/dNNN/doc_NNNNNN.txt` and `out/queries.jsonl`. Every document has
    a title line and 2-4 paragraphs of Zipf-distributed pseudo-words, three topic words shared with about
    1 in 200 other documents, and one unique "needle" word so queries have exactly one right answer.
    Locators are character offsets, as in the fixture libraries.
    """
    import random

    rng = random.Random(seed)
    syllables = [
        'ba',
        'ke',
        'li',
        'mo',
        'nu',
        'sa',
        'te',
        'vo',
        'ri',
        'du',
        'fa',
        'gi',
        'ho',
        'ju',
        'ze',
        'pi',
        'qua',
        'xe',
    ]
    vocabulary = sorted(
        {''.join(rng.choice(syllables) for _ in range(rng.randint(2, 4))) for _ in range(30_000)}
    )[:12_000]
    rng.shuffle(vocabulary)
    weights = [1 / (rank + 1) for rank in range(len(vocabulary))]
    topics = [
        f'{rng.choice(syllables)}{rng.choice(syllables)}ology{i}' for i in range(max(1, documents // 200))
    ]
    corpus = out / 'corpus'
    corpus.mkdir(parents=True, exist_ok=True)
    needles, records = [], []
    for number in range(documents):
        needle = f'zq{number:06d}n'
        doc_topics = [topics[(number * 7 + k * 31) % len(topics)] for k in range(3)]
        paragraphs = []
        for index in range(rng.randint(2, 4)):
            words = rng.choices(vocabulary, weights, k=rng.randint(40, 90))
            if index == 0:
                for slot, planted in zip(
                    rng.sample(range(len(words)), 3), (needle, doc_topics[0], doc_topics[1]), strict=True
                ):
                    words[slot] = planted
            sentence_length, text = 12, []
            for position in range(0, len(words), sentence_length):
                chunk = words[position : position + sentence_length]
                text.append(' '.join(chunk).capitalize() + '.')
            paragraphs.append(' '.join(text))
        title = f'Record {number:06d} on {doc_topics[0]}'
        body = title + '\n\n' + '\n\n'.join(paragraphs) + '\n'
        path = corpus / f'd{number // 100:03d}' / f'doc_{number:06d}.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')
        first = body.index(paragraphs[0])
        records.append(
            (
                path.relative_to(corpus).as_posix(),
                needle,
                doc_topics,
                first,
                first + len(paragraphs[0]),
                paragraphs[0],
            )
        )
        needles.append(number)
    chosen = rng.sample(range(documents), min(queries, documents))
    lines = []
    for position, number in enumerate(sorted(chosen)):
        path, needle, doc_topics, start, end, paragraph = records[number]
        if position % 2 == 0:
            words = paragraph.rstrip('.').split()
            at = max(0, [w.strip('.').lower() for w in words].index(needle) - 3)
            query, kind = ' '.join(words[at : at + 8]).strip('.'), 'known_item_quote'
        else:
            query, kind = f'{needle} {doc_topics[0]}', 'factual_lookup'
        lines.append(
            json.dumps(
                {
                    'id': f'scale-{position + 1:03d}',
                    'type': kind,
                    'query': query,
                    'relevant_chunk_locators': [{'path': path, 'start': start, 'end': end, 'quote': needle}],
                }
            )
        )
    (out / 'queries.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'documents': documents, 'queries': len(lines), 'corpus': str(corpus)}


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
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('artifacts/fixtures'))
    parser.add_argument('--name', choices=NAMES, action='append')
    parser.add_argument(
        '--eval-scale',
        type=int,
        metavar='N',
        help='instead of .byax documents, write an N-document synthetic text corpus + golden queries to '
        '<out>/eval-scale for the retrieval harness (never committed)',
    )
    args = parser.parse_args()
    if args.eval_scale:
        print(json.dumps(make_eval_scale(args.out / 'eval-scale', args.eval_scale), indent=2))
        return
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
