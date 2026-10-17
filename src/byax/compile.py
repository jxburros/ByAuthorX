"""Compile pipeline (CP B2): one command turns a manuscript project into a finished book.

A *compile profile* extends the export profile: it chooses the binder items to include and their
order, the front and back matter (generated from metadata or taken from ``front``/``back`` binder
items), what each item kind looks like (chapter openers with page starts, number styles, drop caps
and a small-caps lead-in; scene separators; part pages) and the book design for print (trim size,
mirrored margins with a gutter, running heads, folios, widow and orphan control, bleed and crop
marks). Profiles are data: the bundled presets in ``resources/compile`` are settings collections
(``byax-settings-collection`` with a ``compile`` kind), and writers save their own in the document
(``settings.compile_presets``) or in a collection file.

Compiling never touches the master. :func:`build` copies it, strips editing state that is not
output (history, review, protections, locks, the binder itself) and turns the copy into a transient
*compiled document* through ``engine.apply`` operations. Each target then reuses its existing
exporter and that exporter's located-loss report: ``exchange.export_document`` for PDF, Word and
Markdown and ``epub.export_epub`` for EPUB. Word and Markdown output is re-imported and compared
with the compiled document, and every difference is added to the report as a located loss.

Preflight findings use the shared findings model. The compile report carries the
``layout_signature`` digest (spec 6.4) and the page each block starts on, and the same inputs give
the same EPUB bytes and the same PDF page breaks.
"""

from __future__ import annotations

import copy
import difflib
import itertools
import re
import unicodedata
from pathlib import Path
from typing import Any, NoReturn

import regex

from .errors import ByaxError
from .model import canonical_json, object_hash, text_of

Doc = dict[str, Any]

