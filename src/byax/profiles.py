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
RULE_FAMILIES = ('spelling', 'grammar', 'editorial', 'style', 'factual', 'accessibility', 'print')
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
    allowed = set(defaults) | {'minimum_dpi'}
    unknown = set(profile) - allowed
    if strict and unknown:
        _fail('Unknown profile fields: ' + ', '.join(sorted(unknown)), '')
    result = {**defaults, **copy.deepcopy(profile)}
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
        if family in {'spelling', 'editorial', 'accessibility', 'print'}:
            suites.add(family)
        elif family == 'style':
            suites.add('editorial')
    return sorted(suites)


def filter_findings(report, profile):
    """Annotate exceptions without removing findings or hiding validation errors."""
    config = validate_profile(profile, strict=False)
    result = copy.deepcopy(report)
    for finding in result.get('findings', []):
        if finding.get('severity') == 'error' or finding.get('family') in {'structure', 'requirements'}:
            continue
        for exception in config['intentional_exceptions']:
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
