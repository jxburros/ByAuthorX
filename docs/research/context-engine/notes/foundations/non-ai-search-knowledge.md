# Non-AI (Deterministic) Context, Search, and Knowledge-Management Features for a Long-Form Writing App (Python/Qt), 2026

Scope note: this covers deterministic techniques only, with no embeddings or LLMs. Items marked "(prior knowledge, unverified this session)" come from the researcher's background knowledge and were not confirmed against a fetched source. Verify them before relying on them.

## 1. Embeddable full-text search engines (SQLite FTS5, Tantivy, Whoosh, Xapian), tokenization, fuzzy matching, query syntax, latency targets

### Takeaway
SQLite FTS5 is the default choice for a Python/Qt desktop app. It ships in SQLite and has BM25 ranking, prefix indexes, a trigram tokenizer for substring/LIKE search, diacritic folding, snippet/highlight, NEAR/phrase/boolean/column queries, and external-content tables kept in sync by triggers. Tantivy (MIT, via `pip install tantivy`) is the upgrade path when you need richer tokenizers (CJK, stemming in 17 languages), facets, and Lucene-class speed. Avoid Xapian (GPL) for embedding. Avoid Whoosh, which is unmaintained. Add typo tolerance with RapidFuzz (MIT) over the FTS vocabulary.

