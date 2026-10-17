"""Per-request routing for the assistant: routes R0–R4 chosen by visible rules (context engine 8.3).

Every assistant request goes through :func:`decide`, which picks a route with deterministic rules
and says why, and :func:`gather`, which collects that route's context and escalates when the rules
say so. :mod:`byax.context_assembly` turns the gathered items into the request (budgets, layout) and
the "What the AI saw" inspector payload.

Rules, in the order they run (each one is recorded in the decision):

1. **Privacy** runs before every other rule. A ``local_only`` library either stays out of a request
   to a remote model (``privacy='exclude'``, the default) or switches the request to a configured
   local model (``privacy='local'``). The decision says which, and nothing can override it.
2. **Override**: a route the author picked replaces the suggestion (the suggestion is kept).
3. **Structure** questions (word counts, outline, statuses) are answered from binder and outline
   data without retrieval: route R2, variant ``structure`` (R-meta).
4. **Everywhere** wording ("every place", "throughout", "ever", "consistent") needs several
   searches: R3.
5. **Whole-book** wording ("whole book", "act 2", "themes", "pacing") and analysis of a named
   chapter or scene: R4 (scoped to that chapter or scene when one is named).
6. With a **selection**: a source request or a reference elsewhere goes to R2; an edit, grammar or
   tone request stays on the selection (R0); anything else is local (R1), escalating to R2 when the
   request names an entity that the selection and nearby blocks do not mention.
7. Without a selection: source requests and questions naming an entity or a chapter go to R2;
   continuation is local (R1, around the cursor); edits and general questions use the whole
   document when it fits the model's real context (R4), otherwise R1 or R2.

Context-size-aware thresholds (R-rev): the full document is used only below a model-dependent
threshold (about 20,000 tokens for local models, half of a verified context window for providers
whose window is known), and top-k scales from 4 at 8K tokens or less to 20 at 32K or more.
Retrieval is scoped and ranked by structure and codex metadata (R-meta): a named chapter or the
selection's scene filters the manuscript search, and passages from chapters where the requested
point-of-view character or tag appears rank first.

Escalation while gathering: R1 → R2 (unknown entity), R2 → R3 (the sufficiency check fails), R3 → R4
or abstain after verified absence (Q14). The R3 loop has read-only tools only (``search``,
``semantic_search``, ``get_outline``, ``read_range``, ``get_entity``): it never combines private
data and untrusted content with an outbound or writing tool. Learned routing is deliberately not
implemented: there are no route-accuracy labels from real use yet.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .errors import ByaxError

Doc = dict[str, Any]
ROUTES = ('R0', 'R1', 'R2', 'R3', 'R4')
ROUTE_NAMES = {'R0': 'selection', 'R1': 'local', 'R2': 'lookup', 'R3': 'multi-hop', 'R4': 'full document'}
ROUTE_LABELS = {
    'R0': 'Selection only',
    'R1': 'Selection and nearby text',
    'R2': 'Look it up',
    'R3': 'Search in several steps',
    'R4': 'Whole document',
}
PRIVACY_MODES = ('exclude', 'local')
READ_ONLY_TOOLS = ('search', 'semantic_search', 'get_outline', 'read_range', 'get_entity')

# R-rev thresholds. Open WebUI's tuning guidance: full context beats retrieval under about 20K
# tokens on local models; top-k 3–5 at 8K or less and 15–25 at 32K or more.
LOCAL_FULL_CONTEXT_TOKENS = 20_000
VERIFIED_WINDOW_SHARE = 0.5
SMALL_WINDOW = 8192
LARGE_WINDOW = 32768
TOP_K_SMALL = 4
TOP_K_LARGE = 20
RADIUS_SMALL = 2
RADIUS_LARGE = 5
DEFAULT_WINDOW = 8192
R3_MAX_CALLS = 12
SUFFICIENT_COVERAGE = 0.5

LEARNED_ROUTING = (
    'not used: a learned router needs route-accuracy labels from real use, which do not exist yet'
)


def _rx(pattern):
    return re.compile(pattern, re.IGNORECASE)


def _words(text):
    return text.split()


_POLITE = _rx(
    r'^(?:(?:please|ok(?:ay)?|now|so|hey)[,\s]+)*(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|'
    r"i\s+(?:want|need|would\s+like|'d\s+like)\s+(?:you\s+)?to\s+|let'?s\s+|help\s+me\s+|try\s+to\s+)?"
)
_QUESTION_START = _rx(
    r'^(?:what|who|whom|whose|where|when|why|how|which|is|are|was|were|does|do|did|can|could|should|'
    r'would|will|has|have|had|am|isn\'t|doesn\'t|didn\'t)\b'
)
EDIT_VERBS = _words(
    'tighten fix rewrite reword rephrase shorten trim cut condense expand lengthen polish edit '
    'proofread correct improve simplify clarify smooth punctuate translate paraphrase change '
    'replace remove delete make turn convert split merge vary soften strengthen sharpen tidy '
    'clean redo adjust tweak streamline reduce capitalize swap restructure reorder punch fill'
)
TASK_VERBS = EDIT_VERBS + _words(
    'continue summarize summarise list find show tell give check compare assess review critique '
    'analyze analyse evaluate describe explain write draft suggest brainstorm outline count '
    'track trace identify highlight locate search look name recap propose note flag spot'
)
_EDIT_TOPICS = _rx(
    r'\b(?:grammar|grammatical(?:ly)?|typos?|spelling|misspell\w*|punctuation|commas?|tense|tone|wording|'
    r'word choice|passive(?: voice)?|adverbs?|phrasing|clunky|awkward|rhythm|clich[eé]s?|redundan\w*|'
    r'synonyms?|repetiti\w*|run-on|fragment|readab\w*|flows?|formal|informal|concise|wordy|verbose)\b'
)
_CONTINUE = _rx(
    r'\bcontinue\b|\bkeep going\b|\bcarry on\b|\bwhat happens next\b|\bwrite the next\b|'
    r'\bnext (?:line|paragraph|sentence|beat|bit)\b|\bfinish (?:this|the) (?:scene|paragraph|sentence|thought)\b|'
    r'\bpick up (?:from|where)\b|\bgo on\b'
)
_STRUCTURE = _rx(
    r'\bword ?counts?\b|\bhow many (?:words|chapters|scenes|parts|pages|sections)\b|\bhow long is\b|'
    r'\b(?:longest|shortest) (?:chapter|scene|part)\b|\boutline\b|\btable of contents\b|\bstatus(?:es)?\b|'
    r'\bword targets?\b|\btargets?\b|\bbinder\b|\bsynops[ie]s\b|\bchapter (?:titles|names|headings)\b|'
    r'\blist (?:the |all |my )?(?:chapters|scenes|parts|headings|sections)\b|'
    r'\bwhich (?:chapters|scenes) (?:are|have) (?:marked|still|not|been|done|drafted|final)\b|'
    r'\bin what order\b|\border of (?:the )?(?:chapters|scenes)\b|\bmarked (?:as )?(?:draft|final|revised|to do)\b|'
    r'\b(?:still )?to do\b(?= (?:chapters|scenes))|\bhow far along\b'
)
_SOURCES = _rx(
    r'\bsources?\b|\blibrar(?:y|ies)\b|\bresearch\b|\bcit(?:e|es|ing|ation|ations)\b|\breferences?\b|'
    r'\bevidence\b|\baccording to\b|\bmy notes\b|\bbibliograph\w*|\bfootnotes?\b|\bpapers?\b|\barticles?\b|'
    r'\bhistorical(?:ly)?\b|\bfact[- ]check\w*\b'
)
_WHOLE = _rx(
    r'\bwhole (?:book|manuscript|novel|story|draft|thing)\b|\bentire (?:book|manuscript|novel|story|draft)\b|'
    r'\b(?:the |my )?(?:book|novel|manuscript|story) as a whole\b|\boverall\b|'
    r'\b(?:summari[sz]e|recap|assess|critique|review|evaluate|analy[sz]e|beta[- ]read) (?:the |my |this )?'
    r'(?:book|novel|manuscript|story|draft)\b|\bact (?:\d|one|two|three|i{1,3})\b|\b(?:first|second) half\b|'
    r'\bthemes?\b|\b(?:character|story|plot|emotional) arcs?\b|\bpacing\b|\bplot holes?\b|\bblurb\b|'
    r'\blogline\b|\bquery letter\b|\bback[- ]cover\b|\bfrom (?:start|beginning) to (?:finish|end)\b|'
    r'\bthe ending\b|\bstructure of (?:the |my )?(?:book|novel|story|manuscript)\b|\bmain plot\b'
)
_EVERYWHERE = _rx(
    r'\beverywhere\b|\bevery (?:place|time|scene|chapter|mention|instance|occurrence|reference)\b|'
    r'\beach (?:time|place|scene|chapter|mention)\b|\ball (?:the )?(?:places|times|scenes|mentions|instances|'
    r'occurrences|moments|references)\b|\bthroughout\b|\bacross (?:the )?(?:book|manuscript|chapters|novel|story|scenes)\b|'
    r'\btrace\b|\btrack\b|\btimeline\b|\b(?:list|find|show) (?:all|every)\b|'
    r'\bhow (?:does|do|did) .{1,40}? (?:change|evolve|develop|grow)\b|\bcompare\b|\bcontradict\w*|'
    r'\binconsisten\w*|\bcontinuity\b|\bconsistent(?:ly)?\b|\bever\b|\bwhenever\b|\bin which (?:chapters|scenes)\b|'
    r'\bhow often\b|\bhow many times\b|'
    r'\bwhich (?:chapters|scenes)\b[^.?!]{0,60}?\b(?:appears?|mentions?|features?|shows? up)\b'
)
_ANALYSIS = _rx(
    r'\bsummari[sz]e\b|\brecap\b|\bassess\b|\bcritique\b|\breview\b|\bevaluate\b|\banaly[sz]e\b|\bfeedback\b|'
    r'\bpacing\b|\bstrengths?\b|\bweakness\w*\b|\bwhat works\b|\bbeta[- ]read\b|\btension\b|\bstakes\b|'
    r'\bnotes on\b|\bthoughts on\b|\bhow (?:does|is) (?:this|the) (?:chapter|scene) (?:work|read|land|flow)\b|'
    r'\bwhat happens in\b|\bsummary of\b|\bboring\b|\bdrag\w*\b'
)
_REFERENCE_ELSEWHERE = _rx(
    r'\bearlier\b|\bpreviously\b|\belsewhere\b|\bconsistent with\b|\bas (?:i|we) (?:described|wrote|said)\b|'
    r'\bthe way .{1,40}? (?:described|said|wrote)\b|\bin (?:the )?(?:first|last|previous|next|earlier|later) chapter\b|'
    r'\b(?:in|from) chapter\b'
)
_SELECTION_WORDS = _rx(
    r'\bthis (?:paragraph|sentence|line|passage|bit|text|selection|dialogue|exchange|description|part)\b|'
    r'\bthe selection\b|\bthe selected\b|\bthese (?:lines|sentences|paragraphs|words)\b|\bhere\b|\bthis\b|\bit\b'
)
_LOCAL_SCOPE = _rx(r'\b(?:this|the current) (scene|chapter|section)\b')
_NUMBERS = {
    word: index
    for index, word in enumerate(
        _words(
            'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen '
            'sixteen seventeen eighteen nineteen twenty'
        )
    )
}
_ORDINALS = {
    word: index
    for index, word in enumerate(
        _words('zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth')
    )
}
_CHAPTER = _rx(
    r'\b(chapter|scene|part)s?\s+(\d{1,3}|[ivxlc]{1,7}|' + '|'.join(_NUMBERS) + r')\b|'
    r'\bthe (' + '|'.join(list(_ORDINALS)[1:]) + r'|last|final|opening|closing) (chapter|scene|part)\b'
)
_ROMAN = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100}
COMMON_CAPITALS = frozenset(
    _words(
        "i i'm i've i'd chapter chapters act part scene book novel monday tuesday wednesday thursday friday "
        'saturday sunday january february march april may june july august september october november december '
        'english french spanish german italian latin greek russian chinese japanese portuguese dutch '
        'mr mrs ms dr sir lady lord god ok ai pov'
    )
)


def _roman(text):
    total, previous = 0, 0
    for char in reversed(text.lower()):
        value = _ROMAN.get(char, 0)
        total = total - value if value < previous else total + value
        previous = max(previous, value)
    return total


# --- signals ------------------------------------------------------------------------------------


def phrasing(request: str) -> str:
    """``imperative``, ``interrogative`` or ``statement`` from the opening words and final mark."""
    text = ' '.join(request.split())
    rest = _POLITE.sub('', text, count=1)
    first = (re.findall(r"[\w'-]+", rest.lower()) or [''])[0]
    if rest != text and first in TASK_VERBS:
        return 'imperative'  # "Can you tighten this?" is a polite instruction
    if text.endswith('?') or _QUESTION_START.match(rest):
        return 'interrogative'
    if first in TASK_VERBS:
        return 'imperative'
    return 'statement'


def _first_verb(request):
    rest = _POLITE.sub('', ' '.join(request.split()), count=1)
    return (re.findall(r"[\w'-]+", rest.lower()) or [''])[0]


def _chapters(doc):
    """Chapter-level sections in order: binder chapters, else top-level binder items, else headings."""
    from . import project

    if isinstance(doc.get('project'), dict):
        rows = project.items(doc)
        chosen = [r for r in rows if r['kind'] == 'chapter'] or [
            r for r in rows if r['parent'] is None and r['kind'] != 'note'
        ]
        return [{'id': r['id'], 'heading': r['heading'], 'title': r['title'], 'binder': True} for r in chosen]
    headings = [b for b in doc.get('blocks', []) if b.get('type') == 'heading']
    if not headings:
        return []
    top = min(b.get('attrs', {}).get('level', 1) for b in headings)
    return [
        {'id': b['id'], 'heading': b['id'], 'title': b.get('text', ''), 'binder': False}
        for b in headings
        if b.get('attrs', {}).get('level', 1) == top
    ]


def _scenes(doc):
    from . import project

    if isinstance(doc.get('project'), dict):
        return [
            {'id': r['id'], 'heading': r['heading'], 'title': r['title'], 'binder': True}
            for r in project.items(doc)
            if r['kind'] == 'scene'
        ]
    return []


def section_blocks(doc: Doc, heading_id: str) -> list[str]:
    """Block IDs of the section a heading opens (up to the next heading of the same or a higher level)."""
    blocks = doc.get('blocks', [])
    ids = [b['id'] for b in blocks]
    if heading_id not in ids:
        return []
    start = ids.index(heading_id)
    level = blocks[start].get('attrs', {}).get('level', 1)
    end = next(
        (
            i
            for i in range(start + 1, len(blocks))
            if blocks[i].get('type') == 'heading' and blocks[i].get('attrs', {}).get('level', 1) <= level
        ),
        len(blocks),
    )
    return ids[start:end]


def _containing(doc, block_id, kind):
    """The chapter (or scene) section that contains ``block_id``."""
    if not block_id:
        return None
    for section in _scenes(doc) if kind == 'scene' else _chapters(doc):
        if block_id in section_blocks(doc, section['heading']):
            return section
    if kind == 'scene':
        return _containing(doc, block_id, 'chapter')
    return None


def resolve_scope(doc: Doc, request: str, *, anchor: str | None = None) -> dict[str, Any]:
    """Scope words in ``request``: ``book``, ``everywhere``, a numbered or relative chapter or scene."""
    scope: dict[str, Any] = {'kind': None}
    found = _CHAPTER.search(request)
    if found:
        unit = (found.group(1) or found.group(4) or 'chapter').lower()
        raw = (found.group(2) or found.group(3) or '').lower()
        sections = _scenes(doc) if unit == 'scene' else _chapters(doc)
        if raw.isdigit():
            number = int(raw)
        elif raw in _NUMBERS:
            number = _NUMBERS[raw]
        elif raw in _ORDINALS:
            number = _ORDINALS[raw]
        elif raw in ('last', 'final', 'closing'):
            number = len(sections)
        elif raw == 'opening':
            number = 1
        else:
            number = _roman(raw)
        scope = {'kind': unit if unit != 'part' else 'chapter', 'label': found.group(0), 'number': number}
        if 1 <= number <= len(sections):
            section = sections[number - 1]
            scope.update(item=section['id'], heading=section['heading'], title=section['title'])
        else:
            scope['unresolved'] = (
                f'The manuscript has {len(sections)} {unit}(s); "{found.group(0)}" was not found.'
            )
        return scope
    local = _LOCAL_SCOPE.search(request)
    if local:
        unit = local.group(1).lower()
        unit = 'chapter' if unit == 'section' else unit
        section = _containing(doc, anchor, unit)
        scope = {'kind': unit, 'label': local.group(0), 'relative': True}
        if section:
            scope.update(item=section['id'], heading=section['heading'], title=section['title'])
        else:
            scope['unresolved'] = f'No {unit} contains the cursor or selection.'
        return scope
    if _WHOLE.search(request):
        return {'kind': 'book', 'label': _WHOLE.search(request).group(0)}
    if _EVERYWHERE.search(request):
        return {'kind': 'everywhere', 'label': _EVERYWHERE.search(request).group(0)}
    return scope


def _anchor(selection, cursor):
    if selection:
        return selection[0].get('target')
    return cursor


def _local_window(doc, selection, cursor, radius):
    """Text of the selection (or cursor block) and ``radius`` blocks around it."""
    from .codex import text_objects

    anchor = _anchor(selection, cursor)
    blocks = doc.get('blocks', [])
    ids = [b['id'] for b in blocks]
    owner = {object_id: block_id for object_id, block_id, _ in text_objects(doc)}
    block = owner.get(anchor, anchor)
    if block not in ids:
        return ''
    position = ids.index(block)
    window = blocks[max(0, position - radius) : position + radius + 1]
    from .model import text_of

    return text_of({'blocks': window})


def entities(doc: Doc, request: str) -> dict[str, Any]:
    """Codex entries the request names (AI-visible only) and capitalized names not in the codex."""
    from .codex import mentions
    from .search_query import STOPWORDS

    found = mentions(doc, request, for_ai=True, limit=50)
    codex = [{'id': e['id'], 'name': e['name'], 'kind': e['kind']} for e in found['entries']]
    surfaces = {m['surface'].casefold() for m in found['mentions']}
    covered = {word for surface in surfaces for word in re.findall(r'\w+', surface)}
    names = []
    # A name is a capitalized word that does not open its sentence (the opening word is capitalized anyway).
    for sentence in re.split(r'(?<=[.!?,;:])\s+', re.sub(r'["“][^"”]*["”]', ' ', request)):
        for position, token in enumerate(re.findall(r"\w+(?:'\w+)?", sentence)):
            token = re.sub(r"'s$", '', token)
            folded = token.casefold()
            if position == 0 or not token[:1].isupper() or folded in STOPWORDS:
                continue
            if folded in COMMON_CAPITALS or folded in covered or folded in surfaces:
                continue
            names.append(token)
    return {'codex': codex, 'names': list(dict.fromkeys(names))}


# --- thresholds (R-rev) ----------------------------------------------------------------------------


def _get(provider, key, default=None):
    if provider is None:
        return default
    if isinstance(provider, dict):
        return provider.get(key, default)
    return getattr(provider, key, default)


def model_info(provider) -> dict[str, Any]:
    """Alias, kind, locality and the context window the router may rely on, with where it came from."""
    from .context_assembly import DEFAULT_MAX_CONTEXT_TOKENS, chars_to_tokens

    if provider is None:
        return {
            'alias': None,
            'locality': None,
            'window': None,
            'window_source': 'no model selected',
            'verified_window': False,
        }
    from .providers import provider_locality

    kind = _get(provider, 'kind')
    locality = provider_locality(provider)
    real = _get(provider, 'context_tokens')
    info = {
        'alias': _get(provider, 'alias'),
        'kind': kind,
        'model': _get(provider, 'model'),
        'locality': locality['locality'],
        'label': locality['label'],
    }
    if kind == 'ollama':
        cap = _get(provider, 'max_context_tokens') or DEFAULT_MAX_CONTEXT_TOKENS
        info.update(
            window=min(cap, real) if real else cap,
            window_source='the model reports '
            + str(real)
            + f' tokens; requests use at most {cap} (max_context_tokens)'
            if real
            else f'max_context_tokens ({cap}); the model context is not known yet (Test connection reads it)',
            verified_window=bool(real),
        )
    elif real:
        info.update(window=real, window_source='context_tokens set for this provider', verified_window=True)
    else:
        chars = _get(provider, 'max_input_chars') or 100_000
        info.update(
            window=chars_to_tokens(chars),
            window_source='not verified: the character cap max_input_chars, converted to tokens',
            verified_window=False,
        )
    return info


def thresholds(provider, document_tokens: int | None = None) -> dict[str, Any]:
    """R-rev: the full-document threshold for this model, top-k and the R1 radius."""
    from .context_assembly import response_headroom

    info = model_info(provider)
    window = info['window']
    effective = window or DEFAULT_WINDOW
    if info['locality'] == 'remote' and info['verified_window']:
        full = int(window * VERIFIED_WINDOW_SHARE)
        basis = f'{int(VERIFIED_WINDOW_SHARE * 100)}% of the verified context window'
    else:
        full = LOCAL_FULL_CONTEXT_TOKENS
        basis = (
            'about 20,000 tokens for local models'
            if info['locality'] == 'local'
            else 'about 20,000 tokens: this provider has no verified long context'
        )
        if window:
            full = min(full, max(0, window - response_headroom(window)) * 3 // 4)
            if full < LOCAL_FULL_CONTEXT_TOKENS:
                basis += f', limited to three quarters of the {window}-token window'
    if effective <= SMALL_WINDOW:
        top_k, radius = TOP_K_SMALL, RADIUS_SMALL
    elif effective >= LARGE_WINDOW:
        top_k, radius = TOP_K_LARGE, RADIUS_LARGE
    else:
        share = (effective - SMALL_WINDOW) / (LARGE_WINDOW - SMALL_WINDOW)
        top_k = round(5 + share * 10)
        radius = 3 if share < 0.5 else 4
    result = {
        'source': 'R-rev',
        'window': window,
        'window_source': info['window_source'],
        'full_document_tokens': full,
        'full_document_basis': basis,
        'top_k': top_k,
        'radius': radius,
    }
    if document_tokens is not None:
        result['document_tokens'] = document_tokens
        result['fits_full_document'] = document_tokens <= full
    return result


def _document_tokens(doc, provider=None, block_ids=None):
    from .context_assembly import estimate_tokens
    from .model import text_of

    blocks = doc.get('blocks', [])
    if block_ids is not None:
        wanted = set(block_ids)
        blocks = [b for b in blocks if b['id'] in wanted]
    return estimate_tokens(text_of({'blocks': blocks}), provider)


# --- privacy ------------------------------------------------------------------------------------


def attached_libraries(doc: Doc, registry=None) -> list[dict[str, Any]]:
    """Attached library definitions the document (and its series view) names; unreadable ones skipped."""
    from .libraries import LibraryRegistry

    registry = registry or LibraryRegistry()
    found = []
    for attachment in doc.get('settings', {}).get('libraries', []) or []:
        try:
            library = registry.get(attachment['library_id'])
        except (ByaxError, OSError, KeyError):
            continue
        found.append(dict(library, role=attachment.get('role', 'supplementary')))
    return found


def privacy_rule(provider, libraries, *, mode='exclude', local_providers=None) -> dict[str, Any]:
    """The privacy outcome: which local-only sources are excluded, or the local model used instead."""
    if mode not in PRIVACY_MODES:
        raise ByaxError(
            'invalid_input', 'Privacy mode must be exclude or local', supported=list(PRIVACY_MODES)
        )
    info = model_info(provider)
    local_only = [
        {'library_id': lib['library_id'], 'name': lib.get('name', lib['library_id'])}
        for lib in libraries or []
        if lib.get('retrieval_policy') == 'local_only'
    ]
    outcome: dict[str, Any] = {
        'rule': 'privacy',
        'mode': mode,
        'remote': info['locality'] == 'remote',
        'local_only_sources': local_only,
        'action': 'none',
        'excluded_libraries': [],
        'provider': info['alias'],
    }
    if not local_only or info['locality'] != 'remote':
        outcome['reason'] = (
            'No local-only source is attached.'
            if not local_only
            else 'The model is local, so local-only sources may be used.'
            if info['locality'] == 'local'
            else 'No model selected; local-only sources are kept on this computer.'
        )
        return outcome
    names = ', '.join(s['name'] for s in local_only)
    if mode == 'local':
        local = next((p for p in local_providers or [] if p.get('locality') == 'local'), None)
        if local is not None:
            outcome.update(
                action='force_local_model',
                provider=local['alias'],
                reason=f'{names} is local-only, so this request uses the local model {local["alias"]} instead of '
                f'{info["alias"]}.',
            )
            return outcome
        outcome['reason_detail'] = (
            'No local model is configured, so the local-only sources are left out instead.'
        )
    outcome.update(
        action='exclude_sources',
        excluded_libraries=[s['library_id'] for s in local_only],
        reason=f'{names} is local-only and {info["alias"]} is remote, so it is left out of this request.',
    )
    return outcome


# --- the decision -------------------------------------------------------------------------------


def signals(
    request: str, doc: Doc, *, selection: list | None = None, cursor: str | None = None, radius: int = 2
) -> dict[str, Any]:
    """Every signal the rules read, for the decision record and the inspector."""
    anchor = _anchor(selection, cursor)
    found = entities(doc, request)
    local = _local_window(doc, selection, cursor, radius) if anchor else ''
    folded = local.casefold()
    unknown = []
    if anchor:
        from .codex import effective_entries

        entries = effective_entries(doc)
        for entry in found['codex']:
            surfaces = [entries[entry['id']]['name'], *entries[entry['id']].get('aliases', [])]
            if not any(s.casefold() in folded for s in surfaces):
                unknown.append(entry['name'])
        unknown += [name for name in found['names'] if name.casefold() not in folded]
    verb = _first_verb(request)
    return {
        'selection': bool(selection),
        'cursor': cursor if not selection else None,
        'phrasing': phrasing(request),
        'first_verb': verb,
        'edit': verb in EDIT_VERBS or bool(_EDIT_TOPICS.search(request)),
        'continue': bool(_CONTINUE.search(request)),
        'structure': bool(_STRUCTURE.search(request)),
        'sources': bool(_SOURCES.search(request)),
        'whole': bool(_WHOLE.search(request)),
        'everywhere': bool(_EVERYWHERE.search(request)),
        'analysis': bool(_ANALYSIS.search(request)),
        'elsewhere': bool(_REFERENCE_ELSEWHERE.search(request)),
        'selection_words': bool(_SELECTION_WORDS.search(request)),
        'codex_hits': found['codex'],
        'names': found['names'],
        'unknown_entities': list(dict.fromkeys(unknown)),
        'scope': resolve_scope(doc, request, anchor=anchor),
    }


def _rules(sig, fits):
    """The routing rules in order; returns ``(route, rule, reason, variant, trace)``."""
    trace = []

    def rule(name, fired, detail):
        trace.append({'rule': name, 'fired': bool(fired), 'detail': detail})
        return fired

    scope = sig['scope']
    named_unit = scope.get('kind') in ('chapter', 'scene') and not scope.get('relative')
    if rule('structure', sig['structure'], 'asks about word counts, the outline, statuses or targets'):
        return (
            'R2',
            'structure',
            'A question about the manuscript structure: answered from the binder and outline.',
            'structure',
            trace,
        )
    if rule(
        'everywhere', sig['everywhere'], 'wording that needs every occurrence ("every", "ever", "consistent")'
    ):
        return (
            'R3',
            'everywhere',
            'The request needs every place something occurs, so it searches in several steps.',
            None,
            trace,
        )
    whole = (sig['whole'] or scope.get('kind') == 'book') and scope.get('kind') not in ('chapter', 'scene')
    if rule('whole_book', whole, 'whole-book wording or analysis'):
        return 'R4', 'whole_book', 'The request is about the whole manuscript.', None, trace
    scoped = scope.get('kind') in ('chapter', 'scene') and sig['analysis'] and not sig['sources']
    if rule('scoped_analysis', scoped, 'analysis of a named chapter or scene'):
        return (
            'R4',
            'scoped_analysis',
            f'The request analyses {scope.get("label")}, so that whole section is used.',
            'scoped',
            trace,
        )
    if sig['selection']:
        lookup = (
            sig['sources'] or (sig['elsewhere'] and not sig['continue']) or (named_unit and not sig['edit'])
        )
        if rule('selection_lookup', lookup, 'a source request or a reference to another part of the book'):
            return (
                'R2',
                'selection_lookup',
                'The request asks for sources or for text outside the selection.',
                None,
                trace,
            )
        edit = sig['edit'] and not sig['continue'] and not sig['unknown_entities']
        if rule('selection_edit', edit, 'an edit, grammar, wording or tone request on the selection'):
            return (
                'R0',
                'selection_edit',
                'An edit or wording request about the selected text only.',
                None,
                trace,
            )
        if rule(
            'unknown_entity',
            sig['unknown_entities'],
            'names something the selection and nearby text do not mention',
        ):
            names = ', '.join(sig['unknown_entities'])
            return (
                'R2',
                'unknown_entity',
                f'Escalated from R1: {names} is not in the selection or nearby text.',
                None,
                trace,
            )
        rule('selection_local', True, 'a question or continuation about the selection')
        return 'R1', 'selection_local', 'About the selection and the text around it.', None, trace
    if rule('sources', sig['sources'], 'asks for sources, research or citations'):
        return 'R2', 'sources', 'The request asks about your sources, so it looks them up.', None, trace
    asks = sig['phrasing'] == 'interrogative' or sig['first_verb'] in (
        'find',
        'locate',
        'show',
        'tell',
        'name',
    )
    lookup = asks and (sig['codex_hits'] or sig['names'] or named_unit)
    if rule('entity_lookup', lookup, 'a question naming a character, place or chapter'):
        what = ', '.join([e['name'] for e in sig['codex_hits']] + sig['names']) or scope.get('label', '')
        return 'R2', 'entity_lookup', f'A question about {what}: codex first, then search.', None, trace
    if rule('continue', sig['continue'], 'continuation of the text at the cursor'):
        return 'R1', 'continue', 'Continue from the cursor using the nearby text.', None, trace
    if rule('fits_full_document', fits, "the whole document fits the model's real context"):
        return (
            'R4',
            'fits_full_document',
            'No selection, and the whole document fits this model, so it is used.',
            None,
            trace,
        )
    if sig['edit'] or sig['cursor']:
        rule(
            'cursor_local',
            True,
            'the document is too large for this model; the text around the cursor is used',
        )
        return (
            'R1',
            'cursor_local',
            'The document is too large for this model; select the passage you mean for better results.',
            None,
            trace,
        )
    rule('lookup_default', True, 'the document is too large for this model; search instead')
    return (
        'R2',
        'lookup_default',
        'The document is too large for this model, so the request is answered by searching.',
        None,
        trace,
    )


def decide(
    request: str,
    doc: Doc,
    *,
    selection: list | None = None,
    cursor: str | None = None,
    provider: Any = None,
    override: str | None = None,
    privacy: str = 'exclude',
    libraries: list | None = None,
    local_providers: list | None = None,
    use_libraries: bool = True,
) -> dict[str, Any]:
    """The route for one request, with its reason, every rule that ran and the signals they read.

    ``selection`` is a list of ``{target, start, end}`` ranges with text selected, ``cursor`` the block
    at the cursor when nothing is selected. ``provider`` is the chosen ProviderConfig (or its dict),
    or None to route without a model. ``override`` is a route the author picked. Deterministic: the
    same inputs give the same decision. Nothing is searched or sent.
    """
    if not isinstance(request, str) or not request.strip():
        raise ByaxError('invalid_input', 'A request is required to choose a route')
    if override not in (None, 'auto', *ROUTES):
        raise ByaxError(
            'invalid_input', 'Route override must be auto or one of R0–R4', supported=list(ROUTES)
        )
    selection = [s for s in selection or [] if isinstance(s, dict) and s.get('end', 0) > s.get('start', 0)]
    if libraries is None:
        libraries = attached_libraries(doc) if use_libraries else []
    privacy_outcome = privacy_rule(provider, libraries, mode=privacy, local_providers=local_providers)
    effective = provider
    if privacy_outcome['action'] == 'force_local_model':
        effective = next(p for p in local_providers or [] if p.get('alias') == privacy_outcome['provider'])
    limits = thresholds(effective, _document_tokens(doc, effective))
    sig = signals(request, doc, selection=selection, cursor=cursor, radius=limits['radius'])
    route, rule, reason, variant, trace = _rules(sig, limits['fits_full_document'])
    trace.insert(
        0,
        {
            'rule': 'privacy',
            'fired': privacy_outcome['action'] != 'none',
            'detail': privacy_outcome['reason'],
        },
    )
    escalation = []
    if rule == 'unknown_entity':
        escalation.append({'from': 'R1', 'to': 'R2', 'reason': reason})
    decision: dict[str, Any] = {
        'route': route,
        'name': ROUTE_NAMES[route],
        'label': ROUTE_LABELS[route],
        'reason': reason,
        'rule': rule,
        'variant': variant,
        'suggested': None,
        'override': None,
        'escalations': escalation,
        'rules': trace,
        'signals': sig,
        'privacy': privacy_outcome,
        'model': model_info(effective),
        'thresholds': limits,
        'learned_routing': LEARNED_ROUTING,
    }
    if override not in (None, 'auto') and override != route:
        decision['suggested'] = {'route': route, 'reason': reason, 'rule': rule}
        decision.update(
            route=override,
            name=ROUTE_NAMES[override],
            label=ROUTE_LABELS[override],
            reason=f'Chosen by you. The router suggested {route} ({ROUTE_NAMES[route]}): {reason}',
            rule='override',
            variant='scoped' if override == 'R4' and sig['scope'].get('heading') else None,
            override={'requested': override},
            escalations=[],
        )
        trace.append({'rule': 'override', 'fired': True, 'detail': f'you chose {override}'})
    elif override == route:
        decision['override'] = {'requested': override, 'matches_suggestion': True}
    return decision


# --- retrieval for R2 and R3 -------------------------------------------------------------------


def score_source(hit: dict[str, Any]) -> str:
    """Where a hit's rank came from: ``bm25``, ``vector``, ``rrf`` (fused) or ``rerank``."""
    if hit.get('rerank_score') is not None or hit.get('reranked'):
        return 'rerank'
    ranks = hit.get('ranks') or {}
    if hit.get('method') == 'hybrid' or 'dense' in ranks or len(ranks) > 1:
        return 'rrf'
    if hit.get('match') == 'dense' or hit.get('method') == 'dense':
        return 'vector'
    if hit.get('match') == 'memory':
        return 'term count'
    if hit.get('match') == 'read_range':
        return 'neighbour'
    return 'bm25'


