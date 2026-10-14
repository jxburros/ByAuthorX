# Provider setup

No provider is required for local writing. Configure only services you intend to use. Hosted calls transmit the selected text/context or audio to that provider; the GUI shows the payload before transmission. CLI calls require `--allow-remote` or a saved `allow_remote: true` policy. Document contents cannot select a provider, grant transmission permission, or execute tools.

Use Tools → Provider settings in the desktop or a JSON configuration file:

```json
{
  "alias": "my-reviewer",
  "kind": "openai",
  "model": "YOUR_CHOSEN_MODEL_ID",
  "credential_ref": "env:OPENAI_API_KEY",
  "timeout": 60,
  "allow_remote": false
}
```

```bash
byax providers --action configure --input provider.json --json
byax providers --action list --json
byax providers --action preview --alias my-reviewer --text "Text selected for review" --json
byax providers --action test --alias my-reviewer --allow-remote --json
byax providers --action review --alias my-reviewer --text "Text selected for review" --allow-remote --json
```

Enter secret values locally, never in documents, templates, source exports or version control. The GUI can store keys in an available OS keyring (`keyring:alias`); an explicitly named environment variable (`env:VARIABLE`) is also supported. If no usable keyring is available, the app reports failure rather than falling back to plaintext storage.

## Adapters

| Kind | Default endpoint | Text review | Audio transcription |
| --- | --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | Chat completions JSON review | Audio transcription endpoint |
| `anthropic` | `https://api.anthropic.com/v1` | Messages API | Not provided by this adapter |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | GenerateContent | Not provided by this adapter |
| `ollama` | `http://127.0.0.1:11434` | Local chat API | Not provided by this adapter |
| `elevenlabs` | `https://api.elevenlabs.io/v1` | Not provided by this adapter | Speech-to-text endpoint |
| `whisper.cpp` | Explicit local executable | Not provided by this adapter | Local WAV recognition |

Choose a model actually available to your account/local installation. Connection testing establishes reachability, not verified model inference or linguistic quality. No model keys were available in the development environment; automated adapter tests use HTTP fixtures. Supported language quality is model dependent; English is the primary test language.

Hosted audio adapters accept declared `.wav`, `.mp3`, `.m4a`, `.mp4`, `.ogg`, `.webm`, `.flac` extensions subject to the chosen model/provider's decoder support and byte limits. An extension does not prove the remote decoder accepts every codec inside that container. Errors remain explicit. Automatic media conversion is not performed.

## whisper.cpp

