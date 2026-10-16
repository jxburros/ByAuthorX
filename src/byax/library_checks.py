"""Local indexed citation evidence for explicit checks, never for the typing path."""

from .errors import ByaxError
from .model import object_hash


def evidence_key(source_id, mark):
    return object_hash({'source': source_id, 'locator': mark.get('locator', {})})


def quoted_passage(text, mark):
    """The quotation a citation mark claims, or None for paraphrase."""
    import re

    quoted = re.fullmatch(r'\s*["“](.+?)["”]\s*', text, re.DOTALL)
    return quoted.group(1) if quoted else (text if mark.get('kind') == 'quotation' else None)


def _elsewhere(doc, config, quote, *, registry, cache_dir):
    """Verified absence (Q14): literal occurrences of a quote in any eligible attached library."""
    from .retrieval import verify_absence

    exclusive = config.get('source_authority') == 'exclusive'
    eligible = [
        a['library_id']
        for a in doc.get('settings', {}).get('libraries', [])
        if not (exclusive and a.get('role', 'supplementary') == 'open')
    ]
    if not eligible:
        return []
    try:
        report = verify_absence([quote], eligible, registry=registry, cache_dir=cache_dir, limit=5)
    except (ByaxError, OSError):
        return []
    return [
        {k: m.get(k) for k in ('library_id', 'document_id', 'chunk_id', 'path', 'locator', 'excerpt')}
        for m in report['matches']
        if m.get('literal') and not m.get('stale')
    ]


def read_citation(doc, source_id, mark, config=None, *, registry=None, cache_dir=None, text=None):
    """Read only attached, profile-authorized indexed evidence, without indexing or network.

    With the citing object's ``text``, a quotation that is not at the cited locator is
    also looked up literally in every eligible attached library (``elsewhere``).
    """
    from .libraries import LibraryIndex, LibraryRegistry

    source = doc.get('references', {}).get(source_id, {})
    pointer = source.get('library')
    if not pointer:
        return None
    config = config or {}
    identity = pointer.get('library_id')
    attachment = next(
        (a for a in doc.get('settings', {}).get('libraries', []) if a.get('library_id') == identity), None
    )
    base = {
        'library_id': identity,
        'document_id': pointer.get('document_id'),
        'content_hash': pointer.get('content_hash'),
        'source_id': source_id,
        'locator': mark.get('locator', {}),
        'passages': [],
    }
    exclusive = config.get('source_authority') == 'exclusive'
    if attachment is None or (
        exclusive
        and (
            attachment.get('role') == 'open'
            or ('references' in config and source_id not in config['references'])
        )
    ):
        return {
            **base,
            'status': 'unverifiable',
            'reason': 'Library evidence is not authorized by the attachment or profile.',
        }
    locator = mark.get('locator') or {}
    if not isinstance(locator, dict):
        return {**base, 'status': 'unverifiable', 'reason': 'Citation locator cannot be resolved.'}
    try:
        registry = registry or LibraryRegistry()
        library = registry.get(identity)
        index = LibraryIndex(library, cache_dir=cache_dir)
        try:
            result = index.read(pointer['document_id'], chunk_id=locator.get('chunk'))
        except ByaxError:
            # Content identity changes after refresh. The recorded path is only
            # compared to local index records, never opened as an authorization.
            replaced = any(
                row['path'] == pointer.get('path')
                and row.get('document_id')
                and row['document_id'] != pointer['document_id']
                for row in index.status()['files']
            )
            if replaced:
                return {**base, 'status': 'stale', 'reason': 'The indexed library file changed.'}
            raise
    except (ByaxError, OSError, KeyError) as exc:
        return {**base, 'status': 'unverifiable', 'reason': str(exc)}
    if result.get('stale') or result.get('content_hash') != pointer.get('content_hash'):
        return {
            **base,
            'status': 'stale',
            'reason': 'Library content changed; refresh and review the citation.',
        }
    passages = result.get('results', [])
    coordinates = ('page', 'block', 'ordinal', 'heading', 'heading_path', 'start', 'end')
    for key in coordinates:
        if key in locator:
            passages = [p for p in passages if str(p.get('locator', {}).get(key)) == str(locator[key])]
    if any(key not in (*coordinates, 'chunk', 'label') for key in locator):
        return {**base, 'status': 'unverifiable', 'reason': 'This locator format needs manual verification.'}
    if not passages:
        return {**base, 'status': 'unverifiable', 'reason': 'The cited locator has no indexed passage.'}
    snapshot = {
        **base,
        'status': 'available',
        'passages': passages,
        'truncated': result.get('truncated', False),
    }
    quote = (
        quoted_passage(text[mark['start'] : mark['end']], mark)
        if text is not None and 'start' in mark
        else None
    )
    if quote and not any(quote in p['text'] for p in passages):
        snapshot['elsewhere'] = _elsewhere(doc, config, quote, registry=registry, cache_dir=cache_dir)
    return snapshot


