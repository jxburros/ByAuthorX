# ByAuthorX Product Specification

Version 1.0 | October 13 2026 | Product owner JX Holdings, LLC

ByAuthorX is a native desktop word processor for writing, formatting, reviewing, dictating, and publishing documents. It combines an approachable graphical editor with a complete command line interface and agent interfaces. Its native editable document format is `.byax`.

The product serves basic to intermediate Word and Pages use cases while making documents inspectable, testable, versioned, and programmable. Markdown, rich text, page layout, layers, broad import, configurable checking, and transcription belong to one integrated product. Every functional requirement below is part of that product specification; the document does not divide capabilities into releases or delivery stages.

## 1 Product goals

- Provide a responsive writing application that people can use without learning commands, source formats, or AI terminology.
- Support practical letters, resumes, reports, essays, manuscripts, newsletters, press kits, and documents generated from templates.
- Give people and agents equivalent access to structured editing, inspection, checking, history, previews, and export.
- Preserve authorship through configurable suggestions, protected passages, explicit edits, and reviewable changes.
- Keep editing and deterministic document functions available locally without requiring an account, subscription, or AI connection.
- Support user selected hosted models, API keys, local models, and external transcription processes.
- Make advanced functionality discoverable without putting every feature in the everyday writing interface.

Lean behavior means low idle activity, responsive interaction, modular integrations, and understandable controls. It does not mean dropping the layout, review, or automation features specified here.

## 2 Platforms and application form

### 2.1 Native Windows application

ByAuthorX shall run as an installed native Windows desktop application. The editor shall use a desktop application framework and rendering surface rather than a browser editor. A website, PWA, Electron style browser shell, or WebView based writing interface does not meet this requirement. HTML remains a supported exchange format, not the application architecture.

Windows integration shall include file dialogs, clipboard, drag and drop, installed fonts, microphone selection, native printing, file associations for `.byax`, keyboard navigation, accessibility services, and appropriate display scaling. Installation shall supply the GUI and CLI without requiring a developer environment. Editing shall not require a local HTTP server.

### 2.2 Linux desktop target

Linux is a concurrent desktop target where the chosen framework and dependencies permit it. The shared document engine, native format, CLI, provider contracts, and core behaviors shall remain portable from the outset. Linux shall use desktop file dialogs, system fonts, microphone and printing facilities, accessibility integration, and desktop file associations.

The exact supported Windows versions, Linux distributions, architectures, and packaging formats are implementation decisions that require an explicit compatibility matrix. Platform specific gaps must be identified individually; Linux shall not be replaced with a hosted web version.

### 2.3 Shared engine and interfaces

The GUI, CLI, public library interface, MCP tools, and optional local REST interface shall call the same document engine and canonical operation schema. The headless CLI and MCP interfaces shall work without a desktop session. A local REST interface is an integration boundary, not a web application, and is disabled unless configured.

## 3 Everyday editor

### 3.1 Writing and navigation

The editor shall provide reliable typing, cursor movement, selection, keyboard shortcuts, Unicode text input, input method composition, clipboard operations, drag and drop, undo and redo, find and replace, and document navigation. Find and replace shall support scoped search, case and whole word options, regular expressions when explicitly selected, and a replacement preview for broad changes.

Users shall be able to navigate by headings, sections, bookmarks, comments, findings, tables, figures, and pages. The outline shall stay synchronized with document headings. Word count, character count, selection counts, estimated reading time, and document statistics shall distinguish authored content from the selected published output.

The editor shall offer a continuous writing view and a paginated print layout view. Markdown documents shall additionally offer source and visual views. Switching views must not create unrelated content changes.

### 3.2 Formatting and styles

Supported formatting shall include fonts, sizes, color, bold, italic, underline, strikethrough, highlighting, superscript, subscript, hyperlinks, paragraph alignment, line spacing, paragraph spacing, indentation, hanging indents, and tab stops. Lists shall support bullets, numbering, nested levels, continuation, restart, and stable editing across paragraph splits and joins.

Named character and paragraph styles shall support inheritance and deliberate local overrides. Applying or changing a style shall preserve content and report affected elements. Users shall be able to clear direct formatting without deleting text. Common styles shall include title, subtitle, body, heading levels, quotation, caption, code, and list styles.

### 3.3 Recovery and files

Autosave, crash recovery, explicit save, save as, recent documents, and detection of external file changes shall be provided. Saved changes shall use atomic replacement or an equivalent recoverable persistence strategy. A failed save must not corrupt the prior document.

An open GUI document shall maintain one authoritative working state. CLI or service edits to that document must coordinate through the editor session or fail with a structured conflict. They must not silently edit a disk copy behind unsaved GUI changes.

## 4 Markdown and rich documents

### 4.1 Standard Markdown

Markdown support shall include headings, emphasis, lists, links, images, quotations, code blocks, thematic breaks, and a documented table extension. The Markdown dialect and supported extensions shall be explicitly declared. Source edits shall preserve meaningful whitespace, line endings, and source syntax where possible; visual edits shall make localized source changes rather than reformatting the whole file.

A Markdown file remains a standard `.md` file. Formatting that cannot be represented in the declared dialect shall require explicit conversion to `.byax` or an explicitly requested companion configuration. ByAuthorX must not silently insert proprietary layout syntax into ordinary Markdown.

### 4.2 Rich structured documents

