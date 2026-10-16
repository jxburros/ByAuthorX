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

Tools → Libraries manages local research folders. Add creates and attaches a library;
Attach reuses an existing definition. Refresh/rebuild run as cancellable background
jobs. Relink restores a missing definition on another machine. Remove removes the
local definition and current attachment, never the research files. Other documents
keep their attachment and can relink it. Opening an attached document checks for
changed files in the background.

Select document text and choose Cite. Search existing sources or attached libraries,
inspect the passage and locator, then insert. Stale results cannot be inserted.
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
byax library-read draft.byax --library-id lib_... --document-id doc_... --chunk-id chunk_...
byax context draft.byax --library-query moons --library-limit 5
byax source draft.byax --action search --query Mars
byax source draft.byax --action suggest --target p_... --query moons
byax cite draft.byax --target p_... --start 0 --end 20 --source source_...
byax source draft.byax --action bibliography --style apa
```

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

The preview reports provider, documents, character count and exact passages.
A remote embedding call requires independent consent (`--allow-remote` for that
explicit call, or the separate saved provider setting). Ordinary chat consent does
not authorize it. A `local_only` library refuses remote embeddings and context.
Hybrid searches are explicit because embedding the query may contact the provider;
ordinary library searches remain keyword-only. Model, dimension and chunking changes
invalidate old vectors. No weights are bundled or downloaded automatically.

## Storage, bounds and remaining phases

Library definitions contain paths/policies but no credentials, beside provider
configuration. SQLite indexes live in the machine-local cache, never the library
folder or document package. WAL permits reads during indexing; timestamped per-file
leases prevent duplicate concurrent parsing. Missing, unsupported, no-text and
failed files have individual status. Cancellation retains usable completed files.

Defaults: 32 MiB/file, two million extracted characters/file, 100,000 chunks/library,
30 seconds/parser, one parser at a time per indexer, 100 search results and 100,000
read characters. Paragraph chunks are at most 1,800 characters with up to 160
characters overlap when splitting an oversized paragraph. LibraryIndex accepts
explicit `limits` overrides; worker parsing is isolated and archive import limits
also apply. No performance numbers are claimed without reference-hardware measurement.

The first-release keyword library, citation and context/review paths are implemented.
Configured embeddings and explicit hybrid ranking are available. Optional downloadable
weights, outside DOI/catalog lookup and Zotero local API remain future capabilities
(the specification's optional installer work and phase 5). PDFs require an extractable
text layer; OCR is not included. Citation processing dependency licenses are recorded
in [citation-licenses.md](citation-licenses.md).
