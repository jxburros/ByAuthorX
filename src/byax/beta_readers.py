"""Beta-reader round trip: send read-and-comment copies, merge the comments readers send back.

``create_beta_copy`` writes a copy of the current revision for readers who are not in Word or ByAuthorX:

* ``html``: a single self-contained page (``review_html``) with offline commenting that exports a
  ``<title>.byaxcomments.json`` file; ``companion=True`` makes it a read-only viewer without any comment
  interface,
* ``pdf``: the controlled PDF export with a review footer that carries the review identifier,
* ``docx``: the Word review copy of ``docx_review`` restricted to comments.

HTML and PDF copies, like Word copies, pin the sent revision as the merge base of the peer ``review:<id>``
and record the package in ``metadata.review_packages`` before the file is written. A companion copy takes no
comments, so it pins and records nothing and leaves the document untouched.

``plan_comment_merge`` reads what comes back: comment files (``merge_comments`` accepts several; each is
merged on its own) and PDFs a reader annotated. Every file is validated strictly and treated as data, never
as instructions: size limits, a closed schema, cleaned text, no markup ever interpreted. Each comment is
re-anchored against the pinned snapshot with its three selectors (position, quote, prefix and suffix), then
mapped onto the current document through the word-level change map the Word merge uses, so a passage the
writer has since edited still gets its comment on the edited text. The result is operations only:

* ``add_comment`` attributed to the reader (never ``ByAuthorX``), stable and deduplicated by reader and
  comment ID, so importing the same file or a later export again adds nothing twice,
* one ``propose`` (kind ``beta_reader``, with ``review_source``) per suggested replacement or deletion that
  still matches the current text; the text changes only when the writer accepts it,
* ``reply_comment`` for replies in an annotated PDF,
* the updated package record.

A comment that cannot be anchored is never dropped: it becomes a comment on the paragraph (or the first
paragraph when that is gone) that quotes the lost passage, with a located loss in the report.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .docx_review import (
    PEER_PREFIX,
    _date,
    _describe,
    _digest,
    _loss,
    _now,
    _Offsets,
    _short,
    _snap,
    _text_ops,
    publish_review_copy,
)
from .errors import ByaxError
from .review_html import COMMENTS_FORMAT, COMMENTS_VERSION

FORMATS = ('html', 'pdf', 'docx')
SUFFIXES = {'html': ('.html', '.htm'), 'pdf': ('.pdf',), 'docx': ('.docx',)}
FILE_LIMIT = 4 * 1024 * 1024
MAX_COMMENTS = 5000
TEXT_LIMIT = 10000
QUOTE_LIMIT = 20000
CONTEXT_LIMIT = 200
READER_LIMIT = 80
ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,64}')
BLOCK_PATTERN = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
REVIEW_PATTERN = re.compile(r'rv_[0-9a-f]{16}')
HASH_PATTERN = re.compile(r'[0-9a-f]{64}')
_TOP = {
    'format',
    'version',
    'review_id',
    'document_id',
    'snapshot_hash',
    'title',
    'reader',
    'exported',
    'comments',
}
_ENTRY = {'id', 'block', 'kind', 'text', 'replacement', 'quote', 'prefix', 'suffix', 'position', 'created'}
_BIDI = re.compile('[\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]')
_SURROGATE = re.compile('[\ud800-\udfff]')
_CONTROL = re.compile('[\x00-\x08\x0b-\x1f\x7f-\x9f]')


# -- The comment file ---------------------------------------------------------------------------


def _reject(message, field):
    raise ByaxError('invalid_input', message, field=field)


def _single(value, field, limit, *, required=True):
    """A one-line label (reader, title): printable, no direction overrides, bounded."""
    if not isinstance(value, str):
        _reject(f'{field} must be text', field)
    text = ' '.join(_CONTROL.sub(' ', _BIDI.sub('', _SURROGATE.sub('\ufffd', value))).split())
    if len(text) > limit:
        _reject(f'{field} is longer than {limit} characters', field)
    if required and not text:
        _reject(f'{field} must not be empty', field)
    return text


def _multi(value, field, limit, *, required=False):
    """Free text (a comment, a suggestion): control characters other than newline and tab are removed."""
    if not isinstance(value, str):
        _reject(f'{field} must be text', field)
    if len(value) > limit:
        _reject(f'{field} is longer than {limit} characters', field)
    text = _CONTROL.sub('', _SURROGATE.sub('\ufffd', value.replace('\r\n', '\n').replace('\r', '\n')))
    if required and not text.strip():
        _reject(f'{field} must not be empty', field)
    return text


def _exact(value, field, limit):
    """Anchor text must match the document character for character, so it is bounded but not cleaned."""
    if not isinstance(value, str):
        _reject(f'{field} must be text', field)
    if len(value) > limit:
        _reject(f'{field} is longer than {limit} characters', field)
    return _SURROGATE.sub('\ufffd', value)


def _loads(raw: bytes):
    def refuse(name):
        raise ValueError(f'{name} is not allowed in JSON')

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate key in the comment file')
            out[key] = value
        return out

    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs, parse_constant=refuse)
    except UnicodeDecodeError as exc:
        raise ByaxError('invalid_input', 'The comment file is not UTF-8 text') from exc
    except (ValueError, RecursionError) as exc:
        raise ByaxError('invalid_input', f'The comment file is not valid JSON: {str(exc)[:120]}') from exc


def validate_comment_file(raw: bytes) -> dict[str, Any]:
    """Parse and strictly validate a ``.byaxcomments.json`` file; returns the cleaned content.

    The file is untrusted: it is bounded in size and count, closed over its keys, typed field by field and
    cleaned. Nothing in it is interpreted. Raises ``invalid_input`` (or ``resource_limit``) naming the field.
    """
    if len(raw) > FILE_LIMIT:
        raise ByaxError(
            'resource_limit', f'The comment file exceeds the {FILE_LIMIT // (1024 * 1024)} MiB limit'
        )
    data = _loads(raw)
    if not isinstance(data, dict):
        _reject('The comment file must be a JSON object', 'file')
    unknown = sorted(set(data) - _TOP)
    if unknown:
        _reject('The comment file has unknown fields: ' + ', '.join(k[:40] for k in unknown[:5]), 'file')
    if data.get('format') != COMMENTS_FORMAT:
        _reject('This is not a ByAuthorX comment file', 'format')
    if type(data.get('version')) is not int or data['version'] != COMMENTS_VERSION:
        _reject(f'Unsupported comment file version (this version reads {COMMENTS_VERSION})', 'version')
    review_id = data.get('review_id')
    if not isinstance(review_id, str) or not REVIEW_PATTERN.fullmatch(review_id):
        _reject('review_id is missing or malformed', 'review_id')
    result: dict[str, Any] = {'review_id': review_id}
    for key in ('document_id', 'snapshot_hash'):
        if key in data:
            value = data[key]
            if not isinstance(value, str) or not (
                HASH_PATTERN.fullmatch(value) or re.fullmatch(r'[\w.:-]{1,80}', value)
            ):
                _reject(f'{key} is malformed', key)
            result[key] = value
    result['title'] = _single(data['title'], 'title', 200, required=False) if 'title' in data else ''
    result['reader'] = _single(data.get('reader', ''), 'reader', READER_LIMIT)
    result['exported'] = _date(data['exported']) if isinstance(data.get('exported'), str) else ''
    comments = data.get('comments')
    if not isinstance(comments, list):
        _reject('comments must be a list', 'comments')
    if len(comments) > MAX_COMMENTS:
        raise ByaxError('resource_limit', f'The comment file has more than {MAX_COMMENTS} comments')
    seen: set[str] = set()
    cleaned = []
    for index, entry in enumerate(comments):
        field = f'comments[{index}]'
        if not isinstance(entry, dict):
            _reject('Each comment must be an object', field)
        extra = sorted(set(entry) - _ENTRY)
        if extra:
            _reject('A comment has unknown fields: ' + ', '.join(k[:40] for k in extra[:5]), field)
        identity, block = entry.get('id'), entry.get('block')
        if not isinstance(identity, str) or not ID_PATTERN.fullmatch(identity):
            _reject('A comment ID is missing or malformed', field + '.id')
        if identity in seen:
            _reject('Two comments share an ID', field + '.id')
        seen.add(identity)
        if not isinstance(block, str) or not BLOCK_PATTERN.fullmatch(block):
            _reject('A comment block is missing or malformed', field + '.block')
        kind = entry.get('kind', 'comment')
        if kind not in ('comment', 'suggestion'):
            _reject('A comment kind must be comment or suggestion', field + '.kind')
        out: dict[str, Any] = {
            'id': identity,
            'block': block,
            'kind': kind,
            'text': _multi(entry.get('text', ''), field + '.text', TEXT_LIMIT),
            'created': _date(entry['created']) if isinstance(entry.get('created'), str) else '',
        }
        if 'quote' in entry:
            quote = _exact(entry['quote'], field + '.quote', QUOTE_LIMIT)
            if not quote.strip():
                _reject('A quoted passage must not be empty', field + '.quote')
            out['quote'] = quote
            out['prefix'] = _exact(entry.get('prefix', ''), field + '.prefix', CONTEXT_LIMIT)
            out['suffix'] = _exact(entry.get('suffix', ''), field + '.suffix', CONTEXT_LIMIT)
            position = entry.get('position')
            if position is None:
                position = {'start': 0, 'end': len(quote)}
            valid = isinstance(position, dict) and set(position) == {'start', 'end'}
            if not valid or any(
                type(position[k]) is not int or not 0 <= position[k] <= 10**7 for k in position
            ):
                _reject('A position must be {start, end} with whole numbers', field + '.position')
            if position['start'] > position['end']:
                _reject('A position must not end before it starts', field + '.position')
            out['position'] = {'start': position['start'], 'end': position['end']}
        elif 'prefix' in entry or 'suffix' in entry or 'position' in entry:
            _reject('prefix, suffix and position need a quote', field)
        if kind == 'suggestion':
            if 'quote' not in out:
                _reject('A suggestion must quote the text it changes', field + '.quote')
            out['replacement'] = _multi(entry.get('replacement', ''), field + '.replacement', TEXT_LIMIT)
        else:
            if 'replacement' in entry:
                _reject('Only a suggestion has replacement text', field + '.replacement')
            if not out['text'].strip():
                _reject('A comment must have text', field + '.text')
        cleaned.append(out)
    result['comments'] = cleaned
    return result


# -- Texts of the sent snapshot -------------------------------------------------------------------


def _texts(blocks) -> dict[str, str]:
    """Text of every block and table cell by identity."""
    out = {}
    for block in blocks:
        if block.get('type') != 'table':
            out[block['id']] = block.get('text', '')
        for row in block.get('rows', []):
            for cell in row.get('cells', []):
                out[cell['id']] = cell.get('text', '')
    return out


def _comment_ids(comments, out=None):
    out = set() if out is None else out
    for comment in comments:
        out.add(comment.get('id'))
        _comment_ids(comment.get('replies', []), out)
    return out


class _Placer:
    """Maps comments of one returned file onto the current document and builds the operations."""

    def __init__(self, doc, record, snap_texts, shown_texts, source, used_ids, proposal_keys):
        self.doc, self.record, self.source = doc, record, source
        self.snap, self.shown = snap_texts, shown_texts
        self.ours = _texts(doc['blocks'])
        self.order = [b['id'] for b in doc['blocks']]
        self.used, self.proposal_keys = used_ids, proposal_keys
        self.offsets: dict[str, _Offsets] = {}
        self.losses: list[dict[str, Any]] = []
        self.reanchored = 0
        self.stats = {
            'added': 0,
            'replies': 0,
            'existing': 0,
            'suggestions': 0,
            'unanchored': 0,
            'remapped': 0,
        }
        self.proposals: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []

    def derived(self, prefix, reader, identity):
        return f'{prefix}_' + _digest(self.record['id'], reader, identity)[:32]

    def lose(self, code, message, target, c, reader):
        entry = _loss(code, message, target, comment=c['id'], reader=reader)
        self.losses.append(entry)
        return entry

    def fallback_target(self, block):
        if block in self.ours:
            return block
        return next(
            (b for b in self.order if self.ours.get(b, '').strip()), self.order[0] if self.order else None
        )

    def unplaced(self, c, reader, code, message):
        self.stats['unanchored'] += 1
        self.lose(code, message, c['block'], c, reader)
        return {'target': self.fallback_target(c['block']), 'unanchored': True}

    def locate_in_snapshot(self, c, snap, shown):
        from .quote_match import reanchor

        quote = c['quote']
        position = c.get('position') or {'start': 0, 'end': len(quote)}
        if shown[position['start'] : position['end']] == quote and position['end'] > position['start']:
            return (position['start'], position['end']), 'position'
        found = reanchor(
            {'quote': quote, 'prefix': c['prefix'], 'suffix': c['suffix'], 'position': position}, shown
        )
        if found is None or found.end > len(snap):
            return None, ''
        return found.span, found.method

    def elsewhere(self, quote):
        hits = [(b, t.find(quote)) for b, t in self.ours.items() if quote and quote in t]
        return hits[0] if len(hits) == 1 and self.ours[hits[0][0]].count(quote) == 1 else None

    def offsets_for(self, block):
        if block not in self.offsets:
            self.offsets[block] = _Offsets(self.snap[block], self.ours[block])
        return self.offsets[block]

    def place(self, c, reader):
        block, quote = c['block'], c.get('quote')
        if block not in self.snap:
            return self.unplaced(
                c,
                reader,
                'beta_comment_block_unknown',
                'The comment is on a paragraph that was not in the copy that was sent.',
            )
        if not quote:
            if block not in self.ours:
                return self.unplaced(
                    c,
                    reader,
                    'beta_comment_block_removed',
                    'The paragraph this comment is on has since been removed.',
                )
            return {'target': block}
        snap, shown = self.snap[block], self.shown[block]
        span, method = self.locate_in_snapshot(c, snap, shown)
        if span is None:
            return self.unplaced(
                c,
                reader,
                'beta_comment_quote_lost',
                'The quoted passage was not found in the copy that was sent.',
            )
        if method != 'position':
            self.reanchored += 1
        if block not in self.ours:
            found = self.elsewhere(quote)
            if found is None:
                return self.unplaced(
                    c,
                    reader,
                    'beta_comment_block_removed',
                    'The paragraph this comment is on has since been removed.',
                )
            self.lose(
                'beta_comment_moved',
                'The paragraph was removed; the comment follows the quoted text.',
                found[0],
                c,
                reader,
            )
            return {'target': found[0], 'start': found[1], 'end': found[1] + len(quote), 'clean': True}
        offsets, text = self.offsets_for(block), self.ours[block]
        start, end = span
        if offsets.clean(start, end):
            low = offsets.start(start)
            high = max(low, offsets.end(end))
            clean = True
        else:
            _, _, low, high = offsets.region(start, end)
            clean = False
            self.stats['remapped'] += 1
            self.lose(
                'beta_comment_remapped',
                'The commented text changed since the copy was sent; check the comment range.',
                block,
                c,
                reader,
            )
        low, high = _snap(text, min(low, len(text)), min(high, len(text)))
        return {'target': block, 'start': low, 'end': high, 'clean': clean}

    def add(self, c, reader, *, prefix='comment', source='beta_reader'):
        """Plan one comment (and its proposal); returns the derived comment ID or None when already merged."""
        comment_id = self.derived('comment', reader, c['id'])
        key = (self.record['id'], reader, c['id'])
        if comment_id in self.used or key in self.proposal_keys:
            self.stats['existing'] += 1
            return comment_id
        placed = self.place(c, reader)
        if placed['target'] is None:
            self.lose(
                'beta_comment_dropped', 'The document has no paragraph to hold this comment.', None, c, reader
            )
            return None
        text = c.get('text', '')
        quote = c.get('quote', '')
        suggestion = c['kind'] == 'suggestion'
        if (
            suggestion
            and 'start' in placed
            and self.ours[placed['target']][placed['start'] : placed['end']] == quote
        ):
            replacement = c.get('replacement', '')
            operations = _text_ops(
                placed['target'], self.ours[placed['target']], placed['start'], placed['end'], replacement
            )
            self.proposal_keys.add(key)
            self.stats['suggestions'] += 1
            explanation = f'{_describe(quote, replacement)} — {reader}' + (
                f': {_short(text, 80)}' if text else ''
            )
            self.proposals.append(
                {
                    'type': 'propose',
                    'values': {
                        'kind': 'beta_reader',
                        'operations': operations,
                        'explanation': explanation,
                        'author': reader,
                        'review_source': {
                            'review_id': self.record['id'],
                            'format': self.source['format'],
                            'file': self.source['file'],
                            'sha256': self.source['sha256'],
                            'reader': reader,
                            'comment': c['id'],
                            'quote': quote,
                            'replacement': replacement,
                        },
                    },
                }
            )
            if not text.strip():
                return comment_id
        elif suggestion:
            self.lose(
                'beta_suggestion_not_placed',
                'The suggested text could not be proposed because the passage has changed or was not found; '
                'it is kept as a comment.',
                placed['target'],
                c,
                reader,
            )
            what = f'replacing “{_short(quote, 80)}” with “{_short(c.get("replacement", ""), 80)}”'
            if not c.get('replacement'):
                what = f'deleting “{_short(quote, 80)}”'
            text = f'Suggested {what}.' + (f' {text}' if text.strip() else '')
        if placed.get('unanchored'):
            text = f'[Passage not found: “{_short(quote or "the paragraph", 120)}”] {text}'.strip()
        operation: dict[str, Any] = {
            'type': 'add_comment',
            'id': comment_id,
            'target': placed['target'],
            'text': text,
            'author': reader,
            'source': source,
        }
        if 'start' in placed:
            operation.update(start=placed['start'], end=placed['end'])
        date = _date(c.get('created', ''))
        if date:
            operation['date'] = date
        self.comments.append(operation)
        self.used.add(comment_id)
        self.stats['added'] += 1
        return comment_id

    def reply(self, c, reader, parent_id):
        identity = self.derived('comment', reader, c['id'])
        if identity in self.used:
            self.stats['existing'] += 1
            return
        operation = {
            'type': 'reply_comment',
            'id': identity,
            'target': parent_id,
            'text': c['text'] or '(no text)',
            'author': reader,
        }
        date = _date(c.get('created', ''))
        if date:
            operation['date'] = date
        self.comments.append(operation)
        self.used.add(identity)
        self.stats['replies'] += 1


# -- Merging ----------------------------------------------------------------------------------


def _read_file(path, limit):
    from .exchange import MAX_INPUT

    source = Path(path)
    if not source.is_file():
        raise ByaxError('not_found', 'The returned file does not exist', path=str(source))
    if source.stat().st_size > min(limit, MAX_INPUT):
        raise ByaxError('resource_limit', 'The returned file is too large', path=str(source))
    return source.read_bytes()


def _record_for(doc, review_id, wanted, source):
    packages = doc.get('metadata', {}).get('review_packages', {})
    if wanted and review_id and wanted != review_id:
        raise ByaxError(
            'conflict',
            'The returned file belongs to a different review package.',
            target=review_id,
            expected=wanted,
        )
    chosen = wanted or review_id
    if not chosen:
        raise ByaxError(
            'missing_target',
            'The returned file does not name its review copy; pass the review package ID explicitly.',
            candidates=sorted(packages),
        )
    record = packages.get(chosen)
    if record is None:
        raise ByaxError(
            'missing_target',
            'This document has no record of that review package.',
            target=chosen,
            candidates=sorted(packages),
        )
    if record.get('format') not in ('html', 'pdf'):
        raise ByaxError(
            'invalid_input',
            'That review package is a Word copy; merge the returned .docx with merge-review.',
            target=chosen,
        )
    if source != record['format']:
        raise ByaxError(
            'invalid_input',
            f'That review package is a {record["format"].upper()} copy; this file is a {source.upper()}.',
            target=chosen,
        )
    if doc['history'].get('peer_bases', {}).get(record['peer']) != record['revision']:
        raise ByaxError(
            'missing_target',
            'The review snapshot is no longer pinned in this document, so a safe three-way merge is not possible.',
            target=chosen,
        )
    return record


def _sent(doc, record):
    """``(snapshot, snap_texts, shown_texts)`` of the pinned snapshot, checked against the recorded hash."""
    from . import engine
    from .docx_review import snapshot_hash

    snapshot = engine.revision_document(doc, record['revision'])
    if record['format'] == 'html':
        from .review_html import html_projection

        candidate, _, units = html_projection(snapshot)
    else:
        from .review_pdf import pdf_projection

        candidate, _, units = pdf_projection(snapshot, record['id'])
    if snapshot_hash(units) != record['snapshot_hash']:
        raise ByaxError(
            'conflict',
            'The pinned review snapshot does not reproduce the copy that was sent.',
            target=record['id'],
        )
    raw = _texts(snapshot['blocks'])
    shown = _texts(candidate['blocks'])
    from .docx_review import _clean

    return snapshot, {k: v for k, v in raw.items() if k in shown}, {k: _clean(v) for k, v in shown.items()}


def _merge_one(doc, path, wanted, shared, packages):
    """Plan one returned file: ``(proposals, comments, entry)``; raises for a file that cannot be merged."""
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix == '.docx':
        raise ByaxError(
            'invalid_input',
            'A Word file is merged with merge-review, not merge-comments.',
            path=str(source_path),
        )
    pdf = suffix == '.pdf'
    raw = _read_file(path, 64 * 1024 * 1024 if pdf else FILE_LIMIT)
    sha = hashlib.sha256(raw).hexdigest()
    source = {'file': source_path.name, 'sha256': sha, 'format': 'pdf' if pdf else 'html'}
    losses: list[dict[str, Any]] = []
    if pdf:
        from .review_pdf import read_pdf_annotations

        parsed = read_pdf_annotations(raw)
        found, readers = parsed['review_id'], None
        losses.extend(parsed['losses'])
    else:
        parsed = validate_comment_file(raw)
        found, readers = parsed['review_id'], parsed['reader']
    record = _record_for(doc, found, wanted, source['format'])
    chosen = record['id']
    record = packages[chosen]
    if not pdf:
        if parsed.get('document_id') not in (None, doc['id']):
            raise ByaxError('conflict', 'The comment file is for a different document.', target=chosen)
        if parsed.get('snapshot_hash') not in (None, record['snapshot_hash']):
            raise ByaxError('conflict', 'The comment file was made from a different snapshot.', target=chosen)
    entry = {
        'file': source['file'],
        'sha256': sha,
        'review_id': chosen,
        'reader': readers,
        'status': 'merged',
    }
    if any(item.get('sha256') == sha for item in record.get('returns', [])):
        entry['status'] = 'already_merged'
        return [], [], entry, None
    if chosen not in shared['sent']:
        shared['sent'][chosen] = _sent(doc, record)
    snapshot, snap_texts, shown_texts = shared['sent'][chosen]
    placer = _Placer(doc, record, snap_texts, shown_texts, source, shared['ids'], shared['proposal_keys'])
    placer.losses.extend(losses)
    if pdf:
        from .review_pdf import locate_annotations

        notes = locate_annotations(snapshot, chosen, parsed['annotations'], placer.losses)
        ids = {n['id']: n for n in notes}
        roots = [n for n in notes if not n['parent'] or n['parent'] not in ids]
        planned: dict[str, str | None] = {}
        for note in roots:
            note_reader = note['author'] or 'PDF reader'
            note.setdefault('quote', '')
            if not note.get('quote'):
                note.pop('quote', None)
            planned[note['id']] = placer.add(
                {
                    **note,
                    'kind': 'comment',
                    'prefix': note.get('prefix', ''),
                    'suffix': note.get('suffix', ''),
                },
                note_reader,
                source='pdf_review',
            )
        for note in notes:
            if note in roots:
                continue
            parent = planned.get(note['parent'])
            if parent:
                placer.reply(note, note['author'] or 'PDF reader', parent)
            else:
                placer.add({**note, 'kind': 'comment'}, note['author'] or 'PDF reader', source='pdf_review')
        entry['reader'] = ', '.join(sorted({n['author'] or 'PDF reader' for n in notes}))
    else:
        for comment in parsed['comments']:
            placer.add(comment, readers)
    entry.update(
        comments=placer.stats['added'],
        replies=placer.stats['replies'],
        proposals=len(placer.proposals),
        existing=placer.stats['existing'],
        reanchored=placer.reanchored,
        unanchored=placer.stats['unanchored'],
        losses=len(placer.losses),
    )
    return placer.proposals, placer.comments, entry, placer


def plan_comment_merge(doc, paths, *, review_id=None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Canonical operations that merge returned comment files (or annotated PDFs) into ``doc``, plus a report.

    Files are merged independently: one that is invalid, belongs to another document or was already merged
    is reported and skipped, the others are still planned. With a single file the error is raised instead.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = list(paths)
    if not paths:
        raise ByaxError(
            'invalid_input', 'merge-comments needs at least one comment file', field='comments_path'
        )
    packages = copy.deepcopy(doc.get('metadata', {}).get('review_packages', {}))
    shared: dict[str, Any] = {
        'sent': {},
        'ids': _comment_ids(doc['review']['comments']),
        'proposal_keys': {
            (
                p['review_source'].get('review_id'),
                p['review_source'].get('reader'),
                p['review_source'].get('comment'),
            )
            for p in doc['review']['proposals']
            if p.get('kind') == 'beta_reader' and isinstance(p.get('review_source'), dict)
        },
    }
    proposals: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    losses: list[dict[str, Any]] = []
    totals = {'added': 0, 'replies': 0, 'existing': 0, 'suggestions': 0, 'unanchored': 0, 'reanchored': 0}
    first_error: ByaxError | None = None
    for path in paths:
        try:
            made, added, entry, placer = _merge_one(doc, path, review_id, shared, packages)
        except ByaxError as exc:
            first_error = first_error or exc
            rejected.append({'file': Path(path).name, 'code': exc.code, 'message': exc.message})
            continue
        files.append(entry)
        if placer is None:
            continue
        proposals += made
        comments += added
        losses += placer.losses
        for key in ('added', 'replies', 'existing', 'suggestions', 'unanchored'):
            totals[key] += placer.stats[key]
        totals['reanchored'] += placer.reanchored
        record = packages[entry['review_id']]
        record['status'] = 'merged'
        record['returns'] = [
            *record.get('returns', []),
            {
                'file': entry['file'],
                'sha256': entry['sha256'],
                'reader': entry['reader'],
                'merged_at': _now(),
                'base_revision': doc['revision'],
                'proposals': entry['proposals'],
                'comments': entry['comments'] + entry['replies'],
            },
        ]
    if not files and first_error is not None:
        raise first_error
    merged = [f for f in files if f['status'] == 'merged']
    operations = proposals + comments
    if merged:
        operations.append({'type': 'set_metadata', 'values': {'review_packages': packages}})
    summary = [
        (
            f'{totals["added"]} new comment(s), {totals["replies"]} repl(ies), {len(proposals)} suggestion(s) '
            f'from {len(merged)} file(s)'
        )
    ]
    if totals['existing']:
        summary.append(f'{totals["existing"]} comment(s) were already in the document')
    if totals['reanchored']:
        summary.append(f'{totals["reanchored"]} comment(s) were re-anchored because the sent text differed')
    if totals['unanchored']:
        summary.append(
            f'{totals["unanchored"]} comment(s) could not be placed and were kept as paragraph comments'
        )
    if any(f['status'] == 'already_merged' for f in files):
        summary.append('Some files were already merged.')
    if rejected:
        summary.append(f'{len(rejected)} file(s) were not merged')
    report = {
        'comment_merge': {'files': files},
        'proposals': len(proposals),
        'proposal_summaries': [op['values']['explanation'] for op in proposals],
        'comments': totals,
        'losses': losses,
        'rejected': rejected,
        'summary': summary,
    }
    return operations, report


def merge_comments(doc, paths, *, review_id=None, dry_run=False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plan and apply the merge; returns ``(document, result)``. Suggestions stay pending proposals."""
    from . import engine

    operations, report = plan_comment_merge(doc, paths, review_id=review_id)
    if not operations:
        return doc, {**report, 'changed_ids': []}
    updated, result = engine.apply(
        doc, {'expected_revision': doc['revision'], 'operations': operations}, dry_run=dry_run
    )
    shas = {f['sha256'] for f in report['comment_merge']['files']}
    report['proposal_ids'] = [
        p['id']
        for p in updated['review']['proposals']
        if p.get('kind') == 'beta_reader' and p.get('review_source', {}).get('sha256') in shas
    ]
    return (doc if dry_run else updated), {**result, **report}


