"""Bibliography health (context engine plan 9, B1): is the reference list sound?

One report, :func:`health`, combines checks that need no network with checks that use outside
registries. The offline checks always run:

- DOI syntax (malformed, or copied with trailing punctuation);
- citations against the reference list: cited but not listed, listed but never cited (citation
  marks, plus ``(Author, Year)`` text that is not backed by a mark);
- citation density per section and the age distribution of the references;
- style-specific punctuation placement: the sentence period belongs after a parenthetical citation
  in author-date and numeric styles, and a note mark belongs after the punctuation in note styles;
- retractions, from an offline Retraction Watch CSV the person imported.

The registry checks run only when the caller passes a :class:`byax.citation_lookup.Lookup` and the
person has allowed those lookups (``citation_lookup`` consent), so they are never implicit:

- DOI resolution and metadata (title, author family names, year) against Crossref and DataCite, with
  OpenAlex as the fallback; Crossref's ``updated-by`` and ``relation`` notices and OpenAlex's
  ``is_retracted`` flag feed the retraction check;
- dead links in references, with an optional archived copy from the Wayback Machine.

A lookup that is empty never says "fake": a DOI no registry knows is reported as *unverified,
please check*. With ``Lookup(offline=True)`` the report is built from the cache only and says so.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

import regex
from rapidfuzz import fuzz

from .citation_lookup import SERVICES, RetractionIndex, doi_problem, normalize_doi
from .errors import ByaxError

ALL_CHECKS = ('doi', 'metadata', 'retractions', 'citations', 'density', 'age', 'punctuation', 'links')
OFFLINE_CHECKS = ('doi', 'retractions', 'citations', 'density', 'age', 'punctuation')
TITLE_MATCH = 92.0
TITLE_MINOR = 80.0
UNVERIFIED = 'Unverified, please check: this does not mean the reference is wrong.'
AGE_BUCKETS = (('0-2 years', 0, 2), ('3-5 years', 3, 5), ('6-10 years', 6, 10), ('11-20 years', 11, 20))
SEVERITIES = ('error', 'warning', 'info')
NO_CITATIONS_WORDS = 300

_NAME = r"\p{Lu}[\p{L}'’\-]+(?:\s+(?:van|von|de|der|den|la|le|di|da|del|bin)\s+\p{Lu}[\p{L}'’\-]+)?"
_AUTHORS = rf'(?P<names>{_NAME}(?:\s*(?:,|and|&)\s*{_NAME})*)(?P<etal>\s+et\s+al\.?)?'
_PAREN_PART = regex.compile(
    rf'^\s*(?:(?:see|cf\.?|e\.g\.,?|also)\s+)?{_AUTHORS},?\s+(?P<year>(?:1[5-9]|20)\d{{2}})[a-z]?'
    r'(?:\s*[,:]\s*(?:pp?\.\s*)?[\d\-–,\s]+)?\s*$'
)
_PAREN = regex.compile(r'\(([^()]{4,200})\)')
_NARRATIVE = regex.compile(rf'{_AUTHORS}\s*\((?P<year>(?:1[5-9]|20)\d{{2}})[a-z]?(?:[,:][^()]*)?\)')
_TERMINAL = '.?!'
_CLOSERS = '"\'”’)]'
_OPENERS_AFTER_NOTE = '”’"\')'


def _error(message, **details):
    raise ByaxError('invalid_input', message, **details)


def _fold(text):
    """Lower case, accent-free text with punctuation turned into spaces."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c)).casefold()
    return ' '.join(re.sub(r'[^\w]+', ' ', text).split())


def _csl_year(csl):
    parts = ((csl.get('issued') or {}).get('date-parts') or [[None]])[0] or [None]
    return parts[0] if isinstance(parts[0], int) else None


def _csl_families(csl):
    return [
        (person.get('family') or person.get('literal') or '').strip()
        for person in csl.get('author') or []
        if isinstance(person, dict)
    ]


