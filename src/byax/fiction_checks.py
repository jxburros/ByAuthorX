"""Deterministic, advisory fiction analysis checks (competitiveness plan, section 6.2).

The ``fiction`` suite plugs into the findings pipeline in ``checks.py``. It never changes text
and ships with low-noise defaults: the suite is not in the default suites, each rule has a
sensitivity level, and a profile or section profile can enable, disable and tune any rule through
its ``fiction`` setting. Word lists are data (``resources/fiction/<language>.json``).

Dependency units, so incremental runs re-evaluate only what an edit can change:

* ``block:<id>``: rules that read one paragraph (filter words, tags, long paragraphs, telling).
* ``fiction:window:<id>``: rules that compare a paragraph with what precedes it (echoes,
  repeated openings, tag repetition, rhythm). The unit depends on the paragraph plus the
  preceding paragraphs of the same section needed to fill the enabled rules' windows, so an edit
  re-runs the edited paragraph and only the following paragraphs whose window reaches it.
* ``context:fiction_chapter:<id>``: chapter-wide balance rules (adverb density, sensory balance).
* ``context:fiction_names``: invented-name spelling across the whole document.
"""

import bisect
import copy
import functools
import re

from .language_packs import data_signature, on_refresh
from .model import object_hash
from .prose import dialogue_spans, in_spans, is_adverb, languages, sentences, wordlist, words

FAMILY = 'fiction'
VERSION = 1
# rule: (suite, family, level, languages or None for every language, severity)
RULES = {
    'fiction.name_spelling': ('fiction', FAMILY, 'conservative', None, 'warning'),
    'fiction.phrase_echo': ('fiction', FAMILY, 'conservative', None, 'advisory'),
    'fiction.word_echo': ('fiction', FAMILY, 'balanced', None, 'advisory'),
    'fiction.filter_words': ('fiction', FAMILY, 'balanced', None, 'advisory'),
    'fiction.tag_adverb': ('fiction', FAMILY, 'balanced', None, 'advisory'),
    'fiction.tag_repetition': ('fiction', FAMILY, 'balanced', None, 'advisory'),
    'fiction.tag_bookism': ('fiction', FAMILY, 'broad', None, 'advisory'),
    'fiction.adverb_density': ('fiction', FAMILY, 'broad', None, 'advisory'),
    'fiction.sentence_opening': ('fiction', FAMILY, 'broad', None, 'advisory'),
    'fiction.sentence_rhythm': ('fiction', FAMILY, 'broad', None, 'advisory'),
    'fiction.paragraph_rhythm': ('fiction', FAMILY, 'broad', None, 'advisory'),
    'fiction.paragraph_length': ('fiction', FAMILY, 'broad', None, 'advisory'),
    'fiction.sensory_balance': ('fiction', FAMILY, 'broad', None, 'advisory'),
    'fiction.show_tell': ('fiction', FAMILY, 'broad', None, 'advisory'),
}
# Published precision and recall are reported per group (docs/benchmarks).
GROUPS = {
    'fiction.tag_bookism': 'dialogue',
    'fiction.tag_adverb': 'dialogue',
    'fiction.tag_repetition': 'dialogue',
    'fiction.filter_words': 'style',
    'fiction.adverb_density': 'style',
    'fiction.show_tell': 'style',
    'fiction.word_echo': 'repetition',
    'fiction.phrase_echo': 'repetition',
    'fiction.sentence_opening': 'repetition',
    'fiction.sentence_rhythm': 'rhythm',
    'fiction.paragraph_rhythm': 'rhythm',
    'fiction.paragraph_length': 'rhythm',
    'fiction.sensory_balance': 'sensory',
    'fiction.name_spelling': 'names',
}
LEVELS = {'conservative': 0, 'balanced': 1, 'broad': 2}
# name: (default, minimum, maximum); a float default admits fractional values.
THRESHOLDS = {
    'echo_window_words': (30, 5, 500),
    'echo_min_length': (5, 3, 12),
    'phrase_window_words': (120, 10, 1000),
    'filter_per_paragraph': (2, 1, 20),
    'opening_run': (3, 2, 10),
    'tag_window_words': (120, 20, 1000),
    'tag_repeat': (3, 2, 10),
    'adverb_per_100_words': (2.5, 0.1, 50.0),
    'adverb_min_words': (300, 50, 100000),
    'rhythm_run': (6, 3, 30),
    'rhythm_spread_words': (4, 0, 30),
    'rhythm_min_mean_words': (8, 1, 100),
    'paragraph_rhythm_run': (6, 3, 30),
    'paragraph_rhythm_tolerance': (0.15, 0.0, 1.0),
    'paragraph_rhythm_min_words': (20, 1, 1000),
    'paragraph_max_words': (250, 50, 5000),
    'sensory_min_words': (500, 50, 100000),
    'sensory_min_hits': (10, 1, 10000),
    'sensory_dominance': (0.8, 0.5, 1.0),
    'name_max_distance': (1, 1, 2),
    'name_variant_max_count': (3, 1, 100),
    'name_majority_ratio': (2, 1, 50),
}
CHAPTER_RULES = ('fiction.adverb_density', 'fiction.sensory_balance')
WINDOW_RULES = (
    'fiction.word_echo',
    'fiction.phrase_echo',
    'fiction.sentence_opening',
    'fiction.sentence_rhythm',
    'fiction.paragraph_rhythm',
    'fiction.tag_repetition',
)
# Seam for the context engine's codex (context engine plan, section 8.2): each source is a
# callable ``source(doc) -> iterable of canonical names``. A spelling within edit distance of a
# canonical name is proposed for correction whatever the counts. No source ships yet.
NAME_SOURCES = []