def _memory_search(doc, query, *, block_ids=None, limit=20):
    """Keyword search over the open document when no project index exists (unsaved or never built)."""
    from .codex import text_objects
    from .search_query import parse

    parsed = parse(query)
    words = [re.findall(r'\w+', term) for term in parsed.literal_terms()]
    patterns = [
        re.compile(r'(?<!\w)' + r'\W+'.join(re.escape(w) for w in parts) + r'\w*', re.IGNORECASE)
        for parts in words
        if parts
    ]
    allowed = set(block_ids) if block_ids is not None else None
    hits = []
    for ordinal, (target, block, text) in enumerate(text_objects(doc)):
        if allowed is not None and block not in allowed:
            continue
        score = sum(len(p.findall(text)) for p in patterns)
        matched = sum(1 for p in patterns if p.search(text))
        if not score:
            continue
        hits.append(
            {
                'chunk_id': f'{doc["id"]}:{target}',
                'corpus': 'manuscript',
                'document_id': doc['id'],
                'target': target,
                'block_id': block,
                'ordinal': ordinal,
                'text': text,
                'match': 'memory',
                'score': float(matched * 100 + score),
                'open': True,
                'path': None,
                'locator': {
                    'document_id': doc['id'],
                    'block_id': block,
                    'target': target,
                    'ordinal': ordinal,
                },
            }
        )
    hits.sort(key=lambda h: (-h['score'], h['ordinal']))
    return {
        'results': hits[:limit],
        'total': len(hits),
        'method': 'keyword',
        'corpus': 'open document (in memory)',
    }


