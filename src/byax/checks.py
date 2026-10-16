"""Deterministic, offline checks with explicit coverage, dependencies and finding lifecycle.

Checks never change document content. Each result belongs to a dependency unit
(one block, a context-sensitive rule group, or the whole document) whose
fingerprint records exactly what the result depends on. Incremental runs reuse
only units whose fingerprints still match and re-evaluate everything else.
"""

import copy
import re

from . import fiction_checks
from .errors import ByaxError
from .model import object_hash, text_of, validate_document

ENGINE = 'byax-deterministic/2'
SUITES = (
    'structure',
    'accessibility',
    'editorial',
    'grammar',
    'consistency',
    'requirements',
    'print',
    'layout',
    'spelling',
    'factual',
    'publication',
    'fiction',
)
DEFAULT_SUITES = ['structure', 'accessibility', 'requirements', 'print']
PUBLICATION_SUITES = ('structure', 'accessibility', 'requirements', 'print', 'editorial')
BLOCK_SUITES = {'spelling', 'grammar', 'editorial', 'factual', 'accessibility', 'fiction'}
STATUSES = ('open', 'accepted edit', 'dismissed', 'intentional exception', 'stale', 'needs review')
# Manual transitions. "accepted edit" is reached only by applying a repair and
# "stale" only by dependency invalidation; a recheck replaces stale findings.
TRANSITIONS = {
    'open': ('dismissed', 'intentional exception', 'needs review'),
    'needs review': ('open', 'dismissed', 'intentional exception'),
    'dismissed': ('open',),
    'intentional exception': ('open',),
    'accepted edit': (),
    'stale': (),
}
LEVELS = {'conservative': 0, 'balanced': 1, 'broad': 2}
INTENTIONAL_ATTRS = ('intentional', 'code', 'quotation', 'lyric', 'dialect', 'verbatim')
INTENTIONAL_STYLES = ('code', 'quotation')
FORMATTING_MARKS_EXCLUDED = ('citation', 'reference', 'field', 'comment', 'cross_reference', 'bookmark')

# rule: (suite, family, level, languages or None for every language, severity)
RULES = {
    'grammar.double_space': ('grammar', 'grammar', 'conservative', None, 'advisory'),
    'grammar.space_before_punctuation': ('grammar', 'grammar', 'conservative', ('en',), 'advisory'),
    'grammar.missing_space': ('grammar', 'grammar', 'balanced', ('en',), 'advisory'),
    'grammar.repeated_punctuation': ('grammar', 'grammar', 'balanced', None, 'advisory'),
    'grammar.sentence_capital': ('grammar', 'grammar', 'balanced', ('en',), 'advisory'),
    'grammar.article': ('grammar', 'grammar', 'balanced', ('en',), 'advisory'),
    'grammar.unbalanced': ('grammar', 'grammar', 'conservative', None, 'warning'),
    'word.repeated': ('editorial', 'editorial', 'balanced', None, 'advisory'),
    'editorial.sentence_length': ('editorial', 'editorial', 'broad', None, 'advisory'),
    'spelling.unknown': ('spelling', 'spelling', 'conservative', None, 'advisory'),
    'terminology.preferred': ('spelling', 'spelling', 'conservative', None, 'warning'),
    'consistency.term_variant': ('consistency', 'consistency', 'balanced', None, 'warning'),
    'consistency.acronym': ('consistency', 'consistency', 'conservative', None, 'warning'),
    'factual.quotation': ('factual', 'factual', 'conservative', None, 'warning'),
    'factual.library_citation': ('factual', 'factual', 'conservative', None, 'warning'),
}
RULES.update(fiction_checks.RULES)
# Coverage that deterministic rules never provide; always reported for the suite.
UNMEASURED = {
    'grammar': (
        'grammar.agreement_structure',
        'Agreement, sentence structure and ambiguous constructions require a configured reviewer; deterministic punctuation rules do not establish them.',
    ),
    'editorial': (
        'editorial.clarity_voice',
        'Deterministic repetition and length rules do not evaluate clarity, transitions, readability or consistent voice.',
    ),
    'consistency': (
        'consistency.statements',
        'Conflicting dates, numbers, names and statements require semantic interpretation by a configured reviewer.',
    ),
    'fiction': (
        'fiction.craft_judgement',
        'Word lists and counts cannot judge voice, intent or effect; these advisory flags only point at places to reread.',
    ),
    'factual': (
        'factual.uncited_claims',
        'Uncited factual claims are not identified deterministically; claim interpretation needs a configured reviewer.',
    ),
}
_SPELLERS = {}
_ABBREVIATIONS = {
    'e.g',
    'i.e',
    'etc',
    'vs',
    'cf',
    'mr',
    'mrs',
    'ms',
    'dr',
    'st',
    'no',
    'fig',
    'approx',
    'p',
    'pp',
}
_CONSONANT_VOWELS = ('uni', 'use', 'usu', 'usa', 'ute', 'uti', 'eu', 'one', 'once', 'ure', 'ewe')
_SILENT_H = ('hour', 'honest', 'honor', 'honour', 'heir')
_LINK_TEXT = {'click here', 'here', 'this', 'link', 'more', 'read more', 'this link'}


def run_checks(doc, suites=None, *, limit=100, profile=None, scope=None, previous=None, track=False):
    """Run deterministic suites; unbounded totals; never a clean pass for missing coverage.

    ``scope`` selects the document (default), a block, table cell or section heading
    ID, a list of IDs (batch), or ``{'target', 'start', 'end'}`` selection.
    ``previous`` is an earlier tracked report; unchanged units are reused.
    """
    profile_id = profile if isinstance(profile, str) else None
    if profile_id is not None and profile_id not in doc.get('profiles', {}):
        raise ByaxError('missing_target', 'Unknown check profile.', target=profile)
    config = doc['profiles'][profile_id] if profile_id else profile
    profile_scope = (
        suites is None or suites == 'publication' or isinstance(suites, list) and 'publication' in suites
    )
    if suites is None:
        if config is not None:
            from .profiles import check_suites

            suites = check_suites(config)
        else:
            suites = list(DEFAULT_SUITES)
    if isinstance(suites, str):
        suites = [suites]
    if not isinstance(suites, list) or any(s not in SUITES for s in suites):
        raise ByaxError('invalid_input', 'Unknown check suite.', supported=list(SUITES))
    if type(limit) is not int or limit < 0:
        raise ByaxError('invalid_input', 'Finding limit must be a nonnegative integer.')
    expanded = set(suites)
    if 'publication' in expanded:
        if config is not None:
            from .profiles import check_suites

            expanded.update(check_suites(config))
        else:
            expanded.update(PUBLICATION_SUITES)
        expanded.remove('publication')
    if 'print' in expanded:
        expanded.add('layout')
    selection = resolve_scope(doc, scope)
    library_expansion = (
        'factual' in expanded
        and selection['kind'] != 'document'
        and any(source.get('library') for source in doc.get('references', {}).values())
    )
    if library_expansion:
        selection = resolve_scope(doc, None)
    ctx = _Context(doc, config, profile_id, sorted(expanded), selection)
    if 'factual' in expanded:
        ctx.prepare_library_evidence()
    compatible = _compatible(previous, ctx)
    units, evaluation = (
        {},
        {'mode': 'incremental' if compatible else 'full', 'rechecked': [], 'reused': [], 'expanded': []},
    )
    if library_expansion:
        evaluation['expanded'].append(
            {
                'unit': 'library citations',
                'reason': 'External library dependencies may have changed; factual scope expanded to the whole document.',
            }
        )
    if previous is not None and not compatible:
        evaluation['reason'] = (
            'Previous report used different suites, scope, profile or engine; every unit was rechecked.'
        )
    for key, fingerprint in ctx.unit_fingerprints().items():
        prior = (previous or {}).get('units', {}).get(key) if compatible else None
        if prior is not None and prior.get('fingerprint') == fingerprint:
            units[key] = {
                'fingerprint': fingerprint,
                'findings': copy.deepcopy(prior['findings']),
                'coverage': copy.deepcopy(prior['coverage']),
                'evaluated_revision': prior.get('evaluated_revision'),
            }
            for finding in units[key]['findings']:
                finding['revision'] = doc['revision']
            evaluation['reused'].append(key)
            continue
        findings, coverage = ctx.evaluate(key)
        units[key] = {
            'fingerprint': fingerprint,
            'findings': findings,
            'coverage': coverage,
            'evaluated_revision': doc['revision'],
        }
        evaluation['rechecked'].append(key)
        if key.startswith('context:') and selection['kind'] != 'document':
            evaluation['expanded'].append(
                {
                    'unit': key,
                    'reason': 'Context-sensitive rules were evaluated over the whole document; findings are limited to the selected scope.',
                }
            )
    findings, coverage = [], []
    for key, unit in units.items():
        findings.extend(f for f in unit['findings'] if _in_scope(f, selection))
        coverage.extend(unit['coverage'])
    coverage = _merge_coverage(coverage)
    for suite in sorted(expanded):
        coverage.insert(
            0,
            {
                'suite': suite,
                'rule': '*',
                'status': 'completed',
                'scope': selection['label'],
                'engine': ENGINE,
                'level': 'report',
                'rules': sorted(rule for rule, spec in RULES.items() if spec[0] == suite),
            },
        )
    for suite in sorted(expanded & set(UNMEASURED)):
        coverage.append(
            {
                'suite': suite,
                'rule': UNMEASURED[suite][0],
                'status': 'unmeasurable',
                'reason': UNMEASURED[suite][1],
                'scope': selection['label'],
                'level': 'report',
            }
        )
    if selection['kind'] != 'document':
        document_suites = sorted(expanded - BLOCK_SUITES - {'consistency'})
        if document_suites:
            coverage.append(
                {
                    'suite': ','.join(document_suites),
                    'rule': 'document_rules',
                    'status': 'skipped',
                    'level': 'report',
                    'scope': selection['label'],
                    'reason': 'Document-wide requirements, structure and layout are outside the selected scope.',
                }
            )
    if profile_scope and config is not None:
        families = (config if isinstance(config, dict) else {}).get('rule_families', [])
        for family in families:
            if family in ('grammar', 'factual', 'style'):
                coverage.append(
                    {
                        'suite': family,
                        'rule': family + '.model_review',
                        'status': 'unmeasurable',
                        'level': 'report',
                        'reason': 'This selected family requires a configured reviewer and a separate explicit review call; deterministic checks do not establish it.',
                    }
                )
    errors = sum(f['severity'] == 'error' for f in findings)
    incomplete = any(
        c['status'] in ('unmeasurable', 'incomplete', 'skipped', 'failed', 'stale') and not c.get('by_policy')
        for c in coverage
    )
    statuses = {}
    for finding in findings:
        statuses[finding['status']] = statuses.get(finding['status'], 0) + 1
    report = {
        'schema_version': 1,
        'document_id': doc['id'],
        'revision': doc['revision'],
        'engine': ENGINE,
        'validation': 'failed' if errors else 'incomplete' if incomplete else 'passed',
        'suites': sorted(expanded),
        'scope': selection['label'],
        'profile': ctx.profile_label,
        'findings': findings,
        'coverage': coverage,
        'evaluation': evaluation,
        'evidence': [c['evidence'] for c in coverage if 'evidence' in c],
        'summary': {
            'total': len(findings),
            'errors': errors,
            'warnings': sum(f['severity'] == 'warning' for f in findings),
            'advisory': sum(f['severity'] == 'advisory' for f in findings),
            'blocking': errors,
            'returned': min(len(findings), limit),
            'truncated': len(findings) > limit,
            'incomplete': incomplete,
            'by_status': statuses,
            'intentional_exceptions': statuses.get('intentional exception', 0),
        },
    }
    if track:
        report['units'] = units
        report['parameters'] = ctx.parameters()
    report['findings'] = findings[:limit]
    return report