def register_name_source(source):
    if source not in NAME_SOURCES:
        NAME_SOURCES.append(source)


def canonical_names(doc):
    names = set()
    for source in NAME_SOURCES:
        names.update(n for n in source(doc) if isinstance(n, str) and n.strip())
    return sorted(names)


def group_names():
    return sorted(set(GROUPS.values()))


def group_profile(doc, profile, groups):
    """Inline profile that turns on exactly the rules of the named groups, keeping other settings.

    ``profile`` is a document profile ID or None. Used by the Checks tab to run one group at a time.
    """
    from .profiles import presets

    unknown = sorted(set(groups) - set(GROUPS.values()))
    if unknown:
        from .errors import ByaxError

        raise ByaxError('invalid_input', 'Unknown fiction group', groups=unknown, supported=group_names())
    base = copy.deepcopy(doc.get('profiles', {}).get(profile) or presets()['conservative_proofreading'])
    wanted = sorted(rule for rule, group in GROUPS.items() if group in groups)
    base['fiction'] = {
        'enabled': wanted,
        'disabled': sorted(set(RULES) - set(wanted)),
        'thresholds': dict(base.get('fiction', {}).get('thresholds', {})),
    }
    return base


def supported_languages():
    return languages('fiction')


def versions():
    """Word-list versions per language; an installed pack adds its identity so edits invalidate caches."""
    result = {}
    for lang in supported_languages():
        version = (wordlist('fiction', lang) or {}).get('version')
        signature = data_signature('fiction', lang)
        result[lang] = version if signature in (None, 'builtin') else f'{version}+{signature}'
    return result


# Settings --------------------------------------------------------------------------
def validate_settings(value):
    """Validate a profile's ``fiction`` setting and return it normalized."""
    from .errors import ByaxError

    def fail(message, field):
        raise ByaxError('invalid_input', message, field='profile.fiction' + field)

    if not isinstance(value, dict) or set(value) - {'enabled', 'disabled', 'thresholds'}:
        fail('Fiction settings are enabled, disabled and thresholds', '')
    result = {}
    for key in ('enabled', 'disabled'):
        rules = value.get(key, [])
        if not isinstance(rules, list) or any(r not in RULES for r in rules):
            fail('Expected a list of fiction rule IDs: ' + ', '.join(sorted(RULES)), '.' + key)
        result[key] = sorted(dict.fromkeys(rules))
    if set(result['enabled']) & set(result['disabled']):
        fail('A rule cannot be both enabled and disabled', '')
    thresholds = value.get('thresholds', {})
    if not isinstance(thresholds, dict) or set(thresholds) - set(THRESHOLDS):
        fail('Unknown threshold; supported: ' + ', '.join(sorted(THRESHOLDS)), '.thresholds')
    result['thresholds'] = {}
    for name, number in thresholds.items():
        default, low, high = THRESHOLDS[name]
        whole = isinstance(default, int)
        valid = (type(number) is int or (not whole and type(number) is float)) and low <= number <= high
        if not valid:
            fail(f'{name} must be a {"whole " if whole else ""}number from {low} to {high}', '.thresholds')
        result['thresholds'][name] = number
    return result


class _Settings:
    """Rules in force and thresholds for one section, resolved from its effective profile."""

    def __init__(self, config, level):
        fiction = (config or {}).get('fiction') or {}
        enabled, disabled = set(fiction.get('enabled', ())), set(fiction.get('disabled', ()))
        self.active = frozenset(
            rule
            for rule, spec in RULES.items()
            if rule not in disabled and (rule in enabled or LEVELS[spec[2]] <= level)
        )
        self.thresholds = {name: spec[0] for name, spec in THRESHOLDS.items()}
        self.thresholds.update(fiction.get('thresholds', {}))
        self.windowed = bool(self.active & set(WINDOW_RULES))

    def quotas(self):
        t, active = self.thresholds, self.active
        words_needed = max(
            t['echo_window_words'] if 'fiction.word_echo' in active else 0,
            t['phrase_window_words'] if 'fiction.phrase_echo' in active else 0,
            t['tag_window_words'] if 'fiction.tag_repetition' in active else 0,
        )
        sentences_needed = max(
            t['opening_run'] if 'fiction.sentence_opening' in active else 0,
            t['rhythm_run'] if 'fiction.sentence_rhythm' in active else 0,
        )
        return {
            'words': words_needed,
            'sentences': sentences_needed,
            'paragraphs': t['paragraph_rhythm_run'] if 'fiction.paragraph_rhythm' in active else 0,
        }


