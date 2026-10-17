# Reference libraries, retrieval and citations

Status: **implemented first-release paths, with later capabilities still pending.** This document extends sections 5.2, 12, 13, 14, 16 and 17 of the product specification. The local keyword library, citation, context/review and configured embedding paths are implemented; on-device embedding and reranking with explicit, verified model downloads are implemented (context engine Phase 3); phase 5 remains pending. See [the usage and limitations guide](library-and-citations-guide.md) and [the acceptance matrix](acceptance-matrix.md) for delivered behavior and evidence.

Normative words follow the product specification: "shall" is a requirement, "may" is optional, "should" is a recommendation that can be departed from with a stated reason.

## 1 Goals

1. Let a user point ByAuthorX at a folder of documents (research, notes, earlier drafts, series bibles) and let connected AI agents use that material while working on the current document or on other documents in the folder.
2. Let a user add a citation with one action when a source exists, and let an agent propose sources by finding them in the library.
3. Make those two capabilities one system. A citation is a verifiable pointer into a library document, and retrieval is how candidates are found.
4. Preserve existing guarantees: local-first operation, no telemetry, no background network activity, explicit consent before data leaves the computer, and document text treated as data.

Non-goals for the first release: OCR of scanned documents, automatic web search, live Zotero API sync, shared multi-user index servers, and bundled hosted services.

## 2 Terms

| Term | Meaning |
| --- | --- |
| Library | A named, user-selected set of folders whose documents are indexed for retrieval. A library is independent of any one document. |
| Library document | A file in a library, identified by library ID and content hash. |
| Chunk | A bounded passage of a library document, the unit of retrieval and citation. |
| Source | A structured bibliographic record (CSL-JSON) with optional library locators. Stored in the document's `references`. |
| Citation | A mark in document text that points to a source, with an optional locator (page, paragraph, chunk). |
| Index | A derived, rebuildable search database for one library on one machine. |
| Embedding provider | A component that converts text to vectors. Optional. |

## 3 Libraries

### 3.1 Library object

A library shall have:

- a stable `library_id` (random UUID assigned at creation),
- a display name,
- one or more root folders (absolute paths, resolved per machine),
- inclusion and exclusion patterns, with defaults that skip hidden folders, version-control folders and the application's own cache,
- a retrieval policy (section 6.4) and an embedding setting (section 5).

Library definitions are local settings stored in the user's ByAuthorX configuration directory, next to provider configurations. They shall contain no credentials.

A library may be shared across books or series. The same library may be attached to any number of documents. A library may also be exported as a small definition file (name, `library_id`, relative layout hints, patterns) so collaborators can import it and point it at their own copy of the folder.

### 3.2 Attaching libraries to documents

A document shall store only attachment records in `settings.libraries`:

```json
{"library_id": "lib_…", "name": "Series bible", "path_hint": "…", "role": "supplementary"}
```

`role` is `exclusive`, `supplementary` or `open`, matching the reference authority levels already defined for profiles in section 13.1. The document shall never store library file contents or the index. `path_hint` is advisory and shall not be trusted as an authorization to read a path.

If a library cannot be found when a document opens, the document shall open normally. The application shall report the library as unavailable and offer to relink it. Existing citations shall remain valid as records (section 7.4), flagged as unverifiable until the library is relinked and the content hash matches.

### 3.3 Supported file types (first release)

Markdown, plain text, DOCX, `.byax`, and PDF with an extractable text layer. A PDF with no extractable text shall be listed with the status `no_text` and shall not produce empty chunks. OCR is out of scope. Other types may be listed as `unsupported` and shall not be silently skipped.

Parsing shall reuse the safe import paths in `exchange` and obey the limits in section 21 of the product specification: files are data; archives, macros and active content do not execute; expansion, recursion and parsing time are bounded; failures are reported per file and do not abort indexing.

### 3.4 Identity

- A library document ID shall derive from `library_id` and the content hash, with the relative path stored as a mutable attribute. Renaming or moving a file therefore does not orphan citations.
- A chunk ID shall derive from the document ID and the chunk's position and text hash, so two machines indexing the same bytes produce identical chunk IDs.
- Duplicate content in two paths yields one document ID with multiple paths recorded.

## 4 Indexing

### 4.1 Index as derived data

