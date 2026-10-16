# Grammar checking

ByAuthorX checks grammar on your computer with two tiers behind one interface. Nothing here uses a model, an account or the network unless you install the optional tier yourself.

| Tier | What it is | Always available | Coverage |
| --- | --- | --- | --- |
| **Built-in rules** | Data-driven regular-expression rules (`resources/grammar/<language>.json`, or an installed language pack) | Yes | Narrow: 85 English rules in 8 families, precision first |
| **LanguageTool** | The open-source LanguageTool server, running as a separate local process | No: you install it | Broad: thousands of rules, many languages |

Both tiers feed the existing findings pipeline (`byax check --suite grammar`, the Checks tab): each finding has a rule, family `grammar`, a severity, an exact range in Unicode scalar indices and, where there is a fix, a repair proposal that you accept one at a time. Language comes from the paragraph's `language` attribute, else the document language.

The Checks tab shows the state of both tiers honestly: `Built-in rules: on (85 English rules)` and `LanguageTool: not installed`, `stopped` or `running`. A check report that did not use LanguageTool carries a coverage entry saying so, so a "clean" grammar run is never read as broader coverage than it had.

## Decision record (competitiveness plan, open decision 2)

**Decision: support both tiers.** A small built-in rule set is always there, deterministic and cheap; LanguageTool is the opt-in high-coverage tier. Neither alone meets the plan: the built-in set is too narrow to compete with Grammarly-class checking, and a mandatory Java sidecar of this size contradicts a lightweight local-first desktop app.

Measured on 2026-10-16 on a 4-core, 16 GB Linux machine with OpenJDK 21 and LanguageTool 6.6 (the numbers are in `docs/benchmarks/0.3-grammar-*.json` and are reproduced by `scripts/grammar_benchmark.py`):

| Criterion | LanguageTool 6.6 as a sidecar | Built-in rules |
| --- | --- | --- |
| Licence | LGPL-2.1-or-later for LanguageTool itself; the distribution also carries libraries and data under Apache, BSD, CPL, CC-BY-SA and other terms (its own `third-party-licenses` folder). Fine as a separate process the user installs; **not bundled** with ByAuthorX | MIT (this repository) |
| Download | 251,998,221 bytes (240 MiB) zip, 386 MB unpacked, **plus** Java 17 or newer (not provided) | 38 KB of rule data inside the application |
| Start-up | JVM ready in 1.9 s (warm disk cache), first request 4.2 s | none |
| Memory | about 530 MB resident with `-Xmx1024m` | negligible |
| Per request | median 52 ms, 95th percentile 97 ms per sentence over loopback | about 49,000 words per second in process |
| 100,000-word document | not measured as a whole (about 1,800 paragraphs at the latency above) | full check 4.5 s; one edited paragraph 0.2 s with only that paragraph re-checked |
| Languages | dozens of language variants | English now; other languages as grammar packs |

Accuracy, from the labelled fixtures (next section). The fixture was written alongside the built-in rules, so its numbers flatter them; the open fixture is the better guide to breadth.

| Measure | Built-in | LanguageTool 6.6 |
| --- | --- | --- |
| Labelled fixture, recall (155 errors) | 91.6% | 69.7% |
| Labelled fixture, matches on 144 clean sentences | 2 | 3 |
| Open fixture, recall (39 ordinary errors not written for the rules) | 10.3% | 61.5% |

LanguageTool finds far more ordinary errors, but on this fixture its recall is lowest in the `usage` family (22%: it reports missing apostrophes and run-together words as spelling, which ByAuthorX hands to the spelling suite and drops from the LanguageTool results) and in the `punctuation` family (25%: comma splices are not among its rules). The two tiers are complementary, which is why the combined tier reports built-in findings first and drops any LanguageTool match that overlaps one.

## Accuracy: published per release

`tests/fixtures/grammar/en.txt` has 299 hand-written sentences: 155 labelled errors across eight families and 144 clean sentences (including near misses such as "Would he have done it?" and "It was a matter of course"). Each error is written `[[flagged text|rule]]`; a label `[[text|?family]]` marks a real error the rules are known not to catch. A finding is correct when its family and exact range match a label.

