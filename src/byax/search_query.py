"""Search syntax shared by the CLI, MCP, REST and desktop library search.

A query is parsed into terms, quoted phrases, ``NEAR(...)`` groups, ``-`` negations and
``field:value`` filters, and compiled into FTS5 expressions built only from quoted word
tokens. User text never reaches FTS5 as syntax, so a query cannot inject operators,
columns or functions. Typo suggestions come from the index vocabulary through RapidFuzz
and are applied only when a caller asks for expansion.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

MAX_QUERY_CHARS = 4096
MAX_CLAUSES = 64
NEAR_DEFAULT = 10
NEAR_MAXIMUM = 100
# Column filters that select FTS5 columns, and filters applied to hit metadata.
COLUMNS = ('heading', 'text')
FILTERS = ('title', 'path')
# BM25 weights: chunk_id (unindexed), heading, text. Headings rank above body text.
COLUMN_WEIGHTS = (0.0, 4.0, 1.0)
STOPWORDS = frozenset(
    [
        'a',
        'about',
        'above',
        'after',
        'again',
        'against',
        'all',
        'also',
        'am',
        'an',
        'and',
        'any',
        'are',
        'as',
        'at',
        'be',
        'because',
        'been',
        'before',
        'being',
        'below',
        'between',
        'both',
        'but',
        'by',
        'can',
        'could',
        'did',
        'do',
        'does',
        'doing',
        'down',
        'during',
        'each',
        'few',
        'for',
        'from',
        'further',
        'had',
        'has',
        'have',
        'having',
        'he',
        'her',
        'here',
        'hers',
        'herself',
        'him',
        'himself',
        'his',
        'how',
        'i',
        'if',
        'in',
        'into',
        'is',
        'it',
        'its',
        'itself',
        'just',
        'me',
        'more',
        'most',
        'my',
        'myself',
        'no',
        'nor',
        'not',
        'now',
        'of',
        'off',
        'on',
        'once',
        'only',
        'or',
        'other',
        'our',
        'ours',
        'ourselves',
        'out',
        'over',
        'own',
        'same',
        'she',
        'should',
        'so',
        'some',
        'such',
        'than',
        'that',
        'the',
        'their',
        'theirs',
        'them',
        'themselves',
        'then',
        'there',
        'these',
        'they',
        'this',
        'those',
        'through',
        'to',
        'too',
        'under',
        'until',
        'up',
        'very',
        'was',
        'we',
        'were',
        'what',
        'when',
        'where',
        'which',
        'while',
        'who',
        'whom',
        'why',
        'will',
        'with',
        'would',
        'you',
        'your',
        'yours',
        'yourself',
        'yourselves',
    ]
)
# Question scaffolding that is never a useful key term when checking absence.
_QUESTION_WORDS = frozenset(
    [
        'ever',
        'mention',
        'mentioned',
        'mentions',
        'find',
        'found',
        'appear',
        'appears',
        'appeared',
        'source',
        'sources',
        'library',
        'libraries',
        'document',
        'documents',
        'text',
        'notes',
        'manuscript',
        'anywhere',
        'say',
        'says',
        'said',
        'tell',
        'show',
        'please',
    ]
)
_WORD = re.compile(r'\w+', re.UNICODE)


@dataclass(frozen=True)
class Clause:
    """One query element. ``parts`` holds one token tuple, or several for NEAR."""

    kind: str  # 'term', 'phrase' or 'near'
    parts: tuple[tuple[str, ...], ...]
    raw: str = ''
    column: str | None = None
    prefix: bool = False
    negated: bool = False
    distance: int = NEAR_DEFAULT

    @property
    def tokens(self):
        return tuple(token for part in self.parts for token in part)

    def text(self):
        return ' '.join(' '.join(part) for part in self.parts)

    def to_dict(self):
        value = {'kind': self.kind, 'text': self.text()}
        if self.column:
            value['field'] = self.column
        if self.prefix:
            value['prefix'] = True
        if self.negated:
            value['negated'] = True
        if self.kind == 'near':
            value['distance'] = self.distance
        return value


@dataclass(frozen=True)
class Query:
    raw: str
    clauses: tuple[Clause, ...] = ()
    filters: tuple[tuple[str, str], ...] = ()
    dropped_stopwords: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    expansions: dict = field(default_factory=dict)

    @property
    def positive(self):
        return tuple(c for c in self.clauses if not c.negated)

    @property
    def negative(self):
        return tuple(c for c in self.clauses if c.negated)

    @property
    def empty(self):
        return not self.positive

    def fts(self):
        """Porter-table MATCH expression, or None when nothing positive can match."""
        if not self.positive:
            return None
        expression = ' OR '.join(_fts_clause(c) for c in self.positive)
        if self.negative:
            expression = f'({expression}) NOT ({" OR ".join(_fts_clause(c) for c in self.negative)})'
        return expression

    def trigram(self):
        """Substring expression for the trigram table (text column only), or None.

        Each positive clause becomes a literal substring; NEAR becomes all of its parts.
        Fragments shorter than three characters cannot match a trigram index and are skipped.
        """
        positive = [x for x in (_trigram_clause(c) for c in self.positive if c.column != 'heading') if x]
        if not positive:
            return None
        expression = ' OR '.join(positive)
        negative = [x for x in (_trigram_clause(c) for c in self.negative) if x]
        if negative:
            expression = f'({expression}) NOT ({" OR ".join(negative)})'
        return expression

    def accepts(self, hit):
        """Apply title:/path: filters to a hit's metadata (all filters must hold)."""
        for name, value in self.filters:
            pattern = value.casefold()
            if name == 'path':
                paths = [str(p).casefold() for p in hit.get('paths') or [hit.get('path', '')]]
                if not any(_matches(path, pattern) for path in paths):
                    return False
            elif name == 'title':
                titles = [_title(p) for p in hit.get('paths') or [hit.get('path', '')]]
                heading = (hit.get('locator') or {}).get('heading_path') or []
                titles += [str(h).casefold() for h in heading[:1]]
                if not any(_matches(title, pattern) for title in titles):
                    return False
        return True

    def excludes(self, text):
        """True when text literally contains a negated clause (used for dense hits)."""
        words = [w.casefold() for w in _WORD.findall(text or '')]
        joined = ' ' + ' '.join(words) + ' '
        for clause in self.negative:
            for part in clause.parts:
                needle = ' '.join(t.casefold() for t in part)
                if clause.prefix and len(part) == 1:
                    if any(w.startswith(needle) for w in words):
                        return True
                elif ' ' + needle + ' ' in joined:
                    return True
        return False

    def semantic_text(self):
        """Text to embed for dense search: the raw query unless it uses search syntax."""
        syntax = (
            self.filters
            or self.negative
            or any(c.column or c.prefix or c.kind == 'near' for c in self.clauses)
        )
        if not syntax and '"' not in self.raw:
            return self.raw.strip()[:MAX_QUERY_CHARS]
        return ' '.join(c.raw or c.text() for c in self.positive)

    def literal_terms(self):
        """Positive terms and phrases as plain strings, for highlighting and absence checks."""
        return list(dict.fromkeys(c.raw or c.text() for c in self.positive))

    def expand(self, suggestions):
        """Return a copy that also matches suggested spellings (explicit, per query)."""
        added, expansions = [], {}
        for clause in self.positive:
            if clause.kind != 'term' or clause.prefix:
                continue
            for suggestion in suggestions.get(clause.tokens[0].casefold(), [])[:3]:
                tokens = tuple(_WORD.findall(suggestion['term']))
                if tokens:
                    added.append(Clause('term', (tokens,), raw=suggestion['term'], column=clause.column))
                    expansions.setdefault(clause.tokens[0], []).append(suggestion['term'])
        if not added:
            return self
        return replace(self, clauses=self.clauses + tuple(added[:MAX_CLAUSES]), expansions=expansions)

    def to_dict(self):
        value = {
            'raw': self.raw,
            'clauses': [c.to_dict() for c in self.clauses],
            'filters': [{'field': n, 'value': v} for n, v in self.filters],
        }
        if self.dropped_stopwords:
            value['dropped_stopwords'] = list(self.dropped_stopwords)
        if self.warnings:
            value['warnings'] = list(self.warnings)
        if self.expansions:
            value['expanded'] = dict(self.expansions)
        return value