def source_fields(source):
    csl = source.get('csl') if isinstance(source.get('csl'), dict) else {}
    title = csl.get('title') or source.get('title') or ''
    doi = csl.get('DOI') or source.get('doi') or ''
    url = csl.get('URL') or source.get('url') or ''
    return {
        'title': title if isinstance(title, str) else '',
        'doi': doi if isinstance(doi, str) else '',
        'url': url if isinstance(url, str) else '',
        'families': _csl_families(csl),
        'year': _csl_year(csl),
        'csl': csl,
    }


def _label(ident, fields):
    return fields['title'] or ident


# --- block walking -----------------------------------------------------------------------------


def _objects(doc):
    """Every block and table cell with text, in document order."""
    for block in doc.get('blocks', []):
        yield block
        for row in block.get('rows', []):
            yield from row.get('cells', [])


def _citation_marks(doc):
    for obj in _objects(doc):
        for mark in obj.get('marks', []):
            if mark.get('type') == 'citation':
                yield obj, mark


# --- metadata comparison -----------------------------------------------------------------------


def compare_metadata(fields, record):
    """Compare a source's title, author family names and year with a registry record.

    Each field is ``match``, ``minor`` (close, probably a spelling or online-first difference),
    ``mismatch`` or ``missing`` (one side has no value); the overall status is the worst of them.
    """
    result = {}
    ours, theirs = _fold(fields['title']), _fold(record.get('title') or '')
    if ours and theirs:
        score = max(fuzz.ratio(ours, theirs), fuzz.token_sort_ratio(ours, theirs))
        # A registry title often carries a subtitle ours lacks; accept an exact leading part.
        if theirs.startswith(ours + ' ') or ours.startswith(theirs + ' '):
            score = max(score, TITLE_MATCH)
        result['title'] = {
            'status': 'match' if score >= TITLE_MATCH else 'minor' if score >= TITLE_MINOR else 'mismatch',
            'score': round(score, 1),
            'ours': fields['title'],
            'registry': record.get('title'),
        }
    else:
        result['title'] = {'status': 'missing', 'ours': fields['title'], 'registry': record.get('title')}
    our_families = [_fold(f) for f in fields['families'] if f]
    their_families = [_fold(a.get('family') or '') for a in record.get('authors') or [] if a.get('family')]
    if our_families and their_families:
        first_ok = fuzz.ratio(our_families[0], their_families[0]) >= 85 or our_families[0] in their_families
        shared = sum(1 for f in our_families if any(fuzz.ratio(f, t) >= 85 for t in their_families))
        if not first_ok:
            status = 'mismatch'
        elif shared < min(len(our_families), len(their_families)):
            status = 'minor'
        elif len(our_families) != len(their_families):
            status = 'minor'  # a truncated or extended author list is common and worth a look, not an error
        else:
            status = 'match'
        result['authors'] = {
            'status': status,
            'ours': fields['families'],
            'registry': [a.get('family') for a in record.get('authors') or []],
        }
    else:
        result['authors'] = {'status': 'missing', 'ours': fields['families'], 'registry': None}
    ours_year, their_year = fields['year'], record.get('year')
    if ours_year and their_year:
        gap = abs(ours_year - their_year)
        result['year'] = {
            'status': 'match' if gap == 0 else 'minor' if gap == 1 else 'mismatch',
            'ours': ours_year,
            'registry': their_year,
        }
    else:
        result['year'] = {'status': 'missing', 'ours': ours_year, 'registry': their_year}
    states = [v['status'] for v in result.values()]
    overall = 'mismatch' if 'mismatch' in states else 'minor' if 'minor' in states else 'match'
    if all(s == 'missing' for s in states):
        overall = 'not_comparable'
    return {'status': overall, 'registry': record.get('registry'), 'fields': result}


def _metadata_message(comparison):
    parts = []
    for name, field in comparison['fields'].items():
        if field['status'] in ('mismatch', 'minor'):
            parts.append(f'{name}: yours {field["ours"]!r}, {comparison["registry"]} {field["registry"]!r}')
    return '; '.join(parts)


# --- retractions -------------------------------------------------------------------------------