def check_citation(sink, block, mark, source_id, snapshot):
    """Report located quotation outcomes. Stored excerpts never override live staleness."""
    import re

    text = block.get('text', '')[mark['start'] : mark['end']]
    quoted = re.fullmatch(r'\s*["“](.+?)["”]\s*', text, re.DOTALL)
    passage = quoted.group(1) if quoted else (text if mark.get('kind') == 'quotation' else None)
    status = snapshot['status']
    if status != 'available':
        message = snapshot.get('reason', 'Library citation cannot be verified.')
        sink.cover('factual', 'factual.library_citation', 'unmeasurable', message, targets=[block['id']])
        sink.add(
            'factual.library_citation',
            'factual',
            'warning',
            message,
            block['id'],
            mark['start'],
            mark['end'],
            text,
            outcome='not_established',
            measured={'source_id': source_id, 'verification': status},
        )
        return
    if passage is None:
        sink.cover(
            'factual',
            'factual.library_citation',
            'unmeasurable',
            'Paraphrase and inference require semantic review; matching library identity does not establish support.',
            targets=[block['id']],
        )
        return
    matches = [p for p in snapshot['passages'] if passage in p['text']]
    evidence = [
        {
            'source_id': source_id,
            'chunk_id': p['chunk_id'],
            'locator': p['locator'],
            'library_id': snapshot['library_id'],
            'document_id': snapshot['document_id'],
            'content_hash': snapshot['content_hash'],
            'excerpt': passage,
            'relation': 'quotation',
            'source_version': {'content_hash': snapshot['content_hash']},
        }
        for p in matches
    ]
    if matches:
        sink.coverage.append(
            {
                'suite': 'factual',
                'rule': 'factual.library_citation',
                'status': 'completed',
                'evidence': {
                    'target': block['id'],
                    'range': {'start': mark['start'], 'end': mark['end']},
                    'outcome': 'supported',
                    'verification': 'verified',
                    'evidence': evidence,
                },
            }
        )
    else:
        elsewhere = snapshot.get('elsewhere') or []
        message = (
            'The bounded library read is incomplete; quotation support is not established.'
            if snapshot.get('truncated')
            else 'Quoted passage does not match the cited library locator, but the exact text occurs at '
            'another indexed location; check the citation locator.'
            if elsewhere
            else 'Quoted passage does not match the cited library locator.'
        )
        if snapshot.get('truncated'):
            sink.cover('factual', 'factual.library_citation', 'unmeasurable', message, targets=[block['id']])
        sink.add(
            'factual.library_citation',
            'factual',
            'warning',
            message,
            block['id'],
            mark['start'],
            mark['end'],
            text,
            outcome='not_established',
            measured={
                'source_id': source_id,
                'verification': 'unconfirmed',
                'library_id': snapshot['library_id'],
                'document_id': snapshot['document_id'],
                'content_hash': snapshot['content_hash'],
                'locator': snapshot['locator'],
                **({'literal_matches': elsewhere} if elsewhere else {}),
            },
            repair={
                'kind': 'manual',
                'guidance': 'Correct the quotation or citation explicitly; source corrections are separate.',
            },
        )


def invalidate_library_findings(doc, library_id):
    """Called after explicit library refresh, without I/O or changes to authored text."""
    source_ids = {
        key
        for key, source in doc.get('references', {}).items()
        if source.get('library', {}).get('library_id') == library_id
    }
    for source_id in source_ids:
        doc['references'][source_id]['verification'] = 'unverifiable'
    for finding in doc.get('review', {}).get('findings', []):
        if finding.get('measured', {}).get('source_id') in source_ids or source_ids.intersection(
            finding.get('source_versions', {})
        ):
            finding['status'] = 'stale'
            finding['stale_reasons'] = ['Library refreshed; recheck indexed evidence.']
    health = doc.get('review', {}).get('health')
    if health and source_ids:
        health['stale'] = True