Rich documents shall represent semantic content independently from pagination. Sections, paragraphs, lists, tables, inline marks, images, captions, footnotes, bookmarks, references, and objects shall have stable identities. Reflow shall calculate page fragments rather than turn the authored document into independently editable page slices.

Conversion between Markdown and rich documents shall preview and report unsupported constructs, changes in meaning or structure, and formatting losses. The application shall preserve the original file during conversion.

Rich text is an editing capability. `.rtf` is also an import and exchange format as defined in Section 9; these are separate concepts.

## 5 Native ByAuthorX document format

### 5.1 Portable editable package

`.byax` shall be the portable native master containing editable content, layout, styles, assets, references, checking profiles, review state, and persistent history. Saving an export shall not replace or flatten the master.

The specified storage design is a versioned ZIP package with UTF-8 structured manifests and content addressed assets. A manifest shall declare format version, document identity, current revision, resource hashes, and schema versions. Package members shall be validated without trusting archive paths or file extensions. `.byax` and `.BYAX` shall both be recognized on supported platforms, with user supplied filename case preserved.

Logical components shall include:

| Component | Stored information |
| --- | --- |
| Document content | Sections, paragraphs, lists, tables, inline text, object IDs and relationships |
| Styles and layout | Style definitions, page masters, section settings, anchors and layering |
| Assets | Embedded images, permitted fonts, and optionally retained audio |
| Review | Comments, tracked changes, proposals, accepted exceptions and findings |
| Checks and sources | Rule suites, profile definitions, evidence references and permitted source snapshots |
| History | Revisions, deltas, checkpoints, branches and operation provenance |
| Metadata | Title, author, language, description, tags and import provenance |

Credentials, API keys, access tokens, and executable provider configuration shall never be embedded in the package. Profile definitions may refer to local provider aliases, with a clear unresolved configuration state on another machine.

### 5.2 Dependencies and portability

Images shall embed by default. External links shall be explicit, inspectable dependencies with refresh and embed operations. Missing links shall produce located findings rather than silently disappear. Fonts shall carry identity and substitution information; embedding shall respect declared permissions. Missing glyphs and font substitution must be reported.

References may be stored as identifiers, excerpts, hashes, or permitted snapshots according to source and privacy settings. Audio retention is optional and separate from retaining the transcript. Resource limits shall be documented and errors must identify the exceeded limit.

### 5.3 Readable source representation

Unpack and pack operations shall convert a `.byax` package to and from a deterministic source folder. Stable ordering, readable content records, and asset hashes shall make changes reviewable in Git. Source exports shall omit volatile caches, credentials, and working history by default. This source snapshot is distinct from the portable document's internal history.

Packing shall validate schemas, identities, dependencies, asset hashes, references, and supported format versions before publishing a package. Migrations must preserve originals and report transformations. Unknown constructs must not be silently discarded during a save.

## 6 Page layout and print

### 6.1 Page and section geometry

The engine shall support Letter, A4, and custom paper dimensions; portrait and landscape orientation; margins; page and section breaks; and section specific page settings. User measurements shall support points, inches, and millimeters. Text layout shall not depend on choosing an arbitrary raster DPI; image resolution and render scale are separate settings.

Columns shall be configurable by section, including count, widths, gaps, explicit column breaks, and full width material between column sections. The engine shall support automatic pagination, widow and orphan control, keep with next, keep together where possible, and deliberate page breaks. An impossible rule shall produce a finding rather than drop text.

### 6.2 Repeated regions and masters

Page masters shall define backgrounds, repeated artwork, letterhead, and reusable geometry. Headers and footers shall be distinct document regions with first page and section settings, odd and even page options, page numbers, numbering formats, and numbering restarts.

Automatic contents tables shall derive from headings and update their page references after reflow. Bookmarks and internal cross references shall use semantic targets rather than stored page positions. Footnotes shall remain associated with their references and participate in page layout; numbering, continuation, and overflow behavior must be explicit.

### 6.3 Images and objects

Images, figures, captions, callouts, and text boxes shall support inline placement, paragraph anchors, and explicit page positioning where appropriate. Wrapping shall include inline, square, above and below, behind text, and in front of text behaviors. Anchor and movement policies shall be inspectable.

Object sizing shall preserve aspect ratio when selected. Captions and objects shall have a keep together relationship when feasible. Alternative text and logical reading order shall be independent of drawing order.

### 6.4 Preview and printing

Print preview, GUI layout, CLI rendering, and PDF output shall consume the same resolved layout. Page previews and contact sheets shall be available, with overlays and focused crops for diagnosis. Native printing shall support selected pages and ranges and disclose printer scaling or printable area differences.

Font environment differences across machines and exchange applications must be reported; exact cross application pagination is not assumed. Reproduction records shall capture the dependencies used for controlled output.

## 7 Tables and document structure

Tables shall remain semantic editable tables, not pictures or collections of unrelated shapes. They shall support row and column insertion, removal and reordering; cell text and formatting; width adjustment; alignment; padding; borders and shading; merged cells; header rows; and repeating headers across pages.

Pagination shall define whether rows may split and preserve continued cell content. Oversized cells or unsatisfiable row policies shall produce located findings. Agents shall address tables, rows, columns, and cells through stable IDs or explicit coordinates with expected state checks.

Table contents shall support CSV import and export for data interchange. Updating data must preserve unrelated table formatting. Table header semantics shall be available for accessibility and export.

Sections shall be movable as units, carrying their paragraphs, objects, comments, and references. Bookmarks, captions, and cross references shall survive ordinary edits and report deleted targets.

