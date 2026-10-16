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
| Discovery | update check | `byax version --check-updates` | `byax_version` (refused) | `POST /v1/execute` (refused) | `byax.updates.check_for_updates` | Help > Check for updates… |
| Discovery | sample project | `byax sample` | `byax_sample` | `POST /v1/execute` | `byax.samples.install_sample` | Help > Open sample project |
| Discovery | first-run tour | — (desktop) | — | — | `byax.gui_onboarding.TOUR_STEPS` | Help > Take the tour… |
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
| Files | synced-folder status | `byax sync --action status` | `byax_sync` | `POST /v1/execute` | `byax.sync_safety.sync_status` | Tools > Synced folder safety… |
| Files | conflicted copies | `byax sync --action conflicts` | `byax_sync` | `POST /v1/execute` | `byax.sync_safety.find_conflicted_copies` | Tools > Review conflicted copies… |
| Files | archive merged conflicted copy | `byax sync --action archive-conflict` | `byax_sync` | `POST /v1/execute` | `byax.sync_safety.archive_conflicted_copy` | Tools > Review conflicted copies… |
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
| Review | merge conflicted copy | `byax sync --action merge-conflict` | `byax_sync` | `POST /v1/execute` | `byax.sync_safety.propose_conflicted_copy_merge` | Tools > Review conflicted copies… |
| Review | send for review (Word) | `byax review-package` | `byax_review_package` | `POST /v1/execute` | `byax.docx_review.create_review_package` | File > Send for review (Word)… |
| Review | list review packages | `byax review-package --action list` | `byax_review_package` | `POST /v1/execute` | `byax.docx_review.review_packages` | — (headless) |
| Review | merge reviewed Word copy | `byax merge-review` | `byax_merge_review` | `POST /v1/execute` | `byax.docx_review.merge_reviewed_copy` | File > Merge reviewed copy… |
| History | list | `byax history` | `byax_history` | `POST /v1/execute` | `byax.engine.inspect` | Review > Revision history… |
| History | checkpoint | `byax history --action checkpoint` | `byax_history` | `POST /v1/execute` | `byax.engine.checkpoint` | Review > Checkpoint… |
| History | branch | `byax history --action branch` | `byax_history` | `POST /v1/execute` | `byax.engine.branch` | Review > Start alternate draft… |
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
- **Update checks** are refused over MCP and REST (`unsupported_feature`): the one network request a
  version query can make happens only from the local CLI or desktop, on explicit request.
  `sample` over MCP and REST requires `out` (a path inside the authorized roots).
- **First-run tour** is desktop-only onboarding with no headless equivalent.

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
| Inspection | readability report | `readability` | `byax_readability` | execute | `byax.readability.readability_report` | Tools > Readability report… |
| Inspection | chapter pacing series | `readability` | `byax_readability` | execute | `byax.fiction_checks.pacing_series` | Tools > Readability report… |
| Checks | fiction analysis families | `check --family fiction` | `byax_check` | execute | `byax.fiction_checks.RULES` | Inspector > Checks |
| Checks | citation audit | `check --citations` | `byax_check` (`citations: true`) | execute | `byax.grounding.audit_citations` | Inspector > Checks (Citation audit) |
| Editing | re-anchor citation | `apply` (`reanchor_citation`, proposed by the audit) | `byax_apply` | execute | `byax.engine.apply` | Inspector > Checks (Apply proposed anchor updates) |
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

## Manuscript projects (binder)

