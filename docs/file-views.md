# File views

**Status: implemented subset of Phases 1–2; acceptance remains bounded.** The registry, pure projections, desktop selector, source previews, output-policy filtering, hidden-content guards, save offers and Markdown opening choices are implemented. See [the acceptance matrix](acceptance-matrix.md#file-view-evidence--14-october-2026) for named tests and limits. Phase 3 and the explicitly pending original-replacement proposal remain deferred. The requirements below remain the design source, not a claim that every format feature has passed exhaustive round-trip acceptance.

A **file view** lets a writer look at, and keep editing, the current document as it would exist in another file format. The `.byax` document stays the single master and holds everything. Choosing TXT, Markdown, DOCX and so on hides what that format could not carry, shows the rest the way it would be saved, and offers only the editing commands that format can express. Edits made in any view are written to the master.

## 1. Terms

| Term | Meaning |
| --- | --- |
| **Master** | The canonical `.byax` document: blocks, marks, attrs, styles, layout, review, layers, history. It is a superset of every export format. |
| **File view** | A read/edit lens over the master for one target format. UI state only; it is never stored in the document. |
| **Full view** | The ByAuthorX view (`byax`). Shows everything, including the hand-drawn marks. The default. |
| **Projection** | The pure function `project(master, view)` that produces what the view shows. It never mutates the master. |
| **Capability registry** | The single table of what each format can carry and how unsupported content degrades. Views, exports and loss reports all read it. |
| **Hidden** | In the master but absent from a view because the format cannot carry it. |
| **Degraded** | Present in a view in a simpler form (a heading shown as plain text, a table shown as tab-separated text). |
| **Backing file** | The file on disk a session is saving to. For a `.byax` session this is the `.byax`; for an in-place Markdown session (section 8.3) it is the `.md`. |

## 2. Decisions

| # | Decision | Source |
| --- | --- | --- |
| D1 | Save always writes the `.byax`. After an explicit save, a dismissible, non-blocking offer proposes exporting to the active format. | Product owner |
| D2 | No new underline types. The hand-drawn pen strokes remain an editor-only rendering and appear in the Full view only. | Default assumption: the question was withdrawn ("never mind"), so nothing new is specified. Confirm. |
| D3 | TXT and Markdown get a Rendered/Source toggle. | Product owner |
| D4 | Opening a Markdown file imports it into a `.byax` by default. Users can opt out and edit the `.md` in place. | Product owner |
| P1 | An export may replace the imported original only after an explicit confirmation. **Pending confirmation; conflicts with the product specification as written** (section 8.2). | Proposal |
| P2 | The TXT Source mode shows invisible characters and the encoding/line-ending settings. Low priority; drop it if it adds nothing. | Proposal |
| P3 | Every export and every view applies the same projection, including authored per-output policies (section 5.3). | Proposal |
| P4 | An untouched import is not "dirty", so closing it does not prompt to save (section 8.3). | Proposal |

## 3. Goals and non-goals

### Goals

- Show, while writing, exactly which parts of a document a chosen format would keep, simplify or drop.
- Let the writer edit from any view without damaging content the view cannot show.
- Keep one source of truth for "what survives in format X", shared by views, export and loss reports.
- Make the same information available to the CLI, MCP and library (specification 1 and 2.3: people and agents get equivalent access).

### Non-goals

- No change to the `.byax` schema, so no migration (see section 11).
- No claim that DOCX, ODT or RTF views reproduce Word's or another application's fonts or pagination (section 5.4).
- No new export formats in the first release. HTML and ODT are already export formats, so they get views at no extra cost. (EPUB was added later through the same registry.)
- Views do not restrict the engine. CLI, MCP and REST clients keep full access to the master (an opt-in `apply --view` is proposed in section 9).
- No view state in the document, history or revisions.

## 4. Views

| Id | Label | Pagination | Fidelity class |
| --- | --- | --- | --- |
| `byax` | ByAuthorX (full) | Paged and flow | Everything |
| `pdf` | PDF | Paged | **Exact**: the Qt layout that draws the preview is the layout that writes the PDF. Fonts still depend on the installed font set. |
| `docx` | Word (.docx) | Paged | **Approximate** |
| `odt` | OpenDocument (.odt) | Paged | **Approximate** |
| `rtf` | Rich Text (.rtf) | Paged | **Approximate** |
| `html` | HTML | Flow | Semantic |
| `epub` | EPUB (.epub) | Flow | Semantic: reflowable EPUB 3. Read-only view; no source mode, since the export is a ZIP package |
| `md` | Markdown | Flow | Semantic (dialect `commonmark+tables`) |
| `txt` | Plain text | Flow | Semantic |

Fidelity classes decide the label shown with the view:

- **Exact:** no label beyond the format name.
- **Approximate:** "Approximate preview. Fonts and page breaks may differ in other applications."
- **Semantic:** no pagination is shown.

The view list is generated from the registry, not hand-written, so a format added to `EXPORT_FORMATS` cannot be forgotten. Aliases (`markdown`, `htm`) are not separate views. `doc` is import-only and has no view. `epub` was added with the EPUB exporter (`byax.epub`): it needed only a registry entry, a ribbon and the `epub` branch of the loss rules in `exchange`, and `tests/test_file_views.py::test_view_list_in_this_document_matches_the_registry` fails when this table and the registry disagree. A table-only CSV view is still a candidate.

## 5. Behavior

### 5.1 Selecting a view

- A **File view** drop-down in the toolbar and a **View → File view** submenu list the views above. The drop-down has an accessible name and is keyboard operable.
- The status bar always shows the backing file and the active view, for example `notes.byax · Viewing as Markdown · 4 features hidden`.
- Switching views never creates a revision, never clears undo or redo, and never changes the master (specification 3.1: "Switching views must not create unrelated content changes"). Scroll position and the caret's block are preserved where that block is visible.
- The last-used view for a document is remembered per document in the application settings (keyed by the document id), never in the document. New and never-viewed documents open in the Full view.
- Motion follows the existing contract: a brief fade, immediate under **Reduce motion**.

### 5.2 Projection

`project(doc, view, mode='rendered')` returns a candidate document and a report. Rules:

1. **Block ids are preserved.** The editor maps edits to the master by block id, so every projected block keeps the id of its master block. Projection never invents ids for editable blocks.
2. **Hidden blocks are omitted.** Block types the view cannot carry (for example footnote, TOC, section break and page break in Markdown) are dropped from the projection.
3. **Hidden marks are removed** from the projected block. The text is untouched.
4. **Hidden attributes are removed** (for example alignment in Markdown).
5. **Degradation** follows the registry's rules (section 5.5).
6. **Review state** is carried only if the view's format carries it (section 5.7).
7. The projection is pure and deterministic and has no Qt dependency. Rendering is done afterwards by the existing `layout.build_document`.

The report lists, with located targets, everything hidden or degraded, using the same loss codes the exporters use (`inline_formatting`, `structural_object`, `paragraph_format`, `table_flattened`, `heading_flattened`, `image_not_embedded`, `list_subset`, `named_styles`, `page_layout`, and so on). It must agree with `conversion_preview` for the same document and format (section 12).

### 5.3 Authored output policies

Blocks and layers can already carry `outputs` and `exclude_outputs` (validated in `model.py`, applied by `layout.output_view`). The projection treats the view id as the output name, so a block marked "PDF only" or "exclude from Markdown" behaves consistently in the view and in the export.

As far as could be determined from reading the code, only the layout path accepts an output name; `exchange.export_document` filters only on `excluded`. **Before the view ships, exports of every format must apply the same projection (P3)**, otherwise the view would show something the saved file does not contain. This is a behavior change to the exporters and needs its own tests.

### 5.4 Fidelity and honesty

- The view follows what **our exporter writes**, not what the format could hold in theory. DOCX supports tracked changes; the DOCX exporter writes comments only, so tracked changes are hidden in the DOCX view.
- If an exporter gains a capability, the registry changes with it. A round-trip test (section 12) fails when they disagree.
- DOCX, ODT and RTF views are drawn by ByAuthorX's own layout. They show the right features but not the target application's fonts, metrics or page breaks, and are labeled as approximate.

### 5.5 Capability registry

One registry entry per format. Fields:

| Field | Content |
| --- | --- |
| `id`, `label`, `extensions` | Identity |
| `fidelity` | `exact`, `approximate` or `semantic` |
| `paged` | Whether a page view applies |
| `blocks` | Supported block types, each with a mode: `full`, `degraded` (editable, simpler) or `locked` (shown simplified, read-only) |
| `marks` | Supported inline mark types |
| `block_attrs` | Supported paragraph attributes (alignment, spacing, indents, tabs, list, level and so on) |
| `styles` | Whether named styles and inheritance survive |
| `images` | `embedded`, `reference_only` or `alt_text_only`, with allowed MIME types |
| `tables` | Features that survive (header rows, spans, widths, cell formatting) |
| `lists` | `full`, `simplified` or `flattened` |
| `layout` | Page geometry, masters, headers/footers, columns, sections |
| `review` | Which of comments, tracked changes, proposals, findings survive |
| `degradations` | Rules such as `heading → paragraph`, `table → tab-separated text` |
| `commands` | Mapping from editor commands to the capability each needs (section 5.8) |

Initial values are transcribed from the current declarations in `exchange._exchange_losses` and `exchange.markdown_unrepresentable` (summarized in Appendix A). The Phase 1 round-trip tests verify them against the real exporters; **where an exporter and the table disagree, the exporter wins and the table is corrected.**

The registry lives in a new headless module (`byax.formats`). `exchange._exchange_losses`, `exchange.markdown_unrepresentable` and the hard-coded `formats` list in `cli._cmd_capabilities` are refactored to read from it. That refactor must be behavior-preserving, checked by golden loss reports captured before the change.

### 5.6 Hidden-content indicator

- The status bar shows a count: "4 features hidden in this view".
- Activating it opens a list of located items (what, where, which format limit). Each item navigates to its block when it is visible, or offers "Show in Full view".
- The list is the projection report, so it matches the export loss report.
- The count updates as the document changes. It is plain text with an accessible name, never colour-only.
- An edit that leaves the view with no visible content while hidden content remains shows a non-modal notice: "N hidden items remain in this document. Switch to the Full view to see them." This addresses the case where a writer selects all, deletes, and believes the document is empty.

### 5.7 Review, layers and document policy

- **Review.** The Review panel keeps showing the master's comments, proposals and findings. Items the view's format would not carry are tagged "not in this format". Comment anchors on hidden text stay attached.
- **Tracked changes and protections.** `settings.track_changes`, `protections`, `requirements` and locked layers are document policy. They apply identically in every view. An edit in the TXT view with tracking on is a tracked change. A view never grants an edit the Full view would refuse.
- **Find, spelling and checks** operate on the master. Results in hidden content are listed with a "hidden in this view" tag and offer "Show in Full view". They are never silently skipped.
- **Hand-drawn marks** apply in the Full view only (still subject to **View → Hand-drawn marks**). In every other view, underline and strikethrough render as the exact lines the export would contain.

### 5.8 Editing through a view

**Principle.** An edit made in any view must be equivalent to the same edit made in the Full view, and must not alter anything the view hides.

**Editable versus locked.**

- A block is **editable** only if its projected text equals its master text. Text edits are then localized insert/delete operations (`gui_editor.text_operations`), which preserve marks and anchors outside the edited range, including marks the view hides.
- A block is **locked** (shown read-only) when projection changes its structure or text: a table in TXT, an image shown as a placeholder, a table with merged cells in Markdown, a generated TOC. Editing through a degraded rendering could write placeholder text into the master.
- Degrading an *attribute* (heading level, style, alignment, a hidden mark) leaves the block editable. Degrading *structure* locks it.
- The existing guard in `collect_changes` ("Use the object or table commands…") enforces locked blocks, and a locked block is restored if the user types over it.

**Command gating.** Each editor command declares the capability it needs, for example:

| Command | Capability |
| --- | --- |
| Bold, Italic | `marks.bold`, `marks.italic` |
| Underline | `marks.underline` |
| Font, size, colour | `marks.font_family`, `marks.font_size`, `marks.color` |
| Align, spacing, indent | `block_attrs.alignment`, `block_attrs.space_*`, `block_attrs.indent` |
| Heading styles | `blocks.heading` |
| Insert table, merge cells | `blocks.table`, `tables.spans` |
| Insert footnote, TOC, section, page break | `blocks.footnote`, `blocks.toc`, `blocks.section_break`, `blocks.page_break` |
| Embed image | `images.embedded` |

Unsupported controls are disabled, with a tooltip and accessible description such as "Not available in the Markdown view". Gating is a GUI convenience. The engine is not restricted by views.

**Edit rules.** These are proposed and must be confirmed against `anchors` and the engine's splice behavior in Phase 2. Each becomes a test.

| # | Situation | Rule |
| --- | --- | --- |
| E1 | Typing at the edge of a range whose mark is hidden in the view | The inserted text is placed outside the hidden range, so it carries no hidden marks. Typing strictly inside a range uses the engine's normal splice rule (the range grows). |
| E2 | Enter in a heading when the view has no headings (TXT) | The new tail block is converted to `body`; in views that support headings, existing behavior is unchanged. |
| E3 | New paragraph after a visible block that is followed by hidden blocks | Inserted immediately after the preceding visible block, before the hidden blocks. This matches the editor's existing `insert_paragraph after=previous` behavior and is deterministic. |
| E4 | Backspace or Delete that would join two visible paragraphs with a hidden block between them | Blocked, with a status message: "A hidden item sits between these paragraphs. Switch to the Full view to join them." As I read `collect_changes`, its join scan stops at an unrendered block, so the following block would be deleted instead of joined and its marks and anchors lost. Not yet reproduced; write the failing test first. |
| E5 | Deleting a selection that spans a hidden block | The selected visible content is deleted. The hidden block remains and the hidden count updates. |
| E6 | Select all, then delete | Same as E5, plus the notice in section 5.6. |
| E7 | Typed Markdown syntax such as `**x**` in the Rendered Markdown view | Stays literal text. No auto-formatting. The Markdown exporter escapes it so it round-trips as literal text. |
| E8 | Paste | Plain text only, as today. Rich paste goes through the bounded import adapters. |
| E9 | Undo and redo | Edits through a view are ordinary revisions. View switches are not. |

**Invariant to prove with tests.** For any sequence of edits made in view V, every master block and mark that V hides is byte-identical afterwards, except where the edit's range overlaps it.

### 5.9 Rendered and Source modes

Formats with a textual form get a **Rendered | Source** toggle next to the view selector.

| View | Rendered | Source |
| --- | --- | --- |
| Markdown | Styled text as it would render, editable under section 5.8 | The exact Markdown the export would write, monospace |
| HTML | Styled text | The HTML source |
| RTF | Styled text | The RTF source |
| TXT | Normal text | Monospace with tabs and line endings made visible, and the detected encoding and line-ending style shown (P2) |

- **Source is read-only in the first release.** It is generated from the projection with the real exporter, so it shows what would be written.
- For a document imported from Markdown, unchanged blocks reproduce the original source (the exporter's localized rewrite).
- Editable Source mode is a later phase. It would parse edits back through the existing `apply_markdown_source` path and needs its own design for undo and anchors.
- The existing **View → Markdown source…** dialog stays.
- No toggle for DOCX, ODT, PDF or the Full view.
- Paged views have no Source mode; they have the page preview.

### 5.10 Paged and flow views

- Paged views (`pdf`, `docx`, `odt`, `rtf`) pair with the existing print-layout preview. The preview is drawn from the projection, using the view id as the output name (`draw_page(..., output=view)`).
- Flow views (`html`, `epub`, `md`, `txt`) have no pages. The print-layout toggle is disabled with the explanation "This format has no pages". The Full view has both.
- The page preview is paint-only today, so editing happens in the writing view, filtered by the active view.

## 6. Save and export offer (D1)

1. **Save** (**File → Save**, Ctrl+S) always writes the `.byax` through the existing background save. Nothing about saving changes.
2. When an **explicit** save completes, an in-window, non-modal notice appears if either condition holds and the user has not suppressed it:
   - the active view is not `byax`, or
   - the document was imported from another format.
3. Wording: "Saved. Export as Word (.docx) too? 3 features won't carry over." Actions: **Export…**, **Details** (the located list), **Not now**, and **Don't ask again** (this document, or always).
4. If both conditions apply, the active view's format is offered. If only the second does, the original format is offered.
5. The notice does not take focus, does not auto-dismiss (so keyboard and screen-reader users are not timed out), is announced as an alert, and is reachable by keyboard. The same action is always available as **File → Export…**.
6. **Export…** opens the native save dialog with the document's name, the format's extension and the document's folder, then calls `export_document` (staged, atomic, with the loss report). Losses are shown through the existing report dialog.
7. Suppression is a user preference, not document content. It lives in the application settings: global `export/offerAfterSave`, and per document keyed by the document id. Autosave and background saves never show the notice.
8. Not in the first release: remembering the last export target per document for one-click re-export.

## 7. Replacing the imported original (P1)

`export_document` currently refuses a destination equal to the imported original (`metadata.import.original_path`), with "Conversion must preserve the imported original; choose a separate output path". This follows specification 4.2: "The application shall preserve the original file during conversion."

For a document imported from `notes.md` and viewed as Markdown, the natural export target is the original file, which the engine rejects.

**Proposal.** Allow it only after an explicit confirmation ("Replace the original notes.md?"). The engine gains an explicit `replace_original` option, off by default, exposed in the CLI as a flag and never inferred. If this is rejected, the offer proposes a sibling name (for example `notes-export.md`), and writers who want edits written back to the file use the in-place Markdown mode (section 8.3).

This contradicts the specification as written, so the specification needs a deliberate amendment. It should not be implemented quietly.

## 8. Opening files

### 8.1 Import by default (D4)

- Every format except `.byax` opens as an import into a new, unsaved `.byax` document. This is already true for TXT, DOCX, ODT, RTF, HTML and DOC.
- **Markdown changes.** Today `.md` and `.markdown` keep the file as the backing file and save back byte-faithfully (`gui.open_path`, `gui._save_markdown`). Under D4 they are imported like the others, with the Markdown source retained as import provenance (`metadata.import`).
- The import compatibility report appears when it has losses, as today. The original file is never modified.

### 8.2 Choosing at first open

The first time a user opens a Markdown file, a one-time chooser appears:

- **Open as a ByAuthorX document** (default, recommended)
- **Edit the .md file directly**
- "Remember my choice" (checked by default)

The choice is stored as the application setting `files/markdownOpenMode` (`ask`, `import`, `inplace`) and can be changed in the application's preferences. A hidden setting alone is not enough, because a user who expects to edit a README in a repository must be able to see the choice.

### 8.3 In-place Markdown mode (the opt-out)

The existing behavior is kept unchanged: byte-faithful, localized source rewrites, the dialect declaration, and the "Convert to ByAuthorX?" prompt in `gui.commit` for edits the dialect cannot carry (specification 4.1, 4.2).

Additions:

- The view selector shows **Markdown** and disables the others, with the tooltip "Convert to a ByAuthorX document to use other views". A **Convert to ByAuthorX…** command is offered (the existing Save As path).
- Command gating for the Markdown view applies, so the conversion prompt becomes rare.
- No export offer is shown (the backing file is already the target format).

### 8.4 Untouched imports (P4)

`gui.open_path` currently marks every import dirty (`self.dirty = self.path is None`). With Markdown now imported, opening a README only to read it would prompt "Save changes?" on close. An import with no edits is treated as clean for the close prompt. **Save** on it still goes to Save As, with the suggested name `<name>.byax` in the source folder. This applies to every import format for consistency.

## 9. Interfaces

The specification requires equivalent access (2.3). The parity inventory (`transport_policy.INTERFACE_INVENTORY`, `docs/interface-parity.md`) is enforced by `tests/test_interface_parity.py`, so new operations must be added to both.

| Operation | CLI | MCP | Library | GUI |
| --- | --- | --- | --- | --- |
| Format capability registry | `byax capabilities --topic formats` | `byax_capabilities` | `byax.formats.FORMATS` | File view drop-down |
| Project a document into a view | `byax view FILE --as md [--source]` (new, read-only) | `byax_view` | `byax.formats.project` | View → File view |
| Hidden/degraded report | `byax view … --json`, `byax convert --dry-run` | `byax_view`, `byax_convert` | `byax.formats.project`, `exchange.conversion_preview` | Status bar count and list |

- `byax view` returns the format, its fidelity class, the projected outline or text (the exact export text with `--source`), and the located hidden and degraded items. It never writes.
- The hard-coded `formats` list in `cli._cmd_capabilities` is replaced by the registry.
- **Optional, later:** `byax apply --view md` would reject operations whose effect is not representable in that view (`unsupported_in_view`), letting agents edit "as if in Markdown". Without the flag nothing changes.

## 10. Architecture

| Area | Change |
| --- | --- |
| `byax/formats.py` (new, headless) | Registry, `project()`, hidden/degraded report, capability queries for command gating |
| `exchange.py` | `_exchange_losses`, `markdown_unrepresentable` and the export paths read the registry. Exports apply the same projection (P3). Optional `replace_original` (P1). Behavior-preserving otherwise. |
| `layout.py` | `output_view` and the `output` plumbing receive the view id. `hand_drawn_marks` applies in the Full view only. |
| `gui.py` | File view selector and Rendered/Source toggle, status-bar chip and hidden list, export-offer notice, Markdown open chooser and preferences, untouched-import handling, in-place mode lock. `refresh()` builds from the projection. |
| `gui_editor.py` | View-aware command gating, locked blocks, key guards for E4, notices. `collect_changes` is unchanged in principle: it already diffs only the blocks that were rendered (`object_text`), which is what keeps hidden blocks safe. |
| `cli.py`, `mcp.py`, `transport_policy.py`, `docs/interface-parity.md` | `view` command, capabilities topic |

The projection is cached by `(revision, view)`. Incremental editing (`layout.update_document`) must keep working on projected documents.

## 11. Data and storage

- **No `.byax` schema change and no migration.** Hidden content never leaves the master.
- The view state, the Rendered/Source choice, the export-offer suppression and the Markdown open mode are application settings, not document content.
- If a later phase adds anything to the document (for example a saved export target), it needs a schema transition, a historical fixture and the migration tests required by `docs/architecture.md`.

## 12. Verification

Automated tests are the acceptance evidence. Statuses in the acceptance matrix start as "missing / unverified" and change only when the named test exists and passes.

| Area | Test |
| --- | --- |
| Registry | Every format in `EXPORT_FORMATS` has an entry. Every block type, mark type and paragraph attribute known to the model is classified for every format (exhaustiveness, so a new type cannot be forgotten). |
| Refactor safety | Golden loss reports for the existing fixtures are identical before and after `exchange` reads the registry. |
| **Round-trip agreement** | For a corpus covering each feature, `project(doc, fmt)` matches the normalized result of `import_document(export_document(doc, fmt))` for text, block types, marks and registry attributes, for every format with an importer (TXT, MD, HTML, DOCX, ODT, RTF). PDF has no importer; its view is checked structurally against the shared layout path. This is the test that stops the view from misdescribing a save. |
| Report parity | The projection's hidden/degraded list agrees with `conversion_preview` (codes and targets). |
| Output policy | `outputs` and `exclude_outputs` behave identically in the view and in the export (P3). |
| Edit invariant | Seeded randomized edit sequences made through `collect_changes` in each view leave hidden blocks and hidden marks byte-identical outside the edited range (section 5.8). |
| Edit rules | A focused test for each of E1 to E9, including E4 (the join across a hidden block). |
| GUI | Switching views creates no revision and keeps undo. Selector state. Command gating and tooltips. Locked blocks. Hidden count and list. Export offer shown once, and suppression persists per document and globally. Markdown chooser and stored mode. Untouched import is clean. In-place mode locks the selector. |
| Interfaces | `tests/test_interface_parity.py` passes with the new inventory rows. `byax view` returns the same report as the library. |
| Accessibility | Accessible names for the selector, toggle, count and notice. The notice is keyboard operable and not timed. No colour-only state. Reduced motion respected. |
| Performance | Measure typing and refresh on the benchmark documents with `scripts/benchmark.py` before and after. The project makes no typing-latency claim without measurement, and none is made here. |

Not claimed by any of the above: physical Windows 11 or Ubuntu 24.04 behavior, screen-reader behavior, or appearance in Word, LibreOffice or any other application.

## 13. Phases

**Phase 1: see it (read-only).**
Registry and refactor. `project()`. File view selector and status bar. Hidden count and list. Rendered/Source toggle (read-only). Page preview for paged views. Export offer after save. Markdown import by default with the chooser and in-place opt-out. Untouched imports clean. Exports apply the shared projection. CLI `view`. Round-trip, report-parity and refactor tests. In this phase edits in a non-Full view are limited to plain text edits of editable blocks, and other commands are disabled.

*Done when:* the round-trip and parity tests pass for every format, switching views creates no revision, and the acceptance matrix rows are recorded honestly.

**Phase 2: edit through it.**
Command gating from the registry. Locked blocks. Edit rules E1 to E9. Edit-invariant and rule tests. Resolve E4.

*Done when:* the edit invariant holds under the randomized tests and every rule has a passing test.

**Phase 3: polish and reach.**
Editable Source mode. Remembered export targets. `apply --view`. A table-CSV view if wanted. (The EPUB view shipped with the EPUB exporter.)

## 14. Risks

| Risk | Mitigation |
| --- | --- |
| The view drifts from the exporters | One registry, plus the round-trip test as a hard gate |
| Hidden content surprises writers | Persistent count, list and the empty-view notice (5.6) |
| Edits through a degraded rendering corrupt the master | Locked blocks; editable only when projected text equals master text |
| Join and delete across hidden blocks lose marks or anchors | Block E4 first; test before relying on `collect_changes` |
| Approximate views read as exact | Fidelity label (section 4); only PDF is page-exact |
| Three concepts (document, view, exported file) confuse | Status bar always names the backing file and the view |
| Opt-out hidden in settings | First-open chooser (8.2) |
| Replacing the original conflicts with the specification | P1 stays pending; fallback is a sibling name |
| Refactoring `exchange` regresses loss reports | Golden reports before the change |
| Added filter pass slows large documents | Cache by `(revision, view)`; measure |

## Appendix A. Initial capability values

Transcribed from `exchange._exchange_losses` and `exchange.markdown_unrepresentable`. **These are starting values to be verified in Phase 1, not facts about the exporters.** "—" means not carried.

### Inline marks

| Mark | pdf | docx | odt | rtf | html | md | txt |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bold, italic | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| underline, strikethrough | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| superscript, subscript | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| font family, font size | ✓ | ✓ | ✓ | — | — | — | — |
| colour | ✓ | ✓ | ✓ | — | — | — | — |
| highlight | ✓ | — | ✓ | — | — | — | — |
| code | ✓ | — | — | — | ✓ | ✓ | — |
| link | ✓ | ✓ | ✓ | — | ✓ | ✓ | — |

### Blocks and structure

| Feature | pdf | docx | odt | rtf | html | md | txt |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Headings | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | flattened |
| Lists | ✓ | simplified | simplified | simplified | simplified | ✓ | simplified |
| Tables | ✓ | subset | subset | subset | subset | subset | flattened (tab-separated) |
| Embedded images | ✓ | ✓ | ✓ | PNG/JPEG | ✓ | reference or alt text only | alt text only |
| Page break | ✓ | ✓ | — | ✓ | — | — | — |
| Footnote, TOC, section break | ✓ | — | footnote with reference only | footnote with reference only | — | — | — |
| Paragraph alignment | ✓ | ✓ | — | ✓ | — | — | — |
| Spacing, indents | ✓ | ✓ | to verify | to verify | to verify | — | — |
| Named styles | ✓ | ✓ | ✓ | ✓ | — | — | — |
| Page geometry and masters | ✓ | no loss declared (masters to verify) | no loss declared (masters to verify) | size, margins, one header/footer | — | — | — |

### Document-level state

| State | pdf | docx | odt | rtf | html | md | txt |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Comments | — | ✓ | — | — | — | — | — |
| Tracked changes, proposals, findings | — | — | — | — | — | — | — |
| References, fields | — | — | — | — | — | — | — |
| Layers | ✓ | — | — | — | — | — | — |

Cells marked "to verify" are not declared as losses by `_exchange_losses` for that format, so either the exporter carries them or the loss is currently unreported. The round-trip test decides which, and an unreported loss is a bug to fix in the exporter's report, not a reason to show a capability the file does not have.

### EPUB (added after the first release)

The `epub` column is not in the tables above because it was declared in the registry from the exporter's behaviour (`byax.epub`) rather than transcribed. Carried: bold, italic, underline, strikethrough, superscript, subscript, code, links, font family and size, colour, highlight and character styles; headings (the outline becomes the navigation document); nested bullet and ordered lists (restart and start); tables with header rows and spans; embedded PNG, JPEG, GIF and WebP images with alt text; footnotes that have a reference (as EPUB 3 notes); named styles (as CSS); paragraph alignment, spacing and indents. Reported as losses: page and section breaks, the table of contents block, footnotes without a reference, page geometry, review state, links that leave the book, unsupported image types and images without alt text. See [compatibility](compatibility.md).
