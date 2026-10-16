"""Reusable, independently controlled review and dictation profiles.

Profiles contain local provider aliases only. Endpoints, executables and secrets
belong to provider configuration and are rejected from profile collections.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .errors import ByaxError
from .providers import _atomic_json

SENSITIVITY = ('conservative', 'balanced', 'broad')
EDITING_FREEDOM = ('minimal', 'moderate', 'broad')
VOICE_PRESERVATION = ('strict', 'balanced', 'flexible')
RULE_FAMILIES = (
    'spelling',
    'grammar',
    'editorial',
    'style',
    'factual',
    'accessibility',
    'print',
    'fiction',
    'continuity',
)
# Rule family -> check suites; shared by profiles and the CLI --family option.
FAMILY_SUITES = {
    'spelling': ('spelling',),
    'grammar': ('grammar',),
    'editorial': ('editorial', 'consistency'),
    'style': ('editorial',),
    'factual': ('factual',),
    'accessibility': ('accessibility',),
    'print': ('print',),
    'fiction': ('fiction',),
    'continuity': ('continuity',),
}
SOURCE_AUTHORITY = ('exclusive', 'supplementary', 'open')
APPLICATION_POLICY = ('suggestions_only', 'automatic_direct', 'automatic_cleanup')
PROVIDER_ROLES = ('review', 'recognition', 'cleanup')


def presets():
    common = {
        'schema_version': 1,
        'version': 1,
        'voice_preservation': 'strict',
        'source_authority': 'exclusive',
        'references': [],
        'dictionary': [],
        'providers': {role: None for role in PROVIDER_ROLES},
        'application_policy': 'suggestions_only',
        'intentional_exceptions': [],
    }
    return {
        'conservative_proofreading': {
            **copy.deepcopy(common),
            'name': 'Conservative proofreading',
            'sensitivity': 'conservative',
            'editing_freedom': 'minimal',
            'rule_families': ['spelling', 'grammar'],
        },
        'editorial_review': {
            **copy.deepcopy(common),
            'name': 'Editorial review',
            'sensitivity': 'broad',
            'editing_freedom': 'moderate',
            'rule_families': ['spelling', 'grammar', 'editorial', 'style', 'factual', 'accessibility'],
        },
    }


def _fail(message, field):
    raise ByaxError('invalid_input', message, field='profile.' + field)


def validate_profile(profile, *, strict=True):
    if not isinstance(profile, dict):
        _fail('Profile must be an object', '')
    from .storage import _check_secrets

    _check_secrets(profile)
    defaults = presets()['conservative_proofreading']
    from .publication import PUBLICATION_CATEGORIES

    allowed = (
        set(defaults)
        | {'minimum_dpi', 'format', 'clean', 'required_suites', 'fiction'}
        | set(PUBLICATION_CATEGORIES)
    )
    unknown = set(profile) - allowed
    if strict and unknown:
        _fail('Unknown profile fields: ' + ', '.join(sorted(unknown)), '')
    result = {**defaults, **copy.deepcopy(profile)}
    for field in set(PUBLICATION_CATEGORIES) | {'clean'}:
        if field in result and type(result[field]) is not bool:
            _fail('Publication inclusion settings must be booleans', field)
    if 'format' in result:
        from .formats import canonical_format

        if not isinstance(result['format'], str):
            _fail('Output format must be a supported format name', 'format')
        result['format'] = canonical_format(result['format'])
    if 'required_suites' in result:
        from .checks import SUITES

        suites = result['required_suites']
        if not isinstance(suites, list) or any(not isinstance(s, str) or s not in SUITES for s in suites):
            _fail('Required checks must name supported check suites', 'required_suites')
    if 'fiction' in result:
        from .fiction_checks import validate_settings

        result['fiction'] = validate_settings(result['fiction'])
    if type(result['schema_version']) is not int or result['schema_version'] != 1:
        _fail('Unsupported profile schema version', 'schema_version')
    if type(result['version']) is not int or result['version'] < 1:
        _fail('Profile version must be a positive integer', 'version')
    if not isinstance(result['name'], str) or not result['name'].strip() or len(result['name']) > 150:
        _fail('Profile name must contain 1–150 characters', 'name')
    for field, values in (
        ('sensitivity', SENSITIVITY),
        ('editing_freedom', EDITING_FREEDOM),
        ('voice_preservation', VOICE_PRESERVATION),
        ('source_authority', SOURCE_AUTHORITY),
        ('application_policy', APPLICATION_POLICY),
    ):
        if not isinstance(result[field], str) or result[field] not in values:
            _fail('Expected one of: ' + ', '.join(values), field)
    for field in ('rule_families', 'references', 'dictionary'):
        value = result[field]
        if (
            not isinstance(value, list)
            or len(value) > 10000
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            _fail('Expected a bounded list of nonempty strings', field)
        result[field] = list(dict.fromkeys(value))
    if any(family not in RULE_FAMILIES for family in result['rule_families']):
        _fail('Unknown rule family', 'rule_families')
    providers = result['providers']
    if not isinstance(providers, dict) or set(providers) - set(PROVIDER_ROLES):
        _fail('Providers must map review, recognition and cleanup to local aliases', 'providers')
    for role, alias in providers.items():
        if alias is not None and (not isinstance(alias, str) or not re.fullmatch(r'[\w .-]{1,100}', alias)):
            _fail(
                'Provider aliases must be local names, never keys, URLs or executable paths',
                'providers.' + role,
            )
    result['providers'] = {role: providers.get(role) for role in PROVIDER_ROLES}
    exceptions = result['intentional_exceptions']
    if not isinstance(exceptions, list) or len(exceptions) > 10000:
        _fail('Intentional exceptions must be a bounded list', 'intentional_exceptions')
    for exception in exceptions:
        if not isinstance(exception, dict) or set(exception) - {'rule', 'target', 'text', 'reason'}:
            _fail('Exception fields are rule, target, text and reason', 'intentional_exceptions')
        if not all(
            isinstance(exception.get(key), str) and exception[key].strip() for key in ('rule', 'reason')
        ):
            _fail('Every intentional exception requires a rule and reason', 'intentional_exceptions')
        for key in ('target', 'text'):
            if key in exception and (not isinstance(exception[key], str) or not exception[key]):
                _fail('Exception target and text must be nonempty strings', 'intentional_exceptions')
    if 'minimum_dpi' in result and (
        type(result['minimum_dpi']) not in {int, float} or not 1 <= result['minimum_dpi'] <= 2400
    ):
        _fail('Minimum image resolution must be 1–2400 DPI', 'minimum_dpi')
    return result


def check_suites(profile):
    config = validate_profile(profile, strict=False)
    suites = {'structure', 'requirements'}
    for family in config['rule_families']:
        suites.update(FAMILY_SUITES[family])
    return sorted(suites)


def filter_findings(report, profile):
    """Annotate exceptions without removing findings or hiding validation errors."""
    config = validate_profile(profile, strict=False)
    result = copy.deepcopy(report)
    for finding in result.get('findings', []):
        apply_exception(finding, config)
    result.setdefault('summary', {})['intentional_exceptions'] = sum(
        finding.get('status') == 'intentional exception' for finding in result.get('findings', [])
    )
    return result


def export_collection(profiles, path):
    if not isinstance(profiles, dict) or len(profiles) > 1000:
        raise ByaxError('invalid_input', 'A profile collection is a mapping of at most 1000 IDs')
    normalized = {}
    for identity, profile in profiles.items():
        if not isinstance(identity, str) or not re.fullmatch(r'[\w.-]{1,100}', identity):
            raise ByaxError('invalid_input', 'Invalid profile collection ID')
        normalized[identity] = validate_profile(profile)
    _atomic_json(Path(path), {'schema_version': 1, 'profiles': normalized})
    return {'path': str(Path(path).resolve()), 'count': len(normalized)}


def import_collection(path):
    path = Path(path)
    if path.stat().st_size > 4_000_000:
        raise ByaxError('limit_exceeded', 'Profile collection exceeds 4 MB')
    try:
        collection = json.loads(path.read_text(encoding='utf-8'))
        if (
            not isinstance(collection, dict)
            or set(collection) != {'schema_version', 'profiles'}
            or collection['schema_version'] != 1
        ):
            raise ValueError()
        profiles = collection['profiles']
        if not isinstance(profiles, dict) or len(profiles) > 1000:
            raise ValueError()
        if not all(
            isinstance(identity, str) and re.fullmatch(r'[\w.-]{1,100}', identity) for identity in profiles
        ):
            raise ValueError()
        return {identity: validate_profile(profile) for identity, profile in profiles.items()}
    except (OSError, ValueError, TypeError) as exc:
        raise ByaxError('invalid_input', 'Invalid profile collection') from exc


# Section-specific profiles -------------------------------------------------
# Assignments are keyed by the stable heading ID, so a section keeps its
# profile when it moves. Scalar settings override what the enclosing scope
# supplies; dictionaries and intentional exceptions are additive unless the
# assignment declares inherit=false.
SECTION_FIELDS = (
    'sensitivity',
    'editing_freedom',
    'voice_preservation',
    'source_authority',
    'references',
    'dictionary',
    'intentional_exceptions',
    'minimum_dpi',
    'application_policy',
    'fiction',
)
ADDITIVE_FIELDS = ('dictionary', 'intentional_exceptions')


def _headings(doc):
    return {b['id']: b for b in doc.get('blocks', []) if b.get('type') == 'heading'}


def validate_section_profile(doc, heading_id, entry):
    if heading_id not in _headings(doc):
        raise ByaxError(
            'missing_target', 'Section profiles attach to an existing heading.', target=heading_id
        )
    if not isinstance(entry, dict) or set(entry) - {'profile', 'overrides', 'inherit'}:
        _fail('Section profile fields are profile, overrides and inherit', 'section')
    from .storage import _check_secrets

    _check_secrets(entry)
    profile = entry.get('profile')
    if profile is not None and (not isinstance(profile, str) or profile not in doc.get('profiles', {})):
        raise ByaxError(
            'missing_target', 'Section profile refers to an unknown document profile.', target=profile
        )
    overrides = entry.get('overrides', {})
    if not isinstance(overrides, dict) or set(overrides) - set(SECTION_FIELDS):
        _fail('Section overrides may set only: ' + ', '.join(SECTION_FIELDS), 'section.overrides')
    if type(entry.get('inherit', True)) is not bool:
        _fail('Section inherit must be a boolean', 'section.inherit')
    validate_profile({**presets()['conservative_proofreading'], **copy.deepcopy(overrides)})
    result = {'inherit': entry.get('inherit', True), 'overrides': copy.deepcopy(overrides)}
    if profile is not None:
        result['profile'] = profile
    return result


def section_map(doc):
    """Map every block, row and cell ID to its enclosing headings, outermost first."""
    stack, result = [], {}
    for block in doc.get('blocks', []):
        if block.get('type') == 'heading':
            level = block.get('attrs', {}).get('level', 1)
            level = level if type(level) is int else 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block['id']))
        chain = [ident for _, ident in stack]
        result[block['id']] = chain
        for row in block.get('rows', []):
            result[row.get('id')] = chain
            for cell in row.get('cells', []):
                result[cell.get('id')] = chain
    return result


def effective_profile(doc, target=None, base=None, *, sections=None):
    """Resolve the settings that apply to ``target`` with an origin for each field.

    ``base`` is a document profile ID or a profile object chosen for the run.
    Returns ``None`` when neither a base profile nor a section assignment applies.
    """
    from .model import object_hash

    assignments = doc.get('settings', {}).get('section_profiles', {})
    chain = (sections if sections is not None else section_map(doc)).get(target, []) if target else []
    chain = [heading for heading in chain if heading in assignments]
    if base is None and not chain:
        return None
    if isinstance(base, str):
        if base not in doc.get('profiles', {}):
            raise ByaxError('missing_target', 'Unknown check profile.', target=base)
        origin, base = 'profile:' + base, doc['profiles'][base]
    else:
        origin = 'run' if base is not None else 'default'
    config = validate_profile(base if base is not None else {}, strict=False)
    origins = {field: origin for field in config}
    for heading in chain:
        entry = assignments[heading]
        inherit = entry.get('inherit', True)
        if entry.get('profile') in doc.get('profiles', {}):
            selected = validate_profile(doc['profiles'][entry['profile']], strict=False)
            if inherit:
                for field in ADDITIVE_FIELDS:
                    selected[field] = _union(config[field], selected[field])
            config = selected
            origins = {field: 'section:' + heading + '/profile:' + entry['profile'] for field in config}
        for field, value in entry.get('overrides', {}).items():
            config[field] = (
                _union(config[field], value) if field in ADDITIVE_FIELDS and inherit else copy.deepcopy(value)
            )
            origins[field] = 'section:' + heading
    return {'profile': config, 'origins': origins, 'sections': chain, 'fingerprint': object_hash(config)}


def _union(first, second):
    result = list(first)
    for item in second:
        if item not in result:
            result.append(item)
    return result


def request_settings(doc, target=None, base=None):
    """Effective, credential-free settings for checks, model and transcription requests."""
    resolved = effective_profile(doc, target, base)
    if resolved is None:
        return {'profile': None, 'voice': None, 'origins': {}, 'sections': [], 'fingerprint': None}
    config = resolved['profile']
    return {
        'profile': config,
        'origins': resolved['origins'],
        'sections': resolved['sections'],
        'voice': {key: config[key] for key in ('sensitivity', 'editing_freedom', 'voice_preservation')},
        'source_policy': {'authority': config['source_authority'], 'references': config['references']},
        'dictionary': config['dictionary'],
        'intentional_exceptions': config['intentional_exceptions'],
        'fingerprint': resolved['fingerprint'],
    }


def apply_exception(finding, config):
    """Mark one finding as an intentional exception without hiding strict failures."""
    if finding.get('severity') == 'error' or finding.get('family') in {'structure', 'requirements'}:
        return finding
    for exception in config.get('intentional_exceptions', []):
        if exception['rule'] not in {'*', finding.get('rule')}:
            continue
        targets = finding.get('affected_ids', []) + ([finding['target']] if finding.get('target') else [])
        if exception.get('target') and exception['target'] not in targets:
            continue
        if exception.get('text') and exception['text'] not in {
            finding.get('text'),
            finding.get('claim'),
            finding.get('measured', {}).get('word'),
        }:
            continue
        finding['status'] = 'intentional exception'
        finding['exception_reason'] = exception['reason']
        break
    return finding


# Reusable settings collections and templates --------------------------------
COLLECTION_KINDS = (
    'styles',
    'profiles',
    'sources',
    'terminology',
    'fields',
    'layout',
    'requirements',
    'section_profiles',
    'compile',
)
CONFLICT_POLICIES = ('error', 'replace', 'keep')
FIELD_TYPES = ('text', 'number', 'integer', 'date', 'enum', 'choice', 'boolean', 'bool', 'asset')


def source_hash(source):
    import hashlib

    material = [source[key] for key in ('text', 'excerpt', 'content') if isinstance(source.get(key), str)]
    excerpts = source.get('excerpts') if isinstance(source.get('excerpts'), list) else []
    for excerpt in excerpts:
        if isinstance(excerpt, str):
            material.append(excerpt)
        elif isinstance(excerpt, dict) and isinstance(excerpt.get('text'), str):
            material.append(excerpt['text'])
    return hashlib.sha256('\n'.join(material).encode()).hexdigest()


def export_settings(doc, kinds=None, *, styles=None, profiles=None, sources=None):
    """Collect reusable settings with their dependencies closed over; never credentials."""
    from .storage import _check_secrets

    explicit = kinds is not None
    kinds = list(COLLECTION_KINDS) if kinds is None else kinds
    if not isinstance(kinds, list) or any(kind not in COLLECTION_KINDS for kind in kinds):
        raise ByaxError('invalid_input', 'Unknown collection kind.', supported=list(COLLECTION_KINDS))
    settings = doc.get('settings', {})
    result, requires = {}, {'provider_aliases': [], 'styles': [], 'sources': []}
    selected_profiles = set()
    if 'profiles' in kinds:
        selected_profiles.update(sorted(doc.get('profiles', {})) if profiles is None else profiles)
    if 'section_profiles' in kinds:
        selected_profiles.update(
            v['profile'] for v in settings.get('section_profiles', {}).values() if v.get('profile')
        )
    if selected_profiles:
        result['profiles'] = {}
        for identity in sorted(selected_profiles):
            if identity not in doc.get('profiles', {}):
                raise ByaxError('missing_target', 'Unknown profile.', target=identity)
            profile = validate_profile(doc['profiles'][identity])
            result['profiles'][identity] = profile
            requires['sources'].extend(profile['references'])
            requires['provider_aliases'].extend(alias for alias in profile['providers'].values() if alias)
    if 'sources' in kinds or requires['sources']:
        selected = set(doc.get('references', {}) if sources is None and 'sources' in kinds else sources or [])
        selected.update(requires['sources'])
        missing = sorted(selected - set(doc.get('references', {})))
        if missing:
            raise ByaxError('missing_target', 'Selected sources are missing.', targets=missing)
        result['sources'] = {
            key: {
                **copy.deepcopy(doc['references'][key]),
                'content_hash': source_hash(doc['references'][key]),
            }
            for key in sorted(selected)
        }
    if 'styles' in kinds:
        names = set(doc.get('styles', {}) if styles is None else styles)
        pending = list(names)
        while pending:
            name = pending.pop()
            if name not in doc.get('styles', {}):
                raise ByaxError('missing_target', 'Style dependency is missing.', target=name)
            parent = doc['styles'][name].get('based_on')
            if parent and parent not in names:
                names.add(parent)
                pending.append(parent)
                requires['styles'].append(parent)
        result['styles'] = {name: copy.deepcopy(doc['styles'][name]) for name in sorted(names)}
    if 'terminology' in kinds:
        result['terminology'] = {
            'dictionary': list(settings.get('dictionary', [])),
            'preferred': copy.deepcopy(settings.get('terminology', {})),
        }
    if 'fields' in kinds:
        result['fields'] = {
            key: {k: v for k, v in value.items() if k != 'value'}
            if isinstance(value, dict)
            else {'type': 'text'}
            for key, value in doc.get('fields', {}).items()
        }
    if 'layout' in kinds:
        result['layout'] = copy.deepcopy(doc.get('layout', {}))
    if 'requirements' in kinds:
        result['requirements'] = copy.deepcopy(settings.get('requirements', {}))
    # Compile presets (byax.compile) are listed only when the document saved some or they are asked for.
    if 'compile' in kinds and (explicit or settings.get('compile_presets')):
        result['compile'] = copy.deepcopy(settings.get('compile_presets', {}))
    if 'section_profiles' in kinds:
        headings = _headings(doc)
        result['section_profiles'] = {
            key: {**copy.deepcopy(value), 'heading_text': headings.get(key, {}).get('text')}
            for key, value in settings.get('section_profiles', {}).items()
        }
    collection = {
        'schema_version': 1,
        'kind': 'byax-settings-collection',
        'collections': result,
        'requires': {key: sorted(set(value)) for key, value in requires.items()},
    }
    _check_secrets(collection)
    return collection


def write_collection(collection, path):
    validate_collection(collection)
    _atomic_json(Path(path), collection)
    return {'path': str(Path(path).resolve()), 'kinds': sorted(collection['collections'])}


def read_collection(path):
    path = Path(path)
    if path.stat().st_size > 4_000_000:
        raise ByaxError('limit_exceeded', 'Settings collection exceeds 4 MB')
    try:
        collection = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ByaxError('invalid_input', 'Invalid settings collection') from exc
    return validate_collection(collection)


def validate_collection(collection):
    from .storage import _check_secrets

    if (
        not isinstance(collection, dict)
        or set(collection) != {'schema_version', 'kind', 'collections', 'requires'}
        or collection['schema_version'] != 1
        or collection['kind'] != 'byax-settings-collection'
    ):
        raise ByaxError('invalid_input', 'Invalid settings collection')
    _check_secrets(collection)
    values = collection['collections']
    if not isinstance(values, dict) or set(values) - set(COLLECTION_KINDS):
        raise ByaxError('invalid_input', 'Unknown settings collection kind', supported=list(COLLECTION_KINDS))
    for kind, value in values.items():
        if not isinstance(value, dict):
            raise ByaxError('invalid_input', 'Collection entries must be objects', field=kind)
    for identity, profile in values.get('profiles', {}).items():
        values['profiles'][identity] = validate_profile(profile)
    for name, style in values.get('styles', {}).items():
        if not isinstance(style, dict):
            raise ByaxError('invalid_input', 'Style definitions must be objects', field='styles.' + name)
    for identity, source in values.get('sources', {}).items():
        if not isinstance(source, dict):
            raise ByaxError('invalid_input', 'Sources must be objects', field='sources.' + identity)
        if 'content_hash' in source and source['content_hash'] != source_hash(source):
            raise ByaxError(
                'invalid_input', 'Source content does not match its recorded hash', target=identity
            )
    for name, definition in values.get('fields', {}).items():
        if not isinstance(definition, dict) or definition.get('type', 'text') not in FIELD_TYPES:
            raise ByaxError('invalid_input', 'Unsupported field type', field='fields.' + name)
    if values.get('compile'):
        from .compile import validate_profile as validate_compile_profile

        for name, preset in values['compile'].items():
            values['compile'][name] = validate_compile_profile(preset, field='compile.' + str(name))
    return collection


def compile_collection(doc, collection, *, aliases=None, available_providers=None, on_conflict='error'):
    """Compile a collection into bounded canonical operations for ``doc``.

    Nothing is applied here. Provider aliases are mapped through ``aliases``;
    aliases unavailable on this installation are cleared and reported.
    """
    validate_collection(collection)
    if on_conflict not in CONFLICT_POLICIES:
        raise ByaxError('invalid_input', 'Unknown conflict policy', supported=list(CONFLICT_POLICIES))
    aliases = aliases or {}
    values = collection['collections']
    operations, report = [], {'added': [], 'replaced': [], 'kept': [], 'conflicts': [], 'unresolved': []}

    def place(kind, key, existing, incoming, op):
        label = kind + ':' + key
        if existing is not None and existing == incoming:
            report['kept'].append(label)
            return
        if existing is not None and on_conflict != 'replace':
            report['conflicts' if on_conflict == 'error' else 'kept'].append(label)
            return
        report['added' if existing is None else 'replaced'].append(label)
        operations.append(op)

    styles = values.get('styles', {})
    ordered, seen = [], set()

    def visit(name, trail=()):
        if name in seen or name not in styles:
            return
        if name in trail:
            raise ByaxError('invalid_input', 'Style collection inheritance is cyclic', target=name)
        parent = styles[name].get('based_on')
        if parent:
            if parent not in styles and parent not in doc.get('styles', {}):
                report['unresolved'].append({'kind': 'style', 'id': name, 'missing': parent})
            visit(parent, (*trail, name))
        seen.add(name)
        ordered.append(name)

    for name in sorted(styles):
        visit(name)
    for name in ordered:
        place(
            'style',
            name,
            doc.get('styles', {}).get(name),
            styles[name],
            {'type': 'define_style', 'name': name, 'values': styles[name]},
        )
    for key, source in values.get('sources', {}).items():
        incoming = {k: v for k, v in source.items() if k != 'content_hash'}
        place(
            'source',
            key,
            doc.get('references', {}).get(key),
            incoming,
            {'type': 'add_reference', 'id': key, 'values': incoming},
        )
    available = None if available_providers is None else set(available_providers)
    for key, profile in values.get('profiles', {}).items():
        profile = copy.deepcopy(profile)
        for role, alias in profile['providers'].items():
            mapped = aliases.get(alias, alias) if alias else None
            if mapped and available is not None and mapped not in available:
                report['unresolved'].append(
                    {'kind': 'provider_alias', 'profile': key, 'role': role, 'alias': mapped}
                )
                mapped = None
            profile['providers'][role] = mapped
        for source in profile['references']:
            if source not in values.get('sources', {}) and source not in doc.get('references', {}):
                report['unresolved'].append({'kind': 'source', 'profile': key, 'id': source})
        place(
            'profile',
            key,
            doc.get('profiles', {}).get(key),
            profile,
            {'type': 'set_profile', 'id': key, 'values': profile},
        )
    if 'terminology' in values:
        terms = values['terminology']
        settings = doc.get('settings', {})
        merged = {
            'dictionary': _union(settings.get('dictionary', []), terms.get('dictionary', [])),
            'terminology': {**settings.get('terminology', {}), **terms.get('preferred', {})},
        }
        if merged != {
            'dictionary': settings.get('dictionary', []),
            'terminology': settings.get('terminology', {}),
        }:
            operations.append({'type': 'set_settings', 'values': merged})
            report['added'].append('terminology')
    for name, definition in values.get('fields', {}).items():
        existing = doc.get('fields', {}).get(name)
        existing = (
            {k: v for k, v in existing.items() if k != 'value'} if isinstance(existing, dict) else existing
        )
        place(
            'field',
            name,
            existing,
            definition,
            {'type': 'fill_fields', 'values': {name: copy.deepcopy(definition)}},
        )
    if 'layout' in values:
        place(
            'layout',
            'page',
            doc.get('layout'),
            values['layout'],
            {'type': 'set_layout', 'values': copy.deepcopy(values['layout'])},
        )
    for key, requirement in values.get('requirements', {}).items():
        place(
            'requirement',
            key,
            doc.get('settings', {}).get('requirements', {}).get(key),
            requirement,
            {'type': 'set_requirement', 'id': key, 'values': requirement},
        )
    presets = copy.deepcopy(doc.get('settings', {}).get('compile_presets', {}))
    presets_changed = False
    for key, preset in sorted(values.get('compile', {}).items()):
        existing, label = presets.get(key), 'compile:' + key
        if existing == preset:
            report['kept'].append(label)
        elif existing is not None and on_conflict != 'replace':
            report['conflicts' if on_conflict == 'error' else 'kept'].append(label)
        else:
            report['added' if existing is None else 'replaced'].append(label)
            presets[key], presets_changed = copy.deepcopy(preset), True
    if presets_changed:
        operations.append({'type': 'set_settings', 'values': {'compile_presets': presets}})
    headings = _headings(doc)
    for key, entry in values.get('section_profiles', {}).items():
        target = key if key in headings else None
        if target is None and entry.get('heading_text'):
            matches = [
                ident for ident, block in headings.items() if block.get('text') == entry['heading_text']
            ]
            target = matches[0] if len(matches) == 1 else None
        if target is None:
            report['unresolved'].append(
                {'kind': 'section', 'id': key, 'heading_text': entry.get('heading_text')}
            )
            continue
        incoming = {k: v for k, v in entry.items() if k != 'heading_text'}
        place(
            'section_profile',
            target,
            doc.get('settings', {}).get('section_profiles', {}).get(target),
            incoming,
            {'type': 'set_section_profile', 'target': target, 'values': incoming},
        )
    if report['conflicts']:
        raise ByaxError(
            'conflict',
            'Collection entries conflict with existing definitions.',
            conflicts=report['conflicts'],
        )
    if len(operations) > 10000:
        raise ByaxError('limit_exceeded', 'A collection compiles to at most 10,000 operations')
    return {'operations': operations, 'report': report}


def validate_field_value(definition, value, doc=None):
    """Return ``(ok, reason)`` for one typed template field value."""
    import math
    from datetime import date

    if not isinstance(definition, dict):
        return True, None
    kind = definition.get('type', 'text')
    if value is None or value == '':
        return False, 'missing'
    if kind == 'integer':
        return type(value) is int, None if type(value) is int else 'expected an integer'
    if kind == 'number':
        ok = type(value) in (int, float) and math.isfinite(value)
        return ok, None if ok else 'expected a finite number'
    if kind in ('boolean', 'bool'):
        return type(value) is bool, None if type(value) is bool else 'expected true or false'
    if kind in ('enum', 'choice'):
        ok = value in definition.get('options', definition.get('choices', []))
        return ok, None if ok else 'expected a declared choice'
    if kind == 'date':
        try:
            date.fromisoformat(value)
            return True, None
        except (TypeError, ValueError):
            return False, 'expected an ISO date'
    if kind == 'asset':
        asset = (doc or {}).get('assets', {}).get(value) if isinstance(value, str) else None
        if asset is None:
            return False, 'expected a document asset identity'
        permitted = definition.get('mime', [])
        if permitted and not any(asset.get('mime', '').startswith(prefix) for prefix in permitted):
            return False, 'asset type is not permitted'
        return True, None
    if not isinstance(value, str) or not value.strip():
        return False, 'expected nonempty text'
    pattern = definition.get('pattern')
    if isinstance(pattern, str) and not re.fullmatch(pattern, value):
        return False, 'does not match the declared pattern'
    return True, None


def template_operations():
    """Canonical operations that mark a native document as a reusable template."""
    return [{'type': 'set_metadata', 'values': {'kind': 'template'}}]


def instantiate_template(template, values=None, *, title=None):
    """Create an independent document from a template and report each field outcome.

    The template is never modified. Invalid typed values are reported and not
    filled; defaults apply only when a value is absent. Requirements are checked.
    """
    from .checks import run_checks
    from .engine import apply
    from .model import new_id

    if template.get('metadata', {}).get('kind') != 'template':
        raise ByaxError('invalid_input', 'Document is not marked as a template')
    values = {} if values is None else values
    if not isinstance(values, dict):
        raise ByaxError('invalid_input', 'Template values must be an object')
    doc = copy.deepcopy(template)
    doc['id'], doc['revision'] = new_id('doc'), new_id('rev')
    doc['history'] = {'revisions': [], 'branches': {}, 'current_branch': 'main'}
    doc['requests'] = {}
    doc['review']['findings'] = []
    doc['review'].pop('health', None)
    doc['metadata'] = {
        **doc['metadata'],
        'kind': 'document',
        'template': {'id': template['id'], 'revision': template['revision']},
    }
    if title is not None:
        doc['metadata']['title'] = title
    outcomes, fill = {}, {}
    for name, definition in doc.get('fields', {}).items():
        supplied = name in values
        value = (
            values[name] if supplied else definition.get('default') if isinstance(definition, dict) else None
        )
        if not supplied and value in (None, ''):
            outcomes[name] = {
                'status': 'missing',
                'required': bool(isinstance(definition, dict) and definition.get('required')),
            }
            continue
        ok, reason = validate_field_value(definition, value, doc)
        outcomes[name] = {'status': ('filled' if supplied else 'default') if ok else 'invalid'}
        if ok:
            fill[name] = value
        else:
            outcomes[name]['reason'] = reason
    for name in values:
        if name not in doc.get('fields', {}):
            outcomes[name] = {'status': 'unknown_field'}
    if fill:
        doc, _ = apply(doc, {'operations': [{'type': 'fill_fields', 'values': fill}], 'origin': 'template'})
    report = run_checks(doc, ['requirements'])
    return doc, {
        'fields': outcomes,
        'validation': report['validation'],
        'findings': report['findings'],
        'template': doc['metadata']['template'],
    }
