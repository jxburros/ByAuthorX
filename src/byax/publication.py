"""Publication policies, deliberately destructive sanitization of a COPY, and reproduction records."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import platform
import re
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from .errors import ByaxError
from .model import new_document


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()


def _text_targets(doc):
    for block in doc['blocks']:
        if block.get('type') == 'table':
            for row in block.get('rows', []):
                yield from row.get('cells', [])
        else:
            yield block


PRESERVABLE = frozenset({'styles', 'layout', 'images'})
# Formatting-only block attributes that may follow preserved styles.
_FORMAT_ATTRS = frozenset(
    {
        'alignment',
        'list',
        'level',
        'start',
        'restart',
        'space_before',
        'space_after',
        'indent',
        'first_line_indent',
        'line_spacing',
        'page_break_before',
        'keep_together',
        'keep_with_next',
        'widow_control',
        'orphan_control',
        'header_rows',
        'column_widths',
        'rowspan',
        'colspan',
        'width',
        'height',
        'wrap',
    }
)


def _term_encodings(terms):
    for term in terms:
        for encoding in ('utf-8', 'utf-16-le', 'utf-16-be'):
            yield term.encode(encoding)
        try:
            yield term.encode('latin-1')
        except UnicodeEncodeError:
            pass


def _contains_term(value, terms):
    return any(term in text for text in _string_values(value) for term in terms) or (
        isinstance(value, dict)
        and any(isinstance(key, str) and term in key for key in value for term in terms)
    )


def sanitize(doc: dict, targets: list, *, terms: list[str] | None = None, preserve=None) -> tuple[dict, dict]:
    """Rebuild a master with selected text removed everywhere.

    targets contains IDs or {target,start,end} scalar ranges. Review, history,
    sources, metadata, embedded audio, fields, layers and arbitrary extension data
    are excluded by reconstruction, never merely hidden. ``preserve`` may opt in to
    keeping named styles, page layout and embedded images; each is kept only where
    the redacted terms can be verified absent. The original is untouched.
    """
    if preserve is not None and not isinstance(preserve, (list, tuple, set)):
        raise ByaxError(
            'invalid_input', 'Preserve must be a list of categories.', supported=sorted(PRESERVABLE)
        )
    preserve = set(preserve or [])
    if preserve - PRESERVABLE:
        raise ByaxError(
            'invalid_input',
            'Preserve may contain only styles, layout and images.',
            supported=sorted(PRESERVABLE),
        )
    terms = list(terms or [])
    source = copy.deepcopy(doc)
    indexed = {node['id']: node for node in _text_targets(source)}
    selected = []
    for target in targets:
        selector = {'target': target} if isinstance(target, str) else target
        if not isinstance(selector, dict) or set(selector) - {'target', 'start', 'end'}:
            raise ByaxError(
                'invalid_input', 'A redaction selector must contain target and optional start/end.'
            )
        node = indexed.get(selector.get('target'))
        if node is None or 'text' not in node:
            raise ByaxError(
                'invalid_input',
                'Redaction target must identify a text block or table cell.',
                target=selector.get('target'),
            )
        text = node['text']
        start, end = selector.get('start', 0), selector.get('end', len(text))
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            raise ByaxError('invalid_input', 'Redaction range must be a nonempty scalar-value range.')
        import regex

        boundaries = {0, *(match.end() for match in regex.finditer(r'\X', text))}
        if start not in boundaries or end not in boundaries:
            raise ByaxError('invalid_input', 'Redaction boundaries must not split grapheme clusters.')
        terms.append(text[start:end])
        selected.append(node['id'])
    if not terms or any(not isinstance(term, str) or not term for term in terms):
        raise ByaxError('invalid_input', 'Provide at least one nonempty redaction target or term.')
    terms = sorted(set(terms), key=len, reverse=True)
    pattern = re.compile('|'.join(re.escape(term) for term in terms))
    occurrences = 0

    def scrub(text):
        nonlocal occurrences
        result, count = pattern.subn('[REDACTED]', text)
        occurrences += count
        return result

    clean = new_document('Sanitized document')
    clean['blocks'] = []
    removed_objects, dropped_styles, dropped_assets, preserved_assets = 0, [], [], []
    if 'styles' in preserve:
        for name, values in source.get('styles', {}).items():
            if _contains_term({name: values}, terms):
                dropped_styles.append(hashlib.sha256(name.encode()).hexdigest())
            else:
                clean['styles'][name] = copy.deepcopy(values)
    if 'layout' in preserve:

        def scrub_values(value):
            if isinstance(value, str):
                return scrub(value)
            if isinstance(value, dict):
                return {key: scrub_values(item) for key, item in value.items()}
            if isinstance(value, list):
                return [scrub_values(item) for item in value]
            return value

        clean['layout'] = scrub_values(copy.deepcopy(source.get('layout', clean['layout'])))
    encoded = list(_term_encodings(terms))

    def formatting(attrs):
        if 'styles' not in preserve:
            return {}
        return {
            key: copy.deepcopy(value)
            for key, value in attrs.items()
            if key in _FORMAT_ATTRS and not _contains_term(value, terms)
        }

    for block in source['blocks']:
        kind = block.get('type')
        if kind == 'image' and 'images' in preserve:
            attrs = block.get('attrs', {})
            asset = source.get('assets', {}).get(attrs.get('asset'))
            raw = base64.b64decode(asset.get('data', '')) if isinstance(asset, dict) else b''
            # Byte scans cannot read pixels; images whose bytes carry a term are removed.
            if not raw or any(needle in raw for needle in encoded):
                if raw:
                    dropped_assets.append(attrs.get('asset'))
                removed_objects += 1
                continue
            digest = hashlib.sha256(raw).hexdigest()
            clean['assets'][digest] = {
                'data': base64.b64encode(raw).decode('ascii'),
                'mime': asset.get('mime', 'application/octet-stream'),
                'name': 'image',
            }
            preserved_assets.append(digest)
            image_attrs = {
                key: copy.deepcopy(attrs[key])
                for key in ('width', 'height', 'wrap', 'alignment')
                if key in attrs and not _contains_term(attrs[key], terms)
            }
            image_attrs.update(asset=digest, alt=scrub(str(attrs.get('alt', ''))))
            clean['blocks'].append(
                {
                    'id': 'image_' + uuid.uuid4().hex,
                    'type': 'image',
                    'text': '',
                    'style': 'body',
                    'marks': [],
                    'attrs': image_attrs,
                }
            )
            continue
        if kind in {'image', 'page_break', 'section_break', 'toc'}:
            removed_objects += 1
            continue
        if kind not in {'paragraph', 'heading', 'table', 'footnote'}:
            raise ByaxError(
                'unsupported_feature',
                'Cannot verify sanitization of this block type.',
                target=block.get('id'),
                block_type=kind,
            )
        out = {
            'id': 'block_' + uuid.uuid4().hex,
            'type': kind if kind != 'footnote' else 'paragraph',
            'text': scrub(block.get('text', '')),
            'style': 'body',
            'marks': [],
            'attrs': formatting(block.get('attrs', {})),
        }
        if block.get('style') in clean['styles'] and 'styles' in preserve:
            out['style'] = block['style']
        if kind == 'heading':
            out['attrs']['level'] = min(6, max(1, int(block.get('attrs', {}).get('level', 1))))
            if out['style'] == 'body':
                out['style'] = f'heading{out["attrs"]["level"]}'
        if kind == 'table':
            out['rows'] = [
                {
                    'id': 'row_' + uuid.uuid4().hex,
                    'cells': [
                        {
                            'id': 'cell_' + uuid.uuid4().hex,
                            'text': scrub(cell.get('text', '')),
                            'attrs': formatting(cell.get('attrs', {})),
                        }
                        for cell in row.get('cells', [])
                    ],
                }
                for row in block.get('rows', [])
            ]
        clean['blocks'].append(out)
    if not clean['blocks']:
        clean['blocks'] = new_document()['blocks']
    report = {
        'schema_version': 1,
        'execution': 'completed',
        'validation': 'not_run',
        'review': 'required',
        'document_id': clean['id'],
        'base_revision': doc['revision'],
        'revision': clean['revision'],
        'selected_ids': selected,
        'occurrences_removed': occurrences,
        'removed_objects': removed_objects,
        'removed_categories': [
            category
            for category in (
                'assets',
                'audio',
                'review',
                'history',
                'sources',
                'metadata',
                'fields',
                'layers',
                'custom_styles',
                'layout',
                'extension_data',
            )
            if not (
                category == 'custom_styles'
                and 'styles' in preserve
                or category == 'layout'
                and 'layout' in preserve
            )
        ],
        'preserved': sorted(preserve),
        'preserved_assets': preserved_assets,
        'dropped_style_hashes': dropped_styles,
        'dropped_assets': dropped_assets,
        'losses': [
            'Sanitization rebuilds the document; marks, review, history and unselected categories are removed.'
        ]
        + (
            ['Preserved images were checked byte-for-byte; text drawn in their pixels is not inspected.']
            if preserved_assets
            else []
        ),
        'term_hashes': [hashlib.sha256(term.encode()).hexdigest() for term in terms],
        'outputs': [],
        'coverage': {'serialized_output': 'not_run'},
    }
    return clean, report


def _string_values(value, skip=frozenset()):
    """String values in parsed JSON, skipping identifiers and generated defaults."""
    if isinstance(value, str):
        if value not in skip:
            yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ('id', 'revision', 'base_revision', 'document_id'):
                yield from _string_values(item, skip)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item, skip)


def _copy_exclusive(source, destination):
    import os

    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    except FileExistsError as exc:
        raise ByaxError('conflict', 'Sanitized output already exists.') from exc
    except OSError as exc:
        raise ByaxError(
            'execution_failure', f'Cannot create sanitized output: {exc.strerror or exc}'
        ) from exc
    try:
        with os.fdopen(fd, 'wb') as out, open(source, 'rb') as stream:
            while chunk := stream.read(1 << 20):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        os.unlink(destination)
        raise


def save_sanitized(doc, out_path, targets, *, terms=None, preserve=None):
    """Verify the actual staged archive before publishing the sanitized master."""
    import tempfile
    import zipfile

    from .model import new_document
    from .storage import load_document, save_document

    destination = Path(out_path)
    if destination.suffix.lower() != '.byax':
        raise ByaxError(
            'invalid_input', 'Verified sanitization outputs a .byax master; export it separately.'
        )
    if destination.exists():
        raise ByaxError('conflict', 'Sanitized output already exists; choose a new path.')
    clean, report = sanitize(doc, targets, terms=terms, preserve=preserve)
    needles = list(terms or [])
    nodes = {node['id']: node for node in _text_targets(doc)}
    for selector in targets:
        selector = {'target': selector} if isinstance(selector, str) else selector
        text = nodes[selector['target']]['text']
        needles.append(text[selector.get('start', 0) : selector.get('end', len(text))])
    baseline = set(_string_values(new_document('Sanitized document')))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp) / 'sanitized.byax'
        save_document(clean, stage)
        checked = load_document(stage)
        with zipfile.ZipFile(stage) as archive:
            encoded = list(_term_encodings(needles))
            for member in archive.infolist():
                raw = archive.read(member)
                # Only explicitly preserved, byte-checked image assets may be binary.
                if not member.filename.endswith('.json'):
                    if member.filename not in {f'assets/{digest}' for digest in report['preserved_assets']}:
                        raise ByaxError(
                            'failed_requirement', 'Unexpected binary member in sanitized archive.'
                        )
                    if any(needle in raw for needle in encoded):
                        raise ByaxError(
                            'failed_requirement', 'Serialized sanitization verification found retained text.'
                        )
                    continue
                # Search decoded string values, not escaped JSON: escaping hid terms
                # containing quotes or newlines, while keys and generated defaults
                # caused false failures for ordinary words such as "body".
                values = _string_values(json.loads(raw), baseline)
                if any(term in value for value in values for term in needles):
                    raise ByaxError(
                        'failed_requirement', 'Serialized sanitization verification found retained text.'
                    )
        if set(checked.get('assets', {})) - set(report['preserved_assets']):
            raise ByaxError('failed_requirement', 'Sanitized archive retained assets.')
        # A same-filesystem hard link publishes the fully written archive atomically
        # and refuses to replace an existing file, including a racing private original.
        import os

        try:
            os.link(stage, destination)
        except FileExistsError as exc:
            raise ByaxError('conflict', 'Sanitized output already exists.') from exc
        except OSError:
            # FAT/exFAT and some network shares lack hard links; exclusive
            # creation still refuses to replace an existing file.
            _copy_exclusive(stage, destination)
    report.update(validation='passed', outputs=[str(destination)])
    report['coverage']['serialized_output'] = 'passed'
    return report


def export_clean(doc, path, format=None, profile=None, *, owner=None):
    from .checks import run_checks
    from .exchange import export_document

    settings = {
        'include_comments': False,
        'include_tracked_changes': False,
        'include_proposals': False,
        'include_findings': False,
        'include_history': False,
        'include_metadata': False,
        'include_references': False,
        'include_audio': False,
        'include_transcripts': False,
        'include_hidden': False,
        'include_source_snapshots': False,
        'include_originals': False,
        'required_suites': [],
        **(profile or {}),
    }
    for key, value in settings.items():
        if key.startswith('include_') and type(value) is not bool:
            raise ByaxError('invalid_input', 'Publication inclusion settings must be booleans.', field=key)
    clean = copy.deepcopy(doc)
    for category in ('comments', 'tracked_changes', 'proposals', 'findings'):
        if not settings['include_' + category]:
            clean['review'][category] = []
    if not settings['include_history']:
        clean['history'] = {'revisions': [], 'branches': {}, 'current_branch': 'main'}
        clean['requests'] = {}
    original_asset = clean.get('metadata', {}).get('import', {}).get('original_asset')
    if not settings['include_metadata']:
        clean['metadata'] = {
            'title': 'Published document',
            'author': '',
            'language': doc.get('metadata', {}).get('language', 'en'),
        }
    elif not settings['include_originals']:
        clean['metadata'].pop('import', None)
    if not settings['include_references']:
        clean['references'] = {}
    elif not settings['include_source_snapshots']:
        for reference in clean['references'].values():
            if isinstance(reference, dict):
                for key in ('snapshot', 'source_snapshot', 'data', 'asset'):
                    reference.pop(key, None)
    if not settings['include_originals'] and original_asset:
        clean['assets'].pop(original_asset, None)
    if not settings['include_transcripts']:
        clean.pop('transcripts', None)
        for key in ('transcripts', 'transcript', 'raw_transcript', 'dictation_sessions'):
            clean['settings'].pop(key, None)
    if not settings['include_audio']:
        clean['assets'] = {
            key: value
            for key, value in clean.get('assets', {}).items()
            if not value.get('mime', '').startswith('audio/')
        }
    if not settings['include_hidden']:
        clean['blocks'] = [
            block for block in clean['blocks'] if not block.get('attrs', {}).get('excluded', False)
        ]
        clean['layers'] = [
            layer
            for layer in clean.get('layers', [])
            if layer.get('visible', True) and not layer.get('excluded')
        ]
    # Remove unreferenced attachment copies from the clean deliverable. Referenced
    # visible images/fonts remain intact. This is publication filtering, not redaction.
    if not settings['include_originals'] and not settings['include_source_snapshots']:
        referenced = set()

        def collect_assets(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ('asset', 'font_asset', 'background_asset') and isinstance(child, str):
                        referenced.add(child)
                    else:
                        collect_assets(child)
            elif isinstance(value, list):
                for child in value:
                    collect_assets(child)

        for category in ('blocks', 'layers', 'styles', 'layout', 'references'):
            collect_assets(clean[category])
        clean['assets'] = {
            key: value
            for key, value in clean['assets'].items()
            if key in referenced or (settings['include_audio'] and value.get('mime', '').startswith('audio/'))
        }
    check = run_checks(clean, settings['required_suites']) if settings['required_suites'] else None
    if check and check.get('validation') != 'passed':
        raise ByaxError(
            'failed_requirement' if check.get('validation') == 'failed' else 'incomplete_coverage',
            'Selected publication content did not satisfy required checks.',
            report=check,
        )
    report = export_document(clean, path, format=format, profile=settings, owner=owner)
    report['publication_policy'] = settings
    # Preserve adapter/layout validation: semantic checks cannot certify serialization.
    if check and check['validation'] != 'passed':
        report['validation'] = check['validation']
    elif not check:
        report['validation'] = report.get('validation', 'not_run')
    if check:
        report['checks'] = check
    return report


def font_dependencies(doc):
    """Hash resolved primary font binaries when the platform exposes their files."""
    import os
    import shutil
    import subprocess

    from PySide6.QtGui import QFont, QFontInfo

    selected = {'DejaVu Sans', 'DejaVu Serif'} | {
        value.get('font_family', 'Noto Sans') for value in doc.get('styles', {}).values()
    }
    selected.update(
        mark['value']
        for block in doc['blocks']
        for mark in block.get('marks', [])
        if mark.get('type') == 'font_family' and isinstance(mark.get('value'), str)
    )
    dependencies = []
    for family in sorted(selected):
        for bold, italic in ((False, False), (True, False), (False, True)):
            font = QFont(family, 11)
            font.setBold(bold)
            font.setItalic(italic)
            resolved = QFontInfo(font).family()
            from .fonts import bundled_font_path

            path = bundled_font_path(resolved, bold, italic)
            if path is None and os.name == 'posix' and shutil.which('fc-match'):
                pattern = (
                    resolved
                    + ':weight='
                    + ('bold' if bold else 'regular')
                    + ':slant='
                    + ('italic' if italic else 'roman')
                )
                result = subprocess.run(
                    ['fc-match', '--format=%{file}', pattern],
                    capture_output=True,
                    timeout=5,
                    check=False,
                    text=True,
                )
                found = Path(result.stdout.strip())
                if result.returncode == 0 and found.is_file():
                    path = found
            elif path is None and os.name == 'nt':
                import winreg

                style_name = resolved + (' Bold' if bold else '') + (' Italic' if italic else '')
                for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(
                            root, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts'
                        ) as key:
                            for index in range(winreg.QueryInfoKey(key)[1]):
                                name, file, _ = winreg.EnumValue(key, index)
                                if name.removesuffix(' (TrueType)').casefold() == style_name.casefold():
                                    candidate = Path(file)
                                    if not candidate.is_absolute():
                                        candidate = (
                                            Path(os.environ.get('WINDIR', r'C:\Windows'))
                                            / 'Fonts'
                                            / candidate
                                        )
                                    if candidate.is_file():
                                        path = candidate
                                        break
                    except OSError:
                        continue
            dependencies.append(
                {
                    'requested': family,
                    'resolved': resolved,
                    'bold': bold,
                    'italic': italic,
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path else None,
                    'substituted': resolved.casefold() != family.casefold(),
                    'status': 'hashed' if path else 'unresolved',
                }
            )
    return dependencies


def reproduction_record(doc, settings=None):
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QFontDatabase

    from .layout import ensure_app, render

    ensure_app()
    settings = dict(settings or {})
    tolerance = settings.get('tolerance', {'channel_delta': 0, 'max_changed_fraction': 0})
    _validate_tolerance(tolerance)
    families = sorted(QFontDatabase.families())
    fonts = font_dependencies(doc)
    record = {
        'schema_version': 1,
        'engine': __version__,
        'document_id': doc['id'],
        'revision': doc['revision'],
        'document_hash': _hash({k: v for k, v in doc.items() if k not in {'history', 'requests'}}),
        'platform': platform.platform(),
        'qt': qVersion(),
        'font_families': families,
        'font_inventory_hash': _hash(families),
        'font_dependencies': fonts,
        'assets': sorted(doc.get('assets', {})),
        'sources': {key: _hash(value) for key, value in doc.get('references', {}).items()},
        'settings': settings,
        'tolerance': tolerance,
        'references': [],
        'verification': 'dependency_inventory_only',
        'limitations': [
            'Primary font files are hashed where resolvable; glyph-specific fallback fonts may differ.'
        ],
    }
    if settings.get('reference_dir'):
        directory = Path(settings['reference_dir']).resolve()
        if directory.exists() and any(directory.iterdir()):
            raise ByaxError('conflict', 'Reference directory must be new or empty.')
        rendered = render(doc, directory)
        record['references'] = [
            {'path': str(Path(path).resolve()), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            for path in rendered['outputs']
            if str(path).lower().endswith('.png')
        ]
        record['reference_render_findings'] = rendered.get('findings', [])
        record['verification'] = 'reference_recorded'
    return record


def _validate_tolerance(tolerance):
    if not isinstance(tolerance, dict) or set(tolerance) != {'channel_delta', 'max_changed_fraction'}:
        raise ByaxError('invalid_input', 'Tolerance requires channel_delta and max_changed_fraction.')
    delta, fraction = tolerance['channel_delta'], tolerance['max_changed_fraction']
    if (
        type(delta) is not int
        or not 0 <= delta <= 255
        or type(fraction) not in (int, float)
        or not 0 <= fraction <= 1
    ):
        raise ByaxError('invalid_input', 'Channel delta must be 0–255 and changed fraction 0–1.')


def reproduce(doc, record, out_dir):
    from PIL import Image, ImageChops

    from .layout import render

    if not isinstance(record, dict) or record.get('schema_version') != 1:
        raise ByaxError('invalid_input', 'Unsupported reproduction record.')
    tolerance = record.get('tolerance', {'channel_delta': 0, 'max_changed_fraction': 0})
    _validate_tolerance(tolerance)
    references = record.get('references', [])
    out = Path(out_dir).resolve()
    for reference in references:
        path = Path(reference['path']).resolve()
        if path.parent == out:
            raise ByaxError('conflict', 'Reproduction must not overwrite its reference images.')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
            raise ByaxError(
                'failed_requirement', 'Reference rendering is missing or changed.', target=str(path)
            )
    current = reproduction_record(
        doc, {k: v for k, v in record.get('settings', {}).items() if k != 'reference_dir'}
    )
    keys = [
        'engine',
        'document_hash',
        'qt',
        'font_inventory_hash',
        'font_dependencies',
        'assets',
        'sources',
        'platform',
    ]
    drift = [key for key in keys if current.get(key) != record.get(key)]
    result = render(doc, out_dir)
    outputs = [path for path in result['outputs'] if str(path).lower().endswith('.png')]
    comparisons = []
    for index, reference in enumerate(references):
        if index >= len(outputs):
            comparisons.append({'page': index + 1, 'matched': False, 'reason': 'Missing output page'})
            continue
        with Image.open(reference['path']) as before, Image.open(outputs[index]) as after:
            if before.size != after.size:
                comparisons.append({'page': index + 1, 'matched': False, 'reason': 'Image dimensions differ'})
                continue
            difference = ImageChops.difference(before.convert('RGB'), after.convert('RGB'))
            channels = difference.split()
            mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
            mask = mask.point(lambda value: 255 if value > tolerance['channel_delta'] else 0)
            fraction = mask.histogram()[255] / (before.width * before.height)
            comparisons.append(
                {
                    'page': index + 1,
                    'matched': fraction <= tolerance['max_changed_fraction'],
                    'changed_fraction': fraction,
                    'tolerance': tolerance,
                }
            )
    verified = (
        bool(references) and len(references) == len(outputs) and all(item['matched'] for item in comparisons)
    )
    state = 'drift' if drift else 'matched' if verified else 'mismatch' if references else 'renderable'
    return {
        **result,
        'reproduction': {
            'dependency_drift': drift,
            'dependencies_matched': not drift,
            'reference_verified': verified,
            'state': state,
            'page_comparisons': comparisons,
            'limitations': current['limitations'],
        },
        'validation': 'failed'
        if references and not verified
        else 'incomplete'
        if drift or not references
        else 'passed',
    }