## 8 Layers and visual organization

### 8.1 Layer capabilities

Layering shall support backgrounds, watermarks, letterheads, illustrations, callouts, text boxes, and foreground graphics. Users and agents shall be able to name, group, lock, reorder, show, and hide applicable objects. Object IDs shall remain stable after renaming or moving.

The layer inspector shall be optional in the everyday interface. Normal body text shall remain a flowing semantic document rather than require users to manage one layer per paragraph.

### 8.2 Distinct visibility semantics

| Action | Required behavior |
| --- | --- |
| Hide a decorative object | Change visual output without changing body content flow unless wrapping requires it |
| Exclude semantic content | Remove it from the selected output and reflow remaining content |
| Show review overlays | Display comments, guides or findings without modifying document content |
| Redact content | Remove protected information from the sanitized output and its recoverable data |

Stacking order must not alter reading order. Layer lock prevents ordinary edits; semantic protection rules separately govern wording, data, references, or formatting. Layout changes caused by reflow shall not be mislabeled as violations of a text only preservation rule.

## 9 Import and export

### 9.1 Format matrix

Broad document import is a core product requirement. Imports shall produce editable content, preserve originals, offer previews, provide loss reports, and participate in history as the starting state.

| Format | Import behavior | Export behavior |
| --- | --- | --- |
| TXT | Detect supported encoding and preserve text and paragraph boundaries | Plain text with explicit encoding and line ending settings |
| MD and Markdown | Preserve supported semantic constructs and source representation | Declared Markdown dialect with conversion loss report |
| DOC | Import legacy Word content and supported structure through a defined conversion adapter | Legacy DOC export is not required |
| DOCX | Import supported Word text, styles, tables, images, page settings and review structures | Editable DOCX subset with explicit compatibility report |
| RTF | Import rich text, supported structures and embedded content | Supported rich text exchange subset with reported losses |
| ODT | Import supported OpenDocument text, styles, structure and layout | Supported editable OpenDocument subset with reported losses |
| HTML and HTM | Import document content, supported formatting, links and tables | Semantic HTML with applicable styles and resources |
| BYAX | Load the complete supported native model | Save the native editable master |
| PDF | Not a general editable import requirement | Controlled paginated output with selectable text where supported |

Import and export coverage shall separately describe content preservation, structural preservation, visual similarity, and review feature support. Unsupported features must be reported by type and location. Where possible, unsupported imported material shall be retained as an opaque attachment or provenance record rather than silently lost; the original always remains available.

Legacy DOC conversion shall be packaged or explicitly configured locally and may not depend on uploading files to an unselected service. Macros and active imported content shall not execute. Encrypted or protected input shall produce a clear supported workflow or failure state.

### 9.2 Export profiles and publication

Saved export profiles shall hold format, page selection, layout settings, required checks, review inclusion, metadata handling, and asset policies. Batch conversion and export shall return per file outcomes and stage output before publication.

PDF is the controlled print deliverable. DOCX and ODT shall preserve supported editable structure but may paginate differently elsewhere. Publication checks shall run against the actual selected content and settings. Exports shall not be presented as validated merely because serialization succeeded.

## 10 Templates and reusable settings

Templates shall capture content structure, styles, page settings, masters, table patterns, fields, terminology, voice settings, approved references, and check requirements. A template may be a `.byax` package marked with a template document kind; an additional file extension is not required.

Typed fields shall include text, numbers, dates, enumerated values, and references to permitted assets. Fields shall have required status, defaults, descriptions, and validation. Missing or invalid fields shall be identified before publication. Literal placeholder text shall not silently become accepted content.

Users shall be able to fill fields interactively or from JSON and CSV through automation. Long values and missing data shall trigger reflow and checks. A repeated generation run shall preserve its inputs and report its outputs; it shall not replace manually edited documents without an explicit operation.

Style collections, checking profiles, terminology dictionaries, source collections, and export profiles shall be reusable. Templates and shared configuration shall omit credentials. Reusable automation shall compile to bounded canonical operations and shall not secretly execute arbitrary shell code.

## 11 History and review

### 11.1 Persistent history

History shall preserve normal undo and redo, named checkpoints, revision comparison, alternate draft branches, and restoration. Structural deltas and periodic snapshots shall bound storage while preserving referenced revisions. History limits and explicit compaction shall be documented; compaction shall report which recovery capabilities and assets it removes.

Typing shall create sensible undo groups. A batch edit shall be one atomic history step unless explicitly grouped differently. Each committed operation shall record its origin, timestamp, revision, and relevant request identity. Model based actions shall record model and profile identifiers without credentials or hidden model reasoning.

### 11.2 Comments and tracked changes

Comments shall support stable passage anchors, replies, resolution, and navigation. Anchor behavior across insertion, deletion, and movement shall be defined. A deleted target shall become an explicitly orphaned comment rather than silently attach elsewhere.

Tracked changes shall separately represent insertions, deletions, and formatting edits with attribution and timestamps. Users shall be able to accept or reject individual changes or a selected group. Accepting a proposal, resolving a comment, and accepting a tracked change are distinct actions.

### 11.3 Proposals and comparisons

Proposals shall contain exact edits, preconditions, explanations, related findings, expected starting revision, and check outcomes. They shall be reviewable in the GUI and inspectable through the CLI. Selected edits may be accepted while others are rejected, but dependent edits must disclose their dependencies.

