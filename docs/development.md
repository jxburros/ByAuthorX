# Development guide

## Setup

Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
uv run byauthorx          # desktop app
uv run byax capabilities --json
```

Optional extras (their tests skip when the package is missing; CI installs the first two):

```bash
uv sync --extra dev --extra local-models --extra sqlite-vec   # on-device models runtime; sqlite-vec backend
# --extra docling pulls PyTorch (several GB); the Docling tests use a fake package, so you rarely need it
BYAX_REAL_MODELS_DIR=/path/to/models uv run pytest tests/test_local_inference.py -k real   # real models, maintainers
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
The whole suite runs in one process (about 8 minutes, 11 with branch coverage), so two rules keep it healthy.
A hung test fails after 300 seconds with a stack dump (`pytest-timeout`, thread method, set in `pyproject.toml`;
pass `--timeout=0` to debug under a debugger). `tests/conftest.py` fails any test that changes the runner's
resource limits (the extension and parser workers apply `RLIMIT_CPU`/`RLIMIT_AS`/`RLIMIT_FSIZE` to their own
process, so call them in a subprocess or replace `apply_parser_limits`) and any test module that leaves a child
process or non-daemon thread behind. Tests of flows that need the optional `local-models` runtime stub
`local_models.runtime_status` instead of requiring the extra, because an unstubbed "install it" prompt opens a
modal dialog that never returns offscreen.
Live providers are exercised only by the opt-in `scripts/provider_smoke.py` with `--allow-remote`.
Offscreen results do not establish physical-desktop behavior; record such evidence in the
[acceptance matrix](acceptance-matrix.md).

The HTML review copy is also tested in a real browser (`tests/test_review_html.py`: no network request,
phone width, keyboard and touch flows, a downloaded comment file imported back). It needs the `dev` extra
(Playwright for Python) and a Chromium build; the tests skip themselves otherwise. Point
`PLAYWRIGHT_BROWSERS_PATH` at an installed Playwright browser folder, or set `BYAX_TEST_CHROMIUM` to a
Chromium executable. ByAuthorX never downloads a browser.

Fixtures and benchmarks:

```bash
uv run python scripts/generate_fixtures.py --out artifacts/fixtures
QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark.py --out artifacts/benchmark.json
uv run python scripts/grammar_benchmark.py --write                 # built-in grammar precision/recall (docs/benchmarks)
uv run python scripts/grammar_benchmark.py --write --languagetool <folder>   # also a local LanguageTool (Java 17+)
uv run python scripts/extract_strings.py --write                   # refresh the interface-string catalogue
BYAX_LANGUAGETOOL_URL=http://127.0.0.1:8081 uv run python -m pytest -m languagetool   # tests against a real local server
QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark_compile.py --out artifacts/compile.json  # compile the 100k project
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
scripts/eval_quotes.py                    quote verification rates and the near-match threshold sweep
scripts/eval_chunk_sweep.py               chunk-size sweep; scripts/eval_fusion.py sweeps the fusion's keyword weight
scripts/build_scale_index.py              builds and keeps the 10,000-document index; scripts/profile_search.py times and profiles it
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
uv run python scripts/eval_retrieval.py --sample --all-sample-queries --configs hybrid-qwen3 hybrid-qwen3-colbert --label qwen3  # needs the local models
```

`--label` names the output file and `--scale-work`/`--scale-queries` reuse a kept 10,000-document index
instead of rebuilding it. `quote_match.NEAR_THRESHOLD` (94) comes from `scripts/eval_quotes.py`; the test
`test_near_threshold_was_chosen_from_the_published_sweep` ties it to `context-0.4-quotes.json`. Published
results and their limits: [Context engine benchmarks](benchmarks/context-engine.md).

- **Labels are locators, not chunk IDs**: `{path, page}` for PDFs, `{path, start, end}` (characters of the
  file's text) otherwise. `.html` files are not indexed by the library parser (`libraries.SUPPORTED`), so the harness
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
The grammar and language-pack tests never need Java or the network: they use a fake local server
(`tests/fixtures/grammar/fake_languagetool.py`) and a local file server. `tests/conftest.py` points
`BYAX_GRAMMAR_DIR` and `BYAX_LANGUAGES_DIR` at temporary folders for every test.

## CI gates (run these before pushing)

```bash
uv run ruff format --check .      # single quotes, line length 110
uv run ruff check src tests
uv run mypy                        # engine, storage, CLI, MCP, REST, transport policy, citations
uv run python -m pytest -q --cov=byax --cov-branch --cov-fail-under=70
uv run python scripts/build.py --bundle-only
```

CI runs the full suite, static checks, a locked runtime dependency audit and branch coverage on Ubuntu/Python 3.12. Focused
storage, sync, extension, transports, jobs, security and GUI regressions also run on Windows/Python 3.12
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
- **Sample project** – after any change to the document model or operations that alters what the bundled sample
  contains (schema bump, default styles, check rules), run `uv run python scripts/build_sample_project.py` and commit
  the regenerated `src/byax/resources/samples/sample-project.byax`; `tests/test_onboarding.py` fails when it is stale.
- **Platform code** – read `sys.platform` when called (see `platform_support.py`) so tests can monkeypatch it, and
  cover each branch without a second machine. macOS-only behaviour that cannot be unit tested goes on the
  pending-verification list in the acceptance matrix.
- **Release** – follow [packaging](packaging.md#cutting-a-release); `tests/test_release_metadata.py` enforces matching
  versions and a changelog entry.

## Conventions

Match surrounding code (single quotes, type hints on public functions). Never claim a verification the evidence
does not support. Untrusted text (document content, model output) is data and must not select executables, enable
retrieval or touch secrets. No network except explicit, user-selected provider calls.

## Bibliography fixtures and live checks

Bibliography tests never reach the network. `tests/bibliography_fixtures.py` provides `ReplayTransport`, which
answers `httpx` requests from `tests/fixtures/bibliography/*.json` and fails on a request that has no recording,
and `RecordingTransport`. To re-record after a registry changes its answers (network access needed):

```bash
uv run python tests/bibliography_fixtures.py record
```

This runs the real lookup client against Crossref, DataCite, OpenAlex and the Wayback Machine for the DOIs and
links in `SCENARIOS` and `LINKS`, and rewrites one file per request (Crossref `reference` lists and some bulky
OpenAlex and DataCite fields are dropped to keep them small; the file says so). Review the diff before committing.
Tests that make real requests carry the `live` marker, which `addopts` deselects; run them with
`uv run python -m pytest -m live tests/test_bibliography_live.py`. The Zotero fixtures in `tests/fixtures/zotero`
are hand-written to the Zotero Web API v3 format. `tests/conftest.py` points `BYAX_BIBLIOGRAPHY_DIR` (lookup
cache, Retraction Watch index, installed CSL styles, contact address) at a temporary folder for every test.

## Word review fixtures

`tests/fixtures/docx-review` holds the round-trip fixture set used by `tests/test_docx_review.py`. It
contains `sent.byax`, the review copy, two files edited the way Word saves them (emulated in
`tests/docx_word.py`), and two files written by LibreOffice. Regenerate them all together, because
`sent.byax` gets new identities on every run:

```bash
uv run python scripts/docx_review_fixtures.py --out tests/fixtures/docx-review --libreoffice
```

`--libreoffice` needs `soffice` and the system Python's UNO bridge (`python3-uno`). It re-saves the
Word fixture through LibreOffice and runs `scripts/libreoffice_review_edit.py`, which edits the review
copy in LibreOffice with change recording on. A file saved by Microsoft Word itself would be the next
fixture to add. Add it beside these with its own test, and record the Word version.

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