# -- Sending ------------------------------------------------------------------------------------


def beta_identifier(doc, fmt, reviewer='', *, companion=False) -> str:
    """Identifier of the beta copy of ``doc``'s current revision in ``fmt``."""
    from .docx_review import review_identifier
    from .review_html import review_html_id

    if fmt == 'docx':
        return review_identifier(doc, reviewer)
    if fmt == 'html':
        return review_html_id(doc, reviewer, companion=companion)
    return 'rv_' + _digest('review', 'pdf', doc['id'], doc['revision'], reviewer or '')[:16]


def prepare_beta_copy(
    doc, fmt, *, reviewer='', note='', companion=False, file_name='', created=None
) -> tuple[bytes, list[dict[str, Any]], dict[str, Any]]:
    """Build a beta-reader copy of ``doc``'s current revision without writing anything.

    Returns ``(bytes, operations, info)``. Applying ``operations`` through ``engine.apply`` pins the
    revision and records the package; a companion copy has none (nothing comes back from it).
    """
    from .docx_review import prepare_review_package
    from .model import snapshot

    if fmt not in FORMATS:
        raise ByaxError('invalid_input', 'format must be html, pdf or docx', field='format')
    if not isinstance(reviewer, str) or not isinstance(note, str):
        raise ByaxError('invalid_input', 'reviewer and note must be text')
    if companion and fmt != 'html':
        raise ByaxError('invalid_input', 'A read-only companion copy is HTML only', field='companion')
    if fmt == 'docx':
        data, operations, info = prepare_review_package(
            doc, restrict='comments', reviewer=reviewer, file_name=file_name, created=created
        )
        return data, operations, {**info, 'format': 'docx', 'companion': False}
    review_id = beta_identifier(doc, fmt, reviewer, companion=companion)
    created = created or _now()
    if fmt == 'html':
        from .review_html import build_html_copy

        data, info = build_html_copy(
            snapshot(doc), review_id=review_id, reviewer=reviewer, note=note, companion=companion
        )
    else:
        from .review_pdf import build_pdf_copy

        data, info = build_pdf_copy(snapshot(doc), review_id=review_id)
    if companion:
        record = {
            'id': review_id,
            'format': 'html',
            'companion': True,
            'revision': doc['revision'],
            'snapshot_hash': info['snapshot_hash'],
            'file': file_name,
            'sha256': info['sha256'],
            'created': created,
        }
        return (
            data,
            [],
            {
                'review_id': review_id,
                'record': record,
                'losses': info['losses'],
                'units': len(info['units']),
                'format': 'html',
                'companion': True,
            },
        )
    previous = doc.get('metadata', {}).get('review_packages', {}).get(review_id, {})
    record = {
        'id': review_id,
        'peer': PEER_PREFIX + review_id,
        'format': fmt,
        'revision': doc['revision'],
        'snapshot_hash': info['snapshot_hash'],
        'restrict': 'comments',
        'reviewer': reviewer,
        'note': note,
        'file': file_name,
        'sha256': info['sha256'],
        'created': created,
        'status': previous.get('status', 'sent'),
        'comments_sent': 0,
        'returns': copy.deepcopy(previous.get('returns', [])),
    }
    packages = copy.deepcopy(doc.get('metadata', {}).get('review_packages', {}))
    packages[review_id] = record
    operations = [
        {'type': 'pin_merge_base', 'peer': record['peer'], 'revision': doc['revision']},
        {'type': 'set_metadata', 'values': {'review_packages': packages}},
    ]
    return (
        data,
        operations,
        {
            'review_id': review_id,
            'record': record,
            'losses': info['losses'],
            'units': len(info['units']),
            'format': fmt,
            'companion': False,
        },
    )