def check_batch(documents, suites=None, **options):
    """Independent per-document outcomes for batch inputs; one failure never hides others."""
    results = []
    for index, doc in enumerate(documents):
        try:
            report = run_checks(doc, suites, **options)
            results.append(
                {'index': index, 'document_id': doc.get('id'), 'execution': 'completed', 'report': report}
            )
        except (ByaxError, KeyError, TypeError, AttributeError) as exc:
            error = (
                exc
                if isinstance(exc, ByaxError)
                else ByaxError('invalid_input', 'Input is not a valid native document.')
            )
            results.append(
                {
                    'index': index,
                    'document_id': doc.get('id') if isinstance(doc, dict) else None,
                    'execution': 'failed',
                    'error': error.to_dict(),
                }
            )
    validations = [r['report']['validation'] for r in results if r['execution'] == 'completed']
    overall = (
        'failed'
        if 'failed' in validations or len(validations) < len(results)
        else 'incomplete'
        if 'incomplete' in validations
        else 'passed'
    )
    return {'schema_version': 1, 'validation': overall, 'results': results, 'total': len(results)}


def rule_catalog():
    return {
        rule: {
            'suite': s,
            'family': f,
            'sensitivity': level,
            'languages': list(langs) if langs else 'all',
            'severity': severity,
        }
        for rule, (s, f, level, langs, severity) in RULES.items()
    }


# Scope -----------------------------------------------------------------------
def resolve_scope(doc, scope):
    blocks = doc.get('blocks', [])
    if scope in (None, 'document'):
        return {'kind': 'document', 'label': 'document', 'blocks': None, 'targets': None, 'range': None}
    if isinstance(scope, dict):
        if set(scope) - {'target', 'start', 'end'} or not isinstance(scope.get('target'), str):
            raise ByaxError(
                'invalid_input', 'A selection scope requires target, start and end.', field='scope'
            )
        owner = _owner(doc, scope['target'])
        start, end = scope.get('start', 0), scope.get('end')
        text = _object(doc, scope['target']).get('text', '')
        end = len(text) if end is None else end
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
            raise ByaxError('invalid_input', 'Selection range is out of bounds.', field='scope')
        return {
            'kind': 'selection',
            'label': {'target': scope['target'], 'start': start, 'end': end},
            'blocks': {owner},
            'targets': {scope['target']},
            'range': (start, end),
        }
    identities = [scope] if isinstance(scope, str) else scope
    if not isinstance(identities, list) or not identities or not all(isinstance(i, str) for i in identities):
        raise ByaxError(
            'invalid_input', 'Scope must be document, an ID, a list of IDs or a selection.', field='scope'
        )
    selected_blocks, targets = set(), set()
    for identity in identities:
        owner = _owner(doc, identity)
        index = next(i for i, b in enumerate(blocks) if b['id'] == owner)
        block = blocks[index]
        if identity == owner and block.get('type') == 'heading':
            level = block.get('attrs', {}).get('level', 1)
            stop = next(
                (
                    i
                    for i in range(index + 1, len(blocks))
                    if blocks[i]['type'] == 'heading' and blocks[i].get('attrs', {}).get('level', 1) <= level
                ),
                len(blocks),
            )
            members = blocks[index:stop]
        else:
            members = [block]
        for member in members:
            selected_blocks.add(member['id'])
            if identity == owner:
                targets.update(_ids(member))
        if identity != owner:
            targets.add(identity)
    first = _owner(doc, identities[0])
    heading = next(b for b in blocks if b['id'] == first).get('type') == 'heading' and identities[0] == first
    kind = 'batch' if len(identities) > 1 else 'section' if heading else 'block'
    return {
        'kind': kind,
        'label': identities if len(identities) > 1 else identities[0],
        'blocks': selected_blocks,
        'targets': targets,
        'range': None,
    }


def _ids(block):
    return (
        {block['id']}
        | {r['id'] for r in block.get('rows', [])}
        | {c['id'] for r in block.get('rows', []) for c in r['cells']}
    )


def _owner(doc, identity):
    for block in doc.get('blocks', []):
        if identity in _ids(block):
            return block['id']
    raise ByaxError('missing_target', 'Check scope target does not exist.', target=identity)


def _object(doc, identity):
    from .model import find_object

    return find_object(doc, identity)


def _in_scope(finding, selection):
    if selection['kind'] == 'document':
        return True
    if not set(finding.get('affected_ids', [])) & selection['targets']:
        return False
    if selection['range'] and finding.get('range'):
        start, end = selection['range']
        found = finding['range']
        return (found['start'] < end and found['end'] > start) or (
            found['start'] == found['end'] and start <= found['start'] <= end
        )
    return True


def _merge_coverage(entries):
    merged = {}
    for entry in entries:
        key = (
            entry.get('suite'),
            entry.get('rule'),
            entry.get('status'),
            entry.get('reason'),
            entry.get('language'),
            object_hash(entry['evidence']) if 'evidence' in entry else None,
        )
        if key in merged:
            for target in entry.get('targets', []):
                if target not in merged[key].setdefault('targets', []):
                    merged[key]['targets'].append(target)
        else:
            merged[key] = copy.deepcopy(entry)
    return list(merged.values())