The index shall be a rebuildable cache stored in the user's ByAuthorX cache directory, keyed by `library_id`. It shall never be stored inside a `.byax` package, inside the library folder, or in source exports. Deleting an index shall lose no user data. Each machine builds its own index, which also avoids placing a database on a synced or network folder.

### 4.2 Storage and concurrency

- The index shall use SQLite in write-ahead-log mode with an FTS5 table over chunk text, plus tables for documents, chunks, per-file status and (optionally) vectors.
- Search shall continue to work while indexing runs in another process.
- Indexing shall claim work per file with a timestamped lease, so concurrent indexers on the same machine do not parse the same file twice. Expired leases shall be reclaimable after a crash.
- If two processes produce the same rows, the result is the same because identities are content-derived. Conflicts shall be resolved by redoing work, not by merging partial results.
- If a library root is on a network or synced folder, the index shall still be local to each user. Sharing an index file across machines is not supported.

### 4.3 Change detection

Refresh compares size and modified time first and hashes content only when those differ. The application shall offer an explicit refresh, and shall check for changes when a document with an attached library opens. A background file watcher is optional and shall be off by default. No indexing shall run in the typing path.

### 4.4 Indexing as a job

Indexing shall run as an identified job under section 17 of the product specification: progress, cancellation, recoverable state, and per-file results. A cancelled or interrupted job shall leave a usable partial index and report what is missing. A changed file shall make the affected chunks stale; searches shall report staleness rather than silently return old text.

### 4.5 Chunking

Chunks shall be paragraph-aligned passages of bounded size with small overlap, keeping headings as context. Each chunk shall record its document, locator (page for PDFs, block ID for `.byax`, heading path and ordinal for Markdown, DOCX and text) and exact text. Chunking parameters shall be recorded in the index so a change forces a rebuild.

## 5 Retrieval

### 5.1 Tiers

| Tier | Method | Install cost | Default |
| --- | --- | --- | --- |
| Base | SQLite FTS5 (BM25) keyword search | None | Yes |
| Local embeddings | A small embedding model run locally | Larger install, or download on demand | Opt in |
| Bring your own | A user-configured embedding model (local model file, Ollama, an OpenAI-compatible endpoint, or a hosted API) | None | Opt in |

The base tier shall always work with no model, no network and no extra download. When embeddings are available, search shall be hybrid: keyword and vector results are merged and ranked together, so exact names and terms always match.

### 5.2 Embedding provider interface

Every embedding source shall satisfy one contract: text batch in, vectors out, with model identity, dimension and maximum input length reported. The index shall record which model produced its vectors. Changing the model, its dimension or the chunking parameters shall mark vectors stale and prompt for a rebuild. Vectors from different models shall never be mixed or compared.

### 5.3 Installation options

- Keyword retrieval shall work with no model weights present.
- The standard installer bundles the on-device search models (a local embedding model and reranker). A lean installer variant omits them. Lean users get the same models through an explicit user-initiated download. The models are mirrored on a project-controlled GitHub Release and pinned by SHA-256 (see the [context engine plan](context-engine-plan.md)). Bundled models run only on this computer. Semantic indexing with them starts only after the user turns it on.
- A user-initiated download may provide a local embedding model. A download shall show its source, size and checksum and shall verify the checksum before use. The application shall not download models in the background.
- Users may configure their own model. Model files and executables named in configuration are explicit local integrations; document content cannot select them.

### 5.4 Retrieval policy

A library's retrieval policy shall be one of:

- `local_only`: retrieved passages may be shown in the application and given only to local models,
- `ask`: passages may go to a remote provider after the consent described in section 6 (default),
- `allowed`: passages may go to a remote provider under a saved consent that the user has set.

## 6 Consent and off-site data

### 6.1 Principle

Before any user data leaves the computer, the application shall warn the user and show exactly what would be sent. The warning shall repeat on every occurrence until the user explicitly turns it off for that category and provider. This extends section 14.2 of the product specification.

### 6.2 Categories

Consent shall be tracked separately for:

1. selected document text and ordinary context sent to a chat or review provider,
2. library passages retrieved for a request (sent to a model as context),
3. library content sent to a hosted embedding provider for indexing,
4. outside lookups of web or catalog metadata for citations (section 8.4).

