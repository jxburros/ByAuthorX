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

## Synced folders: conflicted copies, empty files, `.icloud` placeholders

- `Cannot read native package: The file is empty` or `incomplete or damaged`: the sync client has not
  finished writing or downloading it. Wait, then retry; ByAuthorX never modifies such a file. If it stays
  broken, restore the file from your sync service's version history or from a recovery copy
  (File → Recover autosave…).
- `.name.byax.icloud` or an online-only (Files On-Demand) file: download it in Finder or Explorer first.
- Files named `… (…'s conflicted copy …).byax`, `…-HOSTNAME.byax`, `… 2.byax` or `….sync-conflict-….byax`
  next to a document: another computer saved the same file. Run `byax sync DOC --action conflicts`, or
  use Tools → Review conflicted copies…, to merge it as a reviewable proposal. See
  [working in synced folders](user-guide.md#working-in-synced-folders).
- Save says the file changed on disk: a sync client delivered a newer version after you opened it.
  Nothing was overwritten. Use Save as, then merge the two versions.
- Leftover `.name.byax.XXXX.tmp` files come from an interrupted save; delete them once no window has the
  document open and sync has finished. `byax sync DOC --action status` lists them.

## Linux: app will not start, "xcb" / "EGL" / "libxkbcommon" errors

Install the Qt runtime libraries: `sudo apt install libegl1 libgl1 libxkbcommon0 libdbus-1-3
libxcb-cursor0 libasound2t64`. For headless rendering or tests set `QT_QPA_PLATFORM=offscreen`.

## Windows: `byax` not found after install

The installer adds its directory to your *user* PATH. Open a new terminal.

## Windows: SmartScreen warning

Unless the release notes' "Signing status" says the installer is `signed`, it is unsigned. Choose
*More info → Run anyway* only for builds you downloaded from the project's GitHub Releases and verified
against `SHA256SUMS.txt` (`Get-FileHash -Algorithm SHA256 <file>`). A newly signed installer can still
warn until SmartScreen has seen enough downloads.

## macOS: Gatekeeper says the app cannot be opened

A `.dmg` named `...-UNSIGNED.dmg` (or `-NOT-NOTARIZED`) has no Apple Developer ID signature or
notarization ticket, so Gatekeeper blocks it ("cannot be opened because the developer cannot be verified",
or "is damaged" for a quarantined download). First verify the download: `shasum -a 256 <file>.dmg` must
match `SHA256SUMS.txt` on the release. Then drag ByAuthorX to Applications and either Control-click it and
choose *Open* twice, or on macOS 15 and later try to open it once and use *System Settings → Privacy &
Security → Open Anyway*, or run `xattr -dr com.apple.quarantine /Applications/ByAuthorX.app`. Do this
only for builds from the project's GitHub Releases. The same steps are in the `READ ME FIRST` file inside
the unsigned image. The first time you store or use a provider key, macOS asks whether ByAuthorX may use
the Keychain; the first time you dictate it asks for the microphone (System Settings → Privacy &
Security → Microphone if you declined). The `byax` command is inside the app:
`ln -s /Applications/ByAuthorX.app/Contents/MacOS/byax /usr/local/bin/byax`. Apple silicon only; Intel
Macs are not supported yet.

## Check for updates fails

The check makes one request to `api.github.com` and nothing else. "Could not reach GitHub" means the
network or a proxy blocked it: set `HTTPS_PROXY` (and `NO_PROXY` if needed) the way your other tools
expect, or open **Help → Download updates…** to look at the releases page in a browser. "Rate limiting"
means GitHub is throttling your address; wait and try again. If the menu item is missing, either
**Help → Show "Check for updates" in this menu** is unticked or `BYAX_DISABLE_UPDATE_CHECK=1` is set.

## The tour keeps appearing, or never appears

The tour is shown once per tour version, remembered in the application's local settings. Tick *Don't
show this tour again*, or set `BYAX_NO_TOUR=1`, to stop it. **Help → Take the tour…** always opens it.

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
