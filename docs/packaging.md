# Native distribution

Targets: Windows 11 x64 installer, Ubuntu 24.04 x64 `.deb` and a macOS 14+ Apple silicon (arm64) `.dmg`, plus a portable frozen directory containing the GUI, CLI and stdio MCP server on Windows and Linux. Build on the target OS. Intel Macs are not a target; that waits for demand (competitiveness plan decision 4). Python is bundled; end users do not need a developer environment or a local HTTP server.

## Build

```bash
uv sync --extra dev
uv run python scripts/build.py --bundle-only
```

The three entry points share `dist/ByAuthorX/_internal`. Keep that directory intact. The build excludes Qt WebEngine and uses the native Widgets surface, and never bundles the optional `docling` (and its torch) extra. Third-party license files are copied into the distribution. Signing credentials are not included, and model weights are not part of this lean tree; the standard variant adds them (see below).

The wheel and frozen bundles include the identity resources, the bundled fonts (the baseline DejaVu faces, the supplied Manrope, IM FELL English and Source Serif 4, and the six ByAuthorX brand fonts) and their notices under `byax/resources`; `--collect-data byax` picks the fonts up without any list to maintain. PyInstaller embeds the supplied Windows ICO into the GUI executable; Inno Setup uses it for the installer and the executable for uninstall/file-association icons. Ubuntu installs the supplied scalable icon and transparent PNG sizes into the hicolor icon theme. Editable Vixl sources and the reference PDFs live in `design/brand` and are not bundled into the executable. The rendered README and brand documentation use graphics in the bundled `docs/images` folder.

Ubuntu 24.04:

```bash
uv run python scripts/build_deb.py
sudo apt install ./dist/byauthorx_<version>_amd64.deb
```

`scripts/build_deb.py --variant lean|standard` builds the matching package; `byauthorx` and `byauthorx-lean` conflict and replace each other. The package installs under `/opt/byauthorx`, provides `/usr/bin/byauthorx`, `/usr/bin/byax`, and `/usr/bin/byax-mcp`, and registers the desktop launcher and case-insensitive `.byax` MIME association. Do not distribute a package frozen on a newer glibc system as an Ubuntu 24.04 build.

macOS (Apple silicon, macOS 14 or later):

```bash
uv run python scripts/build_macos.py --dmg
```

`scripts/build.py` on macOS calls the same recipe. It freezes **one windowed app**, `dist/ByAuthorX.app`, built for arm64 only, with bundle identifier `io.github.jxburros.byauthorx`. The CLI and MCP server live in the same bundle: `Contents/MacOS/byax` and `byax-mcp` are small launchers that re-enter the application executable (`packaging/gui_entry.py` dispatches on `--byax-cli` / `--byax-mcp`), so there is one copy of Python and Qt. To use them from a terminal, link them: `ln -s /Applications/ByAuthorX.app/Contents/MacOS/byax /usr/local/bin/byax`. The build adds to PyInstaller's `Info.plist`: the document type and exported type declaration for `.byax` (`io.github.jxburros.byauthorx.document`, conforming to `public.data`), `NSMicrophoneUsageDescription` (dictation and audio recording), `NSHighResolutionCapable`, `LSArchitecturePriority`/`LSRequiresNativeExecution` for arm64, and `LSMinimumSystemVersion` 14.0. 14.0 is not arbitrary: the PySide6 6.12 wheels that Python 3.12 resolves are tagged `macosx_14_0` in `uv.lock` (`tests/test_macos_support.py` fails if the plist falls below the locked wheels). The icon is generated at build time from `app-1024.png` as `.icns`. The disk image holds the app, an `Applications` link and, for unsigned builds only, `READ ME FIRST - unsigned build.txt` (`packaging/macos/README-UNSIGNED.txt`).

