# Third-party dependencies

ByAuthorX's original source is MIT licensed. Third-party packages retain their own licenses. The locked dependency inventory is in `uv.lock` and distributions include available license files under `third-party-licenses`.

Qt and PySide6 components use LGPLv3/GPLv3/commercial licensing as applicable. This project uses the LGPL-capable native Widgets, Gui, Core, PrintSupport, and Multimedia modules through dynamically loaded libraries. Preserve the LGPL license notices and recipients' ability to replace/relink those libraries when redistributing the frozen directory. Qt source and corresponding source access are available from [Qt](https://download.qt.io/official_releases/qt/) and [Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/). Do not add GPL-only modules without reviewing the resulting distribution terms.

Python and its standard library retain the PSF license. Other direct dependencies include httpx (BSD), keyring (MIT), platformdirs (MIT), regex (Apache/CNRI terms), markdown-it-py (MIT), python-docx (MIT), odfpy (Apache/GPL dual licensed), Pillow (HPND), striprtf (BSD), defusedxml (PSF), pyspellchecker (MIT), and RapidFuzz (MIT, copyright Max Bachmann and Adam Cohen; verified against the project's LICENSE file and the PyPI licence expression; used by `quote_match.py` for near-match quote alignment). The license text shipped by the actual pinned version controls.

OpenAI, Anthropic, Gemini, Ollama, ElevenLabs, whisper.cpp and LibreOffice are independent integrations. API accounts, downloaded model weights, and locally installed converter executables are not included or licensed by this repository. No provider subscription is required for local editing.

Baseline DejaVu Sans, Serif and Sans Mono fonts are bundled under the Bitstream Vera license (DejaVu changes are public domain). Their unmodified font files and complete notices are in `src/byax/resources/fonts`. These licenses permit embedding and distribution with the application.

The supplied Manrope (Regular and Semibold, the interface fallback), IM FELL English (Regular), and Source Serif 4 (Regular) fonts use the SIL Open Font License 1.1. Unmodified binaries and each family's full license notice are bundled in `src/byax/resources/fonts` and collected into frozen distributions. The ByAuthorX graphics and Vixl masters were supplied by the product owner for this integration; the font licenses apply to the fonts, not to the logo artwork. No Vixl runtime or third-party animation service is bundled.

The six brand fonts (Irongall, Scholium, Tenuis, Brobdingnag, Onionskin and Decussate) are original ByAuthorX designs under the project's MIT license, recorded in each font's name table. They are bundled in `src/byax/resources/fonts` beside the fonts above, and their sources and build are in `assets/brand-fonts`.
