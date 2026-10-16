# Development guide

## Setup

Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
uv run byauthorx          # desktop app
uv run byax capabilities --json
```

Ubuntu needs the Qt libraries listed in [troubleshooting](troubleshooting.md#linux-app-will-not-start-xcb--egl--libxkbcommon-errors).

## Layout

```
src/byax/        engine, storage, exchange, layout, checks, providers, CLI/MCP/REST, gui*
src/byax/resources/   bundled fonts, brand art, tokens
tests/           pytest suite (+ fixtures/, including historical schema-v1 packages)
scripts/         build, benchmark, fixture and asset-authoring tools
packaging/       Inno Setup script, .desktop/MIME files, PyInstaller entry points
docs/            documentation (index: docs/README.md)
design/, assets/ editable brand and illustration sources (Vixl); not bundled
```

The rule that shapes everything: **all transports call the same engine**. GUI, CLI, MCP and REST must
mutate documents only through `engine.apply` operations, never by editing structures directly. The
engine, storage, checks, providers, jobs and CLI have no UI dependency. See [architecture](architecture.md).

## Tests

```bash
QT_QPA_PLATFORM=offscreen uv run pytest -q
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_engine.py -q
```

(PowerShell: `$env:QT_QPA_PLATFORM = "offscreen"`.) The suite mocks all HTTP; no billable calls are made.
Live providers are exercised only by the opt-in `scripts/provider_smoke.py` with `--allow-remote`.
Offscreen results do not establish physical-desktop behavior; record such evidence in the
[acceptance matrix](acceptance-matrix.md).

Fixtures and benchmarks:

```bash
uv run python scripts/generate_fixtures.py --out artifacts/fixtures
QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark.py --out artifacts/benchmark.json
```

## CI gates (run these before pushing)

```bash
uv run ruff format --check .      # single quotes, line length 110
uv run ruff check src tests
uv run mypy                        # engine, storage, CLI, MCP, REST, transport policy, citations
uv run python -m pytest -q --cov=byax --cov-branch --cov-fail-under=70
uv run python scripts/build.py --bundle-only
```

CI runs the full suite, static checks, a locked runtime dependency audit and branch coverage on Ubuntu/Python 3.12. Focused
storage, transports, jobs, security and GUI regressions also run on Windows/Python 3.12
and Ubuntu/Python 3.11. Wheels and source distributions are built every code run, with
CLI document roundtrip and MCP smoke checks against an isolated wheel installation.
Native builds run for packaging/dependency/workflow changes, manual runs, weekly checks
and every release; see [packaging](packaging.md). Dependency caches and superseded-PR
cancellation and prompt Qt window teardown reduce repeated work; documentation/art-only changes skip app CI.

## Common changes

- **New canonical operation** – implement in `engine`, add it to the operation schema surfaced by
  `byax schema --topic operations`, handle anchor remapping in `anchors`, and add engine + CLI tests.
- **New CLI command** – add it to `COMMANDS`, `ARGUMENTS` (the schema also rejects unknown transport arguments and
  scopes paths) and the required-arguments table in `cli.py`; it becomes available over MCP/REST automatically.
  Update the [CLI reference](cli-reference.md).
- **Schema change** – add a transition in `migration.TRANSITIONS`, a fixture from the previous release, and the
  tests described in [architecture](architecture.md#format-versions-and-migration).
- **Export format change** – report every loss; update [compatibility](compatibility.md) and the acceptance matrix.
- **Screens/branding** – rerun `scripts/capture_screens.py` and review the PNGs in `docs/images`.
- **Release** – follow [packaging](packaging.md#cutting-a-release); `tests/test_release_metadata.py` enforces matching
  versions and a changelog entry.

## Conventions

Match surrounding code (single quotes, type hints on public functions). Never claim a verification the evidence
does not support. Untrusted text (document content, model output) is data and must not select executables, enable
retrieval or touch secrets. No network except explicit, user-selected provider calls.

## EPUBCheck

`byax.epub.validate` (the built-in structural check) runs in the normal test suite. EPUBCheck, the
authoritative validator, needs Java and is optional:

```bash
uv run python scripts/epubcheck_fixtures.py --out artifacts/epub                      # built-in check only
uv run python scripts/epubcheck_fixtures.py --out artifacts/epub \
    --epubcheck /path/to/epubcheck-5.3.0/epubcheck.jar --warnings-fail                 # plus EPUBCheck
```

The `epubcheck` CI job runs the second form on manual and weekly runs only. It downloads EPUBCheck 5.3.0
from its release page and verifies the SHA-256 recorded in `.github/workflows/ci.yml` before use. The
script exports every fixture from `scripts/generate_fixtures.py` plus `epub_rich` (one document that
exercises lists, tables, spans, footnotes, images with and without alt text, a cover, marks, language
changes and embedded fonts) and exits non-zero on any error.