# Evaluation context and dependency fingerprints --------------------------------
class _Context:
    def __init__(self, doc, config, profile_id, suites, selection):
        from .profiles import section_map, validate_profile

        self.doc, self.suites, self.selection = doc, suites, selection
        self.base = profile_id if profile_id else config
        self.base_config = validate_profile(config, strict=False) if config is not None else None
        self.profile_label = (
            profile_id
            if profile_id
            else ('inline:' + object_hash(self.base_config)[:12] if config is not None else None)
        )
        self.sections = section_map(doc)
        self._profiles = {}
        self._fiction = None
        self.library_evidence = {}
        self.library_fingerprints = {}
        settings = doc.get('settings', {})
        self.env = object_hash(
            {
                'language': doc.get('metadata', {}).get('language'),
                'dictionary': settings.get('dictionary', []),
                'terminology': settings.get('terminology', {}),
                'protections': settings.get('protections', []),
                'background': doc.get('layout', {}).get('background'),
                'base': self.base_config,
                **(
                    {'fiction': [fiction_checks.VERSION, fiction_checks.versions()]}
                    if 'fiction' in suites
                    else {}
                ),
            }
        )

    def prepare_library_evidence(self):
        from .library_checks import evidence_key, read_citation

        for block in self.doc.get('blocks', []):
            snapshots = {}
            objects = (
                [block]
                if block.get('type') != 'table'
                else [cell for row in block.get('rows', []) for cell in row['cells']]
            )
            for obj in objects:
                config = self.config_for(obj['id'])[0]
                for mark in _marks(obj):
                    if mark.get('type') not in ('citation', 'reference'):
                        continue
                    source_id = mark.get('value')
                    snapshot = read_citation(self.doc, source_id, mark, config)
                    if snapshot is not None:
                        key = evidence_key(source_id, mark)
                        snapshots[key] = snapshot
                        self.library_evidence[(obj['id'], key)] = snapshot
            if snapshots:
                self.library_fingerprints[block['id']] = object_hash(snapshots)

    def parameters(self):
        return {
            'suites': self.suites,
            'scope': self.selection['label'],
            'profile': self.profile_label,
            'profile_config': self.base_config
            if self.profile_label and self.profile_label.startswith('inline:')
            else None,
            'engine': ENGINE,
            'library_fingerprints': self.library_fingerprints,
        }

    def profile_for(self, identity):
        assignments = self.doc.get('settings', {}).get('section_profiles', {})
        chain = tuple(h for h in self.sections.get(identity, []) if h in assignments)
        if chain not in self._profiles:
            from .profiles import effective_profile

            self._profiles[chain] = effective_profile(
                self.doc, identity, self.base_config, sections=self.sections
            )
        return self._profiles[chain]

    def unit_fingerprints(self):
        doc, result = self.doc, {}
        if self.suites and set(self.suites) & BLOCK_SUITES:
            for block in doc.get('blocks', []):
                if self.selection['blocks'] is None or block['id'] in self.selection['blocks']:
                    result['block:' + block['id']] = self.block_fingerprint(block)
        if 'accessibility' in self.suites:
            result['context:structure'] = object_hash(
                {
                    'env': self.env,
                    'sections': doc.get('settings', {}).get('section_profiles', {}),
                    'headings': [
                        (b['id'], b.get('attrs', {}).get('level'))
                        for b in doc.get('blocks', [])
                        if b.get('type') == 'heading'
                    ],
                    'protections': doc.get('settings', {}).get('protections', []),
                }
            )
        if 'consistency' in self.suites:
            result['context:consistency'] = object_hash(
                {
                    'env': self.env,
                    'text': [(b['id'], _texts(b), _intentional(b)) for b in doc.get('blocks', [])],
                    'sections': doc.get('settings', {}).get('section_profiles', {}),
                    'profiles': doc.get('profiles', {}),
                }
            )
        if 'fiction' in self.suites:
            result.update(fiction_checks.unit_fingerprints(self))
        if self.selection['kind'] == 'document' and set(self.suites) - BLOCK_SUITES - {'consistency'}:
            result['document'] = document_fingerprint(doc, self.base_config)
        return result

    def block_fingerprint(self, block):
        cited = sorted(
            {m.get('value') for m in _marks(block) if m.get('type') in ('citation', 'reference')}
            | {k for k, r in self.doc.get('references', {}).items() if r.get('target') in _ids(block)}
        )
        sources = {key: object_hash(self.doc.get('references', {}).get(key)) for key in cited}
        assets = {
            key: self.doc.get('assets', {}).get(key, {}).get('name')
            for key in [block.get('attrs', {}).get('asset')]
            if key
        }
        resolved = self.profile_for(block['id'])
        return object_hash(
            {
                'block': block,
                'env': self.env,
                'sources': sources,
                'library': self.library_fingerprints.get(block['id']),
                'assets': assets,
                'profile': resolved['fingerprint'] if resolved else None,
            }
        )

    def evaluate(self, key):
        if key.startswith('block:'):
            block = next(b for b in self.doc['blocks'] if b['id'] == key[6:])
            return _evaluate_block(self, block, key)
        if key == 'context:structure':
            return _evaluate_structure(self, key)
        if key == 'context:consistency':
            return _evaluate_consistency(self, key)
        if key.startswith(('fiction:', 'context:fiction_')):
            sink = _Sink(self, key)
            fiction_checks.evaluate(self, sink, key)
            return sink.findings, sink.coverage
        return _evaluate_document(self, key)

    def config_for(self, identity):
        resolved = self.profile_for(identity) if identity else None
        if resolved is not None:
            return resolved['profile'], resolved['fingerprint']
        return self.base_config, None

    def level(self, identity):
        config = self.config_for(identity)[0]
        return LEVELS[config['sensitivity']] if config else LEVELS['balanced']


def document_fingerprint(doc, base_config=None):
    return object_hash(
        {
            'content': {
                k: v for k, v in doc.items() if k not in ('review', 'history', 'requests', 'revision')
            },
            'base': base_config,
        }
    )


def _texts(block):
    if block.get('type') == 'table':
        return [c.get('text', '') for r in block.get('rows', []) for c in r['cells']]
    return block.get('text', '')


def _marks(block):
    return [m for m in block.get('marks', []) if isinstance(m, dict)]


def _intentional(block):
    attrs = block.get('attrs', {})
    return any(attrs.get(key) for key in INTENTIONAL_ATTRS) or block.get('style') in INTENTIONAL_STYLES


def _language(doc, block):
    value = block.get('attrs', {}).get('language') or doc.get('metadata', {}).get('language') or ''
    return value.split('-')[0].lower() if isinstance(value, str) else ''


class _Sink:
    def __init__(self, ctx, unit):
        self.ctx, self.unit, self.findings, self.coverage = ctx, unit, [], []

    def add(
        self,
        rule,
        family,
        severity,
        message,
        target=None,
        start=None,
        end=None,
        text=None,
        operations=None,
        repair=None,
        measured=None,
        evidence=None,
        outcome=None,
        config_target=None,
    ):
        doc = self.ctx.doc
        config, profile_fingerprint = self.ctx.config_for(config_target or target)
        f = {
            'rule': rule,
            'family': family,
            'suite': RULES.get(rule, (family,))[0],
            'severity': severity,
            'policy': 'strict' if severity == 'error' else 'advisory',
            'explanation': message,
            'affected_ids': [target] if target else [],
            'range': {'start': start, 'end': end} if start is not None else None,
            'text': text,
            'measured': measured or {},
            'evidence': evidence or [],
            'operations': operations or [],
            'status': 'open',
            'revision': doc['revision'],
            'profile': self.ctx.profile_label,
            'profile_version': (config or {}).get('version', 1),
            'profile_fingerprint': profile_fingerprint,
            'engine': ENGINE,
            'rule_version': 1,
            'source_versions': {},
            'scope': target or 'document',
            'dependencies': {'unit': self.unit},
        }
        if outcome is not None:
            f['outcome'] = outcome
        f['repair'] = repair or (
            {'kind': 'automatic', 'separate_operation': True} if f['operations'] else {'kind': 'manual'}
        )
        if f['operations'] and target and _protected(doc, target):
            f['operations'], f['repair'] = (
                [],
                {
                    'kind': 'blocked',
                    'reason': 'Target is protected; repair requires removing protection in a separate action.',
                },
            )
        if evidence:
            f['source_versions'] = {e['source_id']: e.get('source_version') for e in evidence}
        identity = {k: f[k] for k in ('rule', 'affected_ids', 'range', 'measured', 'explanation')}
        f['id'] = 'finding_' + object_hash(identity)[:20]
        while any(existing['id'] == f['id'] for existing in self.findings):
            identity['occurrence'] = identity.get('occurrence', 1) + 1
            f['id'] = 'finding_' + object_hash(identity)[:20]
        if config:
            from .profiles import apply_exception

            apply_exception(f, config)
        self.findings.append(f)
        return f

    def cover(self, suite, rule, status, reason, **values):
        self.coverage.append({'suite': suite, 'rule': rule, 'status': status, 'reason': reason, **values})


def _protected(doc, target):
    owner = None
    for block in doc.get('blocks', []):
        if target in _ids(block):
            owner = block['id']
    return any(
        rule.get('target') in (target, owner)
        and rule.get('kind')
        in ('text', 'wording', 'values', 'table_values', 'all', 'object', 'visual', 'visual_region')
        for rule in doc.get('settings', {}).get('protections', [])
    )


def _replace_ops(target, text, start, end, replacement):
    ops = []
    if end > start:
        ops.append(
            {
                'type': 'delete_text',
                'target': target,
                'start': start,
                'end': end,
                'expected_text': text[start:end],
            }
        )
    if replacement:
        ops.append({'type': 'insert_text', 'target': target, 'offset': start, 'text': replacement})
    return ops


# Block rules -------------------------------------------------------------------
def _evaluate_block(ctx, block, key):
    sink, doc, suites = _Sink(ctx, key), ctx.doc, ctx.suites
    attrs = block.get('attrs', {})
    level = ctx.level(block['id'])
    language = _language(doc, block)
    objects = (
        [block] if block.get('type') != 'table' else [c for r in block.get('rows', []) for c in r['cells']]
    )
    texts = [
        (o['id'], o.get('text', ''))
        for o in objects
        if o.get('text') and block.get('type') not in ('image', 'toc')
    ]
    intentional = _intentional(block)
    for suite in ('spelling', 'grammar', 'editorial'):
        if suite in suites and intentional and texts:
            sink.cover(
                suite,
                '*',
                'skipped',
                'Passage is marked intentional (code, quotation, lyric, dialect or verbatim); it is not treated as an error.',
                by_policy=True,
                targets=[block['id']],
            )
    if not intentional:
        for target, text in texts:
            if 'grammar' in suites:
                _grammar(sink, target, text, language, level, cell=target != block['id'])
            if 'editorial' in suites:
                _editorial(sink, target, text, level)
            if 'spelling' in suites:
                _spelling(sink, ctx, block, target, text, language)
    if 'factual' in suites:
        for obj in objects:
            _factual(sink, ctx, obj)
    if 'fiction' in suites:
        fiction_checks.evaluate_block(ctx, sink, block)
    if 'accessibility' in suites:
        _accessibility_block(sink, ctx, block, attrs)
    return sink.findings, sink.coverage


