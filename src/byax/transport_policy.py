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
    if command == 'providers' and arguments.get('action') == 'configure':
        raise ByaxError('unsupported_feature', 'Configure provider aliases through the local CLI or desktop')
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
    ('Checks', 'inspect findings', 'inspect --section findings', 'byax.engine.inspect', 'Review panel'),
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
)