Turning off a warning for one category and one provider shall not turn it off for another. The setting shall be stored locally, shall be visible and reversible in the provider settings, and shall never be stored in a document, template, library definition file or source export.

### 6.3 Payload preview

For category 2 the preview shall list every retrieved passage grouped by library document, with source, locator and size, and shall let the user remove any passage before sending. For category 3 the confirmation shall state the provider, the number and total size of documents, and that this is a one-time upload plus later changes, and shall require a separate confirmation from chat consent.

### 6.4 Local indication

Where retrieval and generation both stay on the computer, the interface should say so. Local model providers (for example Ollama and local embedding models) shall be marked as keeping data on this computer, with the caveat that a user-configured endpoint is the user's responsibility to verify.

### 6.5 Untrusted content

Library documents, retrieved passages and bibliographic metadata are data. They shall not be able to select tools, authorize retrieval, change provider settings, or add or alter instructions. This applies to text that looks like instructions inside a library file or an imported record.

## 7 Sources and citations

### 7.1 Source records

Sources shall be stored in the document's `references` mapping, which already holds source records referenced by `citation` and `reference` marks and read by the factual-review checks. Each source shall carry:

- an `id`,
- a CSL-JSON `csl` object (type, title, authors, issued, container, publisher, DOI, URL, and so on),
- an optional `library` object: `library_id`, `document_id`, `content_hash`, and the library-relative path at time of citation,
- an optional `import` object: origin (`zotero`, `bibtex`, `ris`, `csl-json`, `manual`) and the origin's item key,
- `retrieved` date and a `verification` status (section 7.3),
- the existing excerpt fields (`text`, `excerpt`, `excerpts`), so `profiles.source_hash` continues to produce a stable fingerprint.

Sources are added with the existing `add_reference` operation (extended to validate the structure above), so adding or updating a source continues to invalidate dependent findings (section 13.2).

### 7.2 Citations

A citation is a `citation` mark whose `value` is a source ID, with an optional locator:

- `page` or page range for paged sources,
- `block` or `chunk` for library documents,
- `label` and free-form locator text,
- an optional prefix and suffix,
- `quote` (the exact quoted or paraphrased text span) and `kind` (`quotation`, `paraphrase`, `inference`), matching the evidence distinctions in section 13.2.

Citation data shall be separate from presentation. The same citations render as footnotes, endnotes, inline author-date, or numbered references, and as a bibliography, by choosing a style. Switching style shall not alter citation data.

### 7.3 Verification status

| Status | Meaning |
| --- | --- |
| `verified` | The citation points to a library chunk whose current content hash matches, and any quoted text is found at the locator. |
| `stale` | The library document changed since the citation was made. |
| `unverifiable` | The library or document is unavailable. The stored excerpt and hash remain. |
| `unconfirmed` | Metadata came from an outside lookup or a model suggestion and has not been confirmed by the user. |
| `manual` | The user entered the record by hand. |

Status shall be recalculated when a library is available and shall not be inferred from the document alone. A model shall not set `verified` or `manual`.

### 7.4 Missing libraries

A citation shall keep its stored source record, content hash, locator and excerpt even if the library is unavailable. Export and rendering shall use the stored record. Relinking shall re-verify and update status.

### 7.5 Rendering and styles

Rendering shall produce footnotes, endnotes, in-text citations and a bibliography from CSL-JSON using CSL styles. APA, MLA and Chicago shall be supported in the first release of this feature. Footnotes shall use the document's footnote support (section 4.2 of the product specification). The choice of Python CSL processor and its licensing shall be confirmed before adoption (open decision in section 14).

### 7.6 Import and export

First release: read CSL-JSON, BibTeX and RIS files, and a Better BibTeX auto-export file. Export the document's sources as CSL-JSON and BibTeX.

Re-importing shall match on origin item key and update existing sources instead of duplicating them. A changed source record shall invalidate dependent findings through the existing mechanism.

### 7.7 Zotero

- First release: file-based. A Zotero export (including a continuously updated Better BibTeX export) can be imported. Zotero's attachment folder can be added as a library so attached PDFs are searchable and a bibliography entry can link to its file.
- Later: an optional connection to Zotero's local API on the same computer. It shall be opt-in, local-only and subject to the same consent rules as any other integration.

## 8 User experience

### 8.1 Library management