Binder actions are one `project` command. Mutating actions build one canonical `project_*`
operation and run it through `engine.apply`, so `--expect-revision`, `--request-id` and
`--dry-run` behave as for `apply`. The Binder panel (outline and corkboard views) sends the same
operations through the window's `commit`; reordering by drag has keyboard equivalents (Alt+Up/Down/Left/Right
and the Binder menu), and `byax.project.plan_step`/`plan_drop` build the operation lists for both.
`draft-synopsis` is an opt-in request to a provider you configured (`--alias`, with `--allow-remote` for a
remote one; `--dry-run` shows the exact payload without sending it). Its result is a pending proposal.

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| Inspection | project binder | `byax project --action list` | `byax_project` | `POST /v1/execute` | `byax.project.items` | Binder panel (outline) |
| Inspection | project tree | `byax project --action tree` | `byax_project` | `POST /v1/execute` | `byax.project.tree` | Binder panel (corkboard) |
| Inspection | project consistency | `byax project --action reconcile` | `byax_project` | `POST /v1/execute` | `byax.project.reconcile` | — (headless) |
| Inspection | project block map | `byax project --action blocks` | `byax_project` | `POST /v1/execute` | `byax.project.block_map` | — (headless) |
| Output | project compile inclusion | `byax project --action compile` | `byax_project` | `POST /v1/execute` | `byax.project.compile_plan` | — (headless) |
| Editing | create binder | `byax project --action init` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | Binder panel (Create binder) |
| Editing | add binder item | `byax project --action add` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | Binder panel (Add after) |
| Editing | remove binder item | `byax project --action remove` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | — (headless) |
| Editing | move binder item | `byax project --action move` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | Binder panel (drag, Alt+Up/Down/Left/Right) |
| Editing | split binder item | `byax project --action split` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | Binder panel (Split at cursor) |
| Editing | merge binder items | `byax project --action merge` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | Binder panel (Merge with next) |
| Editing | binder status | `byax project --action set-status` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | Binder panel (Status) |
| Editing | binder word target | `byax project --action set-target` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | Tools > Writing goals… |
| Editing | binder metadata | `byax project --action set-meta` | `byax_project` | `POST /v1/execute` | `byax.engine.apply` | Binder panel (synopsis, kind) |
| Editing | draft a synopsis (opt-in) | `byax project --action draft-synopsis` | `byax_project` | `POST /v1/execute` | `byax.project.draft_synopsis` | Binder panel (Draft a synopsis…) |

## Writing goals and sessions

`goals` reports progress against the document target and item targets (both stored in the binder and set
with `project --action set-target`), the daily goal, the session goal and the streak. The daily and
session goals and the session records are local preferences in the user config directory (keyed by the
document identity), never part of the document or its history. Reading is available everywhere; setting a
goal is refused over MCP and REST (`unsupported_feature`) because it writes the preferences of the person
at this computer, like provider configuration.

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| Inspection | writing goals and progress | `byax goals --action status` | `byax_goals` | `POST /v1/execute` | `byax.goals.status` | Tools > Writing goals… |
| Inspection | writing history and streaks | `byax goals --action history` | `byax_goals` | `POST /v1/execute` | `byax.goals.history` | Tools > Writing goals… |
| Editing | daily goal | `byax goals --action set-daily` | `byax_goals` | `POST /v1/execute` | `byax.goals.GoalStore.set_goal` | Tools > Writing goals… |
| Editing | session goal | `byax goals --action set-session` | `byax_goals` | `POST /v1/execute` | `byax.goals.GoalStore.set_goal` | Tools > Writing goals… |

## Named drafts

`drafts` is the writer-facing view of branches and delta history: save a draft with a note, switch,
compare two drafts scene by scene, and bring changes from a draft as a reviewable proposal
(`propose_merge`). The same operations are open to MCP and REST, because they change only the document.

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| History | list drafts | `byax drafts --action list` | `byax_drafts` | `POST /v1/execute` | `byax.drafts.list_drafts` | Review > Drafts… |
| History | save draft | `byax drafts --action save` | `byax_drafts` | `POST /v1/execute` | `byax.drafts.save_draft` | Review > Save draft as… |
| History | switch draft | `byax drafts --action switch` | `byax_drafts` | `POST /v1/execute` | `byax.drafts.switch_draft` | Review > Switch draft… |
| History | compare drafts | `byax drafts --action compare` | `byax_drafts` | `POST /v1/execute` | `byax.drafts.compare_drafts` | Review > Compare drafts… |
| History | bring changes from draft | `byax drafts --action bring` | `byax_drafts` | `POST /v1/execute` | `byax.drafts.bring_operation` | Review > Bring changes from draft… |