# Per-paragraph analysis --------------------------------------------------------------
class _Info:
    """Tokens, sentences, dialogue and attribution tags of one paragraph."""

    def __init__(self, block, language, lists):
        from .checks import _intentional

        text = block['text']
        self.block, self.id, self.text, self.language, self.lists = block, block['id'], text, language, lists
        self.intentional = _intentional(block)
        self.dialogue = dialogue_spans(text)
        self.tokens = words(text)
        self.starts = [s for s, _ in self.tokens]
        self.sentences = sentences(text, frozenset(lists.get('abbreviations', ())))
        begins = [a for a, _ in self.sentences]
        self.sentence_of = [max(0, bisect.bisect_right(begins, start) - 1) for start in self.starts]
        self.first_token = [None] * len(self.sentences)
        self.sentence_words = [0] * len(self.sentences)
        for token, position in enumerate(self.sentence_of):
            if self.sentences:
                self.sentence_words[position] += 1
                if self.first_token[position] is None:
                    self.first_token[position] = token
        self.openers = {self.tokens[i][0] for i in self.first_token if i is not None}

    @functools.cached_property
    def tags(self):
        return _tags(self)

    def word(self, index):
        start, end = self.tokens[index]
        return self.text[start:end]

    def narrative(self, start, end):
        return not in_spans(start, self.dialogue) and not in_spans(end - 1, self.dialogue)

    def dialogue_words(self):
        return sum(1 for s, _ in self.tokens if in_spans(s, self.dialogue))


def _tags(info):
    """Attribution tags: the first speech verb beside each quotation, with an adjacent -ly adverb."""
    spans, text, lists = info.dialogue, info.text, info.lists
    if not spans:
        return []
    verbs = {}
    for kind in ('bookisms', 'neutral', 'plain'):
        for verb in lists.get('speech_verbs', {}).get(kind, ()):
            verbs[verb] = kind
    exceptions = frozenset(lists.get('adverb_exceptions', ()))
    gaps, cursor = [], 0
    for position, (start, end) in enumerate(spans):
        gaps.append((cursor, start, position > 0, True))
        cursor = end
    gaps.append((cursor, len(text), True, False))
    found = {}
    for a, b, left, right in gaps:
        segment = text[a:b]
        regions = []
        if left:
            stop = re.search(r'[.!?…]', segment)
            regions.append((a, a + stop.start() if stop else b, False))
        if right:
            last = None
            for last in re.finditer(r'[.!?…]+\s*', segment):
                pass
            regions.append((a + last.end() if last else a, b, True))
        for lo, hi, from_end in regions:
            inside = [i for i, (s, e) in enumerate(info.tokens) if lo <= s and e <= hi]
            inside = inside[-8:] if from_end else inside[:8]
            for i in inside:
                kind = verbs.get(info.word(i).lower())
                if kind:
                    found.setdefault(i, kind)
                    break
    tags = []
    for i, kind in sorted(found.items()):
        start, end = info.tokens[i]
        adverb = None
        for j, edge in ((i + 1, 'after'), (i - 1, 'before')):
            if 0 <= j < len(info.tokens):
                s, e = info.tokens[j]
                between = text[end:s] if edge == 'after' else text[e:start]
                if re.fullmatch(r'[\s,]*', between) and is_adverb(text[s:e], exceptions):
                    adverb = (s, e)
                    break
        tags.append(
            {'verb': text[start:end].lower(), 'kind': kind, 'start': start, 'end': end, 'adverb': adverb}
        )
    return tags


def chapters_of(doc):
    """Chapters are the sections of top-level headings; text before the first heading is its own."""
    blocks = doc.get('blocks', [])
    levels = [b.get('attrs', {}).get('level', 1) for b in blocks if b.get('type') == 'heading']
    levels = [lv for lv in levels if type(lv) is int]
    top = min(levels) if levels else 1
    result, current = [], None
    for block in blocks:
        level = block.get('attrs', {}).get('level', 1)
        if block.get('type') == 'heading' and level == top:
            current = {'id': block['id'], 'heading': block, 'blocks': []}
            result.append(current)
            continue
        if current is None:
            current = {'id': '_front' if levels else '_document', 'heading': None, 'blocks': []}
            result.append(current)
        current['blocks'].append(block)
    return result