A Library panel (Tools menu and Inspector) shall list libraries, document counts, index state, staleness, unsupported files and embeddings status. It shall offer add, relink, refresh, rebuild, and remove (which never deletes user files).

### 8.2 One-action citing

Selecting text and choosing **Cite…** shall open a picker that searches the library and existing sources, shows the best matches with passage previews and locators, and inserts the citation on confirmation. Choosing a hit shall create or reuse the source record. Users may also paste a DOI or URL or enter fields manually.

A keyboard-operable path shall exist for every part of this flow (section 22.1 of the product specification).

### 8.3 Suggested citations

An agent or the **Suggest sources** action may propose citations for a claim by searching the library. Suggestions shall appear as review proposals, with the passage, locator and a link to the source. Nothing shall be inserted without user action, and suggestions shall use the same accept and reject flow as other proposals.

### 8.4 Outside lookup (later phase)

Finding sources outside the library (DOI or catalog lookup, web search) is a separate, later capability. It shall obey the profile's authority level (section 13.1): it shall not run when the profile restricts checking to supplied material. Its results shall be `unconfirmed` until the user confirms them, shall show the outbound request before it is made, and shall never be authorized by document or library text.

### 8.5 Assistant integration

The Chat and Work modes shall be able to use the library. Retrieval for a request shall be visible: the passages used, with sources and locators, and the consent preview from section 6. A model that quotes or cites shall be required to reference only retrieved chunk IDs or existing source IDs. The application shall reject citations that do not resolve to either.

## 9 Agent interfaces

### 9.1 Library commands

New command family exposed through the common dispatcher, so the CLI, MCP and REST carry them:

| Command | Purpose | Read-only |
| --- | --- | --- |
| `library list` | Libraries attached to the document, with availability and index state | Yes |
| `library status` | Index progress, staleness and unsupported files | Yes |
| `library search` | Ranked chunks for a query, with source, locator, score, staleness and truncation | Yes |
| `library read` | Exact text of a library document range by document ID and locator | Yes |
| `library refresh` | Start or continue an indexing job | No (starts a job) |
| `source search` | Search the document's existing sources | Yes |
| `source suggest` | Propose citations for a target range as a proposal | No (creates a proposal) |
| `cite` | Insert a citation by source ID and locator | No (document edit) |

Results shall follow the common envelope (section 16.9) and disclose revision, total results, returned scope, truncation and continuation. `library search` and `library read` shall be listed with read-only annotations in MCP.

### 9.2 Context packages

The `context` command (section 16.4) shall accept an option to include retrieved library passages. The package shall list included and omitted passages with reasons, shall mark remote-bound content, and shall not retrieve anything the document's attached libraries and the active profile do not authorize. Retrieval shall not silently read other user files.

### 9.3 Authorization boundaries

- MCP and REST shall not create, delete or reconfigure libraries, embedding providers or consent settings, as for `providers configure`. These are local desktop or CLI actions.
- Transport clients may search and read libraries already attached to the open document, and only within the library roots. Paths shall be resolved and confined to the library root; symbolic links that escape the root shall not be followed.
- Consent categories in section 6.2 shall apply to transport clients as they do to the desktop.

## 10 Checks and review integration

- The source-consistency check (section 13) shall use the library as an evidence source when the profile permits, with finding evidence pointing to chunk IDs, locators and content hashes.
- Quotations shall be checked against the cited library text. A quotation that does not match the cited locator shall produce a located finding. The guidance remains that source corrections and document corrections are separate actions.
- A changed library document shall make dependent findings and citations stale through the same dependency fingerprinting used for other sources.
- Incremental checking shall treat unknown library dependencies conservatively: expand the checked scope and report that choice.

## 11 Format and storage impact

- Citation and reference marks already exist; the added source fields live inside `references` entries and `settings.libraries`. This is expected to be an additive change to the document schema. If validation tightens `references` entries, that tightening requires a schema version and migration transition under `docs/architecture.md`, with a fixture generated by the previous release and tests for preserved unknown constructs and for restore of migrated history.
- Library definitions, consent settings, embedding settings and indexes are local files, not part of `.byax` and not part of source exports. Source exports shall exclude indexes and consent settings, and shall exclude library file contents.
- Packages may carry citation excerpts and hashes (permitted by section 5.2 of the product specification) but not entire library documents.
- Publication and redaction (section 18) shall treat citation excerpts as recoverable content that redaction must be able to remove.