def classify_notices(notices):
    """``retracted``, ``concern``, ``reinstated``, ``corrected`` or ``none`` from registry notices."""
    kinds = {}
    for notice in notices:
        kinds.setdefault(notice['type'], []).append(notice)
    retraction = [n for t in ('retraction', 'withdrawal', 'removal') for n in kinds.get(t, [])]
    reinstated = kinds.get('reinstatement', [])
    if retraction and reinstated:
        latest = max((n.get('date') or '' for n in retraction), default='')
        if max((n.get('date') or '' for n in reinstated), default='') >= latest and latest:
            return 'reinstated'
    if retraction:
        return 'retracted'
    if kinds.get('expression_of_concern') or kinds.get('expression-of-concern'):
        return 'concern'
    if reinstated:
        return 'reinstated'
    if kinds.get('correction'):
        return 'corrected'
    return 'none'


def _dedupe_notices(notices):
    seen, out = set(), []
    for notice in notices:
        key = (notice['type'], (notice.get('doi') or '').lower(), notice.get('date'))
        if key not in seen:
            seen.add(key)
            out.append(notice)
    return out


_RETRACTION_TEXT = {
    'retracted': (
        'error',
        'This work has been retracted. Check the notice before you cite it; a retracted source may be cited only as retracted.',
    ),
    'concern': ('warning', 'The publisher has issued an expression of concern about this work.'),
    'reinstated': ('info', 'A retraction of this work was later reinstated; read the notice.'),
    'corrected': ('info', 'A correction has been issued for this work.'),
}


# --- the citation list ---------------------------------------------------------------------------


def _textual_citations(doc, marked):
    """``(Author, Year)`` and ``Author (Year)`` text not covered by a citation mark."""
    found = []
    for obj in _objects(doc):
        text = obj.get('text') or ''
        if not any(ch.isdigit() for ch in text):
            continue
        covered = [(m['start'], m['end']) for m in obj.get('marks', []) if m.get('type') == 'citation']

        def free(start, end, covered=covered):
            return not any(s < end and start < e for s, e in covered)

        for paren in _PAREN.finditer(text):
            offset = paren.start(1)
            position = 0
            for part in paren.group(1).split(';'):
                match = _PAREN_PART.match(part)
                start = offset + position
                position += len(part) + 1
                if match and free(start, start + len(part)):
                    found.append(_textual(obj, match, start, start + len(part), text))
        for match in _NARRATIVE.finditer(text):
            if free(match.start(), match.end()):
                found.append(_textual(obj, match, match.start(), match.end(), text))
    return found


def _textual(obj, match, start, end, text):
    names = [n.strip() for n in re.split(r'\s*(?:,|and|&)\s*', match.group('names')) if n.strip()]
    return {
        'block_id': obj['id'],
        'start': start,
        'end': end,
        'text': text[start:end].strip(),
        'family': names[0].split()[-1] if names else '',
        'year': int(match.group('year')),
    }


def _reference_matches(references, family, year):
    wanted = _fold(family)
    for ident, source in references.items():
        fields = source_fields(source)
        if fields['year'] == year and fields['families'] and _fold(fields['families'][0]).endswith(wanted):
            return ident
    return None


def citation_list(doc):
    """Cited-but-not-listed and listed-but-never-cited, from citation marks and unmarked author-year text."""
    references = doc.get('references', {}) or {}
    cited, counts, unlisted_marks = set(), {}, []
    for obj, mark in _citation_marks(doc):
        ident = mark.get('value')
        counts[ident] = counts.get(ident, 0) + 1
        if ident in references:
            cited.add(ident)
        else:
            unlisted_marks.append(
                {
                    'block_id': obj['id'],
                    'source': ident,
                    'start': mark['start'],
                    'end': mark['end'],
                    'text': obj['text'][mark['start'] : mark['end']],
                }
            )
    textual, unlisted_text = _textual_citations(doc, cited), []
    for item in textual:
        match = _reference_matches(references, item['family'], item['year'])
        if match:
            cited.add(match)
            counts[match] = counts.get(match, 0) + 1
        else:
            unlisted_text.append(item)
    never = [
        {'source': ident, 'title': _label(ident, source_fields(source))}
        for ident, source in references.items()
        if ident not in cited
    ]
    return {
        'cited_not_listed': unlisted_marks,
        'possibly_not_listed': unlisted_text,
        'listed_not_cited': never,
        'cited_sources': len(cited),
        'listed_sources': len(references),
        'counts': counts,
    }


