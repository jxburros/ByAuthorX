"""Opt-in flowing pagination and commit scaling measurements for benchmark.py."""

import hashlib

from generate_fixtures import PROSE, block, document, flowing_fixture

from byax.engine import apply
from byax.layout import resolve_pages
from byax.model import canonical_json, validate_document


def commit_fixture(count):
    doc = document(f'commit_{count}')
    doc['blocks'] = [block(f'p_{index}', PROSE) for index in range(count)]
    return validate_document(doc)


def _identity(doc):
    return {
        'semantic_sha256': hashlib.sha256(canonical_json(doc).encode()).hexdigest(),
        'blocks': len(doc['blocks']),
        'text_characters': sum(len(b['text']) for b in doc['blocks']),
    }


def scaling_metrics(measure, samples, page_sizes=(100, 500, 2000), block_sizes=(379, 1499, 5000, 15000)):
    result = {
        'flowing': {},
        'commits': {},
        'qualification': 'Full resolution and canonical commit on this host; not incremental or desktop latency.',
    }
    for size in block_sizes:
        doc = commit_fixture(size)
        target = f'p_{size // 2}'
        operations = {
            'single_character': {'type': 'insert_text', 'target': target, 'offset': 1, 'text': 'x'},
            'paragraph_split': {'type': 'split_block', 'target': target, 'offset': 20},
            'heading_insert': {
                'type': 'insert_block',
                'after': target,
                'block': block('new_heading', 'New heading', 'heading', level=2),
            },
        }
        metrics = {}
        for name, operation in operations.items():
            metrics[name] = measure(
                lambda _, op=operation, doc=doc: apply(doc, {'operations': [op]}), samples
            )
        result['commits'][str(size)] = {**_identity(doc), 'metrics': metrics}
    for pages in page_sizes:
        doc = flowing_fixture(pages)
        resolved = resolve_pages(doc)
        entry = {
            **_identity(doc),
            'requested_pages': pages,
            'measured_pages': len(resolved['pages']),
            'pagination': 'automatic; no explicit page breaks',
            'metrics': {},
        }
        del resolved
        for position, index in [('start', 0), ('middle', pages // 2), ('end', pages - 1)]:
            edited, _ = apply(
                doc,
                {
                    'operations': [
                        {'type': 'insert_text', 'target': f'paragraph_{index}', 'offset': 0, 'text': 'x'}
                    ]
                },
            )
            entry['metrics'][f'resolve_after_{position}_edit'] = measure(
                lambda _, edited=edited: resolve_pages(edited), samples
            )
        result['flowing'][str(pages)] = entry
    return result
