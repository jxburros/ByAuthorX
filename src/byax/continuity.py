"""Deterministic continuity checks against the temporal codex (context engine plan, section 10).

The ``continuity`` suite compares simple attribute statements in the text with the codex facts in
force where they occur (:func:`byax.codex.facts_at`): a character's eye colour, hair colour, hair
style and age. Patterns, for a codex mention ``<Name>`` (names and aliases, via ``codex.scan``):

- ``<Name>'s <noun> was|were|is|are|looked … <value>`` ("Elizabeth's eyes were blue");
- ``<Name>'s <value> <noun>`` ("Jane's fair hair");
- ``<Name> had|has|with <value> <noun>`` ("Kitty had curly hair");
- ``<Name> was|is <age> years old`` and ``<Name>, aged <age>``.

Lexicons (colours, hair colours and styles, number words, synonyms) are data in
``resources/continuity/<language>.json``. A trait takes part when its name is one of an
attribute's trait names and its value contains a lexicon term. Rules:

- ``continuity.trait_mismatch`` (warning): the statement contradicts every fact in force;
- ``continuity.series_mismatch`` (warning): the same, for a series-level fact shared across books;
- ``continuity.before_established`` (advisory): the statement matches a fact that only becomes
  valid later ("used before it is established");
- ``continuity.expired_fact`` (advisory): it matches a fact that no longer holds while another
  fact is in force.

The suite is advisory, off by default (not in the default suites), never changes text, and reports
exact ranges (the stated value). Hidden codex entries take part: this is a local check.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

from . import codex
from .model import object_hash

FAMILY = 'continuity'
VERSION = 1
RULES = {
    'continuity.trait_mismatch': ('continuity', FAMILY, 'conservative', None, 'warning'),
    'continuity.series_mismatch': ('continuity', FAMILY, 'conservative', None, 'warning'),
    'continuity.before_established': ('continuity', FAMILY, 'conservative', None, 'advisory'),
    'continuity.expired_fact': ('continuity', FAMILY, 'balanced', None, 'advisory'),
}
UNMEASURED = (
    'continuity.statements',
    (
        'Only simple attribute statements (eye colour, hair colour and style, age) are compared with codex '
        'facts; other contradictions need a reader or a configured reviewer.'
    ),
)
UNIT = 'context:continuity'
_RESOURCES = Path(__file__).resolve().parent / 'resources' / 'continuity'
_WORD = r"[^\W\d_]+(?:[-'’][^\W\d_]+)*"


@functools.lru_cache(maxsize=8)
def lexicon(language: str = 'en') -> dict[str, Any]:
    """The continuity lexicon for a language, falling back to English."""
    path = _RESOURCES / f'{language}.json'
    if not path.is_file():
        path = _RESOURCES / 'en.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['_terms'] = {
        name: {data['synonyms'].get(w, w) for key in spec['lexicons'] for w in data['lexicons'][key]}
        for name, spec in data['attributes'].items()
    }
    data['_traits'] = {t: name for name, spec in data['attributes'].items() for t in spec['traits']}
    return data


def _norm(word: str, data: dict[str, Any]) -> str:
    word = word.casefold().replace('’', "'")
    return data['synonyms'].get(word, word)


def _age(text: str, data: dict[str, Any]) -> int | None:
    text = text.casefold().strip()
    if text.isdigit():
        return int(text)
    total = 0
    for part in re.split(r'[-\s]+', text):
        if part not in data['numbers']:
            return None
        total += data['numbers'][part]
    return total or None


def trait_terms(trait_name: str, value: str, data: dict[str, Any]) -> tuple[str, set[Any]] | None:
    """(attribute, terms) a fact asserts, or None when the fact is not checkable."""
    attribute = data['_traits'].get(' '.join(trait_name.casefold().split()))
    if attribute is None:
        return None
    if attribute == 'age':
        found = re.search(r'\d{1,3}|' + _WORD, value)
        age = _age(found.group(0), data) if found else None
        return ('age', {age}) if age is not None else None
    terms = {_norm(w, data) for w in re.findall(_WORD, value)} & data['_terms'][attribute]
    return (attribute, terms) if terms else None


def consistent(stated: set[Any], fact: set[Any], data: dict[str, Any]) -> bool:
    """A stated value agrees with a fact when they share a term or one is a broad term covering the other."""
    broad = data.get('broad', {})
    return any(s == f or s in broad.get(f, ()) or f in broad.get(s, ()) for s in stated for f in fact)


# --- statements in text ------------------------------------------------------------------------


def _attribute_of(noun: str, terms: set[str], data: dict[str, Any]) -> str | None:
    for name, spec in data['attributes'].items():
        if noun in spec['nouns'] and terms & data['_terms'][name]:
            return name
    return None


def _value_run(text, start, data, wanted):
    """Lexicon words from ``start``: (first, last, terms) over a run of lexicon/modifier words."""
    first = last = None
    terms: set[str] = set()
    for match in re.finditer(_WORD + r'|[^\s]', text[start : start + 80]):
        word = _norm(match.group(0), data)
        if word in wanted:
            terms.add(word)
            first = start + match.start() if first is None else first
            last = start + match.end()
        elif word in data['modifiers'] or word in ('and', 'or') or word == ',':
            if first is None and word in data['modifiers']:
                first = start + match.start()
            continue
        else:
            break
    return (first, last, terms) if terms else None


@functools.lru_cache(maxsize=8)
def _patterns(language: str) -> tuple[Any, Any, Any, frozenset[str]]:
    data = lexicon(language)
    nouns = sorted({n for spec in data['attributes'].values() for n in spec['nouns']}, key=len, reverse=True)
    noun = '(?P<noun>' + '|'.join(map(re.escape, nouns)) + ')'
    verbs = '(?:' + '|'.join(re.escape(v).replace(r'\ ', r'\s+') for v in data['verbs']) + ')'
    having = '(?:' + '|'.join(map(re.escape, data['possessive_verbs'])) + ')'
    numbers = '|'.join(sorted(data['numbers'], key=len, reverse=True))
    age = r'\d{1,3}|(?:' + numbers + r')(?:[-\s](?:' + numbers + r'))?'
    return (
        re.compile(rf"(?:'s|’s)\s+{noun}\s+{verbs}\s+", re.IGNORECASE),
        re.compile(rf"(?:(?:'s|’s)|\s+{having})\s+(?P<run>(?:{_WORD}[\s,]+){{1,4}}?){noun}\b", re.IGNORECASE),
        re.compile(
            rf'(?:\s+(?:was|is)\s+(?P<age>{age})(?:\s+years?\s+old|-years?-old)|,\s+aged\s+(?P<aged>{age})\b)',
            re.IGNORECASE,
        ),
        frozenset().union(*data['_terms'].values()),
    )


def statements(text: str, mentions: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    """Attribute statements about mentioned entries: ``{entry, attribute, terms, start, end}``."""
    after_noun, before_noun, stated_age, every = _patterns(data['language'])
    result = []
    for mention in mentions:
        if mention.get('rejected') or mention.get('ambiguous'):
            continue
        end = mention['end']
        found = after_noun.match(text, end)
        if found:
            run = _value_run(text, found.end(), data, every)
            if run:
                attribute = _attribute_of(found.group('noun').casefold(), run[2], data)
                if attribute:
                    terms = run[2] & data['_terms'][attribute]
                    result.append(_statement(mention, attribute, terms, run[0], run[1]))
                    continue
        found = before_noun.match(text, end)
        if found:
            words = [
                (m.start() + found.start('run'), m.end() + found.start('run'), _norm(m.group(0), data))
                for m in re.finditer(_WORD, found.group('run'))
            ]
            terms = {w for _, _, w in words if w in every}
            attribute = _attribute_of(found.group('noun').casefold(), terms, data)
            if attribute:
                inside = [(a, b) for a, b, w in words if w in data['_terms'][attribute]]
                result.append(
                    _statement(
                        mention, attribute, terms & data['_terms'][attribute], inside[0][0], inside[-1][1]
                    )
                )
                continue
        found = stated_age.match(text, end)
        if found:
            group = 'age' if found.group('age') else 'aged'
            value = found.group(group)
            years = _age(value, data)
            if years is not None:
                a, b = found.span(group)
                result.append(_statement(mention, 'age', {years}, a, b))
    return result


def _statement(mention, attribute, terms, start, end):
    return {
        'entry': mention['entry'],
        'surface': mention.get('surface'),
        'attribute': attribute,
        'terms': terms,
        'start': start,
        'end': end,
    }


# --- evaluation ---------------------------------------------------------------------------------


def series_context(doc: dict[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    """The series data and book number a :func:`check_view` attached, if any."""
    info: dict[str, Any] = doc['_continuity'] if isinstance(doc.get('_continuity'), dict) else {}
    return info.get('series'), info.get('book')


def check_view(doc: dict[str, Any], document_path: Any) -> dict[str, Any]:
    """A read-only view carrying the document's series and its book number for the suite."""
    from . import series as series_module

    data = series_module.series_of(doc, document_path)
    if data is None:
        return doc
    book = None
    info = series_module.resolve(doc, document_path)
    if info and info.get('resolved') and document_path is not None:
        members = series_module.documents(info['resolved'], data)
        here = Path(document_path).resolve()
        if here in members:
            book = members.index(here) + 1
    return dict(doc, _continuity={'series': data, 'book': book})