def density(doc):
    """Citations and words per section (a section runs from a heading to the next heading)."""
    sections = [{'block_id': None, 'heading': None, 'level': 0, 'words': 0, 'citations': 0}]
    marked_ranges = {}
    for obj, mark in _citation_marks(doc):
        marked_ranges.setdefault(obj['id'], []).append((mark['start'], mark['end']))
    for block in doc.get('blocks', []):
        if block.get('type') == 'heading':
            level = (block.get('attrs') or {}).get('level', 1)
            sections.append(
                {
                    'block_id': block['id'],
                    'heading': block.get('text', ''),
                    'level': level,
                    'words': 0,
                    'citations': 0,
                }
            )
            continue
        for obj in (block, *[c for row in block.get('rows', []) for c in row.get('cells', [])]):
            sections[-1]['words'] += len((obj.get('text') or '').split())
            sections[-1]['citations'] += len(marked_ranges.get(obj['id'], []))
    textual = _textual_citations(doc, set())
    owner = {}
    current = sections[0]
    for block in doc.get('blocks', []):
        if block.get('type') == 'heading':
            current = next(s for s in sections if s['block_id'] == block['id'])
        owner[block['id']] = current
        for row in block.get('rows', []):
            for cell in row.get('cells', []):
                owner[cell['id']] = current
    for item in textual:
        owner[item['block_id']]['citations'] += 1
    rows = [s for s in sections if s['block_id'] is not None or s['words'] or s['citations']]
    total = sum(s['citations'] for s in rows)
    for s in rows:
        s['per_1000_words'] = round(1000 * s['citations'] / s['words'], 1) if s['words'] else None
        s['flag'] = (
            'no_citations' if total and not s['citations'] and s['words'] >= NO_CITATIONS_WORDS else None
        )
    return {'sections': rows, 'citations': total, 'words': sum(s['words'] for s in rows)}


def age_distribution(references, cited, today):
    def distribution(idents):
        ages, unknown = [], 0
        for ident in idents:
            year = source_fields(references[ident])['year']
            if year is None:
                unknown += 1
            else:
                ages.append(max(0, today.year - year))
        buckets = [
            {'label': label, 'count': sum(1 for a in ages if low <= a <= high)}
            for label, low, high in AGE_BUCKETS
        ]
        buckets.append({'label': 'over 20 years', 'count': sum(1 for a in ages if a > 20)})
        buckets.append({'label': 'no year', 'count': unknown})
        ordered = sorted(ages)
        middle = len(ordered) // 2
        median = None
        if ordered:
            median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        return {
            'count': len(idents),
            'buckets': buckets,
            'median_age': median,
            'oldest_year': today.year - max(ages) if ages else None,
            'newest_year': today.year - min(ages) if ages else None,
            'older_than_10_years': sum(1 for a in ages if a > 10),
        }

    return {
        'listed': distribution(list(references)),
        'cited': distribution([i for i in references if i in cited]),
    }


# --- punctuation -------------------------------------------------------------------------------


def citation_format(settings):
    """``author-date``, ``numeric`` or ``note``: where this document's citation marker will sit."""
    presentation = settings.get('presentation', 'in-text')
    if presentation in ('footnotes', 'endnotes'):
        return 'note'
    if presentation == 'numbered':
        return 'numeric'
    try:
        from .csl_styles import style_format

        family = style_format(settings.get('style', 'apa'))
    except (ByaxError, OSError, ValueError):
        family = 'author-date'
    # A note style shown inline renders as a parenthetical, so the in-text rule applies.
    return 'numeric' if family == 'numeric' else 'author-date'


