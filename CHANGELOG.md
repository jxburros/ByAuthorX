# Changelog

All notable changes to ByAuthorX are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The single source of the version
number is `src/byax/__init__.py`; `tests/test_release_metadata.py` fails if any other copy
disagrees or if the version has no entry below.

## [0.1.0] - 2026-10-14

First tagged version of ByAuthorX. It is an **unsigned** build for Windows 11 x64 and
Ubuntu 24.04 x64, delivered as a Windows installer, an Ubuntu `.deb` and a portable frozen
directory. Read [Known limitations](#known-limitations-in-010) before relying on it.

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
  [acceptance matrix](docs/acceptance-matrix.md) and the
  [open-issue audit](docs/issue-audit-2026-10-14.md) list verified behaviour and the remaining gaps.

[0.1.0]: https://github.com/jxburros/ByAuthorX/releases/tag/v0.1.0
