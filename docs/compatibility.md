# Compatibility and verification boundaries

ByAuthorX targets **Windows 11 x64** and **Ubuntu 24.04 x64** concurrently. These are the initial targets approved by the product owner, not a claim that physical desktop acceptance has passed. Source and application code are MIT licensed; redistributed dependencies retain their own licenses. Qt for Python/PySide6 is dynamically distributed under its applicable LGPL/commercial terms; distribution must retain its notices and replacement rights.

| Area | Implementation / declared support | Verification boundary |
| --- | --- | --- |
| Native GUI | Python 3.11+, Qt 6 / PySide6 desktop widgets; no browser shell or HTTP dependency | Automated offscreen widget checks do not establish accessibility, IME, clipboard, printing, scaling, or microphone behavior on a physical desktop |
| Windows distribution | PyInstaller executables plus Inno Setup installer definition | Windows 11 installation, uninstallation, associations, PowerShell, signing, antivirus interaction and runtime tested status must be recorded separately |
| Ubuntu distribution | PyInstaller executables plus `.deb` packaging script, desktop/MIME entries | Ubuntu 24.04 installation, desktop launch, fonts, audio, printing and file association validation must be recorded separately |
| Shared engine | Headless library, JSON CLI, MCP; optional explicit local service | Headless operations do not require a running GUI; rendering needs installed Qt runtime libraries and an offscreen QPA platform |
| Native `.byax` | Versioned ZIP/UTF-8 manifests; case-insensitive extension recognition; original filename case retained | ZIP validation, asset hashes, lock conflicts, atomic replacement and round-trip tests are automated; no claim of fault-tolerant storage beyond atomic replacement |
| Text coordinates | Zero-based Unicode scalar indices; end-exclusive ranges; grapheme-aware editing | Qt/Windows use UTF-16 internally; conversion is required at GUI boundaries |
| TXT / Markdown | TXT, CommonMark-style Markdown plus tables through markdown-it-py | Supported constructs and conversion losses are reported. Native rich formatting cannot be represented fully in plain Markdown |
| DOCX / ODT / RTF / HTML | Editable subsets through local adapters | Exact typography/pagination and all review structures are not preserved; review loss reports before publishing |
| Legacy DOC | Explicitly configured local LibreOffice conversion adapter | Requires a separately installed/configured executable; no unselected upload; no claim that every historical Word file is supported |
| PDF | Qt PDF painter consumes shared resolved text layout | Controlled output differs with font environment. No general PDF editable import |
| Text shaping | Qt shaping, installed fonts; mixed-script fixture provided | Licensed DejaVu Sans/Serif/Mono regular/bold/italic variants are bundled; missing-glyph findings are located. Complex-script fallback, bidirectional editing and arbitrary installed-font permissions still need verification |
| OpenAI | Text review and transcription adapters | No live credentialed provider call is implied by unit/contract tests |
| Anthropic | Text review adapter | Audio recognition capability is not inferred from text support |
| Gemini | Text review | Model availability, account quotas and endpoint behavior require user-configured live tests |
| Ollama | Local text model endpoint | Requires a running local service and installed model; compatible wire protocol alone does not verify arbitrary models |
| ElevenLabs | Transcription adapter | Live calls require user credentials and an account-supported model |
| whisper.cpp | Explicitly configured local executable/model | Requires local binary/model and supported audio conversion; shipping the GUI does not download a model |
| Microphone | Qt Multimedia native audio integration where available | Device selection, start/pause/resume/stop, permissions and recoverable recording require physical desktop tests |
| Accessibility | Native controls and document structure checks | No screen reader, WCAG, PDF/UA or assistive technology certification claimed |

Live API support is **implemented but unverified against real accounts** until a dated capability test is run. Provider response shapes, model names, quotas, audio formats and language coverage change independently from the application. English is the initial review fixture language; Unicode fixtures exercise transport and shaping without claiming complete language-tool coverage.

## Resource and output limits

Native storage currently bounds a package to 256 MiB of expanded members, an individual member to 64 MiB, and 10,000 members (`storage.py`). These are safety defaults, not a maximum supported document page count. Images/previews are bounded to 40 million pixels and page dimensions to 14,400 points; CSV is bounded to 16 MiB, 10,000 rows, 1,000 columns and 100,000 cells. Individual import/provider/job adapters apply further bounds; inspect their capability/error reports. Current page-count fixtures are 100 mixed pages and 500 long-text pages with deliberate page breaks. Benchmarks must report the **actual** resolved page count and input hash.

Candidate edits share immutable history but still copy current semantic state, and full physical layout construction can make large-document edits slow. No 50 ms GUI typing or 500 ms incremental reflow claim is made without matching measurements. Benchmark results from a development container are not target Windows/Ubuntu hardware qualification.

## Remaining platform acceptance

Run the scenario matrix in `acceptance-matrix.md` on each target OS, with an installed package and no developer environment. Record OS build, CPU, RAM, SSD, Qt, font binary identities, input language/IME, scale factor, screen reader, audio devices and printer. Exercise offline operation, native dialogs, clipboard and drag/drop, `.BYAX` association, recent files, crash recovery, external changes, printing ranges, keyboard-only dictation, and uninstall behavior. Keep screenshots, output files, logs without secrets, test outcomes and measured durations as artifacts.
