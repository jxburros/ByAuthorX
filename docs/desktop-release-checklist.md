# Installed desktop release acceptance

Run on clean physical Windows 11 and Ubuntu 24.04 x64 machines. Offscreen tests,
build success and generated GitHub provenance do not establish these results.
Record a separate report per platform; leave failures and unexecuted steps visible.

## Record the environment and artifact

Copy this template into a release evidence report:

| Field | Recorded value |
| --- | --- |
| Tester and local date | Pending |
| Release version, tag and commit | Pending |
| Installer filename and SHA-256 | Pending |
| OS version, CPU, RAM, storage, display/scaling | Pending |
| Printer, microphone, input methods and screen reader | Pending |
| Provider alias/kind/model and local/remote status (no keys) | Pending |
| Scenario | Pass / Fail / Not run, steps, output paths and observations |

Verify the download against `SHA256SUMS.txt`. For an attested release, verify the
installer with `gh attestation verify <installer> --repo jxburros/ByAuthorX`.
GitHub build provenance is distinct from Windows Authenticode signing. The project
does not yet ship a configured Windows signing certificate or claim SmartScreen
reputation. Record unsigned-installer warnings rather than marking signing passed.

## Installation and the nine acceptance scenarios

1. **Documents and recovery (spec 23.1).** Install as a normal user; launch from
   the OS application list. Create a letter, resume, report and newsletter. Enter
   Unicode and IME text, edit across paragraphs, copy/paste and drag/drop using
   another native application. Save, close and reopen. Force-terminate with unsaved
   edits and verify recovery without replacing the saved original. Repeat with an
   external disk change and verify explicit conflict handling. Print a page range.
2. **Press kit (23.2).** Create a two-page music press kit with biography, release
   table, image/caption, header/footer and page numbering. Edit the biography and
   release date. Compare preview, PDF and physical print; record wrap, crowded-page,
   column and object-placement limitations instead of treating them as passing.
3. **Interchange (23.3).** Use representative Word/LibreOffice documents with tables,
   images, headings, lists, comments and mixed scripts. Preserve originals and
   hashes. Import, edit, export, reopen in the originating application and compare
   text, structure, fonts, page breaks and located loss reports. Include historical
   DOC with the separately installed converter. Record silent losses as failures.
4. **Review and history (23.4).** Add comments/bookmarks and tracked changes, create
   a checkpoint and branch, move a section, then compare/merge drafts. Accept only
   selected dependent edits; resolve conflicts explicitly. Undo/redo, restore and
   reopen. Check that protections and tracking cannot be weakened by restore.
5. **Checks and evidence (23.5).** Complete the research walkthrough with a local
   sample and a separately authorized real source folder. Check exact quotations,
   paraphrases, missing evidence and source changes. Exercise section profiles and
   intentional exceptions. Verify that an incomplete required check blocks sharing.
6. **Dictation (23.6).** With a real microphone and OS permission, test direct,
   cleaned and document-aware dictation using selected models. Edit while cleanup
   runs; verify late results cannot overwrite edits. Test names, interruptions,
   device removal, recovery and retained-segment controls. Record each provider
   independently; mocked contract tests do not verify the device or service.
7. **Agents and concurrency (23.7).** Run inspect/context/dry-run/apply/check/export
   through CLI and MCP on a closed copy. Test a protected table and stale revision;
   retry the same request ID and confirm a single commit. With the file open in
   desktop, verify an external write is explicitly refused. Include instruction-like
   document/source text and confirm it cannot authorize network or tool execution.
8. **Publication and reproduction (23.8).** Add private comments, metadata, original
   attachments, transcripts and hidden material. Use Prepare to share, inspect the
   exclusion choices and exported bytes, and reopen the master to confirm retention.
   Exercise explicit inclusion and verified sanitization separately. Compare font
   dependencies, glyph fallback, PDF text and page images between the two machines.
9. **Platform, accessibility and performance (23.9).** Complete the workflow using
   keyboard only, NVDA on Windows and Orca on Ubuntu, high contrast, reduced motion
   and 200% scaling. Test bidi selection and IME composition. Run the benchmark
   command below with other workloads stopped and retain the raw JSON. Record cold
   startup, input-to-paint, save, full reflow and idle separately.

```sh
QT_QPA_PLATFORM=offscreen uv run python scripts/benchmark.py --scaling --out artifacts/desktop-benchmark.json
```

For actual desktop input/paint evidence use the native Qt platform (omit
`QT_QPA_PLATFORM=offscreen`); note the platform in the report. In PowerShell set or
remove `$env:QT_QPA_PLATFORM` explicitly. Benchmark fixtures and timing definitions
are in the acceptance matrix. A container measurement is not qualified desktop data.

Record credential-store save/restart/delete behavior for each installed platform
without exposing secrets. Test every advertised provider with the opt-in smoke
script and explicit remote authorization; keep billable/live tests separate from CI.

## Exit criteria and remaining engineering

Link the evidence report from the acceptance matrix and relevant issues. A failed
or unexecuted scenario cannot be labeled complete. Long-document transaction and
page-resolution scaling (#66/#73/#74/#75/#82), the real-file fidelity corpus and
font substitutions (#78/#79/#103), OS signing and physical/live acceptance remain
separate work; this checklist does not close them.
