# Native distribution

Targets: Windows 11 x64 installer and Ubuntu 24.04 x64 `.deb`, plus a portable frozen directory containing the GUI, CLI and stdio MCP server. Build on the target OS. Python is bundled; end users do not need a developer environment or a local HTTP server.

## Build

```bash
uv sync --extra dev
uv run python scripts/build.py --bundle-only
```

The three entry points share `dist/ByAuthorX/_internal`. Keep that directory intact. The build excludes Qt WebEngine and uses the native Widgets surface. Third-party license files are copied into the distribution. Signing credentials and model weights are not included.

The wheel and frozen bundles include the identity resources, the bundled fonts (the baseline DejaVu faces, the supplied Manrope, IM FELL English and Source Serif 4, and the six ByAuthorX brand fonts) and their notices under `byax/resources`; `--collect-data byax` picks the fonts up without any list to maintain. PyInstaller embeds the supplied Windows ICO into the GUI executable; Inno Setup uses it for the installer and the executable for uninstall/file-association icons. Ubuntu installs the supplied scalable icon and transparent PNG sizes into the hicolor icon theme. Editable Vixl sources and the reference PDFs live in `design/brand` and are not bundled into the executable. The rendered README and brand documentation use graphics in the bundled `docs/images` folder.

Ubuntu 24.04:

```bash
uv run python scripts/build_deb.py
sudo apt install ./dist/byauthorx_<version>_amd64.deb
```

The package installs under `/opt/byauthorx`, provides `/usr/bin/byauthorx`, `/usr/bin/byax`, and `/usr/bin/byax-mcp`, and registers the desktop launcher and case-insensitive `.byax` MIME association. Do not distribute a package frozen on a newer glibc system as an Ubuntu 24.04 build.

Windows:

```powershell
iscc packaging/windows.iss
```

The installer registers `.byax` and a Start menu shortcut. The installer adds the installation directory to your user PATH; open a new terminal to run `byax` and `byax-mcp`. Uninstall removes that directory from the user PATH. It requires Windows 11 build 22000 or later. Current packages are unsigned development builds. Signing credentials are not configured, so these are not signed production releases. The explicit update workflow below is the distribution policy for development builds.

## Continuous integration

`.github/workflows/ci.yml` tests and freezes on Ubuntu 24.04 and a Windows Server 2022 x64 build runner, then prepares native installers. A Windows build runner is not evidence of physical Windows 11 acceptance. CI is configured but its remote execution results must be inspected after pushing; local test results do not establish remote CI success.

## Verification still required for release

Install the generated packages on clean Windows 11 and Ubuntu 24.04 desktops. Exercise file associations, PowerShell/UTF-8 CLI, native dialogs, clipboard, input methods, display scaling, accessibility services, physical printing, keyring access and microphone capture. Provider integrations require selected accounts or installed local executables and models. Record these results in the acceptance matrix rather than implying they were tested by offscreen unit tests.

## Cutting a release

The version lives in `src/byax/__init__.py` (`__version__`). `pyproject.toml`, `uv.lock` and the `MyAppVersion` define in `packaging/windows.iss` must carry the same value; the About dialog, `byax version` and the `.deb` name read it from source. `tests/test_release_metadata.py` fails when any copy disagrees or `CHANGELOG.md` has no `## [x.y.z]` entry.

1. Change `__version__`, `pyproject.toml` and `packaging/windows.iss`, run `uv lock` to refresh `uv.lock`, and add the changelog entry with its date and known limitations.
2. Run the CI gates locally (format, lint, mypy, tests with the coverage floor, `scripts/build.py --bundle-only`) and merge to `main` only when CI is green on both runners.
3. Tag the merge commit `vX.Y.Z` and push the tag. Create the GitHub Release from that tag, attach the installer and `.deb` from the CI artifacts of that commit, and copy the changelog entry into the release notes. Mark it as a pre-release while the installers remain unsigned and uninstalled-on-hardware (see below).

The `.deb` is written to `dist/byauthorx_<version>_amd64.deb` and the Windows installer to `dist/ByAuthorX-<version>-windows-x64-setup.exe`.

## Explicit update and rollback policy

Help → Download updates opens the official GitHub Releases page only when requested.
The application never polls for updates, downloads an installer or replaces itself in the background.
Choose the installer for the supported platform and retain the previous installer until the new version
has passed local acceptance. CI artifacts are development packages, not signed production releases.
There is no claim that signing, notarization or SmartScreen reputation has been established.

Before upgrading, save and keep a separate copy of working documents. Close the application, install the
selected version using the platform package manager, and reopen the copies. To roll back, close the new
version, reinstall the retained prior package and open a pre-upgrade document copy. Native migration
retains schema backups as documented in `architecture.md`; an older application refuses newer schemas
instead of silently downgrading them. Release signing remains blocked on separately provisioned signing
identities and a real Windows 11 / Ubuntu 24.04 installation audit.
