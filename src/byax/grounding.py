"""Grounding records: what an answer cites, where in the source, and how well it is supported.

Context engine plan 6.1, 6.4 and 6.5. Every citation, whether a model returned it natively
(Anthropic citations, Gemini grounding supports, Cohere spans), wrote a ``[[cite:ID]]`` token, or the
writer inserted a citation mark, becomes one :class:`GroundingRecord`: the answer span, the cited
chunk and the span inside it, the source quote with 32 characters of context either side, and the
source version (content hash) at citation time.

The verification cascade runs named stages in order. Stage 1 is deterministic and local: ``quote``
checks a claimed quotation against the cited chunk with :mod:`byax.quote_match`, and ``fuzzy``
aligns an unquoted claim sentence with the chunk. Stage 2 (local entailment) and stage 3 (an
optional LLM judge) register later through :func:`register_stage` (plan Phase 5); until then they
are reported as ``not_run`` rather than skipped silently. Lexical overlap is never presented as
proof that a paraphrase is supported: an unquoted claim that does not align closely stays
``not_checkable`` until a model-based stage exists.

Record states:

- ``supported``: the claimed quote (or a near-verbatim claim) is found in the cited chunk;
- ``partial``: a close but inexact match, or a claim that shares only part of the chunk's wording;
- ``unsupported``: the claimed quote is not in the cited chunk;
- ``not_checkable``: a paraphrase that stage 1 cannot judge;
- ``unanchored``: the cited source text is not available or could not be re-found after a change;
- ``unverified``: the citation ID was not supplied with the request, or no stage has run.

Audit mode (:func:`audit_citations`) applies stage 1 to every citation mark the writer made. It is
deterministic, local and needs no consent: it reads only attached library indexes and the
document, never the network.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field, fields

from .errors import ByaxError

STATES = ('supported', 'partial', 'unsupported', 'not_checkable', 'unanchored', 'unverified')
# ``model``: an AI-written sentence with no citation (claim-level provenance, plan section 10).
ORIGINS = ('native', 'token', 'user', 'model')
STAGE_ORDER = ('quote', 'fuzzy', 'entailment', 'judge')
_DECISIVE = ('supported', 'partial', 'unsupported', 'not_checkable')
DEFAULT_STAGES = ('quote', 'fuzzy')
CONTEXT_CHARS = 32
# Claim-to-chunk alignment (0-100). At or above NEAR the cited words are effectively the source's;
# at or above PARTIAL they share a good part of the wording. Below that a claim is a paraphrase.
PARTIAL_THRESHOLD = 60.0
CITE_TOKEN = re.compile(r'\[\[cite:([^\]]+)\]\]')
_QUOTE_IN = re.compile(r'["“„«]([^"“”„‟«»]{4,}?)["”‟»]')
_SENTENCE_END = re.compile(r'[.!?]["”’)\]]*\s+|\n+')
# Locator keys a citation mark accepts (engine ``cite``), and the structural ones re-anchoring keeps.
LOCATOR_KEYS = (
    'page',
    'paragraph',
    'block',
    'chunk',
    'label',
    'text',
    'heading_path',
    'start',
    'end',
    'ordinal',
)
STRUCTURE_KEYS = ('page', 'block', 'paragraph', 'heading_path')
AUDIT_STATUSES = (
    'quote_found',
    'near_match',
    'quote_not_found',
    'source_changed',
    'source_unavailable',
    'not_checkable',
    'unanchored',
)
_AUDIT_STATES = {
    'quote_found': 'supported',
    'near_match': 'partial',
    'quote_not_found': 'unsupported',
    'source_changed': 'unverified',
    'source_unavailable': 'unverified',
    'not_checkable': 'not_checkable',
    'unanchored': 'unanchored',
}


@dataclass
class GroundingRecord:
    """One citation. The first thirteen fields are the plan's 6.1 shape; the rest serve the chips."""

    answer_span: list | None  # [start, end] in the displayed reply, proposed edit or cited block text
    chunk_id: str | None
    document_id: str | None
    library_id: str | None
    block_id: str | None  # the source block, when the chunk locator names one
    source_span: list | None  # [start, end] within the chunk text
    quote: str  # the source text at source_span
    prefix: str  # up to 32 characters of source text before the quote, for re-anchoring
    suffix: str
    content_hash: str | None  # source version at citation time
    origin: str  # native | token | user | model (uncited AI sentence)
    state: str = 'unverified'
    checks: list = field(default_factory=list)
    citation_id: str | None = None  # the cited chunk or reference ID as the model or writer gave it
    valid_id: bool = True
    claim: str = ''  # the answer text the citation is attached to
    claimed_quote: str | None = None  # a quotation the claim makes, or native cited text
    locator: dict = field(default_factory=dict)
    title: str | None = None
    path: str | None = None  # library-relative file path, to follow the source after it changes
    marker: int | None = None  # where the chip sits in the displayed reply
    reason: str | None = None

    def to_dict(self):
        return copy.deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, GroundingRecord):
            return copy.deepcopy(value)
        known = {item.name for item in fields(cls)}
        return cls(**{key: copy.deepcopy(item) for key, item in value.items() if key in known})

    @property
    def key(self):
        return self.chunk_id or self.citation_id


# --- building records ---------------------------------------------------------------------------


def _passages(passages):
    return {p['chunk_id']: p for p in passages or [] if isinstance(p, dict) and p.get('chunk_id')}


def _reference_text(source):
    from .library_checks import source_texts

    return '\n\n'.join(text for text in source_texts(source) if text)


def source_texts(passages=None, references=None):
    """``{citation key: source text}`` for :func:`verify`: chunk text and reference excerpts."""
    texts = {key: p.get('text', '') for key, p in _passages(passages).items()}
    for ident, source in (references or {}).items():
        if isinstance(source, dict) and ident not in texts:
            texts[ident] = _reference_text(source)
    return texts


def _title(passage):
    return (
        passage.get('title')
        or passage.get('path')
        or passage.get('library_name')
        or passage.get('document_id')
    )


def _context(text, span):
    start, end = span
    return text[max(0, start - CONTEXT_CHARS) : start], text[end : end + CONTEXT_CHARS]


def _source_span(text, claimed, claim):
    """The cited span in the chunk: the claimed quote located, else the claim fuzzily aligned."""
    from .quote_match import align, locate

    if claimed:
        found = locate(claimed, text)
        if found['span']:
            return found['span']
    if claim:
        aligned = align(claim, text, PARTIAL_THRESHOLD)
        if aligned:
            return aligned['span']
    return None


