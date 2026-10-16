# Libraries, citations and review

## Desktop

The ribbon sits beside the logo in two rows and changes with the file view. Font
family/size, alignment, lists, superscript/subscript, colors, paragraph formatting and
Cite are available there in views that support them. Each side panel's title is its
selector: Outline or Inspector, Layout, Bookmarks, Writing statistics, Personal
scratchpad, or Library. Panel choices and the scratchpad are local settings; the
scratchpad is not saved in the document.

The Inspector's Checks tab marks each check active or inactive and can run one on its
own; Proposed changes runs all active checks. Spelling, grammar and document health run
locally. Information repository, factual accuracy, voice consistency and custom checks
use an explicitly selected AI reviewer. Libraries and sources are set up in Configure. Review results are suggestions;
text changes require Apply/accept. Repository and factual reviews can use attached
library passages, with a passage picker and separate consent before remote transfer.
A factual review is evidence checking, not an automatic web fact-checking service.

Every provider and embedder is labelled **Local** (stays on this computer) or **Remote**
(leaves this computer, with its host) in the Libraries panel (the embedder line under the
index status), the passage preview, the payload authorization dialog, Provider settings
and the assistant's provider list. Local means a loopback address (`localhost`,
`127.0.0.0/8`, `::1`), a unix socket, or whisper.cpp, which runs as a local process. A LAN
address or any other host is Remote. The label describes where the request goes; it cannot
see where a service behind a loopback port forwards it.

Tools → Libraries manages local research folders. Add creates and attaches a library;
Attach reuses an existing definition. Refresh/rebuild run as cancellable background
jobs. Relink restores a missing definition on another machine. **Preview extracted text…**
opens a read-only view of each indexed file, with the text and locators exactly as
indexed (page or heading path, offsets). Search, citations and AI passages use only that
text, so it is the place to see why a PDF did not match: scanned pages and images have no
text, and extractors break lines and words in their own way. The preview reads the
index; it does not parse the file again and never changes it. Remove removes the
local definition and current attachment, never the research files. Other documents
keep their attachment and can relink it. Opening an attached document checks for
changed files in the background.