PRESET_ROOT = Path(__file__).parent / 'resources' / 'compile'
BUILTIN_PRESETS = ('novel', 'nonfiction', 'manuscript')
TARGETS = ('pdf', 'epub', 'docx', 'md')
ENGINE = 'byax-compile/1'
# Trim sizes in points (width, height). Common trade, mass-market and international sizes.
TRIM_SIZES = {
    '5x8': (360.0, 576.0),
    '5.06x7.81': (364.32, 562.32),
    '5.25x8': (378.0, 576.0),
    '5.5x8.5': (396.0, 612.0),
    '6x9': (432.0, 648.0),
    '6.14x9.21': (442.08, 663.12),
    '7x10': (504.0, 720.0),
    'a5': (419.528, 595.276),
    'b5': (498.898, 708.661),
    'a4': (595.276, 841.89),
    'letter': (612.0, 792.0),
}
STARTS = ('odd', 'next', 'continuous')
NUMBER_STYLES = ('none', 'arabic', 'roman', 'lower_roman', 'words', 'upper_words')
TITLE_MODES = ('auto', 'show', 'hide')
HEADING_MODES = ('separator', 'keep', 'hide')
FOLIOS = ('outside_head', 'bottom_center', 'bottom_outside', 'none')
OPENER_FOLIOS = ('bottom_center', 'none')
MATTER_KINDS = (
    'half_title',
    'title_page',
    'copyright',
    'dedication',
    'epigraph',
    'toc',
    'acknowledgments',
    'about_author',
    'also_by',
    'manuscript_title',
    'text',
)
ITEM_KINDS = ('part', 'chapter', 'scene', 'front', 'back')
PROFILE_KEYS = {
    'extends',
    'label',
    'description',
    'version',
    'target',
    'include',
    'exclude',
    'order',
    'front_matter',
    'back_matter',
    'items',
    'design',
    'styles',
    'epub',
    'end_mark',
}
ITEM_KEYS = {
    'start',
    'number_style',
    'label',
    'title',
    'drop_cap',
    'small_caps_words',
    'sink',
    'numbering',
    'heading',
    'separator',
}
DESIGN_KEYS = {
    'trim',
    'margins',
    'gutter',
    'mirror',
    'running_heads',
    'folios',
    'opener_folio',
    'header',
    'widows',
    'orphans',
    'bleed',
    'crop_marks',
    'image_dpi',
    'docx',
    'restart_numbering',
}
# Paragraph and character styles every compile can rely on; presets override or extend them.
BASE_STYLES = {
    'compile_first': {'based_on': 'body', 'first_line_indent': 0},
    'compile_scene_break': {
        'based_on': 'body',
        'alignment': 'center',
        'first_line_indent': 0,
        'space_before': 12,
        'space_after': 12,
        'keep_with_next': True,
    },
    'compile_chapter_label': {
        'based_on': 'body',
        'alignment': 'center',
        'first_line_indent': 0,
        'space_after': 6,
        'keep_with_next': True,
    },
    'compile_part_label': {
        'based_on': 'body',
        'alignment': 'center',
        'first_line_indent': 0,
        'space_after': 12,
        'keep_with_next': True,
    },
    'compile_title': {'based_on': 'body', 'alignment': 'center', 'first_line_indent': 0, 'font_size': 24},
    'compile_subtitle': {'based_on': 'body', 'alignment': 'center', 'first_line_indent': 0, 'font_size': 14},
    'compile_author': {'based_on': 'body', 'alignment': 'center', 'first_line_indent': 0, 'space_before': 36},
    'compile_publisher': {
        'based_on': 'body',
        'alignment': 'center',
        'first_line_indent': 0,
        'space_before': 72,
    },
    'compile_half_title': {
        'based_on': 'body',
        'alignment': 'center',
        'first_line_indent': 0,
        'font_size': 16,
    },
    'compile_copyright': {'based_on': 'body', 'first_line_indent': 0, 'font_size': 8, 'space_after': 4},
    'compile_dedication': {'based_on': 'body', 'alignment': 'center', 'first_line_indent': 0, 'italic': True},
    'compile_epigraph': {'based_on': 'body', 'alignment': 'center', 'first_line_indent': 0, 'italic': True},
    'compile_matter_heading': {
        'based_on': 'body',
        'alignment': 'center',
        'first_line_indent': 0,
        'font_size': 14,
        'space_after': 18,
        'keep_with_next': True,
    },
    'compile_matter_text': {'based_on': 'body', 'first_line_indent': 0, 'space_after': 6},
    'compile_contact': {'based_on': 'body', 'first_line_indent': 0, 'line_spacing': 1, 'space_after': 0},
    'compile_word_count': {
        'based_on': 'body',
        'first_line_indent': 0,
        'alignment': 'right',
        'line_spacing': 1,
    },
    'compile_byline': {'based_on': 'body', 'first_line_indent': 0, 'alignment': 'center'},
    'compile_end_mark': {
        'based_on': 'body',
        'first_line_indent': 0,
        'alignment': 'center',
        'space_before': 24,
    },
    'compile_toc': {'based_on': 'body', 'first_line_indent': 0},
    'compile_drop_cap': {'kind': 'character'},
    'compile_lead_in': {'kind': 'character', 'small_caps': True},
}
# Role styles are not defined as styles: their values are given to the heading level the role uses,
# so headings keep their ``headingN`` style names (and Word heading semantics) in every target.
ROLE_STYLES = ('compile_part_title', 'compile_chapter_title', 'compile_subheading')
MATTER_TEMPLATES: dict[str, list[tuple[str, Any]]] = {
    'half_title': [('compile_half_title', '{title}')],
    'title_page': [
        ('compile_title', '{title}'),
        ('compile_subtitle', '{subtitle}'),
        ('compile_author', '{author}'),
        ('compile_publisher', '{publisher}'),
    ],
    'copyright': [
        ('compile_copyright', ['Copyright © {year} {author}', 'Copyright © {author}']),
        ('compile_copyright', 'All rights reserved.'),
        ('compile_copyright', '{rights}'),
        ('compile_copyright', 'ISBN {isbn}'),
        ('compile_copyright', 'Published by {publisher}'),
    ],
    'dedication': [('compile_dedication', '{dedication}')],
    'epigraph': [('compile_epigraph', '{epigraph}')],
    'acknowledgments': [('compile_matter_text', '{acknowledgments}')],
    'about_author': [('compile_matter_text', '{about_author}')],
    'also_by': [('compile_matter_text', '{also_by}')],
    'manuscript_title': [
        ('compile_word_count', 'About {words_rounded} words'),
        ('compile_contact', '{contact_name}'),
        ('compile_contact', '{contact_address}'),
        ('compile_contact', '{contact_email}'),
        ('compile_contact', '{contact_phone}'),
        ('compile_title', '{title}'),
        ('compile_byline', 'by {byline}'),
    ],
    'text': [],
    'toc': [],
}
MATTER_HEADINGS = {
    'toc': 'Contents',
    'acknowledgments': 'Acknowledgments',
    'about_author': 'About the Author',
    'also_by': 'Also by {author}',
}
# Kinds whose required metadata value is missing are skipped (and reported) rather than printed empty.
MATTER_REQUIRES = {
    'dedication': 'dedication',
    'epigraph': 'epigraph',
    'acknowledgments': 'acknowledgments',
    'about_author': 'about_author',
    'also_by': 'also_by',
}
_PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')
_ONES = [
    'zero',
    'one',
    'two',
    'three',
    'four',
    'five',
    'six',
    'seven',
    'eight',
    'nine',
    'ten',
    'eleven',
    'twelve',
    'thirteen',
    'fourteen',
    'fifteen',
    'sixteen',
    'seventeen',
    'eighteen',
    'nineteen',
]
_TENS = ['zero', 'ten', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']


def _fail(message: str, field: str) -> NoReturn:
    raise ByaxError('invalid_input', message, field=field)


# Profiles -----------------------------------------------------------------------------------------


def _number_ok(value, low=0.0, high=10000.0):
    return type(value) in (int, float) and low <= value <= high


def _text_list(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _check_matter(entries, field):
    if not isinstance(entries, list) or len(entries) > 40:
        _fail('Matter must be a list of at most 40 entries.', field)
    for index, entry in enumerate(entries):
        path = f'{field}[{index}]'
        if not isinstance(entry, dict) or entry.get('kind') not in MATTER_KINDS:
            _fail('Matter entries need a kind from ' + ', '.join(MATTER_KINDS) + '.', path)
        unknown = set(entry) - {'kind', 'title', 'text', 'start', 'targets'}
        if unknown:
            _fail('Unknown matter entry keys: ' + ', '.join(sorted(unknown)), path)
        if 'title' in entry and not isinstance(entry['title'], str):
            _fail('A matter title must be text.', path + '.title')
        if 'text' in entry and not (
            isinstance(entry['text'], list)
            and all(isinstance(line, str) or _text_list(line) for line in entry['text'])
        ):
            _fail('Matter text is a list of lines (or lists of alternative lines).', path + '.text')
        if entry.get('start', 'odd') not in STARTS:
            _fail('Matter start must be odd, next or continuous.', path + '.start')
        if 'targets' in entry and not (
            _text_list(entry['targets']) and set(entry['targets']) <= set(TARGETS)
        ):
            _fail('Matter targets must list pdf, epub, docx or md.', path + '.targets')


def _check_item(kind, spec, field):
    if not isinstance(spec, dict):
        _fail('Item formatting must be an object.', field)
    unknown = set(spec) - ITEM_KEYS
    if unknown:
        _fail('Unknown item formatting keys: ' + ', '.join(sorted(unknown)), field)
    checks = (
        ('start', lambda v: v in STARTS, 'odd, next or continuous'),
        ('number_style', lambda v: v in NUMBER_STYLES, ', '.join(NUMBER_STYLES)),
        ('label', lambda v: isinstance(v, str) and len(v) <= 200, 'text'),
        ('title', lambda v: v in TITLE_MODES, 'auto, show or hide'),
        ('drop_cap', lambda v: type(v) is bool, 'true or false'),
        ('small_caps_words', lambda v: type(v) is int and 0 <= v <= 30, 'an integer from 0 to 30'),
        ('sink', lambda v: _number_ok(v, 0, 600), 'points from 0 to 600'),
        ('numbering', lambda v: v in ('continuous', 'per_part'), 'continuous or per_part'),
        ('heading', lambda v: v in HEADING_MODES, 'separator, keep or hide'),
        (
            'separator',
            lambda v: isinstance(v, str) and 0 < len(v) <= 40,
            'nonempty text of up to 40 characters',
        ),
    )
    for key, ok, expected in checks:
        if key in spec and not ok(spec[key]):
            _fail(f'{kind}.{key} must be {expected}.', f'{field}.{key}')


def _check_design(design, field):
    if not isinstance(design, dict):
        _fail('Design must be an object.', field)
    unknown = set(design) - DESIGN_KEYS
    if unknown:
        _fail('Unknown design keys: ' + ', '.join(sorted(unknown)), field)
    if 'trim' in design:
        trim_size(design['trim'], field + '.trim')
    margins = design.get('margins', {})
    if not isinstance(margins, dict) or set(margins) - {'top', 'bottom', 'inside', 'outside'}:
        _fail('Design margins are top, bottom, inside and outside in points.', field + '.margins')
    if not all(_number_ok(v, 0, 400) for v in margins.values()):
        _fail('Design margins must be numbers of points from 0 to 400.', field + '.margins')
    for key, low, high in (('gutter', 0, 144), ('bleed', 0, 72), ('image_dpi', 72, 2400)):
        if key in design and not _number_ok(design[key], low, high):
            _fail(f'design.{key} must be a number from {low} to {high}.', f'{field}.{key}')
    for key in ('widows', 'orphans'):
        if key in design and not (type(design[key]) is int and 0 <= design[key] <= 6):
            _fail(f'design.{key} must be an integer line count from 0 to 6.', f'{field}.{key}')
    for key in ('mirror', 'crop_marks', 'restart_numbering'):
        if key in design and type(design[key]) is not bool:
            _fail(f'design.{key} must be true or false.', f'{field}.{key}')
    if design.get('folios', 'none') not in FOLIOS:
        _fail('design.folios must be ' + ', '.join(FOLIOS) + '.', field + '.folios')
    if design.get('opener_folio', 'none') not in OPENER_FOLIOS:
        _fail('design.opener_folio must be bottom_center or none.', field + '.opener_folio')
    heads = design.get('running_heads')
    if heads is not None:
        if not isinstance(heads, dict) or set(heads) - {'verso', 'recto', 'small_caps', 'font', 'size'}:
            _fail('Running heads are {verso, recto, small_caps, font, size}.', field + '.running_heads')
        if any(not isinstance(heads.get(k, ''), str) for k in ('verso', 'recto', 'font')):
            _fail('Running head templates and font must be text.', field + '.running_heads')
        if 'size' in heads and not _number_ok(heads['size'], 4, 30):
            _fail('Running head size must be 4 to 30 points.', field + '.running_heads.size')
    header = design.get('header')
    if header is not None:
        if not isinstance(header, dict) or set(header) - {'text', 'align', 'first_page'}:
            _fail('A design header is {text, align, first_page}.', field + '.header')
        if not isinstance(header.get('text', ''), str) or header.get('align', 'right') not in (
            'left',
            'center',
            'right',
        ):
            _fail('The design header needs text and an alignment.', field + '.header')
        if type(header.get('first_page', True)) is not bool:
            _fail('design.header.first_page must be true or false.', field + '.header.first_page')
    docx = design.get('docx', {})
    if not isinstance(docx, dict) or set(docx) - {'trim', 'margins'}:
        _fail('design.docx is {trim, margins}.', field + '.docx')
    if 'trim' in docx:
        trim_size(docx['trim'], field + '.docx.trim')
    if 'margins' in docx and not _number_ok(docx['margins'], 0, 300):
        _fail('design.docx.margins must be a number of points.', field + '.docx.margins')


def validate_profile(profile: Any, *, field: str = 'profile') -> dict[str, Any]:
    """Reject unknown keys and wrong types in a compile profile; returns a deep copy."""
    if not isinstance(profile, dict):
        _fail('A compile profile must be an object.', field)
    unknown = set(profile) - PROFILE_KEYS
    if unknown:
        _fail('Unknown compile profile keys: ' + ', '.join(sorted(unknown)), field)
    for key in ('extends', 'label', 'description', 'end_mark'):
        if key in profile and not isinstance(profile[key], str):
            _fail(f'{key} must be text.', f'{field}.{key}')
    if 'version' in profile and type(profile['version']) is not int:
        _fail('version must be an integer.', field + '.version')
    if profile.get('target', 'pdf') not in TARGETS:
        _fail('target must be pdf, epub, docx or md.', field + '.target')
    if profile.get('include') is not None and not _text_list(profile['include']):
        _fail('include lists binder item ids (or null for the binder default).', field + '.include')
    for key in ('exclude', 'order'):
        if key in profile and not _text_list(profile[key]):
            _fail(f'{key} lists binder item ids.', f'{field}.{key}')
    for key in ('front_matter', 'back_matter'):
        if key in profile:
            _check_matter(profile[key], f'{field}.{key}')
    items = profile.get('items', {})
    if not isinstance(items, dict) or set(items) - set(ITEM_KINDS):
        _fail('items formats the kinds ' + ', '.join(ITEM_KINDS) + '.', field + '.items')
    for kind, spec in items.items():
        _check_item(kind, spec, f'{field}.items.{kind}')
    if 'design' in profile:
        _check_design(profile['design'], field + '.design')
    styles = profile.get('styles', {})
    if not isinstance(styles, dict) or not all(
        isinstance(name, str) and isinstance(value, dict) for name, value in styles.items()
    ):
        _fail('styles maps style names to style objects.', field + '.styles')
    if 'epub' in profile:
        from .epub import OPTION_KEYS

        allowed = OPTION_KEYS - {'include', 'split_at', 'split_level', 'modified'}
        if not isinstance(profile['epub'], dict) or set(profile['epub']) - allowed:
            _fail('epub accepts the EPUB options ' + ', '.join(sorted(allowed)) + '.', field + '.epub')
    from .storage import _check_secrets

    _check_secrets(profile)
    try:
        canonical_json(profile)
    except (TypeError, ValueError) as exc:
        _fail(f'A compile profile must be plain JSON: {exc}', field)
    return copy.deepcopy(profile)


def trim_size(value: Any, field: str = 'trim') -> tuple[float, float]:
    """Width and height in points of a named trim size (``6x9``, ``a5``…) or a ``{width, height}`` object."""
    if isinstance(value, str):
        key = value.strip().lower().replace(' ', '').replace('in', '').replace('×', 'x')
        if key in TRIM_SIZES:
            return TRIM_SIZES[key]
        _fail('Unknown trim size; use one of ' + ', '.join(TRIM_SIZES) + ' or {width, height}.', field)
    if (
        isinstance(value, dict)
        and set(value) == {'width', 'height'}
        and _number_ok(value['width'], 72, 2000)
        and _number_ok(value['height'], 72, 2000)
    ):
        return float(value['width']), float(value['height'])
    _fail('A trim is a named size or {width, height} in points (72 to 2000).', field)


def merge(base: Any, override: Any) -> Any:
    """Deep merge of plain JSON objects; lists and scalars in ``override`` replace."""
    if isinstance(base, dict) and isinstance(override, dict):
        result = copy.deepcopy(base)
        for key, value in override.items():
            result[key] = merge(base.get(key), value) if key in base else copy.deepcopy(value)
        return result
    return copy.deepcopy(override)


def builtin_collection(name: str) -> dict[str, Any]:
    if name not in BUILTIN_PRESETS:
        raise ByaxError(
            'missing_target', 'Unknown compile preset.', target=name, supported=list(BUILTIN_PRESETS)
        )
    from .profiles import read_collection

    return read_collection(PRESET_ROOT / f'{name}.json')


def list_presets(doc: Doc | None = None) -> list[dict[str, Any]]:
    """Bundled presets and the presets saved in a document, with their labels."""
    result = []
    for name in BUILTIN_PRESETS:
        profile = builtin_collection(name)['collections']['compile'][name]
        result.append(
            {
                'name': name,
                'source': 'builtin',
                'label': profile.get('label', name),
                'description': profile.get('description', ''),
                'target': profile.get('target', 'pdf'),
            }
        )
    saved = (doc or {}).get('settings', {}).get('compile_presets', {})
    for name in sorted(saved if isinstance(saved, dict) else {}):
        profile = saved[name]
        result.append(
            {
                'name': name,
                'source': 'document',
                'label': profile.get('label', name) if isinstance(profile, dict) else name,
                'description': profile.get('description', '') if isinstance(profile, dict) else '',
                'target': profile.get('target', 'pdf') if isinstance(profile, dict) else 'pdf',
            }
        )
    return result


def resolve_profile(
    doc: Doc | None,
    preset: str | None = None,
    *,
    preset_file: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The effective profile: a preset (document, file or bundled) deep-merged with overrides.

    A preset saved in the document wins over a bundled preset of the same name. Saved and file
    presets may be partial: they are merged onto the bundled preset named by ``extends`` (default:
    the bundled preset of the same name, else ``novel``), so they only need the values they change.
    """
    source: dict[str, Any]
    if preset_file is not None:
        from .profiles import read_collection

        presets = read_collection(preset_file)['collections'].get('compile', {})
        if not presets:
            raise ByaxError(
                'invalid_input', 'The collection file holds no compile preset.', field='preset_file'
            )
        name = preset if preset in presets else None
        if name is None:
            if len(presets) != 1 or preset is not None:
                raise ByaxError(
                    'missing_target',
                    'Name one of the compile presets in the collection file.',
                    target=preset,
                    available=sorted(presets),
                )
            name = next(iter(presets))
        profile, source = presets[name], {'source': 'file', 'name': name, 'path': str(Path(preset_file))}
    else:
        name = preset or 'novel'
        saved = (doc or {}).get('settings', {}).get('compile_presets', {})
        if isinstance(saved, dict) and name in saved:
            profile = validate_profile(saved[name], field='settings.compile_presets.' + name)
            source = {'source': 'document', 'name': name}
        else:
            profile = builtin_collection(name)['collections']['compile'][name]
            source = {'source': 'builtin', 'name': name}
    if source['source'] != 'builtin':
        base = profile.get('extends') or (name if name in BUILTIN_PRESETS else 'novel')
        if base not in BUILTIN_PRESETS:
            raise ByaxError('missing_target', 'extends names a bundled preset.', target=base, field='extends')
        source['extends'] = base
        profile = merge(builtin_collection(base)['collections']['compile'][base], profile)
    if overrides:
        profile = merge(profile, validate_profile(overrides, field='input'))
    profile.pop('extends', None)
    return validate_profile(profile), source


def preset_collection(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """A settings collection holding one compile preset, for ``profiles.write_collection``."""
    return {
        'schema_version': 1,
        'kind': 'byax-settings-collection',
        'collections': {'compile': {name: validate_profile(profile)}},
        'requires': {'provider_aliases': [], 'styles': [], 'sources': []},
    }


def save_preset_operations(doc: Doc, name: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Canonical operations saving a compile preset in the document's settings."""
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}', name):
        _fail('Preset names are 1 to 64 letters, digits, spaces, dots, dashes or underscores.', 'name')
    presets = copy.deepcopy(doc.get('settings', {}).get('compile_presets', {}))
    presets[name] = validate_profile(profile)
    return [{'type': 'set_settings', 'values': {'compile_presets': presets}}]


# Text helpers -------------------------------------------------------------------------------------


def number_words(number: int) -> str:
    """English words for 0..9999 in title case: 21 -> "Twenty-One"."""

    def below_thousand(n):
        words = []
        if n >= 100:
            words.append(_ONES[n // 100] + ' hundred')
            n %= 100
        if n >= 20:
            words.append(_TENS[n // 10] + ('-' + _ONES[n % 10] if n % 10 else ''))
        elif n or not words:
            words.append(_ONES[n])
        return ' '.join(words)

    if not 0 <= number <= 9999:
        return str(number)
    if number >= 1000:
        rest = number % 1000
        text = _ONES[number // 1000] + ' thousand' + (' ' + below_thousand(rest) if rest else '')
    else:
        text = below_thousand(number)
    return ' '.join('-'.join(part.capitalize() for part in word.split('-')) for word in text.split())


def format_number(number: int, style: str) -> str:
    from .layout import _format_page_number

    if style == 'none':
        return ''
    if style == 'arabic':
        return str(number)
    if style in ('roman', 'lower_roman'):
        return _format_page_number(number, 'upper_roman' if style == 'roman' else 'lower_roman')
    words = number_words(number)
    return words.upper() if style == 'upper_words' else words


def rounded_words(words: int) -> str:
    """Manuscript word count: nearest 100 below 10,000 words, else nearest 1,000."""
    step = 100 if words < 10000 else 1000
    return f'{max(step, round(words / step) * step):,}'


def _graphemes(text):
    return [m.end() for m in regex.finditer(r'\X', text)]


def drop_cap_end(text: str) -> int:
    """End offset of the initial: leading punctuation (an opening quote) plus one grapheme."""
    ends, start = _graphemes(text), 0
    for end in ends:
        if unicodedata.category(text[start])[0] == 'P':
            start = end
            continue
        return end if not text[start].isspace() else 0
    return 0


def lead_in_end(text: str, start: int, words: int) -> int:
    """End offset of the first ``words`` words counted from the paragraph start."""
    if words <= 0:
        return start
    matches = list(re.finditer(r'\S+', text))
    if not matches:
        return start
    end = matches[min(words, len(matches)) - 1].end()
    return max(start, end)


def _values(doc: Doc, compiled_words: int) -> dict[str, str]:
    """Placeholder values from the document metadata (text data, never instructions)."""
    meta = doc.get('metadata', {})

    def text(key):
        value = meta.get(key)
        if isinstance(value, list):
            value = '\n'.join(str(v) for v in value if isinstance(v, (str, int)))
        return str(value).strip() if isinstance(value, (str, int)) and str(value).strip() else ''

    contact = meta.get('contact') if isinstance(meta.get('contact'), dict) else {}
    author = text('author')
    values = {
        key: text(key)
        for key in (
            'title',
            'subtitle',
            'author',
            'publisher',
            'year',
            'rights',
            'isbn',
            'dedication',
            'epigraph',
            'acknowledgments',
            'about_author',
            'also_by',
        )
    }
    if values['title'] == 'Untitled':
        values['title'] = ''
    if not values['year'] and text('copyright_year'):
        values['year'] = text('copyright_year')
    values['byline'] = text('pen_name') or author
    values['surname'] = text('surname') or (values['byline'].split()[-1] if values['byline'] else '')
    values['short_title'] = text('short_title') or values['title'].upper()
    for key in ('name', 'address', 'email', 'phone'):
        raw = contact.get(key, meta.get('contact_' + key))
        if isinstance(raw, list):
            raw = '\n'.join(str(v) for v in raw)
        values['contact_' + key] = str(raw).strip() if isinstance(raw, (str, int)) else ''
    if not values['contact_name']:
        values['contact_name'] = author
    values['words'] = f'{compiled_words:,}'
    values['words_rounded'] = rounded_words(compiled_words)
    return values


def fill(template: str | list[str], values: dict[str, str]) -> str | None:
    """First alternative whose placeholders all have values; ``None`` when none does."""
    for option in [template] if isinstance(template, str) else template:
        names = _PLACEHOLDER.findall(option)
        if all(values.get(name) for name in names):
            return _PLACEHOLDER.sub(lambda m: values[m.group(1)], option)
    return None


# Findings ------------------------------------------------------------------------------------------


def _finding(rule, severity, message, target=None, measured=None):
    finding = {
        'rule': rule,
        'family': 'print',
        'suite': 'compile',
        'severity': severity,
        'policy': 'strict' if severity == 'error' else 'advisory',
        'explanation': message,
        'affected_ids': [target] if target else [],
        'range': None,
        'text': None,
        'measured': measured or {},
        'evidence': [],
        'operations': [],
        'status': 'open',
        'repair': {'kind': 'manual'},
        'scope': target or 'document',
        'engine': ENGINE,
    }
    identity = {k: finding[k] for k in ('rule', 'affected_ids', 'measured', 'explanation')}
    finding['id'] = 'finding_' + object_hash(identity)[:20]
    return finding


def _loss(code, message, target=None, **details):
    return {'code': code, 'severity': 'warning', 'message': message, 'target': target, **details}


# Building the compiled document -------------------------------------------------------------------


def _transient_copy(doc: Doc) -> tuple[Doc, dict[str, int]]:
    """A copy without editing state that is never output; the master is not touched."""
    work = copy.deepcopy(doc)
    review = work.get('review', {})
    excluded = {
        'comments': len(review.get('comments', [])),
        'proposals': len(review.get('proposals', [])),
        'tracked_changes': len(review.get('tracked_changes', [])),
        'findings': len(review.get('findings', [])),
        'layers': len(work.get('layers', [])),
        'history_revisions': len(work.get('history', {}).get('revisions', [])),
    }
    work['review'] = {'comments': [], 'proposals': [], 'tracked_changes': [], 'findings': []}
    work['history'] = {'revisions': [], 'branches': {}, 'current_branch': 'main'}
    work['requests'] = {}
    work['layers'] = []
    settings = work['settings']
    for key in ('protections', 'track_changes', 'requirements', 'bookmarks', 'compile_presets'):
        settings.pop(key, None)
    for block in work['blocks']:
        block.get('attrs', {}).pop('locked', None)
        block.pop('locked', None)
        for row in block.get('rows', []):
            for cell in row.get('cells', []):
                cell.get('attrs', {}).pop('locked', None)
    work.pop('project', None)
    return work, excluded


def _rows(doc: Doc) -> tuple[list[dict[str, Any]], dict[str, list[str]], list[str], str]:
    """Binder rows, own block IDs per item, unassigned blocks and how the structure was found."""
    from . import project

    view = doc
    mode = 'binder'
    if not isinstance(doc.get('project'), dict) or not doc['project'].get('items'):
        # No binder: every level-1 heading is a chapter; deeper headings stay as subheadings.
        items = {}
        for block in doc['blocks']:
            if block.get('type') == 'heading' and block.get('attrs', {}).get('level', 1) == 1:
                identity = project.item_id_for(block['id'])
                items[identity] = {
                    'id': identity,
                    'heading': block['id'],
                    'kind': 'chapter',
                    'status': 'draft',
                }
        view = {**doc, 'project': {'items': items}}
        mode = 'headings'
    rows = project.items(view)
    own = {entry['item']: entry['block_ids'] for entry in project.block_map(view)}
    plan = project.compile_plan(view)
    return rows, own, plan['unassigned'], mode


def _role_levels(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Heading level used by each role: the most common level of that kind's items."""
    counts: dict[str, dict[int, int]] = {}
    for row in rows:
        counts.setdefault(row['kind'], {})
        counts[row['kind']][row['level']] = counts[row['kind']].get(row['level'], 0) + 1
    levels = {}
    for kind, by_level in counts.items():
        levels[kind] = max(sorted(by_level), key=lambda level: by_level[level])
    return levels


class _Builder:
    """Plans the canonical operations that turn the transient copy into the compiled document."""

    def __init__(self, master: Doc, profile: dict[str, Any], target: str):
        self.master, self.profile, self.target = master, profile, target
        self.items = profile.get('items', {})
        self.design = profile.get('design', {})
        self.notes: list[dict[str, Any]] = []
        self.losses: list[dict[str, Any]] = []
        self.counter = 0
        self.split_at: list[dict[str, str]] = []

    def ident(self, kind):
        self.counter += 1
        return f'compile_{kind}_{self.counter:04d}'

    # --- inclusion ---------------------------------------------------------------------------
    def inclusion(self, rows, own):
        known = {row['id'] for row in rows}
        by_heading = {row['heading']: row['id'] for row in rows}

        def resolve(values, field):
            result = []
            for value in values:
                identity = by_heading.get(value, value)
                if identity not in known:
                    raise ByaxError(
                        'missing_target',
                        'The profile names a binder item that does not exist.',
                        target=value,
                        field=field,
                    )
                result.append(identity)
            return result

        if self.profile.get('include') is not None:
            included = set(resolve(self.profile['include'], 'include'))
        else:
            included = {row['id'] for row in rows if row['include_in_compile']}
        children = {row['id']: row['children'] for row in rows}
        pending = resolve(self.profile.get('exclude', []), 'exclude')
        while pending:
            identity = pending.pop()
            included.discard(identity)
            pending.extend(children.get(identity, []))
        order = resolve(self.profile.get('order', []), 'order')
        parents = {row['id']: row['parent'] for row in rows}
        if order and len({parents[i] for i in order}) != 1:
            raise ByaxError('invalid_input', 'order lists sibling binder items (one parent).', field='order')
        if len(set(order)) != len(order):
            raise ByaxError('invalid_input', 'order lists each item once.', field='order')
        return included, order

    # --- operations ---------------------------------------------------------------------------
    def phase_one(self, doc, rows, own, included, order):
        """Reorder and remove: section moves, then deletions of excluded items' own blocks."""
        heading = {row['id']: row['heading'] for row in rows}
        operations = []
        chosen = [identity for identity in order if identity in included]
        for previous, current in itertools.pairwise(chosen):
            operations.append(
                {'type': 'move_section', 'target': heading[current], 'after': heading[previous]}
            )
        removed = []
        for row in rows:
            if row['id'] not in included:
                removed.extend(own.get(row['id'], []))
        present = {block['id'] for block in doc['blocks']}
        for identity in removed:
            if identity in present:
                operations.append({'type': 'delete_block', 'target': identity})
        return operations

    def styles(self, doc, rows):
        """define_style operations: base styles, preset styles and role values on heading levels."""
        preset = self.profile.get('styles', {})
        values = {name: dict(spec) for name, spec in BASE_STYLES.items()}
        for name, spec in preset.items():
            if name not in ROLE_STYLES:
                values[name] = merge(values.get(name, doc['styles'].get(name, {})), spec)
        levels = _role_levels(rows)
        heading_roles = {}
        for kind, role in (('part', 'compile_part_title'), ('chapter', 'compile_chapter_title')):
            if kind in levels and role in preset:
                heading_roles.setdefault(levels[kind], role)
        for level in range(1, 7):
            role = heading_roles.get(level, 'compile_subheading')
            if role in preset:
                name = f'heading{level}'
                values[name] = merge(doc['styles'].get(name, {'based_on': 'body'}), preset[role])
        ordered, seen = [], set()

        def visit(name, trail=()):
            if name in seen or name in trail:
                return
            parent = values[name].get('based_on')
            if parent in values:
                visit(parent, (*trail, name))
            seen.add(name)
            ordered.append(name)

        for name in sorted(values):
            visit(name)
        return [{'type': 'define_style', 'name': name, 'values': values[name]} for name in ordered]

    def layout(self, values):
        """set_layout for the target: book design for PDF, a submission page for Word."""
        design = self.design
        if self.target == 'pdf':
            width, height = trim_size(design.get('trim', '5.5x8.5'), 'design.trim')
            margins = {'top': 54, 'bottom': 54, 'inside': 54, 'outside': 45, **design.get('margins', {})}
            layout = {
                'paper': 'custom',
                'width': width,
                'height': height,
                'orientation': 'portrait',
                'margins': {
                    'top': margins['top'],
                    'bottom': margins['bottom'],
                    'left': margins['inside'],
                    'right': margins['outside'],
                },
                'gutter': design.get('gutter', 0),
                'mirror_margins': design.get('mirror', True),
                'page_parity': 'physical',
                'blank_page_regions': False,
                'columns': 1,
                'master': None,
                'header': '',
                'footer': '',
                'page_number_format': 'lower_roman',
                'page_number_start': 1,
                'widow_control': design.get('widows', 2),
                'orphan_control': design.get('orphans', 2),
                'bleed': design.get('bleed', 0),
                'crop_marks': design.get('crop_marks', False),
            }
            heads = design.get('running_heads') or {}
            layout['region_font'] = heads.get('font', 'DejaVu Serif')
            layout['region_font_size'] = heads.get('size', 8)
            layout['region_small_caps'] = bool(heads.get('small_caps', False))
            layout['region_color'] = '#000000'
            return layout
        page = design.get('docx', {})
        width, height = trim_size(page.get('trim', 'letter'), 'design.docx.trim')
        margin = page.get('margins', 72)
        layout = {
            'paper': 'Letter' if (width, height) == TRIM_SIZES['letter'] else 'custom',
            'width': width,
            'height': height,
            'orientation': 'portrait',
            'margins': {key: margin for key in ('top', 'right', 'bottom', 'left')},
            'columns': 1,
            'master': None,
            'header': '',
            'footer': '',
        }
        header = design.get('header')
        if header and self.target == 'docx':
            text = self.region_text(header.get('text', ''), values)
            layout['header'] = text if header.get('first_page', True) else {'first': '', 'text': text}
            layout['header_align'] = header.get('align', 'right')
        elif self.target == 'docx':
            layout['footer'] = '{page}'
            layout['footer_align'] = 'center'
        return layout

    @staticmethod
    def region_text(template, values):
        tokens = {'page': '{page}', 'pages': '{pages}'}
        return _PLACEHOLDER.sub(
            lambda m: tokens.get(m.group(1), values.get(m.group(1), '')), template.replace('{chapter}', '')
        ).strip()

    def body_regions(self, values: dict[str, str], chapter_title: str = '') -> tuple[Any, Any]:
        """Header and footer of body pages: running heads, folios and suppression on openers."""
        design = self.design
        header = design.get('header')
        if header:
            text = self.region_text(header.get('text', ''), values)
            align = header.get('align', 'right')
            region = {align: text}
            return ({'first': '', 'text': region} if not header.get('first_page', True) else region), ''
        heads = design.get('running_heads') or {}
        chapter = {'chapter': chapter_title}
        fields = {**values, **chapter}

        def head(key):
            return _PLACEHOLDER.sub(lambda m: fields.get(m.group(1), ''), heads.get(key, '')).strip()

        verso, recto = head('verso'), head('recto')
        folios = design.get('folios', 'outside_head')
        odd: dict[str, str] = {'center': recto} if recto else {}
        even: dict[str, str] = {'center': verso} if verso else {}
        footer: dict[str, Any] = {}
        if folios == 'outside_head':
            odd['right'], even['left'] = '{page}', '{page}'
        elif folios == 'bottom_center':
            footer = {'odd': {'center': '{page}'}, 'even': {'center': '{page}'}}
        elif folios == 'bottom_outside':
            footer = {'odd': {'right': '{page}'}, 'even': {'left': '{page}'}}
        opener = {'center': '{page}'} if design.get('opener_folio', 'bottom_center') != 'none' else ''
        header_value = {'first': '', 'odd': odd or '', 'even': even or ''}
        footer_value = {'first': opener, 'odd': footer.get('odd', ''), 'even': footer.get('even', '')}
        return header_value, footer_value


def _paragraph(identity, text, style, **attrs):
    return {'id': identity, 'type': 'paragraph', 'text': text, 'style': style, 'marks': [], 'attrs': attrs}


def _section(identity, start, **attrs):
    return {
        'id': identity,
        'type': 'section_break',
        'text': '',
        'style': 'body',
        'marks': [],
        'attrs': {
            'start': {'odd': 'odd_page', 'next': 'next_page', 'continuous': 'continuous'}[start],
            **attrs,
        },
    }


class _Order:
    """Simulated block order so insertions can name the block they follow."""

    def __init__(self, blocks):
        self.ids = [block['id'] for block in blocks]

    def before(self, identity, block):
        index = self.ids.index(identity)
        op = {'type': 'insert_block', 'block': block, 'after': self.ids[index - 1] if index else None}
        self.ids.insert(index, block['id'])
        return op

    def after(self, identity, block):
        index = self.ids.index(identity)
        self.ids.insert(index + 1, block['id'])
        return {'type': 'insert_block', 'block': block, 'after': identity}

    def append(self, block):
        op = (
            {'type': 'insert_block', 'block': block, 'after': self.ids[-1]}
            if self.ids
            else {
                'type': 'insert_block',
                'block': block,
                'after': None,
            }
        )
        self.ids.append(block['id'])
        return op

    def remove(self, identity):
        self.ids.remove(identity)
        return {'type': 'delete_block', 'target': identity}


def _apply(doc, operations):
    from .engine import apply

    for start in range(0, len(operations), 5000):
        doc, _ = apply(doc, {'operations': operations[start : start + 5000], 'origin': 'compile'})
        doc['history'] = {'revisions': [], 'branches': {}, 'current_branch': 'main'}
    return doc


def _matter_blocks(builder, entry, values, matter_heading_level):
    """Blocks for one generated matter entry, or ``(None, reason)`` when its content is missing."""
    kind = entry['kind']
    required = MATTER_REQUIRES.get(kind)
    if required and not values.get(required) and 'text' not in entry:
        return None, f'metadata {required!r} is empty'
    blocks = []
    title = entry.get('title', MATTER_HEADINGS.get(kind, ''))
    title = fill(title, values) if title else ''
    if title and kind != 'toc':
        heading = {
            'id': builder.ident(kind + '_heading'),
            'type': 'heading',
            'text': title,
            'style': f'heading{matter_heading_level}',
            'marks': [],
            'attrs': {'level': matter_heading_level},
        }
        blocks.append(heading)
    elif title:
        blocks.append(_paragraph(builder.ident(kind + '_title'), title, 'compile_matter_heading'))
    lines = (
        [('compile_matter_text', line) for line in entry['text']]
        if 'text' in entry
        else MATTER_TEMPLATES[kind]
    )
    for style, template in lines:
        text = fill(template, values)
        if text is None:
            continue
        for part in (
            [p for p in re.split(r'\n\s*\n', text) if p.strip()] if style == 'compile_matter_text' else [text]
        ):
            blocks.append(_paragraph(builder.ident(kind), part.strip('\n'), style))
    if kind == 'toc':
        blocks.append(
            {
                'id': builder.ident('toc'),
                'type': 'toc',
                'text': '',
                'style': 'compile_toc',
                'marks': [],
                'attrs': {},
            }
        )
    if kind == 'manuscript_title':
        for block in blocks:
            if block['style'] == 'compile_title':
                block['attrs']['space_before'] = 180
    elif kind in ('half_title', 'title_page', 'dedication', 'epigraph') and blocks:
        blocks[0]['attrs']['sink'] = 144  # print only: display pages start a third of the way down
    if not blocks:
        return None, 'no line of its template has values'
    return blocks, None


def build(master: Doc, profile: dict[str, Any], target: str) -> tuple[Doc, dict[str, Any]]:
    """The transient compiled document for one target and a description of what was done."""
    if target not in TARGETS:
        raise ByaxError('unsupported_format', 'Compile targets are pdf, epub, docx and md.', target=target)
    from .statistics import counts

    builder = _Builder(master, profile, target)
    rows, own, _unassigned, mode = _rows(master)
    included, order = builder.inclusion(rows, own)
    work, excluded = _transient_copy(master)
    first = builder.phase_one(work, rows, own, included, order)
    if first:
        work = _apply(work, first)
    present = {block['id']: block for block in work['blocks']}
    # Words of the text itself: binder headings become labels, separators or nothing.
    body_words = counts(
        text_of(
            {'blocks': [b for b in work['blocks'] if b.get('type') in ('paragraph', 'footnote', 'table')]}
        )
    )['words']
    values = _values(master, body_words)
    kind_of = {row['heading']: row for row in rows if row['id'] in included and row['heading'] in present}
    operations = builder.styles(work, rows)
    operations.append({'type': 'set_layout', 'values': builder.layout(values)})
    sim = _Order(work['blocks'])
    print_target = target == 'pdf'
    heads = builder.design.get('running_heads') or {}
    per_chapter_heads = '{chapter}' in heads.get('recto', '') + heads.get('verso', '')
    restart = builder.design.get('restart_numbering', True)
    numbers = {'part': 0, 'chapter': 0}
    openers: list[tuple[str, str, bool]] = []  # (heading id, opener kind, drop cap)
    state = {'body': False}
    docx_breaks: list[str] = []
    report_items = []

    def break_before(block_id):
        index = sim.ids.index(block_id)
        return index == 0 or sim.ids[index - 1].startswith('compile_section')

    def page_start(block_id: str, start: str, chapter_title: str = '', first_body: bool = False) -> None:
        """Open a page (print: a section break; Word: page break before) at ``block_id``."""
        if first_body:
            state['body'] = True
        if start == 'continuous' and not first_body:
            return
        if print_target:
            attrs: dict[str, Any] = {}
            if first_body or per_chapter_heads:
                header, footer = builder.body_regions(values, chapter_title)
                attrs.update(header=header, footer=footer)
            if first_body:
                attrs['page_number_format'] = 'decimal'
                if restart:
                    attrs['page_number_start'] = 1
            # A leading section break is harmless: it only sets the first page's regime.
            operations.append(sim.before(block_id, _section(builder.ident('section'), start, **attrs)))
        elif target == 'docx':
            docx_breaks.append(block_id)

    def split(block_id, title=''):
        if target == 'epub' and not any(entry['id'] == block_id for entry in builder.split_at):
            builder.split_at.append({'id': block_id, 'title': title})

    # Binder headings in body order: chapter and part openers, scene breaks, front and back items.
    for heading_id in [identity for identity in sim.ids if identity in kind_of]:
        row = kind_of[heading_id]
        kind = row['kind'] if row['kind'] in ITEM_KINDS else 'back'
        spec = builder.items.get(kind, {})
        title = present[heading_id].get('text', '')
        entry: dict[str, Any] = {
            'item': row['id'],
            'heading': heading_id,
            'kind': row['kind'],
            'title': title,
        }
        if kind in ('part', 'chapter'):
            if kind == 'part' and builder.items.get('chapter', {}).get('numbering') == 'per_part':
                numbers['chapter'] = 0
            numbers[kind] += 1
            style = spec.get('number_style', 'arabic')
            default_label = 'Chapter {number}' if kind == 'chapter' else 'Part {number}'
            label = (
                ''
                if style == 'none'
                else fill(spec.get('label', default_label), {'number': format_number(numbers[kind], style)})
                or ''
            )
            generic = bool(re.fullmatch(r'(chapter|part|book)\s+[\w.-]+', title.strip(), re.IGNORECASE))
            mode_title = spec.get('title', 'auto')
            show_title = bool(title.strip()) and (
                mode_title == 'show' or (mode_title == 'auto' and not generic)
            )
            if not label and not show_title:
                show_title = bool(title.strip())
            entry.update(number=numbers[kind], label=label, show_title=show_title)
            opener = heading_id
            if label and show_title:
                block = _paragraph(
                    builder.ident(kind + '_label'),
                    label,
                    'compile_chapter_label' if kind == 'chapter' else 'compile_part_label',
                )
                operations.append(sim.before(heading_id, block))
                opener = block['id']
            elif label:
                operations.append({'type': 'set_text', 'target': heading_id, 'text': label})
            if spec.get('sink'):
                # Print sinks the opener on its page; other targets have only space before.
                key = 'sink' if print_target else 'space_before'
                operations.append({'type': 'set_attributes', 'target': opener, 'values': {key: spec['sink']}})
            page_start(
                opener, spec.get('start', 'odd' if kind == 'part' else 'next'), title, not state['body']
            )
            split(opener)
            if kind == 'chapter':
                openers.append((heading_id, 'chapter', bool(spec.get('drop_cap'))))
        elif kind == 'scene':
            mode_heading = spec.get('heading', 'separator')
            index = sim.ids.index(heading_id)
            previous = sim.ids[index - 1] if index else None
            opens = previous is None or (
                previous in kind_of and kind_of[previous]['kind'] in ('part', 'chapter')
            )
            entry.update(heading_mode=mode_heading, opens_chapter=opens)
            if mode_heading == 'keep':
                openers.append((heading_id, 'scene', False))
            elif mode_heading == 'hide' or opens:
                operations.append(sim.remove(heading_id))
                if not opens:
                    openers.append((heading_id, 'scene', False))
            else:
                operations.append(
                    {
                        'type': 'set_block',
                        'target': heading_id,
                        'values': {
                            'type': 'paragraph',
                            'text': spec.get('separator', '* * *'),
                            'style': 'compile_scene_break',
                            'marks': [],
                            'attrs': {},
                        },
                    }
                )
                openers.append((heading_id, 'scene', False))
        else:  # front and back binder items keep their text; their heading can be hidden
            anchor = heading_id
            if spec.get('heading', 'keep') == 'hide':
                operations.append(sim.remove(heading_id))
                anchor = next((i for i in own.get(row['id'], []) if i in present and i in sim.ids), None)
            if anchor:
                page_start(anchor, spec.get('start', 'odd'), title, kind == 'back' and not state['body'])
                split(anchor)
        report_items.append(entry)

    # The first paragraph after an opener or a scene break: no indent, drop cap and small-caps lead-in.
    original = [block['id'] for block in work['blocks']]
    stops = {h for h, row in kind_of.items() if row['kind'] != 'scene'}
    treated: set[str] = set()
    for heading_id, opener_kind, drop in openers:
        if heading_id in sim.ids:
            after = sim.ids[sim.ids.index(heading_id) + 1 :]
        else:  # a hidden scene heading: continue from where it was
            later = set(original[original.index(heading_id) + 1 :])
            after = [i for i in sim.ids if i in later]
        spec = builder.items.get('chapter' if opener_kind == 'chapter' else 'scene', {})
        for identity in after:
            block = present.get(identity)
            if identity in stops or identity in treated or identity in kind_of:
                break
            if block is None:
                continue
            if block.get('type') == 'heading':
                break
            if (
                block.get('type') != 'paragraph'
                or not block.get('text', '').strip()
                or block.get('attrs', {}).get('list')
            ):
                continue
            treated.add(identity)
            text = block['text']
            if block.get('style', 'body') == 'body':
                operations.append({'type': 'apply_style', 'target': identity, 'style': 'compile_first'})
            cut = drop_cap_end(text) if drop else 0
            if cut:
                operations.append(
                    {
                        'type': 'apply_character_style',
                        'target': identity,
                        'start': 0,
                        'end': cut,
                        'style': 'compile_drop_cap',
                    }
                )
                if print_target:
                    builder.losses.append(
                        _loss(
                            'compile_drop_cap_raised',
                            'PDF draws the drop cap as an enlarged raised initial; text does not wrap around it.',
                            identity,
                        )
                    )
            end = lead_in_end(text, cut, spec.get('small_caps_words', 0))
            if end > cut:
                operations.append(
                    {
                        'type': 'apply_character_style',
                        'target': identity,
                        'start': cut,
                        'end': end,
                        'style': 'compile_lead_in',
                    }
                )
            break

    # Generated front matter goes before everything; then the text before the first opener.
    front_ids: list[str] = []
    for entry in profile.get('front_matter', []):
        blocks = _matter_for(builder, entry, values, target)
        if not blocks:
            continue
        for block in blocks:
            if front_ids:
                operations.append(sim.after(front_ids[-1], block))
            else:
                operations.append({'type': 'insert_block', 'block': block, 'after': None})
                sim.ids.insert(0, block['id'])
            front_ids.append(block['id'])
        page_start(blocks[0]['id'], entry.get('start', 'next' if entry['kind'] == 'copyright' else 'odd'))
        split(blocks[0]['id'], _matter_title(entry))
    leading = next((i for i in sim.ids if i not in front_ids and not i.startswith('compile_section')), None)
    if leading is not None and front_ids and not break_before(leading):
        page_start(leading, 'odd', '', not state['body'])
        split(leading)
    elif leading is not None and not state['body']:
        page_start(leading, 'odd', '', True)
    if profile.get('end_mark'):
        operations.append(
            sim.append(_paragraph(builder.ident('end'), profile['end_mark'], 'compile_end_mark'))
        )
    for entry in profile.get('back_matter', []):
        blocks = _matter_for(builder, entry, values, target)
        if not blocks:
            continue
        for block in blocks:
            operations.append(sim.append(block))
        page_start(blocks[0]['id'], entry.get('start', 'odd'), blocks[0].get('text', ''))
        split(blocks[0]['id'])
    for block_id in dict.fromkeys(docx_breaks):
        if sim.ids.index(block_id):  # never a page break before the first page
            operations.append(
                {'type': 'set_attributes', 'target': block_id, 'values': {'page_break_before': True}}
            )
    compiled = _apply(work, operations) if operations else work
    info = {
        'target': target,
        'structure': mode,
        'items': report_items,
        'included': [row['id'] for row in rows if row['id'] in included],
        'excluded_items': [row['id'] for row in rows if row['id'] not in included],
        'excluded': excluded,
        'matter': builder.notes,
        'split_at': builder.split_at,
        'losses': builder.losses,
        'values': values,
        'words': body_words,
    }
    return compiled, info


def _matter_title(entry):
    return (
        entry.get('title')
        or MATTER_HEADINGS.get(entry['kind'])
        or entry['kind'].replace('_', ' ').capitalize()
    )


def _matter_for(builder, entry, values, target):
    """Blocks of one matter entry for this target, recording why an entry was skipped."""
    if entry.get('targets') and target not in entry['targets']:
        return None
    if entry['kind'] == 'toc' and target != 'pdf':
        reason = 'the EPUB navigation document is the contents' if target == 'epub' else 'no page numbers'
        builder.notes.append({'kind': 'toc', 'skipped': reason})
        return None
    blocks, reason = _matter_blocks(builder, entry, values, 1)
    if blocks is None:
        builder.notes.append({'kind': entry['kind'], 'skipped': reason})
        return None
    builder.notes.append({'kind': entry['kind'], 'blocks': [b['id'] for b in blocks]})
    return blocks


def compiled_digest(compiled: Doc) -> str:
    """Content hash of a compiled document, excluding the transient revision identity."""
    return object_hash({k: v for k, v in compiled.items() if k not in ('revision', 'history', 'requests')})


# Preflight -------------------------------------------------------------------------------------------


def _image_dpi(compiled, block, body_width):
    import base64

    from .layout import _decode_image, _image_size, ensure_app

    asset = compiled.get('assets', {}).get(block.get('attrs', {}).get('asset'))
    if not asset:
        return None
    ensure_app()
    try:
        image = _decode_image(base64.b64decode(asset.get('data', ''), validate=True))
    except (ValueError, TypeError):
        return None
    if image.isNull():
        return None
    width, _height = _image_size(block.get('attrs', {}), image, body_width)
    return image.width() / (width / 72.0) if width > 0 else None


def _used_faces(compiled):
    """(family, bold, italic) faces the compiled text uses through styles and marks."""
    from .formatting import style_values

    faces = set()
    styles = compiled.get('styles', {})

    def family_of(values):
        return values.get('font_family') or values.get('font')

    for block in compiled['blocks']:
        if block.get('type') not in ('paragraph', 'heading', 'footnote'):
            continue
        name = block.get('style', 'body')
        try:
            values = style_values(compiled, name) if name in styles else {}
        except ByaxError:
            values = {}
        values = {
            **values,
            **{k: v for k, v in block.get('attrs', {}).items() if k in ('font_family', 'bold', 'italic')},
        }
        family = family_of(values) or 'DejaVu Serif'
        bold = bool(values.get('bold', block.get('type') == 'heading'))
        italic = bool(values.get('italic'))
        if block.get('text'):
            faces.add((family, bold, italic))
        for mark in block.get('marks', []):
            kind, value = mark.get('type'), mark.get('value', True)
            if kind in ('bold', 'strong') and value:
                faces.add((family, True, italic))
            elif kind in ('italic', 'emphasis') and value:
                faces.add((family, bold, True))
            elif kind in ('font_family', 'font') and isinstance(value, str):
                faces.add((value, bold, italic))
            elif kind == 'character_style' and value in styles:
                try:
                    character = style_values(compiled, value)
                except ByaxError:
                    continue
                faces.add(
                    (
                        family_of(character) or family,
                        bool(character.get('bold', bold)),
                        bool(character.get('italic', italic)),
                    )
                )
    return sorted(faces)


def preflight(
    master: Doc,
    compiled: Doc,
    info: dict[str, Any],
    profile: dict[str, Any],
    target: str,
    *,
    page_starts: list[str | None] | None = None,
) -> dict[str, Any]:
    """Findings before (or after) writing: metadata, binder, content, images, fonts, page breaks."""
    from . import project
    from .epub import _language
    from .layout import geometry

    findings = []
    meta = master.get('metadata', {})
    for key, label in (('title', 'title'), ('author', 'author')):
        value = meta.get(key)
        if (
            not isinstance(value, str)
            or not value.strip()
            or (key == 'title' and value.strip() == 'Untitled')
        ):
            findings.append(
                _finding(
                    'compile.metadata_missing',
                    'warning',
                    f'The document has no {label}; set it in Document properties before publishing.',
                    'metadata/' + key,
                    {'field': key},
                )
            )
    language = meta.get('language')
    if not isinstance(language, str) or _language(language) is None:
        findings.append(
            _finding(
                'compile.metadata_missing',
                'warning',
                'The document language is missing or not a BCP 47 tag; readers and hyphenation rely on it.',
                'metadata/language',
                {'field': 'language', 'value': language if isinstance(language, str) else None},
            )
        )
    if isinstance(master.get('project'), dict):
        report = project.reconcile(master)
        for orphan in report['orphaned']:
            findings.append(
                _finding(
                    'compile.binder_orphan',
                    'warning',
                    'A binder item lost its heading and is not compiled; rebind or remove it.',
                    orphan['item'],
                    {'heading': orphan['heading'], 'reason': orphan['reason']},
                )
            )
        for heading in report['unbound_headings']:
            findings.append(
                _finding(
                    'compile.unbound_heading',
                    'advisory',
                    'A heading is not a binder item; it is compiled as a subheading of the item that contains it.',
                    heading['id'],
                )
            )
    from .statistics import counts

    rows, _own, _unassigned, _mode = _rows(master)
    included = set(info['included']) - set(info['excluded_items'])
    # Words below a heading, not counting the headings of the item and its descendants.
    heading_words = {row['id']: counts(row['title'])['words'] for row in rows}
    parents = {row['id']: row['parent'] for row in rows}
    for row in reversed(rows):
        if parents[row['id']] is not None:
            heading_words[parents[row['id']]] += heading_words[row['id']]
    for row in rows:
        text_words = row['total_words'] - heading_words[row['id']]
        if row['id'] in included and row['kind'] in ('chapter', 'scene', 'part') and text_words <= 0:
            findings.append(
                _finding(
                    'compile.empty_item',
                    'warning',
                    f'The {row["kind"]} "{row["title"][:60]}" has no text.',
                    row['heading'],
                    {'kind': row['kind'], 'item': row['id']},
                )
            )
    if not any(
        b.get('text', '').strip()
        for b in compiled['blocks']
        if b.get('type') in ('paragraph', 'heading') and not b['id'].startswith('compile_')
    ):
        findings.append(
            _finding('compile.nothing_included', 'error', 'The compile includes no text.', 'project')
        )
    blocks = compiled['blocks']
    for index, block in enumerate(blocks):
        if block.get('type') != 'heading' or not block.get('text', '').strip():
            continue
        level = block.get('attrs', {}).get('level', 1)
        following = next(
            (b for b in blocks[index + 1 :] if b.get('type') not in ('section_break', 'page_break')), None
        )
        if following is None or (
            following.get('type') == 'heading' and following.get('attrs', {}).get('level', 1) <= level
        ):
            findings.append(
                _finding(
                    'compile.heading_without_content',
                    'warning',
                    'A heading is followed by no text before the next heading of the same or a higher level.',
                    block['id'],
                )
            )
    width, _height, margins = geometry(compiled)
    body_width = width - margins['left'] - margins['right']
    required_dpi = profile.get('design', {}).get('image_dpi', 300)
    for block in blocks:
        if block.get('type') != 'image':
            continue
        attrs = block.get('attrs', {})
        if not str(attrs.get('alt', '')).strip() and not attrs.get('decorative'):
            findings.append(
                _finding(
                    'compile.image_alt_missing',
                    'warning',
                    'An image has no alternative text; add it or mark the image decorative.',
                    block['id'],
                )
            )
        if attrs.get('asset') not in compiled.get('assets', {}):
            findings.append(
                _finding(
                    'compile.image_missing',
                    'warning',
                    'An image has no embedded picture to print.',
                    block['id'],
                )
            )
            continue
        if target == 'pdf':
            dpi = _image_dpi(compiled, block, body_width)
            if dpi is not None and dpi < required_dpi:
                findings.append(
                    _finding(
                        'compile.image_resolution',
                        'warning',
                        f'The image prints at {dpi:.0f} pixels per inch; {required_dpi} are needed at this trim size.',
                        block['id'],
                        {'dpi': round(dpi, 1), 'required': required_dpi},
                    )
                )
    if target in ('pdf', 'epub'):
        from .fonts import inspect_font

        embed = target == 'pdf' or profile.get('epub', {}).get('embed_fonts', True)
        for family, bold, italic in _used_faces(compiled):
            info_font = inspect_font(family, bold, italic)
            face = ('bold ' if bold else '') + ('italic' if italic else '') or 'regular'
            measured = {'family': family, 'face': face.strip(), 'resolved': info_font.get('resolved')}
            if info_font.get('substituted') or info_font.get('status') == 'unavailable':
                findings.append(
                    _finding(
                        'compile.font_unavailable',
                        'warning',
                        f'Font {family} ({face.strip()}) is not installed; {info_font.get("resolved")} is used instead.',
                        'styles',
                        measured,
                    )
                )
            elif embed and not info_font.get('embeddable'):
                findings.append(
                    _finding(
                        'compile.font_not_embeddable',
                        'warning',
                        f'Font {family} ({face.strip()}) declares embedding permission '
                        f'{info_font.get("embedding")!r}; it may not be embedded in the {target.upper()}.',
                        'styles',
                        {**measured, 'embedding': info_font.get('embedding')},
                    )
                )
    if page_starts:
        findings.extend(orphan_headings(compiled, page_starts))
    review = info['excluded']
    pending = {k: review[k] for k in ('comments', 'proposals', 'tracked_changes') if review.get(k)}
    if pending:
        findings.append(
            _finding(
                'compile.review_excluded',
                'advisory',
                'Comments, proposals and tracked changes are not compiled; the text is compiled as it stands.',
                'review',
                pending,
            )
        )
    for note in info['matter']:
        if note.get('skipped') and note['kind'] == 'manuscript_title':
            findings.append(
                _finding(
                    'compile.matter_skipped',
                    'warning',
                    'The manuscript title page has no content.',
                    'metadata',
                )
            )
    if any(e.get('kind') == 'manuscript_title' for e in profile.get('front_matter', [])):
        values = info['values']
        if not (values.get('contact_email') or values.get('contact_address') or values.get('contact_phone')):
            findings.append(
                _finding(
                    'compile.contact_missing',
                    'advisory',
                    'Standard manuscript format puts your contact details on the first page; '
                    'set metadata contact (name, address, email, phone).',
                    'metadata/contact',
                )
            )
    return _preflight_report(findings, target, paginated=bool(page_starts))


PREFLIGHT_RULES = (
    'compile.metadata_missing',
    'compile.binder_orphan',
    'compile.empty_item',
    'compile.heading_without_content',
    'compile.image_alt_missing',
    'compile.image_resolution',
    'compile.font_unavailable',
    'compile.orphan_heading',
)


def _preflight_report(findings: list[dict[str, Any]], target: str, *, paginated: bool) -> dict[str, Any]:
    findings = sorted(
        findings,
        key=lambda f: ({'error': 0, 'warning': 1, 'advisory': 2}[f['severity']], f['rule'], f['scope']),
    )
    summary = {
        'total': len(findings),
        'errors': sum(f['severity'] == 'error' for f in findings),
        'warnings': sum(f['severity'] == 'warning' for f in findings),
        'advisory': sum(f['severity'] == 'advisory' for f in findings),
    }
    coverage = []
    for rule in PREFLIGHT_RULES:
        entry: dict[str, Any] = {'suite': 'compile', 'rule': rule, 'status': 'completed'}
        if rule == 'compile.orphan_heading' and target == 'pdf' and not paginated:
            entry.update(status='skipped', reason='Page breaks are known only after pagination.')
        elif rule == 'compile.orphan_heading' and target != 'pdf':
            entry.update(status='skipped', reason='Reflowable or editable targets have no fixed pages.')
        elif rule == 'compile.image_resolution' and target != 'pdf':
            entry.update(status='skipped', reason='Print resolution applies to PDF only.')
        coverage.append(entry)
    return {
        'validation': 'failed' if summary['errors'] else 'passed',
        'findings': findings,
        'summary': summary,
        'coverage': coverage,
    }


def orphan_headings(compiled: Doc, page_starts: list[str | None]) -> list[dict[str, Any]]:
    """Headings, chapter labels and scene breaks left at the foot of a page, from the page starts."""
    blocks = compiled['blocks']
    order = [b['id'] for b in blocks]
    index = {identity: i for i, identity in enumerate(order)}
    findings = []
    for page, identity in enumerate(page_starts[1:], start=2):
        if identity is None or identity not in index or index[identity] == 0:
            continue
        previous = blocks[index[identity] - 1]
        style = previous.get('style')
        if previous.get('type') == 'heading' or style in (
            'compile_chapter_label',
            'compile_part_label',
            'compile_scene_break',
        ):
            what = 'A scene break' if style == 'compile_scene_break' else 'A heading'
            findings.append(
                _finding(
                    'compile.orphan_heading',
                    'warning',
                    f'{what} ends page {page - 1} and the text it introduces starts on page {page}.',
                    previous['id'],
                    {'page': page - 1},
                )
            )
    return findings


# Round-trip verification ------------------------------------------------------------------------------

_COMPARED_MARKS = (
    'bold',
    'italic',
    'underline',
    'strikethrough',
    'superscript',
    'subscript',
    'character_style',
)
_COMPARED_ATTRS = (
    'alignment',
    'first_line_indent',
    'indent',
    'space_before',
    'space_after',
    'line_spacing',
    'page_break_before',
)
_COMPARED_STYLE = (
    'font_family',
    'font_size',
    'bold',
    'italic',
    'alignment',
    'line_spacing',
    'first_line_indent',
    'space_before',
    'space_after',
)


def _unit(block):
    kind = block.get('type')
    if kind in ('page_break', 'section_break', 'toc'):
        return ('paragraph', '')
    if kind == 'footnote':
        return ('paragraph', block.get('text', ''))
    if kind == 'table':
        return (
            'table',
            '\t'.join(c.get('text', '') for r in block.get('rows', []) for c in r.get('cells', [])),
        )
    if kind == 'image':
        return ('image', '')
    return (kind, block.get('text', ''))


def _mark_cover(block: dict[str, Any]) -> dict[tuple[str, Any], set[int]]:
    covered: dict[tuple[str, Any], set[int]] = {}
    for mark in block.get('marks', []):
        kind = mark.get('type')
        kind = 'strikethrough' if kind == 'strike' else kind
        if kind not in _COMPARED_MARKS or not mark.get('value', True):
            continue
        value = mark.get('value', True) if kind == 'character_style' else True
        covered.setdefault((kind, value), set()).update(range(mark.get('start', 0), mark.get('end', 0)))
    return {key: value for key, value in covered.items() if value}


def _style_view(doc, name):
    from .formatting import style_values

    if name not in doc.get('styles', {}):
        return {}
    try:
        values = style_values(doc, name)
    except ByaxError:
        values = dict(doc['styles'][name])
    return {k: values[k] for k in _COMPARED_STYLE if k in values}


def roundtrip_differences(compiled: Doc, reimported: Doc, fmt: str) -> list[dict[str, Any]]:
    """Every difference between the compiled document and its re-import, located in the compiled one."""
    differences = []

    def add(field, message, target, expected=None, actual=None):
        differences.append(
            _loss(
                'roundtrip_difference',
                f'{fmt.upper()} round trip: {message}',
                target,
                field=field,
                expected=expected,
                actual=actual,
            )
        )

    meta_a, meta_b = compiled.get('metadata', {}), reimported.get('metadata', {})
    for key in ('title', 'author'):
        if fmt == 'docx' and (meta_a.get(key) or '') != (meta_b.get(key) or ''):
            add(
                'metadata.' + key,
                f'document {key} differs.',
                'metadata/' + key,
                meta_a.get(key),
                meta_b.get(key),
            )
    if fmt == 'docx':
        layout_a, layout_b = compiled.get('layout', {}), reimported.get('layout', {})
        for key in ('width', 'height'):
            if abs(float(layout_a.get(key, 0)) - float(layout_b.get(key, 0))) > 0.5:
                add('layout.' + key, f'page {key} differs.', 'layout', layout_a.get(key), layout_b.get(key))
        for key in ('top', 'right', 'bottom', 'left'):
            a = float(layout_a.get('margins', {}).get(key, 0))
            b = float(layout_b.get('margins', {}).get(key, 0))
            if abs(a - b) > 0.5:
                add('layout.margins.' + key, f'{key} margin differs.', 'layout', a, b)
        for key in ('header', 'footer', 'header_align', 'footer_align'):
            region_a = layout_a.get(key) or ('' if not key.endswith('align') else None)
            region_b = layout_b.get(key) or ('' if not key.endswith('align') else None)
            if key.endswith('align') and not (layout_a.get(key[:-6]) or layout_b.get(key[:-6])):
                continue
            if region_a != region_b:
                add(
                    'layout.' + key,
                    f'the page {key.replace("_", " ")} differs.',
                    'layout/' + key,
                    region_a,
                    region_b,
                )
    blocks_a = compiled['blocks']
    blocks_b = reimported['blocks']
    keys_a = [_unit(b) for b in blocks_a]
    keys_b = [_unit(b) for b in blocks_b]
    matcher = difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False)
    style_pairs: dict[tuple[str, str], str] = {}

    def compare(a, b):
        """One compiled block against the block it re-imported as."""
        kind = a.get('type')
        (kind_a, text_a), (kind_b, text_b) = _unit(a), _unit(b)
        if kind in ('page_break', 'section_break', 'toc', 'footnote') or kind_a != kind_b:
            add(
                'type',
                f'a {kind.replace("_", " ")} re-imports as a {b.get("type")}.',
                a['id'],
                kind,
                b.get('type'),
            )
        if text_a != text_b:
            if text_a.strip() == text_b.strip():
                add(
                    'text.whitespace',
                    'leading or trailing spaces are not kept.',
                    a['id'],
                    text_a[-20:],
                    text_b[-20:],
                )
            else:
                add('text', 'the text differs.', a['id'], text_a[:80], text_b[:80])
        if kind not in ('paragraph', 'heading') or kind_a != kind_b:
            return
        if kind == 'heading' and a.get('attrs', {}).get('level', 1) != b.get('attrs', {}).get('level', 1):
            add(
                'level',
                'heading level differs.',
                a['id'],
                a['attrs'].get('level', 1),
                b['attrs'].get('level', 1),
            )
        cover_a, cover_b = _mark_cover(a), _mark_cover(b)
        if text_a.strip() == text_b.strip():
            shift = len(text_a) - len(text_a.lstrip()) - (len(text_b) - len(text_b.lstrip()))
            cover_b = {key: {i + shift for i in value} for key, value in cover_b.items()}
        for key in sorted(set(cover_a) | set(cover_b), key=str):
            if cover_a.get(key, set()) != cover_b.get(key, set()):
                label = key[0] if key[0] != 'character_style' else f'character style {key[1]}'
                add(
                    'marks.' + key[0],
                    f'{label} formatting differs.',
                    a['id'],
                    len(cover_a.get(key, ())),
                    len(cover_b.get(key, ())),
                )
        attrs_a, attrs_b = a.get('attrs', {}), b.get('attrs', {})
        for key in _COMPARED_ATTRS:
            va, vb = attrs_a.get(key), attrs_b.get(key)
            if _close(va, vb):
                continue
            add('attrs.' + key, f'paragraph {key.replace("_", " ")} differs.', a['id'], va, vb)
        style_pairs.setdefault((a.get('style', 'body'), b.get('style', 'Normal')), a['id'])

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal' or (tag == 'replace' and i2 - i1 == j2 - j1):
            for a, b in zip(blocks_a[i1:i2], blocks_b[j1:j2]):
                compare(a, b)
            continue
        for a in blocks_a[i1:i2]:
            add(
                'content',
                f'a {a.get("type")} is missing or changed after re-import.',
                a['id'],
                _unit(a)[1][:80],
                None,
            )
        if j2 > j1:
            anchor = blocks_a[i1 - 1]['id'] if i1 else (blocks_a[0]['id'] if blocks_a else 'document')
            for b in blocks_b[j1:j2]:
                add(
                    'content',
                    f'an extra {b.get("type")} appears after re-import.',
                    anchor,
                    None,
                    _unit(b)[1][:80],
                )
    for (name_a, name_b), target in sorted(style_pairs.items()):
        view_a, view_b = _style_view(compiled, name_a), _style_view(reimported, name_b)
        for key in sorted(set(view_a) | set(view_b)):
            va, vb = view_a.get(key), view_b.get(key)
            if not _close(va, vb):
                add(
                    f'styles.{name_a}.{key}',
                    f'style {name_a} {key.replace("_", " ")} differs.',
                    target,
                    va,
                    vb,
                )
    return differences


def _close(first, second):
    """Equal, both unset (None, False or zero), or numbers within 0.05 pt."""
    if first in (None, False, 0) and second in (None, False, 0):
        return True
    numbers = (int, float)
    if isinstance(first, numbers) and isinstance(second, numbers) and not isinstance(first, bool):
        return abs(first - second) < 0.05
    return first == second


# Running a compile ---------------------------------------------------------------------------------


def _target_for(target):
    target = str(target or '').lower().lstrip('.')
    target = 'md' if target == 'markdown' else target
    if target not in TARGETS:
        raise ByaxError('unsupported_format', 'Compile targets are pdf, epub, docx and md.', target=target)
    return target


def _epub_options(master, profile, info):
    from .epub import _modified

    options = copy.deepcopy(profile.get('epub', {}))
    # The modification date comes from the master, never from the transient copy's clock.
    options['modified'] = _modified(master, {'modified': options.get('modified')})[0]
    options['split_at'] = info['split_at']
    options['split_level'] = 0
    options.setdefault('toc_title', 'Contents')
    return options


def _page_starts(resolved):
    return [
        next((f['ids'][0] for f in page['fragments'] if f['kind'] == 'body' and f.get('ids')), None)
        for page in resolved['pages']
    ]


def _master_digest(master):
    return object_hash({k: v for k, v in master.items() if k not in ('history', 'requests')})


def compile_document(
    master: Doc,
    out: str | Path | None = None,
    *,
    profile: dict[str, Any] | None = None,
    preset: str | None = None,
    target: str | None = None,
    preflight_only: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
    owner: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile ``master`` for one target and return the compile report; the master is never changed.

    ``dry_run`` builds the compiled document and previews the exporter's losses without layout or
    output; ``preflight_only`` also paginates PDF to find headings stranded at the foot of a page.
    Preflight errors refuse the compile before anything is written.
    """
    import time

    from .exchange import conversion_preview, export_document

    started = time.perf_counter()
    if profile is None:
        profile, source = resolve_profile(master, preset)
    profile = validate_profile(profile)
    suffix = Path(out).suffix.lower().lstrip('.') if out is not None else ''
    known_suffix = _target_for(suffix) if suffix in ('pdf', 'epub', 'docx', 'md', 'markdown') else None
    target = _target_for(target or known_suffix or profile.get('target', 'pdf'))
    if known_suffix and known_suffix != target and not (dry_run or preflight_only):
        raise ByaxError(
            'invalid_input', 'The output extension does not match the compile target.', field='out'
        )
    digest = _master_digest(master)
    compiled, info = build(master, profile, target)
    data: dict[str, Any] = {
        'engine': ENGINE,
        'preset': source or {'source': 'profile'},
        'target': target,
        'structure': info['structure'],
        'items': info['items'],
        'included': info['included'],
        'excluded_items': info['excluded_items'],
        'excluded': info['excluded'],
        'matter': info['matter'],
        'words': info['words'],
        'compiled': {'blocks': len(compiled['blocks']), 'sha256': compiled_digest(compiled)},
        'profile': profile,
    }
    if target == 'pdf':
        layout = compiled['layout']
        data['design'] = {
            'trim': [layout['width'], layout['height']],
            'margins': layout['margins'],
            'gutter': layout.get('gutter', 0),
            'mirror_margins': layout.get('mirror_margins', False),
            'bleed': layout.get('bleed', 0),
            'crop_marks': layout.get('crop_marks', False),
        }
    report: dict[str, Any] = {'compile': data, 'losses': list(info['losses']), 'outputs': []}
    page_starts = None
    if preflight_only and target == 'pdf':
        from .layout import build_document, layout_signature, resolve_layout

        resolved = resolve_layout(build_document(compiled), compiled)
        page_starts = _page_starts(resolved)
        data.update(
            layout_signature=layout_signature(resolved),
            page_count=len(resolved['pages']),
            page_starts=page_starts,
        )
    checks = preflight(master, compiled, info, profile, target, page_starts=page_starts)
    if dry_run or preflight_only:
        if dry_run:
            preview_profile: dict[str, Any] = {'allow_markdown_losses': True}
            if target == 'epub':
                preview_profile['epub'] = _epub_options(master, profile, info)
            report['losses'].extend(conversion_preview(compiled, target, preview_profile).get('losses', []))
        report.update(dry_run=dry_run, preflight_only=preflight_only)
    else:
        if out is None:
            raise ByaxError('invalid_input', 'An output path is required unless previewing.', field='out')
        if checks['validation'] == 'failed':
            raise ByaxError(
                'failed_requirement',
                'Preflight found errors; nothing was written.',
                findings=[f for f in checks['findings'] if f['severity'] == 'error'],
            )
        destination = Path(out)
        if target == 'epub':
            from .epub import export_epub

            exported = export_epub(
                compiled, destination, _epub_options(master, profile, info), overwrite=overwrite, owner=owner
            )
        else:
            settings = {'allow_markdown_losses': True} if target == 'md' else {}
            exported = export_document(
                compiled, destination, format=target, profile=settings, overwrite=overwrite, owner=owner
            )
        report['losses'].extend(exported.get('losses', []))
        report['outputs'] = exported.get('outputs', [])
        for key in ('epub', 'structure_check', 'markdown', 'font_dependencies', 'print'):
            if key in exported:
                data[key] = exported[key]
        if target == 'pdf':
            page_starts = exported.get('page_starts')
            data.update(
                layout_signature=exported.get('layout_signature'),
                page_count=exported.get('page_count'),
                page_starts=page_starts,
            )
            checks = _preflight_report(
                checks['findings'] + orphan_headings(compiled, page_starts or []), target, paginated=True
            )
        if target in ('docx', 'md'):
            from .exchange import import_document

            reimported, imported = import_document(destination)
            differences = roundtrip_differences(compiled, reimported, target)
            located = {loss.get('target') for loss in report['losses']}
            report['losses'].extend(differences)
            data['roundtrip'] = {
                'format': target,
                'compared_blocks': len(compiled['blocks']),
                'reimported_blocks': len(reimported['blocks']),
                'differences': len(differences),
                'already_located_by_exporter': sum(d['target'] in located for d in differences),
                'import_losses': imported.get('losses', []),
            }
    if _master_digest(master) != digest:  # pragma: no cover - defensive: compile never mutates the master
        raise ByaxError('execution_failure', 'Compile changed the master document.')
    report['preflight'] = checks
    report['findings'] = checks['findings']
    report['coverage'] = checks['coverage']
    report['validation'] = checks['validation']
    report['review'] = 'required' if report['losses'] or checks['summary']['warnings'] else 'not_required'
    data['seconds'] = round(time.perf_counter() - started, 3)
    return report


def compile_file(
    path: str | Path,
    out: str | Path | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Load a ``.byax`` master and compile it; see :func:`compile_document`."""
    from .storage import load_document

    return compile_document(load_document(path), out, **options)


def describe(report: dict[str, Any]) -> str:
    """One-paragraph plain-language summary of a compile report (desktop status and dialogs)."""
    data = report.get('compile', {})
    parts = [f'{data.get("target", "").upper()} compiled from {len(data.get("included", []))} binder items']
    if data.get('page_count'):
        parts.append(f'{data["page_count"]} pages')
    summary = report.get('preflight', {}).get('summary', {})
    if summary:
        parts.append(f'{summary.get("warnings", 0)} preflight warnings')
    parts.append(f'{len(report.get("losses", []))} located losses')
    return ', '.join(parts) + '.'


# CLI (dispatched through cli.execute, so also MCP and REST) ----------------------------------------

ACTIONS = ('run', 'presets', 'show', 'save-preset', 'export-preset')


def _command_profile(doc: Doc, a: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    overrides = copy.deepcopy(a.get('input') or {})
    if not isinstance(overrides, dict):
        _fail('input is a partial compile profile object.', 'input')
    if a.get('trim'):
        overrides = merge(overrides, {'design': {'trim': a['trim']}})
    if a.get('include') is not None:
        overrides['include'] = list(a['include'])
    if a.get('exclude'):
        overrides['exclude'] = list(overrides.get('exclude', [])) + list(a['exclude'])
    if a.get('target'):
        overrides['target'] = _target_for(a['target'])
    return resolve_profile(
        doc, a.get('preset'), preset_file=a.get('preset_file'), overrides=overrides or None
    )


def compile_command(a: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    from . import engine
    from .cli import _destination, _load, _mutate, envelope

    action = a.get('action', 'run')
    if action not in ACTIONS:
        raise ByaxError('invalid_input', f'Unknown compile action: {action}', supported=list(ACTIONS))
    if action == 'presets':
        doc = _load(a['path'])
        return envelope({'presets': list_presets(doc)}, doc)
    if action in ('show', 'export-preset'):
        doc = _load(a['path'])
        profile, source = _command_profile(doc, a)
        if action == 'show':
            return envelope({'profile': profile, 'preset': source}, doc)
        if not a.get('out'):
            _fail('export-preset requires --out (a .json settings collection).', 'out')
        _destination(a['out'], a.get('overwrite', False), a['path'])
        from .profiles import write_collection

        written = write_collection(preset_collection(a.get('name') or source['name'], profile), a['out'])
        return envelope({'collection': written, 'preset': source}, doc, outputs=[written['path']])
    if action == 'save-preset':
        if not a.get('name'):
            _fail('save-preset requires --name.', 'name')
        request: dict[str, Any] = {}
        for option, key in (('expect_revision', 'expected_revision'), ('request_id', 'request_id')):
            if a.get(option) is not None:
                request[key] = a[option]

        def save(doc):
            profile, _source = _command_profile(doc, a)
            request['operations'] = save_preset_operations(doc, a['name'], profile)
            candidate, result = engine.apply(doc, request, dry_run=a.get('dry_run', False))
            result['presets'] = list_presets(candidate)
            return candidate, result, not a.get('dry_run', False)

        return _mutate(a['path'], save)
    doc = _load(a['path'])
    profile, source = _command_profile(doc, a)
    previewing = a.get('dry_run', False) or a.get('preflight_only', False)
    if not previewing:
        if not a.get('out'):
            _fail('compile requires --out unless --dry-run or --preflight-only is given.', 'out')
        _destination(a['out'], a.get('overwrite', False), a['path'])
    report = compile_document(
        doc,
        a.get('out'),
        profile=profile,
        source=source,
        target=a.get('target'),
        preflight_only=a.get('preflight_only', False),
        dry_run=a.get('dry_run', False),
        overwrite=a.get('overwrite', False),
        owner=owner,
    )
    if a.get('strict'):
        report['strict'] = True
    return envelope(report, doc)
