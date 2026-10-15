# Troubleshooting

## Command says the document is `locked` (exit 3)

A document open in the desktop app holds an OS-backed lock, so CLI/MCP/REST writes fail rather than
modify the file behind unsaved edits. Save and close the window (or use the read-only commands,
`inspect`, `view`, `find`). Locks live in the ByAuthorX cache directory, not beside the document. After
upgrading from a pre-0.1 build with sidecar locks, restart old app processes.

## `conflict` / stale revision (exit 3)

Someone (or another client) changed the document since you inspected it. Re-run `byax inspect`,
rebuild the envelope with the new `revision`, and retry. Reusing a `request_id` with a different
payload is also a conflict; identical payloads simply report `already_committed`.

## Linux: app will not start, "xcb" / "EGL" / "libxkbcommon" errors

Install the Qt runtime libraries: `sudo apt install libegl1 libgl1 libxkbcommon0 libdbus-1-3
libxcb-cursor0 libasound2t64`. For headless rendering or tests set `QT_QPA_PLATFORM=offscreen`.

## Windows: `byax` not found after install

The installer adds its directory to your *user* PATH. Open a new terminal.

## Windows: SmartScreen warning

Installers are unsigned. Choose *More info → Run anyway* only for builds you downloaded from the
project's GitHub Releases and verified against `SHA256SUMS.txt`.

## `.doc` files will not import

Legacy `.doc` needs an explicitly configured converter: set `BYAX_LIBREOFFICE` to the absolute path of
a LibreOffice executable. Without it, ByAuthorX will not convert (and never uploads the file). Convert to DOCX/ODT
yourself as an alternative.

## Import or export reports losses

Expected: formats differ. The report lists each loss by type and location. A Markdown source with formatting outside
its dialect (colors, fonts, alignment…) needs an explicit `.byax` conversion or `allow_markdown_losses`.
See [compatibility](compatibility.md).

## AI features are unavailable

Writing, saving, printing and deterministic checks never depend on them. For AI: choose a provider, an explicit model,
and a key (OS credential store or `env:NAME`) under **Add or manage API keys…**. Test the connection: it
separates "credentials reachable" from "inference verified". A timed-out remote call may have an uncertain
outcome and is not retried automatically. See [providers](providers.md).

## `byax serve` refuses to start or rejects calls

Requires `BYAX_SERVICE_TOKEN` of at least 24 characters, at least one `--root`, and loopback only. Calls need
`Authorization: Bearer <token>`, no browser `Origin`, and paths inside a root. `providers --action configure`
is never available remotely.

## Opening an older file

Older packages migrate in memory; nothing is written on open. The first save keeps a byte-identical copy
`<name>.schema<N>-<hash>.byax` beside the file. A package from a *newer* version is refused so it is never
downgraded. See [architecture](architecture.md#format-versions-and-migration).

## Fonts look different on another machine

Fallback fonts differ between Windows and Ubuntu. Use `byax fonts` to see which font shapes each cluster and
whether embedding is permitted, and embed permitted fonts for portable PDFs.

## Animations are distracting or slow

View → Reduce motion, or set `BYAX_REDUCED_MOTION=1`.

## Tests fail locally with Qt errors

Set `QT_QPA_PLATFORM=offscreen`. A pen-mark pixel test is retried up to twice; see
[development](development.md#tests).