def _allowed(sink, rule, language, level):
    suite, _, needed, languages, _ = RULES[rule]
    if languages and language not in languages:
        sink.cover(
            suite,
            rule,
            'unmeasurable',
            f'No deterministic rule is available for language {language or "unspecified"}.',
            language=language,
        )
        return False
    return LEVELS[needed] <= level


def _grammar(sink, target, text, language, level, cell=False):
    def add(rule, message, start, end, replacement=None, measured=None):
        operations = _replace_ops(target, text, start, end, replacement) if replacement is not None else []
        sink.add(
            rule,
            'grammar',
            RULES[rule][4],
            message,
            target,
            start,
            end,
            text[start:end],
            operations,
            measured=measured,
        )

    if _allowed(sink, 'grammar.double_space', language, level):
        for match in re.finditer(r'(?<=\S)( {2,})(?=\S)', text):
            add('grammar.double_space', 'Several spaces separate words.', match.start(1), match.end(1), ' ')
    if _allowed(sink, 'grammar.space_before_punctuation', language, level):
        for match in re.finditer(r'(?<=\w)( +)(?=[,.;:!?](?:\s|$))', text):
            add(
                'grammar.space_before_punctuation',
                'Space before punctuation.',
                match.start(1),
                match.end(1),
                '',
            )
    if _allowed(sink, 'grammar.missing_space', language, level):
        for match in re.finditer(r'(?<=[a-z])([,;])(?=[A-Za-z])', text):
            add(
                'grammar.missing_space',
                'Punctuation is not followed by a space.',
                match.end(1),
                match.end(1),
                ' ',
            )
    if _allowed(sink, 'grammar.repeated_punctuation', language, level):
        for match in re.finditer(r'([,;:!?])\1+', text):
            add(
                'grammar.repeated_punctuation', 'Punctuation is repeated.', match.start() + 1, match.end(), ''
            )
    if _allowed(sink, 'grammar.sentence_capital', language, level):
        for match in re.finditer(r'(?:^|(?<=[.!?])\s+)([a-z][a-z]*)\b', text):
            before = text[: match.start(1)].rstrip()
            if not before and cell:
                continue
            previous = re.search(r'([\w.]+)[.!?]$', before)
            if before and (
                previous is None
                or previous.group(1).lower().rstrip('.') in _ABBREVIATIONS
                or '.' in previous.group(1)
            ):
                continue
            start = match.start(1)
            add(
                'grammar.sentence_capital',
                'Sentence begins with a lowercase letter.',
                start,
                start + 1,
                text[start].upper(),
            )
    if _allowed(sink, 'grammar.article', language, level):
        for match in re.finditer(r'\b(a|an|A|An)\s+([A-Za-z][\w-]*)', text):
            article, word = match.group(1), match.group(2)
            if word.isupper() or not word.isascii():
                continue
            lower = word.lower()
            vowel = (lower[0] in 'aeiou' and not lower.startswith(_CONSONANT_VOWELS)) or lower.startswith(
                _SILENT_H
            )
            expected = 'an' if vowel else 'a'
            if article.lower() != expected:
                replacement = expected.capitalize() if article[0].isupper() else expected
                add(
                    'grammar.article',
                    f'Use "{expected}" before "{word}".',
                    match.start(1),
                    match.end(1),
                    replacement,
                    {'article': article, 'word': word},
                )
    if _allowed(sink, 'grammar.unbalanced', language, level):
        for opening, closing in (('(', ')'), ('[', ']'), ('{', '}'), ('“', '”')):
            if text.count(opening) != text.count(closing):
                add(
                    'grammar.unbalanced',
                    f'Unbalanced {opening}{closing} pair.',
                    0,
                    len(text),
                    measured={
                        'opening': text.count(opening),
                        'closing': text.count(closing),
                        'pair': opening + closing,
                    },
                )
        if text.count('"') % 2:
            add(
                'grammar.unbalanced',
                'Unbalanced straight quotation marks.',
                0,
                len(text),
                measured={'pair': '"', 'count': text.count('"')},
            )


def _editorial(sink, target, text, level):
    if LEVELS['balanced'] <= level:
        for match in re.finditer(r'\b(\w+)(\s+)\1\b', text, re.IGNORECASE):
            sink.add(
                'word.repeated',
                'editorial',
                'advisory',
                'Repeated word; this may be intentional.',
                target,
                match.start(),
                match.end(),
                text[match.start() : match.end()],
                _replace_ops(target, text, match.end(1), match.end(), ''),
            )
    if LEVELS['broad'] <= level:
        for match in re.finditer(r'[^.!?]+[.!?]?', text):
            words = len(re.findall(r'\w+', match.group()))
            if words > 40:
                sink.add(
                    'editorial.sentence_length',
                    'editorial',
                    'advisory',
                    'Sentence is long; consider splitting it.',
                    target,
                    match.start(),
                    match.end(),
                    match.group(),
                    measured={'words': words, 'limit': 40},
                )


def _speller(language):
    if language not in _SPELLERS:
        try:
            from spellchecker import SpellChecker

            _SPELLERS[language] = SpellChecker(language=language)
        except (ImportError, ValueError):
            _SPELLERS[language] = None
    return _SPELLERS[language]


def _spelling(sink, ctx, block, target, text, language):
    doc = ctx.doc
    config = ctx.config_for(block['id'])[0] or {}
    terminology = doc.get('settings', {}).get('terminology', {})
    accepted = {
        w.lower() for w in config.get('dictionary', []) + doc.get('settings', {}).get('dictionary', [])
    }
    accepted.update(term.lower() for term in terminology)
    for preferred, variants in terminology.items():
        for variant in variants if isinstance(variants, list) else []:
            for match in re.finditer(r'\b' + re.escape(variant) + r'\b', text, re.IGNORECASE):
                sink.add(
                    'terminology.preferred',
                    'spelling',
                    'warning',
                    f'Use the preferred term "{preferred}".',
                    target,
                    match.start(),
                    match.end(),
                    match.group(),
                    _replace_ops(target, text, match.start(), match.end(), preferred),
                    measured={'preferred': preferred, 'variant': match.group()},
                )
    checker = _speller(language)
    if checker is None:
        sink.cover(
            'spelling',
            'spelling.unknown',
            'unmeasurable',
            f'No installed spelling dictionary for {language or "unspecified"}.',
            language=language,
        )
        return
    for match in re.finditer(r"\b[^\W\d_]+(?:'[^\W\d_]+)?\b", text, re.UNICODE):
        word = match.group()
        if word.lower() not in accepted and word.lower() in checker.unknown([word]):
            sink.add(
                'spelling.unknown',
                'spelling',
                'advisory',
                'Word is absent from the selected local dictionary.',
                target,
                match.start(),
                match.end(),
                word,
                measured={'word': word},
            )


def _factual(sink, ctx, block):
    from .profiles import source_hash

    doc = ctx.doc
    config = ctx.config_for(block['id'])[0] or {}
    authorized = (
        config.get('references')
        if config.get('source_authority', 'exclusive') == 'exclusive' and config.get('references')
        else None
    )
    for mark in _marks(block):
        if mark.get('type') not in ('citation', 'reference'):
            continue
        source_id, text = mark.get('value'), block.get('text', '')
        source = doc.get('references', {}).get(source_id)
        claim = text[mark['start'] : mark['end']]
        if source is None:
            continue
        if authorized is not None and source_id not in authorized:
            sink.cover(
                'factual',
                'factual.source_policy',
                'skipped',
                'Cited source is not authorized by the exclusive profile.',
                by_policy=True,
                targets=[block['id']],
            )
            continue
        if source.get('library'):
            from .library_checks import check_citation, evidence_key

            snapshot = ctx.library_evidence.get((block['id'], evidence_key(source_id, mark)))
            if snapshot is not None:
                check_citation(sink, block, mark, source_id, snapshot)
                continue
        quoted = re.fullmatch(r'\s*["“](.+?)["”]\s*', claim, re.DOTALL)
        version = {
            'version': source.get('version'),
            'retrieved_at': source.get('retrieved_at'),
            'content_hash': source_hash(source),
        }
        if not quoted:
            sink.cover(
                'factual',
                'factual.claim_interpretation',
                'unmeasurable',
                'Paraphrased or inferred claims need a configured reviewer; citation existence alone does not establish support.',
                targets=[block['id']],
            )
            continue
        passage = quoted.group(1)
        materials = [source[k] for k in ('text', 'excerpt', 'content') if isinstance(source.get(k), str)]
        materials += [
            e if isinstance(e, str) else e.get('text', '')
            for e in source.get('excerpts', [])
            if isinstance(e, (str, dict))
        ]
        if any(passage in material for material in materials):
            record = {
                'claim': claim,
                'target': block['id'],
                'range': {'start': mark['start'], 'end': mark['end']},
                'outcome': 'supported',
                'evidence': [
                    {
                        'source_id': source_id,
                        'excerpt': passage,
                        'relation': 'quotation',
                        'source_version': version,
                    }
                ],
                'note': 'Exact quotation found in the selected source; this establishes consistency with that source, not universal truth.',
            }
            sink.coverage.append(
                {'suite': 'factual', 'rule': 'factual.quotation', 'status': 'completed', 'evidence': record}
            )
        else:
            sink.add(
                'factual.quotation',
                'factual',
                'warning',
                'Quoted passage is not found in the cited source; support is not established.',
                block['id'],
                mark['start'],
                mark['end'],
                claim,
                outcome='not_established',
                measured={'source_id': source_id},
                repair={
                    'kind': 'manual',
                    'guidance': 'Correct the quotation or the citation; source corrections are a separate action.',
                },
            )
            sink.findings[-1]['source_versions'] = {source_id: version}
            sink.findings[-1]['claim'] = claim