## 12 Performance and resource limits

- Typing, scrolling and save shall not wait on indexing, embedding or search.
- Limits shall be documented and configurable: maximum file size, maximum extracted text per file, maximum chunks per library, maximum parse time per file, maximum concurrent parse workers, maximum retrieval result count and context size.
- Errors shall identify the exceeded limit and the file. A file that exceeds a limit shall not fail the whole job.
- Search latency and indexing throughput targets shall be measured on reference hardware, per section 22.2, before numbers are published.

## 13 Phased delivery

| Phase | Scope |
| --- | --- |
| 1 | Library registry and relink, safe parsers for the first-release file types, SQLite WAL index with FTS5, indexing job with leases and cancellation, `library list/status/search/read/refresh`, consent categories 1 and 2 with passage-level preview. |
| 2 | Source records (CSL-JSON in `references`), `cite` operation, one-action citing UI, verification statuses, CSL rendering for footnotes, endnotes and bibliography, BibTeX/RIS/CSL-JSON import and export, Zotero file import and attachment-folder libraries. |
| 3 | Retrieval in `context` and the Chat/Work assistant, `source suggest` as proposals, citation checks against the library. |
| 4 | Embedding provider interface, local and bring-your-own embeddings, hybrid ranking, consent category 3, optional fuller installer and verified model download. |
| 5 | Outside lookup (DOI, catalogs, web) under profile authority and consent category 4; optional Zotero local API. |

Each phase shall ship with acceptance criteria added to the acceptance matrix before it is marked complete.

## 14 Open decisions

1. Which Python CSL processor to use, and whether its license and size are acceptable for the installers.
2. Which local embedding model and runtime to bundle or offer as a download, and its license. *Decided (16 October 2026):* ONNX Runtime with Qwen3-Embedding-0.6B (int8, 512 dimensions, Apache-2.0) and the 33M answerai-colbert-small-v1 reranker are bundled in the standard installer; bge-reranker-v2-m3 is an optional download. See the [library guide](library-and-citations-guide.md#on-device-semantic-search).
3. Whether SQLite FTS5 plus brute-force vector search meets size and speed needs, or a vector index extension is needed. Decide from measurements on realistic libraries. *Measured:* exact search is enough to about 10,000 chunks; above that the optional sqlite-vec backend applies (numbers in the library guide).
4. How `settings.libraries` and source structure are validated, and whether validation changes force a schema version.
5. Concurrency target: how many simultaneous indexers on one library, and behavior when two machines index the same network folder (each builds a local index; confirm this is acceptable for very large libraries).
6. PDF text extraction library and its license.
7. Default chunk size and overlap.

## 15 Acceptance scenarios (to be added to the matrix)

1. Attach a library of mixed supported files, index it, and search for a character name. Results cite the correct file and locator. Unsupported and `no_text` files are listed, not dropped.
2. Edit a library file and refresh. Affected chunks become stale, dependent citations show `stale`, and dependent findings are invalidated.
3. Run two indexing processes over one library. No file is parsed twice at the same time, the final index is correct, and search works throughout.
4. Open a document whose library path is missing. The document opens, citations render from stored records, and relinking restores `verified` status.
5. Cite a library passage with one action. The inserted citation carries source ID, locator and quote, and renders as a footnote, then as an author-date citation, without changing citation data.
6. Import a Zotero Better BibTeX export, then re-import it after a change. Sources update without duplicates.
7. Use a remote model with library retrieval. The warning lists every retrieved passage, allows removal, and repeats until the user turns it off for that category and provider.
8. Choose a hosted embedding provider. A separate confirmation states the document count and size, and nothing is uploaded until it is given.
9. Place instruction-like text in a library file. It cannot trigger tool use, retrieval, provider changes or citations.
10. A model proposes a citation that resolves to no chunk or source. The application rejects it.
11. Call `library search` and `library read` over MCP. Results are read-only, confined to the library roots, and carry the standard envelope. A call to create or reconfigure a library over MCP is refused.
12. Install without embeddings and confirm keyword retrieval works. Add a user-configured embedding model, rebuild, and confirm hybrid search. Change the model and confirm vectors are marked stale.
