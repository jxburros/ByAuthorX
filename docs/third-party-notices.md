# Third-party dependencies

ByAuthorX's original source is MIT licensed. Third-party packages retain their own licenses. The locked dependency inventory is in `uv.lock` and distributions include available license files under `third-party-licenses`.

Qt and PySide6 components use LGPLv3/GPLv3/commercial licensing as applicable. This project uses the LGPL-capable native Widgets, Gui, Core, PrintSupport, and Multimedia modules through dynamically loaded libraries. Preserve the LGPL license notices and recipients' ability to replace/relink those libraries when redistributing the frozen directory. Qt source and corresponding source access are available from [Qt](https://download.qt.io/official_releases/qt/) and [Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/). Do not add GPL-only modules without reviewing the resulting distribution terms.

Python and its standard library retain the PSF license. Other direct dependencies include httpx (BSD), keyring (MIT), platformdirs (MIT), regex (Apache/CNRI terms), markdown-it-py (MIT), python-docx (MIT), odfpy (Apache/GPL dual licensed), Pillow (HPND), striprtf (BSD), defusedxml (PSF), pyspellchecker (MIT), and RapidFuzz (MIT, Copyright © 2020-present Max Bachmann and © 2011 Adam Cohen; verified from the project's `LICENSE` and PyPI metadata for 3.14.6), which supplies typo suggestions for library search and near-match quote alignment in `quote_match.py`. The license text shipped by the actual pinned version controls.

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
EPUBCheck 5.3.0 (W3C, BSD-3-Clause) is used only by the optional `epubcheck` CI job to validate exported EPUB fixtures. It is downloaded in CI from its release page, verified against the SHA-256 pinned in `.github/workflows/ci.yml`, and is not bundled with, imported by or redistributed with ByAuthorX. It needs a Java runtime, which the CI job installs; the application does not.

LanguageTool (LGPL-2.1-or-later, https://languagetool.org) is an **optional component the user installs**. ByAuthorX does not bundle, link, import or redistribute it: it runs as a separate Java process that the user installed, or that the user explicitly asked ByAuthorX to download, and ByAuthorX talks to it over HTTP on the loopback address. The only version ByAuthorX offers to download is 6.6, from `https://languagetool.org/download/LanguageTool-6.6.zip` (251,998,221 bytes, SHA-256 `53600506b399bb5ffe1e4c8dec794fd378212f14aaf38ccef9b6f89314d11631`); the download is shown with this source, size and checksum before it starts and verified before it is unpacked. The release carries its own `COPYING.txt` and a `third-party-licenses` folder (Apache, BSD, CPL, CC-BY-SA and other terms for the libraries and language data it contains); those apply to the installed copy, not to ByAuthorX. It needs a Java runtime (17 or newer), which ByAuthorX does not provide. The `grammar_benchmark.py` script and the `languagetool`-marked tests use a locally installed copy only when one is configured.

The built-in grammar rules (`src/byax/resources/grammar`) and the interface-string catalogue are original ByAuthorX data under the project's MIT license. Language packs a user installs keep their own licence and source, which each pack's manifest records and `byax languages --action list --verbose` shows.

The codex test fixture `tests/fixtures/codex/pride-and-prejudice-1-6.json` holds chapters I–VI of Jane Austen's *Pride and Prejudice* (public domain), taken from the Project Gutenberg text of eBook #1342 with the Project Gutenberg header, licence and trademark removed; `scripts/build_codex_fixture.py` records the source file's SHA-256 and the processing. The optional `pyahocorasick` package (BSD-3-Clause) is used for codex mention detection only when a user has installed it; it is not a dependency and is not bundled.

## On-device search (context engine Phase 3)

Licences below were read from the primary sources on 16 October 2026; the licence text shipped by the pinned version controls.

Runtime and optional extras (not part of the core install):

| Component | Use | Licence | Where it is verified |
| --- | --- | --- | --- |
| onnxruntime 1.31 | `byauthorx[local-models]`: runs the embedder and rerankers on the CPU; frozen into both installer variants | MIT | PyPI metadata and the project's `LICENSE` |
| tokenizers 0.23 | `byauthorx[local-models]`: model tokenizers | Apache-2.0 | PyPI classifiers and the project's `LICENSE` |
| numpy | installed by onnxruntime; speeds exact vector search | BSD-3-Clause (the wheel also records 0BSD, MIT, Zlib and CC0 components) | PyPI metadata |
| sqlite-vec 0.1.9 | `byauthorx[sqlite-vec]`: optional vector backend (binary quantization with float re-scoring) | MIT or Apache-2.0 (dual), `LICENSE-MIT` and `LICENSE-APACHE` in the repository | github.com/asg017/sqlite-vec |
| Docling 2.140 | `byauthorx[docling]`: optional PDF engine, run only in the parser subprocess; never bundled in installers | MIT | github.com/docling-project/docling `LICENSE` and PyPI |

The Docling extra pulls PyTorch. On Linux the default PyTorch wheels depend on NVIDIA CUDA runtime packages under NVIDIA's own licence terms; these are installed by the user's package manager, never redistributed by ByAuthorX. Install a CPU build of PyTorch first to avoid them. No GPL or AGPL package was found in the Docling dependency tree on Linux (checked from installed package metadata). **PyMuPDF is deliberately not used**: its AGPL licence conflicts with the project's MIT licence, and pypdf (BSD-3-Clause) stays the default PDF reader.

Models (downloaded on request or bundled in the standard installers; catalogue in `src/byax/resources/models.json`; each model's card is saved next to its files as `MODEL-CARD.md` and the Apache-2.0 text in `models/licences`):

| Model | Licence | Source of the licence statement |
| --- | --- | --- |
| Qwen3-Embedding-0.6B, int8 ONNX export of onnx-community, 512 dimensions by Matryoshka truncation | Apache-2.0 | `license: apache-2.0` in the model card of `Qwen/Qwen3-Embedding-0.6B` (the export repository carries no licence of its own and names the model as `base_model`) |
| bge-reranker-v2-m3, int8 ONNX export of onnx-community | Apache-2.0 | `license: apache-2.0` in the model card of `BAAI/bge-reranker-v2-m3` |
| answerai-colbert-small-v1 (the repository's `vespa_colbert.onnx`, which includes the projection layer) | Apache-2.0 | `license: apache-2.0` in the model card of `answerdotai/answerai-colbert-small-v1` |
| docling-layout-heron | Apache-2.0 | `license: apache-2.0` in the model card of `docling-project/docling-layout-heron` |
| HHEM-2.1-Open (Vectara), the project's int8 ONNX build (`hhem-2.1-open-int8`; weights of `vectara/hallucination_evaluation_model` at `de9a5e6b…`, FLAN-T5-base tokenizer) | Apache-2.0 | `license: apache-2.0` in the model card front matter of `vectara/hallucination_evaluation_model` and of its base model `google/flan-t5-base`, read 17 October 2026. The upstream repository has no separate LICENSE file. Both cards are saved next to the model (`MODEL-CARD.md`, `BASE-MODEL-CARD.md`) |

Downloading a model is not a licence to anything beyond these terms; weights are not covered by the repository's MIT licence. Qwen3-Reranker has not passed the dependency gate and is not in the catalogue. Bespoke-MiniCheck is excluded (non-commercial licence). HHEM-2.1-Open passed the licence gate; it is a separate download and is not bundled (size and measured agreement, see the [library guide](library-and-citations-guide.md#support-check-stage-2)). The HHEM export is built with PyTorch, `transformers`, `onnx` and `onnxscript` in a scratch environment by the maintainer (`scripts/export_hhem_onnx.py`); none of them is a dependency of ByAuthorX or shipped with it, and the run-time path uses only `onnxruntime` and `tokenizers`.

Playwright for Python (Apache-2.0, https://github.com/microsoft/playwright-python) and its dependencies pyee (MIT) and greenlet (MIT and PSF-2.0) are **development and test dependencies only** (the `dev` extra). `tests/test_review_html.py` drives the self-contained HTML review copy in a locally installed Chromium and is skipped when the package or a browser is missing. They are not bundled in the application or its installers, and no browser is downloaded by ByAuthorX.

Crossref, DataCite, OpenAlex, the Internet Archive (Wayback Machine), Zotero and the Citation Style Language repository are independent services and software that ByAuthorX talks to only when a person asks it to; none is bundled or required, and no new Python dependency was added for them (HTTP uses httpx, fuzzy matching RapidFuzz, XML defusedxml). `tests/fixtures/csl/ieee.csl` (CC BY-SA 3.0, authors named in the file) and the recorded registry answers in `tests/fixtures/bibliography` are test data only; see [citation-licenses.md](citation-licenses.md). Retraction Watch data is never bundled: a person imports a CSV they downloaded.
