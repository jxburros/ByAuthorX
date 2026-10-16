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
| `libraries`, `library_parser`, `library_checks`, `retrieval`, `embeddings` | Attached local research libraries, indexing (index schema 2: chunks with heading and fingerprint, porter FTS5 with a heading column and prefix indexes, trigram FTS5, `fts5vocab`), retrieval orchestration with per-library method selection and verified absence, incremental embeddings and library-backed review checks (see the [library guide](library-and-citations-guide.md)) |
| `quote_match`, `context_assembly` | Quote normalization, exact/normalized/near alignment and three-selector anchors (RapidFuzz); offline token estimates, token budgets over the character caps, and the Ollama `num_ctx` plan with its `limit_exceeded` refusal |
| `search_query`, `ranking`, `vector_store` | Shared search syntax compiled to safe FTS5 expressions, with RapidFuzz suggestions; `Hit`, reciprocal-rank fusion, de-duplication, MMR and the reranker interface; vector storage behind one interface (float32 BLOBs in the index cache today) |
| `citations`, `citation_render` | Source records, CSL-JSON/BibTeX/RIS exchange, citation marks and APA/MLA/Chicago rendering |
| `project` | Manuscript binder inside one `.byax`: binder items bound to heading IDs, queries (tree, word counts, compile inclusion, item to block map), consistency report and the `project_*` canonical operations |
| `profiles`, `statistics`, `table_io`, `text_merge`, `proof`, `fonts`, `formats`, `transport_policy` | Section profiles and reusable settings; writing statistics; CSV tables; grapheme-safe paragraph merging; contact sheets and overlays; font identity and embedding; file-view projections; outbound transmission policy |
| `gui_*` | Focused GUI pieces split from `gui`: AI tab, libraries, providers, review (Checks tab), readability report and pacing graph, configure, ribbon, views, workflows, workspace, document settings, accessibility, and brand art/ink/motion/icons |
| `sync_safety`, `gui_sync` | Synced-folder detection, risk report, conflicted-copy discovery and merge-as-proposal (`engine.import_branch` + `propose_merge`), explicit archive of merged copies; the notice bar and review dialog |
| `brand`, `resources/` | Bundled identity, fonts, tokens and baked UI art |

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
| 2 → 3 | Adds the optional top-level `project` binder (absent until created; a version 2 document that already uses `project` for something else is rejected) and optional text `prefix`/`suffix` fields on citation marks. No data changes: existing citation affixes are already text, and a non-text affix is rejected rather than rewritten. The report counts citation marks carrying the fields. Note: `cite` already stores rendering affixes (such as `see `) under these names, so the context-engine 6.5 selectors (32-character context) need a decision on meaning or separate keys before re-anchoring ships. |

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

These operations are local history policy, available through canonical apply.
A future hosted implementation (#81) must authorize pin changes at the host and
maintain the registry from authenticated sync acknowledgements. Host authorization
and live peer discovery are not provided by the local engine.
