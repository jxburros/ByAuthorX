"""Local bibliographic interchange and CSL rendering; never performs outside lookups.

Legacy reference records remain valid. Structured records are checked on creation,
without tightening package validation or discarding unknown CSL metadata.
"""

import copy
import hashlib
import json
import re
from datetime import date

from .errors import ByaxError

# quote_near_match is advisory: the quotation aligns closely but not exactly with the source, so it
# is shown with its differing characters and is never reported as verified.
STATUSES = {'verified', 'stale', 'unverifiable', 'unconfirmed', 'manual', 'quote_near_match'}
STYLES = {'apa': 'apa', 'mla': 'modern-language-association', 'chicago': 'chicago-notes-bibliography'}
MAX_IMPORT_BYTES = 8 * 1024 * 1024
MAX_SOURCES = 10000


def _error(message):
    raise ByaxError('invalid_input', message)


def validate_source(source, source_id=None):
    """Validate new structured fields while preserving existing excerpt-only records."""
    if not isinstance(source, dict):
        _error('Source must be an object.')
    result = copy.deepcopy(source)
    if 'csl' not in result:
        return result
    csl = result['csl']
    if not isinstance(csl, dict) or not isinstance(csl.get('type'), str) or not csl['type']:
        _error('A structured source requires a CSL object with a type.')
    if 'title' in csl and not isinstance(csl['title'], str):
        _error('CSL title must be text.')
    if 'author' in csl and (
        not isinstance(csl['author'], list)
        or any(
            not isinstance(person, dict) or any(not isinstance(v, str) for v in person.values())
            for person in csl['author']
        )
    ):
        _error('CSL authors must be name objects.')
    for field in ('issued', 'accessed', 'original-date'):
        if field not in csl:
            continue
        value = csl[field]
        if not isinstance(value, dict):
            _error('CSL dates must be objects.')
        parts = value.get('date-parts', [])
        if not isinstance(parts, list) or any(
            not isinstance(part, list)
            or not 1 <= len(part) <= 3
            or any(type(number) is not int for number in part)
            for part in parts
        ):
            _error('CSL date-parts must contain numeric year, month and day arrays.')
    if source_id is not None:
        if result.get('id', source_id) != source_id:
            _error('Source ID must match the reference key.')
        result['id'] = source_id
    if (
        not isinstance(result.get('verification', 'unconfirmed'), str)
        or result.get('verification', 'unconfirmed') not in STATUSES
    ):
        _error('Unknown source verification status.')
    if 'library' in result:
        lib = result['library']
        if not isinstance(lib, dict) or any(
            not isinstance(lib.get(k), str) or not lib[k]
            for k in ('library_id', 'document_id', 'content_hash')
        ):
            _error('Library sources require library ID, document ID and content hash.')
    if 'byax' in result:
        # Another .byax draft as a source, pinned by content hash (byax.version_pins).
        from .version_pins import pointer_problem

        problem = pointer_problem(result['byax'])
        if problem:
            _error(problem)
    if 'import' in result:
        origin = result['import']
        if not isinstance(origin, dict) or origin.get('origin') not in {
            'zotero',
            'bibtex',
            'ris',
            'csl-json',
            'manual',
        }:
            _error('Unknown source import origin.')
    return result


def _name(text):
    if ',' in text:
        family, given = text.split(',', 1)
        return {'family': family.strip(), 'given': given.strip()}
    parts = text.strip().rsplit(' ', 1)
    return {'family': parts[-1], **({'given': parts[0]} if len(parts) == 2 else {})}


def _bibtex_records(text):
    import bibtexparser
    from bibtexparser.bparser import BibTexParser

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    database = bibtexparser.loads(text, parser=parser)
    if text.strip() and not database.entries:
        _error('No BibTeX entries could be read.')
    types = {
        'article': 'article-journal',
        'inproceedings': 'paper-conference',
        'phdthesis': 'thesis',
        'mastersthesis': 'thesis',
        'incollection': 'chapter',
        'techreport': 'report',
        'misc': 'document',
    }
    fields = {
        'title': 'title',
        'journal': 'container-title',
        'booktitle': 'container-title',
        'publisher': 'publisher',
        'address': 'publisher-place',
        'doi': 'DOI',
        'url': 'URL',
        'volume': 'volume',
        'number': 'issue',
        'pages': 'page',
        'isbn': 'ISBN',
        'abstract': 'abstract',
    }
    records = []
    for entry in database.entries:
        record = {'id': entry['ID'], 'type': types.get(entry['ENTRYTYPE'], 'book')}
        record.update({dest: entry[key] for key, dest in fields.items() if key in entry})
        for field in ('author', 'editor'):
            if entry.get(field):
                record[field] = [_name(n) for n in entry[field].split(' and ')]
        year = re.match(r'\d{1,4}', entry.get('year', ''))
        if year:
            record['issued'] = {'date-parts': [[int(year.group())]]}
        records.append(record)
    return records


