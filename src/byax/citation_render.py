"""Derived citation presentation. Never writes generated labels into the master."""

import copy
import hashlib

from .citations import render_bibliography, render_citation
from .errors import ByaxError


def citation_view(document, *, footnotes=True):
    """Project citations to output blocks while leaving source records and marks intact.

    Footnotes use the existing note reference/offset mechanism. Exporters without
    native note support can request numbered endnotes instead. Only sources cited
    by the supplied (already visibility-filtered) blocks enter the bibliography.
    """
    if document.get('_citations_projected'):
        return document
    references = document.get('references', {})
    objects = []
    for block in document.get('blocks', []):
        objects.append(block)
        for row in block.get('rows', []):
            objects.extend(row.get('cells', []))
    if not any(
        mark.get('type') == 'citation' and mark.get('value') in references
        for block in objects
        for mark in block.get('marks', [])
    ):
        return document
    doc = copy.deepcopy(document)
    doc['_citations_projected'] = True
    settings = doc.get('settings', {}).get('citations', {})
    style = settings.get('style', 'apa')
    mode = settings.get('presentation', 'in-text')
    if mode not in ('in-text', 'footnotes', 'endnotes', 'numbered'):
        raise ByaxError('invalid_input', 'Unknown citation presentation.')
    objects = []
    for block in doc['blocks']:
        objects.append(block)
        for row in block.get('rows', []):
            objects.extend(row.get('cells', []))
    # A native document can use any stable ID, including our readable prefixes.
    # Reserve all nested IDs, then allocate deterministic collision-free output IDs.
    used_ids, pending = set(), [doc]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if isinstance(value.get('id'), str):
                used_ids.add(value['id'])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)

    def output_id(preferred):
        candidate, suffix = preferred, 1
        while candidate in used_ids:
            candidate = f'{preferred}_{suffix}'
            suffix += 1
        used_ids.add(candidate)
        return candidate

    downgraded = []
    cited, notes = {}, []
    sequence = 0
    for block in objects:
        citations = [
            m for m in block.get('marks', []) if m.get('type') == 'citation' and m.get('value') in references
        ]
        insertions = []
        for mark in sorted(citations, key=lambda item: (item['end'], item['start'])):
            ident = mark['value']
            cited.setdefault(ident, references[ident])
            sequence += 1
            label = render_citation(
                dict(references[ident], id=ident),
                style,
                mark.get('locator'),
                mark.get('prefix', ''),
                mark.get('suffix', ''),
            )
            if mode == 'in-text':
                insertions.append((mark['end'], ' ' + label))
            elif mode == 'numbered':
                insertions.append((mark['end'], f' [{list(cited).index(ident) + 1}]'))
            else:
                note_id = (
                    'citation_note_'
                    + hashlib.sha256(
                        f'{block["id"]}:{mark["start"]}:{mark["end"]}:{ident}:{sequence}'.encode()
                    ).hexdigest()[:20]
                )
                native = mode == 'footnotes' and footnotes
                notes.append(
                    {
                        'id': output_id(note_id),
                        'type': 'footnote' if native else 'paragraph',
                        'text': label if native else f'{sequence}. {label}',
                        'style': 'body',
                        'marks': [],
                        'attrs': {'reference': block['id'], 'offset': mark['end']} if native else {},
                    }
                )
                if mode == 'footnotes' and not footnotes and block['id'] not in downgraded:
                    downgraded.append(block['id'])
                if not native:
                    insertions.append((mark['end'], f' [{sequence}]'))
        # Insert from the end, preserving the coordinates of every original mark.
        for position, label in sorted(insertions, key=lambda pair: pair[0], reverse=True):
            block['text'] = block['text'][:position] + label + block['text'][position:]
            for mark in block.get('marks', []):
                if mark['start'] >= position:
                    mark['start'] += len(label)
                if mark['end'] > position:
                    mark['end'] += len(label)
            for note in notes:
                attrs = note['attrs']
                if attrs.get('reference') == block['id'] and attrs.get('offset', 0) > position:
                    attrs['offset'] += len(label)
    if notes:
        if mode == 'endnotes' or not footnotes:
            doc['blocks'].append(
                {
                    'id': output_id('citation_notes_heading'),
                    'type': 'heading',
                    'text': 'Notes',
                    'style': 'heading1',
                    'attrs': {'level': 1},
                    'marks': [],
                }
            )
        doc['blocks'].extend(notes)
    if settings.get('bibliography', True):
        doc['blocks'].append(
            {
                'id': output_id('citation_bibliography_heading'),
                'type': 'heading',
                'text': 'Bibliography',
                'style': 'heading1',
                'attrs': {'level': 1},
                'marks': [],
            }
        )
        # Numbered references follow first citation order; other styles use CSL sorting.
        entries = (
            [(ident, render_bibliography({ident: source}, style)[0]) for ident, source in cited.items()]
            if mode == 'numbered'
            else list(enumerate(render_bibliography(cited, style)))
        )
        for index, (_, text) in enumerate(entries, 1):
            doc['blocks'].append(
                {
                    'id': output_id(f'citation_bibliography_{index}'),
                    'type': 'paragraph',
                    'text': f'[{index}] {text}' if mode == 'numbered' else text,
                    'style': 'body',
                    'attrs': {},
                    'marks': [],
                }
            )
    if downgraded:
        doc['_citation_footnotes_downgraded'] = downgraded
    return doc
