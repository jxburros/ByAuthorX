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

## Working with an editor in Word

Editors work in Microsoft Word. ByAuthorX sends them a Word copy and turns what comes back into
proposals and comments you review. Nothing in your text changes until you accept a proposal.

1. Save your document as a `.byax` file, then choose **File → Send for review (Word)…**. Name the
   reviewer if you like and choose what Word allows: *tracked changes and comments only*
   (recommended), *comments only*, or *no restriction*. Track Changes is on in every copy. Word
   enforces the restriction through Restrict Editing with no password, so an editor can lift it.
2. ByAuthorX remembers this version as the review snapshot, saves your document, and writes the
   `.docx`. Your open comments and replies go with it, on their exact passages. Footnotes, the
   contents block, section breaks and content excluded from output stay in ByAuthorX. They are listed
   when the copy is written.
3. Keep writing. When the editor returns the file, choose **File → Merge reviewed copy…**. ByAuthorX
   compares three versions: the snapshot you sent, the returned file, and your document now. Only the
   editor's changes become proposals, and your later edits are kept.
4. **Proposed changes** opens with one proposal for each change, labelled with the editor's name and
   date. These include insertions, deletions, replacements, moved paragraphs, new or deleted
   paragraphs, split or joined paragraphs, bold/italic/underline changes, alignment and heading-level
   changes, and table cell edits. Changes the editor made with Track Changes off are compared with
   the snapshot. They are proposed as well, marked *edit made without Track Changes*. The editor's
   comments and replies become comment threads on the same passages, and a comment the editor marked
   done arrives resolved. Replies to your own comments join your threads.
5. Accept or reject each proposal. If you rewrote a passage that the editor also changed, its
   proposal starts with **Conflict:** and shows both versions. Accepting it replaces only that passage
   with the editor's version. Rejecting keeps yours.

The merge report lists what could not be merged, each with its paragraph: field codes (their visible
result is still compared as text), SmartArt, charts, shapes and text boxes, content controls, ink,
equations, embedded objects, footnotes added in Word, changes to headers and footers, table rows or
columns added or removed, other kinds of tracked change, and paragraph formatting such as spacing or
indentation. Review those in Word. Merging the same returned file twice changes nothing. The copy
records which review it belongs to. If a file has lost that record, ByAuthorX asks which copy you
sent.