def _from_passage(passage, *, origin, claim, claimed, answer_span, marker, citation_id):
    text = passage.get('text', '') or ''
    span = _source_span(text, claimed, claim)
    prefix, suffix = _context(text, span) if span else ('', '')
    locator = {**(passage.get('locator') or {}), 'chunk': passage['chunk_id']}
    return GroundingRecord(
        answer_span=answer_span,
        chunk_id=passage['chunk_id'],
        document_id=passage.get('document_id'),
        library_id=passage.get('library_id'),
        block_id=(passage.get('locator') or {}).get('block'),
        source_span=list(span) if span else None,
        quote=text[span[0] : span[1]] if span else (claimed or ''),
        prefix=prefix,
        suffix=suffix,
        content_hash=passage.get('content_hash'),
        origin=origin,
        citation_id=citation_id,
        claim=claim,
        claimed_quote=claimed,
        locator=locator,
        title=_title(passage),
        path=passage.get('path'),
        marker=marker,
    )


def _from_reference(ident, source, *, origin, claim, claimed, answer_span, marker):
    text = _reference_text(source)
    span = _source_span(text, claimed, claim) if text else None
    prefix, suffix = _context(text, span) if span else ('', '')
    library = source.get('library') or {}
    return GroundingRecord(
        answer_span=answer_span,
        chunk_id=library.get('chunk_id') or None,
        document_id=library.get('document_id'),
        library_id=library.get('library_id'),
        block_id=None,
        source_span=list(span) if span else None,
        quote=text[span[0] : span[1]] if span else (claimed or ''),
        prefix=prefix,
        suffix=suffix,
        content_hash=library.get('content_hash'),
        origin=origin,
        citation_id=ident,
        claim=claim,
        claimed_quote=claimed,
        locator={},
        title=(source.get('csl') or {}).get('title') or source.get('title') or ident,
        path=library.get('path') or None,
        marker=marker,
    )


def _invalid(ident, *, origin, claim, answer_span, marker, reason):
    return GroundingRecord(
        answer_span=answer_span,
        chunk_id=None,
        document_id=None,
        library_id=None,
        block_id=None,
        source_span=None,
        quote='',
        prefix='',
        suffix='',
        content_hash=None,
        origin=origin,
        state='unverified',
        citation_id=ident,
        valid_id=False,
        claim=claim,
        marker=marker,
        reason=reason,
    )


def _claim_span(text, marker):
    """The sentence a citation at ``marker`` is attached to: back to the previous sentence end."""
    end = marker
    while end > 0 and text[end - 1].isspace():
        end -= 1
    probe = end
    while probe > 0 and text[probe - 1] in '"”’)]':
        probe -= 1
    if probe > 0 and text[probe - 1] in '.!?':
        probe -= 1
    start = 0
    for match in _SENTENCE_END.finditer(text, 0, probe):
        start = match.end()
    return [start, end]


def claimed_quotation(claim):
    """A quotation inside a claim (text between quotation marks), or None."""
    match = _QUOTE_IN.search(claim or '')
    return match.group(1) if match else None


_ABBREVIATIONS = frozenset(
    [
        'mr',
        'mrs',
        'ms',
        'dr',
        'prof',
        'sr',
        'jr',
        'st',
        'mt',
        'vs',
        'etc',
        'al',
        'fig',
        'figs',
        'eq',
        'no',
        'nos',
        'vol',
        'vols',
        'ch',
        'sec',
        'pp',
        'ed',
        'eds',
        'cf',
        'approx',
        'dept',
        'inc',
        'ltd',
        'co',
        'corp',
        'gen',
        'col',
        'lt',
        'capt',
        'sgt',
        'rev',
        'hon',
        'gov',
        'sen',
        'rep',
        'ca',
        'viz',
    ]
)
_ABBREVIATION_PHRASES = ('e.g.', 'i.e.', 'a.m.', 'p.m.', 'u.s.', 'u.k.', 'ph.d.', 'cf.')
_BOUNDARY = re.compile(r'[.!?\u2026]+[\"\u201d\u2019\')\]]*(?=\s)|\n[ \t]*\n')
_CLAUSE_JOINERS = re.compile(
    r';\s+|,\s+(?=(?:but|yet|whereas|while|although|though|however|and|so)\b)|\s+(?=(?:whereas|although)\b)',
    re.IGNORECASE,
)
MIN_CLAUSE_WORDS = 4


def _opens_sentence(text, position):
    """Whether the text after ``position`` starts like a new sentence (a capital, digit, quote or bracket)."""
    rest = text[position:].lstrip()
    return bool(rest) and (rest[0].isupper() or rest[0].isdigit() or rest[0] in '"\u201c\u2018\'([')


def _is_abbreviation(text, boundary_start):
    word = re.search(r'([\w.]+)$', text[:boundary_start])
    if not word:
        return False
    token = word.group(1).lower()
    if any(text[: boundary_start + 1].lower().endswith(p) for p in _ABBREVIATION_PHRASES):
        return True
    # A single capital initial ("J. Smith") and a listed abbreviation are not sentence ends.
    return token.rstrip('.') in _ABBREVIATIONS or (
        len(token) == 1 and token.isalpha() and word.group(1).isupper()
    )


def _sentence_spans(text):
    spans, start = [], 0
    for match in _BOUNDARY.finditer(text):
        end = match.end()
        group = match.group()
        if group[0] != '\n':
            if not _opens_sentence(text, end):
                continue
            if group[0] == '.' and not group.startswith('..') and _is_abbreviation(text, match.start()):
                continue
        spans.append((start, end))
        start = end
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _outside_quotes(text, start, end):
    """Positions in ``text[start:end]`` that are not inside quotation marks or brackets."""
    depth, quoted, free = 0, False, set()
    for index in range(start, end):
        char = text[index]
        if char in '"\u201c\u201d':
            quoted = not quoted if char == '"' else char == '\u201c'
        elif char in '([':
            depth += 1
        elif char in ')]':
            depth = max(0, depth - 1)
        elif not quoted and depth == 0:
            free.add(index)
    return free


def split_claims(text, *, clauses=True):
    """Split ``text`` into claims for a later entailment or judge stage (plan Phase 5).

    Sentences first (abbreviations such as ``Dr.``, ``e.g.`` and ``et al.``, initials and decimals do
    not end one); then, with ``clauses``, a sentence is split at a semicolon or at ``, but`` / ``, yet``
    / ``, and`` / ``, so`` / ``whereas`` / ``although`` when each side has at least four words, never
    inside quotation marks or brackets. Returns ``[{'text', 'start', 'end', 'sentence', 'clause'}]``
    in order; ``text[start:end] == item['text']`` always holds, and ``clause`` is the position of the
    piece within its sentence (0 when the sentence was not split).
    """
    if not isinstance(text, str):
        raise ByaxError('invalid_input', 'Claim text must be a string')
    claims = []
    for number, (begin, end) in enumerate(_sentence_spans(text)):
        pieces = [(begin, end)]
        if clauses:
            free = _outside_quotes(text, begin, end)
            cuts = [m for m in _CLAUSE_JOINERS.finditer(text, begin, end) if m.start() in free]
            pieces, last = [], begin
            for cut in cuts:
                left, right = text[last : cut.start()], text[cut.end() : end]
                if len(left.split()) >= MIN_CLAUSE_WORDS and len(right.split()) >= MIN_CLAUSE_WORDS:
                    pieces.append((last, cut.start() + (1 if text[cut.start()] == ';' else 0)))
                    last = cut.end()
            pieces.append((last, end))
        for clause, (a, b) in enumerate(pieces):
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if a < b:
                claims.append(
                    {
                        'text': text[a:b],
                        'start': a,
                        'end': b,
                        'sentence': number,
                        'clause': clause if len(pieces) > 1 else 0,
                    }
                )
    return claims


