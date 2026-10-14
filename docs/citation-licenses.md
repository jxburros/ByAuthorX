# Citation processing dependencies

Bibliographic rendering runs locally using `citeproc-py` (BSD-2-Clause-Views).
The `citeproc-py-styles` Python package is MIT licensed; the bundled Citation
Style Language styles are licensed CC BY-SA 3.0. The application selects APA,
Modern Language Association and Chicago note/bibliography styles without
modifying them. Style authors, attribution and license links remain in the
installed CSL files, available through `citeproc_styles.get_style_filepath`.

- [citeproc-py](https://github.com/brechtm/citeproc-py)
- [citeproc-py-styles](https://github.com/citation-style-language/styles-distribution)
- [Citation Style Language styles and license](https://github.com/citation-style-language/styles)
- [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/)

BibTeX and Better BibTeX file parsing uses `bibtexparser` 1.x, dual licensed
LGPLv3 or BSD; this application adopts the BSD option. RIS parsing is implemented
in the application. No citation or metadata operation makes network requests.

The styles package adds approximately 15 MiB to the download. Keeping the
upstream style distribution preserves dependent styles and attribution and
avoids maintaining an incomplete hand-written imitation of the citation rules.
CSL rendering is limited to the processor's supported CSL features; it is not a
claim that every feature of every style is supported.
