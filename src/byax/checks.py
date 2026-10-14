"""Deterministic, offline checks with explicit coverage and unbounded totals."""

import re

from .errors import ByaxError
from .model import object_hash, text_of, validate_document

SUITES = ('structure', 'accessibility', 'editorial', 'requirements', 'print', 'spelling', 'publication')


def run_checks(doc, suites=None, *, limit=100, profile=None):
    if isinstance(profile, str):
        if profile not in doc.get('profiles', {}):
            raise ByaxError('missing_target', 'Unknown check profile.', target=profile)
        profile = doc['profiles'][profile]
    config = profile or {}
    profile_scope = (
        suites is None or suites == 'publication' or isinstance(suites, list) and 'publication' in suites
    )
    if suites is None:
        if profile is not None:
            from .profiles import check_suites

            suites = check_suites(config)
        else:
            suites = ['structure', 'accessibility', 'requirements', 'print']
    if isinstance(suites, str):
        suites = [suites]
    if not isinstance(suites, list) or any(s not in SUITES for s in suites):
        raise ByaxError('invalid_input', 'Unknown check suite.', supported=list(SUITES))
    if type(limit) is not int or limit < 0:
        raise ByaxError('invalid_input', 'Finding limit must be a nonnegative integer.')
    expanded = set(suites)
    if 'publication' in expanded:
        if profile is not None:
            from .profiles import check_suites

            expanded.update(check_suites(config))
        else:
            expanded.update(('structure', 'accessibility', 'requirements', 'print', 'editorial'))
        expanded.remove('publication')
    findings, coverage = [], []

    def add(rule, family, severity, message, target=None, start=None, end=None, **values):
        f = {
            'rule': rule,
            'family': family,
            'severity': severity,
            'explanation': message,
            'affected_ids': [target] if target else [],
            'range': {'start': start, 'end': end} if start is not None else None,
            'measured': values,
            'evidence': [],
            'operations': [],
            'status': 'open',
            'revision': doc['revision'],
            'profile_version': config.get('version', 1),
            'engine': 'byax-deterministic/1',
            'source_versions': {},
            'scope': target or 'document',
        }
        f['id'] = 'finding_' + object_hash(f)[:20]
        findings.append(f)

    for suite in sorted(expanded):
        coverage.append(
            {'suite': suite, 'status': 'completed', 'scope': 'document', 'engine': 'byax-deterministic/1'}
        )
    if 'structure' in expanded:
        try:
            validate_document(doc, include_history=False)
        except ByaxError as exc:
            add('document.schema', 'structure', 'error', exc.message, **exc.details)
        for style_name, style in doc.get('styles', {}).items():
            ancestors, parent = {style_name}, style.get('based_on')
            while parent:
                if parent in ancestors:
                    add(
                        'style.cycle',
                        'structure',
                        'error',
                        'Named style inheritance is cyclic.',
                        style=style_name,
                    )
                    break
                if parent not in doc['styles']:
                    add(
                        'style.parent_missing',
                        'structure',
                        'error',
                        'Named style parent is missing.',
                        style=style_name,
                        parent=parent,
                    )
                    break
                ancestors.add(parent)
                parent = doc['styles'][parent].get('based_on')
        for b in doc.get('blocks', []):
            if b.get('style') and b['style'] not in doc.get('styles', {}):
                add(
                    'style.missing',
                    'structure',
                    'error',
                    'Block refers to a missing named style.',
                    b['id'],
                    style=b['style'],
                )
            link = b.get('attrs', {}).get('link')
            if b.get('type') == 'image' and link:
                dependency = doc.get('dependencies', {}).get(link)
                if dependency is None:
                    add(
                        'dependency.record_missing',
                        'structure',
                        'error',
                        'Linked image has no dependency record.',
                        b['id'],
                    )
                elif dependency.get('status') in ('unresolved', 'missing'):
                    add(
                        'dependency.' + dependency['status'],
                        'structure',
                        'warning',
                        'Linked image content is unavailable; refresh it with explicit authorization or embed a copy.'
                        if not dependency.get('asset')
                        else 'Linked image source is missing; the last retrieved copy is used.',
                        b['id'],
                        dependency=link,
                        uri=dependency.get('uri'),
                    )
            elif b.get('type') == 'image' and b.get('attrs', {}).get('asset') not in doc.get('assets', {}):
                add('asset.missing', 'structure', 'error', 'Image asset is missing.', b['id'])
            for mark in b.get('marks', []):
                if mark.get('type') in ('citation', 'reference') and mark.get('value') not in doc.get(
                    'references', {}
                ):
                    add(
                        'reference.missing',
                        'structure',
                        'error',
                        'Citation refers to a missing reference.',
                        b['id'],
                        mark['start'],
                        mark['end'],
                    )
        all_ids = {b['id'] for b in doc.get('blocks', [])} | {
            c['id'] for b in doc.get('blocks', []) for r in b.get('rows', []) for c in r['cells']
        }
        bookmarks = doc.get('settings', {}).get('bookmarks', {})
        for name, bookmark in bookmarks.items():
            if bookmark.get('target') not in all_ids:
                add(
                    'bookmark.orphaned',
                    'structure',
                    'warning',
                    'Bookmark target was deleted.',
                    bookmark.get('target'),
                    bookmark=name,
                )
        for block in doc.get('blocks', []):
            for mark in block.get('marks', []):
                if mark.get('type') == 'cross_reference':
                    destination = bookmarks.get(mark.get('value'), {}).get('target', mark.get('value'))
                    if destination not in all_ids:
                        add(
                            'cross_reference.missing',
                            'structure',
                            'warning',
                            'Cross-reference target is unresolved.',
                            block['id'],
                            mark['start'],
                            mark['end'],
                            destination=mark.get('value'),
                        )
        for comment in doc.get('review', {}).get('comments', []):
            if comment.get('status') == 'orphaned':
                add(
                    'comment.orphaned',
                    'structure',
                    'warning',
                    'Comment target was deleted.',
                    comment.get('target'),
                )
    if 'accessibility' in expanded:
        level = 0
        for b in doc.get('blocks', []):
            attrs = b.get('attrs', {})
            if b['type'] == 'heading':
                current = attrs.get('level', 1)
                if not isinstance(current, int) or current < 1 or current > 6:
                    add(
                        'heading.level',
                        'accessibility',
                        'error',
                        'Heading level must be between one and six.',
                        b['id'],
                    )
                elif current > level + 1:
                    add(
                        'heading.skipped',
                        'accessibility',
                        'warning',
                        'Heading hierarchy skips a level.',
                        b['id'],
                        previous=level,
                        current=current,
                    )
                level = current if isinstance(current, int) else level
            if b['type'] == 'image' and not attrs.get('alt', '').strip() and not attrs.get('decorative'):
                add('image.alt', 'accessibility', 'warning', 'Image has no text alternative.', b['id'])
            if b['type'] == 'table' and not attrs.get('header_rows'):
                add(
                    'table.headers',
                    'accessibility',
                    'warning',
                    'Table has no designated header rows.',
                    b['id'],
                )
    content = text_of(doc)
    if 'requirements' in expanded:
        requirements = doc.get('settings', {}).get('requirements', {})
        for key, req in requirements.items():
            kind = req.get('kind')
            value = req.get('value')
            passed = True
            if kind == 'required_text':
                passed = isinstance(value, str) and value in content
            elif kind == 'prohibited_text':
                passed = isinstance(value, str) and value not in content
            elif kind == 'required_section':
                passed = any(b['type'] == 'heading' and b.get('text') == value for b in doc['blocks'])
            elif kind in ('max_words', 'min_words') and type(value) is int:
                count = len(re.findall(r'\w+', content, re.UNICODE))
                passed = count <= value if kind == 'max_words' else count >= value
            elif kind == 'filled_field':
                passed = _field_filled(doc.get('fields', {}).get(value))
            else:
                coverage.append(
                    {
                        'suite': 'requirements',
                        'rule': key,
                        'status': 'unmeasurable',
                        'reason': 'Unsupported requirement kind or value.',
                    }
                )
                continue
            if not passed:
                add(
                    'requirement.' + key,
                    'requirements',
                    'error',
                    req.get('message', f'Requirement {key} is not satisfied.'),
                    kind=kind,
                    value=value,
                )
        for field_id, definition in doc.get('fields', {}).items():
            if isinstance(definition, dict) and definition.get('required') and not _field_filled(definition):
                add(
                    'field.required',
                    'requirements',
                    'error',
                    'Required field is missing or invalid.',
                    field=field_id,
                )
        for match in re.finditer(r'\{\{\s*([\w.-]+)\s*\}\}', content):
            if not _field_filled(doc.get('fields', {}).get(match.group(1))):
                add(
                    'field.unfilled',
                    'requirements',
                    'warning',
                    'Template field is unfilled.',
                    field=match.group(1),
                )
    if 'print' in expanded:
        layout = doc.get('layout', {})
        w, h, margins = layout.get('width', 0), layout.get('height', 0), layout.get('margins', {})
        if not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            for v in (w, h, *margins.values())
        ):
            add(
                'paper.invalid', 'print', 'error', 'Paper dimensions and margins must be nonnegative numbers.'
            )
        elif (
            not w
            or not h
            or margins.get('left', 0) + margins.get('right', 0) >= w
            or margins.get('top', 0) + margins.get('bottom', 0) >= h
        ):
            add('paper.invalid', 'print', 'error', 'Margins leave no printable area.')
        for b in doc['blocks']:
            if b['type'] == 'image':
                a = b.get('attrs', {})
                if a.get('pixel_width') and a.get('width'):
                    dpi = a['pixel_width'] / a['width'] * 72
                    if dpi < config.get('minimum_dpi', 150):
                        add(
                            'image.resolution',
                            'print',
                            'warning',
                            'Placed image resolution is low.',
                            b['id'],
                            dpi=round(dpi, 1),
                        )
        try:
            from .layout import check_layout

            resolved = check_layout(doc)
            for finding in resolved.get('findings', []):
                add(
                    'layout.' + finding.get('code', 'diagnostic'),
                    'print',
                    finding.get('severity', 'warning'),
                    finding.get('message', finding.get('explanation', 'Layout diagnostic.')),
                    finding.get('target'),
                )
            for item in resolved.get('coverage', []):
                coverage.append({'suite': 'print', **item})
        except (ImportError, RuntimeError, ByaxError) as exc:
            coverage.append(
                {
                    'suite': 'print',
                    'rule': 'rendered_geometry',
                    'status': 'unmeasurable',
                    'reason': 'Fresh layout verification unavailable: ' + str(exc),
                }
            )
    if 'editorial' in expanded:
        for b in doc['blocks']:
            if b.get('attrs', {}).get('intentional') or b.get('attrs', {}).get('code'):
                continue
            for match in re.finditer(r'\b(\w+)\s+\1\b', b.get('text', ''), re.IGNORECASE):
                add(
                    'word.repeated',
                    'editorial',
                    'advisory',
                    'Repeated word; this may be intentional.',
                    b['id'],
                    match.start(),
                    match.end(),
                )
        coverage.append(
            {
                'suite': 'editorial',
                'rule': 'grammar_voice_factual',
                'status': 'unmeasurable',
                'reason': 'Deterministic repetition checking does not evaluate grammar, voice, or factual support.',
            }
        )
    if 'spelling' in expanded:
        language = doc.get('metadata', {}).get('language', 'en').split('-')[0]
        try:
            from spellchecker import SpellChecker

            checker = SpellChecker(language=language)
            checker.word_frequency.load_words(
                config.get('dictionary', []) + doc.get('settings', {}).get('dictionary', [])
            )
            for b in doc['blocks']:
                if b.get('attrs', {}).get('intentional') or b.get('attrs', {}).get('code'):
                    continue
                for match in re.finditer(r"\b[^\W\d_]+(?:'[^\W\d_]+)?\b", b.get('text', ''), re.UNICODE):
                    word = match.group()
                    if word.lower() in checker.unknown([word]):
                        add(
                            'spelling.unknown',
                            'spelling',
                            'advisory',
                            'Word is absent from the selected local dictionary.',
                            b['id'],
                            match.start(),
                            match.end(),
                            word=word,
                        )
        except (ImportError, ValueError):
            coverage.append(
                {
                    'suite': 'spelling',
                    'status': 'unmeasurable',
                    'reason': f'No installed spelling dictionary for {language}.',
                }
            )
    if profile_scope and profile is not None:
        for family in config.get('rule_families', []):
            if family in ('grammar', 'factual', 'style'):
                coverage.append(
                    {
                        'suite': family,
                        'status': 'unmeasurable',
                        'reason': 'This selected family requires a configured reviewer and a separate explicit review call; deterministic checks do not establish it.',
                    }
                )
    errors = sum(f['severity'] == 'error' for f in findings)
    incomplete = any(c['status'] in ('unmeasurable', 'incomplete', 'skipped', 'failed') for c in coverage)
    report = {
        'schema_version': 1,
        'document_id': doc['id'],
        'revision': doc['revision'],
        'validation': 'failed' if errors else 'incomplete' if incomplete else 'passed',
        'findings': findings,
        'coverage': coverage,
        'summary': {
            'total': len(findings),
            'errors': errors,
            'warnings': sum(f['severity'] == 'warning' for f in findings),
            'advisory': sum(f['severity'] == 'advisory' for f in findings),
            'returned': min(len(findings), limit),
            'truncated': len(findings) > limit,
            'incomplete': incomplete,
        },
    }
    if profile is not None:
        from .profiles import filter_findings

        report = filter_findings(report, config)
    report['findings'] = report['findings'][:limit]
    return report


def _field_filled(field):
    if not isinstance(field, dict):
        return field is not None and field != ''
    value = field.get('value')
    if value is None or value == '':
        return False
    kind = field.get('type', 'text')
    if kind in ('number', 'integer'):
        return type(value) in ((int,) if kind == 'integer' else (int, float))
    if kind in ('boolean', 'bool'):
        return type(value) is bool
    if kind in ('enum', 'choice'):
        return value in field.get('options', field.get('choices', []))
    if kind == 'date':
        from datetime import date

        try:
            date.fromisoformat(value)
            return True
        except (ValueError, TypeError):
            return False
    return isinstance(value, str) and bool(value.strip())
