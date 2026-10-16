# Specification acceptance matrix

This matrix maps the complete product specification to the implementation and its remaining verification. **This implementation is not a completed acceptance of the entire specification.** A feature represented in the document schema is not necessarily rendered or operable in the GUI. Passing serialization is not publication validation.

Statuses: **verified subset** means a named automated test exercises the stated behavior; **partial** means implemented behavior has material limits or incomplete scenario coverage; **missing / unverified** means no implementation or no relevant end-to-end evidence. Physical Windows/Linux and credentialed provider acceptance remain unverified.

## Feature coverage

| Spec | Required capability | Implementation / evidence | Status and remaining work |
| --- | --- | --- | --- |
| 1 | Everyday writing; shared interfaces; authorship; local deterministic functions; advanced discovery | `gui.py`, `engine.py`, `cli.py`, `mcp.py`, `providers.py` | Partial: architecture and native editor provided; the complete Word/Pages scenario set has not passed |
| 2.1–2.2 | Native Windows/Linux install, dialogs, clipboard, drag/drop, fonts, mic, printing, associations, accessibility/scaling | Qt GUI; `packaging/`; `scripts/build.py`, `scripts/build_deb.py`; compatibility matrix | Partial: installer recipes exist; physical installed Windows 11 / Ubuntu 24.04 acceptance unverified |
| 2.3 | One engine for GUI, library, CLI, MCP, optional disabled REST; headless operations | `engine.py`, `cli.py`, `mcp.py`, `service.py`, `transport_policy.INTERFACE_INVENTORY`, `docs/interface-parity.md`; `tests/test_interface_parity.py` | Verified subset: every spec 16.2 operation is inventoried against CLI command, MCP tool, REST call, importable library callable and GUI action, and tests fail on drift; library/CLI/MCP/REST results and errors are compared for the same inputs. Gaps recorded: headless document statistics, native printing (GUI only) and a dedicated repair generator |
| 3.1 | Typing, selections, Unicode/IME, clipboard, shortcuts, undo/redo, drag/drop | `gui_editor.py`, `gui.py` | Partial: native widget paths; IME, complex-script selection and OS clipboard unverified |
| 3.1 | Scoped/case/whole-word/regex find; replacement preview; navigation by all semantic targets | `engine.find`, GUI find/outline | Verified subset: scoped regex replacement previews expand groups, outline/section navigation tests pass. Complete bookmark/comment/finding/figure/page navigation remains to be exercised on both platforms |
| 3.1 | Counts/reading time distinguishing authored/selected output; continuous/paginated/Markdown views with stable source | Inspection and GUI views | Partial: full selection/publication statistics and edit-free view switching need acceptance |
| 3.2 | Inline/paragraph formatting, tab stops, indents; nested lists, restart/continuation, split/join | Canonical marks/attrs; `set_list`; `formatting.resolve_lists`; `layout.py`, `gui_editor.py` | Verified subset (engine): every spec 3.2 inline mark and paragraph attribute (alignment, indent, negative first-line/hanging indent, line/paragraph spacing, tab stops) survives save/reopen and builds in the shared Qt layout (`test_every_inline_and_paragraph_format_survives_save_reopen_and_layout`). Lists carry explicit `list_id`, kind and level; `resolve_lists` numbers nested levels, continuation and restart; splits keep list identity without copying a restart, joins re-merge divided marks, and numbering survives restore and reopen (`test_list_identity_levels_restart_and_numbering_survive_split_join_undo_and_reopen`). Remaining: the Qt renderer still numbers implicit lists by (kind, level) rather than `resolve_lists`, and GUI list controls do not yet set `list_id`; physical GUI acceptance unverified |
| 3.2 | Named inheritable character/paragraph styles, overrides, clear formatting, affected IDs | `formatting.py`; `define_style`, `apply_style`, `apply_character_style`, `clear_formatting`; `inspect --section formatting/style_inheritance` | Verified subset (engine): paragraph and character styles (`kind`) inherit independently; cross-kind `based_on`, cycles and kind changes that strand uses are rejected; `element_report` gives inheritance chains, effective values and explicit paragraph/run overrides; changing a style reports every dependent element ID without changing text; `clear_formatting` removes direct formatting while keeping text, semantic marks (links, fields, citations) and list/structure attributes and is a rejectable tracked change; all common styles apply via canonical operations (`test_character_and_paragraph_styles_inherit_independently_with_override_reports`, `test_clear_formatting_keeps_text_semantic_marks_and_structure_and_is_tracked`). Remaining: GUI character-style picker and override inspector, Qt rendering of `character_style` marks |
| 3.3 | Atomic save/save-as, autosave/recovery, recent files, external change detection | `storage.py`, GUI recovery/session handling; interrupted-replacement test; `tests/test_history_save.py` | Verified subset: failed replacement preserves prior package. Save, Save As and autosave serialize a model copy on a worker, and a second save queues behind the running write. Close waits for an in-flight write before releasing the lock. The disk stamp and dirty state settle only when the write completes, and edits made during a write stay unsaved. The external-change check pauses while this session writes. GUI crash/recovery and external-change scenarios on installed desktops are unverified |
| 3.3 | Authoritative GUI state versus CLI/service writes | `DocumentLock`; independent-process acceptance test | Verified subset: independent writer fails while session lock held; full GUI/CLI interaction still needs installed desktop test |
| 4.1 | Declared Markdown syntax/table extension; whitespace/source preservation; localized visual changes; explicit rich conversion | `exchange.MARKDOWN_DIALECT` (CommonMark + pipe tables, reported in import/export reports), localized source rewriting, `markdown_unrepresentable`; `tests/test_markdown_fidelity.py`, `tests/fixtures/markdown/nested.md` | Verified subset: edit-free view switching is byte-identical for LF and CRLF on a nested fixture (setext/ATX headings, nested and ordered lists, quotes with lists, `~~~` fences, indented code, aligned tables, breaks, images); visual edits rewrite only the changed lines of list items, quoted paragraphs, setext headings, fences and individual table rows; inserted/deleted blocks touch only their lines; formatting outside the dialect on a Markdown source requires explicit `.byax` conversion or `allow_markdown_losses` (`review_required`) with located losses. Structural changes inside lists/quotes and reordering fall back to a reported normalized rewrite; hard-break syntax is not preserved on rewritten paragraphs |
| 4.2 | Semantic rich blocks, tables, marks, images/captions, notes/bookmarks/references; stable IDs and reflow | `model.py`, canonical operations, shared layout resolver | Verified subset: semantic blocks, tables, marks, bookmarks and footnote associations survive tested edits/reflow. Caption keeps (`caption` style or `caption_for`) keep object and caption on one page/column; `xref` marks (`insert_cross_reference`) resolve page/text/number labels from semantic targets or bookmarks on every reflow and deleted targets render `??` with a located `xref_unresolved` finding (tests in `tests/test_layout.py`). Broader deleted-object relationships across review/history remain with their own issues |
| 4.2 | Conversion preview/loss reports and preserve original | Exchange adapters; `conversion_preview` / `convert --dry-run`; per-import `content_audit`; `tests/test_exchange_fidelity.py` | Verified subset: previews write nothing and list located losses; DOCX/ODT/TXT imports compare every source paragraph with imported content and report any omission at its XML location (`content_omitted`), so text is never silently dropped; originals stay attached and conversion refuses to overwrite them. Visual similarity is not measured |
| 5.1 | Portable ZIP master with complete model/history, manifests/hashes/schema versions, case handling, no secrets | `storage.py`; representative fixture round-trip tests | Verified subset: generated documents round-trip; unknown-extension, corrupt-archive, secret rejection cases covered separately in core tests |
| 5.2 | Embedded images, explicit external links, missing-link findings, fonts/substitution/permissions, sources/audio retention, limits | Assets/`dependencies` model; `link_asset`, `place_linked_image`, `refresh_dependency`, `mark_dependency_missing`, `embed_dependency`; `dependencies.refresh_operations`; layout findings, provider/transcription metadata; `fonts.font_file_info` / `inspect_font` / `embed_font` / `glyph_fallbacks`; `tests/test_fonts_reproduction.py` | Verified subset: any installed or bundled family can be inspected for resolved identity, substitution, file hash and declared OS/2 embedding permission (installable/editable/preview-print/restricted, subsetting, bitmap-only); embedding copies the font into the package only when permitted and is kept by clean publication; unavailable or restricted fonts fail with structured errors; missing glyphs are reported per grapheme cluster. Windows font resolution and arbitrary third-party font licences are not physically verified. Assets: Images still embed by default; external images are explicit dependency records (HTML/ODT imports and schema-1 packages included) inspected with `inspect --section dependencies`, with `dependency.unresolved`/`dependency.missing` findings located at the image blocks. Retrieval happens only through `dependencies.refresh_operations(authorize=True)` (network additionally needs `allow_network=True`); refresh updates SHA-256 hashes, replaces stale caches and invalidates findings; embed severs the link; link/embedded policy survives save/reopen and clean publication (`test_linked_images_are_inspectable_refreshed_with_authorization_and_embedded`). Remaining: GUI dependency panel and a dedicated CLI retrieval command (headless use is the library helper plus canonical operations through `byax apply`) |
| 5.3 | Deterministic readable unpack/pack, omitted history/secrets/caches, validation/migration/unknown preservation | `storage.pack/unpack/open_document/migrate_package`; `migration.py`; `tests/fixtures/schema-v1-*`, `tests/fixtures/schema-v2-*` | Verified subset: native pack/unpack omits history and replay records by default and stays deterministic; manual source edits regenerate hashes and a deterministic revision while validating asset identity. Schema policy is documented in `docs/architecture.md`: document schema 3 is current; the registered 2→3 transition (optional binder, optional citation `anchor`; rendering affixes only checked to be text) is proven against a schema-2 manuscript package and source folder with restorable history, preserved unknown keys and rejected conflicting constructs (`test_schema_v2_fixture_opens_migrated_with_history_restorable`, `test_v2_unmigratable_constructs_are_rejected_without_writes`, `test_v2_citation_anchor_is_kept_when_valid_and_rejected_when_it_means_something_else`), and the registered 1→2 transition upgrades live content and every retained history state in memory, reports each transformation, preserves unknown top-level constructs, and refuses output that would introduce dangling identities or references. Opening never rewrites a file; the first save over an older package keeps a byte-identical `name.schema1-<hash>.byax` copy; `migrate_package` writes to a different path only. Future versions, conflicting constructs and unknown block types are rejected without a destructive save (`tests/test_migration.py`, using real schema-1 package and source-folder fixtures) |
| 6.1 | Physical geometry/units, paper/orientation, sections/columns/breaks; widow/orphan/keep rules | `layout.py` `_Resolver`: per-column-width Qt flow runs and line-level break decisions | Verified subset (offscreen PNG/PDF): explicit per-section column `widths`/`ratios`/`gaps`, `column_break_before`, full-width blocks and `continuous`/`odd_page`/`even_page` section starts share or start physical pages as declared, with balanced columns before continuous transitions; unequal-width flow relays out remainders so pdftotext finds every word exactly once. `widow_control`/`orphan_control` line counts split paragraphs at legal lines across page and column boundaries, keep-with-next chains and keep-together fit-checks move content, and impossible rules or geometry produce located findings (`keep_rule_unsatisfiable`, `widow_orphan_unsatisfiable`, `column_geometry`) without dropping text; crowded fixtures run across three paper/font combinations. Text exclusion zones around page-positioned layers are not implemented |
| 6.2 | Masters/backgrounds/letterhead; first/odd/even headers, numbering formats/restarts | `layout.masters`, `define_master`/`delete_master`, shared page painter | Verified subset: reusable masters carry geometry, columns, header/footer regions and artwork (with `pages` first/odd/even/list policies) and are assigned to the document or a section with section overrides; masters survive save/reopen and in-use masters cannot be deleted. Header/footer regions accept first/odd/even variants with left/center/right parts, distances and alignment; decimal/Roman/alphabetic numbering and restarts remain tested. Full press-kit/report scenarios and installed-GUI master editing remain unverified |
| 6.2 | Updated TOC, semantic cross-references/bookmarks, real page footnotes with overflow/continuation | Resolver note areas and bounded label passes | Verified subset: footnotes are numbered in reference order (formats, per-section restart, marker offsets) and placed on the page of the line carrying their marker; each page reserves only its own notes (no uniform reservation), and notes taller than the remaining area continue in following note areas with a `footnote_continued` finding. TOC and cross-reference labels iterate to a fixed point. Physical print comparison remains unverified |
| 6.3 | Anchored/absolute objects, five wrap modes, sizing/aspect, caption keep, alt/reading order | Image attrs, resolver `objects` report, shared painter | Verified subset: inline/square/above-below objects flow and never split; behind/in-front objects use inspectable `move_with_text`, `fix_on_anchor_page` or `fixed_page` policies, follow their anchor across pages as text grows, report missing anchors and page clipping, preserve aspect (`lock_aspect`, column scaling with `image_scaled`), and are reported in semantic reading order with alt text independent of z-order. Text wrapping around free-positioned objects, tagged-PDF alt text and GUI drag manipulation remain incomplete |
| 6.4 | Same preview/GUI/render/PDF layout; contact sheets/overlays/crops; native page-range print; reproduction | `layout.py`, GUI print, publication records; actual PNG/PDF acceptance | Verified subset: GUI preview/print, PNG render and PDF export paint one cached resolution; reports carry a `layout_signature` digest of break decisions and tests assert equality across PNG, PDF and the editor-document print path. Proof overlays use resolved fragment bounds per column. Physical native printing remains unverified |
| 7 | Editable table insertion/deletion/reorder, cell formatting/width/padding/borders/shading/merges, repeat headers, row splitting | Table model/operations; resolver table units | Verified subset: semantic cells/header rows/merged cells render; header rows repeat on continuation columns/pages (`repeat_header`), an optional `continued_label` is drawn, and `row_split` `auto`/`allow`/`never` decides line-level row splits; rows linked by row spans move as a unit; oversized unsplittable rows split with a located `table_row_unsplittable` finding. Fixtures assert every cell ID appears once and pdftotext finds each body row once. Column-span reorder semantics remain as previously validated |
| 7 | CSV interchange preserving formatting, accessibility headers, movable semantic sections with anchors | Canonical table/section operations and adapters | Verified subset: CSV import/export retains existing identities/attributes, supports explicit atomic growth and reports per-row template outcomes; deleted table cells orphan review/bookmarks. Semantic section movement preserves content and anchors as a unit in regression tests |
| 8.1 | Named/grouped/locked/reordered/visible layers; optional inspector; stable IDs | `group_layers`/`ungroup_layers`/`reorder_layer`, `layout.layer_inspector`, layer painter | Verified subset: layers can be grouped (group visibility/opacity apply to members, a locked group locks members through canonical edits), reordered with plane-preserving z renumbering, anchored to semantic blocks (`move_with_text`) and limited by page/section/master; `layer_inspector` reports stacking, plane, lock, group, anchor and output state. Layer wrapping of body text and the GUI inspector dialog remain incomplete |
| 8.2 | Decoration hide versus semantic exclusion versus overlays versus true redaction; reading order/protection | `layout.output_view`; `publication.py`; serialized-redaction acceptance test | Verified subset: render/PDF accept an output kind; hidden objects (layers, groups, invisible images that keep their space) are not drawn, `excluded`/`outputs`/`exclude_outputs` content is removed from that output and reflowed, and every report includes a visibility summary stating that neither is redaction; tests confirm the authored document is unchanged. Conservative sanitized copies remain the only redaction path; all protected kinds remain incomplete |
| 9.1 | TXT/MD/DOC/DOCX/RTF/ODT/HTML/BYAX import and listed exports with editable semantics, provenance, located losses | `exchange.py`, bounded LibreOffice DOC adapter; `tests/test_exchange_fidelity.py` | Verified subset: DOCX import keeps inserted text and maps tracked insertions/deletions, comments (with ranges), footnotes/endnotes, bookmarks, simple/complex fields (hyperlinks become links), numbering kind/level and text boxes to native, actionable structures; DOCX export writes comments and bookmarks. ODT import maps annotations, bookmarks, fields and text tracked changes. RTF import reads named styles (with inheritance), page size/margins, header/footer, footnotes, annotations, fields and PNG/JPEG pictures; RTF export writes stylesheet, tables, notes, pictures and geometry. Non-Latin, bidirectional, combining and emoji text survives TXT/MD/HTML/RTF/DOCX/ODT → BYAX → PDF. The LibreOffice DOC adapter (LibreOffice 24.2.7.2 in the Linux test container) imported a converted historical-format `.doc` with that text and records the converter version; it runs with macros disabled, a private profile, and kills its process group on timeout. Remaining losses (tracked formatting changes, section-specific geometry, merged-cell/border details, equations, OLE objects) are reported by type and location. Windows LibreOffice installations and a corpus of real historical Word files remain unverified |
| 9.2 | Saved export profiles, staged batch publication, selected-output checks; never equate export success with validation | `publication.export_clean` (`PUBLICATION_CATEGORIES`, `verify_absent`), `exchange.publish_file`, `jobs.run_batch`; `tests/test_publication_policy.py`, `tests/test_jobs_recovery.py` | Verified subset: saved profiles may set `clean: true`; every inclusion category (comments, tracked changes, proposals, findings, history, metadata, references, source snapshots, transcripts, audio, hidden content, originals) is independent and reported as included/excluded; unknown categories are rejected; checks run on the selected content and execution, validation and review stay separate fields. Exports refuse to overwrite unless authorized, stage beside the destination and publish atomically; interrupted adapters leave no partial or temporary files; batch `staging=all` withholds every output when any input fails. The desktop export-profile dialog is outside this change |
| 10 | Templates include content/styles/settings/masters/fields/voice/references/checks; typed validation; JSON/CSV fill; settings collections | `profiles.py` (`export_settings`, `compile_collection`, `instantiate_template`, `validate_field_value`), `table_io.py`, `gui_profiles.py`, engine fill operations and CLI | Verified subset: typed text/number/integer/date/enum/boolean/asset fields with defaults and per-field outcomes; JSON template instantiation keeps the template unchanged, preserves marks and repeated tokens, and leaves literal placeholders visible (defining a field no longer blanks its token); CSV per-row outcomes. Style/profile/source/terminology/field/layout/requirement/section-profile collections close over dependencies (style parents, profile sources), verify source hashes, reject credentials, map or clear provider aliases unavailable on another installation, resolve sections by heading text, and compile to bounded canonical operations with explicit conflict policies (`test_profiles.py`). Remaining: GUI management dialogs and CLI commands for collections/templates; masters are exchanged only as page layout settings |
| 11.1 | Undo/redo, checkpoints/branches/restoration, semantic diffs, bounded deltas/snapshots/compaction and provenance | Engine history functions (`switch_branch`, `merge_base`, `propose_merge`); GUI undo/history | Verified subset: deltas, periodic snapshots and explicit compaction (test_delta_history_restores_every_revision_and_compaction_preserves_checkpoint). Revisions record parents and compact provenance (origin, author, request ID, operation types). The checkpoint → branch → GUI-origin and CLI-origin edits → switch → merge proposal → semantic diff → save/reopen → restore sequence runs headlessly (`test_branch_merge_through_proposal_full_history_scenario`). Remaining: the same sequence driven through the desktop GUI |
| 11.2 | Anchored threaded comments/orphans; separately attributed insert/delete/format tracking; grouped accept/reject | `anchors.py`; review model and canonical operations (`accept_changes`/`reject_changes`) | Verified subset: passage comments, threaded replies, bookmarks, proposal ranges and tracked changes follow insert/delete/split/join and section moves; results report `anchor_mappings` and `identity_changes`; deleted passages become `unresolved` and deleted targets `orphaned`. Tracked changes are range-level insertions, deletions, replacements and formatting changes with author/origin/timestamp; rejecting one restores its text and marks without undoing unrelated later edits; grouped accept/reject by IDs, author, origin, kind, object or group is atomic (`tests/test_review_anchors.py`). Remaining: GUI group-selection controls and physical GUI review |
| 11.3 | Exact selective proposals with preconditions/dependencies/checks, semantic comparison | Engine proposals (`edits`, `depends_on`, `required_by`), `preview_proposal`, `compare.py`; GUI review dock | Verified subset: proposals carry per-edit identities with inferred (created IDs, same-target order) and explicit dependencies; selected edits accept or reject atomically only when dependency-consistent, revalidating exact original preconditions or engine-remapped exact text, protection rules and required suites; partial outcomes are recorded and accepted edits are attributed to the proposal author and accepting user. `diff`, dry runs and `preview_proposal` include a semantic comparison separating wording (word-level), structure/moves, styles, tables, layout, assets, dependencies, references, review and metadata with a readable summary (`tests/test_proposals_history.py`). Remaining: GUI per-edit selection and comparison view |
| 12.1 | Spelling/terminology/grammar/editorial/internal/source consistency/requirements/structure/layout/print/accessibility, all scopes | `checks.py` rule catalog; providers/factual review | Verified subset: deterministic grammar/punctuation (spacing, article, capitalization, repeated punctuation, unbalanced pairs), editorial repetition/length, preferred terminology, term-variant and acronym consistency, layout overflow/blank pages/stranded headings plus resolved geometry, accessibility language/title/link text/contrast/empty headers/reading-order anchors; English-only rules report unmeasurable coverage for other languages; document, section, block, selection and batch scopes (`test_checks_lifecycle.py`). Agreement/ambiguity grammar, clarity/voice, semantic contradictions and uncited claims always report unmeasurable coverage: they need a configured reviewer and were not exercised against live models |
| 12.2 | Independent sensitivity/freedom/voice controls, conservative/editorial presets, intentional exceptions/protection and section profiles | `profiles.py` (`effective_profile`, `request_settings`, `set_section_profile` operation), checks | Verified subset: section profiles keyed by stable heading IDs with explicit inheritance (scalar overrides, additive dictionaries/exceptions, `inherit: false`) and per-field origins; sections keep profiles when moved; effective settings drive check sensitivity, dictionaries and exceptions per block and appear in context packages; protected passages keep findings but block automatic repairs; exceptions never hide strict failures; assignments persist and exchange without credentials (`test_profiles.py`). Remaining: GUI assignment UI; provider/transcription callers do not yet send `request_settings` output |
| 12.3 | Located versioned findings, statuses/evidence/repairs; separate undoable repair; failures survive pagination | Check findings, `record_checks`/`set_finding_status`/`apply_repair` operations | Verified subset: every finding carries rule, family, severity, strict/advisory policy, affected IDs, exact range and passage, measured values, evidence, repair contract, profile version/fingerprint, engine, source versions and scope; all six statuses with enforced transitions (reasons required for dismissals/exceptions, strict failures cannot be dismissed, stale/accepted edit are system states); repairs are separate undoable revisions guarded by expected text; recording checks never changes content; limit zero keeps failing totals (`test_checks_lifecycle.py`). GUI status controls remain to be wired |
| 12.4 | Concrete coverage health; dependency-sensitive invalidation and incremental scopes | `checks.py` dependency units, `invalidate_findings`, `health`, incremental `record_checks` | Verified subset: findings depend on block content/effective profile/cited sources, context-sensitive heading and consistency units, or the whole document; commits stale only affected findings; health reports completed/skipped/failed/unmeasurable/stale coverage and never keeps a prior clean pass; scoped checks report expanded context evaluation. Incremental rechecks equal full rechecks for text, insert, structure, move, delete, source, terminology and profile edits (`test_checks_incremental.py`). Layout-dependent document rules always recheck as one unit |
| CP 6.2, 6.5 | Fiction analysis checks: dialogue tags, adverbs, filter words, openings, echoes, rhythm, pacing, sensory balance, show/tell, invented-name spelling; advisory, tunable per section, no findings on clean text, expected findings on seeded text, incremental | `fiction_checks.py` (rules, `fiction` suite in `checks.py`), `prose.py`, `resources/fiction/en.json`, `check --family fiction`, Checks tab families | Verified subset: 14 rules with exact ranges and low-noise sensitivity gating; per-profile and per-section enable/disable/thresholds; English word list only, other languages report unmeasurable coverage; the clean fixture yields no findings at any sensitivity and the seeded fixture exactly its 18 annotations (`test_fiction_checks.py`); one-paragraph edits re-run only the paragraph, the following paragraphs whose look-back reaches it, its chapter unit and the names unit, and incremental equals full (`test_fiction_incremental.py`); precision/recall published and pinned (`test_fiction_benchmark.py`, `benchmarks/0.3-fiction-checks.json`). Remaining: heuristics only (a literal "felt" is flagged, unlisted ways of telling an emotion are missed); name checks use the dictionary, not the codex, which will plug in through `register_name_source`; other languages (D4); fixtures are small and curated, not a manuscript corpus |
| CP 6.3 | Per-chapter readability and style reports with sample-size caveats, exportable as a report document with located losses; pacing graph with a table equivalent | `readability.py`, `readability` command, `gui_readability.py`, Tools > Readability report… | Verified subset: six indices against their published formulas on a known sample, per-index sample-size caveats (SMOG 30 sentences), chapter and pacing data, report export through the `.byax`, Word, Markdown, HTML and text exporters with the exporters' loss report, library/CLI/MCP/REST parity (`test_readability.py`); dialog, keyboard-operable QPainter pacing graph and table equivalent in offscreen tests (`test_gui_readability.py`). Syllable counting and passive voice are heuristics and the indices are calibrated for English; physical-desktop screen-reader behaviour of the graph is unverified |
| 13.1–13.2 | Explicit exclusive/supplementary/open sources; no instruction-triggered retrieval; evidence hashes/excerpts; five factual outcomes/invalidation | Reference model, `record_findings`, `remove_reference`, deterministic quotation verification, provider context contract | Verified subset: all five outcomes recorded through canonical operations with exact claims, excerpt-verified evidence, content hashes and quotation/paraphrase/inference relations; invented or mismatched evidence rejected; citation marks attach sources to claims; quoted citations are verified (supported/not established) without a model; updating or removing a source stales only dependent findings; source and document corrections cannot share a batch and required checks use frozen requirements/profiles/references; context packages never retrieve under any authority policy; CLI apply/check/export strict-publication scenario (`test_checks_evidence.py`). Live-model semantic evidence quality remains unverified |
| 14.1 | Explicit text/proposal/audio/streaming/language/limit capabilities; independent recognition/editorial models and defaults | `providers.py` six provider adapters; `ProviderRegistry.set_route`/`resolve` | Verified subset (mocked transports and fixture processes): truthful per-adapter `streaming` flags (OpenAI SSE deltas and whisper.cpp incremental segments declare streaming; ElevenLabs, Anthropic, Gemini and Ollama do not); recognition, review and cleanup roles resolve independently through per-role defaults, per-check and per-workflow overrides, profile mapping and explicit choice, with capability enforcement; missing configuration leaves local writing available. Not verified: dated real inference, account/model/language/decoder matrices (`scripts/provider_smoke.py` collects this evidence when credentials are supplied) |
| 14.2 | Endpoint/key reference/timeout/retry/limits/cost, connection test, OS secrets, inspect outbound data, validated proposals/provenance | Provider configuration and keyring; GUI/provider commands; Inspector AI tab (`gui_ai.py`, `ProviderRegistry.chat`) | Verified subset (mocked transports): `unsent_only` retry policy retries only undelivered or 429-rejected requests and never delivered/timed-out ones; total-deadline timeout; input-limit preflight with measured size; missing credentials block before network activity and 401/403 report `credential_status: rejected` without echoing secrets; preview returns the exact transmitted body, redacted header names, limits, policy and credential availability; normalized usage with estimates only from user-supplied, sourced rates (`cost` stays null). The AI tab assistant reuses payload preview/authorization and the review proposal validator: chat mode returns text only; work-mode edits are validated range edits shown to the author and registered as pending Review proposals only after confirmation (`tests/test_gui_ai.py`). Not verified: OS keyring storage on installed Windows/Ubuntu and live provider behaviour |
| 15.1 | Microphone/audio import same pipeline; start/pause/resume/stop/select device/progress/cancel/keyboard/destination | `transcription.py`, Qt recording, GUI dictation dialog and jobs | Partial: the dictation dialog sends recordings and imported files through one `DictationSession`/adapter decoder path, with cursor, selection or bookmark destinations, keyboard shortcuts and progress state. The GUI pipeline is tested offscreen with fake recognizers. Physical microphone devices, OS permissions and decoder matrices are not verified in this headless environment |
| 15.2 | Direct, cleanup, document-aware modes; original comparison, separate recognizer/editor, bounded local process | Transcription/provider pipeline | Verified subset (fixtures): opt-in direct handling of fillers, repetitions and spoken punctuation; word-level raw/processed comparison; cleanup rejected (direct transcript kept) when approved terminology changes, a protected passage is rewritten or editing freedom is exceeded; cleanup sends only terms and protected passages, document-aware sends the selected context. Live mode quality and voice preservation with real models remain unverified |
| 15.3 | Provisional display/history grouping, revision-safe cleanup/anchors, application policy, failure retention/recovery | `LiveDictation`, `DictationJournal`, `recover_sessions`, GUI dictation | Verified subset (fixtures and offscreen GUI): streaming adapters feed provisional hypotheses that stay outside document and undo history; each final phrase is one canonical revision at a relocating anchor; removed or ambiguous destinations turn remaining phrases into suggestions; suggestions-only, automatic direct and authorized automatic cleanup policies; late cleanup replaces only unchanged or moved committed text and conflicts on edits or removal; interrupted sessions recover journaled segments, repair unfinalized WAV audio into a copy, disclose uncaptured audio and never resubmit jobs. Physical capture interruption on installed systems is not verified |
| 15.4 | Authorized context options, separate raw/audio retention, segment links; speech never commands | `build_context`, `RetentionStore`, GUI dictation | Verified subset: explicit nearby-paragraph, current-section, whole-document, dictionary, writing-sample and selected-reference controls with a per-part inclusion report shown in the authorization preview; profile voice/editing-freedom/protected passages applied; separate raw-transcript and audio retention with storage impact, usage totals and record/audio links (path plus SHA-256); timestamped segments from whisper.cpp, OpenAI verbose JSON and ElevenLabs word timings navigate to document text and audio ranges; dictated commands stay literal text. Live recognizer timestamp quality is unverified |
| 16.1–16.2 | UTF-8 JSON/human CLI, strict stdout/stderr/exit behavior, all command families, no invisible prompts; MCP/library parity | `cli.py`, `mcp.py`, `service.py`, `transport_policy.INTERFACE_INVENTORY`; `tests/test_interface_parity.py` | Verified subset: every family is mapped and drift-tested; the real CLI process is exercised with stdin JSON, stdout-only JSON, empty stderr, overwrite/remote policies as explicit flags and a shell pipeline; MCP results add `structuredContent` with the exit state. PowerShell and installed launchers are unverified. Library/CLI/MCP byte-identity tests also cover success, validation, stale-revision, unknown-action and missing-file results |
| 16.3–16.4 | Bounded selective inspection/context, total/scope/truncation/continuation, requirements/provenance/source versions/no secrets | Engine `inspect`/`find`/`context` | Verified subset: outline/section/nearby/table/field/finding/health inspection and scoped find with revision, scope, totals, offsets, object hashes, optional page locations and revision-bound continuation tokens that page every item once and fail when stale. Context packages include selected and nearby content with hashes, styles, terminology, effective voice/section settings, applicable protections, requirements, authorized references with versions, relevant findings and fields; a complete inclusion/omission report; incomplete state plus continuation when required data exceeds the bound; derived summaries marked inexact; no file reads or retrieval (`test_engine.py`). CLI does not yet expose the new inspection parameters |
| 16.5 | Stable/quoted/typed selectors; candidates on ambiguity/stale; scalar/grapheme boundaries; split/join remapping | Engine selectors; `anchors.py`; ambiguity/stale/grapheme acceptance tests | Verified subset: ambiguity, stale writes and grapheme boundaries; split/join report identity changes and anchor mappings for comments, replies, bookmarks, tracked changes and proposal edits (quoted-occurrence proposals are re-indexed); mixed emoji/ZWJ/combining/regional-indicator review state survives save/reopen (`test_mixed_grapheme_review_state_survives_save_and_reopen`). Remaining: the same scenarios through GUI input |
| 16.6–16.7 | Atomic canonical operations, schema errors/preconditions, dry-run exact effects/checks; separate measured protection types; no gate tampering | Engine apply, `_measure`/`_preserve`, `checks.layout_effects`/`layout_snapshot` | Verified subset: dry runs measure page counts, moved/reflowed blocks, affected pages and changed regions without committing or producing outputs; wording, table values, citations (with source hashes), object attributes, styles and visual regions are enforced independently; text-only protection allows repagination while visual-region protection compares rendered page, geometry and pixels and fails when unmeasurable; rollback is atomic across semantic and rendered checks, including through the CLI (`test_engine.py`). Visual protection of layers/overlays is reported unmeasurable |
| 16.8 | Durable idempotent retries, ID-content mismatch, revision checks, transaction owners and explicit merge conflict resolution | Requests/history, session locks; `request_status`; `propose_merge`/`resolve_conflict` | Verified subset: identical retries return the recorded outcome and mismatches fail. Retention is documented: the newest `request_retention` (default 1000) outcomes are kept, older ones become fingerprint tombstones (10×) that still replay as `already_committed` with `result_available: false` and still reject mismatches; forgotten identities are disclosed in results, `request_status` and `inspect --section requests`; records survive reopen and compaction, and unsaved interrupted work replays once (`test_request_retention_keeps_replay_and_mismatch_across_boundaries`, `test_interrupted_unsaved_work_replays_once_after_reopen`). Branches merge only through proposals with field-level three-way planning; conflicting edits require `resolve_conflict` (ours/theirs/custom) before acceptance (`test_conflicting_branch_edits_require_explicit_resolution`) |
| 16.9–16.10 | Versioned result/error envelope, all exit states, illustrated inspect/edit/check/preview/export | CLI/core error contracts; `docs/interface-parity.md`; `tests/test_interface_parity.py` | Verified subset: exit codes 0–7 are each produced through the CLI with structured JSON; library, CLI, MCP and REST return the same envelope or error code for the same inputs; the spec 16.10 sequence (capabilities, outline, context, dry-run, request-ID apply, stale retry, diff, affected render, export, conversion preview) runs through the CLI process; interface-specific persistence is documented |
| 17 | Persistent jobs/progress/cancel/remote uncertainty/recovery without resubmission; bounded staged batches; reproduction evidence/tolerance | `jobs.py` (`TRANSITIONS`, `run_batch`), publication reproduction, `fonts.glyph_fallbacks`; `tests/test_jobs_recovery.py`, `tests/test_fonts_reproduction.py` | Verified subset: every queued/running/completed/failed/cancelled/uncertain transition is validated and recorded; local cancellation is acknowledged immediately and states whether remote work was not sent, is unknown, stopped or continued; late results are kept but stale; staleness covers revision, profile and each source version; recovery queries registered lookups by recorded remote ID and never re-runs the worker, and retry of uncertain remote work requires explicit duplicate-risk acceptance; worker, pending-job, payload, batch memory and overwrite limits are configurable; batch results list every input outcome, output, losses, findings and failure. Reproduction records hash every font that shapes document text (raw tables plus file) per grapheme cluster, name changed fallback fonts and report renderable / dependencies-matched / reference-verified / drift / mismatch separately. Remote lookups exist only where a caller registers a provider-specific lookup; no shipped provider adapter offers one. Cross-platform reference drift is not physically verified |
| 18 | Explicit clean inclusion policies; sanitized copy removes all recoverable copies; actual serialization inspection | `publication.py` (`sanitize`, `save_sanitized`, `export_clean`, `inspect_residue`); `tests/test_publication.py`, `tests/test_publication_policy.py` | Verified subset: rebuild excludes review, tracked deletions, history, sources, transcripts, metadata, fields, layers and extension data; `preserve: [styles, layout, images]` keeps unrelated styles, scrubbed layout and images whose bytes are verified free of the terms, re-encoding pixels only when a term sits in image metadata. Clean publication can require `verify_absent` terms: the staged deliverable is inspected (ZIP/XML members with tags removed, JSON, HTML entities, RTF escapes, PDF streams) before it is published, and PDF text that cannot be decoded is reported `unverifiable`, never clean. The full scenario (DOCX with comment and author metadata → sanitized master → DOCX/ODT/PDF/MD publication) leaves the private master and original bytes unchanged. Text drawn in image pixels and selective preservation of review/history remain unsupported |
| 19 | Modular engine/layout/render/adapters/checks/providers/jobs; GUI not sole edit authority; incremental work outside UI | Module boundaries and canonical operations; `layout.update_document`; `tests/test_incremental_layout.py` | Partial: structural edits confined to text blocks update only the changed runs of the shared editor document; tables, images, lists, contents and style or geometry changes still rebuild. Page-preview resolution, export, save and autosave run on workers from model copies. History deltas record block order as splices, and snapshots follow accumulated delta size. Canonical transactions still deep-copy the semantic model on the UI thread, so the typing commit has a p95 of 148 ms on the 500-page fixture. Keep-rule breaks in the writing view are not recomputed incrementally; physical pages are resolved separately |
| 20 | Vixl canonical/schema/atomic/identity/history/inspection principles while supporting true word processing | Model/engine/storage/check contracts | Partial: foundational patterns retained; this does not establish complete required page-layout behavior |
| 21 | Offline local functions; explicit network; bounded untrusted archives/paths/processes; scoped disabled services; no secrets in outputs | Storage/adapters/providers/service restrictions, `transport_policy.PATH_KEYS`; `tests/test_security_acceptance.py`, `tests/test_service_boundary.py`, `tests/test_fuzz_import.py` | Verified subset: earlier hostile-archive/XML/JSON/HTML/raster/page-range/lock coverage plus: linked local files in HTML/ODT/DOCX are never read, nested archives are not expanded, macros/scripts/OLE objects are retained inert with losses, malformed XML is `invalid_input` while entity/DTD declarations stay `unsafe_xml`, and silent or hanging DOC converters fail with `adapter_failed`/`adapter_timeout` after the whole process group is killed. REST: token/origin/method/route/body/JSON errors, path scoping for every path-like argument (including nested batch, job and reproduction inputs and symlinks), the reserved `.byax` folder, loopback-only binding, no service or thread at import, and logs free of tokens and document text. Instruction-like document, Markdown and dictated text cannot cause network, provider or process activity through inspect/context/check/export/render/redact/MCP. Provider credentials do not appear in outputs, packages, job state, reproduction records, errors or logs, and offline local functions work with networking disabled. REST check-then-use path races (a symlink swapped after validation) are a documented residual risk; Windows service behaviour is unverified |
| 22.1 | Keyboard/screen reader/high contrast/scaling/dictation/semantic navigation and honest accessibility coverage | `gui_a11y.py`; `tests/test_accessibility.py`; [accessibility audit](accessibility-audit.md) | Partial (offscreen only). Every actionable control in the main window and 16 dialogs has an accessible name and a tooltip, verified by a widget-tree walk. Action tooltips name their shortcuts, and shortcuts are unique. The focus chain cycles through the workspace. Strings pass through `tr()`, with a pseudo-locale smoke test. Brand text pairs meet AA, control outlines now meet 3:1, and the brand theme yields to a platform high-contrast preference. NVDA and Orca runs, physical keyboard-only workflows, real high-contrast themes and 200% scaling are pending physical verification |
| 22.2 | Measured startup, typing p95, reflow p95, inspection, idle/cancellation; 100/500-page fixtures; explicit limits/progress | `scripts/generate_fixtures.py`, `scripts/benchmark.py` | Partial: the harness now also measures, through the real window, cold startup to a shown window, keystroke to paint, the typing commit, structural edit to paint, save blocking and completion, and idle CPU. The offscreen run is recorded below. No target-hardware or installed-desktop GUI performance claim, and cancellation is not measured |
| 24–25 | Explicit framework/OS/packaging/provider/license/extension decisions; retain `.byax` | `pyproject.toml`, `LICENSE`, compatibility matrix | Documented: implementation choices recorded; `.byax` collision absence is not a universal uniqueness guarantee |