def create_beta_copy(
    doc, out, *, format='html', reviewer='', note='', companion=False, overwrite=False, dry_run=False
) -> tuple[dict[str, Any], dict[str, Any], bytes | None]:
    """Plan the beta copy for ``out``: returns ``(updated_doc, result, bytes)``; nothing is written here.

    The caller saves ``updated_doc`` (which holds the pin and the record) and then publishes the bytes, so
    a copy never exists without its recorded snapshot. On a dry run the original document and ``None`` come
    back with the planned losses.
    """
    from . import engine
    from .exchange import _report

    if format not in FORMATS:
        raise ByaxError('invalid_input', 'format must be html, pdf or docx', field='format')
    destination = Path(out)
    if destination.suffix.lower() not in SUFFIXES.get(format, ()):
        raise ByaxError(
            'invalid_input',
            f'A {format} beta copy must be a {" or ".join(SUFFIXES.get(format, ("?",)))} file',
            field='out',
        )
    if not overwrite and destination.exists() and not dry_run:
        raise ByaxError(
            'conflict', 'Output already exists; explicitly select overwrite', target=str(destination)
        )
    data, operations, info = prepare_beta_copy(
        doc, format, reviewer=reviewer, note=note, companion=companion, file_name=destination.name
    )
    report = _report(format, info['losses'], [] if dry_run else [str(destination.resolve())])
    report['review'] = 'required' if info['losses'] else 'not_required'
    result = {
        'beta_copy': {
            'format': format,
            'companion': info['companion'],
            'review_id': info['review_id'],
            'recorded': bool(operations),
            **{k: info['record'][k] for k in ('revision', 'snapshot_hash', 'sha256')},
        },
        'review_package': {**info['record'], 'units': info['units']},
        'export_report': report,
    }
    if dry_run:
        return doc, {**result, 'dry_run': True}, None
    if not operations:
        return doc, {**result, 'outputs': [str(destination.resolve())]}, data
    updated, applied = engine.apply(doc, {'expected_revision': doc['revision'], 'operations': operations})
    return updated, {**applied, **result, 'outputs': [str(destination.resolve())]}, data


