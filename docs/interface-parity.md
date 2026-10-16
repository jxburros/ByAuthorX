# Interface parity (spec 2.3, 16.1–16.2, 16.9–16.10)

Every headless operation is one `byax.cli.execute(command, arguments)` call. The CLI parses
flags into that call, MCP `tools/call` invokes it for tool `byax_<command>`, and the optional
REST service invokes it for `POST /v1/execute` with `{"command", "arguments"}`. The desktop GUI
calls the same engine functions directly. Nothing below requires driving the GUI.

The machine-readable source of this table is `byax.transport_policy.INTERFACE_INVENTORY`;
`tests/test_interface_parity.py` fails when a CLI command, MCP tool, library callable or this
document drifts from it.

## Command families

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| Discovery | version | `byax version` | `byax_version` | `POST /v1/execute` | `byax.__version__` | Help > About ByAuthorX |
| Discovery | capabilities | `byax capabilities` | `byax_capabilities` | `POST /v1/execute` | `byax.providers.list_capabilities` | — (headless) |
| Discovery | command schema | `byax schema` | `byax_schema` | `POST /v1/execute` | `byax.cli.command_schema` | — (headless) |
| Discovery | operation schema | `byax schema --topic operations` | `byax_schema` | `POST /v1/execute` | `byax.engine.operation_schema` | — (headless) |
| Discovery | supported formats | `byax capabilities` | `byax_capabilities` | `POST /v1/execute` | `byax.exchange.EXPORT_FORMATS` | File > Export… |
| Discovery | provider capabilities | `byax providers --action list` | `byax_providers` | `POST /v1/execute` | `byax.providers.ProviderRegistry.list` | Tools > Provider settings… |
| Files | new | `byax new` | `byax_new` | `POST /v1/execute` | `byax.model.new_document` | File > New |
| Files | open or inspect | `byax inspect` | `byax_inspect` | `POST /v1/execute` | `byax.storage.load_document` | File > Open… |
| Files | import | `byax import` | `byax_import` | `POST /v1/execute` | `byax.exchange.import_document` | File > Open… |
| Files | save as | `byax save-as` | `byax_save_as` | `POST /v1/execute` | `byax.storage.save_document` | File > Save as… |
| Files | convert | `byax convert` | `byax_convert` | `POST /v1/execute` | `byax.exchange.export_document` | File > Export… |
| Files | conversion preview | `byax convert --dry-run` | `byax_convert` | `POST /v1/execute` | `byax.exchange.conversion_preview` | — (headless) |
| Files | pack | `byax pack` | `byax_pack` | `POST /v1/execute` | `byax.storage.pack` | — (headless) |
| Files | unpack | `byax unpack` | `byax_unpack` | `POST /v1/execute` | `byax.storage.unpack` | — (headless) |
| Inspection | outline | `byax inspect --outline` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | View > Outline |
| Inspection | selected content | `byax inspect --target` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | View > Inspector |
| Inspection | styles | `byax inspect --section styles` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | Format > Define named style… |
| Inspection | tables | `byax inspect --target` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | Table > Table properties… |
| Inspection | layers | `byax inspect --section layers` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | Insert > Watermark / layer… |
| Inspection | assets | `byax inspect --section assets` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | — (headless) |
| Inspection | fonts | `byax fonts --action inspect` | `byax_fonts` | `POST /v1/execute` | `byax.fonts.inspect_font` | — (headless) |
| Inspection | references | `byax inspect --section references` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | Insert > Source reference… |
| Inspection | metadata | `byax inspect --section metadata` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | Tools > Document properties… |
| Inspection | statistics | — (GUI only) | — | — | — | Tools > Document statistics |
| Search and targeting | find text | `byax find` | `byax_find` | `POST /v1/execute` | `byax.engine.find` | Edit > Find and replace… |
| Search and targeting | resolve bookmarks and IDs | `byax inspect --section settings` | `byax_inspect` | `POST /v1/execute` | `byax.model.find_object` | Insert > Cross reference… |
| Search and targeting | scope by section or object | `byax context --target` | `byax_context` | `POST /v1/execute` | `byax.engine.context` | — (headless) |
| Editing | apply canonical operations | `byax apply` | `byax_apply` | `POST /v1/execute` | `byax.engine.apply` | Edit / Format / Insert menus |
| Editing | dry run | `byax apply --dry-run` | `byax_apply` | `POST /v1/execute` | `byax.engine.apply` | — (headless) |
| Editing | transact | `byax transact` | `byax_transact` | `POST /v1/execute` | `byax.engine.apply` | — (headless) |
| Editing | fill fields | `byax fill` | `byax_fill` | `POST /v1/execute` | `byax.engine.apply` | Insert > Template fields… |
| Editing | structured data | `byax table` | `byax_table` | `POST /v1/execute` | `byax.table_io.import_csv` | Table > Update selected table from CSV… |
| Review | propose | `byax review --action propose` | `byax_review` | `POST /v1/execute` | `byax.engine.apply` | Review > Review with a model… |
| Review | list proposals | `byax review --action list` | `byax_review` | `POST /v1/execute` | `byax.engine.inspect` | Review panel |
| Review | accept or reject | `byax review --action accept` | `byax_review` | `POST /v1/execute` | `byax.engine.apply` | Review panel |
| Review | comments | `byax review --action comment` | `byax_review` | `POST /v1/execute` | `byax.engine.apply` | Review > Add comment… |
| Review | tracked changes | `byax review --action accept-change` | `byax_review` | `POST /v1/execute` | `byax.engine.apply` | Review > Track changes |
| History | list | `byax history` | `byax_history` | `POST /v1/execute` | `byax.engine.inspect` | Review > Revision history… |
| History | checkpoint | `byax history --action checkpoint` | `byax_history` | `POST /v1/execute` | `byax.engine.checkpoint` | Review > Checkpoint… |
| History | branch | `byax history --action branch` | `byax_history` | `POST /v1/execute` | `byax.engine.branch` | Review > Create branch… |
| History | restore | `byax history --action restore` | `byax_history` | `POST /v1/execute` | `byax.engine.restore` | Review > Revision history… |
| History | diff and changes since revision | `byax diff` | `byax_diff` | `POST /v1/execute` | `byax.engine.diff` | Review > Revision history… |
| Checks | run suites | `byax check` | `byax_check` | `POST /v1/execute` | `byax.checks.run_checks` | Review > Run document checks |
| Checks | inspect findings | `byax inspect --section findings` | `byax_inspect` | `POST /v1/execute` | `byax.engine.inspect` | Review panel |
| Checks | propose repairs | `byax review --action propose` | `byax_review` | `POST /v1/execute` | `byax.engine.apply` | Review > Review with a model… |
| Checks | evaluate preservation rules | `byax apply --dry-run` | `byax_apply` | `POST /v1/execute` | `byax.engine.apply` | Review > Protect selected paragraph |
| Output | render pages | `byax render` | `byax_render` | `POST /v1/execute` | `byax.layout.render` | File > Print preview… |
| Output | proof output | `byax proof` | `byax_proof` | `POST /v1/execute` | `byax.proof.create_proof` | — (headless) |
| Output | print | — (GUI only) | — | — | — | File > Print… |
| Output | export using profiles | `byax export --profile` | `byax_export` | `POST /v1/execute` | `byax.exchange.export_document` | File > Export… |
| Output | clean publication | `byax export --profile clean` | `byax_export` | `POST /v1/execute` | `byax.publication.export_clean` | File > Export… |
| Output | sanitized copy | `byax redact` | `byax_redact` | `POST /v1/execute` | `byax.publication.save_sanitized` | File > Redact a sanitized copy… |
| Output | reproduction record | `byax reproduction` | `byax_reproduction` | `POST /v1/execute` | `byax.publication.reproduction_record` | — (headless) |
| Providers | configure aliases | `byax providers --action configure` | `byax_providers` | `POST /v1/execute` | `byax.providers.ProviderRegistry.save` | Tools > Provider settings… |
| Providers | test capabilities | `byax providers --action test` | `byax_providers` | `POST /v1/execute` | `byax.providers.ProviderRegistry.test` | Tools > Provider settings… |
| Providers | inspect models without secrets | `byax providers --action preview` | `byax_providers` | `POST /v1/execute` | `byax.providers.ProviderRegistry.preview` | Tools > Provider settings… |
| Transcription | submit audio and mode | `byax transcribe` | `byax_transcribe` | `POST /v1/execute` | `byax.transcription.DictationSession` | Tools > Import audio… |
| Transcription | inspect transcript | `byax transcribe` | `byax_transcribe` | `POST /v1/execute` | `byax.transcription.DictationSession` | Tools > Dictation… |
| Transcription | insert approved text | `byax transcribe --insert` | `byax_transcribe` | `POST /v1/execute` | `byax.transcription.DictationSession` | Tools > Dictation… |
| Jobs and batches | submit | `byax jobs --action submit` | `byax_jobs` | `POST /v1/execute` | `byax.jobs.JobManager.submit` | Tools > Background jobs… |
| Jobs and batches | status | `byax jobs --action status` | `byax_jobs` | `POST /v1/execute` | `byax.jobs.JobManager.status` | Tools > Background jobs… |
| Jobs and batches | results | `byax jobs --action result` | `byax_jobs` | `POST /v1/execute` | `byax.jobs.JobManager.result` | Tools > Background jobs… |
| Jobs and batches | cancel | `byax jobs --action cancel` | `byax_jobs` | `POST /v1/execute` | `byax.jobs.JobManager.cancel` | Tools > Background jobs… |
| Jobs and batches | recover | `byax jobs --action recover` | `byax_jobs` | `POST /v1/execute` | `byax.jobs.JobManager.recover` | Tools > Background jobs… |
| Jobs and batches | per-file batch outcomes | `byax batch` | `byax_batch` | `POST /v1/execute` | `byax.jobs.run_batch` | — (headless) |
| Jobs and batches | template batch | `byax template-batch` | `byax_template_batch` | `POST /v1/execute` | `byax.table_io.template_rows` | — (headless) |