class ReadTools:
    """The read-only tools of route R3 (and the corpus access of R2). Nothing here writes or sends.

    ``search`` and ``semantic_search`` cover the manuscript (the project index when one exists, else
    the open document) and the eligible attached libraries; ``get_outline`` is :func:`byax.outline.outline`;
    ``read_range`` reads blocks around a manuscript target; ``get_entity`` is the AI view of a codex
    lookup (hidden entries never match).
    """

    read_only = True

    def __init__(
        self,
        doc: Doc,
        *,
        document_path=None,
        project_index=None,
        libraries=None,
        use_libraries=True,
        excluded_libraries=(),
        remote=False,
        registry=None,
        cache_dir=None,
        embedding_provider=None,
        profile=None,
        target=None,
    ):
        self.doc = doc
        self.document_path = document_path
        self.index = project_index
        self.index_note = None
        if self.index is None and document_path is not None:
            from .project_index import ProjectIndex

            try:
                self.index = ProjectIndex.existing(document_path, doc=doc)
                if self.index is not None:
                    self.index.update_document(doc)
            except (ByaxError, OSError) as exc:
                self.index, self.index_note = None, f'Project index unavailable: {exc}'
        if self.index is None and not self.index_note:
            self.index_note = (
                'No project index yet: the open document is searched in memory '
                '(Edit > Find in project builds the index for sibling documents).'
            )
        self.libraries = [l for l in libraries or [] if l['library_id'] not in set(excluded_libraries)]
        self.use_libraries = use_libraries
        self.remote = remote
        self.registry = registry
        self.cache_dir = cache_dir
        self.embedding_provider = embedding_provider
        self.profile = profile
        self.target = target

    @property
    def corpus(self):
        if self.index is not None:
            return 'project index'
        return 'open document (in memory)'

    def _manuscript(self, query, *, scope_blocks=None, item=None, limit=20, method='keyword'):
        if self.index is None:
            return _memory_search(self.doc, query, block_ids=scope_blocks, limit=limit)
        result = self.index.search(
            query,
            limit=limit if scope_blocks is None else 100,
            item=item,
            method=method,
            provider=self.embedding_provider if method != 'keyword' else None,
        )
        if scope_blocks is not None and item is None:
            allowed = set(scope_blocks)
            result['results'] = [
                h for h in result['results'] if h.get('open') and h.get('block_id') in allowed
            ]
            result['results'] = result['results'][:limit]
        return result

    def _libraries(self, query, *, limit, method='keyword', pinned=None):
        if not self.use_libraries or not self.libraries:
            return None
        from .retrieval import retrieve_context

        view = dict(self.doc)
        allowed = {l['library_id'] for l in self.libraries}
        view['settings'] = dict(
            self.doc.get('settings') or {},
            libraries=[
                a for a in self.doc.get('settings', {}).get('libraries', []) if a['library_id'] in allowed
            ],
        )
        try:
            return retrieve_context(
                view,
                query,
                profile=self.profile,
                target=self.target,
                registry=self.registry,
                cache_dir=self.cache_dir,
                limit=max(1, min(limit, 100)),
                remote=self.remote,
                method=method,
                pinned=pinned,
                embedding_provider=self.embedding_provider,
            )
        except ByaxError as exc:
            if exc.code == 'invalid_input':
                return {'included': [], 'omitted': [{'reason': exc.message}], 'libraries': [], 'pinned': []}
            raise

    def search(self, query, *, scope_blocks=None, item=None, limit=10, pinned=None):
        manuscript = self._manuscript(query, scope_blocks=scope_blocks, item=item, limit=limit)
        libraries = self._libraries(query, limit=limit, pinned=pinned)
        return {'manuscript': manuscript, 'libraries': libraries}

    def semantic_search(self, query, *, scope_blocks=None, item=None, limit=10, pinned=None):
        notice = None
        if self.index is not None and self.embedding_provider is not None:
            manuscript = self._manuscript(
                query, scope_blocks=scope_blocks, item=item, limit=limit, method='auto'
            )
        else:
            manuscript = {'results': [], 'total': 0, 'method': 'none'}
            notice = 'No manuscript vectors (no embedding provider chosen for the project index).'
        libraries = self._libraries(query, limit=limit, method='hybrid', pinned=pinned)
        return {'manuscript': manuscript, 'libraries': libraries, 'notice': notice}

    def get_outline(self, *, depth=2, counts=True, binder=True):
        from .outline import outline

        return outline(self.doc, depth=depth, counts=counts, binder=binder, limit=200)

    def read_range(self, target, *, before=1, after=1):
        from .codex import text_objects

        ids = [b['id'] for b in self.doc.get('blocks', [])]
        owner = {o: b for o, b, _ in text_objects(self.doc)}
        block = owner.get(target, target)
        if block not in ids:
            raise ByaxError('missing_target', 'The target is not in the open document.', target=target)
        position = ids.index(block)
        blocks = self.doc['blocks'][max(0, position - before) : position + after + 1]
        return {
            'blocks': [
                {'id': b['id'], 'type': b.get('type'), 'text': b.get('text', '')}
                for b in blocks
                if b.get('text')
            ]
        }

    def get_entity(self, name):
        from .codex import lookup

        return lookup(self.doc, name, include_hidden=False)