class _Index:
    """Analysis cache for one check run; built lazily so scoped and reused runs stay cheap."""

    def __init__(self, ctx):
        self.ctx, self.doc = ctx, ctx.doc
        self.blocks = self.doc.get('blocks', [])
        self.order = {b['id']: i for i, b in enumerate(self.blocks)}
        self._infos, self._settings, self._chapters = {}, {}, None

    def info(self, block):
        if block['id'] not in self._infos:
            from .checks import _language

            info = None
            if block.get('type') == 'paragraph' and block.get('text', '').strip():
                language = _language(self.doc, block)
                lists = wordlist('fiction', language)
                if lists is not None:
                    info = _Info(block, language, lists)
                    if info.intentional:
                        info = None
            self._infos[block['id']] = info
        return self._infos[block['id']]

    def settings(self, identity):
        config, fingerprint = self.ctx.config_for(identity)
        if fingerprint not in self._settings:
            self._settings[fingerprint] = {}
        cache = self._settings[fingerprint]
        if 'value' not in cache:
            cache['value'] = _Settings(config, self.ctx.level(identity))
        return cache['value']

    def chapters(self):
        if self._chapters is None:
            self._chapters = chapters_of(self.doc)
        return self._chapters

    def reach(self, block, settings):
        """Preceding paragraphs of the same section the enabled window rules can look back into."""
        need = settings.quotas()
        if not any(need.values()):
            return []
        have = dict.fromkeys(need, 0)
        chain, info = [], self.info(block)
        for position in range(self.order[block['id']] - 1, -1, -1):
            earlier = self.blocks[position]
            if earlier.get('type') == 'heading':
                break
            before = self.info(earlier)
            if before is None:
                continue
            if before.language != info.language:
                break
            chain.append(before)
            have['words'] += len(before.tokens)
            have['sentences'] += len(before.sentences)
            have['paragraphs'] += 1
            if all(have[k] >= need[k] for k in need):
                break
        chain.reverse()
        return chain


def index(ctx):
    if getattr(ctx, '_fiction', None) is None:
        ctx._fiction = _Index(ctx)
    return ctx._fiction


# Dependency units ---------------------------------------------------------------------
def _digest(info):
    return (info.id, info.text, info.language, info.intentional)


def unit_fingerprints(ctx):
    """Fingerprints of the windowed, chapter and names units that apply to the selected scope."""
    idx, result = index(ctx), {}
    selected = ctx.selection['blocks']
    name_blocks = []
    for block in idx.blocks:
        info = idx.info(block)
        if info is None:
            continue
        settings = idx.settings(block['id'])
        if 'fiction.name_spelling' in settings.active:
            name_blocks.append((info, ctx.config_for(block['id'])[1]))
        if (selected is not None and block['id'] not in selected) or not settings.windowed:
            continue
        result['fiction:window:' + block['id']] = object_hash(
            {
                'v': VERSION,
                'env': ctx.env,
                'profile': ctx.config_for(block['id'])[1],
                'block': _digest(info),
                'reach': [_digest(i) for i in idx.reach(block, settings)],
            }
        )
    for chapter in idx.chapters():
        infos = [i for i in (idx.info(b) for b in chapter['blocks']) if i is not None]
        owner = chapter['heading']['id'] if chapter['heading'] else infos[0].id if infos else None
        if owner is None or not infos or not idx.settings(owner).active & set(CHAPTER_RULES):
            continue
        members = {chapter['id'], *(i.id for i in infos)}
        if selected is not None and not members & selected:
            continue
        result['context:fiction_chapter:' + chapter['id']] = object_hash(
            {
                'v': VERSION,
                'env': ctx.env,
                'profile': ctx.config_for(owner)[1],
                'heading': (chapter['heading'] or {}).get('text'),
                'blocks': [_digest(i) for i in infos],
            }
        )
    if name_blocks:
        result['context:fiction_names'] = object_hash(
            {
                'v': VERSION,
                'env': ctx.env,
                'blocks': [(_digest(i), fp) for i, fp in name_blocks],
                'canonical': canonical_names(ctx.doc),
            }
        )
    return result


def evaluate(ctx, sink, key):
    idx = index(ctx)
    if key.startswith('fiction:window:'):
        block = next(b for b in idx.blocks if b['id'] == key[len('fiction:window:') :])
        _window(idx, sink, block)
    elif key.startswith('context:fiction_chapter:'):
        identity = key[len('context:fiction_chapter:') :]
        _chapter(idx, sink, next(c for c in idx.chapters() if c['id'] == identity))
    else:
        _names(idx, sink)


