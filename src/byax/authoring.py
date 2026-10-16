"""Author-facing review constraints and explicit, local citation inspection."""

import copy
import re
from collections import Counter

from . import model
from .library_checks import read_citation
from .profiles import effective_profile, section_map

_NUMBER = r'[+−-]?(?:\d+(?:[.,:/–-]\d+)*|\.\d+)(?:[eE][+-]?\d+)?(?:\s?%)?'
_QUOTATION = r"""“[^”]*”|"[^"\n]*"|‘[^’]*’|(?<!\w)'[^'\n]*'(?!\w)"""

REVIEW_BRIEFS = {
    'profile': ('Use active profile', ''),
    'light': ('Copyedit lightly', 'Make minimal spelling and grammar edits. Preserve meaning and voice.'),
    'voice': ('Preserve my voice', 'Preserve distinctive phrasing, rhythm and intentional variation.'),
    'structure': (
        'Suggest structure separately',
        'Report structural recommendations as findings only. Do not propose text edits.',
    ),
}


def protected_spans(block, constraints):
    """Canonical ranges keep partial selections from bypassing preservation."""
    text = block.get('text', '')
    spans = []
    if constraints.get('preserve_quotes'):
        for match in re.finditer(_QUOTATION, text):
            spans.append({'start': match.start(), 'end': match.end()})
    patterns = []
    if constraints.get('preserve_numbers'):
        patterns.append(_NUMBER)
    patterns.extend(r'(?<!\w)' + re.escape(term) + r'(?!\w)' for term in constraints.get('terms', []))
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            spans.append({'start': match.start(), 'end': match.end()})
    return spans


def validate_author_edit(block, start, end, replacement, constraints):
    """Reject returned edits that violate explicit preservation choices.

    Counts are compared across the complete supplied block, including edits that
    touch only part of a number, quotation or term. Citation offsets are canonical.
    These lexical checks do not certify preservation of meaning or voice.
    """
    if not constraints:
        return
    if constraints.get('brief') == 'structure':
        raise ValueError('Structural review permits findings only')
    text = block['text']
    candidate = text[:start] + replacement + text[end:]
    offset = block.get('source_offset', 0)
    for span in block.get('author_protected_spans', []):
        a, b = span['start'], span['end']
        if (start + offset < b and a < end + offset) or (start == end and a < start + offset < b):
            raise ValueError('Proposed edit touches a protected quotation, number or term')
    if constraints.get('preserve_numbers') and re.findall(_NUMBER, text) != re.findall(_NUMBER, candidate):
        raise ValueError('Proposed edit changes protected numbers')
    if constraints.get('preserve_quotes'):
        if re.findall(_QUOTATION, text) != re.findall(_QUOTATION, candidate):
            raise ValueError('Proposed edit changes protected quotations')
        if block.get('style') == 'quotation' or block.get('attrs', {}).get('quotation'):
            raise ValueError('Proposed edit changes a quotation block')
    for term in constraints.get('terms', []):
        pattern = r'(?<!\w)' + re.escape(term) + r'(?!\w)'
        if len(re.findall(pattern, text)) != len(re.findall(pattern, candidate)):
            raise ValueError('Proposed edit changes protected terminology')
    if constraints.get('preserve_citations'):
        offset = block.get('source_offset', 0)
        for mark in block.get('marks', []):
            if mark.get('type') != 'citation':
                continue
            a, b = mark['start'], mark['end']
            if (start + offset < b and a < end + offset) or (start == end and a < start + offset < b):
                raise ValueError('Proposed edit touches cited text')