def from_tokens(text, passages=None, references=None):
    """Records for the ``[[cite:ID]]`` tokens in ``text``.

    Returns ``{'text', 'records', 'invalid'}``: ``text`` is the reply with every token removed (the
    chip markers say where they were), ``records`` one :class:`GroundingRecord` per token, and
    ``invalid`` the IDs that were not supplied in the request. An invalid token never rejects the
    reply; its record is ``unverified``. The source span of a valid token is the claim's quotation
    located in the chunk, or the claim sentence fuzzily aligned with it (:func:`quote_match.align`).
    """
    if not isinstance(text, str):
        raise ByaxError('invalid_input', 'Citation text must be a string')
    by_chunk, references = _passages(passages), references or {}
    pieces, tokens, removed, length, cursor = [], [], [], 0, 0
    for match in CITE_TOKEN.finditer(text):
        piece = text[cursor : match.start()]
        following = text[match.end() : match.end() + 1]
        if not following.isalnum():
            piece = piece.rstrip(' \t')
        removed.append((cursor + len(piece), match.end()))
        pieces.append(piece)
        length += len(piece)
        tokens.append((match.group(1).strip(), length))
        cursor = match.end()
    pieces.append(text[cursor:])
    shown = ''.join(pieces)
    records, invalid = [], []
    for ident, marker in tokens:
        span = _claim_span(shown, marker)
        claim = shown[span[0] : span[1]].strip()
        claimed = claimed_quotation(claim)
        common = {'origin': 'token', 'claim': claim, 'answer_span': span, 'marker': marker}
        if ident in by_chunk:
            records.append(_from_passage(by_chunk[ident], claimed=claimed, citation_id=ident, **common))
        elif isinstance(references.get(ident), dict):
            records.append(_from_reference(ident, references[ident], claimed=claimed, **common))
        else:
            invalid.append(ident)
            records.append(
                _invalid(
                    ident,
                    reason='The model cited an ID that was not supplied with this request; the token was removed.',
                    **common,
                )
            )
    return {'text': shown, 'records': records, 'invalid': list(dict.fromkeys(invalid)), 'removed': removed}


def _shifted(position, removed):
    """A position in the original text mapped into the text with ``removed`` ranges taken out."""
    return position - sum(min(end, position) - start for start, end in removed if start < position)


def ground_reply(text, *, passages=None, references=None, native=None, stages=DEFAULT_STAGES):
    """Records for one assistant reply, verified by the cascade: what providers attach to a result.

    ``native`` is the output of :func:`from_anthropic` (or another native adapter) when the provider
    returned native citations; any ``[[cite:ID]]`` tokens in the same text (for supplied reference
    IDs) are recorded too and the native spans are shifted to the displayed text. Returns
    ``{'text', 'records', 'invalid', 'origin', 'summary', 'stages'}`` with records as dicts.
    """
    tokens = from_tokens(text, passages, references)
    records = list(tokens['records'])
    invalid = list(tokens['invalid'])
    if native:
        for record in native['records']:
            if record.answer_span:
                record.answer_span = [_shifted(p, tokens['removed']) for p in record.answer_span]
            if record.marker is not None:
                record.marker = _shifted(record.marker, tokens['removed'])
            records.append(record)
        invalid += [ident for ident in native['invalid'] if ident not in invalid]
        records.sort(key=lambda r: r.marker if r.marker is not None else 0)
    verified = verify(records, stages, texts=source_texts(passages, references))
    return {
        'text': tokens['text'],
        'records': [record.to_dict() for record in verified],
        'invalid': invalid,
        'token_invalid': tokens['invalid'],
        'origin': 'native' if native else 'token',
        'summary': summarize(verified),
        'stages': [name for name in STAGE_ORDER if name in stages],
    }


def strip_invalid_tokens(text, allowed):
    """``(text, invalid)``: tokens whose ID is not in ``allowed`` removed, valid tokens kept."""
    invalid = []

    def replace(match):
        ident = match.group(1).strip()
        if ident in allowed:
            return match.group(0)
        invalid.append(ident)
        return ''

    cleaned = CITE_TOKEN.sub(replace, text)
    cleaned = re.sub(r'[ \t]+([.,;:!?)])', r'\1', cleaned) if invalid else cleaned
    return cleaned, list(dict.fromkeys(invalid))


# --- native citations ---------------------------------------------------------------------------


def anthropic_documents(passages):
    """Group library passages into Anthropic custom-content documents, one content block per chunk.

    Returns ``[{'title', 'library_id', 'document_id', 'passages': [...]}]`` in first-seen order; the
    request builder sends each as a ``document`` block and :func:`from_anthropic` maps
    ``document_index`` and block indices back through the same list.
    """
    documents, index = [], {}
    for passage in passages or []:
        if not isinstance(passage, dict) or not passage.get('chunk_id'):
            continue
        key = (passage.get('library_id'), passage.get('document_id'))
        if key not in index:
            index[key] = len(documents)
            documents.append(
                {
                    'title': str(_title(passage) or 'Library passage'),
                    'library_id': key[0],
                    'document_id': key[1],
                    'passages': [],
                }
            )
        documents[index[key]]['passages'].append(passage)
    return documents


def _cited_span(text, cited):
    """Span of the cited text in a chunk; a whole chunk when the citation covers several blocks."""
    from .quote_match import locate, normalize

    if not cited:
        return None
    found = locate(cited, text)
    if found['span'] and found['match'] in ('exact', 'normalized'):
        return found['span']
    if normalize(text)[0].strip() and normalize(text)[0].strip() in normalize(cited)[0]:
        return [0, len(text)]
    return found['span']