class ToolLoop:
    """A budgeted loop over the read-only tools. Any other tool name is refused (lethal trifecta)."""

    def __init__(self, tools: ReadTools, log, *, max_calls: int = R3_MAX_CALLS):
        if not getattr(tools, 'read_only', False):
            raise ByaxError('unsupported_feature', 'Route R3 runs read-only tools only')
        self.tools = tools
        self.log = log
        self.max_calls = max_calls
        self.calls = 0

    def call(self, tool: str, /, **arguments: Any) -> Any:
        if tool not in READ_ONLY_TOOLS:
            raise ByaxError(
                'unsupported_feature',
                'Route R3 can use only the read-only tools search, semantic_search, get_outline, read_range '
                'and get_entity; it never writes or sends anything.',
                tool=tool,
            )
        if self.calls >= self.max_calls:
            raise ByaxError('limit_exceeded', 'The R3 tool budget is used up', calls=self.calls)
        self.calls += 1
        return getattr(self.tools, tool)(**arguments)


# --- items --------------------------------------------------------------------------------------


def _passage_item(hit, *, tier='retrieved', reason='Found by search', rank=None):
    if hit.get('corpus') == 'manuscript' or hit.get('library_id') is None:
        where = hit.get('path') or 'this document'
        item = {
            'id': hit['chunk_id'],
            'kind': 'manuscript_passage',
            'tier': tier,
            'label': f'Manuscript · {where} · {hit.get("heading") or hit.get("block_id") or hit.get("target")}',
            'payload': {
                key: hit[key]
                for key in (
                    'chunk_id',
                    'document_id',
                    'target',
                    'block_id',
                    'path',
                    'heading',
                    'text',
                    'locator',
                )
                if hit.get(key) is not None
            },
            'source': 'manuscript',
            'library_id': None,
            'library': None,
            'retrieval_policy': None,
        }
    else:
        item = {
            'id': hit['chunk_id'],
            'kind': 'library_passage',
            'tier': tier,
            'label': f'{hit.get("library_name", hit["library_id"])} · {hit.get("path") or hit.get("title") or hit.get("document_id")}',
            'payload': copy.deepcopy(hit),
            'source': 'library',
            'library_id': hit['library_id'],
            'library': hit.get('library_name', hit['library_id']),
            'retrieval_policy': hit.get('retrieval_policy', 'ask'),
        }
    item.update(
        score=hit.get('fused_score', hit.get('score')),
        score_source=score_source(hit),
        reason=reason,
        rank=rank,
    )
    return item