Diffs shall distinguish wording, structure, styles, layout, assets, references, and review state. Pagination movement must not look like wholesale content replacement. Comparison views shall include readable summaries and exact changes.

## 12 Unified checking system

### 12.1 Check families

| Family | Required checks |
| --- | --- |
| Spelling and terminology | Misspellings, custom dictionaries, preferred terms, names and language settings |
| Grammar and punctuation | Agreement, punctuation, sentence structure and ambiguous constructions |
| Editorial | Clarity, repetition, readability, structure, transitions and consistent voice |
| Internal consistency | Conflicting dates, numbers, names, definitions and statements |
| Source consistency | Support or contradiction relative to selected evidence |
| Requirements | Required sections and wording, prohibited terms, length limits and template fields |
| Structure and dependencies | Invalid objects, broken references, missing assets and unresolved links |
| Layout | Overflow, clipping, stranded headings, unintended blank pages and problematic overlap |
| Print | Paper dimensions, margins, printable area and placed image resolution |
| Accessibility | Heading hierarchy, descriptions, table headers and logical reading order |

Deterministic rules, local language tools, and model based reviewers shall emit compatible findings. Basic spellcheck and deterministic checks shall function without an AI provider. Check scope shall support selections, sections, the document, and batch inputs.

### 12.2 Profiles and strength controls

Profiles shall independently configure sensitivity, editing freedom, voice preservation, rule families, sources, models, and application policy. Presets shall include conservative proofreading and broader editorial review, while exposing the underlying settings.

Sensitivity controls which concerns are raised. Editing freedom controls what proposed rewrites may change. Voice preservation controls vocabulary, rhythm, informality, and intentional expression. These must not collapse into one ambiguous strength slider.

Users shall be able to mark passages as intentional, add accepted exceptions with reasons, protect text from rewriting, and apply section specific profiles. Lyric fragments, profanity, repetition, dialect, quotations, and code shall not automatically be treated as errors merely because they differ from formal prose.

### 12.3 Finding contract

Each finding shall identify its rule, family, severity, affected IDs and text range, explanation, measured values where available, evidence where applicable, optional proposed operations, and status. It shall also record document revision, profile version, checking engine or model, source versions, and actual scope evaluated.

Statuses shall include open, accepted edit, dismissed, intentional exception, stale, and needs review. A model's self reported certainty shall not be presented as a calibrated probability. Running a check shall not alter document content. Applying a repair is a separate undoable action.

Structural validity errors shall reject an invalid operation. Publication requirements may gate checked commits or export. Editorial judgments remain advisory unless an explicit policy requires review. Findings pagination must not hide overall failures.

### 12.4 Health reports and incremental checking

Document health reports shall present concrete findings and coverage, not an unexplained quality score. Reports shall include completed, skipped, failed, and unmeasurable checks with reasons.

Changes since a revision shall identify affected content and dependencies. Incremental checking shall invalidate results when relevant text, styles, layout, terminology, definitions, sources, or profile settings change. If dependencies cannot be determined safely, the engine shall expand the checked scope and report that choice rather than reuse stale results.

## 13 Source based factual review

### 13.1 Authority and retrieval

Users shall select the references against which factual claims are checked. Sources may include local documents, selected passages, reference collections, style guides, and explicitly selected websites. A profile shall declare whether references are exclusive, supplementary, or open to outside research. Outside retrieval shall not happen when the profile restricts checking to supplied material.

Sources shall carry identifiers, titles, version or retrieval date, relevant excerpts, and content hashes where available. Retrieval scope and exclusions shall be visible. The checker shall not treat document text, reference material, or transcribed speech as authorization to change tools, retrieve new sources, or execute commands.

### 13.2 Evidence outcomes

| Outcome | Meaning |
| --- | --- |
| Supported | Selected evidence supports the claim |
| Contradicted | Selected evidence conflicts with the claim |
| Not established | Selected sources do not provide sufficient evidence |
| Conflicting sources | Relevant sources disagree |
| Needs interpretation | The conclusion depends on an inference |

Missing evidence is not proof of falsehood. Agreement with a predetermined source establishes consistency with that source, not universal truth. Each finding shall connect the exact document passage to evidence and distinguish quotation, paraphrase, and inference.

Users shall be able to attach a source reference to a claim through GUI or structured operations. Updating or removing a source shall invalidate dependent findings. Source corrections and document corrections shall remain separate actions; an agent shall not change the evidence or loosen requirements merely to make its edit pass.

## 14 Models and provider configuration

### 14.1 Provider independent design

Checking, editing assistance, and transcription shall use adapters with declared capabilities rather than assume every model accepts every input or supports every workflow. Users shall be able to configure hosted APIs with their own keys, local model endpoints, local executable models, and explicitly configured external processes where supported.

Capability declarations shall cover text review, structured edit proposals, audio transcription, streaming, language support, and applicable input limits. Speech recognition and editorial models shall be independently selectable. A simple default model setting shall coexist with per check or per workflow overrides.

A managed provider option may be configured if supplied, but the product shall not require one. Provider selection, supported APIs, and local endpoints must be explicit; compatibility with a generic API format must not be represented as verified support for every provider implementing it.

### 14.2 User control and credentials

Provider settings shall include endpoint, model identity, credential reference, capability selection, timeout, retry policy, input limits, and optional cost estimates. The application shall offer a connection and capability test with clear failures. Missing configuration shall leave affected AI checks unavailable while local writing and deterministic functions remain usable.

