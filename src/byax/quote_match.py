"""Quote normalization, alignment and three-selector anchors (context engine plan 5.3, 6.5).

A quotation copied from a PDF rarely matches the extracted text byte for byte: the extractor
breaks lines mid-word, expands ligatures and keeps soft hyphens, while the writer's keyboard
produces curly quotes. ``locate`` therefore tries three levels, and reports which one passed so
a result can say "exact" or "only after normalization" and never presents a fuzzy alignment as
a verified quote.
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# partial_ratio threshold (0-100) for a `near` match. A provisional value: the Phase 0 harness
# (context engine plan, section 4) records the genuine-quote pass rate and the false-positive
# rate for altered quotes, and this constant is tuned from those numbers, not by taste.
NEAR_THRESHOLD = 90.0
# Quotes shorter than this (after normalization) are only ever matched exactly: one changed
# character in a short quote is a different quote, not a near miss.
MIN_NEAR_CHARS = 12
PREFIX_SUFFIX_CHARS = 32
# Search window on each side of the old position for the first fuzzy re-anchoring pass.
NEARBY_WINDOW = 2000
MAX_DIFFERENCES = 20

_QUOTES = {
    '\u2018': "'",
    '\u2019': "'",
    '\u201a': "'",
    '\u201b': "'",
    '\u2032': "'",
    '`': "'",
    '\u00b4': "'",
    '\u201c': '"',
    '\u201d': '"',
    '\u201e': '"',
    '\u201f': '"',
    '\u2033': '"',
    '\u00ab': '"',
    '\u00bb': '"',
}
# Hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal bar, minus sign.
_DASHES = dict.fromkeys('\u2010\u2011\u2012\u2013\u2014\u2015\u2212', '-')
_LIGATURES = {
    '\ufb00': 'ff',
    '\ufb01': 'fi',
    '\ufb02': 'fl',
    '\ufb03': 'ffi',
    '\ufb04': 'ffl',
    '\ufb05': 'st',
    '\ufb06': 'st',
}
# Soft hyphen and invisible characters that extractors leave behind.
_DROPPED = frozenset('\u00ad\u200b\u2060\ufeff')
_LINE_BREAKS = frozenset('\n\r\u2028\u2029\x0b\x0c')
_HYPHENS = frozenset('-\u2010\u2011')

Token = tuple[str, int, int]


def _clusters(text):
    """NFC per base-plus-combining-marks cluster, each output character mapped to its cluster."""
    tokens: list[Token] = []
    start = 0
    for index in range(1, len(text) + 1):
        if index == len(text) or not unicodedata.combining(text[index]):
            composed = unicodedata.normalize('NFC', text[start:index])
            tokens.extend((char, start, index) for char in composed)
            start = index
    return tokens


def _expand(tokens):
    out: list[Token] = []
    for char, start, end in tokens:
        if char in _DROPPED:
            continue
        out.extend((c, start, end) for c in _LIGATURES.get(char, char))
    return out


def _join_hyphenation(tokens, join):
    """``exam-\\nple`` becomes ``example`` (or ``exam-ple`` when join is False).

    Only a letter, a hyphen, a line break and a lowercase letter qualify, so a dash before a
    capital or a digit (``pre-\\nWar``, ``1990-\\n2000``) is left alone.
    """
    out: list[Token] = []
    index = 0
    while index < len(tokens):
        char = tokens[index][0]
        if char in _HYPHENS and out and out[-1][0].isalpha():
            probe = index + 1
            while probe < len(tokens) and tokens[probe][0] in ' \t\u00a0':
                probe += 1
            broke = False
            while probe < len(tokens) and (tokens[probe][0] in _LINE_BREAKS or tokens[probe][0] in ' \t'):
                broke = broke or tokens[probe][0] in _LINE_BREAKS
                probe += 1
            if broke and probe < len(tokens) and tokens[probe][0].islower():
                if not join:
                    out.append(('-', tokens[index][1], tokens[index][2]))
                index = probe
                continue
        out.append(tokens[index])
        index += 1
    return out


def _fold(tokens):
    return [(_DASHES.get(c, _QUOTES.get(c, c)), s, e) for c, s, e in tokens]


_ELISION = re.compile(r'\[\s*(?:\.{3}|\u2026|\.\s\.\s\.)\s*\]|\.\s\.\s\.|\.{3,}|\u2026+')


def _ellipses(tokens):
    """``...``, ``. . .``, ``[...]`` and ``[…]`` all become one ``…`` (a trailing fourth dot stays)."""
    text = ''.join(c for c, _, _ in tokens)
    out: list[Token] = []
    cursor = 0
    for match in _ELISION.finditer(text):
        out.extend(tokens[cursor : match.start()])
        span_start, span_end = tokens[match.start()][1], tokens[match.end() - 1][2]
        out.append(('\u2026', span_start, span_end))
        if match.group().startswith('....') and set(match.group()) == {'.'} and len(match.group()) == 4:
            out.append(('.', tokens[match.end() - 1][1], span_end))
        cursor = match.end()
    out.extend(tokens[cursor:])
    return out


def _collapse(tokens):
    out: list[Token] = []
    for char, start, end in tokens:
        if char.isspace():
            if out and out[-1][0] == ' ':
                out[-1] = (' ', out[-1][1], end)
            else:
                out.append((' ', start, end))
        else:
            out.append((char, start, end))
    return out


def normalize(text, *, join_hyphenation=True):
    """Return ``(normalized, offset_map)``; ``offset_map[i]`` is ``(start, end)`` in ``text``.

    Steps: NFC, soft hyphen and zero-width removal, ligature expansion, line-break hyphenation
    join, curly quotes and apostrophes to straight, dash folding, ellipsis normalization and
    whitespace collapse. Case is kept, and leading or trailing whitespace becomes one space.
    Every normalized character maps to the slice of the original it came from, so a span found
    in the normalized text maps back with ``map_span``.
    """
    tokens = _expand(_clusters(text))
    tokens = _join_hyphenation(tokens, join_hyphenation)
    tokens = _collapse(_ellipses(_fold(tokens)))
    return ''.join(c for c, _, _ in tokens), [(s, e) for _, s, e in tokens]


def map_span(offset_map, start, end):
    """Original ``(start, end)`` for a non-empty normalized span."""
    return offset_map[start][0], offset_map[end - 1][1]


def _differences(quote, source):
    """Edit operations that turn the quote into the aligned source text (``equal`` runs omitted)."""
    from rapidfuzz.distance import Levenshtein

    result = [
        {
            'op': op.tag,
            'quote': quote[op.src_start : op.src_end],
            'source': source[op.dest_start : op.dest_end],
            'quote_offset': op.src_start,
            'source_offset': op.dest_start,
        }
        for op in Levenshtein.opcodes(quote, source)
        if op.tag != 'equal'
    ]
    return result[:MAX_DIFFERENCES]


def _refine(quote, source, start, end):
    """Nudge the RapidFuzz alignment's edges (a few characters) to the closest edit distance.

    ``partial_ratio`` can end a character early or late. Start and end are tuned in turn, and a
    tie keeps the edge nearest the original alignment, so the reported differences describe the
    quote and not stray context.
    """
    from rapidfuzz.distance import Levenshtein

    reach = min(8, max(2, len(quote) // 10))

    def best(candidates, cost, original):
        return min(candidates, key=lambda c: (cost(c), abs(c - original)))

    starts = range(max(0, start - reach), min(end, start + reach + 1))
    start = best(starts, lambda c: Levenshtein.distance(quote, source[c:end]), start)
    ends = range(max(start + 1, end - reach), min(len(source), end + reach) + 1)
    end = best(ends, lambda c: Levenshtein.distance(quote, source[start:c]), end)
    return start, end


def describe_differences(differences):
    """One line for a finding: what the quote says versus what the source says."""
    parts = []
    for item in differences:
        if item['op'] == 'insert':
            parts.append(f'source adds {item["source"]!r}')
        elif item['op'] == 'delete':
            parts.append(f'quote adds {item["quote"]!r}')
        else:
            parts.append(f'quote {item["quote"]!r} vs source {item["source"]!r}')
    return '; '.join(parts)


def _result(match, span=None, score=0.0, **extra):
    return {
        'match': match,
        'span': list(span) if span else None,
        'score': score,
        'raw_pass': match == 'exact',
        'normalized_pass': match in ('exact', 'normalized'),
        **extra,
    }


_NONE = _result('none')


def _normalized_find(quote, source, hyphenation):
    nq, _ = normalize(quote, join_hyphenation=hyphenation)
    nq = nq.strip()
    if not nq:
        return None
    ns, mapping = normalize(source, join_hyphenation=hyphenation)
    at = ns.find(nq)
    if at >= 0:
        return {'span': map_span(mapping, at, at + len(nq))}
    # An elided quote ("first part ... last part") matches when each segment follows the last.
    if '\u2026' in nq:
        segments = [part.strip() for part in nq.split('\u2026') if part.strip()]
        cursor, first, last = 0, None, 0
        for segment in segments:
            at = ns.find(segment, cursor)
            if at < 0:
                return None
            first = at if first is None else first
            cursor = last = at + len(segment)
        if first is not None:
            return {'span': map_span(mapping, first, last), 'elided': True}
    return None


def _near(quote, source, threshold):
    from rapidfuzz import fuzz

    nq, _ = normalize(quote)
    nq = nq.strip()
    ns, mapping = normalize(source)
    if len(nq) < MIN_NEAR_CHARS or not ns:
        return None
    if len(nq) > len(ns):
        score = fuzz.ratio(nq, ns)
        start, end = 0, len(ns)
    else:
        alignment = fuzz.partial_ratio_alignment(nq, ns, score_cutoff=threshold)
        if alignment is None:
            return None
        score, start, end = alignment.score, alignment.dest_start, alignment.dest_end
    if score < threshold or end <= start:
        return None
    start, end = _refine(nq, ns, start, end)
    return {
        'span': map_span(mapping, start, end),
        'score': round(score, 2),
        'differences': _differences(nq, ns[start:end]),
    }


def locate(quote, source_text, threshold=None):
    """Find ``quote`` in ``source_text``: exact, normalized, near (fuzzy) or none.

    The result always has ``match`` (``exact`` | ``normalized`` | ``near`` | ``none``), ``span``
    (``[start, end]`` in ``source_text`` or None), ``score`` (100 for a literal or normalized
    match, the RapidFuzz ``partial_ratio`` for ``near``), ``raw_pass`` and ``normalized_pass``.
    A ``near`` result also lists ``differences`` and is advisory: callers must not treat it as a
    verified quotation. ``threshold`` defaults to ``NEAR_THRESHOLD``.
    """
    if not isinstance(quote, str) or not isinstance(source_text, str) or not quote.strip():
        return dict(_NONE)
    at = source_text.find(quote)
    if at >= 0:
        return _result('exact', (at, at + len(quote)), 100.0)
    for hyphenation in (True, False):
        found = _normalized_find(quote, source_text, hyphenation)
        if found:
            return _result(
                'normalized',
                found['span'],
                100.0,
                **({'elided': True} if found.get('elided') else {}),
            )
    found = _near(quote, source_text, NEAR_THRESHOLD if threshold is None else threshold)
    if found:
        return _result('near', found['span'], found['score'], differences=found['differences'])
    return dict(_NONE)


def align(text, source_text, threshold=0.0):
    """Best fuzzy alignment of ``text`` (a claim sentence, not a quotation) inside ``source_text``.

    Returns ``{'span', 'score'}`` with the span in ``source_text`` coordinates, or None when the
    score is below ``threshold`` (0-100) or either side is empty. Used to recover the source span a
    ``[[cite:ID]]`` token points at; the score is lexical overlap, never evidence of support.
    """
    from rapidfuzz import fuzz

    if not isinstance(text, str) or not isinstance(source_text, str):
        return None

    def fold(value):
        # Length-preserving lower case, so offsets into the normalized text stay valid.
        return ''.join(c.lower() if len(c.lower()) == 1 else c for c in value)

    nt = fold(normalize(text)[0].strip())
    ns, mapping = normalize(source_text)
    if not nt or not ns.strip():
        return None
    alignment = fuzz.partial_ratio_alignment(nt, fold(ns))
    if alignment is None or alignment.score < threshold or alignment.dest_end <= alignment.dest_start:
        return None
    start, end = alignment.dest_start, alignment.dest_end
    while start < end and ns[start] == ' ':
        start += 1
    while end > start and ns[end - 1] == ' ':
        end -= 1
    if end <= start:
        return None
    return {'span': list(map_span(mapping, start, end)), 'score': round(alignment.score, 2)}


_QUOTED = re.compile(r'\s*["\u201c\u201e\u00ab](.+?)["\u201d\u201f\u00bb]\s*', re.DOTALL)


def quoted_passage(text):
    """The text inside a pair of quotation marks that encloses all of ``text``, else None."""
    match = _QUOTED.fullmatch(text or '')
    return match.group(1) if match else None


def infer_kind(selected, source_texts, threshold=None):
    """``quotation`` when the selection is quoted or found in a source, else ``paraphrase``.

    ``near`` matches do not count: a close paraphrase is not a quotation. Returns
    ``{'kind', 'reason'}`` so a picker can say why it preselected the kind.
    """
    if quoted_passage(selected) is not None:
        return {'kind': 'quotation', 'reason': 'The selected text is in quotation marks.'}
    candidate = (selected or '').strip()
    if len(normalize(candidate)[0].strip()) >= MIN_NEAR_CHARS:
        for source in source_texts:
            if locate(candidate, source, threshold)['normalized_pass']:
                return {'kind': 'quotation', 'reason': 'The selected text appears verbatim in the source.'}
    return {'kind': 'paraphrase', 'reason': 'The selected text is not quoted and is not found in the source.'}


class Reanchor(NamedTuple):
    start: int
    end: int
    method: str  # 'position' | 'nearby' | 'anywhere'
    score: float

    @property
    def span(self):
        return (self.start, self.end)


def selectors(source_text, span):
    """The three-selector anchor for ``span``: position, quote, and 32 characters either side."""
    start, end = span
    if not 0 <= start < end <= len(source_text):
        raise ValueError('Anchor span must be a non-empty range inside the text')
    return {
        'position': {'start': start, 'end': end},
        'quote': source_text[start:end],
        'prefix': source_text[max(0, start - PREFIX_SUFFIX_CHARS) : start],
        'suffix': source_text[end : end + PREFIX_SUFFIX_CHARS],
    }


def _common_suffix(a, b):
    count = 0
    while count < min(len(a), len(b)) and a[-1 - count] == b[-1 - count]:
        count += 1
    return count


def _common_prefix(a, b):
    count = 0
    while count < min(len(a), len(b)) and a[count] == b[count]:
        count += 1
    return count


def _best_occurrence(anchor, text, base, threshold):
    """Best span of the quote in ``text`` (offset ``base`` in the full text), using prefix and suffix."""
    quote = anchor['quote']
    old = anchor['position']['start']
    occurrences, at = [], text.find(quote)
    while at >= 0 and len(occurrences) < 200:
        occurrences.append((at, at + len(quote), 100.0))
        at = text.find(quote, at + 1)
    if not occurrences:
        found = locate(quote, text, threshold)
        if found['match'] == 'none':
            return None
        occurrences = [(*found['span'], found['score'])]

    def rank(item):
        start, end, _ = item
        context = _common_suffix(text[max(0, start - PREFIX_SUFFIX_CHARS) : start], anchor.get('prefix', ''))
        context += _common_prefix(text[end : end + PREFIX_SUFFIX_CHARS], anchor.get('suffix', ''))
        return (-context, abs(base + start - old))

    start, end, score = min(occurrences, key=rank)
    return Reanchor(base + start, base + end, '', score)


def reanchor(anchor, new_text, threshold=None):
    """Re-find an anchored quote after its source changed; None when every step fails.

    Order (context engine plan 6.5, after the caller has tried block and page structure):
    the recorded position, fuzzy search near the old position, then fuzzy search anywhere.
    Among several equal candidates the prefix and suffix decide, then distance to the old position.
    """
    quote = anchor.get('quote') or ''
    if not quote:
        return None
    position = anchor.get('position', {})
    start, end = position.get('start', 0), position.get('end', 0)
    if new_text[start:end] == quote and end > start:
        return Reanchor(start, end, 'position', 100.0)
    low = max(0, start - NEARBY_WINDOW)
    window = new_text[low : end + NEARBY_WINDOW]
    found = _best_occurrence(anchor, window, low, threshold)
    if found:
        return found._replace(method='nearby')
    found = _best_occurrence(anchor, new_text, 0, threshold)
    return found._replace(method='anywhere') if found else None