def _native_record(passage, *, claim, cited, answer_span, marker):
    record = _from_passage(
        passage,
        origin='native',
        claim=claim,
        claimed=None,
        answer_span=answer_span,
        marker=marker,
        citation_id=passage['chunk_id'],
    )
    record.claimed_quote = cited or None
    text = passage.get('text', '') or ''
    span = _cited_span(text, cited)
    if span:
        record.source_span = list(span)
        record.quote = text[span[0] : span[1]]
        record.prefix, record.suffix = _context(text, span)
        if list(span) == [0, len(text)] and cited.strip() != text.strip():
            # A citation across several blocks: this chunk's share of the cited text is all of it.
            record.claimed_quote = text
    elif cited:
        record.source_span, record.quote, record.prefix, record.suffix = None, cited, '', ''
    return record


def _trimmed(start, text):
    end = start + len(text.rstrip())
    start += len(text) - len(text.lstrip())
    return [start, max(start, end)]


def from_anthropic(raw, documents):
    """Records from an Anthropic Messages response that used custom-content document citations.

    ``documents`` is the list :func:`anthropic_documents` built for the request. Each
    ``content_block_location`` citation maps ``document_index`` to a document and
    ``start_block_index``/``end_block_index`` (end exclusive) to chunks; ``cited_text`` becomes the
    claimed quote, checked against the chunk by stage 1. Anything that does not map is
    ``unverified``. Returns ``{'text', 'records', 'invalid'}`` like :func:`from_tokens`.
    """
    parts, records, invalid, offset = [], [], [], 0
    content = raw.get('content') if isinstance(raw, dict) else None
    for block in content if isinstance(content, list) else []:
        if (
            not isinstance(block, dict)
            or block.get('type') != 'text'
            or not isinstance(block.get('text'), str)
        ):
            continue
        text = block['text']
        span = _trimmed(offset, text)
        offset += len(text)
        parts.append(text)
        claim = text.strip()
        for citation in block.get('citations') or []:
            common = {'claim': claim, 'answer_span': span, 'marker': span[1]}
            cited = citation.get('cited_text') if isinstance(citation, dict) else None
            cited = cited if isinstance(cited, str) else ''
            location = citation.get('type') if isinstance(citation, dict) else None
            first = citation.get('start_block_index') if isinstance(citation, dict) else None
            last = citation.get('end_block_index') if isinstance(citation, dict) else None
            number = citation.get('document_index') if isinstance(citation, dict) else None
            valid = (
                location == 'content_block_location'
                and type(number) is int
                and 0 <= number < len(documents)
                and type(first) is int
                and type(last) is int
                and 0 <= first < len(documents[number]['passages'])
            )
            if not valid:
                ident = f'document {number}' if type(number) is int else str(location)
                invalid.append(ident)
                records.append(
                    _invalid(
                        ident,
                        origin='native',
                        reason='The provider returned a citation location that does not match a supplied passage.',
                        **common,
                    )
                )
                continue
            chunks = documents[number]['passages']
            for index in range(first, min(max(first + 1, last), len(chunks))):
                records.append(_native_record(chunks[index], cited=cited, **common))
    return {'text': ''.join(parts), 'records': records, 'invalid': list(dict.fromkeys(invalid))}


def _char_offset(text, byte_offset):
    """Gemini segment offsets are UTF-8 byte offsets; convert to Unicode scalar offsets."""
    data = text.encode('utf-8')
    byte_offset = max(0, min(len(data), int(byte_offset or 0)))
    return len(data[:byte_offset].decode('utf-8', errors='ignore'))


def _passage_for(context, passages, index):
    """Match a provider's source reference to a supplied passage by chunk ID, then by position.

    Position is used only when the reference carries no URI or ID of its own; a reference that
    names something else (a web page, an unknown document) never borrows a supplied passage.
    """
    by_chunk = _passages(passages)
    for value in (context.get('uri'), context.get('title'), context.get('id'), context.get('chunk_id')):
        if isinstance(value, str):
            ident = value.rsplit('/', 1)[-1] if value.startswith('byax:') else value
            if ident in by_chunk:
                return by_chunk[ident]
    if any(isinstance(context.get(key), str) for key in ('uri', 'id', 'chunk_id')):
        return None
    ordered = [p for p in passages or [] if isinstance(p, dict) and p.get('chunk_id')]
    return ordered[index] if isinstance(index, int) and 0 <= index < len(ordered) else None


