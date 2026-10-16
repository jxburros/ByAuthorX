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