def punctuation_findings(doc):
    """Sentence punctuation against the citation marker, by the document's citation format."""
    settings = doc.get('settings', {}).get('citations', {}) or {}
    kind = citation_format(settings)
    out = []
    for obj, mark in _citation_marks(doc):
        text, end = obj.get('text') or '', mark['end']
        if not text or obj.get('style') == 'quotation':
            continue  # block quotations place the period before the citation in every style
        before = text[:end].rstrip(_CLOSERS)
        context = text[max(0, end - 24) : end + 12]
        if kind == 'note':
            following = text[end : end + 1]
            if following and (following in '.,;:!?' or following in _OPENERS_AFTER_NOTE):
                out.append(
                    {
                        'block_id': obj['id'],
                        'start': mark['start'],
                        'end': end,
                        'rule': 'note_after_punctuation',
                        'message': 'The note mark sits before the punctuation. In note styles it follows the punctuation (and a closing quotation mark).',
                        'context': context,
                    }
                )
        else:
            rest = text[end:].lstrip()
            # An abbreviation such as "et al." before a citation is not the sentence end.
            ends_sentence = not rest or rest[0].isupper() or rest[0].isdigit()
            if (
                before
                and before[-1] in _TERMINAL
                and text[end : end + 1] not in ('.', '?', '!')
                and ends_sentence
            ):
                out.append(
                    {
                        'block_id': obj['id'],
                        'start': mark['start'],
                        'end': end,
                        'rule': 'period_after_citation',
                        'message': 'The citation follows the sentence’s final punctuation. In author-date and numeric styles the period comes after the parenthetical citation.',
                        'context': context,
                    }
                )
    return {'format': kind, 'findings': out}


# --- the report ---------------------------------------------------------------------------------


def _finding(source, check, severity, status, message, **detail):
    return {
        'id': f'bib:{source or "document"}:{check}:{detail.get("block_id", "")}{detail.get("start", "")}',
        'source': source,
        'check': check,
        'severity': severity,
        'status': status,
        'message': message,
        'detail': {k: v for k, v in detail.items() if v is not None},
    }