macOS notes for contributors: Finder "Open With" and double-clicks arrive as `QFileOpenEvent`, handled by `gui_platform.FileOpenFilter`; About, Provider settings and Quit carry explicit `QAction.MenuRole`s so macOS moves them into the application menu; documents are flushed with `F_FULLFSYNC`; lock identity folds case and Unicode normalization form because default APFS volumes are case-insensitive; installed fonts are found by scanning `~/Library/Fonts`, `/Library/Fonts` and `/System/Library/Fonts` (there is no fontconfig); the library parser's `RLIMIT_AS` request is best-effort, because macOS may refuse or ignore it, and the parent's wall-clock timeout and output limits remain the enforced bounds. Credentials use the system Keychain through `keyring`'s macOS backend, which `scripts/build_macos.py` includes with `--hidden-import keyring.backends.macOS`; macOS asks for permission the first time the app reads an item.

Windows:

```powershell
iscc packaging/windows.iss
```

The installer registers `.byax` and a Start menu shortcut. The installer adds the installation directory to your user PATH; open a new terminal to run `byax` and `byax-mcp`. Uninstall removes that directory from the user PATH. It requires Windows 11 build 22000 or later. Signing credentials are not configured, so the installer is unsigned and Windows SmartScreen warns on first run. Installers are distributed as GitHub Releases published from version tags (see "Cutting a release").

## Signing and notarization

Signing is **optional and secret-driven**. `release.yml` signs only when the repository secrets below exist; without them it publishes unsigned, clearly labelled files and says so in the release notes ("Signing status"). A signature, a notarization ticket or a Gatekeeper pass has not been observed for any ByAuthorX build; those remain pending maintainer verification in the [acceptance matrix](acceptance-matrix.md).

| Platform | Secrets (Settings, Secrets and variables, Actions) | What happens |
| --- | --- | --- |
| macOS | `APPLE_CERTIFICATE_P12` (base64 of the Developer ID Application `.p12`), `APPLE_CERTIFICATE_PASSWORD`, `APPLE_TEAM_ID`, `APPLE_ID`, `APPLE_APP_PASSWORD` (app-specific password) | **All five** must exist. The `macos` job imports the certificate into a temporary keychain, signs every nested binary and the app inside-out with `codesign --options runtime --timestamp` and `packaging/macos/entitlements.plist`, builds and signs the `.dmg`, submits it with `xcrun notarytool submit --wait`, staples the ticket (`xcrun stapler staple`) and checks it. The file is named `ByAuthorX-<version>-macos-arm64.dmg`. With any secret missing the job builds `ByAuthorX-<version>-macos-arm64-UNSIGNED.dmg` instead |
| Windows | `WINDOWS_CERTIFICATE_PFX` (base64 of a code-signing `.pfx`), `WINDOWS_CERTIFICATE_PASSWORD` | The `windows-sign` job runs `signtool sign /fd SHA256 /tr <timestamp server> /td SHA256` on the installer and `signtool verify /pa`. Without the secrets the installer is published unsigned |
| Linux | `GPG_PRIVATE_KEY` (armored secret key), `GPG_PASSPHRASE` | The `publish` job writes a detached, armored signature `SHA256SUMS.txt.asc`, attaches the public key `byauthorx-release-key.asc`, and, where `dpkg-sig` installs, embeds a `builder` signature in the `.deb`. Without the key the checksums and package are published unsigned |

Choices and limits to know before you provision them:

- **Windows: signtool with a PFX, not Azure Trusted Signing.** A PFX is the simplest option that works with a plain GitHub secret. Azure Trusted Signing (or an EV certificate on a hardware token) would be needed for immediate SmartScreen reputation and cannot be exported to a PFX; switching means replacing only the `Sign the installer` step. A new certificate still builds SmartScreen reputation over time.
- **Windows: only the installer is signed.** The executables inside it (`ByAuthorX.exe`, `byax.exe`, `byax-mcp.exe`) and the uninstaller are not. Signing them would mean signing before `iscc` runs, inside the CI `packages` job; add that when a certificate exists.
- **macOS hardened runtime.** The entitlements enable only microphone input and outbound network connections. JIT, unsigned executable memory and disabled library validation are deliberately absent because the bundle excludes Qt WebEngine and QtQuick. If a notarized build fails to launch with a library validation error, the documented fallback is to add `com.apple.security.cs.disable-library-validation` to the plist after confirming the cause, not before. `notarytool` needs an app-specific password; keep the Apple ID secret on a team account.
- **Verification the script cannot do.** `scripts/build_macos.py` was written without a Mac. The command lines it builds are covered by `tests/test_macos_support.py`; the first tagged release must be watched, and after it run `spctl --assess --type exec --verbose=2 /Applications/ByAuthorX.app`, `codesign --verify --deep --strict --verbose=2`, `xcrun stapler validate` on the downloaded `.dmg`, and open the app once from a quarantined download. Record the result in the acceptance matrix.
- **Linux users verify** with `gpg --import byauthorx-release-key.asc`, `gpg --verify SHA256SUMS.txt.asc SHA256SUMS.txt` and `sha256sum -c --ignore-missing SHA256SUMS.txt`. Publish the key fingerprint somewhere other than the release itself (the repository README) when a key is first created. `dpkg-sig --verify` works on the package only with the key imported. APT does not check these signatures automatically because the `.deb` is not distributed through an APT repository.

## Standard and lean variants (on-device search models)

Each release has two variants. Both contain the application, the CLI, the MCP server and the small ONNX runtime (`onnxruntime`, `tokenizers`, from the `local-models` extra) so on-device search works as soon as a model is present.

| Variant | Contents | Files |
| --- | --- | --- |
| **standard** | the lean tree plus the catalogue models marked `bundled`: Qwen3-Embedding-0.6B (int8, 625 MB) and answerai-colbert-small-v1 (134 MB), about 760 MB in all, under `models/` with `SHA256SUMS.txt` and the Apache-2.0 text | `byauthorx_<version>_amd64.deb`, `ByAuthorX-<version>-windows-x64-setup.exe` |
| **lean** | no model files; a model is fetched only from the *On-device search* dialog or `byax models --action download` | `byauthorx-lean_<version>_amd64.deb`, `ByAuthorX-<version>-windows-x64-lean-setup.exe` |

`SHA256SUMS.txt` on the GitHub Release covers every installer of both variants. The bge-reranker-v2-m3 (571 MB), the Docling layout model (172 MB) and the HHEM-2.1-Open entailment checker (167 MB) are never bundled: bundling them as well would pass the 800 MB budget the plan sets, so they are installable on request. The smaller reranker is the bundled one until the retrieval harness shows the larger one earns its size.

```bash
uv sync --extra dev --extra local-models
uv run python scripts/build.py --bundle-only      # lean tree in dist/ByAuthorX
uv run python scripts/build_deb.py --variant lean # byauthorx-lean_<version>_amd64.deb
uv run python scripts/build.py --models-status    # populated=true|false
uv run python scripts/build.py --variant standard # copies the lean tree to dist/ByAuthorX-standard and adds models/
uv run python scripts/build_deb.py --variant standard
```

On Windows run `iscc /DLean packaging/windows.iss` for the lean installer and `iscc packaging/windows.iss` (after `--variant standard`) for the standard one.

The standard variant is built only from the project's own mirror, never from a third-party host. `build.py --variant standard` downloads every `bundled` model from the `models-v1` release, checks each file against the pinned SHA-256 in `src/byax/resources/models.json` and fails with a clear message while the mirror is not populated. CI and `release.yml` ask `build.py --models-status` first: until the release exists they build and publish the lean variant only (`::warning::` in the log and a line in the release notes) so the existing release build keeps working.

### Maintainer step: create the `models-v1` release

The release does not exist yet. To create it:

