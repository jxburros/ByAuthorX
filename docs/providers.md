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

Requests are bounded and do not follow redirects or automatically retry. A timed-out or interrupted hosted request may have completed remotely, so it is recorded as uncertain. Cancellation cannot prove a remote request stopped. Usage is shown when returned by the API; unavailable price estimates remain unavailable.

Provider settings and job records live in the platform's ByAuthorX config/data directories. They are distinct from portable `.byax` files. Audio retention and transcript retention are separate concerns; inspect the selected workflow's retention settings before recording.
