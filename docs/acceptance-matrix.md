# Specification acceptance matrix

This matrix maps the complete product specification to the implementation and its remaining verification. **This implementation is not a completed acceptance of the entire specification.** A feature represented in the document schema is not necessarily rendered or operable in the GUI. Passing serialization is not publication validation.

Statuses: **verified subset** means a named automated test exercises the stated behavior; **partial** means implemented behavior has material limits or incomplete scenario coverage; **missing / unverified** means no implementation or no relevant end-to-end evidence. Physical Windows/Linux and credentialed provider acceptance remain unverified.

## Feature coverage

| Spec | Required capability | Implementation / evidence | Status and remaining work |
| --- | --- | --- | --- |
| 1 | Everyday writing; shared interfaces; authorship; local deterministic functions; advanced discovery | `gui.py`, `engine.py`, `cli.py`, `mcp.py`, `providers.py` | Partial: architecture and native editor provided; the complete Word/Pages scenario set has not passed |
| 2.1–2.2 | Native Windows/Linux install, dialogs, clipboard, drag/drop, fonts, mic, printing, associations, accessibility/scaling | Qt GUI; `packaging/`; `scripts/build.py`, `scripts/build_deb.py`; compatibility matrix | Partial: installer recipes exist; physical installed Windows 11 / Ubuntu 24.04 acceptance unverified |
| 2.3 | One engine for GUI, library, CLI, MCP, optional disabled REST; headless operations | `engine.py`, `cli.py`, `mcp.py`, `service.py` | Partial: interfaces share engine; full semantic parity needs exhaustive command/UI inventory |
| 3.1 | Typing, selections, Unicode/IME, clipboard, shortcuts, undo/redo, drag/drop | `gui_editor.py`, `gui.py` | Partial: native widget paths; IME, complex-script selection and OS clipboard unverified |
| 3.1 | Scoped/case/whole-word/regex find; replacement preview; navigation by all semantic targets | `engine.find`, GUI find/outline | Verified subset: scoped regex replacement previews expand groups, outline/section navigation tests pass. Complete bookmark/comment/finding/figure/page navigation remains to be exercised on both platforms |
| 3.1 | Counts/reading time distinguishing authored/selected output; continuous/paginated/Markdown views with stable source | Inspection and GUI views | Partial: full selection/publication statistics and edit-free view switching need acceptance |
| 3.2 | Inline/paragraph formatting, tab stops, indents; nested lists, restart/continuation, split/join | Canonical marks/attrs; `layout.py`, `gui_editor.py` | Partial: Qt supports a subset; stable list restart/continuation through split/join not fully verified |
| 3.2 | Named inheritable character/paragraph styles, overrides, clear formatting, affected IDs | Model styles; engine style/format operations; layout inheritance | Partial: named style inheritance, common styles and clear-format controls exist; full independent character-style inheritance and override reporting need acceptance |
| 3.3 | Atomic save/save-as, autosave/recovery, recent files, external change detection | `storage.py`, GUI recovery/session handling; interrupted-replacement test | Verified subset: failed replacement preserves prior package. GUI crash/recovery and external-change scenarios unverified |
| 3.3 | Authoritative GUI state versus CLI/service writes | `DocumentLock`; independent-process acceptance test | Verified subset: independent writer fails while session lock held; full GUI/CLI interaction still needs installed desktop test |
| 4.1 | Declared Markdown syntax/table extension; whitespace/source preservation; localized visual changes; explicit rich conversion | `exchange.py` Markdown source metadata and import/export losses | Partial: rich-to-source limitations require review; arbitrary Markdown syntax/localized editing equivalence not complete |
| 4.2 | Semantic rich blocks, tables, marks, images/captions, notes/bookmarks/references; stable IDs and reflow | `model.py`, canonical operations, shared layout | Verified subset: semantic blocks, tables, marks, bookmarks and footnote associations survive tested edits/reflow. Caption keeps, all cross-reference updates and deleted-object relationships need broader acceptance |
| 4.2 | Conversion preview/loss reports and preserve original | Exchange adapters; exchange round-trip acceptance tests | Verified subset: original bytes and selected text survive supported simple round-trips; complex loss location completeness unverified |
| 5.1 | Portable ZIP master with complete model/history, manifests/hashes/schema versions, case handling, no secrets | `storage.py`; representative fixture round-trip tests | Verified subset: generated documents round-trip; unknown-extension, corrupt-archive, secret rejection cases covered separately in core tests |
| 5.2 | Embedded images, explicit external links, missing-link findings, fonts/substitution/permissions, sources/audio retention, limits | Assets/model/storage, layout findings, provider/transcription metadata | Verified subset: licensed DejaVu font bundles register locally; absent scalar glyphs produce located findings; primary font binaries are hashed. External asset refresh/embed workflows, arbitrary font embedding permissions and glyph-fallback reproducibility remain partial |
| 5.3 | Deterministic readable unpack/pack, omitted history/secrets/caches, validation/migration/unknown preservation | `storage.pack/unpack`; edited-document source round-trip test | Verified subset: native pack/unpack omits history by default; manual source edits regenerate hashes and a deterministic revision while validating asset identity. Historical schema migrations remain unsupported; unsupported versions fail explicitly |
| 6.1 | Physical geometry/units, paper/orientation, sections/columns/breaks; widow/orphan/keep rules | `layout.py`; Qt physical-point document | Verified subset: section geometry/equal columns preserve flow and render consistent pages. Unequal widths remain unsupported; full-width/section transitions start separate page segments; headings keep with following content (measured at the first rendered line) and fitting paragraphs keep together; `widow_control`/`orphan_control` line counts move paragraphs (with directly preceding keep-with-next blocks) across page and column boundaries, and a widow rule on a paragraph longer than a page produces a located `widow_unsatisfiable` finding without dropping text. Moving individual lines of long paragraphs and crowded-page fixtures across fonts/paper sizes remain incomplete |
| 6.2 | Masters/backgrounds/letterhead; first/odd/even headers, numbering formats/restarts | Shared page painter and layout metadata | Partial: repeated backgrounds/layers and first/odd/even header/footer text are implemented. Decimal, Roman and alphabetic page-number formats and section restarts are tested; complete reusable master geometry remains partial |
| 6.2 | Updated TOC, semantic cross-references/bookmarks, real page footnotes with overflow/continuation | TOC/layout model support | Verified subset: TOC resolves pages and footnotes follow moved references after reflow. Uniform note reservation can add whitespace; oversized notes continue on dedicated pages; exact numbering and crowded-page cases need acceptance |
| 6.3 | Anchored/absolute objects, five wrap modes, sizing/aspect, caption keep, alt/reading order | Image attrs, shared Qt floats/overlays and resolved layout | Partial: inline, square left/right floats, above/below and behind/front image overlays now render. Arbitrary movement/anchor policies, caption keep relations and overlap-sensitive reading order need acceptance |
| 6.4 | Same preview/GUI/render/PDF layout; contact sheets/overlays/crops; native page-range print; reproduction | `layout.py`, GUI print, publication records; actual PNG/PDF acceptance | Verified subset: shared physical display pages produce matching PNG/PDF page counts; pixel reproduction comparison has declared tolerances and reference hashes. Proof contact sheets, physical-point crops and diagnostic overlays are tested; physical native printing remains unverified |
| 7 | Editable table insertion/deletion/reorder, cell formatting/width/padding/borders/shading/merges, repeat headers, row splitting | Table model/operations; Qt tables; press-kit cell update test | Verified subset: semantic cells/header rows/merged cells render, and merged-table GUI refresh is a no-op. Explicit row/column reorder preserves stable cell identities and validates spans; configurable row-split/continued-cell policies remain incomplete |
| 7 | CSV interchange preserving formatting, accessibility headers, movable semantic sections with anchors | Canonical table/section operations and adapters | Verified subset: CSV import/export retains existing identities/attributes, supports explicit atomic growth and reports per-row template outcomes; deleted table cells orphan review/bookmarks. Semantic section movement preserves content and anchors as a unit in regression tests |
| 8.1 | Named/grouped/locked/reordered/visible layers; optional inspector; stable IDs | Layer model/operations and GUI inspector | Partial: structural layer management and repeated foreground/background painter exist; full grouping/wrapping/anchoring behavior and complete layer inspector acceptance remain |
| 8.2 | Decoration hide versus semantic exclusion versus overlays versus true redaction; reading order/protection | Output filtering; `publication.py`; serialized-redaction acceptance test | Verified subset: conservative text-only sanitized copy removes recoverable categories. Full layer/output distinction and all protected kinds incomplete |
| 9.1 | TXT/MD/DOC/DOCX/RTF/ODT/HTML/BYAX import and listed exports with editable semantics, provenance, located losses | `exchange.py`, configured LibreOffice DOC adapter | Verified subset: DOCX/ODT named styles, marks and links round-trip; RTF rich marks/tables/Unicode import; ODT footnote association; opaque originals retained. Review structures and complex numbering/layout have reported losses; encrypted input fails explicitly; local DOC integration remains unverified |
| 9.2 | Saved export profiles, staged batch publication, selected-output checks; never equate export success with validation | `publication.export_clean`, CLI batch/export | Partial: explicit policies and gating; complete profile UI and every stage/overwrite failure path need tests |
| 10 | Templates include content/styles/settings/masters/fields/voice/references/checks; typed validation; JSON/CSV fill; settings collections | `table_io.py`, `profiles.py`, `gui_profiles.py`, engine fill operations and CLI | Verified subset: typed required fields gate publication, filling materializes repeated tokens while preserving marks, CSV generates independent templates, and profile collections round-trip without credentials. Full template/master/terminology/source/style collection management remains partial |
| 11.1 | Undo/redo, checkpoints/branches/restoration, semantic diffs, bounded deltas/snapshots/compaction and provenance | Engine history functions; GUI undo/history | Verified subset: deltas, periodic snapshots and explicit compaction are exercised by test_delta_history_restores_every_revision_and_compaction_preserves_checkpoint; full cross-interface branch/proposal scenario remains |
| 11.2 | Anchored threaded comments/orphans; separately attributed insert/delete/format tracking; grouped accept/reject | Review model and canonical operations | Verified subset: bookmarks follow split/join, deletion produces unresolved/orphaned states, and rejecting tracked formatting restores formatting. Nested threaded replies and deletion orphan propagation are tested; complete range remapping and grouped tracked-change workflows remain partial |
| 11.3 | Exact selective proposals with preconditions/dependencies/checks, semantic comparison | Engine proposals/diff; GUI review dock | Verified subset: validated model proposals carry exact edits and required policies; stale targets and table preconditions fail. Accepting selected dependent edits within one proposal and full semantic diff presentation remain incomplete |
| 12.1 | Spelling/terminology/grammar/editorial/internal/source consistency/requirements/structure/layout/print/accessibility, all scopes | `checks.py`, providers/factual review | Verified subset: deterministic spelling/requirements/structure/accessibility plus fresh resolved geometry checks; explicit unmeasurable grammar/factual coverage without reviewer calls. Comprehensive grammar/internal-consistency/layout diagnosis remains partial |
| 12.2 | Independent sensitivity/freedom/voice controls, conservative/editorial presets, intentional exceptions/protection and section profiles | Profiles/settings and provider review contract | Verified subset: independent sensitivity/freedom/voice controls, conservative/editorial presets, profile collection import/export and native dialog persistence; intentional exceptions stay visible and cannot conceal errors. Section-specific profile application remains incomplete |
| 12.3 | Located versioned findings, statuses/evidence/repairs; separate undoable repair; failures survive pagination | Check findings and engine; findings-limit acceptance test | Verified subset: limit zero still reports failing totals and unavailable checks remain incomplete. All statuses/transitions need acceptance |
| 12.4 | Concrete coverage health; dependency-sensitive invalidation and incremental scopes | Check coverage and revision/source metadata | Partial: no complete incremental dependency graph or safely expanded recheck implementation |
| 13.1–13.2 | Explicit exclusive/supplementary/open sources; no instruction-triggered retrieval; evidence hashes/excerpts; five factual outcomes/invalidation | Reference model, provider authorized context contract | Verified subset: exclusive source filtering, invented-evidence rejection and required not-established outcomes without evidence are tested. Complete claim interpretation, all five outcomes and source-dependency invalidation need broader acceptance |
| 14.1 | Explicit text/proposal/audio/streaming/language/limit capabilities; independent recognition/editorial models and defaults | `providers.py` six provider adapters | Partial: six explicit provider adapters have protocol/authorization/capability tests; all currently declare streaming=False. Live account/model/language support remains unverified |
| 14.2 | Endpoint/key reference/timeout/retry/limits/cost, connection test, OS secrets, inspect outbound data, validated proposals/provenance | Provider configuration and keyring; GUI/provider commands | Partial: no invented price claims; live credential storage, outbound inspection UX and full retry/cost policy need acceptance |
| 15.1 | Microphone/audio import same pipeline; start/pause/resume/stop/select device/progress/cancel/keyboard/destination | `transcription.py`, Qt recording, GUI dictation and jobs | Partial: code present; physical microphone, decoder matrix and bookmark insertion acceptance unverified |
| 15.2 | Direct, cleanup, document-aware modes; original comparison, separate recognizer/editor, bounded local process | Transcription/provider pipeline | Partial: pipeline modes exist; live mode quality/voice/terminology acceptance unverified |
| 15.3 | Provisional display/history grouping, revision-safe cleanup/anchors, application policy, failure retention/recovery | Transcription proposal/precondition handling, persistent jobs | Partial: final-segment insertion and revision-safe cleanup are tested; provisional text staging exists, but no streaming recognizer adapter supplies live updates. Full interrupted microphone capture recovery remains incomplete |
| 15.4 | Authorized context options, separate raw/audio retention, segment links; speech never commands | Transcription settings/provider input contract | Partial: full context-selection UI, retention storage accounting and timestamp navigation need acceptance |
| 16.1–16.2 | UTF-8 JSON/human CLI, strict stdout/stderr/exit behavior, all command families, no invisible prompts; MCP/library parity | `cli.py`, `mcp.py`, `service.py` | Partial: broad command coverage; each spec family has varied depth, and PowerShell/install behavior unverified |
| 16.3–16.4 | Bounded selective inspection/context, total/scope/truncation/continuation, requirements/provenance/source versions/no secrets | Engine inspect/context and commands | Partial: compact contexts; complete inclusion/omission, continuation and required-information semantics need acceptance |
| 16.5 | Stable/quoted/typed selectors; candidates on ambiguity/stale; scalar/grapheme boundaries; split/join remapping | Engine selectors; ambiguity/stale/grapheme acceptance tests | Verified subset: ambiguity, stale writes and grapheme boundaries; bookmark split/join/deletion and table-cell orphan states are tested. Complete passage-comment/proposal anchor mapping remains unverified |
| 16.6–16.7 | Atomic canonical operations, schema errors/preconditions, dry-run exact effects/checks; separate measured protection types; no gate tampering | Engine apply; dry-run and batch failure tests | Verified subset: original remains unchanged on failure/dry-run and table attributes survive. Layout effects/all preservation kinds incomplete |
| 16.8 | Durable idempotent retries, ID-content mismatch, revision checks, transaction owners and explicit merge conflict resolution | Requests/history, session locks; retry/reopen/process conflict tests | Verified subset: same request does not apply twice; complete branch merge and retention/recovery policy incomplete |
| 16.9–16.10 | Versioned result/error envelope, all exit states, illustrated inspect/edit/check/preview/export | CLI/core error contracts | Partial: broad workflow implemented; exact schema equivalence and all failure state propagation need tests |
| 17 | Persistent jobs/progress/cancel/remote uncertainty/recovery without resubmission; bounded staged batches; reproduction evidence/tolerance | `jobs.py`, CLI batch, publication reproduction | Verified subset: persistent jobs/remote uncertainty; primary font file hashes, immutable PNG reference hashes and tolerance-based pixel comparison detect drift/mismatch. Glyph-specific fallback hashes and remote cancellation completion remain limited |
| 18 | Explicit clean inclusion policies; sanitized copy removes all recoverable copies; actual serialization inspection | `publication.py`; adversarial metadata/review/history/transcript/extension redaction test | Verified subset: rebuild excludes review/history/sources/metadata/fields/layers/extension data; original preserved. `preserve: [styles, layout, images]` opts in to keeping named styles and formatting attributes free of redacted terms, scrubbed layout header/footer text, and images whose bytes (UTF-8/UTF-16/Latin-1) contain no redacted term, with scrubbed alt text. Serialized JSON values and preserved asset members are inspected before publishing. Text drawn in image pixels is not inspected (review stays required); marks, tracked deletions and selective preservation of review/history remain unsupported |
| 19 | Modular engine/layout/render/adapters/checks/providers/jobs; GUI not sole edit authority; incremental work outside UI | Module boundaries and canonical operations | Partial: shared architecture; full candidate copying/layout rebuilds and synchronous expensive GUI work may miss performance targets |
| 20 | Vixl canonical/schema/atomic/identity/history/inspection principles while supporting true word processing | Model/engine/storage/check contracts | Partial: foundational patterns retained; this does not establish complete required page-layout behavior |
| 21 | Offline local functions; explicit network; bounded untrusted archives/paths/processes; scoped disabled services; no secrets in outputs | Storage/adapters/providers/service restrictions and tests | Verified subset: security acceptance tests cover hostile ZIP paths/duplicates/expansion, XML entities, deep JSON, pre-read folder budgets, inert HTML, decoded image/raster bounds, page-range bounds and export locks. A code audit added regressions for ODT repeated-space/table-repeat expansion, nesting depth and malformed numbers; HTML bare attributes, unbounded spans and omitted end tags; RTF font-table backtracking; quadratic mark validation; DOCX control characters; and a REST service boundary in which provider configuration and job state live in a reserved, client-unwritable `.byax` folder. Instruction-like content, artifact credential-leak inspection and malformed external converters remain |
| 22.1 | Keyboard/screen reader/high contrast/scaling/dictation/semantic navigation and honest accessibility coverage | Native Qt controls, accessibility checks | Missing / unverified: physical assistive-technology and keyboard-only full-workflow audit not completed |
| 22.2 | Measured startup, typing p95, reflow p95, inspection, idle/cancellation; 100/500-page fixtures; explicit limits/progress | `scripts/generate_fixtures.py`, `scripts/benchmark.py` | Partial: reproducible fixtures and honest local timing harness; no target-hardware GUI performance claim |
| 24–25 | Explicit framework/OS/packaging/provider/license/extension decisions; retain `.byax` | `pyproject.toml`, `LICENSE`, compatibility matrix | Documented: implementation choices recorded; `.byax` collision absence is not a universal uniqueness guarantee |