def _label(doc, value):
    if value is None:
        return 'the start'
    if value.startswith('book:'):
        return 'book ' + value[5:]
    index = codex.resolve_position(doc, value)
    if index is None:
        return value
    block = doc['blocks'][index]
    return block.get('text', '')[:60] or value


def _facts(doc, data, series, book):
    """``{entry: [(attribute, terms, trait row, start bound, end bound)]}`` for checkable traits."""
    result: dict[str, list[tuple[str, set[Any], dict[str, Any]]]] = {}
    for key, entry in codex.effective_entries(doc, series).items():
        for trait in entry.get('traits', []):
            parsed = trait_terms(trait['name'], trait['value'], data)
            if parsed is None:
                continue
            row = {
                'entry': key,
                'name': entry['name'],
                'trait': trait['name'],
                'trait_id': trait['id'],
                'value': trait['value'],
                'scope': entry.get('scope', 'document'),
                **{k: trait[k] for k in codex.TRAIT_VALIDITY if k in trait},
            }
            result.setdefault(key, []).append((parsed[0], parsed[1], row))
    return result


def fingerprint(ctx: Any) -> str:
    doc = ctx.doc
    series, book = series_context(doc)
    return object_hash(
        {
            'v': VERSION,
            'env': ctx.env,
            'blocks': [
                (b['id'], b.get('type'), b.get('text', ''), b.get('attrs', {}).get('level'))
                for b in doc.get('blocks', [])
            ],
            'cells': [(o, t) for o, _, t in codex.text_objects(doc)],
            'codex': codex.effective_entries(doc, series),
            'project': doc.get('project'),
            'book': book,
        }
    )


