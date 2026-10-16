# Native distribution

Targets: Windows 11 x64 installer and Ubuntu 24.04 x64 `.deb`, plus a portable frozen directory containing the GUI, CLI and stdio MCP server. Build on the target OS. Python is bundled; end users do not need a developer environment or a local HTTP server.

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

Windows:

```powershell
iscc packaging/windows.iss
```

The installer registers `.byax` and a Start menu shortcut. The installer adds the installation directory to your user PATH; open a new terminal to run `byax` and `byax-mcp`. Uninstall removes that directory from the user PATH. It requires Windows 11 build 22000 or later. Signing credentials are not configured, so the installer is unsigned and Windows SmartScreen warns on first run. Installers are distributed as GitHub Releases published from version tags (see "Cutting a release").

## Standard and lean variants (on-device search models)

Each release has two variants. Both contain the application, the CLI, the MCP server and the small ONNX runtime (`onnxruntime`, `tokenizers`, from the `local-models` extra) so on-device search works as soon as a model is present.

| Variant | Contents | Files |
| --- | --- | --- |
| **standard** | the lean tree plus the catalogue models marked `bundled`: Qwen3-Embedding-0.6B (int8, 625 MB) and answerai-colbert-small-v1 (134 MB), about 760 MB in all, under `models/` with `SHA256SUMS.txt` and the Apache-2.0 text | `byauthorx_<version>_amd64.deb`, `ByAuthorX-<version>-windows-x64-setup.exe` |
| **lean** | no model files; a model is fetched only from the *On-device search* dialog or `byax models --action download` | `byauthorx-lean_<version>_amd64.deb`, `ByAuthorX-<version>-windows-x64-lean-setup.exe` |

`SHA256SUMS.txt` on the GitHub Release covers every installer of both variants. The bge-reranker-v2-m3 (571 MB) and the Docling layout model (172 MB) are never bundled: bundling them as well would pass the 800 MB budget the plan sets, so they are installable on request. The smaller reranker is the bundled one until the retrieval harness shows the larger one earns its size.

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
2. Download each file from its `url` in `src/byax/resources/models.json` (pinned to a commit) and check the SHA-256 (`python scripts/build_model_catalogue.py --check` verifies the pins against Hugging Face).
3. Create the GitHub Release `models-v1` in `jxburros/ByAuthorX`, upload each file under its `mirror_asset` name, add a `SHA256SUMS.txt` generated with `sha256sum` and each model's licence (Apache-2.0; the model cards `MODEL-CARD.md` are already files of the catalogue entries).
4. Set `"populated": true` in the `mirror` section of `models.json` (and flip `test_the_shipped_catalogue_has_an_unpopulated_mirror...` in `tests/test_local_models.py`), commit, and tag a release. The next CI run builds both variants.

Models are mirrored unchanged, so a mirror file's hash equals the upstream hash. Do not rename or re-export a file without updating the catalogue entry and `build_model_catalogue.py`.

### Sizes

Measured locally on 16 October 2026 (Linux x64, Python 3.13, PyInstaller `--onedir`, `scripts/build.py --bundle-only`): the **lean frozen tree is 643 MB uncompressed**, of which the on-device runtime is 55 MB (`onnxruntime` 30 MB, `tokenizers` 11 MB, `numpy` 14 MB). The standard tree adds the bundled models, 759 MB (`build.py --models-status` reports `bundled_bytes=758947422`), so it is about 1.4 GB uncompressed. The frozen lean CLI listed the catalogue with the runtime installed and the Linux smoke test passed. The standard variant was rehearsed end to end against a fixture mirror with tiny fake model files (`SHA256SUMS.txt` cross-check, bundling, licences, checksums, frozen CLI reporting the models as bundled and verified); it has not been built with the real 759 MB of files because the mirror does not exist yet.

Installer (`.deb`, `.exe`) sizes are not recorded: they need a CI run (the new "Record installer sizes" step prints `ls -l dist/*.deb dist/*setup.exe`). Copy those numbers here and into the acceptance matrix when the first release is cut. The model files are already quantized and do not compress, so the standard installers will be about 760 MB larger than the lean ones.

