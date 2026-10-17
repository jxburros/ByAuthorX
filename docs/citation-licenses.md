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
LGPLv3 or BSD; this application adopts the BSD option. RIS reading and writing is
implemented in the application (`src/byax/ris.py`). No citation or metadata
operation makes a network request on its own: the optional bibliography checks,
style downloads and Zotero reads described below run only when the person asks for
them.

## Additional CSL styles (user action)

Beyond the three bundled styles, a person can add any style from the
[CSL styles repository](https://github.com/citation-style-language/styles), either by
downloading it by name from
`https://raw.githubusercontent.com/citation-style-language/styles/master/<name>.csl`
(`byax bibliography --action styles-preview` shows the URL, size and SHA-256 first;
`styles-add` installs it and can pin the previewed hash) or by importing a local `.csl`
file. ByAuthorX does not bundle or redistribute these files: the person downloads
them to their own computer.

- **Licence and attribution.** Styles in the repository are licensed
  [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Each file
  names its authors and contributors and carries its licence in its `info` block;
  ByAuthorX never edits an installed style, so that attribution travels with it. The
  styles list (`byax bibliography --action styles-list`, Tools > Citation styles…) shows each
  installed style's licence URL and where it came from. A document that is rendered with such a
  style contains no part of the style file; the ShareAlike term applies to adaptations of the style
  itself.
- **Integrity.** The installed file's SHA-256 is recorded when it is added and checked
  every time it is used; an edited file is refused.
- **Limits.** The file must be an independent CSL 1.0 style and loadable by `citeproc-py`;
  dependent styles (which only name a parent) are refused with the parent's name. The
  file is parsed with `defusedxml` and must be at most 2 MiB.
- The style used in `tests/fixtures/csl/ieee.csl` is the repository's `ieee.csl`
  (CC BY-SA 3.0; authors Michael Berkowitz, Julian Onions, Rintze Zelle, Stephen Frank,
  Sebastian Karcher, Giuseppe Silano, Patrick O'Brien, Brenton M. Wiernik, Oliver Couch and
  others named in the file). It is included only as a test fixture.

## Outside registries (user action)

The bibliography health checks can ask Crossref, DataCite and OpenAlex about the DOIs of the
references and the Internet Archive's Wayback availability API about dead links. These are
independent services with their own terms; bibliographic metadata from Crossref and DataCite
is openly available, and OpenAlex data is CC0. Recorded answers from these services in
`tests/fixtures/bibliography` are small excerpts used as test data. Retraction Watch data is
not bundled: a person imports a CSV they downloaded themselves under the terms Retraction
Watch gives it.

The styles package adds approximately 15 MiB to the download. Keeping the
upstream style distribution preserves dependent styles and attribution and
avoids maintaining an incomplete hand-written imitation of the citation rules.
CSL rendering is limited to the processor's supported CSL features; it is not a
claim that every feature of every style is supported.