| Family | Rules | Precision | Recall | Missed (known gaps) |
| --- | --- | --- | --- | --- |
| confusion | 29 | 100% | 89.3% | 6 |
| agreement | 10 | 100% | 100% | 0 |
| verb_form | 7 | 100% | 94.1% | 1 |
| pronoun_case | 2 | 100% | 66.7% | 2 |
| quantity | 5 | 100% | 91.7% | 1 |
| punctuation | 4 | 77.8% | 87.5% | 1 |
| idiom | 26 | 100% | 96.3% | 1 |
| usage | 2 | 100% | 88.9% | 1 |
| **overall** | 85 | **98.6%** | **91.6%** | 13 |

The two false positives are classic deliberate asyndeton ("It was the best of times, it was the worst of times."), reported at the `broad` sensitivity level only. Thresholds per family live in `tests/fixtures/grammar/thresholds.json` and `tests/test_grammar.py` fails if a rule change drops below them or if these published numbers differ from a fresh measurement:

```
uv run python scripts/grammar_benchmark.py --write                                  # built-in numbers
uv run python scripts/grammar_benchmark.py --write --languagetool <LanguageTool folder>   # also LanguageTool
```

Caveats, stated plainly: the fixture is small and curated, the rules were developed with it in view, and LanguageTool is scored on overlap with labels (every match elsewhere is counted against it, although some are real catches of errors the fixture does not mark). These numbers show where each family stands, not performance on a novel. `tests/fixtures/grammar/en-open.txt` exists so the narrowness of the built-in tier is measured and published rather than assumed.

## Built-in rules

Rules are data. A rule has an `id`, a `group` (its family), a `level` (`conservative`, `balanced` or `broad`, matching the sensitivity of a checking profile), a `pattern` (regular expression, case-insensitive with `"flags": "i"`), a `message`, an optional `highlight` group (the range to report), `replacements` (templates, `\1` allowed) or a `replacement_map`, and `skip_dialogue` for dialect-prone patterns. Patterns are compiled with the `regex` module and run with a time limit; a file with a pattern that does not compile or that backtracks badly on probe text is refused (at install for packs, with a coverage note at load for built-in data).

Precision first: a rule fires only where the flagged form is wrong ("should of", "they was", "less people") and stays quiet on its correct neighbours ("its time", "would of course", "fewer water bottles"). Agreement and verb-form rules do not fire inside quoted speech, where dialect is the author's choice; confusions do.

Spec 12.1 already covers double spaces, spacing before punctuation, a missing space after a comma, repeated punctuation, sentence capitals, a/an and unbalanced pairs (`grammar.*`), and doubled words (`word.repeated`). The built-in rules do not repeat them, and LanguageTool's duplicates of them (`WHITESPACE_RULE`, `EN_A_VS_AN`, `ENGLISH_WORD_REPEAT_RULE` and the like) are dropped. Spelling matches from LanguageTool are dropped by default because the spelling suite owns them (`grammar --action configure --include-spelling` keeps them).

`byax grammar --action rules` lists every rule; the families are:

| Family | Examples |
| --- | --- |
| confusion (29) | should of; its / it's; your / you're; their / there / they're; then / than; to / too; loose / lose; who's / whose; affect / effect; weather / whether; lead / led; advice / advise; breath / breathe |
| agreement (10) | he don't; he have; they was; I is; everyone are; there is many |
| verb_form (7) | has went (every common irregular verb, with the participle as the repair); I seen; didn't used to; suppose to; can't hardly; don't have no (broad) |
| pronoun_case (2) | between you and I; Me and him went |
| quantity (5) | less people; fewer water; amount of people; much people; many information |
| punctuation (4) | comma splices (after "however", between pronoun clauses at broad); "; however we"; lowercase "i" |
| idiom (26) | could care less; for all intensive purposes; wreck havoc; free reign; peaked my interest; baited breath; case and point; tow the line |
| usage (2) | alot, everytime, noone and other run-together words; dont, cant, thats and other missing apostrophes |

