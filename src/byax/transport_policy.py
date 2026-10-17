"""Shared transport policy: administrative actions and path-bearing arguments.

Administrative actions are available only in the local CLI and desktop. Every
argument that names a filesystem location is listed in PATH_KEYS so scoped
transports (REST) resolve it under authorized roots, wherever it is nested.
"""

from .errors import ByaxError

PATH_KEYS = frozenset(
    (
        'path',
        'out',
        'document',
        'config_dir',
        'root',
        'audio_path',
        'source_path',
        'job_root',
        'csv',
        'reference_dir',
        'copy_path',
        'series_path',
        'reviewed_path',
    )
)


# Extension actions that install, change or authorize code: local CLI and desktop only.
EXTENSION_ADMIN_ACTIONS = ('inspect-package', 'install', 'enable', 'disable', 'remove', 'token')


def is_path_argument(name):
    """Naming rule used to audit that every path-like argument is scoped."""
    return name in PATH_KEYS or name.endswith(('_path', '_dir', '_root', '_file'))


def check_provider_admin(command, arguments):
    if not isinstance(arguments, dict):
        raise ByaxError('invalid_input', 'Arguments must be an object')
    if command in ('apply', 'transact', 'review'):
        _check_source_claims(arguments.get('input', {}))
    if command == 'library' and arguments.get('action', 'list') not in (
        'list',
        'status',
        'search',
        'read',
        'refresh',
        'verify-absence',
    ):
        raise ByaxError(
            'unsupported_feature',
            'Manage library attachments and configuration through the local CLI or desktop',
        )
    if command == 'codex' and arguments.get('include_hidden'):
        raise ByaxError(
            'unsupported_feature', 'Codex entries hidden from AI are shown only in the local CLI and desktop'
        )
    if command == 'codex' and arguments.get('action') in ('series-attach', 'series-detach'):
        raise ByaxError(
            'unsupported_feature', 'Attach a series (and its libraries) through the local CLI or desktop'
        )
    if command == 'dependencies' and arguments.get('action') == 'refresh':
        raise ByaxError(
            'unsupported_feature',
            'Retrieve document-linked files through the local CLI or desktop; transport path scope does not authorize embedded URIs',
        )
    if command == 'version' and arguments.get('check_updates'):
        raise ByaxError(
            'unsupported_feature',
            'Check for updates through the local CLI or desktop; automation does not reach out',
        )
    if command == 'sample' and not arguments.get('out'):
        raise ByaxError('invalid_input', 'Name the output path for the sample project copy', field='out')
    if command == 'models' and arguments.get('action', 'list') not in ('list', 'status', 'verify'):
        raise ByaxError(
            'unsupported_feature',
            'Download, remove and configure on-device models through the local CLI or desktop',
        )
    if command == 'providers' and arguments.get('action') == 'configure':
        raise ByaxError('unsupported_feature', 'Configure provider aliases through the local CLI or desktop')
    if command == 'grammar' and arguments.get('action', 'status') not in ('status', 'rules', 'check'):
        raise ByaxError(
            'unsupported_feature',
            'Configure, install, start or stop LanguageTool through the local CLI or desktop',
        )
    if command == 'languages' and arguments.get('action', 'list') not in ('list', 'verify', 'coverage'):
        raise ByaxError(
            'unsupported_feature', 'Install or remove language packs through the local CLI or desktop'
        )
    if command == 'goals' and arguments.get('action') in ('set-daily', 'set-session'):
        raise ByaxError(
            'unsupported_feature',
            'Writing goals are local preferences of the person at this computer; set them through the local CLI or desktop',
        )
    if command == 'extensions' and arguments.get('action', 'list') in EXTENSION_ADMIN_ACTIONS:
        raise ByaxError(
            'unsupported_feature',
            'Install, inspect packages, enable, disable, remove or issue tokens for extensions through the local CLI or desktop',
        )
    if command == 'macro':
        if arguments.get('allow_admin'):
            raise ByaxError(
                'unsupported_feature', 'Macro steps that transports refuse run only from the local CLI'
            )
        if arguments.get('action') in ('save', 'remove') and arguments.get('store', 'user') != 'document':
            raise ByaxError(
                'unsupported_feature', 'Saved user macros are managed through the local CLI or desktop'
            )
    if command == 'jobs' and arguments.get('action') == 'submit':
        spec = arguments.get('input', {})
        if not isinstance(spec, dict):
            raise ByaxError('invalid_input', 'Job input must be an object')
        check_provider_admin(spec.get('command'), spec.get('arguments', {}))


