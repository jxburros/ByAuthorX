"""RIS reading and writing (context engine plan 9: bibliography exchange).

RIS is a tagged text format: ``TY  - JOUR`` starts a record, ``ER  - `` ends it, and every other
line is a two-character tag, two spaces, a hyphen and a value. :func:`parse` maps the tags in
common use to CSL-JSON (type, title, authors, editors, container, series, date, pages, volume,
issue, publisher and place, ISBN or ISSN, DOI, URL, abstract, keywords, notes, language, edition,
accession number); tags it does not map are kept under ``custom.ris`` so nothing is dropped, and
:func:`dump` writes them back. Only bibliographic metadata is written, never library paths or
excerpts.
"""

from __future__ import annotations

import re

from .errors import ByaxError

TYPES = {
    'JOUR': 'article-journal',
    'EJOUR': 'article-journal',
    'INPR': 'article-journal',
    'JFULL': 'article-journal',
    'MGZN': 'article-magazine',
    'NEWS': 'article-newspaper',
    'BOOK': 'book',
    'EBOOK': 'book',
    'EDBOOK': 'book',
    'SER': 'book',
    'CHAP': 'chapter',
    'ECHAP': 'chapter',
    'CONF': 'paper-conference',
    'CPAPER': 'paper-conference',
    'THES': 'thesis',
    'RPRT': 'report',
    'ELEC': 'webpage',
    'WEB': 'webpage',
    'BLOG': 'post-weblog',
    'UNPB': 'manuscript',
    'PAT': 'patent',
    'DATA': 'dataset',
    'COMP': 'software',
    'MPCT': 'motion_picture',
    'VIDEO': 'motion_picture',
    'SOUND': 'song',
    'MAP': 'map',
    'ART': 'graphic',
    'ENCYC': 'entry-encyclopedia',
    'DICT': 'entry-dictionary',
    'STAT': 'legislation',
    'BILL': 'bill',
    'CASE': 'legal_case',
    'PCOMM': 'personal_communication',
    'STAND': 'standard',
    'GEN': 'document',
}
# CSL type -> the RIS type written on export (the first RIS type listed for it above).
EXPORT_TYPES = {}
for _ris, _csl in TYPES.items():
    EXPORT_TYPES.setdefault(_csl, _ris)
EXPORT_TYPES.update({'article': 'JOUR', 'webpage': 'ELEC', 'document': 'GEN'})
# Tags this module maps, so everything else can be kept verbatim under custom.ris.
_MAPPED = {
    'TY',
    'ID',
    'TI',
    'T1',
    'T2',
    'T3',
    'BT',
    'JO',
    'JF',
    'JA',
    'J1',
    'J2',
    'A1',
    'AU',
    'A2',
    'ED',
    'A3',
    'A4',
    'TA',
    'PY',
    'Y1',
    'DA',
    'Y2',
    'AB',
    'N2',
    'N1',
    'KW',
    'SP',
    'EP',
    'VL',
    'IS',
    'CP',
    'CY',
    'PB',
    'SN',
    'DO',
    'UR',
    'LA',
    'ET',
    'M3',
    'AN',
    'ER',
}
_ISSN = re.compile(r'^\d{4}-?\d{3}[\dxX]$')
_ISBN = re.compile(r'^(?:97[89][- ]?)?(?:\d[- ]?){9}[\dxX]$')
_BOOK_LIKE = {'book', 'chapter', 'report', 'thesis', 'entry-encyclopedia', 'entry-dictionary'}


def _error(message):
    raise ByaxError('invalid_input', message)


def person(text):
    """A CSL name from ``Family, Given[, Suffix]``; a corporate author ends with a comma or has none."""
    text = text.strip()
    if text.endswith(','):
        return {'literal': text.rstrip(',').strip()}
    parts = [p.strip() for p in text.split(',')]
    if len(parts) == 1:
        words = parts[0].rsplit(' ', 1)
        # No comma: "Jane Smith" is a personal name, a longer phrase is more likely an organization.
        if len(words) == 2 and len(parts[0].split()) <= 3:
            return {'family': words[1], 'given': words[0]}
        return {'literal': parts[0]}
    name = {'family': parts[0], 'given': parts[1]}
    if len(parts) > 2 and parts[2]:
        name['suffix'] = parts[2]
    return name