## End-to-end scenario ledger (Section 23)

| Scenario | Current evidence | Remaining acceptance |
| --- | --- | --- |
| 23.1 Letter/resume/report/newsletter and recovery | Deterministic editable fixture save/reopen; PNG/PDF letter; failed atomic replacement preserves archive | GUI create/edit/print on both OSes; complete footnote/column crowded-page cases, anchored wraps and crash recovery |
| 23.2 Two-page music press kit | Original generated cover; semantic biography/release table/field; canonical date edit preserves attributes | Columns/footnotes/float overlays and geometry checks added; full press-kit GUI scenario, letterhead/numbering fidelity and biography reflow across difficult anchored objects remain |
| 23.3 Import/conversion | Simple multilingual TXT/MD/HTML/DOCX/ODT/RTF export/reimport; preserved original bytes | Representative historical DOC through installed adapter; tables/images/styles/review/non-Latin fidelity and every located loss |
| 23.4 History/review | Headless checkpoint/branch/GUI- and CLI-origin edits/switch/merge proposal/semantic diff/restore scenario; selective dependent proposals; grouped tracked changes; section moves with anchored comments and bookmarks; orphaned/unresolved states | The same sequence driven interactively through the desktop GUI on both platforms |
| 23.5 Checks/evidence | Limit-zero failures retained; incomplete editorial coverage disclosed | Native profile presets/exceptions and exclusive evidence validation now tested; full semantic evidence quality, changed-source invalidation and cross-interface strict publication scenario remain |
| 23.6 Transcription | Contract tests and local processing paths | Real mic direct/cleaned/document-aware session, manual edits, late cleanup conflict, names, recovery and retained segments |
| 23.7 Agents/concurrency | Dry run, atomic failure, ambiguous candidates, stale revision, durable retry and subprocess lock conflict | Full protected table/context/check/commit/diff/page preview workflow and instruction-like material adversarial audit |
| 23.8 Publication/reproduction | Actual sanitized ZIP members inspected; original untouched; render dependency inventory | Broader selected-inclusion/complex-layout publication cases and glyph-specific fallback font reproducibility; primary font hashes and actual pixel-reference comparisons now tested |
| 23.9 Platform/performance | Local benchmark harness records environment, fixture hashes, raw samples and measured pages | Installed Windows/Ubuntu desktop, assistive technologies and GUI typing/reflow/startup measurements on qualified hardware |