# -- Commands (CLI, MCP and REST through cli.execute) ---------------------------------------------


def beta_copy_command(a: dict[str, Any], owner: str | None) -> dict[str, Any]:
    """``beta-copy``: write a read-and-comment copy for beta readers (html, pdf or docx)."""
    from .cli import envelope
    from .docx_review import _check_revision
    from .storage import DocumentLock, load_document, save_document

    if not a.get('out'):
        raise ByaxError('invalid_input', 'beta-copy requires --out', field='out')
    lock = DocumentLock(a['path'])
    with lock:
        doc = load_document(a['path'])
        _check_revision(doc, a.get('expect_revision'))
        overwrite = a.get('overwrite', False)
        updated, result, data = create_beta_copy(
            doc,
            a['out'],
            format=a.get('format', 'html'),
            reviewer=a.get('reviewer', ''),
            note=a.get('note', ''),
            companion=a.get('companion', False),
            overwrite=overwrite,
            dry_run=a.get('dry_run', False),
        )
        if data is None:
            return envelope(result, doc)
        # The snapshot is recorded before the copy exists, so a published copy can always be merged.
        if updated is not doc:
            save_document(updated, a['path'], owner=lock.owner)
        publish_review_copy(data, a['out'], owner=owner, overwrite=overwrite)
        return envelope(result, updated)


def merge_comments_command(a: dict[str, Any], owner: str | None) -> dict[str, Any]:
    """``merge-comments``: merge reader comment files (and annotated PDFs) as comments and proposals."""
    from .cli import _mutate
    from .docx_review import _check_revision

    paths = a.get('comments_path')
    if isinstance(paths, str):
        paths = [paths]
    if not paths or not all(isinstance(p, str) and p for p in paths):
        raise ByaxError('invalid_input', 'merge-comments requires --comments', field='comments_path')

    def merge(doc):
        _check_revision(doc, a.get('expect_revision'))
        updated, result = merge_comments(
            doc, paths, review_id=a.get('review_id'), dry_run=a.get('dry_run', False)
        )
        return updated, result, updated is not doc

    return _mutate(a['path'], merge)