Keys shall reside in appropriate OS credential storage or explicit local secret configuration. Shared documents, templates, source exports, logs, and context packages shall exclude secrets. Before a remote operation, users shall be able to inspect the selected text, references, and audio that would be sent. A saved explicit policy can authorize routine calls without repeated prompts.

The application shall expose available usage and cost information without inventing provider prices or presenting unavailable estimates as exact. Model outputs shall be validated before they become edit operations. Remote model results are not assumed to be reproducible; approved results shall be stored with provenance.

## 15 Transcription and dictation

### 15.1 Input and interaction

Transcription is part of the product's initial complete implementation scope. Users shall be able to dictate through a microphone and import audio files into the same processing pipeline. Audio formats shall be determined by a declared decoder and adapter capability matrix, with clear conversion or unsupported input results.

The GUI shall provide explicit start, pause, resume, and stop controls; microphone selection; recording state; processing progress; cancellation; and keyboard access. Recording shall not start implicitly. Users shall choose insertion at the cursor, a bookmark, or another selected destination.

### 15.2 Three processing modes

| Mode | Required behavior |
| --- | --- |
| Direct | Transcribe spoken wording with configurable punctuation, filler and disfluency handling |
| Spelling and grammar cleanup | Correct transcription errors, spelling, punctuation and grammar while preserving meaning and voice |
| Document aware | Use authorized document context, terminology, styles and references to produce appropriate inserted content |

Direct mode is the recognizer's transcript, not a guarantee of perfect verbatim recognition. Cleanup must preserve the unprocessed transcript for comparison during review. Document aware mode shall use only its configured context and shall not invent facts or silently rewrite surrounding content.

Users shall independently select recognition and editorial processing. An external recognizer may feed a local cleanup model or a selected hosted reviewer. Local tools and external processes shall have an explicit input and output contract, bounded execution, cancellation, and structured failures.

### 15.3 Live text and document changes

Provisional recognition shall be visually distinct from committed text. Revised provisional words shall not create a new undo entry for every recognition update. Final phrases or paragraphs shall become coherent document operations with stable insertion anchors.

Cleanup shall produce a proposal against the exact committed transcript. If the user edits that text before cleanup returns, the application shall revalidate or report a conflict rather than overwrite the user. An insertion destination that has moved or been removed shall receive equivalent protection.

Users shall choose suggestions only, automatic direct insertion, or explicitly authorized automatic cleanup. Failure of cleanup shall not discard a successful direct transcript. Interrupted sessions shall recover completed transcript segments and pending job state where possible; the application must disclose any uncaptured or unavailable audio.

### 15.4 Context and retention

Context options shall include nearby paragraphs, current section, whole document, dictionaries, writing samples, and selected references. Profile settings for voice preservation, editing freedom, and protected passages apply to transcription.

Raw transcript retention and audio retention shall be separate choices with inspectable storage impact. Optional timestamps and source segment links shall support review when the recognition process supplies them. Dictation content shall remain text; phrases such as delete this paragraph shall not execute editor commands. Voice command execution is outside the required dictation behavior.

## 16 CLI and agent interfaces

### 16.1 Command contract

The command interface shall provide human readable output and versioned machine readable JSON. The executable name is an implementation detail; examples in this specification use `byax` as the proposed command. Command and package name availability is separate from file extension selection.

Commands shall work without an open GUI and provide help and capability information without a document. UTF-8 JSON input shall be accepted through files and standard input. Progress and diagnostics shall go to standard error; machine results shall go to standard output. Machine mode shall not mix banners or unstructured messages into result JSON.

Shell pipelines and PowerShell shall be supported. Noninteractive calls shall use explicit policies for overwrite, publication, remote model use, and conflicts. They must not wait for an invisible GUI dialog. Required policy decisions shall return a structured error. Explicitly authorized work shall not require a confirmation on every tool call.

### 16.2 Command families

| Family | Required operations |
| --- | --- |
| Discovery | Version, capabilities, command schema, operation schema, supported formats and provider capabilities |
| Files | New, open or inspect, import, save as, convert, pack and unpack |
| Inspection | Outline, selected content, styles, tables, layers, assets, references, metadata and statistics |
| Search and targeting | Find text, resolve bookmarks and IDs, scope by section or object type |
| Editing | Apply canonical operations, dry run, transact, fill fields and change structured data |
| Review | Propose, list proposals, accept or reject selected changes, comments and tracked changes |
| History | List, checkpoint, branch, restore, diff and changes since revision |
| Checks | Run suites, inspect findings, propose repairs and evaluate preservation rules |
| Output | Render pages or regions, proof output, print where supported and export using profiles |
| Providers | Configure aliases, test capabilities and inspect available models without returning secrets |
| Transcription | Submit audio, select processing mode, inspect transcript and insert approved text |
| Jobs and batches | Submit, status, results, cancel, recover and per file batch outcomes |

MCP and library interfaces shall expose equivalent operations without forcing agents to emulate GUI clicks. Optional REST responses shall preserve the same contracts. Interface specific persistence behavior shall be documented.

### 16.3 Selective inspection

Inspection shall return semantic content and stable identities with optional resolved layout. Agents shall request outline only, a specific section, nearby paragraphs, a table, unresolved findings, or selected fields. Results shall disclose revision, total results, returned scope, truncation, and continuation information.

Display page numbers are navigation aids, not permanent content identities. Compact summaries shall be derived from document data; model generated summaries shall be labeled and must not replace exact content required for an edit.