def _emit(sink, info, rule, message, start, end, measured, operations=None):
    sink.add(
        rule,
        FAMILY,
        RULES[rule][4],
        message,
        info.id,
        start,
        end,
        info.text[start:end],
        operations or [],
        measured=measured,
    )


def _phrase_pattern(items):
    ordered = sorted({i.lower() for i in items}, key=lambda i: (-len(i), i))
    return re.compile(
        r'\b(?:' + '|'.join(re.escape(i).replace(r'\ ', r'\s+') for i in ordered) + r')\b', re.IGNORECASE
    )


_PATTERNS = {}


def _pattern(name, language, items):
    key = (name, language)
    if key not in _PATTERNS:
        _PATTERNS[key] = _phrase_pattern(items)
    return _PATTERNS[key]


# Paragraph-local rules ------------------------------------------------------------------
def evaluate_block(ctx, sink, block):
    """Rules that read a single paragraph; called from the block unit."""
    idx = index(ctx)
    if block.get('type') != 'paragraph' or not block.get('text', '').strip():
        return
    from .checks import _intentional, _language

    if _intentional(block):
        return
    info = idx.info(block)
    if info is None:
        language = _language(ctx.doc, block)
        sink.cover(
            'fiction',
            'fiction.language',
            'unmeasurable',
            f'No fiction word list for language {language or "unspecified"}.',
            language=language,
        )
        return
    settings = idx.settings(block['id'])
    active, t = settings.active, settings.thresholds
    lists = info.lists
    if 'fiction.filter_words' in active:
        pattern = _pattern('filter', info.language, lists.get('filter_words', ()))
        hits = [m for m in pattern.finditer(info.text) if info.narrative(m.start(), m.end())]
        if len(hits) >= t['filter_per_paragraph']:
            for m in hits:
                _emit(
                    sink,
                    info,
                    'fiction.filter_words',
                    f'"{m.group().lower()}" filters the scene through a character; consider showing it directly.',
                    m.start(),
                    m.end(),
                    {
                        'word': m.group().lower(),
                        'in_paragraph': len(hits),
                        'threshold': t['filter_per_paragraph'],
                    },
                )
    for tag in info.tags:
        if tag['kind'] == 'bookisms' and 'fiction.tag_bookism' in active:
            _emit(
                sink,
                info,
                'fiction.tag_bookism',
                f'"{tag["verb"]}" is an ornate speech tag; "said" or an action beat is less distracting.',
                tag['start'],
                tag['end'],
                {'word': tag['verb']},
            )
        if tag['adverb'] and 'fiction.tag_adverb' in active:
            a, b = tag['adverb']
            _emit(
                sink,
                info,
                'fiction.tag_adverb',
                f'The adverb "{info.text[a:b]}" modifies a speech tag; let the dialogue carry the manner.',
                a,
                b,
                {'word': info.text[a:b].lower(), 'tag': tag['verb']},
            )
    if 'fiction.paragraph_length' in active and len(info.tokens) > t['paragraph_max_words']:
        _emit(
            sink,
            info,
            'fiction.paragraph_length',
            'Paragraph is very long; consider breaking it.',
            0,
            len(info.text),
            {'words': len(info.tokens), 'limit': t['paragraph_max_words']},
        )
    if 'fiction.show_tell' in active:
        telling = lists.get('telling', {})
        if telling.get('linking_verbs') and telling.get('emotions'):
            pattern = re.compile(
                r'\b(?:'
                + '|'.join(map(re.escape, telling['linking_verbs']))
                + r')\s+(?:(?:'
                + '|'.join(map(re.escape, telling.get('intensifiers', ['\0'])))
                + r')\s+)*('
                + '|'.join(map(re.escape, telling['emotions']))
                + r')\b',
                re.IGNORECASE,
            )
            for m in pattern.finditer(info.text):
                if info.narrative(m.start(), m.end()):
                    _emit(
                        sink,
                        info,
                        'fiction.show_tell',
                        f'Names the emotion "{m.group(1).lower()}" outright; consider showing it through action or detail.',
                        m.start(),
                        m.end(),
                        {'word': m.group(1).lower()},
                    )


# Windowed rules --------------------------------------------------------------------------
def _window(idx, sink, block):
    info = idx.info(block)
    if info is None:
        return
    settings = idx.settings(block['id'])
    chain = idx.reach(block, settings) + [info]
    last = len(chain) - 1
    active, t = settings.active, settings.thresholds
    lists = info.lists
    stop = frozenset(lists.get('stopwords', ()))
    # One token stream across the window: (paragraph, start, end, word, sentence, opener).
    stream = []
    for k, item in enumerate(chain):
        for n, (s, e) in enumerate(item.tokens):
            stream.append((k, s, e, item.text[s:e], (k, item.sentence_of[n]), s in item.openers))
    if 'fiction.word_echo' in active:
        _word_echo(sink, info, chain, stream, last, stop, t)
    if 'fiction.phrase_echo' in active:
        _phrase_echo(sink, info, stream, last, stop, t)
    if 'fiction.sentence_opening' in active:
        _openings(sink, info, chain, last, t)
    if 'fiction.sentence_rhythm' in active:
        _sentence_rhythm(sink, info, chain, last, t)
    if 'fiction.paragraph_rhythm' in active:
        _paragraph_rhythm(sink, info, chain, t)
    if 'fiction.tag_repetition' in active:
        _tag_repetition(sink, info, chain, last, t)