The same steps run headless as `byax review-package` and `byax merge-review`
([reference](cli-reference.md#word-review-round-trip)).

## Sharing with beta readers

Beta readers rarely use Word or ByAuthorX. **File → Send to beta readers…** writes one file for them to
read and comment on, and **File → Import reader comments…** turns what they send back into comments
and suggestions. There is no hosted service and no account: you send the file however you like (email, a
shared folder, a message) and they send theirs back the same way. Nothing is uploaded by ByAuthorX.

1. Save your document as a `.byax` file, then choose **File → Send to beta readers…**. Pick the kind of
   copy:
   - **Web page** (recommended): one `.html` file that opens in any browser on a phone, tablet or
     computer, with no internet connection needed. You can add a short note at the top, for example
     what kind of feedback you want, and name the reader.
   - **PDF**: readers mark it up in their own PDF app. Every page footer carries the copy's ID.
   - **Word**: the Word review copy, with editing restricted to comments.
2. ByAuthorX remembers this version as the snapshot, saves your document, and writes the file. Your own
   open comments, footnotes, the contents block and content excluded from output are not in a web page
   or PDF copy; they are listed when the file is written.
3. Keep writing. Each reader opens the web page, enters a name, selects text and presses **Comment**.
   They can also suggest replacing or deleting the selected text. When they are done they press
   **Download my comments** and send you the `.byaxcomments.json` file. (PDF readers send back the
   annotated PDF; Word readers send back the `.docx`, which you merge with **Merge reviewed copy…**.)
4. Choose **File → Import reader comments…** and select every file you received (you can pick several).
   Each reader's file is merged on its own. Comments arrive in the review panel marked with the reader's
   name, on the passage they selected even if you have edited around it since. A reader's suggestion
   arrives as a pending proposal; accept or reject it like any other, and nothing in your text changes
   until you accept.
5. A comment that cannot be placed exactly is never thrown away: it is kept as a comment on its
   paragraph (or the first paragraph) that quotes the passage it was about, and the import report says
   why. Importing the same file twice, or a later download from the same reader that repeats earlier
   comments, adds nothing twice.

Comment files and annotated PDFs come from other people, so ByAuthorX treats them as data only: they are
size-limited and checked before anything is merged, their text is shown as plain text, and nothing in
them is ever run as an instruction.

PDF notes work on the sent layout: sticky notes and highlights are matched to the lines they mark, so
re-saving the PDF in a tool that rewrites the pages, or reflowing it, can leave notes on a paragraph rather
than on exact words. Annotation types that carry no text (drawings, stamps) are reported, not imported.

The same steps run headless as `byax beta-copy` and `byax merge-comments`
([reference](cli-reference.md#beta-readers)).

### Reading on your phone

The web page copy is also the companion viewer: send yourself the file, open it on a phone or in any
browser, and read the manuscript away from your desk. Choose **Read-only copy with no comment tools** in
the Send to beta readers dialog for a pure reader. It has no comment buttons and no script, it is not
remembered in your document, and it works with no connection.

With comments on, the page is built for a small screen as well as a keyboard:

- The text reflows to the screen width, follows the system light or dark setting and respects reduced
  motion. Buttons are large enough to touch.
- Press and hold a word and drag to select, then press **Comment**. Use **Comments** to jump to the
  list, **Download** to save the file for the writer, and **Show in text**, **Edit** or **Delete** on each
  comment.
- With a keyboard, press Tab to enter the text, move between paragraphs with the Up and Down arrow keys,
  and press C to comment on the paragraph (or on the selected text).
- Comments are kept in the browser on that device until downloaded. A private or "incognito" window,
  clearing site data, or a different browser starts empty, so download before closing.

The page was checked in a desktop Chromium, including phone-sized windows and touch emulation; it has not
been run on a physical phone by the maintainers. The page makes no network requests at all.

## Reviewing your own text

The Inspector's **Checks** tab lists every check with an active checkbox and its own **Run**
button; **Run all active checks** in **Proposed changes** runs the active ones together:

- *On this computer*: spelling, grammar, document health. Active by default. Grammar has two tiers,
  the built-in rules and the optional LanguageTool; see
  [Grammar tiers and languages](#grammar-tiers-and-languages).
- *Fiction analysis (advisory)*: dialogue tags, filter words and telling, repeated words and
  openings, rhythm, sensory balance and invented-name spelling. Local and inactive by default; see
  [Fiction checks and readability](#fiction-checks-and-readability).
- *Extension checks*: checks added by extensions you installed and enabled under **Tools →
  Extensions…**. Inactive by default; see [Extensions and macros](#extensions-and-macros).
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

**Facts that change over time.** A trait can hold *as of* a chapter and *until* a chapter: give it
`valid_from` and `valid_to` (a binder item, heading or block; for a series fact, `book:2`), for
example `byax codex Novel.byax --action add-trait --target cx_mara --name hair --value grey
--valid-from <chapter-3 item>`. `byax codex Novel.byax --action facts-at --target <block>` lists
the facts in force there, the ones not yet established and the ones that no longer hold.

**Continuity checks** (Checks → Continuity, or `byax check Novel.byax --family continuity`) compare
simple statements in your text with those facts: eye colour, hair colour and style, and age, in
patterns such as "Mara's eyes were blue", "Mara had black hair" or "Tom was twelve years old". A
finding marks the exact words and says whether they contradict the fact in force, use a fact before
the chapter where it is established, or repeat a fact that no longer holds. In a series, facts in
the series codex bounded by `book:N` are checked in each book. The check is advisory and off by
default; it reads only your document, codex and series file. Anything beyond these simple patterns
still needs a reader.

## Provenance and disclosure

ByAuthorX keeps a **provenance log** in each document: which spans you typed, which you pasted
(with the page address when your browser puts it on the clipboard), and which came from an AI
proposal you accepted, generated (new text) or edited (rewritten text), with the model and provider
alias and the sources the model cited. Every sentence an accepted AI proposal inserted is recorded
as a *claim* with its grounding record; when you edit the sentence the claim follows the text and is
marked *edited*, and **Re-find claim sources** follows a cited library passage that changed (or
marks it unanchored, never dropping it). Text from before the log existed, imported text and edits
made by tools that do not declare an origin show as *not recorded*. Reviewed dictation you insert
counts as typed text.

The log is local document data. It is never sent anywhere and no model sees it. Turn recording
off for a document with `byax apply` and `{"type": "set_settings", "values": {"provenance":
false}}`, and forget it with **Clear log…** or `byax provenance Novel.byax --action clear` (both
undoable). Exports to other formats keep only the text.

- **View → Show text origin** tints text by origin and draws a different underline for each
  (dashed: AI-generated, dotted: AI-edited, dash-dot: pasted; typed text is unmarked), so the
  distinction never depends on colour. In high-contrast mode only the underlines are drawn.
- **Tools → Provenance…** shows the legend, the share of each origin for the document and for every
  chapter, the list of spans (double-click one to jump there) and the AI claims with their sources.
  `byax provenance Novel.byax` gives the same report (`--target` limits it to a chapter or block).
- **Disclosure** writes an AI-use statement in APA, MLA or Chicago form from the log: the tools and
  models, the share of AI-generated and AI-edited text, and the tasks (drafting, editing,
  summarizing, citing, translating, from the kinds of proposal you accepted). **Copy** it, or **Add
  to Review as a proposal** to insert it as a paragraph you accept like any other change
  (`byax provenance Novel.byax --action disclose --style mla`, `--action propose-disclosure`). It
  is a starting point: square brackets mark what the log cannot know (a chat's share link, an
  unknown developer), and your instructor's or publisher's policy comes first.
- **Export C2PA sidecar…** (`--action export-c2pa --out Novel.c2pa.json`) writes an optional
  manifest describing the text's origins in C2PA 2.x terms (`c2pa.actions.v2` with IPTC digital
  source types, a schema.org `CreativeWork` and a SHA-256 binding to the NFC-normalized text). It is
  **unsigned and informational**: anyone can write such a file. C2PA 2.3 also defines embedding a
  signed manifest in plain text; that needs a C2PA signing tool and your own certificate, which
  ByAuthorX does not bundle. `--key-file` adds a detached Ed25519 signature (it needs the optional
  `cryptography` package and is refused over MCP and REST); it shows who produced the file and is
  not a C2PA claim signature.

The disclosure formats follow the publishers' current guidance:

- APA Style, [Citing generative AI in APA Style: Part 1—Reference formats](https://apastyle.apa.org/blog/cite-generative-ai-references)
  (2025; it replaces the 2023 post [How to cite ChatGPT](https://apastyle.apa.org/blog/how-to-cite-chatgpt)):
  describe the use in the text, such as the Method section or author note, with an in-text citation,
  and list the tool as `Company. (year). Tool or model [Large language model]. URL`.
- MLA Style Center, [How do I cite generative AI in MLA style? (Updated and Revised)](https://style.mla.org/citing-generative-ai-updated-revised/)
  and [Beyond Citation: Describing AI Use in Your Work](https://style.mla.org/describing-ai-use/)
  (13 August 2025): acknowledge substantive use in prose; works-cited entries are
  `"Description" prompt. Tool, model Name, Company, Day Mon. Year, URL.`
- The Chicago Manual of Style Online, [Q&A: citing content generated by AI](https://www.chicagomanualofstyle.org/qanda/data/faq/topics/Documentation/faq0422.html)
  (18th edition): name the tool in the text or in a note such as `1. Text generated by Tool,
  Company, Month Day, Year.`, adding "edited for style and content" when you edited the output.
- C2PA, [Content Credentials specification 2.3](https://spec.c2pa.org/specifications/specifications/2.3/specs/C2PA_Specification.html),
  appendix A.7 "Embedding Manifests into Unstructured Text".

## Extensions and macros

**Extensions** add checks and export hooks. They run as separate programs outside ByAuthorX, are given
only the text a check needs or the exported copy a hook needs, and can only *suggest*: they cannot edit
your document. ByAuthorX has no extension store and never downloads one; you install from a folder or
ZIP you already have. They are not sandboxed, so install only extensions whose author you trust. The
full guide, with the file format for writing your own, is in [Extensions](extensions.md).

**Tools → Extensions…**

1. *Install from folder…* (or *from archive…*). A confirmation shows the extension's name, author and
   licence, the permissions it asks for in plain words (for example "read: receive the text of the
   document"), and the SHA-256 that was checked. Say **Yes** only if you trust it; nothing is
   installed otherwise. It is installed **switched off**.
2. Select it and press *Enable*. The list shows its permissions and status. If a file changes after
   install, the status becomes *modified*, it stops running, and *Verify* names the file.
3. *Run its checks on this document* tries a check without touching the document; its findings also
   appear with the other checks when you tick **Extension checks** in the Inspector's Checks tab.
4. *Run an export hook on a file…* runs a hook on a file you pick. Tick *Run enabled export hooks
   after each export* to run them after every export (off by default). Hook results are listed in the
   export result; a hook that fails never makes the export fail, and your document is never opened by
   a hook.
5. *Disable* or *Remove…* at any time.

**Tools → Macros…** runs saved sequences of ByAuthorX commands, such as "check, then export", on the
saved copy of the open document. Pick a macro, optionally give parameters as a JSON object, and press
*Dry run* (changes nothing; steps that cannot preview are skipped and marked) or *Run*. The transcript
lists each step and what happened; select a step for its details. A macro stops at the first problem,
and steps that need the local command line (for example changing provider or language-pack settings)
are refused here and shown in the *Needs command line* column. *Import from file…* saves a macro JSON
into **My macros** or into **This document** (it then travels with the file). The document must be
saved as a ByAuthorX file first. Macros are also available as `byax macro` and over MCP and REST.

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
The audit also tells you when a citation points to an **earlier version** of its source (a library
file that changed, or another `.byax` draft you cited that has moved on); see
[version-pinned citations](library-and-citations-guide.md#version-pinned-citations).

**On-device semantic search** finds passages by meaning as well as by words, with no AI account and no
Ollama. It is off until you turn it on for a library: choose **Enable on-device semantic search…** in
Tools → Libraries and answer *Enable on-device semantic search? Nothing leaves this computer.* The
model runs on your computer. The standard installer includes it; with the lean installer, Tools →
On-device search… shows the source, size and checksum of each model and downloads one only when you
press Download. Nothing is downloaded in the background. The same dialog sets **Fast** (the default) or
**Accurate** search, which also reorders the best results with a second on-device model. See
[on-device semantic search](library-and-citations-guide.md#on-device-semantic-search).

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