### 16.4 Context packages

A context command shall collect selected content, nearby passages, relevant styles, terminology, voice settings, applicable requirements, protected regions, authorized references, and relevant findings. Packages shall include revision, source versions, provenance, and an inclusion and omission report.

Context limits shall be explicit. Required information exceeding a limit shall produce continuation options or an incomplete state. Context packaging shall not silently retrieve other user files, consult unselected accounts, or include credentials.

### 16.5 Precise text selectors

Operations shall support stable object IDs, quoted text with explicit occurrence, and typed ranges. Match selectors shall verify expected original text. Ambiguous matches shall list candidates instead of selecting arbitrarily. Deleted targets and stale revisions shall fail clearly.

For the version 1 operation contract, text offsets shall be zero based Unicode scalar value indices with end exclusive ranges. Selection boundaries shall not split grapheme clusters. Invalid boundaries shall be rejected with valid alternatives rather than silently adjusted. All clients shall use the declared coordinate system, with conversion helpers for UTF-16 based environments.

Paragraph split and join operations shall report identity changes and anchor mappings. Agents must be able to resolve affected comments, bookmarks, and proposals afterward.

### 16.6 Semantic operations

Canonical operations shall include insert, replace and delete text; insert paragraphs; move sections; apply styles; update table cells; add or remove rows and columns; place images; set anchors and wrapping; edit layers; fill fields; attach references; manage comments; propose changes; and accept or reject tracked changes.

Operations shall preserve unrelated structure by default. Unknown fields and invalid values shall return schema errors with operation index and field path. Preconditions may include document revision, expected text, object hash, and preservation rules. All operations in a batch shall succeed together or commit nothing.

### 16.7 Dry runs and protected edits

A dry run shall evaluate the proposed candidate without saving, advancing history, or publishing outputs. It shall return exact changes, affected IDs, layout effects, check outcomes, and references needed to review the candidate.

Protection shall separately cover exact wording, table values, citations, object attributes, styles, and visual regions. Text only protection allows repagination. Protected edits shall fail if their declared guarantees cannot be measured or are violated. A failed check may not be made to pass by changing its definition within the same editorial action.

### 16.8 Reliable retries and concurrency

Mutation requests shall accept a caller supplied request ID. A committed request retried with identical content shall return the recorded outcome without repeating edits. Reusing an ID with different content shall fail. Retention and recovery limits for these records shall be disclosed.

Revision checks shall prevent stale writes. Locks and atomic batches provide serialization and rollback, not automatic collaborative merge. Transactions shall define their owner and prevent independent clients from accidentally joining one another's changes. Alternate branches can be compared and merged through proposals; conflicting semantic edits require explicit resolution.

### 16.9 Result and error envelope

Machine results shall include schema version, execution state, validation state, review state, document identity, base and resulting revision, changed IDs, findings summary, actual coverage, and output references. A rendered or exported file may be completed but unvalidated.

| Field | Representative states |
| --- | --- |
| Execution | Completed, failed, cancelled, pending |
| Validation | Passed, failed, incomplete, not run |
| Review | Required, not required |

Structured errors shall include a stable code, message, field or operation location, affected target, retry guidance, and suggestions where available. Codes shall distinguish invalid input, unsupported feature, stale revision, ambiguous selection, missing dependency, provider failure, limit exceeded, failed requirement, and uncertain external outcome.

The specified CLI exit codes are 0 for completed work satisfying its configured policy; 1 for execution or dependency failure; 2 for invalid command or schema input; 3 for conflict or ambiguous target; 4 for failed required validation; 5 for required review or incomplete coverage under a strict policy; 6 for cancelled work; and 7 for pending or uncertain outcome when synchronous completion was required. Advisory findings do not force failure under a policy that permits them. JSON shall carry the authoritative detailed status.

### 16.10 Illustrative command workflow

The following shows the intended command contract rather than an existing executable implementation. A dry run does not reserve a revision; apply verifies that the document has not changed since the plan was reviewed.

```text
byax capabilities --topic editing --json
byax inspect report.byax --outline --json
byax context report.byax --target sec_summary --profile editorial --json
byax apply report.byax --input edits.json --expect-revision rev_42 --dry-run --json
byax apply report.byax --input edits.json --expect-revision rev_42 --request-id edit_001 --require-suite publication --json
byax diff report.byax --from rev_42 --json
byax render report.byax --pages affected --out proof-images
byax export report.byax --profile print --out report.pdf --require-suite publication --json
```

The editing envelope shall support schema version, request ID, expected revision, operations, preservation rules, and required check suites. An example semantic replacement is:

```json
{
  "schema_version": 1,
  "request_id": "edit_001",
  "expected_revision": "rev_42",
  "operations": [
    {
      "type": "replace_text",
      "target": "para_018",
      "match": "The old wording",
      "occurrence": 1,
      "expected_text": "The old wording",
      "replacement": "The revised wording"
    }
  ],
  "protect": [{"target": "table_release", "kind": "values"}],
  "required_suites": ["publication"]
}
```

## 17 Jobs and batch work

Long transcription, provider review, source retrieval, and batch conversion shall run as identified jobs with progress, cancellation, result retrieval, and recoverable state. Jobs shall record their frozen input revision and relevant profile and source versions. New results against changed content shall be marked stale until reconciled.

Job states shall distinguish queued, running, completed, failed, cancelled, and uncertain. Cancellation of a local job does not prove that a remote provider request stopped. Recovery shall query recorded remote identifiers when supported; it shall not blindly submit a second potentially billable request.