1. `python scripts/build.py --models-help` prints every file, asset name, size and SHA-256.
2. Download each file from its `url` in `src/byax/resources/models.json` (pinned to a commit) and check the SHA-256 (`python scripts/build_model_catalogue.py --check` verifies the pins against Hugging Face). Files flagged `mirror_only` have no upstream copy: `hhem-2.1-open-int8__model.onnx` is the project's own ONNX build, produced by `scripts/export_hhem_onnx.py` (recipe and pinned library versions in its docstring). Run it, compare the printed size and SHA-256 with the catalogue (`166804567` bytes, `8ac275e8…`), and upload that file; if a different CPU or library version gives other bytes, update the pin in `scripts/build_model_catalogue.py`, rerun it and `scripts/entailment_benchmark.py --write docs/benchmarks/0.4-entailment.json`. Until the release is populated `byax models --action download --name hhem-2.1-open-int8` explains this and refuses.
3. Create the GitHub Release `models-v1` in `jxburros/ByAuthorX`, upload each file under its `mirror_asset` name, add a `SHA256SUMS.txt` generated with `sha256sum` and each model's licence (Apache-2.0; the model cards `MODEL-CARD.md` are already files of the catalogue entries).
4. Set `"populated": true` in the `mirror` section of `models.json` (and flip `test_the_shipped_catalogue_has_an_unpopulated_mirror...` in `tests/test_local_models.py`), commit, and tag a release. The next CI run builds both variants.

Models are mirrored unchanged, so a mirror file's hash equals the upstream hash, except for `mirror_only` files, whose hash is the one the catalogue pins for the project's own build. Do not rename or re-export a file without updating the catalogue entry and `build_model_catalogue.py`.

### Sizes

Measured locally on 16 October 2026 (Linux x64, Python 3.13, PyInstaller `--onedir`, `scripts/build.py --bundle-only`): the **lean frozen tree is 643 MB uncompressed**, of which the on-device runtime is 55 MB (`onnxruntime` 30 MB, `tokenizers` 11 MB, `numpy` 14 MB). The standard tree adds the bundled models, 759 MB (`build.py --models-status` reports `bundled_bytes=758947422`), so it is about 1.4 GB uncompressed. The frozen lean CLI listed the catalogue with the runtime installed and the Linux smoke test passed. The standard variant was rehearsed end to end against a fixture mirror with tiny fake model files (`SHA256SUMS.txt` cross-check, bundling, licences, checksums, frozen CLI reporting the models as bundled and verified); it has not been built with the real 759 MB of files because the mirror does not exist yet.

Installer (`.deb`, `.exe`) sizes are not recorded: they need a CI run (the new "Record installer sizes" step prints `ls -l dist/*.deb dist/*setup.exe`). Copy those numbers here and into the acceptance matrix when the first release is cut. The model files are already quantized and do not compress, so the standard installers will be about 760 MB larger than the lean ones.

## Continuous integration

`.github/workflows/ci.yml` runs full Linux tests (including the optional `local-models` and `sqlite-vec` extras) with branch coverage, focused Windows, macOS (`macos-14`, Apple silicon) and minimum-Python checks (the same short list of storage, CLI, MCP, GUI, release and platform tests on each), and installed-wheel CLI/MCP smoke tests. Packaging/dependency/workflow changes, weekly and manual runs, and all release tags also freeze on Ubuntu 24.04, Windows Server 2022 and macOS 14 (the app bundle is frozen and its CLI and MCP launchers are smoke-tested, but the disk image is only built in `release.yml`), smoke-test the frozen CLI/MCP, and build native installers. Only installers are uploaded, with seven-day retention and no redundant compression; release publication downloads those artifacts. `release.yml` reuses these gates on every version tag. Ordinary source PRs defer native freezing to the weekly/release runs. A Windows or macOS build runner is not evidence of physical Windows 11 or Mac desktop acceptance. CI is configured but its remote execution results must be inspected after pushing; local test results do not establish remote CI success.

## Verification still outstanding

Releases are published from CI builds without a physical-desktop installation pass, and each changelog entry's known limitations say so. These checks remain open and do not gate a release: install the generated packages on clean Windows 11, Ubuntu 24.04 and Apple silicon macOS 14+ desktops. Exercise file associations (on macOS, double-clicking and Open With reach the running app), PowerShell/UTF-8 CLI, native dialogs, clipboard, input methods, display scaling, accessibility services, physical printing, keyring access and microphone capture. Provider integrations require selected accounts or installed local executables and models. Record these results in the acceptance matrix rather than implying they were tested by offscreen unit tests.

