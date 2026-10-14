"""Headless format capabilities and pure, identity-preserving file views.

Projection is presentation data, never a replacement for the canonical master.
Policy (protections, tracking and requirements) is always enforced on the master.
"""

from __future__ import annotations

import copy

from .errors import ByaxError
from .model import BLOCK_TYPES

ALIASES = {'markdown': 'md', 'htm': 'html'}
MD_MARKS = {'bold', 'italic', 'code', 'link'}
MD_STYLES = {'body', 'code', 'quotation', 'Normal'} | {f'heading{n}' for n in range(1, 7)}
BASIC_MARKS = {'bold', 'italic', 'underline', 'strikethrough', 'superscript', 'subscript'}
PARAGRAPH_ATTRS = {'alignment', 'space_before', 'space_after', 'indent', 'first_line_indent'}


COMMAND_CAPABILITIES = {
    'Bold': 'marks.bold',
    'Italic': 'marks.italic',
    'Underline': 'marks.underline',
    'Strikethrough': 'marks.strikethrough',
    'Superscript': 'marks.superscript',
    'Subscript': 'marks.subscript',
    'Text color…': 'marks.color',
    'Highlight…': 'marks.highlight',
    'Hyperlink…': 'marks.link',
    'Font and paragraph…': 'marks.font_family',
    'Define named style…': 'styles',
    'Table…': 'blocks.table',
    'Image…': 'images.embedded',
    'Page break': 'blocks.page_break',
    'Section / columns…': 'blocks.section_break',
    'Table of contents': 'blocks.toc',
    'Footnote…': 'blocks.footnote',
    'Merge cells…': 'tables.spans',
    'Unmerge selected cell': 'tables.spans',
    'Watermark / layer…': 'full',
    'Cross reference…': 'full',
    'Define template field…': 'full',
    'Template fields…': 'full',
    'Restart list numbering': 'full',
    'Continue list numbering': 'full',
    'Apply character style…': 'marks.character_style',
    'Page masters…': 'layout.full',
    'Edit selected layer…': 'layout.full',
    'Group selected layers…': 'layout.full',
    'Ungroup selected layers': 'layout.full',
    'Move layer up': 'layout.full',
    'Move layer down': 'layout.full',
    'Bulleted list': 'block_attrs.list',
    'Numbered list': 'block_attrs.list',
    'Remove list': 'block_attrs.list',
    'Increase list level': 'lists.nested',
    'Decrease list level': 'lists.nested',
}


def _entry(ident, label, marks, *, styles=False, attrs=(), images='embedded', extra=()):
    full = ident in ('byax', 'pdf')
    blocks = {kind: 'hidden' for kind in BLOCK_TYPES}
    blocks.update(paragraph='full', heading='full', table='full', image='locked')
    blocks.update({kind: 'full' for kind in extra})
    if full:
        blocks = {kind: 'full' for kind in BLOCK_TYPES}
    if ident == 'txt':
        blocks.update(heading='degraded', table='locked')
    return {
        'id': ident,
        'label': label,
        'extensions': [ident] + [k for k, v in ALIASES.items() if v == ident],
        'fidelity': 'exact' if full else 'semantic' if ident in ('txt', 'md', 'html') else 'approximate',
        'paged': ident not in ('txt', 'md', 'html'),
        'blocks': blocks,
        'marks': '*' if full else sorted(marks),
        'styles': styles or full,
        'block_attrs': '*' if full else sorted(set(attrs) | ({'level', 'list'} if ident != 'txt' else set())),
        'images': images,
        'image_mimes': ['image/png', 'image/jpeg'] if ident == 'rtf' else '*',
        'tables': {
            'spans': full or ident == 'odt',
            'header_rows': ident != 'txt',
            'cell_formatting': ident in ('byax', 'pdf', 'html', 'rtf', 'odt'),
        },
        'lists': 'full' if full or ident == 'md' else 'flattened' if ident == 'txt' else 'simplified',
        'layout': 'full' if full else 'geometry' if ident in ('docx', 'odt', 'rtf') else 'none',
        'review': ['comments', 'tracked_changes', 'proposals', 'findings']
        if ident == 'byax'
        else ['comments']
        if ident == 'docx'
        else [],
        'source': ident in ('md', 'txt', 'html', 'rtf'),
        'commands': dict(COMMAND_CAPABILITIES),
    }