Batch results shall include every input's outcome, output, findings, losses, and failure details. A partial batch shall never be represented as complete success. Worker counts, memory budgets, and overwrite behavior shall be configurable. Output staging shall prevent half written deliverables from appearing as completed exports.

Reproduction records shall capture document revision, engine and schema versions, relevant rendering environment, font and asset hashes, linked source versions, export settings, and check profile versions. A reproduction command shall distinguish renderable output, matched dependencies, verified reference output, and drift or mismatch. Tolerances shall be declared for visual comparison. Stored model results may be reused as recorded inputs; a fresh model inference is not represented as deterministic replay.

## 18 Publishing and true redaction

Publication shall explicitly select whether comments, tracked changes, hidden content, reference excerpts, transcripts, metadata, and history are included. Shared deliverables shall use a clean publication profile with deliberate inclusion rules.

True redaction shall create a sanitized copy that removes selected text and recoverable copies from history, tracked deletions, comments, transcripts, source snapshots, metadata, and embedded assets as applicable. Merely drawing a black rectangle or hiding a layer does not meet redaction requirements.

The original private document shall remain unchanged unless the user explicitly chooses a destructive replacement. Sanitization shall report what was removed, what was checked, and any unsupported content requiring review. Verification shall examine the actual serialized output, not only the preview.

## 19 Architecture and portability

The core shall separate semantic content, operation validation, revision storage, layout resolution, rendering, exchange adapters, checks, model providers, transcription, and job management. GUI controls shall not contain the only implementation of editing behavior.

The layout engine shall resolve content, styles, anchors, page geometry, and typography into a shared display representation. GUI drawing, page previews, print and controlled PDF output shall consume that representation. Layout caches are derived data, not authored content.

Incremental text editing and layout shall keep typing responsive. Expensive AI review, source retrieval, rendering, and import conversion shall run outside the UI interaction path. Full document serialization and full history snapshots shall not be required on every keystroke.

The architecture shall support versioned adapters and documented interfaces. The desktop framework must meet native interaction, custom document rendering, accessibility, and Windows and Linux needs. Language, framework, rendering libraries, converter dependencies, and specific provider adapters remain engineering selections; they shall not change the defined product behavior or substitute a browser editor.

## 20 Vixl patterns to retain

ByAuthorX shall retain Vixl's principles of shared canonical operations, schema checked inputs, atomic candidate edits, stable identities, explicit assets, persistent branching history, saved requirements, actionable diagnostics, and inspect then edit then check then preview workflows.

Vixl's text flow, masters, rich formatting, source folders, and production checks provide useful design references. ByAuthorX still requires a word processing content model, interactive text selection, automatic pagination, table flow, footnotes, exchange coverage, and speech insertion behavior. Use of Vixl code is an implementation decision rather than a requirement to adopt its entire renderer or pixel coordinate model.

Reference locations:

