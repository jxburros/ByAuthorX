"""EPUB 3 export, EPUB import and a built-in structural validator.

Export produces a reflowable EPUB 3 package: XHTML content documents split at chapter
boundaries, a navigation document (plus an NCX for EPUB 2 reading systems) built from the
outline, a package document with metadata and accessibility metadata, CSS derived from the
named styles, images, EPUB 3 footnotes, and fonts only where their embedding permission allows.

The package is a pure function of the document and the options: no clock, no random values,
sorted entries and fixed ZIP metadata, so the same inputs give the same bytes. Anything the
format cannot carry is reported as a located loss by the caller (``exchange.export_document``).

Cover convention: ``metadata['cover']`` is the SHA-256 key of an image asset; the ``cover``
option overrides it; otherwise an image block with ``attrs['cover']`` set is used (and not
repeated in the text). There is no implicit "first image" cover.

``export_epub`` is the library entry point other pipelines (compile) use; ``validate`` is the
built-in structural check that runs everywhere. EPUBCheck remains the authoritative validator and
runs as an optional CI job (``scripts/epubcheck_fixtures.py``).
"""

from __future__ import annotations

import base64
import html
import io
import itertools
import math
import posixpath
import re
import struct
import uuid
import zipfile
from datetime import UTC, datetime
from urllib.parse import unquote

from .errors import ByaxError
from .exchange import (
    _XML_INVALID,
    MAX_INPUT,
    _asset,
    _block,
    _decode,
    _HTMLImport,
    _list_kind,
    _list_level,
    _loss,
    _parse_xml,
    _safe_url,
    _safe_zip,
)