def _quote(tokens):
    return '"' + ' '.join(tokens) + '"'


def _fts_clause(clause):
    if clause.kind == 'near':
        body = f'NEAR({" ".join(_quote(p) for p in clause.parts)}, {clause.distance})'
    else:
        body = _quote(clause.parts[0]) + ('*' if clause.prefix else '')
    return f'{clause.column} : {body}' if clause.column else body


def _trigram_clause(clause):
    pieces = []
    sources = [clause.raw] if clause.kind != 'near' and clause.raw else [' '.join(p) for p in clause.parts]
    for source in sources:
        literal = source.strip().rstrip('*')
        if len(literal) >= 3:
            pieces.append('"' + literal.replace('"', '""') + '"')
    if not pieces:
        return None
    return pieces[0] if len(pieces) == 1 else '(' + ' AND '.join(pieces) + ')'


def _matches(value, pattern):
    if any(ch in pattern for ch in '*?['):
        return fnmatch.fnmatchcase(value, pattern) or fnmatch.fnmatchcase(value, '*' + pattern)
    return pattern in value


def _title(path):
    name = str(path).replace('\\', '/').rsplit('/', 1)[-1]
    return (name.rsplit('.', 1)[0] if '.' in name else name).casefold()


def _scan_quoted(text, position):
    end = text.find('"', position + 1)
    if end < 0:
        return text[position + 1 :], len(text)
    return text[position + 1 : end], end + 1


