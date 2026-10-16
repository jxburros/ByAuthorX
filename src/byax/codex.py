"""Codex: characters, places, items and terms as project artifacts (context engine plan 8.2, C1/C2).

The optional top-level ``codex`` object is ``{"entries": {entry_id: entry}}``. An entry has a
kind, a canonical name, aliases, a case-sensitivity flag, excluded phrases, a description, ordered
traits and a **hide from AI** flag; each trait may be hidden from AI on its own. Entries are edited
only through canonical ``codex_*`` operations, so they are undoable, versioned and reviewable.

Mention detection uses one Aho-Corasick automaton over every name and alias (``pyahocorasick`` when
it is installed, otherwise the pure-Python automaton below). Matches respect word boundaries, each
entry's case sensitivity and its excluded phrases, and overlapping candidates resolve
leftmost-longest. Text is folded with a length-preserving map, so offsets always index the
original text.

Hidden data never reaches a model: :func:`ai_visible_entries`, :func:`ai_context` and
:func:`redact_for_ai` are the only codex views used where AI context is assembled, and transport
tools (MCP, REST) cannot ask for hidden entries. The assistant never writes the codex directly;
:func:`propose_fact` turns a model-suggested fact into a review proposal with provenance.
"""

from __future__ import annotations

import copy
import functools
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from . import anchors
from .errors import ByaxError
from .model import new_id, object_hash

Doc = dict[str, Any]

KINDS = ('character', 'place', 'item', 'term')
CODEX_FIELDS = frozenset(('entries',))
ENTRY_FIELDS = frozenset(
    (
        'id',
        'kind',
        'name',
        'aliases',
        'case_sensitive',
        'excluded',
        'description',
        'traits',
        'hide_from_ai',
        'tags',
        'created',
        'updated',
        'provenance',
    )
)
# Fields codex_add and codex_update accept (traits have their own operations).
EDITABLE = ('kind', 'name', 'aliases', 'case_sensitive', 'excluded', 'description', 'hide_from_ai', 'tags')
TRAIT_FIELDS = frozenset(('id', 'name', 'value', 'hide_from_ai', 'provenance', 'created', 'updated'))
TRAIT_EDITABLE = ('name', 'value', 'hide_from_ai', 'provenance')
PROVENANCE_FIELDS = frozenset(
    (
        'origin',
        'author',
        'provider',
        'document_id',
        'revision',
        'target',
        'start',
        'end',
        'quote',
        'item',
        'label',
        'proposed_at',
        'note',
    )
)
MAX_ENTRIES = 5000
MAX_NAME = 200
MAX_ALIASES = 64
MAX_DESCRIPTION = 20000
MAX_TRAITS = 200
MAX_VALUE = 5000
MAX_TAG = 64
MAX_PROVENANCE_TEXT = 2000
# Entries match case-sensitively unless the writer turns it off: "Will" the name is not "will".
DEFAULT_CASE_SENSITIVE = True


def _now() -> str:
    return anchors.attribution().get('timestamp') or datetime.now(UTC).isoformat()


def _text(value, limit, *, empty=False):
    return (
        isinstance(value, str)
        and len(value) <= limit
        and (empty or bool(value.strip()))
        and not any(0xD800 <= ord(c) <= 0xDFFF for c in value)
    )


# Validation (called from model.validate_document)


def _problem(entry: Any, *, series: bool = False) -> tuple[str, str] | None:
    """First structural problem of an entry as (message, field), or None."""
    if not isinstance(entry, dict):
        return 'Codex entries must be objects.', ''
    unknown = set(entry) - ENTRY_FIELDS
    if unknown:
        return 'Unknown codex entry field: ' + ', '.join(sorted(unknown)), ''
    if entry.get('kind') not in KINDS:
        return 'Codex kind must be character, place, item or term.', '.kind'
    if not _text(entry.get('name'), MAX_NAME) or entry['name'] != entry['name'].strip():
        return 'Codex names must be trimmed text of 1 to 200 characters.', '.name'
    for key, limit in (('aliases', MAX_ALIASES), ('excluded', MAX_ALIASES), ('tags', MAX_ALIASES)):
        values = entry.get(key, [])
        size = MAX_TAG if key == 'tags' else MAX_NAME
        if (
            not isinstance(values, list)
            or len(values) > limit
            or any(not _text(v, size) or v != v.strip() for v in values)
            or len({v.casefold() for v in values}) != len(values)
        ):
            return f'Codex {key} must be up to {limit} distinct trimmed texts.', '.' + key
    for key in ('case_sensitive', 'hide_from_ai'):
        if key in entry and type(entry[key]) is not bool:
            return f'{key} must be a boolean.', '.' + key
    if 'description' in entry and not _text(entry['description'], MAX_DESCRIPTION, empty=True):
        return 'Descriptions must be text of at most 20,000 characters.', '.description'
    for key in ('created', 'updated'):
        if key in entry and not _text(entry[key], 64):
            return 'Dates must be ISO 8601 text.', '.' + key
    if 'provenance' in entry and _provenance_problem(entry['provenance']):
        return _provenance_problem(entry['provenance']), '.provenance'
    traits = entry.get('traits', [])
    if not isinstance(traits, list) or len(traits) > MAX_TRAITS:
        return 'Traits must be a list of at most 200 objects.', '.traits'
    seen = set()
    for index, trait in enumerate(traits):
        path = f'.traits[{index}]'
        if not isinstance(trait, dict) or set(trait) - TRAIT_FIELDS:
            return 'Traits need id, name and value and only known fields.', path
        if not _text(trait.get('id'), 128) or trait['id'] in seen:
            return 'Traits need distinct identities.', path + '.id'
        seen.add(trait['id'])
        if not _text(trait.get('name'), MAX_NAME):
            return 'Trait names must be text of 1 to 200 characters.', path + '.name'
        if not _text(trait.get('value'), MAX_VALUE, empty=True):
            return 'Trait values must be text of at most 5,000 characters.', path + '.value'
        if 'hide_from_ai' in trait and type(trait['hide_from_ai']) is not bool:
            return 'hide_from_ai must be a boolean.', path + '.hide_from_ai'
        for key in ('created', 'updated'):
            if key in trait and not _text(trait[key], 64):
                return 'Dates must be ISO 8601 text.', path + '.' + key
        if 'provenance' in trait and _provenance_problem(trait['provenance']):
            return _provenance_problem(trait['provenance']), path + '.provenance'
    return None