## End-to-end scenario ledger (Section 23)

| Scenario | Current evidence | Remaining acceptance |
| --- | --- | --- |
| 23.1 Letter/resume/report/newsletter and recovery | Deterministic editable fixture save/reopen; PNG/PDF letter; failed atomic replacement preserves archive | GUI create/edit/print on both OSes; complete footnote/column crowded-page cases, anchored wraps and crash recovery |
| 23.2 Two-page music press kit | Original generated cover; semantic biography/release table/field; canonical date edit preserves attributes | Columns/footnotes/float overlays and geometry checks added; full press-kit GUI scenario, letterhead/numbering fidelity and biography reflow across difficult anchored objects remain |
| 23.3 Import/conversion | Simple multilingual TXT/MD/HTML/DOCX/ODT/RTF export/reimport; preserved original bytes | Representative historical DOC through installed adapter; tables/images/styles/review/non-Latin fidelity and every located loss |
| 23.4 History/review | Canonical history/proposal/comment interfaces; core tests | Full checkpoint/branch/GUI+CLI/diff/restore/selective proposal/tracked change/section anchor sequence |
| 23.5 Checks/evidence | Limit-zero failures retained; incomplete editorial coverage disclosed | Native profile presets/exceptions and exclusive evidence validation now tested; full semantic evidence quality, changed-source invalidation and cross-interface strict publication scenario remain |
| 23.6 Transcription | Contract tests and local processing paths | Real mic direct/cleaned/document-aware session, manual edits, late cleanup conflict, names, recovery and retained segments |
| 23.7 Agents/concurrency | Dry run, atomic failure, ambiguous candidates, stale revision, durable retry and subprocess lock conflict | Full protected table/context/check/commit/diff/page preview workflow and instruction-like material adversarial audit |
| 23.8 Publication/reproduction | Actual sanitized ZIP members inspected; original untouched; render dependency inventory | Broader selected-inclusion/complex-layout publication cases and glyph-specific fallback font reproducibility; primary font hashes and actual pixel-reference comparisons now tested |
| 23.9 Platform/performance | Local benchmark harness records environment, fixture hashes, raw samples and measured pages | Installed Windows/Ubuntu desktop, assistive technologies and GUI typing/reflow/startup measurements on qualified hardware |

