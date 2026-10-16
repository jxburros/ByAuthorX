# User guide

ByAuthorX is a native desktop word processor. You write in a Qt editor, save editable `.byax`
documents, and export through the same typography engine used for the paginated preview. The
document stays yours: nothing is uploaded, no account is needed and there is no telemetry.

## Install

Installers are attached to each [GitHub release](https://github.com/jxburros/ByAuthorX/releases):
a Windows 11 x64 setup program and an Ubuntu 24.04 x64 `.deb`. They are unsigned, so Windows
SmartScreen warns on first run. Ubuntu: `sudo apt install ./byauthorx_<version>_amd64.deb`.
To run from source see the [README](../README.md#run-from-source). **Help → Download updates**
only opens the releases page; the app never updates itself.

## The window

- **Writing view** – continuous editing. **Pages** shows the paginated preview that print and PDF use.
- **Ribbon** – two rows: font, size, alignment, lists, indents, super/subscript, colors,
  paragraph formatting and **Cite**.
- **Side panels** – each has a selector: Outline, Inspector (Review, Layers, AI), Bookmarks,
  Writing statistics, Personal scratchpad, Library. Panel choices and the scratchpad are local
  settings, not saved in the document.
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

For a complete guided example, open **Help → Research to publication…**. It can
create an offline sample with fictional research in a fresh directory you choose.
The blank-document introduction links to the same guide and can be dismissed.
See [Research to publication](research-workflow.md) for evidence inspection,
voice/preservation choices and **File → Prepare to share…**.

The Inspector's **AI** tab checks spelling locally (selection or whole document) with
suggestions, exact replacements, a document dictionary and session-only ignore. Review's check
dropdown + **Go** runs:

- *Local*: spelling, grammar, document health.
- *Needs a configured reviewer*: information repository, factual data, voice consistency and
  custom instructions.

Results are suggestions; text changes require Apply/accept. Spelling and grammar marks are
interface-only pen strokes and never appear in print or exports.

### Assistant: Chat vs Work

A persisted **Chat / Work** switch controls the assistant. *Chat* only answers and never touches
the document. *Work* may propose exact range edits, which appear in Review as pending proposals;
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