### Cited Findings
**SQLite FTS5**
- FTS5 is a virtual-table module included in the SQLite amalgamation since 3.9.0. — [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- Query syntax: `*` suffix for prefix queries, `^` to anchor at column start, `NEAR(...)` (default distance 10 tokens), column filters (a leading `-` excludes columns), boolean NOT > AND > OR precedence, and implicit AND between adjacent terms. — [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- `bm25()` uses hard-coded k1=1.2 and b=0.75, takes optional per-column weights, and returns lower values for better matches. Ordering by the hidden `rank` column is faster than calling bm25() directly. — [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- `highlight()` wraps matches in caller-supplied markers. `snippet()` returns at most 64 tokens and prefers fragments at column starts or after "." or ":". — [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- Tokenizers:
  - `unicode61` is the default. Its `remove_diacritics` takes 0, 1 or 2 (default 1). Mode 1 misses some multi-diacritic codepoints, so use 2. It also has `tokenchars` and `separators` options.
  - `porter` wraps another tokenizer for English stemming.
  - `trigram` gives substring matching and indexed LIKE/GLOB, with `case_sensitive` and `remove_diacritics` options. "Substrings consisting of fewer than 3 unicode characters do not match any rows."
  - Source: [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- Index options:
  - `prefix='2 3'` builds prefix indexes, which speed up prefix queries at the cost of index size.
  - `detail=full|column|none` trades features for size. In one test the index was 743 MiB (full), 340 MiB (column) and 134 MiB (none). `column` loses phrase and NEAR queries, and `none` also loses column filters.
  - Source: [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- Table types:
  - External-content tables (`content='table'`) are kept in sync with triggers, and the `rebuild` command recovers from drift.
  - Contentless tables (`content=''`) store only the index.
  - `contentless_delete=1` (3.43.0+) allows DELETE and REPLACE on contentless tables.
  - `secure-delete` removes old index entries on update or delete.
  - Source: [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- Index maintenance: `automerge` defaults to 4 (max 16), `crisismerge` to 16, and `optimize` merges everything into one b-tree. There is also an `integrity-check` command. — [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- FTS5 index size in practice: on an older Enron email test, the FTS data was just under 32% of the total DB with default settings and about 7.6% with `detail=none`. — [SQLite forum](https://sqlite.org/forum/info/a56bfc92a7c41131) (as summarized in search results; the specific forum thread among those returned was not individually verified)
- A SQLite maintainer has argued there is no point benchmarking indexing or search on a "typical" setup, because results depend heavily on storage and configuration. — [SQLite forum](https://sqlite.org/forum/info/ccf1ea60a33705a2) (search-result summary)
- FTS vs LIKE: in a Room FTS4 test at 5,000 records, FTS took about 6.81 ms versus about 63.76 ms for LIKE (9.4x faster). — [ProAndroidDev](https://proandroiddev.com/scaling-android-search-room-fts4-vs-sql-like-b2a8232cbd12)

**Tantivy / tantivy-py**
- Tantivy uses Lucene-style BM25. It supports phrase queries, hierarchical facets, incremental indexing and an mmap directory, and claims a "tiny startup time (<10ms)". It is MIT licensed. It claims to be about 2x faster than Lucene on its own benchmark, with the caveat "your mileage WILL vary". — [tantivy GitHub](https://github.com/quickwit-oss/tantivy)
- Tantivy has stemming for 17 Latin-script languages. CJK is handled by third-party tokenizers: tantivy-jieba and cang-jie (Chinese), Lindera, Vaporetto and tiny-segmenter (Japanese), and Lindera with ko-dic (Korean). — [tantivy GitHub](https://github.com/quickwit-oss/tantivy)
- tantivy-py is the official MIT-licensed Python binding, installed with `pip install tantivy`. Where no wheel exists it builds from source and needs Rust. — [tantivy-py GitHub](https://github.com/quickwit-oss/tantivy-py)
- The latest tantivy-py found was 0.25.1 (2025-12-02), after 0.25.0 (2025-09-09). — [piwheels tantivy](https://www.piwheels.org/project/tantivy)

**Whoosh / Xapian**
- Snyk rates Whoosh's maintenance as inactive, with its last PyPI release about nine years ago. It is BSD-2-Clause. — [Snyk: whoosh](https://security.snyk.io/package/pip/whoosh)
- Xapian is GPL. Its maintainers said they could not relicense because of legacy code, and in 2008 no commercial license was available. Non-GPL users were advised to put Xapian behind an IPC or client-server boundary. These sources date from 2005 to 2012. — [xapian-discuss 2012](https://lists.xapian.org/pipermail/xapian-discuss/2012-September/008812.html); [xapian-discuss 2008](https://lists.xapian.org/pipermail/xapian-discuss/2008-December/006209.html)

**Fuzzy / typo tolerance**
- RapidFuzz is MIT licensed with a C++ core. Batch scoring through processors such as `cdist` is much faster than calling scorers directly. Older release notes claim a 2-100x speedup over FuzzyWuzzy, which is a vendor claim. The latest version found was 3.14.1 (Sep 2025). — [RapidFuzz GitHub](https://github.com/rapidfuzz/RapidFuzz); [PyPI](https://pypi.org/project/RapidFuzz)

**Search query syntax exemplar (Obsidian)**
- Obsidian's search supports these operators:
  - Scope: `file:`, `path:`, `content:`
  - Case: `match-case:`, `ignore-case:`
  - Tags: `tag:`
  - Scoping within text: `line:`, `block:`, and `section:` (text between two headings)
  - Tasks: `task:`, `task-todo:`, `task-done:`
  - JavaScript regex as `/.../`
  - Properties: `[prop]`, `[prop:value]`, `[prop:null]`
  - `OR`, `-` negation, and `"quoted phrases"`
- Live search results can be embedded in a note with a ```query``` code block. — [Obsidian Help: Search](https://obsidian.md/help/plugins/search)

**Latency targets**
- Nielsen's limits are 0.1 s (feels instantaneous, like direct manipulation), 1.0 s (flow of thought unbroken), and 10 s (the limit of attention, which needs a progress indicator). — [NN/g Response Times](https://www.nngroup.com/articles/response-times-3-important-limits/)

### Inferences
- Recommended stack for a Python/Qt app:
  - Use FTS5 with two indexes over the same content: (a) `unicode61 remove_diacritics 2` plus a `porter` wrapper for ranked word search, and (b) a `trigram` index for substring, mid-word and CJK-tolerant matching.
  - Use `prefix='2 3 4'` for search-as-you-type.
  - Use external-content tables so manuscript text is stored once.
  - For ranking, weight the title column above the body via bm25 column weights.
- For typo tolerance, pull the vocabulary from an `fts5vocab` table, run RapidFuzz `process.extract` (or a SymSpell symmetric-delete dictionary) to expand misspelled terms into an OR query, and then run FTS. This is the standard deterministic "did you mean" pattern.
- Target under 100 ms per keystroke for search-as-you-type. Run queries on a worker QThread, debounce at roughly 50-150 ms, and cancel stale queries.
- (Prior knowledge, unverified this session) Tantivy's QueryParser supports per-field fuzzy matching through Levenshtein automata, plus FuzzyTermQuery, RegexQuery and a SnippetGenerator. tantivy-py exposes `Query.fuzzy_term_query`, `regex_query` and `parse_query(..., fuzzy_fields=...)`. Verify against the tantivy-py docs.
- (Prior knowledge, unverified this session) The following are worth evaluating:
  - SymSpell's symmetric-delete algorithm, which claims orders-of-magnitude-faster spelling correction. symspellpy is MIT.
  - PyICU, for ICU word-break tokenization in Thai/CJK as a custom FTS5 tokenizer outside SQLite core.
  - The `whoosh-reloaded` fork. Its status was not confirmed.

### Gaps
- No authoritative FTS5 or Tantivy benchmark at about 100k notes with latency and index size together was found. The app should run its own benchmark on representative corpora.
- The tantivy-py API reference page returned 404, so its fuzzy, snippet and tokenizer API was not confirmed this session.
- Current Xapian licensing (post-2012) was not confirmed. The symspellpy repo page returned 404, so its license and speed were not confirmed.
- The SQLite version that introduced the trigram tokenizer was not stated in the excerpt read. (Prior knowledge: 3.34.0, with `remove_diacritics` for trigram added later. Verify.)

## 2. Faceted / structured search and saved searches

### Takeaway
Writers' tools treat saved searches as "smart folders" that update live. Scrivener's Saved Search Collections and Obsidian's embedded `query` blocks are the reference designs. Facets such as source type, author, date, tag, project and citation status are best stored as ordinary SQL columns joined to FTS results, or as Tantivy facet fields.

### Cited Findings
- Scrivener's Project Search (Cmd/Ctrl-Shift-F) searches the entire project, including the Research folder. Results replace the Binder view, which turns purple. — [L&L blog](https://www.literatureandlatte.com/blog/use-scriveners-project-search-to-find-anything-in-your-projects)
- A Scrivener Saved Search Collection is "constantly updated", and forum staff describe it as a "smart folder". Users keep saved date-based searches such as recently modified files. Standard Collections are manual, alias-like groupings, for example by character, POV, timeline or location. — [Kirkville on Collections](https://kirkville.com/how-to-use-scriveners-collections-to-access-groups-of-files-or-search-results-quickly/); [L&L forum](https://forum.literatureandlatte.com/t/sort-by-date-modified/36814/6)
- Obsidian supports property filters (`[prop:value]`), tag filters and path filters, and can embed live query results in a note. — [Obsidian Help: Search](https://obsidian.md/help/plugins/search)
- Tantivy supports hierarchical facet fields. — [tantivy GitHub](https://github.com/quickwit-oss/tantivy)

### Inferences
- Implementation:
  - Store a saved search as a serialized query AST (text query + facet filters + sort) in the project DB, and re-evaluate it when the index changes.
  - Show facet counts with `GROUP BY` over the FTS rowid join.
  - Persist "citation status" (cited / uncited / needs page number) by joining citation keys found in the manuscript against the source library.

### Gaps
- No benchmark or UX study comparing facet UIs in writing apps was found.

## 3. Knowledge-management patterns: backlinks, unlinked mentions, transclusion, codex/story-bible mention detection

### Takeaway
The highest-value deterministic patterns for writers are:
- backlinks with an "unlinked mentions" pane, which Obsidian matches on the note name;
- block references and embeds, pioneered by Roam and adopted by Logseq and Obsidian;
- a codex or story bible whose entries are auto-detected in prose by name and aliases. Novelcrafter is the best example, with case-sensitivity toggles, plural handling, exclusions and a mention heatmap.

All of this is deterministic and can be implemented with Aho-Corasick (pyahocorasick, BSD-3-Clause).

### Cited Findings
- Obsidian:
  - Linked mentions are notes containing an internal link to the active note.
  - Unlinked mentions are "any unlinked occurrence of the name of the active note". Excluded-files patterns are honored.
  - The pane offers "show more context" (full paragraph), sort order, filtering, and a toggle to show backlinks at the bottom of the document.
  - Source: [Obsidian Help: Backlinks](https://obsidian.md/help/plugins/backlinks)
- Block references were pioneered by Roam Research and later adopted by Logseq and Obsidian. They link to or embed a specific block rather than a page. — [dsebastien notes](https://notes.dsebastien.net/30+Areas/33+Permanent+notes/33.02+Content/Block+references)
- Logseq distinguishes block embeds (a block with its children) from page embeds. Users have reported reference counters failing to update until the graph was re-indexed, which is an index-consistency hazard. — [Logseq forum](https://discuss.logseq.com/t/the-difference-between-logseqs-block-embeds-and-page-embeds/8460)
- Roam and Logseq offer "replace block ref with text" and "replace with embed". Swapping a block with its reference is available via a Logseq plugin. — [Logseq forum](https://discuss.logseq.com/t/replace-block-ref-with-text-and-alias-and-more/2190); [Logseq forum](https://discuss.logseq.com/t/swap-a-blockref-with-the-original-item/1214)
- Novelcrafter Codex tracking:
  - Each entry has a "Track this entry by name/alias" toggle. Names and aliases are underlined in the manuscript, plan and snippets, and tracked entries feed a mention heatmap.
  - Matching is case-insensitive by default, with case-sensitive matching available for names like "Will" or "May".
  - English plurals are matched automatically, but irregular plurals such as "Wolves" must be added as aliases.
  - Each entry has an exclusion list.
  - Source: [Novelcrafter: Codex tracking](https://www.novelcrafter.com/help/docs/codex/codex-tracking); [Aliases](https://www.novelcrafter.com/help/docs/codex/aliases); [Codex mentions](https://www.novelcrafter.com/help/docs/codex/codex-mentions)
- pyahocorasick is a "fast and memory efficient library for exact or approximate multi-pattern string search". Its runtime depends mainly on input length. The automaton must be built and finalized before searching. It is BSD-3-Clause. — [pyahocorasick docs](https://pyahocorasick.readthedocs.io/)
- In one third-party benchmark, pyahocorasick handled 1,000 keywords over 1M characters in about 0.03 s to build and about 0.05 s to search. This is a single, non-independent benchmark. — [PyPI pyahocorasick](https://pypi.org/project/pyahocorasick/1.4.4) (search summary)
- Spark NLP's EntityRuler uses Aho-Corasick for dictionary (gazetteer) entity matching, which suggests the pattern is standard. — [John Snow Labs](https://nlp.johnsnowlabs.com/licensed/api/com/johnsnowlabs/nlp/annotators/er/index.html)

### Inferences
- Codex design:
  - Build one Aho-Corasick automaton from all entity names and aliases, lowercased and diacritic-folded, plus generated regular plurals and possessives ("'s").
  - Post-filter matches on word boundaries.
  - Apply per-entry case-sensitive flags and exclusion phrases.
  - Rebuild the automaton only when the codex changes, and re-scan only edited paragraphs, keyed by a paragraph hash.
  - This drives underline highlighting, hover cards, per-chapter mention counts and heatmaps, and "first/last appearance" for continuity.
- Unlinked mentions are the same mechanism applied to note titles and aliases. Offer a one-click "link it" action.
- For transclusion, give each block a stable ID (UUID stored in the document model) so references survive edits. Logseq's counter bugs show the link index must be updated transactionally with the edit.
- Graph views and Zettelkasten are mostly navigation sugar over the same link table (`links(src_block, dst_note, dst_block, kind)`). Backlinks are a reverse index lookup on that table.

### Gaps
- Tana supertags, Notion synced blocks and Obsidian alias handling for unlinked mentions were not verified this session. Search returned nothing on Tana.
- No sources were gathered on timeline views. Prior knowledge: Aeon Timeline and the Scrivener/Plottr ecosystem. Unverified.

## 4. Writer research tools, PDF annotation, and robust anchoring (W3C Web Annotation, Hypothesis)

### Takeaway
Link highlights to manuscript passages with the W3C Web Annotation selector model, storing a TextQuoteSelector, a TextPositionSelector and a structural selector together. Re-anchor with Hypothesis's cascade: structure, then position, then fuzzy context, then fuzzy quote, using diff-match-patch (Bitap and Myers diff). Zotero's open-pdf deep links are the model for jump-to-source. For PDF extraction in Python, watch licensing: PyMuPDF is AGPL or commercial.

### Cited Findings
- W3C Web Annotation Data Model became a Recommendation on 23 Feb 2017. Its selectors are:
  - FragmentSelector, CssSelector and XPathSelector
  - TextQuoteSelector (`exact`, plus `prefix`/`suffix` for disambiguation)
  - TextPositionSelector (`start` inclusive, `end` exclusive)
  - DataPositionSelector, SvgSelector, and RangeSelector (startSelector/endSelector)
  - `refinedBy`, for narrowing a broader selector
  - Source: [W3C Annotation Model](https://www.w3.org/TR/annotation-model/)
- Hypothesis stores three selectors per annotation: RangeSelector (XPaths + offsets), TextPositionSelector (global offsets) and TextQuoteSelector (exact + 32 characters of prefix and suffix). It re-anchors in this order:
  1. Range, if the quote matches.
  2. Position, if the quote matches.
  3. Context-first fuzzy: find the prefix near the expected start and the suffix near the expected end, then accept if the span is close to the exact text.
  4. Fuzzy search of the whole document for the exact quote.
  - It uses a modified fork of Google's diff-match-patch: Bitap for fuzzy search and Myers diff for comparison.
  - Source: [Hypothesis: Fuzzy Anchoring](https://web.hypothes.is/blog/fuzzy-anchoring/)
- Zotero 7 extracts annotations into a note via "Add Note from Annotations". The note is organized by page and includes tags and comments. — [UCL Zotero guide](https://library-guides.ucl.ac.uk/zotero/extracting-annotations); [UCL FAQ](https://library-help.ucl.ac.uk/faq/242186)
- `zotero://open-pdf/...?page=N&annotation=KEY` links open the PDF at the annotation's page, whereas `zotero://select` only selects the item. Zotero 7 lost the highlight "blink" on arrival, which staff called a regression, and opening EPUBs or snapshots at a position was not yet supported then. — [Zotero forums](https://forums.zotero.org/discussion/comment/401697); [Zotero forums](https://forums.zotero.org/discussion/109109)
- PyMuPDF is available under AGPL or a commercial Artifex license, with non-public pricing. pypdf is BSD-3-Clause and pure Python, and reportedly about 15x slower at text extraction according to a vendor comparison. — [PyPI PyMuPDF](https://pypi.org/project/PyMuPDF/1.19.2/); [file2markdown](https://www.file2markdown.ai/blog/pymupdf-alternatives)
- Scrivener's Research folder is included in project search. — [L&L blog](https://www.literatureandlatte.com/blog/use-scriveners-project-search-to-find-anything-in-your-projects)

### Inferences
- Store each "research link" as a W3C-style annotation (JSON-LD compatible) with:
  - target: the source file hash plus the three selectors;
  - body: a manuscript block ID.
- Apply the same anchoring in reverse for manuscript-side anchors, so links survive the author's edits.
- `diff-match-patch` has a Python port (Apache-2.0, prior knowledge). It provides `match_main` (Bitap), which can implement steps 3 and 4 directly.
- For permissive PDF handling, consider pypdfium2 (Apache/BSD, prior knowledge) for rendering and text, pypdf for reading annotation dictionaries, or Qt's QtPdf module (part of Qt; check the LGPL/commercial terms of your Qt license).

### Gaps
- No details were gathered this session on LiquidText, MarginNote, Readwise export formats, Ulysses or Nota.
- pypdfium2 and QtPdf annotation-extraction capability was not verified.

## 5. Deterministic "related content", keyword extraction, NER, and where the AI line is

### Takeaway
"More like this" can be done fully deterministically:
- BM25 or TF-IDF using the current paragraph's top-weighted terms as a query;
- shared tags and links (co-citation, bibliographic coupling);
- MinHash/SimHash for near-duplicates;
- statistical keyword extraction with YAKE, RAKE or TextRank.

Classical NER with spaCy small models is a trained statistical model, so it is machine learning, though not generative AI. Gazetteer matching is the strictly rule-based alternative.

### Cited Findings
- FTS5 exposes per-term statistics through `fts5vocab` and BM25 ranking, which are the building blocks for a TF-IDF/BM25 more-like-this query. — [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- YAKE's license is inconsistent across sources:
  - INESC TEC (the owner) lists AGPLv3 or commercial.
  - The PyPI page for 0.3.0 says MIT.
  - DepScope reports LGPLv3 for 0.7.3.
  - Sources: [INESC TEC YAKE](https://www.inesctec.pt/en/technologies/yake); [PyPI yake 0.3.0](https://pypi.org/project/yake/0.3.0); [DepScope](https://depscope.dev/pkg/pypi/yake)

### Inferences
- More-like-this recipe:
  - Tokenize the current paragraph.
  - Score terms by tf × idf, using FTS5's vocab document counts.
  - Take the top 10-20 terms as an OR query, rank by bm25, and exclude the current document.
  - Blend in Jaccard overlap of tags and outgoing links.
  - This runs in milliseconds and is explainable ("shares: Vienna, 1938, Anschluss").
- Co-citation: two sources cited in the same paragraph or chapter are related. Compute this from the citation-key table.
- Near-duplicates: SimHash for drafts and snippets, or MinHash LSH. (Prior knowledge, unverified: datasketch is MIT licensed.)
- (Prior knowledge, unverified) Licensing of the options:
  - rake-nltk is MIT.
  - TextRank via `pytextrank` is MIT but requires spaCy.
  - spaCy is MIT, and its `en_core_web_sm` models are MIT.
- Classify spaCy statistical NER as "classical ML" in product copy, separate from generative AI. EntityRuler and pyahocorasick gazetteers are purely rule-based.
- YAKE's license must be checked against the exact release before bundling.

### Gaps
- No benchmarks were found for YAKE, RAKE or TextRank on literary prose.
- datasketch, spaCy and symspellpy licenses were not confirmed via fetched sources.

## 6. Context-surfacing UX, document-internal context, and offline indexing performance

### Takeaway
The proven UX set is:
- a backlinks/mentions sidebar with paragraph context;
- project search that converts results into a navigable list, as Scrivener does with the Binder;
- embedded live queries and saved searches;
- hover cards for codex entities;
- deep links that jump to a source page.

For performance, use incremental indexing driven by file watching or edit events, FTS5 external-content triggers, and periodic merge/optimize. Keep interaction under 100 ms per Nielsen.

### Cited Findings
- Obsidian's backlinks pane offers "show more context" (full paragraph), a filter field, and backlinks rendered in the document. — [Obsidian Help: Backlinks](https://obsidian.md/help/plugins/backlinks)
- Scrivener project search turns the Binder into a results list and can be saved as a self-updating collection. — [L&L blog](https://www.literatureandlatte.com/blog/use-scriveners-project-search-to-find-anything-in-your-projects)
- Novelcrafter underlines tracked entity names in the manuscript and provides a mention heatmap. — [Novelcrafter: Codex tracking](https://www.novelcrafter.com/help/docs/codex/codex-tracking)
- FTS5 supports incremental updates via triggers on external-content tables, `automerge`/`crisismerge` to bound segment counts, `optimize` for a full merge, and `rebuild` and `integrity-check` for recovery. — [SQLite FTS5 docs](https://sqlite.org/fts5.html)
- Tantivy supports incremental indexing and mmap with a startup time under 10 ms. — [tantivy GitHub](https://github.com/quickwit-oss/tantivy)
- 0.1 s is the threshold at which a response feels instantaneous. — [NN/g](https://www.nngroup.com/articles/response-times-3-important-limits/)

### Inferences
- Indexing pipeline:
  - Index at paragraph or block granularity rather than per file, so edits re-index only the changed blocks. Detect changes by a block content hash.
  - Batch writes in a single transaction on a background thread, with SQLite WAL mode.
  - Use watchdog (prior knowledge: Apache-2.0) or `QFileSystemWatcher` for external research folders, debounced.
  - Run `merge` during idle time and `optimize` on project close.
- Document-internal context features, all deterministic:
  - Cross-references: stable IDs on headings and figures, with references resolved at render time.
  - Index and glossary generation: term list plus Aho-Corasick occurrences mapped to page or section.
  - Consistency checks:
    - name variants, by fuzzy-clustering capitalized tokens with RapidFuzz (e.g., "Katharine/Katherine");
    - date and number extraction with regex;
    - inconsistent hyphenation or spelling, by counting variant forms in the FTS vocab.
  - Outline navigator: built from heading structure.
  - Find-in-project with replace: run FTS for candidate blocks, then a regex pass, with a preview and undo transaction.
- A command palette and quick-open should use a subsequence fuzzy scorer over titles (RapidFuzz `WRatio` or `partial_ratio`), held in memory. For about 100k titles this is a cdist-style batch call.
- Version-history search: index snapshots in a separate FTS table with a `snapshot_id` UNINDEXED column. A contentless-delete table can hold old versions compactly.

### Gaps
- No published latency or index-size benchmark for about 100k notes in FTS5 or tantivy-py was found. Treat any figure as needing in-house measurement.
- No authoritative sources were gathered on Ulysses, Nota, LiquidText, MarginNote, Readwise, citation hover-preview implementations, or Notion synced blocks.