## Running and interpreting evidence

On 13 October 2026, `tests/test_acceptance.py` completed with **22 passed** on the Linux development container in 0.69 seconds. This is cross-module automated evidence, not a claim that the full Section 23 GUI/platform scenarios passed.

```bash
uv sync --extra dev
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_acceptance.py
uv run python scripts/generate_fixtures.py --out artifacts/fixtures
QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark.py --out artifacts/benchmark.json
```

Fixture source is deterministic and MIT licensed. PNG cover art is generated locally from basic geometry. Explicit page breaks stabilize workload size; they do not validate automatic break selection. Benchmark `model_edit` is a canonical transaction timing, **not** keystroke-to-paint feedback. Full reflow timing is **not** incremental affected-region GUI reflow. Unmeasured targets must remain unclaimed even when proxy metrics are fast.

## Recorded local benchmark — 13 October 2026

Measured at `2026-10-13T16:43:35.228087+00:00` on `Linux-6.18.44-x86_64-with-glibc2.41`, AMD EPYC 9V45 96-Core Processor, 5 exposed logical CPUs, Python 3.12.14, Qt 6.12.0, offscreen platform. RAM, SSD and physical target-machine identity are not verified; this is a shared development container with uncontrolled concurrent load, not platform qualification. No providers were configured or called. Each metric has 20 sequential samples; p95 uses nearest rank.