## Running and interpreting evidence

### Open-issue regressions — 13 October 2026

The latest `main` source (`657b121`) was fetched before this issue pass. Verification uses Python 3.12.14, Qt 6.12.0 and the offscreen platform on Linux 6.18.44 / glibc 2.41. These tests do not establish installed Windows 11 or Ubuntu 24.04 acceptance.

Before publication, the fixes were rebased onto current `main` (`42d1f87`). Final validation: **278 tests passed in 24.95 seconds**, including the loopback REST integration and subprocess locking tests. `ruff check src tests` and `git diff --check` passed. The newly merged creative authoring scripts have existing lint findings outside this gate. No live or billable model calls were made.

| Issue | Implementation and regression evidence | Status |
| --- | --- | --- |
| #47, editing stops after 1,000 revisions | Desktop compaction retains named checkpoints and branch heads, prunes unavailable undo states, and preserves pending editor text on failed flush. `test_1500_typing_bursts_reopen_and_checkpoint_survival` covers 1,500 bursts, save/reopen and undo/redo. Engine cap/branch and failed-flush tests cover policy boundaries. | Fixed in source; offscreen GUI verified |
| #49, settings disable protection/tracking | Generic settings cannot replace protections/requirements or disable tracking. Explicit `unprotect` / `set_tracking` require trusted desktop confirmation; a claimed GUI origin grants no authority. Restore cannot weaken retained policy. Engine, CLI, MCP and REST dispatch regressions preserve the document after rejected calls; a GUI test covers confirmation and cancellation. | Fixed in source; automated regressions verified |
| #50, MCP configures executable providers | Shared transport guard rejects provider administration and nested job submissions before writing state. Existing aliases remain callable; local CLI/desktop administration remains available. `test_mcp_and_rest_refuse_provider_administration_without_writes` verifies both transport policies. | Fixed in source; automated regressions verified |
| #51, default table rendering | Solid 1 pt borders; cells inherit the table/body named style before cell overrides. Default heading/title/list styles inherit body. A raster regression detects at least four distinct horizontal rules, verifies cell font/size and local overrides, and exports the same table to PDF. | Fixed in source; shared editor/preview/export layout verified |
| #54, non-ASCII RTF control scanner failure | Only ASCII letters enter the control-word parser; unmatched words produce structured `invalid_input`. The reported example and 200 seeded RTF mutations produce valid documents or `ByaxError` results. | Fixed in source; importer/CLI regressions verified |
| #55, lock-file sidecars | Stable hashed locks live in the per-user cache, with no sidecars beside new/apply/export/render outputs. Existing subprocess lock-conflict tests still pass on Linux. Restart older versions using sidecar locks before upgrading. | Fixed in source; Windows lock-conflict execution remains for Windows CI |
| #48, history/save overhead | Nonstructural deltas omit unchanged block order, and GUI edits do not create unused replay identities. Legacy full-order deltas still restore correctly. **14 Oct:** order changes are stored as splices, snapshots follow accumulated delta size, and commits diff against cached recent states instead of replaying the chain. Desktop commits keep no request replay record; other callers use the shared retention policy (1,000 full results plus fingerprint tombstones). Saving verifies the written bytes instead of re-parsing the package, and save and autosave run on a worker. On the issue's 300-paragraph reproduction with 800 edits, history fell from 16.5 MB to 1.96 MB, `save_document` from 1.18 s to 0.12 s, and `flush_edits` stays flat at 15–18 ms. `tests/test_history_save.py` covers bytes per edit against block count, splices, snapshot policy, request retention, and non-blocking, queued and close-waiting saves. | Fixed in source; offscreen GUI verified; installed-desktop timing remains |
| #56, CI quality gates | Ubuntu and Windows CI run `ruff format --check .`, `ruff check src tests`, an advisory `mypy` step over `engine.py`, `storage.py` and `cli.py` (`[tool.mypy]` in `pyproject.toml`), and pytest with `--cov=byax --cov-fail-under=70` (local total 71%). `engine._operation` is a handler registry; `cli._execute` dispatches through a per-command `_HANDLERS` table and no `cli.py` function exceeds 100 lines. Public `cli.py`/`storage.py` functions and the public `engine` entry-point signatures are annotated. `tests/test_fuzz_import.py` runs 1,550 seeded byte and in-package mutations over RTF/DOCX/ODT/HTML/MD/TXT (about 6 s) and asserts only `ByaxError`; it found and now guards DOCX missing-part `KeyError`/`AttributeError` and corrupt ZIP member CRC/version failures. `tests/test_gui_soak.py` runs a bounded 300-edit open/type/split/delete/save/undo/redo soak. | Partial: `engine.apply` (120 lines) and `engine.context` (102 lines) still exceed 100 lines; mypy reports 3 advisory `engine.apply` body findings; Windows CI execution of the new gates is pending a CI run |

