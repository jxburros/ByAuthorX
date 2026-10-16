"""Local indexed citation evidence for explicit checks, never for the typing path."""

from .errors import ByaxError
from .model import object_hash
from .quote_match import describe_differences, infer_kind, locate, quoted_passage


def evidence_key(source_id, mark):
    return object_hash({'source': source_id, 'locator': mark.get('locator', {})})


def read_citation(doc, source_id, mark, config=None, *, registry=None, cache_dir=None):
    """Read only attached, profile-authorized indexed evidence, without indexing or network."""
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
    return {**base, 'status': 'available', 'passages': passages, 'truncated': result.get('truncated', False)}


def source_texts(source):
    """Text a source carries inside the document: its excerpt, text, content and excerpt list."""
    texts = [source[k] for k in ('text', 'excerpt', 'content') if isinstance(source.get(k), str)]
    texts += [
        e if isinstance(e, str) else e.get('text', '')
        for e in source.get('excerpts', [])
        if isinstance(e, (str, dict))
    ]
    return texts


def infer_citation_kind(doc, op, *, registry=None, cache_dir=None):
    """``{'kind', 'reason'}`` for a cite operation that names no kind.

    ``quotation`` when the selected text is in quotation marks or is found in the cited source
    (its stored excerpt, or the indexed passage the locator names); otherwise ``paraphrase``.
    Reads only attached, authorized library passages, like the citation checks.
    """
    from .model import find_object

    block = find_object(doc, op['target'])
    selected = block.get('text', '')[op['start'] : op['end']]
    source = doc.get('references', {}).get(op['source'], {})
    texts = source_texts(source)
    if source.get('library') and op.get('locator'):
        snapshot = read_citation(
            doc, op['source'], {'locator': op['locator']}, registry=registry, cache_dir=cache_dir
        )
        if snapshot and snapshot['status'] == 'available':
            texts += [p['text'] for p in snapshot['passages']]
    return infer_kind(selected, texts)


def check_citation(sink, block, mark, source_id, snapshot):
    """Report located quotation outcomes. Stored excerpts never override live staleness."""
    text = block.get('text', '')[mark['start'] : mark['end']]
    quoted = quoted_passage(text)
    passage = quoted if quoted is not None else (text if mark.get('kind') == 'quotation' else None)
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
    # Exact and normalized matches (curly quotes, line-break hyphens, ligatures) verify the quotation.
    # A near match is advisory only: it is shown with its differing characters, never confirmed.
    located = [(p, locate(passage, p['text'])) for p in snapshot['passages']]
    matches = [(p, found) for p, found in located if found['normalized_pass']]
    near = max(
        (item for item in located if item[1]['match'] == 'near'),
        key=lambda item: item[1]['score'],
        default=None,
    )
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
            'quote_match': {
                'match': found['match'],
                'raw_pass': found['raw_pass'],
                'normalized_pass': found['normalized_pass'],
                'span': found['span'],
            },
        }
        for p, found in matches
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
    elif near is not None:
        passage_near, found = near
        sink.add(
            'factual.library_citation',
            'factual',
            'advisory',
            'Quoted passage nearly matches the cited library passage but is not identical ('
            + describe_differences(found['differences'])
            + '). It is not confirmed.',
            block['id'],
            mark['start'],
            mark['end'],
            text,
            outcome='not_established',
            measured={
                'source_id': source_id,
                'verification': 'quote_near_match',
                'match': 'near',
                'score': found['score'],
                'span': found['span'],
                'differences': found['differences'],
                'chunk_id': passage_near['chunk_id'],
                'library_id': snapshot['library_id'],
                'document_id': snapshot['document_id'],
                'content_hash': snapshot['content_hash'],
                'locator': snapshot['locator'],
            },
            repair={
                'kind': 'manual',
                'guidance': 'Compare the quotation with the source and correct it, or cite it as a paraphrase.',
            },
        )
    else:
        message = (
            'The bounded library read is incomplete; quotation support is not established.'
            if snapshot.get('truncated')
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