def _check_source_claims(value):
    if isinstance(value, dict):
        if (
            value.get('type', value.get('op')) == 'set_settings'
            and isinstance(value.get('values'), dict)
            and ('libraries' in value['values'] or 'series' in value['values'])
        ):
            raise ByaxError(
                'unsupported_feature',
                'Manage library and series attachments through the local CLI or desktop',
            )
        if value.get('type', value.get('op')) == 'add_reference':
            source = value.get('values', {})
            if isinstance(source, dict) and source.get('verification') in ('manual', 'verified'):
                raise ByaxError(
                    'invalid_input', 'Transport clients cannot assert manual or verified source status'
                )
        for child in value.values():
            _check_source_claims(child)
    elif isinstance(value, list):
        for child in value:
            _check_source_claims(child)


# Spec 16.2 command families mapped to every interface. CLI commands are also MCP
# tools (byax_<name>) and REST commands (POST /v1/execute), all dispatched through
# cli.execute; library entries are importable callables; GUI entries name the menu
# action, or None when the operation is headless-only. Kept in sync with
# docs/interface-parity.md by tests/test_interface_parity.py.
INTERFACE_INVENTORY = (
    ('Inspection', 'attached libraries', 'library', 'byax.cli_libraries.library_command', None),
    ('Search and targeting', 'library search', 'library-search', 'byax.cli_libraries.library_search', None),
    ('Inspection', 'library passage', 'library-read', 'byax.cli_libraries.library_read', None),
    (
        'Search and targeting',
        'verified absence',
        'library --action verify-absence',
        'byax.retrieval.verify_absence',
        'AI tab (assistant replies)',
    ),
    (
        'Discovery',
        'on-device model catalogue',
        'models',
        'byax.local_models.models_command',
        'Tools > On-device search…',
    ),
    (
        'Providers',
        'download or remove on-device models',
        'models --action download',
        'byax.local_models.download_model',
        'Tools > On-device search…',
    ),
    (
        'Search and targeting',
        'enable on-device semantic search',
        'library --action semantic-enable',
        'byax.cli_libraries.library_command',
        'Library panel (Enable on-device semantic search)',
    ),
    ('Review', 'citation sources', 'source', 'byax.cli_libraries.source_command', None),
    ('Editing', 'insert citation', 'cite', 'byax.cli_libraries.cite_command', None),
    (
        'Inspection',
        'document statistics',
        'statistics',
        'byax.statistics.document_statistics',
        'Tools > Document statistics',
    ),
    (
        'Inspection',
        'linked dependencies',
        'dependencies',
        'byax.engine.dependency_report',
        'Tools > Linked assets…',
    ),
    (
        'Files',
        'settings collections',
        'collection',
        'byax.profiles.export_settings',
        'Tools > Reusable settings…',
    ),
    ('Inspection', 'file view', 'view', 'byax.formats.project', 'View > File view'),
    ('Discovery', 'version', 'version', 'byax.__version__', 'Help > About ByAuthorX'),
    (
        'Discovery',
        'update check',
        'version --check-updates',
        'byax.updates.check_for_updates',
        'Help > Check for updates…',
    ),
    ('Discovery', 'sample project', 'sample', 'byax.samples.install_sample', 'Help > Open sample project'),
    ('Discovery', 'first-run tour', None, 'byax.gui_onboarding.TOUR_STEPS', 'Help > Take the tour…'),
    ('Discovery', 'capabilities', 'capabilities', 'byax.providers.list_capabilities', None),
    ('Discovery', 'command schema', 'schema', 'byax.cli.command_schema', None),
    ('Discovery', 'operation schema', 'schema --topic operations', 'byax.engine.operation_schema', None),
    ('Discovery', 'supported formats', 'capabilities', 'byax.exchange.EXPORT_FORMATS', 'File > Export…'),
    (
        'Discovery',
        'provider capabilities',
        'providers --action list',
        'byax.providers.ProviderRegistry.list',
        'Tools > Provider settings…',
    ),
    ('Files', 'new', 'new', 'byax.model.new_document', 'File > New'),
    ('Files', 'open or inspect', 'inspect', 'byax.storage.load_document', 'File > Open…'),
    ('Files', 'import', 'import', 'byax.exchange.import_document', 'File > Open…'),
    ('Files', 'save as', 'save-as', 'byax.storage.save_document', 'File > Save as…'),
    ('Files', 'convert', 'convert', 'byax.exchange.export_document', 'File > Export…'),
    ('Files', 'conversion preview', 'convert --dry-run', 'byax.exchange.conversion_preview', None),
    ('Files', 'pack', 'pack', 'byax.storage.pack', None),
    ('Files', 'unpack', 'unpack', 'byax.storage.unpack', None),
    (
        'Files',
        'synced-folder status',
        'sync --action status',
        'byax.sync_safety.sync_status',
        'Tools > Synced folder safety…',
    ),
    (
        'Files',
        'conflicted copies',
        'sync --action conflicts',
        'byax.sync_safety.find_conflicted_copies',
        'Tools > Review conflicted copies…',
    ),
    (
        'Review',
        'merge conflicted copy',
        'sync --action merge-conflict',
        'byax.sync_safety.propose_conflicted_copy_merge',
        'Tools > Review conflicted copies…',
    ),
    (
        'Review',
        'send for review (Word)',
        'review-package',
        'byax.docx_review.create_review_package',
        'File > Send for review (Word)…',
    ),
    (
        'Review',
        'list review packages',
        'review-package --action list',
        'byax.docx_review.review_packages',
        None,
    ),
    (
        'Review',
        'merge reviewed Word copy',
        'merge-review',
        'byax.docx_review.merge_reviewed_copy',
        'File > Merge reviewed copy…',
    ),
    (
        'Files',
        'archive merged conflicted copy',
        'sync --action archive-conflict',
        'byax.sync_safety.archive_conflicted_copy',
        'Tools > Review conflicted copies…',
    ),
    ('Inspection', 'outline', 'inspect --outline', 'byax.engine.inspect', 'View > Outline'),
    ('Inspection', 'selected content', 'inspect --target', 'byax.engine.inspect', 'View > Inspector'),
    (
        'Inspection',
        'styles',
        'inspect --section styles',
        'byax.engine.inspect',
        'Format > Define named style…',
    ),
    ('Inspection', 'tables', 'inspect --target', 'byax.engine.inspect', 'Table > Table properties…'),
    (
        'Inspection',
        'layers',
        'inspect --section layers',
        'byax.engine.inspect',
        'Insert > Watermark / layer…',
    ),
    ('Inspection', 'assets', 'inspect --section assets', 'byax.engine.inspect', None),
    ('Inspection', 'fonts', 'fonts --action inspect', 'byax.fonts.inspect_font', None),
    (
        'Inspection',
        'references',
        'inspect --section references',
        'byax.engine.inspect',
        'Insert > Source reference…',
    ),
    (
        'Inspection',
        'metadata',
        'inspect --section metadata',
        'byax.engine.inspect',
        'Tools > Document properties…',
    ),
    ('Inspection', 'statistics', None, None, 'Tools > Document statistics'),
    (
        'Inspection',
        'readability report',
        'readability',
        'byax.readability.readability_report',
        'Tools > Readability report…',
    ),
    (
        'Inspection',
        'chapter pacing series',
        'readability',
        'byax.fiction_checks.pacing_series',
        'Tools > Readability report…',
    ),
    ('Search and targeting', 'find text', 'find', 'byax.engine.find', 'Edit > Find and replace…'),
    (
        'Search and targeting',
        'resolve bookmarks and IDs',
        'inspect --section settings',
        'byax.model.find_object',
        'Insert > Cross reference…',
    ),
    ('Search and targeting', 'scope by section or object', 'context --target', 'byax.engine.context', None),
    ('Editing', 'apply canonical operations', 'apply', 'byax.engine.apply', 'Edit / Format / Insert menus'),
    ('Editing', 'dry run', 'apply --dry-run', 'byax.engine.apply', None),
    ('Editing', 'transact', 'transact', 'byax.engine.apply', None),
    ('Editing', 'fill fields', 'fill', 'byax.engine.apply', 'Insert > Template fields…'),
    (
        'Editing',
        'structured data',
        'table',
        'byax.table_io.import_csv',
        'Table > Update selected table from CSV…',
    ),
    ('Review', 'propose', 'review --action propose', 'byax.engine.apply', 'Review > Review with a model…'),
    ('Review', 'list proposals', 'review --action list', 'byax.engine.inspect', 'Review panel'),
    ('Review', 'accept or reject', 'review --action accept', 'byax.engine.apply', 'Review panel'),
    ('Review', 'comments', 'review --action comment', 'byax.engine.apply', 'Review > Add comment…'),
    (
        'Review',
        'tracked changes',
        'review --action accept-change',
        'byax.engine.apply',
        'Review > Track changes',
    ),
    ('History', 'list', 'history', 'byax.engine.inspect', 'Review > Revision history…'),
    (
        'History',
        'checkpoint',
        'history --action checkpoint',
        'byax.engine.checkpoint',
        'Review > Checkpoint…',
    ),
    ('History', 'branch', 'history --action branch', 'byax.engine.branch', 'Review > Start alternate draft…'),
    ('History', 'restore', 'history --action restore', 'byax.engine.restore', 'Review > Revision history…'),
    ('History', 'diff and changes since revision', 'diff', 'byax.engine.diff', 'Review > Revision history…'),
    ('Checks', 'run suites', 'check', 'byax.checks.run_checks', 'Review > Run document checks'),
    (
        'Checks',
        'fiction analysis families',
        'check --family fiction',
        'byax.fiction_checks.RULES',
        'Inspector > Checks',
    ),
    ('Checks', 'inspect findings', 'inspect --section findings', 'byax.engine.inspect', 'Review panel'),
    # Context engine plan 6.4/6.5: the writer's own citations, stage 1, local and deterministic.
    ('Checks', 'citation audit', 'check --citations', 'byax.grounding.audit_citations', 'Inspector > Checks'),
    ('Editing', 're-anchor citation', 'apply', 'byax.engine.apply', 'Inspector > Checks'),
    (
        'Checks',
        'propose repairs',
        'review --action propose',
        'byax.engine.apply',
        'Review > Review with a model…',
    ),
    (
        'Checks',
        'evaluate preservation rules',
        'apply --dry-run',
        'byax.engine.apply',
        'Review > Protect selected paragraph',
    ),
    ('Output', 'render pages', 'render', 'byax.layout.render', 'File > Print preview…'),
    ('Output', 'proof output', 'proof', 'byax.proof.create_proof', None),
    ('Output', 'print', None, None, 'File > Print…'),
    (
        'Output',
        'export using profiles',
        'export --profile',
        'byax.exchange.export_document',
        'File > Export…',
    ),
    (
        'Output',
        'clean publication',
        'export --profile clean',
        'byax.publication.export_clean',
        'File > Export…',
    ),
    (
        'Output',
        'sanitized copy',
        'redact',
        'byax.publication.save_sanitized',
        'File > Redact a sanitized copy…',
    ),
    ('Output', 'reproduction record', 'reproduction', 'byax.publication.reproduction_record', None),
    (
        'Providers',
        'configure aliases',
        'providers --action configure',
        'byax.providers.ProviderRegistry.save',
        'Tools > Provider settings…',
    ),
    (
        'Providers',
        'test capabilities',
        'providers --action test',
        'byax.providers.ProviderRegistry.test',
        'Tools > Provider settings…',
    ),
    (
        'Providers',
        'inspect models without secrets',
        'providers --action preview',
        'byax.providers.ProviderRegistry.preview',
        'Tools > Provider settings…',
    ),
    (
        'Transcription',
        'submit audio and mode',
        'transcribe',
        'byax.transcription.DictationSession',
        'Tools > Import audio…',
    ),
    (
        'Transcription',
        'inspect transcript',
        'transcribe',
        'byax.transcription.DictationSession',
        'Tools > Dictation…',
    ),
    (
        'Transcription',
        'insert approved text',
        'transcribe --insert',
        'byax.transcription.DictationSession',
        'Tools > Dictation…',
    ),
    (
        'Jobs and batches',
        'submit',
        'jobs --action submit',
        'byax.jobs.JobManager.submit',
        'Tools > Background jobs…',
    ),
    (
        'Jobs and batches',
        'status',
        'jobs --action status',
        'byax.jobs.JobManager.status',
        'Tools > Background jobs…',
    ),
    (
        'Jobs and batches',
        'results',
        'jobs --action result',
        'byax.jobs.JobManager.result',
        'Tools > Background jobs…',
    ),
    (
        'Jobs and batches',
        'cancel',
        'jobs --action cancel',
        'byax.jobs.JobManager.cancel',
        'Tools > Background jobs…',
    ),
    (
        'Jobs and batches',
        'recover',
        'jobs --action recover',
        'byax.jobs.JobManager.recover',
        'Tools > Background jobs…',
    ),
    ('Jobs and batches', 'per-file batch outcomes', 'batch', 'byax.jobs.run_batch', None),
    ('Jobs and batches', 'template batch', 'template-batch', 'byax.table_io.template_rows', None),
    # Manuscript project binder (CP A1) with its outline and corkboard views (A2).
    ('Inspection', 'project binder', 'project --action list', 'byax.project.items', 'Binder panel (outline)'),
    ('Inspection', 'project tree', 'project --action tree', 'byax.project.tree', 'Binder panel (corkboard)'),
    ('Inspection', 'project consistency', 'project --action reconcile', 'byax.project.reconcile', None),
    ('Inspection', 'project block map', 'project --action blocks', 'byax.project.block_map', None),
    ('Output', 'project compile inclusion', 'project --action compile', 'byax.project.compile_plan', None),
    (
        'Editing',
        'create binder',
        'project --action init',
        'byax.engine.apply',
        'Binder panel (Create binder)',
    ),
    ('Editing', 'add binder item', 'project --action add', 'byax.engine.apply', 'Binder panel (Add after)'),
    ('Editing', 'remove binder item', 'project --action remove', 'byax.engine.apply', None),
    (
        'Editing',
        'move binder item',
        'project --action move',
        'byax.engine.apply',
        'Binder panel (drag, Alt+Up/Down/Left/Right)',
    ),
    (
        'Editing',
        'split binder item',
        'project --action split',
        'byax.engine.apply',
        'Binder panel (Split at cursor)',
    ),
    (
        'Editing',
        'merge binder items',
        'project --action merge',
        'byax.engine.apply',
        'Binder panel (Merge with next)',
    ),
    ('Editing', 'binder status', 'project --action set-status', 'byax.engine.apply', 'Binder panel (Status)'),
    (
        'Editing',
        'binder word target',
        'project --action set-target',
        'byax.engine.apply',
        'Tools > Writing goals…',
    ),
    (
        'Editing',
        'binder metadata',
        'project --action set-meta',
        'byax.engine.apply',
        'Binder panel (synopsis, kind)',
    ),
    (
        'Editing',
        'draft a synopsis (opt-in)',
        'project --action draft-synopsis',
        'byax.project.draft_synopsis',
        'Binder panel (Draft a synopsis…)',
    ),
    # Writing goals and sessions (A3): progress is read-only everywhere, goals are local preferences.
    (
        'Inspection',
        'writing goals and progress',
        'goals --action status',
        'byax.goals.status',
        'Tools > Writing goals…',
    ),
    (
        'Inspection',
        'writing history and streaks',
        'goals --action history',
        'byax.goals.history',
        'Tools > Writing goals…',
    ),
    (
        'Editing',
        'daily goal',
        'goals --action set-daily',
        'byax.goals.GoalStore.set_goal',
        'Tools > Writing goals…',
    ),
    (
        'Editing',
        'session goal',
        'goals --action set-session',
        'byax.goals.GoalStore.set_goal',
        'Tools > Writing goals…',
    ),
    # Named drafts (A4): branches and delta history under the words draft and compare.
    ('History', 'list drafts', 'drafts --action list', 'byax.drafts.list_drafts', 'Review > Drafts…'),
    ('History', 'save draft', 'drafts --action save', 'byax.drafts.save_draft', 'Review > Save draft as…'),
    (
        'History',
        'switch draft',
        'drafts --action switch',
        'byax.drafts.switch_draft',
        'Review > Switch draft…',
    ),
    (
        'History',
        'compare drafts',
        'drafts --action compare',
        'byax.drafts.compare_drafts',
        'Review > Compare drafts…',
    ),
    (
        'History',
        'bring changes from draft',
        'drafts --action bring',
        'byax.drafts.bring_operation',
        'Review > Bring changes from draft…',
    ),
    # Grammar tiers (CP D1) and language packs (CP D4). Administrative actions are refused over MCP and REST.
    (
        'Checks',
        'grammar tiers status',
        'grammar --action status',
        'byax.grammar.languagetool_status',
        'Inspector > Checks',
    ),
    (
        'Checks',
        'grammar rule catalogue',
        'grammar --action rules',
        'byax.grammar.rule_set',
        'Inspector > Checks',
    ),
    (
        'Checks',
        'check text with the grammar tiers',
        'grammar --action check',
        'byax.grammar.check_text',
        None,
    ),
    (
        'Checks',
        'configure the LanguageTool tier',
        'grammar --action configure',
        'byax.grammar.save_config',
        'Inspector > Checks',
    ),
    (
        'Checks',
        'start or stop the local LanguageTool server',
        'grammar --action start',
        'byax.grammar.start_languagetool',
        'Inspector > Checks',
    ),
    (
        'Checks',
        'install LanguageTool (explicit download)',
        'grammar --action install-languagetool',
        'byax.grammar.install_languagetool',
        'Tools > Languages…',
    ),
    (
        'Discovery',
        'language packs',
        'languages --action list',
        'byax.language_packs.list_packs',
        'Tools > Languages…',
    ),
    (
        'Inspection',
        'verify language packs',
        'languages --action verify',
        'byax.language_packs.verify_packs',
        'Tools > Languages…',
    ),
    (
        'Inspection',
        'translation coverage',
        'languages --action coverage',
        'byax.language_packs.coverage_report',
        'Tools > Languages…',
    ),
    (
        'Files',
        'install language pack',
        'languages --action install',
        'byax.language_packs.install_pack',
        'Tools > Languages…',
    ),
    (
        'Files',
        'remove language pack',
        'languages --action remove',
        'byax.language_packs.remove_pack',
        'Tools > Languages…',
    ),
    # Compile pipeline (CP B2).
    ('Output', 'compile a book', 'compile', 'byax.compile.compile_document', 'File > Compile…'),
    ('Checks', 'compile preflight', 'compile --preflight-only', 'byax.compile.preflight', 'File > Compile…'),
    (
        'Discovery',
        'compile presets',
        'compile --action presets',
        'byax.compile.list_presets',
        'File > Compile…',
    ),
    (
        'Inspection',
        'effective compile profile',
        'compile --action show',
        'byax.compile.resolve_profile',
        None,
    ),
    (
        'Editing',
        'save compile preset',
        'compile --action save-preset',
        'byax.compile.save_preset_operations',
        'File > Compile…',
    ),
    (
        'Files',
        'export compile preset',
        'compile --action export-preset',
        'byax.compile.preset_collection',
        None,
    ),
    # Manuscript corpus and codex (context engine 8.1, 8.2).
    (
        'Search and targeting',
        'project find',
        'project-search --action find',
        'byax.project_index.ProjectIndex.search',
        'Edit > Find in project…',
    ),
    (
        'Inspection',
        'project index status',
        'project-search --action status',
        'byax.project_index.ProjectIndex.status',
        None,
    ),
    (
        'Search and targeting',
        'project index refresh',
        'project-search --action refresh',
        'byax.project_index.ProjectIndex.refresh',
        'Edit > Find in project…',
    ),
    (
        'Search and targeting',
        'saved searches',
        'project-search --action saved-list',
        'byax.project_index.saved_searches',
        'Edit > Find in project…',
    ),
    (
        'Search and targeting',
        'run saved search',
        'project-search --action saved-run',
        'byax.project_index.run_saved',
        'Edit > Find in project…',
    ),
    (
        'Editing',
        'save search',
        'project-search --action saved-add',
        'byax.engine.apply',
        'Edit > Find in project…',
    ),
    (
        'Editing',
        'remove saved search',
        'project-search --action saved-remove',
        'byax.engine.apply',
        'Edit > Find in project…',
    ),
    (
        'Search and targeting',
        'backlinks',
        'project-search --action backlinks',
        'byax.project_index.links',
        'Edit > Find in project…',
    ),
    (
        'Search and targeting',
        'unlinked mentions',
        'project-search --action unlinked-mentions',
        'byax.project_index.links',
        'Edit > Find in project…',
    ),
    ('Inspection', 'codex entries', 'codex --action list', 'byax.codex.list_entries', 'Tools > Codex…'),
    ('Inspection', 'codex lookup', 'codex --action lookup', 'byax.codex.lookup', 'Tools > Codex…'),
    ('Inspection', 'codex mentions', 'codex --action mentions', 'byax.codex.mentions', 'Tools > Codex…'),
    ('Inspection', 'codex mention heatmap', 'codex --action heatmap', 'byax.codex.heatmap', 'Tools > Codex…'),
    ('Inspection', 'codex series', 'codex --action series', 'byax.series.resolve', 'Tools > Codex…'),
    ('Editing', 'add codex entry', 'codex --action add', 'byax.engine.apply', 'Tools > Codex…'),
    ('Editing', 'edit codex entry', 'codex --action update', 'byax.engine.apply', 'Tools > Codex…'),
    ('Editing', 'remove codex entry', 'codex --action remove', 'byax.engine.apply', 'Tools > Codex…'),
    ('Editing', 'add codex trait', 'codex --action add-trait', 'byax.engine.apply', 'Tools > Codex…'),
    ('Editing', 'edit codex trait', 'codex --action update-trait', 'byax.engine.apply', 'Tools > Codex…'),
    ('Editing', 'remove codex trait', 'codex --action remove-trait', 'byax.engine.apply', 'Tools > Codex…'),
    ('Review', 'propose codex fact', 'codex --action propose-fact', 'byax.codex.propose_fact', None),
    ('Files', 'attach series', 'codex --action series-attach', 'byax.series.reference_for', None),
    ('Files', 'detach series', 'codex --action series-detach', 'byax.engine.apply', None),
    ('Files', 'export series codex', 'codex --action series-export', 'byax.series.save', None),
    ('Inspection', 'entity lookup (read only)', 'entity', 'byax.cli_codex.entity_command', None),
    # Extension layer (CP 7.4): out-of-process checks and export hooks, and saved command macros.
    (
        'Discovery',
        'list extensions',
        'extensions --action list',
        'byax.extensions.summaries',
        'Tools > Extensions…',
    ),
    (
        'Inspection',
        'extension details',
        'extensions --action info',
        'byax.extensions.describe',
        'Tools > Extensions…',
    ),
    (
        'Inspection',
        'verify installed extensions',
        'extensions --action verify',
        'byax.extensions.verify',
        'Tools > Extensions…',
    ),
    (
        'Checks',
        'run extension checks',
        'extensions --action run-check',
        'byax.extensions.evaluate',
        'Tools > Extensions…',
    ),
    (
        'Output',
        'run an export hook',
        'extensions --action run-hook',
        'byax.extensions.run_hook',
        'Tools > Extensions…',
    ),
    (
        'Output',
        'export with hooks',
        'export --hooks',
        'byax.extensions.run_export_hooks',
        'Tools > Extensions…',
    ),
    (
        'Files',
        'install extension',
        'extensions --action install',
        'byax.extensions.install',
        'Tools > Extensions…',
    ),
    (
        'Files',
        'enable or disable extension',
        'extensions --action enable',
        'byax.extensions.set_enabled',
        'Tools > Extensions…',
    ),
    (
        'Files',
        'remove extension',
        'extensions --action remove',
        'byax.extensions.remove',
        'Tools > Extensions…',
    ),
    (
        'Files',
        'scoped extension token',
        'extensions --action token',
        'byax.service.issue_extension_token',
        None,
    ),
    ('Inspection', 'list macros', 'macro --action list', 'byax.macros.user_macros', 'Tools > Macros…'),
    ('Editing', 'run or dry-run a macro', 'macro --action run', 'byax.macros.run_macro', 'Tools > Macros…'),
    ('Files', 'save macro', 'macro --action save', 'byax.macros.validate_macro', 'Tools > Macros…'),
    ('Files', 'remove macro', 'macro --action remove', 'byax.macros.find', 'Tools > Macros…'),
)