_KNOWN = {}


def _is_word(language, lower):
    """Whether the local dictionary knows a word; True when no dictionary is installed."""
    from .checks import _speller

    key = (language, lower)
    if key not in _KNOWN:
        checker = _speller(language)
        _KNOWN[key] = checker is None or not checker.unknown([lower])
    return _KNOWN[key]


on_refresh(_KNOWN.clear)
on_refresh(_PATTERNS.clear)


def _word_echo(sink, info, chain, stream, last, stop, t):
    seen = {}
    for n, (k, s, e, word, _, opener) in enumerate(stream):
        lower = word.lower()
        if len(lower) < t['echo_min_length'] or lower in stop:
            continue
        # A capital inside a sentence marks a name; at a sentence start an unknown word is one too.
        if word[0].isupper() and (not opener or not _is_word(info.language, lower)):
            continue
        key = lower[:-1] if lower.endswith('s') and not lower.endswith('ss') and len(lower) > 5 else lower
        earlier = seen.get(key)
        seen[key] = n
        if k == last and earlier is not None and n - earlier <= t['echo_window_words']:
            ek, es, ee = stream[earlier][:3]
            _emit(
                sink,
                info,
                'fiction.word_echo',
                f'"{word}" echoes "{chain[ek].text[es:ee]}" {n - earlier} words earlier.',
                s,
                e,
                {'word': lower, 'distance': n - earlier, 'window': t['echo_window_words']},
            )


def _phrase_echo(sink, info, stream, last, stop, t):
    seen, spans = {}, []
    for n in range(2, len(stream)):
        trio = stream[n - 2 : n + 1]
        if len({x[4] for x in trio}) != 1:
            continue
        lower = tuple(x[3].lower() for x in trio)
        if sum(w not in stop for w in lower) < 2:
            continue
        earlier = seen.get(lower)
        if (
            earlier is not None
            and 3 <= n - earlier <= t['phrase_window_words']
            and all(x[0] == last for x in trio)
        ):
            spans.append((trio[0][1], trio[2][2], n - earlier))
        seen[lower] = n
    merged = []
    for start, end, distance in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1], merged[-1][2] = max(end, merged[-1][1]), min(distance, merged[-1][2])
        else:
            merged.append([start, end, distance])
    for start, end, distance in merged:
        phrase = info.text[start:end]
        _emit(
            sink,
            info,
            'fiction.phrase_echo',
            f'The phrase "{phrase}" repeats within {t["phrase_window_words"]} words.',
            start,
            end,
            {'phrase': phrase.lower(), 'distance': distance, 'window': t['phrase_window_words']},
        )


def _openings(sink, info, chain, last, t):
    run = t['opening_run']
    items = []
    for k, item in enumerate(chain):
        for position, (a, b) in enumerate(item.sentences):
            first = item.first_token[position]
            word = None
            if first is not None and item.text[a] not in '“"«‘':
                word = item.word(first).lower()
            items.append((k, a, b, word, first))

    def same(j):
        if j < run - 1:
            return False
        group = {items[x][3] for x in range(j - run + 1, j + 1)}
        return len(group) == 1 and None not in group

    for j, (k, a, b, word, first) in enumerate(items):
        if k == last and same(j) and not same(j - 1):
            s, e = info.tokens[first]
            _emit(
                sink,
                info,
                'fiction.sentence_opening',
                f'{run} consecutive sentences begin with "{info.text[s:e]}".',
                s,
                e,
                {'word': word, 'run': run},
            )


def _sentence_rhythm(sink, info, chain, last, t):
    run = t['rhythm_run']
    items = [
        (k, a, b, n) for k, item in enumerate(chain) for (a, b), n in zip(item.sentences, item.sentence_words)
    ]

    def uniform(j):
        if j < run - 1:
            return False
        lengths = [items[x][3] for x in range(j - run + 1, j + 1)]
        return (
            max(lengths) - min(lengths) <= t['rhythm_spread_words']
            and sum(lengths) / run >= t['rhythm_min_mean_words']
        )

    for j, (k, a, b, n) in enumerate(items):
        if k == last and uniform(j) and not uniform(j - 1):
            lengths = [items[x][3] for x in range(j - run + 1, j + 1)]
            _emit(
                sink,
                info,
                'fiction.sentence_rhythm',
                f'{run} sentences in a row are nearly the same length ({min(lengths)}-{max(lengths)} words).',
                a,
                b,
                {'run': run, 'min_words': min(lengths), 'max_words': max(lengths)},
            )


