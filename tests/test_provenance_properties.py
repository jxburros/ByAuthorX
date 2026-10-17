"""Randomized (seeded, deterministic) properties: provenance spans follow text through every edit.

Each seed runs a random sequence of insertions, deletions, replacements, paragraph splits, joins
and moves with random declared origins (typed, pasted, an AI tool, or none) through
``engine.apply``. A shadow model keeps one origin per character, edited exactly like the text. After
every edit the per-character origins expanded from the document's spans must equal the shadow:
no span drifts, grows over neighbouring text, or survives deletion; spans stay sorted, disjoint
and compacted (no two touching spans share a record), and the document validates.
"""

import random

import pytest

from byax import engine
from byax.errors import ByaxError
from byax.model import new_document, validate_document

WORDS = ['tide', 'lamp', 'gull', 'Café', 'naïve', 'rope', 'salt', 'keeper', 'night', 'Ωmega']
ORIGINS = (None, 'human', 'pasted', 'ai_generated')


def _declared(origin):
    if origin is None:
        return {}
    if origin == 'ai_generated':
        return {'provenance': {'origin': origin, 'model': 'local-model', 'provider': 'local'}}
    return {'provenance': {'origin': origin}}


def _expanded(doc):
    log = doc.get('provenance') or {'spans': {}, 'records': {}}
    result = {}
    for block in doc['blocks']:
        chars = [None] * len(block['text'])
        spans = log['spans'].get(block['id'], [])
        for (a, b, rid), following in zip(spans, spans[1:] + [None]):
            assert 0 <= a < b <= len(chars)
            if following is not None:
                assert b <= following[0], 'spans overlap or are unsorted'
                assert not (b == following[0] and rid == following[2]), 'touching spans were not merged'
            for i in range(a, b):
                chars[i] = log['records'][rid]['origin']
        result[block['id']] = chars
    return result


def _boundaries(text):
    return engine.grapheme_boundaries(text)


@pytest.mark.parametrize('seed', range(40))
def test_random_edit_sequences_keep_spans_on_their_characters(seed):
    rng = random.Random(seed)
    doc = new_document('Property', '\n'.join(' '.join(rng.choice(WORDS) for _ in range(4)) for _ in range(3)))
    shadow = {b['id']: [None] * len(b['text']) for b in doc['blocks']}
    for step in range(60):
        blocks = doc['blocks']
        block = rng.choice(blocks)
        text, target = block['text'], block['id']
        origin = rng.choice(ORIGINS)
        kind = rng.choice(('insert', 'insert', 'delete', 'replace', 'split', 'join', 'move'))
        bounds = _boundaries(text)
        if kind == 'insert':
            offset = rng.choice(bounds)
            new = rng.choice(WORDS) + rng.choice(('', ' ', '. '))
            operation = {'type': 'insert_text', 'target': target, 'offset': offset, 'text': new}
            shadow[target][offset:offset] = [origin] * len(new)
        elif kind in ('delete', 'replace') and len(bounds) > 1:
            start, end = sorted(rng.sample(bounds, 2))
            if kind == 'delete':
                operation = {'type': 'delete_text', 'target': target, 'start': start, 'end': end}
                shadow[target][start:end] = []
            else:
                new = rng.choice(WORDS)
                match = text[start:end]
                occurrences = [i for i in range(len(text)) if text.startswith(match, i)]
                operation = {
                    'type': 'replace_text',
                    'target': target,
                    'match': match,
                    'replacement': new,
                    'occurrence': occurrences.index(start) + 1,
                }
                shadow[target][start:end] = [origin] * len(new)
        elif kind == 'split':
            offset = rng.choice(bounds)
            fresh = f'p_{seed}_{step}'
            operation = {'type': 'split_block', 'target': target, 'offset': offset, 'id': fresh}
            shadow[fresh] = shadow[target][offset:]
            shadow[target] = shadow[target][:offset]
        elif kind == 'join' and blocks.index(block) + 1 < len(blocks):
            other = blocks[blocks.index(block) + 1]
            operation = {'type': 'join_blocks', 'target': target, 'next': other['id']}
            shadow[target] = shadow[target] + shadow.pop(other['id'])
        elif kind == 'move' and len(blocks) > 1:
            after = rng.choice([b['id'] for b in blocks if b['id'] != target])
            operation = {'type': 'move_block', 'target': target, 'after': after}
        else:
            continue
        doc, _ = engine.apply(doc, {'operations': [operation], **_declared(origin)})
        assert _expanded(doc) == {b['id']: shadow[b['id']] for b in doc['blocks']}, (seed, step, operation)
        validate_document(doc)


@pytest.mark.parametrize('seed', range(10))
def test_rejected_edits_leave_the_log_unchanged(seed):
    rng = random.Random(1000 + seed)
    doc = new_document('Atomic', 'alpha beta gamma')
    target = doc['blocks'][0]['id']
    doc, _ = engine.apply(
        doc,
        {
            'operations': [{'type': 'insert_text', 'target': target, 'offset': 5, 'text': ' x'}],
            'provenance': {'origin': 'human'},
        },
    )
    before = doc.get('provenance')
    with pytest.raises(ByaxError):
        engine.apply(
            doc,
            {
                'operations': [
                    {'type': 'insert_text', 'target': target, 'offset': rng.randint(0, 5), 'text': 'y'},
                    {'type': 'delete_text', 'target': 'missing', 'start': 0, 'end': 1},
                ],
                'provenance': {'origin': 'pasted'},
            },
        )
    assert doc.get('provenance') == before
