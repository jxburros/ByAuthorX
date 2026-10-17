# Architecture

ByAuthorX is a native Qt 6 desktop application. Python supplies the shared document engine; PySide6 supplies native widgets, text shaping, clipboard, file dialogs, accessibility hooks, printing, and microphone access. No local web server is needed for editing. Qt WebEngine is neither an application dependency nor a rendering surface.

The canonical editable representation is a JSON-compatible semantic document. Paragraphs, headings, tables, cells, images, review objects, and layers have stable identities. Canonical text coordinates are zero-based Unicode scalar indices with end-exclusive ranges; edit boundaries must coincide with grapheme boundaries. GUI code converts Qt's UTF-16 positions at the interface boundary.

## Modules

| Module | Responsibility |
| --- | --- |
| `model`, `errors` | Document structures, validation, stable errors |
| `engine` | Atomic candidate operations, targeting, preservation, revision history |
| `storage`, `migration` | Versioned ZIP packages, content hashes, atomic replacement, process locks, source folders, schema transitions |
| `anchors`, `formatting`, `compare`, `dependencies` | Review-anchor remapping and tracked changes; style inheritance and lists; semantic comparison and merge planning; authorized dependency retrieval |
| `checks` | Located findings, actual coverage, deterministic requirements |
| `fiction_checks`, `readability`, `prose` | Advisory fiction analysis suite with word lists as data (`resources/fiction`, `resources/readability`) and its incremental dependency units; per-chapter readability indices, style statistics and pacing data; shared sentence, dialogue and syllable primitives |
| `layout` | Qt typography, pagination and shared page drawing for previews/PDF/images/printing |
| `exchange` | Safe imports, editable exports, explicit compatibility/loss reports |
| `epub` | EPUB 3 export (deterministic package, navigation from the outline, permitted embedded fonts, accessibility metadata), EPUB import through the HTML importer and the hostile-archive limits, and the built-in structural validator |
| `publication` | Selected publication policies, serialized redaction verification, reproduction inventory |
| `providers` | Six explicitly selected model adapters and local secret references |
| `transcription`, `jobs` | Recording, reviewed transcript insertion, persistent work state |
| `gui`, `gui_editor` | Native desktop controls; operations go through the engine |
| `cli` | Common command dispatcher and structured CLI envelopes |
| `mcp`, `service` | Stdio MCP; separately enabled local REST transport |
| `evaluation` | Developer-only retrieval measurement: builds fixture libraries through the public library API, scores any retrieval configuration (recall, nDCG, MRR, latency, index size) against locator-labelled golden queries; no application code imports it (see [development](development.md#retrieval-evaluation-harness)) |
| `libraries`, `library_parser`, `library_checks`, `retrieval`, `embeddings` | Attached local research libraries, indexing (index schema 3: chunks with heading and fingerprint, porter FTS5 with heading, text and contextual-note columns and prefix indexes, trigram FTS5, `fts5vocab`, the `contexts` table), the structure-aware chunker v3 (`libraries.chunk_passages`), retrieval orchestration with per-library method selection, optional reranking and verified absence, incremental embeddings (the indexed form carries the heading path and the note, never the stored text) and library-backed review checks (see the [library guide](library-and-citations-guide.md)). `library_parser` also hosts the optional Docling PDF engine, which runs only in the parser subprocess |
| `quote_match`, `context_assembly` | Quote normalization, exact/normalized/near alignment, claim alignment and three-selector anchors (RapidFuzz); offline token estimates, token budgets over the character caps, the Ollama `num_ctx` plan with its `limit_exceeded` refusal, and the cache-ordered prompt layout (stable instruction, profile, snapshot slot, conversation, volatile tail, each data block in untrusted-data delimiters) |
| `router`, `research_log`, `outline`, `summaries`, `cli_context`, `gui_context_inspector` | Per-request routing (R0–R4: privacy rule first, override, deterministic signals, context-size thresholds R-rev, metadata scoping and ranking R-meta, escalation R1→R2→R3→R4 or abstention after verified absence, the read-only R3 tool loop); `context_assembly.assemble` applies the token budget to the gathered items and `inspector_payload` describes them ("What the AI saw"); the PRISMA-style research log and conversation export; the outline with counts and binder metadata; opt-in RAPTOR-style chapter and book summaries in the user cache keyed by block content hashes; the `route`, `outline`, `verify-citations` and `summaries` commands; the inspector panel of the AI tab |
| `grounding` | Grounding records from native citations (Anthropic custom-content documents; Gemini and Cohere adapters) and `[[cite:ID]]` tokens; the verification cascade (stage 1 quote and fuzzy; stage 2 is `grounding_entailment`; stage 3 is `grounding_judge`), `split_claims` for the model-based stages; the writer's citation audit and re-anchoring of changed sources; the record a citation chip opens |
| `search_query`, `ranking`, `vector_store` | Shared search syntax compiled to safe FTS5 expressions, with RapidFuzz suggestions; `Hit`, reciprocal-rank fusion, de-duplication, MMR and the reranker interface; vector storage behind one interface: float32 BLOBs with exact cosine (numpy when installed) up to about 10,000 vectors, and the optional `sqlite-vec` backend (binary quantization, then exact float re-scoring) above that. LanceDB is a documented escape hatch, not implemented |
| `local_models`, `local_inference`, `gui_local_models` | Optional extra `byauthorx[local-models]`. `local_models` is the pinned model catalogue (`resources/models.json`), the explicit resumable download with size and SHA-256 verification, the settings (Fast or Accurate) and `byax models`; it imports without the runtime and reports `not installed`. `local_inference` is the ONNX Runtime embedder (`LocalEmbeddingProvider`, an `EmbeddingProvider`) the cross-encoder and ColBERT rerankers (`ranking.Reranker`) and `EntailmentScorer` (a T5 classifier for stage 2). `gui_local_models` is Tools > On-device search… and the semantic-search prompt |
| `contextual` | The opt-in job that writes a short situating note per chunk through a provider alias (local, or remote with `library_embeddings` consent) into a column that is searchable but never source text |
| `citations`, `citation_render` | Source records, CSL-JSON/BibTeX/RIS exchange, citation marks and APA/MLA/Chicago rendering |
| `grounding_entailment` | Cascade stage 2: the on-device entailment check (`register_stage('entailment', ...)`). Splits a claim into sentences and clauses, scores each piece against the cited text with the catalogue model (HHEM-2.1-Open through `local_inference.EntailmentScorer`), and reports `supported`, `partial` or `unsupported` per piece and for the claim; categories only, the score never leaves the module. Abstains where stage 1 decided; `not_run` with the reason when the model or runtime is absent. Used by `check --citations --entailment`, `verify-citations --entailment`, `ProviderRegistry.chat(entailment=True)` (replies and proposed edits) and the AI tab and Checks tab underlines |
| `llm_rerank` | Optional LLM reranker (a `ranking.Reranker`) for thematic R3 queries: deterministic `classify` of the request, a configured provider alias under the same consent rules as the judge (remote needs authorization and `library_passages`; `local_only` text never goes to a remote model), candidates framed as untrusted data, a validated order only, score source `llm-rerank`; used by `router.gather(llm_reranker=)` and `route --inspect --llm-rerank` |
| `grounding_judge` | Cascade stage 3: an optional LLM judge that must be a different provider from the generator, sees only low-confidence records under existing consent (`library_passages`, never `local_only` text to a remote judge), frames the claim and source as untrusted data and answers `supported`/`partial`/`unsupported` with a short rationale; used by `ProviderRegistry.chat(judge_alias=)` and `check --citations --judge` |
| `citation_lookup`, `bibliography` | Outside lookups (Crossref, DataCite, OpenAlex, Wayback, linked sites) through `httpx` with the `citation_lookup` consent per service, polite-pool etiquette, per-host rate limits, timeouts and a user-cache answer cache; the offline Retraction Watch index; and the bibliography health report (DOI syntax, registry metadata comparison with RapidFuzz, retractions, citations against the list, density, reference age, style-specific punctuation, dead links). Recorded-fixture transport in `tests/bibliography_fixtures.py` |
| `ris`, `csl_styles`, `zotero` | RIS reading and writing; additional CSL styles downloaded from the CSL repository on explicit action (URL, size and SHA-256 shown first, hash re-checked on every use) or imported from a file; the read-only Zotero local API client, item and annotation import (linked notes with page locators) and `.bib` export with citation keys |
| `excerpts`, `cli_bibliography`, `gui_bibliography` | Live excerpts (`excerpt` marks with a source back-pointer and anchor selectors; unanchored, never erased, when the source goes), the where-used view and excerpt checks; the `bibliography` and `excerpt` commands; the health panel, Zotero import, where-used and citation-styles dialogs |
| `project` | Manuscript binder inside one `.byax`: binder items bound to heading IDs, queries (tree, word counts, compile inclusion, item to block map), consistency report, the `project_*` canonical operations, interactive move/drop planners and the opt-in synopsis proposal |
| `goals`, `drafts` | Writing goals, sessions and streaks (local preferences keyed by document identity, never in the document) and named drafts built on branches with scene-level comparison |
| `gui_project`, `gui_goals`, `gui_drafts` | Binder panel (outline and corkboard, drag and keyboard reordering, synopsis editing), writing-goals button and dialog, draft save, compare and bring-changes dialogs |
| `compile`, `gui_compile` | Compile pipeline: compile profiles (presets in `resources/compile` are settings collections), the transient compiled document built by `engine.apply` on a copy, per-target export through the existing exporters, Word/Markdown round-trip verification, preflight findings; File → Compile… |
| `project_index` | The manuscript as a corpus: one block-level index (schema 2 conventions) of the open document, sibling `.byax` files and series members, shared with the binder (block map, item, chapter, kind); incremental by block hash, in the user cache; project find, saved searches (`saved_search_*` operations), backlinks and unlinked mentions, optional vectors |
| `codex`, `series`, `cli_codex` | Codex entries (aliases, excluded phrases, case sensitivity, traits, hide from AI) and the `codex_*` operations; Aho-Corasick mention scan, heatmap, AI-visible views and redaction, fact proposals; `.byaxseries` files shared by several projects; the `codex` and read-only `entity` commands |
| `provenance`, `disclosure`, `cli_provenance`, `gui_provenance` | Claim-level provenance (context engine 10): the optional per-span origin log (typed, AI-generated, AI-edited, pasted) remapped by `anchors` like every other anchor, AI claims carrying grounding records that re-anchor on both sides, the per-section report; APA, MLA and Chicago AI-use disclosures and the optional, unsigned C2PA sidecar manifest; the `provenance` command; the origin tint (View > Show text origin) and Tools > Provenance… |
| `version_pins`, `continuity` | Version-pinned citations: library and `.byax` draft sources pinned by content hash, superseded versions reported in the citation audit with record and re-pin proposals; the advisory `continuity` check suite comparing simple attribute statements (eyes, hair, hair style, age; lexicons in `resources/continuity`) with the codex facts in force (`codex.facts_at`), across books through the series |
| `profiles`, `statistics`, `table_io`, `text_merge`, `proof`, `fonts`, `formats`, `transport_policy` | Section profiles and reusable settings; writing statistics; CSV tables; grapheme-safe paragraph merging; contact sheets and overlays; font identity and embedding; file-view projections; outbound transmission policy |
| `gui_*` | Focused GUI pieces split from `gui`: AI tab, libraries, providers, review (Checks tab), readability report and pacing graph, configure, ribbon, views, workflows, workspace, document settings, accessibility, and brand art/ink/motion/icons |
| `grammar`, `language_packs`, `gui_languages` | Offline grammar tiers behind one engine interface: data-driven built-in rules (`resources/grammar`) and an adapter for a local LanguageTool server (loopback only, explicit pinned download, UTF-16 to scalar offset mapping); versioned, checksummed language packs for spelling dictionaries, grammar rules, word lists and interface strings (installed under the user data folder, verified before use, built-in English packs), the translation catalogue and the `QTranslator` plumbing; the Languages dialog and the Checks tab tier status |
| `docx_review`, `gui_docx_review` | Word review round trip: review copy (Track Changes, restriction, package ID, identity bookmarks, comment threads) pinned as peer `review:<id>`; returned-copy reader (revisions, moves, formatting, comments, located losses) and three-way planner that turns editor changes into `docx_review` proposals and comment threads; File menu actions |
| `beta_readers`, `review_html`, `review_pdf`, `gui_beta_readers` | Beta-reader round trip: reader copies (a single self-contained HTML page with offline commenting, a read-only companion page, a PDF with a review footer, or the Word copy restricted to comments) pinned like Word packages; the strict `byax-comments` file schema; re-anchoring of each comment with its three selectors against the pinned snapshot and mapping onto the current document; `add_comment` attributed to the reader and one `beta_reader` proposal per suggestion; PDF annotation import through the layout's page map; File menu actions |
| `sync_safety`, `gui_sync` | Synced-folder detection, risk report, conflicted-copy discovery and merge-as-proposal (`engine.import_branch` + `propose_merge`), explicit archive of merged copies; the notice bar and review dialog |
| `platform_support`, `gui_platform` | Platform seams read at call time so tests can patch `sys.platform`: macOS font folders and scan, full fsync, lock identity (case and Unicode folding), best-effort parser limits; Finder open events, application-menu roles and native key names |
| `updates`, `samples`, `gui_onboarding` | The one manual update request (GitHub Releases API, proxy-aware, never at startup), the bundled sample project and its safe copy, and the Help menu glue: first-run tour, sample, update check |
| `extensions`, `extension_worker`, `gui_extensions` | Extension layer (E4): strict `byax-extension.json` manifest (`api_version` 1) with scopes `read`, `checks`, `propose`, `export-hook`; install only by explicit local action that shows the archive/folder SHA-256 and the requested scopes and requires both back; per-file hashes recorded at install and re-checked; data checks (word lists and regular expressions, evaluated in-process with runaway-pattern probes) and script checks and export hooks run out of process through `extension_worker` (shared `platform_support.apply_parser_limits`, process group, wall-clock timeout, output caps, scrubbed environment); script output validated strictly as a whole; findings under family `extension:<id>` in the `extension` suite; Tools → Extensions… |
| `macros` | Saved command macros (user config or `settings.macros`): placeholders, automatic `expect_revision` chaining, dry run, stop on first error, transcript; each step through `cli.execute` after `transport_policy.check_provider_admin` (unless the local CLI passes `--allow-admin`) and, behind REST, the service path policy; Tools → Macros… |
| `brand`, `resources/` | Bundled identity, fonts, tokens and baked UI art; `resources/samples/` holds the generated sample project (`scripts/build_sample_project.py`) |

## Mutation and persistence

Clients inspect an identity and revision, construct an operation envelope, optionally dry-run it, and apply it with preconditions. Operations evaluate a candidate copy. Failed validation or preservation leaves the original unchanged. Successful operations advance history and record a request identity when one is supplied. Repeated identical committed requests return the recorded result; changing the payload for a reused identity is a conflict. Desktop edits omit replay identities because they are not retried remotely.

The desktop treats `history_limit` as a soft cap: it compacts older unnamed revisions while retaining every named checkpoint and branch head, plus the latest half of the configured limit. Undo/redo lists retain only available revisions. The API retains its hard cap and explicit `compact_history` operation. Failed desktop flushes leave pending text in the editor for correction or retry. Nonstructural history deltas omit unchanged block order; readers also accept older deltas containing the full order.

`set_settings` cannot replace protection rules or requirements, or disable active tracking. Removing a protection uses `unprotect(target, kind)` and changing tracking uses `set_tracking(enabled)`; these actions require local desktop confirmation. The desktop passes the trusted library option `allow_policy_changes=True` for these explicit operations. CLI, MCP and REST do not expose that option, and the untrusted envelope's `origin` grants no authority. History restore cannot weaken retained protection or tracking. Use `protect` and `set_requirement` for ordinary policy setup.

An editor holds an OS-backed document lock for its session. Headless mutation commands hold the same lock across read, validation, and write. GUI saves use their existing owner identity. This implements a clear conflict rather than silently mutating disk behind an unsaved editor. Advisory OS locks cannot prevent an unrelated application that ignores them from writing the file; the GUI separately detects external file changes.

Lock files live in the user's ByAuthorX cache directory, keyed by a hash of the resolved document path. They persist there to keep a stable lock inode across competing processes; no lock sidecars are created beside documents or exports. Restart older app processes when upgrading from sidecar-based locking. Separate OS users have separate cache directories and do not coordinate through these locks.

## Format versions and migration

Packages declare `format_version` (package layout, currently 1) and `schema_versions.document` (document schema, currently 3) in `manifest.json`; `document.json` repeats the document schema. Supported versions are the current schema plus every version with a registered transition in `migration.TRANSITIONS`:

| Transition | Change |
| --- | --- |
| 1 → 2 | Adds the required `dependencies` object. Image blocks whose `source` is an external URI (and Markdown `external_images` entries) become explicit linked dependency records (`status: unresolved`) referenced by `attrs.link`; the original `source` value is kept. |
| 2 → 3 | Adds the optional top-level `project` binder and `codex` (both absent until created; a version 2 document that already uses either name for something else is rejected), the optional `history.drafts` records of named drafts (draft name to note, save time, revision and words; absent until a draft is saved, so nothing is rewritten) and the optional citation-mark `anchor` object (context engine 6.5): `{quote, prefix, suffix, position: {start, end}, content_hash?, state?}`, the source-side selectors for re-anchoring (`prefix`/`suffix` up to 32 characters of source text, `position` in the cited chunk, `state` `anchored` or `unanchored`). The mark's own `prefix`/`suffix` keep their meaning, rendering affixes such as `see `, and are only checked to be text (a non-text affix is rejected rather than rewritten). No data changes; a version 2 mark that already uses `anchor` for something else is rejected. The report counts marks with an anchor and marks with affixes. Also optional and absent until used (context engine 10): the top-level `provenance` log `{version: 1, records, spans: {object_id: [[start, end, record]]}, claims}` (a version 2 document that already uses the name is rejected), codex trait `valid_from`/`valid_to` positions (binder item, heading or block identity, or `book:N`), the source `byax` pointer `{path, document_id, revision, content_hash}` for citing another `.byax` draft and its `superseded_by` record, and `settings.provenance` (false turns recording off). The optional `excerpt` mark (context engine 9, A2) is also part of schema 3: `{type: "excerpt", value: source ID, start, end, quote, locator?, anchor?, unanchored_reason?}` marks text taken from a source and keeps its back-pointer; removing the source sets its `anchor.state` to `unanchored` and never removes the text; a version 2 document that already contains an `excerpt` mark is rejected rather than rewritten. The report also lists `marks.excerpt` with count 0. |

Policy:

- Each transition is explicit, deterministic (no clock or random identities) and moves exactly one version forward. Upgrades chain transitions until the current schema is reached.
- Opening an older package migrates it in memory only. The live document and every retained history snapshot/delta state are upgraded; deltas are re-encoded against migrated states so every revision, checkpoint and branch head stays restorable. Request records, revision identities and document identity are unchanged.
- The report (`storage.open_document`, `migrate_package`, `pack`/`unpack` results) lists every transformation; a compact summary with the source `document.json` hash is appended to `metadata.migrations`.
- Before an upgraded document is accepted, identities, schemas and asset hashes are revalidated and the migration must not introduce dangling asset, dependency, citation, comment, proposal, tracked-change or bookmark references; otherwise it fails with `migration_failed` and nothing is written.
- Originals are preserved. Opening never writes. The first save that replaces an older-schema package first writes a byte-identical copy beside it as `<name>.schema<N>-<sha256 prefix>.byax`. `storage.migrate_package(source, destination)` writes an upgraded copy and refuses to replace the source or an existing destination.
- Unknown constructs: unknown top-level document keys are preserved through migration and saves. Constructs that cannot be upgraded safely (for example a version 1 document already using `dependencies` for something else), unknown block types, unknown manifest fields or package members, and newer schema versions are rejected explicitly. Saving over a package written with a newer schema is refused, so an older build never downgrades a newer file.
- Source folders follow the same rules: `pack` migrates older source folders deterministically and `unpack` writes the current schema; history, replay records and caches stay omitted by default.

Adding a schema version requires a transition function, a historical fixture generated by the previous release (see `tests/fixtures/schema-v1-*` and `tests/fixtures/schema-v2-*`), and tests proving restore of migrated history, preservation of unknown constructs and rejection without destructive saves.

## Manuscript projects (binder)

A manuscript is still one `.byax` master. The optional `project` object is
`{"items": {item_id: item}, "target_words"?: int}`; each item is
`{id, heading, kind, status, synopsis?, target_words?, label?, include_in_compile?}` with `kind` one
of `part`, `chapter`, `scene`, `note`, `front`, `back`. The text stays in the body. Binder order is
the body order of the bound headings and nesting follows heading levels, so a block belongs to the
innermost item whose section (its heading up to the next heading of the same or a higher level)
contains it. `project.block_map` exposes that mapping (own and section block IDs per item, in binder
order) for the shared project index. Item IDs from `project_init` and splits are derived from heading
IDs, so binding is reproducible. Synopses are authored text only.

Binder edits are canonical operations (`project_init`, `project_add_item`, `project_remove_item`,
`project_move`, `project_split`, `project_merge`, `project_set_status`, `project_set_target`,
`project_set_meta`). Move reuses section moves, split reuses `split_block` plus an inserted heading,
and merge removes the next item's heading: comments and bookmarks on that heading move to the seam
(the merged item's first block) and comments are marked `needs_review`; a pending tracked change in
the removed heading blocks the merge. Headings removed or demoted by other edits leave their item
*orphaned*: validation still passes, `engine.apply` returns `project_orphans`, and
`project.reconcile` lists orphaned items, unbound headings and blocks outside every item. Orphans are
rebound (`project_set_meta` with `heading`) or removed explicitly, never dropped. Exports write the
binder as headings and report kinds, statuses, targets and synopses as located `binder_metadata`
losses; clean publication drops the binder with the other metadata.

### Binder views, goals and drafts

The Binder panel (`gui_project`) is a view over this structure, not a second model: it rebuilds from
`project.items` after every revision and sends every change through the window's `commit`, so a
reorder is one `engine.apply` envelope (one revision, one undo step). `project.plan_step` (keyboard:
up, down, outdent, indent) and `project.plan_drop` (drag: before, after, into) turn an interaction into
that operation list: one `project_move` plus `set_attributes` (and `apply_style` when the heading's style
tracks its level) so heading levels follow the new nesting, refused when a level would leave 1 to 6 or a
section would land inside itself. Dragging and keyboard use the same planners, so neither can do what the
other cannot. The editor document is updated in place for binder-only edits: `project` is not a layout
input (`layout._render_inputs`), so a status or synopsis change does not rebuild the text.

*Goals* (`goals`) split by who owns the fact. Targets about the manuscript (document and item) are binder
fields (`project_set_target`) and travel with the file. The daily and session goals and the session
records are local preferences in `goals.json` under the user config directory, keyed by document
identity and replaced atomically; they never enter the document, its revisions or its history. A session is
the word count at open and now (authored counter, `statistics.counts`; `--format` uses the publication
projection); `written` is the non-negative growth, days add their sessions (bounded to 100 sessions and
1,000 days per document), and streaks are derived from the day totals. `status` and `history` are read-only
over every transport; `set-daily`/`set-session` are local only (`transport_policy.check_provider_admin`,
and REST gives the `goals` command the service-owned config folder).

*Named drafts* (`drafts`) are branches with a name and a note. `save_draft` records the current revision
as a branch head and fork point (`history.branches`, `history.forks`) and keeps the working branch;
`history.drafts[name]` holds the note, time and word count. Retention is the existing branch-head
retention, so history compaction keeps drafts. `compare_drafts` reconstructs both states
(`engine._revision`), matches binder items by identity and compares each item's own blocks, using
`compare.compare` on those blocks for the edit counts and summaries of a changed item and
`compare._moved` for reordering. `bring_operation` is `propose_merge`, so bringing changes is a
reviewable proposal with the usual conflict handling.

*Synopsis drafting* (`project.draft_synopsis`) is the only model-assisted piece: the chosen provider
alias receives the item's own text (bounded to 12,000 characters, head and tail when longer) as untrusted
context with a fixed instruction, through `ProviderRegistry.chat` in chat mode, so remote authorization and
payload preview are unchanged. The reply becomes a `propose` payload containing one `project_set_meta`
edit; the synopsis exists only after the proposal is accepted.

## Compile pipeline

`compile.build(master, profile, target)` deep-copies the master, drops editing state that is never
output (history, replay records, review items, layers, protections, locks, tracking and the binder
object) and turns the copy into a *compiled document* with canonical operations through
`engine.apply`: excluded items are deleted, `order` uses `move_section`, styles are defined, binder
headings become numbered openers, scene headings become separators, the first paragraph after an
opener gets character-style marks for the drop cap and lead-in, and generated front and back matter is
inserted. Print uses one section break per opener (so each opener is its section's first page and
the `first` header and footer variants suppress the running heads) with `start: odd_page` or
`next_page`; Word uses `page_break_before`; EPUB uses explicit `split_at` points. Generated block IDs
are counters (`compile_<kind>_NNNN`), the EPUB `modified` date comes from the master and nothing reads
the clock, so the compiled document's hash, the EPUB bytes and the PDF page breaks repeat.

Layout additions used by book design (all optional layout keys, default off): `gutter` (added to the
inside margin), `mirror_margins` (swap inside and outside on versos), `page_parity: physical` (recto
and verso, odd/even section starts and header variants follow the sheet, not the folio, so a
restarted folio 1 still lands on a recto), `blank_page_regions: false` (inserted blank pages carry no
running head or folio; blank pages are not counted as a section's first page and belong to the
sequence they complete), a block `sink` (space above a block that opens a page), `bleed` and
`crop_marks` (PDF sheet enlarged around the trim), `region_font`, `region_font_size`,
`region_small_caps` and `region_color`, and `small_caps` and `letter_spacing` character values. The
PDF report adds `page_starts` (the block that opens each page body). The Word adapter now writes and
reads `{page}`/`{pages}` as PAGE/NUMPAGES fields, a distinct first-page header or footer, region
alignment, line spacing, style alignment and page break before.

## Codex and the project index

The optional `codex` object is `{"entries": {entry_id: entry}}`; an entry is
`{id, kind, name, aliases?, excluded?, case_sensitive?, description?, traits, hide_from_ai?, tags?,
created, updated, provenance?}` with `kind` one of `character`, `place`, `item`, `term`, and each
trait `{id, name, value, hide_from_ai?, provenance?, valid_from?, valid_to?, created, updated}`
(`valid_from`/`valid_to`: the binder item, heading or block from which and through whose section the
fact holds, or `book:N` for a series fact; `codex.facts_at` answers which facts are in force where). Entry identities may not
collide with body objects or binder items, so proposals can target them (`engine._find_reviewable`)
with hash preconditions. Edits are the canonical operations `codex_add`, `codex_update`,
`codex_remove`, `codex_add_trait`, `codex_update_trait` and `codex_remove_trait`; dates come from
the edit envelope's timestamp. `settings.series` (`{"path": "<relative>.byaxseries"}`) and
`settings.saved_searches` (`{name: {query, scope, kind?, item?, created, updated}}`) are validated
settings; saved searches change only through `saved_search_set` and `saved_search_remove`.

`project_index` is derived data in the user cache keyed by the project folder. It never writes to
the project or a document, reads only local files, and is refreshed explicitly (CLI calls refresh
before answering; the desktop after the first project search and on save).

## Review anchors, proposals and branches

Text anchors (passage comments and their replies, bookmarks, proposal edit ranges and tracked changes) are remapped by every splice, split, join and move. Mutation results include `anchor_mappings` and `identity_changes` when anchors move. A passage whose text is wholly deleted becomes `unresolved`; partially overlapped passages become `needs_review`; deleted targets become `orphaned`.

Tracked changes are range-level records (`insertion`, `deletion`, `replacement`, `formatting`) attributed with author, origin and timestamp from the edit envelope. `accept_changes`/`reject_changes` resolve a selected group atomically, rejecting later edits first.

Proposals record per-edit identities and dependencies (explicit, plus IDs created by earlier edits and earlier edits of the same target). `accept_proposal`/`reject_proposal` accept an optional `edits` selection that must be dependency-consistent. Acceptance revalidates original object hashes, or for edits the engine itself remapped, the exact anchored text.

Revisions record `history.parents` and compact `history.provenance`. `switch_branch` makes another branch head the working document without creating a revision. `propose_merge(source)` finds the nearest common ancestor (falling back to the branch fork point), plans a field-level three-way merge as a proposal and lists conflicts; `resolve_conflict` chooses ours, theirs or a custom value. Merges never commit without proposal acceptance. `import_branch(doc, other, name)` brings a diverged copy of the same document (for example a sync conflicted copy) into history as a branch without a new revision, so the same `propose_merge` and `resolve_conflict` flow merges it; a different document is refused.

## Request identity retention

Each document keeps full replay records for the newest `settings.request_retention` requests (default 1000). Older records become fingerprint tombstones that still replay identical requests as `already_committed` (with `result_available: false`) and still reject mismatched reuse. Tombstones are kept for ten times the retention count; identities beyond that window are forgotten and the loss is disclosed by `request_status`, `inspect --section requests` and the `request_retention` field of later results. Records survive reopen and history compaction. Uncommitted or unsaved work has no record, so a retry after an interruption applies once. Source-folder unpack and clean publication omit replay records. Transactions are owned by the process holding the document lock; a second writer fails with `locked` instead of joining another client's transaction.

## Packages

Packages contain `manifest.json`, `document.json`, and content-addressed asset members. Import validates member names, sizes, hashes, schemas, and identities before making a document available. Native persistence stages output and replaces it atomically; `save_document(expected_stamp=...)` refuses, under the lock and just before the replace, to overwrite a file that changed since it was read. Truncated, empty and placeholder files fail with a clear `invalid_input` and are never modified. See [synced folders](user-guide.md#working-in-synced-folders). Source folder exports omit history and replay records by default.

## Providers and trust boundaries

Document text and model output are data. Neither can select executables, enable remote retrieval, configure secrets, or execute editor commands. Provider configurations are local settings and hold keyring/environment references, not embedded credentials. Remote workflows require a selected provider and explicit transmission policy. The runtime has no model polling and no telemetry.

MCP and REST reject `providers configure`, including wrapped job submissions, before creating configuration or job state. Configure aliases through the local CLI or desktop; existing selected aliases remain available to transport clients.

Network response fixtures exercise adapter contracts; they do not establish live model compatibility. A connection test distinguishes reachable credentials/endpoints from verified inference. Timeouts and interrupted remote calls can have uncertain outcomes and are not automatically repeated.

## On-device models

Semantic search and reranking can run on the user's computer. The runtime (`onnxruntime`, `tokenizers`) is the optional extra `byauthorx[local-models]` and ships in both installer variants; model weights ship only in the standard variant. A pinned catalogue (`resources/models.json`) is the single source of truth: name, version, licence (re-verified from the primary source), upstream commit, mirror asset, size and SHA-256 of each file. Models live under the user data directory, are downloaded only by an explicit command or button after the source, size and checksum were shown, resume from partial files and are hash-checked before they are loaded. There is no background download, update check or telemetry.

A library opts in per library (*Enable on-device semantic search? Nothing leaves this computer.*): its embedding setting becomes `local:<model>` and `LocalEmbeddingProvider` (not remote, so `local_only` libraries may use it) feeds the existing incremental embedding rebuild. Search quality is Fast (the default) or Accurate: Accurate reorders the fused top 30 to 50 hits with a local reranker, falls back to Fast with a notice when the runtime or model is missing, and reports each moved hit's previous rank. Administrative actions (`download`, `remove`, `configure`, `semantic-enable`) are refused by MCP and REST like provider configuration.

Chunker v3 merges short paragraphs up to a token target (400 by default, per library `chunk_tokens`), never crosses a heading, keeps the heading path in the indexed form only and gives every chunk a `parent` pointer for expanding a hit into its section. Contextual notes are stored in their own table and full-text column; embeddings include them, `chunks.text` never does.
## Extensions and trust boundaries

Extensions (see [extensions](extensions.md)) cross the process boundary in four places, each narrow and
validated:

| Direction | What crosses | Enforcement |
| --- | --- | --- |
| Engine → script check | JSON with `targets` (id, block, type, text, language) and the check's declared `options`; intentional passages excluded; scrubbed environment | Only the `read` scope sends text; no metadata, comments, history, sources, libraries, settings or paths |
| Script check → engine | Findings JSON on stdout | Parsed as UTF-8 JSON without `NaN`; unknown keys, bad ranges, unknown targets, `error` severity, replacements without `propose`, more than 1,000 findings or more than 4 MiB reject the whole output; a replacement string, never operations; failure is `failed` coverage and an `incomplete` report |
| Engine → export hook → folder | Read-only copy of the exported file and the loss report; a staging folder | Hook output is collected from the staging folder only (plain files, safe names, count and size limits) and copied to the folder you chose; the master is never passed |
| Extension → REST service | A bearer token derived from the service token | `read` (read-only commands) and `propose` (text-edit proposals with `expect_revision`, author stamped) only; bound to an installed, enabled, intact extension and expiring |

Installing, enabling, disabling, removing and issuing tokens are administrative (local CLI and desktop
only), like provider configuration. Installed extensions are code you chose to run; they are not
sandboxed, and the limits are resource limits (memory and CPU where the platform enforces them, always a
wall-clock timeout and output caps), not a security boundary against a malicious author. Macros add no
privilege: each step passes the same administrative and path checks as a request over the transport it
arrived on.

## Rendering limits

`QTextDocument` supplies native bidirectional shaping and text pagination. The current renderer explicitly reports features it cannot faithfully resolve. See the acceptance matrix for section columns, sophisticated object wrapping, footnote placement, and font reproducibility coverage. A serialization success alone does not establish validated publication.

## Shared-history merge bases (0.2)

Before distributing a document copy, pin its current revision with a canonical
operation such as `{"type":"pin_merge_base","peer":"editor-laptop","revision":"rev_…"}`.
Pins are stored in `history.peer_bases` and survive save/reopen. Update the pin
only when that peer acknowledges a newer shared base. Removing a peer is explicit:
`{"type":"unpin_merge_base","peer":"editor-laptop"}`.

Both explicit and automatic history compaction retain peer pins and branch fork
points, as well as named checkpoints and branch heads. The compaction report lists
`pinned_revisions_retained` and `removed_revisions`. Pins can exceed the soft
history limit; storage is not silently reclaimed at the expense of a known peer.
Unknown revision pins fail atomically. A missing ancestry path fails with a
structured `missing_target` error; merge no longer guesses a branch fork as its
base. Old copies distributed without a pin may need manual reconciliation.

The Word review round trip (`docx_review`) uses this mechanism. Sending a review copy applies
`pin_merge_base` for the peer `review:<id>` together with a `set_metadata` record in
`metadata.review_packages` (`revision`, `snapshot_hash`, `restrict`, `reviewer`, the copy's
`sha256`, `status`, `returns`). The document is saved before the DOCX is published. Merging a returned
copy recomputes the review projection of the pinned revision and checks its hash. It then aligns the
returned paragraphs to the snapshot by their `_BX…` bookmarks, falling back to text, and plans
`propose`, `add_comment`, `reply_comment` and `resolve_comment` operations. Comment operations accept
an optional stable `id` and `date` for this purpose. Changes on a paragraph that is also moved, joined,
deleted or reformatted share one proposal. Inside a proposal, text edits run from the end of each
paragraph backwards, so recorded offsets stay exact. A pending proposal edit whose target changes
without moving its range is re-anchored, so proposals on the same paragraph can be accepted in any
order. Removing a pin (`unpin_merge_base`) makes the package unmergeable; merge refuses rather than
guessing a base.

Beta-reader copies (`beta_readers`) use the same records. An HTML, PDF or Word reader copy pins the
revision for `review:<id>` and records the package with its `format`; the snapshot hash is the digest
of the same block units the Word copy hashes, recomputed from the pinned snapshot when comments come
back. The HTML page carries the review ID, document ID and snapshot hash and writes them into the
comment file. A reader's comment file is untrusted: it is validated against a closed schema, each
comment is re-anchored in the snapshot text (`quote_match.reanchor` with the position, quote, prefix and
suffix selectors) and mapped to the current text through the Word merge's word-level change map, and
only `add_comment`, `reply_comment`, `propose` (kind `beta_reader`) and the package record are planned.
A companion copy takes no comments and records nothing.

These operations are local history policy, available through canonical apply.
A future hosted implementation (#81) must authorize pin changes at the host and
maintain the registry from authenticated sync acknowledgements. Host authorization
and live peer discovery are not provided by the local engine.