def _date(value):
    """CSL date from ``YYYY/MM/DD/other`` (parts may be empty), or None."""
    pieces = (value.strip().split('/') + ['', '', ''])[:4]
    numbers = []
    for piece in pieces[:3]:
        if piece.strip().isdigit():
            numbers.append(int(piece))
        else:
            break
    if not numbers:
        match = re.match(r'\s*(\d{4})\b', value)
        if not match:
            return None
        numbers = [int(match.group(1))]
    if len(numbers) > 1 and not 1 <= numbers[1] <= 12:
        numbers = numbers[:1]
    if len(numbers) > 2 and not 1 <= numbers[2] <= 31:
        numbers = numbers[:2]
    date = {'date-parts': [numbers]}
    if pieces[3].strip():
        date['season'] = pieces[3].strip()
    return date


def read_records(text):
    """The records of a RIS file as ``{tag: [values]}`` dictionaries (tag order is kept)."""
    records, entry, last = [], None, None
    for line in text.splitlines():
        match = re.match(r'^([A-Z][A-Z0-9])  - ?(.*)$', line.lstrip('﻿'))
        if not match:
            if line.strip() and entry is not None and last:
                entry[last][-1] += ' ' + line.strip()
            continue
        tag, value = match.groups()
        value = value.strip()
        if tag == 'TY':
            if entry is not None:
                _error('RIS record is missing its ER terminator.')
            entry = {'TY': [value]}
            last = 'TY'
        elif tag == 'ER':
            if entry is not None:
                records.append(entry)
            entry, last = None, None
        elif entry is not None:
            entry.setdefault(tag, []).append(value)
            last = tag
    if entry is not None:
        _error('RIS record is missing its ER terminator.')
    return records


def parse(text):
    """CSL-JSON records for a RIS file."""
    result = []
    for item in read_records(text):
        first = lambda *keys, item=item: next((item[k][0] for k in keys if item.get(k) and item[k][0]), '')
        ris_type = first('TY').upper()
        kind = TYPES.get(ris_type, 'document')
        record = {
            'type': kind,
            'title': first('TI', 'T1', 'BT') if ris_type not in ('CHAP', 'ECHAP') else first('TI', 'T1'),
        }
        for target, tags in {
            'id': ('ID',),
            'publisher': ('PB',),
            'publisher-place': ('CY',),
            'DOI': ('DO',),
            'URL': ('UR',),
            'volume': ('VL',),
            'language': ('LA',),
            'edition': ('ET',),
            'abstract': ('AB', 'N2'),
            'number': ('AN',),
            'genre': ('M3',),
            'collection-title': ('T3',),
            'container-title-short': ('JA', 'J1', 'J2'),
        }.items():
            if value := first(*tags):
                record[target] = value
        container = first('JO', 'JF', 'T2') or (first('BT') if ris_type in ('CHAP', 'ECHAP') else '')
        if container:
            record['container-title'] = container
        issue = first('IS')
        if issue:
            record['issue'] = issue
        elif ris_type in ('JOUR', 'EJOUR', 'MGZN') and first('CP'):
            record['issue'] = first('CP')
        if first('CP') and 'publisher-place' not in record and ris_type not in ('JOUR', 'EJOUR', 'MGZN'):
            record['publisher-place'] = first('CP')
        for field, tags in (
            ('author', ('AU', 'A1')),
            ('editor', ('ED', 'A2')),
            ('collection-editor', ('A3',)),
            ('translator', ('TA', 'A4')),
        ):
            names = [person(n) for tag in tags for n in item.get(tag, []) if n.strip()]
            if names:
                record[field] = names
        issued = _date(first('PY', 'Y1', 'DA'))
        if first('DA') and (not issued or len(issued['date-parts'][0]) < 2):
            issued = _date(first('DA')) or issued
        if issued:
            record['issued'] = issued
        if accessed := _date(first('Y2')):
            record['accessed'] = accessed
        if first('SP'):
            record['page'] = first('SP') + ('-' + first('EP') if first('EP') else '')
        for serial in item.get('SN', []):
            value = serial.strip()
            if _ISSN.match(value) and 'ISSN' not in record:
                record['ISSN'] = value
            elif _ISBN.match(value) and 'ISBN' not in record:
                record['ISBN'] = value
            elif 'ISSN' not in record and 'ISBN' not in record:
                record['ISBN' if kind in _BOOK_LIKE else 'ISSN'] = value
        if item.get('KW'):
            record['keyword'] = '; '.join(k for k in item['KW'] if k)
        if item.get('N1'):
            record['note'] = '\n'.join(n for n in item['N1'] if n)
        extra = {tag: values for tag, values in item.items() if tag not in _MAPPED}
        if extra:
            record['custom'] = {'ris': extra}
        result.append(record)
    if text.strip() and not result:
        _error('No RIS entries could be read.')
    return result


