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

**Anchors.** A citation mark can carry an optional `anchor` object with three selectors on
the *source* side: `quote` (the source text), `prefix` and `suffix` (up to 32 characters of
source text either side) and `position` (`{start, end}` in the cited chunk), plus the
`content_hash` of the source version it was anchored to and a `state` (`anchored` or
`unanchored`). It is separate from the mark's own `prefix`/`suffix`, which remain rendering
affixes such as `see `. "Insert as citation" (below) fills it; `byax cite --input` accepts
it; older readers ignore it (document schema 3).

## Grounding, citation chips and the citation audit

Every citation, whatever made it, becomes one grounding record
(`byax.grounding.GroundingRecord`): the answer span, the cited chunk and the span inside it,
the source quote with its 32-character prefix and suffix, the source content hash at
citation time, the origin (`native`, `token` or `user`) and a state. Records come from:

- **Native citations.** In CHAT mode an Anthropic provider receives the attached library
  passages as custom-content documents, one content block per chunk, with citations
  enabled; the reply's citations are mapped back to chunks. Gemini grounding supports and
  Cohere citation spans are mapped the same way (`from_gemini`, `from_cohere`; tested on
  shape fixtures, no adapter requests them yet).
- **`[[cite:ID]]` tokens.** WORK mode and every other adapter keep the token protocol. The
  source span is the claim's quotation located in the chunk, or the cited sentence aligned
  with it (`quote_match.align`).
- **Your own citation marks** (the audit below).

A cited ID that was not supplied with the request no longer rejects the reply: the token is
removed and its record is `unverified`. For a local provider the request is retried once
with a corrective note; for a remote one only when the provider setting **Retry once when a
remote model cites an unknown source** (`citation_retry`) is on, because the retry is a
second transmission.

**States.** Stage 1 of the verification cascade is deterministic and local:

| State | Meaning |
| --- | --- |
| `supported` | The claimed quotation (or a near-verbatim claim) is in the cited chunk, exactly or after normalization |
| `partial` | A near match, or a claim that shares part of the chunk's wording |
| `unsupported` | The claimed quotation is not in the cited chunk |
| `not_checkable` | A paraphrase: wording does not show meaning, so stage 1 cannot judge it |
| `unanchored` | The cited source text is not available, or was not found again after the source changed |
| `unverified` | The ID was not supplied with the request |