FORMATS = {
    entry['id']: entry
    for entry in (
        _entry('byax', 'ByAuthorX (full)', set()),
        _entry('pdf', 'PDF', set()),
        _entry(
            'docx',
            'Word (.docx)',
            BASIC_MARKS | {'link', 'font_family', 'font_size', 'font', 'size', 'color'},
            styles=True,
            attrs=PARAGRAPH_ATTRS,
            extra=('page_break',),
        ),
        _entry(
            'odt',
            'OpenDocument (.odt)',
            BASIC_MARKS | {'font_family', 'font_size', 'color', 'highlight', 'link'},
            styles=True,
            attrs=PARAGRAPH_ATTRS - {'alignment'},
            extra=('footnote',),
        ),
        _entry(
            'rtf',
            'Rich Text (.rtf)',
            BASIC_MARKS | {'font_size'},
            styles=True,
            attrs={'alignment'},
            extra=('page_break', 'footnote'),
        ),
        _entry('html', 'HTML', BASIC_MARKS | {'code', 'link'}),
        _entry('md', 'Markdown', MD_MARKS, images='reference_only'),
        _entry('txt', 'Plain text', set(), images='alt_text_only'),
    )
}


def canonical_format(view: str) -> str:
    view = ALIASES.get(view.lower(), view.lower())
    if view not in FORMATS:
        raise ByaxError('unsupported_format', f'Unsupported file view: {view}')
    return view


def supports(view: str, capability: str) -> bool:
    entry = FORMATS[canonical_format(view)]
    if entry['id'] == 'byax':
        return True
    group, _, key = capability.partition('.')
    value = entry.get(group)
    if group == 'images':
        return value == key
    if group == 'lists' and key == 'nested':
        return value == 'full' or view == 'odt'
    if group == 'blocks':
        return value.get(key) == 'full'
    if isinstance(value, dict):
        return bool(value.get(key))
    return bool(value) if not key else value == '*' or key in (value or [])


def output_document(doc: dict, view: str, *, include_hidden=False) -> tuple[dict, list]:
    """Shared authored output policy, with no Qt import or implicit retrieval."""
    view = canonical_format(view)
    candidate = copy.deepcopy(doc)
    losses = []
    if view == 'byax':
        return candidate, losses

    def selected(attrs):
        return (
            not attrs.get('redacted')
            and (include_hidden or not attrs.get('excluded'))
            and view not in attrs.get('exclude_outputs', [])
            and ('outputs' not in attrs or view in attrs['outputs'])
        )

    for collection in ('blocks', 'layers'):
        groups = {item['id']: item for item in candidate.get('layers', [])}
        kept = []
        for item in candidate.get(collection, []):
            attrs = item.get('attrs', {}) if collection == 'blocks' else item
            group = groups.get(attrs.get('group'), {})
            if selected(attrs) and selected(group):
                kept.append(item)
            else:
                losses.append(
                    {
                        'code': 'output_excluded',
                        'message': f'Excluded from {view.upper()} by authored output policy.',
                        'target': item['id'],
                    }
                )
        candidate[collection] = kept
    return candidate, losses


def project(doc: dict, view: str, mode: str = 'rendered') -> tuple[dict, dict]:
    """Return an independent projection and a located loss/locking report."""
    from .exchange import conversion_preview

    view = canonical_format(view)
    if mode not in ('rendered', 'source') or (mode == 'source' and not FORMATS[view]['source']):
        raise ByaxError('unsupported_feature', 'This file view has no requested source mode')
    candidate, _ = output_document(doc, view)
    report = conversion_preview(doc, view)
    report.update(format=view, fidelity=FORMATS[view]['fidelity'], locked=[], hidden=[])
    excluded = {b['id'] for b in doc['blocks']} - {b['id'] for b in candidate['blocks']}
    candidate = _project_content(candidate, view, report)
    report['hidden'] = sorted(set(report['hidden']) | excluded)
    if mode == 'source':
        from .exchange import source_text

        report['source'] = source_text(doc, view)
    return candidate, report