MIMETYPE = b'application/epub+zip'
OPF_DIR = 'OEBPS'
OPF_PATH = OPF_DIR + '/content.opf'
XHTML = 'application/xhtml+xml'
NS_XHTML = '{http://www.w3.org/1999/xhtml}'
NS_OPF = '{http://www.idpf.org/2007/opf}'
NS_DC = '{http://purl.org/dc/elements/1.1/}'
NS_EPUB = '{http://www.idpf.org/2007/ops}'
NS_CONTAINER = '{urn:oasis:names:tc:opendocument:xmlns:container}'
NS_NCX = '{http://www.daisy.org/z3986/2005/ncx/}'
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
DEFAULT_MODIFIED = '1980-01-01T00:00:00Z'
IMAGE_TYPES = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
}
FONT_TYPES = {'font/ttf': 'ttf', 'font/otf': 'otf'}
SPAN_LIMIT = 1000
MAX_SPINE = 5000
MAX_IMPORT_IMAGES = 2000
HAZARDS = {
    'none',
    'unknown',
    'flashing',
    'noFlashingHazard',
    'motionSimulation',
    'noMotionSimulationHazard',
    'sound',
    'noSoundHazard',
}
FEATURES = {
    'alternativeText',
    'annotations',
    'ARIA',
    'audioDescription',
    'bookmarks',
    'braille',
    'captions',
    'describedMath',
    'displayTransformability',
    'highContrastAudio',
    'highContrastDisplay',
    'index',
    'largePrint',
    'longDescription',
    'none',
    'pageBreakMarkers',
    'pageNavigation',
    'printPageNumbers',
    'readingOrder',
    'rubyAnnotations',
    'signLanguage',
    'structuralNavigation',
    'synchronizedAudioText',
    'tableOfContents',
    'tactileGraphic',
    'tactileObject',
    'timingControl',
    'transcript',
    'unlocked',
}
ACCESS_MODES = {'textual', 'visual', 'auditory', 'tactile'}
OPTION_KEYS = {
    'split_at',
    'split_level',
    'css',
    'cover',
    'include',
    'language',
    'identifier',
    'modified',
    'title',
    'author',
    'embed_fonts',
    'ncx',
    'accessibility',
    'toc_title',
}
_LANGUAGE = re.compile(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*')
_COLOR = re.compile(r'#[0-9A-Fa-f]{3,8}|[A-Za-z]{3,20}')
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')


def _esc(value):
    return html.escape(_XML_INVALID.sub('�', str(value)), quote=True)


def _slug(value, fallback='x'):
    return re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-')[:40] or fallback


def _number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and abs(value) < 1e6 else None


def _utc(value):
    """ISO 8601 text to the ``CCYY-MM-DDThh:mm:ssZ`` form EPUB requires, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return parsed.strftime('%Y-%m-%dT%H:%M:%SZ')


def _language(value):
    value = str(value or '').strip().replace('_', '-')
    return value if _LANGUAGE.fullmatch(value) else None


def _sniff(data):
    """Image media type from magic bytes (the declared type must agree before bytes are packaged)."""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


# Options -----------------------------------------------------------------------------------------


def _options(options):
    if options is not None and not isinstance(options, dict):
        raise ByaxError('invalid_input', 'EPUB options must be an object')
    options = dict(options or {})
    unknown = sorted(set(options) - OPTION_KEYS)
    if unknown:
        raise ByaxError(
            'invalid_input', 'Unknown EPUB option', options=unknown, supported=sorted(OPTION_KEYS)
        )
    for key in ('title', 'author', 'identifier', 'css', 'cover', 'language', 'modified', 'toc_title'):
        if options.get(key) is not None and not isinstance(options[key], str):
            raise ByaxError('invalid_input', f'EPUB option {key} must be text')
    for key in ('embed_fonts', 'ncx'):
        if key in options and type(options[key]) is not bool:
            raise ByaxError('invalid_input', f'EPUB option {key} must be true or false')
    level = options.get('split_level', 1)
    if type(level) is not int or not 0 <= level <= 6:
        raise ByaxError('invalid_input', 'EPUB option split_level must be an integer from 0 to 6')
    options['split_level'] = level
    splits = []
    for entry in options.get('split_at') or []:
        if isinstance(entry, str):
            entry = {'id': entry}
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            raise ByaxError('invalid_input', 'EPUB split_at entries are block ids or {id, title} objects')
        splits.append({'id': entry['id'], 'title': str(entry.get('title') or '')})
    options['split_at'] = splits
    ranges = []
    for entry in options.get('include') or []:
        if isinstance(entry, dict):
            entry = [entry.get('from'), entry.get('to')]
        if (
            not isinstance(entry, (list, tuple))
            or len(entry) != 2
            or not all(isinstance(x, str) for x in entry)
        ):
            raise ByaxError('invalid_input', 'EPUB include ranges are [first_block_id, last_block_id] pairs')
        ranges.append((entry[0], entry[1]))
    options['include'] = ranges
    access = options.get('accessibility') or {}
    if not isinstance(access, dict):
        raise ByaxError('invalid_input', 'EPUB option accessibility must be an object')
    vocab = {'hazards': HAZARDS, 'features': FEATURES, 'modes': ACCESS_MODES, 'sufficient': ACCESS_MODES}
    for key, value in access.items():
        if key == 'summary':
            if not isinstance(value, str):
                raise ByaxError('invalid_input', 'EPUB accessibility summary must be text')
        elif key in vocab:
            if not isinstance(value, list) or not set(value) <= vocab[key]:
                raise ByaxError(
                    'invalid_input',
                    f'EPUB accessibility {key} must list values from the schema.org vocabulary',
                )
        else:
            raise ByaxError('invalid_input', 'Unknown EPUB accessibility option', option=key)
    options['accessibility'] = access
    return options


# Fonts -------------------------------------------------------------------------------------------


def _font_stack(family):
    name = re.sub(r'[^A-Za-z0-9 _.\-]', '', str(family or '')).strip()
    if not name:
        return None
    lowered = name.lower()
    generic = (
        'monospace'
        if any(word in lowered for word in ('mono', 'courier', 'code', 'consolas'))
        else 'sans-serif'
        if 'sans' in lowered or 'arial' in lowered or 'helvetica' in lowered
        else 'serif'
    )
    return f'"{name}", {generic}'


def _family_name(family):
    return re.sub(r'[^A-Za-z0-9 _.\-]', '', str(family or '')).strip()


# Package -----------------------------------------------------------------------------------------


class Package:
    """The files of one EPUB (path to bytes, in archive order) and a report of what was built."""

    def __init__(self, files, info):
        self.files, self.info = files, info


def _style_values(doc, name):
    from .formatting import style_values

    try:
        return style_values(doc, name)
    except ByaxError:
        values = doc.get('styles', {}).get(name, {})
        return {k: v for k, v in values.items() if k not in ('based_on', 'kind', 'name', 'description')}


class _Book:
    def __init__(self, doc, options, losses):
        self.doc, self.opts, self.losses = doc, options, losses
        self.metadata = doc.get('metadata', {})
        self.styles = doc.get('styles', {})
        body = _style_values(doc, 'body') if 'body' in self.styles else {}
        self.base = _number(body.get('font_size')) or 11.0
        self.direct, self.marks = {}, {}  # declaration text -> class name
        self.images = {}  # asset key -> (archive path, mime, bytes)
        self.faces = {}  # (family, bold, italic) -> embed record
        self.ids, self.used_ids = {}, set()
        self.note_numbers = {}
        self.invalid_xml = False
        self.alt_total = self.alt_missing = self.footnotes = 0
        self.language = self._document_language()
        self.class_for = self._style_classes()

    # --- metadata ------------------------------------------------------------------------------
    def _document_language(self):
        raw = self.opts.get('language') or self.metadata.get('language') or 'en'
        found = _language(raw)
        if found is None:
            _loss(
                self.losses,
                'metadata_language',
                f'Language tag {raw!r} is not a well-formed BCP 47 tag; "en" was written.',
                'metadata/language',
            )
            return 'en'
        return found

    # --- identifiers ---------------------------------------------------------------------------
    def xml_id(self, raw, prefix='b-'):
        base = re.sub(r'[^A-Za-z0-9_.-]', '-', str(raw))[:60]
        base = base if re.match(r'[A-Za-z_]', base) else prefix + base
        candidate, count = base, 1
        while candidate in self.used_ids:
            count += 1
            candidate = f'{base}-{count}'
        self.used_ids.add(candidate)
        return candidate

    def anchor(self, block_id):
        if block_id not in self.ids:
            self.ids[block_id] = self.xml_id(block_id)
        return self.ids[block_id]

    # --- CSS -----------------------------------------------------------------------------------
    def _style_classes(self):
        mapping, taken = {}, set()
        for name in sorted(self.styles):
            if name == 'body' or not isinstance(self.styles[name], dict):
                continue
            slug = 's-' + _slug(name, 'style')
            while slug in taken:
                slug += '-x'
            taken.add(slug)
            mapping[name] = slug
        return mapping

    def _em(self, points):
        number = _number(points)
        return None if number is None else f'{round(number / self.base, 3):g}em'

    def declarations(self, values, *, paragraph=True):
        out = []
        family = _font_stack(values.get('font_family') or values.get('font'))
        if family:
            out.append(f'font-family: {family}')
        size = _number(values.get('font_size') or values.get('size'))
        if size and 1 <= size <= 400:
            out.append(f'font-size: {self._em(size)}')
        if values.get('bold') is not None:
            out.append(f'font-weight: {"bold" if values["bold"] else "normal"}')
        if values.get('italic') is not None:
            out.append(f'font-style: {"italic" if values["italic"] else "normal"}')
        lines = []
        if values.get('underline'):
            lines.append('underline')
        if values.get('strikethrough') or values.get('strike'):
            lines.append('line-through')
        if lines:
            out.append('text-decoration: ' + ' '.join(lines))
        color = values.get('color')
        if isinstance(color, str) and _COLOR.fullmatch(color):
            out.append(f'color: {color}')
        highlight = values.get('highlight') or values.get('background')
        if isinstance(highlight, str) and _COLOR.fullmatch(highlight):
            out.append(f'background-color: {highlight}')
        if paragraph:
            if values.get('alignment') in ('left', 'right', 'center', 'justify'):
                out.append(f'text-align: {values["alignment"]}')
            for key, prop in (
                ('space_before', 'margin-top'),
                ('space_after', 'margin-bottom'),
                ('indent', 'margin-left'),
                ('left_margin', 'margin-left'),
                ('right_indent', 'margin-right'),
                ('first_line_indent', 'text-indent'),
            ):
                if key in values and (em := self._em(values[key])) and abs(_number(values[key]) or 0) < 2000:
                    out.append(f'{prop}: {em}')
            spacing = _number(values.get('line_spacing'))
            if spacing and 0.5 <= spacing <= 5:
                out.append(f'line-height: {spacing:g}')
        return out

    def direct_class(self, attrs):
        keys = ('alignment', 'space_before', 'space_after', 'indent', 'first_line_indent', 'line_spacing')
        declared = self.declarations({k: attrs[k] for k in keys if k in attrs})
        if not declared:
            return None
        text = '; '.join(declared)
        return self.direct.setdefault(text, f'd-{len(self.direct) + 1}')

    def mark_class(self, props):
        declared = self.declarations(props, paragraph=False)
        if not declared:
            return None
        text = '; '.join(declared)
        return self.marks.setdefault(text, f'm-{len(self.marks) + 1}')

    def stylesheet(self, fonts):
        lines = ['/* Generated from the document styles; deterministic. */']
        for record in fonts:
            lines.append(
                f'@font-face {{ font-family: "{record["family"]}"; '
                f'font-weight: {"bold" if record["bold"] else "normal"}; '
                f'font-style: {"italic" if record["italic"] else "normal"}; src: url("../{record["path"]}"); }}'
            )
        body = _style_values(self.doc, 'body') if 'body' in self.styles else {}
        lines.append('body { ' + '; '.join(self.declarations(body)) + '; line-height: 1.4; margin: 0 }')
        lines.append(
            'h1, h2, h3, h4, h5, h6 { break-after: avoid; page-break-after: avoid; line-height: 1.2 }\n'
            'p { margin: 0 0 0.8em 0 }\n'
            'p.blank { margin: 0 }\n'
            'blockquote { margin: 1em 2em }\n'
            'pre { white-space: pre-wrap; font-family: monospace }\n'
            'img { max-width: 100%; height: auto }\n'
            'div.image { text-align: center; margin: 1em 0; break-inside: avoid }\n'
            'div.cover { text-align: center; margin: 0 }\n'
            'table { border-collapse: collapse; margin: 1em 0 }\n'
            'th, td { border: 1px solid #888888; padding: 0.25em 0.5em; text-align: left; vertical-align: top }\n'
            'th { font-weight: bold }\n'
            'a.noteref { font-size: 0.75em; vertical-align: super; line-height: 0; text-decoration: none }\n'
            'aside.footnote { font-size: 0.9em; margin: 0.5em 0 }\n'
            'aside.footnote p { margin: 0 }'
        )
        for name in sorted(self.class_for):
            values = _style_values(self.doc, name)
            kind = self.styles[name].get('kind', 'paragraph')
            declared = self.declarations(values, paragraph=kind != 'character')
            if declared:
                lines.append(f'.{self.class_for[name]} {{ ' + '; '.join(declared) + ' }')
        for text, name in self.direct.items():
            lines.append(f'.{name} {{ {text} }}')
        for text, name in self.marks.items():
            lines.append(f'.{name} {{ {text} }}')
        if self.opts.get('css'):
            lines.append('/* Supplied CSS */')
            lines.append(_XML_INVALID.sub('', self.opts['css']))
        return '\n'.join(lines) + '\n'

    # --- inline --------------------------------------------------------------------------------
    def href(self, value, block_id, file_of):
        value = str(value)
        if value.startswith('#'):
            target = value[1:]
            if target in file_of:
                return f'{file_of[target]}#{self.anchor(target)}'
            _loss(
                self.losses,
                'link_target_missing',
                f'Internal link {value} points outside the exported content; the text was kept without a link.',
                block_id,
            )
            return None
        if value.startswith(('http://', 'https://', 'mailto:')) and _safe_url(value):
            return value
        _loss(
            self.losses,
            'link_dropped',
            'Only http, https, mailto and in-book links are written to EPUB; the text was kept without a link.',
            block_id,
        )
        return None

    def inline(self, obj, owner, file_of, notes=()):
        text = obj.get('text', '')
        marks = [
            m for m in obj.get('marks', []) if isinstance(m, dict) and m.get('start', 0) < m.get('end', 0)
        ]
        limit = len(text)

        def clamp(value):
            return max(0, min(limit, int(value)))

        note_at = {}
        for offset, number in notes:
            note_at.setdefault(clamp(offset), []).append(number)
        points = {0, limit, *note_at, *(clamp(m[k]) for m in marks for k in ('start', 'end'))}
        out, ordered = [], sorted(points)
        for index, start in enumerate(ordered):
            for number in note_at.get(start, []):
                out.append(
                    f'<a class="noteref" epub:type="noteref" role="doc-noteref" id="fnref-{number}" '
                    f'href="#fn-{number}">{number}</a>'
                )
            if index + 1 == len(ordered):
                break
            end = ordered[index + 1]
            active = [
                m
                for m in marks
                if clamp(m['start']) <= start and clamp(m['end']) >= end and m.get('value', True)
            ]
            out.append(self._segment(text[start:end], active, owner, file_of))
        return ''.join(out)

    def _segment(self, text, active, owner, file_of):
        value = _esc(text).replace('\n', '<br/>')
        if _XML_INVALID.search(text):
            self.invalid_xml = True
        flags, props, classes, link = set(), {}, [], None
        for mark in active:
            kind, setting = mark.get('type'), mark.get('value', True)
            if kind in ('bold', 'strong'):
                flags.add('strong')
            elif kind in ('italic', 'emphasis'):
                flags.add('em')
            elif kind == 'underline':
                flags.add('u')
            elif kind in ('strikethrough', 'strike'):
                flags.add('s')
            elif kind == 'code':
                flags.add('code')
            elif kind == 'superscript':
                flags.add('sup')
            elif kind == 'subscript':
                flags.add('sub')
            elif kind in ('link', 'href'):
                link = setting
            elif kind in ('color', 'highlight', 'background', 'font_family', 'font', 'font_size', 'size'):
                props[kind] = setting
            elif kind == 'character_style' and setting in self.class_for:
                classes.append(self.class_for[setting])
        if (cls := self.mark_class(props)) is not None:
            classes.append(cls)
        if classes:
            value = f'<span class="{" ".join(classes)}">{value}</span>'
        for flag in ('strong', 'em', 'u', 's', 'code'):
            if flag in flags:
                value = f'<{flag}>{value}</{flag}>'
        for flag in ('sup', 'sub'):
            if flag in flags:
                value = f'<{flag}>{value}</{flag}>'
                break
        if link is not None and (target := self.href(link, owner, file_of)) is not None:
            value = f'<a href="{_esc(target)}">{value}</a>'
        return value


def _included(doc, options):
    blocks = doc.get('blocks', [])
    ranges = options['include']
    if not ranges:
        return list(blocks)
    index = {b['id']: i for i, b in enumerate(blocks)}
    spans = []
    for first, last in ranges:
        for ident in (first, last):
            if ident not in index:
                raise ByaxError(
                    'missing_target', 'EPUB include range names a block that does not exist', target=ident
                )
        if index[first] > index[last]:
            raise ByaxError('invalid_input', 'EPUB include range ends before it starts', target=first)
        spans.append((index[first], index[last]))
    chosen = {i for lo, hi in spans for i in range(lo, hi + 1)}
    kept = {blocks[i]['id'] for i in chosen}
    return [
        b
        for i, b in enumerate(blocks)
        if i in chosen or (b.get('type') == 'footnote' and b.get('attrs', {}).get('reference') in kept)
    ]


def _text_of(obj):
    return re.sub(r'\s+', ' ', obj.get('text', '')).strip()


def _cover(book, blocks):
    """(asset key, block id or None) of the cover image, or None."""
    assets = book.doc.get('assets', {})
    for source, key in (
        ('option', book.opts.get('cover')),
        ('metadata', book.metadata.get('cover')),
    ):
        if key:
            if key in assets:
                return key, None
            _loss(
                book.losses,
                'cover_missing',
                f'The {source} cover names an asset that does not exist; the book has no cover.',
                'metadata/cover' if source == 'metadata' else 'options/cover',
            )
            return None
    for block in blocks:
        if (
            block.get('type') == 'image'
            and block.get('attrs', {}).get('cover')
            and block['attrs'].get('asset')
        ):
            return block.get('attrs', {}).get('asset'), block['id']
    return None


def _register_image(book, key, block_id):
    """Package an image asset once; return its archive path (relative to the OPF) or None."""
    if key in book.images:
        return book.images[key][0]
    asset = book.doc.get('assets', {}).get(key)
    if not asset:
        _loss(
            book.losses,
            'image_not_embedded',
            'The image has no embedded asset; only its alternative text was kept.',
            block_id,
        )
        return None
    mime = asset.get('mime')
    try:
        data = base64.b64decode(asset.get('data', ''), validate=False)
    except ValueError:
        data = b''
    if mime not in IMAGE_TYPES:
        _loss(
            book.losses,
            'image_not_embedded',
            f'{mime or "This image type"} is not an EPUB core image type; only its alternative text was kept.',
            block_id,
        )
        return None
    if not data or _sniff(data) != mime:
        _loss(
            book.losses,
            'image_not_embedded',
            'The image bytes do not match their declared type; only its alternative text was kept.',
            block_id,
        )
        return None
    path = f'images/img-{str(key)[:16]}.{IMAGE_TYPES[mime]}'
    book.images[key] = (path, mime, data)
    return path


def _resolve_face(book, family, bold, italic):
    """File bytes and permission info for one face, or (None, reason)."""
    from .fonts import font_bytes_info, font_file_info, resolve_font_file

    face = ('bold' if bold else '') + ('italic' if italic else '') or 'regular'
    embedded = book.doc.get('layout', {}).get('fonts', {}).get(family, {}).get(face)
    try:
        if isinstance(embedded, dict) and embedded.get('font_asset') in book.doc.get('assets', {}):
            raw = base64.b64decode(book.doc['assets'][embedded['font_asset']].get('data', ''))
            return raw, font_bytes_info(raw, 'document asset')
        path = resolve_font_file(family, bold, italic)
        if path is None:
            return None, 'not installed'
        info = font_file_info(path)
        return path.read_bytes(), info
    except (ByaxError, OSError, ValueError, struct.error):
        return None, 'unreadable'


def _used_faces(book, blocks):
    """(family, bold, italic) faces that the exported text and styles can use."""
    faces = set()
    body = _style_values(book.doc, 'body') if 'body' in book.styles else {}
    default = body.get('font_family') or body.get('font')

    def add(obj, values):
        family = values.get('font_family') or values.get('font') or default
        if not family and not any(m.get('type') in ('font_family', 'font') for m in obj.get('marks', [])):
            return  # no family is declared in the CSS, so the reader's own font applies
        text = obj.get('text', '')
        limit = len(text)
        marks = [m for m in obj.get('marks', []) if isinstance(m, dict) and m.get('value', True)]
        points = sorted({0, limit, *(max(0, min(limit, int(m[k]))) for m in marks for k in ('start', 'end'))})
        for start, end in itertools.pairwise(points):
            if not text[start:end].strip():
                continue
            active = [m for m in marks if m['start'] <= start and m['end'] >= end]
            bold = bool(values.get('bold')) or any(m.get('type') in ('bold', 'strong') for m in active)
            italic = bool(values.get('italic')) or any(
                m.get('type') in ('italic', 'emphasis') for m in active
            )
            current = next((m['value'] for m in active if m.get('type') in ('font_family', 'font')), family)
            if current:
                faces.add((str(current), bold, italic))

    for block in blocks:
        kind = block.get('type')
        if kind == 'image':
            continue
        style = block.get('style')
        values = _style_values(book.doc, style) if style in book.styles else body
        if kind == 'heading':
            values = {**values, 'bold': values.get('bold', True)}
        if kind == 'table':
            for row in block.get('rows', []):
                for cell in row.get('cells', []):
                    add(cell, body)
        else:
            add(block, values)
    return sorted(faces)


def _fonts(book, blocks):
    """Embed permitted faces; report the rest. Returns [{family, bold, italic, path, data, media}]."""
    chosen = []
    if not book.opts.get('embed_fonts', True):
        return chosen
    for family, bold, italic in _used_faces(book, blocks):
        name = _family_name(family)
        face = ('bold' if bold else '') + ('italic' if italic else '') or 'regular'
        target = f'fonts/{family}/{face}'
        data, info = _resolve_face(book, family, bold, italic)
        record = {'family': family, 'face': face}
        if data is None:
            _loss(
                book.losses,
                'font_unavailable',
                f'Font {family} ({face}) is {info} on this system and was not embedded; readers substitute their own.',
                target,
            )
            record['embedded'] = False
        elif data[:4] == b'ttcf' or data[:4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
            _loss(
                book.losses,
                'font_not_embedded',
                f'Font {family} is not a single TrueType or OpenType file.',
                target,
            )
            record['embedded'] = False
        elif not info['embeddable']:
            _loss(
                book.losses,
                'font_not_embedded',
                f'Font {family} ({face}) declares embedding permission {info["embedding"]!r} and was not embedded.',
                target,
            )
            record['embedded'] = False
        elif not name or (info.get('family') or '').casefold() != family.casefold():
            _loss(
                book.losses,
                'font_unavailable',
                f'The installed substitute for {family} ({face}) is a different family and was not embedded.',
                target,
            )
            record['embedded'] = False
        elif bold and 'bold' not in (info.get('style') or '').lower():
            _loss(
                book.losses,
                'font_unavailable',
                f'No bold face of {family} is installed; the bold face was not embedded.',
                target,
            )
            record['embedded'] = False
        elif italic and not any(w in (info.get('style') or '').lower() for w in ('italic', 'oblique')):
            _loss(
                book.losses,
                'font_unavailable',
                f'No italic face of {family} is installed; the italic face was not embedded.',
                target,
            )
            record['embedded'] = False
        else:
            media = 'font/otf' if data[:4] == b'OTTO' else 'font/ttf'
            chosen.append(
                {
                    'family': name,
                    'bold': bold,
                    'italic': italic,
                    'path': f'fonts/{_slug(name, "font")}-{face}-{info["sha256"][:8]}.{FONT_TYPES[media]}',
                    'data': data,
                    'media': media,
                    'sha256': info['sha256'],
                }
            )
            record.update(embedded=True, sha256=info['sha256'], embedding=info['embedding'])
        book.faces[(family, bold, italic)] = record
    # One file per distinct font program even when several faces resolve to it.
    unique = {}
    for entry in chosen:
        unique.setdefault(entry['path'], entry)
    return [unique[path] for path in sorted(unique)]


# Content -----------------------------------------------------------------------------------------


def _chapters(book, blocks):
    """Split the included blocks into content documents: [{title, blocks}]."""
    splits = {s['id']: s['title'] for s in book.opts['split_at']}
    ids = {b['id'] for b in book.doc.get('blocks', [])}
    for ident in splits:
        if ident not in ids:
            raise ByaxError(
                'missing_target', 'EPUB split point names a block that does not exist', target=ident
            )
    level = book.opts['split_level']
    chapters = []
    for block in blocks:
        attrs = block.get('attrs', {})
        heading = block.get('type') == 'heading' and _text_of(block)
        starts = block['id'] in splits or (
            level and heading and isinstance(attrs.get('level', 1), int) and attrs.get('level', 1) <= level
        )
        if starts and chapters and chapters[-1]['blocks']:
            chapters.append({'title': '', 'blocks': [], 'explicit': False})
        if not chapters:
            chapters.append({'title': '', 'blocks': [], 'explicit': False})
        chapter = chapters[-1]
        if not chapter['blocks']:
            chapter['title'] = splits.get(block['id']) or (heading and _text_of(block)) or ''
            chapter['explicit'] = bool(splits.get(block['id']))
        chapter['blocks'].append(block)
    return chapters or [{'title': '', 'blocks': [], 'explicit': False}]


def _render_chapter(book, chapter, file_of, notes, cover_block):
    """XHTML body fragments and outline entries (level, text, anchor) of one chapter."""
    out, outline, queue = [], [], []
    stack = []  # open lists: [kind]; each open list has one open <li>

    def close_lists(depth=0):
        while len(stack) > depth:
            out.append('</li></ul>' if stack.pop() == 'ul' else '</li></ol>')
        if not stack:
            flush_notes()

    def flush_notes():
        while queue:
            number, note = queue.pop(0)
            out.append(
                f'<aside class="footnote" epub:type="footnote" role="doc-footnote" id="fn-{number}">'
                f'<p>{book.inline(note, note["id"], file_of)}</p></aside>'
            )

    def classes(block, extra=()):
        names = list(extra)
        style = block.get('style')
        if style in book.class_for:
            names.append(book.class_for[style])
        direct = book.direct_class(block.get('attrs', {}))
        if direct:
            names.append(direct)
        return f' class="{" ".join(names)}"' if names else ''

    def lang_attr(block):
        tag = _language(block.get('attrs', {}).get('language'))
        return f' lang="{tag}" xml:lang="{tag}"' if tag and tag != book.language else ''

    for block in chapter['blocks']:
        kind, attrs = block.get('type'), block.get('attrs', {})
        if kind == 'footnote':
            if attrs.get('reference') in notes:
                continue  # emitted after the block that references it
            if attrs.get('reference'):
                _loss(
                    book.losses,
                    'footnote_reference_missing',
                    'The footnote refers to a block that is not in the exported content; its text was kept as a paragraph.',
                    block['id'],
                )
            close_lists()
            out.append(
                f'<p{classes(block, ["note"])}{lang_attr(block)}>{book.inline(block, block["id"], file_of)}</p>'
            )
            continue
        if kind in ('page_break', 'section_break', 'toc'):
            close_lists()
            continue
        if block['id'] == cover_block:
            continue
        number_list = [(n['offset'], n['number']) for n in notes.get(block['id'], [])]
        if kind == 'table':
            close_lists()
            out.append(_render_table(book, block, file_of))
            continue
        if kind == 'image':
            close_lists()
            out.append(_render_image(book, block))
            continue
        content = book.inline(block, block['id'], file_of, number_list)
        anchor = f' id="{book.anchor(block["id"])}"' if block['id'] in book.ids or kind == 'heading' else ''
        if kind == 'heading':
            level = attrs.get('level', 1)
            level = level if type(level) is int and 1 <= level <= 6 else 1
            if not _text_of(block):
                _loss(book.losses, 'empty_heading', 'An empty heading was left out of the EPUB.', block['id'])
                continue
            close_lists()
            out.append(f'<h{level}{anchor}{classes(block)}{lang_attr(block)}>{content}</h{level}>')
            outline.append((level, _text_of(block), book.anchor(block['id'])))
        elif attrs.get('list'):
            _list_item(
                book, block, stack, out, close_lists, content, classes(block), lang_attr(block), anchor
            )
        else:
            close_lists()
            style = block.get('style')
            if not content.strip():
                out.append('<p class="blank"><br/></p>')
            elif style == 'code':
                out.append(f'<pre{anchor}{classes(block)}{lang_attr(block)}>{content}</pre>')
            elif style == 'quotation':
                out.append(
                    f'<blockquote{anchor}{classes(block)}{lang_attr(block)}><p>{content}</p></blockquote>'
                )
            else:
                out.append(f'<p{anchor}{classes(block)}{lang_attr(block)}>{content}</p>')
        for note in notes.get(block['id'], []):
            queue.append((note['number'], note['block']))
        if not stack:
            flush_notes()
    close_lists()
    return out, outline


def _list_item(book, block, stack, out, close_lists, content, classes, lang, anchor):
    attrs = block.get('attrs', {})
    kind = 'ol' if _list_kind(attrs) in ('ordered', 'numbered', 'decimal') else 'ul'
    try:
        level = max(1, min(9, _list_level(attrs)))
    except (TypeError, ValueError):
        level = 1
    if len(stack) > level:
        close_lists(level)
    if len(stack) == level and stack[-1] != kind:
        close_lists(level - 1)
    if len(stack) == level:
        out.append('</li>')
    while len(stack) < level:
        start = ''
        if kind == 'ol' and len(stack) == level - 1 and attrs.get('restart') and attrs.get('start', 1) != 1:
            start = f' start="{int(attrs["start"])}"' if type(attrs['start']) is int else ''
        out.append(f'<{kind}{start}>')
        stack.append(kind)
        if len(stack) < level:
            out.append('<li>')
    out.append(f'<li{anchor}{classes}{lang}>{content}')


def _render_table(book, block, file_of):
    rows = block.get('rows', [])
    header = block.get('attrs', {}).get('header_rows', 0)
    header = header if type(header) is int and 0 <= header <= len(rows) else 0
    covered = set()
    sections = {'thead': [], 'tbody': []}
    for r, row in enumerate(rows):
        cells = row.get('cells', [])
        out = []
        limit = header if r < header else len(rows)
        for c, cell in enumerate(cells):
            if (r, c) in covered:
                continue
            attrs = cell.get('attrs', {})
            rs = max(1, min(_span(attrs.get('rowspan', 1)), limit - r))
            cs = max(1, min(_span(attrs.get('colspan', 1)), len(cells) - c))
            covered.update(
                (rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs) if (rr, cc) != (r, c)
            )
            span = (f' rowspan="{rs}"' if rs > 1 else '') + (f' colspan="{cs}"' if cs > 1 else '')
            tag = 'th scope="col"' if r < header else 'td'
            out.append(
                f'<{tag}{span}>{book.inline(cell, cell.get("id", block["id"]), file_of)}</{tag.split()[0]}>'
            )
        sections['thead' if r < header else 'tbody'].append('<tr>' + ''.join(out) + '</tr>')
    parts = ['<table>']
    for name in ('thead', 'tbody'):
        if sections[name]:
            parts.append(f'<{name}>' + ''.join(sections[name]) + f'</{name}>')
    parts.append('</table>')
    return ''.join(parts)


def _span(value):
    value = str(value).strip()
    return max(1, min(int(value), SPAN_LIMIT)) if value.isascii() and value.isdecimal() else 1


def _render_image(book, block):
    attrs = block.get('attrs', {})
    alt = str(attrs.get('alt', '') or '').strip()
    decorative = bool(attrs.get('decorative'))
    path = _register_image(book, attrs.get('asset'), block['id']) if attrs.get('asset') else None
    if attrs.get('asset') is None:
        _loss(
            book.losses,
            'image_not_embedded',
            'The image is an external dependency that is never fetched; only its alternative text was kept.',
            block['id'],
        )
    book.alt_total += 1 if path else 0
    if not alt and not decorative:
        book.alt_missing += 1 if path else 0
        _loss(
            book.losses,
            'image_alt_missing',
            'The image has no text alternative; screen readers cannot describe it. Add alt text or mark it decorative.',
            block['id'],
        )
    if path is None:
        return (
            f'<p class="image-missing">[Image: {_esc(alt)}]</p>'
            if alt
            else '<p class="image-missing">[Image]</p>'
        )
    role = ' role="presentation"' if decorative and not alt else ''
    return f'<div class="image"><img src="../{path}" alt="{_esc(alt)}"{role}/></div>'


# Package documents -------------------------------------------------------------------------------


def _xhtml(title, body, language, extra_body='', css='../styles/book.css'):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'lang="{language}" xml:lang="{language}">\n'
        f'<head><meta charset="utf-8"/><title>{_esc(title)}</title>'
        f'<link rel="stylesheet" type="text/css" href="{css}"/></head>\n'
        f'<body{extra_body}>\n{body}\n</body>\n</html>\n'
    )


def _tree(entries):
    """Nest (level, text, href) entries by heading level."""
    root = {'level': 0, 'children': []}
    stack = [root]
    for level, text, href in entries:
        node = {'level': level, 'text': text, 'href': href, 'children': []}
        while stack[-1]['level'] >= level:
            stack.pop()
        stack[-1]['children'].append(node)
        stack.append(node)
    return root['children']


def _nav_list(nodes, indent=1):
    pad = '  ' * indent
    items = []
    for node in nodes:
        inner = f'<a href="{_esc(node["href"])}">{_esc(node["text"])}</a>'
        if node['children']:
            inner += '\n' + _nav_list(node['children'], indent + 2) + '\n' + pad + '  '
        items.append(f'{pad}  <li>{inner}</li>')
    return f'{pad}<ol>\n' + '\n'.join(items) + f'\n{pad}</ol>'


def _ncx(identifier, title, nodes, depth):
    counter = itertools.count(1)

    def render(items, indent):
        out = []
        for node in items:
            number = next(counter)
            out.append(
                f'{indent}<navPoint id="np-{number}" playOrder="{number}">'
                f'<navLabel><text>{_esc(node["text"])}</text></navLabel>'
                f'<content src="{_esc(node["href"])}"/>'
                + ('\n' + render(node['children'], indent + '  ') + '\n' + indent if node['children'] else '')
                + '</navPoint>'
            )
        return '\n'.join(out)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="{_esc(identifier)}"/><meta name="dtb:depth" content="{depth}"/>'
        '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>\n'
        f'<docTitle><text>{_esc(title)}</text></docTitle>\n'
        '<navMap>\n' + render(nodes, '  ') + '\n</navMap>\n</ncx>\n'
    )


def _depth(nodes):
    return 1 + max((_depth(n['children']) for n in nodes), default=0) if nodes else 0


def _modified(doc, options):
    for source, value in (
        ('option', options.get('modified')),
        ('metadata', doc.get('metadata', {}).get('modified')),
    ):
        if (found := _utc(value)) is not None:
            return found, source
    stamps = [
        found
        for record in doc.get('history', {}).get('revisions', [])
        if isinstance(record, dict) and (found := _utc(record.get('timestamp'))) is not None
    ]
    return (max(stamps), 'history') if stamps else (DEFAULT_MODIFIED, 'default')


def _identifier(doc, options):
    explicit = options.get('identifier') or doc.get('metadata', {}).get('identifier')
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return 'urn:uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'urn:byax:document:' + str(doc.get('id', ''))))


def build_package(doc, options=None, losses=None):
    """Build the EPUB for a document that already passed the shared output policy; no I/O."""
    options = _options(options)
    losses = [] if losses is None else losses
    book = _Book(doc, options, losses)
    blocks = _included(doc, options)
    chapters = _chapters(book, blocks)
    cover = _cover(book, blocks)
    cover_block = cover[1] if cover else None

    included = {b['id'] for b in blocks}
    file_of = {}
    for number, chapter in enumerate(chapters, 1):
        chapter['file'] = f'chapter-{number:03d}.xhtml'
        for block in chapter['blocks']:
            file_of[block['id']] = chapter['file']
    # Footnotes: numbered in reading order, rendered after the block that references them.
    notes, number = {}, 0
    positions = {b['id']: i for i, b in enumerate(blocks)}
    for block in blocks:
        target = (
            blocks[positions[block['attrs']['reference']]]
            if block.get('type') == 'footnote' and block.get('attrs', {}).get('reference') in included
            else None
        )
        if (
            target is None
            or target.get('type') not in ('paragraph', 'heading')
            or (target['type'] == 'heading' and not _text_of(target))
        ):
            continue
        notes.setdefault(block['attrs']['reference'], []).append(block)
    ordered = {}
    for ident in sorted(notes, key=lambda i: positions[i]):
        entries = []
        for note in sorted(
            notes[ident], key=lambda n: (int(n['attrs'].get('offset', 0) or 0), positions[n['id']])
        ):
            number += 1
            entries.append(
                {'number': number, 'offset': int(note['attrs'].get('offset', 0) or 0), 'block': note}
            )
        ordered[ident] = entries
    book.footnotes = number
    for block in blocks:  # anchors for internal link targets
        for mark in block.get('marks', []):
            target = str(mark.get('value', ''))
            if mark.get('type') in ('link', 'href') and target.startswith('#') and target[1:] in file_of:
                book.anchor(target[1:])
    for chapter in chapters:
        body, outline = _render_chapter(book, chapter, file_of, ordered, cover_block)
        chapter['body'], chapter['outline'] = body, outline

    cover_path = None
    if cover and cover[0]:
        cover_path = _register_image(book, cover[0], cover_block or 'metadata/cover')
        if cover_path is None and cover_block is None:
            _loss(
                book.losses,
                'cover_missing',
                'The cover image cannot be packaged; the book has no cover.',
                'metadata/cover',
            )
    fonts = _fonts(book, blocks)

    title = (options.get('title') or book.metadata.get('title') or '').strip()
    if not title:
        _loss(
            book.losses,
            'metadata_missing',
            'The document has no title; "Untitled" was written to the EPUB metadata.',
            'metadata/title',
        )
        title = 'Untitled'
    author = (options.get('author') or book.metadata.get('author') or '').strip()
    identifier = _identifier(doc, options)
    modified, modified_source = _modified(doc, options)
    language = book.language

    # Navigation: the outline, or one entry per content document when there are no headings.
    entries = []
    for chapter in chapters:
        first = chapter['blocks'][0] if chapter['blocks'] else None
        opens_with_heading = bool(
            first
            and first.get('type') == 'heading'
            and chapter['outline']
            and chapter['outline'][0][2] == book.ids.get(first['id'])
        )
        if chapter['explicit'] and chapter['title'] and not opens_with_heading:
            entries.append((1, chapter['title'], f'text/{chapter["file"]}'))
        for level, text, anchor in chapter['outline']:
            entries.append((level, text, f'text/{chapter["file"]}#{anchor}'))
    if not entries:
        entries = [
            (1, chapter['title'] or (title if index == 0 else f'Part {index + 1}'), f'text/{chapter["file"]}')
            for index, chapter in enumerate(chapters)
        ]
    for chapter in chapters:
        chapter['title'] = chapter['title'] or (chapter['outline'][0][1] if chapter['outline'] else title)
    tree = _tree(entries)

    files = {}
    stylesheet = book.stylesheet(fonts)
    files['styles/book.css'] = stylesheet.encode()
    for chapter in chapters:
        body = (
            f'<section epub:type="chapter" role="doc-chapter">\n{chr(10).join(chapter["body"])}\n</section>'
        )
        if not chapter['blocks']:
            body = '<section epub:type="chapter"><p class="blank"><br/></p></section>'
        files[f'text/{chapter["file"]}'] = _xhtml(chapter['title'], body, language).encode()
    for _key, (path, _mime, data) in sorted(book.images.items(), key=lambda item: item[1][0]):
        files[path] = data
    for font in fonts:
        files[font['path']] = font['data']
    cover_page = None
    if cover_path:
        cover_page = 'text/cover.xhtml'
        files[cover_page] = _xhtml(
            title,
            f'<div class="cover"><img src="../{cover_path}" alt="{_esc("Cover of " + title)}"/></div>',
            language,
            ' epub:type="cover"',
        ).encode()
    toc_title = options.get('toc_title') or 'Contents'
    landmarks = []
    if cover_page:
        landmarks.append(('cover', cover_page, 'Cover'))
    landmarks.append(('bodymatter', f'text/{chapters[0]["file"]}', title))
    nav = (
        f'<nav epub:type="toc" id="toc" role="doc-toc">\n  <h1>{_esc(toc_title)}</h1>\n{_nav_list(tree)}\n</nav>\n'
        '<nav epub:type="landmarks" id="landmarks" hidden="hidden">\n  <h2>Landmarks</h2>\n  <ol>\n'
        + '\n'.join(
            f'    <li><a epub:type="{kind}" href="{_esc(href)}">{_esc(label)}</a></li>'
            for kind, href, label in landmarks
        )
        + '\n  </ol>\n</nav>'
    )
    files['nav.xhtml'] = _xhtml(toc_title, nav, language, css='styles/book.css').encode()
    if options.get('ncx', True):
        files['toc.ncx'] = _ncx(identifier, title, tree, _depth(tree)).encode()
    if book.invalid_xml:
        _loss(
            book.losses,
            'xml_control_characters',
            'Control characters that XML cannot store were replaced with U+FFFD.',
            'document',
        )

    access = _accessibility(book, options, any(c['outline'] for c in chapters))
    opf = _opf(
        book,
        title=title,
        author=author,
        identifier=identifier,
        modified=modified,
        language=language,
        chapters=chapters,
        cover_page=cover_page,
        cover_path=cover_path,
        fonts=fonts,
        access=access,
        ncx=options.get('ncx', True),
    )
    files['content.opf'] = opf.encode()
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        f'  <rootfiles><rootfile full-path="{OPF_PATH}" media-type="application/oebps-package+xml"/></rootfiles>\n'
        '</container>\n'
    )
    archive = {'mimetype': MIMETYPE, 'META-INF/container.xml': container.encode()}
    archive.update({f'{OPF_DIR}/{path}': data for path, data in files.items()})
    info = {
        'version': '3.0',
        'identifier': identifier,
        'language': language,
        'modified': modified,
        'modified_source': modified_source,
        'chapters': [
            {
                'file': f'{OPF_DIR}/text/{c["file"]}',
                'title': c['title'],
                'blocks': [b['id'] for b in c['blocks']],
            }
            for c in chapters
        ],
        'navigation': [{'level': lv, 'title': text, 'href': href} for lv, text, href in entries],
        'cover': cover_path and f'{OPF_DIR}/{cover_path}',
        'images': len(book.images),
        'footnotes': book.footnotes,
        'fonts': [
            {'family': family, 'bold': bold, 'italic': italic, **record}
            for (family, bold, italic), record in sorted(book.faces.items())
        ],
        'accessibility': access,
        'files': len(archive),
    }
    return Package(archive, info)


def _accessibility(book, options, has_headings):
    given = options['accessibility']
    visual = bool(book.images)
    modes = given.get('modes') or (['textual', 'visual'] if visual else ['textual'])
    alt_complete = book.alt_missing == 0
    sufficient = given.get('sufficient') or (
        ['textual'] if (alt_complete or not visual) else ['textual', 'visual']
    )
    features = {'readingOrder', 'displayTransformability', 'tableOfContents', 'unlocked'}
    if has_headings:
        features.add('structuralNavigation')
    if visual and alt_complete and book.alt_total:
        features.add('alternativeText')
    if book.footnotes:
        features.add('ARIA')
    animated = any(mime in ('image/gif', 'image/webp') for _p, mime, _d in book.images.values())
    hazards = given.get('hazards') or (['unknown'] if animated else ['none'])
    if given.get('features'):
        features = set(given['features'])
    parts = ['This publication is reflowable text with a navigation document built from its headings.']
    if book.alt_total and alt_complete:
        parts.append('Every image has a text alternative.')
    elif book.alt_missing:
        parts.append(f'{book.alt_missing} of {book.alt_total} images lack a text alternative.')
    if book.footnotes:
        parts.append('Footnotes are marked up as EPUB 3 notes.')
    return {
        'modes': list(modes),
        'sufficient': list(sufficient),
        'features': sorted(features),
        'hazards': list(hazards),
        'summary': given.get('summary') or ' '.join(parts),
    }


def _opf(
    book,
    *,
    title,
    author,
    identifier,
    modified,
    language,
    chapters,
    cover_page,
    cover_path,
    fonts,
    access,
    ncx,
):
    meta = [
        f'    <dc:identifier id="pub-id">{_esc(identifier)}</dc:identifier>',
        f'    <dc:title id="title">{_esc(title)}</dc:title>',
    ]
    if author:
        meta += [
            f'    <dc:creator id="creator">{_esc(author)}</dc:creator>',
            '    <meta refines="#creator" property="role" scheme="marc:relators">aut</meta>',
        ]
    meta.append(f'    <dc:language>{_esc(language)}</dc:language>')
    for key, tag in (('publisher', 'publisher'), ('description', 'description'), ('rights', 'rights')):
        value = str(book.metadata.get(key) or '').strip()
        if value:
            meta.append(f'    <dc:{tag}>{_esc(value)}</dc:{tag}>')
    subjects = book.metadata.get('subjects')
    for subject in subjects if isinstance(subjects, list) else []:
        if isinstance(subject, str) and subject.strip():
            meta.append(f'    <dc:subject>{_esc(subject.strip())}</dc:subject>')
    meta.append(f'    <meta property="dcterms:modified">{modified}</meta>')
    for mode in access['modes']:
        meta.append(f'    <meta property="schema:accessMode">{mode}</meta>')
    meta.append(f'    <meta property="schema:accessModeSufficient">{",".join(access["sufficient"])}</meta>')
    for feature in access['features']:
        meta.append(f'    <meta property="schema:accessibilityFeature">{feature}</meta>')
    for hazard in access['hazards']:
        meta.append(f'    <meta property="schema:accessibilityHazard">{hazard}</meta>')
    meta.append(f'    <meta property="schema:accessibilitySummary">{_esc(access["summary"])}</meta>')
    manifest = ['    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    if ncx:
        manifest.append('    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    manifest.append('    <item id="css" href="styles/book.css" media-type="text/css"/>')
    spine = []
    if cover_page:
        manifest.append(f'    <item id="cover-page" href="{cover_page}" media-type="application/xhtml+xml"/>')
        spine.append('    <itemref idref="cover-page"/>')
    for number, chapter in enumerate(chapters, 1):
        manifest.append(
            f'    <item id="chapter-{number:03d}" href="text/{chapter["file"]}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'    <itemref idref="chapter-{number:03d}"/>')
    for number, (path, mime, _data) in enumerate(sorted(book.images.values()), 1):
        flag = ' properties="cover-image"' if path == cover_path else ''
        manifest.append(f'    <item id="image-{number:03d}" href="{path}" media-type="{mime}"{flag}/>')
    for number, font in enumerate(fonts, 1):
        manifest.append(
            f'    <item id="font-{number:03d}" href="{font["path"]}" media-type="{font["media"]}"/>'
        )
    toc_attr = ' toc="ncx"' if ncx else ''
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id" '
        f'xml:lang="{language}">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n' + '\n'.join(meta) + '\n  </metadata>\n'
        '  <manifest>\n' + '\n'.join(manifest) + '\n  </manifest>\n'
        f'  <spine{toc_attr}>\n' + '\n'.join(spine) + '\n  </spine>\n</package>\n'
    )


def write_package(package, path):
    """Deterministic ZIP: ``mimetype`` first and stored, sorted entries, fixed timestamps and attributes."""
    names = ['mimetype'] + sorted(n for n in package.files if n != 'mimetype')
    with zipfile.ZipFile(path, 'w') as archive:
        for name in names:
            entry = zipfile.ZipInfo(name, ZIP_EPOCH)
            entry.create_system = 3
            entry.external_attr = 0o644 << 16
            entry.compress_type = zipfile.ZIP_STORED if name == 'mimetype' else zipfile.ZIP_DEFLATED
            archive.writestr(entry, package.files[name], compresslevel=9)


def export_epub(document, path, options=None, *, profile=None, overwrite=True, owner=None):
    """Export a document as EPUB 3 and return the located-loss report.

    ``options`` (all optional): ``split_at`` (block ids or ``{id, title}`` starting a content
    document), ``split_level`` (split before headings up to this level; default 1, 0 for one
    document), ``include`` (``[first_id, last_id]`` ranges of blocks to export), ``css`` (appended
    to the generated stylesheet), ``cover`` (asset key), ``language``, ``title``, ``author``,
    ``identifier``, ``modified`` (ISO 8601; default from metadata or history, never the clock),
    ``embed_fonts`` (default true), ``ncx`` (default true), ``toc_title`` and ``accessibility``
    (``summary``, ``modes``, ``sufficient``, ``features``, ``hazards``). The same keys can be given
    as the ``epub`` object of an export profile.
    """
    from .exchange import export_document

    settings = {**(profile or {}), 'epub': {**(profile or {}).get('epub', {}), **(options or {})}}
    return export_document(document, path, format='epub', profile=settings, overwrite=overwrite, owner=owner)


# Validation --------------------------------------------------------------------------------------


def _problem(report, severity, code, message, member=None):
    report['errors' if severity == 'error' else 'warnings'].append(
        {'code': code, 'message': message, 'member': member}
    )


def _local_ref(base, ref):
    """(archive path, fragment) of a relative reference, or None for external/empty ones."""
    if not ref or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', ref) or ref.startswith('//'):
        return None
    path, _, fragment = ref.partition('#')
    path = unquote(path)
    target = posixpath.normpath(posixpath.join(posixpath.dirname(base), path)) if path else base
    return target, fragment


def validate(path):
    """Structural check of an EPUB 3 file; returns ``{'valid', 'errors', 'warnings', 'checked'}``.

    Checks the container (mimetype first and stored, container.xml), the package document
    (required metadata, manifest/spine consistency, every manifest item present, media types),
    the navigation document, XHTML well-formedness, unique ids, in-book links and fragments,
    CSS resources, and image and font signatures. It is not a substitute for EPUBCheck.
    """
    report = {'valid': False, 'errors': [], 'warnings': [], 'checked': {}}
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(path)) if isinstance(path, (bytes, bytearray)) else path)
    except (OSError, zipfile.BadZipFile) as exc:
        _problem(report, 'error', 'not_zip', f'Not a ZIP archive: {exc}')
        return report
    with archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            _problem(report, 'error', 'duplicate_member', 'The archive contains duplicate member names.')
        if not infos or infos[0].filename != 'mimetype':
            _problem(
                report, 'error', 'mimetype_first', 'The first archive member must be "mimetype".', 'mimetype'
            )
        elif infos[0].compress_type != zipfile.ZIP_STORED or infos[0].extra:
            _problem(
                report,
                'error',
                'mimetype_stored',
                'The mimetype member must be stored without an extra field.',
                'mimetype',
            )
        if 'mimetype' in names and archive.read('mimetype') != MIMETYPE:
            _problem(
                report,
                'error',
                'mimetype_content',
                'The mimetype member must be exactly application/epub+zip.',
                'mimetype',
            )
        members = set(names)
        root = _read_xml(archive, 'META-INF/container.xml', report)
        opf_path = None
        if root is not None:
            rootfile = root.find(f'.//{NS_CONTAINER}rootfile')
            opf_path = rootfile.get('full-path') if rootfile is not None else None
            if not opf_path or opf_path not in members:
                _problem(
                    report,
                    'error',
                    'rootfile_missing',
                    'container.xml names no package document that exists.',
                    'META-INF/container.xml',
                )
                opf_path = None
        if opf_path:
            _validate_package(archive, members, opf_path, report)
    report['valid'] = not report['errors']
    return report


def _read_xml(archive, member, report):
    if member not in archive.namelist():
        _problem(report, 'error', 'member_missing', f'{member} is missing.', member)
        return None
    try:
        return _parse_xml(archive.read(member), member)
    except ByaxError as exc:
        _problem(report, 'error', 'xml_invalid', f'{exc.message}.', member)
        return None


def _validate_package(archive, members, opf_path, report):
    opf = _read_xml(archive, opf_path, report)
    if opf is None:
        return
    if opf.get('version') not in ('3.0', '3.1', '3.2', '3.3'):
        _problem(report, 'error', 'opf_version', 'The package version must be 3.x.', opf_path)
    metadata = opf.find(f'{NS_OPF}metadata')
    manifest = opf.find(f'{NS_OPF}manifest')
    spine = opf.find(f'{NS_OPF}spine')
    if metadata is None or manifest is None or spine is None:
        _problem(
            report, 'error', 'opf_structure', 'The package needs metadata, manifest and spine.', opf_path
        )
        return
    identifiers = {e.get('id'): (e.text or '').strip() for e in metadata.findall(f'{NS_DC}identifier')}
    unique = opf.get('unique-identifier')
    if not unique or not identifiers.get(unique):
        _problem(
            report, 'error', 'identifier', 'unique-identifier must name a non-empty dc:identifier.', opf_path
        )
    for tag in ('title', 'language'):
        values = [(e.text or '').strip() for e in metadata.findall(f'{NS_DC}{tag}')]
        if not any(values):
            _problem(
                report, 'error', f'metadata_{tag}', f'dc:{tag} is required and must not be empty.', opf_path
            )
    for e in metadata.findall(f'{NS_DC}language'):
        if _language((e.text or '').strip()) is None:
            _problem(
                report,
                'error',
                'metadata_language',
                'dc:language is not a well-formed language tag.',
                opf_path,
            )
    modified = [e for e in metadata.findall(f'{NS_OPF}meta') if e.get('property') == 'dcterms:modified']
    if len(modified) != 1 or not _DATE.fullmatch((modified[0].text or '').strip()):
        _problem(
            report,
            'error',
            'modified',
            'Exactly one dcterms:modified in CCYY-MM-DDThh:mm:ssZ form is required.',
            opf_path,
        )
    items, hrefs = {}, {}
    for item in manifest.findall(f'{NS_OPF}item'):
        ident, href, media = item.get('id'), item.get('href'), item.get('media-type')
        if not ident or ident in items:
            _problem(
                report, 'error', 'manifest_id', f'Manifest id {ident!r} is missing or duplicated.', opf_path
            )
            continue
        if not href or not media:
            _problem(
                report,
                'error',
                'manifest_item',
                f'Manifest item {ident} needs href and media-type.',
                opf_path,
            )
            continue
        target = _local_ref(opf_path, href)
        path = target[0] if target else None
        if path is None or target[1]:
            _problem(
                report, 'error', 'manifest_href', f'Manifest item {ident} has an unusable href.', opf_path
            )
            continue
        if path not in members:
            _problem(
                report,
                'error',
                'manifest_missing',
                f'Manifest item {ident} ({href}) is not in the archive.',
                opf_path,
            )
        if path in hrefs:
            _problem(
                report, 'error', 'manifest_duplicate', f'{href} is declared by two manifest items.', opf_path
            )
        hrefs[path] = ident
        items[ident] = {'path': path, 'media': media, 'properties': (item.get('properties') or '').split()}
    for name in sorted(members):
        if name == 'mimetype' or name.startswith('META-INF/') or name.endswith('/') or name == opf_path:
            continue
        if name not in hrefs:
            _problem(
                report,
                'warning',
                'not_in_manifest',
                f'{name} is in the archive but not in the manifest.',
                name,
            )
    refs = spine.findall(f'{NS_OPF}itemref')
    if not refs:
        _problem(report, 'error', 'spine_empty', 'The spine lists no content documents.', opf_path)
    for ref in refs:
        item = items.get(ref.get('idref'))
        if item is None:
            _problem(
                report,
                'error',
                'spine_idref',
                f'Spine item {ref.get("idref")!r} is not in the manifest.',
                opf_path,
            )
        elif item['media'] != XHTML:
            _problem(
                report,
                'error',
                'spine_type',
                f'Spine item {ref.get("idref")} is not an XHTML content document.',
                opf_path,
            )
    if spine.get('toc') and items.get(spine.get('toc'), {}).get('media') != 'application/x-dtbncx+xml':
        _problem(report, 'error', 'spine_toc', 'spine toc must name the NCX manifest item.', opf_path)
    navs = [i for i in items.values() if 'nav' in i['properties']]
    covers = [i for i in items.values() if 'cover-image' in i['properties']]
    if len(navs) != 1:
        _problem(
            report, 'error', 'nav_count', 'Exactly one manifest item must carry the nav property.', opf_path
        )
    if len(covers) > 1 or any(
        i['media'] not in IMAGE_TYPES and i['media'] != 'image/svg+xml' for i in covers
    ):
        _problem(report, 'error', 'cover_image', 'At most one image may carry cover-image.', opf_path)
    ids_by_doc, docs = {}, {}
    for item in items.values():
        path, media = item['path'], item['media']
        if path not in members:
            continue
        content = archive.read(path)
        if media == XHTML:
            root = _read_xml(archive, path, report)
            if root is not None:
                docs[path] = root
                ids_by_doc[path] = _collect_ids(root, path, report)
        elif media in IMAGE_TYPES and _sniff(content) != media:
            _problem(report, 'error', 'image_type', f'{path} is not a valid {media} image.', path)
        elif media in FONT_TYPES and content[:4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
            _problem(report, 'error', 'font_type', f'{path} is not a TrueType or OpenType font.', path)
        elif media == 'text/css':
            for ref in re.findall(r'url\(\s*["\']?([^)"\']+)["\']?\s*\)', content.decode('utf-8', 'replace')):
                if re.match(r'https?:|//', ref):
                    _problem(
                        report, 'error', 'remote_resource', f'{path} loads a remote resource: {ref}', path
                    )
                    continue
                found = _local_ref(path, ref)
                if found and found[0] not in hrefs:
                    _problem(
                        report,
                        'error',
                        'css_resource',
                        f'{path} refers to {ref}, which is not a manifest resource.',
                        path,
                    )
        elif media == 'application/x-dtbncx+xml':
            ncx = _read_xml(archive, path, report)
            if ncx is not None:
                for point in ncx.iter(f'{NS_NCX}content'):
                    found = _local_ref(path, point.get('src'))
                    if found is None or found[0] not in hrefs:
                        _problem(
                            report,
                            'error',
                            'ncx_link',
                            f'NCX link {point.get("src")} does not resolve.',
                            path,
                        )
    for path, root in docs.items():
        _validate_links(root, path, hrefs, ids_by_doc, report)
    if navs:
        spine_paths = {items[r.get('idref')]['path'] for r in refs if r.get('idref') in items}
        _validate_nav(docs.get(navs[0]['path']), navs[0]['path'], spine_paths, report)
    report['checked'] = {
        'package': opf_path,
        'manifest_items': len(items),
        'spine_items': len(refs),
        'xhtml_documents': len(docs),
    }


def _collect_ids(root, path, report):
    seen = set()
    for element in root.iter():
        ident = element.get('id')
        if ident is None:
            continue
        if ident in seen:
            _problem(report, 'error', 'duplicate_id', f'Duplicate id {ident!r}.', path)
        seen.add(ident)
    return seen


def _validate_links(root, path, hrefs, ids_by_doc, report):
    head_title = root.find(f'{NS_XHTML}head/{NS_XHTML}title')
    if head_title is None or not ''.join(head_title.itertext()).strip():
        _problem(report, 'warning', 'title_empty', f'{path} has no title.', path)
    for element in root.iter():
        local = element.tag.rsplit('}', 1)[-1]
        if local == 'script':
            _problem(report, 'error', 'script', f'{path} contains a script element.', path)
        if local == 'img' and element.get('alt') is None:
            _problem(report, 'error', 'img_alt', f'{path} has an img without an alt attribute.', path)
        for attribute in ('href', 'src'):
            value = element.get(attribute)
            if value is None or (local == 'a' and attribute == 'src'):
                continue
            if re.match(r'https?:|mailto:', value):
                if local in ('img', 'link', 'source', 'audio', 'video'):
                    _problem(
                        report, 'error', 'remote_resource', f'{path} loads a remote resource: {value}', path
                    )
                continue
            found = _local_ref(path, value)
            if found is None:
                if value.strip():
                    _problem(
                        report,
                        'error',
                        'bad_reference',
                        f'{path} has an unsupported reference {value!r}.',
                        path,
                    )
                continue
            target, fragment = found
            if target not in hrefs:
                _problem(
                    report,
                    'error',
                    'dangling_reference',
                    f'{path} refers to {value}, which is not a manifest resource.',
                    path,
                )
            elif fragment and target in ids_by_doc and fragment not in ids_by_doc[target]:
                _problem(
                    report,
                    'error',
                    'dangling_fragment',
                    f'{path} refers to {value}, but the target id does not exist.',
                    path,
                )


def _validate_nav(root, path, spine_paths, report):
    if root is None:
        return
    for anchor in root.iter(f'{NS_XHTML}a'):
        found = _local_ref(path, anchor.get('href') or '')
        if found is not None and found[0] not in spine_paths:
            _problem(
                report,
                'error',
                'nav_target',
                f'Navigation link {anchor.get("href")} is not a spine document.',
                path,
            )
    toc = [n for n in root.iter(f'{NS_XHTML}nav') if 'toc' in (n.get(f'{NS_EPUB}type') or '').split()]
    if len(toc) != 1:
        _problem(
            report,
            'error',
            'nav_toc',
            'The navigation document needs exactly one nav with epub:type="toc".',
            path,
        )
        return
    anchors = list(toc[0].iter(f'{NS_XHTML}a'))
    if not anchors or toc[0].find(f'{NS_XHTML}ol') is None:
        _problem(report, 'error', 'nav_empty', 'The table of contents has no entries.', path)
    for anchor in anchors:
        if not ''.join(anchor.itertext()).strip():
            _problem(report, 'error', 'nav_label', 'A table of contents entry has no label.', path)


def check_written(path):
    """Validate a freshly written package and refuse to publish one that fails the structural check."""
    result = validate(path)
    if not result['valid']:
        raise ByaxError(
            'execution_failure',
            'The EPUB failed its structural check and was not written',
            errors=result['errors'][:20],
        )
    return result


# Import ------------------------------------------------------------------------------------------

_OBFUSCATION = ('http://www.idpf.org/2008/embedding', 'http://ns.adobe.com/pdf/enc#RC')


class _EpubHTML(_HTMLImport):
    """HTML import for one content document: package-relative images, EPUB 3 notes, no label text."""

    def __init__(self, doc, losses, archive, member, names, images):
        super().__init__(doc, losses)
        self.archive, self.member, self.names, self.images = archive, member, names, images
        self.refs, self.notes = [], {}  # (block id, offset, target id); target id -> footnote block
        self.in_note = self.note_block = None
        self.skipped = []  # tags whose content is a label or note reference, not text

    def flush(self):
        current = self.current
        if (
            current is not None
            and self.in_note is not None
            and (current['text'].strip() or current['type'] == 'heading')
        ):
            current['type'], current['style'] = 'footnote', 'body'
            if self.note_block is not None:
                shift = len(self.note_block['text']) + 1
                self.note_block['text'] += '\n' + current['text']
                for mark in current['marks']:
                    mark['start'] += shift
                    mark['end'] += shift
                self.note_block['marks'].extend(current['marks'])
                self.current = None
                self.stack = []
                return
            self.note_block = current
            self.notes[self.in_note] = current
        super().flush()

    def handle_starttag(self, tag, attrs):
        values = {key: value or '' for key, value in attrs}
        kinds = (values.get('epub:type') or values.get('type') or '').split()
        classes = values.get('class', '').split()
        if not self.skip:
            if tag == 'aside' and {'footnote', 'endnote', 'rearnote'} & set(kinds):
                self.flush()
                self.in_note, self.note_block = values.get('id') or f'note-{len(self.notes)}', None
                return
            if tag == 'a' and 'noteref' in kinds:
                if self.current is None:
                    self.current = _block()
                target = values.get('href', '').partition('#')[2]
                if target and not self.tables:
                    self.refs.append((self.current['id'], len(self.current['text']), target))
                self.skipped.append(tag)
                self.skip += 1
                return
            if tag == 'span' and 'fn-label' in classes:
                self.skipped.append(tag)
                self.skip += 1
                return
            if tag == 'img' and self._image(values):
                return
            if tag in ('audio', 'video'):
                _loss(
                    self.losses,
                    'html_inactive_content',
                    f'<{tag}> was not imported.',
                    f'line:{self.getpos()[0]}',
                )
                self.skipped.append(tag)
                self.skip += 1
                return
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.skipped and self.skipped[-1] == tag and tag in ('a', 'span', 'audio', 'video'):
            self.skipped.pop()
            self.skip -= 1
            return
        if tag == 'aside' and self.in_note is not None:
            self.flush()
            self.in_note = self.note_block = None
            return
        super().handle_endtag(tag)

    def _image(self, values):
        source = values.get('src', '')
        found = _local_ref(self.member, source)
        if found is None or found[0] not in self.names:
            return False
        if found[0] not in self.images:
            data = self.archive.read(found[0])
            self.images[found[0]] = (data, _sniff(data))
        data, mime = self.images[found[0]]
        if mime is None:
            return False
        if len(self.doc.get('assets', {})) >= MAX_IMPORT_IMAGES:
            raise ByaxError(
                'resource_limit', 'EPUB contains more images than the import limit', member=found[0]
            )
        self.flush()
        block = _block(kind='image', alt=values.get('alt', ''), source=found[0])
        block['attrs']['asset'] = _asset(self.doc, data, mime, posixpath.basename(found[0]))
        if values.get('role') == 'presentation' and not values.get('alt'):
            block['attrs']['decorative'] = True
        self.doc['blocks'].append(block)
        return True


def import_epub(doc, raw, losses):
    """Import an EPUB into ``doc`` as blocks (like HTML import), with the hostile-archive limits."""
    archive = _safe_zip(raw)
    try:
        _import_package(doc, archive, losses)
    finally:
        archive.close()


def _xml_member(archive, member):
    data = archive.read(member)
    if b'<!ENTITY' in data:
        raise ByaxError(
            'unsafe_xml', 'Prohibited XML entity, DTD or external reference declaration', member=member
        )
    return data


def _import_package(doc, archive, losses):
    names = set(archive.namelist())
    if 'META-INF/container.xml' not in names:
        raise ByaxError('invalid_input', 'EPUB container.xml is missing', member='META-INF/container.xml')
    if 'mimetype' not in names or archive.read('mimetype').strip() != MIMETYPE:
        _loss(
            losses, 'epub_mimetype', 'The mimetype member is missing or not application/epub+zip.', 'mimetype'
        )
    container = _parse_xml(_xml_member(archive, 'META-INF/container.xml'), 'META-INF/container.xml')
    rootfile = container.find(f'.//{NS_CONTAINER}rootfile')
    opf_path = rootfile.get('full-path') if rootfile is not None else None
    if not opf_path or opf_path not in names or _unsafe_path(opf_path):
        raise ByaxError('invalid_input', 'EPUB container.xml names no readable package document')
    if 'META-INF/encryption.xml' in names:
        encryption = _parse_xml(_xml_member(archive, 'META-INF/encryption.xml'), 'META-INF/encryption.xml')
        algorithms = {e.get('Algorithm') for e in encryption.iter() if e.tag.endswith('EncryptionMethod')}
        if algorithms - set(_OBFUSCATION):
            raise ByaxError('encrypted_input', 'This EPUB uses DRM or encryption, which is not supported')
        _loss(
            losses, 'epub_font_obfuscation', 'Obfuscated fonts were not imported.', 'META-INF/encryption.xml'
        )
    opf = _parse_xml(_xml_member(archive, opf_path), opf_path)
    metadata = opf.find(f'{NS_OPF}metadata')
    manifest = opf.find(f'{NS_OPF}manifest')
    spine = opf.find(f'{NS_OPF}spine')
    if manifest is None or spine is None:
        raise ByaxError('invalid_input', 'EPUB package document has no manifest or spine', member=opf_path)
    items = {}
    for item in manifest.findall(f'{NS_OPF}item'):
        target = _local_ref(opf_path, item.get('href') or '')
        if item.get('id') and target and target[0] in names:
            items[item.get('id')] = {
                'path': target[0],
                'media': item.get('media-type') or '',
                'properties': (item.get('properties') or '').split(),
            }
    refs = spine.findall(f'{NS_OPF}itemref')
    if len(refs) > MAX_SPINE:
        raise ByaxError('resource_limit', 'EPUB spine exceeds import limit')
    fixed = [
        e
        for e in (metadata.findall(f'{NS_OPF}meta') if metadata is not None else [])
        if e.get('property') == 'rendition:layout'
    ]
    if any((e.text or '').strip() == 'pre-paginated' for e in fixed):
        _loss(losses, 'epub_fixed_layout', 'Fixed-layout pages were imported as reflowable text.', opf_path)
    _import_metadata(doc, archive, metadata, items, losses)
    images = {}  # archive path to (bytes, media type), read once however often it is used
    for ref in refs:
        item = items.get(ref.get('idref'))
        if item is None or item['media'] != XHTML or 'nav' in item['properties']:
            continue
        raw = archive.read(item['path'])
        if re.search(rb'<body[^>]*epub:type="[^"]*\bcover\b', raw[:4096]):
            continue
        if b'<!ENTITY' in raw:
            raise ByaxError(
                'unsafe_xml',
                'Prohibited XML entity, DTD or external reference declaration',
                member=item['path'],
            )
        text, _encoding = _decode(raw)
        parser = _EpubHTML(doc, [], archive, item['path'], names, images)
        before = len(doc['blocks'])
        parser.feed(text)
        parser.close()
        parser.flush()
        styled = [x for x in parser.losses if x['code'] == 'html_css']
        for loss in parser.losses:
            if loss['code'] == 'html_css':
                continue
            if str(loss.get('target', '')).startswith('line:'):
                loss['target'] = f'{item["path"]}#{loss["target"]}'
            losses.append(loss)
        if styled:
            _loss(
                losses,
                'html_css',
                f'CSS styling on {len(styled)} elements is not imported; the original EPUB remains attached.',
                item['path'],
            )
        _link_notes(doc, parser, before)
        _tidy_marks(doc['blocks'][before:], item['path'], losses)
    if len(doc['blocks']) > 0 and not any(
        b.get('text') or b['type'] in ('table', 'image') for b in doc['blocks']
    ):
        _loss(losses, 'epub_no_text', 'The spine contained no importable text.', opf_path)


def _unsafe_path(path):
    return path.startswith('/') or '..' in path.split('/') or '\\' in path


def _tidy_marks(blocks, member, losses):
    """Drop empty marks left by skipped note references and links that only made sense inside the book."""
    for block in blocks:
        for obj in [block, *(c for row in block.get('rows', []) for c in row.get('cells', []))]:
            kept = []
            for mark in obj.get('marks', []):
                if mark['start'] == mark['end']:
                    continue
                if mark['type'] == 'link' and not str(mark['value']).startswith(
                    ('http://', 'https://', 'mailto:')
                ):
                    _loss(
                        losses,
                        'epub_internal_link',
                        'A link inside the book was imported as plain text.',
                        f'{member}#{block["id"]}',
                    )
                    continue
                kept.append(mark)
            obj['marks'] = kept


def _link_notes(doc, parser, before):
    blocks = {b['id']: b for b in doc['blocks'][before:]}
    for block_id, offset, target in parser.refs:
        note = parser.notes.get(target)
        if note is not None and block_id in blocks and 'reference' not in note['attrs']:
            note['attrs'].update(reference=block_id, offset=min(offset, len(blocks[block_id]['text'])))


def _import_metadata(doc, archive, metadata, items, losses):
    if metadata is None:
        return
    values = doc['metadata']

    def first(tag):
        return next(
            ((e.text or '').strip() for e in metadata.findall(f'{NS_DC}{tag}') if (e.text or '').strip()), ''
        )

    if title := first('title'):
        values['title'] = title
    creators = [(e.text or '').strip() for e in metadata.findall(f'{NS_DC}creator') if (e.text or '').strip()]
    if creators:
        values['author'] = ', '.join(creators)
    if language := _language(first('language')):
        values['language'] = language
    for key in ('identifier', 'publisher', 'description', 'rights'):
        if value := first(key):
            values[key] = value
    for meta in metadata.findall(f'{NS_OPF}meta'):
        if meta.get('property') == 'dcterms:modified' and _utc((meta.text or '').strip()):
            values['modified'] = _utc((meta.text or '').strip())
    cover = next((i for i in items.values() if 'cover-image' in i['properties']), None)
    if cover is None:
        legacy = next(
            (m.get('content') for m in metadata.findall(f'{NS_OPF}meta') if m.get('name') == 'cover'), None
        )
        cover = items.get(legacy)
    if cover is not None:
        data = archive.read(cover['path'])
        mime = _sniff(data)
        if mime and len(data) <= MAX_INPUT:
            values['cover'] = _asset(doc, data, mime, posixpath.basename(cover['path']))
        else:
            _loss(
                losses,
                'epub_cover',
                'The cover image is not a supported image type and was not imported.',
                cover['path'],
            )
