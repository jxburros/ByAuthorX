# CLI reference

`byax` is the headless interface to the same engine the desktop uses. `byax-mcp` (or `byax mcp`)
exposes the same dispatcher over stdio MCP, and `byax serve` over an optional loopback REST
service. All three call `byax.cli.execute`, so a command behaves identically everywhere, except
that `providers --action configure` is local-only (see [providers](providers.md)).

```bash
byax <command> [options]
byax --help
byax <command> --help
byax schema --topic commands --json      # machine-readable command schemas
byax schema --topic operations --json    # canonical edit operations
byax capabilities --json                 # supported features and provider adapters
```

## Conventions

- Add `--json` for machine output. Stdout then carries only the JSON result; progress and
  diagnostics go to stderr.
- `--input FILE` reads UTF-8 JSON (at most 16 MiB); `--input -` reads stdin.
- Mutations follow *inspect → (dry-run) → apply*: read the current `revision` and stable IDs with
  `inspect`, send an envelope with `expected_revision`, and optionally a `request_id`. Batches are
  atomic, stale revisions fail with a conflict, and a repeated committed `request_id` is not
  applied twice.
- An open desktop window holds the document lock. CLI writes fail with `locked` while it is open.
- Text offsets are zero-based Unicode scalar indices with end-exclusive ranges.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Completed under the selected policy |
| 1 | Execution or dependency failure |
| 2 | Invalid input |
| 3 | Conflict (stale revision, lock, request-id reuse) |
| 4 | Failed required validation |
| 5 | Strict incomplete coverage or review |
| 6 | Cancelled |
| 7 | Pending or uncertain |

Advisory findings do not fail writing.

## Commands

The table below mirrors `COMMANDS` in `src/byax/cli.py`; run `byax <command> --help` for each
command's flags.

| Command | Purpose |
| --- | --- |
| `library` | Manage attached local libraries and indexing jobs |
| `library-search` | Search attached library passages (read only) |
| `library-read` | Read an indexed attached library passage (read only) |
| `source` | Search, import, export and suggest citation sources |
| `cite` | Insert a citation referencing an existing source. Without `--kind` it infers `quotation` or `paraphrase` and reports `citation_kind` |
| `statistics` | Count authored, publication and selected text |
| `readability` | Per-chapter readability indices, style statistics and pacing; optionally export a report |
| `collection` | Inspect, export or apply reusable document settings |
| `dependencies` | Inspect, explicitly refresh or embed linked assets |
| `view` | Read a format projection and its located losses without changing the master |
| `version` | Engine and schema versions |
| `capabilities` | Discover supported features and provider adapters |
| `schema` | Discover command or canonical operation schemas |
| `new` | Create a native document |
| `inspect` | Inspect selected semantic content without opening an editor |
| `find` | Locate text and stable IDs |
| `context` | Build a bounded, exact context package |
| `apply` | Apply one atomic canonical operation envelope |
| `transact` | Alias for atomic apply; transactions never span invocations |
| `fill` | Fill document fields using a JSON values object |
| `history` | List revisions, checkpoint, branch, or restore |
| `diff` | Compare semantic content against a saved revision |
| `check` | Run deterministic validation suites |
| `review` | Inspect, propose, accept, or reject changes and comments |
| `import` | Import an exchange document into a native package |
| `save-as` | Save an existing document under another path |
| `convert` | Convert a document with an explicit loss report |
| `export` | Export a document with optional validation gates |
| `render` | Render pages through the desktop layout engine |
| `pack` | Pack a readable native source folder |
| `unpack` | Unpack a portable native package |
| `providers` | Inspect, configure, test, or call explicitly selected providers. `list` includes `locality` (`local` or `remote`) |
| `transcribe` | Transcribe an explicitly selected audio file |
| `jobs` | Inspect persistent job status, results, cancellation, and recovery |
| `batch` | Convert inputs with individual outcomes and bounded workers |
| `redact` | Create and verify a sanitized copy |
| `reproduction` | Record or check reproducible rendering inputs |
| `proof` | Create contact sheets, focused crops, and semantic diagnostic overlays |
| `table` | Import or export CSV table values while preserving native identities and formatting |
| `template-batch` | Fill independent copies of a template from CSV field rows |
| `fonts` | Inspect font identity, substitution, embedding permission and glyph fallbacks; embed permitted fonts |
| `project` | List, reorder, split, merge and annotate manuscript binder items |
| `sync` | Check synced-folder safety, list conflicted copies, and merge one as a reviewable proposal |
| `compile` | Compile a manuscript project into a book (PDF, EPUB, Word or Markdown) with preflight |
`serve` and `mcp` start the REST and stdio transports and are not engine commands.

