"""Bounded, offline import and staged export adapters.

Markdown dialect: CommonMark plus markdown-it's table extension. Raw HTML is
retained in provenance but is never executed. Original imports are embedded as
opaque assets, and conversion cannot overwrite the imported source path.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import html
import io
import itertools
import json
import os
import re
import subprocess
import tempfile
import uuid
import zipfile
import zlib
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from .errors import ByaxError
from .model import link_external_image, new_document

MAX_INPUT = 32 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_REPEATED_SPACES = 1_000_000
MAX_TABLE_CELLS = 100_000
MAX_DEPTH = 256
DOC_TIMEOUT = 90
MARKDOWN_DIALECT = {
    'name': 'commonmark+tables',
    'parser': 'markdown-it-py CommonMark preset with the GFM pipe-table extension',
    'constructs': {
        'headings': 'ATX (# to ######) and setext (=== / ---); setext syntax is kept on localized edits',
        'emphasis': '*italic* / _italic_, **bold** / __bold__ and `code` spans',
        'lists': 'bullet (-, *, +) and ordered (1. or 1)) lists, nested by indentation',
        'links': 'inline [text](url); only http(s), mailto, fragment and relative targets become links',
        'images': '![alt](src) kept as an explicit external dependency; never fetched',
        'quotations': '> block quotes, nested',
        'code': 'fenced (``` or ~~~, info string retained) and indented code blocks',
        'thematic_breaks': '---, *** and ___',
        'tables': 'pipe tables with one header row and delimiter row',
    },
    'raw_html': 'kept as literal text; never parsed as active content',
    'not_represented': [
        'underline',
        'strikethrough',
        'superscript',
        'subscript',
        'color',
        'highlight',
        'font family/size',
        'paragraph alignment and spacing',
        'named styles',
        'page layout',
        'comments and tracked changes',
        'footnotes',
        'embedded image bytes',
        'table spans and widths',
    ],
    'line_endings': 'LF or CRLF detected on import and preserved by localized edits',
    'conversion': 'Formatting outside this dialect requires explicit conversion to .byax (or allow_markdown_losses) '
    'when saving a Markdown source back to .md; layout syntax is never inserted into ordinary Markdown.',
}


def _id(prefix='block'):
    return f'{prefix}_{uuid.uuid4().hex}'


def _block(text='', kind='paragraph', **attrs):
    return {'id': _id(), 'type': kind, 'text': text, 'style': 'body', 'marks': [], 'attrs': attrs}


def _loss(losses, code, message, target=None):
    losses.append({'code': code, 'severity': 'warning', 'message': message, 'target': target})


def _asset(doc, data, mime, name):
    key = hashlib.sha256(data).hexdigest()
    doc.setdefault('assets', {})[key] = {
        'data': base64.b64encode(data).decode('ascii'),
        'mime': mime,
        'name': name,
    }
    return key


def _fingerprint(block):
    return hashlib.sha256(json.dumps(block, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _list_kind(attrs):
    value = attrs.get('list')
    return value.get('kind', 'bullet') if isinstance(value, dict) else value


def _list_level(attrs):
    value = attrs.get('list')
    return int(value.get('level', 1)) if isinstance(value, dict) else int(attrs.get('level', 1))


def _safe_url(value):
    return value.startswith(('http://', 'https://', 'mailto:', '#')) or ':' not in value


def _decode(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16'), 'utf-16'
    try:
        return raw.decode('utf-8-sig'), 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace'), 'cp1252'


def _raised_by_package_reader(exc):
    """True when the innermost frame is python-docx or zipfile rather than ByAuthorX code."""
    import traceback

    import docx

    frames = traceback.extract_tb(exc.__traceback__)
    origin = os.path.normcase(frames[-1].filename) if frames else ''
    zip_source = os.path.normcase(zipfile.__file__)
    if zip_source.endswith('__init__.py'):
        zip_source = os.path.dirname(zip_source) + os.sep
    return origin.startswith((os.path.normcase(os.path.dirname(docx.__file__)) + os.sep, zip_source))


def _parse_xml(raw, member):
    """Distinguish hostile XML declarations from ordinary corruption."""
    from xml.etree.ElementTree import ParseError

    from defusedxml import DefusedXmlException
    from defusedxml.ElementTree import fromstring

    try:
        return fromstring(raw)
    except DefusedXmlException as exc:
        raise ByaxError(
            'unsafe_xml', 'Prohibited XML entity, DTD or external reference declaration', member=member
        ) from exc
    except (ParseError, ValueError, UnicodeError) as exc:
        raise ByaxError('invalid_input', 'Malformed XML in document member', member=member) from exc


def _safe_zip(raw):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
        if len(entries) > 10000 or sum(item.file_size for item in entries) > MAX_EXPANDED:
            raise ByaxError('resource_limit', 'Archive expanded size or member count exceeds import limit')
        names = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or item.filename in names:
                raise ByaxError(
                    'unsafe_archive', 'Archive contains unsafe or duplicate member', member=item.filename
                )
            names.add(item.filename)
            if item.flag_bits & 1:
                raise ByaxError('encrypted_input', 'Encrypted archive is not supported')
            if item.file_size > MAX_INPUT or (
                item.compress_size and item.file_size / item.compress_size > 500
            ):
                raise ByaxError(
                    'resource_limit', 'Archive member exceeds size or expansion limit', member=item.filename
                )
            if item.filename.endswith(('.xml', '.rels')):
                _parse_xml(archive.read(item), item.filename)
        corrupt = archive.testzip()
        if corrupt:
            raise ByaxError('invalid_input', 'Corrupt ZIP member', member=corrupt)
        return archive
    except (zipfile.BadZipFile, NotImplementedError, EOFError, zlib.error, ValueError) as exc:
        raise ByaxError('invalid_input', 'Invalid ZIP-based document') from exc


def _report(fmt, losses, outputs=None, direction='export'):
    return {
        'schema_version': 1,
        'execution': 'completed',
        'validation': 'not_run',
        'format': fmt,
        'direction': direction,
        'outputs': outputs or [],
        'losses': losses,
        'findings': losses,
        'coverage': {
            'content': 'supported text retained; see located losses',
            'structure': 'editable adapter subset',
            'visual': 'not verified against source application',
            'review': 'native only; exchange losses reported',
        },
        'fidelity': 'partial' if losses else 'supported_subset',
    }


def import_document(path):
    path = Path(path)
    fmt = path.suffix.lower().lstrip('.')
    if fmt == 'byax':
        from .storage import load_document

        return load_document(path), _report(fmt, [], direction='import')
    if not path.is_file():
        raise ByaxError('not_found', 'Import file does not exist', path=str(path))
    if path.stat().st_size > MAX_INPUT:
        raise ByaxError('resource_limit', 'Import file exceeds 32 MiB limit', path=str(path))
    raw, losses = path.read_bytes(), []
    adapter = None
    doc = new_document(path.stem)
    doc['blocks'] = []
    source = None
    if fmt in ('txt', 'md', 'markdown', 'html', 'htm', 'rtf'):
        source, encoding = _decode(raw)
        if fmt == 'txt':
            doc['blocks'] = [_block(line) for line in source.splitlines()]
        elif fmt in ('md', 'markdown'):
            _import_markdown(doc, source, losses)
        elif fmt in ('html', 'htm'):
            parser = _HTMLImport(doc, losses)
            parser.feed(source)
            parser.close()
            parser.flush()
        else:
            _import_rtf(doc, source, losses)

    elif fmt == 'docx':
        _safe_zip(raw).close()
        try:
            _import_docx(doc, raw, losses)
        except (KeyError, AttributeError, ValueError) as exc:
            # python-docx reports missing or mistyped package parts this way; keep our own bugs visible.
            if not _raised_by_package_reader(exc):
                raise
            raise ByaxError(
                'invalid_input', f'DOCX package is missing or has an invalid part: {exc}'
            ) from exc
    elif fmt == 'odt':
        _import_odt(doc, raw, losses)
    elif fmt == 'doc':
        doc, adapter_report = _import_doc(path)
        losses.extend(adapter_report['losses'])
        adapter = adapter_report.get('adapter')
    else:
        raise ByaxError('unsupported_format', f'Unsupported import format: {fmt or "(none)"}')
    for block in doc['blocks']:
        if block.get('type') == 'table':
            rows = block.get('rows', [])
            width = max((len(row.get('cells', [])) for row in rows), default=0)
            if not rows or not width:
                block['type'], block['text'] = 'paragraph', ''
                block.pop('rows', None)
                _loss(losses, 'empty_table', 'Empty table normalized to an empty paragraph.', block['id'])
                continue
            for row in rows:
                if len(row['cells']) < width:
                    row['cells'].extend(
                        {'id': _id('cell'), 'text': '', 'attrs': {}} for _ in range(width - len(row['cells']))
                    )
                    _loss(
                        losses,
                        'ragged_table',
                        'Short table row padded with empty cells to preserve an editable rectangular grid.',
                        row['id'],
                    )
    if not doc['blocks']:
        doc['blocks'] = [_block()]
    original_key = _asset(doc, raw, 'application/octet-stream', path.name)
    provenance = {
        'filename': path.name,
        'original_path': str(path.resolve()),
        'format': fmt,
        'original_asset': original_key,
        'sha256': hashlib.sha256(raw).hexdigest(),
        'losses': losses,
    }
    if source is not None:
        provenance.update(
            {
                'source': source,
                'encoding': encoding,
                'line_ending': '\r\n' if '\r\n' in source else '\n',
                'fingerprints': {b['id']: _fingerprint(b) for b in doc['blocks']},
                'block_order': [b['id'] for b in doc['blocks']],
            }
        )
        if fmt in ('md', 'markdown'):
            provenance.update(
                {
                    'dialect': MARKDOWN_DIALECT['name'],
                    'spans': {b['id']: b['attrs'].get('source_lines') for b in doc['blocks']},
                    'structure': {b['id']: _md_structure(b) for b in doc['blocks']},
                }
            )
    doc['metadata']['import'] = provenance
    _snap_marks(doc)
    from .model import validate_document

    validate_document(doc)
    audit = _content_audit(doc, fmt, raw, source, losses)
    report = _report(fmt, losses, direction='import')
    report['content_audit'] = audit
    if adapter:
        report['adapter'] = adapter
    if fmt in ('md', 'markdown'):
        report['dialect'] = MARKDOWN_DIALECT
    return doc, report


def _squash(value):
    return re.sub(r'\s+', '', value or '')


def _corpus(doc):
    parts = []
    for block in doc['blocks']:
        parts.append(block.get('text', ''))
        parts.extend(cell.get('text', '') for row in block.get('rows', []) for cell in row.get('cells', []))
        parts.append(block.get('attrs', {}).get('alt', '') or '')
    review = doc.get('review', {})
    parts.extend(item.get('text', '') for item in review.get('comments', []))
    parts.extend(
        item.get('before', '')
        for item in review.get('tracked_changes', [])
        if isinstance(item.get('before'), str)
    )
    for region in ('header', 'footer'):
        value = doc.get('layout', {}).get(region, '')
        parts.append(value if isinstance(value, str) else value.get('text', ''))
    return _squash('\n'.join(parts))


def _content_audit(doc, fmt, raw, source, losses):
    """Compare source text segments with imported content so omissions are always located."""
    segments = []
    if fmt == 'docx':
        w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        mc = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for member in (
                'word/document.xml',
                'word/footnotes.xml',
                'word/endnotes.xml',
                'word/comments.xml',
                *[name for name in archive.namelist() if re.fullmatch(r'word/(header|footer)\d*\.xml', name)],
            ):
                if member not in archive.namelist():
                    continue
                root = _parse_xml(archive.read(member), member)
                fallback = {id(node) for parent in root.iter(mc + 'Fallback') for node in parent.iter()}

                def own(node):
                    text = node.text or '' if node.tag == w + 't' else ''
                    for child in node:
                        if child.tag not in (w + 'p', mc + 'Fallback'):
                            text += own(child)
                    return text

                separators = {
                    id(node)
                    for kind in ('footnote', 'endnote')
                    for note in root.iter(w + kind)
                    if note.get(w + 'type') not in (None, 'normal')
                    for node in note.iter()
                }
                for index, paragraph in enumerate(root.iter(w + 'p')):
                    if id(paragraph) not in fallback and id(paragraph) not in separators:
                        segments.append((f'{member}#p{index + 1}', own(paragraph)))
    elif fmt == 'odt':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            root = _parse_xml(archive.read('content.xml'), 'content.xml')
            text_ns = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
            nested = {text_ns + name for name in ('p', 'h', 'note', 'tracked-changes')} | {
                '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}annotation'
            }

            def own(node):
                text = node.text or ''
                for child in node:
                    if child.tag not in nested:
                        text += own(child)
                    text += child.tail or ''
                return text

            body = [node for node in root.iter() if node.tag in (text_ns + 'p', text_ns + 'h')]
            changes = {
                id(node) for region in root.iter(text_ns + 'tracked-changes') for node in region.iter()
            }
            for index, paragraph in enumerate(body):
                if id(paragraph) not in changes:
                    segments.append((f'content.xml#p{index + 1}', own(paragraph)))
    elif fmt == 'txt':
        segments = [(f'line:{index + 1}', line) for index, line in enumerate(source.splitlines())]
    else:
        return {
            'method': 'original_source_retained',
            'status': 'not_audited',
            'note': 'Original bytes are attached; this adapter is not text-segment audited.',
        }
    corpus, missing = _corpus(doc), []
    for location, text in segments:
        if _squash(text) and _squash(text) not in corpus:
            missing.append(location)
            _loss(
                losses,
                'content_omitted',
                'Source text was not represented as editable content; it remains in the original attachment.',
                location,
            )
    return {
        'method': 'source_text_segments',
        'segments': len(segments),
        'missing': missing,
        'status': 'omissions_reported' if missing else 'complete',
    }


def _snap_marks(doc):
    """Source runs may split grapheme clusters; widen marks to whole clusters."""
    import regex

    for block in doc['blocks']:
        for item in [block, *(cell for row in block.get('rows', []) for cell in row.get('cells', []))]:
            marks, text = item.get('marks'), item.get('text', '')
            if not marks:
                continue
            boundaries = sorted({0, len(text)} | {m.end() for m in regex.finditer(r'\X', text)})
            boundary_set = set(boundaries)
            for mark in marks:
                if mark.get('start') not in boundary_set:
                    mark['start'] = max(x for x in boundaries if x < mark['start'])
                if mark.get('end') not in boundary_set:
                    mark['end'] = min(x for x in boundaries if x > mark['end'])


def _inline(tokens):
    text, marks, opened, images = '', [], [], []
    for token in tokens or []:
        kind = token.type
        if kind in ('text', 'code_inline', 'softbreak', 'hardbreak', 'html_inline'):
            value = '\n' if kind in ('softbreak', 'hardbreak') else token.content
            start = len(text)
            text += value
            if kind == 'code_inline':
                marks.append({'start': start, 'end': len(text), 'type': 'code', 'value': True})
        elif kind == 'image':
            images.append({'src': token.attrGet('src'), 'alt': token.content})
            text += token.content
        elif kind.endswith('_open'):
            name = {
                'strong_open': 'bold',
                'em_open': 'italic',
                's_open': 'strikethrough',
                'link_open': 'link',
            }.get(kind)
            if name:
                opened.append((kind, name, len(text), token.attrGet('href') if name == 'link' else True))
        elif kind.endswith('_close') and opened:
            opening = kind.replace('_close', '_open')
            for index in range(len(opened) - 1, -1, -1):
                if opened[index][0] == opening:
                    _, name, start, value = opened.pop(index)
                    if name != 'link' or _safe_url(value or ''):
                        marks.append({'start': start, 'end': len(text), 'type': name, 'value': value})
                    break
    return text, marks, images


def _import_markdown(doc, source, losses):
    from markdown_it import MarkdownIt

    parser = MarkdownIt('commonmark', {'html': False}).enable('table')
    tokens = parser.parse(source)
    lists, quote_depth, i = [], 0, 0
    while i < len(tokens):
        token = tokens[i]
        kind = token.type
        if kind in ('bullet_list_open', 'ordered_list_open'):
            lists.append('ordered' if kind == 'ordered_list_open' else 'bullet')
        elif kind in ('bullet_list_close', 'ordered_list_close'):
            lists.pop()
        elif kind == 'blockquote_open':
            quote_depth += 1
        elif kind == 'blockquote_close':
            quote_depth -= 1
        elif kind in ('heading_open', 'paragraph_open'):
            inline = tokens[i + 1]
            text, marks, images = _inline(inline.children)
            block = _block(text, 'heading' if kind == 'heading_open' else 'paragraph')
            block['marks'] = marks
            if kind == 'heading_open':
                block['attrs']['level'] = int(token.tag[1:])
            if lists:
                block['attrs'].update({'list': lists[-1], 'level': len(lists)})
            if quote_depth:
                block['style'] = 'quotation'
                block['attrs']['quote_depth'] = quote_depth
            block['attrs']['source_lines'] = token.map
            doc['blocks'].append(block)
            for image in images:
                _loss(
                    losses,
                    'external_image',
                    'Markdown image is not fetched; source URL retained in provenance.',
                    block['id'],
                )
                block['attrs'].setdefault('external_images', []).append(image)
            i += 1
        elif kind in ('fence', 'code_block'):
            block = _block(token.content.rstrip('\n'), code_language=token.info, source_lines=token.map)
            block['style'] = 'code'
            block['marks'] = [{'start': 0, 'end': len(block['text']), 'type': 'code', 'value': True}]
            doc['blocks'].append(block)
        elif kind == 'hr':
            block = _block('―', thematic_break=True, source_lines=token.map)
            doc['blocks'].append(block)
        elif kind == 'table_open':
            block = _block(kind='table', header_rows=1, source_lines=token.map)
            rows, cells, cell = [], None, None
            i += 1
            while i < len(tokens) and tokens[i].type != 'table_close':
                current = tokens[i]
                if current.type == 'tr_open':
                    cells = []
                elif current.type in ('th_open', 'td_open'):
                    cell = {'id': _id('cell'), 'text': '', 'marks': [], 'attrs': {}}
                elif current.type == 'inline' and cell is not None:
                    cell['text'], cell['marks'], _ = _inline(current.children)
                elif current.type in ('th_close', 'td_close'):
                    cells.append(cell)
                    cell = None
                elif current.type == 'tr_close':
                    rows.append({'id': _id('row'), 'cells': cells})
                i += 1
            block['rows'] = rows
            doc['blocks'].append(block)
        i += 1


MAX_SPAN = 1000


def _heading_level(level, losses, target):
    """The model supports heading levels 1-6; deeper outline levels are reported."""
    if 1 <= level <= 6:
        return level
    _loss(losses, 'heading_level', f'Heading level {level} is outside 1-6 and was clamped.', target)
    return min(6, max(1, level))


def _span(value):
    """Row/column spans are small positive ASCII integers ("²".isdigit() is true)."""
    value = str(value).strip()
    if not value.isascii() or not value.isdecimal():
        return 1
    return max(1, min(int(value), MAX_SPAN))


class _HTMLImport(HTMLParser):
    def __init__(self, doc, losses):
        super().__init__(convert_charrefs=True)
        self.doc, self.losses = doc, losses
        self.current = None
        self.stack, self.lists, self.tables = [], [], []
        self.skip = 0

    def flush(self):
        if self.current is not None:
            if self.current['text'].strip() or self.current['type'] == 'heading':
                self.doc['blocks'].append(self.current)
            self.current = None
        self.stack = []

    def _end_cell(self):
        current = self.tables[-1]
        if current['cell'] is not None:
            current['cells'].append(current['cell'])
            current['cell'] = None

    def _end_row(self):
        # HTML lets </td>, </th> and </tr> be omitted; close them implicitly.
        self._end_cell()
        current = self.tables[-1]
        if current['row_open']:
            current['rows'].append({'id': _id('row'), 'cells': current['cells']})
            current['cells'], current['row_open'] = [], False

    def handle_starttag(self, tag, attrs):
        # Bare attributes such as <a href> have no value.
        attrs = {key: value or '' for key, value in attrs}
        if tag == 'title':
            # Document titles are metadata, never body text; </head> is optional.
            self.skip += 1
            return
        if tag in ('script', 'style', 'iframe', 'object', 'embed', 'svg', 'math'):
            if tag != 'embed':
                self.skip += 1
            _loss(
                self.losses,
                'html_inactive_content',
                f'<{tag}> was not executed or rendered; original HTML remains attached.',
                f'line:{self.getpos()[0]}',
            )
            return
        if self.skip:
            return
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'li'):
            self.flush()
            self.current = _block(kind='heading' if re.fullmatch(r'h[1-6]', tag) else 'paragraph')
            if tag.startswith('h') and len(tag) == 2:
                self.current['attrs']['level'] = int(tag[1])
            if tag == 'blockquote':
                self.current['style'] = 'quotation'
            if tag == 'pre':
                self.current['style'] = 'code'
            if tag == 'li':
                self.current['attrs'].update(
                    {'list': self.lists[-1] if self.lists else 'bullet', 'level': len(self.lists)}
                )
        elif tag in ('ul', 'ol'):
            self.lists.append('ordered' if tag == 'ol' else 'bullet')
        elif tag == 'br':
            self.handle_data('\n')
        elif tag == 'table':
            self.flush()
            self.tables.append(
                {
                    'block': _block(kind='table', header_rows=0),
                    'rows': [],
                    'cells': [],
                    'cell': None,
                    'row_open': False,
                }
            )
        elif tag == 'tr' and self.tables:
            self._end_row()
            self.tables[-1]['row_open'] = True
        elif tag in ('th', 'td') and self.tables:
            self._end_cell()
            self.tables[-1]['row_open'] = True
            self.tables[-1]['cell'] = {
                'id': _id('cell'),
                'text': '',
                'attrs': {key: _span(attrs[key]) for key in ('rowspan', 'colspan') if attrs.get(key)},
            }
            if tag == 'th':
                self.tables[-1]['block']['attrs']['header_rows'] = 1
        elif tag == 'img':
            self.flush()
            block = _block(kind='image', alt=attrs.get('alt', ''), source=attrs.get('src', ''))
            self.doc['blocks'].append(block)
            data_uri = re.fullmatch(
                r'data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)', attrs.get('src', '')
            )
            if data_uri and len(data_uri[2]) <= MAX_INPUT * 2:
                try:
                    raw = base64.b64decode(data_uri[2], validate=False)
                    block['attrs']['asset'] = _asset(self.doc, raw, data_uri[1], 'embedded-image')
                except ValueError:
                    _loss(self.losses, 'invalid_image', 'Malformed embedded image data.', block['id'])
            else:
                if attrs.get('src'):
                    link_external_image(self.doc, block, attrs['src'])
                _loss(
                    self.losses,
                    'external_image',
                    'HTML images are not fetched automatically; explicit embedding is required.',
                    block['id'],
                )
        else:
            mark = {
                'b': 'bold',
                'strong': 'bold',
                'i': 'italic',
                'em': 'italic',
                'u': 'underline',
                's': 'strikethrough',
                'del': 'strikethrough',
                'code': 'code',
                'sup': 'superscript',
                'sub': 'subscript',
                'a': 'link',
            }.get(tag)
            if mark:
                if self.current is None:
                    self.current = _block()
                self.stack.append(
                    (tag, mark, len(self.current['text']), attrs.get('href', '') if mark == 'link' else True)
                )
        if attrs.get('style') or attrs.get('class'):
            _loss(
                self.losses,
                'html_css',
                'CSS styling is not imported by the semantic HTML adapter.',
                f'line:{self.getpos()[0]}',
            )

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'iframe', 'object', 'svg', 'math', 'title') and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag in ('td', 'th') and self.tables:
            self._end_cell()
        elif tag == 'tr' and self.tables:
            self._end_row()
        elif tag == 'table' and self.tables:
            self._end_row()
            current = self.tables.pop()
            current['block']['rows'] = current['rows']
            self.doc['blocks'].append(current['block'])
        elif tag in ('ul', 'ol') and self.lists:
            self.lists.pop()
        elif tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'li'):
            self.flush()
        elif self.current:
            for index in range(len(self.stack) - 1, -1, -1):
                item_tag, mark, start, value = self.stack[index]
                if item_tag == tag:
                    self.stack.pop(index)
                    if mark != 'link' or _safe_url(value):
                        self.current['marks'].append(
                            {'start': start, 'end': len(self.current['text']), 'type': mark, 'value': value}
                        )
                    break

    def handle_data(self, data):
        if self.skip:
            return
        if self.tables and self.tables[-1]['cell'] is not None:
            self.tables[-1]['cell']['text'] += data
        else:
            if self.current is None:
                if not data.strip():
                    return
                self.current = _block()
            self.current['text'] += data


def _import_docx(doc, raw, losses):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run

    word = Document(io.BytesIO(raw))
    for style in word.styles:
        try:
            if str(style.type) not in ('PARAGRAPH (1)', 'CHARACTER (2)'):
                continue
            values = {'family': 'text' if str(style.type) == 'CHARACTER (2)' else 'paragraph'}
            if style.base_style is not None:
                values['based_on'] = style.base_style.style_id
            for key, value in (
                ('bold', style.font.bold),
                ('italic', style.font.italic),
                ('underline', style.font.underline),
                ('strikethrough', style.font.strike),
                ('font_family', style.font.name),
                ('font_size', style.font.size.pt if style.font.size else None),
            ):
                if value is not None:
                    values[key] = value
            if style.font.color.rgb:
                values['color'] = '#' + str(style.font.color.rgb)
            if str(style.type) == 'PARAGRAPH (1)':
                pf = style.paragraph_format
                for key, prop in (
                    ('space_before', 'space_before'),
                    ('space_after', 'space_after'),
                    ('indent', 'left_indent'),
                    ('first_line_indent', 'first_line_indent'),
                ):
                    value = getattr(pf, prop)
                    if value is not None:
                        values[key] = value.pt
            doc['styles'][style.style_id] = values
        except (ValueError, AttributeError):
            _loss(
                losses,
                'docx_style_partial',
                'A style uses values outside the supported Word subset and was not imported; the original remains attached.',
                'styles/' + str(style.style_id),
            )
    if word.sections:
        section = word.sections[0]
        if section.page_width and section.page_height:
            doc['layout'].update({'width': section.page_width.pt, 'height': section.page_height.pt})
        for key in ('top', 'right', 'bottom', 'left'):
            if getattr(section, f'{key}_margin') is not None:
                doc['layout']['margins'][key] = getattr(section, f'{key}_margin').pt
        doc['layout']['header'] = '\n'.join(p.text for p in section.header.paragraphs)
        doc['layout']['footer'] = '\n'.join(p.text for p in section.footer.paragraphs)
        if len(word.sections) > 1:
            _loss(
                losses,
                'docx_sections',
                'Only first section page geometry and repeated regions are imported.',
                'sections',
            )
    doc['metadata'].update(
        {
            'title': word.core_properties.title or doc['metadata']['title'],
            'author': word.core_properties.author or '',
        }
    )
    w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    mc = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
    numbering = _docx_numbering(word, w)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        notes = {kind: _docx_notes(archive, kind, w) for kind in ('footnote', 'endnote')}
        comments = _docx_comments(archive, w)
        if any(name.startswith('word/vbaProject') for name in archive.namelist()):
            _loss(
                losses,
                'docx_macros',
                'Macro project retained only in the original attachment; it is never executed.',
                'word/vbaProject.bin',
            )
    anchors, ends, bookmarks = {}, {}, doc['settings'].setdefault('bookmarks', {})

    def paragraph_block(element, paragraph):
        name = paragraph.style.name if paragraph.style else 'Normal'
        match = re.match(r'Heading\s*(\d)', name, re.IGNORECASE)
        block = _block('', 'heading' if match else 'paragraph')
        if paragraph.style:
            block['style'] = paragraph.style.style_id
        if match:
            block['attrs']['level'] = _heading_level(int(match[1]), losses, block['id'])
        try:
            alignment = {0: 'left', 1: 'center', 2: 'right', 3: 'justify'}.get(paragraph.alignment)
        except ValueError:
            # Producers such as LibreOffice write bidi-aware values python-docx cannot map.
            value = element.find(w + 'pPr/' + w + 'jc') if element.find(w + 'pPr') is not None else None
            alignment = {'start': 'left', 'end': 'right', 'both': 'justify', 'center': 'center'}.get(
                value.get(w + 'val') if value is not None else None
            )
        if alignment:
            block['attrs']['alignment'] = alignment
        for prop, target in (
            ('space_before', 'space_before'),
            ('space_after', 'space_after'),
            ('left_indent', 'indent'),
            ('first_line_indent', 'first_line_indent'),
        ):
            value = getattr(paragraph.paragraph_format, prop)
            if value is not None:
                block['attrs'][target] = value.pt
        state = {'inserted': [], 'deleted': [], 'fields': [], 'stack': [], 'notes': [], 'authors': set()}

        def add_run(node, hyperlink, change):
            run = Run(node, paragraph)
            for item in node:
                if item.tag == w + 'fldChar':
                    kind = item.get(w + 'fldCharType')
                    if kind == 'begin':
                        state['stack'].append({'instruction': '', 'start': None})
                    elif kind == 'separate' and state['stack']:
                        state['stack'][-1]['start'] = len(block['text'])
                    elif kind == 'end' and state['stack']:
                        field = state['stack'].pop()
                        start = len(block['text']) if field['start'] is None else field['start']
                        state['fields'].append(
                            {
                                'instruction': field['instruction'].strip(),
                                'start': start,
                                'end': len(block['text']),
                            }
                        )
                elif item.tag == w + 'instrText' and state['stack']:
                    state['stack'][-1]['instruction'] += item.text or ''
                elif item.tag in (w + 'footnoteReference', w + 'endnoteReference'):
                    state['notes'].append(
                        ('footnote' if item.tag == w + 'footnoteReference' else 'endnote', item.get(w + 'id'))
                    )
                elif item.tag == w + 'commentReference':
                    anchors.setdefault(item.get(w + 'id'), (block, len(block['text'])))
                    ends.setdefault(item.get(w + 'id'), (block, len(block['text'])))
            text = run.text
            start, end = len(block['text']), len(block['text']) + len(text)
            if not text:
                return
            if hyperlink and _safe_url(hyperlink):
                block['marks'].append({'start': start, 'end': end, 'type': 'link', 'value': hyperlink})
            for key, value in (
                ('bold', run.bold),
                ('italic', run.italic),
                ('underline', run.underline),
                ('strikethrough', run.font.strike),
                ('superscript', run.font.superscript),
                ('subscript', run.font.subscript),
                ('font_family', run.font.name),
                ('font_size', run.font.size.pt if run.font.size else None),
                ('color', '#' + str(run.font.color.rgb) if run.font.color.rgb else None),
            ):
                if value is not None:
                    block['marks'].append({'start': start, 'end': end, 'type': key, 'value': value})
            block['text'] += text
            if change:
                state['inserted'].append((start, end))

        def walk(element, hyperlink=None, change=None):
            for node in element:
                tag = node.tag
                if tag == w + 'r':
                    add_run(node, hyperlink, change)
                elif tag == w + 'hyperlink':
                    rid = node.get(qn('r:id'))
                    url = word.part.rels[rid].target_ref if rid in word.part.rels else None
                    if node.get(w + 'anchor'):
                        url = '#' + node.get(w + 'anchor')
                    walk(node, url, change)
                elif tag in (w + 'ins', w + 'moveTo'):
                    state['authors'].add(node.get(w + 'author', ''))
                    walk(node, hyperlink, 'insert')
                elif tag in (w + 'del', w + 'moveFrom'):
                    state['authors'].add(node.get(w + 'author', ''))
                    removed = ''.join(item.text or '' for item in node.iter(w + 'delText'))
                    removed += ''.join(item.text or '' for item in node.iter(w + 't'))
                    if removed:
                        state['deleted'].append((len(block['text']), removed))
                elif tag == w + 'fldSimple':
                    start = len(block['text'])
                    walk(node, hyperlink, change)
                    state['fields'].append(
                        {
                            'instruction': (node.get(w + 'instr') or '').strip(),
                            'start': start,
                            'end': len(block['text']),
                        }
                    )
                elif tag in (w + 'smartTag', w + 'sdt', w + 'sdtContent', w + 'customXml'):
                    walk(node, hyperlink, change)
                elif tag == w + 'bookmarkStart':
                    name = node.get(w + 'name')
                    if name and not name.startswith('_'):
                        bookmarks[name] = {'target': block['id'], 'offset': len(block['text'])}
                elif tag == w + 'commentRangeStart':
                    anchors[node.get(w + 'id')] = (block, len(block['text']))
                elif tag == w + 'commentRangeEnd':
                    ends[node.get(w + 'id')] = (block, len(block['text']))

        walk(element)
        for field in state['fields']:
            link = re.match(r'HYPERLINK\s+(\\l\s+)?"([^"]+)"', field['instruction'])
            if link and field['end'] > field['start']:
                url = ('#' if link[1] else '') + link[2]
                if _safe_url(url):
                    block['marks'].append(
                        {'start': field['start'], 'end': field['end'], 'type': 'link', 'value': url}
                    )
                continue
            block['attrs'].setdefault('fields', []).append(field)
            _loss(
                losses,
                'docx_field',
                f'Field result imported as static text; instruction {field["instruction"].split(" ")[0] or "(empty)"} is recorded but will not update.',
                block['id'],
            )
        if state['inserted'] or state['deleted']:
            before = _before_text(block['text'], state['inserted'], state['deleted'])
            doc['review']['tracked_changes'].append(
                {
                    'id': _id('change'),
                    'target': block['id'],
                    'before': before,
                    'after': block['text'],
                    'before_marks': [],
                    'after_marks': copy.deepcopy(block['marks']),
                    'status': 'pending',
                    'base_revision': doc['revision'],
                    'source': 'docx',
                    'authors': sorted(a for a in state['authors'] if a),
                }
            )
        properties = element.find(w + 'pPr')
        numbering_properties = properties.find(w + 'numPr') if properties is not None else None
        if numbering_properties is not None:
            num = numbering_properties.find(w + 'numId')
            level = numbering_properties.find(w + 'ilvl')
            ilvl = int(level.get(w + 'val', '0')) if level is not None else 0
            formats, overridden = numbering.get(num.get(w + 'val') if num is not None else None, ({}, False))
            block['attrs']['list'] = (
                'bullet' if formats.get(ilvl, 'bullet') in ('bullet', 'none') else 'ordered'
            )
            block['attrs']['level'] = ilvl + 1
            if overridden:
                _loss(
                    losses,
                    'docx_list_numbering',
                    'List kind and level imported; numbering restarts/start overrides are not resolved.',
                    block['id'],
                )
        return block, state['notes']

    for child in word.element.body:
        if child.tag == qn('w:p'):
            block, note_refs = paragraph_block(child, Paragraph(child, word))
            doc['blocks'].append(block)
            for kind, note_id in note_refs:
                note = _block(notes[kind].get(note_id, ''), 'footnote', reference=block['id'])
                if kind == 'endnote':
                    note['attrs']['note_class'] = 'endnote'
                    _loss(
                        losses,
                        'docx_endnote',
                        'Endnote imported as an associated note; end-of-document placement is not preserved.',
                        note['id'],
                    )
                doc['blocks'].append(note)
            for drawing in child.xpath('.//a:blip'):
                relationship = drawing.get(qn('r:embed'))
                if relationship in word.part.related_parts:
                    part = word.part.related_parts[relationship]
                    key = _asset(doc, part.blob, part.content_type, str(part.partname).split('/')[-1])
                    image = _block(kind='image', asset=key, alt='')
                    doc['blocks'].append(image)
                    _loss(
                        losses,
                        'docx_image_anchor',
                        'Image bytes imported; original inline/anchor position, wrapping and alternative text require review.',
                        image['id'],
                    )
                elif drawing.get(qn('r:link')):
                    _loss(
                        losses,
                        'docx_linked_image',
                        'Linked external image was not retrieved; explicit embedding is required.',
                        block['id'],
                    )
            for box in child.iter(w + 'txbxContent'):
                if any(ancestor.tag == mc + 'Fallback' for ancestor in box.iterancestors()):
                    continue
                for inner in box.iter(w + 'p'):
                    text_box, _refs = paragraph_block(inner, Paragraph(inner, word))
                    text_box['attrs']['text_box'] = True
                    doc['blocks'].append(text_box)
                    _loss(
                        losses,
                        'docx_textbox',
                        'Text box content imported as a following paragraph; frame position and wrapping are not preserved.',
                        text_box['id'],
                    )
            for unsupported in ('pict', 'object', 'oMath'):
                if child.xpath(f'.//w:{unsupported}' if unsupported != 'oMath' else './/m:oMath'):
                    _loss(
                        losses,
                        'docx_' + unsupported,
                        f'Word {unsupported} structure is retained only in original attachment; it is not executed or editable.',
                        block['id'],
                    )
        elif child.tag == qn('w:tbl'):
            table = Table(child, word)
            block = _block(
                kind='table',
                header_rows=1 if table.rows and table.rows[0]._tr.xpath('./w:trPr/w:tblHeader') else 0,
            )
            block['rows'] = [
                {
                    'id': _id('row'),
                    'cells': [{'id': _id('cell'), 'text': cell.text, 'attrs': {}} for cell in row.cells],
                }
                for row in table.rows
            ]
            doc['blocks'].append(block)
            _loss(
                losses,
                'docx_table_format',
                'Table text and grid retained; cell marks, merged spans, widths and border details require review.',
                block['id'],
            )
        elif child.tag == qn('w:sdt'):
            for inner in child.iter(w + 'p'):
                block, _refs = paragraph_block(inner, Paragraph(inner, word))
                doc['blocks'].append(block)
            _loss(
                losses,
                'docx_content_control',
                'Content control text imported; control binding and protection are not preserved.',
                'body',
            )
        elif child.tag != qn('w:sectPr'):
            _loss(
                losses,
                'docx_unknown',
                f'Unsupported Word body element {child.tag}; original remains attached.',
                'body',
            )
    for comment_id, comment in comments.items():
        anchor = anchors.get(comment_id)
        if anchor is None:
            if not doc['blocks']:
                continue
            anchor = (doc['blocks'][0], 0)
            _loss(
                losses,
                'docx_comment_unanchored',
                'Comment had no resolvable anchor; attached to the first block.',
                doc['blocks'][0]['id'],
            )
        block, start = anchor
        end_block, end = ends.get(comment_id, (block, len(block['text'])))
        if end_block is not block or end < start:
            end = len(block['text'])
            _loss(
                losses,
                'docx_comment_range',
                'Comment range spans paragraphs; anchored to the end of its first paragraph.',
                block['id'],
            )
        doc['review']['comments'].append(
            {
                'id': _id('comment'),
                'target': block['id'],
                'start': start,
                'end': end,
                'text': comment['text'],
                'author': comment['author'],
                'status': 'open',
                'revision': doc['revision'],
                'source': 'docx',
            }
        )


def _before_text(text, inserted, deleted):
    """Reconstruct pre-review text from inserted ranges and point deletions."""
    covered = {i for start, end in inserted for i in range(start, end)}
    removed = {}
    for offset, value in deleted:
        removed[offset] = removed.get(offset, '') + value
    return ''.join(
        removed.get(i, '') + ('' if i in covered else char) for i, char in enumerate(text)
    ) + removed.get(len(text), '')


def _docx_numbering(word, w):
    try:
        root = word.part.numbering_part.element
    except (KeyError, NotImplementedError, AttributeError):
        return {}
    abstract = {}
    for item in root.findall(w + 'abstractNum'):
        levels = {}
        for level in item.findall(w + 'lvl'):
            fmt = level.find(w + 'numFmt')
            levels[int(level.get(w + 'ilvl', '0'))] = fmt.get(w + 'val') if fmt is not None else 'bullet'
        abstract[item.get(w + 'abstractNumId')] = levels
    result = {}
    for num in root.findall(w + 'num'):
        reference = num.find(w + 'abstractNumId')
        result[num.get(w + 'numId')] = (
            abstract.get(reference.get(w + 'val') if reference is not None else None, {}),
            bool(num.findall(w + 'lvlOverride')),
        )
    return result


def _docx_notes(archive, kind, w):
    member = f'word/{kind}s.xml'
    if member not in archive.namelist():
        return {}
    root = _parse_xml(archive.read(member), member)
    return {
        note.get(w + 'id'): '\n'.join(
            ''.join(t.text or '' for t in p.iter(w + 't')) for p in note.iter(w + 'p')
        ).strip()
        for note in root.findall(w + kind)
        if note.get(w + 'type') in (None, 'normal')
    }


def _docx_comments(archive, w):
    if 'word/comments.xml' not in archive.namelist():
        return {}
    root = _parse_xml(archive.read('word/comments.xml'), 'word/comments.xml')
    return {
        item.get(w + 'id'): {
            'author': item.get(w + 'author', ''),
            'text': '\n'.join(
                ''.join(t.text or '' for t in p.iter(w + 't')) for p in item.iter(w + 'p')
            ).strip(),
        }
        for item in root.findall(w + 'comment')
    }


def _import_odt(doc, raw, losses):
    from defusedxml.ElementTree import fromstring

    ns = {
        'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
        'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
        'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
        'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
        'xlink': 'http://www.w3.org/1999/xlink',
        'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
        'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
        'svg': 'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0',
        'xml': 'http://www.w3.org/XML/1998/namespace',
    }

    def attr(element, family, key, default=None):
        return element.get('{' + ns[family] + '}' + key, default)

    def integer(element, family, key, default):
        value = attr(element, family, key, default)
        if not re.fullmatch(r'-?[0-9]{1,9}', str(value).strip()):
            raise ByaxError('invalid_input', f'ODT {key} must be an integer', field=key)
        return int(value)

    def points(value):
        match = re.fullmatch(r'(-?[0-9]+(?:\.[0-9]+)?|-?\.[0-9]+)(pt|in|cm|mm|px)?', str(value))
        if not match:
            return None
        return (
            float(match[1])
            * {None: 1, 'pt': 1, 'in': 72, 'cm': 72 / 2.54, 'mm': 72 / 25.4, 'px': 0.75}[match[2]]
        )

    with _safe_zip(raw) as archive:
        if 'META-INF/manifest.xml' in archive.namelist() and b'encryption-data' in archive.read(
            'META-INF/manifest.xml'
        ):
            raise ByaxError('encrypted_input', 'Encrypted ODT input is not supported')
        if 'content.xml' not in archive.namelist():
            raise ByaxError('invalid_input', 'ODT lacks content.xml')
        roots = [fromstring(archive.read('content.xml'))]
        if 'styles.xml' in archive.namelist():
            roots.append(fromstring(archive.read('styles.xml')))
        fonts, list_styles = {}, {}
        for root in roots:
            for listing in root.findall('.//text:list-style', ns):
                list_styles[attr(listing, 'style', 'name')] = (
                    'ordered' if listing.find('text:list-level-style-number', ns) is not None else 'bullet'
                )
        for root in roots:
            for face in root.findall('.//style:font-face', ns):
                fonts[attr(face, 'style', 'name')] = attr(face, 'svg', 'font-family', '').strip("'")
            for style in root.findall('.//style:style', ns):
                name = attr(style, 'style', 'name')
                values = {'family': attr(style, 'style', 'family', 'paragraph')}
                parent = attr(style, 'style', 'parent-style-name')
                if parent:
                    values['based_on'] = parent
                properties = style.find('style:text-properties', ns)
                if properties is not None:
                    font = attr(properties, 'fo', 'font-family') or fonts.get(
                        attr(properties, 'style', 'font-name')
                    )
                    if font:
                        values['font_family'] = font.strip("'")
                    size = points(attr(properties, 'fo', 'font-size', ''))
                    if size:
                        values['font_size'] = size
                    for key, prop, setting in (
                        ('bold', 'font-weight', 'bold'),
                        ('italic', 'font-style', 'italic'),
                    ):
                        value = attr(properties, 'fo', prop)
                        if value:
                            values[key] = value == setting
                    for key in ('color', 'background-color'):
                        value = attr(properties, 'fo', key)
                        if value and value != 'transparent':
                            values['highlight' if key == 'background-color' else key] = value
                    if attr(properties, 'style', 'text-underline-style', 'none') != 'none':
                        values['underline'] = True
                    if attr(properties, 'style', 'text-line-through-style', 'none') != 'none':
                        values['strikethrough'] = True
                paragraph = style.find('style:paragraph-properties', ns)
                if paragraph is not None:
                    alignment = attr(paragraph, 'fo', 'text-align')
                    if alignment:
                        values['alignment'] = {'start': 'left', 'end': 'right'}.get(alignment, alignment)
                    for key, source in (
                        ('space_before', 'margin-top'),
                        ('space_after', 'margin-bottom'),
                        ('indent', 'margin-left'),
                        ('first_line_indent', 'text-indent'),
                    ):
                        value = points(attr(paragraph, 'fo', source, ''))
                        if value is not None:
                            values[key] = value
                    line_height = attr(paragraph, 'fo', 'line-height', '')
                    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?%', line_height):
                        values['line_spacing'] = float(line_height[:-1]) / 100
                if name:
                    doc['styles'][name] = values
        for root in roots:
            page = root.find('.//style:page-layout-properties', ns)
            if page is not None:
                for target, source in (('width', 'page-width'), ('height', 'page-height')):
                    value = points(attr(page, 'fo', source, ''))
                    if value:
                        doc['layout'][target] = value
                for side in ('top', 'right', 'bottom', 'left'):
                    value = points(attr(page, 'fo', 'margin-' + side, ''))
                    if value is not None:
                        doc['layout']['margins'][side] = value
            for region in ('header', 'footer'):
                element = root.find('.//style:' + region, ns)
                if element is not None:
                    doc['layout'][region] = '\n'.join(
                        ''.join(p.itertext()) for p in element.findall('text:p', ns)
                    )

        def style_values(name, seen=None):
            seen = set() if seen is None else seen
            if not name or name in seen:
                return {}
            seen.add(name)
            own = doc['styles'].get(name, {})
            return {**style_values(own.get('based_on'), seen), **own}

        inline_keys = {
            'bold',
            'italic',
            'underline',
            'strikethrough',
            'font_family',
            'font_size',
            'color',
            'highlight',
            'superscript',
            'subscript',
        }
        spaces = [0]

        def rich_text(element, block):
            def walk(node, depth=0):
                if depth > MAX_DEPTH:
                    raise ByaxError('resource_limit', 'ODT inline nesting is too deep')
                start = len(block['text'])
                if node.text:
                    block['text'] += node.text
                for child in node:
                    name = child.tag.rsplit('}', 1)[-1]
                    if name == 's':
                        count = integer(child, 'text', 'c', '1')
                        spaces[0] += max(count, 0)
                        if count < 0 or count > 100000 or spaces[0] > MAX_REPEATED_SPACES:
                            raise ByaxError('resource_limit', 'ODT repeated-space limit exceeded')
                        block['text'] += ' ' * count
                    elif name == 'tab':
                        block['text'] += '\t'
                    elif name == 'line-break':
                        block['text'] += '\n'
                    elif name == 'annotation' and child.tag.startswith('{' + ns['office']):
                        paragraphs = child.findall('text:p', ns)
                        creator = (
                            next((node.text for node in child if node.tag.endswith('}creator')), '') or ''
                        )
                        pending_comments.append(
                            {
                                'block': block,
                                'start': len(block['text']),
                                'name': attr(child, 'office', 'name'),
                                'text': '\n'.join(''.join(p.itertext()) for p in paragraphs),
                                'author': creator,
                            }
                        )
                    elif name == 'annotation-end':
                        for comment in pending_comments:
                            if (
                                comment.get('name')
                                and comment['name'] == attr(child, 'office', 'name')
                                and comment['block'] is block
                            ):
                                comment['end'] = len(block['text'])
                    elif name in ('bookmark', 'bookmark-start'):
                        bookmark = attr(child, 'text', 'name')
                        if bookmark:
                            doc['settings'].setdefault('bookmarks', {})[bookmark] = {
                                'target': block['id'],
                                'offset': len(block['text']),
                            }
                    elif name in ('change-start', 'change-end', 'change', 'bookmark-end', 'soft-page-break'):
                        change_id = attr(child, 'text', 'change-id')
                        events = tracked_marks.setdefault(
                            block['id'], {'open': {}, 'inserted': [], 'deleted': []}
                        )
                        if name == 'change-start':
                            events['open'][change_id] = len(block['text'])
                        elif name == 'change-end' and change_id in events['open']:
                            start = events['open'].pop(change_id)
                            if regions.get(change_id, ('',))[0] == 'insertion':
                                events['inserted'].append((start, len(block['text'])))
                        elif name == 'change' and regions.get(change_id, ('',))[0] == 'deletion':
                            events['deleted'].append((len(block['text']), regions[change_id][1]))
                    elif name == 'note':
                        body = child.find('text:note-body', ns)
                        note = _block(
                            '\n'.join(''.join(p.itertext()) for p in body.findall('text:p', ns))
                            if body is not None
                            else '',
                            'footnote',
                            reference=block['id'],
                        )
                        pending_notes.append(note)
                    elif name == 'frame':
                        picture = child.find('draw:image', ns)
                        if picture is not None:
                            source = attr(picture, 'xlink', 'href', '')
                            image = _block(
                                kind='image',
                                alt='',
                                width=points(attr(child, 'svg', 'width', '4in')),
                                height=points(attr(child, 'svg', 'height', '3in')),
                            )
                            if source in archive.namelist():
                                ext = Path(source).suffix.lower().lstrip('.')
                                mime = 'image/' + {'jpg': 'jpeg', 'svg': 'svg+xml'}.get(ext, ext)
                                image['attrs']['asset'] = _asset(
                                    doc, archive.read(source), mime, Path(source).name
                                )
                            else:
                                image['attrs']['source'] = source
                                link_external_image(doc, image, source)
                                _loss(
                                    losses,
                                    'odt_external_image',
                                    'External image was not fetched.',
                                    image['id'],
                                )
                            pending_images.append(image)
                    else:
                        child_start = len(block['text'])
                        walk(child, depth + 1)
                        if name == 'a':
                            href = attr(child, 'xlink', 'href', '')
                            if _safe_url(href):
                                block['marks'].append(
                                    {
                                        'start': child_start,
                                        'end': len(block['text']),
                                        'type': 'link',
                                        'value': href,
                                    }
                                )
                        elif name in (
                            'date',
                            'time',
                            'page-number',
                            'page-count',
                            'author-name',
                            'title',
                            'subject',
                            'chapter',
                            'user-field-get',
                            'variable-get',
                            'sequence',
                            'bookmark-ref',
                            'reference-ref',
                            'note-ref',
                            'file-name',
                        ):
                            block['attrs'].setdefault('fields', []).append(
                                {'instruction': name, 'start': child_start, 'end': len(block['text'])}
                            )
                            _loss(
                                losses,
                                'odt_field',
                                f'ODT {name} field result imported as static text; it will not update.',
                                block['id'],
                            )
                    if child.tail:
                        block['text'] += child.tail
                for key, value in style_values(attr(node, 'text', 'style-name')).items():
                    if key in inline_keys:
                        block['marks'].append(
                            {'start': start, 'end': len(block['text']), 'type': key, 'value': value}
                        )

            walk(element)

        pending_notes, pending_images, pending_comments, tracked_marks = [], [], [], {}

        def paragraph(element, list_level=0, list_kind='bullet'):
            name = element.tag.rsplit('}', 1)[-1]
            block = _block(kind='heading' if name == 'h' else 'paragraph')
            if name == 'h':
                block['attrs']['level'] = _heading_level(
                    integer(element, 'text', 'outline-level', '1'), losses, block['id']
                )
            style = attr(element, 'text', 'style-name')
            if style in doc['styles']:
                block['style'] = style
            if list_level:
                block['attrs'].update(list=list_kind, level=list_level)
            rich_text(element, block)
            return block

        def visit(element, level=0, list_kind='bullet', depth=0):
            if depth > MAX_DEPTH:
                raise ByaxError('resource_limit', 'ODT structure nesting is too deep')
            name = element.tag.rsplit('}', 1)[-1]
            if name in ('p', 'h'):
                doc['blocks'].append(paragraph(element, level, list_kind))
                doc['blocks'].extend(pending_images)
                doc['blocks'].extend(pending_notes)
                pending_images.clear()
                pending_notes.clear()
            elif name in ('list', 'list-item', 'section'):
                if name == 'list':
                    list_kind = list_styles.get(attr(element, 'text', 'style-name'), list_kind)
                for child in element:
                    visit(child, level + (1 if name == 'list' else 0), list_kind, depth + 1)
                if name == 'section':
                    _loss(
                        losses,
                        'odt_section_geometry',
                        'Section content retained; section-specific geometry requires review.',
                        'body/section',
                    )
            elif name == 'table':
                block = _block(kind='table', header_rows=0)
                block['rows'] = []
                row_elements = list(element.findall('table:table-row', ns))
                header = element.find('table:table-header-rows', ns)
                if header is not None:
                    headers = list(header.findall('table:table-row', ns))
                    row_elements = headers + row_elements
                    block['attrs']['header_rows'] = len(headers)
                total = 0
                for row in row_elements:
                    cells = []
                    row_repeat = integer(row, 'table', 'number-rows-repeated', '1')
                    if not 1 <= row_repeat <= 1000:
                        raise ByaxError('resource_limit', 'ODT repeated-row limit exceeded')
                    for cell in row:
                        count = integer(cell, 'table', 'number-columns-repeated', '1')
                        if not 1 <= count <= 1000:
                            raise ByaxError('resource_limit', 'ODT repeated-column limit exceeded')
                        # Check before expanding so repeats cannot allocate first.
                        if total + (len(cells) + count) * row_repeat > MAX_TABLE_CELLS:
                            raise ByaxError('resource_limit', 'ODT table exceeds 100,000 cells')
                        paragraphs = [paragraph(p) for p in cell.findall('text:p', ns)]
                        value = {'id': _id('cell'), 'text': '', 'marks': [], 'attrs': {}}
                        for p in paragraphs:
                            offset = len(value['text'])
                            value['text'] += ('\n' if offset else '') + p['text']
                            shift = offset + (1 if offset else 0)
                            value['marks'].extend(
                                {**m, 'start': m['start'] + shift, 'end': m['end'] + shift}
                                for m in p['marks']
                            )
                        for key, xml in (
                            ('rowspan', 'number-rows-spanned'),
                            ('colspan', 'number-columns-spanned'),
                        ):
                            if attr(cell, 'table', xml):
                                value['attrs'][key] = _span(attr(cell, 'table', xml))
                        for _ in range(count):
                            cells.append({**copy.deepcopy(value), 'id': _id('cell')})
                    for _ in range(row_repeat):
                        block['rows'].append(
                            {
                                'id': _id('row'),
                                'cells': [{**copy.deepcopy(c), 'id': _id('cell')} for c in cells],
                            }
                        )
                    total += len(cells) * row_repeat
                doc['blocks'].append(block)
            elif name == 'tracked-changes':
                if any(kind == 'format-change' for kind, _ in regions.values()):
                    _loss(
                        losses,
                        'odt_format_change',
                        'Tracked formatting changes are not imported as native review.',
                        'body/tracked-changes',
                    )
            elif name in ('sequence-decls', 'user-field-decls', 'variable-decls'):
                _loss(
                    losses,
                    'odt_fields',
                    'ODT field definitions retained only in original attachment.',
                    'body/' + name,
                )
            else:
                text = ''.join(element.itertext()).strip()
                if text:
                    doc['blocks'].append(_block(text))
                _loss(
                    losses,
                    'odt_structure',
                    f'Unsupported ODT {name} flattened to text and preserved in original.',
                    'body/' + name,
                )

        body = roots[0].find('.//office:text', ns)
        if body is None:
            raise ByaxError('invalid_input', 'ODT lacks text body')
        regions = {}
        for region in body.findall('text:tracked-changes/text:changed-region', ns):
            for kind in ('insertion', 'deletion', 'format-change'):
                change = region.find('text:' + kind, ns)
                if change is not None:
                    removed = '\n'.join(''.join(p.itertext()) for p in change.findall('text:p', ns))
                    regions[attr(region, 'text', 'id') or attr(region, 'xml', 'id')] = (kind, removed)
        for element in body:
            visit(element)
        known = {block['id'] for block in doc['blocks']}
        for comment in pending_comments:
            block = comment['block']
            if block['id'] not in known:
                block = doc['blocks'][0] if doc['blocks'] else None
                if block is None:
                    continue
                comment.update(start=0, end=0)
                _loss(
                    losses,
                    'odt_comment_unanchored',
                    'Comment anchored inside an unsupported container; attached to the first block.',
                    block['id'],
                )
            end = max(comment['start'], min(comment.get('end', len(block['text'])), len(block['text'])))
            doc['review']['comments'].append(
                {
                    'id': _id('comment'),
                    'target': block['id'],
                    'start': min(comment['start'], end),
                    'end': end,
                    'text': comment['text'],
                    'author': comment['author'],
                    'status': 'open',
                    'revision': doc['revision'],
                    'source': 'odt',
                }
            )
        for block in doc['blocks']:
            events = tracked_marks.get(block['id'])
            if events and (events['inserted'] or events['deleted']):
                doc['review']['tracked_changes'].append(
                    {
                        'id': _id('change'),
                        'target': block['id'],
                        'before': _before_text(block['text'], events['inserted'], events['deleted']),
                        'after': block['text'],
                        'before_marks': [],
                        'after_marks': copy.deepcopy(block['marks']),
                        'status': 'pending',
                        'base_revision': doc['revision'],
                        'source': 'odt',
                    }
                )


def _run_bounded(command, timeout):
    """Run an explicitly configured local converter; kill its whole process group on timeout."""
    options = (
        {'start_new_session': True}
        if os.name == 'posix'
        else {'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        shell=False,
        **options,
    )
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            import signal

            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
        else:
            process.kill()
        process.communicate()
        raise
    return process.returncode, stdout[:4096]


def _import_doc(path):
    configured = os.environ.get('BYAX_LIBREOFFICE')
    if not configured or not Path(configured).is_absolute() or not Path(configured).is_file():
        raise ByaxError(
            'adapter_unavailable',
            'DOC import requires the absolute path to a local LibreOffice executable in BYAX_LIBREOFFICE',
        )
    executable = str(Path(configured).resolve())
    with tempfile.TemporaryDirectory(prefix='byax-doc-') as temporary:
        root = Path(temporary)
        profile = root / 'profile'
        (profile / 'user').mkdir(parents=True)
        (profile / 'user' / 'registrymodifications.xcu').write_text(
            '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>',
            encoding='utf-8',
        )
        environment = [
            '-env:UserInstallation=' + profile.as_uri(),
            '--headless',
            '--nologo',
            '--nodefault',
            '--norestore',
        ]
        try:
            code, version = _run_bounded([executable, *environment, '--version'], min(DOC_TIMEOUT, 30))
        except (subprocess.TimeoutExpired, OSError):
            code, version = 1, b''
        outdir = root / 'out'
        outdir.mkdir()
        try:
            returncode, _ = _run_bounded(
                [
                    executable,
                    *environment,
                    '--convert-to',
                    'docx',
                    '--outdir',
                    str(outdir),
                    str(path.resolve()),
                ],
                DOC_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise ByaxError(
                'adapter_timeout', f'Local DOC conversion exceeded {DOC_TIMEOUT} seconds and was stopped'
            ) from exc
        except OSError as exc:
            raise ByaxError('adapter_failed', 'Configured DOC converter could not be started') from exc
        outputs = [
            item
            for item in outdir.iterdir()
            if item.suffix.lower() == '.docx' and item.is_file() and not item.is_symlink()
        ]
        if returncode or len(outputs) != 1:
            raise ByaxError('adapter_failed', 'Local DOC conversion failed', returncode=returncode)
        doc, report = import_document(outputs[0])
        # The outer import attaches the original .doc; drop the intermediate .docx copy.
        doc['assets'].pop(doc['metadata']['import']['original_asset'], None)
        _loss(
            report['losses'],
            'doc_adapter',
            'DOC converted by explicitly configured local LibreOffice; cross-application fidelity is not verified.',
            'source',
        )
        report['adapter'] = {
            'name': 'libreoffice',
            'executable': executable,
            'version': version.decode('utf-8', 'replace').strip() if code == 0 else None,
        }
        return doc, report


def _plain(doc):
    parts = []
    for b in doc['blocks']:
        if b.get('type') == 'table':
            parts.extend('\t'.join(c.get('text', '') for c in r.get('cells', [])) for r in b.get('rows', []))
        elif b.get('type') == 'image':
            parts.append('[Image: ' + b.get('attrs', {}).get('alt', '') + ']')
        else:
            parts.append(b.get('text', ''))
    return '\n'.join(parts)


def _html_inline(block):
    text = block.get('text', '')
    marks = block.get('marks', [])
    segments = sorted(
        {0, len(text), *[max(0, min(len(text), int(m.get(k, 0)))) for m in marks for k in ('start', 'end')]}
    )
    result = []
    for start, end in itertools.pairwise(segments):
        value = html.escape(text[start:end]).replace('\n', '<br>')
        for mark in marks:
            if mark.get('start', 0) <= start and mark.get('end', 0) >= end:
                kind, setting = mark.get('type'), mark.get('value', True)
                tags = {
                    'bold': 'strong',
                    'italic': 'em',
                    'underline': 'u',
                    'strikethrough': 's',
                    'code': 'code',
                    'superscript': 'sup',
                    'subscript': 'sub',
                }
                if kind in tags and setting:
                    value = f'<{tags[kind]}>{value}</{tags[kind]}>'
                elif kind == 'link' and _safe_url(str(setting)):
                    value = f'<a href="{html.escape(str(setting), quote=True)}">{value}</a>'
        result.append(value)
    return ''.join(result)


def _html(doc):
    result = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>'
        + html.escape(doc.get('metadata', {}).get('title', ''))
        + '</title></head><body>'
    ]
    for block in doc['blocks']:
        kind, attrs = block.get('type'), block.get('attrs', {})
        if kind == 'table':
            result.append('<table>')
            for index, row in enumerate(block.get('rows', [])):
                tag = 'th' if index < attrs.get('header_rows', 0) else 'td'
                result.append(
                    '<tr>'
                    + ''.join(f'<{tag}>' + _html_inline(cell) + f'</{tag}>' for cell in row.get('cells', []))
                    + '</tr>'
                )
            result.append('</table>')
        elif kind == 'image':
            asset = doc.get('assets', {}).get(attrs.get('asset'), {})
            mime = asset.get('mime', '')
            if mime in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
                result.append(
                    f'<img src="data:{mime};base64,{asset.get("data", "")}" alt="{html.escape(attrs.get("alt", ""), quote=True)}">'
                )
            else:
                result.append('<p>' + html.escape(attrs.get('alt', '[unavailable image]')) + '</p>')
        else:
            tag = (
                'h' + str(max(1, min(6, attrs.get('level', 1))))
                if kind == 'heading'
                else 'pre'
                if block.get('style') == 'code'
                else 'blockquote'
                if block.get('style') == 'quotation'
                else 'p'
            )
            content = _html_inline(block)
            if attrs.get('list'):
                lt = 'ol' if _list_kind(attrs) in ('ordered', 'numbered') else 'ul'
                result.append(f'<{lt}><li>{content}</li></{lt}>')
            else:
                result.append(f'<{tag}>{content}</{tag}>')
    return '\n'.join(result) + '</body></html>'


def _md_inline(block):
    text = block.get('text', '')

    def escape(value):
        return re.sub(r'([\\`*_{}\[\]<>])', r'\\\1', value)

    marks = block.get('marks', [])
    if not marks:
        return escape(text)
    # Segmenting marks keeps content intact even with overlapping rich ranges.
    boundaries = sorted(
        {0, len(text), *[max(0, min(len(text), int(m.get(k, 0)))) for m in marks for k in ('start', 'end')]}
    )
    output = []
    for start, end in itertools.pairwise(boundaries):
        value = escape(text[start:end])
        for mark in marks:
            if mark.get('start', 0) <= start and mark.get('end', 0) >= end:
                kind = mark.get('type')
                if kind in ('bold', 'italic', 'code') and mark.get('value', True):
                    delimiter = {'bold': '**', 'italic': '*', 'code': '`'}[kind]
                    value = delimiter + value + delimiter
                elif kind == 'link' and _safe_url(str(mark.get('value', ''))):
                    value = '[' + value + '](' + str(mark['value']).replace(')', '%29') + ')'
        output.append(value)
    return ''.join(output)


def _md_block(block):
    attrs, kind = block.get('attrs', {}), block.get('type')
    if kind == 'table':
        rows = [
            '| '
            + ' | '.join(c.get('text', '').replace('|', '\\|').replace('\n', ' ') for c in r.get('cells', []))
            + ' |'
            for r in block.get('rows', [])
        ]
        if rows:
            columns = len(block['rows'][0]['cells'])
            rows.insert(1, '| ' + ' | '.join(['---'] * columns) + ' |')
        return '\n'.join(rows)
    if kind == 'image':
        return '![' + attrs.get('alt', '').replace(']', '\\]') + '](' + attrs.get('source', '') + ')'
    if attrs.get('thematic_break'):
        return '---'
    if block.get('style') == 'code':
        fence = '`' * max(3, max((len(v) for v in re.findall(r'`+', block.get('text', ''))), default=0) + 1)
        return fence + attrs.get('code_language', '') + '\n' + block.get('text', '') + '\n' + fence
    text = _md_inline(block)
    if kind == 'paragraph':
        text = _md_escape_lines(text)
    if kind == 'heading':
        text = '#' * max(1, min(6, attrs.get('level', 1))) + ' ' + text
    if block.get('style') == 'quotation':
        text = '\n'.join('> ' + line for line in text.split('\n'))
    if attrs.get('list'):
        text = (
            '  ' * (_list_level(attrs) - 1)
            + ('1. ' if _list_kind(attrs) in ('ordered', 'numbered') else '- ')
            + text
        )
    return text


def _md_structure(block):
    attrs = block.get('attrs', {})
    return [
        block.get('type'),
        block.get('style'),
        attrs.get('level') if block.get('type') == 'heading' else None,
        _list_kind(attrs) if attrs.get('list') else None,
        _list_level(attrs) if attrs.get('list') else None,
        attrs.get('quote_depth'),
        bool(attrs.get('thematic_break')),
    ]


from .formats import MD_MARKS as _MD_MARKS
from .formats import MD_STYLES as _MD_STYLES


def markdown_unrepresentable(doc):
    """Located constructs that the declared Markdown dialect cannot carry."""
    found = []
    for block in doc['blocks']:
        kind, attrs = block.get('type'), block.get('attrs', {})
        marks = {m.get('type') for m in block.get('marks', [])} - _MD_MARKS
        marks |= {
            m.get('type')
            for row in block.get('rows', [])
            for cell in row.get('cells', [])
            for m in cell.get('marks', [])
        } - _MD_MARKS
        if marks:
            _loss(
                found,
                'inline_formatting',
                'Not representable in Markdown: ' + ', '.join(sorted(marks)),
                block['id'],
            )
        if kind in ('footnote', 'toc', 'section_break', 'page_break'):
            _loss(
                found,
                'structural_object',
                f'{kind} cannot be represented in the declared Markdown dialect.',
                block['id'],
            )
        if kind == 'image' and attrs.get('asset'):
            _loss(
                found, 'image_not_embedded', 'Embedded image bytes cannot be stored in Markdown.', block['id']
            )
        if any(
            key in attrs
            for key in ('alignment', 'space_before', 'space_after', 'indent', 'first_line_indent')
        ):
            _loss(
                found,
                'paragraph_format',
                'Paragraph alignment/spacing cannot be represented in Markdown.',
                block['id'],
            )
        if block.get('style') not in _MD_STYLES and kind != 'table':
            _loss(
                found,
                'named_styles',
                f'Named style {block.get("style")} cannot be represented in Markdown.',
                block['id'],
            )
        if any(
            cell.get('attrs', {}).get(key, 1) != 1
            for row in block.get('rows', [])
            for cell in row.get('cells', [])
            for key in ('rowspan', 'colspan')
        ):
            _loss(
                found,
                'table_subset',
                'Merged table cells cannot be represented in Markdown tables.',
                block['id'],
            )
    review = doc.get('review', {})
    if any(review.get(key) for key in ('comments', 'tracked_changes', 'proposals')):
        _loss(found, 'review_not_exported', 'Review state cannot be stored in Markdown.', 'review')
    return found


def _md_escape_lines(value):
    lines = value.split('\n')
    for index, line in enumerate(lines):
        if re.match(r'(#{1,6}|[-+*]|=+|-+)(\s|$)', line):
            lines[index] = '\\' + line
        elif re.match(r'\d{1,9}[.)](\s|$)', line):
            lines[index] = re.sub(r'^(\d+)([.)])', r'\1\\\2', line)
    return '\n'.join(lines)


def _md_localized_block(block, original, old_structure, ending):
    """Render one changed block inside its original source span, keeping container syntax."""
    new_structure = _md_structure(block)
    if new_structure != old_structure and (
        old_structure[3] or old_structure[5] or new_structure[3] or new_structure[5]
    ):
        return None
    first = original[0].rstrip('\r\n') if original else ''
    container = re.match(r'[ \t]*(?:>[ \t]?)*[ \t]*', first)[0]
    if old_structure[3]:
        marker = re.match(r'[ \t]*(?:>[ \t]?)*[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?', first)[0]
    else:
        marker = container
    continuation = marker[: len(container)] + ' ' * (len(marker) - len(container))
    plain = copy.deepcopy(block)
    plain['attrs'] = {
        k: v for k, v in plain.get('attrs', {}).items() if k not in ('list', 'level', 'quote_depth')
    }
    if block.get('type') == 'heading':
        plain['attrs']['level'] = block.get('attrs', {}).get('level', 1)
    if plain.get('style') == 'quotation':
        plain['style'] = 'body'
    body = [
        line[len(container) :] if line.startswith(container) else line.lstrip()
        for line in (x.rstrip('\r\n') for x in original)
    ]
    if (
        block.get('style') == 'code'
        and old_structure[1] == 'code'
        and body
        and re.match(r'(`{3,}|~{3,})', body[0].lstrip())
    ):
        fence = re.match(r'(`{3,}|~{3,})', body[0].lstrip())[1]
        closing = body[-1] if len(body) > 1 and body[-1].strip().startswith(fence) else fence
        if fence in block.get('text', ''):
            return None
        rendered = [body[0]] + block.get('text', '').split('\n') + [closing]
    elif (
        block.get('type') == 'heading'
        and new_structure == old_structure
        and len(body) == 2
        and re.fullmatch(r'\s*(=+|-+)\s*', body[1])
        and '\n' not in block.get('text', '')
    ):
        rendered = [_md_escape_lines(_md_inline(block)), body[1]]
    elif block.get('type') == 'table' and len(body) == len(block.get('rows', [])) + 1 and len(body) > 1:

        def cells(line):
            return [
                part.strip().replace('\\|', '|')
                for part in re.split(r'(?<!\\)\|', line.strip().removeprefix('|').removesuffix('|'))
            ]

        rendered = []
        for index, row in enumerate(block['rows']):
            original_line = body[0 if index == 0 else index + 1]
            values = [cell.get('text', '') for cell in row.get('cells', [])]
            inline = [
                _md_inline(cell).replace('|', '\\|').replace('\n', ' ') for cell in row.get('cells', [])
            ]
            unchanged = cells(original_line) in (values, [value.replace('\\|', '|') for value in inline])
            rendered.append(original_line if unchanged else '| ' + ' | '.join(inline) + ' |')
        delimiter = (
            body[1]
            if len(cells(body[1])) == len(block['rows'][0].get('cells', []))
            else '| ' + ' | '.join(['---'] * len(block['rows'][0].get('cells', []))) + ' |'
        )
        rendered.insert(1, delimiter)
    elif (
        block.get('type') == 'paragraph'
        and block.get('style') != 'code'
        and not block.get('attrs', {}).get('thematic_break')
    ):
        rendered = _md_escape_lines(_md_inline(block)).split('\n')
    else:
        rendered = _md_block(plain).split('\n')
    lines = [(marker if index == 0 else continuation) + line for index, line in enumerate(rendered)]
    text = ending.join(lines)
    if original and original[-1].endswith(('\n', '\r')):
        text += ending
    return text


def _markdown_localized(doc, provenance):
    source, ending = provenance['source'], provenance.get('line_ending', '\n')
    order, spans = provenance.get('block_order', []), provenance.get('spans', {})
    fingerprints, structure = provenance.get('fingerprints', {}), provenance.get('structure', {})
    blocks = doc['blocks']
    position = {identity: index for index, identity in enumerate(order)}
    surviving = [b['id'] for b in blocks if b['id'] in position]
    if [position[i] for i in surviving] != sorted(position[i] for i in surviving):
        return None
    lines = source.splitlines(keepends=True)
    edits, present = [], {b['id'] for b in blocks}
    for identity in order:
        if identity not in present:
            span = spans.get(identity)
            if not span:
                return None
            start, end = span
            if (start == 0 or not lines[start - 1].strip()) and end < len(lines) and not lines[end].strip():
                end += 1
            elif end >= len(lines) and start and not lines[start - 1].strip():
                start -= 1
            edits.append((start, end, '', identity, 'deleted'))
    previous, pending = None, []

    def flush(anchor_after, following):
        if not pending:
            return True
        rendered = []
        prior = previous
        for block in pending:
            tight = (
                prior is not None
                and prior.get('attrs', {}).get('list')
                and block.get('attrs', {}).get('list')
            )
            rendered.append(
                ('' if prior is None and anchor_after is None else ('' if tight else ending))
                + _md_block(block).replace('\n', ending)
                + ending
            )
            prior = block
        if anchor_after is not None:
            span = spans.get(anchor_after)
            if not span:
                return False
            at = span[1]
            if at and not lines[at - 1].endswith(('\n', '\r')):
                rendered[0] = ending + rendered[0]
        else:
            at = spans.get(following, [0])[0] if following else len(lines)
            rendered[-1] += '' if following is None else ending
        edits.append((at, at, ''.join(rendered), [b['id'] for b in pending], 'inserted'))
        pending.clear()
        return True

    for block in blocks:
        identity = block['id']
        if identity not in position:
            pending.append(block)
            continue
        if not flush(previous['id'] if previous else None, identity):
            return None
        if _fingerprint(block) != fingerprints.get(identity):
            span = spans.get(identity)
            if not span or identity not in structure:
                return None
            replacement = _md_localized_block(block, lines[span[0] : span[1]], structure[identity], ending)
            if replacement is None:
                return None
            edits.append((span[0], span[1], replacement, identity, 'modified'))
        previous = block
    if not flush(previous['id'] if previous else None, None):
        return None
    edits.sort(key=lambda edit: (edit[0], edit[0] != edit[1]))
    output, cursor = [], 0
    for start, end, replacement, _target, _kind in edits:
        if start < cursor:
            return None
        output.extend(lines[cursor:start])
        output.append(replacement)
        cursor = end
    output.extend(lines[cursor:])
    report = [
        {'target': target, 'change': kind, 'source_lines': [start, end]}
        for start, end, _r, target, kind in edits
    ]
    return ''.join(output), report


def _markdown(doc, losses, info=None):
    info = {} if info is None else info
    provenance = doc.get('metadata', {}).get('import', {})
    if (
        provenance.get('format') in ('md', 'markdown')
        and isinstance(provenance.get('source'), str)
        and provenance.get('spans')
    ):
        localized = _markdown_localized(doc, provenance)
        if localized is not None:
            text, edits = localized
            info.update(
                mode='localized' if edits else 'verbatim',
                edits=edits,
                line_ending=provenance.get('line_ending', '\n'),
            )
            return text
    elif (
        provenance.get('format') in ('md', 'markdown')
        and [b['id'] for b in doc['blocks']] == provenance.get('block_order')
        and all(_fingerprint(b) == provenance.get('fingerprints', {}).get(b['id']) for b in doc['blocks'])
    ):
        info.update(mode='verbatim', edits=[])
        return provenance['source']
    _loss(
        losses,
        'markdown_normalized',
        'Markdown regenerated using CommonMark plus tables; source whitespace/syntax may change.',
        'document',
    )
    info.update(mode='regenerated', edits=[])
    ending = provenance.get('line_ending', '\n') if provenance.get('format') in ('md', 'markdown') else '\n'
    return ('\n\n'.join(_md_block(b) for b in doc['blocks']) + '\n').replace('\n', ending)


_XML_INVALID = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]')


def _xml_safe(doc, losses):
    """Replace characters XML cannot carry one-for-one so mark offsets stay valid."""
    found = []

    def clean(value):
        if isinstance(value, str):
            if _XML_INVALID.search(value):
                found.append(True)
                return _XML_INVALID.sub('\ufffd', value)
            return value
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    cleaned = {
        **doc,
        **{key: clean(doc[key]) for key in ('blocks', 'metadata', 'layout', 'styles') if key in doc},
    }
    if found:
        _loss(
            losses,
            'xml_control_characters',
            'Control characters that DOCX cannot store were replaced with U+FFFD.',
            'document',
        )
    return cleaned


def _export_docx(doc, path):
    from docx import Document
    from docx.shared import Pt, RGBColor

    word = Document()
    paragraphs = {}
    from docx.enum.style import WD_STYLE_TYPE

    named = {}
    for name, values in doc.get('styles', {}).items():
        actual = 'Normal' if name == 'body' else name
        style = (
            word.styles[actual]
            if actual in word.styles
            else word.styles.add_style(
                actual, WD_STYLE_TYPE.CHARACTER if values.get('family') == 'text' else WD_STYLE_TYPE.PARAGRAPH
            )
        )
        named[name] = style
        for key in ('bold', 'italic', 'underline'):
            if key in values:
                setattr(style.font, key, bool(values[key]))
        if values.get('font_family'):
            style.font.name = values['font_family']
        if values.get('font_size'):
            style.font.size = Pt(values['font_size'])
        if values.get('color') and re.fullmatch(r'#[0-9a-fA-F]{6}', values['color']):
            style.font.color.rgb = RGBColor.from_string(values['color'][1:])
        if style.type == WD_STYLE_TYPE.PARAGRAPH:
            for key, target in (
                ('space_before', 'space_before'),
                ('space_after', 'space_after'),
                ('indent', 'left_indent'),
                ('first_line_indent', 'first_line_indent'),
            ):
                if key in values:
                    setattr(style.paragraph_format, target, Pt(values[key]))
    for name, values in doc.get('styles', {}).items():
        if values.get('based_on') in named:
            named[name].base_style = named[values['based_on']]
    section = word.sections[0]
    layout = doc.get('layout', {})
    section.page_width, section.page_height = Pt(layout.get('width', 612)), Pt(layout.get('height', 792))
    for key, value in layout.get('margins', {}).items():
        if key in ('top', 'right', 'bottom', 'left'):
            setattr(section, key + '_margin', Pt(value))
    for region in ('header', 'footer'):
        value = layout.get(region, '')
        getattr(section, region).paragraphs[0].text = (
            value if isinstance(value, str) else value.get('text', '')
        )
    word.core_properties.title = doc.get('metadata', {}).get('title', '')
    word.core_properties.author = doc.get('metadata', {}).get('author', '')
    for block in doc['blocks']:
        kind, attrs = block.get('type'), block.get('attrs', {})
        if kind == 'table':
            rows = block.get('rows', [])
            columns = max((len(row['cells']) for row in rows), default=0)
            if not rows or not columns:
                continue
            table = word.add_table(rows=len(rows), cols=columns)
            table.style = 'Table Grid'
            for r, row in enumerate(rows):
                for c, cell in enumerate(row['cells']):
                    table.cell(r, c).text = cell.get('text', '')
            if attrs.get('header_rows'):
                from docx.oxml import OxmlElement

                for row in table.rows[: int(attrs['header_rows'])]:
                    row._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
            continue
        if kind == 'image':
            asset = doc.get('assets', {}).get(attrs.get('asset'))
            if asset:
                word.add_picture(
                    io.BytesIO(base64.b64decode(asset['data'])), width=Pt(attrs.get('width', 288))
                )
            continue
        if kind in ('page_break', 'section_break'):
            word.add_page_break()
            continue
        paragraph = (
            word.add_heading(level=max(1, min(6, attrs.get('level', 1))))
            if kind == 'heading'
            else word.add_paragraph()
        )
        paragraphs[block['id']] = paragraph
        if block.get('style') in named and (kind != 'heading' or block['style'] != 'body'):
            paragraph.style = named[block['style']]
        if attrs.get('list'):
            paragraph.style = 'List Number' if _list_kind(attrs) in ('ordered', 'numbered') else 'List Bullet'
        if attrs.get('alignment'):
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            paragraph.alignment = {
                'left': WD_ALIGN_PARAGRAPH.LEFT,
                'right': WD_ALIGN_PARAGRAPH.RIGHT,
                'center': WD_ALIGN_PARAGRAPH.CENTER,
                'justify': WD_ALIGN_PARAGRAPH.JUSTIFY,
            }.get(attrs['alignment'])
        for name, prop in (
            ('space_before', 'space_before'),
            ('space_after', 'space_after'),
            ('indent', 'left_indent'),
            ('first_line_indent', 'first_line_indent'),
        ):
            if name in attrs:
                setattr(paragraph.paragraph_format, prop, Pt(attrs[name]))
        text, marks = block.get('text', ''), block.get('marks', [])
        boundaries = sorted(
            {
                0,
                len(text),
                *[max(0, min(len(text), int(m.get(k, 0)))) for m in marks for k in ('start', 'end')],
            }
        )
        for start, end in itertools.pairwise(boundaries):
            run = paragraph.add_run(text[start:end])
            for mark in marks:
                if mark.get('start', 0) <= start and mark.get('end', 0) >= end:
                    name, value = mark.get('type'), mark.get('value', True)
                    if name in ('bold', 'italic', 'underline'):
                        setattr(run, name, bool(value))
                    elif name in ('strikethrough', 'superscript', 'subscript'):
                        setattr(run.font, 'strike' if name == 'strikethrough' else name, bool(value))
                    elif name in ('font_family', 'font'):
                        run.font.name = str(value)
                    elif name in ('font_size', 'size'):
                        run.font.size = Pt(value)
                    elif name == 'color' and re.fullmatch(r'#[0-9a-fA-F]{6}', str(value)):
                        run.font.color.rgb = RGBColor.from_string(value[1:])
            link = next(
                (
                    m.get('value')
                    for m in marks
                    if m.get('type') == 'link' and m.get('start', 0) <= start and m.get('end', 0) >= end
                ),
                None,
            )
            if link and _safe_url(str(link)):
                from docx.opc.constants import RELATIONSHIP_TYPE
                from docx.oxml import OxmlElement
                from docx.oxml.ns import qn

                anchor = OxmlElement('w:hyperlink')
                if str(link).startswith('#'):
                    anchor.set(qn('w:anchor'), str(link)[1:])
                else:
                    anchor.set(
                        qn('r:id'),
                        paragraph.part.relate_to(str(link), RELATIONSHIP_TYPE.HYPERLINK, is_external=True),
                    )
                anchor.append(run._r)
                paragraph._p.append(anchor)
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    for index, (name, bookmark) in enumerate(sorted(doc.get('settings', {}).get('bookmarks', {}).items())):
        target = paragraphs.get(bookmark.get('target'))
        if target is None:
            continue
        start, end = OxmlElement('w:bookmarkStart'), OxmlElement('w:bookmarkEnd')
        start.set(qn('w:id'), str(index))
        start.set(qn('w:name'), str(name))
        end.set(qn('w:id'), str(index))
        properties = target._p.find(qn('w:pPr'))
        position = 1 if properties is not None else 0
        target._p.insert(position, start)
        target._p.insert(position + 1, end)
    for comment in doc.get('review', {}).get('comments', []):
        target = paragraphs.get(comment.get('target'))
        if target is None or comment.get('status') == 'resolved':
            continue
        runs = target.runs or [target.add_run('')]
        word.add_comment(
            runs, text=str(comment.get('text', '')), author=str(comment.get('author', '') or 'ByAuthorX')
        )
    word.save(path)


def _export_odt(doc, path):
    from odf.dc import Title
    from odf.draw import Frame, Image
    from odf.meta import InitialCreator
    from odf.opendocument import OpenDocumentText
    from odf.style import (
        Footer,
        Header,
        MasterPage,
        PageLayout,
        PageLayoutProperties,
        ParagraphProperties,
        Style,
        TextProperties,
    )
    from odf.table import CoveredTableCell, Table, TableCell, TableHeaderRows, TableRow
    from odf.text import (
        A,
        H,
        List,
        ListItem,
        ListLevelStyleBullet,
        ListLevelStyleNumber,
        ListStyle,
        Note,
        NoteBody,
        NoteCitation,
        P,
        Span,
    )

    output = OpenDocumentText()
    output.meta.addElement(Title(text=doc.get('metadata', {}).get('title', '')))
    output.meta.addElement(InitialCreator(text=doc.get('metadata', {}).get('author', '')))
    style_map, mark_styles = {}, {}

    def text_properties(values):
        properties = {}
        for name, odf_name in (
            ('font_family', 'fontfamily'),
            ('color', 'color'),
            ('highlight', 'backgroundcolor'),
        ):
            if name in values:
                properties[odf_name] = values[name]
        if values.get('font_size'):
            properties['fontsize'] = f'{values["font_size"]}pt'
        if 'bold' in values:
            properties['fontweight'] = 'bold' if values['bold'] else 'normal'
        if 'italic' in values:
            properties['fontstyle'] = 'italic' if values['italic'] else 'normal'
        if 'underline' in values:
            properties['textunderlinestyle'] = 'solid' if values['underline'] else 'none'
        if 'strikethrough' in values:
            properties['textlinethroughstyle'] = 'solid' if values['strikethrough'] else 'none'
        if values.get('superscript'):
            properties['textposition'] = 'super 58%'
        if values.get('subscript'):
            properties['textposition'] = 'sub 58%'
        return properties

    for name, values in doc.get('styles', {}).items():
        if not isinstance(values, dict):
            continue
        style = Style(name=name, family='text' if values.get('family') == 'text' else 'paragraph')
        properties = text_properties(values)
        if properties:
            style.addElement(TextProperties(**properties))
        paragraph = {}
        if values.get('alignment'):
            paragraph['textalign'] = values['alignment']
        for key, target in (
            ('space_before', 'margintop'),
            ('space_after', 'marginbottom'),
            ('indent', 'marginleft'),
            ('first_line_indent', 'textindent'),
        ):
            if key in values:
                paragraph[target] = f'{values[key]}pt'
        if paragraph and values.get('family') != 'text':
            style.addElement(ParagraphProperties(**paragraph))
        output.styles.addElement(style)
        style_map[name] = style
    for name, values in doc.get('styles', {}).items():
        if values.get('based_on') in style_map and name in style_map:
            style_map[name].setAttribute('parentstylename', style_map[values['based_on']])
    layout = doc.get('layout', {})
    page = PageLayout(name='ByaxPage')
    props = {'pagewidth': f'{layout.get("width", 612)}pt', 'pageheight': f'{layout.get("height", 792)}pt'}
    props.update(
        {
            'margin' + side: f'{layout.get("margins", {}).get(side, 72)}pt'
            for side in ('top', 'right', 'bottom', 'left')
        }
    )
    page.addElement(PageLayoutProperties(**props))
    output.automaticstyles.addElement(page)
    master = MasterPage(name='Standard', pagelayoutname=page)
    for name, cls in (('header', Header), ('footer', Footer)):
        value = layout.get(name)
        if value:
            region = cls()
            region.addElement(P(text=value if isinstance(value, str) else value.get('text', '')))
            master.addElement(region)
    output.masterstyles.addElement(master)
    for kind in ('bullet', 'ordered'):
        style = ListStyle(name='ByaxList' + kind)
        for level in range(1, 10):
            style.addElement(
                ListLevelStyleNumber(level=level, numformat='1', numsuffix='.')
                if kind == 'ordered'
                else ListLevelStyleBullet(level=level, bulletchar='•')
            )
        output.styles.addElement(style)

    def add_text(parent, block):
        text, marks = block.get('text', ''), block.get('marks', [])
        boundaries = sorted(
            {
                0,
                len(text),
                *[max(0, min(len(text), int(m.get(k, 0)))) for m in marks for k in ('start', 'end')],
            }
        )
        for start, end in itertools.pairwise(boundaries):
            values = {
                m['type']: m.get('value', True)
                for m in marks
                if m.get('start', 0) <= start and m.get('end', 0) >= end
            }
            props = text_properties(values)
            container = parent
            if props:
                key = json.dumps(props, sort_keys=True)
                if key not in mark_styles:
                    style = Style(name='ByaxInline' + str(len(mark_styles)), family='text')
                    style.addElement(TextProperties(**props))
                    output.automaticstyles.addElement(style)
                    mark_styles[key] = style
                container = Span(stylename=mark_styles[key])
                parent.addElement(container)
            if values.get('link') and _safe_url(str(values['link'])):
                anchor = A(href=str(values['link']))
                container.addElement(anchor)
                container = anchor
            from odf.teletype import addTextToElement

            addTextToElement(container, text[start:end])

    notes = {}
    for index, note in enumerate(b for b in doc['blocks'] if b.get('type') == 'footnote'):
        notes.setdefault(note.get('attrs', {}).get('reference'), []).append((index + 1, note))
    list_stack, last_list_kind = [], None
    for block in doc['blocks']:
        kind, attrs = block.get('type'), block.get('attrs', {})
        if kind == 'footnote' and attrs.get('reference'):
            continue
        if kind == 'table':
            table = Table(name=block['id'])
            covered = set()
            headers = TableHeaderRows() if attrs.get('header_rows') else None
            if headers is not None:
                table.addElement(headers)
            rows = block.get('rows', [])
            for r, row in enumerate(rows):
                tr = TableRow()
                for c, cell in enumerate(row.get('cells', [])):
                    if (r, c) in covered:
                        tr.addElement(CoveredTableCell())
                        continue
                    ca = cell.get('attrs', {})
                    # Spans never reach past the table, so covered stays bounded.
                    rs = max(1, min(int(ca.get('rowspan', 1)), len(rows) - r))
                    cs = max(1, min(int(ca.get('colspan', 1)), len(row.get('cells', [])) - c))
                    td = TableCell(numberrowsspanned=rs, numbercolumnsspanned=cs)
                    paragraph = P()
                    add_text(paragraph, cell)
                    td.addElement(paragraph)
                    tr.addElement(td)
                    covered.update(
                        (rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs) if (rr, cc) != (r, c)
                    )
                (headers if headers is not None and r < attrs['header_rows'] else table).addElement(tr)
            output.text.addElement(table)
            list_stack = []
            continue
        if kind == 'image':
            asset = doc.get('assets', {}).get(attrs.get('asset'))
            paragraph = P()
            if asset:
                href = output.addPictureFromString(
                    base64.b64decode(asset['data']), asset.get('mime', 'image/png')
                )
                frame = Frame(
                    anchortype='as-char',
                    width=f'{attrs.get("width", 288)}pt',
                    height=f'{attrs.get("height", 216)}pt',
                )
                frame.addElement(Image(href=href, type='simple', show='embed', actuate='onLoad'))
                paragraph.addElement(frame)
            else:
                paragraph.addText('[Image: ' + attrs.get('alt', '') + ']')
            output.text.addElement(paragraph)
            continue
        element = H(outlinelevel=attrs.get('level', 1)) if kind == 'heading' else P()
        if block.get('style') in style_map:
            element.setAttribute('stylename', style_map[block['style']])
        add_text(element, block)
        for number, note in notes.get(block.get('id'), []):
            note_element = Note(id=note['id'], noteclass='footnote')
            note_element.addElement(NoteCitation(text=str(number)))
            note_body = NoteBody()
            p = P()
            add_text(p, note)
            note_body.addElement(p)
            note_element.addElement(note_body)
            element.addElement(note_element)
        if attrs.get('list'):
            list_kind = 'ordered' if _list_kind(attrs) in ('ordered', 'numbered') else 'bullet'
            level = max(1, min(9, _list_level(attrs)))
            if last_list_kind != list_kind:
                list_stack = []
            while len(list_stack) > level:
                list_stack.pop()
            while len(list_stack) < level:
                listing = List(stylename='ByaxList' + list_kind)
                if list_stack:
                    item = ListItem()
                    list_stack[-1].addElement(item)
                    item.addElement(listing)
                else:
                    output.text.addElement(listing)
                list_stack.append(listing)
            item = ListItem()
            item.addElement(element)
            list_stack[-1].addElement(item)
            last_list_kind = list_kind
        else:
            output.text.addElement(element)
            list_stack = []
    output.save(str(path), addsuffix=False)


def _rtf_escape(text):
    result = []
    for char in text:
        if char in '\\{}':
            result.append('\\' + char)
        elif char == '\n':
            result.append('\\line ')
        elif char == '\t':
            result.append('\\tab ')
        elif 32 <= ord(char) < 127:
            result.append(char)
        else:
            raw = char.encode('utf-16-le')
            for index in range(0, len(raw), 2):
                code = int.from_bytes(raw[index : index + 2], 'little', signed=True)
                result.append(f'\\u{code}?')
    return ''.join(result)


def _rtf_runs(block):
    text, marks = block.get('text', ''), block.get('marks', [])
    boundaries = sorted(
        {0, len(text), *[max(0, min(len(text), int(m.get(k, 0)))) for m in marks for k in ('start', 'end')]}
    )
    output = []
    for start, end in itertools.pairwise(boundaries):
        controls = []
        for mark in marks:
            if mark.get('start', 0) <= start and mark.get('end', 0) >= end and mark.get('value', True):
                control = {
                    'bold': 'b',
                    'italic': 'i',
                    'underline': 'ul',
                    'strikethrough': 'strike',
                    'superscript': 'super',
                    'subscript': 'sub',
                }.get(mark.get('type'))
                if control:
                    controls.append('\\' + control + ' ')
                elif mark.get('type') == 'font_size' and isinstance(mark.get('value'), (int, float)):
                    controls.append(f'\\fs{round(mark["value"] * 2)} ')
        output.append('{' + ''.join(controls) + _rtf_escape(text[start:end]) + '}')
    return ''.join(output)


def _rtf(doc):
    styles = {
        name: values
        for name, values in doc.get('styles', {}).items()
        if isinstance(values, dict) and values.get('family') != 'text'
    }
    numbers = {
        name: index
        for index, name in enumerate(sorted(styles, key=lambda n: (n not in ('body', 'Normal'), n)))
    }
    sheet = []
    for name, number in numbers.items():
        values = styles[name]
        controls = ''.join(
            f'\\{word}'
            for key, word in (('bold', 'b'), ('italic', 'i'), ('underline', 'ul'))
            if values.get(key)
        )
        if isinstance(values.get('font_size'), (int, float)):
            controls += f'\\fs{round(values["font_size"] * 2)}'
        if values.get('based_on') in numbers:
            controls += f'\\sbasedon{numbers[values["based_on"]]}'
        sheet.append('{\\s' + str(number) + controls + ' ' + _rtf_escape(name) + ';}')
    layout = doc.get('layout', {})
    margins = layout.get('margins', {})
    geometry = (
        f'\\paperw{round(layout.get("width", 612) * 20)}\\paperh{round(layout.get("height", 792) * 20)}'
    )
    geometry += ''.join(
        f'\\marg{side[0]}{round(margins.get(side, 72) * 20)}' for side in ('left', 'right', 'top', 'bottom')
    )
    output = [
        '{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}{\\stylesheet'
        + ''.join(sheet)
        + '}\\uc1'
        + geometry
        + ' '
    ]
    for region in ('header', 'footer'):
        value = layout.get(region)
        value = value if isinstance(value, str) else (value or {}).get('text', '')
        if value:
            output.append('{\\' + region + '\\pard\\plain ' + _rtf_escape(value) + '\\par}\n')
    notes = {}
    for note in doc['blocks']:
        if note.get('type') == 'footnote' and note.get('attrs', {}).get('reference'):
            notes.setdefault(note['attrs']['reference'], []).append(note)
    for block in doc['blocks']:
        kind, attrs = block.get('type'), block.get('attrs', {})
        if kind == 'footnote' and attrs.get('reference'):
            continue
        if kind == 'table':
            for row in block.get('rows', []):
                cells = row.get('cells', [])
                output.append(
                    '\\trowd' + ''.join(f'\\cellx{(index + 1) * 2000}' for index in range(len(cells))) + '\n'
                )
                output.append(
                    ''.join('\\pard\\intbl ' + _rtf_runs(cell) + '\\cell ' for cell in cells) + '\\row\n'
                )
            output.append('\\pard\n')
            continue
        if kind == 'image':
            asset = doc.get('assets', {}).get(attrs.get('asset'), {})
            blip = {'image/png': 'pngblip', 'image/jpeg': 'jpegblip'}.get(asset.get('mime'))
            if blip and asset.get('data'):
                output.append('{\\pict\\' + blip + ' ' + base64.b64decode(asset['data']).hex() + '}\\par\n')
            else:
                output.append(_rtf_escape('[Image: ' + attrs.get('alt', '') + ']') + '\\par\n')
            continue
        if kind == 'page_break':
            output.append('\\page\n')
            continue
        style = block.get('style')
        if kind == 'heading' and style not in numbers:
            style = f'heading{attrs.get("level", 1)}'
        prefix = '\\pard\\plain'
        if style in numbers:
            prefix += f'\\s{numbers[style]}'
        if attrs.get('alignment') in ('left', 'right', 'center', 'justify'):
            prefix += {'left': '\\ql', 'right': '\\qr', 'center': '\\qc', 'justify': '\\qj'}[
                attrs['alignment']
            ]
        output.append(prefix + ' ' + _rtf_runs(block))
        for note in notes.get(block.get('id'), []):
            output.append('{\\super\\chftn}{\\footnote\\pard\\plain{\\super\\chftn} ' + _rtf_runs(note) + '}')
        output.append('\\par\n')
    return ''.join(output) + '}'


def _exchange_losses(doc, fmt, losses):
    if fmt == 'byax':
        return
    for category in ('review', 'references', 'fields', 'layers'):
        value = doc.get(category)
        if category == 'review':
            review = value or {}
            exported = {'comments'} if fmt == 'docx' else set()
            value = any(items for key, items in review.items() if key not in exported)
        if value and not (fmt == 'pdf' and category == 'layers'):
            _loss(
                losses,
                category + '_not_exported',
                f'{category.title()} state is not preserved by this {fmt.upper()} adapter.',
                category,
            )
    if fmt != 'pdf':
        if doc.get('styles') and fmt not in ('docx', 'odt', 'rtf'):
            _loss(
                losses,
                'named_styles',
                'Named style definitions and inheritance are not preserved; supported direct formatting is exported.',
                'styles',
            )
        if fmt == 'rtf':
            _loss(
                losses,
                'page_layout',
                'Page size, margins and one header/footer are exported; masters, sections and exact pagination are not.',
                'layout',
            )
        elif fmt not in ('docx', 'odt'):
            _loss(
                losses,
                'page_layout',
                'Page geometry, masters, repeated regions and exact pagination are not represented in this adapter.',
                'layout',
            )
    from .formats import FORMATS, canonical_format

    supported_marks = set(FORMATS[canonical_format(fmt)]['marks'])
    for block in doc['blocks']:
        kind, attrs = block.get('type'), block.get('attrs', {})
        if fmt != 'pdf':
            omitted_marks = {m.get('type') for m in block.get('marks', [])} - supported_marks
            if omitted_marks:
                _loss(
                    losses,
                    'inline_formatting',
                    'Unsupported inline marks: ' + ', '.join(sorted(omitted_marks)),
                    block['id'],
                )
        if (
            kind in ('footnote', 'toc', 'section_break')
            and fmt != 'pdf'
            and not (kind == 'footnote' and fmt in ('odt', 'rtf') and attrs.get('reference'))
        ):
            _loss(
                losses,
                'structural_object',
                f'{kind} has no active equivalent in this export adapter; only available text is retained.',
                block['id'],
            )
        if kind == 'page_break' and fmt not in ('pdf', 'docx', 'rtf'):
            _loss(losses, 'page_break', 'Explicit page break is not represented.', block['id'])
        if kind == 'table':
            if fmt == 'txt':
                _loss(
                    losses,
                    'table_flattened',
                    'Table exported as tab-separated text; editable grid and formatting are lost.',
                    block['id'],
                )
            elif fmt not in ('pdf',):
                _loss(
                    losses,
                    'table_subset',
                    'Table text/grid exported; widths, spans, cell formatting and pagination policies may be lost.',
                    block['id'],
                )
        mime = doc.get('assets', {}).get(attrs.get('asset'), {}).get('mime')
        if (
            kind == 'image'
            and fmt not in ('pdf', 'docx', 'odt', 'html', 'htm')
            and not (fmt == 'rtf' and mime in ('image/png', 'image/jpeg'))
        ):
            _loss(
                losses,
                'image_not_embedded',
                'Embedded image bytes are not included; source/alternative text is retained where representable.',
                block['id'],
            )
        if attrs.get('list') and fmt not in ('pdf', 'md', 'markdown'):
            _loss(
                losses,
                'list_subset',
                'List nesting, continuation and restart settings are not preserved by this adapter.',
                block['id'],
            )
        if fmt == 'txt' and kind == 'heading':
            _loss(
                losses, 'heading_flattened', 'Heading text retained without heading semantics.', block['id']
            )
        if attrs.get('alignment') and fmt not in ('pdf', 'docx', 'rtf'):
            _loss(losses, 'paragraph_format', 'Paragraph alignment is not represented.', block['id'])


EXPORT_FORMATS = ('byax', 'txt', 'md', 'markdown', 'html', 'htm', 'rtf', 'docx', 'odt', 'pdf')


def publish_file(stage, destination, *, overwrite=True, owner=None):
    """Atomically publish a fully written staged file; never expose a partial output."""
    from .storage import DocumentLock

    destination = Path(destination)
    with DocumentLock(destination, owner=owner):
        if overwrite:
            os.replace(stage, destination)
            return
        try:
            os.link(stage, destination)
        except FileExistsError as exc:
            raise ByaxError(
                'conflict', 'Output already exists; explicitly select overwrite', target=str(destination)
            ) from exc
        except OSError:
            # Filesystems without hard links: check under the lock, then replace.
            if destination.exists():
                raise ByaxError(
                    'conflict', 'Output already exists; explicitly select overwrite', target=str(destination)
                ) from None
            os.replace(stage, destination)
        else:
            os.unlink(stage)


def conversion_preview(doc, format, profile=None):
    """Located losses for a conversion without writing anything."""
    return export_document(doc, None, format=format, profile=profile, preview=True)


def export_document(doc, path, format=None, profile=None, *, owner=None, overwrite=True, preview=False):
    destination = Path(path) if path is not None else None
    fmt = (format or (destination.suffix.lstrip('.') if destination else '')).lower()
    settings = doc.get('profiles', {}).get(profile) if isinstance(profile, str) else profile or {}
    if settings is None:
        raise ByaxError('missing_target', 'Unknown export profile', profile=profile)
    if not fmt:
        fmt = settings.get('format', '')
    if fmt not in EXPORT_FORMATS:
        raise ByaxError('unsupported_format', f'Unsupported export format: {fmt}')
    if settings.get('clean') and not preview:
        from .publication import export_clean

        return export_clean(
            doc,
            destination,
            format=fmt,
            profile={k: v for k, v in settings.items() if k != 'clean'},
            owner=owner,
            overwrite=overwrite,
        )
    if settings.get('pages') and fmt != 'pdf':
        raise ByaxError(
            'unsupported_feature',
            'Page selection is supported for controlled PDF output; editable exports retain whole semantic content',
        )
    provenance = doc.get('metadata', {}).get('import', {})
    original = provenance.get('original_path')
    if destination is not None and original and destination.resolve() == Path(original).resolve():
        raise ByaxError(
            'conflict', 'Conversion must preserve the imported original; choose a separate output path'
        )
    from .formats import output_document

    candidate, policy_losses = output_document(doc, fmt, include_hidden=settings.get('include_hidden', False))
    if settings.get('layout'):
        candidate['layout'].update(settings['layout'])
    if settings.get('metadata') == 'omit' or settings.get('include_metadata') is False:
        candidate['metadata'] = {'title': '', 'author': '', 'language': 'en'}
    losses = list(policy_losses)
    _exchange_losses(candidate, fmt, losses)
    markdown = None
    if fmt in ('md', 'markdown'):
        markdown = {
            'dialect': MARKDOWN_DIALECT['name'],
            'unrepresentable': markdown_unrepresentable(candidate),
        }
        if (
            provenance.get('format') in ('md', 'markdown')
            and markdown['unrepresentable']
            and not settings.get('allow_markdown_losses')
            and not preview
        ):
            raise ByaxError(
                'review_required',
                'Formatting cannot be represented in the declared Markdown dialect; convert to .byax '
                'or explicitly allow Markdown losses',
                losses=markdown['unrepresentable'],
                dialect=MARKDOWN_DIALECT['name'],
            )
    # Use the same structural projection as file views. Unsupported structural
    # content remains in the master, never as stray text in an exchange file.
    from .formats import FORMATS, canonical_format

    capabilities = FORMATS[canonical_format(fmt)]
    candidate['blocks'] = [
        b
        for b in candidate['blocks']
        if capabilities['blocks'].get(b['type'], 'full') != 'hidden'
        and not (
            b['type'] == 'footnote' and fmt in ('odt', 'rtf') and not b.get('attrs', {}).get('reference')
        )
    ]
    markdown_content = _markdown(candidate, losses, markdown) if markdown is not None else None
    if preview:
        report = _report(fmt, losses)
        report.update(preview=True, review='required' if losses else 'not_required')
        if markdown:
            report['markdown'] = markdown
        return report
    checks = None
    required = settings.get('required_suites', [])
    if required:
        from .checks import run_checks

        checks = run_checks(candidate, required)
        if checks.get('validation') != 'passed':
            raise ByaxError(
                'failed_requirement' if checks.get('validation') == 'failed' else 'incomplete_coverage',
                'Selected export did not satisfy required checks',
                report=checks,
            )
    if fmt == 'pdf':
        from .layout import build_document, resolve_layout

        shaped = build_document(candidate)
        resolve_layout(shaped, candidate)
        findings = shaped.byax_findings
        if findings and any(suite in required for suite in ('print', 'publication')):
            raise ByaxError(
                'incomplete_coverage',
                'Required print coverage is incomplete for the selected renderer',
                findings=findings,
            )
    if destination is None:
        raise ByaxError('invalid_input', 'An output path is required unless previewing')
    if not overwrite and destination.exists():
        raise ByaxError(
            'conflict', 'Output already exists; explicitly select overwrite', target=str(destination)
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.byax-export-', dir=destination.parent) as temporary:
        stage = Path(temporary) / ('export.' + fmt)
        result = None
        if fmt == 'byax':
            from .storage import save_document

            save_document(candidate, stage)
        elif fmt == 'pdf':
            from .layout import export_pdf

            result = export_pdf(candidate, stage, pages=settings.get('pages'))
            losses.extend(result.get('losses', []))
        elif fmt == 'docx':
            _export_docx(_xml_safe(candidate, losses), stage)
        elif fmt == 'odt':
            _export_odt(candidate, stage)
        else:
            if fmt == 'txt':
                content = _plain(candidate)
                if (
                    provenance.get('format') == 'txt'
                    and [b['id'] for b in candidate['blocks']] == provenance.get('block_order')
                    and all(
                        _fingerprint(b) == provenance.get('fingerprints', {}).get(b['id'])
                        for b in candidate['blocks']
                    )
                ):
                    content = provenance['source']
                elif provenance.get('format') == 'txt' and not settings.get('line_ending'):
                    content = content.replace('\n', provenance.get('line_ending', '\n'))
            elif fmt in ('md', 'markdown'):
                content = markdown_content
            elif fmt in ('html', 'htm'):
                content = _html(candidate)
            else:
                content = _rtf(candidate)
            if fmt == 'txt' and settings.get('line_ending'):
                ending = {'lf': '\n', 'crlf': '\r\n'}.get(settings['line_ending'], settings['line_ending'])
                content = content.replace('\r\n', '\n').replace('\r', '\n').replace('\n', ending)
            encoding = settings.get('encoding', 'utf-8') if fmt == 'txt' else 'utf-8'
            stage.write_bytes(content.encode(encoding))
        publish_file(stage, destination, overwrite=overwrite, owner=owner)
    report = _report(fmt, losses, [str(destination.resolve())])
    report['review'] = 'required' if losses else 'not_required'
    if fmt == 'pdf':
        from .fonts import glyph_fallbacks

        # Controlled output records the fonts that actually shaped each glyph.
        report['font_dependencies'] = glyph_fallbacks(candidate)
    if markdown:
        report['markdown'] = markdown
    if result:
        report.update(
            {
                key: value
                for key, value in result.items()
                if key not in ('outputs', 'losses', 'findings', 'coverage')
            }
        )
    if checks:
        report['checks'] = checks
        report['validation'] = checks['validation']
    return report


def source_text(doc, format):
    """Exact UTF-8 text written by the selected adapter, without a user output file."""
    from .formats import FORMATS, canonical_format

    fmt = canonical_format(format)
    if not FORMATS[fmt]['source']:
        raise ByaxError('unsupported_feature', 'This format has no textual source view')
    with tempfile.TemporaryDirectory(prefix='byax-source-') as temporary:
        target = Path(temporary) / ('source.' + fmt)
        export_document(doc, target, format=fmt, profile={'allow_markdown_losses': True})
        return target.read_bytes().decode('utf-8')


def _rtf_group(source, opening):
    """Return one balanced RTF group so table regexes never scan the whole body."""
    start = source.find(opening)
    if start < 0:
        return ''
    depth, index = 0, start
    while index < len(source):
        char = source[index]
        if char == '\\':
            index += 2
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
        index += 1
    return source[start:]


def _rtf_group_end(source, start):
    """Return the end offset of the brace group starting at ``start``."""
    depth, position = 0, start
    while position < len(source):
        char = source[position]
        if char == '\\':
            position += 2
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return len(source)


def _rtf_stylesheet(source):
    match = re.search(r'\{\\stylesheet', source)
    styles = {}
    if not match:
        return styles
    body = source[match.start() + 1 : _rtf_group_end(source, match.start()) - 1]
    position = body.find('{')
    while position != -1:
        end = _rtf_group_end(body, position)
        entry = body[position + 1 : end - 1]
        controls = {
            word: int(value) if value else 1 for word, value in re.findall(r'\\([a-zA-Z]+)(-?\d+)? ?', entry)
        }
        name = re.sub(r'\{[^{}]*\}|\\[a-zA-Z]+-?\d* ?|\\\*', '', entry).strip().rstrip(';').strip()
        kind = 'cs' if 'cs' in controls else 's' if 's' in controls else None
        if kind and name:
            values = {'family': 'text' if kind == 'cs' else 'paragraph', 'rtf_name': name}
            for word, key in (
                ('b', 'bold'),
                ('i', 'italic'),
                ('ul', 'underline'),
                ('strike', 'strikethrough'),
            ):
                if word in controls:
                    values[key] = controls[word] != 0
            if 'fs' in controls:
                values['font_size'] = controls['fs'] / 2
            if 'sbasedon' in controls:
                values['_based_on'] = controls['sbasedon']
            styles[(kind, controls.get(kind, 0))] = (name, values)
        position = body.find('{', end)
    names = {number: name for (kind, number), (name, _values) in styles.items() if kind == 's'}
    for name, values in styles.values():
        based = values.pop('_based_on', None)
        if based in names and names[based] != name:
            values['based_on'] = names[based]
    return styles


def _import_rtf(doc, source, losses):
    """Parse the editable RTF text/style/table/note/picture subset without object execution."""
    if not source.lstrip().startswith('{\\rtf'):
        raise ByaxError('invalid_input', 'File is not an RTF document')
    fonts = {
        int(match.group(1)): match.group(2).strip()
        for match in re.finditer(
            r'\{\\f(\d+)(?:\\[a-z]+-?\d* ?)*([^;{}\\]*);', _rtf_group(source, '{\\fonttbl')
        )
    }
    colors = [None]
    color_match = re.search(r'\{\\colortbl\s*;(.*?)\}', source, re.DOTALL)
    if color_match:
        for value in color_match.group(1).split(';'):
            numbers = [re.search(r'\\' + name + r'(\d+)', value) for name in ('red', 'green', 'blue')]
            if all(numbers):
                colors.append('#' + ''.join(f'{min(255, int(n.group(1))):02x}' for n in numbers))
    sheet = _rtf_stylesheet(source)
    for name, values in sheet.values():
        doc['styles'][name] = {key: value for key, value in values.items() if key != 'rtf_name'}
    paragraph_styles = {number: name for (kind, number), (name, _v) in sheet.items() if kind == 's'}
    state = {'marks': {}, 'attrs': {}, 'uc': 1, 'skip': False, 'star': False, 'capture': None, 'style': None}
    tails = {}
    stack, block, cells, table = [], _block(), [], None
    fallback, row_active, position = 0, False, 0
    skipped = set()
    captures = []
    pending_notes = []
    destinations = {
        'fonttbl',
        'colortbl',
        'stylesheet',
        'info',
        'object',
        'objdata',
        'datastore',
        'xmlopen',
        'xmlattrname',
        'xmlattrvalue',
        'themedata',
        'colorschememapping',
        'generator',
        'filetbl',
        'listtable',
        'listoverridetable',
        'nonshppict',
        'atnid',
        'atnauthor',
        'atndate',
        'atnref',
        'rsidtbl',
        'latentstyles',
        'pgdsctbl',
        'revtbl',
    }
    captured = {
        'header': 'header',
        'headerl': 'header',
        'headerr': 'header',
        'headerf': 'header',
        'footer': 'footer',
        'footerl': 'footer',
        'footerr': 'footer',
        'footerf': 'footer',
        'footnote': 'footnote',
        'annotation': 'annotation',
        'fldinst': 'fldinst',
        'pict': 'pict',
    }
    field_instruction = None

    def append(value):
        nonlocal fallback
        if state['skip']:
            return
        if fallback:
            cut = min(fallback, len(value))
            value, fallback = value[cut:], fallback - cut
        if not value:
            return
        if state['capture'] is not None:
            captures[-1]['text'] += value
            return
        start = len(block['text'])
        block['text'] += value
        latest = tails.setdefault(block['id'], {})
        for name, setting in state['marks'].items():
            if setting is not None and setting is not False:
                # Only the newest mark of a kind can end where this run starts;
                # scanning every mark was quadratic for alternating runs.
                key = (name, repr(setting))
                existing = latest.get(key)
                if existing is not None and existing['end'] == start:
                    existing['end'] += len(value)
                else:
                    latest[key] = {'start': start, 'end': start + len(value), 'type': name, 'value': setting}
                    block['marks'].append(latest[key])

    def scalar(value):
        return value.encode('utf-16-le', errors='surrogatepass').decode('utf-16-le', errors='replace')

    def normalized():
        raw = block['text']
        output = copy.deepcopy(block)
        output['text'] = scalar(raw)
        if len(output['text']) != len(raw):
            # Map UTF-16 offsets to scalar offsets in one pass; a surrogate pair
            # becomes one scalar. Re-encoding each prefix per mark was quadratic.
            offsets, count, index = [0] * (len(raw) + 1), 0, 0
            while index < len(raw):
                offsets[index] = count
                pair = (
                    '\ud800' <= raw[index] <= '\udbff'
                    and index + 1 < len(raw)
                    and '\udc00' <= raw[index + 1] <= '\udfff'
                )
                if pair:
                    offsets[index + 1] = count + 1
                index += 2 if pair else 1
                count += 1
            offsets[len(raw)] = count
            for mark in output['marks']:
                mark['start'], mark['end'] = offsets[mark['start']], offsets[mark['end']]
            for field in output['attrs'].get('fields', []):
                field['start'], field['end'] = offsets[field['start']], offsets[field['end']]
        output['attrs'].update(state['attrs'])
        name = paragraph_styles.get(state['style'])
        if name:
            output['style'] = name
            heading = re.fullmatch(r'(?i)heading\s*(\d)', name)
            if heading and 1 <= int(heading[1]) <= 6:
                output['type'] = 'heading'
                output['attrs']['level'] = int(heading[1])
        return output

    def paragraph(force=False):
        nonlocal block, table
        if block['text'] or force:
            doc['blocks'].append(normalized())
            doc['blocks'].extend(pending_notes)
            pending_notes.clear()
            block = _block()
            table = None

    def cell():
        nonlocal block
        value = normalized()
        cells.append({'id': _id('cell'), 'text': value['text'], 'marks': value['marks'], 'attrs': {}})
        block = _block()

    def finish(capture, offset):
        nonlocal field_instruction
        kind, text = capture['kind'], scalar(capture['text'])
        if kind in ('header', 'footer'):
            existing = doc['layout'].get(kind, '')
            doc['layout'][kind] = '\n'.join(
                part for part in (existing if isinstance(existing, str) else '', text.strip()) if part
            )
            _loss(
                losses,
                'rtf_page_region',
                f'RTF {kind} text imported as the document {kind}; odd/even/first-page variants are merged.',
                f'offset:{offset}',
            )
        elif kind == 'footnote':
            pending_notes.append(_block(text.strip(), 'footnote', reference=block['id']))
        elif kind == 'annotation':
            doc['review']['comments'].append(
                {
                    'id': _id('comment'),
                    'target': block['id'],
                    'start': len(scalar(block['text'])),
                    'end': len(scalar(block['text'])),
                    'text': text.strip(),
                    'status': 'open',
                    'revision': doc['revision'],
                    'source': 'rtf',
                }
            )
        elif kind == 'fldinst':
            field_instruction = text.strip()
        elif kind == 'pict':
            data = re.sub(r'[^0-9a-fA-F]', '', capture['text'])
            mime = capture.get('mime')
            try:
                raw = bytes.fromhex(data[: len(data) // 2 * 2])
            except ValueError:
                raw = b''
            signatures = {'image/png': b'\x89PNG\r\n\x1a\n', 'image/jpeg': b'\xff\xd8\xff'}
            if mime in signatures and raw.startswith(signatures[mime]):
                paragraph()
                image = _block(kind='image', alt='', asset=_asset(doc, raw, mime, 'rtf-picture'))
                doc['blocks'].append(image)
                _loss(
                    losses,
                    'rtf_picture',
                    'Embedded RTF picture imported; original scaling, cropping and wrapping require review.',
                    image['id'],
                )
            else:
                _loss(
                    losses,
                    'rtf_pict',
                    'RTF picture format is retained in the original but not imported as an editable image.',
                    f'offset:{offset}',
                )

    token_count = 0
    control_pattern = re.compile(r'([a-zA-Z]+)(-?\d+)? ?')
    while position < len(source):
        token_count += 1
        if token_count > 500000:
            raise ByaxError('resource_limit', 'RTF exceeds 500,000 parser tokens')
        char = source[position]
        if char == '{':
            if len(stack) > 1000:
                raise ByaxError('resource_limit', 'RTF group nesting exceeds 1,000 levels')
            stack.append(copy.deepcopy(state))
            position += 1
            continue
        if char == '}':
            if stack:
                previous, state = state, stack.pop()
                if previous['capture'] is not None and previous['capture'] != state['capture'] and captures:
                    finish(captures.pop(), position)
                if previous.get('field_start') is not None and state.get('field_start') is None:
                    start, end = previous['field_start'], len(block['text'])
                    link = re.match(
                        r'HYPERLINK\s+(\\l\s+)?"([^"]+)"', previous.get('field_instruction') or ''
                    )
                    url = (('#' if link[1] else '') + link[2]) if link else None
                    if url and _safe_url(url) and end > start:
                        block['marks'].append({'start': start, 'end': end, 'type': 'link', 'value': url})
                    elif end >= start:
                        block['attrs'].setdefault('fields', []).append(
                            {
                                'instruction': previous.get('field_instruction') or '',
                                'start': start,
                                'end': end,
                            }
                        )
                        _loss(
                            losses,
                            'rtf_field',
                            'RTF field result imported as static text; the instruction is recorded but will not update.',
                            block['id'],
                        )
            position += 1
            continue
        if char != '\\':
            end = position
            while end < len(source) and source[end] not in '{}\\':
                end += 1
            append(source[position:end].replace('\r', '').replace('\n', ''))
            position = end
            continue
        position += 1
        if position >= len(source):
            break
        char = source[position]
        if char in '{}\\':
            append(char)
            position += 1
            continue
        if char == "'" and position + 2 < len(source):
            try:
                append(
                    bytes([int(source[position + 1 : position + 3], 16)]).decode('cp1252', errors='replace')
                )
            except ValueError:
                pass
            position += 3
            continue
        if not (char.isascii() and char.isalpha()):
            if char == '*':
                state['star'] = True
            elif char == '~':
                append('\u00a0')
            elif char == '_':
                append('\u2011')
            elif char == '-':
                append('\u00ad')
            elif char in '\r\n' and not state['skip']:
                if row_active or state['capture'] is not None:
                    append('\n')
                else:
                    paragraph(force=True)
            position += 1
            continue
        match = control_pattern.match(source, position)
        if match is None:
            raise ByaxError('invalid_input', 'Invalid RTF control word', target=f'offset:{position}')
        word, number = match.group(1), match.group(2)
        argument = int(number) if number is not None else 1
        position = match.end()
        if word == 'bin':
            if argument < 0 or argument > MAX_INPUT:
                raise ByaxError('resource_limit', 'RTF binary payload exceeds limit')
            position += argument
            continue
        if state['skip']:
            continue
        if word == 'shppict':
            state['star'] = False
            continue
        if word in captured and not (state['capture'] == 'pict' and word == 'pict'):
            state['star'] = False
            state['capture'] = captured[word]
            captures.append({'kind': captured[word], 'text': ''})
            continue
        if word == 'fldrslt':
            state['star'] = False
            state['field_start'], state['field_instruction'] = len(block['text']), field_instruction
            field_instruction = None
            continue
        if word in destinations or state['star']:
            state['skip'], state['star'] = True, False
            if (
                word
                not in {
                    'fonttbl',
                    'colortbl',
                    'info',
                    'generator',
                    'stylesheet',
                    'atnid',
                    'atnauthor',
                    'atndate',
                    'atnref',
                    'rsidtbl',
                    'latentstyles',
                    'themedata',
                    'colorschememapping',
                    'datastore',
                    'xmlopen',
                    'listtable',
                    'listoverridetable',
                    'nonshppict',
                }
                and word not in skipped
            ):
                skipped.add(word)
                _loss(
                    losses,
                    'rtf_' + word,
                    f'RTF {word} destination is retained in original but not imported as editable content.',
                    f'offset:{position}',
                )
            continue
        if state['capture'] == 'pict':
            if word in ('pngblip', 'jpegblip'):
                captures[-1]['mime'] = 'image/png' if word == 'pngblip' else 'image/jpeg'
            continue
        if word == 'uc':
            state['uc'] = max(0, min(16, argument))
        elif word == 'u':
            append(chr(argument % 65536))
            fallback = state['uc']
        elif word in ('b', 'i', 'ul', 'strike', 'super', 'sub'):
            state['marks'][
                {
                    'b': 'bold',
                    'i': 'italic',
                    'ul': 'underline',
                    'strike': 'strikethrough',
                    'super': 'superscript',
                    'sub': 'subscript',
                }[word]
            ] = argument != 0
        elif word == 'ulnone':
            state['marks']['underline'] = False
        elif word == 'nosupersub':
            state['marks'].update(superscript=False, subscript=False)
        elif word == 'plain':
            state['marks'] = {}
        elif word == 'fs':
            state['marks']['font_size'] = argument / 2
        elif word == 'f' and argument in fonts:
            state['marks']['font_family'] = fonts[argument]
        elif word in ('cf', 'highlight') and 0 <= argument < len(colors):
            state['marks']['color' if word == 'cf' else 'highlight'] = colors[argument]
        elif word in ('ql', 'qr', 'qc', 'qj'):
            state['attrs']['alignment'] = {'ql': 'left', 'qr': 'right', 'qc': 'center', 'qj': 'justify'}[word]
        elif word in ('li', 'fi', 'sb', 'sa'):
            state['attrs'][
                {'li': 'indent', 'fi': 'first_line_indent', 'sb': 'space_before', 'sa': 'space_after'}[word]
            ] = argument / 20
        elif word == 's':
            state['style'] = argument
        elif word in ('paperw', 'paperh') and 72 <= argument <= 288000:
            doc['layout']['width' if word == 'paperw' else 'height'] = argument / 20
        elif word in ('margl', 'margr', 'margt', 'margb') and 0 <= argument <= 144000:
            doc['layout']['margins'][{'l': 'left', 'r': 'right', 't': 'top', 'b': 'bottom'}[word[-1]]] = (
                argument / 20
            )
        elif word == 'pard':
            state['attrs'], state['style'] = {}, None
        elif word in ('par', 'sect'):
            if row_active or state['capture'] is not None:
                append('\n')
            else:
                paragraph(force=True)
        elif word == 'line':
            append('\n')
        elif word == 'tab':
            append('\t')
        elif word == 'page':
            paragraph()
            doc['blocks'].append(_block(kind='page_break'))
        elif word == 'trowd':
            if block['text']:
                paragraph()
            row_active, cells = True, []
        elif word == 'cell':
            cell()
        elif word == 'row':
            if block['text']:
                cell()
            if table is None:
                table = _block(kind='table', header_rows=0)
                table['rows'] = []
                doc['blocks'].append(table)
            table['rows'].append({'id': _id('row'), 'cells': cells})
            cells, row_active = [], False
        elif word in ('emdash', 'endash', 'bullet', 'lquote', 'rquote', 'ldblquote', 'rdblquote'):
            append(
                {
                    'emdash': '—',
                    'endash': '–',
                    'bullet': '•',
                    'lquote': '‘',
                    'rquote': '’',
                    'ldblquote': '“',
                    'rdblquote': '”',
                }[word]
            )
        elif word == 'chftn':
            continue
    paragraph()
    if pending_notes:
        doc['blocks'].extend(pending_notes)
