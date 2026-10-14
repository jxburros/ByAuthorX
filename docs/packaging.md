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
sudo apt install ./dist/byauthorx_0.1.0_amd64.deb
```

The package installs under `/opt/byauthorx`, provides `/usr/bin/byauthorx`, `/usr/bin/byax`, and `/usr/bin/byax-mcp`, and registers the desktop launcher and case-insensitive `.byax` MIME association. Do not distribute a package frozen on a newer glibc system as an Ubuntu 24.04 build.

Windows:

```powershell
iscc packaging/windows.iss
```

The installer registers `.byax` and a Start menu shortcut. The installer adds the installation directory to your user PATH; open a new terminal to run `byax` and `byax-mcp`. Uninstall removes that directory from the user PATH. It requires Windows 11 build 22000 or later. Current packages are unsigned development builds; signing and an update channel are not configured.

## Continuous integration

`.github/workflows/ci.yml` tests and freezes on Ubuntu 24.04 and a Windows Server 2022 x64 build runner, then prepares native installers. A Windows build runner is not evidence of physical Windows 11 acceptance. CI is configured but its remote execution results must be inspected after pushing; local test results do not establish remote CI success.

## Verification still required for release

Install the generated packages on clean Windows 11 and Ubuntu 24.04 desktops. Exercise file associations, PowerShell/UTF-8 CLI, native dialogs, clipboard, input methods, display scaling, accessibility services, physical printing, keyring access and microphone capture. Provider integrations require selected accounts or installed local executables and models. Record these results in the acceptance matrix rather than implying they were tested by offscreen unit tests.