def _contrast(foreground, background):
    def luminance(value):
        value = value.lstrip('#')
        channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    a, b = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) is not None


def _accessibility_block(sink, ctx, block, attrs):
    doc = ctx.doc
    kind = block.get('type')
    if kind == 'heading':
        current = attrs.get('level', 1)
        if not isinstance(current, int) or current < 1 or current > 6:
            sink.add(
                'heading.level',
                'accessibility',
                'error',
                'Heading level must be between one and six.',
                block['id'],
            )
        if not block.get('text', '').strip():
            sink.add('heading.empty', 'accessibility', 'warning', 'Heading has no text.', block['id'])
    if kind == 'image':
        alt = attrs.get('alt', '')
        if not str(alt).strip() and not attrs.get('decorative'):
            sink.add('image.alt', 'accessibility', 'warning', 'Image has no text alternative.', block['id'])
        elif re.fullmatch(r'\S+\.(png|jpe?g|gif|svg|webp|bmp|tiff?)', str(alt).strip(), re.IGNORECASE) or str(
            alt
        ).strip() == doc.get('assets', {}).get(attrs.get('asset'), {}).get('name'):
            sink.add(
                'image.alt_filename',
                'accessibility',
                'warning',
                'Text alternative is a file name, not a description.',
                block['id'],
                measured={'alt': alt},
            )
        if str(attrs.get('wrap', 'inline')).replace('-', '_') in (
            'behind',
            'in_front',
            'front',
            'behind_text',
            'in_front_of_text',
        ) and not attrs.get('anchor'):
            sink.add(
                'reading_order.unanchored',
                'accessibility',
                'warning',
                'Overlay object has no anchor, so its reading order is undefined.',
                block['id'],
            )
    if kind == 'table':
        if not attrs.get('header_rows'):
            sink.add(
                'table.headers',
                'accessibility',
                'warning',
                'Table has no designated header rows.',
                block['id'],
                operations=[{'type': 'set_attributes', 'target': block['id'], 'values': {'header_rows': 1}}],
            )
        else:
            for row in block.get('rows', [])[
                : attrs['header_rows'] if type(attrs['header_rows']) is int else 0
            ]:
                for cell in row['cells']:
                    if not cell.get('text', '').strip():
                        sink.add(
                            'table.header_empty',
                            'accessibility',
                            'warning',
                            'Header cell is empty.',
                            cell['id'],
                            config_target=block['id'],
                        )
    background = doc.get('layout', {}).get('background', '#ffffff')
    background = background if _hex(background) else '#ffffff'
    for mark in _marks(block):
        covered = block.get('text', '')[mark.get('start', 0) : mark.get('end', 0)]
        if mark.get('type') == 'link' and (
            covered.strip().lower() in _LINK_TEXT or re.fullmatch(r'\s*(https?://|www\.)\S+\s*', covered)
        ):
            sink.add(
                'link.text',
                'accessibility',
                'warning',
                'Link text does not describe its destination.',
                block['id'],
                mark['start'],
                mark['end'],
                covered,
            )
        if mark.get('type') in ('color', 'foreground') and _hex(mark.get('value')):
            ratio = _contrast(mark['value'], background)
            if ratio < 4.5:
                sink.add(
                    'color.contrast',
                    'accessibility',
                    'warning',
                    'Text color contrast is below 4.5:1.',
                    block['id'],
                    mark['start'],
                    mark['end'],
                    covered,
                    measured={'ratio': round(ratio, 2), 'minimum': 4.5},
                )


# Context-sensitive rules -------------------------------------------------------
def _evaluate_structure(ctx, key):
    sink, level = _Sink(ctx, key), 0
    for block in ctx.doc.get('blocks', []):
        if block['type'] != 'heading':
            continue
        current = block.get('attrs', {}).get('level', 1)
        if isinstance(current, int) and current > level + 1:
            fix = [{'type': 'set_attributes', 'target': block['id'], 'values': {'level': level + 1}}]
            sink.add(
                'heading.skipped',
                'accessibility',
                'warning',
                'Heading hierarchy skips a level.',
                block['id'],
                operations=fix,
                measured={'previous': level, 'current': current},
            )
        level = current if isinstance(current, int) else level
    return sink.findings, sink.coverage


def _evaluate_consistency(ctx, key):
    sink, doc = _Sink(ctx, key), ctx.doc
    occurrences, definitions, order = {}, {}, 0
    for block in doc.get('blocks', []):
        if _intentional(block):
            continue
        objects = (
            [block]
            if block.get('type') != 'table'
            else [c for r in block.get('rows', []) for c in r['cells']]
        )
        for obj in objects:
            text = obj.get('text', '')
            for match in re.finditer(r"\b[^\W\d_][\w'-]*[^\W_]\b", text):
                word = match.group()
                if word.isupper():
                    continue
                signature = word[0].lower() + word[1:]
                order += 1
                occurrences.setdefault(signature.lower().replace('-', ''), []).append(
                    (obj['id'], block['id'], match.start(), match.end(), word, signature, text, order)
                )
            for match in re.finditer(
                r'\b([A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*){0,7})\s+\(([A-Z][A-Z0-9]{1,9})\)', text
            ):
                acronym = match.group(2)
                words = match.group(1).split()[-len(acronym) :]
                expansion = ' '.join(words).lower()
                definitions.setdefault(acronym, []).append(
                    (obj['id'], block['id'], match.start(), match.end(), expansion, text)
                )
    for items in occurrences.values():
        signatures = {}
        for item in items:
            signatures.setdefault(item[5], []).append(item)
        if len(signatures) < 2 or not any('-' in s or any(c.isupper() for c in s[1:]) for s in signatures):
            continue
        preferred = max(signatures, key=lambda s: (len(signatures[s]), -min(i[7] for i in signatures[s])))
        majority = signatures[preferred][0][4]
        for signature, members in signatures.items():
            if signature == preferred:
                continue
            for target, owner, start, end, word, _, text, _ in members:
                replacement = (
                    word[0] + majority[1:] if word[0].isupper() != majority[0].isupper() else majority
                )
                sink.add(
                    'consistency.term_variant',
                    'consistency',
                    'warning',
                    f'"{word}" is spelled differently elsewhere ("{majority}").',
                    target,
                    start,
                    end,
                    word,
                    _replace_ops(target, text, start, end, replacement),
                    measured={
                        'variant': word,
                        'preferred': majority,
                        'occurrences': {s: len(m) for s, m in signatures.items()},
                    },
                    config_target=owner,
                )
    for acronym, items in definitions.items():
        first = items[0]
        for target, owner, start, end, expansion, text in items[1:]:
            if expansion != first[4]:
                sink.add(
                    'consistency.acronym',
                    'consistency',
                    'warning',
                    f'{acronym} is defined inconsistently.',
                    target,
                    start,
                    end,
                    text[start:end],
                    measured={
                        'acronym': acronym,
                        'first': first[4],
                        'conflicting': expansion,
                        'first_target': first[0],
                    },
                    config_target=owner,
                )
    return sink.findings, sink.coverage