def _provenance_problem(value):
    if not isinstance(value, dict) or set(value) - PROVENANCE_FIELDS:
        return 'Provenance must be an object with known fields: ' + ', '.join(sorted(PROVENANCE_FIELDS))
    for key, item in value.items():
        if key in ('start', 'end'):
            if type(item) is not int or item < 0:
                return 'Provenance offsets must be nonnegative integers.'
        elif not _text(item, MAX_PROVENANCE_TEXT, empty=True):
            return 'Provenance values must be short text.'
    return None


def validate_entries(entries: Any, fail: Callable[[str, str], Any], path: str) -> None:
    if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
        fail('Codex entries must be a mapping of at most 5,000 entries.', path)
    for key, entry in entries.items():
        if not isinstance(key, str) or not key or not isinstance(entry, dict) or entry.get('id') != key:
            fail('Codex entries require a nonempty identity matching their key.', f'{path}.{key}')
        problem = _problem(entry)
        if problem:
            fail(problem[0], f'{path}.{key}{problem[1]}')


def validate_codex(doc: Doc, fail: Callable[[str, str], Any]) -> None:
    """Structure and types of the optional ``codex`` object and the ``settings.series`` reference."""
    from .series import validate_reference

    validate_reference(doc.get('settings', {}).get('series'), fail)
    if 'codex' not in doc:
        return
    codex = doc['codex']
    if not isinstance(codex, dict) or set(codex) - CODEX_FIELDS or not isinstance(codex.get('entries'), dict):
        fail('A codex requires an entries mapping and only known fields.', 'codex')
    validate_entries(codex['entries'], fail, 'codex.entries')
    objects = {block.get('id') for block in doc['blocks']}
    items = (doc.get('project') or {}).get('items', {}) if isinstance(doc.get('project'), dict) else {}
    for key in codex['entries']:
        if key in objects or key in items:
            fail('Codex entry identity collides with a document object.', f'codex.entries.{key}.id')


# Queries


def _entries(doc: Doc) -> dict[str, dict[str, Any]]:
    codex = doc.get('codex')
    entries = codex.get('entries') if isinstance(codex, dict) else None
    return entries if isinstance(entries, dict) else {}


def find_entry(doc: Doc, identity: Any) -> dict[str, Any] | None:
    """The document's codex entry with this identity, if any."""
    entry = _entries(doc).get(identity) if isinstance(identity, str) else None
    return entry if isinstance(entry, dict) else None


