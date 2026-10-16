# User guide

ByAuthorX is a native desktop word processor. You write in a Qt editor, save editable `.byax`
documents, and export through the same typography engine used for the paginated preview. The
document stays yours: nothing is uploaded, no account is needed and there is no telemetry.
AI is opt-in, not opt-out. No AI feature runs until you configure a provider and choose to use it,
and you choose the provider yourself. It can be a hosted service with your own key or a local model on your own
computer (see [providers](providers.md)).

## Install

Installers are attached to each [GitHub release](https://github.com/jxburros/ByAuthorX/releases):
a Windows 11 x64 setup program, an Ubuntu 24.04 x64 `.deb` and a macOS `.dmg` for Apple silicon
(macOS 14 or later). The release notes say, per file, whether it is signed. Unsigned files make
Windows SmartScreen or macOS Gatekeeper warn on first run; see
[troubleshooting](troubleshooting.md#macos-gatekeeper-says-the-app-cannot-be-opened). Ubuntu:
`sudo apt install ./byauthorx_<version>_amd64.deb`. macOS: open the `.dmg` and drag ByAuthorX to
Applications. To run from source see the [README](../README.md#run-from-source).

## Your first minutes

- **The tour.** The first time ByAuthorX starts, a short tour (six steps) explains styles, the outline,
  proposals, checks and citations. Use **Next** and **Back** or the arrow keys, **Skip tour** or Escape
  to leave, and tick **Don't show this tour again** to stop it ever returning (a future, different tour
  would otherwise be offered once). Reopen it any time with **Help → Take the tour…**. With reduced
  motion on (**View → Reduce motion**, your system preference, or `BYAX_REDUCED_MOTION=1`) steps swap
  without any fade.
- **The sample project.** **Help → Open sample project** copies a small manuscript, *The Lamp at
  Harrow Point*, into `Documents/ByAuthorX` and opens the copy. It has named styles (including one it
  defines), a binder with chapters, scenes and a note, a pending proposal from a reviewer, a comment, a
  citation that points at a source record, and a clean recorded check. The bundled original is never
  opened for editing, so change anything you like; asking again makes another copy and never replaces
  one you edited. From the command line: `byax sample --out practice.byax`.
- **Updates.** ByAuthorX never looks for updates by itself. **Help → Check for updates…** makes one
  request to GitHub's releases API when you click it, tells you whether a newer version exists and links
  to the release page; it downloads and installs nothing. Remove the menu item with **Help → Show
  "Check for updates" in this menu**. **Help → Download updates…** only opens the releases page in your browser.

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
| Turn a manuscript into a print book, e-book or submission | File → Compile…. See [Compiling a book](#compiling-a-book) |
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

- *On this computer*: spelling, grammar, document health. Active by default. Grammar has two tiers,
  the built-in rules and the optional LanguageTool; see
  [Grammar tiers and languages](#grammar-tiers-and-languages).
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

### Grammar tiers and languages

Grammar checking has two tiers, both on your computer; the Checks tab shows the state of each
(*Built-in rules: on (85 English rules)* and *LanguageTool: not installed, stopped or running*):

- **Built-in rules** are always available. They are deliberately narrow and precise: commonly
  confused words in clear contexts (*should of*, *its a*, *your welcome*, *taller then*), simple
  agreement (*they was*, *he don't*), verb forms (*has went*, *I seen*), *between you and I*,
  *less people*, comma splices, a lowercase *i* and about two dozen misheard idioms (*for all
  intensive purposes*). Spacing, capitals, *a/an*, doubled words and unbalanced quotes are covered by
  the older checks and are not repeated. They do not run inside quoted speech when the pattern is
  dialect (*he don't*), and a profile's sensitivity decides which rules run.
- **LanguageTool** is optional and adds broad grammar and style coverage in many languages. It is
  free software that needs Java 17 or newer and runs as a separate local program; ByAuthorX never
  bundles it and only ever sends text to it on this computer. Use it by choosing a LanguageTool
  folder you already have, by pointing at a server you started, or by letting ByAuthorX download the
  official release. Nothing is downloaded until you confirm a dialog that shows the source address,
  the size (about 240 MiB) and the SHA-256, and the file is checked before it is unpacked.
  **Tools > Languages… > Grammar** (or `byax grammar`) installs, starts and stops it.

Findings from either tier have exact ranges and, where there is a fix, a proposal you accept one at
a time. The built-in tier is much narrower than LanguageTool, and a clean grammar run says so in its
coverage. The language of a paragraph is its own `language` setting, else the document language.
Accuracy per rule family on a labelled fixture, the comparison between the tiers and the reasoning
are in [grammar](grammar.md).

**Tools > Languages…** also lists the language packs (dictionaries, grammar rules, word lists and
interface strings; English ships built in), installs a pack from a file after showing its checksum,
verifies them and removes them, and chooses the interface language. No interface translation ships
yet; see [translations](translations.md) for coverage per language and how to make a pack.

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
checkpoints and drafts from History, and restore any retained revision. Restoring cannot weaken
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

Notes are left out of the compile list by default (`--action compile`); see
[Compiling a book](#compiling-a-book). Comments, bookmarks and
tracked changes travel with the text through every binder change. If you delete a heading in the
editor, its item is kept and reported as orphaned (`--action reconcile`) until you rebind or remove
it. Word, Markdown and other exports keep the binder order as headings and list statuses and targets
as losses. Every binder action works from the CLI, MCP and REST as well as the Binder panel below.

### The Binder panel: outline and corkboard

Choose **Binder** in either sidebar's panel selector (or **Binder → Show Binder**, Ctrl+Alt+B). A
document without a binder shows **Create binder**, which binds the headings and leaves the text alone.

- **Outline** lists parts, chapters and scenes as an indented tree. Each row shows the title, the kind,
  the status and the words, with the target and a thin progress line when the item has one.
- **Corkboard** shows cards with the title, status, synopsis, words and target. It shows every scene
  and note, or the items inside one chapter or part (the scope box above the cards, or **Open in
  corkboard**).
- Clicking an item or pressing Enter shows its heading in the editor. Selecting with the arrow keys
  does not move the editor.
- **Reordering.** Drag a row or a card before or after another (on an outline row, the middle of the
  row makes it a child). Everything you can drag has a keyboard route: **Alt+Up** and **Alt+Down**
  move the selected item earlier or later among its siblings (at the end of a chapter the item
  crosses into the next or previous one), **Alt+Left** outdents it so it follows its parent, and
  **Alt+Right** indents it into the item before it. The same commands are in the **Binder** menu,
  the panel's **Binder actions** button and the context menu (the Menu key or a right click). Each
  move is one revision you can undo, and comments, bookmarks and tracked changes move with the text.
  Heading levels follow the new position, so an indented scene becomes one level deeper (with Track
  changes on, the level changes are recorded as formatting changes like any other heading edit); a move
  that would pass level six, or put a section inside itself, is refused and explained in the status bar.
- **Synopsis** is text you write, or nothing. Edit it in the field under the views (Ctrl+Enter or
  leaving the field saves, Escape restores) or in place on a card (F2). **Draft a synopsis…** is
  optional: it needs a text provider you configured, shows what will be sent when the provider is
  remote, and puts the answer in **Proposed changes** as a proposal. Nothing is written until you
  accept it.
- **Split at editor cursor…** starts a new item where the editor cursor is (inside the item's own text,
  after its heading); **Merge with next item** joins an item with the one that follows it and asks
  first. **Status**, **Word target…**, **Add scene after…** and **Add chapter after…** are in the same
  menu.

A binder item whose heading was deleted in the editor is not shown but never deleted; the panel says so.

## Writing goals

**Tools → Writing goals…** (or the progress button at the right of the status bar) sets and shows:

- a **document target** and **item targets**, saved in the binder (set an item's in the Binder with
  **Word target…**);
- a **daily goal** and a **session goal**, and your **streak** of days that met the daily goal (days with
  any writing when there is no goal).

Progress counts the words you add: a session's count is its growth in words since you opened the
document, so deleting text neither counts against you nor makes up for words you wrote earlier. A day's
total adds its sessions, and a session belongs to the day it started. The words are the same authored
words the status bar and **Document statistics** report.

The daily and session goals and your session records are kept **on this computer, outside the
document**, in the per-user configuration folder, keyed by the document's identity. They are never saved
in the `.byax` file, never create a revision and never appear in history, so sharing or syncing a
manuscript does not share your writing habits. `byax goals` reads the same information (see the
[CLI reference](cli-reference.md#writing-goals)).

## Named drafts

A draft is a saved state of the whole manuscript with a name and a note. Drafts are built on the
document's existing branches and history, so they cost no extra files.

- **Review → Versions and drafts → Save draft as…** saves the document as it is now. You keep writing in
  the current draft; tick **Switch to the new draft now** to continue in the saved one instead.
- **Drafts…** lists every draft with its note, save time and word count, and can switch to one.
  Switching replaces the text you are editing with that draft's text and clears undo for the previous
  one; your other draft stays exactly as it was.
- **Compare drafts…** shows, scene by scene, whether each binder item is unchanged, changed, added or
  removed between two drafts, with the words before and after and the change. Select a row for the
  wording edits, field changes (status, synopsis…) and moves; Enter shows the scene in the editor. A
  document without a binder compares as a whole.
- **Bring changes from draft…** proposes the changes saved in another draft for the current one. It
  adds a proposal to **Proposed changes**; scenes that changed in both drafts are listed as conflicts
  to resolve, and nothing changes until you accept.

The command line has the same operations (`byax drafts`, see the [CLI reference](cli-reference.md#named-drafts)).

## Compiling a book

**File → Compile…** (or `byax compile`) turns a manuscript into a finished book: a print-ready PDF,
an EPUB, a Word document for submission or plain Markdown. Your `.byax` file is never changed; the
compile works on a copy and every format reports what it could not carry.

1. **Pick a preset.** Three ship with ByAuthorX:
   - **Novel**: 5.5 × 8.5 in trade paperback, mirrored margins with a gutter, chapters opening a
     third of the way down a new page with a drop cap and a small-caps lead-in, `* * *` between
     scenes, running heads (your name on left pages, the title on right pages) with page numbers,
     none on chapter openers, and a half title, title page, copyright page, dedication and contents.
   - **Non-fiction**: 6 × 9 in, chapters on right-hand pages with a number and title, section
     headings kept, the chapter title in the right-hand running head, page numbers at the foot.
   - **Manuscript (standard format)**: Letter paper, 1 in margins, 12 pt Times New Roman,
     double-spaced, ragged right with 0.5 in indents; a first page with your contact details and the
     word count rounded to the nearest thousand (hundred for short works), your title and byline; a
     `Surname / TITLE / page` header on every later page; chapters on a new page a third of the way
     down; `#` between scenes and `END` at the end. Italics stay italic. It produces Word by default.
2. **Choose the format and trim size.** Trim sizes include 5 × 8, 5.25 × 8, 5.5 × 8.5, 6 × 9,
   6.14 × 9.21, 7 × 10 in, A5 and B5.
3. **Choose what goes in.** The list shows your binder; untick anything to leave out. Notes are left
   out unless you tick them. A document without a binder compiles each top-level heading as a chapter.
4. **Run preflight.** It lists what to fix before you publish: a missing title, author or language;
   fonts that are not installed or whose licence forbids embedding; pictures below 300 pixels per inch
   at the printed size; pictures without alt text; empty chapters; headings with nothing under them;
   headings or scene breaks stranded at the foot of a page; binder items that lost their heading.
5. **Compile…** and choose where to save.

What the compile builds from your metadata (Tools → Document properties, or `set_metadata`): `title`,
`subtitle`, `author`, `publisher`, `year`, `rights`, `isbn`, `dedication`, `epigraph`,
`acknowledgments`, `about_author`, `also_by`, and for manuscripts `contact` (`name`, `address`,
`email`, `phone`), `surname`, `short_title` and `pen_name`. A page whose text is missing (an empty
dedication, say) is skipped and listed in the report; binder items of kind `front` and `back` are
compiled where they are. Chapter titles such as "Chapter 3" become the numbered label ("Chapter
Three"); real titles keep a label line above them.

**Your own presets.** *Save preset…* stores the current choices in the document;
`byax compile novel.byax --action export-preset --out mine.json` writes them as a reusable settings
collection that `--preset-file` (or Tools → Reusable settings…) brings into another manuscript. A
preset file only needs what differs from the bundled preset it names in `extends`.

**What to expect from each format.**

- *PDF*: the same manuscript and preset always give the same page breaks; the report records the
  layout signature and the block that opens every page. Bleed (up to 72 pt) and crop marks enlarge
  each sheet around the trim (set `design.bleed` and `design.crop_marks`). The PDF is not PDF/X: trim
  and bleed boxes are not written and fonts are embedded as Qt subsets. Drop caps print as an
  enlarged raised initial (the text does not wrap around it), small capitals are synthesized by the
  font engine and there is no hyphenation; the report says so where it applies.
- *EPUB*: the same inputs give byte-identical files. Each part, chapter and front-matter page is its
  own file and the contents come from the headings; the drop cap and small caps are CSS.
- *Word*: compiled through the Word exporter, then opened again and compared with what was
  compiled. Every difference (for example drop caps and small caps, which Word output does not keep)
  is listed with its location. Page numbers in the header are real Word page fields.
- *Markdown*: plain text with headings; styles and page design are listed as losses.

Comments, tracked changes and proposals are not compiled (preflight notes how many there are).

## Codex, project find and series

These features work without any AI: they read only your files and never use the network.

**Codex** (Tools → Codex…) keeps characters, places, items and terms. Each entry has a name,
aliases ("Lizzy", "Eliza"), excluded phrases (occurrences inside them are not counted), a *Match
letter case* switch (on by default, so the name "Will" is not the word "will"), a description and
traits such as "eyes: grey". The panel lists each entry's mentions (activate one to jump there) and
a **heatmap** of mentions per chapter; every count is also the cell's text. Edits are ordinary
undoable changes saved in the `.byax` file. Exports keep your text and report that the codex stays
in the master; clean publications leave it out. The fiction name-spelling check uses codex names
and aliases as known spellings.

**Hide from AI** works per entry and per trait. Hidden entries and traits are never sent to a model,
never appear in assistant context and are not shown to MCP or REST clients. An assistant never edits
the codex: it can only propose a fact ("Mara's eyes are grey, ch. 3 ¶12"), which arrives in
Proposed changes with where it was read, for you to accept or reject.

**Find in project** (Edit → Find in project…, Ctrl+Shift+F) searches this document and every other
`.byax` file in the same folder with the [library search syntax](cli-reference.md#library-search-syntax).
Results show the document and chapter; results in this document open at the passage. Choose
*This document* to stay in the open file. **Save search…** keeps a query as a saved search (a
self-updating collection): it is stored in this document and its results are recomputed each time
you run it. The **Backlinks** tab lists, for a heading, bookmark or codex entry, the cross-references
or recognized mentions pointing to it, and *unlinked mentions*: plain-text occurrences of a heading
or bookmark name, and codex near misses (a different letter case, or an excluded phrase). The search
index is rebuilt from your files as needed and kept in your user cache folder, never next to your
documents; after the first project search it is also updated whenever you save.

**Series.** Books of a series can share one codex and set of research libraries through a
`.byaxseries` file in the folder above the books: `byax codex book-1.byax --action series-export
--out ../saga.byaxseries`, then `--action series-attach --series-path ../saga.byaxseries` in each
book. A book's own entry wins over a series entry with the same name, and its own library
attachment wins over the series'. The series file can also list member documents, which then join
project find. The format is described in the
[library and citations guide](library-and-citations-guide.md#series-files).

## Libraries and citations

Tools → Libraries attaches local research folders for retrieval; **Cite** inserts a citation from
existing or attached sources, with APA, MLA or Chicago presentation. Library search works
without any AI and understands `"exact phrases"`, `-excluded` words, `prefix*`,
`NEAR(a b)` and `heading:`/`title:`/`path:` filters, and suggests spellings when a word
matches nothing ([search syntax](cli-reference.md#library-search-syntax)). When an assistant
says something is not in your sources, ByAuthorX searches your libraries and this document
literally and shows any matches. The assistant's citations appear as numbered chips: hover to
see the cited passage, click to open the source at that page or block, and **Insert as
citation** to cite it in your text. Checks → **Citation audit** (or `byax check --citations`)
checks every citation you made against its source on this computer, without AI. See the
[library and citations guide](library-and-citations-guide.md#grounding-citation-chips-and-the-citation-audit).

## Keyboard and accessibility

Controls are native, with accessible names; dragging in the Binder always has Alt+arrow and menu equivalents. Shortcuts written Ctrl+… in this guide are Cmd+… on macOS
(Qt translates them); the application, preferences and quit entries sit in the macOS application menu.
Motion respects **View → Reduce motion**, system
preferences and `BYAX_REDUCED_MOTION=1`. No screen-reader or WCAG certification is claimed; see
the [accessibility audit](accessibility-audit.md).

## Printing

File → Print uses the same layout as the Pages preview and supports page ranges.

## Going further

Anything in the app can be scripted: see the [CLI reference](cli-reference.md). Problems:
[troubleshooting](troubleshooting.md).
