# Research to publication

Write with AI. Keep your voice, your sources, and the final say.

ByAuthorX is designed for substantial documents whose wording and sources matter:
research reports, analysis, technical writing and client deliverables. The desktop
editor, CLI and MCP share document operations, revision checks and review proposals.
The desktop holds an exclusive document lock; this is not simultaneous co-editing.

## Try the workflow without an account

1. Choose **Help → Research to publication…**, or use the introduction on a blank
   document. The guide is modeless: close it or return to the editor between steps.
2. Choose **Create sample project…** and select a destination folder. A fresh
   `ByAuthorX-research-*` directory contains `Pilot report.byax` and a `research`
   folder with fictional pilot notes. No existing files are replaced. Creation and
   evidence inspection stay local; a remote reviewer can receive passages only
   through the existing provider selection and passage authorization workflow.
3. Choose **Review → Claims and evidence…**. Select the quotation to see its
   current indexed passage and locator. Activate an item to locate its text in
   the full master view. The first quotation should match; that does not establish
   the truth of the report's uncited conclusion.
4. Edit the notes in the sample's research folder, then inspect again. Evidence
   is marked changed, with no fallback to the old stored excerpt. Refresh the
   library and deliberately choose a current citation when updating the report.
5. Choose **File → Prepare to share…** to inspect a publication copy and export it.
   The editable master and research folder remain separate from the deliverable.

The sample writes a local library definition and cache. Remove that definition
through **Tools → Libraries** when finished; remove the sample folder separately
if you no longer need the files. Moving the folder requires relinking the library.

## Work with your own research

Attach a folder through **Tools → Libraries**. Select document text and choose
**Cite** to search and inspect passages before inserting a citation. Source imports
from Zotero/BibTeX, RIS and CSL-JSON remain available. See the
[library guide](library-and-citations-guide.md) for formats, permissions and bounds.

**Claims and evidence** reads already indexed, attached, profile-authorized sources;
it does not index files, browse the web or call a model. Section source policies
apply. It distinguishes exact quotation matching, source retrieval requiring
interpretation, manually supplied citations, changed sources and unavailable
evidence. It also displays recorded evidence assessments from review results.
An uncited claim is not automatically discovered by local inspection. Re-run
semantic review when the document or evidence changes.

Inspection reads at most 100 citations per page; use Previous/Next citations to
continue. Agents can use the same bounded operation through
`byax evidence report.byax --limit 100 --offset 0 --json`, the read-only
`byax_evidence` MCP tool or REST `evidence`. Results disclose total and continuation.

## Review without losing your voice

In Review, select an AI check and choose an approach:

- **Use active profile** keeps the existing profile workflow.
- **Copyedit lightly** asks for minimal spelling and grammar changes.
- **Preserve my voice** asks the reviewer to retain phrasing and intentional variation.
- **Suggest structure separately** permits findings only; returned text edits are rejected.

Optional controls keep numbers, quotations or cited text unchanged. Supply a
comma-separated list of terminology to preserve. These choices appear in the
exact outbound payload and accompany returned proposals. The provider adapter
rejects edits that violate the lexical preservation rules before registering
proposals. Selection ranges retain protected quotation/number/term boundaries;
edits touching protected spans can be conservatively rejected.

These checks do not prove that meaning or voice is preserved. Inspect the full
before/proposed wording, rationale, instructions and selected operation in Review.
Nothing is accepted automatically. Existing section profiles, protected passages,
revision preconditions and per-edit acceptance continue to apply.

AI checks need an explicitly configured provider/model. The fictional sample can
be used with a local model or an explicitly authorized remote reviewer. Library
passages still require their own transfer permission and payload preview. Your own
local-only libraries continue to refuse remote sharing. Local writing, evidence
inspection and publication work without a provider.

## Prepare a deliverable

**Prepare to share** defaults to excluding comments, tracked changes, proposals,
findings, history, metadata, references/excerpts, source snapshots, transcripts,
audio, hidden content and original attachments. Select any category deliberately.
If your deliverable needs rendered source citations and bibliography, include
references; inspect their excerpts before sharing. This selection controls the
publication copy, not the private master.

Select a format and optional required structure, requirements or print checks.
The preview uses the same filtered copy and inclusion settings as clean export.
It lists format limitations and check findings; activate a located item to return
to its master content. Required checks that fail or have incomplete coverage block
export. A changed document invalidates the preview and requires reopening it.

The final result reports the actual included/excluded categories and compatibility
losses. Word, ODT and RTF previews do not certify another application's pagination.
Publication filtering is not full redaction or a guarantee that image pixels are
free of private information. Verified term-based sanitization remains available
through the existing publication engine.