Install/build [whisper.cpp](https://github.com/ggml-org/whisper.cpp) locally and obtain compatible model weights under their own license. Configure an absolute executable and model path:

```json
{
  "alias": "local-speech",
  "kind": "whisper.cpp",
  "model": "/absolute/path/ggml-base.en.bin",
  "executable": "/absolute/path/whisper-cli",
  "timeout": 300
}
```

The adapter invokes an argument array without a shell. It requires **16 kHz mono 16-bit PCM WAV**, bounds input/output and execution time, and terminates the process on cancellation. The application does not download weights or run executables named by document content.

```bash
byax providers --action configure --input local-speech.json --json
byax transcribe recording.wav --alias local-speech --mode direct --json
```

For cleanup/document-aware modes select `--cleanup-alias` independently. To insert, supply `--document`, `--target`, `--expect-revision`, and `--insert`. A stale revision or changed destination conflicts instead of overwriting manual edits. The original recognized transcript remains available when cleanup fails.

## Outcomes, privacy and costs

Review output is validated advisory data. Proposed changes are reviewed against current text; model outputs are never directly executed as shell or canonical operations. Source-backed findings cite selected source IDs and exact excerpts. Evidence presence is checked; model interpretation is not claimed as proof of truth.

Requests are bounded and do not follow redirects. They are not retried automatically unless you choose the `unsent_only` policy described below, which never repeats a delivered request. A timed-out or interrupted hosted request may have completed remotely, so it is recorded as uncertain. Cancellation cannot prove a remote request stopped. Usage is shown when returned by the API; unavailable price estimates remain unavailable.

Provider settings and job records live in the platform's ByAuthorX config/data directories. They are distinct from portable `.byax` files. Audio retention and transcript retention are separate concerns; inspect the selected workflow's retention settings before recording.

## Provider controls

Each provider configuration also accepts:

| Field | Default | Meaning |
| --- | --- | --- |
| `timeout` | `60` | Seconds for the whole request, including retries and reading the response |
| `retry_policy` | `"never"` | `never`, or `unsent_only`: retry only when the request provably did not reach the provider (connection refused, TLS/connect failure) or the provider rejected it before processing (HTTP 429, honouring `Retry-After` up to 30 s) |
| `max_retries` | `0` | 0–5; requires `unsent_only` |
| `max_input_chars` / `max_audio_bytes` | `100000` / `25000000` | Checked before anything is sent; failures report the measured size and the limit |
| `cost_rates` | `null` | Optional rates you supply, for example `{"input_per_million": 2.5, "output_per_million": 10, "currency": "USD", "source": "my plan, checked 2026-10"}`. `source` is required |

Delivered requests are never retried: an HTTP error, a timeout after the request was sent, or an interrupted response stays `provider_failure` or `uncertain_external_outcome`, so a billed request is never repeated without you deciding to. Results include `transport.attempts`.

Usage reported by the provider is normalised into `usage_summary` (`input_tokens`, `output_tokens`, `reported`). `cost` is always `null` because the application does not know what you were actually charged. `cost_estimate` appears only when you supplied `cost_rates`. It is computed as your rates multiplied by the usage the provider reported, is marked `exact: false`, and names your rate source. When the provider reports no usage, the estimate amount is `null` with a reason, never a guess.

**Credentials.** `credential_status(alias)` reports the reference, its storage (`environment` or `os_keyring`) and whether a value is currently available, never the value. A missing credential stops the request before any network activity (`missing_dependency`). An expired or revoked key is only detectable when the provider answers 401/403. That answer is reported as `provider_failure` with `credential_status: "rejected"` and update guidance, without echoing the response body.

**Payload inspection.** `preview()` for a review returns the exact JSON body and path that `review()` would send, the names of the authentication headers (values redacted), the serialized size against the limit, the timeout and retry policy, the saved remote-authorization flag, and the credential status. For audio, it returns the file path, size, SHA-256 and WAV duration when known. The GUI shows this preview before every remote operation.

**Defaults and overrides.** Each role (`review`, `recognition`, `cleanup`) can have a simple default plus per-check and per-workflow overrides, stored as aliases in `routing.json` beside `providers.json`:

```python
registry.set_route('review', 'my-reviewer')  # default
registry.set_route('review', 'fact-model', check='factual')  # per check
registry.set_route('cleanup', 'local-llm', workflow='cleanup')  # per workflow
registry.resolve('review', check='factual')  # {'alias': 'fact-model', 'source': 'check:factual'}
```

Resolution order is explicit choice, workflow override, check override, the active profile's `providers` mapping, then the default. An alias whose adapter lacks the role's capability is rejected. Example: an Anthropic alias cannot be the recognition provider. When no provider is configured, the error says that AI function is unavailable and `local_writing: "available"`. Writing, checking and export never depend on a provider.

## Streaming recognition

`list_capabilities()` declares `streaming` per adapter, and only adapters with an implemented incremental path claim it:

| Kind | `streaming` | What arrives while recognition runs |
| --- | --- | --- |
| `openai` | true | Server-sent `transcript.text.delta` hypotheses (provisional) and a final `transcript.text.done`. Model dependent: a model without streaming support answers with one JSON result, which is reported as `streamed: false` |
| `whisper.cpp` | true | Each `[start --> end] text` segment that whisper-cli prints while decoding, as a final phrase with timestamps |
| `elevenlabs` | false | The batch speech-to-text result. Word timings are grouped into timestamped sentence segments. The realtime WebSocket API is not implemented |
| text adapters | false | Not applicable |

`transcribe_stream(alias, audio, on_event=...)` emits `{"type": "provisional", "text"}` and `{"type": "final", "text", "start", "end", "index"}` events. Non-streaming adapters emit their final segments once recognition completes. A stream that ends without a final transcript is `uncertain_external_outcome`, and partial text is never committed. If whisper.cpp fails, the error carries `completed_segments`. Request `timestamps=True` from `transcribe()` to ask OpenAI for `verbose_json` segment timings. Some models reject this, so it is opt-in.

## Dictation pipeline

Microphone recordings and imported files go through the same `DictationSession` and the same adapter decoder checks. In Tools → Dictation you choose:

- **Insert at**: the cursor, the start of the selection, or a bookmark.
- **Processing**: direct, spelling and grammar cleanup, or document aware. Direct mode has opt-in deterministic handling for fillers (`um`, `uh`), repeated words and spoken punctuation (“comma”, “period”). The recognizer transcript is always kept for comparison.
- **Application**:
  - *Suggestions only*: nothing is inserted until you review the comparison.
  - *Insert final phrases automatically*: each final phrase is one canonical edit.
  - *Insert phrases, then apply authorized cleanup*: the processed text replaces the committed dictation through a guarded edit.
- **Context**: nearby paragraphs (0–10), current section, whole document, the document and profile dictionary, writing samples, and selected references. Nothing else is sent. Cleanup mode sends only dictionary terms and protected passages, never document text. Document-aware mode sends the selected blocks. The authorization preview lists every included part and its size.
- **Retention**: separate choices for the raw transcript (with timestamps) and the audio, with the storage each would use and the total already retained.

Provisional hypotheses are shown in italics outside the document. They never create document revisions or undo entries. Each final phrase is inserted at a stable anchor as a single revision. If the destination is edited, the anchor is relocated by its surrounding text. If the destination is removed or becomes ambiguous, the remaining phrases are kept as suggestions and nothing is overwritten. Late cleanup replaces the exact committed text only when it is still present (unchanged, or moved within its block). If you have edited or removed that text, the cleanup reports a `conflict` and leaves your text alone. Cleanup is also rejected, and the direct transcript kept, when it changes approved dictionary terms, rewrites a protected passage, or exceeds the profile's editing freedom (`minimal` and `moderate` require at least 70% and 40% similarity). Dictated words such as “delete this paragraph” are always inserted as text.

The recognized-segments list shows audio timestamps. Activating a segment selects its current text in the document and shows its audio range and file.

**Interrupted sessions.** While a dictation runs, a journal in the platform state directory (`ByAuthorX/dictation`) records audio progress (about once per second), completed segments, the anchor and submitted job IDs. After a crash, “Recover interrupted sessions…” reports:

- completed segments, which you can insert
- pending jobs, which are never resubmitted: hosted jobs stay uncertain until you check the provider
- the audio, rebuilt into a `.recovered.wav` copy when the WAV header was never finalized

The report also discloses audio after the interruption that was not captured, and audio after the last recognized segment. A finished session deletes its journal.

**Retention store.** Retained material is written to the platform data directory (`ByAuthorX/dictation/<session>/record.json`), with an `audio.*` file when you keep a recording. Records link to the audio by path and SHA-256. Imported audio is linked in place and never deleted. A recording that you choose not to keep is deleted when the session finishes. `RetentionStore.usage()` reports transcript and audio bytes separately.

## Live acceptance evidence

Automated tests use mocked HTTP transports and fixture processes only. To record dated real-inference evidence with your own accounts, set the provider variables documented in `scripts/provider_smoke.py` and run:

```bash
python scripts/provider_smoke.py --allow-remote --out provider-evidence.json
```

Each provider runs only when its credential and model variables are present. Hosted calls also need `--allow-remote`. For every configured provider, the report records the date, the declared capabilities, the model and language, the connection test, text review, batch transcription and the streaming outcome. Secrets are read through `env:` references and are never printed.