Known gaps (recorded rather than hidden):

- **Document statistics** are GUI-only (`Tools > Document statistics`); there is no headless
  statistics command yet.
- **Printing** requires the native print dialog; headless callers use `render` or PDF `export`.
- **Propose repairs** has no dedicated repair generator; findings are repaired through
  `review --action propose` / `apply` with explicit operations.
- **Provider configuration** is refused over MCP and REST by design (`unsupported_feature`);
  configure aliases through the local CLI or desktop.

## One envelope everywhere

Results carry `schema_version`, `execution` (completed, failed, cancelled, pending/uncertain),
`validation` (passed, failed, incomplete, not_run), `review` (required, not_required),
`document_id`, `base_revision`, `revision`, `changed_ids`, `findings`, `coverage` and `outputs`.
Errors are the same envelope with `execution: failed` and `error: {code, message, exit_code, …}`
carrying the field/operation location, target, retry guidance and candidates where available.

| Transport | Success | Failure | Exit state |
| --- | --- | --- | --- |
| Library | returns the envelope | raises `ByaxError(code, message, **details)` | `ByaxError.exit_code` |
| CLI | envelope on stdout, exit code | error envelope on stdout, exit code; diagnostics only on stderr | process exit code |
| MCP | `content[0].text` = envelope, `structuredContent` = envelope + `exit_code` | same, `isError: true` | `structuredContent.exit_code` |
| REST | HTTP 200, envelope + `exit_code` (including completed-but-failed batches) | raised errors: 400 (exit 2), 409 (exit 3), 422 (exit 4/5), 500 (exit 1, 6, 7); 401/403/404/405 for transport rejections | `exit_code` field |