def health(
    doc,
    *,
    lookup=None,
    checks=None,
    today=None,
    retractions=None,
    archive=False,
    progress=None,
    cancelled=None,
):
    """The bibliography health report for ``doc``.

    ``lookup`` is a :class:`byax.citation_lookup.Lookup` (online, or ``offline=True`` for the cache
    alone); without one only the offline checks run. ``checks`` selects from :data:`ALL_CHECKS`.
    ``archive`` also looks for archived copies of dead links. ``progress(done, total)`` and
    ``cancelled()`` support a background run. The result lists ``findings`` (most serious first),
    per-source ``sources`` and the document-level tables.
    """
    wanted = set(checks) if checks else set(ALL_CHECKS)
    unknown = wanted - set(ALL_CHECKS)
    if unknown:
        _error(
            'Unknown bibliography check', field='checks', supported=list(ALL_CHECKS), unknown=sorted(unknown)
        )
    today = today or date.today()
    references = doc.get('references', {}) or {}
    online = lookup is not None and not lookup.offline
    if online:
        needed = []
        if wanted & {'metadata', 'retractions', 'doi'} and any(
            source_fields(s)['doi'] for s in references.values()
        ):
            needed.append('crossref')
        if 'links' in wanted and any(source_fields(s)['url'] for s in references.values()):
            needed.append('links')
            if archive:
                needed.append('wayback')
        lookup.require(needed)
    index = retractions if retractions is not None else RetractionIndex()
    findings, sources = [], []
    resolved = {}
    total = len(references)
    for position, (ident, source) in enumerate(references.items()):
        if cancelled and cancelled():
            raise ByaxError('cancelled', 'Bibliography check was cancelled.')
        if progress:
            progress(position, total)
        fields = source_fields(source)
        entry = {
            'id': ident,
            'title': fields['title'],
            'doi': normalize_doi(fields['doi']),
            'url': fields['url'] or None,
            'checks': {},
        }
        sources.append(entry)
        doi = entry['doi']
        valid_doi = False
        if doi and 'doi' in wanted:
            problem = doi_problem(doi)
            if problem:
                entry['checks']['doi'] = 'invalid'
                findings.append(
                    _finding(
                        ident,
                        'doi',
                        'warning',
                        'invalid',
                        f'The DOI “{fields["doi"]}” is not valid. {problem["reason"]}',
                        suggestion=problem['suggestion'],
                    )
                )
            else:
                valid_doi = True
        elif doi:
            valid_doi = doi_problem(doi) is None
        record = None
        if doi and valid_doi and lookup is not None and wanted & {'doi', 'metadata', 'retractions'}:
            if doi not in resolved:
                resolved[doi] = lookup.registry_record(doi)
            result = resolved[doi]
            entry['registry'] = result['status']
            if result['status'] == 'found':
                record = result['record']
                entry['checks']['doi'] = 'resolved'
                entry['found_in'] = record['registry']
            elif result['status'] == 'unverified' and 'doi' in wanted:
                entry['checks']['doi'] = 'unverified'
                findings.append(
                    _finding(
                        ident,
                        'doi',
                        'info',
                        'unverified',
                        f'No registry we asked ({", ".join(result["tried"])}) knows the DOI {doi}. {UNVERIFIED}',
                    )
                )
            elif result['status'] == 'not_cached' and 'doi' in wanted:
                entry['checks']['doi'] = 'not_checked'
            elif result['status'] == 'error' and 'doi' in wanted:
                entry['checks']['doi'] = 'error'
                findings.append(
                    _finding(
                        ident,
                        'doi',
                        'info',
                        'error',
                        f'The DOI {doi} could not be checked just now (a registry did not answer). Try again later.',
                    )
                )
        if record and 'metadata' in wanted:
            comparison = compare_metadata(fields, record)
            entry['metadata'] = comparison
            entry['checks']['metadata'] = comparison['status']
            if comparison['status'] in ('mismatch', 'minor'):
                findings.append(
                    _finding(
                        ident,
                        'metadata',
                        'warning' if comparison['status'] == 'mismatch' else 'info',
                        comparison['status'],
                        f'The details differ from {SERVICES.get(record["registry"], (record["registry"],))[0]}: '
                        + _metadata_message(comparison)
                        + '. Check which is right.',
                        registry=record['registry'],
                    )
                )
        if 'retractions' in wanted and doi and valid_doi:
            notices = list(record['notices']) if record else []
            notices += index.lookup(doi)
            notices = _dedupe_notices(notices)
            status = classify_notices(notices)
            if record and record.get('title_marked_retracted') and status == 'none':
                status, notices = (
                    'retracted',
                    [
                        {
                            'type': 'retraction',
                            'label': 'Title marked as retracted',
                            'doi': None,
                            'date': None,
                            'source': record['registry'],
                        }
                    ],
                )
            if record and record.get('is_notice') and status == 'none':
                findings.append(
                    _finding(
                        ident,
                        'retractions',
                        'warning',
                        'is_notice',
                        'This reference is itself a retraction or correction notice. Cite the original work instead, or say it was retracted.',
                        notice_types=record['is_notice'],
                    )
                )
            if status != 'none':
                severity, message = _RETRACTION_TEXT[status]
                entry['checks']['retractions'] = status
                findings.append(_finding(ident, 'retractions', severity, status, message, notices=notices))
            elif record or index.available():
                entry['checks']['retractions'] = 'none_found'
            else:
                entry['checks']['retractions'] = 'not_checked'
        if 'links' in wanted and fields['url'] and lookup is not None:
            url = fields['url']
            link = lookup.check_url(url)
            entry['link'] = link
            entry['checks']['links'] = link['status']
            if link['status'] in ('dead', 'unverified'):
                severity = 'warning' if link['status'] == 'dead' else 'info'
                detail = {'url': url, 'http_status': link.get('http_status')}
                if archive and link['status'] == 'dead' and online:
                    copy_ = lookup.archived_copy(url)
                    entry['archived_copy'] = copy_
                    if copy_.get('available'):
                        detail['archived_copy'] = copy_['url']
                text = link.get('message') or 'The link could not be checked.'
                findings.append(
                    _finding(
                        ident,
                        'links',
                        severity,
                        link['status'],
                        f'{text} {UNVERIFIED if link["status"] == "unverified" else "The page may have moved."}'.strip(),
                        **detail,
                    )
                )
    if progress:
        progress(total, total)
    listing = citation_list(doc)
    report = {
        'schema_version': 1,
        'generated_for': today.isoformat(),
        'checks': sorted(wanted, key=ALL_CHECKS.index),
        'network': {
            'mode': 'online' if online else 'cache only' if lookup is not None else 'none',
            'requests': lookup.requests_made if lookup is not None else 0,
            'cache_hits': lookup.cache_hits if lookup is not None else 0,
        },
        'retraction_index': index.info(),
        'sources': sources,
    }
    if 'citations' in wanted:
        report['citation_list'] = listing
        for item in listing['cited_not_listed']:
            findings.append(
                _finding(
                    item['source'],
                    'citations',
                    'warning',
                    'cited_not_listed',
                    f'“{item["text"][:60]}” cites a source that is not in the reference list.',
                    block_id=item['block_id'],
                    start=item['start'],
                )
            )
        for item in listing['possibly_not_listed']:
            findings.append(
                _finding(
                    None,
                    'citations',
                    'info',
                    'possibly_not_listed',
                    f'“{item["text"]}” has no matching entry in the reference list.',
                    block_id=item['block_id'],
                    start=item['start'],
                )
            )
        for item in listing['listed_not_cited']:
            findings.append(
                _finding(
                    item['source'],
                    'citations',
                    'info',
                    'listed_not_cited',
                    f'“{item["title"][:80]}” is in the reference list but is never cited.',
                )
            )
    if 'density' in wanted:
        report['density'] = density(doc)
        for section in report['density']['sections']:
            if section['flag']:
                findings.append(
                    _finding(
                        None,
                        'density',
                        'info',
                        'no_citations',
                        f'The section “{section["heading"] or "(before the first heading)"}” has {section["words"]} words and no citations.',
                        block_id=section['block_id'],
                    )
                )
    if 'age' in wanted:
        report['age'] = age_distribution(references, set(listing['counts']) & set(references), today)
    if 'punctuation' in wanted:
        report['punctuation'] = punctuation_findings(doc)
        for item in report['punctuation']['findings']:
            findings.append(
                _finding(
                    None,
                    'punctuation',
                    'info',
                    item['rule'],
                    item['message'],
                    block_id=item['block_id'],
                    start=item['start'],
                    context=item['context'],
                )
            )
    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order[f['severity']], f['check'], f['source'] or '', f['id']))
    report['findings'] = findings
    report['summary'] = summarize(report)
    return report