def _scan_word(text, position):
    match = re.compile(r'[^\s"()]+').match(text, position)
    if not match:
        return '', position + 1
    return match.group(0), match.end()


def parse(query):
    """Parse user search syntax into a :class:`Query`. Never raises on odd input."""
    raw = query if isinstance(query, str) else ''
    text = raw[:MAX_QUERY_CHARS]
    clauses, filters, warnings = [], [], []
    if len(raw) > MAX_QUERY_CHARS:
        warnings.append(f'Query truncated to {MAX_QUERY_CHARS} characters')
    position, negate = 0, False
    while position < len(text):
        char = text[position]
        if char.isspace() or char in '()':
            position += 1
            continue
        if char == '-' and position + 1 < len(text) and not text[position + 1].isspace():
            negate = True
            position += 1
            continue
        if text.startswith('NEAR(', position):
            close = text.find(')', position)
            close = len(text) if close < 0 else close
            body = text[position + 5 : close]
            distance = NEAR_DEFAULT
            if ',' in body:
                body, _, number = body.rpartition(',')
                if number.strip().isdigit():
                    distance = min(NEAR_MAXIMUM, int(number.strip()))
                else:
                    warnings.append('NEAR distance must be a whole number; using the default')
            parts = []
            for piece in re.findall(r'"[^"]*"|[^\s"]+', body):
                tokens = tuple(_WORD.findall(piece))
                if tokens:
                    parts.append(tokens)
            if len(parts) >= 2:
                clauses.append(
                    Clause('near', tuple(parts), raw=body.strip(), negated=negate, distance=distance)
                )
            elif parts:
                clauses.append(Clause('phrase', (parts[0],), raw=body.strip(), negated=negate))
            negate, position = False, close + 1
            continue
        column = None
        field_match = re.compile(r'(\w+):(?=\S)').match(text, position)
        if field_match:
            name = field_match.group(1).casefold()
            if name in COLUMNS or name in FILTERS:
                position = field_match.end()
                if name in FILTERS:
                    if text[position] == '"':
                        value, position = _scan_quoted(text, position)
                    else:
                        value, position = _scan_word(text, position)
                    if value.strip():
                        filters.append((name, value.strip()))
                    if negate:
                        warnings.append(f'{name}: filters cannot be negated; the filter was applied')
                    negate = False
                    continue
                column = name
            else:
                warnings.append(f'Unknown field "{field_match.group(1)}"; searched as words')
        char = text[position] if position < len(text) else ''
        if char == '"':
            value, position = _scan_quoted(text, position)
            tokens = tuple(_WORD.findall(value))
            if tokens:
                # Quoted text is always exact: never dropped as a stopword or spell-expanded.
                clauses.append(Clause('phrase', (tokens,), raw=value.strip(), column=column, negated=negate))
            negate = False
            continue
        word, position = _scan_word(text, position)
        if column is None and not negate and word in ('AND', 'OR'):
            continue
        if column is None and word == 'NOT':
            negate = True
            continue
        prefix = word.endswith('*')
        tokens = tuple(_WORD.findall(word))
        if tokens:
            kind = 'term' if len(tokens) == 1 else 'phrase'
            clauses.append(
                Clause(
                    kind,
                    (tokens,),
                    raw=word.rstrip('*'),
                    column=column,
                    prefix=prefix and kind == 'term',
                    negated=negate,
                )
            )
        negate = False
    if len(clauses) > MAX_CLAUSES:
        warnings.append(f'Only the first {MAX_CLAUSES} terms are searched')
        clauses = clauses[:MAX_CLAUSES]
    dropped = []

    def stop(c):
        return (
            c.kind == 'term'
            and not c.negated
            and not c.prefix
            and c.column is None
            and c.tokens[0].casefold() in STOPWORDS
        )

    # Stopwords only carry a query when nothing else does.
    if any(not c.negated and not stop(c) for c in clauses):
        dropped = [c.tokens[0] for c in clauses if stop(c)]
        clauses = [c for c in clauses if not stop(c)]
    if clauses and all(c.negated for c in clauses):
        warnings.append('Negated terms need at least one positive term')
    elif filters and not clauses:
        warnings.append('Filters only narrow results; add at least one search term')
    return Query(
        raw=raw,
        clauses=tuple(clauses),
        filters=tuple(filters),
        dropped_stopwords=tuple(dropped),
        warnings=tuple(warnings),
    )


