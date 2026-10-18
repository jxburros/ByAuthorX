<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/wordmark-dark.svg">
  <img src="docs/images/wordmark-light.svg" alt="ByAuthorX" width="360">
</picture>

# ByAuthorX

**Your words. Your call.**

A desktop word processor for writers, from a first letter to a finished book. Write in a clean, paged editor, keep everything in one editable `.byax` file you own, and share it as PDF, Word, EPUB or Markdown. Suggestions from checks, reviewers and AI arrive as proposals you accept or reject one by one. Nothing changes your text until you say so.

A desktop word processor for writers, from a first letter to a finished book. Write in a clean, paged editor, keep everything in one editable `.byax` file you own, and share it as PDF, Word, EPUB or Markdown. Edits from checks, reviewers and AI arrive as proposals you accept or reject one by one. Nothing changes your text until you say so.

![Native ByAuthorX desktop editor](docs/images/editor.png)

## Why ByAuthorX

It is built for long work and for review: the book, the thesis, the report that passes through an editor, a beta reader or an AI assistant before it is done. You decide what goes in.

- **Proposals, not surprises.** Spelling, grammar, fiction and continuity checks, your editor's tracked changes in Word, and AI edits all arrive as proposed changes. You decide each one.
- **Citations you can check.** Attach your own research, cite it, and see whether a quotation really appears in its source. When an assistant helps, you can see exactly what it was shown, and a record of what it did stays with the file.
- **You own the file.** The `.byax` master keeps your text, styles, images, review notes and history together. Exporting makes a copy and never replaces the master, and every export tells you exactly what a format could not carry.
- **Private by default.** No account, no telemetry, no background network traffic, and nothing leaves your computer until you choose a provider. AI is opt-in: a hosted service with your own key, or a model on your own machine.
- **Built for long work.** A binder with chapters, scenes and notes, a corkboard, writing goals, named drafts you can compare, a codex of characters and places, and EPUB and print-ready PDF output.

Letters, resumes, reports and press kits work too; the editor is a full word processor. Scripting is there if you want it, and you never need it to write.

## Get started

Download an installer from the [releases page](https://github.com/jxburros/ByAuthorX/releases) and follow the [user guide](docs/user-guide.md). **Help → Open sample project** opens a short worked manuscript to practise on, and **Help → Take the tour…** shows the main ideas in six steps. The [documentation index](docs/README.md) lists everything else.

## What you can do

- **Write and format.** A continuous writing view and a paginated preview, named styles, tables and images, find and replace, undo and history, templates, printing and dictation. See [the window](docs/user-guide.md#the-window).
- **Organize a book.** Parts, chapters, scenes and notes in a binder, with outline and corkboard views, synopses, writing goals and named drafts you can compare scene by scene. See [manuscript projects](docs/user-guide.md#manuscript-projects-binder).
- **Check your writing.** Spelling and grammar run on your computer. Fiction, readability and continuity checks are advisory, and a codex tracks characters and places. See [reviewing your own text](docs/user-guide.md#reviewing-your-own-text).
- **Work with editors and readers.** A Word round trip returns your editor's tracked changes and comments as proposals; beta-reader copies and synced-folder safety checks protect your file. See [working with an editor in Word](docs/user-guide.md#working-with-an-editor-in-word).
- **Publish.** EPUB 3 export and import, and **File → Compile…** with novel, non-fiction and manuscript presets, preflight checks and print-book design. See [compiling a book](docs/user-guide.md#compiling-a-book).
- **Use AI on your terms.** Chat only answers. Work mode suggests exact edits that appear in **Proposed changes**; you accept each one. When a request would send text off your computer, you see exactly what goes first, unless you have saved a policy for that provider. See [providers](docs/providers.md).
- **See a file in other formats.** The File view selector shows the master as PDF, Word, OpenDocument, EPUB, RTF, HTML, Markdown or plain text, and says what each format cannot carry. See [file views](docs/file-views.md).

ByAuthorX never checks for updates by itself. **Help → Check for updates…** makes one request to GitHub when you ask, and downloads and installs nothing.

## Status

Version 0.4.1 (see the [changelog](CHANGELOG.md)). ByAuthorX is a working application with automated engine, format, provider-contract, desktop and integration tests. Windows 11 x64, Ubuntu 24.04 x64 and macOS 14+ on Apple silicon are the intended installation targets.

Not yet verified: installers are unsigned unless a release's notes say otherwise, and they have not been run on physical Windows 11, Ubuntu 24.04 or Apple silicon desktops (the macOS port builds in CI and its platform code is unit tested, but no Mac has run it yet). Live model services also need separate verification. The [acceptance matrix](docs/acceptance-matrix.md) records what is verified and what is not, and this project does not claim that every scenario in the [product specification](PRODUCT-SPECIFICATION.md) is complete.

## Look and feel

Warm paper, quiet ink and a clean white writing page. Spelling and grammar marks are drawn as pen strokes in the writing view only; Pages, print and every export keep exact lines. Motion is brief and appears only while something is happening. **View → Reduce motion** and **View → Hand-drawn marks** switch these off, and **View → Theme** offers System, Light or Dark. The design system is in [brand, design and motion](docs/brand.md), and the way ByAuthorX writes is in [voice and messaging](docs/voice.md).

![Hand-inked marks, icons and chrome in the writing view](docs/images/editor-marks.png)

## For developers and automation

ByAuthorX is open source (MIT). The desktop app, command line, MCP server and optional local REST service all call one document engine. [For developers and automation](docs/developers.md) covers running from source, the command line, MCP and REST, tests and builds. See also the [CLI reference](docs/cli-reference.md), [architecture](docs/architecture.md) and [CONTRIBUTING](CONTRIBUTING.md).

## License

ByAuthorX is a product of JX Holdings, LLC. MIT for ByAuthorX source. Qt/PySide6 and other dependencies retain their licenses; see [third-party notices](docs/third-party-notices.md). Provider services and downloaded model weights are separate integrations.