The remaining platform, physical printer/microphone, screen-reader, live provider and complete specification scenario issues remain open. This pass makes no claims about their acceptance.

On 13 October 2026, `tests/test_acceptance.py` completed with **22 passed** on the Linux development container in 0.69 seconds. This is cross-module automated evidence, not a claim that the full Section 23 GUI/platform scenarios passed.

```bash
uv sync --extra dev
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_acceptance.py
uv run python scripts/generate_fixtures.py --out artifacts/fixtures
QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark.py --out artifacts/benchmark.json
```

Fixture source is deterministic and MIT licensed. PNG cover art is generated locally from basic geometry. Explicit page breaks stabilize workload size; they do not validate automatic break selection. Benchmark `model_edit` is a canonical transaction timing, **not** keystroke-to-paint feedback. Full reflow timing is **not** incremental affected-region GUI reflow. Unmeasured targets must remain unclaimed even when proxy metrics are fast.

## Recorded GUI benchmark — 14 October 2026

Measured at `2026-10-14T02:53:59Z` with `scripts/benchmark.py`, all defaults: 20 samples, 5 startup launches, 10 s idle and 1,000 typing edits before the save samples. The host was `Linux-6.18.44-x86_64-with-glibc2.39`, an Intel Xeon Processor @ 2.80 GHz with 4 logical CPUs and 15 GiB RAM, running Python 3.13.16 and Qt 6.12.0 on the **offscreen** platform. The host is a shared, unqualified development container: there is no compositor, GPU or installed desktop, and the storage type is unverified. No providers were configured. All p95 values use nearest rank; startup has 5 samples and save has 5.

