# User guide

ByAuthorX is a native desktop word processor. You write in a Qt editor, save editable `.byax`
documents, and export through the same typography engine used for the paginated preview. The
document stays yours: nothing is uploaded, no account is needed and there is no telemetry.
AI is opt-in, not opt-out. No AI feature runs until you configure a provider and choose to use it,
and you choose the provider yourself. It can be a hosted service with your own key or a local model on your own
computer (see [providers](providers.md)).

## Install

Installers are attached to each [GitHub release](https://github.com/jxburros/ByAuthorX/releases):
a Windows 11 x64 setup program and an Ubuntu 24.04 x64 `.deb`. They are unsigned, so Windows
SmartScreen warns on first run. Ubuntu: `sudo apt install ./byauthorx_<version>_amd64.deb`.
To run from source see the [README](../README.md#run-from-source). **Help → Download updates**
only opens the releases page; the app never updates itself.

## The window

- **Writing view** – continuous editing. **Pages** shows the paginated preview that print and PDF use.
- **Ribbon** – the logo beside two rows of tools. Each file view has its own ribbon with only
  what that format supports: the Markdown ribbon has no underline or font size, plain text keeps
  find, check, view and export, and the full ByAuthorX view carries every tool plus track
  changes, versions and protection. Related commands share one dropdown (**Text**, alignment,
  lists, **Insert**, versions). **Export** in the ByAuthorX view asks for a format; in any other
  view it exports that view's format, with the other formats on its arrow.
- **Side panels** – the panel title is the selector: Outline or Inspector, **Layout** (layers,
  page setup, page masters, sections), Bookmarks, Writing statistics, Personal scratchpad,
  Library. Layout follows whichever side selects it. Panel choices and the scratchpad are local
  settings, not saved in the document.
- **Inspector** – **Proposed changes** (pending edits, comments and findings, and **Run all
  active checks**), **Checks** (every check, marked active or inactive), **AI** (Work and Chat)
  and **Configure** (API keys, information repositories, profiles, export and appearance).
- **Menus** – long menus group related commands in submenus (Format → Text, Paragraph, Lists,
  Styles, Layers; Review → Protection, Versions and drafts) and scroll rather than split into
  columns.
- **Status bar** – counters and file-format limits.
- **View → Theme** (System/Light/Dark), **Reduce motion**, **Hand-drawn marks**.

## Files and formats

| You want to | Do this |
| --- | --- |
| Keep a master you can edit later | Save as `.byax` (identities, assets, review, history are kept) |
| Open Word, ODT, RTF, HTML, EPUB, TXT | File → Open. These are imports; a compatibility report lists anything lost |
| Edit Markdown directly | Markdown opens as an import by default; the first-open chooser or Tools → Markdown opening preference keeps `.md` as the working file |
| Share a PDF or Word copy | File → Export. Exporting never replaces the master |
| Publish an e-book | File → Export → EPUB. See [Exporting an EPUB](#exporting-an-epub) |
| See how a format will look | Toolbar **File view** selector (PDF, Word, OpenDocument, EPUB, RTF, HTML, Markdown, plain text). Word/ODT/RTF previews are approximate. The source toggle shows exact read-only export text |

Hidden or unsupported content stays in the master; objects simplified in a view are locked there.
Details: [file views](file-views.md) and [compatibility](compatibility.md).

### Exporting an EPUB

**File → Export → EPUB** (or `byax export draft.byax --out draft.epub`) writes a reflowable EPUB 3. The
same document and options always give the same bytes; nothing reads the clock.

- **Chapters and contents.** The book splits into one file per level-1 heading, and the contents page
  (and the older NCX contents) is built from the headings of every level. A document with no headings
  gets a single contents entry named after the title.
- **Metadata.** Title, author and language come from the document's metadata. Optional metadata keys
  `publisher`, `description`, `rights`, `subjects` (a list), `identifier` and `modified` are written
  when present. The book identifier is derived from the document's identity unless you set
  `identifier`; the modification date comes from `modified`, else from the document history, else a
  fixed date, so exporting twice never changes the file.
- **Cover.** Pick the image in Tools → Document properties (**EPUB cover image**), or set the document
  metadata `cover` to the key of an image asset, or mark one image block
  with the `cover` attribute (it is then used as the cover and not repeated in the text). A cover is
  never chosen implicitly.
- **Fonts.** Fonts used by the text are embedded only when the font's own embedding permission allows
  it; any other font is listed in the export report and the reader substitutes one.
- **Footnotes** that are attached to a paragraph become EPUB 3 footnotes (pop-ups in most readers).
- **Accessibility.** The package declares access modes, accessibility features, hazards and a
  summary. Images without alt text are reported in the export report (mark an image `decorative` when
  it carries no information); this is a report, not a block.
- **Options.** Chapter split points, an extra stylesheet, the cover, a language override and which
  blocks to include are given in an export profile's `epub` section (`split_at`, `split_level`,
  `css`, `cover`, `include`, `language`, `embed_fonts`, `ncx`, `accessibility`), or through the
  `byax.epub.export_epub` library call.

The export report lists every loss with its location. Page breaks, the contents block, section
breaks, links that leave the book and unsupported image types are not carried. The built-in structural
check runs on every export; EPUBCheck can be run on the fixtures with `scripts/epubcheck_fixtures.py`
(see the [development guide](development.md#epubcheck)).

Autosave writes recovery copies; an explicit **Save** draws a brief checkmark. If the file changes
on disk behind you, the app tells you rather than overwriting.

## Working in synced folders

Dropbox, iCloud Drive, OneDrive, Google Drive, Box, Nextcloud/ownCloud, Syncthing and pCloud folders
work, with these limits. ByAuthorX does not co-edit live ([the decision](coediting-spike.md) stands):
the safe pattern is one computer at a time, and a merge you review if two computers did edit.

- **The editing lock is per computer.** It lives in this computer's cache folder, never beside the
  document, so it cannot stop another computer from editing the synced file. Close the document and let
  sync finish before switching computers.
- **Saves are atomic and checked.** A save writes a temporary file and swaps it in. If your sync client
  delivered a newer version of the file after you opened it, the save is refused and nothing is
  overwritten; use Save as to keep both. A half-downloaded, empty or online-only file is reported as
  such rather than opened.
- **Notice bar.** Opening a document in a synced folder with something risky (a temporary save file
  left behind, a backup copy beside it, an unfinished download) or with conflicted copies shows a
  dismissible bar. **Details…** explains it; **Tools → Synced folder safety…** shows it any time.
- **Conflicted copies.** When two computers edit the same file, your sync client keeps both and names
  the second one like `Chapter (Sam's conflicted copy 2026-10-14).byax` (Dropbox), `Chapter-LAPTOP.byax`
  (OneDrive), `Chapter 2.byax` (iCloud), `Chapter (1).byax` (Google Drive, Box),
  `Chapter (conflicted copy …).byax` (Nextcloud) or `Chapter.sync-conflict-….byax` (Syncthing). A file
  is only offered when it is a complete package of the same document. **Review conflicted copy…** (or
  **Tools → Review conflicted copies…**) imports its history and registers one merge in **Proposed
  changes**. Edits to different sections merge with nothing lost. Edits to the same section become
  conflicts you resolve with **Review → Versions and drafts → Resolve selected merge conflicts…**.
  Comments and review items in the copy are listed as not merged. Nothing changes until you accept,
  and the copy is never deleted. After you accept the merge and save, the review dialog can move the
  copy into a `ByAuthorX conflicted copies` folder beside the document.
- **Keep autosave and caches out of the sync folder.** Recovery copies and locks are stored in your
  user data and cache folders; the check warns if those are themselves inside a synced folder.

The same checks run headless as `byax sync` ([reference](cli-reference.md#sync-folder-safety)).

## Reviewing your own text

The Inspector's **Checks** tab lists every check with an active checkbox and its own **Run**
button; **Run all active checks** in **Proposed changes** runs the active ones together:

- *On this computer*: spelling, grammar, document health. Active by default.
- *Fiction analysis (advisory)*: dialogue tags, filter words and telling, repeated words and
  openings, rhythm, sensory balance and invented-name spelling. Local and inactive by default; see
  [Fiction checks and readability](#fiction-checks-and-readability).
- *With an AI provider*: information repository, factual accuracy, voice consistency and any
  number of named custom checks you write. Inactive by default; each asks for authorization.

Spelling results (suggestions, exact replacements, a document dictionary and session-only
ignore) appear in **Proposed changes** once a spelling check runs. Repositories are the libraries
and sources set up in **Configure**.

Results are suggestions; text changes require Apply/accept. Spelling and grammar marks are
interface-only pen strokes and never appear in print or exports.

### Fiction checks and readability

The fiction checks are word-list and counting rules that run on your computer and only point at
places to reread; nothing is changed unless you accept a proposal, and only the invented-name rule
offers one (use the common spelling). They are off until you switch a family on in the **Checks**
tab (or run `byax check --family fiction`), and even then each rule has a sensitivity so they do
not nag:

| Family | Rules | Starts at sensitivity |
| --- | --- | --- |
| Names | `fiction.name_spelling`: a capitalized word the dictionary does not know that is one edit from a more common spelling | conservative |
| Repetition | `fiction.phrase_echo` (a three-word phrase repeated within 120 words) | conservative |
| | `fiction.word_echo` (a word of five or more letters repeated within 30 words) | balanced |
| | `fiction.sentence_opening` (three sentences in a row starting with the same word) | broad |
| Dialogue | `fiction.tag_adverb` (an -ly adverb on a speech tag), `fiction.tag_repetition` (one non-"said" tag three times within 120 words) | balanced |
| | `fiction.tag_bookism` (ornate tags such as *hissed* or *chuckled*) | broad |
| Style | `fiction.filter_words` (*saw*, *felt*, *noticed* and similar, two or more in a paragraph) | balanced |
| | `fiction.show_tell` (*was furious*), `fiction.adverb_density` (adverbs per 100 words in a chapter) | broad |
| Rhythm | `fiction.sentence_rhythm`, `fiction.paragraph_rhythm` (runs of nearly equal length), `fiction.paragraph_length` | broad |
| Sensory | `fiction.sensory_balance` (a chapter that leans on one sense) | broad |

Dialogue is excluded from the filter-word and telling rules, and chapters are the sections of your
top-level headings. Echo, rhythm and tag rules look back only within the same section, which is also
what keeps rechecking fast: editing one paragraph re-runs that paragraph, the following paragraphs
whose look-back reaches it, its chapter's balance rules and the document-wide name comparison.
Tune any rule per document or per section with the profile's `fiction` setting, for example
`{"enabled": ["fiction.show_tell"], "disabled": ["fiction.word_echo"], "thresholds": {"echo_window_words": 20}}`
(valid thresholds are listed by `byax.fiction_checks.THRESHOLDS`); a section profile may override it,
so a poetry chapter can switch the rules off. Findings follow the usual rules: exact ranges, statuses,
intentional exceptions and protected passages. Passages marked intentional (quotation, lyric,
dialect, verbatim) are skipped. English is the only shipped word list
(`src/byax/resources/fiction/en.json`); other languages report *unmeasurable* coverage until a
language file is added. A clean result is never a pass: the coverage always says that voice and
intent are not judged.

Invented names use the local dictionary and counts only, so a distinct character with a similar
name can be flagged (the rule needs the common spelling to be used at least twice as often and the
variant at most three times). The context engine's codex will later supply canonical names through
`byax.fiction_checks.register_name_source`.

**Tools > Readability report…** (or `byax readability`) gives each chapter and the whole
manuscript the Flesch Reading Ease, Flesch-Kincaid grade, Gunning fog, SMOG, Coleman-Liau and
Automated Readability indices, plus average sentence length, a passive-voice count, adverb rate,
dialogue share, reading time and a pacing series (words, average sentence length and dialogue
share for every paragraph). The dialog draws the pacing series as a graph you can step through with
the arrow keys; the same values are in the table beside it. Every index carries a caveat when the
sample is too short for it (for example SMOG needs 30 sentences and the others about 100 words),
syllable counting and passive-voice detection are heuristics, and the indices are calibrated for
English. **Save report as document…** writes the report as a ByAuthorX, Word, Markdown, HTML or PDF
document and lists any conversion losses.

Published precision and recall for these rules on the seeded and "limits" fixtures are in
[`benchmarks/0.3-fiction-checks.json`](benchmarks/0.3-fiction-checks.json): on the seeded fixture
every one of the 18 annotated findings is found with no extras (precision and recall 1.0), the clean
fixture produces none, and the limits fixture shows where the heuristics are wrong (a literal
*felt*, an emotion told in words the list lacks, a distinct character called Corin). These are
small hand-annotated fixtures, not an accuracy estimate for a manuscript. Timing on a 100,000-word
manuscript is in [`benchmarks/0.3-fiction-performance.json`](benchmarks/0.3-fiction-performance.json).

### Assistant: Chat vs Work

A persisted **Chat / Work** switch controls the assistant. *Chat* only answers and never touches
the document. *Work* may propose exact range edits, which appear in Proposed changes as pending proposals;
nothing changes until you accept each one. Remote messages show the exact outbound payload for
your approval unless that provider's saved policy allows remote calls. Set up keys through
**Add or manage API keys…**; see [providers](providers.md).

### Dictation

Choose a transcription provider (OpenAI, ElevenLabs, or a local whisper.cpp binary), record, and
review the transcript before it is inserted. Insertion is a reviewed proposal against the revision
you were on, so it cannot silently overwrite later edits.

## Comments, tracked changes, history

Comments, bookmarks and tracked changes follow the text as it moves. A passage whose text is
fully deleted becomes *unresolved*; a partial overlap becomes *needs review*. Create named
checkpoints and branches from History, and restore any retained revision. Restoring cannot weaken
protections or tracking you have set.

## Manuscript projects (binder)

A long manuscript can carry a binder: an ordered list of parts, chapters and scenes built from its
headings, plus `note` items for research and character sheets. The binder lives inside the same
`.byax` file and the text stays in the document, so history, comments, checks and exports work as
before. Run `byax project novel.byax --action init` to bind every heading (kinds are inferred from
heading levels; pass `--input` with `{"kinds": {"1": "part", "2": "chapter", "3": "scene"}}` to
choose), then:

- `--action list` or `tree` shows items with status, target and authored word counts;
- `move` reorders an item with its text (`--before`, `--after` or `--parent` another item);
- `split` starts a new item at an exact offset (`--target ITEM --at BLOCK --offset N --text TITLE`);
- `merge` joins an item with the next one (`--next ITEM`);
- `set-status`, `set-target --words N` and `set-meta --input meta.json` (synopsis, label,
  `include_in_compile`) record your plan. Synopses are only what you write.

Notes are left out of the compile list by default (`--action compile`). Comments, bookmarks and
tracked changes travel with the text through every binder change. If you delete a heading in the
editor, its item is kept and reported as orphaned (`--action reconcile`) until you rebind or remove
it. Word, Markdown and other exports keep the binder order as headings and list statuses and targets
as losses. The corkboard and outline views come later; every binder action already works from the
CLI, MCP and REST.

## Libraries and citations

Tools → Libraries attaches local research folders for retrieval; **Cite** inserts a citation from
existing or attached sources, with APA, MLA or Chicago presentation. Library search works
without any AI and understands `"exact phrases"`, `-excluded` words, `prefix*`,
`NEAR(a b)` and `heading:`/`title:`/`path:` filters, and suggests spellings when a word
matches nothing ([search syntax](cli-reference.md#library-search-syntax)). When an assistant
says something is not in your sources, ByAuthorX searches your libraries and this document
literally and shows any matches. See the
[library and citations guide](library-and-citations-guide.md).

**On-device semantic search** finds passages by meaning as well as by words, with no AI account and no
Ollama. It is off until you turn it on for a library: choose **Enable on-device semantic search…** in
Tools → Libraries and answer *Enable on-device semantic search? Nothing leaves this computer.* The
model runs on your computer. The standard installer includes it; with the lean installer, Tools →
On-device search… shows the source, size and checksum of each model and downloads one only when you
press Download. Nothing is downloaded in the background. The same dialog sets **Fast** (the default) or
**Accurate** search, which also reorders the best results with a second on-device model. See
[on-device semantic search](library-and-citations-guide.md#on-device-semantic-search).

## Keyboard and accessibility

Controls are native, with accessible names; motion respects **View → Reduce motion**, system
preferences and `BYAX_REDUCED_MOTION=1`. No screen-reader or WCAG certification is claimed; see
the [accessibility audit](accessibility-audit.md).

## Printing

File → Print uses the same layout as the Pages preview and supports page ranges.

## Going further

Anything in the app can be scripted: see the [CLI reference](cli-reference.md). Problems:
[troubleshooting](troubleshooting.md).
