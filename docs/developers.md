# For developers and automation

ByAuthorX is open source (MIT). The desktop app, the command line, the MCP server and the optional local REST service all call one document engine, so anything you can do in the window you can script. You never need any of this to write.

Related: [CLI reference](cli-reference.md), [architecture](architecture.md), [development guide](development.md), [interface parity](interface-parity.md), [CONTRIBUTING](../CONTRIBUTING.md).

## Run from source

Python 3.11 or newer is required for development; frozen distributions include Python.

```bash
uv sync --extra dev
uv run byauthorx
```

Alternatively:

```bash
python -m venv .venv
# Ubuntu:
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/byauthorx
# Windows PowerShell:
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\byauthorx
```

On Ubuntu, install the native Qt dependencies documented in [compatibility](compatibility.md). For headless PNG/PDF rendering set `QT_QPA_PLATFORM=offscreen`. Ordinary CLI editing and inspection need no desktop session.

## CLI

```bash
byax capabilities --json
byax view report.byax --as md --source --json
byax statistics report.byax --format txt --json
byax new --out report.byax --title "Quarterly report" --text "An opening paragraph." --json
byax inspect report.byax --outline --json
byax inspect report.byax --json
byax find report.byax --query "opening" --json
byax context report.byax --limit 10000 --json
byax apply report.byax --input edits.json --dry-run --json
byax apply report.byax --input edits.json --json
byax check report.byax --suite structure --suite requirements --json
byax history report.byax --action checkpoint --name "Before review" --json
byax export report.byax --out report.pdf --json
byax unpack report.byax --out report-source --json
byax pack report-source --out restored.byax --json
```

Create `edits.json` using the identities and revision returned by inspection:

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

`byax schema --topic operations --json` exposes the current canonical operations. `--input -` reads UTF-8 JSON from stdin. Machine stdout contains only JSON results; progress/diagnostics belong to stderr. Exit codes: 0 completed under selected policy, 1 execution/dependency, 2 invalid input, 3 conflict, 4 failed required validation, 5 strict incomplete coverage/review, 6 cancelled, 7 pending/uncertain. Advisory findings do not automatically fail writing.

An open GUI holds a document lock. CLI writes fail explicitly while that document is open; the disk file is never silently modified behind unsaved GUI changes. Apply batches are atomic, stale revisions fail, and identical committed request IDs do not apply twice.

## Providers and transcription

Supported adapter kinds are `openai`, `anthropic`, `gemini`, `ollama`, `elevenlabs`, and `whisper.cpp`. Text review is available through OpenAI/Anthropic/Gemini/Ollama; transcription through OpenAI/ElevenLabs/whisper.cpp. Recognition and cleanup models are independently selected. Model names must be selected explicitly; API-compatible endpoints are not assumed to be universally verified.

See [provider setup](providers.md) for configuration, transmission policies and offline transcription. Contract tests mock HTTP responses; no billable calls were required to run this suite.

## MCP and optional local REST

Launch `byax-mcp` as a stdio MCP server, or `byax mcp`. For example:

```json
{"mcpServers":{"byauthorx":{"command":"byax-mcp","args":[]}}}
```

The server exposes the common CLI dispatcher as tools with input schemas. Inspect before editing and preserve revision preconditions. Do not expose the stdio process to untrusted clients.

REST is disabled unless explicitly started. Set a randomly generated `BYAX_SERVICE_TOKEN` with at least 24 characters and run:

```bash
byax serve --root /absolute/authorized/document/folder
```

POST `{"command":"inspect","arguments":{"path":"/absolute/authorized/document/folder/report.byax"}}` to `http://127.0.0.1:8765/v1/execute` with `Authorization: Bearer <token>`. The service rejects non-loopback binding, browser origins, unauthenticated calls, and paths outside the configured roots. Provider configuration and job state live in a reserved `.byax` folder inside the first root; clients cannot select another configuration folder or write into that one, and `providers --action configure` is available only through the local CLI or desktop.

## Tests, fixtures and builds

```bash
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv run python scripts/generate_fixtures.py --out artifacts/fixtures
QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark.py --out artifacts/benchmark.json
uv run python scripts/build.py --bundle-only
```

For Windows PowerShell set `$env:QT_QPA_PLATFORM = "offscreen"` before headless tests. The build script freezes the native GUI, CLI and MCP executables into `dist/ByAuthorX`. Run `python scripts/build_deb.py` **on Ubuntu 24.04** to build the Ubuntu package, or Inno Setup `iscc packaging/windows.iss` on Windows for the Windows 11 installer. CI contains both build jobs, and pushing a `vX.Y.Z` tag publishes the installers as a GitHub Release (see [packaging](packaging.md#cutting-a-release)). A Linux binary built on a newer glibc host is not a validated Ubuntu 24.04 binary.

Build and installation details: [packaging](packaging.md). Architecture: [architecture](architecture.md). Complete requirements: [product specification](../PRODUCT-SPECIFICATION.md).

## Creative resources

The [creative library](../assets/creative-library/README.md) contains 54 original designs for future app and marketing use: illustrations, icons, loading loops, completion animations, decorations, and backgrounds. It includes light/dark variants, editable Vixl masters, SVG/PNG exports, animation formats, and a local browsable catalog. These are optional assets; Vixl is an authoring tool rather than an app dependency.

The additional [logo companion library](../assets/logo-companion-library/README.md) adds 24 designs and 44 variants based on the supplied green pen loop, irregular red ink mark, and canonical brand palette, including a compact ink-mark loader and 180 ms confirmation animations. The app bakes a few of them into `src/byax/resources/brand/ui` for waiting states and empty panels; see [brand](brand.md).