## Grammar tiers and language packs

The `grammar` command reports and configures the two grammar tiers (the built-in rules and the optional, local LanguageTool server) and checks plain text; `languages` manages dictionary, grammar and interface-string packs. Over MCP and REST only the read-only actions are available (`grammar` status, rules and check; `languages` list, verify and coverage). Configuring, installing, starting or stopping LanguageTool and installing or removing a pack are refused with `unsupported_feature`; use the local CLI or desktop. See [grammar](grammar.md) and [translations](translations.md).

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| Checks | grammar tiers status | `byax grammar --action status` | `byax_grammar` | `POST /v1/execute` | `byax.grammar.languagetool_status` | Inspector > Checks |
| Checks | grammar rule catalogue | `byax grammar --action rules` | `byax_grammar` | `POST /v1/execute` | `byax.grammar.rule_set` | Inspector > Checks |
| Checks | check text with the grammar tiers | `byax grammar --action check` | `byax_grammar` | `POST /v1/execute` | `byax.grammar.check_text` | — (headless) |
| Checks | configure the LanguageTool tier | `byax grammar --action configure` | `byax_grammar` | `POST /v1/execute` | `byax.grammar.save_config` | Inspector > Checks |
| Checks | start or stop the local LanguageTool server | `byax grammar --action start` | `byax_grammar` | `POST /v1/execute` | `byax.grammar.start_languagetool` | Inspector > Checks |
| Checks | install LanguageTool (explicit download) | `byax grammar --action install-languagetool` | `byax_grammar` | `POST /v1/execute` | `byax.grammar.install_languagetool` | Tools > Languages… |
| Discovery | language packs | `byax languages --action list` | `byax_languages` | `POST /v1/execute` | `byax.language_packs.list_packs` | Tools > Languages… |
| Inspection | verify language packs | `byax languages --action verify` | `byax_languages` | `POST /v1/execute` | `byax.language_packs.verify_packs` | Tools > Languages… |
| Inspection | translation coverage | `byax languages --action coverage` | `byax_languages` | `POST /v1/execute` | `byax.language_packs.coverage_report` | Tools > Languages… |
| Files | install language pack | `byax languages --action install` | `byax_languages` | `POST /v1/execute` | `byax.language_packs.install_pack` | Tools > Languages… |
| Files | remove language pack | `byax languages --action remove` | `byax_languages` | `POST /v1/execute` | `byax.language_packs.remove_pack` | Tools > Languages… |

## Compile pipeline