| Fixture | Actual pages | Blocks | Text characters | Model edit p95 | Full physical reflow p95 | Selected inspection p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mixed_100 | 100 | 379 | 87862 | 29.38 ms | 608.38 ms | 0.031 ms |
| long_500 | 500 | 1499 | 861392 | 107.82 ms | 549.96 ms | 0.038 ms |

Full physical reflow includes shared resolved display pages, section layout and font coverage checks after a paragraph insertion. Both full-layout proxy measurements exceed 500 ms in this run; incremental affected-layout latency remains unmeasured. These results do not establish GUI startup, visible typing latency, idle behavior, cancellation or remote-model performance. Qt emitted OpenType diagnostics for a fixture script; font fallback and complex-script visual quality still need platform review. An unrenderable scalar produces a located missing-glyph finding (security regression test).

The completed full automated suite on this development container reported **218 passed in 3.48 seconds** after integration of section/table operations, threaded review, numbering, proof output and REST rendering and cross-platform CSV soft breaks. This includes 22 cross-module acceptance tests and 22 additional hostile-input/GUI-state security regressions; it does not mean all Section 23 product scenarios passed. All seven fixtures were regenerated with the current model/font defaults and deterministic identities.

<details>
<summary>Recorded environment, fixture hashes, definitions, and raw timing samples</summary>

