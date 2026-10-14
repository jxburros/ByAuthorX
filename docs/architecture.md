# Architecture

ByAuthorX is a native Qt 6 desktop application. Python supplies the shared document engine; PySide6 supplies native widgets, text shaping, clipboard, file dialogs, accessibility hooks, printing, and microphone access. No local web server is needed for editing. Qt WebEngine is neither an application dependency nor a rendering surface.

The canonical editable representation is a JSON-compatible semantic document. Paragraphs, headings, tables, cells, images, review objects, and layers have stable identities. Canonical text coordinates are zero-based Unicode scalar indices with end-exclusive ranges; edit boundaries must coincide with grapheme boundaries. GUI code converts Qt's UTF-16 positions at the interface boundary.

## Modules

| Module | Responsibility |
| --- | --- |
| `model`, `errors` | Document structures, validation, stable errors |
| `engine` | Atomic candidate operations, targeting, preservation, revision history |
| `storage` | Versioned ZIP packages, content hashes, atomic replacement, process locks, source folders |
| `checks` | Located findings, actual coverage, deterministic requirements |
| `layout` | Qt typography, pagination and shared page drawing for previews/PDF/images/printing |
| `exchange` | Safe imports, editable exports, explicit compatibility/loss reports |
| `publication` | Selected publication policies, serialized redaction verification, reproduction inventory |
| `providers` | Six explicitly selected model adapters and local secret references |
| `transcription`, `jobs` | Recording, reviewed transcript insertion, persistent work state |
| `gui`, `gui_editor` | Native desktop controls; operations go through the engine |
| `cli` | Common command dispatcher and structured CLI envelopes |
| `mcp`, `service` | Stdio MCP; separately enabled local REST transport |

## Mutation and persistence

Clients inspect an identity and revision, construct an operation envelope, optionally dry-run it, and apply it with preconditions. Operations evaluate a candidate copy. Failed validation or preservation leaves the original unchanged. Successful operations advance history and record a request identity when one is supplied. Repeated identical committed requests return the recorded result; changing the payload for a reused identity is a conflict. Desktop edits omit replay identities because they are not retried remotely.

The desktop treats `history_limit` as a soft cap: it compacts older unnamed revisions while retaining every named checkpoint and branch head, plus the latest half of the configured limit. Undo/redo lists retain only available revisions. The API retains its hard cap and explicit `compact_history` operation. Failed desktop flushes leave pending text in the editor for correction or retry. Nonstructural history deltas omit unchanged block order; readers also accept older deltas containing the full order.

`set_settings` cannot replace protection rules or requirements, or disable active tracking. Removing a protection uses `unprotect(target, kind)` and changing tracking uses `set_tracking(enabled)`; these actions require local desktop confirmation. The desktop passes the trusted library option `allow_policy_changes=True` for these explicit operations. CLI, MCP and REST do not expose that option, and the untrusted envelope's `origin` grants no authority. History restore cannot weaken retained protection or tracking. Use `protect` and `set_requirement` for ordinary policy setup.

An editor holds an OS-backed document lock for its session. Headless mutation commands hold the same lock across read, validation, and write. GUI saves use their existing owner identity. This implements a clear conflict rather than silently mutating disk behind an unsaved editor. Advisory OS locks cannot prevent an unrelated application that ignores them from writing the file; the GUI separately detects external file changes.

Lock files live in the user's ByAuthorX cache directory, keyed by a hash of the resolved document path. They persist there to keep a stable lock inode across competing processes; no lock sidecars are created beside documents or exports. Restart older app processes when upgrading from sidecar-based locking. Separate OS users have separate cache directories and do not coordinate through these locks.

Packages contain `manifest.json`, `document.json`, and content-addressed asset members. Import validates member names, sizes, hashes, schemas, and identities before making a document available. Native persistence stages output and replaces it atomically. Source folder exports omit history and replay records by default.

## Providers and trust boundaries

Document text and model output are data. Neither can select executables, enable remote retrieval, configure secrets, or execute editor commands. Provider configurations are local settings and hold keyring/environment references, not embedded credentials. Remote workflows require a selected provider and explicit transmission policy. The runtime has no model polling and no telemetry.

MCP and REST reject `providers configure`, including wrapped job submissions, before creating configuration or job state. Configure aliases through the local CLI or desktop; existing selected aliases remain available to transport clients.

Network response fixtures exercise adapter contracts; they do not establish live model compatibility. A connection test distinguishes reachable credentials/endpoints from verified inference. Timeouts and interrupted remote calls can have uncertain outcomes and are not automatically repeated.

## Rendering limits

`QTextDocument` supplies native bidirectional shaping and text pagination. The current renderer explicitly reports features it cannot faithfully resolve. See the acceptance matrix for section columns, sophisticated object wrapping, footnote placement, and font reproducibility coverage. A serialization success alone does not establish validated publication.