# Document rules -----------------------------------------------------------------
def _evaluate_document(ctx, key):
    sink, doc, suites = _Sink(ctx, key), ctx.doc, ctx.suites
    config = ctx.base_config or {}
    add = sink.add
    if 'structure' in suites:
        try:
            validate_document(doc, include_history=False)
        except ByaxError as exc:
            add('document.schema', 'structure', 'error', exc.message, measured=exc.details)
        for style_name, style in doc.get('styles', {}).items():
            ancestors, parent = {style_name}, style.get('based_on')
            while parent:
                if parent in ancestors:
                    add(
                        'style.cycle',
                        'structure',
                        'error',
                        'Named style inheritance is cyclic.',
                        measured={'style': style_name},
                    )
                    break
                if parent not in doc['styles']:
                    add(
                        'style.parent_missing',
                        'structure',
                        'error',
                        'Named style parent is missing.',
                        measured={'style': style_name, 'parent': parent},
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
                    measured={'style': b['style']},
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
                        measured={'dependency': link, 'uri': dependency.get('uri')},
                    )
            elif b.get('type') == 'image' and b.get('attrs', {}).get('asset') not in doc.get('assets', {}):
                add('asset.missing', 'structure', 'error', 'Image asset is missing.', b['id'])
            for mark in _marks(b):
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
                        b.get('text', '')[mark['start'] : mark['end']],
                    )
        all_ids = {i for b in doc.get('blocks', []) for i in _ids(b)}
        bookmarks = doc.get('settings', {}).get('bookmarks', {})
        for name, bookmark in bookmarks.items():
            if bookmark.get('target') not in all_ids:
                add(
                    'bookmark.orphaned',
                    'structure',
                    'warning',
                    'Bookmark target was deleted.',
                    bookmark.get('target'),
                    measured={'bookmark': name},
                )
        for block in doc.get('blocks', []):
            for mark in _marks(block):
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
                            measured={'destination': mark.get('value')},
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
        headings = {b['id'] for b in doc.get('blocks', []) if b.get('type') == 'heading'}
        for heading in doc.get('settings', {}).get('section_profiles', {}):
            if heading not in headings:
                add(
                    'section_profile.orphaned',
                    'structure',
                    'warning',
                    'Section profile refers to a deleted heading.',
                    measured={'heading': heading},
                )
    if 'accessibility' in suites:
        metadata = doc.get('metadata', {})
        if not str(metadata.get('language') or '').strip():
            add('document.language', 'accessibility', 'warning', 'Document language is not set.')
        if not str(metadata.get('title') or '').strip():
            add('document.title', 'accessibility', 'warning', 'Document title is not set.')
        overlays = [
            b['id']
            for b in doc.get('blocks', [])
            if b.get('type') == 'image'
            and str(b.get('attrs', {}).get('wrap', 'inline')).replace('-', '_')
            in ('behind', 'in_front', 'front', 'behind_text', 'in_front_of_text')
        ]
        if overlays:
            sink.cover(
                'accessibility',
                'reading_order.visual',
                'unmeasurable',
                'Visual overlap of overlay objects against semantic reading order is not measured.',
                targets=overlays,
            )
    content = text_of(doc)
    if 'requirements' in suites:
        for key_, req in doc.get('settings', {}).get('requirements', {}).items():
            kind, value, passed = req.get('kind'), req.get('value'), True
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
                passed = _field_filled(doc.get('fields', {}).get(value), doc)
            else:
                sink.cover('requirements', key_, 'unmeasurable', 'Unsupported requirement kind or value.')
                continue
            if not passed:
                add(
                    'requirement.' + key_,
                    'requirements',
                    'error',
                    req.get('message', f'Requirement {key_} is not satisfied.'),
                    measured={'kind': kind, 'value': value},
                )
        for field_id, definition in doc.get('fields', {}).items():
            if (
                isinstance(definition, dict)
                and definition.get('required')
                and not _field_filled(definition, doc)
            ):
                add(
                    'field.required',
                    'requirements',
                    'error',
                    'Required field is missing or invalid.',
                    measured={'field': field_id},
                )
        for match in re.finditer(r'\{\{\s*([\w.-]+)\s*\}\}', content):
            if not _field_filled(doc.get('fields', {}).get(match.group(1)), doc):
                add(
                    'field.unfilled',
                    'requirements',
                    'warning',
                    'Template field is unfilled.',
                    measured={'field': match.group(1)},
                )
    if 'print' in suites:
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
                            measured={'dpi': round(dpi, 1)},
                        )
    if 'layout' in suites:
        _layout_rules(sink, doc)
    return sink.findings, sink.coverage


def _layout_rules(sink, doc):
    layout = doc.get('layout', {})
    margins = layout.get('margins', {})
    numbers = (layout.get('width'), margins.get('left', 0), margins.get('right', 0))
    body = numbers[0] - numbers[1] - numbers[2] if all(type(v) in (int, float) for v in numbers) else None
    blocks = doc.get('blocks', [])
    for index, block in enumerate(blocks):
        attrs = block.get('attrs', {})
        width = attrs.get('width')
        if (
            block.get('type') == 'table'
            and isinstance(attrs.get('column_widths'), list)
            and all(type(v) in (int, float) for v in attrs['column_widths'])
        ):
            width = sum(attrs['column_widths'])
        if body is not None and type(width) in (int, float) and width > body + 0.01:
            sink.add(
                'layout.overflow',
                'layout',
                'warning',
                'Object is wider than the printable body and will be clipped or overflow.',
                block['id'],
                measured={'width': width, 'body_width': round(body, 2)},
            )
        if block.get('type') == 'page_break' and (
            index == 0
            or index == len(blocks) - 1
            or blocks[index + 1].get('type') in ('page_break', 'section_break')
        ):
            sink.add(
                'layout.blank_page',
                'layout',
                'warning',
                'Page break produces an unintended blank page.',
                block['id'],
                operations=[{'type': 'delete_block', 'target': block['id']}],
            )
    try:
        from .layout import build_document, resolve_layout, resolved_page_count

        qdoc = build_document(doc)
        resolved = resolve_layout(qdoc, doc)
    except ByaxError as exc:
        sink.add('layout.' + exc.code, 'layout', 'error', exc.message)
        sink.cover('layout', 'resolved_geometry', 'completed', 'Page geometry is invalid.')
        return
    except ValueError as exc:
        sink.add('layout.geometry', 'layout', 'error', str(exc))
        return
    except (ImportError, RuntimeError) as exc:
        sink.cover(
            'layout',
            'rendered_geometry',
            'unmeasurable',
            'Fresh layout verification unavailable: ' + str(exc),
        )
        return
    incomplete = {
        'toc_unstable',
        'footnote_reflow',
        'column_widths',
        'pagination_policy',
        'unknown_block',
        'image_wrap',
    }
    for finding in qdoc.byax_findings:
        sink.add(
            'layout.' + finding.get('code', 'diagnostic'),
            'layout',
            finding.get('severity', 'warning'),
            finding.get('message', 'Layout diagnostic.'),
            finding.get('target'),
        )
    positions = resolved.get('positions', {})
    for index, block in enumerate(blocks[:-1]):
        following = blocks[index + 1]
        if (
            block.get('type') == 'heading'
            and block['id'] in positions
            and following['id'] in positions
            and following.get('type') not in ('page_break', 'section_break')
            and positions[following['id']] > positions[block['id']]
        ):
            sink.add(
                'layout.stranded_heading',
                'layout',
                'warning',
                'Heading is separated from the content that follows it.',
                block['id'],
                measured={'heading_page': positions[block['id']], 'next_page': positions[following['id']]},
            )
    status = 'incomplete' if any(f.get('code') in incomplete for f in qdoc.byax_findings) else 'completed'
    sink.coverage.append(
        {
            'suite': 'layout',
            'rule': 'resolved_geometry',
            'status': status,
            'scope': 'selected_document',
            'page_count': resolved_page_count(qdoc),
        }
    )
    if any(
        str(b.get('attrs', {}).get('wrap', 'inline')).replace('-', '_')
        in ('behind', 'in_front', 'front', 'behind_text', 'in_front_of_text')
        for b in blocks
    ):
        sink.cover(
            'layout',
            'layout.overlap',
            'unmeasurable',
            'Overlap between overlay objects and body text is not measured.',
        )


def _field_filled(field, doc=None):
    if not isinstance(field, dict):
        return field is not None and field != ''
    from .profiles import validate_field_value

    return validate_field_value(field, field.get('value'), doc)[0]


def _compatible(previous, ctx):
    if not isinstance(previous, dict) or 'units' not in previous:
        return False
    return previous.get('parameters', {}) == ctx.parameters()


# Layout measurement (dry-run effects and visual protection) -----------------------
def layout_snapshot(doc, targets=None, *, pixels=False):
    """Measure rendered regions per stable ID; optional pixel hashes for visual protection."""
    import hashlib
    import math

    from .layout import build_document, draw_page, resolve_layout

    wanted = None if targets is None else set(targets)
    qdoc = build_document(doc)
    resolved = resolve_layout(qdoc, doc)
    tables = {b['id']: b for b in doc.get('blocks', []) if b.get('type') == 'table'}
    parents, rows = {}, set()
    for table in tables.values():
        rows.update(row['id'] for row in table.get('rows', []))
        for row in table.get('rows', []):
            for cell in row['cells']:
                parents[cell['id']] = (table['id'], row['id'])
    regions = {}
    for page_index, page in enumerate(resolved['pages']):
        for fragment in page['fragments']:
            if 'document' not in fragment or fragment.get('kind') == 'table_header':
                continue
            # Fragments map a source rectangle of a shared flow document onto the page.
            part, rect, source = fragment['document'], fragment['rect'], fragment['source']
            for ident, position in getattr(part, 'byax_positions', {}).items():
                owners = [ident, *parents.get(ident, ())]
                if wanted is not None and not set(owners) & wanted:
                    continue
                box = part.documentLayout().blockBoundingRect(part.findBlock(position))
                top, bottom = max(box.top(), source.top()), min(box.bottom(), source.bottom())
                left, right = max(box.left(), source.left()), min(box.right(), source.right())
                if bottom <= top and not source.top() <= box.top() < source.bottom():
                    continue
                if right <= left:
                    continue
                region = {
                    'page': page_index + 1,
                    'x': round(rect.x() + left - source.left(), 2),
                    'y': round(rect.y() + top - source.top(), 2),
                    'width': round(right - left, 2),
                    'height': round(max(0.0, bottom - top), 2),
                }
                if ident in tables:
                    continue
                for owner in owners:
                    regions.setdefault(owner, []).append(dict(region))
    for owner, items in list(regions.items()):
        if owner in tables or owner in rows:
            merged = {}
            for item in items:
                page = merged.setdefault(item['page'], dict(item))
                right = max(page['x'] + page['width'], item['x'] + item['width'])
                lower = max(page['y'] + page['height'], item['y'] + item['height'])
                page['x'], page['y'] = min(page['x'], item['x']), min(page['y'], item['y'])
                page['width'], page['height'] = round(right - page['x'], 2), round(lower - page['y'], 2)
            regions[owner] = [merged[p] for p in sorted(merged)]
    if pixels and regions:
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QImage, QPainter

        images = {}
        for owner, items in regions.items():
            for item in items:
                page = item['page'] - 1
                if page not in images:
                    width, height = resolved['pages'][page]['width'], resolved['pages'][page]['height']
                    image = QImage(math.ceil(width * 2), math.ceil(height * 2), QImage.Format.Format_ARGB32)
                    image.fill(QColor('white'))
                    painter = QPainter(image)
                    painter.scale(2, 2)
                    try:
                        draw_page(painter, qdoc, doc, page)
                    finally:
                        painter.end()
                    images[page] = image
                crop = images[page].copy(
                    QRect(
                        math.floor(item['x'] * 2),
                        math.floor(item['y'] * 2),
                        max(1, math.ceil(item['width'] * 2)),
                        max(1, math.ceil(item['height'] * 2)),
                    )
                )
                item['pixels'] = hashlib.sha256(bytes(crop.constBits())).hexdigest()
    return {
        'status': 'measured',
        'page_count': len(resolved['pages']),
        'regions': regions,
        'pages': {ident: sorted({r['page'] for r in items}) for ident, items in regions.items()},
    }


