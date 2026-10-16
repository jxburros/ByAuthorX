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
| Open Word, ODT, RTF, HTML, TXT | File → Open. These are imports; a compatibility report lists anything lost |
| Edit Markdown directly | Markdown opens as an import by default; the first-open chooser or Tools → Markdown opening preference keeps `.md` as the working file |
| Share a PDF or Word copy | File → Export. Exporting never replaces the master |
| See how a format will look | Toolbar **File view** selector (PDF, Word, OpenDocument, RTF, HTML, Markdown, plain text). Word/ODT/RTF previews are approximate. The source toggle shows exact read-only export text |

Hidden or unsupported content stays in the master; objects simplified in a view are locked there.
Details: [file views](file-views.md) and [compatibility](compatibility.md).

Autosave writes recovery copies; an explicit **Save** draws a brief checkmark. If the file changes
on disk behind you, the app tells you rather than overwriting.

## Reviewing your own text

The Inspector's **Checks** tab lists every check with an active checkbox and its own **Run**
button; **Run all active checks** in **Proposed changes** runs the active ones together:

- *On this computer*: spelling, grammar, document health. Active by default.
- *With an AI provider*: information repository, factual accuracy, voice consistency and any
  number of named custom checks you write. Inactive by default; each asks for authorization.

Spelling results (suggestions, exact replacements, a document dictionary and session-only
ignore) appear in **Proposed changes** once a spelling check runs. Repositories are the libraries
and sources set up in **Configure**.

Results are suggestions; text changes require Apply/accept. Spelling and grammar marks are
interface-only pen strokes and never appear in print or exports.

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

## Libraries and citations

Tools → Libraries attaches local research folders for retrieval; **Cite** inserts a citation from
existing or attached sources, with APA, MLA or Chicago presentation. See the
[library and citations guide](library-and-citations-guide.md).

## Keyboard and accessibility

Controls are native, with accessible names; motion respects **View → Reduce motion**, system
preferences and `BYAX_REDUCED_MOTION=1`. No screen-reader or WCAG certification is claimed; see
the [accessibility audit](accessibility-audit.md).

## Printing

File → Print uses the same layout as the Pages preview and supports page ranges.

## Going further

Anything in the app can be scripted: see the [CLI reference](cli-reference.md). Problems:
[troubleshooting](troubleshooting.md).
