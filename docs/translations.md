# Language packs and translations

Dictionaries, grammar rules, word lists and interface strings are data. A **language pack** bundles any of them for one language, with a version, a licence, a source and a SHA-256 for every file. English ships inside the application; anything else is a pack you install yourself. Installing never uses the network unless you give an explicit URL together with the expected hash.

## Translation coverage

Coverage is the share of the interface texts that have a usable translation. The catalogue is generated from the literal `tr()` texts in the desktop code (`scripts/extract_strings.py`, stored in `src/byax/resources/catalog/strings.json`): 1353 interface texts at the time of writing. A translation counts only if it is not empty and keeps the source text's placeholders (`{0}`, `%1`), because a translation that loses one would break the sentence it is inserted into. English is the source language.

| Language | Coverage | Notes |
| --- | --- | --- |
| English (en) | 100% | Source language |
| every other language | 0% | No interface translation ships. Nothing is claimed for languages without a strings pack |

56 call sites pass a variable to `tr()` (menu tables, for instance) instead of a literal. They are translated at run time when the text is in the table, but they are not in the catalogue, so the percentages above ignore them. `byax languages --action coverage` prints the same numbers for the packs installed on your computer, and the Tools > Languages… dialog shows them next to the interface-language choice.

What is not yet verified: right-to-left layout of the interface, plural forms (Qt's `n` argument is ignored, so strings with counts need separate wording), and fonts for scripts outside the bundled fonts. Document text in other languages is unaffected by the interface language and already shapes through Qt.

## Choosing an interface language

Install a pack that contains a strings file (`byax languages --action install --pack-file …`), open Tools > Languages… > Interface language, choose it and restart ByAuthorX. The choice is stored as `interface/language` in the application settings; English is the default and needs no pack. Texts missing from the table stay in English.

Under the hood the application installs one `QTranslator` that looks a text up in the pack's string table (first by Qt context, then by source text). This sits on top of the existing `tr()` hooks, so no widget code changed.

## What a pack contains

A pack is a folder or a `.byaxpack` ZIP with a `manifest.json` and the files it lists:

```json
{
  "format": 1,
  "id": "de-core",
  "name": "German",
  "language": "de",
  "version": "1.0.0",
  "licence": "CC-BY-4.0",
  "source": "https://example.org/where-the-data-came-from",
  "description": "Spelling dictionary and interface strings",
  "files": [
    {"path": "spelling/de.json.gz", "kind": "spelling", "sha256": "…", "size": 123456},
    {"path": "strings/de.json", "kind": "strings", "sha256": "…", "size": 45678}
  ]
}
```

| `kind` | File | Used by |
| --- | --- | --- |
| `spelling` | `{"word": frequency, …}` as JSON or gzipped JSON (the pyspellchecker format) | the spelling suite, spelling suggestions |
| `grammar` | rules in the format described in [grammar](grammar.md) | the built-in grammar tier |
| `fiction` | word lists like `resources/fiction/en.json` | fiction checks |
| `readability` | word lists like `resources/readability/en.json` | readability reports |
| `strings` | `{"language": "de", "strings": {"Save": "Speichern"}, "contexts": {"ChecksPanel": {…}}}` | the interface |

`byax.language_packs.build_manifest(folder, …)` writes the manifest with the hashes and `pack_archive(folder, out)` creates a deterministic `.byaxpack`. A pack may offer each kind once; an id is lowercase letters, digits, `.`, `_` and `-`; versions are dotted numbers.

## Trust and verification

- **Install** checks the manifest, rejects unsafe paths, unlisted files, oversized files and wrong checksums, validates the content of each kind (grammar rules compile and pass the runaway-pattern probe; spelling maps words to positive counts) and only then moves the pack into place with an atomic rename. An archive can additionally be pinned with `--sha256`; installing from a URL requires it and refuses plain `http` (except loopback).
- **Use** re-checks the SHA-256 of a file before it is read. A file that no longer matches is ignored (the built-in data, if any, is used instead) and `byax languages --action verify` says which file differs.
- **Precedence**: the newest installed version of a pack for a language and kind wins over the built-in data; removing it restores the built-in data. Installing an equal or older version needs `--replace`.
- A pack is data, never code. Built-in packs (`builtin-en`, and one spelling pack per language that pyspellchecker ships) cannot be removed.
- MCP and REST can list, verify and report coverage; installing and removing are local actions.

Installed packs live under the user data folder (`BYAX_LANGUAGES_DIR` overrides it): `…/ByAuthorX/languages/<pack id>/`.

## Commands

```
byax languages                                   # list built-in and installed packs
byax languages --action list --verbose           # with each file's SHA-256 and size
byax languages --action install --pack-file de-core.byaxpack [--sha256 …] [--replace]
byax languages --action install --url https://… --sha256 …   # explicit network install
byax languages --action verify [--id de-core]
byax languages --action remove --id de-core
byax languages --action coverage [--language de]
```

## Making a translation

1. Run `uv run python scripts/extract_strings.py --write` and take `src/byax/resources/catalog/strings.json` as the list of source texts.
2. Write `strings/<code>.json` mapping each source text to its translation, keeping placeholders such as `{0}` unchanged.
3. Put it in a pack folder, run `build_manifest` and `pack_archive`, install the archive and check `byax languages --action coverage`.