def _project_content(candidate, view, report):
    if view == 'byax':
        return candidate
    entry = FORMATS[view]
    original_ids = {b['id'] for b in candidate['blocks']}
    kept = []
    for block in candidate['blocks']:
        kind, attrs = block['type'], block.get('attrs', {})
        behavior = entry['blocks'].get(kind, 'locked')
        if kind == 'footnote' and view in ('rtf', 'odt') and not attrs.get('reference'):
            behavior = 'hidden'
        if behavior == 'hidden':
            continue
        if behavior == 'locked' or kind == 'toc':
            report['locked'].append(block['id'])
        if kind == 'heading' and behavior == 'degraded':
            block.update(type='paragraph', style='body')
        if kind == 'table' and view == 'txt':
            block.update(
                type='paragraph',
                text='\n'.join('\t'.join(c['text'] for c in row['cells']) for row in block['rows']),
                style='body',
                marks=[],
                attrs={},
            )
            block.pop('rows')
        elif kind == 'table' and not entry['tables']['spans']:
            for row in block.get('rows', []):
                for cell in row['cells']:
                    if any(cell.get('attrs', {}).get(k, 1) != 1 for k in ('rowspan', 'colspan')):
                        report['locked'].append(cell['id'])
                    cell['attrs'] = {}
                    cell['marks'] = [
                        m
                        for m in cell.get('marks', [])
                        if supports(view, 'marks.' + m['type']) and entry['tables']['cell_formatting']
                    ]
        if kind == 'image' and (
            entry['images'] != 'embedded'
            or (
                view == 'rtf'
                and candidate['assets'].get(attrs.get('asset'), {}).get('mime') not in entry['image_mimes']
            )
        ):
            block.update(
                type='paragraph',
                text=attrs.get('alt', '') if view == 'md' else '[Image: ' + attrs.get('alt', '') + ']',
                style='body',
                marks=[],
                attrs={},
            )
        block['marks'] = [m for m in block.get('marks', []) if supports(view, 'marks.' + m['type'])]
        if not entry['styles'] and (view == 'txt' or block.get('style') not in MD_STYLES):
            block['style'] = 'body'
        if entry['block_attrs'] != '*' and kind in ('paragraph', 'heading'):
            block['attrs'] = {k: v for k, v in block.get('attrs', {}).items() if k in entry['block_attrs']}
        if kind in ('paragraph', 'heading') and entry['lists'] == 'flattened':
            block['attrs'].pop('list', None)
            block['attrs'].pop('level', None)
        if kind in ('paragraph', 'heading') and entry['lists'] == 'simplified' and block['attrs'].get('list'):
            listing = block['attrs']['list']
            block['attrs']['list'] = {
                'kind': listing.get('kind', 'bullet') if isinstance(listing, dict) else listing,
                'level': (
                    listing.get('level', attrs.get('level', 1))
                    if isinstance(listing, dict)
                    else attrs.get('level', 1)
                )
                if view == 'odt'
                else 1,
            }
            if view == 'html':
                block['attrs'].update(restart=True, start=1)
        kept.append(block)
    candidate['blocks'] = kept
    if not entry['styles']:
        from .model import new_document

        # Fresh built-in style values, without introducing any new editable IDs.
        candidate['styles'] = new_document()['styles']
    if entry['layout'] == 'none':
        candidate['layout'] = {
            'width': 612,
            'height': 792,
            'margins': dict.fromkeys(('top', 'right', 'bottom', 'left'), 36),
        }
    elif entry['layout'] == 'geometry':
        candidate['layout'] = {
            key: value
            for key, value in candidate['layout'].items()
            if key in ('width', 'height', 'margins', 'header', 'footer')
        }
        for key in ('header', 'footer'):
            value = candidate['layout'].get(key)
            if isinstance(value, dict):
                candidate['layout'][key] = value.get('text', '')
    candidate['review'] = {
        key: value if key in entry['review'] else [] for key, value in candidate['review'].items()
    }
    if view != 'pdf':
        candidate['layers'] = []
    report['hidden'] = sorted(original_ids - {b['id'] for b in kept})
    report['locked'] = sorted(set(report['locked']))
    return candidate
