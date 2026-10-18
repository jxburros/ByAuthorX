# Changelog

All notable changes to ByAuthorX are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The single source of the version
number is `src/byax/__init__.py`; `tests/test_release_metadata.py` fails if any other copy
disagrees or if the version has no entry below. The release workflow publishes a version's
entry as its GitHub Release notes, so use absolute links and no in-page anchors in entries.

## [Unreleased]

### Added

- Visual guides: six flowcharts (proposals, what the assistant sees, sharing, manuscript to book, opting in to AI, synced-folder conflicts) and seven one-page guides (meet and use the context engine, the window, first minutes, checks, files and formats, privacy), authored in Vixl in the brand's look, with PNG, SVG and editable `.vixl` masters and a 12-page PDF booklet. Rebuild with `scripts/build_visual_guides.py`; see `docs/visual-guides/README.md`.

### Changed

- Documentation: the README is now a writer's page (pitch, proof points, get started, what you can do, status). Running from source, the command line, MCP and REST, tests and builds, and the creative resource libraries moved to the new `docs/developers.md`. The documentation index is split into using, automating, building and project history. The voice and messaging guide moved from `docs/brand.md` to `docs/voice.md`, and `brand.md` is now the design system. The product is described in one sentence across the README, package metadata, Linux desktop entry and `.deb`, and the product specification; the lead proof point reads "Proposals, not surprises". EPUB option names moved from the user guide to the CLI reference.
- Documentation: the README now opens with who ByAuthorX is for and why to choose it, with developer and automation material in its own section. `docs/brand.md` gains a voice and messaging guide (tagline hierarchy, tone, vocabulary, copy examples) and records where the product departs from the original brand guide on typography. The product specification file is renamed `PRODUCT-SPECIFICATION.md`, and JX Holdings, LLC is named as product owner.
- Documentation: the README now states the current version and summarizes the 0.4 manuscript, publishing and
  review features, and the documentation screenshots were regenerated so they show the Binder menu.

## [0.4.1] - 2026-10-17

### Fixed

- Finding the review package from a returned PDF's footer no longer fails when the review identifier contains
  `ff`, which the PDF text layer can store as a single ligature character. This broke the 0.4.0 release build.

## [0.4.0] - 2026-10-17