def _codex_item(entry, reason):
    payload = {
        'id': entry['id'],
        'kind': entry['kind'],
        'name': entry['name'],
        'aliases': list(entry.get('aliases', [])),
        'description': entry.get('description', ''),
        'traits': [
            {'name': t['name'], 'value': t['value']}
            for t in entry.get('traits', [])
            if not t.get('hide_from_ai')
        ],
    }
    return {
        'id': f'codex:{entry["id"]}',
        'kind': 'codex_entry',
        'tier': 'pinned',
        'label': f'Codex · {entry["name"]} ({entry["kind"]})',
        'payload': payload,
        'source': 'codex',
        'score': None,
        'score_source': 'codex',
        'reason': reason,
    }


def _fused(results):
    """Manuscript hits then library hits, fused by reciprocal rank across both corpora."""
    from .ranking import Hit, dedupe, rrf

    lists, names = [], []
    manuscript = (results.get('manuscript') or {}).get('results') or []
    if manuscript:
        lists.append([Hit.from_result(dict(h, library_id=None)) for h in manuscript])
        names.append('manuscript')
    libraries = (results.get('libraries') or {}).get('included') or []
    if libraries:
        lists.append([Hit.from_result(h) for h in libraries])
        names.append('libraries')
    fused, duplicates = dedupe(rrf(lists, names=names)) if lists else ([], [])
    rows = []
    for hit in fused:
        row = hit.to_dict()
        if len(hit.ranks) <= 1:
            row.pop('ranks', None)
            row.pop('fused_score', None)
        rows.append(row)
    return rows, duplicates