A checking profile's sensitivity selects the level (`conservative` runs none of the `balanced` or `broad` rules). `builtin.disabled_rules` in the grammar settings switches individual rules off.

## LanguageTool

LanguageTool is Java software under the LGPL. ByAuthorX never bundles it and never links against it: it is a separate program you install, and ByAuthorX talks to it over HTTP on `127.0.0.1` only (a non-loopback address is refused, and the client ignores proxy settings). Document text is sent nowhere else.

Three ways to use it:

1. **You already run a server.** `byax grammar --action configure --port 8081`, then `--action enable`.
2. **You have the folder.** `byax grammar --action configure --languagetool-dir <folder with languagetool-server.jar>`, then `--action enable` and `--action start`. ByAuthorX starts `java -cp languagetool-server.jar org.languagetool.server.HTTPServer --port N` from that folder with its output in a log file in your data folder, and `--action stop` stops a server it started (it will not signal a process it did not start). It is not started automatically unless you set `--autostart`. ByAuthorX does not pass `--allow-origin`: nothing here needs browser cross-origin access.
3. **Let ByAuthorX download the official release.** `byax grammar --action install-languagetool` prints the plan and downloads nothing. Repeat it with `--sha256 <the digest shown>` to confirm:

   | | |
   | --- | --- |
   | Source | `https://languagetool.org/download/LanguageTool-6.6.zip` |
   | Size | 251,998,221 bytes |
   | SHA-256 | `53600506b399bb5ffe1e4c8dec794fd378212f14aaf38ccef9b6f89314d11631` |
   | Licence | LGPL-2.1-or-later |
   | Needs | Java 17 or newer |

   A successful install records the folder and turns the tier on (start the server with `--action start`). The download is checked for size and SHA-256 before a single file is unpacked, extraction refuses unsafe member names, and the result is stored under the user data folder. Java is not installed for you. The desktop shows the same plan in a confirmation dialog (Tools > Languages… > Grammar).

LanguageTool 6.6 is the last release published as a zip; later versions are daily snapshots. The pinned version and digest are in `byax.grammar.LANGUAGETOOL`. The site publishes no checksum for the file: the digest above was computed from a direct download on 2026-10-16 (twice, identical) and should be re-verified by the maintainer against an independent download before a release.

Offsets are converted correctly: LanguageTool counts UTF-16 code units, ByAuthorX uses Unicode scalar indices, and an emoji earlier in a paragraph shifts them. `tests/test_grammar.py` covers astral characters against a fake server and, when `BYAX_LANGUAGETOOL_URL` points at a real local server (`pytest -m languagetool`), against LanguageTool itself.

LanguageTool findings use the rule `grammar.languagetool.<LT rule id>`, family `grammar`, severity `advisory`, `measured.engine = languagetool`, its category and issue type, the first replacement as the repair proposal and further replacements under `measured.alternatives`.

## Incremental checks and caching

Grammar is evaluated per paragraph. A paragraph's dependency fingerprint includes the state of the tiers (built-in rule version and pack, and for LanguageTool whether it is running and its version), so editing one paragraph re-runs only that paragraph, and starting or stopping LanguageTool re-runs everything once.

## Interfaces

| Interface | How |
| --- | --- |
| CLI | `byax grammar` (status), `--action rules`, `--action check --text "…" [--language en] [--engine builtin\|languagetool\|all]`, and the setup actions above; `byax check --suite grammar` for documents |
| MCP / REST | `status`, `rules` and `check` only. Configuring, enabling, installing, starting and stopping LanguageTool are refused with `unsupported_feature` (they download files and start processes) |
| Desktop | Checks tab: tier status and "Set up grammar tiers…"; Tools > Languages… > Grammar |

Settings live in `grammar.json` in the user config folder (`BYAX_GRAMMAR_DIR` overrides the location for tests and portable setups); the downloaded release, the server log and the process record live in the user data folder.

## Other languages

A grammar pack for another language is a data file in the same rule format inside a language pack (see [translations and language packs](translations.md)). It is validated at install, including the runaway-pattern probe. No other language ships rules today, and LanguageTool covers them when it is running.