- [Vixl architecture](https://github.com/jxburros/Vixl/blob/main/docs/architecture.md)
- [Vixl operation semantics](https://github.com/jxburros/Vixl/blob/main/docs/operations.md)
- [Vixl text flow](https://github.com/jxburros/Vixl/blob/main/docs/text-flow.md)
- [Vixl production checks](https://github.com/jxburros/Vixl/blob/main/docs/production.md)
- [Vixl agent discovery](https://github.com/jxburros/Vixl/blob/main/docs/agent-discovery.md)
- [Vixl outcomes and diagnostics](https://github.com/jxburros/Vixl/blob/main/docs/agent-trust.md)
- [Vixl source folder implementation](https://github.com/jxburros/Vixl/blob/main/src/vixl/project_folder.py)

## 21 Security and local control

Ordinary editing, native saving, deterministic checks, rendering, printing, and configured local transcription shall remain usable without internet access. Network activity shall occur through explicitly selected retrieval, provider, or update workflows. Optional telemetry shall be opt in and shall not transmit document content by default.

Import adapters shall treat files as data and bound archive expansion, asset decoding, recursive structures, linked paths, and execution time. Macros, scripts, and active embedded content shall not run implicitly. External executables are explicitly configured local integrations with clear trust and resource boundaries.

Optional local services shall default to local machine access, scope filesystem operations to authorized locations, and require an explicit access policy for broader exposure. Document material shall not override those policies. Provider secrets shall be redacted from logs and diagnostics.

Protected content is an editing constraint, not encryption or access control. A user holding an editable package may inspect its contents. Sanitized publication, rather than hidden layers, is the mechanism for removing sensitive material from a shared file.

## 22 Accessibility and performance requirements

### 22.1 Accessibility

Core workflows shall be keyboard operable, with clear focus, labeled controls, readable status messages, screen reader support, high contrast compatibility, and appropriate scaling. Dictation shall be operable without continuous mouse interaction. Markdown source and rich text navigation shall preserve semantic headings and tables.

Document checks shall identify missing descriptions and problematic structure. An accessibility check pass shall disclose the checks performed and shall not claim certification for untested assistive technology or exchange formats.

### 22.2 Performance and resource behavior

The following are proposed measurable engineering targets, not claims of achieved performance. Benchmarks shall publish hardware, document fixtures, provider configuration, and measurement definitions.

| Metric | Target and measurement |
| --- | --- |
| Cold startup | Editor ready within 3 seconds on a documented contemporary four core machine with SSD |
| Typing | Local edit feedback within 50 ms at the 95th percentile on the standard 100 page fixture |
| Reflow | Affected layout available within 500 ms at the 95th percentile for ordinary paragraph edits on that fixture |
| Basic inspection | Selected section or outline response within 500 ms on a loaded standard fixture |
| Idle behavior | No continuous model polling or active background document processing without a configured task |
| Cancellation | Local job acknowledges cancellation promptly and reports any continuing remote request |

Test fixtures shall include a 100 page mixed document and a 500 page long text document. These are required workload examples, not maximum supported sizes. Limits for assets, pages, history, input length, jobs, and memory shall be explicit and configurable where appropriate. Large documents shall degrade with progress and bounded work rather than freeze the editor or silently omit content.

## 23 Acceptance criteria

The complete product shall satisfy the following end to end scenarios. Each scenario shall be exercised through the GUI and applicable headless interfaces, with platform dependent behavior identified.

### 23.1 Everyday documents

- Create, save, reopen, edit and print a letter and resume with consistent typography and page settings.
- Create a multi-page report with headings, contents table, footnotes, tables, captions, cross references, headers and page numbers. Changes update dependent page references without losing content.
- Create a newsletter with columns, a full width heading, a wrapped image and a repeated watermark. Reflow preserves reading order and object anchors.
- Recover a document after interrupted saving or application failure without corrupting the last committed state.

### 23.2 Music press kit

Create a two page press kit containing letterhead, a flowing biography, a two column section, a release information table, an anchored cover image, and page numbers. Lengthening the biography repaginates the document. CLI updates to the release table preserve unrelated formatting. Publication checks locate unresolved fields, low resolution imagery and clipping.

### 23.3 Import and conversion

Import representative TXT, Markdown, DOC, DOCX, RTF, ODT and HTML files. Verify retained content, supported structures, and located loss reports. Save `.byax`, reopen it, and export supported editable formats and controlled PDF. Originals remain untouched. Non-Latin text, combining marks, bidirectional text and missing fonts are included in fixtures.

### 23.4 History and review

Create a checkpoint, branch a draft, make GUI and CLI edits, compare semantic changes, and restore a revision. Apply a model proposal selectively. Accept and reject tracked changes. Move a section with anchored comments and bookmarks. Deleted targets produce explicit orphaned or unresolved states.

### 23.5 Checks and evidence

Run conservative and editorial profiles against the same text and observe their distinct settings. Intentional expressions and protected passages remain respected. Check claims against exclusive references and produce supported, contradicted and not established outcomes without outside retrieval. Change a source and verify dependent results become stale.

A findings limit must not hide a failing overall status. An unavailable model or unmeasurable check shall produce incomplete coverage rather than a clean pass. A strict publication gate shall prevent publishing invalid output while ordinary advisory findings still allow writing.

### 23.6 Transcription

Dictate several paragraphs, pause to edit manually, resume, and compare direct, cleaned and document aware output. Names present in approved terminology remain consistent. Provisional updates do not flood history. Cleanup arriving after manual edits produces a conflict rather than an overwrite. An imported audio file uses the same modes and produces a recoverable job result.

### 23.7 Agents and concurrency

Inspect an outline, obtain a bounded context package, target a quoted phrase, dry run a structured edit, protect table values, run checks, commit, compare, preview affected pages, and export. An ambiguous phrase fails with candidate locations. A stale revision fails. Retrying an already committed request does not insert content twice.

Attempt a CLI edit while the GUI contains unsaved changes and verify coordinated editing or a clear conflict. A proposal cannot change its required checks to conceal a failure. A document containing instruction-like text cannot cause unauthorized source retrieval or command execution.

### 23.8 Publication and reproduction

Export with a clean profile and confirm the selected inclusion policy. Create a truly redacted copy and verify removed text is absent from recoverable history and serialized output. Record engine, fonts, assets, source versions and export settings for reproduction. Compare reference renderings with declared tolerance; do not assume byte identical output across unmatched environments.

### 23.9 Platform and performance

Run the declared workload and accessibility checks on supported Windows and Linux configurations. Verify native file handling, clipboard, scaling, printing and dictation integrations. Benchmark local editing separately from remote model latency and publish any platform specific limitations.

## 24 Remaining implementation decisions

The complete feature scope above is fixed by this specification. Remaining engineering decisions concern how to fulfill it, not which feature groups to postpone:

- Native cross platform desktop framework and implementation language.
- Supported OS versions, architectures and distribution packages.
- Typography, layout and PDF libraries and the exchange conversion adapters.
- Detailed package member layout, schema migration policy and configurable limits.
- Provider adapters, tested models, supported languages and audio decoder matrix.
- CLI executable naming and install behavior.
- Measured resource budgets and reference benchmark hardware.
- Packaging, update delivery and license terms.

The native extension is `.byax`, using the owner's first alternative because `.bax` already has an established Bluebeam use. `.AuthX` remains the owner's alternative if a concrete `.byax` file format collision is identified. Extension selection does not claim exclusive ownership or guarantee that every private format has been discovered.

## 25 File extension reference

Bluebeam's official bFX guide describes markup storage using the `.bax` extension. The specification therefore uses `.byax` for ByAuthorX native documents. Searches performed on October 13 2026 found no documented file format using `.byax`; this is a bounded search result, not proof of universal uniqueness.

[Bluebeam Revu bFX Guide page 4](https://support.bluebeam.com/de/resources/pdfs/bfx-guide.pdf)