def from_gemini(raw, passages):
    """Records from a Gemini ``groundingMetadata`` (grounding supports mapped like native citations).

    ``groundingSupports[].segment`` gives the answer span (UTF-8 byte offsets, converted here) and
    ``groundingChunkIndices`` the chunks; a chunk is matched to a supplied passage by a
    ``byax:chunk/<id>`` URI or title, else by its position in the supplied passages. The adapter is
    tested against shape fixtures; the Gemini chat request does not ask for grounding yet.
    """
    candidate = (raw.get('candidates') or [{}])[0] if isinstance(raw, dict) else {}
    parts = (candidate.get('content') or {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts if isinstance(p, dict))
    metadata = candidate.get('groundingMetadata') or {}
    chunks = metadata.get('groundingChunks') or []
    records, invalid = [], []
    for support in metadata.get('groundingSupports') or []:
        segment = support.get('segment') or {}
        start = _char_offset(text, segment.get('startIndex', 0))
        end = _char_offset(text, segment.get('endIndex', 0))
        span = _trimmed(start, text[start:end])
        claim = text[span[0] : span[1]]
        for index in support.get('groundingChunkIndices') or []:
            chunk = chunks[index] if type(index) is int and 0 <= index < len(chunks) else {}
            source = chunk.get('retrievedContext') or chunk.get('web') or {}
            passage = _passage_for(source, passages, index)
            common = {'claim': claim, 'answer_span': span, 'marker': span[1]}
            if passage is None:
                invalid.append(f'grounding chunk {index}')
                records.append(
                    _invalid(
                        f'grounding chunk {index}',
                        origin='native',
                        reason='The grounding chunk does not match a supplied passage.',
                        **common,
                    )
                )
                continue
            cited = source.get('text') if isinstance(source.get('text'), str) else None
            record = _native_record(passage, cited=cited or '', **common)
            if not cited:
                record = _from_passage(
                    passage, origin='native', claimed=None, citation_id=passage['chunk_id'], **common
                )
            records.append(record)
    return {'text': text, 'records': records, 'invalid': list(dict.fromkeys(invalid))}


def from_cohere(raw, passages):
    """Records from a Cohere v2 chat response: ``message.citations[]`` with character spans.

    Each citation's ``start``/``end`` is the answer span and each ``sources[]`` entry of type
    ``document`` names a supplied passage by ``id`` (the chunk ID the request used as document ID).
    Tested against shape fixtures; there is no Cohere provider adapter yet.
    """
    message = (raw.get('message') or {}) if isinstance(raw, dict) else {}
    text = ''.join(
        part.get('text', '')
        for part in message.get('content') or []
        if isinstance(part, dict) and part.get('type') == 'text'
    )
    records, invalid = [], []
    for citation in message.get('citations') or []:
        start, end = citation.get('start'), citation.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
            start, end = 0, 0
        span = _trimmed(start, text[start:end])
        claim = text[span[0] : span[1]]
        for source in citation.get('sources') or []:
            document = source.get('document') or {}
            reference = {
                'id': source.get('id'),
                'chunk_id': document.get('chunk_id'),
                'title': document.get('title'),
            }
            passage = _passage_for(reference, passages, None)
            common = {'claim': claim, 'answer_span': span, 'marker': span[1]}
            if passage is None:
                ident = str(source.get('id'))
                invalid.append(ident)
                records.append(
                    _invalid(ident, origin='native', reason='The cited document was not supplied.', **common)
                )
                continue
            records.append(_native_record(passage, cited=citation.get('text') or '', **common))
    return {'text': text, 'records': records, 'invalid': list(dict.fromkeys(invalid))}


# --- verification cascade ------------------------------------------------------------------------


def _quote_stage(record, text):
    """Stage 1a: a claimed quotation must be found in the cited chunk (exact, normalized or near)."""
    from .quote_match import describe_differences, locate

    if not record.claimed_quote:
        return None
    found = locate(record.claimed_quote, text)
    check = {'stage': 'quote', 'model': None, 'match': found['match'], 'score': found['score']}
    if found['match'] in ('exact', 'normalized'):
        return {**check, 'result': 'supported', 'span': found['span']}
    if found['match'] == 'near':
        return {
            **check,
            'result': 'partial',
            'span': found['span'],
            'differences': found['differences'],
            'summary': describe_differences(found['differences']),
        }
    return {**check, 'result': 'unsupported'}


def _fuzzy_stage(record, text):
    """Stage 1b: an unquoted claim aligned with the chunk. Close wording only; never meaning."""
    from .quote_match import NEAR_THRESHOLD, align

    if any(
        c.get('stage') == 'quote' and c.get('result') in ('supported', 'partial', 'unsupported')
        for c in record.checks
    ):
        return None
    if not record.claim:
        return {'stage': 'fuzzy', 'model': None, 'result': 'not_checkable', 'reason': 'No claim text.'}
    aligned = align(record.claim, text)
    score = aligned['score'] if aligned else 0.0
    check = {'stage': 'fuzzy', 'model': None, 'score': score, 'span': aligned['span'] if aligned else None}
    if score >= NEAR_THRESHOLD:
        return {
            **check,
            'result': 'supported',
            'basis': 'The claim repeats the cited passage nearly verbatim.',
        }
    if score >= PARTIAL_THRESHOLD:
        return {
            **check,
            'result': 'partial',
            'basis': 'The claim shares part of the cited wording; its meaning is not checked.',
        }
    return {
        **check,
        'result': 'not_checkable',
        'basis': 'A paraphrase; deterministic matching cannot judge support (needs the entailment stage).',
    }


_STAGES = {'quote': _quote_stage, 'fuzzy': _fuzzy_stage}


def register_stage(name, check):
    """Install a cascade stage (``entailment`` and ``judge`` arrive in plan Phase 5).

    ``check(record, source_text)`` returns ``None`` to abstain, or a dict with ``stage``,
    ``result`` (``supported`` | ``partial`` | ``unsupported`` | ``not_checkable``) and ``model``.
    ``check=None`` removes the stage.
    """
    if name not in STAGE_ORDER:
        raise ByaxError(
            'invalid_input', 'Unknown verification stage', stage=name, supported=list(STAGE_ORDER)
        )
    if check is None:
        _STAGES.pop(name, None)
        if name in DEFAULT_STAGES:
            _STAGES[name] = {'quote': _quote_stage, 'fuzzy': _fuzzy_stage}[name]
    else:
        _STAGES[name] = check


def available_stages():
    return [name for name in STAGE_ORDER if name in _STAGES]


def verify(records, stages=DEFAULT_STAGES, *, texts=None, overrides=None):
    """Run the cascade over ``records`` and return updated copies.

    ``texts`` maps each record's key (chunk ID, else citation ID) to the source text it cites, for
    example :func:`source_texts` of the request's passages. A record with an ID that was not supplied
    stays ``unverified``; one whose source text is unavailable becomes ``unanchored``. Each stage's
    outcome is appended to ``checks``; the state is the last decisive result. Stages that are not
    installed are listed as ``not_run``, never silently treated as passed. ``overrides`` maps stage
    names to checks used for this call only (the optional LLM judge is built per request, see
    :mod:`byax.grounding_judge`). Records that already carry checks keep their earlier result, so a
    later stage can be run over verified records without repeating the first ones.
    """
    if isinstance(stages, str):
        stages = (stages,)
    unknown = [name for name in stages if name not in STAGE_ORDER]
    if unknown:
        raise ByaxError(
            'invalid_input', 'Unknown verification stage', stages=unknown, supported=list(STAGE_ORDER)
        )
    texts = texts or {}
    results = []
    for item in records:
        record = GroundingRecord.from_dict(item)
        if not record.valid_id:
            record.state = 'unverified'
            results.append(record)
            continue
        text = texts.get(record.key) if record.key else None
        if not text:
            record.state = 'unanchored'
            record.reason = record.reason or 'The cited source text is not available to check.'
            results.append(record)
            continue
        decided = current_result(record)
        for name in sorted(dict.fromkeys(stages), key=STAGE_ORDER.index):
            stage = (overrides or {}).get(name) or _STAGES.get(name)
            if stage is None:
                record.checks.append(
                    {
                        'stage': name,
                        'result': 'not_run',
                        'model': None,
                        'reason': 'This verification stage is not installed (context engine plan Phase 5).',
                    }
                )
                continue
            check = stage(record, text)
            if check:
                record.checks.append(check)
                if check.get('result') in _DECISIVE:
                    decided = check['result']
        record.state = decided or 'unverified'
        results.append(record)
    return results


def current_result(record):
    """The last decisive result among a record's checks, else its decisive state, else None."""
    record = GroundingRecord.from_dict(record)
    decided = None
    for check in record.checks:
        if check.get('result') in _DECISIVE:
            decided = check['result']
    return decided or (record.state if record.state in _DECISIVE else None)


def summarize(records):
    """Counts per state, for transcripts and reports."""
    counts = dict.fromkeys(STATES, 0)
    for record in records:
        state = record.state if isinstance(record, GroundingRecord) else record.get('state')
        counts[state] = counts.get(state, 0) + 1
    return counts


# --- source access and re-anchoring --------------------------------------------------------------


def _document_chunks(index, document_id):
    """Every indexed chunk of one library document, in order (bounded pages of the index read)."""
    chunks, offset = [], 0
    for _ in range(10000):
        page = index.read(document_id, limit=1_000_000, offset=offset)
        chunks.extend(page['results'])
        if page.get('continuation') is None:
            return chunks, page
        offset = page['continuation']
    return chunks, page


def _current_document(index, pointer):
    """``(document_id, chunks, page)`` for the library document a pointer names, following a change.

    The pointer's own document when it is still indexed; else the document now indexed at the
    recorded path (content-hash identities change when the file changes); else ``None``.
    """
    try:
        chunks, page = _document_chunks(index, pointer.get('document_id'))
        return pointer.get('document_id'), chunks, page
    except ByaxError:
        pass
    path = pointer.get('path') or pointer.get('relative_path')
    for row in index.status()['files']:
        if path and row.get('path') == path and row.get('document_id'):
            try:
                chunks, page = _document_chunks(index, row['document_id'])
            except ByaxError:
                return None
            return row['document_id'], chunks, page
    return None


def _structure_keys(locator):
    """Structural locator keys that actually name a place (an empty heading path does not)."""
    return [key for key in STRUCTURE_KEYS if locator.get(key) not in (None, '', [])]


def _structural(chunk, locator):
    keys = _structure_keys(locator)
    return bool(keys) and all(str(chunk['locator'].get(key)) == str(locator[key]) for key in keys)


def reanchor_chunks(anchor, chunks, locator=None, threshold=None):
    """Re-find an anchored quote among a changed source's chunks (context engine plan 6.5).

    Order: structure (chunks with the same page, block, paragraph or heading path), then within
    each the recorded position, fuzzy search near it and anywhere in the chunk
    (:func:`quote_match.reanchor`), then fuzzy search across every chunk. Returns
    ``{'chunk', 'span', 'method', 'structure', 'score'}`` or None when every step fails.
    """
    from .quote_match import reanchor

    if not anchor.get('quote'):
        return None
    locator = locator or {}
    anchor = {**anchor, 'position': anchor.get('position') or {'start': 0, 'end': 0}}
    candidates = [c for c in chunks if _structural(c, locator)]
    for chunk in candidates:
        found = reanchor(anchor, chunk['text'], threshold)
        if found:
            return {
                'chunk': chunk,
                'span': list(found.span),
                'method': found.method,
                'structure': True,
                'score': found.score,
            }
    loose = {**anchor, 'position': {'start': 0, 'end': 0}}
    for chunk in chunks:
        if chunk in candidates:
            continue
        found = reanchor(loose, chunk['text'], threshold)
        if found:
            return {
                'chunk': chunk,
                'span': list(found.span),
                'method': 'anywhere',
                'structure': False,
                'score': found.score,
            }
    return None


def _library_index(library_id, registry, cache_dir):
    from .libraries import LibraryIndex, LibraryRegistry

    registry = registry or LibraryRegistry()
    library = registry.get(library_id)
    return library, LibraryIndex(library, cache_dir=cache_dir)


def locate_record(record, *, registry=None, cache_dir=None):
    """Where a record's citation opens: the library chunk, the span in it, and whether it moved.

    ``status`` is ``available`` (same source version, chunk found), ``reanchored`` (the source
    changed and re-anchoring found the quote), ``unanchored`` (changed and not re-found) or
    ``unavailable``. Reads only the local library index.
    """
    record = GroundingRecord.from_dict(record)
    base = {'record': record.to_dict(), 'library_id': record.library_id, 'title': record.title}
    if not record.library_id or not record.document_id:
        return {
            **base,
            'status': 'unavailable',
            'reason': 'This citation does not point at a library passage.',
        }
    try:
        library, index = _library_index(record.library_id, registry, cache_dir)
    except (ByaxError, OSError) as exc:
        return {**base, 'status': 'unavailable', 'reason': str(exc)}
    pointer = {'document_id': record.document_id, 'path': record.path}
    try:
        page = index.read(record.document_id, chunk_id=record.chunk_id)
        chunk = page['results'][0]
    except (ByaxError, IndexError):
        chunk, page = None, None
    if chunk is not None and page['content_hash'] == record.content_hash and not page.get('stale'):
        span = record.source_span or [0, 0]
        return {
            **base,
            'status': 'available',
            'library': library,
            'document_id': record.document_id,
            'chunk': chunk,
            'span': list(span),
            'locator': {**chunk['locator'], 'chunk': chunk['chunk_id']},
            'paths': page.get('paths', []),
        }
    current = (
        _current_document(index, pointer)
        if chunk is None
        else (record.document_id, *_document_chunks(index, record.document_id))
    )
    if not current:
        return {**base, 'status': 'unavailable', 'reason': 'The cited library document is no longer indexed.'}
    document_id, chunks, page = current
    anchor = {
        'quote': record.quote,
        'prefix': record.prefix,
        'suffix': record.suffix,
        'position': {'start': (record.source_span or [0, 0])[0], 'end': (record.source_span or [0, 0])[1]},
    }
    found = reanchor_chunks(anchor, chunks, record.locator)
    if not found:
        return {
            **base,
            'status': 'unanchored',
            'library': library,
            'document_id': document_id,
            'reason': 'The source changed and the cited text could not be found again.',
        }
    return {
        **base,
        'status': 'reanchored',
        'library': library,
        'document_id': document_id,
        'chunk': found['chunk'],
        'span': found['span'],
        'method': found['method'],
        'locator': {**found['chunk']['locator'], 'chunk': found['chunk']['chunk_id']},
        'paths': page.get('paths', []),
    }


# --- audit of the writer's own citations (plan 6.4) ----------------------------------------------


def _claimed_quote(text, mark):
    from .quote_match import quoted_passage

    anchor = mark.get('anchor') if isinstance(mark.get('anchor'), dict) else {}
    if anchor.get('quote'):
        return anchor['quote']
    if mark.get('kind') in ('paraphrase', 'inference') and quoted_passage(text) is None:
        return None
    quoted = quoted_passage(text)
    if quoted is not None:
        return quoted
    return (mark.get('quote') or text) if mark.get('kind') == 'quotation' else None


def _locator(locator):
    return {k: copy.deepcopy(v) for k, v in (locator or {}).items() if k in LOCATOR_KEYS}


def _match(quote, passages):
    """Best quote_match result over passages: ``(passage, result)`` preferring exact, then nearest."""
    from .quote_match import locate

    best = None
    rank = {'exact': 3, 'normalized': 2, 'near': 1, 'none': 0}
    for passage in passages:
        found = locate(quote, passage.get('text', ''))
        if best is None or (rank[found['match']], found['score']) > (
            rank[best[1]['match']],
            best[1]['score'],
        ):
            best = (passage, found)
    return best


def _audit_item(block, mark, doc):
    text = block.get('text', '')[mark['start'] : mark['end']]
    source = doc.get('references', {}).get(mark.get('value'), {})
    return {
        'target': block['id'],
        'start': mark['start'],
        'end': mark['end'],
        'text': text,
        'source_id': mark.get('value'),
        'title': (source.get('csl') or {}).get('title') or source.get('title') or mark.get('value'),
        'kind': mark.get('kind', 'paraphrase'),
        'locator': copy.deepcopy(mark.get('locator', {})),
        'quote': _claimed_quote(text, mark),
        'anchor_state': (mark.get('anchor') or {}).get('state', 'anchored') if mark.get('anchor') else None,
    }


def _user_record(item, status, *, passage=None, span=None, pointer=None, check=None):
    pointer = pointer or {}
    text = (passage or {}).get('text', '')
    prefix, suffix = _context(text, span) if passage is not None and span else ('', '')
    record = GroundingRecord(
        answer_span=[item['start'], item['end']],
        chunk_id=(passage or {}).get('chunk_id') or item['locator'].get('chunk'),
        document_id=(passage or {}).get('document_id') or pointer.get('document_id'),
        library_id=pointer.get('library_id'),
        block_id=((passage or {}).get('locator') or {}).get('block') or item['locator'].get('block'),
        source_span=list(span) if span else None,
        quote=text[span[0] : span[1]] if passage is not None and span else (item['quote'] or ''),
        prefix=prefix,
        suffix=suffix,
        content_hash=pointer.get('content_hash'),
        origin='user',
        state=_AUDIT_STATES[status],
        checks=[check] if check else [],
        citation_id=item['source_id'],
        claim=item['text'],
        claimed_quote=item['quote'],
        locator=item['locator'],
        title=item['title'],
        path=pointer.get('path') or None,
    )
    return record.to_dict()


def _quote_outcome(item, passages, pointer, everything=None):
    """quote_found / near_match / quote_not_found for a claimed quote against the cited passages."""
    from .quote_match import describe_differences

    best = _match(item['quote'], passages) if passages else None
    if best and best[1]['match'] in ('exact', 'normalized'):
        passage, found = best
        status, extra = 'quote_found', {'match': found['match'], 'raw_pass': found['raw_pass']}
    elif best and best[1]['match'] == 'near':
        passage, found = best
        status = 'near_match'
        extra = {
            'match': 'near',
            'score': found['score'],
            'differences': found['differences'],
            'message': 'Near match: ' + describe_differences(found['differences']),
        }
    else:
        passage, found = best or (None, {'match': 'none', 'span': None, 'score': 0.0})
        status, extra = 'quote_not_found', {'match': 'none'}
        elsewhere = [
            {**_locator(p.get('locator')), 'chunk': p['chunk_id']}
            for p in everything or []
            if p not in passages and _match(item['quote'], [p])[1]['match'] in ('exact', 'normalized')
        ]
        if elsewhere:
            extra['found_elsewhere'] = elsewhere[:5]
            extra['message'] = 'The quotation is not at the cited locator but occurs elsewhere in the source.'
    check = {'stage': 'quote', 'model': None, 'match': found['match'], 'score': found['score']}
    item.update(extra)
    item['status'] = status
    item['record'] = _user_record(
        item, status, passage=passage, span=found.get('span'), pointer=pointer, check=check
    )
    return item


def _filter_locator(chunks, locator, same_version):
    if same_version and locator.get('chunk'):
        exact = [c for c in chunks if c['chunk_id'] == locator['chunk']]
        if exact:
            return exact
    keys = _structure_keys(locator)
    if keys:
        return [c for c in chunks if all(str(c['locator'].get(k)) == str(locator[k]) for k in keys)]
    return list(chunks)


def _audit_library(item, mark, source, doc, registry, cache_dir, operations, moved):
    pointer = source['library']
    attached = {a.get('library_id') for a in doc.get('settings', {}).get('libraries', [])}
    unavailable = {'status': 'source_unavailable'}
    if pointer.get('library_id') not in attached:
        item.update(unavailable, message='The cited library is not attached to this document.')
        item['record'] = _user_record(item, 'source_unavailable', pointer=pointer)
        return item
    try:
        _, index = _library_index(pointer['library_id'], registry, cache_dir)
        current = _current_document(index, pointer)
    except (ByaxError, OSError, KeyError) as exc:
        item.update(unavailable, message='The cited library cannot be read: ' + str(exc))
        item['record'] = _user_record(item, 'source_unavailable', pointer=pointer)
        return item
    if current is None:
        item.update(unavailable, message='The cited library document is no longer indexed.')
        item['record'] = _user_record(item, 'source_unavailable', pointer=pointer)
        return item
    document_id, chunks, page = current
    anchor = mark.get('anchor') if isinstance(mark.get('anchor'), dict) else {}
    cited_hash = anchor.get('content_hash') or pointer.get('content_hash')
    current_pointer = {**pointer, 'document_id': document_id, 'content_hash': page['content_hash']}
    if page.get('stale'):
        item.update(
            status='source_changed',
            message='The source file changed since it was indexed; refresh the library, then audit again.',
        )
        item['record'] = _user_record(item, 'source_changed', pointer=pointer)
        return item
    if page['content_hash'] == cited_hash and anchor.get('state') != 'unanchored':
        if not item['quote']:
            item.update(
                status='not_checkable', message='A paraphrase without a quotation; stage 1 cannot check it.'
            )
            item['record'] = _user_record(item, 'not_checkable', pointer=pointer)
            return item
        passages = _filter_locator(chunks, item['locator'], same_version=True)
        return _quote_outcome(item, passages, current_pointer, chunks)
    # The source changed since the citation (content hash): re-anchor (plan 6.5).
    item['changed'] = {'cited_content_hash': cited_hash, 'current_content_hash': page['content_hash']}
    quote = item['quote']
    if not quote:
        item.update(
            status='source_changed',
            message='The source changed since this paraphrase was cited; review that it still holds.',
        )
        item['record'] = _user_record(item, 'source_changed', pointer=current_pointer)
        return item
    selectors = {
        'quote': anchor.get('quote') or quote,
        'prefix': anchor.get('prefix', ''),
        'suffix': anchor.get('suffix', ''),
        **({'position': anchor['position']} if isinstance(anchor.get('position'), dict) else {}),
    }
    found = reanchor_chunks(selectors, chunks, item['locator'])
    reference = {
        'target': item['target'],
        'start': item['start'],
        'end': item['end'],
        'source': item['source_id'],
    }
    if found is None:
        item.update(
            status='unanchored',
            message='The source changed and the quotation could not be found again. The citation is kept, '
            'shown with its quote struck through; correct or re-cite it.',
        )
        item['record'] = _user_record(item, 'unanchored', pointer=current_pointer)
        operations.append(
            {
                'type': 'reanchor_citation',
                **reference,
                'anchor': {
                    **{k: v for k, v in selectors.items()},
                    'content_hash': cited_hash or '',
                    'state': 'unanchored',
                },
            }
        )
        return item
    from .quote_match import selectors as anchor_selectors

    chunk, span = found['chunk'], found['span']
    new_anchor = {
        **anchor_selectors(chunk['text'], span),
        'content_hash': page['content_hash'],
        'state': 'anchored',
    }
    new_locator = {**_locator(chunk['locator']), 'chunk': chunk['chunk_id']}
    item.update(
        status='source_changed',
        reanchor={
            'method': found['method'],
            'structure': found['structure'],
            'locator': new_locator,
            'span': span,
        },
        message='The source changed since citation; the quotation was found again ('
        + found['method']
        + ('' if found['structure'] else ', outside the cited page or block')
        + '). Apply the proposed update to re-anchor it.',
    )
    check = {'stage': 'quote', 'model': None, 'match': 'reanchored', 'score': found['score']}
    record = _user_record(
        item, 'source_changed', passage=chunk, span=span, pointer=current_pointer, check=check
    )
    item['record'] = record
    operations.append(
        {'type': 'reanchor_citation', **reference, 'locator': new_locator, 'anchor': new_anchor}
    )
    moved.setdefault(item['source_id'], {**current_pointer, 'chunk_id': chunk['chunk_id']})
    return item


def audit_citations(doc, *, registry=None, cache_dir=None, stages=DEFAULT_STAGES, document_path=None):
    """Stage 1 of the cascade over every citation mark in ``doc`` (plan 6.4 and 6.5).

    Each citation gets a status: ``quote_found``; ``near_match`` with the differences;
    ``quote_not_found`` (with ``found_elsewhere`` locators when the quotation occurs at another place
    in the source); ``source_changed`` (content hash differs from citation time; re-anchored when the
    quotation is found again); ``unanchored`` (changed and not re-found; never dropped);
    ``source_unavailable``; ``not_checkable`` (paraphrase without a quotation). ``operations`` lists
    proposed ``reanchor_citation`` (and source pointer) updates; nothing is applied. Deterministic
    and local: reads only the document and attached library indexes, no network and no consent.

    ``versions`` is the version-pin check (:func:`byax.version_pins.check_pins`): citations whose
    source has been superseded (a newer library version or a newer revision of a cited ``.byax``
    draft, resolved against ``document_path``) carry ``superseded`` and count as needing review.
    """
    stages = tuple(stages)
    if 'quote' not in stages:
        raise ByaxError('invalid_input', 'The citation audit runs stage 1 (quote)', stages=list(stages))
    items, operations, moved = [], [], {}
    references = doc.get('references', {})
    for block in doc.get('blocks', []):
        marks = sorted(
            (m for m in block.get('marks', []) if isinstance(m, dict) and m.get('type') == 'citation'),
            key=lambda m: (m.get('start', 0), m.get('end', 0), str(m.get('value'))),
        )
        for mark in marks:
            item = _audit_item(block, mark, doc)
            source = references.get(mark.get('value'))
            if not isinstance(source, dict):
                item.update(status='source_unavailable', message='The cited source record does not exist.')
                item['record'] = _user_record(item, 'source_unavailable')
            elif source.get('library'):
                item = _audit_library(item, mark, source, doc, registry, cache_dir, operations, moved)
            elif not item['quote']:
                item.update(
                    status='not_checkable',
                    message='A paraphrase without a quotation; stage 1 cannot check it.',
                )
                item['record'] = _user_record(item, 'not_checkable')
            else:
                text = _reference_text(source)
                if not text:
                    item.update(
                        status='source_unavailable',
                        message='The source has no text in this document or an attached library to compare.',
                    )
                    item['record'] = _user_record(item, 'source_unavailable')
                else:
                    item = _quote_outcome(item, [{'chunk_id': None, 'text': text, 'locator': {}}], {})
            if (
                item.get('anchor_state') == 'unanchored'
                and item['status'] != 'quote_found'
                and not item.get('reanchor')
            ):
                item['status'] = 'unanchored'
                item['record']['state'] = 'unanchored'
            items.append(item)
    for source_id, pointer in moved.items():
        updated = copy.deepcopy(references[source_id])
        updated['library'] = {**updated['library'], **pointer}
        updated['verification'] = 'unconfirmed'
        operations.insert(0, {'type': 'add_reference', 'id': source_id, 'values': updated})
    summary = dict.fromkeys(AUDIT_STATUSES, 0)
    for item in items:
        summary[item['status']] += 1
    from .version_pins import check_pins

    versions = check_pins(doc, document_path=document_path, registry=registry, cache_dir=cache_dir)
    superseded = {row['source_id']: row for row in versions['sources'] if row['state'] == 'superseded'}
    for item in items:
        if item['source_id'] in superseded:
            item['superseded'] = {
                k: superseded[item['source_id']][k]
                for k in ('pinned', 'current', 'message')
                if k in superseded[item['source_id']]
            }
    failed = summary['quote_not_found'] + summary['unanchored']
    attention = failed + summary['near_match'] + summary['source_changed'] + summary['source_unavailable']
    attention += versions['summary']['superseded']
    return {
        'audit': 'citations',
        'stages': [name for name in STAGE_ORDER if name in stages],
        'stages_run': ['quote', 'fuzzy'],
        'deterministic': True,
        'network': False,
        'consent_required': False,
        'citations': items,
        'summary': summary,
        'total': len(items),
        'operations': operations,
        'versions': versions,
        'validation': 'failed' if failed else 'passed',
        'review': 'required' if attention else 'not_required',
        'note': 'Stage 1 (quote match, fuzzy alignment) only. A paraphrase is not checked for meaning; '
        'proposed operations change nothing until applied.',
    }


def describe_audit(report):
    """Plain-language lines for the audit, one per citation (the GUI and the human CLI view)."""
    labels = {
        'quote_found': 'Quote found',
        'near_match': 'Near match',
        'quote_not_found': 'Quote not found',
        'source_changed': 'Source changed since citation',
        'source_unavailable': 'Source unavailable',
        'not_checkable': 'Not checkable (paraphrase without a quote)',
        'unanchored': 'Unanchored',
    }
    lines = [
        f'{report["total"]} citation(s): '
        + ', '.join(
            f'{count} {labels[status].lower()}' for status, count in report['summary'].items() if count
        )
        if report['total']
        else 'This document has no citation marks.'
    ]
    for item in report['citations']:
        detail = f' — {item["message"]}' if item.get('message') else ''
        lines.append(f'{labels[item["status"]]}: “{item["text"]}” ({item["title"]}){detail}')
    for row in (report.get('versions') or {}).get('sources', []):
        if row['state'] == 'superseded':
            lines.append(f'Superseded source: {row["message"]} ({len(row["citations"])} citation(s))')
    return lines