Exit codes: 0 completed under policy; 1 execution or dependency failure; 2 invalid input;
3 conflict, lock or ambiguous target; 4 failed required validation; 5 required review or
incomplete coverage under a strict policy; 6 cancelled; 7 pending or uncertain outcome.

## Interface-specific persistence

| Interface | Persistence behaviour |
| --- | --- |
| Library | Functions return new document dicts; nothing is written unless the caller saves (`storage.save_document`) or calls an export function. |
| CLI / MCP / REST | Each mutating call is one locked, atomic read–validate–write transaction on the named file; transactions never span calls. Outputs are staged and published atomically; `--overwrite` is required to replace an existing file. |
| REST | Paths are resolved (including symlinks and nested batch/job inputs) under the authorized roots; provider configuration and job state live in a service-owned `.byax/` folder in the first root that clients cannot address. |
| MCP | Runs with the invoking user's filesystem access (local stdio); no path scoping beyond the user's own permissions. |
| GUI | Edits accumulate in an editor session with autosave; saving writes the whole package atomically under the editor's lock, which blocks headless mutation of the same file. |
| Jobs | Job state is a JSON file per job under the job root (`--root`, or the platform state directory); results survive process restarts. |

## Spec 16.10 workflow

`tests/test_interface_parity.py::test_spec_16_10_workflow_through_the_cli_process` runs the
illustrated `capabilities → inspect --outline → context → apply --dry-run → apply (request id,
required suite) → diff → render --pages affected → export --profile … --require-suite` sequence
through the real `python -m byax.cli` process, checking stdout JSON, stderr separation, stdin
JSON (`--input -`) and exit codes.

## Verification boundary

Automated tests run on Linux with POSIX shells. PowerShell pipelines, installed-package
launchers and Windows console encoding have not been exercised and remain physical
verification items.

| Inspection | file view | `view --as FORMAT [--source]` | `byax_view` | `POST /v1/execute` | `byax.formats.project` | View > File view |

| Inspection | document statistics | `statistics` | `byax_statistics` | execute | `byax.statistics.document_statistics` | Tools > Document statistics |
| Inspection | linked dependencies | `dependencies` | `byax_dependencies` | execute | `byax.engine.dependency_report` | Tools > Linked assets… |
| Files | settings collections | `collection` | `byax_collection` | execute | `byax.profiles.export_settings` | Tools > Reusable settings… |

Linked asset retrieval requires explicit authorization in the local CLI or desktop. MCP/REST can inspect or embed already retrieved content; they cannot retrieve embedded file/HTTP URIs, which are outside transport path authorization.

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| Inspection | attached libraries | `byax library` | `byax_library` | `POST /v1/execute` | `byax.cli_libraries.library_command` | Library / Cite |
| Search and targeting | library search | `byax library-search` | `byax_library_search` | `POST /v1/execute` | `byax.cli_libraries.library_search` | Library / Cite |
| Inspection | library passage | `byax library-read` | `byax_library_read` | `POST /v1/execute` | `byax.cli_libraries.library_read` | Library / Cite |
| Search and targeting | verified absence | `byax library --action verify-absence` | `byax_library` | `POST /v1/execute` | `byax.retrieval.verify_absence` | AI tab (assistant replies) |
| Review | citation sources | `byax source` | `byax_source` | `POST /v1/execute` | `byax.cli_libraries.source_command` | Library / Cite |
| Editing | insert citation | `byax cite` | `byax_cite` | `POST /v1/execute` | `byax.cli_libraries.cite_command` | Library / Cite |
