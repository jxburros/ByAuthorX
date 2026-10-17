# Distinctive `.byax` format ideas

Status: **ideas only, nothing here is built or scheduled**. Collected 17 October 2026 against version 0.4.0.

Goal: give authors reasons to choose ByAuthorX that Word cannot easily match. A Word page is a flat canvas that reflows differently on each machine. ByAuthorX already has named layers anchored to text, page masters, fields, a deterministic layout engine, and a per-span record of who wrote what. Ideas that need that foundation are more distinctive than ones Word could add as a menu item.

Each idea must survive export to Word, PDF and EPUB and import back. The export report already lists located losses, so for every idea decide whether it degrades gracefully (for example to a footnote in Word) or is unavailable in that format.

## Print-page ideas

1. **Spot-color and special-finish layers.** A layer tagged foil, spot UV, emboss or die-cut exports as its own separation in a print-ready PDF. It builds on the bleed and crop marks the compile pipeline already writes, and serves zines, covers, chapbooks and art books that now leave Word for InDesign.
2. **Pinned pagination (layout contracts).** Declare rules such as "this scene starts on a recto", "this poem never splits across pages" or "this chapter ends on a full page". Checks enforce them and the compile report proves them. Layout is deterministic, so this can be exact.
3. **Marginalia layer.** Side notes, glosses and a commentary track anchored to a sentence and kept aligned across page breaks. Suits poetry, scholarship, religious and legal texts, and annotated editions.
4. **Parallel text.** Two or more aligned columns, such as original and translation or script and stage directions, linked at sentence level so an edit on one side flags the other.
5. **Variable-data print.** A page template filled from a table of values, giving personalized letters, certificates, name cards, or a dedication with the reader's name. Builds on existing fields and the CLI.
6. **Print-safe links and QR codes.** A short code beside a readable URL. A companion link can point at the beta-reader viewer or an audio edition so print and screen stay in step.

## Ideas that use what the file already knows

7. **Provenance on the page.** An optional origin ribbon or margin tick, drawn from the typed, pasted and AI-text log, that prints or exports as a human-written certificate for a manuscript.
8. **Evidence strip for citations.** A footer or appendix showing each source, its version and its verified date, drawn from the citation audit.
9. **Reader-facing change history.** A published edition can include an "errata and revisions since printing" page generated from tracked changes and draft compare.
10. **Layered editions from one master.** One file yields large-print, dyslexia-friendly, screen and print editions with per-edition typography and the same text. The variants are rules, not copies.

## Wider ideas

11. **Executable fields.** Word counts, chapter timelines, character-appearance tables or pacing charts generated from the codex and the manuscript, placed on a page, and updated when the text changes.
12. **Sealed review copies.** A package carrying a signed hash of the text at a revision, so a reviewer or contest can verify they received the unmodified file. This is integrity, not secrecy: protection in ByAuthorX is not encryption.
13. **Audio and read-aloud anchors.** Link a paragraph to an audio take or a read-aloud marker, building on the dictation sources the file already keeps, as a step toward book and audiobook sync.

## Suggested focus

The strongest single theme is **print-grade layout that is deterministic and provable**: ideas 1, 2, 3 and 7 together (spot-finish layers, pinned pagination, marginalia and a provenance ribbon). They need the existing engine, are hard to copy in Word, and serve authors who self-publish print books. Ideas 4 and 5 are strong second choices for niche audiences such as translators and event stationery.
