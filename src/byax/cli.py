"""Headless command interface. All transports dispatch through :func:`execute`.

JSON input uses UTF-8; ``--input -`` reads stdin. Mutation files are locked for
read/validate/write and never join an editor's outstanding session transaction.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from .jobs import JobManager

Args = dict[str, Any]
Result = dict[str, Any]
Document = dict[str, Any]
Handler = Callable[[Args, str | None], Result]

SCHEMA_VERSION = 1
COMMANDS = {
    'library': 'Manage attached local libraries and indexing jobs',
    'library-search': 'Search attached library passages (read only)',
    'library-read': 'Read an indexed attached library passage (read only)',
    'source': 'Search, import, export and suggest citation sources',
    'cite': 'Insert a citation referencing an existing source',
    'statistics': 'Count authored, publication and selected text',
    'readability': 'Per-chapter readability indices, style statistics and pacing; optionally export a report',
    'collection': 'Inspect, export or apply reusable document settings',
    'dependencies': 'Inspect, explicitly refresh or embed linked assets',
    'view': 'Read a format projection and its located losses without changing the master',
    'version': 'Engine and schema versions; --check-updates makes one explicit GitHub Releases request',
    'capabilities': 'Discover supported features and provider adapters',
    'schema': 'Discover command or canonical operation schemas',
    'new': 'Create a native document',
    'inspect': 'Inspect selected semantic content without opening an editor',
    'find': 'Locate text and stable IDs',
    'context': 'Build a bounded, exact context package',
    'apply': 'Apply one atomic canonical operation envelope',
    'transact': 'Alias for atomic apply; transactions never span invocations',
    'fill': 'Fill document fields using a JSON values object',
    'history': 'List revisions, checkpoint, branch, or restore',
    'diff': 'Compare semantic content against a saved revision',
    'check': 'Run deterministic validation suites',
    'review': 'Inspect, propose, accept, or reject changes and comments',
    'import': 'Import an exchange document into a native package',
    'save-as': 'Save an existing document under another path',
    'convert': 'Convert a document with an explicit loss report',
    'export': 'Export a document with optional validation gates',
    'render': 'Render pages through the desktop layout engine',
    'pack': 'Pack a readable native source folder',
    'unpack': 'Unpack a portable native package',
    'providers': 'Inspect, configure, test, or call explicitly selected providers',
    'transcribe': 'Transcribe an explicitly selected audio file',
    'jobs': 'Inspect persistent job status, results, cancellation, and recovery',
    'batch': 'Convert inputs with individual outcomes and bounded workers',
    'redact': 'Create and verify a sanitized copy',
    'reproduction': 'Record or check reproducible rendering inputs',
    'proof': 'Create contact sheets, focused crops, and semantic diagnostic overlays',
    'table': 'Import or export CSV table values while preserving native identities and formatting',
    'template-batch': 'Fill independent copies of a template from CSV field rows',
    'fonts': 'Inspect font identity, substitution, embedding permission and glyph fallbacks; embed permitted fonts',
    'sync': 'Check synced-folder safety, list conflicted copies, and merge one as a reviewable proposal',
    'sample': 'Copy the bundled sample project to a folder you choose (never edits the bundled file)',
    'grammar': 'Grammar tiers: built-in rules and the optional local LanguageTool server (status, rules, check, setup)',
    'languages': 'List, verify, install and remove language packs; report interface translation coverage',
}
# This schema is also used to reject unknown transport arguments and scope paths.
ARGUMENTS: dict[str, dict[str, str]] = {
    'library': {
        'path': 'string',
        'action': 'string',
        'library_id': 'string',
        'query': 'string',
        'document_id': 'string',
        'chunk_id': 'string',
        'limit': 'integer',
        'offset': 'integer',
        'input': 'object',
        'role': 'string',
        'allow_remote': 'boolean',
        'rebuild': 'boolean',
        'expect_revision': 'string',
        'method': 'string',
        'expand': 'boolean',
    },
    'library-search': {
        'path': 'string',
        'library_id': 'string',
        'query': 'string',
        'limit': 'integer',
        'offset': 'integer',
        'method': 'string',
        'expand': 'boolean',
    },
    'library-read': {
        'offset': 'integer',
        'path': 'string',
        'library_id': 'string',
        'document_id': 'string',
        'chunk_id': 'string',
        'limit': 'integer',
    },
    'source': {
        'path': 'string',
        'action': 'string',
        'query': 'string',
        'target': 'string',
        'start': 'integer',
        'end': 'integer',
        'limit': 'integer',
        'offset': 'integer',
        'text': 'string',
        'format': 'string',
        'style': 'string',
        'expect_revision': 'string',
        'request_id': 'string',
        'dry_run': 'boolean',
    },
    'cite': {
        'path': 'string',
        'target': 'string',
        'start': 'integer',
        'end': 'integer',
        'source': 'string',
        'quote': 'string',
        'kind': 'string',
        'prefix': 'string',
        'suffix': 'string',
        'input': 'object',
        'expect_revision': 'string',
        'request_id': 'string',
        'dry_run': 'boolean',
    },
    'statistics': {
        'path': 'string',
        'format': 'string',
        'target': 'string',
        'start': 'integer',
        'end': 'integer',
    },
    'readability': {
        'path': 'string',
        'target': 'string',
        'out': 'string',
        'format': 'string',
        'overwrite': 'boolean',
    },
    'collection': {
        'path': 'string',
        'action': 'string',
        'input': 'object',
        'out': 'string',
        'on_conflict': 'string',
        'overwrite': 'boolean',
    },
    'dependencies': {
        'path': 'string',
        'action': 'string',
        'target': 'string',
        'authorize': 'boolean',
        'allow_network': 'boolean',
    },
    'view': {'path': 'string', 'as': 'string', 'source': 'boolean'},
    'version': {'check_updates': 'boolean'},
    'sample': {'out': 'string'},
    'capabilities': {'topic': 'string'},
    'schema': {'topic': 'string'},
    'new': {'out': 'string', 'title': 'string', 'text': 'string', 'overwrite': 'boolean'},
    'inspect': {
        'path': 'string',
        'section': 'string',
        'target': 'string',
        'limit': 'integer',
        'outline': 'boolean',
    },
    'find': {
        'path': 'string',
        'query': 'string',
        'case_sensitive': 'boolean',
        'whole_word': 'boolean',
        'regex': 'boolean',
    },
    'context': {
        'path': 'string',
        'target': 'string',
        'limit': 'integer',
        'profile': 'string',
        'library_query': 'string',
        'library_limit': 'integer',
        'library_chars': 'integer',
        'library_method': 'string',
        'library_pinned': 'array',
        'remote_bound': 'boolean',
    },
    'apply': {
        'path': 'string',
        'input': 'object',
        'expect_revision': 'string',
        'request_id': 'string',
        'dry_run': 'boolean',
        'require_suite': 'array',
    },
    'fill': {
        'path': 'string',
        'input': 'object',
        'expect_revision': 'string',
        'request_id': 'string',
        'dry_run': 'boolean',
        'require_suite': 'array',
    },
    'history': {
        'path': 'string',
        'action': 'string',
        'name': 'string',
        'revision': 'string',
        'expect_revision': 'string',
        'limit': 'integer',
    },
    'diff': {'path': 'string', 'from_revision': 'string'},
    'check': {
        'path': 'string',
        'suite': 'array',
        'family': 'array',
        'profile': 'string',
        'limit': 'integer',
        'strict': 'boolean',
        # Context engine plan 6.4: audit every citation mark (stage 1; local, no consent).
        'citations': 'boolean',
    },
    'review': {
        'path': 'string',
        'action': 'string',
        'target': 'string',
        'input': 'object',
        'expect_revision': 'string',
        'request_id': 'string',
        'dry_run': 'boolean',
    },
    'import': {'path': 'string', 'out': 'string', 'overwrite': 'boolean'},
    'save-as': {'path': 'string', 'out': 'string', 'overwrite': 'boolean'},
    'convert': {
        'path': 'string',
        'out': 'string',
        'format': 'string',
        'profile': 'string',
        'overwrite': 'boolean',
        'dry_run': 'boolean',
        'require_suite': 'array',
        'strict': 'boolean',
    },
    'export': {
        'path': 'string',
        'out': 'string',
        'format': 'string',
        'profile': 'string',
        'overwrite': 'boolean',
        'dry_run': 'boolean',
        'require_suite': 'array',
        'strict': 'boolean',
        # Extension export hooks (byax.extensions): run after the export on a read-only copy.
        'hooks': 'boolean',
        'hooks_dir': 'string',
    },
    'render': {'path': 'string', 'out': 'string', 'pages': 'array', 'overwrite': 'boolean'},
    'pack': {'path': 'string', 'out': 'string', 'overwrite': 'boolean'},
    'unpack': {'path': 'string', 'out': 'string', 'include_history': 'boolean', 'overwrite': 'boolean'},
    'providers': {
        'action': 'string',
        'alias': 'string',
        'input': 'object',
        'text': 'string',
        'config_dir': 'string',
        'allow_remote': 'boolean',
    },
    'transcribe': {
        'path': 'string',
        'alias': 'string',
        'language': 'string',
        'mode': 'string',
        'document': 'string',
        'target': 'string',
        'cleanup_alias': 'string',
        'insert': 'boolean',
        'expect_revision': 'string',
        'config_dir': 'string',
        'allow_remote': 'boolean',
        'offset': 'integer',
        'job_root': 'string',
    },
    'jobs': {'action': 'string', 'id': 'string', 'root': 'string', 'input': 'object'},
    'batch': {
        'input': 'object',
        'workers': 'integer',
        'overwrite': 'boolean',
        'max_memory_mb': 'integer',
        'staging': 'string',
    },
    'fonts': {
        'path': 'string',
        'action': 'string',
        'family': 'string',
        'bold': 'boolean',
        'italic': 'boolean',
    },
    'redact': {'path': 'string', 'out': 'string', 'input': 'object', 'overwrite': 'boolean'},
    'sync': {
        'path': 'string',
        'action': 'string',
        'copy_path': 'string',
        'name': 'string',
        'author': 'string',
        'expect_revision': 'string',
        'dry_run': 'boolean',
    },
    'grammar': {
        'action': 'string',
        'text': 'string',
        'language': 'string',
        'engine': 'string',
        'group': 'string',
        'port': 'integer',
        'languagetool_dir': 'string',
        'java': 'string',
        'memory_mb': 'integer',
        'autostart': 'boolean',
        'include_spelling': 'boolean',
        'sha256': 'string',
    },
    'languages': {
        'action': 'string',
        'id': 'string',
        'language': 'string',
        'pack_file': 'string',
        'url': 'string',
        'sha256': 'string',
        'replace': 'boolean',
        'verbose': 'boolean',
    },
    'proof': {
        'path': 'string',
        'out': 'string',
        'pages': 'array',
        'input': 'object',
        'columns': 'integer',
        'thumbnail_width': 'integer',
        'no_contact': 'boolean',
    },
    'table': {
        'path': 'string',
        'action': 'string',
        'table': 'string',
        'csv': 'string',
        'out': 'string',
        'grow': 'boolean',
        'encoding': 'string',
        'dialect': 'string',
        'delimiter': 'string',
        'newline': 'string',
        'overwrite': 'boolean',
        'expect_revision': 'string',
        'request_id': 'string',
        'dry_run': 'boolean',
    },
    'template-batch': {
        'path': 'string',
        'csv': 'string',
        'out': 'string',
        'encoding': 'string',
        'dialect': 'string',
        'delimiter': 'string',
        'format': 'string',
        'profile': 'string',
        'overwrite': 'boolean',
    },
    'reproduction': {
        'path': 'string',
        'action': 'string',
        'input': 'object',
        'out': 'string',
        'overwrite': 'boolean',
    },
}
ARGUMENTS['transact'] = ARGUMENTS['apply']
# On-device search models (byax.local_models): list/status/verify are read-only; download, remove and
# configure are administrative (transport_policy.check_provider_admin).
COMMANDS['models'] = 'List, download, verify and remove on-device search models; set Fast or Accurate search'
ARGUMENTS['models'] = {
    'action': 'string',
    'name': 'string',
    'source': 'string',
    'confirm': 'boolean',
    'input': 'object',
    'models_dir': 'string',
    'config_dir': 'string',
}
ARGUMENTS['library'].update(
    {'confirm': 'boolean', 'rerank': 'string', 'model': 'string', 'section': 'boolean'}
)
ARGUMENTS['library-search'].update({'rerank': 'string'})
ARGUMENTS['library-read'].update({'section': 'boolean'})
# Manuscript project binder (byax.project); mutating actions are canonical project_* operations.
COMMANDS['project'] = 'List, reorder, split, merge and annotate manuscript binder items'
ARGUMENTS['project'] = {
    'path': 'string',
    'action': 'string',
    'target': 'string',
    'after': 'string',
    'before': 'string',
    'parent': 'string',
    'next': 'string',
    'at': 'string',
    'offset': 'integer',
    'text': 'string',
    'expected_text': 'string',
    'kind': 'string',
    'status': 'string',
    'words': 'integer',
    'clear': 'boolean',
    'level': 'integer',
    'id': 'string',
    'heading': 'string',
    'delete_content': 'boolean',
    'alias': 'string',
    'allow_remote': 'boolean',
    'config_dir': 'string',
    'input': 'object',
    'expect_revision': 'string',
    'request_id': 'string',
    'dry_run': 'boolean',
}
# Writing goals and sessions (byax.goals): the daily and session goals are local preferences.
COMMANDS['goals'] = 'Show progress against document, item, daily and session goals; set local goals'
ARGUMENTS['goals'] = {
    'path': 'string',
    'action': 'string',
    'words': 'integer',
    'clear': 'boolean',
    'format': 'string',
    'limit': 'integer',
    'config_dir': 'string',
}
# Named drafts (byax.drafts): saved states built on branches and delta history.
COMMANDS['drafts'] = 'List, save, switch, compare and bring changes from named drafts'
ARGUMENTS['drafts'] = {
    'path': 'string',
    'action': 'string',
    'name': 'string',
    'note': 'string',
    'against': 'string',
    'switch': 'boolean',
    'expect_revision': 'string',
    'request_id': 'string',
    'dry_run': 'boolean',
}
# Compile pipeline (byax.compile, CP B2): a project to a print PDF, EPUB, Word or Markdown book.
COMMANDS['compile'] = 'Compile a manuscript project into a book (PDF, EPUB, Word or Markdown) with preflight'
ARGUMENTS['compile'] = {
    'path': 'string',
    'out': 'string',
    'action': 'string',
    'preset': 'string',
    'preset_file': 'string',
    'target': 'string',
    'trim': 'string',
    'include': 'array',
    'exclude': 'array',
    'input': 'object',
    'name': 'string',
    'preflight_only': 'boolean',
    'dry_run': 'boolean',
    'overwrite': 'boolean',
    'strict': 'boolean',
    'expect_revision': 'string',
    'request_id': 'string',
}
# Manuscript corpus and codex (context engine 8.1, 8.2); see byax.project_index and byax.cli_codex.
COMMANDS['project-search'] = (
    'Find across the project folder, run saved searches, and list backlinks and unlinked mentions'
)
ARGUMENTS['project-search'] = {
    'path': 'string',
    'action': 'string',
    'query': 'string',
    'name': 'string',
    'target': 'string',
    'bookmark': 'string',
    'scope': 'string',
    'kind': 'string',
    'item': 'string',
    'documents': 'array',
    'limit': 'integer',
    'offset': 'integer',
    'expand': 'boolean',
    'rebuild': 'boolean',
    'expect_revision': 'string',
    'request_id': 'string',
    'dry_run': 'boolean',
}
COMMANDS['codex'] = 'List, look up, edit and scan for codex entries; propose facts for review'
ARGUMENTS['codex'] = {
    'path': 'string',
    'action': 'string',
    'target': 'string',
    'name': 'string',
    'kind': 'string',
    'id': 'string',
    'text': 'string',
    'trait': 'string',
    'value': 'string',
    'start': 'integer',
    'end': 'integer',
    'radius': 'integer',
    'limit': 'integer',
    'offset': 'integer',
    'include_hidden': 'boolean',
    'hide_from_ai': 'boolean',
    'input': 'object',
    'out': 'string',
    'overwrite': 'boolean',
    'series_path': 'string',
    'author': 'string',
    'expect_revision': 'string',
    'request_id': 'string',
    'dry_run': 'boolean',
}
COMMANDS['entity'] = 'Read-only codex lookup, mentions and heatmap (entries hidden from AI are never shown)'
ARGUMENTS['entity'] = {
    key: ARGUMENTS['codex'][key]
    for key in (
        'path',
        'action',
        'target',
        'name',
        'kind',
        'text',
        'start',
        'end',
        'radius',
        'limit',
        'offset',
    )
}
# Word review round trip (byax.docx_review): send a review copy, merge the returned file as proposals.
COMMANDS['review-package'] = (
    'Send a Word review copy: pin its snapshot and write a DOCX with Track Changes on'
)
COMMANDS['merge-review'] = 'Merge a returned Word review copy as pending proposals and comment threads'
ARGUMENTS['review-package'] = {
    'path': 'string',
    'action': 'string',
    'out': 'string',
    'restrict': 'string',
    'reviewer': 'string',
    'overwrite': 'boolean',
    'expect_revision': 'string',
    'dry_run': 'boolean',
}
ARGUMENTS['merge-review'] = {
    'path': 'string',
    'reviewed_path': 'string',
    'review_id': 'string',
    'expect_revision': 'string',
    'dry_run': 'boolean',
}
# Extension layer (byax.extensions, byax.macros): out-of-process checks and export hooks; saved macros.
COMMANDS['extensions'] = (
    'List, inspect, install, enable and run extensions (custom checks and export hooks, out of process)'
)
COMMANDS['macro'] = 'List, save, run (or dry-run) and remove saved command macros'
ARGUMENTS['extensions'] = {
    'action': 'string',
    'id': 'string',
    'check': 'string',
    'hook': 'string',
    'document': 'string',
    'source_path': 'string',
    'sha256': 'string',
    'scopes': 'array',
    'enable': 'boolean',
    'replace': 'boolean',
    'overwrite': 'boolean',
    'exported_file': 'string',
    'hooks_dir': 'string',
    'format': 'string',
    'input': 'object',
    'token_env': 'string',
    'ttl': 'integer',
    'limit': 'integer',
}
ARGUMENTS['macro'] = {
    'action': 'string',
    'name': 'string',
    'store': 'string',
    'document': 'string',
    'input': 'object',
    'params': 'object',
    'dry_run': 'boolean',
    'allow_admin': 'boolean',
    'replace': 'boolean',
    'verbose': 'boolean',
    'expect_revision': 'string',
    'request_id': 'string',
    'config_dir': 'string',
}
# Beta-reader round trip (byax.beta_readers): read-and-comment copies out, reader comments back in.
COMMANDS['beta-copy'] = 'Write a read-and-comment copy for beta readers: self-contained HTML, PDF or Word'
COMMANDS['merge-comments'] = (
    'Merge beta readers\u2019 comment files (and annotated PDFs) as comments and proposals'
)
ARGUMENTS['beta-copy'] = {
    'path': 'string',
    'out': 'string',
    'format': 'string',
    'reviewer': 'string',
    'note': 'string',
    'companion': 'boolean',
    'overwrite': 'boolean',
    'expect_revision': 'string',
    'dry_run': 'boolean',
}
ARGUMENTS['merge-comments'] = {
    'path': 'string',
    'comments_path': 'array',
    'review_id': 'string',
    'expect_revision': 'string',
    'dry_run': 'boolean',
}
REQUIRED = {
    'compile': ['path'],
    'statistics': ['path'],
    'readability': ['path'],
    'collection': ['path'],
    'dependencies': ['path'],
    'view': ['path', 'as'],
    'proof': ['path', 'out'],
    'table': ['path', 'table'],
    'template-batch': ['path', 'csv', 'out'],
    'new': ['out'],
    'find': ['path', 'query'],
    'apply': ['path', 'input'],
    'transact': ['path', 'input'],
    'fill': ['path', 'input'],
    'diff': ['path', 'from_revision'],
    'import': ['path', 'out'],
    'save-as': ['path', 'out'],
    'convert': ['path', 'out'],
    'export': ['path', 'out'],
    'render': ['path', 'out'],
    'pack': ['path', 'out'],
    'unpack': ['path', 'out'],
    'transcribe': ['path', 'alias'],
    'batch': ['input'],
    'redact': ['path', 'out', 'input'],
    'reproduction': ['path'],
}
REQUIRED.update(
    {
        'library-search': ['path', 'library_id', 'query'],
        'library-read': ['path', 'library_id', 'document_id'],
        'cite': ['path', 'target', 'start', 'end', 'source'],
    }
)
for _name in (
    'inspect',
    'context',
    'history',
    'check',
    'review',
    'library',
    'source',
    'project',
    'sync',
    'goals',
    'drafts',
):
    REQUIRED[_name] = ['path']
for _name in ('project-search', 'codex', 'entity'):
    REQUIRED[_name] = ['path']
REQUIRED.update({'review-package': ['path'], 'merge-review': ['path', 'reviewed_path']})
REQUIRED.update({'beta-copy': ['path'], 'merge-comments': ['path', 'comments_path']})


def command_schema(command: str) -> dict[str, Any]:
    props: dict[str, dict[str, Any]] = {key: {'type': kind} for key, kind in ARGUMENTS[command].items()}
    for prop in props.values():
        if prop['type'] == 'array':
            prop['items'] = {'type': 'string'}
    if 'pages' in props:
        props['pages'] = {
            'oneOf': [
                {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}},
                {'type': 'string', 'enum': ['affected']},
            ]
        }
    return {
        'type': 'object',
        'properties': props,
        'required': REQUIRED.get(command, []),
        'additionalProperties': False,
    }


def _error(code: str, message: str, **details: Any) -> NoReturn:
    from .errors import ByaxError

    raise ByaxError(code, message, **details)


def _validate(command: str, args: Args) -> None:
    if not isinstance(command, str) or command not in COMMANDS:
        _error('invalid_input', f'Unknown command: {command}')
    if not isinstance(args, dict):
        _error('invalid_input', 'Arguments must be a JSON object')
    for key in set(args) - set(ARGUMENTS[command]):
        _error('invalid_input', f'Unknown argument: {key}', field=key)
    for key in REQUIRED.get(command, []):
        if key not in args or args[key] is None:
            _error('invalid_input', f'Missing required argument: {key}', field=key)
    types: dict[str, Any] = {'string': str, 'object': dict, 'array': list, 'boolean': bool, 'integer': int}
    for key, value in args.items():
        if value is None:
            continue
        kind = ARGUMENTS[command][key]
        if key == 'pages' and value == 'affected':
            continue
        if not isinstance(value, types[kind]) or (kind == 'integer' and isinstance(value, bool)):
            _error('invalid_input', f'{key} must be {kind}', field=key)
        if key in ('limit', 'workers', 'library_limit', 'library_chars') and value < 1:
            _error('invalid_input', f'{key} must be positive', field=key)
        if key == 'offset' and value < 0:
            _error('invalid_input', 'offset must be nonnegative', field=key)
        if kind == 'array' and not all(isinstance(x, int if key == 'pages' else str) for x in value):
            _error('invalid_input', f'Invalid elements in {key}', field=key)


def envelope(data: Any = None, doc: Document | None = None, **overrides: Any) -> Result:
    result: Result = {
        'schema_version': 1,
        'execution': 'completed',
        'validation': 'not_run',
        'review': 'not_required',
        'document_id': doc.get('id') if doc else None,
        'base_revision': doc.get('revision') if doc else None,
        'revision': doc.get('revision') if doc else None,
        'changed_ids': [],
        'findings': [],
        'coverage': {},
        'outputs': [],
    }
    if isinstance(data, dict):
        result.update(data)
    elif data is not None:
        result['data'] = data
    result.update(overrides)
    return result


def _destination(path: str, overwrite: bool = False, source: str | None = None) -> None:
    target = Path(path)
    if source and target.resolve() == Path(source).resolve():
        _error(
            'invalid_input', 'Output must differ from the source; use apply for in-place edits', field='out'
        )
    if target.exists() and not overwrite:
        _error('conflict', 'Output already exists; explicitly select overwrite', target=str(target))
    target.parent.mkdir(parents=True, exist_ok=True)


def _load(path: str) -> Document:
    from .storage import load_document

    return load_document(path)


def _gate(doc: Document, suites: list[str] | None, strict: bool = False) -> Result:
    from .checks import run_checks

    report = run_checks(doc, suites=suites)
    if report.get('validation') == 'failed':
        _error(
            'failed_requirement',
            'Required validation failed',
            findings=report.get('findings'),
            coverage=report.get('coverage'),
        )
    if (strict or suites) and report.get('validation') != 'passed':
        _error(
            'incomplete_coverage',
            'Strict policy requires complete validation coverage',
            coverage=report.get('coverage'),
        )
    return report


def _mutate(path: str, operation: Callable[[Document], tuple[Document, Result, bool]]) -> Result:
    from .storage import DocumentLock, save_document

    lock = DocumentLock(path)
    with lock:
        doc = _load(path)
        updated, result, save = operation(doc)
        if save:
            save_document(updated, path, owner=lock.owner)
        return envelope(result, updated)


def execute(command: str, arguments: Args | None = None) -> Result:
    """Dispatch with exclusive output ownership, including exports over open files."""
    arguments = {} if arguments is None else arguments
    _validate(command, arguments)
    if arguments.get('out'):
        from .storage import DocumentLock

        with DocumentLock(arguments['out']) as lock:
            return _execute(command, arguments, output_owner=lock.owner)
    return _execute(command, arguments)


def _execute(command: str, arguments: Args | None = None, *, output_owner: str | None = None) -> Result:
    """Execute one validated command; no printing and no implicit network calls."""
    a = {} if arguments is None else arguments
    _validate(command, a)
    handler = _HANDLERS.get('apply' if command == 'transact' else command)
    if handler is None:
        _error('unsupported_feature', f'Command is unavailable: {command}')
    return handler(dict(a), output_owner)


def _cmd_statistics(a: Args, owner: str | None) -> Result:
    from .statistics import document_statistics

    doc = _load(a['path'])
    return envelope(
        document_statistics(
            doc, output=a.get('format'), target=a.get('target'), start=a.get('start'), end=a.get('end')
        ),
        doc,
    )


def _cmd_readability(a: Args, owner: str | None) -> Result:
    from .readability import readability_document, readability_report

    doc = _load(a['path'])
    report = readability_report(doc, target=a.get('target'))
    result: Result = {'readability': report}
    if a.get('out'):
        from .exchange import export_document

        _destination(a['out'], a.get('overwrite', False), a['path'])
        title = 'Readability report: ' + (doc.get('metadata', {}).get('title') or 'Untitled')
        exported = export_document(
            readability_document(report, title),
            a['out'],
            format=a.get('format'),
            owner=owner,
            overwrite=a.get('overwrite', False),
        )
        for key in ('format', 'outputs', 'losses', 'findings', 'coverage', 'fidelity', 'review'):
            if key in exported:
                result[key] = exported[key]
    return envelope(result, doc)


def _cmd_collection(a: Args, owner: str | None) -> Result:
    from . import engine, profiles

    action = a.get('action', 'inspect')
    if action in ('inspect', 'export'):
        doc = _load(a['path'])
        collection = profiles.export_settings(doc)
        if action == 'export':
            if not a.get('out'):
                _error('invalid_input', 'Collection export requires --out')
            _destination(a['out'], a.get('overwrite', False), a['path'])
            profiles.write_collection(collection, a['out'])
        return envelope({'collection': collection}, doc, outputs=[a['out']] if action == 'export' else [])
    if action != 'apply':
        _error('invalid_input', 'Use collection --action inspect, export or apply')

    def apply(doc: Document) -> tuple[Document, Result, bool]:
        compiled = profiles.compile_collection(
            doc, a.get('input', {}), on_conflict=a.get('on_conflict', 'error')
        )
        operations, report = compiled['operations'], compiled['report']
        if not operations:
            return doc, envelope({'outcome': 'unchanged', 'report': report}, doc), False
        candidate, result = engine.apply(
            doc, {'expected_revision': doc['revision'], 'operations': operations}
        )
        result['collection'] = report
        return candidate, result, bool(operations)

    return _mutate(a['path'], apply)


def _cmd_dependencies(a: Args, owner: str | None) -> Result:
    from . import dependencies, engine

    action = a.get('action', 'inspect')
    if action == 'inspect':
        doc = _load(a['path'])
        return envelope(engine.dependency_report(doc), doc)
    if action not in ('refresh', 'embed'):
        _error('invalid_input', 'Use dependencies --action inspect, refresh or embed')

    def apply(doc: Document) -> tuple[Document, Result, bool]:
        if action == 'refresh':
            operations, report = dependencies.refresh_operations(
                doc,
                [a['target']] if a.get('target') else None,
                base_dir=Path(a['path']).resolve().parent,
                authorize=a.get('authorize', False),
                allow_network=a.get('allow_network', False),
            )
        else:
            if not a.get('target'):
                _error('invalid_input', 'Embedding requires --target')
            operations, report = [{'type': 'embed_dependency', 'target': a['target']}], {}
        if not operations:
            return doc, envelope({'outcome': 'unchanged', 'report': report}, doc), False
        candidate, result = engine.apply(
            doc, {'expected_revision': doc['revision'], 'operations': operations}
        )
        result['dependencies'] = report
        return candidate, result, bool(operations)

    return _mutate(a['path'], apply)


def _cmd_view(a: Args, owner: str | None) -> Result:
    from .formats import project
    from .model import text_of

    doc = _load(a['path'])
    projected, report = project(doc, a['as'], 'source' if a.get('source') else 'rendered')
    return envelope({**report, 'text': text_of(projected), 'blocks': projected['blocks']}, doc)


def _cmd_version(a: Args, owner: str | None) -> Result:
    from . import __version__

    result = {
        'engine_version': __version__,
        'operation_schema_version': 1,
        'offset_unit': 'unicode_scalar',
        'range_end': 'exclusive',
    }
    if a.get('check_updates'):
        # The one place a version query touches the network, and only when asked for by name.
        from .updates import check_for_updates

        result['update_check'] = check_for_updates()
    return envelope(result)


def _cmd_sample(a: Args, owner: str | None) -> Result:
    from .samples import install_sample

    copied = install_sample(a.get('out'))
    return envelope({'sample': copied}, outputs=[copied['path']])


def _cmd_capabilities(a: Args, owner: str | None) -> Result:
    from .formats import FORMATS
    from .providers import list_capabilities

    return envelope(
        {
            'commands': COMMANDS,
            'providers': list_capabilities(),
            'formats': list(FORMATS) + ['doc'],
            'format_capabilities': FORMATS,
            'topic': a.get('topic'),
            'transactions': 'atomic envelope under exclusive file lock',
            'network': 'only explicitly selected provider calls',
            'offset_unit': 'unicode_scalar',
            'limitations': [
                'Format fidelity and provider availability are reported per operation.',
                'Native OS print dialogs require the desktop application.',
            ],
        }
    )


def _cmd_schema(a: Args, owner: str | None) -> Result:
    from . import engine

    topic = a.get('topic', 'commands')
    if topic in ('operations', 'operation', 'editing'):
        if hasattr(engine, 'operation_schema'):
            return envelope({'schema': engine.operation_schema()})
        return envelope({'schema': getattr(engine, 'OP_SCHEMAS', {}), 'offset_unit': 'unicode_scalar'})
    return envelope({'commands': {key: command_schema(key) for key in COMMANDS}})


def _cmd_new(a: Args, owner: str | None) -> Result:
    from . import model, storage

    _destination(a['out'], a.get('overwrite', False))
    doc = model.new_document(a.get('title', 'Untitled'), a.get('text', ''))
    storage.save_document(doc, a['out'], owner=owner)
    return envelope(doc=doc, outputs=[a['out']])


def _cmd_inspect(a: Args, owner: str | None) -> Result:
    from . import engine

    doc = _load(a['path'])
    result = engine.inspect(
        doc,
        section='outline' if a.get('outline') else a.get('section'),
        target=a.get('target'),
        limit=a.get('limit', 100),
    )
    return envelope(result, doc)


def _cmd_context(a: Args, owner: str | None) -> Result:
    from . import engine

    doc = _load(a['path'])
    result = engine.context(
        doc, target=a.get('target'), limit=a.get('limit', 10000), profile=a.get('profile')
    )
    if a.get('library_query'):
        from .retrieval import augment_context, retrieve_context
        from .series import with_series

        # Series-level library attachments take part; the document's own attachments win.
        retrieval = retrieve_context(
            with_series(doc, a['path']),
            a['library_query'],
            target=a.get('target'),
            profile=a.get('profile'),
            limit=a.get('library_limit', 12),
            max_chars=a.get('library_chars', 12000),
            remote=a.get('remote_bound', False),
            method=a.get('library_method', 'auto'),
            pinned=a.get('library_pinned'),
        )
        result = augment_context(result, retrieval)
    from .codex import redact_for_ai

    return envelope(redact_for_ai(doc, result), doc)


def _cmd_find(a: Args, owner: str | None) -> Result:
    from . import engine

    doc = _load(a['path'])
    result = engine.find(
        doc,
        a['query'],
        case_sensitive=a.get('case_sensitive', False),
        whole_word=a.get('whole_word', False),
        regex=a.get('regex', False),
    )
    return envelope(result, doc)


def _cmd_diff(a: Args, owner: str | None) -> Result:
    from . import engine

    doc = _load(a['path'])
    return envelope(engine.diff(doc, a['from_revision']), doc)


def _cmd_check(a: Args, owner: str | None) -> Result:
    from .checks import run_checks

    doc = _load(a['path'])
    if a.get('citations'):
        from .grounding import audit_citations

        if a.get('suite') or a.get('family'):
            _error('invalid_input', 'Run --citations on its own; it is a separate audit', field='citations')
        report = audit_citations(doc)
        return envelope(report, doc, **({'strict': True} if a.get('strict') else {}))
    suites = a.get('suite')
    if a.get('family'):
        from .profiles import FAMILY_SUITES

        unknown = sorted(set(a['family']) - set(FAMILY_SUITES))
        if unknown:
            _error('invalid_input', 'Unknown rule family', field='family', supported=sorted(FAMILY_SUITES))
        suites = list(dict.fromkeys([*(suites or []), *(x for f in a['family'] for x in FAMILY_SUITES[f])]))
    result = run_checks(doc, suites=suites, limit=a.get('limit', 100), profile=a.get('profile'))
    if a.get('strict'):
        result = dict(result, strict=True)
    return envelope(result, doc)


def _cmd_apply(a: Args, owner: str | None, *, fill: bool = False) -> Result:
    from . import engine

    request = dict(a['input'])
    if fill:
        request = {'operations': [{'type': 'fill_fields', 'values': request}]}
    for option, key in (
        ('expect_revision', 'expected_revision'),
        ('request_id', 'request_id'),
        ('require_suite', 'required_suites'),
    ):
        if a.get(option) is not None:
            request[key] = a[option]

    def apply(doc: Document) -> tuple[Document, Result, bool]:
        candidate, result = engine.apply(doc, request, dry_run=a.get('dry_run', False))
        return candidate, result, not a.get('dry_run', False)

    return _mutate(a['path'], apply)


def _cmd_fill(a: Args, owner: str | None) -> Result:
    return _cmd_apply(a, owner, fill=True)


def _cmd_history(a: Args, owner: str | None) -> Result:
    from . import engine

    action = a.get('action', 'list')
    if action == 'list':
        doc = _load(a['path'])
        result = engine.inspect(doc, section='history', limit=a.get('limit', 100))
        result['branches'] = doc['history'].get('branches', {})
        result['current_branch'] = doc['history'].get('current_branch', 'main')
        return envelope(result, doc)

    def history(doc: Document) -> tuple[Document, Result, bool]:
        if a.get('expect_revision') and doc['revision'] != a['expect_revision']:
            _error(
                'stale_revision',
                'Document revision changed',
                expected=a['expect_revision'],
                actual=doc['revision'],
            )
        if action in ('checkpoint', 'branch', 'switch_branch'):
            if not a.get('name'):
                _error('invalid_input', 'History action requires --name')
            updated, result = getattr(engine, action)(doc, a['name'])
        elif action == 'restore':
            if not a.get('revision'):
                _error('invalid_input', 'Restore requires --revision')
            updated, result = engine.restore(doc, a['revision'])
        else:
            _error('invalid_input', f'Unknown history action: {action}')
        return updated, result, True

    return _mutate(a['path'], history)


_REVIEW_OPS = {
    'propose': 'propose',
    'accept': 'accept_proposal',
    'reject': 'reject_proposal',
    'comment': 'add_comment',
    'resolve': 'resolve_comment',
    'accept-change': 'accept_change',
    'reject-change': 'reject_change',
}


def _cmd_review(a: Args, owner: str | None) -> Result:
    action = a.get('action', 'list')
    if action == 'list':
        doc = _load(a['path'])
        return envelope({'items': doc.get('review', {})}, doc)
    if action not in _REVIEW_OPS:
        _error('invalid_input', f'Unknown review action: {action}')
    op = dict(a.get('input') or {})
    if action == 'propose':
        op = {'values': op}
    op['type'] = _REVIEW_OPS[action]
    if a.get('target'):
        op['target'] = a['target']
    return execute(
        'apply',
        {
            'path': a['path'],
            'input': {'operations': [op]},
            **{k: a[k] for k in ('expect_revision', 'request_id', 'dry_run') if k in a},
        },
    )


def _exchange(command: str) -> Handler:
    """import/convert read exchange formats; save-as/export read native packages."""

    def handler(a: Args, owner: str | None) -> Result:
        from . import storage
        from .exchange import export_document, import_document

        if not a.get('dry_run'):
            _destination(a['out'], a.get('overwrite', False), a['path'])
        if command in ('import', 'convert'):
            doc, imported = import_document(a['path'])
        else:
            doc, imported = _load(a['path']), None
        gate = (
            _gate(doc, a.get('require_suite'), a.get('strict', False))
            if a.get('require_suite') or a.get('strict')
            else None
        )
        overwrite = a.get('overwrite', False)
        if command in ('import', 'save-as'):
            storage.save_document(doc, a['out'], owner=owner)
            result: Result = {'outputs': [a['out']]}
        elif a.get('dry_run'):
            fmt = a.get('format') or Path(a['out']).suffix.lstrip('.')
            result = export_document(doc, None, format=fmt, profile=a.get('profile'), preview=True)
        elif a.get('profile') == 'clean':
            from .publication import export_clean

            result = export_clean(
                doc, a['out'], format=a.get('format'), profile=None, owner=owner, overwrite=overwrite
            )
        else:
            result = export_document(
                doc,
                a['out'],
                format=a.get('format'),
                profile=a.get('profile'),
                owner=owner,
                overwrite=overwrite,
            )
        if imported:
            result['import_report'] = imported
        if command == 'export' and a.get('hooks') and not a.get('dry_run'):
            from .extensions import run_export_hooks

            result['hooks'] = run_export_hooks(
                a['out'],
                fmt=(a.get('format') or Path(a['out']).suffix.lstrip('.')).lower(),
                loss_report={'losses': result.get('losses', []), 'fidelity': result.get('fidelity')},
                output_dir=a.get('hooks_dir') or a['out'] + '.hooks',
                overwrite=a.get('overwrite', False),
            )
        if gate:
            result['required_checks'] = gate
            result['validation'] = gate['validation']
        return envelope(result, doc)

    return handler


def _cmd_render(a: Args, owner: str | None) -> Result:
    from .layout import render

    _destination(a['out'], a.get('overwrite', False))
    doc = _load(a['path'])
    pages, selection = _affected_pages(doc) if a.get('pages') == 'affected' else (a.get('pages'), None)
    result = render(doc, a['out'], pages=pages)
    if selection:
        result['selection'] = selection
    return envelope(result, doc)


def _cmd_pack(a: Args, owner: str | None) -> Result:
    import tempfile

    from . import storage

    _destination(a['out'], a.get('overwrite', False), a['path'])
    with tempfile.TemporaryDirectory(dir=Path(a['out']).parent) as temporary:
        stage = Path(temporary) / 'packed.byax'
        storage.pack(a['path'], stage)
        os.replace(stage, a['out'])
    return envelope(outputs=[a['out']])


def _cmd_unpack(a: Args, owner: str | None) -> Result:
    from . import storage

    _destination(a['out'], a.get('overwrite', False), a['path'])
    storage.unpack(a['path'], a['out'], include_history=a.get('include_history', False))
    return envelope(outputs=[a['out']])


def _cmd_providers(a: Args, owner: str | None) -> Result:
    from .providers import ProviderConfig, ProviderRegistry

    registry = ProviderRegistry(config_dir=a.get('config_dir'))
    action = a.get('action', 'list')
    if action == 'list':
        return envelope({'providers': registry.list_with_locality()})
    if action == 'configure':
        try:
            config = ProviderConfig(**a.get('input', {}))
        except TypeError as exc:
            _error('invalid_input', str(exc), field='input')
        registry.save(config)
        return envelope({'providers': registry.list_with_locality()})
    if not a.get('alias'):
        _error('invalid_input', 'Provider action requires --alias')
    if action == 'preview':
        return envelope(registry.preview(a['alias'], text=a.get('text', ''), context=a.get('input')))
    if action == 'test':
        options = a.get('input', {})
        if set(options) - {'capability', 'text', 'audio_path'}:
            _error('invalid_input', 'Provider test input supports capability, text and audio_path')
        return envelope(registry.test(a['alias'], authorized=a.get('allow_remote', False), **options))
    if action == 'review':
        if not a.get('text'):
            _error('invalid_input', 'Review requires --text')
        return envelope(
            registry.review(
                a['alias'], a['text'], context=a.get('input'), authorized=a.get('allow_remote', False)
            )
        )
    _error('invalid_input', f'Unknown provider action: {action}')


def _job_worker(payload: Args, cancel: threading.Event) -> Result:
    if cancel.is_set():
        _error('cancelled', 'Job cancelled')
    if payload.get('document_snapshot'):
        import tempfile

        from .storage import save_document

        with tempfile.TemporaryDirectory(prefix='byax-job-input-') as temporary:
            snapshot_path = str(Path(temporary) / 'frozen.byax')
            save_document(payload['document_snapshot'], snapshot_path)
            result = execute(payload['command'], dict(payload['arguments'], path=snapshot_path))
        result['stale'] = _load(payload['arguments']['path'])['revision'] != payload['revision']
        if result['stale']:
            result['review'] = 'required'
    else:
        result = execute(payload['command'], payload['arguments'])
    child_code = result_exit_code(result)
    if child_code:
        # Keep the child's error class so the job exits like a direct call.
        code = (result.get('error') or {}).get('code') or _EXIT_ERRORS.get(child_code, 'execution_failure')
        _error(code, 'Job did not satisfy policy', result=result)
    return result


def _submit_job(manager: JobManager, a: Args) -> Result:
    spec = a.get('input', {})
    allowed = ('batch', 'convert', 'check', 'render', 'providers')
    if spec.get('command') not in allowed or not isinstance(spec.get('arguments'), dict):
        _error(
            'invalid_input',
            'Job input requires command (batch/convert/check/render/providers) and arguments',
        )
    frozen = dict(spec)
    path = spec['arguments'].get('path')
    if path and spec['command'] in ('check', 'render'):
        frozen['document_snapshot'] = _load(path)
        frozen['revision'] = frozen['document_snapshot']['revision']
    if spec['command'] == 'render':
        from .layout import ensure_app

        ensure_app()
    job = manager.submit(spec['command'], frozen, _job_worker)
    return manager.wait(job['id'])


def _cmd_jobs(a: Args, owner: str | None) -> Result:
    from .jobs import JobManager

    manager = JobManager(root=a.get('root'))
    try:
        action = a.get('action', 'list')
        if action == 'list':
            return envelope({'jobs': manager.list()})
        if action == 'submit':
            result = _submit_job(manager, a)
        else:
            if action not in ('status', 'result', 'cancel', 'recover') or not a.get('id'):
                _error(
                    'invalid_input',
                    'Use submit/list/status/result/cancel/recover; supply --id where required',
                )
            result = getattr(manager, action)(a['id'])
        if result.get('state') == 'failed' and isinstance(result.get('error'), dict):
            from .errors import ByaxError

            code = result['error'].get('code', 'execution_failure')
            if ByaxError(code, '').exit_code != 1:
                raise ByaxError(code, 'Job ended with an error', job=result)
        return envelope({'job': result}, execution=result.get('state', 'completed'))
    finally:
        manager.close(wait=True)


def _cmd_proof(a: Args, owner: str | None) -> Result:
    from .proof import create_proof

    doc = _load(a['path'])
    selections = a.get('input', {})
    if set(selections) - {'crops', 'overlays'}:
        _error('invalid_input', 'Proof JSON accepts crops and overlays arrays')
    pages, selection = _affected_pages(doc) if a.get('pages') == 'affected' else (a.get('pages'), None)
    result = create_proof(
        doc,
        a['out'],
        pages=pages,
        crops=selections.get('crops'),
        overlays=selections.get('overlays'),
        contact=not a.get('no_contact', False),
        columns=a.get('columns', 3),
        thumbnail_width=a.get('thumbnail_width', 320),
        owner=owner,
    )
    if selection:
        result['selection'] = selection
    return envelope(result, doc)


def _cmd_table(a: Args, owner: str | None) -> Result:
    from .table_io import export_csv, import_csv

    common = {key: a[key] for key in ('encoding', 'dialect', 'delimiter') if key in a}
    if a.get('action', 'import') == 'import':
        if not a.get('csv'):
            _error('invalid_input', 'Table import requires --csv')
        if a.get('out'):
            _error('invalid_input', 'Table import edits the native document; --out is for CSV export')

        def import_table(doc: Document) -> tuple[Document, Result, bool]:
            updated, result = import_csv(
                doc,
                a['table'],
                a['csv'],
                expected_revision=a.get('expect_revision'),
                grow=a.get('grow', False),
                dry_run=a.get('dry_run', False),
                request_id=a.get('request_id'),
                **common,
            )
            return updated, result, not a.get('dry_run', False)

        return _mutate(a['path'], import_table)
    if a.get('action') != 'export' or not a.get('out'):
        _error(
            'invalid_input',
            'Use table --action import --csv input.csv or --action export --out output.csv',
        )
    _destination(a['out'], a.get('overwrite', False), a['path'])
    doc = _load(a['path'])
    return envelope(
        export_csv(
            doc,
            a['table'],
            a['out'],
            newline=a.get('newline'),
            overwrite=a.get('overwrite', False),
            owner=owner,
            **common,
        ),
        doc,
    )


def _cmd_template_batch(a: Args, owner: str | None) -> Result:
    from .exchange import export_document
    from .storage import DocumentLock
    from .table_io import template_rows

    _destination(a['out'], a.get('overwrite', False))
    output_dir = Path(a['out'])
    if output_dir.exists() and not output_dir.is_dir():
        _error('invalid_input', 'Template batch output must be a directory')
    template = _load(a['path'])
    # Parse and validate before creating deliverables, then retain every row outcome.
    rows = template_rows(
        template, a['csv'], **{key: a[key] for key in ('encoding', 'dialect', 'delimiter') if key in a}
    )
    fmt = a.get('format', 'byax')
    if fmt not in ('byax', 'pdf', 'docx', 'odt', 'txt', 'md', 'html', 'rtf', 'epub'):
        _error('invalid_input', 'Unsupported template batch output format')
    outcomes = []
    for index, (doc, result) in enumerate(rows, start=1):
        output = output_dir / f'document-{index:04d}.{fmt}'
        if doc is not None:
            try:
                with DocumentLock(output) as lock:
                    _destination(str(output), a.get('overwrite', False))
                    exported = export_document(
                        doc, output, format=fmt, profile=a.get('profile'), owner=lock.owner
                    )
                result = envelope(exported, doc, csv_row=result['csv_row'])
            except Exception as exc:  # noqa: BLE001 - preserve every batch row outcome
                result, _ = error_result(exc)
                result['csv_row'] = index + 1
        outcomes.append(result)
    failed = sum(bool(result_exit_code(item)) for item in outcomes)
    return envelope(
        {
            'items': outcomes,
            'total': len(outcomes),
            'failed': failed,
            'template_revision': template['revision'],
        },
        execution='failed' if failed else 'completed',
    )


def _cmd_redact(a: Args, owner: str | None) -> Result:
    from .publication import save_sanitized

    _destination(a['out'], False, a['path'])
    doc = _load(a['path'])
    if set(a['input']) - {'targets', 'terms', 'preserve'}:
        _error('invalid_input', 'Redaction input supports targets, terms and preserve', field='input')
    result = save_sanitized(
        doc,
        a['out'],
        a['input'].get('targets', []),
        terms=a['input'].get('terms'),
        preserve=a['input'].get('preserve'),
    )
    return envelope(result, doc)


def _cmd_reproduction(a: Args, owner: str | None) -> Result:
    from .publication import reproduce, reproduction_record

    doc = _load(a['path'])
    if a.get('action', 'record') == 'record':
        result = reproduction_record(doc, settings=a.get('input'))
        if a.get('out'):
            _destination(a['out'], a.get('overwrite', False))
            Path(a['out']).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return envelope({'record': result, 'outputs': [a['out']] if a.get('out') else []}, doc)
    if a.get('action') != 'verify' or not a.get('input') or not a.get('out'):
        _error('invalid_input', 'Verification requires --input record.json and --out directory')
    _destination(a['out'], a.get('overwrite', False))
    return envelope(reproduce(doc, a['input'], a['out']), doc)


def _affected_pages(doc: Document) -> tuple[list[int], Result]:
    """Conservative reflow range derived from retained before/after target layout."""
    from .engine import diff, revision_document
    from .layout import build_document, resolve_layout

    history = doc.get('history', {}).get('revisions', [])
    if not history:
        _error(
            'incomplete_coverage',
            'Affected pages need a retained previous revision; choose explicit pages or render all pages',
            field='pages',
        )
    base_revision = history[-1]['revision']
    before = revision_document(doc, base_revision)
    changes = diff(doc, base_revision)['changes']
    current_layout, old_layout = build_document(doc), build_document(before)
    current_resolved = resolve_layout(current_layout, doc)
    old_resolved = resolve_layout(old_layout, before)
    total, old_total = len(current_resolved['pages']), len(old_resolved['pages'])
    global_change = bool(changes['properties'] or changes['order_changed'])
    if any(block.get('type') == 'toc' for block in doc['blocks']):
        global_change = True
    if old_total != total:
        # Page-count fields or numbering in any repeated region can affect earlier pages.
        global_change = True
    positions = []

    def add(resolved: Result, target: str) -> bool:
        page = resolved['positions'].get(target)
        if page is None:
            return False
        positions.append(page)
        return True

    for item in changes['modified']:
        global_change |= not add(current_resolved, item['id'])
        global_change |= not add(old_resolved, item['id'])
    for item in changes['added']:
        global_change |= not add(current_resolved, item['id'])
    for item in changes['removed']:
        global_change |= not add(old_resolved, item['id'])
    first = 1 if global_change or not positions else min(max(1, min(positions)), total)
    pages = list(range(first, total + 1))
    return pages, {
        'kind': 'affected',
        'base_revision': base_revision,
        'revision': doc['revision'],
        'pages': pages,
        'method': 'conservative before/after target page mapping plus downstream reflow',
        'global_reflow': global_change,
        'previous_page_count': old_total,
        'coverage': 'All potentially changed current pages; renderer findings still apply.',
    }


def _transcribe(a: Args, owner: str | None = None) -> Result:
    from .jobs import JobManager
    from .model import new_document
    from .providers import ProviderRegistry
    from .transcription import DictationSession

    mode = {'cleaned': 'cleanup', 'document-aware': 'document_aware'}.get(
        a.get('mode', ''), a.get('mode', 'direct')
    )
    if mode not in ('direct', 'cleanup', 'document_aware'):
        _error('invalid_input', 'Mode must be direct, cleaned, or document-aware')
    if a.get('insert') and (not a.get('document') or not a.get('target') or not a.get('expect_revision')):
        _error('invalid_input', 'Insertion requires --document, --target, and --expect-revision')
    if a.get('document') and not a.get('target'):
        _error('invalid_input', 'Select a target block for document transcription')
    registry = ProviderRegistry(config_dir=a.get('config_dir'))
    doc = _load(a['document']) if a.get('document') else new_document()
    if a.get('expect_revision') and doc['revision'] != a['expect_revision']:
        _error('stale_revision', 'Document changed before transcription began')
    target = a.get('target') or doc['blocks'][0]['id']
    session = DictationSession(doc, target, offset=a.get('offset', 0), mode=mode)
    context = None
    if mode == 'document_aware':
        from .engine import context as build_context

        context = build_context(doc, target=target)
    remote = registry.preview(a['alias'], kind='transcription')['remote']
    if mode != 'direct' and a.get('cleanup_alias'):
        remote = remote or registry.preview(a['cleanup_alias'], kind='review')['remote']
    manager = JobManager(root=a.get('job_root'))
    try:

        def worker(payload: Args, cancel: threading.Event) -> Result:
            return session.process(
                registry,
                payload['alias'],
                payload['audio_path'],
                editor=a.get('cleanup_alias'),
                context=context,
                language=a.get('language', 'en'),
                authorized=a.get('allow_remote', False),
                cancel_event=cancel,
            )

        job = manager.submit(
            'transcription',
            {
                'revision': doc['revision'],
                'alias': a['alias'],
                'audio_path': str(Path(a['path']).resolve()),
                'mode': mode,
                'remote': remote,
                'source_versions': (context or {}).get('source_versions', {}),
                'profile_version': (context or {}).get('profile_version'),
            },
            worker,
        )
        result = manager.wait(job['id'])
        if result['state'] != 'completed':
            return envelope({'job': result}, doc, execution=result['state'], review='required')
        if a.get('insert'):

            def insert(current: Document) -> tuple[Document, Result, bool]:
                updated, change = session.apply(current)
                return updated, change, True

            inserted = _mutate(a['document'], insert)
            inserted['transcription'] = result
            return inserted
        return envelope({'job': result, 'transcript': result['result']}, doc, review='required')
    finally:
        manager.close(wait=True)


def _batch(a: Args, owner: str | None = None) -> Result:
    from .jobs import run_batch

    spec = a['input']
    if set(spec) - {'items'} or not isinstance(spec.get('items'), list):
        _error('invalid_input', 'Batch input must contain an items array')
    if any(
        isinstance(item, dict)
        and (item.get('format') == 'pdf' or str(item.get('out', '')).lower().endswith('.pdf'))
        for item in spec['items']
    ):
        from .layout import ensure_app

        ensure_app()
    report = run_batch(
        spec['items'],
        lambda item: execute('convert', item),
        workers=a.get('workers', 1),
        overwrite=a.get('overwrite', False),
        max_memory_mb=a.get('max_memory_mb', 1024),
        staging=a.get('staging', 'each'),
        describe_error=error_result,
    )
    return envelope(report, execution=report.pop('execution'))


def _cmd_fonts(a: Args, owner: str | None) -> Result:
    from . import fonts

    action = a.get('action', 'inspect')
    if action == 'inspect':
        if not a.get('family'):
            _error('invalid_input', 'Font inspection requires --family', field='family')
        info = fonts.inspect_font(a['family'], a.get('bold', False), a.get('italic', False))
        info.pop('path', None)
        return envelope({'font': info})
    if not a.get('path'):
        _error('invalid_input', f'Font action {action} requires a document path', field='path')
    if action == 'glyphs':
        doc = _load(a['path'])
        return envelope({'glyph_fallbacks': fonts.glyph_fallbacks(doc)}, doc)
    if action == 'embed':
        if not a.get('family'):
            _error('invalid_input', 'Font embedding requires --family', field='family')

        def embed(doc: Document) -> tuple[Document, Result, bool]:
            from .engine import _commit

            updated, info = fonts.embed_font(doc, a['family'], a.get('bold', False), a.get('italic', False))
            committed = _commit(doc, updated, [{'type': 'embed_font', 'family': a['family']}])
            return committed, {'font': info, 'changed_ids': [info['sha256']]}, True

        return _mutate(a['path'], embed)
    _error('invalid_input', 'Use fonts --action inspect, glyphs or embed')


from .beta_readers import beta_copy_command, merge_comments_command
from .cli_codex import codex_command, entity_command
from .cli_libraries import cite_command, library_command, library_read, library_search, source_command
from .compile import compile_command
from .docx_review import merge_review_command, review_package_command
from .drafts import drafts_command
from .extensions import extensions_command
from .goals import goals_command
from .grammar import grammar_command
from .language_packs import languages_command
from .local_models import models_command
from .macros import macro_command
from .project import project_command
from .project_index import project_search_command
from .sync_safety import sync_command

_HANDLERS: dict[str, Handler] = {
    'library': library_command,
    'library-search': library_search,
    'library-read': library_read,
    'source': source_command,
    'cite': cite_command,
    'statistics': _cmd_statistics,
    'readability': _cmd_readability,
    'collection': _cmd_collection,
    'dependencies': _cmd_dependencies,
    'view': _cmd_view,
    'version': _cmd_version,
    'capabilities': _cmd_capabilities,
    'schema': _cmd_schema,
    'new': _cmd_new,
    'inspect': _cmd_inspect,
    'context': _cmd_context,
    'find': _cmd_find,
    'diff': _cmd_diff,
    'check': _cmd_check,
    'apply': _cmd_apply,
    'fill': _cmd_fill,
    'history': _cmd_history,
    'review': _cmd_review,
    'import': _exchange('import'),
    'convert': _exchange('convert'),
    'export': _exchange('export'),
    'save-as': _exchange('save-as'),
    'render': _cmd_render,
    'pack': _cmd_pack,
    'unpack': _cmd_unpack,
    'providers': _cmd_providers,
    'transcribe': _transcribe,
    'jobs': _cmd_jobs,
    'proof': _cmd_proof,
    'table': _cmd_table,
    'template-batch': _cmd_template_batch,
    'batch': _batch,
    'redact': _cmd_redact,
    'reproduction': _cmd_reproduction,
    'fonts': _cmd_fonts,
    'project': project_command,
    'sync': sync_command,
    'sample': _cmd_sample,
    'grammar': grammar_command,
    'languages': languages_command,
    'goals': goals_command,
    'drafts': drafts_command,
    'compile': compile_command,
    'project-search': project_search_command,
    'codex': codex_command,
    'entity': entity_command,
    'review-package': review_package_command,
    'merge-review': merge_review_command,
    'models': models_command,
    'extensions': extensions_command,
    'macro': macro_command,
    'beta-copy': beta_copy_command,
    'merge-comments': merge_comments_command,
}


_EXIT_ERRORS = {
    2: 'invalid_input',
    3: 'conflict',
    4: 'failed_requirement',
    5: 'incomplete_coverage',
    6: 'cancelled',
    7: 'pending',
}


def result_exit_code(result: Result) -> int:
    state = result.get('execution')
    if state == 'cancelled':
        return 6
    if state in ('pending', 'uncertain', 'queued', 'running'):
        return 7
    if state == 'failed':
        return 1
    if result.get('validation') == 'failed':
        return 4
    if result.get('strict') and (result.get('validation') != 'passed' or result.get('review') == 'required'):
        return 5
    return 0


def error_result(exc: BaseException) -> tuple[Result, int]:
    from .errors import ByaxError

    if isinstance(exc, ByaxError):
        data = exc.to_dict()
        code = exc.exit_code
    elif isinstance(exc, (ValueError, TypeError, json.JSONDecodeError)):
        data, code = {'code': 'invalid_input', 'message': str(exc)}, 2
    elif isinstance(exc, (ImportError, ModuleNotFoundError)):
        data, code = {'code': 'missing_dependency', 'message': str(exc)}, 1
    elif isinstance(exc, OSError):
        data, code = {'code': 'execution_failure', 'message': str(exc)}, 1
    else:
        data, code = {'code': 'execution_failure', 'message': str(exc)}, 1
    return envelope({'error': data}, execution='failed'), code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _error('invalid_input', message)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog='byax',
        description='ByAuthorX shared document engine. UTF-8 JSON stdout; diagnostics use stderr. No GUI or provider call is implicit.',
        epilog='Exit codes: 0 completed, 1 execution/dependency, 2 input, 3 conflict, 4 required validation, 5 strict coverage/review, 6 cancelled, 7 pending/uncertain.',
    )
    parser.add_argument('--json', action='store_true', help='Emit versioned JSON (the default)')
    parser.add_argument('--human', action='store_true', help='Emit indented human-readable JSON')
    sub = parser.add_subparsers(dest='command', required=True)
    for name, help_text in COMMANDS.items():
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
        p.add_argument('--human', action='store_true', default=argparse.SUPPRESS)
        for key, kind in ARGUMENTS[name].items():
            if key == 'path':
                p.add_argument('path', help='Input document, source directory, or audio file')
                continue
            flag = '--from' if key == 'from_revision' else '--' + key.replace('_', '-')
            aliases = ('--comments',) if key == 'comments_path' else ()
            options: dict = {'dest': key, 'default': argparse.SUPPRESS}
            if key in REQUIRED.get(name, []):
                options['required'] = True
            if kind == 'boolean':
                options['action'] = 'store_true'
            elif kind == 'integer':
                options['type'] = int
            elif kind == 'array' and key != 'pages':
                options['action'] = 'append'
            if key == 'input':
                options['help'] = 'UTF-8 JSON file, or - for stdin'
            elif key == 'pages':
                options['help'] = '1-based pages/ranges (1,3-5), or affected for the last committed edit'
            p.add_argument(flag, *aliases, **options)
    service = sub.add_parser('serve', help='Explicitly start an authenticated, scoped loopback REST service')
    service.add_argument(
        '--root', action='append', required=True, help='Authorized filesystem root; repeat as needed'
    )
    service.add_argument('--port', type=int, default=8765)
    service.add_argument(
        '--token-env', default='BYAX_SERVICE_TOKEN', help='Environment variable containing access token'
    )
    sub.add_parser('mcp', help='Start newline-delimited stdio MCP server')
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        ns = vars(build_parser().parse_args(argv))
        command = ns.pop('command')
        human = ns.pop('human', False)
        ns.pop('json', None)
        if command == 'mcp':
            from .mcp import main as mcp_main

            return mcp_main()
        if command == 'serve':
            from .service import serve

            token = os.environ.get(ns['token_env'], '')
            serve(ns['root'], token=token, port=ns['port'])
            return 0
        if 'input' in ns:
            if ns['input'] == '-':
                raw = sys.stdin.read(16 * 1024 * 1024 + 1)
            else:
                try:
                    with Path(ns['input']).open(encoding='utf-8') as handle:
                        raw = handle.read(16 * 1024 * 1024 + 1)
                except OSError as exc:
                    _error('invalid_input', f'Cannot read --input file: {exc.strerror or exc}', field='input')
            if len(raw) > 16 * 1024 * 1024:
                _error('limit_exceeded', 'JSON input exceeds 16 MiB')
            ns['input'] = json.loads(raw)
        if isinstance(ns.get('params'), str):
            ns['params'] = json.loads(ns['params'])  # macro parameters: inline JSON object
        if 'pages' in ns and ns['pages'] != 'affected':
            pages: list[int] = []
            for token in ns['pages'].split(','):
                if '-' in token:
                    start, end = map(int, token.split('-', 1))
                    if end < start or end - start > 10000:
                        _error('invalid_input', 'Invalid or excessive page range')
                    pages.extend(range(start, end + 1))
                else:
                    pages.append(int(token))
            if any(page < 1 for page in pages):
                _error('invalid_input', 'Page numbers start at 1')
            ns['pages'] = pages
        result = execute(command, ns)
        code = result_exit_code(result)
    except KeyboardInterrupt:
        result, code, human = envelope(execution='cancelled'), 6, False
    except Exception as exc:  # noqa: BLE001 - transport boundary must return structured failures
        result, code = error_result(exc)
        human = False
    print(json.dumps(result, ensure_ascii=False, indent=2 if human else None, default=str))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