def metadata_boosts(
    doc: Doc, hits: list[dict[str, Any]], sig: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """R-meta ranking: hits in the scoped chapter, or in chapters where a requested entity appears, first.

    Returns the reordered hits and a description of each boost applied. Stable for equal input.
    """
    from .codex import heatmap

    reasons = []
    scope = sig.get('scope') or {}
    scoped = set(section_blocks(doc, scope['heading'])) if scope.get('heading') else set()
    entity_ids = [e['id'] for e in sig.get('codex_hits') or []]
    chapters_with = set()
    if entity_ids:
        sections = heatmap(doc, for_ai=True, entries=entity_ids)['sections']
        chapters_with = {row['heading'] for row in sections if row['total']}
        if chapters_with:
            reasons.append(
                'passages from sections that mention ' + ', '.join(e['name'] for e in sig['codex_hits'])
            )
    if scoped:
        reasons.insert(0, f'passages inside {scope.get("label")}')
    if not reasons:
        return hits, []
    heading_of = {}
    for row in heatmap(doc, for_ai=True, entries=entity_ids)['sections'] if chapters_with else []:
        for block in section_blocks(doc, row['heading']):
            heading_of.setdefault(block, set()).add(row['heading'])

    def boost(hit):
        block = hit.get('block_id') or (hit.get('locator') or {}).get('block_id')
        return (0 if block in scoped else 1, 0 if heading_of.get(block, set()) & chapters_with else 1)

    # Manuscript metadata reorders manuscript hits among themselves; library passages keep their
    # fused places, so a metadata boost never pushes sources out of the top-k.
    slots = [n for n, hit in enumerate(hits) if hit.get('library_id') is None]
    ranked = sorted(slots, key=lambda n: (*boost(hits[n]), n))
    ordered = list(hits)
    for slot, source in zip(slots, ranked, strict=True):
        ordered[slot] = hits[source]
    return ordered, reasons


# Request wording that is never what the request is about.
_FILLER = frozenset(
    _words(
        'every each place places time times thing things anything something everything anywhere somewhere '
        'whole entire book novel manuscript story chapter chapters scene scenes passage paragraph text draft '
        'please really still just there here where else ever'
    )
)


def request_terms(request: str, limit: int = 8) -> list[str]:
    """Quoted phrases, names and content words of a request (stopwords and question words dropped)."""
    from .search_query import _QUESTION_WORDS, STOPWORDS

    phrases = [p.strip() for p in re.findall(r'["“]([^"”]{2,200})["”]', request) if p.strip()]
    rest = re.sub(r'["“][^"”]*["”]', ' ', request)
    names = entities({'blocks': []}, rest)['names']
    words = []
    for token in re.findall(r"\w+(?:'\w+)?", rest):
        token = re.sub(r"'s$", '', token)
        folded = token.casefold()
        if folded in STOPWORDS or folded in _QUESTION_WORDS or folded in COMMON_CAPITALS or folded in _FILLER:
            continue
        if token in names or (len(token) < 4 and not token.isdigit()):
            continue
        if folded in {w.casefold() for w in words} or folded in TASK_VERBS:
            continue
        words.append(token)
    return list(dict.fromkeys(phrases + names + words))[:limit]


def _forms(term):
    """The term and simple suffix-stripped forms (eyes → eye, lies → lie), case-folded."""
    folded = term.casefold()
    forms = {folded}
    for suffix in ('ing', 'ed', 'es', 's'):
        if folded.endswith(suffix) and len(folded) - len(suffix) >= 3:
            forms.add(folded[: -len(suffix)])
    return forms


def sufficiency(request: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """CRAG-style check, deterministic: does the evidence cover the request's key terms?

    Sufficient when there is evidence, every name or quoted phrase occurs in it, and at least half of
    the key terms do. Otherwise route R2 escalates to R3.
    """
    terms = request_terms(request)
    texts = []
    for item in items:
        payload = item.get('payload') or {}
        if item['kind'] == 'codex_entry':
            texts.append(
                ' '.join(
                    [payload.get('name', ''), *payload.get('aliases', []), payload.get('description', '')]
                )
            )
            texts += [f'{t["name"]} {t["value"]}' for t in payload.get('traits', [])]
        else:
            texts.append(str(payload.get('text', '')))
    corpus = '\n'.join(texts).casefold()
    covered = [t for t in terms if any(form in corpus for form in _forms(t))]
    missing = [t for t in terms if t not in covered]
    names = [t for t in terms if t[:1].isupper() or ' ' in t]
    ratio = len(covered) / len(terms) if terms else (1.0 if items else 0.0)
    ok = bool(items) and ratio >= SUFFICIENT_COVERAGE and all(n in covered for n in names)
    return {
        'sufficient': ok,
        'evidence': len(items),
        'terms': terms,
        'covered': covered,
        'missing': missing,
        'coverage': round(ratio, 3),
        'rule': f'evidence present, every name covered and at least {int(SUFFICIENT_COVERAGE * 100)}% of key terms',
    }


def deterministic_planner(request: str, sig: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The in-app R3 plan: entity lookups, the request query, a query per entity, semantic search,
    the blocks around the best manuscript hits (``read_top``) and, for whole-book questions, the outline.

    Each step is ``(tool, arguments)``. ``read_top`` expands into ``read_range`` calls for the top
    manuscript hits found so far. External agents drive the same read-only tools over MCP themselves.
    """
    names = [e['name'] for e in sig.get('codex_hits') or []] + list(sig.get('names') or [])
    steps: list[tuple[str, dict[str, Any]]] = [('get_entity', {'name': name}) for name in names[:3]]
    steps.append(('search', {'query': request}))
    steps += [('search', {'query': f'"{name}"'}) for name in names[:3]]
    steps.append(('semantic_search', {'query': request}))
    steps.append(('read_top', {'count': 3}))
    if sig.get('everywhere') or (sig.get('scope') or {}).get('kind') in ('book', 'everywhere'):
        steps.append(('get_outline', {'depth': 2}))
    return steps


# --- gathering -------------------------------------------------------------------------------------


def _engine_items(doc, *, target, radius, limit, profile, include_nearby=True):
    """engine.context around ``target``: the base context and its blocks as pinned items."""
    from . import engine

    base = engine.context(doc, target=target, limit=limit, profile=profile, radius=radius)
    items = []
    for block in base.pop('blocks', []):
        items.append(
            {
                'id': f'block:{block["id"]}',
                'kind': 'block',
                'tier': 'pinned',
                'label': f'Document · {block.get("type", "block")} {block["id"]}',
                'payload': block,
                'source': 'document',
                'score': None,
                'score_source': None,
                'reason': 'The selected block' if target else 'Document text',
            }
        )
    for block in base.pop('nearby', []):
        if include_nearby:
            items.append(
                {
                    'id': f'nearby:{block["id"]}',
                    'kind': 'nearby',
                    'tier': 'pinned',
                    'label': f'Nearby · {block.get("type", "block")} {block["id"]}',
                    'payload': block,
                    'source': 'document',
                    'score': None,
                    'score_source': None,
                    'reason': f'Within {radius} block(s) of the selection',
                }
            )
    return base, items


def _document_scope(base, scope):
    """Mark a base context whose engine blocks were not sent: no target block, and what was sent instead."""
    base['target'] = None
    base['scope'] = scope
    report = base.get('inclusion_report')
    if isinstance(report, dict):
        report['included_ids'] = []


def _snapshot_item(doc, block_ids, scope):
    from .model import object_hash

    wanted = set(block_ids) if block_ids is not None else None
    blocks = []
    for block in doc.get('blocks', []):
        if wanted is not None and block['id'] not in wanted:
            continue
        row = {'id': block['id'], 'type': block.get('type')}
        if block.get('type') == 'table':
            row['rows'] = [
                {'id': r['id'], 'cells': [{'id': c['id'], 'text': c.get('text', '')} for c in r['cells']]}
                for r in block.get('rows', [])
            ]
        else:
            row['text'] = block.get('text', '')
        if block.get('type') == 'heading':
            row['level'] = block.get('attrs', {}).get('level', 1)
        # The flags work-mode edit validation honours (locked, intentional and code text).
        flags = {
            k: v for k, v in block.get('attrs', {}).items() if k in ('locked', 'intentional', 'code') and v
        }
        if flags:
            row['attrs'] = flags
        blocks.append(row)
    payload = {'scope': scope, 'blocks': blocks}
    payload['sha256'] = object_hash(blocks)
    return {
        'id': 'snapshot',
        'kind': 'snapshot',
        'tier': 'pinned',
        'label': f'Manuscript snapshot · {scope} · {len(blocks)} blocks',
        'payload': payload,
        'source': 'document',
        'score': None,
        'score_source': None,
        'reason': 'Full text, laid out in the stable prompt prefix so provider caches can reuse it',
    }


def _summary_items(doc, summary_cache, block_ids=None):
    from . import summaries

    gathered = summaries.collected(doc, summary_cache)
    items = []
    wanted = None
    if block_ids is not None:
        wanted = {s['id'] for s in summaries.sections(doc) if set(s['block_ids']) & set(block_ids)}
    if gathered['book'] and wanted is None:
        items.append(
            {
                'id': 'summary:book',
                'kind': 'book_summary',
                'tier': 'pinned',
                'label': 'Book summary',
                'payload': {'summary': gathered['book']},
                'source': 'summary',
                'score': None,
                'score_source': None,
                'reason': 'Built from the chapter summaries (cached, fresh)',
            }
        )
    for chapter in gathered['chapters']:
        if wanted is not None and chapter['id'] not in wanted:
            continue
        items.append(
            {
                'id': f'summary:{chapter["id"]}',
                'kind': 'chapter_summary',
                'tier': 'pinned',
                'label': f'Chapter summary · {chapter["title"]}',
                'payload': chapter,
                'source': 'summary',
                'score': None,
                'score_source': None,
                'reason': 'Cached summary; the chapter text has not changed since it was built',
            }
        )
    return items, gathered


def _outline_item(doc, reason):
    from .outline import outline

    data = outline(doc, counts=True, binder=True, limit=1000)
    payload = {'items': data['items'], 'words': data.get('words'), 'truncated': data['truncated']}
    return {
        'id': 'outline',
        'kind': 'outline',
        'tier': 'pinned',
        'label': f'Outline · {data["total"]} headings with word counts and binder metadata',
        'payload': payload,
        'source': 'document',
        'score': None,
        'score_source': 'structure',
        'reason': reason,
    }


def _selection_items(selection):
    return [
        {
            'id': f'selection:{s["target"]}:{s["start"]}:{s["end"]}',
            'kind': 'selection',
            'tier': 'pinned',
            'label': f'Selection · {s["target"]} [{s["start"]}–{s["end"]}]',
            'payload': dict(s),
            'source': 'document',
            'score': None,
            'score_source': None,
            'reason': 'Your selection',
        }
        for s in selection or []
    ]


def _chapter_summary_item(doc, anchor, summary_cache):
    """R1: the fresh cached summary of the chapter around the anchor, else its binder synopsis."""
    from . import project, summaries

    section = summaries.section_for_block(doc, anchor)
    if section is None:
        return None
    record = summaries.fresh_summary(doc, section['id'], summary_cache)
    if record:
        payload = {
            'id': section['id'],
            'title': section['title'],
            'summary': record['summary'],
            'source': 'cached summary',
        }
    else:
        item = project.find_item(doc, section['id']) if isinstance(doc.get('project'), dict) else None
        if not item or not item.get('synopsis'):
            return None
        payload = {
            'id': section['id'],
            'title': section['title'],
            'summary': item['synopsis'],
            'source': 'binder synopsis',
        }
    return {
        'id': f'summary:{section["id"]}',
        'kind': 'chapter_summary',
        'tier': 'pinned',
        'label': f'Chapter summary · {section["title"]} ({payload["source"]})',
        'payload': payload,
        'source': 'summary',
        'score': None,
        'score_source': None,
        'reason': 'The chapter around the selection, summarized',
    }


def _codex_items_for(doc, sig, *, ranges=None, radius=1):
    from .codex import ai_context, lookup

    items, hidden = [], 0
    if ranges is not None:
        found = ai_context(doc, ranges, radius=radius)
        hidden = found['hidden_omitted']
        for entry in found['entries']:
            items.append(
                _codex_item(entry, f'Mentioned {entry["mentions"]} time(s) in the selection or nearby')
            )
    seen = {item['id'] for item in items}
    for name in [e['name'] for e in sig.get('codex_hits') or []] + list(sig.get('names') or []):
        found = lookup(doc, name, include_hidden=False)
        if found['entry'] and f'codex:{found["entry"]["id"]}' not in seen:
            seen.add(f'codex:{found["entry"]["id"]}')
            items.append(_codex_item(found['entry'], f'Named in the request ("{name}")'))
    return items, hidden


def gather(
    doc: Doc,
    request: str,
    decision: dict[str, Any],
    *,
    selection: list | None = None,
    cursor: str | None = None,
    document_path=None,
    provider: Any = None,
    remote: bool = False,
    profile=None,
    use_libraries: bool = True,
    libraries: list | None = None,
    pinned: list | None = None,
    excluded: list | None = None,
    registry=None,
    cache_dir=None,
    embedding_provider=None,
    summary_cache=None,
    project_index=None,
    max_calls: int = R3_MAX_CALLS,
    planner: Any = None,
    context_limit: int = 100_000,
) -> dict[str, Any]:
    """Collect the decided route's context items, escalating R1→R2→R3→R4 or abstaining.

    Returns ``{'route', 'base', 'items', 'dropped', 'retrieval', 'research_log', 'abstain'}``. ``base``
    is the engine context without its blocks; ``items`` are pinned and retrieved candidates in priority
    order (budgets are applied by :func:`byax.context_assembly.assemble`). Local and read-only: indexes
    and the codex are read, nothing is sent and nothing is written.
    """
    from .research_log import ResearchLog

    decision = copy.deepcopy(decision)
    selection = [s for s in selection or [] if isinstance(s, dict) and s.get('end', 0) > s.get('start', 0)]
    excluded_ids = set(excluded or [])
    sig = decision['signals']
    limits = decision['thresholds']
    anchor = _anchor(selection, cursor)
    blocks = doc.get('blocks', [])
    first = blocks[0]['id'] if blocks else None
    dropped: list[dict[str, Any]] = []
    retrieval = None
    log = None
    abstain = None
    if libraries is None:
        libraries = attached_libraries(doc, registry) if use_libraries else []
    excluded_libraries = decision['privacy'].get('excluded_libraries', [])
    for library in libraries:
        if library['library_id'] in excluded_libraries:
            dropped.append(
                {
                    'id': library['library_id'],
                    'label': f'Library · {library.get("name", library["library_id"])}',
                    'reason': 'Left out by the privacy rule: local-only library and a remote model',
                    'policy': 'privacy',
                }
            )
    if not use_libraries and libraries:
        dropped.append(
            {
                'id': 'libraries',
                'label': 'Attached libraries',
                'reason': '"Use attached libraries" is off',
                'policy': 'choice',
            }
        )
    tools = ReadTools(
        doc,
        document_path=document_path,
        project_index=project_index,
        libraries=libraries,
        use_libraries=use_libraries,
        excluded_libraries=excluded_libraries,
        remote=remote,
        registry=registry,
        cache_dir=cache_dir,
        embedding_provider=embedding_provider,
        profile=profile,
        target=anchor,
    )
    pinned_items = []
    for item in pinned or []:
        if isinstance(item, dict) and item.get('id'):
            pinned_items.append(
                dict(copy.deepcopy(item), tier='pinned', pinned_by_you=True, reason='Pinned by you')
            )
    pinned_chunks = [
        i['id'] for i in pinned_items if i.get('kind') in ('library_passage', 'manuscript_passage')
    ]

    def finish(route, base, items, **extra):
        kept, seen = [], set()
        for item in [*pinned_items, *items]:
            if item['id'] in seen:
                continue
            seen.add(item['id'])
            if item['id'] in excluded_ids:
                dropped.append(
                    {
                        'id': item['id'],
                        'label': item['label'],
                        'reason': 'Excluded by you',
                        'policy': 'choice',
                    }
                )
                continue
            kept.append(item)
        decision['route'] = route
        decision['name'] = ROUTE_NAMES[route]
        decision['label'] = ROUTE_LABELS[route]
        base.setdefault('scope', 'selection' if selection else 'document')
        return {
            'route': decision,
            'base': base,
            'items': kept,
            'dropped': dropped,
            'retrieval': retrieval,
            'research_log': log.to_dict() if log else None,
            'abstain': abstain,
            'corpus': {'manuscript': tools.corpus, 'note': tools.index_note},
            **extra,
        }

    def escalate(source, target, reason):
        decision['escalations'].append({'from': source, 'to': target, 'reason': reason})
        decision['reason'] = f'{decision["reason"]} Escalated to {target}: {reason}'

    route = decision['route']
    radius = limits['radius']

    if route == 'R0':
        base, items = _engine_items(
            doc, target=anchor or first, radius=0, limit=context_limit, profile=profile
        )
        return finish('R0', base, [*_selection_items(selection), *items])

    if route == 'R1':
        base, items = _engine_items(
            doc, target=anchor or first, radius=radius, limit=context_limit, profile=profile
        )
        ranges = [dict(s, radius=radius) for s in selection] or (
            [{'target': anchor, 'radius': radius}] if anchor else None
        )
        codex_items, hidden = _codex_items_for(doc, sig, ranges=ranges, radius=radius) if ranges else ([], 0)
        if hidden:
            dropped.append(
                {
                    'id': 'codex:hidden',
                    'label': f'{hidden} codex entr{"y" if hidden == 1 else "ies"}',
                    'reason': 'Hidden from AI',
                    'policy': 'codex',
                }
            )
        summary = _chapter_summary_item(doc, anchor, summary_cache)
        return finish(
            'R1', base, [*_selection_items(selection), *items, *codex_items, *([summary] if summary else [])]
        )

    if route == 'R4':
        scoped = decision.get('variant') == 'scoped' and sig['scope'].get('heading')
        block_ids = section_blocks(doc, sig['scope']['heading']) if scoped else None
        scope_label = (sig['scope'].get('title') or sig['scope'].get('label')) if scoped else 'whole document'
        tokens = _document_tokens(doc, provider, block_ids)
        base, _ = _engine_items(doc, target=anchor or first, radius=0, limit=context_limit, profile=profile)
        # Edits may target any block of the snapshot, so every protection in the document applies.
        base['protections'] = copy.deepcopy(doc.get('settings', {}).get('protections', []))
        _document_scope(base, scope_label)
        if tokens <= limits['full_document_tokens']:
            return finish(
                'R4',
                base,
                [*_selection_items(selection), _snapshot_item(doc, block_ids, scope_label)],
                snapshot=True,
            )
        items, gathered = _summary_items(doc, summary_cache, block_ids)
        missing = len(gathered['missing']) + len(gathered['stale'])
        if missing:
            dropped.append(
                {
                    'id': 'summaries',
                    'label': f'{missing} chapter summar{"y" if missing == 1 else "ies"}',
                    'reason': 'Missing or out of date: build chapter summaries (an explicit AI action) to cover them',
                    'policy': 'summaries',
                }
            )
        too_large = (
            f'the {scope_label} is about {tokens:,} tokens, above the full-context threshold of '
            f'{limits["full_document_tokens"]:,} for this model'
        )
        if items:
            decision['reason'] = f'{decision["reason"]} Chapter summaries are used: {too_large}.'
            items.append(
                _outline_item(doc, 'Chapter order, word counts and binder synopses alongside the summaries')
            )
            return finish(
                'R4', base, [*_selection_items(selection), *items], snapshot=False, summaries=gathered
            )
        # No summaries yet: answer by searching instead of sending nothing useful.
        escalate('R4', 'R3', f'{too_large}, and no chapter summaries have been built')
        route = 'R3'

    # R2 and R3 share the corpus access.
    base, local_items = _engine_items(
        doc, target=anchor or first, radius=0, limit=context_limit, profile=profile
    )
    local_items = [*_selection_items(selection), *(local_items if selection else [])]
    if not selection:
        _document_scope(base, 'retrieved passages')
    if route == 'R2' and decision.get('variant') == 'structure':
        return finish(
            'R2',
            base,
            [
                *local_items,
                _outline_item(doc, 'Structure questions are answered from the outline and binder (R-meta)'),
            ],
        )

    top_k = limits['top_k']
    scope = sig['scope']
    scope_blocks = section_blocks(doc, scope['heading']) if scope.get('heading') else None
    scope_item = scope.get('item') if scope.get('heading') and isinstance(doc.get('project'), dict) else None
    codex_items, _ = _codex_items_for(doc, sig)
    if route == 'R2':
        log = ResearchLog('R2', request)
        found = tools.search(
            request, scope_blocks=scope_blocks, item=scope_item, limit=top_k * 2, pinned=pinned_chunks
        )
        retrieval = found['libraries']
        candidates = len((found['manuscript'] or {}).get('results', [])) + len(
            (retrieval or {}).get('included', [])
        )
        log.query(
            'search',
            {'query': request, **({'scope': scope.get('label')} if scope_blocks else {})},
            candidates,
            notice=tools.index_note if tools.index is None else None,
        )
        hits, duplicates = _fused(found)
        hits, boosts = metadata_boosts(doc, hits, sig)
        log.rerank(
            'metadata boosts (R-meta)' if boosts else 'none (fused order)',
            len(hits),
            [h['chunk_id'] for h in hits],
        )
        retrieved = [_passage_item(h, rank=n + 1) for n, h in enumerate(hits[:top_k])]
        overflow = [
            {
                'id': h['chunk_id'],
                'label': h.get('path') or h.get('chunk_id'),
                'reason': f'Beyond top-k ({top_k}) for this model',
                'policy': 'top_k',
            }
            for h in hits[top_k:]
        ]
        log.keep(
            [i['id'] for i in retrieved],
            overflow + [{'id': d['chunk_id'], 'reason': d.get('reason', 'duplicate')} for d in duplicates],
        )
        dropped.extend(overflow)
        check = sufficiency(request, [*codex_items, *retrieved])
        log.outcome = 'sufficient' if check['sufficient'] else 'insufficient: escalated to R3'
        if check['sufficient']:
            return finish(
                'R2',
                base,
                [*local_items, *codex_items, *retrieved],
                ranking={'boosts': boosts},
                sufficiency=check,
            )
        escalate('R2', 'R3', f'the evidence does not cover {", ".join(check["missing"]) or "the request"}')
        route = 'R3'

    # R3: the budgeted read-only loop.
    log = ResearchLog('R3', request)
    loop = ToolLoop(tools, log, max_calls=max_calls)
    lists: list[list[dict[str, Any]]] = []
    entity_items = list(codex_items)
    neighbours: list[dict[str, Any]] = []
    outline_item = None
    steps = list((planner or deterministic_planner)(request, sig))
    while steps:
        tool, arguments = steps.pop(0)
        if tool == 'read_top':
            manuscript_hits = [
                h for row in lists for h in row if h.get('library_id') is None and h.get('target')
            ]
            targets = list(dict.fromkeys(h['target'] for h in manuscript_hits))[: arguments.get('count', 3)]
            steps[0:0] = [('read_range', {'target': t, 'before': 1, 'after': 1}) for t in targets]
            continue
        call = dict(arguments)
        if tool in ('search', 'semantic_search'):
            call.update(scope_blocks=scope_blocks, item=scope_item, limit=top_k, pinned=pinned_chunks)
        try:
            result = loop.call(tool, **call)
        except ByaxError as exc:
            if exc.code == 'limit_exceeded':
                log.query(tool, arguments, 0, notice='skipped: tool budget used up')
                break
            if exc.code == 'unsupported_feature':
                raise
            log.query(tool, arguments, 0, notice=exc.message)
            continue
        if tool == 'get_entity':
            entry = result.get('entry')
            log.query(tool, arguments, 1 if entry else 0)
            if entry and f'codex:{entry["id"]}' not in {i['id'] for i in entity_items}:
                entity_items.append(_codex_item(entry, f'get_entity("{arguments["name"]}")'))
        elif tool in ('search', 'semantic_search'):
            rows, _ = _fused(result)
            log.query(tool, {'query': arguments['query']}, len(rows), notice=result.get('notice'))
            if result.get('libraries'):
                retrieval = _merge_retrieval(retrieval, result['libraries'])
            if rows:
                lists.append(rows)
        elif tool == 'read_range':
            log.query(tool, arguments, len(result['blocks']))
            for block in result['blocks']:
                neighbours.append(
                    {
                        'chunk_id': f'{doc["id"]}:{block["id"]}',
                        'corpus': 'manuscript',
                        'document_id': doc['id'],
                        'target': block['id'],
                        'block_id': block['id'],
                        'text': block['text'],
                        'match': 'read_range',
                        'locator': {'document_id': doc['id'], 'block_id': block['id']},
                    }
                )
        elif tool == 'get_outline':
            log.query(tool, arguments, result['total'])
            outline_item = _outline_item(doc, 'get_outline: chapter order for an everywhere question')
    from .ranking import Hit, dedupe, rrf

    fused, duplicates = (
        dedupe(rrf([[Hit.from_result(h) for h in row] for row in lists])) if lists else ([], [])
    )
    hits = []
    for hit in fused:
        row = hit.to_dict()
        if len(hit.ranks) <= 1:
            row.pop('ranks', None)
            row.pop('fused_score', None)
        hits.append(row)
    hits, boosts = metadata_boosts(doc, hits, sig)
    log.rerank(
        'rrf across queries' + (' + metadata boosts (R-meta)' if boosts else ''),
        len(hits),
        [h['chunk_id'] for h in hits],
    )
    keep = max(top_k, 6)
    retrieved = [
        _passage_item(h, rank=n + 1, reason='Found by the R3 loop') for n, h in enumerate(hits[:keep])
    ]
    known = {i['id'] for i in retrieved}
    for row in neighbours:
        if row['chunk_id'] not in known and len(retrieved) < keep * 2:
            known.add(row['chunk_id'])
            retrieved.append(_passage_item(row, reason='Neighbouring block of a search hit (read_range)'))
    overflow = [
        {
            'id': h['chunk_id'],
            'label': h.get('path') or h['chunk_id'],
            'reason': f'Beyond the R3 keep limit ({keep})',
            'policy': 'top_k',
        }
        for h in hits[keep:]
    ]
    dropped.extend(overflow)
    log.keep(
        [i['id'] for i in [*entity_items, *retrieved]],
        overflow + [{'id': d['chunk_id'], 'reason': d.get('reason', 'duplicate')} for d in duplicates],
    )
    evidence = [*entity_items, *retrieved]
    extra_items = [outline_item] if outline_item else []
    check = sufficiency(request, evidence)
    # Q14: whatever the evidence does not cover is searched exhaustively and literally before anything
    # may say "not found in your sources".
    # Evidence that covers none of the request's terms is not evidence for it.
    relevant = bool(evidence) and (bool(check['covered']) or not check['terms'])
    terms = check['missing'] if relevant else (check['terms'] or [request.strip()[:200]])
    report = _verify(
        tools, terms, doc, registry=registry, cache_dir=cache_dir, profile=profile, target=anchor
    )
    absence_item = None
    if report is not None:
        log.query('verify_absence', {'terms': terms}, len(report['matches']))
        absence_item = _absence_item(report, top_k)
    if not relevant and evidence:
        dropped.extend(
            {
                'id': i['id'],
                'label': i['label'],
                'reason': 'Covers none of the request terms',
                'policy': 'relevance',
            }
            for i in evidence
        )
    if relevant:
        log.outcome = f'{len(evidence)} item(s) kept after {loop.calls} tool call(s)'
        if report is not None and report['absent']:
            log.outcome += '; verified absent: ' + ', '.join(report['absent'])
        return finish(
            'R3',
            base,
            [*local_items, *evidence, *([absence_item] if absence_item else []), *extra_items],
            ranking={'boosts': boosts},
            sufficiency=check,
            absence=report,
            tool_calls=loop.calls,
        )
    if report is not None and report['found']:
        log.outcome = 'literal matches found by verify_absence'
        return finish('R3', base, [*local_items, absence_item], absence=report, tool_calls=loop.calls)
    if limits.get('fits_full_document'):
        escalate('R3', 'R4', 'search found nothing literal, and the whole document fits this model')
        log.outcome = 'escalated to R4'
        base['protections'] = copy.deepcopy(doc.get('settings', {}).get('protections', []))
        _document_scope(base, 'whole document')
        return finish(
            'R4',
            base,
            [*_selection_items(selection), _snapshot_item(doc, None, 'whole document')],
            absence=report,
            snapshot=True,
            tool_calls=loop.calls,
        )
    abstain = report
    log.outcome = 'abstained after verified absence'
    decision['reason'] = (
        f'{decision["reason"]} Nothing was found, even by an exhaustive literal search, so the assistant abstains.'
    )
    return finish('R3', base, list(local_items), absence=report, tool_calls=loop.calls)


def _verify(tools, terms, doc, **options):
    """verify_absence over the eligible libraries and the open document, plus the project index."""
    from .retrieval import verify_absence

    if not terms:
        return None
    report = verify_absence(
        terms, list(tools.libraries) if tools.use_libraries else [], manuscript=doc, **options
    )
    if tools.index is not None:
        for term in list(report['absent']):
            found = [m for m in tools.index.literal_matches(term, limit=20) if not m.get('open')]
            if found:
                report['absent'].remove(term)
                report['found'].append(term)
                report['matches'] += [dict(m, term=term, scope='project') for m in found]
                for row in report['results']:
                    if row['term'] == term:
                        row['found'] = True
                        row['project_matches'] = len(found)
        report['searched']['project'] = {'documents': len(tools.index.status().get('documents', []))}
    return report


def _absence_item(report, limit):
    found, absent = report['found'], report['absent']
    label = 'Exhaustive literal check: ' + '; '.join(
        part
        for part in (
            ('found ' + ', '.join(found)) if found else '',
            ('no match for ' + ', '.join(absent)) if absent else '',
        )
        if part
    )
    return {
        'id': 'absence_check',
        'kind': 'absence_check',
        'tier': 'retrieved',
        'label': label,
        'payload': {
            'terms': report['terms'],
            'found': found,
            'absent': absent,
            'matches': [
                {k: m[k] for k in ('term', 'scope', 'path', 'target', 'locator', 'excerpt') if k in m}
                for m in report['matches'][:limit]
            ],
            'exhaustive': report['exhaustive'],
            'note': report['note'],
        },
        'source': 'manuscript',
        'score': None,
        'score_source': 'literal',
        'reason': 'Every eligible library, the open document and the project index searched literally (Q14)',
    }


def _merge_retrieval(current, new):
    """Combine library retrieval reports from several R3 queries for the passage approval dialog."""
    if current is None:
        return copy.deepcopy(new)
    merged = copy.deepcopy(current)
    known = {p['chunk_id'] for p in merged.get('included', [])}
    for passage in new.get('included', []):
        if passage['chunk_id'] not in known:
            merged['included'].append(copy.deepcopy(passage))
            known.add(passage['chunk_id'])
    merged['omitted'] = merged.get('omitted', []) + copy.deepcopy(new.get('omitted', []))
    merged['allowed_citation_ids'] = [p['chunk_id'] for p in merged['included']]
    return merged