def _ris_records(text):
    records, entry, last = [], None, None
    for line in text.splitlines():
        match = re.match(r'^([A-Z0-9]{2})  - ?(.*)$', line)
        if not match:
            if line.strip() and entry is not None and last:
                entry[last][-1] += ' ' + line.strip()
            continue
        tag, value = match.groups()
        if tag == 'TY':
            if entry is not None:
                _error('RIS record is missing its ER terminator.')
            entry = {'TY': [value]}
        elif tag == 'ER':
            if entry is not None:
                records.append(entry)
            entry, last = None, None
        elif entry is not None:
            entry.setdefault(tag, []).append(value)
            last = tag
    if entry is not None:
        _error('RIS record is missing its ER terminator.')
    result = []
    for item in records:
        first = lambda *keys, item=item: next((item[k][0] for k in keys if item.get(k)), '')
        record = {
            'type': {
                'JOUR': 'article-journal',
                'CHAP': 'chapter',
                'THES': 'thesis',
                'RPRT': 'report',
                'BOOK': 'book',
            }.get(first('TY'), 'document'),
            'title': first('TI', 'T1'),
        }
        for target, tags in {
            'id': ('ID',),
            'container-title': ('JO', 'JF', 'T2'),
            'publisher': ('PB',),
            'DOI': ('DO',),
            'URL': ('UR',),
            'volume': ('VL',),
            'issue': ('IS',),
        }.items():
            if value := first(*tags):
                record[target] = value
        if item.get('AU') or item.get('A1'):
            record['author'] = [_name(n) for n in item.get('AU', item.get('A1', []))]
        year = re.match(r'\d{1,4}', first('PY', 'Y1'))
        if year:
            record['issued'] = {'date-parts': [[int(year.group())]]}
        if page := first('SP'):
            record['page'] = page + ('-' + first('EP') if first('EP') else '')
        result.append(record)
    if text.strip() and not result:
        _error('No RIS entries could be read.')
    return result


def import_sources(text, format, existing=None):
    """Return add_reference-ready sources; origin item keys make reimport idempotent."""
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_IMPORT_BYTES:
        _error('Source import exceeds the 8 MiB text limit.')
    format = format.lower().lstrip('.')
    format = {'json': 'csl-json', 'bib': 'bibtex', 'better-bibtex': 'bibtex'}.get(format, format)
    try:
        if format == 'csl-json':
            records = json.loads(text)
            if not isinstance(records, list):
                _error('CSL-JSON import must be an array of records.')
        elif format == 'bibtex':
            records = _bibtex_records(text)
        elif format == 'ris':
            records = _ris_records(text)
        else:
            _error('Supported source imports are CSL-JSON, BibTeX and RIS.')
    except (ValueError, TypeError, KeyError) as exc:
        raise ByaxError('invalid_input', 'Malformed bibliographic import.', reason=str(exc)) from exc
    if len(records) > MAX_SOURCES:
        _error('Source import exceeds the 10000 record limit.')
    existing = existing or {}
    by_origin = {
        (s.get('import', {}).get('origin'), str(s.get('import', {}).get('key'))): ident
        for ident, s in existing.items()
        if isinstance(s, dict)
    }
    result = {}
    for csl in records:
        if not isinstance(csl, dict):
            _error('Each CSL record must be an object.')
        key = str(csl.get('id') or hashlib.sha256(json.dumps(csl, sort_keys=True).encode()).hexdigest()[:24])
        ident = by_origin.get(
            (format, key), 'source_' + hashlib.sha256(f'{format}:{key}'.encode()).hexdigest()[:24]
        )
        record = copy.deepcopy(existing.get(ident, {}))
        record.update(
            id=ident,
            csl=copy.deepcopy(csl),
            retrieved=date.today().isoformat(),
            verification='unconfirmed',
            **{'import': {'origin': format, 'key': key}},
        )
        result[ident] = validate_source(record, ident)
    return list(result.values())


def export_sources(references, format='csl-json'):
    """Only export bibliographic metadata, never library paths, excerpts or local settings."""
    records = []
    for ident, source in references.items():
        csl = copy.deepcopy(source.get('csl', {'type': 'document', 'title': source.get('title', ident)}))
        csl['id'] = str(source.get('import', {}).get('key') or csl.get('id') or ident)
        records.append(csl)
    format = format.lower().lstrip('.')
    if format in ('json', 'csl-json'):
        return json.dumps(records, ensure_ascii=False, indent=2) + '\n'
    if format not in ('bib', 'bibtex'):
        _error('Supported source exports are CSL-JSON and BibTeX.')
    import bibtexparser

    database = bibtexparser.bibdatabase.BibDatabase()
    fields = {
        'title': 'title',
        'container-title': 'journal',
        'publisher': 'publisher',
        'DOI': 'doi',
        'URL': 'url',
        'volume': 'volume',
        'issue': 'number',
        'page': 'pages',
    }
    for record in records:
        entry = {
            'ID': re.sub(r'[^\w:.-]', '_', record['id']),
            'ENTRYTYPE': {
                'article-journal': 'article',
                'chapter': 'incollection',
                'book': 'book',
                'thesis': 'phdthesis',
            }.get(record.get('type'), 'misc'),
        }
        entry.update({dest: str(record[key]) for key, dest in fields.items() if key in record})
        for field in ('author', 'editor'):
            if record.get(field):
                entry[field] = ' and '.join(
                    n.get('literal') or ', '.join(filter(None, [n.get('family'), n.get('given')]))
                    for n in record[field]
                )
        parts = record.get('issued', {}).get('date-parts', [])
        if parts and parts[0]:
            entry['year'] = str(parts[0][0])
        database.entries.append(entry)
    return bibtexparser.dumps(database)