def effective_entries(doc: Doc, series: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Document entries plus series entries, document first. Each value carries ``scope``.

    A document entry overrides a series entry with the same identity or the same name
    (compared case-insensitively); the overridden series entry is left out.
    """
    result = {key: dict(entry, scope='document') for key, entry in _entries(doc).items()}
    names = {entry['name'].casefold() for entry in result.values()}
    for key, entry in ((series or {}).get('codex', {}).get('entries', {}) or {}).items():
        if key in result or entry.get('name', '').casefold() in names:
            continue
        result[key] = dict(entry, scope='series')
    return result


def shadowed_series_entries(doc: Doc, series: dict[str, Any] | None) -> list[str]:
    """Series entry identities overridden by a document entry (same identity or name)."""
    effective = effective_entries(doc, series)
    return sorted(set((series or {}).get('codex', {}).get('entries', {}) or {}) - set(effective))


def visible_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """A copy without AI-hidden traits, or None when the whole entry is hidden from AI."""
    if entry.get('hide_from_ai'):
        return None
    result = copy.deepcopy(entry)
    result['traits'] = [t for t in result.get('traits', []) if not t.get('hide_from_ai')]
    return result


def ai_visible_entries(doc: Doc, series: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every codex entry a model may see, with hidden traits removed (document and series)."""
    return [
        visible
        for visible in (visible_entry(e) for e in effective_entries(doc, series).values())
        if visible is not None
    ]


def hidden_entry_ids(doc: Doc, series: dict[str, Any] | None = None) -> set[str]:
    return {key for key, entry in effective_entries(doc, series).items() if entry.get('hide_from_ai')}


def summary(entry: dict[str, Any], *, for_ai: bool = False) -> dict[str, Any]:
    """Compact listing row; ``for_ai`` drops hidden traits and authoring dates."""
    traits = [t for t in entry.get('traits', []) if not (for_ai and t.get('hide_from_ai'))]
    row = {
        'id': entry['id'],
        'kind': entry['kind'],
        'name': entry['name'],
        'aliases': list(entry.get('aliases', [])),
        'case_sensitive': entry.get('case_sensitive', DEFAULT_CASE_SENSITIVE),
        'traits': len(traits),
        'scope': entry.get('scope', 'document'),
    }
    if not for_ai:
        row['hide_from_ai'] = bool(entry.get('hide_from_ai'))
        row['hidden_traits'] = sum(1 for t in entry.get('traits', []) if t.get('hide_from_ai'))
    if entry.get('tags'):
        row['tags'] = list(entry['tags'])
    return row


def list_entries(
    doc: Doc,
    *,
    series: dict[str, Any] | None = None,
    include_hidden: bool = False,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Listing rows sorted by kind and name. Hidden entries only with ``include_hidden``."""
    if kind is not None and kind not in KINDS:
        raise ByaxError('invalid_input', 'Codex kind must be character, place, item or term.', field='kind')
    rows = []
    for entry in effective_entries(doc, series).values():
        if (entry.get('hide_from_ai') and not include_hidden) or (kind and entry['kind'] != kind):
            continue
        rows.append(summary(entry, for_ai=not include_hidden))
    return sorted(rows, key=lambda row: (KINDS.index(row['kind']), row['name'].casefold(), row['id']))


def lookup(
    doc: Doc,
    name: str,
    *,
    series: dict[str, Any] | None = None,
    include_hidden: bool = True,
    limit: int = 5,
) -> dict[str, Any]:
    """Resolve an identity, name or alias to one entry, with close candidates otherwise.

    Match order: identity, then exact name or alias (honouring case sensitivity), then the
    same text ignoring case, then RapidFuzz candidates. ``include_hidden=False`` is the AI view:
    hidden entries are never matched or suggested and hidden traits are removed.
    """
    if not isinstance(name, str) or not name.strip():
        raise ByaxError('invalid_input', 'Name an entry to look up.', field='name')
    entries = [
        entry
        for entry in effective_entries(doc, series).values()
        if include_hidden or not entry.get('hide_from_ai')
    ]
    wanted = ' '.join(name.split())

    def shown(entry):
        return copy.deepcopy(entry) if include_hidden else visible_entry(entry)

    for entry in entries:
        if entry['id'] == wanted:
            return {'query': name, 'match': 'id', 'entry': shown(entry), 'candidates': []}
    folded = []
    for entry in entries:
        for surface in [entry['name'], *entry.get('aliases', [])]:
            if surface == wanted:
                return {
                    'query': name,
                    'match': 'name',
                    'entry': shown(entry),
                    'surface': surface,
                    'candidates': [],
                }
            if surface.casefold() == wanted.casefold():
                folded.append((entry, surface))
    for entry, surface in folded:
        if not entry.get('case_sensitive', DEFAULT_CASE_SENSITIVE) or len(folded) == 1:
            return {
                'query': name,
                'match': 'folded',
                'entry': shown(entry),
                'surface': surface,
                'candidates': [],
            }
    from rapidfuzz import fuzz, process

    surfaces: dict[str, dict[str, Any]] = {}
    for entry in entries:
        for surface in [entry['name'], *entry.get('aliases', [])]:
            surfaces.setdefault(surface, entry)
    ranked = process.extract(wanted, list(surfaces), scorer=fuzz.WRatio, limit=limit * 3, score_cutoff=70)
    candidates, seen = [], set()
    for surface, score, _ in sorted(ranked, key=lambda r: (-round(r[1], 1), r[0])):
        entry = surfaces[surface]
        if entry['id'] in seen:
            continue
        seen.add(entry['id'])
        candidates.append(
            dict(summary(entry, for_ai=not include_hidden), surface=surface, score=round(score, 1))
        )
        if len(candidates) >= limit:
            break
    return {'query': name, 'match': None, 'entry': None, 'candidates': candidates}


# Mention detection


_FOLD = str.maketrans(
    {
        ' ': ' ',
        ' ': ' ',
        ' ': ' ',
        ' ': ' ',
        '\t': ' ',
        '\n': ' ',
        '\r': ' ',
        '’': "'",
        '‘': "'",
        'ʼ': "'",
        '‐': '-',
        '‑': '-',
    }
)


def fold(text: str) -> str:
    """Lowercase and normalize spaces and apostrophes without changing the length."""
    lowered = text.lower()
    if len(lowered) != len(text):
        # A few characters lowercase to two (for example U+0130); keep those unchanged.
        lowered = ''.join(c.lower() if len(c.lower()) == 1 else c for c in text)
    return lowered.translate(_FOLD)


def _pattern(surface: str) -> str:
    return fold(' '.join(surface.split()))


def _is_word(char: str) -> bool:
    return char.isalnum() or char == '_'


class Automaton:
    """Pure-Python Aho-Corasick automaton: ``add`` keys with values, ``build`` once, then ``iter``."""

    def __init__(self):
        self.goto: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[list[tuple[int, Any]]] = [[]]

    def add(self, key: str, value: Any) -> None:
        state = 0
        for char in key:
            nxt = self.goto[state].get(char)
            if nxt is None:
                nxt = len(self.goto)
                self.goto[state][char] = nxt
                self.goto.append({})
                self.fail.append(0)
                self.out.append([])
            state = nxt
        self.out[state].append((len(key), value))

    def build(self) -> None:
        queue = list(self.goto[0].values())
        position = 0
        while position < len(queue):
            state = queue[position]
            position += 1
            for char, nxt in self.goto[state].items():
                queue.append(nxt)
                fallback = self.fail[state]
                while fallback and char not in self.goto[fallback]:
                    fallback = self.fail[fallback]
                candidate = self.goto[fallback].get(char, 0)
                self.fail[nxt] = candidate if candidate != nxt else 0
                self.out[nxt] = self.out[nxt] + self.out[self.fail[nxt]]

    def iter(self, text: str) -> Iterable[tuple[int, int, Any]]:
        """Yield ``(start, end, value)`` for every occurrence of every key (end exclusive)."""
        goto, fail, out = self.goto, self.fail, self.out
        state = 0
        for index, char in enumerate(text):
            while state and char not in goto[state]:
                state = fail[state]
            state = goto[state].get(char, 0)
            if out[state]:
                for length, value in out[state]:
                    yield index + 1 - length, index + 1, value


class _NativeAutomaton:
    """Adapter for pyahocorasick (BSD-3-Clause), used only when it is installed."""

    def __init__(self, module):
        self.automaton = module.Automaton()
        self.values: dict[str, list[Any]] = {}

    def add(self, key, value):
        self.values.setdefault(key, []).append(value)

    def build(self):
        for key, values in self.values.items():
            self.automaton.add_word(key, (len(key), values))
        if self.values:
            self.automaton.make_automaton()

    def iter(self, text):
        if not self.values:
            return
        for last, (length, values) in self.automaton.iter(text):
            for value in values:
                yield last + 1 - length, last + 1, value


def _new_automaton(native: bool | None = None) -> Any:
    if native is not False:
        try:
            import ahocorasick
        except ImportError:
            if native:
                raise ByaxError('missing_dependency', 'pyahocorasick is not installed') from None
        else:
            return _NativeAutomaton(ahocorasick)
    return Automaton()


@functools.cache
def automaton_backend() -> str:
    """``pyahocorasick`` when that optional package is importable, otherwise ``python``."""
    try:
        import ahocorasick  # noqa: F401
    except ImportError:
        return 'python'
    return 'pyahocorasick'


def _matching_key(entries: dict[str, dict[str, Any]]) -> tuple:
    return tuple(
        (
            key,
            entry['name'],
            tuple(entry.get('aliases', [])),
            tuple(entry.get('excluded', [])),
            entry.get('case_sensitive', DEFAULT_CASE_SENSITIVE),
        )
        for key, entry in sorted(entries.items())
    )


@functools.lru_cache(maxsize=16)
def _compiled(key, native=None):
    automaton = _new_automaton(native)
    for identity, name, aliases, excluded, case_sensitive in key:
        for surface in dict.fromkeys([name, *aliases]):
            pattern = _pattern(surface)
            if pattern:
                automaton.add(pattern, ('name', identity, ' '.join(surface.split()), case_sensitive))
        for phrase in excluded:
            pattern = _pattern(phrase)
            if pattern:
                automaton.add(pattern, ('exclude', identity, ' '.join(phrase.split()), case_sensitive))
    automaton.build()
    return automaton


def scan(
    text: str,
    entries: dict[str, dict[str, Any]],
    *,
    native: bool | None = None,
    near_misses: bool = False,
) -> list[dict[str, Any]]:
    """Mentions in one text: ``{entry, entries, surface, alias, start, end}``, leftmost-longest.

    ``entries`` maps identities to entries. A span claimed by several entries (a shared alias)
    names its first entry in ``entry`` and all of them in ``entries`` with ``ambiguous``.
    With ``near_misses`` the result also lists, under ``rejected``, occurrences that were
    not counted because of letter case or an excluded phrase (for unlinked mentions).
    """
    if not text or not entries:
        return []
    automaton = _compiled(_matching_key(entries), native)
    folded = fold(text)
    length = len(text)
    names: dict[tuple[int, int], list[tuple[str, str]]] = {}
    exclusions: dict[str, list[tuple[int, int]]] = {}
    rejected = []
    for start, end, (role, identity, surface, case_sensitive) in automaton.iter(folded):
        if (_is_word(surface[0]) and start > 0 and _is_word(text[start - 1])) or (
            _is_word(surface[-1]) and end < length and _is_word(text[end])
        ):
            continue
        exact = ' '.join(text[start:end].split()) == surface
        if role == 'exclude':
            if exact or not case_sensitive:
                exclusions.setdefault(identity, []).append((start, end))
            continue
        if case_sensitive and not exact:
            if near_misses:
                rejected.append(
                    {'entry': identity, 'alias': surface, 'start': start, 'end': end, 'reason': 'case'}
                )
            continue
        names.setdefault((start, end), []).append((identity, surface))
    spans = []
    for (start, end), claims in names.items():
        kept = []
        for identity, surface in claims:
            if any(s <= start and end <= e for s, e in exclusions.get(identity, [])):
                if near_misses:
                    rejected.append(
                        {
                            'entry': identity,
                            'alias': surface,
                            'start': start,
                            'end': end,
                            'reason': 'excluded',
                        }
                    )
                continue
            kept.append((identity, surface))
        if kept:
            spans.append((start, end, kept))
    spans.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    result: list[dict[str, Any]] = []
    last = -1
    for start, end, claims in spans:
        if start < last:
            continue
        last = end
        identities = list(dict.fromkeys(identity for identity, _ in claims))
        mention = {
            'entry': identities[0],
            'surface': text[start:end],
            'alias': claims[0][1],
            'start': start,
            'end': end,
        }
        if len(identities) > 1:
            mention.update(entries=identities, ambiguous=True)
        result.append(mention)
    if near_misses:
        result.extend(
            dict(r, rejected=True) for r in sorted(rejected, key=lambda r: (r['start'], r['entry']))
        )
    return result


def text_objects(doc: Doc) -> Iterable[tuple[str, str, str]]:
    """``(object_id, block_id, text)`` for every text-bearing body object, table cells included."""
    for block in doc.get('blocks', []):
        if block.get('type') == 'table':
            for row in block.get('rows', []):
                for cell in row.get('cells', []):
                    if cell.get('text'):
                        yield cell['id'], block['id'], cell['text']
        elif block.get('text'):
            yield block['id'], block['id'], block['text']


def _scope_entries(doc, series, for_ai):
    entries = effective_entries(doc, series)
    if for_ai:
        entries = {key: entry for key, entry in entries.items() if not entry.get('hide_from_ai')}
    return entries


def _ranges(doc, text_or_range, radius):
    """Resolve a selection description into ``(object_id, block_id, text, offset, scope)`` items.

    Each selected range is scanned as ``selection``; the rest of its object and every text object
    of the ``radius`` top-level blocks before and after it are scanned once as ``nearby``.
    """
    objects = list(text_objects(doc))
    if text_or_range is None:
        return [(o, b, t, 0, 'document') for o, b, t in objects]
    ranges = text_or_range if isinstance(text_or_range, list) else [text_or_range]
    blocks = [block['id'] for block in doc.get('blocks', [])]
    by_id = {item[0]: item for item in objects}
    by_block: dict[str, list[tuple[str, str, str]]] = {}
    for item in objects:
        by_block.setdefault(item[1], []).append(item)
    selections: dict[str, list[tuple[int, int]]] = {}
    neighbours: list[str] = []
    for selection in ranges:
        if not isinstance(selection, dict) or not isinstance(selection.get('target'), str):
            raise ByaxError('invalid_input', 'A range needs a target identity.', field='target')
        target = selection['target']
        found = by_id.get(target)
        if found is None:
            if target in blocks:
                found = (target, target, '')
            else:
                raise ByaxError('missing_target', 'The target no longer exists.', target=target)
        text = found[2]
        start, end = selection.get('start', 0), selection.get('end', len(text))
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
            raise ByaxError('invalid_input', 'Range offsets are outside the target text.', target=target)
        reach = selection.get('radius', radius)
        if type(reach) is not int or not 0 <= reach <= 100:
            raise ByaxError('invalid_input', 'Radius must be an integer from 0 to 100.', field='radius')
        selections.setdefault(found[0], []).append((start, end))
        position = blocks.index(found[1])
        neighbours.extend(blocks[max(0, position - reach) : position + reach + 1])
    result = []
    for object_id, spans in selections.items():
        text = by_id[object_id][2] if object_id in by_id else ''
        block_id = by_id[object_id][1] if object_id in by_id else object_id
        cursor = 0
        for start, end in sorted(set(spans)):
            if start > cursor:
                result.append((object_id, block_id, text[cursor:start], cursor, 'nearby'))
            if end > start:
                result.append(
                    (object_id, block_id, text[max(start, cursor) : end], max(start, cursor), 'selection')
                )
            cursor = max(cursor, end)
        if cursor < len(text):
            result.append((object_id, block_id, text[cursor:], cursor, 'nearby'))
    for block_id in dict.fromkeys(neighbours):
        for object_id, owner, text in by_block.get(block_id, []):
            if object_id not in selections:
                result.append((object_id, owner, text, 0, 'nearby'))
    return result


def mentions(
    doc: Doc,
    text_or_range: str | dict[str, Any] | list[dict[str, Any]] | None = None,
    *,
    series: dict[str, Any] | None = None,
    for_ai: bool = False,
    radius: int = 0,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Codex mentions in text, in a selection with nearby blocks, or in the whole document.

    ``text_or_range`` is a string (offsets index that string), a range
    ``{"target", "start"?, "end"?, "radius"?}`` (or a list of them; ``radius`` blocks before and
    after are scanned as ``nearby``), or None for every text object of the document. Results
    are bounded by ``limit`` with an ``offset`` continuation. ``for_ai`` excludes entries hidden
    from AI, so their names are not even detected.
    """
    if type(limit) is not int or limit < 1 or type(offset) is not int or offset < 0:
        raise ByaxError('invalid_input', 'Invalid mention pagination.')
    entries = _scope_entries(doc, series, for_ai)
    found = []
    if isinstance(text_or_range, str):
        for mention in scan(text_or_range, entries):
            found.append(dict(mention, scope='text'))
    else:
        for object_id, block_id, text, base, scope in _ranges(doc, text_or_range, radius):
            for mention in scan(text, entries):
                found.append(
                    dict(
                        mention,
                        target=object_id,
                        block=block_id,
                        start=mention['start'] + base,
                        end=mention['end'] + base,
                        scope=scope,
                    )
                )
    counts: dict[str, int] = {}
    for mention in found:
        counts[mention['entry']] = counts.get(mention['entry'], 0) + 1
        mention['name'] = entries[mention['entry']]['name']
        mention['kind'] = entries[mention['entry']]['kind']
    page = found[offset : offset + limit]
    more = offset + len(page) < len(found)
    return {
        'mentions': page,
        'entries': [
            dict(summary(entries[key], for_ai=for_ai), mentions=count)
            for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], entries[kv[0]]['name']))
        ],
        'total': len(found),
        'offset': offset,
        'truncated': more,
        'continuation': offset + len(page) if more else None,
        'automaton': automaton_backend(),
    }