def _paragraph_rhythm(sink, info, chain, t):
    run = t['paragraph_rhythm_run']
    counts = [len(item.tokens) for item in chain]

    def uniform(j):
        if j < run - 1:
            return False
        group = counts[j - run + 1 : j + 1]
        mean = sum(group) / run
        return mean >= t['paragraph_rhythm_min_words'] and max(group) - min(group) <= (
            t['paragraph_rhythm_tolerance'] * mean
        )

    j = len(chain) - 1
    if uniform(j) and not uniform(j - 1):
        group = counts[j - run + 1 :]
        _emit(
            sink,
            info,
            'fiction.paragraph_rhythm',
            f'{run} paragraphs in a row have nearly the same length ({min(group)}-{max(group)} words).',
            0,
            len(info.text),
            {'run': run, 'min_words': min(group), 'max_words': max(group)},
        )


def _tag_repetition(sink, info, chain, last, t):
    window, repeat = t['tag_window_words'], t['tag_repeat']
    tags, offset = [], 0
    for k, item in enumerate(chain):
        for tag in item.tags:
            tags.append((k, offset + bisect.bisect_left(item.starts, tag['start']), tag))
        offset += len(item.tokens)
    for j, (k, position, tag) in enumerate(tags):
        if k != last or tag['kind'] == 'plain':
            continue
        recent = [x[2]['verb'] for x in tags[: j + 1] if position - x[1] < window]
        if recent.count(tag['verb']) == repeat:
            _emit(
                sink,
                info,
                'fiction.tag_repetition',
                f'"{tag["verb"]}" tags {repeat} lines of dialogue within {window} words.',
                tag['start'],
                tag['end'],
                {'word': tag['verb'], 'count': repeat, 'window_words': window},
            )


# Chapter rules -----------------------------------------------------------------------------
def _chapter(idx, sink, chapter):
    infos = [i for i in (idx.info(b) for b in chapter['blocks']) if i is not None]
    if not infos:
        return
    heading = chapter['heading']
    owner = heading['id'] if heading else infos[0].id
    settings = idx.settings(owner)
    active, t = settings.active, settings.thresholds
    language = infos[0].language
    infos = [i for i in infos if i.language == language]
    lists = infos[0].lists
    total = sum(len(i.tokens) for i in infos)
    where = (0, len(heading['text'])) if heading and heading.get('text') else (None, None)

    def report(rule, message, measured):
        sink.add(
            rule,
            FAMILY,
            RULES[rule][4],
            message,
            owner,
            where[0],
            where[1],
            heading['text'] if heading else None,
            measured=measured,
        )

    if 'fiction.adverb_density' in active and total >= t['adverb_min_words']:
        exceptions = frozenset(lists.get('adverb_exceptions', ()))
        found = {}
        for item in infos:
            for n in range(len(item.tokens)):
                word = item.word(n).lower()
                if is_adverb(word, exceptions):
                    found[word] = found.get(word, 0) + 1
        rate = 100 * sum(found.values()) / total
        if rate >= t['adverb_per_100_words']:
            top = sorted(found.items(), key=lambda x: (-x[1], x[0]))[:5]
            report(
                'fiction.adverb_density',
                f'{rate:.1f} adverbs per 100 words in this chapter (threshold {t["adverb_per_100_words"]}).',
                {'adverbs': sum(found.values()), 'words': total, 'per_100_words': round(rate, 2), 'top': top},
            )
    if 'fiction.sensory_balance' in active and total >= t['sensory_min_words']:
        counts = sensory_counts(infos, lists)
        hits = sum(counts.values())
        if hits >= t['sensory_min_hits']:
            lead, share = max(sorted(counts.items()), key=lambda x: x[1])[0], max(counts.values()) / hits
            if share >= t['sensory_dominance']:
                missing = sorted(sense for sense, n in counts.items() if n == 0)
                report(
                    'fiction.sensory_balance',
                    f'{round(100 * share)}% of sensory words in this chapter are {lead}; '
                    + (f'no {", ".join(missing)} words.' if missing else 'other senses are scarce.'),
                    {'counts': dict(sorted(counts.items())), 'dominant': lead, 'share': round(share, 2)},
                )


