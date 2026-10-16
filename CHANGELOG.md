# Changelog

All notable changes to ByAuthorX are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The single source of the version
number is `src/byax/__init__.py`; `tests/test_release_metadata.py` fails if any other copy
disagrees or if the version has no entry below. The release workflow publishes a version's
entry as its GitHub Release notes, so use absolute links and no in-page anchors in entries.

## [Unreleased]

### Added

- Research-to-publication guide, dismissible blank-document introduction and an
  offline sample project with a locally indexed research library.
- Claims and evidence viewer showing current passages, locators, quotation-match
  versus interpretation outcomes, manual sources and stale/unavailable evidence.
- Bounded evidence inspection through the shared CLI, read-only MCP tool and REST
  dispatcher, with continuation and section source-policy enforcement.
- Review approaches and enforced preservation of numbers, quotations, cited text
  and terminology, with readable before/after wording and author instructions.
- Prepare to share: exact publication-copy preview, explicit private-material
  inclusion, required checks, located format losses and separate clean export.
- CI gate requiring executed desktop workflow tests, release build provenance,
  physical desktop acceptance checklist and five-author evaluation protocol.

### Fixed

- Model review stops when pending editor changes cannot be committed and refuses
  late registration after the active document or revision changes.

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

[0.2.0]: https://github.com/jxburros/ByAuthorX/releases/tag/v0.2.0