Stage 2 (local entailment) plugs into the same cascade later (`grounding.register_stage`);
until then a request for it is reported as `not_run`. Stage 3 is the optional judge
([below](#optional-judge-stage-3)). Support for a paraphrase is never inferred from word
overlap. `grounding.split_claims(text)` splits a reply into sentences and clauses (with
exact offsets) for the model-based stages: abbreviations such as `Dr.`, `e.g.`, `et al.`,
initials and decimals do not end a sentence, and a sentence is split at a semicolon or at
`, but` / `, yet` / `, and` / `, so` / `whereas` / `although` only when both sides have four
words and the split is not inside quotation marks or brackets.

**Chips.** In the AI tab each citation shows as a numbered chip after the claim it
supports: `[1]` found or not checkable (the hover card says which), `[1~]` partial, `[1!]` not found, `[1×]` unanchored, `[1?]`
unverified. Hovering a chip shows the source title, the locator (page, heading path,
block, paragraph, offsets) and the chunk text with the cited span highlighted. Clicking
opens the library reader at the locator with the span selected; if the source changed since
the answer, the reader re-anchors and says so. **Insert as citation** in the reader cites
the selected document text with that source, its locator, the quotation and the anchor, so
ordinary checks and the audit cover it afterwards.

**Audit your own citations.** `byax check --citations` (MCP `byax_check` with
`citations: true`, REST, or Checks tab → **Citation audit**) runs stage 1 over every
citation mark:

| Status | Meaning |
| --- | --- |
| `quote_found` | The quotation is at the cited locator (exact or normalized) |
| `near_match` | Close but not identical; the differences are listed |
| `quote_not_found` | Not at the locator; `found_elsewhere` lists other places in the source with the exact text |
| `source_changed` | The source's content hash differs from citation time; when the quotation is found again, `reanchor` gives the new locator and a proposed update |
| `unanchored` | The source changed and every re-anchoring step failed. The citation is kept, shown with its quote struck through |
| `source_unavailable` | The library is not attached or cannot be read, or a manual source has no text to compare |
| `not_checkable` | A paraphrase without a quotation |

Re-anchoring tries structure first (the same page, block, paragraph or heading path), then
the recorded position, a fuzzy search near it, and a fuzzy search anywhere in the source.
The audit is read-only, deterministic and local: it reads the document and the attached
library indexes, needs no consent and makes no network request. Its `operations` propose a
source pointer update and `reanchor_citation` operations (new locator and anchor, or state
`unanchored`); nothing changes until you apply them (`byax apply --input`, or **Apply
proposed anchor updates** in the dialog). `validation` is `failed` when a quotation is not
found or a citation is unanchored (exit code 4); `review` is `required` for any other
outcome that needs a look.

## Version-pinned citations

Every citation records the version of the source it cites: a library source pins the document's
content hash (`library.content_hash`, and the mark's `anchor.content_hash`), and a source that is
another `.byax` draft pins `byax = {path, document_id, revision, content_hash}`, where the content
hash is the SHA-256 of the draft's body text. Cite a draft with
`byax source Essay.byax --action add-draft --draft-path notes.byax` (the draft must be in the
document's folder or its parent folder), then `byax cite` as usual.

The citation audit (`byax check Essay.byax --citations`, Checks → Citation audit) includes
`versions`, the version-pin check (`byax.version_pins.check_pins`). Each cited source is `current`,
`superseded` (a newer version exists: the library file was re-indexed with different content at
the same path, or the draft's current revision differs; when the pinned revision is still in the
draft's history the message says it is an *earlier* version), `unavailable`, `unverified` (the
file changed since indexing; refresh the library) or `unpinned`. A superseded citation is never
hidden: it is a finding, `citations.superseded_version` ("Cites an earlier version of “Notes”"),
on the exact cited range, and counts as needing review. Nothing is changed automatically:

- `versions.operations` record the superseded state on the source (`superseded_by: {content_hash,
  revision?}`), metadata only;
- `versions.proposals` add one review proposal per source that re-pins its citations to the current
  version (and updates their anchors' content hash) once you have read what changed. In the desktop
  app, **Propose re-pinning…** in the audit window does both.

Rendered output (exports, compile, print) is unchanged unless you set
`settings.citations.annotate_superseded` to `true`; then citations whose source records
`superseded_by` get " (cites an earlier version)" after the rendered citation or note.

## Optional judge (stage 3)

Some citations the deterministic stages cannot settle: a paraphrase (`not_checkable`), a near
match (`partial`) or a quotation that was not found (`unsupported`). A second model can read
the claim and the cited text and say `supported`, `partial` or `unsupported` with a one- or
two-sentence reason. It is off unless you name a provider, and:

- **It is never the writer.** `ProviderRegistry.chat(..., judge_alias=)` and
  `byax check --citations --judge ALIAS` refuse the alias that generated the answer, and an alias
  with the same provider kind, model and endpoint, before anything is sent. An alias that
  cannot hold a text conversation (a speech-to-text provider) is refused too.
- **Only low-confidence cases are sent.** A record the quote stage found (`supported`), a
  record with no claim and a record whose source text is unavailable are never sent.
- **Only under existing consent.** A remote judge needs explicit authorization for the run (or a
  saved remote policy on the alias) and the `library_passages` permission for that alias,
  because the cited text is sent. Text from a `local_only` library never goes to a remote
  judge; an unknown library policy is treated the same way. A local (loopback) judge needs
  neither. `--authorize` on the CLI stands for both approvals for that run. MCP and REST refuse
  `judge`.
- **The claim and the source are data.** Both are framed in provenance delimiters and the judge is
  told to ignore any instruction inside them. The answer must be one JSON object; anything else is
  recorded as `not_run`, never guessed. Percentages are not requested and are removed from the
  rationale.
- **It adds to the evidence.** The check is appended to the record (`stage: judge`, `result`,
  `rationale`, the judge's model, and the state it started from); the earlier checks stay. The
  verdict is advisory, like every other support state.

```bash
byax providers --action configure --input judge.json     # a different provider alias from the writer
byax check Essay.byax --citations --judge my-judge       # local judge; add --authorize for a remote one
```

In the audit, each judged item keeps its stage 1 `status` and gains `judge` and an updated
`record`; the report is marked `deterministic: false`, and `network: true` when the judge is
remote. Library API: `grounding_judge.make_stage`, `judge_grounding`, `judge_audit`; tests use a
fake provider (`tests/test_grounding_judge.py`).

## Bibliography health

**Tools → Bibliography health…** (or the Checks tab, or `byax bibliography PATH`) reports on the
reference list. The checks that need no network run first, every time:

| Check | What it finds |
| --- | --- |
| DOI syntax | A DOI that is not `10.<registrant>/<suffix>`, or that ends with punctuation copied from a sentence (a repair is suggested) |
| Citations against the list | Citations whose source is not in the list, entries never cited, and `(Author, Year)` text with no matching entry (marks and plain author-year text are both read) |
| Citation density | Citations and words per section (a section runs from a heading to the next), citations per 1,000 words, and long sections with no citations |
| Reference age | A distribution of the references by age (0–2, 3–5, 6–10, 11–20, over 20 years), the median and how many are over ten years old, for the listed and for the cited references |
| Punctuation placement | In author-date and numeric styles the sentence period follows the parenthetical citation; in note styles (footnotes and endnotes) the note mark follows the punctuation and the closing quotation mark. Block quotations are exempt. The document's style and presentation decide the rule; a CSL style's `citation-format` decides for installed styles |
| Retractions | From an offline Retraction Watch file you imported (below) |

**Online checks** are a separate, explicit step: **Check online…** in the panel, or
`byax bibliography PATH --online`. They need your permission first. The panel lists each service
and what it would receive, and you can remember the choice; on the CLI, `--allow-network` approves one
run and `byax bibliography --action consent` stores the permission (the `citation_lookup` category,
kept per service in the consent file next to provider settings). The services are:

- **Crossref** (every DOI), then **DataCite** for DOIs Crossref does not know, then **OpenAlex** as a
  last fallback: does the DOI resolve, and do the title (fuzzy match with RapidFuzz), the author
  family names and the year agree with the registry? A subtitle the registry adds and a truncated
  author list are small differences, not errors. Differences are reported as *Differs from the
  registry* (title, authors or year more than one year apart) or *Small difference*.
- **Retractions and corrections** from Crossref's `updated-by` notices (which include the Retraction
  Watch data Crossref carries) and `relation` entries, and OpenAlex's retracted flag. A retracted
  work is the only finding shown as serious; the notice DOI and date are listed. A reference that is
  itself a retraction notice is flagged.
- **Dead links** (a reference's `URL`) checked from this computer with a short HEAD request, falling
  back to GET: redirects are followed by hand and every hop must be a public address, so a link
  cannot point the check at your own computer or network. `404`/`410` is reported as *may be dead*;
  anything else that blocks automatic checks (403, 429, timeouts) is *unverified*. With **archived
  copies** the Internet Archive's availability API is asked for the closest snapshot of a dead
  link.

**Not found is not fake.** A DOI that none of the registries knows is shown as *Unverified, please
check*, with the registries that were asked. A registry that did not answer, or that rate-limited the
run, is *could not be checked just now*. Neither is ever called fake or invalid.

**Etiquette.** Every request names ByAuthorX and the project page in its `User-Agent`. A contact
address is sent to Crossref and OpenAlex (their "polite pool") only if you set one (**Contact
address…**, or `--action configure --input mailto.json` holding `{"mailto": "you@example.org"}`). Requests to a host are
spaced (0.2 s for the registries, 1 s for the Wayback Machine, 0.5 s for linked sites), a 429 stops that
host for the rest of the run, timeouts are 15 s and answers are limited to 4 MiB. Answers are cached in
the user cache (found for 30 days, not found for 3), so a repeat check is free; `byax bibliography PATH`
without `--online` builds its report from the cache alone and says so (`network.mode: cache only`).

**Retraction Watch file.** Download the Retraction Watch database as CSV yourself, then **Import
Retraction Watch file…** or `byax bibliography --action retractions-import --source-path rw.csv`.
Required columns: `OriginalPaperDOI` and `RetractionNature` (`Retraction`, `Correction`, `Expression
of concern` or `Reinstatement`). Used when present: `Record ID`, `Title`, `Reason`, `RetractionDate`,
`RetractionDOI`, `Journal`, `Notes`. Rows without a DOI are skipped and counted. The file is indexed
in the user data folder (SQLite); nothing is uploaded.

Over MCP and REST only the cached report is available; online checks, consent, the contact address and
imports are local CLI and desktop actions. `validation` is `failed` (exit code 4) when a reference is
retracted, `review` is `required` for anything to look at. `findings` are paged with `--limit` and
`--offset`. Tests replay recorded Crossref, DataCite, OpenAlex and Wayback answers
(`tests/fixtures/bibliography`; re-record with `tests/bibliography_fixtures.py`); the live checks run
only with `pytest -m live`.

## Bibliography exchange and citation styles

- **RIS.** `byax bibliography PATH --action ris-export [--out refs.ris]` writes every source;
  `--action ris-import --source-path refs.ris` (or the Import sources… button) reads one. Import
  understands the common tags: type (`JOUR`, `BOOK`, `CHAP`, `CONF`, `THES`, `RPRT`, `ELEC`, `NEWS`,
  `DATA`, `COMP` and more), `TI`/`T1`, authors `AU`/`A1`, editors `ED`/`A2`, series editors, translators,
  container `JO`/`JF`/`T2`, `JA`, series `T3`, dates `PY`/`DA`/`Y1` (`2019/03/05/Spring`), access date `Y2`,
  `SP`/`EP`, `VL`, `IS`, `PB`, `CY`, `SN` (told apart as ISBN or ISSN), `DO`, `UR`, `AB`/`N2`, `KW`, `N1`,
  `LA`, `ET`, `AN`, `M3`. A corporate author ends with a comma (`World Health Organization,`). Tags
  with no CSL field are kept under `custom.ris` and written back on export. Re-import is idempotent.
  RIS export carries bibliographic fields only, never library paths or excerpts.
- **BibTeX** export also writes ISBN, ISSN, abstract, edition, series, address, keywords, month and
  editors; organizations are braced. `--action export-bib` writes it with stable citation keys
  (below).
- **More CSL styles.** APA, MLA and Chicago are built in. `byax bibliography --action styles-preview
  --name ieee` downloads one style from
  `https://raw.githubusercontent.com/citation-style-language/styles/master/<name>.csl` for display
  only: it shows the URL, size, SHA-256, title and licence and installs nothing. `--action styles-add
  --name ieee --allow-network --expect-sha256 …` installs it, refusing a file that differs from the one
  previewed. `--source-path file.csl` imports a local file without the network. The file must be a
  standalone CSL 1.0 style (a dependent style names its parent, which you add instead) that the
  citation processor can load; it is stored with its hash in the user data folder, and **every use
  re-checks the hash**, so an edited file is refused. Installed styles appear in **Citation
  style…** and work with `source --action bibliography --style NAME`. A document records only the
  style's name, so on a computer that lacks the style, rendering and export stop with a message naming
  it (add it again or choose another style) instead of silently using a different one. The styles are CC BY-SA 3.0,
  attributed in [citation-licenses.md](citation-licenses.md); each file keeps its own author block.
  **Tools → Citation styles…** does the same with a confirmation showing the source, size and
  checksum.

## Zotero

**Tools → Import from Zotero…** and `byax bibliography PATH --action zotero-import` read Zotero 7's local
API (`http://localhost:23119/api/`, read-only; only `GET` is ever sent, only to a loopback address, and
Zotero is never changed). In Zotero turn on *Settings → Advanced → Allow other applications on this
computer to communicate with Zotero* and keep it open. `zotero-collections` lists collections;
`--collection KEY` imports one; `--no-annotations` skips annotations; `--dry-run` reads and counts without
changing the document.

- **Items** (`/api/users/0/items/top` with `include=data,csljson`) become source records, idempotent by the
  Zotero item key: importing again updates the record, keeps your own notes and does not duplicate.
  Attachments and standalone notes are skipped.
- **Annotations** (`itemType=annotation`, made in Zotero's PDF reader and attached through the PDF
  attachment to the item) become **linked notes** on the source: `kind` (highlight, underline, note, image,
  ink), the highlighted `text`, your `comment`, the **page as a locator** (`{"page": "436"}`, the page
  label, or the reader's page index plus one), colour, tags and the attachment. `zotero.citation_for_note`
  gives the quotation and locator to cite a note with (`cite --kind quotation`).
- **Citation keys.** A Better BibTeX key in the `citationKey` field or in `extra` (`Citation Key:
  smith2020`) is kept (`zotero.citation_key`). `--action export-bib [--only-zotero] [--out refs.bib]` writes
  BibTeX using those keys; other sources get `familyYYYY` with `a`, `b` on a clash, and a generated key never
  collides with a kept one.

The Zotero JSON in `tests/fixtures/zotero` is written to the Zotero Web API v3 format that the local API
serves; it was not captured from a running Zotero. MCP and REST refuse every Zotero action.

## Excerpts and where a source is used

An **excerpt** is source text brought into the manuscript with a pointer back. In the document it is an
ordinary paragraph (a block quotation by default) carrying an `excerpt` mark (document schema 3):
`value` is the source ID, `quote` the text, `locator` the library chunk and page, and `anchor` the same
three source-side selectors a citation keeps (position, quote and 32 characters of context, plus the
source's content hash). `byax excerpt PATH --action add --source ID --chunk-id CHUNK [--start N --end N]
[--target BLOCK]` inserts one after a block (or `--text "phrase"` to find a phrase in the source; it must
be there verbatim). The text is the source's, not retyped.

- **Live.** `--action check` compares every excerpt with its source: `current`, `edited` (you changed
  your copy), `source_changed` (the source changed and the excerpt was found again, with a proposed
  refresh when its wording differs), `unanchored`, `source_removed`, `source_unavailable` or `unchecked`
  (the source is not in a library). It proposes operations and changes nothing. `--action refresh`
  records them as pending proposals (one for the source pointer, one for the excerpts); the desktop's
  **Apply proposed updates** applies them as two separate actions, because source corrections and
  document corrections are never mixed.
- **Deleting a source never erases an excerpt.** `remove_reference` leaves the paragraph and its mark, marks
  the anchor `unanchored` with the reason `source_removed`, and keeps the source ID, so adding the source
  back and checking again re-anchors it. A source that is still cited cannot be removed (the existing
  citation rule); excerpts do not block removal.
- **Where is this source used.** **Tools → Where is this source used…** and `excerpt --action where-used`
  list every citation and excerpt per source with the section (nearest heading), the marked text and its
  state; sources no longer in the list but still pointed to by an excerpt are shown as removed;
  `--include-unused` lists unused sources. Double-click a row to go to the block.

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
disclosed (`coverage`, `notice`). Vectors are float32 values in the index cache. A hosted or
Ollama embedder needs no model files here; for an embedder that runs inside ByAuthorX see
[on-device semantic search](#on-device-semantic-search).

## On-device semantic search

Semantic search can run entirely on your computer, with no provider, no account and no Ollama. It is
the optional extra `byauthorx[local-models]` (ONNX Runtime and tokenizers, MIT and Apache-2.0); the
installers include it. Without it everything else works and `byax models` reports the runtime as
`not installed`.

**Models.** A pinned catalogue (`src/byax/resources/models.json`) lists each model's licence (read from
the model card of the primary source), upstream repository at a fixed commit, mirror URL, size and
SHA-256. See [third-party notices](third-party-notices.md).

| Model | Role | Size | Notes |
| --- | --- | --- | --- |
| Qwen3-Embedding-0.6B (int8 ONNX, truncated to 512 dimensions) | embedder | 625 MB | Apache-2.0. Multilingual. Queries get an instruction prefix, passages do not. Vectors are cut to 512 dimensions (Matryoshka) and re-normalized |
| answerai-colbert-small-v1 (33M, ONNX with its 96-dimension projection) | reranker (default) | 134 MB | Apache-2.0. English. Late interaction (MaxSim); the fp32 export is used because the int8 file in the same repository lacks the projection layer |
| bge-reranker-v2-m3 (int8 ONNX cross-encoder) | reranker (optional) | 571 MB | Apache-2.0. Multilingual, accurate and very slow on a CPU (see below) |
| docling-layout-heron | PDF layout (optional) | 172 MB | Apache-2.0. Only for the Docling PDF engine |

The standard installer bundles the embedder and the small reranker (about 760 MB, under the 800 MB plan
budget); the lean installer bundles none. Other models install on request. **A model downloads only when
you ask.** `byax models --action download --name …` prints the plan (source, each file's URL, size and
SHA-256, the destination, the licence) and downloads nothing until you repeat it with `--confirm`; the
desktop button (Tools → On-device search…) shows the same facts in a confirmation. Downloads resume from
`.part` files, every file is checked against the pinned size and SHA-256 before it is used, a mismatch
discards the file, and the model is checked again when loaded. Nothing is fetched in the background; the
project's `models-v1` mirror release is used once it is published (until then the pinned upstream
revision is the source, shown in the plan). Offline, listing, status and verification still work and
download fails with `network_unavailable`.

**Turning it on.** Semantic indexing starts only when you turn it on for a library: in Tools → Libraries
choose **Enable on-device semantic search…**, or run `byax library … --action semantic-enable --confirm`.
The prompt is *Enable on-device semantic search? Nothing leaves this computer.* (the CLI prints it and
makes no change without `--confirm`). It records the library's embedder as `local:<model>`, then runs the
usual incremental embedding job. A `local_only` library may use it, because the embedder is not remote.
`semantic-disable` turns it off and drops the vectors. The chunk text embedded is the stored text with the
heading path in front (never in the stored text, so quotes still verify).

**Fast and Accurate.** *Fast* (the default) is keyword plus on-device semantic ranking. *Accurate* also
reorders the best 30 to 50 fused results (`rerank_depth`, default 40) with a local reranker. Choose it in
Tools → On-device search…, with `byax models --action configure`, or per search with `--rerank`. If the
runtime or the reranker is not installed, Accurate falls back to Fast and says so. Results keep each
moved hit's `prior_rank`, `rerank_rank` and `rerank_score`, so the order stays inspectable.

**Measured on 16 October 2026 on a shared 4-core Xeon container at 2.1 GHz (Linux, ONNX Runtime 1.31 CPU,
other processes running; load average about 4 to 8), which is not target hardware.** Raw data:
[`benchmarks/0.4-semantic-tier.json`](benchmarks/0.4-semantic-tier.json) and
[`benchmarks/0.4-vector-search.json`](benchmarks/0.4-vector-search.json).

Chunk target 200 estimated tokens, 1421 chunks, 14 queries scored (embedding 332 ms per chunk):

| Pipeline | hit@10 | MRR@10 | nDCG@10 | hit@20 | latency p50 / p95 (ms) | same query again p50 / p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| keyword | 0.286 | 0.25 | 0.2 | 0.571 | 15.4 / 35.5 |  |
| hybrid | 0.571 | 0.292 | 0.265 | 0.714 | 184.6 / 270.5 |  |
| hybrid+answerai-colbert-small-v1 | 0.714 | 0.377 | 0.366 | 0.786 | 2546.3 / 3247.5 | 88.6 / 117.6 |

Chunk target 400 estimated tokens, 633 chunks, 14 queries scored (embedding 1771 ms per chunk):

| Pipeline | hit@10 | MRR@10 | nDCG@10 | hit@20 | latency p50 / p95 (ms) | same query again p50 / p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| keyword | 0.714 | 0.419 | 0.295 | 0.857 | 16.6 / 25.6 |  |
| hybrid | 0.786 | 0.517 | 0.302 | 0.857 | 261.4 / 397.3 |  |
| hybrid+answerai-colbert-small-v1 | 0.857 | 0.696 | 0.483 | 0.857 | 4697.6 / 8109.3 | 81.1 / 115.7 |
| hybrid+bge-reranker-v2-m3-int8 | 0.857 | 0.756 | 0.462 | 0.857 | 30461.7 / 36800.8 | 28866.5 / 34766 |

Chunk target 800 estimated tokens, 280 chunks, 15 queries scored (embedding 1915 ms per chunk):

| Pipeline | hit@10 | MRR@10 | nDCG@10 | hit@20 | latency p50 / p95 (ms) | same query again p50 / p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| keyword | 0.867 | 0.543 | 0.364 | 0.867 | 6.6 / 22.1 |  |
| hybrid | 0.867 | 0.511 | 0.385 | 0.867 | 128.5 / 250.5 |  |
| hybrid+answerai-colbert-small-v1 | 0.8 | 0.639 | 0.519 | 0.867 | 1488 / 4025.4 | 59.2 / 92.8 |

Corpus: *Pride and Prejudice* (Project Gutenberg), 15 hand-written queries in `scripts/data/pride_queries.json`, relevance decided by regular expressions over chunk text (`scripts/benchmark_semantic.py`). This is a small smoke measurement, not the Phase 0 harness; it covers one novel and one language. Latency includes embedding the query. The host is not reference hardware (`x86_64`, 4 logical CPUs, shared with other processes), so treat the figures as an upper bound for a quiet machine and not as the p95 targets' verdict.

What the numbers say, and what they do not:

- **Hybrid search beat keyword search** at every chunk size (nDCG@10 0.265 against 0.200 at 200 tokens, 0.302 against 0.295 at 400, 0.385 against 0.364 at 800; hit@10 0.571 against 0.286 at 200). With 14 or 15 queries one query is worth 0.07 of hit@10, so only the direction is meaningful.
- **The ColBERT reranker lifted ranking further** (nDCG@10 0.366, 0.483 and 0.519; MRR@10 0.377, 0.696 and 0.639), and the cross-encoder gave a similar gain at 400 tokens (nDCG@10 0.462, MRR@10 0.756).
- **Hybrid latency** was within the 300 ms p95 target at 200 and 800 tokens (270 and 250 ms) and above it at 400 tokens (397 ms), the run that shared the host with the heaviest other load.
- **Reranking is not within the 1 s p95 target.** For 40 candidates the first request took 3.2 to 8.1 s p95 with the small reranker (cold document vectors) and about 35 s with the 571 MB cross-encoder; the same request repeated took about 60 to 120 ms. Precomputing the late-interaction vectors for a library would put Accurate inside the budget; it is not implemented. **Accurate therefore stays off by default**, as the plan requires.
- **Chunk size is not decided.** The relevant chunks are re-derived per size, so metrics are not comparable between sizes; embedding time per chunk was also measured under different loads (332 ms at 200 tokens on the quietest run). The default stays at the conservative 400 until the Phase 0 harness can sweep fixed relevance judgements.


**Vector search.** Exact cosine over float32 vectors, vectorised with numpy when installed, is the default
and stays exact. `scripts/benchmark_vectors.py` (512 dimensions, same host) measured:

| Vectors | exact numpy p50 / p95 (ms) | pure Python p50 / p95 (ms) | sqlite-vec p50 / p95 (ms) | sqlite-vec recall@10 |
| --- | --- | --- | --- | --- |
| 10,000 | 64.3 / 95 | 337.2 / 374.3 | 28.5 / 34.7 | 0.977 |
| 25,000 | 229.5 / 282.7 | not run | 59.7 / 71.4 | 0.983 |
| 50,000 | 405.7 / 524 | not run | 119.1 / 140.8 | 0.993 |
| 100,000 | 728.3 / 928.7 | not run | 220.4 / 235.5 | 0.973 |
| 200,000 | 1460.7 / 1900.2 | not run | 337.8 / 403.8 | 0.97 |

The hybrid budget is 300 ms p95 for the whole search: the keyword pass (about 20 ms), the query embedding (about 75 ms warm) and fusion also have to fit, which is why the exact search stops at 10,000 vectors (95 ms p95). With sqlite-vec the budget holds to roughly 60,000 chunks on this host (141 ms at 50,000, 236 ms at 100,000, 404 ms at 200,000). A library of 10,000 documents is likely to exceed that, so the LanceDB question stays open until the `scale` fixture is measured with real chunks.

So brute force stays the default up to 10,000 chunks (`vector_store.BRUTE_FORCE_LIMIT`); a library with
more uses the optional `sqlite-vec` backend (`pip install byauthorx[sqlite-vec]`, MIT or Apache-2.0)
automatically, or whenever the library's embedding setting names `"vector_backend": "sqlite-vec"`. It keeps the
float32 table as the source of truth, adds a 1-bit-per-dimension index, takes the 8 times `limit` nearest by
Hamming distance and re-scores them with the exact float cosine, so the returned scores are exact and only
the candidate set is approximate. `auto` is the default setting; `sqlite-blob` forces the exact one.
LanceDB remains an escape hatch behind the same `VectorStore` interface, to be adopted only if the
`scale` fixture misses its latency target with sqlite-vec; it is not implemented or bundled.

**Chunks.** Chunker v3 merges short paragraphs up to a token target (estimated with
`context_assembly.estimate_tokens`), never crosses a heading, and splits an overlong paragraph at sentence
boundaries with 160 characters of overlap. The default target is 400 tokens, chosen conservatively, not
tuned (the embedder reads at most 1,024 tokens of a chunk, the catalogue's `max_tokens`): set `byax library --action chunking-configure --input '{"target_tokens": 600}'` (50 to 4000) and
refresh to try another size; `scripts/benchmark_semantic.py --chunk-tokens 200 400 800` sweeps sizes. The
stored `text` is the source text as extracted, joined by blank lines. A chunk's `heading` carries the heading
path (full-text index, embeddings), `locator` the first passage's page, ordinal and block, `start`/`end`
offsets within its section, `ordinal_end` when paragraphs were merged, and `parent`, the section's identity.
`byax library-read … --chunk-id … --section` returns the whole section of a hit. Changing the chunker
makes older indexes stale (searchable until the next refresh) and their vectors incompatible. A citation to
a chunk made by an older chunker is checked against the document's current chunks by its quote instead of
failing (`relocated: true`).

## Contextual retrieval notes

An opt-in job can write a note of 50 to 100 tokens per chunk that situates it in its document, which helps
a passage be found by a related question. `byax library … --action contextualize-preview --input
'{"provider": "my-ollama"}'` lists what a run sends (one passage with its file name and heading path per
request, never the whole document); `contextualize` runs it as a job that you can cancel and resume.
A local model (a loopback Ollama endpoint) needs no extra consent; a remote one needs the separate
`library_embeddings` consent for that provider (or `--allow-remote` for the call), and a `local_only`
library never uses a remote model. The prompt frames the passage as untrusted data. The note is kept in its
own table and full-text column and included in the chunk's embedding; it is **never part of the source
text**: it is not returned as a passage, not shown in the extracted-text preview and not available to quote
or cite, and a note that repeats eight consecutive words of its passage is dropped. Embeddings made without
the note become stale, so rebuild them afterwards. Notes survive a refresh of unchanged text and are removed
by `contextualize-clear`. Late chunking, the zero-LLM alternative, is not implemented.

## PDF quality path (Docling)

pypdf is the default PDF reader and PyMuPDF is not used (AGPL). For scanned-quality layouts the optional
extra `byauthorx[docling]` (Docling, MIT) can read PDFs with a layout model: reading order and headings,
the page and a bounding box (`bbox`, top-left origin in PDF points) for every passage, and the PDF's own
text annotations as separate linked notes (`note` type, page and box). Choose it per library with
`byax library … --action pdf-engine --input '{"engine": "docling"}'` after installing the extra and the layout
model (`byax models --action download --name docling-layout-heron --confirm`); the command refuses
otherwise. Docling runs only inside the isolated parser subprocess, with `HF_HUB_OFFLINE=1`, on the
installed layout model: it never fetches one. OCR and table-structure models are off, so scanned pages
without a text layer still yield nothing. Switching engines marks PDFs stale and their vectors
incompatible. Docling pulls PyTorch (several GB); it is never part of the installers.

Recorded run, 16 October 2026 (Docling 2.140 in a scratch environment, the layout model installed with `byax models`, offline): a generated one-page PDF with two headings and two paragraphs came back in reading order, with the heading path and a top-left bounding box on every passage, in 10 to 26 seconds on 4 shared CPU cores including model load. The automated tests use a stand-in `docling` package, so a newer Docling release can change the item API without failing them; run `scripts/check_docling_pdf.py some.pdf` after upgrading Docling.

## Storage, bounds and remaining phases

Library definitions contain paths/policies but no credentials, beside provider
configuration. SQLite indexes live in the machine-local cache, never the library
folder or document package. WAL permits reads during indexing; timestamped per-file
leases prevent duplicate concurrent parsing. Missing, unsupported, no-text and
failed files have individual status. Cancellation retains usable completed files.

Defaults: 32 MiB/file, two million extracted characters/file, 100,000 chunks/library,
30 seconds/parser, one parser at a time per indexer, 100 search results and 100,000
read characters. Chunks merge short paragraphs up to 400 estimated tokens and never
cross a heading (chunking version 3; see [chunks](#on-device-semantic-search)). Index schema 3
stores each chunk's heading path and fingerprint, a stemmed full-text index
with heading, text and contextual-note columns, a trigram index for substrings, a vocabulary table for
suggestions and the notes table. Older index files are marked stale on first open, stay searchable (results
flagged `stale`) and are rebuilt by the next refresh; their old vectors are dropped. LibraryIndex accepts
explicit `limits` overrides; worker parsing is isolated and archive import limits
also apply. No performance numbers are claimed without reference-hardware measurement.

The first-release keyword library, citation and context/review paths are implemented.
Configured embeddings and hybrid ranking (chosen automatically when a library has vectors)
are available, with on-device embedding and reranking as described above. Optional downloadable
weights (and the local entailment model, cascade stage 2) remain future capabilities. Outside DOI lookup,
the Zotero local API, RIS exchange, extra CSL styles, excerpts and the optional judge are described
above. PDFs require an extractable
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