def summarize(report):
    findings = report['findings']
    by_severity = {s: sum(1 for f in findings if f['severity'] == s) for s in SEVERITIES}
    by_status = {}
    for finding in findings:
        key = f'{finding["check"]}:{finding["status"]}'
        by_status[key] = by_status.get(key, 0) + 1
    states = [v for s in report['sources'] for v in s['checks'].values()]
    return {
        'sources': len(report['sources']),
        'findings': len(findings),
        'by_severity': by_severity,
        'by_status': dict(sorted(by_status.items())),
        'retracted': by_status.get('retractions:retracted', 0),
        'unverified': by_status.get('doi:unverified', 0),
        'not_checked': sum(1 for s in states if s == 'not_checked'),
    }


def describe(report):
    """A short text summary, for the CLI's human output and the panel's header."""
    summary = report['summary']
    counts = summary['by_severity']
    head = f'{summary["sources"]} sources, {summary["findings"]} findings '
    head += f'({counts["error"]} serious, {counts["warning"]} to review, {counts["info"]} for information).'
    lines = [head]
    if report['network']['mode'] != 'online':
        lines.append(
            'Checked on this computer only'
            + (
                ' (plus earlier answers kept in the cache).'
                if report['network']['mode'] == 'cache only'
                else '.'
            )
            + ' Online checks run only when you ask.'
        )
    if summary['unverified']:
        lines.append(
            f'{summary["unverified"]} DOIs are unverified: please check them. That does not mean they are wrong.'
        )
    return lines