```json
{
  "schema_version": 1,
  "measured_at_utc": "2026-10-13T16:43:35.228087+00:00",
  "environment": {
    "platform": "Linux-6.18.44-x86_64-with-glibc2.41",
    "machine": "x86_64",
    "processor": "AMD EPYC 9V45 96-Core Processor",
    "logical_cpus": 5,
    "python": "3.12.14",
    "engine": "0.1.0",
    "qt": "6.12.0",
    "qt_platform": "offscreen",
    "hardware_qualification": "Unqualified execution host; CPU/SSD target hardware not verified."
  },
  "provider_configuration": "None; local deterministic operations only.",
  "definitions": {
    "p95": "Nearest-rank percentile of wall-clock sample durations.",
    "model_edit": "Canonical candidate transaction against original loaded fixture; includes validation/history; excludes GUI input and paint.",
    "full_reflow": "After a canonical paragraph insertion, construct shared QTextDocument and resolve all physical display pages; includes full layout, excludes paint and the edit itself.",
    "inspection": "Bounded semantic inspection against an already loaded document.",
    "fixture_load": "Fixture construction excluded from every timed operation."
  },
  "not_measured": [
    "Cold GUI startup to usable window",
    "Keystroke-to-visible-feedback latency",
    "Incremental affected-region reflow",
    "Idle CPU activity",
    "Cancellation acknowledgement",
    "Physical Windows 11 / Ubuntu 24.04 desktop behavior",
    "Remote provider latency"
  ],
  "fixtures": {
    "mixed_100": {
      "semantic_sha256": "4c003481739cd3fdc1fadff4fa25368b82142c2607efece7407aa67a5db4c9b3",
      "blocks": 379,
      "text_characters": 87862,
      "requested_pages": 100,
      "measured_pages": 100,
      "layout_findings_count": 0,
      "pagination": "Explicit page breaks",
      "metrics": {
        "model_edit": {
          "samples": 20,
          "median_ms": 19.07750149985077,
          "p95_ms": 29.37829700022121,
          "min_ms": 18.654770000011922,
          "max_ms": 29.578148999917175,
          "raw_ms": [
            19.976571000370313,
            19.042613999772584,
            18.8796289999118,
            18.79905699979645,
            18.879187000038655,
            28.396918999987975,
            26.724303000264626,
            29.578148999917175,
            29.37829700022121,
            27.264442999694438,
            25.084436999804893,
            18.79735400007121,
            18.720408999797655,
            18.721860999903583,
            18.654770000011922,
            19.112388999928953,
            19.222944999910396,
            18.89728500009369,
            18.845967999823188,
            26.240029000291543
          ]
        },
        "full_reflow": {
          "samples": 20,
          "median_ms": 523.0921739998848,
          "p95_ms": 608.3783520002726,
          "min_ms": 512.0212199999514,
          "max_ms": 611.2988560003032,
          "raw_ms": [
            548.9963029999672,
            512.0212199999514,
            611.2988560003032,
            522.3624380000729,
            608.3783520002726,
            522.3208610000256,
            541.8240199996944,
            553.402473999995,
            534.6497460000137,
            567.2954920000848,
            513.9550609997059,
            519.186985000033,
            525.3776740000831,
            517.8085080001438,
            526.1801229999037,
            523.8219099996968,
            522.3503839997647,
            514.3391670003439,
            517.9262239998934,
            516.4681629998995
          ]
        },
        "inspection": {
          "samples": 20,
          "median_ms": 0.016830500044306973,
          "p95_ms": 0.03098700017289957,
          "min_ms": 0.016575000245211413,
          "max_ms": 0.0638960000287625,
          "raw_ms": [
            0.0638960000287625,
            0.020420999589987332,
            0.01862799990703934,
            0.017286000002059154,
            0.016806000076030614,
            0.017255999864573823,
            0.016814999980852008,
            0.016846000107761938,
            0.01676499960012734,
            0.016725000023143366,
            0.016635000065434724,
            0.016665000202920055,
            0.016715000128897373,
            0.016575000245211413,
            0.03098700017289957,
            0.01701600012893323,
            0.01665499985392671,
            0.01665499985392671,
            0.01687600024524727,
            0.016975000107777305
          ]
        }
      },
      "target_comparison": {
        "typing_50ms": "not_measured: model transaction excludes GUI feedback",
        "reflow_500ms": "full reflow proxy only; affected-region GUI feedback unmeasured",
        "inspection_500ms": true
      }
    },
    "long_500": {
      "semantic_sha256": "d5e58786dad2929b08a724591d9e08c6b435a6e2847012cae84d124625e49b7c",
      "blocks": 1499,
      "text_characters": 861392,
      "requested_pages": 500,
      "measured_pages": 500,
      "layout_findings_count": 0,
      "pagination": "Explicit page breaks",
      "metrics": {
        "model_edit": {
          "samples": 20,
          "median_ms": 96.48987499986106,
          "p95_ms": 107.81782399999429,
          "min_ms": 94.71789099961825,
          "max_ms": 107.85395799985054,
          "raw_ms": [
            97.07794599989938,
            107.10326700018413,
            95.39984899993215,
            95.02990100008901,
            94.71789099961825,
            95.16305200031638,
            107.81782399999429,
            98.53695399988283,
            97.21840800011705,
            96.42128699988461,
            96.54177799984609,
            107.85395799985054,
            97.42557000026864,
            95.72342499996012,
            95.65679500019542,
            95.7788289997552,
            107.50962900010563,
            96.43797199987603,
            95.3168139999434,
            100.82759400029317
          ]
        },
        "full_reflow": {
          "samples": 20,
          "median_ms": 520.3456470001129,
          "p95_ms": 549.959243000103,
          "min_ms": 498.0706179999288,
          "max_ms": 560.6256800001574,
          "raw_ms": [
            498.0706179999288,
            524.3130240000937,
            504.22690799996417,
            499.91405999981,
            509.3968789997234,
            560.6256800001574,
            549.959243000103,
            545.5421909996403,
            520.4846560000078,
            546.9709859999057,
            520.2066380002179,
            501.4592089996768,
            523.9482649999445,
            510.96506200019576,
            515.664384000047,
            515.5281279999144,
            517.9058999997324,
            522.4928620000355,
            526.1389759998565,
            526.3790779999908
          ]
        },
        "inspection": {
          "samples": 20,
          "median_ms": 0.03561349990377494,
          "p95_ms": 0.037586999951599864,
          "min_ms": 0.035242999729234725,
          "max_ms": 0.1730009998937021,
          "raw_ms": [
            0.1730009998937021,
            0.037586999951599864,
            0.03612399996200111,
            0.03577400002541253,
            0.03547400001480128,
            0.03621499990913435,
            0.03609399982451578,
            0.035553999623516575,
            0.035803999708150513,
            0.03550399969753926,
            0.03557299987733131,
            0.03598400007831515,
            0.03533300014169072,
            0.03560400000424124,
            0.03554300019459333,
            0.03537400016284664,
            0.035242999729234725,
            0.036294999972597,
            0.03562299980330863,
            0.03542400008882396
          ]
        }
      },
      "target_comparison": {
        "typing_50ms": "not_measured: model transaction excludes GUI feedback",
        "reflow_500ms": "full reflow proxy only; affected-region GUI feedback unmeasured",
        "inspection_500ms": true
      }
    }
  }
}
```

</details>