def search_sources(references, query=''):
    terms = query.casefold().split()
    return [
        dict(copy.deepcopy(source), id=ident)
        for ident, source in references.items()
        if all(term in json.dumps(source, ensure_ascii=False).casefold() for term in terms)
    ]


def source_from_hit(hit):
    library_id, document_id = hit['library_id'], hit['document_id']
    ident = 'source_' + hashlib.sha256(f'{library_id}:{document_id}'.encode()).hexdigest()[:24]
    source = {
        'id': ident,
        'csl': {'id': ident, 'type': 'document', 'title': hit.get('title') or hit.get('path') or document_id},
        'library': {
            'library_id': library_id,
            'document_id': document_id,
            'content_hash': hit['content_hash'],
            'path': hit.get('path', ''),
            'relative_path': hit.get('path', ''),
            'chunk_id': hit.get('chunk_id', ''),
        },
        'excerpt': hit.get('text', ''),
        'retrieved': date.today().isoformat(),
        'verification': 'unverifiable',
    }
    return validate_source(source, ident)


def verify_source_detail(source, read, locator=None, quote=None):
    """Status plus how a quotation matched: ``{'status', 'quote': {match, raw_pass, normalized_pass, ...}}``.

    read(library_id, document_id, locator) returns text/content_hash/stale, or raises ByaxError
    when unavailable. No stored status is trusted as evidence. A quotation that only matches
    after normalization (curly quotes, line-break hyphenation, ligatures) is ``verified`` with
    ``raw_pass`` false; a close but inexact alignment is the advisory ``quote_near_match``.
    """
    from .quote_match import describe_differences, locate

    library = source.get('library')
    if not library:
        return {'status': 'manual' if source.get('verification') == 'manual' else 'unconfirmed'}
    try:
        current = read(library['library_id'], library['document_id'], locator or {})
    except (ByaxError, OSError, KeyError):
        return {'status': 'unverifiable'}
    if current.get('stale') or current.get('content_hash') != library['content_hash']:
        return {'status': 'stale'}
    if not quote:
        return {'status': 'verified'}
    found = locate(quote, current.get('text', ''))
    detail = {
        'match': found['match'],
        'raw_pass': found['raw_pass'],
        'normalized_pass': found['normalized_pass'],
    }
    if found['match'] == 'none':
        return {'status': 'unconfirmed', 'quote': detail}
    detail.update(span=found['span'], score=found['score'])
    if found['match'] == 'near':
        detail.update(differences=found['differences'], summary=describe_differences(found['differences']))
        return {'status': 'quote_near_match', 'quote': detail}
    return {'status': 'verified', 'quote': detail}


def verify_source(source, read, locator=None, quote=None):
    """Compute status from a fresh authorized library read callback (see verify_source_detail)."""
    return verify_source_detail(source, read, locator, quote)['status']


def _processor(references, style):
    from citeproc import CitationStylesBibliography, CitationStylesStyle, formatter
    from citeproc.source.json import CiteProcJSON
    from citeproc_styles import get_style_filepath

    if style.lower() not in STYLES:
        _error('Citation style must be APA, MLA or Chicago.')
    records = []
    for ident, source in references.items():
        csl = copy.deepcopy(source.get('csl', {'type': 'document', 'title': source.get('title', ident)}))
        csl['id'] = ident
        records.append(csl)
    return CitationStylesBibliography(
        CitationStylesStyle(get_style_filepath(STYLES[style.lower()]), validate=False),
        CiteProcJSON(records),
        formatter.plain,
    )


def render_citation(source, style='apa', locator=None, prefix='', suffix=''):
    from citeproc import Citation, CitationItem
    from citeproc.source import Locator

    ident = source.get('id', 'source')
    processor = _processor({ident: source}, style)
    options = {}
    if locator:
        if isinstance(locator, str):
            locator = {'page': locator}
        label = locator.get('label', 'page' if 'page' in locator else 'paragraph')
        value = locator.get('page', locator.get('text', locator.get('paragraph', '')))
        if value:
            options['locator'] = Locator(label, str(value))
    citation = Citation([CitationItem(ident, **options)])
    processor.register(citation)
    return prefix + str(processor.cite(citation, lambda item: None)) + suffix


def render_bibliography(references, style='apa'):
    from citeproc import Citation, CitationItem

    processor = _processor(references, style)
    for ident in references:
        processor.register(Citation([CitationItem(ident)]))
    processor.sort()
    return [str(entry) for entry in processor.bibliography()]
