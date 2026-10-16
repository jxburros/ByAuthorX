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
    )
)


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
            and 'libraries' in value['values']
        ):
            raise ByaxError(
                'unsupported_feature', 'Manage library attachments through the local CLI or desktop'
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
    ('History', 'branch', 'history --action branch', 'byax.engine.branch', 'Review > Create branch…'),
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
    # Manuscript project binder (CP A1); the corkboard/outline GUI is a later track (A2).
    ('Inspection', 'project binder', 'project --action list', 'byax.project.items', None),
    ('Inspection', 'project tree', 'project --action tree', 'byax.project.tree', None),
    ('Inspection', 'project consistency', 'project --action reconcile', 'byax.project.reconcile', None),
    ('Inspection', 'project block map', 'project --action blocks', 'byax.project.block_map', None),
    ('Output', 'project compile inclusion', 'project --action compile', 'byax.project.compile_plan', None),
    ('Editing', 'create binder', 'project --action init', 'byax.engine.apply', None),
    ('Editing', 'add binder item', 'project --action add', 'byax.engine.apply', None),
    ('Editing', 'remove binder item', 'project --action remove', 'byax.engine.apply', None),
    ('Editing', 'move binder item', 'project --action move', 'byax.engine.apply', None),
    ('Editing', 'split binder item', 'project --action split', 'byax.engine.apply', None),
    ('Editing', 'merge binder items', 'project --action merge', 'byax.engine.apply', None),
    ('Editing', 'binder status', 'project --action set-status', 'byax.engine.apply', None),
    ('Editing', 'binder word target', 'project --action set-target', 'byax.engine.apply', None),
    ('Editing', 'binder metadata', 'project --action set-meta', 'byax.engine.apply', None),
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
)