Select document text and choose Cite. Search existing sources or attached libraries,
inspect the passage and locator, then insert. Stale results cannot be inserted. The
picker preselects the evidence kind (Quotation, Paraphrase or Inference) and says why;
you can change it, and your choice is kept when you pick another result. See
[Quotations and evidence kinds](#quotations-and-evidence-kinds).
Manual sources accept bibliographic fields, DOI or URL without fetching the network.
Import/export supports CSL-JSON, BibTeX (including Zotero Better BibTeX) and RIS.
Reimport uses stable origin keys. Library's Citation style control selects APA, MLA
or Chicago and in-text, footnote, endnote or numbered presentation. The source records
and citation marks stay unchanged. Pages preview/PDF and exchange exports render the
chosen presentation; the writing editor retains authored text. Exports without native
footnote support use numbered endnotes. Bibliography entries include cited sources only.

## Shared commands

Commands use the same document path and `--action` convention as existing command
families. JSON `--input` reads a file (or `-` for stdin).

```sh
# create.json: {"name":"Research","roots":["/absolute/research"]}
byax library draft.byax --action create --input create.json
byax library draft.byax
byax library draft.byax --action refresh --library-id lib_...
byax library-search draft.byax --library-id lib_... --query moons --limit 10
byax library-search draft.byax --library-id lib_... --query 'heading:Mars "two moons" -Venus'
byax library-read draft.byax --library-id lib_... --document-id doc_... --chunk-id chunk_...
byax context draft.byax --library-query moons --library-limit 5
byax source draft.byax --action search --query Mars
byax source draft.byax --action suggest --target p_... --query moons
byax cite draft.byax --target p_... --start 0 --end 20 --source source_...
byax source draft.byax --action bibliography --style apa
```

## Search syntax and methods

The desktop Cite search, `library-search`, `library --action search` and context retrieval
share one syntax ([CLI reference](cli-reference.md#library-search-syntax)): plain words
(combined with OR), `"quoted phrases"`, `NEAR(a b, 10)`, `-word` to exclude, `word*` for a
prefix, and the fields `heading:`, `text:`, `title:` and `path:`. Words such as *the* are
ignored unless the query has nothing else. Headings rank above body text. When the stemmed
pass finds fewer results than requested, a second literal pass finds substrings and mid-word
matches (`match: "trigram"`). A word that matches nothing gets "did you mean" suggestions
from the library's own vocabulary; `--expand` also searches them. The query cannot inject
search operators: every part becomes a quoted token.

`--method auto` (the default) uses hybrid search for a library whose embeddings are `ready`
or `partial` and keyword search otherwise. `keyword` never embeds the query; `hybrid` asks
for it explicitly. Every result reports `method` and `embedding_state`, plus a `notice` when
it fell back to keyword or when only part of the library has vectors. Auto never asks for
new consent: if the embedder is remote and has no saved `library_embeddings` consent, or the
library is `local_only`, the search stays keyword and says why.

Context retrieval (`context --library-query`, the AI tab and model review) takes up to
`--library-limit` candidates from every eligible library and merges them by reciprocal-rank
fusion, so the first attached library no longer fills the whole budget. Duplicate passages
(the same file and location, including the same file in two libraries) are removed, and
items already attached in full (`--library-pinned`) are reported as the `pinned` tier
instead of being sent twice. The result lists each library's method and embedding state.

## Verified absence

Before ByAuthorX lets "not found in your sources" stand, it searches literally:

```sh
byax library draft.byax --action verify-absence --query '"Lady Catherine" Pemberley'
```

Each term or quoted phrase is looked up exhaustively (stemmed words and literal substrings)
in every attached library the active profile permits and in the open document. The result
lists what was searched, every match with its locator, and which terms were not found.
`exhaustive: false` means a library was unavailable or stale. Paraphrases and synonyms are
not checked. When an assistant reply claims something is missing, the AI tab runs this
check in the background and shows any literal matches. A library citation check whose
quotation is not at the cited locator reports where else the exact text occurs
(`measured.literal_matches`). The action is read-only and available over MCP and REST.

`source suggest` records pending proposals with passage evidence; it does not insert
citations or sources until acceptance. `library-search` and `library-read` have MCP
read-only annotations. The `library --action search/read` aliases return the same
results. Search results disclose totals, truncation and continuation; read offsets
are chunk ordinals. All results carry the normal revision envelope.

MCP/REST cannot create, attach, detach, relink, remove or configure libraries or
embeddings. They can use already attached libraries. Document `path_hint` never
authorizes filesystem reads, and library paths are confined to locally selected
roots. Transports cannot assert manual/verified source status. Verification is
computed against indexed content and current file state.

## Quotations and evidence kinds

A quotation is checked against the cited source text with `quote_match.locate`, in three
steps, and the result says which step passed:

| Result | Meaning | Reported as |
| --- | --- | --- |
| `exact` | The quote is a literal substring | verified; `raw_pass` true |
| `normalized` | It matches after normalization: Unicode NFC, collapsed whitespace, curly quotes and apostrophes straightened, soft hyphens and zero-width characters removed, ligatures (`ﬁ`) expanded, a line-break hyphen joined (`exam-` + newline + `ple`), dashes folded, and `...`, `. . .`, `[...]` and `[…]` treated as one ellipsis. An elided quote (`first part ... last part`) matches when its segments occur in order | verified; `raw_pass` false, `normalized_pass` true. The span is mapped back to the original text |
| `near` | No normalized match, but RapidFuzz `partial_ratio` alignment is at or above the threshold (provisionally 90, to be tuned by the Phase 0 harness; quotes shorter than 12 characters are never matched this way) | **`quote_near_match`**, advisory. It lists the differing characters and the score, and is never `confirmed` or `verified` |
| `none` | Nothing close | `unconfirmed` (stored source) or a warning finding (document check) |

Case is significant: a quote that differs only in capitalization is a near match, not a
verified one. A quote that spans two indexed passages is not found, because each passage is
checked on its own.

The result of `verify_source_detail` and the evidence in a factual check record
`quote_match` (`match`, `raw_pass`, `normalized_pass`), so the pass rate before and after
normalization can be counted. The checks keep their rule IDs (`factual.quotation`,
`factual.library_citation`); a near match is a finding of severity `advisory` whose
`measured.verification` is `quote_near_match`.

**Evidence kinds.** A citation records `kind`: `quotation`, `paraphrase` or `inference`.
When the Cite picker or `byax cite` is not told a kind, it chooses `quotation` if the
selected text is inside quotation marks or is found verbatim (exact or normalized, at
least 12 characters) in the cited source, and `paraphrase` otherwise. `byax cite` returns
the choice and its reason as `citation_kind` (`inferred: true`); pass `--kind` to set it
yourself. Only quotations are checked word for word.

**Anchors.** `quote_match.selectors` and `reanchor` implement the three selectors used by
the planned re-anchoring (position, quote, 32 characters of prefix and suffix). Nothing
stores them in documents yet.

## Optional embeddings

Keyword search requires no model or network. Configure an existing Ollama or
OpenAI-compatible provider alias in Provider settings first. Then use the local CLI:

```sh
# embedding.json: {"provider":"my-embedding-provider","dimension":768,"max_input_chars":8000}
byax library draft.byax --action embedding-configure --library-id lib_... --input embedding.json
byax library draft.byax --action embedding-preview --library-id lib_...
byax library draft.byax --action embed --library-id lib_...
byax library draft.byax --action hybrid --library-id lib_... --query moons
```

The preview reports provider, whether it is `local` or `remote`, documents, character count and the exact passages the
next rebuild would send. Rebuilds are incremental: a passage whose text and model are
unchanged is skipped. `embed` runs as one background job and reports `done`, `skipped`
and `failed` counts; requests are batched (`max_batch`, default 32), and a batch is
retried with backoff only when the request provably never reached the provider. A
timeout is never repeated automatically; its passages count as failed.
When semantic search is on, `byax library --action status` names the embedder and its
locality (`embedder: null` when semantic search is off).
A remote embedding call requires independent consent (`--allow-remote` for that
explicit call, or the separate saved provider setting). Ordinary chat consent does
not authorize it. A `local_only` library refuses remote embeddings and context.
Searching with `--method auto` embeds the query only under consent that already exists.
Model, dimension and chunking changes make old vectors `stale`: they are never compared
with the new model, keyword search answers meanwhile, and the next rebuild discards them.
An interrupted or partly failed rebuild leaves `partial` coverage, which is used and
disclosed (`coverage`, `notice`). Vectors are float32 values in the index cache. No
weights are bundled or downloaded automatically.

## Storage, bounds and remaining phases

Library definitions contain paths/policies but no credentials, beside provider
configuration. SQLite indexes live in the machine-local cache, never the library
folder or document package. WAL permits reads during indexing; timestamped per-file
leases prevent duplicate concurrent parsing. Missing, unsupported, no-text and
failed files have individual status. Cancellation retains usable completed files.

Defaults: 32 MiB/file, two million extracted characters/file, 100,000 chunks/library,
30 seconds/parser, one parser at a time per indexer, 100 search results and 100,000
read characters. Paragraph chunks are at most 1,800 characters with up to 160
characters overlap when splitting an oversized paragraph. Index schema 2 (chunking
version 2) stores each chunk's heading path and fingerprint, a stemmed full-text index
with a heading column, a trigram index for substrings and a vocabulary table for
suggestions. Older index files are marked stale on first open, stay searchable (results
flagged `stale`) and are rebuilt by the next refresh; their old vectors are dropped. LibraryIndex accepts
explicit `limits` overrides; worker parsing is isolated and archive import limits
also apply. No performance numbers are claimed without reference-hardware measurement.

The first-release keyword library, citation and context/review paths are implemented.
Configured embeddings and hybrid ranking (chosen automatically when a library has vectors)
are available. Reranking models and on-device embedders are later context-engine phases. Optional downloadable
weights, outside DOI/catalog lookup and Zotero local API remain future capabilities
(the specification's optional installer work and phase 5). PDFs require an extractable
text layer; OCR is not included. Citation processing dependency licenses are recorded
in [citation-licenses.md](citation-licenses.md).

## The manuscript as a corpus

`byax.project_index.ProjectIndex(document_path, doc=...)` indexes a project at block granularity:
one row per text object (paragraph, heading, footnote, table cell) of the open document, its sibling
`.byax` packages and its series' member documents. It is the one index shared with the binder: each
row records the stable block identity (`block_id`, and `target` for a table cell), the owning binder
item, chapter and kind from `project.block_map`/`project.item_for_block`, and the heading path. The
schema follows library index schema 2 (porter FTS5 with a weighted heading column and prefix indexes,
a trigram table, an `fts5vocab` view for suggestions) and the same query syntax, so hits are
`ranking.Hit`-compatible (`corpus: "manuscript"`) and can be fused with library hits by RRF.

The index file is derived data in the user cache (`…/ByAuthorX/projects/project_<hash>.sqlite3`); a
cache inside the project folder is refused. `refresh()` rewrites only blocks whose content hash
changed, reparses a sibling only when its size or modification time changed, and drops documents
that left the folder; `update_document(doc)` indexes the in-memory document (the desktop does this
on save once an index exists). Nothing runs in the background and nothing uses the network.
Optional vectors use `vector_store` with the same model-signature and fingerprint rules as
libraries, but only when a caller passes an embedding provider (`rebuild_vectors(provider)`,
`search(..., method="hybrid", provider=...)`); the CLI and desktop search the project by keyword.
On the 100,000-word project fixture the slowest keyword query took 8.2 ms at p95 on an
unqualified CI-class host ([benchmark](benchmarks/0.3-codex.json)).

## Codex and hide from AI

`byax.codex` holds entries (`character`, `place`, `item`, `term`) with aliases, excluded phrases,
case sensitivity, a description, traits and `hide_from_ai` flags. Mention detection builds one
Aho-Corasick automaton over every name and alias (pure Python; `pyahocorasick`, BSD-3-Clause, is used
instead when it is installed, and is not a dependency). Matches are word-bounded, case-sensitive by
default, resolve overlaps leftmost-longest ("Elizabeth Bennet" before "Elizabeth"), ignore
occurrences inside an entry's excluded phrases and treat curly and straight apostrophes and
no-break spaces alike. On chapters I–VI of *Pride and Prejudice* (268 labelled name-form mentions)
the fixture codex scored precision 1.0 and recall 1.0; the same entries without aliases reach
recall 0.53 ([benchmark](benchmarks/0.3-codex.json)). The labels cover name forms only, so this
measures the matcher against a writer's codex, not coreference.

Hidden entries and traits are removed wherever AI context is assembled: `codex.ai_visible_entries`,
`codex.ai_context` (selection and nearby mentions for route R1, counting but never naming hidden
hits), and `codex.redact_for_ai`, which the assistant, model review and `byax context` apply to their
payloads. MCP and REST cannot request hidden entries. Model-suggested facts go through
`codex.propose_fact`, which returns a `propose` operation with provenance; nothing is written
until the author accepts the proposal.

## Series files

A series file (`*.byaxseries`, UTF-8 JSON, version 1) shares a codex, library attachments and member
documents between books:

```json
{
  "format": "byax-series",
  "version": 1,
  "id": "series_…",
  "title": "The Longbourn Novels",
  "codex": {"entries": {"cx_…": {"id": "cx_…", "kind": "place", "name": "Longbourn"}}},
  "libraries": [{"library_id": "lib_…", "role": "supplementary"}],
  "documents": ["book-1/book-1.byax", "book-2/book-2.byax"]
}
```

A document refers to it with `settings.series = {"path": "../longbourn.byaxseries"}`. The file must
be in the document's folder or its parent; member documents must be inside the series file's
folder. Precedence: a document entry overrides a series entry with the same identity or name
(compared without case), and a document library attachment overrides the series attachment of the
same library; `local_only` and other retrieval policies still come from the library registry.
`series.with_series(doc, path)` gives the merged, read-only view that retrieval and codex queries
use. Attaching a series attaches libraries, so `series-attach`/`series-detach` (and `set_settings`
with `series`) are local CLI or desktop actions; MCP and REST refuse them.