def sensory_counts(infos, lists):
    """Occurrences of each sense's words (with simple inflection) across the given paragraphs."""
    senses = lists.get('sensory', {})
    lookup = {}
    for sense, items in senses.items():
        for word in items:
            lookup.setdefault(word, sense)
    counts = dict.fromkeys(sorted(senses), 0)
    for item in infos:
        for n in range(len(item.tokens)):
            word = item.word(n).lower()
            for form in (word, word[:-1], word[:-2], word[:-3], word[:-3] + 'e'):
                if form in lookup:
                    counts[lookup[form]] += 1
                    break
    return counts


# Invented-name spelling --------------------------------------------------------------------
def _distance(a, b, limit):
    """Optimal-string-alignment edit distance (adjacent transposition counts one), capped."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            row[j] = min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[-2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def _names(idx, sink):
    from .checks import _replace_ops, _speller

    found, language = {}, None
    for block in idx.blocks:
        info = idx.info(block)
        if info is None or 'fiction.name_spelling' not in idx.settings(block['id']).active:
            continue
        language = language or info.language
        for s, e in info.tokens:
            word = info.text[s:e]
            if word.endswith(("'s", '’s')):
                word, e = word[:-2], e - 2
            if len(word) >= 4 and word[0].isupper() and word.isalpha() and not word[1:].isupper():
                found.setdefault(word, []).append((info, s, e))
    if not found:
        return
    checker = _speller(language)
    if checker is None:
        sink.cover(
            'fiction',
            'fiction.name_spelling',
            'unmeasurable',
            f'No installed spelling dictionary for {language or "unspecified"}, so invented names '
            'cannot be told from dictionary words.',
            language=language,
        )
        return
    unknown = checker.unknown([w.lower() for w in found])
    names = {w: places for w, places in found.items() if w.lower() in unknown}
    external = canonical_names(idx.doc)
    established = []
    for name in sorted(names, key=lambda n: (-len(names[n]), n)):
        if name in external:
            continue
        count = len(names[name])
        limits = idx.settings(names[name][0][0].id).thresholds
        reach = limits['name_max_distance']
        match = next(
            ((o, None) for o in external if o[0] == name[0] and _distance(name, o, reach) <= reach), None
        )
        if match is None:
            match = next(
                (
                    (o, n)
                    for o, n in established
                    if o[0] == name[0]
                    and _distance(name, o, reach) <= reach
                    and count <= limits['name_variant_max_count']
                    and n >= limits['name_majority_ratio'] * count
                ),
                None,
            )
        if match is None:
            established.append((name, count))
            continue
        other, other_count = match
        detail = 'a known name' if other_count is None else f'used {other_count} times, this spelling {count}'
        for info, s, e in names[name]:
            _emit(
                sink,
                info,
                'fiction.name_spelling',
                f'"{name}" may be a misspelling of "{other}" ({detail}).',
                s,
                e,
                {
                    'word': name,
                    'suggested': other,
                    'occurrences': count,
                    'canonical_occurrences': other_count,
                },
                _replace_ops(info.id, info.text, s, e, other),
            )


# Pacing data ---------------------------------------------------------------------------------
def pacing_series(doc, *, target=None):
    """Per-chapter pacing as data: one point per paragraph plus a summary, for graphs and reports.

    ``target`` limits the result to the chapter with that heading ID.
    """
    from .checks import _intentional, _language

    result = []
    for chapter in chapters_of(doc):
        if target is not None and chapter['id'] != target:
            continue
        points, cumulative = [], 0
        for block in chapter['blocks']:
            text = block.get('text', '') if block.get('type') == 'paragraph' else ''
            if not text.strip() or _intentional(block):
                continue
            lists = wordlist('fiction', _language(doc, block)) or {}
            spans = sentences(text, frozenset(lists.get('abbreviations', ())))
            tokens = words(text)
            if not tokens:
                continue
            quoted = sum(1 for s, _ in tokens if in_spans(s, dialogue_spans(text)))
            cumulative += len(tokens)
            points.append(
                {
                    'id': block['id'],
                    'words': len(tokens),
                    'sentences': len(spans),
                    'mean_sentence_words': round(len(tokens) / max(1, len(spans)), 2),
                    'dialogue_ratio': round(quoted / len(tokens), 3),
                    'cumulative_words': cumulative,
                }
            )
        lengths = [p['words'] for p in points]
        sentence_means = [p['mean_sentence_words'] for p in points]
        result.append(
            {
                'id': chapter['id'],
                'title': (chapter['heading'] or {}).get('text', ''),
                'series': points,
                'summary': {
                    'paragraphs': len(points),
                    'words': sum(lengths),
                    'mean_paragraph_words': round(sum(lengths) / len(lengths), 2) if lengths else 0,
                    'mean_sentence_words': round(sum(sentence_means) / len(sentence_means), 2)
                    if sentence_means
                    else 0,
                    'dialogue_ratio': round(
                        sum(p['dialogue_ratio'] * p['words'] for p in points) / max(1, sum(lengths)), 3
                    ),
                },
            }
        )
    return result