def _name(person_):
    if person_.get('literal'):
        return person_['literal'] + ','
    parts = [person_.get('family', ''), person_.get('given', '')]
    if person_.get('suffix'):
        parts.append(person_['suffix'])
    return ', '.join(p for p in parts if p)


def _clean(value):
    return ' '.join(str(value).split())


def dump(records):
    """RIS text for CSL-JSON ``records`` (each needs ``id`` and ``type``); only bibliographic fields."""
    out = []
    for record in records:
        kind = EXPORT_TYPES.get(record.get('type'), 'GEN')
        lines = [('TY', kind)]

        def add(tag, value, lines=lines):
            if value not in (None, ''):
                lines.append((tag, _clean(value)))

        add('ID', record.get('id'))
        add('TI', record.get('title'))
        for tag, field in (
            ('AU', 'author'),
            ('A2', 'editor'),
            ('A3', 'collection-editor'),
            ('TA', 'translator'),
        ):
            for name in record.get(field) or []:
                if isinstance(name, dict) and (text := _name(name)):
                    add(tag, text)
        parts = (record.get('issued') or {}).get('date-parts') or [[]]
        numbers = parts[0] if parts and parts[0] else []
        if numbers:
            add('PY', numbers[0])
            add(
                'DA',
                '/'.join([str(numbers[0])] + [f'{n:02d}' for n in numbers[1:]] + [''] * (3 - len(numbers)))
                + (f'/{record["issued"]["season"]}' if (record.get('issued') or {}).get('season') else ''),
            )
        accessed = ((record.get('accessed') or {}).get('date-parts') or [[]])[0]
        if accessed:
            add(
                'Y2',
                '/'.join(
                    [str(accessed[0])] + [f'{n:02d}' for n in accessed[1:]] + [''] * (3 - len(accessed))
                ),
            )
        container = record.get('container-title')
        if container:
            add('JO' if kind in ('JOUR', 'EJOUR', 'MGZN', 'NEWS') else 'T2', container)
        add('JA', record.get('container-title-short'))
        add('T3', record.get('collection-title'))
        add('VL', record.get('volume'))
        add('IS', record.get('issue'))
        pages = re.split(r'\s*[-–—]\s*', str(record.get('page') or ''), maxsplit=1)
        add('SP', pages[0])
        if len(pages) > 1:
            add('EP', pages[1])
        add('PB', record.get('publisher'))
        add('CY', record.get('publisher-place'))
        add('ET', record.get('edition'))
        add('LA', record.get('language'))
        add('SN', record.get('ISBN'))
        add('SN', record.get('ISSN'))
        add('DO', record.get('DOI'))
        add('UR', record.get('URL'))
        add('AB', record.get('abstract'))
        for keyword in str(record.get('keyword') or '').split(';'):
            add('KW', keyword)
        for note in str(record.get('note') or '').splitlines():
            add('N1', note)
        add('AN', record.get('number'))
        add('M3', record.get('genre'))
        for tag, values in ((record.get('custom') or {}).get('ris') or {}).items():
            if isinstance(tag, str) and len(tag) == 2 and tag not in _MAPPED and isinstance(values, list):
                for value in values:
                    add(tag, value)
        out.append('\r\n'.join(f'{tag}  - {value}' for tag, value in lines) + '\r\nER  - \r\n')
    return '\r\n'.join(out)