def key_terms(text, limit=8):
    """Distinctive terms of a request: quoted phrases, then names, then longer words."""
    if not isinstance(text, str):
        return []
    phrases = [p.strip() for p in re.findall(r'["“]([^"”]{2,200})["”]', text) if _WORD.search(p)]
    names, words = [], []
    for sentence in re.split(r'(?<=[.!?])\s+', re.sub(r'["“][^"”]*["”]', ' ', text)):
        tokens = _WORD.findall(sentence)
        for position, token in enumerate(tokens):
            folded = token.casefold()
            if folded in STOPWORDS or folded in _QUESTION_WORDS:
                continue
            if token[:1].isupper() and position > 0:
                names.append(token)
            elif len(token) >= 4 or token.isdigit():
                words.append(token)
    ordered = list(dict.fromkeys(phrases + names + (words if not names and not phrases else [])))
    return ordered[:limit]


def suggest(term, vocabulary: Iterable[tuple[str, int]], *, limit=3, cutoff=80.0):
    """Ranked spelling suggestions for one term from ``(term, document_count)`` pairs."""
    from rapidfuzz import fuzz, process

    folded = term.casefold()
    counts = {}
    for candidate, documents in vocabulary:
        if candidate != folded and abs(len(candidate) - len(folded)) <= 3:
            counts[candidate] = max(documents, counts.get(candidate, 0))
    if not counts or len(folded) < 3:
        return []
    matches = process.extract(
        folded, list(counts), scorer=fuzz.ratio, limit=max(limit * 4, 10), score_cutoff=cutoff
    )
    ranked = sorted(matches, key=lambda m: (-round(m[1], 1), -counts[m[0]], m[0]))
    return [
        {'term': name, 'score': round(score, 1), 'documents': counts[name]}
        for name, score, _ in ranked[:limit]
    ]
