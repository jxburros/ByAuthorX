# Third-party dependencies

ByAuthorX's original source is MIT licensed. Third-party packages retain their own licenses. The locked dependency inventory is in `uv.lock` and distributions include available license files under `third-party-licenses`.

Qt and PySide6 components use LGPLv3/GPLv3/commercial licensing as applicable. This project uses the LGPL-capable native Widgets, Gui, Core, PrintSupport, and Multimedia modules through dynamically loaded libraries. Preserve the LGPL license notices and recipients' ability to replace/relink those libraries when redistributing the frozen directory. Qt source and corresponding source access are available from [Qt](https://download.qt.io/official_releases/qt/) and [Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/). Do not add GPL-only modules without reviewing the resulting distribution terms.

Python and its standard library retain the PSF license. Other direct dependencies include httpx (BSD), keyring (MIT), platformdirs (MIT), regex (Apache/CNRI terms), markdown-it-py (MIT), python-docx (MIT), odfpy (Apache/GPL dual licensed), Pillow (HPND), striprtf (BSD), defusedxml (PSF), and pyspellchecker (MIT). The license text shipped by the actual pinned version controls.

OpenAI, Anthropic, Gemini, Ollama, ElevenLabs, whisper.cpp and LibreOffice are independent integrations. API accounts, downloaded model weights, and locally installed converter executables are not included or licensed by this repository. No provider subscription is required for local editing.

Baseline DejaVu Sans, Serif and Sans Mono fonts are bundled under the Bitstream Vera license (DejaVu changes are public domain). Their unmodified font files and complete notices are in `src/byax/resources/fonts`. These licenses permit embedding and distribution with the application.

The supplied Manrope (Regular and Semibold, the interface fallback), IM FELL English (Regular), and Source Serif 4 (Regular) fonts use the SIL Open Font License 1.1. Unmodified binaries and each family's full license notice are bundled in `src/byax/resources/fonts` and collected into frozen distributions. The ByAuthorX graphics and Vixl masters were supplied by the product owner for this integration; the font licenses apply to the fonts, not to the logo artwork. No Vixl runtime or third-party animation service is bundled.

The six brand fonts (Irongall, Scholium, Tenuis, Brobdingnag, Onionskin and Decussate) are original ByAuthorX designs under the project's MIT license, recorded in each font's name table. They are bundled in `src/byax/resources/fonts` beside the fonts above, and their sources and build are in `assets/brand-fonts`.

## Evaluation fixture data

The retrieval evaluation fixtures under `tests/fixtures/eval/` (see [benchmarks](benchmarks/README.md)) are test data, not application code. They are **not** covered by the repository's MIT licence and are not installed with the application or included in the wheel. `tests/fixtures/eval/MANIFEST.json` records the source URL, licence, attribution and SHA-256 of every file.

| Library | Source | Licence |
| --- | --- | --- |
| `novel`, `series`, `humanities` | Project Gutenberg #1342 (Jane Austen, *Pride and Prejudice*), #1661 and #834 (Arthur Conan Doyle, *The Adventures* and *The Memoirs of Sherlock Holmes*, one file per story) and #731 (Edward Gibbon, *The History of the Decline and Fall of the Roman Empire*, vol. 1, with notes by H. H. Milman) | Public domain in the United States. The Project Gutenberg header, footer and trademark licence text were removed, as the Project Gutenberg licence permits for public-domain works, so the files carry no Project Gutenberg trademark. |
| `novel` drafts | Two hand-made distractor files derived from chapters 1–6 of the text above by `scripts/build_eval_corpus.py` (renamed minor characters, changed details) | Derived from public-domain text; the edits are dedicated to the public domain |
| `webclips` | 38 English Wikipedia articles at pinned revisions (permalinks in the manifest), as served with page chrome; only scripts, styles, comments and presentation attributes were removed | CC BY-SA 4.0. Attribution: Wikipedia contributors (each article's history lists the authors; the title, revision and permalink are in the manifest). **Share-alike:** these files stay under CC BY-SA 4.0 and are not relicensed. |
| `papers` | 30 *PLOS Computational Biology* and *PLOS ONE* articles (PDF and JATS XML), pinned by URL and SHA-256 in the manifest. Three are committed: the remaining 27 are fetched on request by `scripts/fetch_eval_fixtures.py` into the user cache and never committed | CC BY 4.0; the licence URL is read from each article's XML. Committed: Friedberg, Wass, Mooney et al., "Ten Simple Rules for a Community Computational Challenge", *PLOS Comput Biol* (2015), doi:10.1371/journal.pcbi.1004150; Kass, Caffo, Davidian et al., "Ten Simple Rules for Effective Statistical Practice", *PLOS Comput Biol* (2016), doi:10.1371/journal.pcbi.1004961; Malone, Stevens, Jupp et al., "Ten Simple Rules for Selecting a Bio-ontology", *PLOS Comput Biol* (2016), doi:10.1371/journal.pcbi.1004743 |
| `scale` | Synthetic text generated by `scripts/generate_fixtures.py --eval-scale` | Generated; not committed |

The golden queries (`queries.jsonl`) quote short passages of the texts above for evaluation and are distributed under the licence of the text they quote.