def ai_context(
    doc: Doc,
    text_or_range: str | dict[str, Any] | list[dict[str, Any]] | None,
    *,
    series: dict[str, Any] | None = None,
    radius: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """Codex entries mentioned in a selection and nearby text, as model context (route R1).

    Only AI-visible entries and traits are included; ``hidden_omitted`` counts hidden entries
    that would otherwise have been detected, without naming them.
    """
    visible = mentions(doc, text_or_range, series=series, for_ai=True, radius=radius, limit=10**6)
    everything = mentions(doc, text_or_range, series=series, radius=radius, limit=10**6)
    effective = effective_entries(doc, series)
    entries = []
    for row in visible['entries'][:limit]:
        entry = visible_entry(effective[row['id']])
        if entry is None:
            continue
        entries.append(
            {
                'id': entry['id'],
                'kind': entry['kind'],
                'name': entry['name'],
                'aliases': entry.get('aliases', []),
                'description': entry.get('description', ''),
                'traits': [{'name': t['name'], 'value': t['value']} for t in entry.get('traits', [])],
                'mentions': row['mentions'],
                'scope': effective[row['id']].get('scope', 'document'),
            }
        )
    hidden = {m['entry'] for m in everything['mentions'] if effective[m['entry']].get('hide_from_ai')}
    return {
        'entries': entries,
        'omitted': [{'reason': 'Codex entry limit exceeded', 'count': len(visible['entries']) - len(entries)}]
        if len(visible['entries']) > len(entries)
        else [],
        'hidden_omitted': len(hidden),
        'mentions': [
            {k: m[k] for k in ('entry', 'surface', 'start', 'end', 'scope', 'target') if k in m}
            for m in visible['mentions']
        ],
    }


def redact_for_ai(doc: Doc, payload: Any, *, series: dict[str, Any] | None = None) -> Any:
    """Copy of an AI payload with hidden codex entries and traits removed wherever they appear.

    Codex data enters payloads as entry objects (``kind`` and ``name`` with an identity),
    under ``codex`` keys, or as lists of traits. Anything identified as a hidden entry, or a
    trait marked ``hide_from_ai``, is dropped. Applied at every place that assembles AI context.
    """
    hidden = hidden_entry_ids(doc, series)
    drop = object()

    def clean(value):
        if isinstance(value, dict):
            if value.get('id') in hidden and ('name' in value or 'kind' in value):
                return drop
            if value.get('hide_from_ai') is True:
                return drop
            result = {}
            for key, item in value.items():
                if key == 'codex' and isinstance(item, dict) and isinstance(item.get('entries'), dict):
                    item = {
                        'entries': {
                            k: visible_entry(e)
                            for k, e in item['entries'].items()
                            if isinstance(e, dict) and k not in hidden and not e.get('hide_from_ai')
                        }
                    }
                cleaned = clean(item)
                if cleaned is not drop:
                    result[key] = cleaned
            return result
        if isinstance(value, list):
            return [c for c in (clean(item) for item in value) if c is not drop]
        return value

    cleaned = clean(copy.deepcopy(payload))
    return None if cleaned is drop else cleaned


def _sections(doc: Doc) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Binder items when the document has a binder, otherwise its headings, plus block owners."""
    from . import project

    if isinstance(doc.get('project'), dict):
        rows = [
            {'item': row['id'], 'heading': row['heading'], 'title': row['title'], 'kind': row['kind']}
            for row in project.items(doc)
        ]
        return rows, project.item_for_block(doc)
    rows, owner, current = [], {}, None
    for block in doc.get('blocks', []):
        if block.get('type') == 'heading':
            current = block['id']
            rows.append(
                {
                    'item': block['id'],
                    'heading': block['id'],
                    'title': block.get('text', ''),
                    'kind': 'heading',
                }
            )
        if current is not None:
            owner[block['id']] = current
    return rows, owner


def heatmap(
    doc: Doc,
    *,
    series: dict[str, Any] | None = None,
    for_ai: bool = False,
    entries: list[str] | None = None,
) -> dict[str, Any]:
    """Mention counts per binder item (or heading section) and entry, for the whole manuscript."""
    effective = _scope_entries(doc, series, for_ai)
    if entries is not None:
        effective = {key: value for key, value in effective.items() if key in set(entries)}
    rows, owner = _sections(doc)
    counts: dict[str, dict[str, int]] = {row['item']: {} for row in rows}
    unassigned: dict[str, int] = {}
    totals: dict[str, int] = {}
    for _, block_id, text in text_objects(doc):
        bucket = counts.get(owner.get(block_id, ''), unassigned)
        for mention in scan(text, effective):
            bucket[mention['entry']] = bucket.get(mention['entry'], 0) + 1
            totals[mention['entry']] = totals.get(mention['entry'], 0) + 1
    ordered = sorted(totals, key=lambda key: (-totals[key], effective[key]['name'].casefold()))
    return {
        'sections': [
            dict(row, counts=counts[row['item']], total=sum(counts[row['item']].values())) for row in rows
        ],
        'unassigned': {'counts': unassigned, 'total': sum(unassigned.values())},
        'entries': [dict(summary(effective[key], for_ai=for_ai), mentions=totals[key]) for key in ordered],
        'unmentioned': sorted(
            (key for key in effective if key not in totals), key=lambda key: effective[key]['name'].casefold()
        ),
        'grouping': 'binder' if isinstance(doc.get('project'), dict) else 'headings',
    }


def spelling_names(doc: Doc) -> list[str]:
    """Names, aliases and their capitalized words, for the fiction name-spelling check.

    This is a local, deterministic check, so hidden entries take part too. Series entries need
    the document's path; :func:`byax.series.with_series` supplies them in a document view.
    """
    names = set()
    for entry in _entries(doc).values():
        for surface in [entry.get('name', ''), *entry.get('aliases', [])]:
            if not isinstance(surface, str) or not surface.strip():
                continue
            names.add(surface)
            names.update(w.strip(".,'’") for w in surface.split() if len(w) >= 4 and w[:1].isupper())
    return sorted(n for n in names if n)


# Proposals: the assistant never writes the codex directly.


def propose_fact(
    doc: Doc,
    *,
    entry: str,
    trait: str,
    value: str,
    provenance: dict[str, Any] | None = None,
    kind: str = 'character',
    explanation: str | None = None,
    author: str = 'assistant',
    now: str | None = None,
    series: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``propose`` operation for one model-suggested fact, for the review queue.

    ``entry`` is an identity, name or alias. A known entry gets ``codex_add_trait``, or
    ``codex_update_trait`` when it already has a trait of that name (the old value is shown in the
    explanation); an unknown name proposes ``codex_add`` of a new entry carrying the trait. A
    series entry is overridden by a new document entry. ``provenance`` (where the fact was read,
    for example ``{"target": block, "start": 10, "end": 40, "quote": ..., "label": "ch. 3 ¶12"}``)
    is stored with the trait together with ``proposed_at``; created and updated dates are set
    when the proposal is accepted.
    """
    if not _text(trait, MAX_NAME) or not _text(value, MAX_VALUE):
        raise ByaxError('invalid_input', 'A fact needs a trait name and a value.', field='trait')
    record = {k: v for k, v in (provenance or {}).items() if v is not None}
    record.setdefault('origin', 'assistant')
    record.setdefault('proposed_at', now or datetime.now(UTC).isoformat())
    record.setdefault('document_id', doc.get('id', ''))
    record.setdefault('revision', doc.get('revision', ''))
    problem = _provenance_problem(record)
    if problem:
        raise ByaxError('invalid_input', problem, field='provenance')
    found = lookup(doc, entry, series=series)['entry'] if isinstance(entry, str) and entry.strip() else None
    label = record.get('label')
    where = f' ({label})' if label else ''
    if found is not None and found.get('scope', 'document') == 'document':
        existing = next(
            (t for t in found.get('traits', []) if t['name'].casefold() == trait.casefold()), None
        )
        if existing is not None:
            operation = {
                'type': 'codex_update_trait',
                'target': found['id'],
                'trait': existing['id'],
                'values': {'value': value, 'provenance': record},
            }
            message = f'{found["name"]}: {existing["name"]} "{existing["value"]}" → "{value}"{where}'
        else:
            operation = {
                'type': 'codex_add_trait',
                'target': found['id'],
                'name': trait,
                'value': value,
                'provenance': record,
            }
            message = f'{found["name"]}: {trait} is "{value}"{where}'
    else:
        base = found or {'name': ' '.join(entry.split()), 'kind': kind}
        values: dict[str, Any] = {'kind': base['kind'], 'name': base['name']}
        for key in ('aliases', 'case_sensitive', 'excluded', 'description', 'hide_from_ai', 'tags'):
            if key in base:
                values[key] = copy.deepcopy(base[key])
        values['traits'] = [
            {
                'name': t['name'],
                'value': t['value'],
                **({'hide_from_ai': True} if t.get('hide_from_ai') else {}),
            }
            for t in (found or {}).get('traits', [])
            if t['name'].casefold() != trait.casefold()
        ] + [{'name': trait, 'value': value, 'provenance': record}]
        values['provenance'] = record
        operation = {'type': 'codex_add', 'values': values}
        if found is not None:
            operation['id'] = found['id']
        message = f'New codex entry {values["name"]}: {trait} is "{value}"{where}'
    return {
        'type': 'propose',
        'values': {
            'operations': [operation],
            'explanation': explanation or message,
            'author': author,
            'origin': 'codex_fact',
        },
    }


# Canonical operations

# Required fields, optional fields (same convention as engine.OP_SCHEMAS).
OP_SCHEMAS = {
    'codex_add': ('values', 'id'),
    'codex_update': ('target values', ''),
    'codex_remove': ('target', ''),
    'codex_add_trait': ('target name value', 'id hide_from_ai provenance'),
    'codex_update_trait': ('target trait values', ''),
    'codex_remove_trait': ('target trait', ''),
}


def _error(message, field=None, **details):
    raise ByaxError('schema_error', message, field=field, **details)


def _entry(doc, op):
    entry = find_entry(doc, op.get('target'))
    if entry is None:
        raise ByaxError(
            'missing_target', 'Codex entry does not exist.', target=op.get('target'), field='target'
        )
    if op.get('expected_hash') and object_hash(entry) != op['expected_hash']:
        raise ByaxError('precondition_failed', 'Codex entry changed.', target=entry['id'])
    return entry


def _trait(entry, identity):
    trait = next((t for t in entry.get('traits', []) if t['id'] == identity), None)
    if trait is None:
        raise ByaxError('missing_target', 'Trait does not exist.', target=identity, field='trait')
    return trait


def _check_problem(entry, field='values'):
    problem = _problem(entry)
    if problem:
        _error(problem[0], field + problem[1])


def _new_trait(name, value, *, identity=None, hide=None, provenance=None, now=None):
    trait = {'id': identity or new_id('trait'), 'name': name, 'value': value}
    if hide:
        trait['hide_from_ai'] = True
    if provenance is not None:
        trait['provenance'] = copy.deepcopy(provenance)
    trait['created'] = trait['updated'] = now or _now()
    return trait


def _apply_fields(entry, values):
    unknown = set(values) - set(EDITABLE)
    if unknown:
        _error('Unknown codex fields.', 'values', unknown=sorted(unknown))
    for key, value in values.items():
        if value is None and key in (
            'aliases',
            'excluded',
            'description',
            'tags',
            'case_sensitive',
            'hide_from_ai',
        ):
            entry.pop(key, None)
            continue
        if isinstance(value, str) and key == 'name':
            value = ' '.join(value.split())
        entry[key] = copy.deepcopy(value)
    if entry.get('hide_from_ai') is False:
        entry.pop('hide_from_ai')


def _op_add(doc, op, base_revision, kind, changed, allow_policy_changes):
    from .model import find_object

    values = copy.deepcopy(op['values'])
    traits = values.pop('traits', [])
    provenance = values.pop('provenance', None)
    codex = doc.setdefault('codex', {'entries': {}})
    identity = op.get('id') or new_id('cx')
    if identity in codex['entries']:
        raise ByaxError('conflict', 'Codex entry identity already exists.', target=identity)
    try:
        find_object(doc, identity)
    except ByaxError:
        pass
    else:
        raise ByaxError('conflict', 'Identity is already used by a document object.', target=identity)
    if len(codex['entries']) >= MAX_ENTRIES:
        raise ByaxError('limit_exceeded', 'A codex holds at most 5,000 entries.')
    now = _now()
    entry: dict[str, Any] = {'id': identity}
    _apply_fields(entry, values)
    if not isinstance(traits, list):
        _error('Traits must be a list.', 'values.traits')
    entry['traits'] = []
    for index, trait in enumerate(traits):
        if not isinstance(trait, dict) or set(trait) - {'name', 'value', 'hide_from_ai', 'provenance'}:
            _error('Traits need name and value.', f'values.traits[{index}]')
        entry['traits'].append(
            _new_trait(
                trait.get('name'),
                trait.get('value'),
                identity=f'trait_{index + 1}',
                hide=trait.get('hide_from_ai'),
                provenance=trait.get('provenance'),
                now=now,
            )
        )
    if provenance is not None:
        entry['provenance'] = provenance
    entry['created'] = entry['updated'] = now
    _check_problem(entry)
    codex['entries'][identity] = entry
    changed.append(identity)


def _op_update(doc, op, base_revision, kind, changed, allow_policy_changes):
    entry = _entry(doc, op)
    _apply_fields(entry, copy.deepcopy(op['values']))
    entry['updated'] = _now()
    _check_problem(entry)
    changed.append(entry['id'])


def _op_remove(doc, op, base_revision, kind, changed, allow_policy_changes):
    entry = _entry(doc, op)
    del doc['codex']['entries'][entry['id']]
    changed.append(entry['id'])


def _op_add_trait(doc, op, base_revision, kind, changed, allow_policy_changes):
    entry = _entry(doc, op)
    traits = entry.setdefault('traits', [])
    identity = op.get('id')
    if identity is None:
        number = len(traits) + 1
        taken = {t['id'] for t in traits}
        while f'trait_{number}' in taken:
            number += 1
        identity = f'trait_{number}'
    elif any(t['id'] == identity for t in traits):
        raise ByaxError('conflict', 'Trait identity already exists.', target=identity)
    if 'hide_from_ai' in op and type(op['hide_from_ai']) is not bool:
        _error('hide_from_ai must be a boolean.', 'hide_from_ai')
    now = _now()
    traits.append(
        _new_trait(
            op['name'],
            op['value'],
            identity=identity,
            hide=op.get('hide_from_ai'),
            provenance=op.get('provenance'),
            now=now,
        )
    )
    entry['updated'] = now
    _check_problem(entry, 'trait')
    changed.extend((entry['id'], identity))


def _op_update_trait(doc, op, base_revision, kind, changed, allow_policy_changes):
    entry = _entry(doc, op)
    trait = _trait(entry, op['trait'])
    values = copy.deepcopy(op['values'])
    unknown = set(values) - set(TRAIT_EDITABLE)
    if unknown:
        _error('Unknown trait fields.', 'values', unknown=sorted(unknown))
    for key, value in values.items():
        if value is None and key in ('hide_from_ai', 'provenance'):
            trait.pop(key, None)
        else:
            trait[key] = value
    if trait.get('hide_from_ai') is False:
        trait.pop('hide_from_ai')
    trait['updated'] = entry['updated'] = _now()
    _check_problem(entry, 'values')
    changed.extend((entry['id'], trait['id']))


def _op_remove_trait(doc, op, base_revision, kind, changed, allow_policy_changes):
    entry = _entry(doc, op)
    trait = _trait(entry, op['trait'])
    entry['traits'].remove(trait)
    entry['updated'] = _now()
    changed.extend((entry['id'], trait['id']))


HANDLERS = {
    'codex_add': _op_add,
    'codex_update': _op_update,
    'codex_remove': _op_remove,
    'codex_add_trait': _op_add_trait,
    'codex_update_trait': _op_update_trait,
    'codex_remove_trait': _op_remove_trait,
}


def export_losses(doc: Doc, fmt: str) -> list[dict[str, Any]]:
    """Exchange formats keep the text; codex entries stay in the master only."""
    if fmt == 'byax' or not _entries(doc):
        return []
    return [
        {
            'code': 'codex_not_exported',
            'severity': 'warning',
            'message': f'{len(_entries(doc))} codex entries are kept in the .byax master only.',
            'target': 'codex',
            'count': len(_entries(doc)),
        }
    ]


def _register_spelling_source() -> None:
    from . import fiction_checks

    fiction_checks.register_name_source(spelling_names)


_register_spelling_source()