def _geometry(items):
    return [{k: v for k, v in r.items() if k != 'pixels'} for r in items]


def layout_effects(before, after, changed=()):
    """Measured page/region effects of a candidate; never saves or publishes output."""
    try:
        old, new = layout_snapshot(before), layout_snapshot(after)
    except (ImportError, RuntimeError, ByaxError) as exc:
        return {'status': 'unmeasured', 'reason': 'Layout could not be measured: ' + str(exc)}
    blocks = {i for b in after.get('blocks', []) for i in _ids(b)} | {
        i for b in before.get('blocks', []) for i in _ids(b)
    }
    moved, reflowed = [], []
    for ident in sorted(set(old['regions']) & set(new['regions'])):
        if old['pages'][ident] != new['pages'][ident]:
            moved.append({'id': ident, 'before': old['pages'][ident], 'after': new['pages'][ident]})
        elif _geometry(old['regions'][ident]) != _geometry(new['regions'][ident]):
            reflowed.append(ident)
    added = sorted(set(new['regions']) - set(old['regions']))
    removed = sorted(set(old['regions']) - set(new['regions']))
    affected = set()
    for ident in added + reflowed + [m['id'] for m in moved] + [c for c in changed if c in new['regions']]:
        affected.update(new['pages'].get(ident, []))
    for ident in removed:
        affected.update(p for p in old['pages'][ident] if p <= new['page_count'])
    if old['page_count'] != new['page_count']:
        affected.update(
            range(
                min(old['page_count'], new['page_count']) + 1, max(old['page_count'], new['page_count']) + 1
            )
        )
    changed_regions = {
        ident: {'before': old['regions'].get(ident, []), 'after': new['regions'].get(ident, [])}
        for ident in list(dict.fromkeys(c for c in changed if c in blocks))[:200]
    }
    return {
        'status': 'measured',
        'page_count': {'before': old['page_count'], 'after': new['page_count']},
        'moved': moved,
        'reflowed': reflowed,
        'added': added,
        'removed': removed,
        'affected_pages': sorted(p for p in affected if p >= 1),
        'changed_regions': changed_regions,
        'unit': 'points',
        'outputs': [],
    }


# Persistent findings, lifecycle and health -----------------------------------------
def _current_unit_fingerprints(doc, parameters, keys):
    config = parameters.get('profile_config')
    profile = parameters.get('profile')
    if profile and not profile.startswith('inline:'):
        if profile not in doc.get('profiles', {}):
            return {}
        config = doc['profiles'][profile]
    selection = (
        resolve_scope(doc, parameters.get('scope')) if _scope_exists(doc, parameters.get('scope')) else None
    )
    if selection is None:
        return {}
    ctx = _Context(
        doc,
        config,
        profile if profile and not profile.startswith('inline:') else None,
        parameters['suites'],
        selection,
    )
    # Commit/typing paths use the prior explicit check's snapshot; no library I/O.
    ctx.library_fingerprints = parameters.get('library_fingerprints', {})
    current = ctx.unit_fingerprints()
    return {key: current.get(key) for key in keys}


def _scope_exists(doc, scope):
    try:
        resolve_scope(doc, scope)
        return True
    except ByaxError:
        return False


def _source_fingerprints(doc, finding):
    from .profiles import source_hash

    current = {}
    for source_id in finding.get('dependencies', {}).get('sources', {}):
        source = doc.get('references', {}).get(source_id)
        current[source_id] = source_hash(source) if source is not None else None
    return current


def _block_fingerprints(doc, finding):
    current = {}
    for identity in finding.get('dependencies', {}).get('blocks', {}):
        try:
            current[identity] = object_hash(_object(doc, identity))
        except ByaxError:
            current[identity] = None
    return current


def _stale_reasons(doc, finding, units):
    dependencies = finding.get('dependencies')
    if not isinstance(dependencies, dict):
        return ['Finding has no recorded dependencies; it is invalidated on any change.']
    reasons = []
    if 'unit' in dependencies and units.get(dependencies['unit']) != dependencies.get('fingerprint'):
        reasons.append('Checked content, structure, profile or context changed: ' + dependencies['unit'])
    if dependencies.get('blocks') and _block_fingerprints(doc, finding) != dependencies['blocks']:
        reasons.append('Affected passage changed or was removed.')
    if dependencies.get('sources') and _source_fingerprints(doc, finding) != dependencies['sources']:
        reasons.append('A selected evidence source was updated or removed.')
    return reasons


def _health_units(doc):
    health = doc.get('review', {}).get('health')
    if not isinstance(health, dict) or 'parameters' not in health:
        return {}
    return _current_unit_fingerprints(doc, health['parameters'], list(health.get('units', {})))


def invalidate_findings(doc):
    """Mark only findings whose dependencies changed as stale (called on commit)."""
    findings = doc.get('review', {}).get('findings', [])
    if not findings:
        return []
    units = _health_units(doc)
    stale = []
    for finding in findings:
        if finding.get('status') in ('stale', 'accepted edit'):
            continue
        reasons = _stale_reasons(doc, finding, units)
        if reasons:
            finding['previous_status'] = finding.get('status')
            finding['status'] = 'stale'
            finding['stale_reasons'] = reasons
            stale.append(finding.get('id'))
    return stale


def health(doc):
    """Current document health: recorded coverage plus live staleness; never a stale clean pass."""
    record = doc.get('review', {}).get('health')
    findings = doc.get('review', {}).get('findings', [])
    units = _health_units(doc) if record else {}
    statuses = {}
    live = []
    for finding in findings:
        status = finding.get('status')
        if status not in ('stale', 'accepted edit') and _stale_reasons(doc, finding, units):
            status = 'stale'
        statuses[status] = statuses.get(status, 0) + 1
        live.append((finding, status))
    if not record:
        validation = 'stale' if statuses.get('stale') else 'incomplete' if findings else 'not_run'
        return {
            'schema_version': 1,
            'document_id': doc['id'],
            'revision': doc['revision'],
            'validation': validation,
            'coverage': [
                {
                    'suite': '*',
                    'status': 'skipped',
                    'reason': 'No checks have been recorded for this document.',
                }
            ],
            'findings_by_status': statuses,
            'stale_units': [],
        }
    stale_units = sorted(
        key
        for key, fingerprint in record.get('units', {}).items()
        if units.get(key) != fingerprint['fingerprint']
    )
    coverage = []
    for entry in record.get('coverage', []):
        entry = copy.deepcopy(entry)
        if entry.get('unit') in stale_units or (
            entry.get('status') == 'completed' and stale_units and 'unit' not in entry
        ):
            entry['recorded_status'], entry['status'] = entry['status'], 'stale'
        coverage.append(entry)
    unresolved = [f for f, status in live if status in ('open', 'needs review', 'stale')]
    errors = sum(f.get('severity') == 'error' and status in ('open', 'needs review') for f, status in live)
    validation = (
        'stale'
        if stale_units or statuses.get('stale')
        else 'failed'
        if errors
        else record.get('validation', 'incomplete')
    )
    return {
        'schema_version': 1,
        'document_id': doc['id'],
        'revision': doc['revision'],
        'checked_revision': record.get('revision'),
        'validation': validation,
        'coverage': coverage,
        'findings_by_status': statuses,
        'stale_units': stale_units,
        'unresolved': len(unresolved),
        'parameters': record.get('parameters'),
        'note': 'Stale or incomplete coverage is never reported as a clean pass; recheck to refresh it.',
    }


def stored_report(doc):
    """Rebuild a tracked report from persisted health so incremental rechecks can reuse units."""
    record = doc.get('review', {}).get('health')
    if not isinstance(record, dict) or 'units' not in record:
        return None
    by_id = {f.get('id'): f for f in doc['review'].get('findings', [])}
    units = {}
    for key, unit in record['units'].items():
        findings = [copy.deepcopy(by_id[i]) for i in unit.get('finding_ids', []) if i in by_id]
        if any(f.get('status') == 'stale' for f in findings) or len(findings) != len(
            unit.get('finding_ids', [])
        ):
            continue
        units[key] = {
            'fingerprint': unit['fingerprint'],
            'findings': findings,
            'coverage': unit.get('coverage', []),
            'evaluated_revision': unit.get('evaluated_revision'),
        }
    return {'units': units, 'parameters': record['parameters']}