## Cutting a release

The version lives in `src/byax/__init__.py` (`__version__`). `pyproject.toml`, `uv.lock` and the `MyAppVersion` define in `packaging/windows.iss` must carry the same value; the About dialog, `byax version` and the `.deb` name read it from source. `tests/test_release_metadata.py` fails when any copy disagrees or `CHANGELOG.md` has no `## [x.y.z]` entry.

1. Change `__version__`, `pyproject.toml` and `packaging/windows.iss`, run `uv lock` to refresh `uv.lock`, and add the changelog entry with its date and known limitations.
2. Run the CI gates locally (format, lint, mypy, tests with the coverage floor, `scripts/build.py --bundle-only`) and merge to `main` only when CI is green on both runners.
3. Tag the merge commit `vX.Y.Z` and push the tag. `.github/workflows/release.yml` does the rest: it checks that the tag matches `__version__`, that `CHANGELOG.md` has the entry and that the commit is on `main`; it reruns the full CI workflow (gates, frozen builds, Ubuntu package, Windows installer) on the tagged commit; it builds the macOS disk image, signs the installer and notarizes the image when the secrets exist (see "Signing and notarization"); and it publishes a GitHub Release with the installer, the `.deb`, the `.dmg`, `SHA256SUMS.txt` (and, with a GPG key, its signature and the public key) and the changelog entry plus a signing-status summary as notes. Nothing is uploaded by hand. Re-running the workflow for the same tag replaces the attached files. If the release already exists (for example, created from the GitHub web page when tagging), the workflow attaches the files to it and replaces its title and notes with the changelog entry. The workflow cannot be tested before a real tag is pushed, so watch the first run.

Changelog entries become the release notes verbatim, so they must use absolute links.

The `.deb` is written to `dist/byauthorx_<version>_amd64.deb`, the Windows installer to `dist/ByAuthorX-<version>-windows-x64-setup.exe` and the macOS image to `dist/ByAuthorX-<version>-macos-arm64.dmg` (with `-UNSIGNED` or `-NOT-NOTARIZED` in the name when it lacks the signature or ticket).

## Explicit update and rollback policy

Updates are never automatic. Two Help menu items exist, and each acts only when chosen:

- **Help → Download updates…** opens the official GitHub Releases page in your browser. ByAuthorX makes no request.
- **Help → Check for updates…** makes **one** request, `GET https://api.github.com/repos/jxburros/ByAuthorX/releases/latest`, when you click it, compares the release tag with the running version and shows the result with a link to the release page. `byax version --check-updates` does the same from the CLI. It honours the standard proxy variables (`HTTPS_PROXY`, `NO_PROXY` and the certificate-bundle variables) through httpx, sends only a `User-Agent` of `ByAuthorX/<version>`, reads only the tag, the date and a release link on this project's own page, and never downloads or installs anything. There is no request at startup and no polling. MCP and REST refuse the flag. To remove the item, untick **Help → Show "Check for updates" in this menu**, or set `BYAX_DISABLE_UPDATE_CHECK=1` to disable the item and the CLI flag on managed desktops.

Choose the installer for the supported platform and retain the previous installer until the new version
has passed local acceptance. Only builds attached to a GitHub Release are meant for distribution; CI
artifacts from other commits are for testing. Signing, notarization and SmartScreen reputation count as
established only when the release notes' "Signing status" says `signed` (see
[Signing and notarization](#signing-and-notarization)); none has been observed so far.

Before upgrading, save and keep a separate copy of working documents. Close the application, install the
selected version using the platform package manager (on macOS, drag the new app over the old one in
Applications), and reopen the copies. To roll back, close the new
version, reinstall the retained prior package and open a pre-upgrade document copy. Native migration
retains schema backups as documented in `architecture.md`; an older application refuses newer schemas
instead of silently downgrading them. Releases stay unsigned until signing identities are provisioned
separately; neither that nor a platform installation audit gates publishing.