`compile` turns a project into a print PDF, EPUB, Word or Markdown book (see the
[user guide](user-guide.md#compiling-a-book)). It never changes the master; `--preset-file` is a
path argument and is scoped like `--out` over REST. `save-preset` is a canonical `set_settings`
operation, so `--expect-revision`, `--request-id` and `--dry-run` behave as for `apply`.

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| Output | compile a book | `byax compile PATH --preset NAME --target pdf --out FILE` | `byax_compile` | `POST /v1/execute` | `byax.compile.compile_document` | File > Compile… |
| Checks | compile preflight | `byax compile PATH --preflight-only` | `byax_compile` | `POST /v1/execute` | `byax.compile.preflight` | File > Compile… |
| Discovery | compile presets | `byax compile PATH --action presets` | `byax_compile` | `POST /v1/execute` | `byax.compile.list_presets` | File > Compile… |
| Inspection | effective compile profile | `byax compile PATH --action show` | `byax_compile` | `POST /v1/execute` | `byax.compile.resolve_profile` | — (headless) |
| Editing | save compile preset | `byax compile PATH --action save-preset --name NAME` | `byax_compile` | `POST /v1/execute` | `byax.compile.save_preset_operations` | File > Compile… |
| Files | export compile preset | `byax compile PATH --action export-preset --out FILE.json` | `byax_compile` | `POST /v1/execute` | `byax.compile.preset_collection` | — (headless) |

## Manuscript corpus and codex

`project-search` searches the project folder (the open document, its sibling `.byax` packages and
its series' member documents) through the shared block-level index in the user cache. Every call
refreshes that index incrementally from local files before answering, so results are live; no
call uses the network. `saved-add` and `saved-remove` are canonical `saved_search_*` operations.
`codex` reads and edits codex entries (canonical `codex_*` operations, `propose-fact` records a
review proposal instead). Its reads omit entries and traits hidden from AI unless the local CLI
passes `--include-hidden`; MCP and REST refuse that flag, and refuse `series-attach` and
`series-detach` (a series attaches libraries). `entity` (`byax_entity`) is the read-only,
always AI-visible view for model clients. All lists are bounded with `offset` continuations.

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| Search and targeting | project find | `byax project-search --action find` | `byax_project_search` | `POST /v1/execute` | `byax.project_index.ProjectIndex.search` | Edit > Find in project… |
| Inspection | project index status | `byax project-search --action status` | `byax_project_search` | `POST /v1/execute` | `byax.project_index.ProjectIndex.status` | — (headless) |
| Search and targeting | project index refresh | `byax project-search --action refresh` | `byax_project_search` | `POST /v1/execute` | `byax.project_index.ProjectIndex.refresh` | Edit > Find in project… |
| Search and targeting | saved searches | `byax project-search --action saved-list` | `byax_project_search` | `POST /v1/execute` | `byax.project_index.saved_searches` | Edit > Find in project… |
| Search and targeting | run saved search | `byax project-search --action saved-run` | `byax_project_search` | `POST /v1/execute` | `byax.project_index.run_saved` | Edit > Find in project… |
| Editing | save search | `byax project-search --action saved-add` | `byax_project_search` | `POST /v1/execute` | `byax.engine.apply` | Edit > Find in project… |
| Editing | remove saved search | `byax project-search --action saved-remove` | `byax_project_search` | `POST /v1/execute` | `byax.engine.apply` | Edit > Find in project… |
| Search and targeting | backlinks | `byax project-search --action backlinks` | `byax_project_search` | `POST /v1/execute` | `byax.project_index.links` | Edit > Find in project… |
| Search and targeting | unlinked mentions | `byax project-search --action unlinked-mentions` | `byax_project_search` | `POST /v1/execute` | `byax.project_index.links` | Edit > Find in project… |
| Inspection | codex entries | `byax codex --action list` | `byax_codex` | `POST /v1/execute` | `byax.codex.list_entries` | Tools > Codex… |
| Inspection | codex lookup | `byax codex --action lookup` | `byax_codex` | `POST /v1/execute` | `byax.codex.lookup` | Tools > Codex… |
| Inspection | codex mentions | `byax codex --action mentions` | `byax_codex` | `POST /v1/execute` | `byax.codex.mentions` | Tools > Codex… |
| Inspection | codex mention heatmap | `byax codex --action heatmap` | `byax_codex` | `POST /v1/execute` | `byax.codex.heatmap` | Tools > Codex… |
| Inspection | codex series | `byax codex --action series` | `byax_codex` | `POST /v1/execute` | `byax.series.resolve` | Tools > Codex… |
| Editing | add codex entry | `byax codex --action add` | `byax_codex` | `POST /v1/execute` | `byax.engine.apply` | Tools > Codex… |
| Editing | edit codex entry | `byax codex --action update` | `byax_codex` | `POST /v1/execute` | `byax.engine.apply` | Tools > Codex… |
| Editing | remove codex entry | `byax codex --action remove` | `byax_codex` | `POST /v1/execute` | `byax.engine.apply` | Tools > Codex… |
| Editing | add codex trait | `byax codex --action add-trait` | `byax_codex` | `POST /v1/execute` | `byax.engine.apply` | Tools > Codex… |
| Editing | edit codex trait | `byax codex --action update-trait` | `byax_codex` | `POST /v1/execute` | `byax.engine.apply` | Tools > Codex… |
| Editing | remove codex trait | `byax codex --action remove-trait` | `byax_codex` | `POST /v1/execute` | `byax.engine.apply` | Tools > Codex… |
| Review | propose codex fact | `byax codex --action propose-fact` | `byax_codex` | `POST /v1/execute` | `byax.codex.propose_fact` | — (headless) |
| Files | attach series | `byax codex --action series-attach` | `byax_codex` | `POST /v1/execute` | `byax.series.reference_for` | — (headless) |
| Files | detach series | `byax codex --action series-detach` | `byax_codex` | `POST /v1/execute` | `byax.engine.apply` | — (headless) |
| Files | export series codex | `byax codex --action series-export` | `byax_codex` | `POST /v1/execute` | `byax.series.save` | — (headless) |
| Inspection | entity lookup (read only) | `byax entity` | `byax_entity` | `POST /v1/execute` | `byax.cli_codex.entity_command` | — (headless) |

## Extensions and macros

`extensions` lists, inspects, verifies and runs extension checks and export hooks; `macro` lists, saves,
runs (or dry-runs) and removes saved command macros. See [extensions](extensions.md). Reading and
running are open to MCP and REST. Installing, package inspection, enabling, disabling, removing and
issuing a scoped REST token change what code may run on this computer, so they are refused over MCP
and REST with `unsupported_feature` (use the local CLI or desktop), as are `macro --allow-admin` and
saving or removing macros in the user config (macros stored in a document are an ordinary revision and
are allowed). Every macro step is checked with `check_provider_admin`, and behind REST with the
service's path policy after placeholder substitution. `export --hooks` runs enabled export hooks and
reports them under `hooks`; `--hooks-dir` is a path argument scoped like `--out`. A scoped extension
token (`bxe1.…`, from `extensions --action token`) can call only the read-only commands and
`review --action propose` with text-edit operations (`403 forbidden` otherwise).

| Family | Operation | CLI | MCP tool | REST | Library | GUI |
| --- | --- | --- | --- | --- | --- | --- |
| Discovery | list extensions | `byax extensions --action list` | `byax_extensions` | `POST /v1/execute` | `byax.extensions.summaries` | Tools > Extensions… |
| Inspection | extension details | `byax extensions --action info` | `byax_extensions` | `POST /v1/execute` | `byax.extensions.describe` | Tools > Extensions… |
| Inspection | verify installed extensions | `byax extensions --action verify` | `byax_extensions` | `POST /v1/execute` | `byax.extensions.verify` | Tools > Extensions… |
| Checks | run extension checks | `byax extensions --action run-check` | `byax_extensions` | `POST /v1/execute` | `byax.extensions.evaluate` | Tools > Extensions… |
| Output | run an export hook | `byax extensions --action run-hook` | `byax_extensions` | `POST /v1/execute` | `byax.extensions.run_hook` | Tools > Extensions… |
| Output | export with hooks | `byax export --hooks` | `byax_export` | `POST /v1/execute` | `byax.extensions.run_export_hooks` | Tools > Extensions… |
| Files | install extension | `byax extensions --action install` | refused | refused | `byax.extensions.install` | Tools > Extensions… |
| Files | enable or disable extension | `byax extensions --action enable` | refused | refused | `byax.extensions.set_enabled` | Tools > Extensions… |
| Files | remove extension | `byax extensions --action remove` | refused | refused | `byax.extensions.remove` | Tools > Extensions… |
| Files | scoped extension token | `byax extensions --action token` | refused | refused | `byax.service.issue_extension_token` | — (headless) |
| Inspection | list macros | `byax macro --action list` | `byax_macro` | `POST /v1/execute` | `byax.macros.user_macros` | Tools > Macros… |
| Editing | run or dry-run a macro | `byax macro --action run` | `byax_macro` | `POST /v1/execute` | `byax.macros.run_macro` | Tools > Macros… |
| Files | save macro | `byax macro --action save` | document store only | document store only | `byax.macros.validate_macro` | Tools > Macros… |
| Files | remove macro | `byax macro --action remove` | document store only | document store only | `byax.macros.find` | Tools > Macros… |