def record_checks(doc, values):
    """Run checks and persist findings, coverage and dependencies; content never changes."""
    if not isinstance(values, dict) or set(values) - {'suites', 'profile', 'scope', 'incremental'}:
        raise ByaxError(
            'schema_error', 'record_checks values are suites, profile, scope and incremental.', field='values'
        )
    previous = stored_report(doc) if values.get('incremental') else None
    report = run_checks(
        doc,
        values.get('suites'),
        limit=0,
        profile=values.get('profile'),
        scope=values.get('scope'),
        previous=previous,
        track=True,
    )
    selection = resolve_scope(doc, values.get('scope'))
    existing = doc['review']['findings']
    prior = {f.get('id'): f for f in existing if f.get('engine') == ENGINE}
    retained = [f for f in existing if f.get('engine') != ENGINE]
    recorded, units = [], {}
    for key, unit in report['units'].items():
        ids = []
        for finding in unit['findings']:
            if not _in_scope(finding, selection):
                continue
            old = prior.get(finding['id'])
            if (
                old is not None
                and old.get('status') not in ('stale', 'open')
                and finding.get('status') == 'open'
                and old.get('dependencies', {}).get('fingerprint') == unit['fingerprint']
            ):
                finding['status'] = old['status']
                for field in ('status_history', 'status_reason'):
                    if field in old:
                        finding[field] = copy.deepcopy(old[field])
            finding['dependencies'] = {'unit': key, 'fingerprint': unit['fingerprint']}
            recorded.append(finding)
            ids.append(finding['id'])
        units[key] = {
            'fingerprint': unit['fingerprint'],
            'finding_ids': ids,
            'coverage': [dict(c, unit=key) for c in unit['coverage']],
            'evaluated_revision': unit['evaluated_revision'],
        }
    doc['review']['findings'] = retained + recorded
    doc['review']['health'] = {
        'revision': doc['revision'],
        'validation': report['validation'],
        'parameters': report['parameters'],
        'coverage': [c for c in report['coverage'] if c.get('level') == 'report']
        + [c for u in units.values() for c in u['coverage']],
        'units': units,
        'evaluation': report['evaluation'],
        'summary': report['summary'],
        'evidence': report['evidence'],
    }
    return ['review.findings', 'review.health']


def find_finding(doc, identity):
    for finding in doc.get('review', {}).get('findings', []):
        if finding.get('id') == identity:
            return finding
    raise ByaxError('missing_target', 'Finding does not exist.', target=identity)


def set_finding_status(doc, identity, status, reason=None, revision=None, origin=None):
    finding = find_finding(doc, identity)
    if status not in STATUSES:
        raise ByaxError('schema_error', 'Unknown finding status.', field='status', supported=list(STATUSES))
    current = finding.get('status', 'open')
    if status not in TRANSITIONS.get(current, ()):
        raise ByaxError(
            'conflict',
            f'A finding cannot move from {current} to {status}.',
            target=identity,
            allowed=list(TRANSITIONS.get(current, ())),
        )
    if status in ('dismissed', 'intentional exception') and (
        not isinstance(reason, str) or not reason.strip()
    ):
        raise ByaxError(
            'schema_error', 'Dismissals and intentional exceptions require a reason.', field='reason'
        )
    if status in ('dismissed', 'intentional exception') and (
        finding.get('severity') == 'error' or finding.get('family') in ('structure', 'requirements')
    ):
        raise ByaxError(
            'conflict',
            'Strict failures cannot be dismissed; repair the document or change the requirement in a separate action.',
            target=identity,
        )
    if _stale_reasons(doc, finding, _health_units(doc)):
        raise ByaxError(
            'stale_revision',
            'Finding dependencies changed; recheck before changing its status.',
            target=identity,
        )
    finding.setdefault('status_history', []).append(
        {'from': current, 'to': status, 'reason': reason, 'revision': revision, 'origin': origin}
    )
    finding['status'] = status
    if reason:
        finding['status_reason'] = reason
    return [identity]


def repair_operations(doc, identity):
    finding = find_finding(doc, identity)
    if finding.get('status') not in ('open', 'needs review'):
        raise ByaxError(
            'conflict', 'Only open findings or findings that need review can be repaired.', target=identity
        )
    if _stale_reasons(doc, finding, _health_units(doc)):
        raise ByaxError(
            'stale_revision', 'Finding dependencies changed; recheck before repairing.', target=identity
        )
    operations = finding.get('operations') or finding.get('proposed_operations') or []
    if not operations:
        raise ByaxError(
            'unsupported_feature',
            'Finding has no automatic repair; edit the document directly.',
            target=identity,
            repair=finding.get('repair'),
        )
    return copy.deepcopy(operations)


def mark_repaired(doc, identity, revision=None):
    finding = find_finding(doc, identity)
    finding.setdefault('status_history', []).append(
        {'from': finding.get('status'), 'to': 'accepted edit', 'revision': revision, 'origin': 'repair'}
    )
    finding['status'] = 'accepted edit'
    return [identity]


def record_findings(doc, values, revision=None):
    """Record reviewer (model or human) findings after validating passages and evidence.

    Evidence must cite document references with exact excerpts; factual findings
    require an explicit outcome. Recorded findings always need review and depend
    on the affected passages and cited source versions.
    """
    from .profiles import source_hash
    from .providers import _validate_evidence, normalize_review_context

    if (
        not isinstance(values, dict)
        or set(values) - {'findings', 'provenance', 'profile'}
        or not isinstance(values.get('findings'), list)
    ):
        raise ByaxError('schema_error', 'record_findings values require a findings array.', field='values')
    if len(values['findings']) > 1000:
        raise ByaxError('limit_exceeded', 'At most 1,000 findings may be recorded at once.')
    provenance = values.get('provenance', {})
    if not isinstance(provenance, dict):
        raise ByaxError('schema_error', 'Provenance must be an object.', field='values.provenance')
    profile = values.get('profile')
    config = doc['profiles'].get(profile) if isinstance(profile, str) else None
    if profile is not None and config is None:
        raise ByaxError('missing_target', 'Unknown profile.', target=profile)
    context = normalize_review_context(
        {'references': doc.get('references', {}), 'profile': config or {}, 'revision': doc['revision']}
    )
    changed = []
    for index, item in enumerate(values['findings']):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get('explanation'), str)
            or not isinstance(item.get('target'), str)
        ):
            raise ByaxError(
                'schema_error',
                'Each finding requires an explanation and target.',
                field=f'values.findings[{index}]',
            )
        obj = _object(doc, item['target'])
        text = obj.get('text', '')
        start, end = item.get('start', 0), item.get('end', len(text))
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
            raise ByaxError(
                'schema_error', 'Finding range is outside its target.', field=f'values.findings[{index}]'
            )
        try:
            evidence, outcome = _validate_evidence(item, context, text[start:end])
        except (ValueError, TypeError) as exc:
            raise ByaxError(
                'invalid_input', 'Finding evidence is invalid: ' + str(exc), field=f'values.findings[{index}]'
            ) from exc
        severity = item.get('severity', 'advisory')
        if severity not in ('error', 'warning', 'advisory', 'info'):
            raise ByaxError('schema_error', 'Unknown severity.', field=f'values.findings[{index}].severity')
        sources = {e['source_id']: source_hash(doc['references'][e['source_id']]) for e in evidence}
        finding = {
            'rule': str(item.get('rule', 'review.' + str(item.get('family', 'editorial')))),
            'family': str(item.get('family', 'editorial')),
            'severity': 'advisory' if severity == 'info' else severity,
            'policy': 'advisory',
            'explanation': item['explanation'],
            'affected_ids': [item['target']],
            'range': {'start': start, 'end': end},
            'text': text[start:end],
            'claim': text[start:end] if outcome else None,
            'outcome': outcome,
            'evidence': evidence,
            'measured': {},
            'operations': [],
            'status': 'needs review',
            'revision': revision or doc['revision'],
            'profile': profile,
            'profile_version': (config or {}).get('version'),
            'engine': str(provenance.get('engine', provenance.get('model', 'reviewer'))),
            'provenance': copy.deepcopy(provenance),
            'source_versions': {k: v for k, v in context['source_versions'].items() if k in sources},
            'scope': item['target'],
            'calibrated_probability': False,
            'dependencies': {'blocks': {item['target']: object_hash(obj)}, 'sources': sources},
        }
        if outcome == 'not_established' and not evidence:
            finding['note'] = 'Missing evidence is not proof of falsehood.'
        finding['id'] = (
            'finding_'
            + object_hash(
                {
                    k: finding[k]
                    for k in ('rule', 'affected_ids', 'range', 'explanation', 'outcome', 'evidence')
                }
            )[:20]
        )
        doc['review']['findings'] = [f for f in doc['review']['findings'] if f.get('id') != finding['id']] + [
            finding
        ]
        changed.append(finding['id'])
    return changed


def spelling_suggestions(doc, words, *, limit=5):
    """Ranked local-dictionary candidates for unknown words; advisory, never applied."""
    language = doc.get('metadata', {}).get('language', 'en').split('-')[0]
    try:
        from spellchecker import SpellChecker

        checker = SpellChecker(language=language)
    except (ImportError, ValueError):
        return {word: [] for word in words}
    checker.word_frequency.load_words(doc.get('settings', {}).get('dictionary', []))
    result = {}
    for word in words:
        lower = word.lower()
        ranked = sorted(checker.candidates(lower) or (), key=lambda c: (-checker.word_usage_frequency(c), c))
        ranked = [c for c in ranked if c != lower][:limit]
        if word[:1].isupper():
            ranked = [c[:1].upper() + c[1:] for c in ranked]
        result[word] = ranked
    return result