## Continuous integration

`.github/workflows/ci.yml` runs full Linux tests (including the optional `local-models` and `sqlite-vec` extras) with branch coverage, focused Windows and minimum-Python checks, and installed-wheel CLI/MCP smoke tests. Packaging/dependency/workflow changes, weekly and manual runs, and all release tags also freeze on Ubuntu 24.04 and Windows Server 2022, smoke-test the frozen CLI/MCP, and build native installers. Only installers are uploaded, with seven-day retention and no redundant compression; release publication downloads those artifacts. `release.yml` reuses these gates on every version tag. Ordinary source PRs defer native freezing to the weekly/release runs. A Windows build runner is not evidence of physical Windows 11 acceptance. CI is configured but its remote execution results must be inspected after pushing; local test results do not establish remote CI success.

## Verification still outstanding

Releases are published from CI builds without a physical-desktop installation pass, and each changelog entry's known limitations say so. These checks remain open and do not gate a release: install the generated packages on clean Windows 11 and Ubuntu 24.04 desktops. Exercise file associations, PowerShell/UTF-8 CLI, native dialogs, clipboard, input methods, display scaling, accessibility services, physical printing, keyring access and microphone capture. Provider integrations require selected accounts or installed local executables and models. Record these results in the acceptance matrix rather than implying they were tested by offscreen unit tests.

## Cutting a release

The version lives in `src/byax/__init__.py` (`__version__`). `pyproject.toml`, `uv.lock` and the `MyAppVersion` define in `packaging/windows.iss` must carry the same value; the About dialog, `byax version` and the `.deb` name read it from source. `tests/test_release_metadata.py` fails when any copy disagrees or `CHANGELOG.md` has no `## [x.y.z]` entry.

1. Change `__version__`, `pyproject.toml` and `packaging/windows.iss`, run `uv lock` to refresh `uv.lock`, and add the changelog entry with its date and known limitations.
2. Run the CI gates locally (format, lint, mypy, tests with the coverage floor, `scripts/build.py --bundle-only`) and merge to `main` only when CI is green on both runners.
3. Tag the merge commit `vX.Y.Z` and push the tag. `.github/workflows/release.yml` does the rest: it checks that the tag matches `__version__`, that `CHANGELOG.md` has the entry and that the commit is on `main`; it reruns the full CI workflow (gates, frozen builds, Ubuntu package, Windows installer) on the tagged commit; and it publishes a GitHub Release with the installer, the `.deb`, `SHA256SUMS.txt` and the changelog entry as notes. Nothing is uploaded by hand. Re-running the workflow for the same tag replaces the attached files. If the release already exists (for example, created from the GitHub web page when tagging), the workflow attaches the files to it and replaces its title and notes with the changelog entry. The workflow cannot be tested before a real tag is pushed, so watch the first run.

Changelog entries become the release notes verbatim, so they must use absolute links.

The `.deb` is written to `dist/byauthorx_<version>_amd64.deb` and the Windows installer to `dist/ByAuthorX-<version>-windows-x64-setup.exe`.

## Explicit update and rollback policy

Help → Download updates opens the official GitHub Releases page only when requested.
The application never polls for updates, downloads an installer or replaces itself in the background.
Choose the installer for the supported platform and retain the previous installer until the new version
has passed local acceptance. Only builds attached to a GitHub Release are meant for distribution; CI
artifacts from other commits are for testing. There is no claim that signing, notarization or
SmartScreen reputation has been established.

Before upgrading, save and keep a separate copy of working documents. Close the application, install the
selected version using the platform package manager, and reopen the copies. To roll back, close the new
version, reinstall the retained prior package and open a pre-upgrade document copy. Native migration
retains schema backups as documented in `architecture.md`; an older application refuses newer schemas
instead of silently downgrading them. Releases stay unsigned until signing identities are provisioned
separately; neither that nor a Windows 11 / Ubuntu 24.04 installation audit gates publishing.