## Common recipes

Create, inspect, edit and export:

```bash
byax new --out report.byax --title "Quarterly report" --text "An opening paragraph." --json
byax inspect report.byax --outline --json          # IDs and the current revision
byax apply report.byax --input edits.json --dry-run --json
byax apply report.byax --input edits.json --json
byax check report.byax --suite structure --suite requirements --json
byax export report.byax --out report.pdf --json
```

Fiction checks and readability (deterministic, advisory, local; see the
[user guide](user-guide.md#fiction-checks-and-readability)):

```bash
byax check story.byax --family fiction --json                 # the fiction suite, default sensitivity
byax check story.byax --suite fiction --profile Editing --json  # rules and thresholds from a profile
byax readability story.byax --json                            # indices, style and pacing per chapter
byax readability story.byax --target h_chapter2 --json        # one chapter (top-level heading ID)
byax readability story.byax --out report.docx --json          # also write the report as a document
```

`check --family` takes rule families (`spelling`, `grammar`, `editorial`, `style`, `factual`,
`accessibility`, `print`, `fiction`) and adds their suites to any `--suite` values. Fiction rules,
sensitivity and thresholds are tuned through the profile's `fiction` setting, which a section
profile can override; `byax schema --topic operations` documents `set_profile` and
`set_section_profile`. `readability --out` accepts any export format (`.byax`, `.docx`, `.odt`,
`.md`, `.html`, `.rtf`, `.txt`, `.pdf`); the result carries the exporter's `losses`, `fidelity` and
`review` fields, and `--overwrite` is required to replace a file.

Look at the document as another format without changing the master:

```bash
byax view report.byax --as md --source --json
byax statistics report.byax --format txt --json
```

History and review:

```bash
byax history report.byax --action checkpoint --name "Before review" --json
byax diff report.byax --from rev_… --json
byax review report.byax --action list --json
```

Source folders (readable, diff-friendly; history and replay records omitted by default):

```bash
byax unpack report.byax --out report-source --json
byax pack report-source --out restored.byax --json
```

Conversion always returns a compatibility/loss report:

```bash
byax import draft.docx --out draft.byax --json
byax convert draft.byax --out draft.odt --json
```

Manuscript binder (`--action` defaults to `list`; read actions are `list`, `tree`, `reconcile`,
`blocks` and `compile`; mutating actions accept `--expect-revision`, `--request-id` and `--dry-run`):

```bash
byax project novel.byax --action init --json
byax project novel.byax --action tree --json
byax project novel.byax --action move --target item_… --before item_… --json   # or --after / --parent
byax project novel.byax --action split --target item_… --at p_… --offset 120 --text "Night" --json
byax project novel.byax --action merge --target item_… --next item_… --json
byax project novel.byax --action add --kind note --text "Character sheets" --json
byax project novel.byax --action set-status --target item_… --status draft --json
byax project novel.byax --action set-target --target item_… --words 2500 --json   # --clear removes it
byax project novel.byax --action set-meta --target item_… --input meta.json --json
byax project novel.byax --action remove --target item_… --json   # --delete-content also deletes the text
```

`move` without a placement moves the item to the end. `set-target` without `--target` sets the
document target. `set-meta` input keys: `kind`, `status`, `synopsis`, `target_words`, `label`,
`include_in_compile` (`null` clears) and `heading` (rebind an orphaned item). Each mutating action is
one `project_*` canonical operation, also usable in `apply` envelopes
(`byax schema --topic operations`).

EPUB is an export and an import format. `byax export draft.byax --out draft.epub --json` writes a reflowable
EPUB 3 and returns the located-loss report plus an `epub` section (identifier, chapters, navigation, fonts,
accessibility metadata). Chapter splits, extra CSS, the cover and other options come from the `epub` section of
a saved export profile (`--profile NAME`); see the [user guide](user-guide.md#exporting-an-epub). `--dry-run`
returns the same losses without writing. `byax import book.epub --out book.byax` and `convert` read EPUB like HTML.

## Compiling a book

`byax compile PATH` turns a project into a finished book with a compile preset (see the
[user guide](user-guide.md#compiling-a-book)). The master is never changed. Flags: `--preset`
(`novel`, `nonfiction`, `manuscript` or a preset saved in the document), `--preset-file` (a settings
collection holding a compile preset), `--target` (`pdf`, `epub`, `docx`, `md`; default from `--out`, then
the preset), `--trim` (`5x8`, `5.25x8`, `5.5x8.5`, `6x9`, `a5`, …), `--include ITEM` (repeat; the exact
binder items to compile), `--exclude ITEM` (repeat; also excludes descendants), `--input` (a partial
compile profile merged over the preset), `--out`, `--overwrite`, `--strict`, `--preflight-only`,
`--dry-run`, `--action`, `--name`, `--expect-revision`, `--request-id`.

```bash
byax compile novel.byax --preset novel --target pdf --out book.pdf --json
byax compile novel.byax --preset novel --out book.epub                    # target from the extension
byax compile novel.byax --preset manuscript --out submission.docx         # standard manuscript format
byax compile novel.byax --preset novel --trim 6x9 --preflight-only --json # paginate and check, write nothing
byax compile novel.byax --preset nonfiction --dry-run --json              # compiled structure and losses only
byax compile novel.byax --action presets --json                           # bundled and saved presets
byax compile novel.byax --action show --preset novel --input design.json  # the effective profile
byax compile novel.byax --action save-preset --name "Trade 6x9" --trim 6x9 # canonical set_settings
byax compile novel.byax --action export-preset --preset "Trade 6x9" --out trade.json
byax compile novel.byax --preset-file trade.json --out book.pdf
```

The result carries `compile` (preset, target, included and excluded items, generated matter, the
compiled document's hash and, for PDF, `layout_signature`, `page_count`, `page_starts` and the trim and
margins; for Word and Markdown, `roundtrip`), `losses` (the target exporter's located losses plus every
difference found by re-importing Word or Markdown output), `findings` and `preflight` (preflight
findings in the shared findings model) and `outputs`. Preflight errors (nothing to compile) refuse the
compile with exit code 4 before anything is written; `--strict` turns warnings and losses into exit
code 5. `save-preset` is one canonical `set_settings` operation (`compile_presets`), so
`--expect-revision`, `--request-id` and `--dry-run` behave as for `apply`. Over REST, `--out` and
`--preset-file` are scoped to the authorized roots.

## Sync-folder safety

`byax sync PATH --action …` works on a document or a folder. It never uses the network and never
deletes anything. Flags: `--action`, `--copy-path`, `--name`, `--author`, `--expect-revision`,
`--dry-run`.

```bash
byax sync Chapter.byax --action status --json            # provider, confidence, evidence, risks
byax sync Chapter.byax --action conflicts --json         # copies with identity and relation
byax sync Chapter.byax --action merge-conflict --copy-path "Chapter (Sam's conflicted copy 2026-10-14).byax" --json
byax sync Chapter.byax --action archive-conflict --copy-path "Chapter (Sam's conflicted copy 2026-10-14).byax"
```

- `status` reports the detected provider (`dropbox`, `icloud`, `onedrive`, `google_drive`, `box`,
  `nextcloud`, `owncloud`, `syncthing`, `pcloud`), its `confidence` and `evidence`, and `risks`
  (`severity` `warning` or `info`): placeholders and empty or incomplete files, leftover temporary
  files, backup and other-application lock files, lock or recovery folders inside a synced folder,
  large packages or histories, and a document open in another session. Given a folder it summarizes
  every document with conflicted copies.
- `conflicts` lists complete packages with the same `document_id` (`copies`), unfinished candidates
  (`pending`) and look-alikes of other documents (`skipped`). Each copy has a `relation`: `diverged`,
  `ahead`, `behind`, `identical` or `unrelated_history`.
- `merge-conflict` imports the copy as a branch (named from its revision, so repeating it reuses it)
  and registers a pending merge proposal. Different sections merge cleanly; the same section yields
  `conflicts` to resolve with the `resolve_conflict` operation (`byax apply`). Accept with
  `byax review --action accept`. Copies with nothing new or no shared history are refused unchanged.
  `--dry-run` reports the plan without saving.
- `archive-conflict` moves a copy whose latest revision is already part of the document into a
  `ByAuthorX conflicted copies` folder beside it. It refuses before the merge is accepted and never
  overwrites an archived file.

MCP exposes the same command as `byax_sync`; REST scopes both `path` and `copy_path` to its roots.

## Library search syntax

`library-search`, `library --action search` and `context --library-query` accept:

| Syntax | Meaning |
| --- | --- |
| `copper moon` | Either word (OR); passages with more matches rank higher |
| `"copper moon"` | The exact phrase (quoted words are never dropped or spell-expanded) |
| `NEAR(copper moon, 5)` | Both words within 5 words of each other (default 10, maximum 100) |
| `-jupiter`, `NOT jupiter` | Exclude passages containing the word or `-"a phrase"` |
| `cop*` | Words starting with `cop` |
| `heading:Rosings`, `text:moon` | Match only the heading path or only the body text |
| `title:journal`, `path:notes/*.md` | Keep results whose file name/top heading or library path matches (`*` wildcards allowed) |

Plain words are stemmed (`moons` finds `moon`). Common words (*the*, *of*) are ignored unless
nothing else remains. Headings weigh four times body text. Upper-case `AND`/`OR` are ignored
as connectors. If the stemmed pass returns fewer than `--limit` results, a literal substring
pass follows (`match: "trigram"`, three characters or more). Unknown `field:` prefixes are
searched as words and reported in `query.warnings`. Unmatched words return `suggestions`
("did you mean"); `--expand` searches them too.

```bash
byax library-search draft.byax --library-id lib_… --query 'heading:Mars "two moons" -Venus' --json
byax library-search draft.byax --library-id lib_… --query moons --method keyword --json
byax library-search draft.byax --library-id lib_… --query Phobsos --expand --json
byax context draft.byax --library-query moons --library-method auto --library-pinned doc_… --json
byax library draft.byax --action verify-absence --query '"Olympus Mons" Ganymede' --json
```

`--method auto|keyword|hybrid` (default `auto`) chooses hybrid search when a library's
embeddings are `ready` or `partial`; results report `method`, `embedding_state`, `coverage` and
any `notice`. `verify-absence` takes terms from `--query` (quote phrases) or
`--input` `{"terms": [...]}`, optionally limited by `--library-id`, and reports every literal
match in the attached libraries and the document. See the
[library guide](library-and-citations-guide.md#search-syntax-and-methods).

## Edit envelopes

```json
{
  "schema_version": 1,
  "request_id": "edit-001",
  "expected_revision": "COPY_CURRENT_REVISION_HERE",
  "operations": [{
    "type": "replace_text",
    "target": "COPY_PARAGRAPH_ID_HERE",
    "match": "opening",
    "expected_text": "opening",
    "replacement": "revised",
    "occurrence": 1
  }],
  "required_suites": ["structure", "requirements"]
}
```

`byax schema --topic operations --json` lists every canonical operation and its fields. Check
suites are `publication`, `structure`, `accessibility`, `editorial`, `requirements`, `print` and
`spelling`. Untrusted envelopes cannot remove protections or disable tracking; those need local
desktop confirmation (see [architecture](architecture.md#mutation-and-persistence)).

## Environment variables

| Variable | Used by | Purpose |
| --- | --- | --- |
| `BYAX_SERVICE_TOKEN` | `byax serve` | Bearer token (at least 24 random characters). Rename with `--token-env`. |
| `BYAX_LIBREOFFICE` | legacy `.doc` import | Absolute path of the LibreOffice executable to use for conversion. Unset means `.doc` is not converted. |
| `BYAX_REDUCED_MOTION` | desktop | `1` disables all animation. |
| `QT_QPA_PLATFORM` | rendering, tests | Set `offscreen` for headless PNG/PDF rendering and tests. |

Provider keys are referenced as `env:NAME` or stored in the OS credential store; see
[providers](providers.md). Live provider smoke tests use `BYAX_SMOKE_*` variables documented in
`scripts/provider_smoke.py`.

## REST service

```bash
export BYAX_SERVICE_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
byax serve --root /absolute/authorized/folder [--port 8765]
```

POST `{"command": "inspect", "arguments": {"path": "…"}}` to `http://127.0.0.1:8765/v1/execute`
with `Authorization: Bearer <token>`. The service refuses non-loopback binding, browser origins,
unauthenticated calls and paths outside its roots.

## MCP

```json
{"mcpServers": {"byauthorx": {"command": "byax-mcp", "args": []}}}
```

Tools mirror the commands above with input schemas. Inspect before editing and keep revision
preconditions. Do not expose the stdio process to untrusted clients.