def citation_evidence(doc, *, profile=None, registry=None, cache_dir=None, limit=100, offset=0):
    """Inspect cited passages on explicit request; never index, browse, or mutate.

    A matching quotation proves a text match only. Paraphrases always require
    interpretation, and a stored excerpt is never a fallback for changed evidence.
    """
    from .errors import ByaxError

    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ByaxError('invalid_input', 'Evidence limit must be 1–100 and offset must be nonnegative.')
    rows = []
    sections = section_map(doc)
    cited = [
        (target, mark)
        for block in doc['blocks']
        for target in ([cell for row in block.get('rows', []) for cell in row['cells']] or [block])
        for mark in target.get('marks', [])
        if mark.get('type') == 'citation'
    ]
    for target, mark in cited[offset : offset + limit]:
        source_id = mark['value']
        source = doc.get('references', {}).get(source_id, {})
        claim = target.get('text', '')[mark['start'] : mark['end']]
        effective = effective_profile(doc, target['id'], profile, sections=sections)
        snapshot = read_citation(
            doc,
            source_id,
            mark,
            effective['profile'] if effective else None,
            registry=registry,
            cache_dir=cache_dir,
        )
        status = snapshot['status'] if snapshot else 'manual' if source else 'unverifiable'
        passages = snapshot.get('passages', []) if snapshot else []
        quoted = re.fullmatch(r'\s*[“"](.+?)[”"]\s*', claim, re.DOTALL)
        quote = quoted.group(1) if quoted else claim if mark.get('kind') == 'quotation' else None
        method, outcome = 'Author review', 'Needs interpretation'
        if status == 'available':
            method = 'Exact quotation check' if quote is not None else 'Source retrieval'
            if quote is None:
                outcome = 'Needs interpretation'
            elif any(quote in p['text'] for p in passages):
                outcome = 'Quotation matches; assess claim support separately'
            else:
                outcome = 'Quotation match not established'
        elif status != 'manual':
            outcome = 'Evidence changed' if status == 'stale' else 'Evidence unavailable'
        rows.append(
            {
                'target': target['id'],
                'range': {'start': mark['start'], 'end': mark['end']},
                'claim': claim,
                'source_id': source_id,
                'title': source.get('csl', {}).get('title', source_id),
                'locator': copy.deepcopy(mark.get('locator', {})),
                'status': status,
                'method': method,
                'outcome': outcome,
                'reason': snapshot.get('reason', '') if snapshot else 'Manually supplied citation.',
                'passages': copy.deepcopy(passages),
            }
        )
    return {
        'revision': doc['revision'],
        'citations': rows,
        'counts': dict(Counter(r['status'] for r in rows)),
        'total': len(cited),
        'offset': offset,
        'next_offset': offset + len(rows) if offset + len(rows) < len(cited) else None,
        'truncated': offset + len(rows) < len(cited),
    }


def create_research_sample(parent, *, registry=None, cache_dir=None):
    """Create an isolated, offline tutorial project in a fresh directory."""
    import tempfile
    from pathlib import Path

    from .citations import source_from_hit
    from .libraries import LibraryIndex, LibraryRegistry
    from .storage import save_document

    folder = Path(tempfile.mkdtemp(prefix='ByAuthorX-research-', dir=parent))
    sources = folder / 'research'
    sources.mkdir()
    quote = 'The pilot included 24 participants.'
    (sources / 'pilot-notes.txt').write_text(
        quote + '\n\nThese fictional notes are tutorial material, not real research evidence.',
        encoding='utf-8',
    )
    registry = registry or LibraryRegistry()
    library = registry.create('Research walkthrough', [sources], retrieval_policy='ask')
    index = LibraryIndex(library, cache_dir=cache_dir)
    index.refresh()
    hit = index.search('participants')['results'][0]
    source = source_from_hit(hit)
    doc = model.new_document('Research walkthrough', 'Pilot report\n“' + quote + '”\nThe pilot was useful.')
    doc['blocks'][0].update(type='heading', style='heading1', attrs={'level': 1})
    doc['references'][source['id']] = source
    doc['settings']['libraries'] = [
        {'library_id': library['library_id'], 'name': library['name'], 'role': 'supplementary'}
    ]
    doc['blocks'][1]['marks'] = [
        {
            'type': 'citation',
            'start': 0,
            'end': len(quote) + 2,
            'value': source['id'],
            'locator': {**hit.get('locator', {}), 'chunk': hit['chunk_id']},
            'kind': 'quotation',
        }
    ]
    path = folder / 'Pilot report.byax'
    save_document(doc, path)
    return path