| Metric (ms unless noted) | mixed_100 median / p95 | long_500 median / p95 | Spec target |
| --- | ---: | ---: | --- |
| Cold start to shown, exposed, painted window with the fixture | 927 / 963 | 1,057 / 1,140 | 3,000 |
| Keystroke to synchronous editor repaint | 4.6 / 8.5 | 7.6 / 12.4 | 50 |
| Typing commit (`flush_edits`, 300 ms after typing pauses) | 32.1 / 35.9 | 108.9 / 147.5 | — |
| Structural edit (paragraph style) to repaint, incremental | 50.0 / 56.8 | 158.5 / 195.1 | 500 |
| Same edit with a forced full document rebuild | 470.6 / 529.6 | 731.7 / 768.3 | — |
| `save()` holding the UI thread after 1,000 edits | 13.2 / 51.6 | 33.3 / 61.4 | ≈50 (#48) |
| Save completion on the worker | 155.8 / 222.6 | 351.9 / 429.7 | — |
| Full physical page reflow proxy (now on a worker in the GUI) | 1,040 / 1,186 | 1,160 / 1,350 | — |
| Model edit transaction | 24.6 / 39.8 | 72.4 / 132.2 | — |
| Idle CPU over 10 s with the document loaded | 0.04 % | 0.04 % | — |
| History JSON after 1,000 edits | 1.45 MB | 3.64 MB | — |

Keystroke, structural-edit and startup values are below their targets on this host. Save blocking stays below 50 ms at the median, but the slowest of five samples reached 52 ms and 61 ms while the worker started serializing and competed for the interpreter lock. The typing commit is not keystroke feedback, because it runs after input pauses. On the 500-page fixture it still exceeds 100 ms because each canonical transaction copies the semantic model. Offscreen paint excludes compositor and GPU time, so these numbers do not establish installed Windows 11 or Ubuntu 24.04 performance on reference hardware. Cancellation is not measured.

## Recorded local benchmark — 13 October 2026

Measured at `2026-10-13T16:43:35.228087+00:00` on `Linux-6.18.44-x86_64-with-glibc2.41`, AMD EPYC 9V45 96-Core Processor, 5 exposed logical CPUs, Python 3.12.14, Qt 6.12.0, offscreen platform. RAM, SSD and physical target-machine identity are not verified; this is a shared development container with uncontrolled concurrent load, not platform qualification. No providers were configured or called. Each metric has 20 sequential samples; p95 uses nearest rank.

| Fixture | Actual pages | Blocks | Text characters | Model edit p95 | Full physical reflow p95 | Selected inspection p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mixed_100 | 100 | 379 | 87862 | 29.38 ms | 608.38 ms | 0.031 ms |
| long_500 | 500 | 1499 | 861392 | 107.82 ms | 549.96 ms | 0.038 ms |

Full physical reflow includes shared resolved display pages, section layout and font coverage checks after a paragraph insertion. Both full-layout proxy measurements exceed 500 ms in this run; incremental affected-layout latency remains unmeasured. These results do not establish GUI startup, visible typing latency, idle behavior, cancellation or remote-model performance. Qt emitted OpenType diagnostics for a fixture script; font fallback and complex-script visual quality still need platform review. An unrenderable scalar produces a located missing-glyph finding (security regression test).

The completed full automated suite on this development container reported **218 passed in 3.48 seconds** after integration of section/table operations, threaded review, numbering, proof output and REST rendering and cross-platform CSV soft breaks. This includes 22 cross-module acceptance tests and 22 additional hostile-input/GUI-state security regressions; it does not mean all Section 23 product scenarios passed. All seven fixtures were regenerated with the current model/font defaults and deterministic identities.

<details>
<summary>Recorded environment, fixture hashes, definitions, and raw timing samples</summary>

```json
{
  "schema_version": 1,
  "measured_at_utc": "2026-10-13T16:43:35.228087+00:00",
  "environment": {
    "platform": "Linux-6.18.44-x86_64-with-glibc2.41",
    "machine": "x86_64",
    "processor": "AMD EPYC 9V45 96-Core Processor",
    "logical_cpus": 5,
    "python": "3.12.14",
    "engine": "0.1.0",
    "qt": "6.12.0",
    "qt_platform": "offscreen",
    "hardware_qualification": "Unqualified execution host; CPU/SSD target hardware not verified."
  },
  "provider_configuration": "None; local deterministic operations only.",
  "definitions": {
    "p95": "Nearest-rank percentile of wall-clock sample durations.",
    "model_edit": "Canonical candidate transaction against original loaded fixture; includes validation/history; excludes GUI input and paint.",
    "full_reflow": "After a canonical paragraph insertion, construct shared QTextDocument and resolve all physical display pages; includes full layout, excludes paint and the edit itself.",
    "inspection": "Bounded semantic inspection against an already loaded document.",
    "fixture_load": "Fixture construction excluded from every timed operation."
  },
  "not_measured": [
    "Cold GUI startup to usable window",
    "Keystroke-to-visible-feedback latency",
    "Incremental affected-region reflow",
    "Idle CPU activity",
    "Cancellation acknowledgement",
    "Physical Windows 11 / Ubuntu 24.04 desktop behavior",
    "Remote provider latency"
  ],
  "fixtures": {
    "mixed_100": {
      "semantic_sha256": "4c003481739cd3fdc1fadff4fa25368b82142c2607efece7407aa67a5db4c9b3",
      "blocks": 379,
      "text_characters": 87862,
      "requested_pages": 100,
      "measured_pages": 100,
      "layout_findings_count": 0,
      "pagination": "Explicit page breaks",
      "metrics": {
        "model_edit": {
          "samples": 20,
          "median_ms": 19.07750149985077,
          "p95_ms": 29.37829700022121,
          "min_ms": 18.654770000011922,
          "max_ms": 29.578148999917175,
          "raw_ms": [
            19.976571000370313,
            19.042613999772584,
            18.8796289999118,
            18.79905699979645,
            18.879187000038655,
            28.396918999987975,
            26.724303000264626,
            29.578148999917175,
            29.37829700022121,
            27.264442999694438,
            25.084436999804893,
            18.79735400007121,
            18.720408999797655,
            18.721860999903583,
            18.654770000011922,
            19.112388999928953,
            19.222944999910396,
            18.89728500009369,
            18.845967999823188,
            26.240029000291543
          ]
        },
        "full_reflow": {
          "samples": 20,
          "median_ms": 523.0921739998848,
          "p95_ms": 608.3783520002726,
          "min_ms": 512.0212199999514,
          "max_ms": 611.2988560003032,
          "raw_ms": [
            548.9963029999672,
            512.0212199999514,
            611.2988560003032,
            522.3624380000729,
            608.3783520002726,
            522.3208610000256,
            541.8240199996944,
            553.402473999995,
            534.6497460000137,
            567.2954920000848,
            513.9550609997059,
            519.186985000033,
            525.3776740000831,
            517.8085080001438,
            526.1801229999037,
            523.8219099996968,
            522.3503839997647,
            514.3391670003439,
            517.9262239998934,
            516.4681629998995
          ]
        },
        "inspection": {
          "samples": 20,
          "median_ms": 0.016830500044306973,
          "p95_ms": 0.03098700017289957,
          "min_ms": 0.016575000245211413,
          "max_ms": 0.0638960000287625,
          "raw_ms": [
            0.0638960000287625,
            0.020420999589987332,
            0.01862799990703934,
            0.017286000002059154,
            0.016806000076030614,
            0.017255999864573823,
            0.016814999980852008,
            0.016846000107761938,
            0.01676499960012734,
            0.016725000023143366,
            0.016635000065434724,
            0.016665000202920055,
            0.016715000128897373,
            0.016575000245211413,
            0.03098700017289957,
            0.01701600012893323,
            0.01665499985392671,
            0.01665499985392671,
            0.01687600024524727,
            0.016975000107777305
          ]
        }
      },
      "target_comparison": {
        "typing_50ms": "not_measured: model transaction excludes GUI feedback",
        "reflow_500ms": "full reflow proxy only; affected-region GUI feedback unmeasured",
        "inspection_500ms": true
      }
    },
    "long_500": {
      "semantic_sha256": "d5e58786dad2929b08a724591d9e08c6b435a6e2847012cae84d124625e49b7c",
      "blocks": 1499,
      "text_characters": 861392,
      "requested_pages": 500,
      "measured_pages": 500,
      "layout_findings_count": 0,
      "pagination": "Explicit page breaks",
      "metrics": {
        "model_edit": {
          "samples": 20,
          "median_ms": 96.48987499986106,
          "p95_ms": 107.81782399999429,
          "min_ms": 94.71789099961825,
          "max_ms": 107.85395799985054,
          "raw_ms": [
            97.07794599989938,
            107.10326700018413,
            95.39984899993215,
            95.02990100008901,
            94.71789099961825,
            95.16305200031638,
            107.81782399999429,
            98.53695399988283,
            97.21840800011705,
            96.42128699988461,
            96.54177799984609,
            107.85395799985054,
            97.42557000026864,
            95.72342499996012,
            95.65679500019542,
            95.7788289997552,
            107.50962900010563,
            96.43797199987603,
            95.3168139999434,
            100.82759400029317
          ]
        },
        "full_reflow": {
          "samples": 20,
          "median_ms": 520.3456470001129,
          "p95_ms": 549.959243000103,
          "min_ms": 498.0706179999288,
          "max_ms": 560.6256800001574,
          "raw_ms": [
            498.0706179999288,
            524.3130240000937,
            504.22690799996417,
            499.91405999981,
            509.3968789997234,
            560.6256800001574,
            549.959243000103,
            545.5421909996403,
            520.4846560000078,
            546.9709859999057,
            520.2066380002179,
            501.4592089996768,
            523.9482649999445,
            510.96506200019576,
            515.664384000047,
            515.5281279999144,
            517.9058999997324,
            522.4928620000355,
            526.1389759998565,
            526.3790779999908
          ]
        },
        "inspection": {
          "samples": 20,
          "median_ms": 0.03561349990377494,
          "p95_ms": 0.037586999951599864,
          "min_ms": 0.035242999729234725,
          "max_ms": 0.1730009998937021,
          "raw_ms": [
            0.1730009998937021,
            0.037586999951599864,
            0.03612399996200111,
            0.03577400002541253,
            0.03547400001480128,
            0.03621499990913435,
            0.03609399982451578,
            0.035553999623516575,
            0.035803999708150513,
            0.03550399969753926,
            0.03557299987733131,
            0.03598400007831515,
            0.03533300014169072,
            0.03560400000424124,
            0.03554300019459333,
            0.03537400016284664,
            0.035242999729234725,
            0.036294999972597,
            0.03562299980330863,
            0.03542400008882396
          ]
        }
      },
      "target_comparison": {
        "typing_50ms": "not_measured: model transaction excludes GUI feedback",
        "reflow_500ms": "full reflow proxy only; affected-region GUI feedback unmeasured",
        "inspection_500ms": true
      }
    }
  }
}
```

</details>


## File-view evidence — 14 October 2026

`tests/test_file_views.py` verifies:

- Every export format/alias resolves to one registry entry, and all model block types are classified.
- Pure, deterministic projection preserving editable block identities and master state; loss parity with conversion preview.
- Golden adapter loss reports captured from `66ba944` remain unchanged by the registry refactor. Markdown preview now also reports normalization that previously appeared only during export.
- Source text equals actual exporter bytes for TXT, Markdown, HTML and RTF. Non-native output policies are applied identically to views and all six editable exporters.
- Heading/text/explicit-mark round trips for TXT, Markdown, HTML, DOCX, ODT and RTF; DOCX custom heading names now retain heading semantics. This is a bounded corpus, not exhaustive proof of every table/image/style attribute in every adapter.
- View switches preserve revision, undo and visible caret identity; seeded localized edits preserve hidden blocks/marks; joins across hidden blocks are blocked; partial and complete selection deletion preserves hidden content and surviving marks; simplified tables are locked; TXT heading splits produce body text and retain tracking.
- Markdown imports are clean until edited, direct-file editing is an explicit opt-out, the chooser remembers its choice, and direct-file mode locks the selector and preserves original source bytes.
- Export offers are untimed, suppressed per document or globally, and source mode is read-only. CLI/MCP view calls do not modify the master. New controls/dialogs are included in the accessibility tree audit.
- System/Light/Dark appearance is a local preference; dark text/control contrast is measured and changing appearance does not mutate authored content.

`tests/test_backlog_workflows.py` adds evidence for inherited character styles with direct overrides, explicit list identities/restarts, shared authored/output/selection statistics, collection import/conflict preservation, authorized local dependency retrieval boundaries, GUI style inspection and branch-to-merge-proposal workflows. These supplement, rather than replace, the existing feature rows above.

Remaining file-view acceptance: exhaustive per-feature/format round trips (especially images, advanced tables, named-style variations and complex Markdown), randomized *structural* edit sequences, and complete capability gating of every advanced command. Physical screen readers and target-application layout remain unverified. P1 (replacement of imported originals) is deliberately not enabled; exports suggest a sibling filename. Editable source, remembered export targets, `apply --view` and new formats remain Phase 3 work.

The complete open-issue audit is in [issue-audit-2026-10-14.md](issue-audit-2026-10-14.md). It records remaining implementation work as well as external acceptance prerequisites; passing CI must not close the umbrella completion issue.

Additional backlog evidence: `test_backlog_workflows.py` exercises native export-policy creation and collection/storage round-trips, staged DOCX publication, master region editing with artwork preservation and undo, section-profile override preservation/removal, and layer geometry/grouping/output policies. `test_file_views.py` checks the registered inline capabilities against all six import/export adapters, ODT direct spacing, and hidden-content randomized tail insert/delete sequences across all seven export views. These are bounded regressions, not the complete nine-scenario or physical-platform acceptance suite.


## Manuscript projects (competitiveness plan A1)

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| Binder inside one `.byax` (decision 1) | Optional `project` binder bound to heading IDs; kinds part/chapter/scene/note/front/back, status and authored metadata; text stays in the body; schema 3 with a 2→3 transition | `tests/test_project.py`, `tests/test_migration.py` (schema-2 fixture) |
| Canonical operations with revision preconditions and anchor remapping | `project_move`, `project_split`, `project_merge`, `project_set_status`, `project_set_target`, `project_add_item`, `project_remove_item`, `project_set_meta`, `project_init` through `engine.apply`; dry run, request IDs, proposals and history restore | `tests/test_project.py` |
| Split, merge and move preserve every comment, bookmark and tracked-change anchor | Seeded randomized loops (30 seeds × 25 operations) check presence, targets, body-stream positions and anchored text; rejected operations leave the document unchanged | `tests/test_project_properties.py`. A merge with a pending tracked change in the removed heading is refused; comments on a removed heading move to the seam as `needs_review` |
| Headings edited outside binder operations | Orphaned items are kept and reported in `apply` results (`project_orphans`) and by `project.reconcile`; rebind or remove explicitly | `test_headings_removed_outside_binder_operations_are_reported_not_dropped` |
| Notes excluded from compile by default | `project.compile_plan`; exclusion inherits to descendants; `include_in_compile` overrides | `test_notes_are_excluded_from_compile_by_default_and_exclusion_inherits`; the B2 compile pipeline uses it (see [Compile pipeline](#compile-pipeline-competitiveness-plan-b2)) |
| Round trip `.byax` → DOCX/Markdown → import keeps binder order as headings, with a loss report | Located `binder_metadata` losses per item heading; import restores heading order and levels; `project_init` rebuilds the binder | `test_docx_and_markdown_round_trip_binder_order_with_located_losses` |
| 100,000-word project opens, reorders a chapter and saves | `project_100k` fixture (103,681 words, 155 items); `scripts/benchmark.py --project` | `test_100k_word_project_opens_reorders_a_chapter_and_saves` (generous 30 s bound); measurements in [benchmarks/0.3-project.json](benchmarks/0.3-project.json) on an unqualified offscreen host (16 October 2026): engine p95 open 17 ms, chapter reorder 97 ms, save 87 ms (20 samples); real window with reduced samples (10): keystroke to paint 21 ms against the 50 ms target and structural edit to paint 162 ms against 500 ms. Paint after a binder move through the Binder panel is measured in [benchmarks/0.3-project-binder.json](benchmarks/0.3-project-binder.json) (see Binder views below) |
| CLI, MCP, REST parity | `byax project --action …` dispatched through `cli.execute`; inventory rows | `tests/test_interface_parity.py`, `test_cli_actions_dispatch_through_execute_and_mcp`. The GUI is the Binder panel, goals and drafts dialogs described below |

## Binder views, goals and drafts (competitiveness plan A2, A3, A4)

All GUI evidence below runs offscreen (`QT_QPA_PLATFORM=offscreen`): it exercises the real widgets, key events and drop events, not a compositor, a screen reader or a physical drag.

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| Outline view: indented list with title, kind, status, words and target progress | Binder panel in either sidebar (selector, **Binder** menu, Ctrl+Alt+B); rows are drawn from `project.items`; one panel serves both sidebars | `tests/test_gui_binder.py::test_outline_shows_title_kind_status_words_and_progress`, `test_binder_is_a_sidebar_panel_with_an_empty_state_that_creates_the_binder`, `test_the_panel_moves_to_the_sidebar_that_selects_it` |
| Corkboard: cards with title, synopsis, status, words, target | Cards for every scene and note, or for the items inside one chapter or part; cards share the viewport width | `test_corkboard_cards_show_synopsis_status_words_and_target` |
| Drag to reorder calls `project_move` through `engine.apply`; keyboard equivalents are mandatory | Drops and Alt+Up/Down (earlier, later, across chapter ends) and Alt+Left/Right (outdent, indent) become the same operation lists (`project.plan_drop`, `project.plan_step`: one `project_move` plus heading-level changes) sent through `commit`; the Binder menu, panel button and context menu offer every step | `test_alt_arrow_keys_move_and_indent_items_in_the_outline`, `test_dropping_an_item_before_after_or_into_another_reorders_through_the_engine`, `test_cards_reorder_by_dropping_before_or_after_another_card`, `test_keyboard_moves_are_one_undoable_revision_each`, `tests/test_binder_plan.py`, and `tests/test_binder_plan_properties.py` (30 seeds × 25 random steps and drops keep every block, comment, reply, bookmark and tracked change). Drops are delivered as `QDropEvent`s carrying the binder's mime data; the operating system's drag gesture itself (`QDrag.exec`) is not exercised |
| Synopsis is authored text or empty, edited inline | Detail field and in-place card editor save through `project_set_meta`; empty text clears it; Escape cancels | `test_synopsis_is_edited_in_the_detail_field_and_empty_text_clears_it`, `test_synopsis_is_edited_in_place_on_a_card`, `test_escape_cancels_a_card_edit` |
| "Draft a synopsis" is an opt-in proposal that needs a configured provider | Disabled without a text provider; asks first (remote providers show the exact payload); the answer is a pending `propose` of one `project_set_meta`; scene text is context, never the instruction; refused if the document changed meanwhile. CLI: `project --action draft-synopsis` | `tests/test_project_synopsis.py`, `test_drafting_a_synopsis_needs_a_configured_provider`, `test_a_drafted_synopsis_is_a_pending_proposal_never_applied_silently`, `test_a_synopsis_is_not_added_when_the_document_changed_meanwhile`. Providers are mocked; no live model was called |
| Click an item to navigate the editor to its heading; split and merge from the binder | Click or Enter shows the heading; **Split at editor cursor…** and **Merge with next item** call `project_split`/`project_merge` | `test_clicking_or_pressing_enter_shows_the_heading_in_the_editor`, `test_split_at_the_editor_cursor_creates_a_new_scene_and_keeps_comments`, `test_merge_with_the_next_item_asks_first` |
| Accessible names, tooltips; passes the widget-tree walk | Every actionable control and action has a name and tooltip; shortcuts are unambiguous | `test_binder_controls_have_names_tooltips_and_shortcuts`, `tests/test_accessibility.py` (walks the window including the panel). No screen-reader testing is claimed |
| Binder-only edits do not rebuild the editor text | `project` is not a layout input, so status, target and synopsis changes keep the editor document | `test_binder_only_edits_do_not_rebuild_the_editor_document` |
| 100,000-word project opens in the GUI binder and reorders within the targets | `project_100k` (155 items) opens in the panel and reorders by keyboard and by drop; `scripts/benchmark.py --project --project-only` records the Binder metrics | `test_a_100k_word_project_opens_in_the_binder_and_reorders_within_the_structural_target` (loose bounds so a slow runner does not flake). Measured 16 October 2026 on an unqualified offscreen host (10 samples; Binder metrics 5): reorder to paint by keyboard 161 ms median / 199 ms p95 and by drop 136 / 140 ms against the 500 ms structural target; Binder rebuild 29 ms; synopsis save to paint 156 ms; keystroke to paint 10 / 14 ms against 50 ms ([benchmarks/0.3-project-binder.json](benchmarks/0.3-project-binder.json)) |
| Per-document, per-item and per-session word targets; daily goals; streaks | Document and item targets are binder fields; daily and session goals are local preferences; streak counts days meeting the goal (any writing without one). Dialog, status-bar button | `tests/test_goals.py`, `tests/test_gui_goals_drafts.py::test_the_status_bar_button_follows_the_session_and_the_daily_goal`, `test_the_goals_dialog_sets_local_goals_and_the_document_target` |
| Statistics reuse the shared counters | Progress uses `statistics.word_count` (the definition `counts` uses); `--format` uses the publication projection | `test_progress_uses_the_shared_authored_counter_and_binder_targets` |
| Session records are local preferences and never enter history | `goals.json` in the user config directory keyed by document identity; recording changes no revision, history or file | `test_session_records_never_touch_the_document_or_its_history`, `test_sessions_are_stored_outside_the_document_and_its_history`, `test_opening_another_document_stores_the_finished_session`, `test_an_unreadable_goals_file_does_not_stop_writing` |
| `goals` CLI, parity and transport policy | `status`, `history` are read-only everywhere; `set-daily`/`set-session` are refused over MCP and REST as local preferences (REST reads from the service-owned folder) | `test_cli_status_history_and_local_goal_setting`, `test_transports_may_read_goals_but_not_write_local_preferences`, `test_rest_reads_goals_from_the_service_owned_folder_and_refuses_writes`, `tests/test_interface_parity.py` |
| Named drafts: save with a note, switch | `drafts` stores the note in `history.drafts` over an ordinary branch head and fork point; the working draft continues; survives save, reload and history compaction | `tests/test_drafts.py::test_saving_a_draft_freezes_the_state_and_keeps_working_on_the_current_draft`, `test_drafts_survive_save_and_reload_and_history_compaction`, `test_malformed_draft_records_are_rejected_by_validation` |
| Compare two drafts at scene level; bring changes as a proposal | Per binder item `unchanged`/`changed`/`added`/`removed` with word deltas, fields, moves and `compare.compare` details; dialog with details; **Bring changes from draft…** is `propose_merge` | `test_compare_reports_each_scene_as_unchanged_changed_added_or_removed_with_word_deltas`, `test_compare_notices_moved_scenes_and_text_outside_the_binder`, `test_bring_changes_is_a_reviewable_proposal_and_changes_nothing_until_accepted`, `tests/test_gui_goals_drafts.py` (compare, drafts and save dialogs, bring). The interface says draft and compare; the old branch/merge menu labels were renamed |
| Drafts through CLI, MCP and REST | `byax drafts` (`list`, `save`, `switch`, `compare`, `bring`) through `cli.execute` | `test_cli_drafts_list_save_switch_compare_and_bring`, `test_drafts_are_available_over_mcp`, `tests/test_interface_parity.py` |

Limits: drafts do not yet support rename or delete (history retention keeps every saved head); comparison is keyed by binder item identity, so scenes without a binder are compared as a whole; goals count authored words as growth only and do not detect words that were pasted in.

## Manuscript corpus and codex (context engine 8.1, 8.2)

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| One project index shared with the binder (CP decision 6) | `project_index.ProjectIndex`: one row per text object with stable block ID, binder item, chapter and kind from `project.block_map`/`item_for_block`; index schema 2 conventions (porter + trigram + vocab) and the shared query syntax | `test_chunks_carry_stable_block_ids_and_binder_metadata`, `test_find_across_documents_with_filters_pagination_and_suggestions` |
| Project = open document's folder plus series members; backups, syncing files and same-identity copies skipped | `project_index.members` | `test_membership_skips_backups_hidden_files_and_copies_of_the_same_document`, `test_series_documents_join_the_project` |
| Incremental by block hash on save and explicit refresh; derived index in the user cache only | Unchanged blocks are not rewritten; siblings reparse on size/mtime change; departed documents drop out; a cache inside the project is refused; the desktop updates an existing index after save | `test_refresh_is_incremental_by_block_hash_and_tracks_unsaved_edits`, `test_index_lives_in_the_cache_never_in_the_project`, `test_saving_updates_an_existing_project_index_only` |
| Optional vectors | `rebuild_vectors(provider)` and `search(method='hybrid', provider=...)` through `vector_store`, only with an explicit provider; keyword fallback with a notice | `test_optional_vectors_are_built_only_with_an_explicit_provider` (fake provider). No CLI/desktop switch for project embeddings yet |
| Saved searches that update themselves | `settings.saved_searches` through `saved_search_set`/`saved_search_remove`; results computed at run time | `test_saved_searches_are_settings_and_results_are_live`, `test_cli_saved_search_round_trip_and_links` |
| Backlinks and unlinked mentions for headings, bookmarks and codex entries | Cross-references and codex mentions; plain-text title occurrences and codex near misses (case, excluded phrase) across the project | `test_backlinks_and_unlinked_mentions` |
| Codex entries in the document model, undoable and versioned | Optional `codex` in schema 3 (2→3 transition rejects a conflicting `codex`); `codex_*` canonical operations with created/updated dates; proposals target entries with hash preconditions | `test_operations_create_edit_and_remove_entries_with_dates_and_history`, `test_invalid_codex_operations_are_rejected_atomically`, `test_schema_2_documents_with_an_unknown_codex_are_not_upgraded` |
| Aho-Corasick mention detection | Pure-Python automaton (pyahocorasick when installed); word boundaries, case sensitivity, excluded phrases, leftmost-longest, selection ± nearby blocks and whole-manuscript heatmap | `test_mentions_respect_boundaries_case_exclusions_and_longest_match`, `test_pure_python_automaton_matches_a_naive_search`, `test_heatmap_counts_mentions_per_binder_item` |
| Codex mention precision and recall measured | *Pride and Prejudice* chapters I–VI (7,694 words, 268 labelled name-form mentions): precision 1.0, recall 1.0 with aliases; recall 0.53 with names only | `test_mention_precision_and_recall_on_the_novel_fixture`; [benchmarks/0.3-codex.json](benchmarks/0.3-codex.json). Labels are rule-generated and hand-reviewed name forms (no pronouns or descriptions), so the figure measures the matcher, not coreference |
| Project keyword search under 100 ms p95 at 100,000 words | Slowest query of ten 8.2 ms p95 on `project_100k` (103,681 words, 518 rows); build 119 ms, no-op refresh 2 ms, one-paragraph update 32 ms, whole-manuscript mention heatmap about 100 ms | `test_published_project_search_latency_meets_the_keyword_target`; [benchmarks/0.3-codex.json](benchmarks/0.3-codex.json) on an unqualified host (16 October 2026), not a reference laptop |
| Hide from AI at entry and trait level | `ai_visible_entries`, `ai_context`, `redact_for_ai` applied in the assistant, model review and `byax context`; `entity` and default `codex` reads omit hidden data; MCP/REST refuse `include_hidden` | `test_ai_views_never_include_hidden_entries_or_traits`, `test_cli_context_and_transport_tools_hide_codex_data`, `test_gui_assistant_context_is_redacted` |
| The assistant only proposes codex facts | `codex.propose_fact` → `propose` with provenance; accept/reject through review; stale when the entry changed | `test_assistant_facts_become_review_proposals_with_provenance` |
| Series sharing (C2) | `.byaxseries` codex, libraries and documents; document entries and attachments win; attach is local-only | `test_series_codex_and_libraries_are_shared_with_document_precedence`, `test_series_files_are_validated` |
| Name-spelling check uses the codex | `codex.spelling_names` registered with `fiction_checks.register_name_source` | `test_codex_names_feed_the_name_spelling_check` |
| CLI, MCP (`byax_project_search`, `byax_entity`), REST parity | `project-search`, `codex`, `entity` through `cli.execute`; inventory rows | `test_cli_mcp_and_rest_return_the_same_project_results`, `tests/test_interface_parity.py` |
| Codex panel and project find in the desktop | Tools → Codex… (entries, edit dialog with aliases/traits/hide-from-AI, mentions, heatmap with text counts); Edit → Find in project… (results, saved searches, backlinks) | `tests/test_gui_codex.py` (offscreen); physical desktop and screen-reader use unverified |

## Sync-folder safety (competitiveness plan 5.3)

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| Detect cloud-synced folders | Provider, confidence and evidence from marker files, provider environment variables, Dropbox `info.json` and folder-name patterns; local only | `tests/test_sync_safety.py` (patterns for nine providers, markers, environment, no network). The Windows registry is not read; detection is a hint, and installed-client behavior on macOS and Windows is unverified |
| Risky patterns | Placeholders, empty/incomplete files, leftover staging files, backup and other-application lock files, lock or recovery folders inside a synced folder, large package/history, open in another session | `tests/test_sync_safety.py`; open-file blocking is detectable only on Windows and is untested there |
| Conflicted copies | Dropbox, OneDrive, iCloud, Google Drive/Box, Nextcloud, ownCloud and Syncthing names; offered only if the manifest `document_id` matches; unfinished files wait | `tests/test_sync_safety.py`; names follow English-language client defaults, other locales are not recognised |
| Merge through review | Copy imported as a branch (`engine.import_branch`) and merged with `propose_merge`; different sections merge with no loss, same section conflicts and resolves with `resolve_conflict`; copy never deleted; explicit archive after an accepted merge | `test_edits_to_different_sections_merge_with_no_loss_through_review`, `test_edits_to_the_same_section_conflict_and_resolve_explicitly`. Comments and review items in the copy are reported as not merged; needs shared retained history |
| Partial syncs and delayed appearance | Truncated, zero-byte, mixed and bit-flipped packages, `.icloud` placeholders, a file replaced after open (`expected_stamp`), failed or interrupted atomic replace | `tests/test_sync_partial.py`, `tests/test_gui_sync.py`; no real sync client was run |
| GUI | Non-modal notice bar, "Review conflicted copy…", Tools entries, accessible names | `tests/test_gui_sync.py` (offscreen); physical screen-reader pass unverified |
| Interfaces | `sync` through CLI, MCP and REST with path scoping | `tests/test_sync_safety.py`, `tests/test_interface_parity.py` |

## Word review round trip (competitiveness plan 5.1, C1)

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| Review package | `review-package` / `docx_review.create_review_package`: pins the revision for peer `review:<id>`, records `metadata.review_packages`, saves, then writes a deterministic DOCX with Track Changes on, optional `w:documentProtection` (tracked changes or comments), custom-property ID, identity bookmarks, `w14:paraId`, rsid and the writer's open comment threads | `test_review_copy_is_deterministic_and_carries_identity_tracking_and_threads`, `test_restriction_choices`, `test_create_pins_the_snapshot_records_the_package_and_reports_losses` |
| Proposals with correct ranges | Tracked insertions, deletions, replacements, paragraph insertions, splits, whole-paragraph deletions, formatting, table cells, a moved paragraph and an alignment change each become one proposal with exact operations and the snapshot range | `test_word_round_trip_maps_every_revision_to_a_proposal_with_exact_ranges_and_attribution`, `test_merging_adds_pending_proposals_only_and_accepting_them_gives_the_editors_text`, `test_a_moved_paragraph_keeps_its_identity_even_when_word_drops_its_bookmark` |
| Attribution | `w:author` and `w:date` on each proposal (`author`, `review_source.changes[].authors/dates/revision_ids`); the proposal author is the edit's attribution on acceptance; untracked edits are attributed to the file's last author and marked untracked | Same tests, `test_untracked_edits_are_diffed_against_the_snapshot_and_proposed` |
| Comments, replies and done state | Ranged comments, `commentsExtended` threads (`paraIdParent`), `commentsIds` durable IDs and done state become comment threads; replies to the writer's comments join them; returned copies of the writer's comments are not duplicated | `test_word_comments_replies_and_done_state_become_threads_on_the_right_ranges`, `test_ordinary_docx_export_and_import_keep_comment_ranges_threads_and_dates` |
| Writer's later edits preserved | Three-way: snapshot = base, returned = theirs, document = ours. Ranges move with your edits; overlapping changes are flagged `conflict` proposals that replace only that region if accepted | `test_writer_edits_after_sending_are_never_overwritten`, `test_proposals_can_be_accepted_in_any_order_on_the_same_paragraph` |
| Located losses | Every unsupported construct is listed with its code, target block and location | `test_every_unsupported_construct_is_a_located_loss` (field, SmartArt, content control, ink, equation, table cell insertion), `test_header_footnote_and_macro_changes_are_reported` |
| Third-party files | Two LibreOffice 24.2.7.2 files: the Word fixture re-saved, and the review copy edited by LibreOffice with change recording (`scripts/libreoffice_review_edit.py`) | `test_libreoffice_resaved_copy_merges_to_the_same_proposals`, `test_libreoffice_edited_copy_gives_exact_proposals_and_its_comment`. **Not verified:** a file saved by Microsoft Word. The Word fixtures are emulated |
| Hostile input | Bounded, entity-safe archive reader; refusal leaves the document unchanged | `test_hostile_or_malformed_returned_files_are_refused_without_changes` (traversal, DTD entity, malformed XML, missing part, oversized member, truncated ZIP) |
| Performance | 100,000-word `project_100k` with 200 tracked edits, 50 comments and a move: build the copy, plan and apply the merge | `docs/benchmarks/0.3-docx-review.json` (`scripts/benchmark.py --no-gui --docx-review --samples 10`, unqualified container host): p95 0.97 s build, 0.40 s plan, 0.81 s apply with 201 proposals |
| Interfaces | `review-package`, `merge-review` through CLI, MCP and REST with `reviewed_path` scoping; File menu actions open Proposed changes | `test_cli_sends_a_package_and_merges_the_returned_copy`, `test_mcp_and_rest_give_the_same_merge_and_scope_the_reviewed_path`, `tests/test_gui_docx_review.py` (offscreen), `tests/test_interface_parity.py` |

## Reference libraries, citations and editor workspace

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| #99 ribbon; selectable sidebars | Two formatting rows; local persistent panel selectors for outline/inspector, bookmarks, statistics, scratchpad and Library | `test_gui_workspace.py`; native offscreen GUI checks |
| Ribbon and inspector redesign | Two ribbon rows beside the logo at its height; a ribbon per file view (ByAuthorX a superset); Export asks in ByAuthorX and defaults to the open format elsewhere; sidebar selectors in the dock titles; Layout sidebar with layers; Proposed changes / Checks / AI / Configure tabs with active checks and Run all; nested, scrolling menus | `test_gui_workspace.py`, `test_gui_review.py`, `test_gui_ai.py`; native offscreen GUI checks |
| #98 Inspector tab underlines | Selected-tab pen underline clipped to its tab | Pixel regression in `test_gui_workspace.py` |
| Review selector | Dropdown + Go; spelling moved from AI; grammar, health, repository, factual, voice and custom checks; explicit apply | `test_gui_review.py`, existing AI/provider tests |
| Library spec phase 1 | Local registry/relink, safe mixed parsers, SQLite WAL/FTS, per-file leases/cancellation, bounded read/search, transport restrictions and separate passage consent | `test_libraries.py`, `test_library_interfaces.py`, `test_retrieval.py`; no reference-hardware performance claims |
| Library spec phase 2 | Source records and typed cite locators; picker; verification; APA/MLA/Chicago and bibliography; CSL/BibTeX/RIS imports/exports | `test_citations.py`, `test_gui_libraries.py`; exporters without native footnotes use numbered endnotes |
| Library spec phase 3 | Explicit context retrieval; Chat/Work and review passage preview; source proposals; indexed quotation checks and stale findings | `test_library_interfaces.py`, `test_library_checks.py`, provider/AI tests; unknown dependencies expand factual scope |
| Library spec phase 4 (configured providers) | Ollama/OpenAI-compatible embedding contract, model fingerprints, hybrid ranking, independent embedding consent | `test_embeddings.py`; configured providers only, no bundled/downloadable weights |
| Context engine 5.3: quote verification | `quote_match.normalize/locate` (NFC, whitespace, quotes, soft and line-break hyphens, ligatures, dashes, ellipses; offset map); `citations.verify_source_detail`, `library_checks.check_citation` and the evidence checks use it and report `raw_pass`/`normalized_pass`; a near match is the advisory `quote_near_match` and never verified; Cite picker and `byax cite` infer `quotation` or `paraphrase` | `test_quote_match.py`, `test_quote_checks.py`, `test_gui_libraries.py`. The near threshold (90) is provisional until the Phase 0 harness records pass and false-positive rates; the pass rate on real PDFs is not measured |
| Context engine 5.5: budgets | `context_assembly.estimate_tokens` (3.5 characters per token unless a tokenizer is registered; none is bundled), token budgets over the unchanged character caps, library passages limited by tokens; Ollama sets `options.num_ctx` from the payload and fails before sending with `limit_exceeded` ("N tokens needed / M available"); the model window is read only by an explicit connection test | `test_context_assembly.py`, `test_providers.py`. Real Ollama servers were not available; requests are verified against an HTTP fixture |
| Context engine 5.6: transparency | Local/remote label for every provider and embedder (`providers list`, `capabilities`, `preview`, `library status`, library panel, passage picker, payload dialog, Provider settings); read-only extracted text preview per file | `test_locality.py`, `test_gui_libraries.py`. Verified-absence (Q14) is not part of this change |
| Library spec §5.1 hybrid retrieval (context engine 5.1) | `method=auto` picks hybrid per library when embeddings are ready or partial and keyword otherwise, reporting `method` and `embedding_state`; global RRF across libraries, de-duplication, pinned tier, then the budget; auto never escalates embedding consent | `test_context_retrieval.py` (`test_global_fusion_fixes_first_library_starvation`, `test_method_auto_reports_hybrid_or_keyword_per_library`, `test_auto_never_escalates_embedding_consent`, `test_pinned_items_are_excluded_and_reported_as_the_pinned_tier`, `test_policy_checks_and_duplicates_survive_fusion`); `test_retrieval_interfaces.py` for CLI/MCP parity. Retrieval quality numbers await the Phase 0 harness |
| Library spec §5.2 model identity (context engine 5.4) | float32 vectors with model signature and chunk fingerprint; incremental rebuild job with done/skipped/failed counts, batching and unsent-only backoff; changed signature reported `stale`, never mixed; partial coverage used with a notice | `test_context_retrieval.py` (`test_rebuild_is_incremental_batched_and_reports_counts`, `test_rebuild_retries_unsent_requests_counts_failures_and_cancels`, `test_changed_signature_marks_vectors_stale_and_never_mixes`, `test_partial_vectors_are_used_with_a_visible_notice`), `test_embed_runs_as_one_job_with_counts`. Brute-force search only; no latency claim |
| Index schema 2 and query syntax (context engine 5.2) | Heading column, prefix and trigram indexes, `fts5vocab` suggestions (RapidFuzz), phrases/NEAR/negation/fields, stopword rule, injection-safe expressions; schema-1 indexes marked stale, kept searchable and rebuilt | `test_search_query.py` (hostile and random input compile to valid expressions), `test_context_retrieval.py` (`test_schema_one_index_is_marked_stale_kept_searchable_and_rebuilt`, trigram, heading and suggestion tests) |
| Verified absence (context engine 5.6, Q14) | Exhaustive porter + trigram pass over every eligible library plus the open document; CLI/MCP `library --action verify-absence`; AI tab checks "not found" replies; library citation checks report the quote's other literal locations | `test_context_retrieval.py` (`test_verify_absence_searches_every_library_and_the_manuscript`), `test_retrieval_interfaces.py`, `test_library_checks.py::test_quote_missing_at_locator_is_searched_elsewhere_before_reporting`, `test_gui_ai.py::test_absence_reply_is_checked_against_literal_matches`. Semantic absence is not checked |
| Context engine 6.1: grounding records | `grounding.GroundingRecord` (answer span, chunk, source span, quote with 32-character prefix/suffix, content hash, origin, state, checks) from Anthropic custom-content citations, Gemini grounding supports (UTF-8 byte offsets converted), Cohere spans and `[[cite:ID]]` tokens (span recovered with `quote_match.align`); cascade stage 1 (quote, fuzzy) with stages 2–3 reported `not_run` until registered; an invalid cite ID is stripped and `unverified`, never a rejected reply, with one corrective retry for local providers or when `citation_retry` is on | `test_grounding.py` (`test_token_records_follow_the_plan_shape_and_recover_source_spans`, `test_cascade_stage_one_states`, `test_later_stages_plug_into_the_cascade_and_report_not_run_until_installed`, the three native adapter tests), `test_prompt_layout.py` (`test_anthropic_chat_sends_custom_content_documents_and_parses_citations`, `test_invalid_ids_are_stripped_and_retried_once_for_local_providers`, `test_remote_providers_retry_only_when_the_setting_allows`). Native fixtures are written from the public API references, not live recordings; no live Anthropic, Gemini or Cohere call was made; Gemini and Cohere requests are not wired to an adapter |
| Context engine 6.2: citation chips | Chips per citation in the AI tab with state suffixes; hover card with title, locator and highlighted span; click opens the library reader at the chunk with the span selected (re-anchoring a changed source); "Insert as citation" creates a `cite` mark with kind, locator, quote and anchor | `test_gui_citations.py` offscreen with a fake provider; jump fidelity 5/5 (100%) on the available fixture library (text, Markdown, `.byax` blocks, a two-page PDF), `test_jump_fidelity_is_total_on_the_fixture_library`. `tests/fixtures/eval` (Phase 0 corpus) is not in this base, so the plan's fixture-library figure is still to be measured there |
| Context engine 6.3: cache-ordered prompts | `context_assembly.layout_prompt`: instruction, profile/style/memory, manuscript snapshot slot, conversation, then document context, selection, passages and turn; canonical serialization and digest-tagged untrusted-data delimiters around every block; Anthropic `cache_control` on the stable system blocks and the conversation end; `cache_read_tokens`/`cache_write_tokens` recorded for Anthropic, OpenAI and Gemini | `test_prompt_layout.py` (`test_prefixes_are_byte_identical_across_turns` for all four adapters, `test_every_data_block_keeps_the_untrusted_framing`, `test_anthropic_gets_cache_breakpoints_and_cache_usage_is_recorded`, `test_cache_reads_are_recorded_where_providers_report_them`). Actual cache hit rates depend on each provider's minimum prefix length and are not measured |
| Context engine 6.4–6.5: citation audit and unanchored citations | `byax check --citations`, MCP `byax_check`, REST and Checks tab → Citation audit: quote found, near match with differences, not found (with other locations), source changed (content hash), unanchored, source unavailable, not checkable; deterministic, local, no consent. Re-anchoring order structure → position → nearby → anywhere; proposals (`reanchor_citation`, source pointer update) apply only on request; an unanchored citation is kept and shown struck through. Optional `anchor` object on citation marks in document schema 3 | `test_grounding.py` (`test_audit_reports_every_stage_one_outcome`, `test_changed_source_reanchors_or_becomes_unanchored_and_is_never_dropped`, `test_reanchoring_tries_structure_first`, `test_anchor_fields_are_validated_and_separate_from_rendering_affixes`, `test_citation_audit_parity_across_library_cli_mcp_and_rest`), `test_gui_citations.py` (`test_checks_tab_citation_audit_matches_the_cli_report`), `test_migration.py` (`test_v2_citation_anchor_is_kept_when_valid_and_rejected_when_it_means_something_else`). Paraphrases are not checked for meaning (stage 2, Phase 5) |
| Library spec phase 5 | Deferred as specified | No outside DOI/catalog lookup or Zotero local API; manual DOI/URL entry and Zotero file imports work |

See [the guide](library-and-citations-guide.md) for commands, limits and local storage.

## Version 0.2 automated acceptance — 14 October 2026

Issue closure evidence is recorded in [the 0.2 audit](issue-audit-0.2.md).
The prior rows describe historical subsets; this section supersedes their specific
remaining GUI asset, style-picker, section-dictation and bounded-context gaps.

- **#6:** all common styles, including Heading 4–6 and List, are selectable through
  the GUI and preserve text on application/clear. Existing inheritance, list
  split/join/restart, mark, formatting and save/reopen tests remain passing.
- **#9:** the actual linked-assets dialog retrieves the selected local asset only
  after its explicit action, then embeds it; CLI refresh/embed round trips pass.
  Existing network consent, missing-location, invalidation and publication tests
  cover dependency policy persistence.
- **#27:** effective section settings now reach both transcription contexts and
  sessions. An exclusive section policy refuses unapproved reference selections.
  Nothing selects or retrieves source contents automatically. Existing profile
  movement, inheritance, exceptions, protection and collection tests pass.
- **#35:** existing inspection/context tests cover bounded continuation, exact
  editable content, source versions, omission diagnostics and required payloads.
  Added checks prohibit network access and reject embedded credential fields.
- **#69:** disjoint same-paragraph edits merge in the canonical review workflow;
  conflicts include base scalar ranges. Grapheme boundaries and compatible marks
  survive. Ambiguous formatting remains an explicit whole-block conflict.
- **#70 (partial):** local peer pins and fork retention survive both compaction
  modes. Disconnected ancestry no longer guesses a fork. Hosted pin management
  awaits #81; this issue is not closed.
- **#92:** [research note](coediting-spike.md) recommends against live co-typing
  until authenticated host/lease contracts exist; offline diff3 is not a CRDT.

### Resolver measurements (#72)

Five samples per fixture before and after the cache/page-index change on the same
unqualified Linux container, Python 3.12 / Qt 6.12, offscreen. Other test activity
was running, so these timings are indicative, not controlled hardware qualification.
Full reflow includes shared text-document construction and all-page resolution.
Nearest-rank p95 is the maximum with five samples. PNG bytes after decoding,
PDF extracted text, and PNG/PDF layout signatures agree with caches disabled in
the regression fixture; the existing 36 layout tests pass.

| Fixture | Before median / p95 ms | After median / p95 ms |
| --- | ---: | ---: |
| mixed_100 | 684.8 / 778.9 | 494.8 / 527.1 |
| long_500 | 1224.8 / 1300.3 | 953.0 / 1042.4 |

Raw environment, hashes and samples: [before](benchmarks/0.2-layout-before.json),
[after](benchmarks/0.2-layout-after.json). Cache keys hash actual image bytes;
retention is capped at 64 MiB and 128 images, with copy-on-write return values.
Glyph coverage is shared within one resolve and discarded afterwards.

### Scaling baseline (#76, reference for #66/#73/#74/#75)

Reproduce with `QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark.py
--no-gui --scaling --samples 5 --out artifacts/benchmark.json` (one shell line).
The flowing fixtures contain tables, footnotes, images and column changes, with
no explicit page breaks. Requested sizes are estimates; measured pages below are
authoritative. Every edit sample starts from the same unchanged fixture. No claim
of constant commit cost, incremental resolution or desktop input latency is made.

| Requested pages | Actual pages | Start / middle / end edit full-resolution median ms |
| ---: | ---: | ---: |
| 100 | 90 | 172.6 / 177.8 / 174.9 |
| 500 | 450 | 1051.3 / 1004.6 / 1014.4 |
| 2000 | 1825 | 4229.0 / 4284.5 / 4205.0 |

| Blocks | Character edit / split / heading insert median ms |
| ---: | ---: |
| 379 | 10.1 / 9.7 / 9.6 |
| 1499 | 37.4 / 38.1 / 41.6 |
| 5000 | 151.2 / 133.0 / 142.6 |
| 15000 | 476.0 / 478.0 / 446.5 |

[Raw scaling data](benchmarks/0.2-scaling.json) includes all samples, nearest-rank p95, environment and fixture hashes. Commit cost still scales with block count; #73/#74 remain open. Future changes should rerun these workloads on the same host for before/after comparisons.

## EPUB export and import (B1) — 16 October 2026

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| EPUB 3 export | `epub.export_epub` / `export_document(..., format='epub')`: reflowable XHTML split at chosen points, navigation document and NCX from the outline, package metadata (title, creator, language, derived identifier, `dcterms:modified` from metadata or history), cover image, CSS from named styles, EPUB 3 footnotes, images, lists, tables with spans, accessibility metadata | `tests/test_epub.py` (metadata, navigation, chapters, cover, footnotes, lists, tables, marks, untrusted values, output policy) |
| Determinism | Same document and options give identical bytes; entries sorted, `mimetype` first and stored, fixed timestamps and attributes; the clock is never read | `test_export_is_byte_reproducible_and_the_zip_is_canonical`, `test_same_bytes_whatever_the_wall_clock` |
| Fonts | Embedded only where the font's OS/2 embedding permission allows (`fonts.font_bytes_info`); others are reported (`font_not_embedded`, `font_unavailable`) | `test_fonts_whose_permission_forbids_embedding_are_not_embedded`, `test_permitted_bundled_fonts_are_embedded_with_face_rules` |
| Accessibility | `schema:accessMode`, `accessModeSufficient`, `accessibilityFeature`, `accessibilityHazard`, `accessibilitySummary`; images without alt text are located losses (`image_alt_missing`), decorative images are not | `test_images_without_alt_text_are_reported_decorative_images_are_not`, `test_accessibility_metadata_and_options`. Not a conformance claim: no assistive-technology or reading-system testing |
| Built-in structural validator | `epub.validate`: container, mimetype, package metadata, manifest/spine consistency, every manifest item present, XHTML well-formedness, unique ids, in-book links and fragments, navigation, CSS resources, image and font signatures; also run on every export before publication | `test_every_fixture_exports_a_structurally_valid_epub` (letter, resume, report, newsletter, press kit, mixed 100, flowing 100), `test_rich_document_is_valid_and_ids_are_unique`, `test_validator_reports_*` |
| EPUBCheck | Optional CI job (`epubcheck`, manual and weekly runs only) via `scripts/epubcheck_fixtures.py` with EPUBCheck 5.3.0 pinned by SHA-256 | **Recorded local run, 16 October 2026** (Ubuntu 24.04 container, OpenJDK 21, EPUBCheck 5.3.0, `scripts/epubcheck_fixtures.py --epubcheck ... --warnings-fail`): all 11 documents (the ten generated fixtures and `epub_rich`) reported no errors, warnings or infos. This is a recorded run, not an automated test in the suite, and the CI job itself has not yet run on GitHub Actions |
| EPUB import | Spine read in order through the HTML importer; package-relative images, EPUB 3 notes, metadata, cover; scripts, SVG, audio and video are reported and not executed; internal links become plain text with a loss | `test_import_round_trip_keeps_text_structure_metadata_notes_and_images`, `test_import_follows_relative_paths_and_ignores_scripts` |
| Hostile archives | `_safe_zip` limits plus entity rejection in the package, navigation and content documents, DRM rejection, bounded spine and image counts | `test_hostile_archives_are_rejected_with_the_html_import_limits`, `test_oversized_spine_and_nested_input_are_bounded`, seeded mutation fuzzing in `test_fuzz_import.py` |
| Registry and interfaces | `epub` in `EXPORT_FORMATS` and the file-view registry; the view table in `file-views.md` is checked against it; CLI `export`/`convert`, MCP and the ribbon Export menu list it | `test_epub_is_registered_as_an_export_format_and_file_view`, `test_view_list_in_this_document_matches_the_registry`, `test_cli_and_mcp_export_epub`, `test_gui_lists_epub_in_the_export_menus_and_exports_and_opens_it` (offscreen) |

Not verified: how any reading system renders the output (fonts, footnote pop-ups, cover display), Kindle conversion, and Apple Books or other store ingestion checks.

## Reach: macOS, signing and onboarding (E1–E3) — 16 October 2026

Exit criteria of competitiveness plan 7.6: "The offscreen suite passes on macOS, and installers on all three platforms are signed" and "The sample project opens, compiles and passes all checks without findings". The first needs a Mac and signing secrets that were not available when this was written; it is recorded as **pending maintainer verification**, not as met. The second is met for opening and checking and for the exporters that exist; the compile pipeline (B2) is built separately and the sample is not yet compiled through it.

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| E1 platform audit | Findings fixed: fontconfig-only font resolution (macOS folders scanned instead); `RLIMIT_AS` refusal aborting the library parser (now best-effort); plain `fsync` on macOS (`F_FULLFSYNC`); case-sensitive lock identity on case-insensitive volumes; no handler for Finder open events; menu roles left to text heuristics; a hard-coded `Ctrl+Alt+L` hint. Audited and unchanged: `platformdirs` paths, `fcntl.flock` locks, `keyring` backend selection (none is forced), `os.O_DIRECTORY` directory sync, process-group handling, Qt `Ctrl` shortcuts (shown as Cmd), file dialog filters | `tests/test_macos_support.py` (fonts, fsync, locks, limits and the parser under patched `sys.platform`, Finder events, menu roles, no forced keyring backend). Real macOS behaviour is **not** verified |
| E1 packaging | `scripts/build_macos.py` (arm64 `.app`, Info.plist with bundle id, `.byax` document type and UTI, microphone usage string, high-resolution, minimum system 14.0 matching the locked Qt wheels, generated `.icns`, CLI and MCP launchers, `.dmg`); `packaging/macos/entitlements.plist` (microphone, network client, no JIT); `release.yml` `macos` job; `ci.yml` `macos-14` in the compatibility and package matrices | `tests/test_macos_support.py` (plist, entitlements, launchers, signing/notarization command lines, image naming, workflow gating). **Pending maintainer verification:** a successful `macos-14` run of the `compatibility` and `packages` jobs, a frozen app that launches, and the offscreen suite subset passing on macOS |
| E2 signing | Windows `signtool` with a PFX, Linux GPG detached signature of `SHA256SUMS.txt` plus optional `dpkg-sig`, macOS `codesign --options runtime` and `notarytool --wait` + `stapler`; all gated on secrets, otherwise the file is published unsigned and labelled; the release notes state the status per file | `test_release_signs_and_notarizes_only_when_every_secret_exists`, `test_only_a_signed_and_notarized_image_gets_the_plain_name`, `test_codesign_commands_use_hardened_runtime_timestamps_and_entitlements`. **Pending maintainer verification:** provisioning the secrets (see [packaging](packaging.md#signing-and-notarization)) and one signed release: `signtool verify /pa`, `spctl --assess`, `stapler validate`, `gpg --verify`. Only the Windows installer is signed, not the executables inside it |
| E2 manual update check | Help → Check for updates… and `byax version --check-updates` make one GitHub Releases API request when asked; proxy aware (`trust_env`); reads tag, date and an in-project link only; no startup or background request; menu item removable; environment switch for managed desktops; refused over MCP and REST | `tests/test_updates.py`: one request, wording, failures (rate limit, offline, timeout, redirects, size, hostile link), `test_desktop_startup_and_tour_make_no_network_request`, `test_nothing_but_explicit_commands_reference_the_check`, the setting and environment switch |
| E3 sample project | `resources/samples/sample-project.byax`, generated deterministically by `scripts/build_sample_project.py` from canonical operations: a defined and applied style, binder (chapters, scenes, a note, statuses, synopses, targets), a pending proposal, a comment, a citation with a source record, a project dictionary and a recorded clean check. Help → Open sample project and `byax sample` copy it; the bundled file is never opened for editing | `tests/test_onboarding.py`: regeneration to an identical document (compared as documents, since ZIP bytes depend on the zlib build), structure, zero findings under the default suites and the spelling, grammar, editorial, consistency, factual and layout suites, DOCX/PDF/EPUB (structural validator)/Markdown/HTML/TXT export, safe numbered copies, CLI and MCP. The advisory fiction word-echo flags are not suppressed. The compile pipeline is not exercised |
| E3 first-run tour | Six steps; Skip, Back/Next, arrow and Page keys, Escape, "Don't show again", once per tour version, reopen from Help; screen-reader names and descriptions on every control and step; a fade between steps that is replaced by an instant swap (and a resting pen line) under reduced motion | `tests/test_onboarding.py` (first launch once, skip, next/back/finish, keyboard, persistence, accessible names, `fade` versus `instant`, environment and setting). No screen reader was run; offscreen widgets only |

Pending maintainer verification, in one list: run the focused suite and the frozen smoke test on `macos-14`; open the built `.dmg` on an Apple silicon Mac (Gatekeeper, drag to Applications, double-click and Open With a `.byax`, application menu, Cmd shortcuts, Keychain and microphone prompts, fonts, printing, Retina scaling, `byax` through the symlink); provision the signing secrets and cut one signed release; verify all three platforms' signatures as listed above.

## Grammar tiers and language packs (D1, D4) — 16 October 2026

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| CP 6.1 decision gate (open decision 2) | Both tiers behind one `GrammarEngine` interface: built-in rules always, LanguageTool as an opt-in, user-installed sidecar. Licence, size, start-up, memory, latency and accuracy are recorded in [grammar](grammar.md) | **Recorded run, 16 October 2026** (4-core Linux container, OpenJDK 21, LanguageTool 6.6, `scripts/grammar_benchmark.py --languagetool …`, [raw data](benchmarks/0.3-grammar-languagetool.json)): 252 MB download, 386 MB unpacked, JVM ready in 1.9 s, first check 4.2 s, about 530 MB resident, median 52 ms per sentence. A recorded run, not an automated test in the suite |
| CP 6.1 built-in rules | 85 data-driven English rules in 8 families (`resources/grammar/en.json`) with exact ranges, replacements that keep the original case, per-rule sensitivity levels, dialogue exclusion for dialect-prone rules, runaway-pattern probes; no duplicates of the spec 12.1 checks | `tests/test_grammar.py` (engine, validation, findings, repair applied through `engine.apply`, language from document or paragraph, intentional passages, disabling rules); rule files are validated at pack install |
| CP 6.5 grammar accuracy | Precision and recall per rule family on a 299-sentence labelled fixture, plus an open fixture that measures breadth; numbers published in [grammar](grammar.md) and [benchmarks](benchmarks/0.3-grammar-builtin.json) | `test_fixture_accuracy_meets_the_stored_thresholds_per_rule_group`, `test_published_numbers_match_a_fresh_measurement`, `test_every_rule_has_a_labelled_example…`. Overall 98.6% precision, 91.6% recall; punctuation precision 77.8% (two deliberate asyndeton sentences). The fixture was written with the rules in view, so these numbers flatter them: the open fixture shows 10.3% recall for the built-in tier against 61.5% for LanguageTool |
| CP 6.1 LanguageTool adapter | HTTP to a loopback server only (non-loopback refused, proxies ignored); UTF-16 offsets mapped to scalar indices; duplicates of deterministic checks and (by default) spelling dropped; built-in findings win overlaps; replacements become repair operations, alternatives under `measured.alternatives` | `test_languagetool_findings_use_exact_scalar_ranges_and_become_repair_proposals`, `test_utf16_offsets_map_to_scalar_indices_around_astral_characters`, `test_text_only_goes_to_a_loopback_address` (fake server in `tests/fixtures/grammar`); `test_a_real_languagetool_server_returns_scalar_ranges_for_astral_text` (`pytest -m languagetool`, skipped unless `BYAX_LANGUAGETOOL_URL` is set; **passed against a real LanguageTool 6.6 on 16 October 2026**) |
| CP 6.1 setup and download policy | Configure a folder or port, enable, start and stop (a server ByAuthorX started, never a foreign process). `install-languagetool` shows the pinned URL, size and SHA-256 and downloads only when the digest is repeated back; size and SHA-256 are checked before extraction, which refuses unsafe member names | `test_install_shows_the_plan_and_downloads_nothing_without_confirmation`, `test_install_verifies_size_and_sha256_before_unpacking`, `test_install_refuses_unsafe_archive_members`, `test_start_and_stop_a_server_from_a_user_installed_folder` (POSIX). The pinned digest was computed from two identical direct downloads because the site publishes none; maintainer to re-verify independently. Not verified: starting Java on Windows or macOS, a download through the real installer on a user machine |
| CP 6.1 honesty and incrementality | The report carries a `grammar.languagetool` coverage entry (`not installed`, `incomplete` when enabled but unreachable, `completed` when running); tier state is in each block's dependency fingerprint, and one edited paragraph re-runs only itself (0.2 s on 100,000 words, [timing](benchmarks/0.3-grammar-performance.json)) | `test_the_report_says_plainly_that_languagetool_is_not_installed`, `test_an_enabled_but_unreachable_server_is_reported_honestly…`, `test_the_tier_state_is_part_of_the_dependency_fingerprint`, `test_grammar_checks_stay_incremental_per_paragraph` |
| Interface parity | `grammar` and `languages` are CLI commands, MCP tools and REST commands in `INTERFACE_INVENTORY`; MCP/REST refuse configuring, installing, starting, stopping and pack install/remove | `test_interface_parity.py`, `test_administrative_actions_are_refused_over_mcp_and_rest_but_reading_is_not`, `test_install_and_remove_are_refused_over_mcp_and_rest_but_listing_is_not` |
| CP 6.4 language packs | Manifest format (id, language, version, licence, source, per-file kind/size/SHA-256), `.byaxpack` or folder install from a local file with no network, explicit URL install only with the expected hash, verification before use, precedence over built-in data, removal; spelling dictionaries, fiction and readability word lists and grammar rules load from packs by language | `tests/test_language_packs.py` (install, override and restore, newest version wins, tamper detection and fallback, unsafe archives, URL hash check, deterministic archives, CLI) |
| CP 6.4 interface strings | `QTranslator` serving a pack's string table on top of `tr()`; translations that lose a placeholder are ignored; coverage counted against a generated catalogue of 1,241 texts; [translations](translations.md) tracks it per language | `test_a_strings_pack_serves_a_translator_and_rejects_changed_placeholders`, `test_coverage_counts_usable_translations_against_the_source_catalogue`. **No interface translation ships: English 100%, every other language 0%.** Not verified: right-to-left layout, plural forms, a translated interface on a physical desktop; 56 `tr()` call sites pass variables and are not in the catalogue |
| Desktop | Checks tab shows both tiers (`Built-in rules: on`, `LanguageTool: not installed/stopped/running`) with a setup button; Tools > Languages… lists, installs (after showing the file checksum), verifies and removes packs, chooses the interface language, and manages LanguageTool (download only after a dialog with source, size and SHA-256) | `tests/test_gui_languages.py` (offscreen). Not verified on a physical desktop or with a screen reader |

## Compile pipeline (competitiveness plan B2)

| Requirement | Implemented behavior | Evidence / limits |
| --- | --- | --- |
| Compile profile extending the export profile | `compile.validate_profile`/`resolve_profile`: included items (default `project.compile_plan`, exact `include`, `exclude` with descendants), sibling `order`, generated front and back matter (half title, title page, copyright, dedication, epigraph, contents, acknowledgments, about the author, also by, manuscript title page, custom text) or binder `front`/`back` items, per-kind formatting (page start, number style and label, title auto/show/hide, drop cap, small-caps lead-in, sink, scene separator glyph, part pages) | `test_compile_builds_a_transient_document_and_never_changes_the_master`, `test_include_exclude_and_order_choose_binder_items`, `test_profile_validation_rejects_unknown_keys_and_wrong_values`, `test_documents_without_a_binder_compile_level_one_headings_as_chapters` |
| Transient compiled document | `compile.build` copies the master, strips history, review, layers, protections, locks and the binder, and applies canonical operations through `engine.apply`; the master is compared before and after every compile | `test_compile_builds_a_transient_document_and_never_changes_the_master`, `test_compile_writes_the_chosen_items_and_leaves_the_document_alone` (GUI) |
| Book design | Trim sizes (5×8 … 7×10, A5, B5, Letter, A4 or `{width, height}`), mirrored margins with a gutter, physical recto/verso parity, running heads (author verso, title or chapter recto) with folios, suppressed on openers through each opener's section `first` variant, opener folios, blank pages without regions, widow/orphan control, sinks, small caps; reuses page masters, sections, header/footer variants and footnotes in `layout.py` | `test_pdf_compile_is_reproducible_with_running_heads_suppressed_on_openers` checks every page's head, folio and inside margin; existing layout suites pass unchanged. Drop caps print as a raised initial (text does not wrap; reported as `compile_drop_cap_raised`); no hyphenation; small caps are synthesized by Qt |
| Bleed and crop marks | Optional `design.bleed` (≤ 72 pt) and `crop_marks`: the PDF sheet grows around the trim, background fills the bleed, hairline marks sit outside it; the report records trim, bleed and media | `test_bleed_and_crop_marks_enlarge_the_sheet_around_the_trim` (MediaBox and unchanged layout signature). Not PDF/X: TrimBox/BleedBox are not written |
| Targets reuse the exporters and their losses | PDF and Word through `exchange.export_document`, EPUB through `epub.export_epub` (explicit split points, master-derived `modified`), Markdown with the Markdown exporter; Word and Markdown are re-imported and every difference is added as a located `roundtrip_difference` | `test_epub_compile_is_byte_reproducible_valid_and_split_per_opener` (built-in structural check passes), `test_markdown_target_round_trips_with_located_losses`, `test_word_regions_fields_spacing_and_page_breaks_round_trip`. EPUBCheck was not run on compiled books |
| Preflight | Shared findings model (`rule`, `severity`, `explanation`, `affected_ids`, `status`, `id`): missing title/author/language, binder orphans and unbound headings, empty items, headings without text, alt text, image resolution against the trim (`design.image_dpi`), unavailable or non-embeddable fonts (OS/2 permission), headings and scene breaks stranded at a page foot (after pagination), excluded review items, missing manuscript contact; errors refuse to write | `test_preflight_reports_with_the_findings_model`, `test_fonts_that_are_missing_or_forbid_embedding_are_preflight_warnings`, `test_orphan_headings_are_found_from_the_page_starts`, `test_preflight_errors_refuse_to_write`, `test_dry_run_and_preflight_write_nothing_and_report_losses` |
| Determinism (spec 6.4) | Compile report carries `layout_signature`, `page_count` and `page_starts`; generated IDs are counters and nothing reads the clock | `test_pdf_compile_is_reproducible_with_running_heads_suppressed_on_openers`, `test_epub_compile_is_byte_reproducible_valid_and_split_per_opener`; PDF bytes embed a creation date and are not compared |
| Exit 4.3: 100,000-word PDF reproducible, signature recorded | `project_100k` with the novel preset: 553 pages at 5.5 × 8.5 in, **layout signature `a02300811f08af470e1b839f2cd3ca64137460e8762abd3397e59194c769d744`**, page-start hash `129e74b2…c0dd`, identical over two compiles; compile 14.1 s / 13.6 s on an unqualified offscreen host (16 October 2026, Qt 6.12.0, Python 3.13); EPUB 0.64 s with identical bytes; manuscript Word 2.8 s | `test_100k_word_project_compiles_to_a_reproducible_pdf` (compares two compiles; the recorded value is host-specific because it depends on installed fonts and the Qt version); [benchmarks/0.3-compile.json](benchmarks/0.3-compile.json) from `scripts/benchmark_compile.py` |
| Exit 4.3: standard manuscript round-trips through Word with no unreported losses | Manuscript preset → DOCX → import; header page field, first-page variant, Times New Roman 12 pt double spacing, page breaks before chapters, italics and bold survive; any difference is located in the report | `test_standard_manuscript_round_trips_through_word_with_no_unreported_losses` compares the compiled and re-imported documents independently of the compiler's own check; on `project_100k` the round trip has 0 differences (benchmark). Word itself was not used to open the file |
| Presets as data, user presets | `resources/compile/{novel,nonfiction,manuscript}.json` are settings collections with a `compile` kind; presets saved in `settings.compile_presets` travel with `collection` export/apply; partial presets `extends` a bundled one | `test_presets_are_settings_collections_with_a_valid_compile_profile`, `test_saved_and_file_presets_extend_a_bundled_preset`, `test_save_preset_commits_one_settings_operation` (GUI) |
| Interfaces | `compile` through CLI, MCP and REST with `--out`/`--preset-file` scoping; inventory rows | `test_cli_compiles_lists_saves_and_exports_presets`, `test_compile_has_mcp_and_rest_parity_with_path_scoping`, `tests/test_interface_parity.py` |
| GUI | File → Compile…: preset, format, trim, binder checklist (notes unticked), preflight list, compile, save preset; named and described controls | `tests/test_gui_compile.py`, dialog walk in `tests/test_accessibility.py` (offscreen); physical screen-reader pass unverified |

Not verified: print-vendor acceptance (KDP, IngramSpark) of the PDF, how Word, LibreOffice or Google Docs lay out the manuscript, and reading-system rendering of compiled EPUBs.