def analyze(
    doc: dict[str, Any], *, series: Any = None, book: int | None = None, language: str | None = None
) -> list[dict[str, Any]]:
    """Continuity findings as plain dicts (``rule, target, start, end, text, message, measured``)."""
    data = lexicon((language or doc.get('metadata', {}).get('language') or 'en').split('-')[0])
    facts = _facts(doc, data, series, book)
    if not facts:
        return []
    entries = codex.effective_entries(doc, series)
    order = codex._block_order(doc)
    findings = []
    for object_id, _, text in codex.text_objects(doc):
        index = order[object_id]
        for stated in statements(text, codex.scan(text, entries), data):
            known = [f for f in facts.get(stated['entry'], []) if f[0] == stated['attribute']]
            if not known:
                continue
            by_status: dict[str, list[tuple[bool, dict[str, Any]]]] = {}
            for _, terms, row in known:
                status = codex._status(doc, row, index, book)
                by_status.setdefault(status, []).append((consistent(stated['terms'], terms, data), row))
            in_force = by_status.get('in_force', [])
            if any(ok for ok, _ in in_force):
                continue
            later = [row for ok, row in by_status.get('not_yet', []) if ok]
            earlier = [row for ok, row in by_status.get('expired', []) if ok]
            value = text[stated['start'] : stated['end']]
            name = entries[stated['entry']]['name']
            measured = {
                'entry': stated['entry'],
                'attribute': stated['attribute'],
                'stated': sorted(map(str, stated['terms'])),
                'facts_in_force': [row['value'] for _, row in in_force],
                **({'book': book} if book is not None else {}),
            }
            if later:
                row = later[0]
                rule = 'continuity.before_established'
                message = (
                    f'“{value}” for {name} ({row["trait"]}) is established only from '
                    f'{_label(doc, row.get("valid_from"))}; here it is used before that.'
                )
                measured['established_at'] = row.get('valid_from')
            elif not in_force:
                continue
            elif earlier:
                row = earlier[0]
                rule = 'continuity.expired_fact'
                message = (
                    f'“{value}” for {name} ({row["trait"]}) held only until {_label(doc, row.get("valid_to"))}; '
                    f'here the codex says “{in_force[0][1]["value"]}”.'
                )
                measured['expired_at'] = row.get('valid_to')
            else:
                row = in_force[0][1]
                series_only = all(r['scope'] == 'series' for _, r in in_force)
                rule = 'continuity.series_mismatch' if series_only else 'continuity.trait_mismatch'
                where = 'the series codex' if series_only else 'the codex'
                since = f' (since {_label(doc, row.get("valid_from"))})' if row.get('valid_from') else ''
                message = f'“{value}” contradicts {where} for {name}: {row["trait"]} “{row["value"]}”{since}.'
            measured['fact'] = {
                k: row[k]
                for k in ('trait', 'trait_id', 'value', 'scope', 'valid_from', 'valid_to')
                if k in row
            }
            findings.append(
                {
                    'rule': rule,
                    'target': object_id,
                    'start': stated['start'],
                    'end': stated['end'],
                    'text': value,
                    'message': message,
                    'measured': measured,
                }
            )
    return findings


def evaluate(ctx: Any, sink: Any) -> None:
    """The ``context:continuity`` unit: whole-manuscript findings for the checks pipeline."""
    series, book = series_context(ctx.doc)
    for finding in analyze(ctx.doc, series=series, book=book):
        sink.add(
            finding['rule'],
            FAMILY,
            RULES[finding['rule']][4],
            finding['message'],
            finding['target'],
            finding['start'],
            finding['end'],
            finding['text'],
            [],
            measured=finding['measured'],
        )