This release implements the [context engine plan](https://github.com/jxburros/ByAuthorX/blob/main/docs/context-engine-plan.md)
and the [competitiveness plan](https://github.com/jxburros/ByAuthorX/blob/main/docs/competitiveness-plan.md).
AI stays opt-in: nothing here sends your writing anywhere unless you configure a provider and choose to use it.

### Added

- **Manuscript projects.** A binder inside one `.byax` (`byax project`, `project_*` operations) with parts,
  chapters, scenes, notes and front/back matter; outline and corkboard views with drag and keyboard reordering,
  inline synopses and opt-in "Draft a synopsis" proposals; writing goals, sessions and streaks kept as local
  preferences (`byax goals`); named drafts with scene-level compare and "Bring changes from draft"
  (`byax drafts`).
- **Publishing.** Deterministic EPUB 3 export and import with navigation, footnotes, accessibility metadata,
  a cover and licence-respecting font embedding; a built-in EPUB structural check and an optional EPUBCheck CI
  job. A compile pipeline (`byax compile`, File → Compile…) with novel, non-fiction and standard-manuscript
  presets, preflight, reproducible PDF/EPUB/Word/Markdown output, and book design (trim sizes, gutter, mirrored
  margins, running heads, scene breaks, small caps, bleed and crop marks).
- **Interchange.** Word review round trip (File → Send for review (Word)…, Merge reviewed copy…,
  `byax review-package`, `byax merge-review`): editors' tracked changes, untracked edits, moves, formatting,
  comments and replies come back as attributed proposals and threads without overwriting your later edits.
  Beta-reader copies (`byax beta-copy`) as a self-contained offline HTML page with commenting, a PDF or a
  comment-only Word file, a read-only `--companion` viewer for phones, and `byax merge-comments`.
  Synced-folder safety (`byax sync`): provider detection, risk report, conflicted-copy discovery and merge as
  a reviewable proposal.
- **Writing quality.** An advisory fiction check suite (dialogue tags, filter words, echoes, repeated openings,
  rhythm, sensory balance, show/tell, adverb density, invented-name spelling); readability reports with a
  pacing graph (`byax readability`); offline grammar with built-in rules and an optional local LanguageTool
  tier (`byax grammar`); checksummed language packs (`byax languages`) and translation coverage tracking;
  a temporal codex and an advisory continuity suite.
- **Retrieval.** Library search syntax (phrases, `NEAR`, `-word`, `prefix*`, field filters) with "did you mean";
  `--method auto|keyword|hybrid`; global fusion across libraries; verified absence before "not found";
  normalized quote verification with the advisory `quote_near_match` status; token budgets and Ollama
  `num_ctx`; local/remote labels; an extracted-text preview; on-device semantic search (`byax models`, explicit
  verified downloads, local embedder) with optional rerankers, an optional sqlite-vec backend, contextual notes
  and an optional Docling PDF engine.
- **Citations you can check.** Citation chips in the AI tab, Anthropic native citations in Chat mode, the
  citation audit (`byax check --citations`), re-anchoring with `unanchored` instead of silently stale marks,
  version-pinned citations, an optional second-provider judge, a bibliography health panel
  (`byax bibliography`: DOIs, Crossref/DataCite/OpenAlex metadata, retractions, dead links, all behind
  consent), complete RIS exchange, extra CSL styles, Zotero import and live excerpts.
- **Manuscript as a corpus.** Find in project with saved searches, backlinks and unlinked mentions
  (`byax project-search`); a codex of characters, places, items and terms with aliases, traits and
  hide-from-AI (`byax codex`, `byax entity`); series files shared across books.
- **Assistant transparency.** A router (R0–R4) whose route and reason are always shown and can be overridden;
  the "What the AI saw" inspector with pin, exclude and re-run; a research log; opt-in chapter and book
  summaries; cache-ordered prompts with cache usage and cost estimates; `byax route`, `byax outline`,
  `byax verify-citations`.
- **On-device support check.** Optional HHEM-2.1-Open entailment (cascade stage 2): advisory
  supported/partial/unsupported underlines on AI replies and edits, and `byax check --citations --entailment`
  for paraphrase citations. Off by default; agreement with human labels is published in
  `docs/benchmarks/0.4-entailment.json`. An optional, consent-gated LLM reranker serves thematic multi-step
  searches (`byax route --inspect --llm-rerank`).
- **Provenance.** A per-span log of typed, pasted and AI text (View → Show text origin, `byax provenance`),
  APA/MLA/Chicago AI-use disclosures and an optional unsigned C2PA sidecar.
- **Extensions.** Versioned out-of-process extensions with scoped permissions for custom checks and export
  hooks (`byax extensions`), saved command macros (`byax macro`) and scoped REST tokens.
- **Reach.** A macOS (Apple silicon) app and `.dmg`, optional signing and notarization in the release workflow,
  a manual Help → Check for updates… (one request, never automatic), a sample project and a first-run tour.
- **Measurement.** A retrieval evaluation harness with public-domain and openly licensed fixtures, 648 golden
  queries, fidelity tests and published benchmarks
  ([context engine benchmarks](https://github.com/jxburros/ByAuthorX/blob/main/docs/benchmarks/context-engine.md)):
  retrieval, quote verification, false absence, routing and latency against the 0.2 baseline.

### Changed

- Document schema 3, migrated automatically from schema 2; the first save keeps the original as a
  `.schema2-` backup.
- Library indexes use schema 3 with structure-aware chunks; they rebuild on the next refresh.
- Keyword library search is about 20 times faster at 10,000 documents (p95 1.4 s to 71 ms) and re-ranks its
  best 100 candidates by unstemmed-word and word-pair overlap, so the literal word and the quoting passage
  outrank stems and scattered words. The quote near-match threshold is now 94, chosen from a published precision/recall sweep.
- A test that hangs now fails after 300 s (`pytest-timeout`, development only); tests of the on-device flows no
  longer need the `local-models` extra.
- An unknown cite ID is removed and marked unverified instead of rejecting the whole reply.
- Save refuses to overwrite a file changed on disk since it was opened; truncated, empty and placeholder
  packages fail with clear messages.
- Menu wording uses *draft* and *compare*; the Tools menu groups bibliography and document resources.

### Fixed

- The first attached library no longer takes the whole retrieval budget; partial vector sets are no longer
  treated as ready.
- Several pending proposals on one paragraph can be accepted in any order.
- The library parser no longer aborts when macOS refuses its memory limit.
- Citation claim spans no longer split at abbreviations such as "Mrs." or "e.g.".

## [0.3.0] - 2026-10-16

### Fixed

- Reject malformed command/tool names with stable input errors instead of internal failures.
- Reject REST pack sources containing service-owned state before reading their files.
- Handle deeply nested or malformed existing manifests when saving a replacement package.
- Bound Qt save-wait CPU usage with a timer-driven event loop. Keep Python 3.11 on
  PySide versions before 6.12 to avoid a native reference-count crash during save/close;
  tolerate older Qt accessibility APIs when selecting reduced motion.
- Decode local file URIs with the host platform's path rules, including Windows drive letters.

### Changed

- Qt tests destroy closed editor windows between cases so theme changes do not revisit
  accumulated widget trees.
- CI runs static checks and the full suite once on Linux, audits runtime dependencies, measures branch coverage, and
  validates installed wheels. Focused Windows and Python 3.11 regressions extend compatibility
  coverage. Cached dependencies, superseded PR cancellation, conditional native builds and
  installer-only artifacts reduce repeated work and storage. Releases and weekly checks still
  build both installers.

- The ribbon sits beside the logo in two rows matching its height, and each file view has
  its own ribbon showing only what that format supports; the ByAuthorX view carries every
  tool plus track changes, versions and protection. Related commands share dropdowns.
- New ribbon **Export** button: the ByAuthorX view asks for a format; other views export
  their own format, with the others on the button's arrow. File → Export as lists them too.
- Sidebar titles are the panel selectors; the separate dropdown is gone. A new **Layout**
  panel holds layers and page-layout commands and can open in either sidebar.
- The Inspector tabs are now **Proposed changes**, **Checks**, **AI** (Work and Chat) and
  **Configure**. Checks are marked active or inactive, custom checks are named and kept,
  and **Run all active checks** runs them together. API keys and information repositories
  moved to Configure; Layers moved to the Layout sidebar.
- Long menus nest related commands in submenus and scroll instead of splitting into columns.
- Six new Vixl-drawn icons: superscript, subscript, paragraph, cite, insert and text formatting.

## [0.2.0] - 2026-10-14

### Added

- Grapheme-safe merging of disjoint edits within one paragraph, with preserved
  compatible formatting and explicit conflicting scalar ranges.
- Explicit peer merge-base pins, retained through manual and automatic history
  compaction, with a report of retained pins. Missing ancestry fails explicitly.
- Optional flowing-prose and commit-scaling benchmarks, including approximately
  100/500/2,000 pages and up to 15,000 blocks, with raw samples and host details.
- Heading 4–6 and List in the desktop paragraph-style selector.
- Reference libraries, citations and a two-row editing workspace from the changes
  merged since 0.1.1; see the
  [library guide](https://github.com/jxburros/ByAuthorX/blob/main/docs/library-and-citations-guide.md).

### Fixed

- Repeated image decoding, repeated glyph-coverage queries and linear page lookups
  during layout. The image cache is bounded by bytes and entry count; glyph results
  are scoped to a resolve so newly installed fonts do not leave stale results.
- Dictation context and sessions now resolve section-specific voice, sensitivity,
  editing freedom and source policy. Exclusive section source rules reject
  unapproved selections without retrieving any source automatically.

### Verification and limits

- Added GUI/CLI linked-asset retrieval and embedding coverage, complete common-style
  picker coverage, context isolation checks, and merge/history regressions.
- Local layout measurements and raw scaling samples are in
  [the acceptance matrix](https://github.com/jxburros/ByAuthorX/blob/main/docs/acceptance-matrix.md).
  These are offscreen container measurements, not qualified desktop latency.
- Live co-typing remains a no-go pending host, lease and identity contracts; the
  [spike report](https://github.com/jxburros/ByAuthorX/blob/main/docs/coediting-spike.md)
  records the decision. Peer pins do not implement a hosted collaboration service.
- The installers remain unsigned. Physical desktop/device, screen-reader and live
  provider acceptance remain unverified. The full specification is not complete;
  see the [release issue audit](https://github.com/jxburros/ByAuthorX/blob/main/docs/issue-audit-0.2.md).

## [0.1.1] - 2026-10-14

The first release with installers attached. Version 0.1.0 was tagged, but its release page has no
installers, so install 0.1.1 instead. The application itself is unchanged.

### Fixed

- The Ubuntu build failed intermittently, and a failure there stopped the release. One test of the
  hand-drawn spelling marks compared whole pixel rows against a fractional boundary, so faint edge
  pixels of a squiggle that stayed inside the allowed area were reported as outside it. The pen
  stroke is seeded by a random block id, so this happened on some runs only. It failed every
  post-merge Ubuntu run, which skipped the Ubuntu package build, and publishing waits for both
  platforms, so no Windows installer was attached either. The test now allows for the area a pixel
  covers, and a failed check is retried up to twice with a fresh stroke before the test gives up. The
  drawn marks are unchanged.
- When a GitHub Release already exists for the tag, for example one created from the GitHub web
  page, the release workflow now also replaces its title and notes with this changelog entry
  instead of only attaching files.

### Known limitations

The known limitations listed for 0.1.0 below still apply: the installers are unsigned and have not
been verified on physical Windows 11 or Ubuntu 24.04 desktops.

## [0.1.0] - 2026-10-14

First tagged version of ByAuthorX. It is an **unsigned** build for Windows 11 x64 and
Ubuntu 24.04 x64, delivered as a Windows installer and an Ubuntu `.deb`. Read the known
limitations below before relying on it.

### Added

- Native Qt desktop word processor with a continuous writing view, paginated preview, semantic
  outline, review and layer inspectors, find/replace, undo and history, templates, native file
  dialogs and printing, and System/Light/Dark appearance.
- Editable `.byax` documents: portable ZIP packages (document schema 2, with an explicit,
  non-destructive migration from schema 1) that keep semantic identities, assets, review state,
  sources and history. Saves replace the package atomically.
- One shared document engine behind every interface: the desktop app (`byauthorx`), the headless
  CLI (`byax`), a stdio MCP server (`byax-mcp`), an importable library and an optional,
  loopback-only REST service that stays disabled unless started.
- Import and export for TXT, Markdown, HTML, DOCX, ODT and RTF, plus PDF and PNG rendering from
  the same layout engine as the editor. Legacy DOC import runs only through a LibreOffice
  executable you configure. Imports and lossy exports return located compatibility reports and
  never overwrite the original.
- File views that show the master as PDF, Word, OpenDocument, RTF, HTML, Markdown or plain text,
  with exact source previews and a list of what each format hides or degrades.
- Review tooling: threaded comments, tracked changes, selective dependent proposals, branches and
  merge proposals, semantic diffs, and grouped accept/reject.
- Deterministic local checks (spelling, grammar, terminology, structure, layout, accessibility)
  with versioned findings, repairs and per-section writing profiles.
- Optional provider and dictation integrations for OpenAI, Anthropic, Gemini, Ollama,
  ElevenLabs and whisper.cpp. Each is off until you configure it, outbound payloads are shown
  for authorization, and API keys stay in the OS credential store or explicit environment
  references. There are no accounts, telemetry or background update checks.
- ByAuthorX identity: bundled brand fonts, hand-inked icon set and reduced-motion support.
- Accessible names, tooltips and unique shortcuts on every control, plus a localization hook
  through Qt `tr()`.
- Help → Download updates opens the official GitHub Releases page only when you ask; the
  application never downloads or installs updates by itself.

### Known limitations in 0.1.0

- Installers and the `.deb` are unsigned. Windows SmartScreen will warn, and no signing or
  notarization reputation exists yet.
- Automated tests run on an offscreen Qt platform, and CI builds the packages. The packages have
  **not** been installed and checked on physical Windows 11 or Ubuntu 24.04 desktops:
  file associations, native dialogs, clipboard, input methods, display scaling, printing,
  keyring access and microphone capture remain unverified.
- No screen-reader run (NVDA, Orca) has been recorded; the accessibility audit describes what Qt
  exposes, not what assistive technology announces.
- Live provider behaviour (real accounts, models, languages) has not been verified; provider
  contracts are tested against mocked transports.
- Not every scenario in the product specification is complete. The
  [acceptance matrix](https://github.com/jxburros/ByAuthorX/blob/main/docs/acceptance-matrix.md) and the
  [open-issue audit](https://github.com/jxburros/ByAuthorX/blob/main/docs/issue-audit-2026-10-14.md)
  list verified behaviour and the remaining gaps.

[0.1.1]: https://github.com/jxburros/ByAuthorX/releases/tag/v0.1.1
[0.1.0]: https://github.com/jxburros/ByAuthorX/releases/tag/v0.1.0

[0.4.1]: https://github.com/jxburros/ByAuthorX/releases/tag/v0.4.1
[0.4.0]: https://github.com/jxburros/ByAuthorX/releases/tag/v0.4.0
[0.3.0]: https://github.com/jxburros/ByAuthorX/releases/tag/v0.3.0
[0.2.0]: https://github.com/jxburros/ByAuthorX/releases/tag/v0.2.0
