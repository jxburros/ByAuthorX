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

## Retrieval evaluation harness

Phase 0 of the [context engine plan](context-engine-plan.md): no retrieval change merges until the
harness shows its effect. Results are recorded in [benchmarks](benchmarks/README.md).

```
tests/fixtures/eval/<library>/corpus/     committed text, HTML and PDF fixtures (novel, series, humanities, webclips, papers)
tests/fixtures/eval/<library>/queries.jsonl   golden queries: {id, type, query, relevant_chunk_locators, expected_absent?}
tests/fixtures/eval/MANIFEST.json         source URL, licence, attribution and SHA-256 of every fixture file
tests/fixtures/eval/thresholds.json       CI floors and tolerances
src/byax/evaluation.py                    metrics, corpus builder, retrieval configurations
scripts/eval_retrieval.py                 writes docs/benchmarks/context-<version>-<config>.json
scripts/build_eval_corpus.py              rebuilds the committed corpora from Gutenberg and pinned Wikipedia revisions
scripts/fetch_eval_fixtures.py            downloads and verifies the 30 PLOS papers (3 are committed); never runs in tests
scripts/build_eval_queries.py             regenerates queries.jsonl (curated entries live in eval_queries_curated.py)
scripts/generate_fixtures.py --eval-scale N   synthetic N-document corpus for latency and index size (never committed)
```

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_retrieval_eval.py tests/test_context_fidelity.py -q
uv run python scripts/fetch_eval_fixtures.py                     # full papers set, cached outside the repo
uv run python scripts/eval_retrieval.py --configs keyword hybrid-fake   # full fixtures, writes docs/benchmarks/
uv run python scripts/eval_retrieval.py --sample --configs keyword hybrid-fake --update-thresholds
```

- **Labels are locators, not chunk IDs**: `{path, page}` for PDFs, `{path, start, end}` (characters of the
  file's text) otherwise. `.html` files are not indexed by the library parser at 0.3.0, so the harness
  indexes the text that `evaluation.html_text` extracts and labels offsets in that text.
- **Add a configuration** by subclassing `evaluation.RetrievalConfig` (`prepare` once per corpus, `search`
  per query) and calling `evaluation.register_config('name', factory)`, or pass a callable
  `(corpus, query, limit) -> hits` to `evaluate`. Hits are the dictionaries `LibraryIndex.search`
  returns; they only need `document_id`, `chunk_id`, `locator`, `paths` and `text`.
- **CI**: the keyword and fake-embedder configurations run in the normal suite on a deterministic subset
  (`evaluation.SAMPLES`, 24 queries per library) against `thresholds.json`; a metric more than the
  tolerance below its floor fails. Improvements never fail; raise a floor with `--update-thresholds` in the
  same change that earns it. Model-backed runs use `pytest -m eval_models` (deselected by default; see
  `tests/test_eval_models.py`) and the manual `eval` job in the CI workflow.
- **Fidelity tests** (`tests/test_context_fidelity.py`): jump fidelity, scope leaks, false absence, stability
  and concordance. A case the engine fails today is `xfail(strict=True)` and names the plan item that fixes
  it; when that item lands the case flips to a failure and the marker must be removed.
- Fixture data keeps its own licences ([third-party notices](third-party-notices.md)); it is not MIT.

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
