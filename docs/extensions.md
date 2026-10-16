# Extensions

ByAuthorX has one extension point, and it is deliberately small: **extensions run out of process and
reach your writing only through the same scoped operations as any other client.** There is no
in-process plugin loading and no way for an extension to edit a document directly. (Competitiveness
plan 7.4, decision 5.)

An extension can add, and nothing else:

1. **Custom checks**, either *data* (word lists and regular expressions in JSON, evaluated by
   ByAuthorX itself, so no code runs) or *scripts* (a small program that receives the text and returns
   findings). Findings enter the normal checks pipeline under the family `extension:<id>`.
2. **Export hooks**, programs that run after an export on a read-only copy of the exported file plus
   the loss report, and may write extra files (a word-count CSV, a manifest) into a folder you chose.
3. **Saved command macros** are not an extension kind: they are named, versioned sequences of CLI
   commands you save yourself (see [Macros](#macros)).

Extensions that need richer access to a document use the REST service with a token scoped to what the
extension declared (`read`, `propose`); see [Scoped REST tokens](#scoped-rest-tokens).

> **Extensions are not sandboxed.** A script runs as you, with a time, memory, CPU and output limit.
> It receives only the JSON ByAuthorX sends, but a program can open any file you can open. Installing an
> extension is trusting its author, exactly like installing any program. Read
> [Trust model](#trust-model-and-security-notes) before installing one.

## Versioning and compatibility policy

* The extension API has an integer **`api_version`**, currently **1**. A manifest must state it.
* A ByAuthorX release supports a list of API versions (`extensions.SUPPORTED_API_VERSIONS`, shown by
  `byax extensions --action list` as `api_version`). An extension for an unsupported version is
  refused at install with `unsupported_version` and, if it is already installed, listed as
  `incompatible` and never run.
* **Within one `api_version`, changes are additive and backward compatible**: new optional manifest
  fields, new optional JSON keys sent to scripts, new optional finding fields. Scripts must ignore keys
  they do not know. Removing or changing the meaning of a field, a scope or a limit **needs a new
  `api_version`**, and the previous one keeps working for at least one minor release after it is
  deprecated (announced in the changelog).
* The manifest is validated **strictly** for the version it declares: an unknown key is an error, so a
  typo is caught at install rather than ignored. A manifest that needs something added after its first
  release states **`min_byax_version`**; older releases then list it as `incompatible` instead of
  failing in odd ways.
* Limits (timeouts, output sizes, finding counts) are part of the contract and are listed below. They
  can be raised within an API version but not lowered.
* Extension versions (`version`) are the author's own; installing a different version over an installed
  one needs `--replace`, a newer-or-equal version, and a fresh confirmation of its scopes and hash.

## The manifest: `byax-extension.json`

```json
{
  "id": "long-sentences",
  "name": "Sentences over N words",
  "version": "1.0.0",
  "api_version": 1,
  "description": "…",
  "author": "ByAuthorX project",
  "licence": "MIT",
  "homepage": "https://example.org/long-sentences",
  "min_byax_version": "0.3.0",
  "kinds": ["check"],
  "scopes": ["read", "checks"],
  "entry_points": {
    "checks": [
      {"id": "sentence-length", "type": "script", "path": "checks/long_sentences.py",
       "runtime": "python", "timeout": 20, "title": "Long sentences",
       "options": {"max_words": 40}, "rules": ["too-long"]}
    ],
    "export_hooks": []
  }
}
```

| Field | Rule |
| --- | --- |
| `id` | 2 to 48 lowercase letters, digits and hyphens. Becomes the folder name, the finding family `extension:<id>` and the rule prefix `extension.<id>.` |
| `name`, `description`, `author`, `licence` | Required text (name 100, description 2000, author 200, licence 100 characters) |
| `version` | `1`, `1.2` or `1.2.3` |
| `api_version` | Must be a supported integer (1) |
| `homepage` | Optional `http(s)` address. Shown, never opened or fetched by ByAuthorX |
| `min_byax_version` | Optional; older releases list the extension as incompatible |
| `kinds` | `check` and/or `export-hook`; must match the entry points present |
| `scopes` | The permissions requested (below). Installing shows them and you must accept exactly these |
| `entry_points.checks[]` | `id` (lowercase, digits, `-`, `_`), `type` `data` or `script`, `path`, optional `title`, `description`. Script checks also take `runtime` (`python` default, or `executable`), `timeout` (1 to 120 seconds, default 20), `options` (a small JSON object passed to the script) and `rules` (the rule ids the script may report; anything else is rejected) |
| `entry_points.export_hooks[]` | `id`, `path`, `runtime`, `timeout`, optional `formats` (such as `["docx", "pdf"]`; omitted means every format), `title`, `description` |

Paths are relative, use letters, digits, `_`, `.` and `-` only, and may not climb out of the extension
folder. A `python` entry point must be a `.py` file. An `executable` entry point (any program, on
macOS and Linux) must have its execute permission; on Windows the launcher cannot apply memory or
CPU limits to it (the timeout and output caps still hold).

### Scopes

| Scope | Allows |
| --- | --- |
| `read` | Receive the **text** of the document (paragraphs, headings, table cells) as JSON. Required by every script check. Nothing else of the document is sent |
| `checks` | Add findings to the document checks. Required by every check |
| `propose` | Offer a replacement with a finding (the engine turns it into a repair *proposal*), and, through the REST service, submit proposals. Never a direct edit |
| `export-hook` | Run after an export on a read-only copy of the exported file and its loss report |

Consistency rules the installer enforces: a script check needs `read` and `checks`; a data check needs
`checks`; a data rule that offers a replacement needs `propose`; an export hook needs `export-hook`.
The scopes you accept are recorded as **granted**; the runtime uses the intersection of granted and
requested scopes, so tampering with the manifest later cannot widen anything.

## Installing, listing, enabling, removing

Extensions are installed **only by an explicit local action**: the desktop dialog (Tools →
Extensions…) or the local CLI. Over MCP and REST the actions `install`, `inspect-package`, `enable`,
`disable`, `remove` and `token` are refused with `unsupported_feature`; listing, details, verification
and running checks or hooks are available.

```bash
# 1. Look first. Nothing is installed; the SHA-256 and the requested scopes are shown.
byax extensions --action install --source-path ./long-sentences

# 2. Confirm by repeating exactly what was shown.
byax extensions --action install --source-path ./long-sentences \
    --sha256 <shown hash> --scopes checks --scopes read

byax extensions --action list
byax extensions --action enable --id long-sentences
byax extensions --action run-check --id long-sentences --document book.byax
byax extensions --action disable --id long-sentences
byax extensions --action remove --id long-sentences
byax extensions --action verify
```

* The source is a **folder** or a **ZIP**. For a ZIP the shown hash is the SHA-256 of the archive; for a
  folder it is a digest of the folder contents (path, size and hash of every file). The install is
  refused if the hash or the scopes differ from what you confirmed, so a package changed between
  looking and installing is caught.
* Archives and folders are read with hostile-input limits like language packs: safe member names only,
  no links or special files, at most 64 files, 2 MiB per file and 8 MiB in all. Hidden files are not
  installed. Every installed file is hashed.
* Extensions install **switched off**; enable one deliberately. An upgrade (`--replace`) is also
  switched off until enabled again.
* Installed files live under the user data folder (`…/ByAuthorX/extensions/<id>/`, or
  `BYAX_EXTENSIONS_DIR`). ByAuthorX records the hash of every file at install and re-checks it: a file
  changed or added afterwards makes the extension `modified`, it stops running, and `verify` says which
  file. The entry point is hashed again immediately before every run.
* Nothing is fetched from the network, ever. The `homepage` is text.

## Custom checks

Run with `byax check --suite extension` (or `--family extension`), from the Checks tab ("Extension
checks", off by default), from Tools → Extensions… ("Run its checks on this document"), or
`byax extensions --action run-check --id <id> --document <file> [--check <check id>]` while developing
one (the extension must be enabled). A document check with the `extension` suite runs
every enabled extension check; recorded findings (`record_checks`) become stale when an extension is
disabled, replaced or modified, like any other dependency.

A finding looks like every other finding: rule `extension.<id>.<rule>`, `family`
`extension:<id>`, `suite` `extension`, a range and the text it covers, `status` `open`, and (with
`propose`) a repair that is a separate operation you accept. Extensions can only report `advisory` or
`warning`: **an extension can never produce an error that blocks an export gate.**

Coverage is honest. A check that ran adds a `completed` coverage entry naming the extension and
version; one that timed out, crashed or returned something invalid adds a `failed` entry with the
reason and contributes **no** findings, and the report's validation is `incomplete`, never `passed`.
A failed unit is retried by the next incremental check. Passages marked intentional (code, quotation,
lyric, dialect, verbatim) are not sent.

### Data checks

A data check is a JSON file of rules, evaluated in-process. Data is not code, but a regular expression
can still run away, so patterns are compiled with the same runaway-backtracking probes as the grammar
rule files and matched with a per-passage time limit.

```json
{"rules": [
  {"id": "filler", "kind": "words", "words": ["very", "really"], "message": "Cut \"{match}\".",
   "severity": "advisory", "case_sensitive": false, "whole_word": true},
  {"id": "spelling", "kind": "words", "words": ["utilize"], "replacements": {"utilize": "use"},
   "message": "Prefer a plainer word than \"{match}\".", "severity": "warning"},
  {"id": "dash", "kind": "regex", "pattern": "\\s--?\\s", "flags": "i", "replacement": "—",
   "message": "Use an em dash without spaces."}
]}
```

`kind` is `words` (up to 2,000 words or phrases) or `regex` (a pattern up to 500 characters, flags
`i`, `m`, `s`). Optional: `languages` (such as `["en"]`), `title`, `severity`, and `replacement` /
`replacements` (needs the `propose` scope). `{match}` in the message is the matched text. At most 500
rules per file.

### Script checks

The script reads one JSON object on **stdin** and writes one JSON object on **stdout**.

Input (everything a script is given):

```json
{"api_version": 1, "extension": "long-sentences", "check": "sentence-length", "language": "en",
 "targets": [{"id": "p_ab12…", "block": "p_ab12…", "type": "paragraph", "text": "…", "language": "en"}],
 "options": {"max_words": 40}}
```

`targets` are paragraphs, headings and table cells in document order; `id` is what a finding must
name, `block` its containing block. Offsets are Unicode scalar values (Python string indexes). No
metadata, comments, tracked changes, history, sources, libraries, settings or file names are sent.
The child process also gets a scrubbed environment: no service token, no provider keys.

Output:

```json
{"api_version": 1, "findings": [
  {"rule": "too-long", "target": "p_ab12…", "start": 0, "end": 120, "severity": "advisory",
   "message": "This sentence has 61 words; the limit is 40.",
   "replacement": "optional text to propose in place of the range (needs propose)",
   "measured": {"words": 61}}
]}
```

The output is validated **strictly and as a whole**; one violation discards all of it (no partial
trust): top-level keys `api_version`/`findings` only; at most 1,000 findings; each finding only the keys
above; `rule` a lowercase id (and one of the manifest's `rules` when declared); `target` one of the ids
that was sent; `start`/`end` whole numbers with `0 <= start <= end <= len(text)` (booleans are not
numbers); `severity` `advisory` or `warning`; `message` 1 to 500 characters; `replacement` text up to
2,000 characters and only with `propose`; `measured` at most 20 short scalar entries. `NaN` and
`Infinity` are rejected. A reported `replacement` becomes a delete/insert pair the engine builds
itself; scripts never send operations.

### Limits

| Limit | Value |
| --- | --- |
| Wall-clock timeout | Per entry point, 1 to 120 s (default 20 s); the whole process group is killed |
| CPU time | timeout + 2 s (`RLIMIT_CPU`) |
| Memory | 512 MiB address space (`RLIMIT_AS`; not enforced by the macOS kernel, not available on Windows) |
| Check output | 4 MiB on stdout; larger output kills the process |
| Largest file a process may write | `RLIMIT_FSIZE` (hooks 16 MiB, checks 5 MiB) |
| Input | 32 MiB of document JSON |
| Findings per check | 1,000 |
| Hook output | At most 20 plain files of at most 16 MiB; hook stdout at most 64 KiB |

Limits that the operating system cannot enforce (memory on macOS, all `resource` limits on Windows)
are skipped silently by the launcher; the timeout and output caps hold on every platform.

### Packaged builds

From source (`pip`/`uv`), a `python` entry point runs with the same interpreter and packages as
ByAuthorX, in isolated mode (`-I`: no user site folder, no current-directory imports; its own folder is
on `sys.path` so it can import sibling modules). In the packaged installers there is no separate Python:
the launcher is the application itself (`--byax-extension-worker`), and a script can import only the
modules bundled with it (the parts of the standard library ByAuthorX itself uses). The example scripts
use only `json`, `re`, `csv`, `html` and `zipfile`, and a packaged build may not bundle all of those; if
your script needs a module that is missing, ship it as an `executable` entry point (a program with its
own runtime). The frozen path has not been exercised here: only the dispatch flag is tested.

## Export hooks

```bash
byax export book.byax --out book.docx --hooks [--hooks-dir ./book-extras] [--overwrite]
byax extensions --action run-hook --id wordcount-csv --hook wordcount --exported-file book.docx
```

`export --hooks` runs every enabled hook whose `formats` include the export format. In the desktop,
Tools → Extensions… has "Run enabled export hooks after each export" (off by default) and "Run an
export hook on a file…". Hooks never run unless you ask.

A hook receives on stdin:

```json
{"api_version": 1, "extension": "wordcount-csv", "hook": "wordcount", "format": "docx",
 "file": "/tmp/byax-ext-hook-…/input/book.docx", "output_dir": "/tmp/byax-ext-hook-…/output",
 "loss_report": {"losses": [], "fidelity": "supported_subset"}}
```

* `file` is a **copy** of the exported file in a temporary folder, made read-only. The master document
  is never passed or opened by ByAuthorX for a hook.
* The hook writes result files into `output_dir` (also its working directory). When it exits,
  ByAuthorX validates them (plain files only, safe names, size limits) and copies them to the
  designated folder (`--hooks-dir`, default `<export>.hooks`). A file that already exists there is
  skipped unless `--overwrite` is given.
* It may print `{"message": "…"}` on stdout, or nothing.
* The result of each hook is reported in the export result under `hooks`: `status`
  (`completed`, `failed` or `timeout`), `outputs`, `skipped`, `message`, and on failure the failure kind
  and the tail of stderr. **A failing hook never fails the export**, and the export is reported
  unchanged.

## Macros

A macro is a named, versioned sequence of CLI commands saved as JSON, run through `cli.execute` so each
step has the same envelope, validation and preconditions as the same command on its own.

```json
{"schema": 1, "name": "proof-pass", "version": 1, "description": "Checks, then a clean export.",
 "parameters": {"suite": {"type": "array", "default": ["structure", "grammar"]}},
 "steps": [
   {"label": "Check", "command": "check", "arguments": {"path": "{document}", "suite": "{suite}"}},
   {"label": "Export", "command": "export", "arguments": {"path": "{document}", "out": "{document}.docx", "overwrite": true}}
 ]}
```

```bash
byax macro --action save --input proof-pass.json                     # user config
byax macro --action save --input proof-pass.json --store document --document book.byax
byax macro                                                            # list
byax macro --action run --name proof-pass --document book.byax --dry-run
byax macro --action run --name proof-pass --document book.byax --params '{"suite": ["structure"]}'
byax macro --action remove --name proof-pass
```

* **Where**: the user config folder (`macros.json`) or the document's `settings.macros` (saved as a
  canonical `set_settings` edit, so it is a revision and travels with the file). A name in both stores
  needs `--store`.
* **Placeholders**: `{document}` is the document path given to `run`; `{revision}` is the revision the
  previous step left that step's document at; declared `parameters` (types `string`, `integer`,
  `boolean`, `array`, `object`, with `default` and `required`). A string that is exactly one
  placeholder keeps its JSON type; `{{` and `}}` are literal braces. Steps may not call `macro`.
* **Preconditions**: mutating steps get `expect_revision` automatically, set to the revision the
  previous step produced (or `--expect-revision` for the first), so a document that changes under a
  macro stops it with `stale_revision`. A step that sets its own `expect_revision` keeps it.
* **Dry run**: read-only steps run for real; other steps run with their own `dry_run` when the command
  has one; a step with no dry run is *skipped* and the transcript says so. Nothing is written.
* **Stop on first error**: the first step that raises, or returns a non-zero exit state (a failed
  requirement, strict coverage), stops the macro. Earlier steps stay applied, like any other edits.
  The error carries the failing step and the whole **transcript** (step, command, status, exit code,
  a summary of the result, seconds; `--verbose` adds each full result).
* **Administrative steps**: every step is checked with `transport_policy.check_provider_admin`, exactly
  as a transport request would be, so a macro cannot do over MCP, REST or the desktop what those
  refuse (provider or library configuration, language packs, extension install/enable, local goals,
  update checks, …). Only the local CLI with an explicit **`--allow-admin`** runs them. `macro list`
  shows which steps are administrative (`admin_steps`).
* **Over MCP and REST**: list, show and run work; `--allow-admin` is refused; saving or removing a
  macro in the user config is a local action, saving in a document is allowed. Behind the REST service
  the service's path policy is applied to **each step after substitution**, so a stored macro cannot
  write outside the authorized roots.

## Scoped REST tokens

`byax serve` accepts the service token (full access within its roots) and, in addition, per-extension
tokens derived from it:

```bash
export BYAX_SERVICE_TOKEN=…            # the one `byax serve` uses
byax extensions --action token --id my-extension --scopes read --scopes propose --ttl 3600
```

The token is `bxe1.<id>.<scopes>.<expiry>.<hmac>`: signed with the service token (so it cannot be
forged or extended and stops working if the service token changes), bound to one extension and a
lifetime of 1 minute to 30 days. A request is honoured only while that extension is still installed,
enabled, intact and granted the scopes.

| Scope | May call |
| --- | --- |
| `read` | `inspect`, `find`, `context`, `diff`, `statistics`, `readability`, `check` (not the `extension` suite, which would call itself), `schema`, `capabilities`, `version`, and `review`/`history`/`project` listings |
| `propose` | `review --action propose` with `replace_text`, `insert_text`, `delete_text`, `set_text` or `update_cell` operations only, a mandatory `expect_revision`, and only `operations`, `explanation` and `expected_revision` in the input. The author is stamped `extension:<id>` |

Anything else is `403 forbidden`. `propose` does not include `read`. All path scoping, the reserved
state folder and the administrative refusals of the service still apply.

## Examples

`examples/extensions/` holds three working extensions, used by the tests:

| Folder | Kind | What it shows |
| --- | --- | --- |
| `house-style-words/` | data check | Filler words, preferred spellings with replacements (proposals), a regex rule. No code |
| `long-sentences/` | script check | A Python script reporting sentences over N words (`options.max_words`) |
| `wordcount-csv/` | export hook | Writes `<file>.wordcount.csv` (words, bytes, number of reported losses) |

Try them:

```bash
byax extensions --action install --source-path examples/extensions/long-sentences
# repeat with the printed --sha256 and --scopes, then:
byax extensions --action enable --id long-sentences
byax check my-book.byax --suite extension
```

## Trust model and security notes

* **Boundary**: an extension is code you chose to run. ByAuthorX's guarantees are about *what it is
  handed and what it is allowed to change*, not about what the operating system lets a program do.
* **No in-process loading.** Nothing from an extension is imported into the application. Scripts run
  through `python -I -m byax.extension_worker` (or the application's own `--byax-extension-worker`
  flag in frozen builds), which applies the limits and then becomes the extension's entry point, in its
  own process group, in an empty temporary working directory, with an allow-listed environment
  (`PATH`, temp folders, locale; never `BYAX_SERVICE_TOKEN`, provider keys or secrets).
* **No document access** except the stdin JSON of a script check, the exported-file copy and loss report
  of a hook, or authenticated calls to the REST service with a scoped token. Extensions never receive
  the master file path, history, comments, sources, libraries or settings.
* **No direct edits**: findings are advisory (never `error`), repairs are proposals the engine builds
  from a replacement string, REST proposals are text-edit proposals with an `expect_revision`, and
  hook outputs go only to the folder you named.
* **Strict validation** of everything that comes back (see above); malformed, oversized, out-of-range
  or hostile output is rejected whole and shown as failed coverage.
* **Integrity**: hashes of every installed file are recorded at install and checked on listing and
  before each run; the installer shows the archive SHA-256 and requested scopes and requires them back.
  This detects tampering after install; it does not vouch for the author. ByAuthorX has no extension
  store, no signatures and no updates: it never fetches anything.
* **What is not claimed**: a malicious script can read other files you can read, make network
  connections and write anywhere you can write. Run only extensions from authors you trust. On
  Windows no memory or CPU limit is applied to the process; on macOS the memory limit is not enforced
  by the kernel. The timeout and output caps always apply.
* **Reporting**: treat an extension's `description`, finding messages and stderr as untrusted text; the
  desktop shows them as plain text.

## Writing an extension

1. Make a folder with `byax-extension.json` and the files it names. Start from an example.
2. For a data check, edit the JSON and run `byax extensions --action install --source-path <folder>`;
   the installer validates every rule and regular expression.
3. For a script, read stdin, write stdout, exit 0. Print diagnostics to stderr (the last 400
   characters are shown when a script fails).
4. Install, enable, then `byax extensions --action run-check --id <id> --document <file>`. A script that
   times out or returns something invalid shows as `failed` coverage with the reason.
5. Package a ZIP with the files at its root (one wrapping folder is also accepted) and publish its
   SHA-256 so people can check what they installed.

## Where this is implemented

`src/byax/extensions.py` (manifest, install, store, runner, check and hook integration),
`src/byax/extension_worker.py` (launcher), `src/byax/macros.py`, `src/byax/service.py` (scoped tokens),
`src/byax/gui_extensions.py`, and the `extension` suite in `src/byax/checks.py`. Tests:
`tests/test_extensions.py`, `tests/test_macros.py`, `tests/test_extension_service.py`,
`tests/test_gui_extensions.py`.
