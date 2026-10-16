# Context engine implementation plan

Status: **approved direction, not yet implemented**. Written 16 October 2026 against version 0.2.0 (commit `48ca3e3`). The maintainer's decisions are recorded in section 13.
Evidence: [context engine research](research/context-engine/README.md). The two reports are the
[retrieval and citation report](research/context-engine/retrieval-and-citation-report.md) and the
[competitor report](research/context-engine/competitor-report.md). Item codes such as Q5, R-rev and V1 refer to roadmap rows in those reports.

## 1 Goal

ByAuthorX aims to be the word processor whose answers about a writer's own manuscript and research can be **found, checked and trusted**, with or without AI. The research identified three trust failures that competitors still ship:

1. **Misattribution:** a real passage cited for a claim it does not support.
2. **False absence:** "that isn't in your sources" when it is.
3. **Broken links:** a citation that opens the wrong page, a stale draft, or nothing at all.

No product surveyed publishes an accuracy figure for any of the three. The engine described here prevents each one deterministically where it can and with local models where it cannot. It measures all of them, and it shows the writer what was searched, what the model saw, and why.

Success is defined by numbers from our own harness (section 4), not by feature count:

- **Retrieval:** recall@20 and nDCG@10 on the fixture libraries improve on the 0.2.0 baseline at every phase. They must never regress.
- **Citation fidelity:**
  - 100% of citation jumps land on the recorded page or offset in the fixtures.
  - 0 citations come from unattached or excluded libraries.
- **False absence:** 0 false "not found" answers on the absence probes when the term occurs literally. Semantic absence is reported separately.
- **Quote verification:** genuine PDF quotes pass after normalization. The pass rate before and after normalization is recorded.
- **Latency on named reference laptops:**
  - Keyword search: under 100 ms p95.
  - Hybrid search: under 300 ms p95.
  - Hybrid with reranking: under 1 s p95.
  - These targets apply at the 10,000-document fixture scale.

## 2 Invariants this plan must not break

These properties are the reason the research recommends ByAuthorX over cloud suites. Every work item is reviewed against them.

| Invariant | Where it lives today |
| --- | --- |
| **AI is opt-in, not opt-out.** No model sees the user's writing until the user configures a provider and chooses to use it. The user picks the provider, and local providers are first-class | `README.md`, `docs/user-guide.md`, `providers.py` |
| Keyword retrieval works with no model, network or download (spec §5.1) | `libraries.py` FTS5 index |
| No background network and no automatic model download. A download shows source, size and checksum and verifies before use (spec §5.3) | `library-and-citations-guide.md`, `providers.py` |
| `local_only` libraries never reach a remote provider. The rule is enforced where context is assembled, not where the model is picked | `retrieval.py` (`retrieve_context`, `authorize_library_context`) |
| Separate per-provider consent for `library_passages`, `library_embeddings` and `citation_lookup` | `retrieval.ConsentRegistry` |
| The user previews and approves passages before they are sent | `gui_retrieval.select_passages` |
| Document and passage text is untrusted data, never instructions | `providers.chat_instruction` |
| Derived indexes live in the user cache, never in a library folder or the document | `LibraryIndex.__init__` |
| Content-hash identities for documents and chunks; source versions recorded with citations | `libraries._identity`, `citations.py` |
| Parsers run in an isolated, resource-limited subprocess | `library_parser.py` |
| Every bounded result reports what was included and omitted, with continuations | `engine.context`, `retrieve_context` |
| Library, CLI, MCP and REST return the same envelope for the same inputs | `transport_policy.INTERFACE_INVENTORY`, `tests/test_interface_parity.py` |

New rule added by this plan: **every ranking, routing or verification decision is inspectable.** The writer, the CLI and MCP can see the method, the scores' provenance and the reason for each decision. No step may be an opaque cloud call.

## 3 Target architecture

```
                 ┌────────────── Context assembly (context_assembly.py) ───────────────┐
 request ──► Router (router.py) ──► route R0–R4 + reason                              │
                 │                                                                   │
                 ├─ R0/R1: engine.context (selection, nearby, profile, codex hits)   │
                 ├─ R2:    Retriever ───────────────────────────────────────────┐    │
                 ├─ R3:    agentic loop over the same read tools (budgeted)      │    │
                 └─ R4:    cached manuscript snapshot / chapter summaries        │    │
                                                                                 ▼    │
   Retriever (retrieval.py)                                                           │
     corpora: attached libraries + project manuscript blocks (project_index.py)      │
     ├─ lexical: FTS5 porter + trigram (libraries.py, search_query.py)                │
     ├─ dense:   vector store (vector_store.py) via EmbeddingProvider                 │
     ├─ fuse:    global RRF across corpora (ranking.py)                               │
     ├─ rerank:  optional cross-encoder (ranking.py, local_models.py)                 │
     └─ budget:  token budget, pinned items excluded, inclusion/omission report       │
                                                                                      │
   Prompt layout: stable prefix (tools, system, profile, memory, snapshot) → volatile │
   tail (selection, retrieved passages, turn) ─────────────────────────────────────────┘
                 │
                 ▼
   Provider call (providers.py) ── native citations where available, else [[cite:ID]]
                 │
                 ▼
   Grounding (grounding.py, quote_match.py)
     normalize citations → GroundingRecord(answer span → chunk → source span → quote)
     cascade: quote match → fuzzy align → local entailment → optional LLM judge
     states: supported / partial / unsupported / not_checkable / unanchored
                 │
                 ▼
   GUI: citation chips, "What the AI saw" inspector, support underlines, research log
```

### 3.1 New and changed modules

The layout stays flat (`src/byax/*.py`) to match the existing code. Each new module owns one responsibility, consistent with `docs/architecture.md`.

| Module | Status | Responsibility |
| --- | --- | --- |
| `search_query.py` | new | Parses a query into terms, quoted phrases, field filters, NEAR and negation, and builds safe FTS5 expressions. Also handles stopwords and RapidFuzz typo expansion against `fts5vocab` |
| `ranking.py` | new | Defines the `Hit` record, global reciprocal-rank fusion across corpora, de-duplication, MMR diversity and the reranker interface |
| `vector_store.py` | new | Vector storage behind one interface: BLOB float32 now, sqlite-vec later, LanceDB as an escape hatch. Stores model signature, chunk fingerprint and coverage |
| `local_models.py` | new, optional extra | ONNX runtime for local embedder, reranker and entailment models. Downloads only on explicit user action, verified by hash, stored under the user data directory |
| `quote_match.py` | new | Normalization (NFC, whitespace, quote marks, soft and line-break hyphens, ellipsis), exact and fuzzy alignment, and three-selector anchors (position, quote with 32-character prefix and suffix) |
| `grounding.py` | new | Normalizes native and token citations into `GroundingRecord`, runs the verification cascade, and audits the user's own citations |
| `router.py` | new | R0–R4 rules, escalation and abstention, and context-size-aware thresholds |
| `context_assembly.py` | new | Token budgets, cache-ordered prompt layout, the pinned and retrieved tiers, truncation detection and the inspector payload |
| `project_index.py` | new | A block-level index of the open `.byax` document and sibling project documents, using the existing stable block IDs |
| `codex.py` | new | Codex and glossary entries, an Aho-Corasick mention scan, aliases, case sensitivity, excluded phrases and hide-from-AI flags |
| `libraries.py` | changed | Index schema v2 (section 5.2), structure-aware chunker and heading column |
| `retrieval.py` | changed | `retrieve_context` becomes a thin orchestrator over the retriever, keeping its consent and policy checks |
| `embeddings.py` | changed | Batch requests, incremental updates by fingerprint, explicit partial-coverage state, and vectors stored through `vector_store` |
| `providers.py` | changed | Separates the stable system prompt from volatile context, sets `num_ctx` for Ollama, adds cache-control breakpoints, and supports native citation adapters (Anthropic custom-content blocks first) |
| `citations.py`, `library_checks.py`, `checks.py` | changed | Quote checks go through `quote_match`; RIS export; quotation kind inferred when citing |
| `gui_ai.py`, `gui_retrieval.py`, `gui_libraries.py` | changed | Citation chips, inspector, support underlines, extracted-text preview, local/remote labels |
| `mcp.py`, `cli_libraries.py`, `transport_policy.py` | changed | Hybrid search, entity, outline and verify tools, all read-only and kept at parity |

## 4 Phase 0: measurement first (Q1, Q1+)

No retrieval or grounding change merges until the harness can show its effect. This phase has no product-visible change.

**Fixtures.** Build them under `tests/fixtures/eval/` from redistributable material only, and record each item's license in a manifest.

| Library | Content (chosen 16 Oct 2026) | License | Purpose |
| --- | --- | --- | --- |
| `novel` | Jane Austen, *Pride and Prejudice* (Project Gutenberg #1342), plus two hand-made "earlier drafts" of six chapters with renamed minor characters and changed details, used as distractors | Public domain. Strip the Project Gutenberg header, footer and trademark text before committing | Continuity, entity lookup, aliases ("Lizzy", "Miss Bennet", "Elizabeth"), near-duplicate distractors |
| `series` | Arthur Conan Doyle, *The Adventures of Sherlock Holmes* (PG #1661) and *The Memoirs of Sherlock Holmes* (PG #834), with each story treated as a separate project document | Public domain | Cross-document and series continuity, shared codex across projects |
| `papers` | 30 research articles from *PLOS ONE* and *PLOS Computational Biology*, as PDF plus JATS XML | CC BY 4.0, attribution recorded in the manifest | Citation locating and page mapping on real PDF text. The JATS XML gives exact reference lists and text to compare extraction against |
| `humanities` | Edward Gibbon, *The History of the Decline and Fall of the Roman Empire*, vol. 1 (PG #731) | Public domain | Dense footnotes, long quotations, hyphenation, old spelling |
| `webclips` | About 40 Wikipedia articles on the Regency era, Victorian London and computational biology methods, saved as HTML with the page chrome kept | CC BY-SA 4.0, attribution and share-alike noted in the manifest. The fixture data keeps its own license and is not relicensed MIT | Messy markup and boilerplate. Queries that cross libraries and point back into the novels and papers |
| `scale` | Synthetic 10,000-document corpus, generated by `scripts/generate_fixtures.py` | Generated | Latency and index size only |

**Storage.** Plain-text fixtures (`novel`, `series`, `humanities`, `webclips`) are committed under `tests/fixtures/eval/`, a few MB in total. The `papers` PDFs are larger. `scripts/fetch_eval_fixtures.py` downloads them from pinned PLOS URLs, verifies a SHA-256 for each, and caches them, and CI caches the same set. Three small PDFs are committed so that offline unit tests still cover PDF paths. A `MANIFEST.json` records the source URL, license, attribution and hash of every fixture file.

**Golden queries.** Write at least 100 labelled queries per library in `tests/fixtures/eval/<library>/queries.jsonl`. Each record has `{id, type, query, relevant_chunk_locators, expected_absent?}` and uses one of the following types:

- `known_item_quote`
- `factual_lookup`
- `thematic`
- `citation_locating`
- `continuity`
- `absence_probe`

Labels are locators (document path plus page, block or offset), not chunk IDs, so that chunking changes do not invalidate them.

**Harness.**

- Add `src/byax/evaluation.py`. It computes recall@5 and @20, nDCG@10, MRR, p50 and p95 latency, index size and build time. It runs against any retrieval configuration.
- Add `scripts/eval_retrieval.py`. It writes `docs/benchmarks/context-<version>-<config>.json`, including hardware details, matching how the existing layout benchmarks are recorded.

**Fidelity tests (Q1+).** These live in `tests/test_context_fidelity.py`:

- **Jump fidelity.** Every hit's locator round-trips through `LibraryIndex.read` to text containing the cited quote, for all fixture PDFs.
- **Scope leaks.** No hit comes from an unattached, excluded or `local_only` library under a remote route.
- **False absence.** Each absence probe whose term occurs literally must be found by an exhaustive lexical pass.
- **Stability.** Identical queries return identical rankings when all models are deterministic.
- **Concordance.** Cited IDs are a subset of retrieved IDs.

**CI.** The keyword and fake-embedder configurations run in `pytest` on every push, with thresholds stored next to the fixtures. A regression beyond tolerance fails the build. Model-backed configurations run on demand: `pytest -m eval_models`, plus a workflow-dispatch job.

**Baseline.** Record 0.2.0 keyword-only numbers as `docs/benchmarks/context-0.2-baseline.json`. Every later phase reports against them.

**Exit criteria:**

- The baseline file is committed.
- The fidelity tests run, and currently failing cases are marked `xfail` with a linked work item.

## 5 Phase 1: wire, fix and harden, with no heavy dependencies (Q2–Q6, Q9, Q11–Q14)

This phase closes the specification gap (spec §5.1 hybrid retrieval) and the cheap bugs the audit found. The only new dependency is **RapidFuzz** (MIT). The maintainer approved it as a core dependency. It still passes the dependency gate in section 11: confirm the license, add notices, and check the frozen build.

### 5.1 Retrieval wiring (Q2, Q3, Q12)

- `retrieve_context` gains `method='auto'`.
  - It uses `hybrid_search` for each library whose embedding state is `ready`, and keyword search otherwise.
  - It reports `method` and `embedding_state` for each library in the result.
  - Callers: the GUI chat path (`gui_ai.py`), review (`gui_providers.py`) and MCP and CLI `library-search` (new `--method auto|keyword|hybrid`, default `auto`).
- **Partial vector sets.** `hybrid_search` reports `embedding_state: 'partial'` with a coverage ratio. It is still used, with a visible notice, and is never treated silently as `ready`.
- **Global fusion (`ranking.py`).** Collect up to `k` candidates from every eligible library, fuse them with RRF, de-duplicate by `document_id` and overlapping locator, and only then apply the passage and token budget. This replaces concatenation in attachment order (`retrieval.py:28-65`).
- **Pinned items (Q12).** Items attached in full to the request are excluded from retrieval results and reported as the `pinned` tier.

### 5.2 Index schema v2 and query handling (Q4)

The index is derived data, so the upgrade is a rebuild, not a document migration. Bump `CHUNKING['version']` and record `schema: 2` in `meta`. Old index files are marked stale and rebuilt by the existing job.

```sql
CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, document_id TEXT, ordinal INTEGER,
                     locator TEXT, heading TEXT, text TEXT, fingerprint TEXT);
CREATE VIRTUAL TABLE search USING fts5(chunk_id UNINDEXED, heading, text,
    tokenize="porter unicode61 remove_diacritics 2", prefix='2 3');
CREATE VIRTUAL TABLE search_tri USING fts5(chunk_id UNINDEXED, text, tokenize='trigram');
CREATE VIRTUAL TABLE vocab USING fts5vocab(search, 'row');
```

Query handling moves from `LibraryIndex.search` to `search_query.py`:

- Quoted strings become FTS5 phrases.
- Stopwords are dropped only when non-stopword terms remain.
- Remaining terms are combined with `OR`, with BM25 column weights (heading above text).
- `NEAR`, `-term` and `field:` filters are supported in the CLI and GUI syntax.
- When the porter pass returns fewer than `k` hits, a second pass runs on `search_tri` for substring and mid-word matches.
- RapidFuzz suggests corrections from `vocab` and shows them as "did you mean". Expansion is opt-in per query.

The CLI and GUI search syntax is documented in `docs/cli-reference.md` and the user guide.

### 5.3 Normalized quote verification (Q5, Q6)

- `quote_match.normalize(text)` applies these steps:
  - NFC normalization.
  - Collapse whitespace.
  - Fold curly quotes and apostrophes to straight.
  - Remove soft hyphens.
  - Join line-break hyphenation (`exam-\nple`).
  - Normalize ellipses (`…` and `...`, including bracketed elisions).
  - Fold dashes.

  It returns the normalized text and an offset map back to the original.
- `quote_match.locate(quote, source_text)` returns one of three results:
  - `exact` with the span.
  - `normalized` with the span mapped back.
  - `near` with a RapidFuzz `partial_ratio` alignment at or above a threshold, the score and the span.
  - Otherwise `none`.

  The threshold is set from the harness.
- **Callers.** `citations.verify_source` (`citations.py:342`), `library_checks.check_citation` (`library_checks.py:119`) and the evidence checks in `checks.py` (around 1056–1063).
  - `near` maps to a new advisory status, `quote_near_match`. It shows the differing characters and is never reported as `confirmed`.
- **Cite picker (Q6).** The GUI cite picker (`gui_libraries.py:517`) records `kind` as follows:
  - `quotation` when the selected text is in quotation marks or `locate` finds it in the source.
  - `paraphrase` otherwise.
  - The user can override the kind.

### 5.4 Embeddings that are incremental and honest (Q9)

- Store vectors as float32 BLOBs (`vector_store.py`) together with `model_signature` and `chunk_fingerprint`.
- Skip chunks whose fingerprint and signature are unchanged.
- Batch requests up to the provider's limit, with backoff and cancellation.
- One rebuild job reports done, skipped and failed counts. A changed model signature marks vectors stale (spec §5.2) and never mixes them.

### 5.5 Context budgets and truncation (Q11)

- **Token estimate.** `context_assembly.estimate_tokens(text, provider)` uses a provider tokenizer when one is available offline and a conservative characters-per-token ratio otherwise. Budgets move from characters to tokens. The character limits in `providers.py` remain as hard safety caps.
- **Ollama `num_ctx`.** Requests to Ollama set `options.num_ctx` from the assembled token count plus response headroom, capped by a per-provider setting. If the payload still exceeds the model's real context, the request fails before sending with `limit_exceeded` and reports "N tokens needed / M available". Nothing is silently truncated.

### 5.6 Transparency basics (Q13, Q14)

- **Local or remote labels (Q13).** Every provider and embedder shows a local or remote label in the library panel and the payload preview. The library panel adds an **extracted text preview** per file, showing the parsed text and locators exactly as indexed.
- **Verified absence (Q14).** Before the assistant or a check reports "not found in your sources", run an exhaustive lexical pass (porter and trigram, every eligible library plus the manuscript) over the key terms. Show the user what was searched. If literal matches exist, say so and offer them instead of the model's answer.

**Exit criteria:**

- Every item above is shipped behind tests, and the docs are updated: the library guide, the CLI reference and the acceptance matrix rows for spec §5.
- The harness shows:
  - hybrid search at or above keyword search on every library;
  - fusion fixing the first-library starvation case;
  - the normalized quote pass rate reported;
  - the Q1+ fidelity cases un-`xfail`ed.

## 6 Phase 2: citations you can click and verify (Q7, Q8, V1, A1)

### 6.1 GroundingRecord

```python
GroundingRecord = {
    'answer_span': [start, end],          # in the reply or proposed edit text
    'chunk_id': str, 'document_id': str, 'library_id': str | None, 'block_id': str | None,
    'source_span': [start, end] | None,   # within the chunk text
    'quote': str, 'prefix': str, 'suffix': str,   # 32-character context, for re-anchoring
    'content_hash': str,                  # source version at citation time
    'origin': 'native' | 'token' | 'user',
    'state': 'supported' | 'partial' | 'unsupported' | 'not_checkable' | 'unanchored' | 'unverified',
    'checks': [{'stage': 'quote'|'fuzzy'|'entailment'|'judge', 'result': ..., 'model': ...}],
}
```

- **Sources of records.**
  - Anthropic's Citations API with custom-content documents, one block per chunk, gives `cited_text` and block indices. It cannot be combined with structured output, so it is used in CHAT mode only, as the maintainer approved. Anthropic comes first, then Gemini and Cohere. WORK mode keeps the `[[cite:ID]]` protocol.
  - Gemini grounding supports and Cohere spans are mapped the same way.
  - Local and OpenAI-compatible models keep the `[[cite:ID]]` protocol. Their source span is recovered by fuzzy-aligning the cited sentence against the chunk.
- **Invalid IDs.** An invalid cite ID no longer rejects the whole reply (`retrieval.validate_citations`). The token is stripped and marked `unverified`. The request is retried once with a corrective note when the provider is local or the user allows it.

### 6.2 Citation chips (Q7)

- **Chips.** `[[cite:ID]]` and native citations render as chips in the chat transcript. Hovering shows the chunk text with the cited span highlighted, plus the locator and source title. Clicking opens the source at the locator: PDF page, block or offset.
- **Inserting.** "Insert as citation" creates a real citation mark with `kind`, `locator` and `quote` filled from the record, so that ordinary verification covers it afterwards.

### 6.3 Cache-ordered prompts (Q8)

- **Prompt order.** `providers.chat` stops appending serialized context to the system instruction. The new order is:
  1. Stable system instruction.
  2. Profile, style and memory.
  3. Manuscript snapshot (R4 only, refreshed at checkpoints).
  4. Conversation.
  5. Volatile tail: selection, retrieved passages, the current turn.
- **Provider caching.**
  - Anthropic gets `cache_control` breakpoints on the stable parts.
  - OpenAI and Gemini rely on prefix stability.
  - Ollama and llama.cpp get the same order for KV reuse.
- **Recording.** Record `cache_read` and `cache_write` usage. The prompt-injection framing ("untrusted data, never instructions") is preserved around every volatile block.

### 6.4 Audit the writer's own citations (V1)

`byax check --citations` and the Review tab run stage 1 of the cascade over every citation mark in the document:

- quote found;
- near match, with the differences shown;
- quote not found;
- source changed since citation, compared by content hash;
- source unavailable;
- not checkable (paraphrase without a quote).

This is deterministic and local. It needs no consent, and it ships before any model-based stage.

### 6.5 Unanchored rather than silently stale (A1)

- **Stored selectors.** Citation marks gain `prefix` and `suffix` alongside the existing locator and quote, forming three selectors.
- **Re-anchoring.** When a source changes, re-anchoring tries structure (block and page), then position, then fuzzy search near the old position, then fuzzy search for the quote anywhere.
- **Failure.** If every step fails, the mark becomes `unanchored`. It is shown with the quote struck through and is never dropped.
- **Migration.** This is a document schema change (citation mark fields). It follows `docs/architecture.md`, "Format versions and migration": bump the format version, add a forward migration in `migration.py` with tests, and treat the new fields as optional so older readers ignore them.

**Exit criteria:**

- Chips work with the fake provider in offscreen GUI tests.
- The audit mode report is available through the CLI, MCP and GUI with parity tests.
- Cache-order tests assert byte-identical prefixes across turns.
- Jump fidelity is 100% on the fixtures.

## 7 Phase 3: local semantic tier and reranking (medium-term items 1–4 and 8)

**Included by default, with an option to leave it out** (maintainer decision). The runtime (`onnxruntime`, `tokenizers`) and the on-device search models ship in the **standard installer**. A **lean installer** variant leaves them out. Source installs get them through the `byauthorx[local-models]` extra. Keyword retrieval still works with no model present (spec §5.1, §5.3 as amended).

- **Mirror.** Model files are too large for the Git repository: GitHub rejects files over 100 MB, and Git LFS would bloat clones and spend bandwidth quota. They are therefore mirrored as assets on a dedicated GitHub Release in this repository, tagged `models-v<N>`, with a `SHA256SUMS.txt`. The project controls that copy, and it does not change when upstream does. Each mirrored model keeps its upstream license and NOTICE file next to it.
- **Model catalogue.**
  - `local_models.py` reads a pinned catalogue (`resources/models.json`) listing each model's name, version, license, upstream source, mirror URL, size and SHA-256.
- **Packaging.**
  - `release.yml` and `scripts/build.py` download the catalogue models from the mirror, verify the hashes, and bundle them into the standard Windows installer and `.deb`.
  - They also build the `-lean` variants without models.
  - `SHA256SUMS.txt` covers both variants, and `docs/packaging.md` documents both.
- **Lean installs.** On a lean install, models are fetched only from an explicit button or `byax models download <name>`. The dialog shows source, size and checksum (spec §5.3). Files are verified before use and stored under the user data directory. Nothing downloads in the background.
- **Turning it on.** Bundled models are local and send nothing off the computer, but semantic indexing still starts only when the user turns it on for a library. The prompt reads: "Enable on-device semantic search? Nothing leaves this computer." This keeps the product consistent with "AI is opt-in" while making the local tier one click away.
- **Size budget.** The added installer size is measured, not assumed. Int8 ONNX exports of 0.6B-parameter models are several hundred MB each. If the embedder and reranker together exceed about 800 MB, the harness compares a smaller reranker (for example answerai-colbert-small, 33M parameters, license to verify) before the larger one is bundled.
- **Initial catalogue entries**, with licenses re-verified at the gate:
    - Qwen3-Embedding-0.6B (Apache-2.0), as int8 ONNX truncated to 512 dimensions by Matryoshka. The dimension is chosen by the harness.
    - bge-reranker-v2-m3 (license to verify), as int8 ONNX.
- **Entailment model.** The Phase 5 entailment model joins the same catalogue and mirror. It is bundled only if its license and size pass the gate.
- **Embedder.** A `LocalEmbeddingProvider` satisfies the existing `EmbeddingProvider` protocol (spec §5.2), so `local_only` libraries can have semantic search with no Ollama.
- **Vector search.**
  - `vector_store.py` gains a sqlite-vec backend (license to verify) with binary quantization and float re-scoring.
  - Brute force is acceptable up to the measured limit.
  - LanceDB stays an escape hatch behind the same interface and is only adopted if the `scale` fixture fails its latency target.
- **Reranker.** A cross-encoder reranks the fused top 30–50.
  - Settings: "Fast" (off) and "Accurate" (on).
  - It turns on by default only if the harness shows a gain and the p95 stays under 1 s on reference hardware.
- **Structure-aware chunker** (`libraries.chunk_passages` v3).
  - Merges short paragraphs up to a target token size and never crosses a heading.
  - Prepends the heading path to the indexed text, keeping the stored text unchanged so quotes still verify.
  - Keeps `parent` pointers to support expanding a hit into its wider section.
  - **The target size is swept by the harness (200–1,000 tokens), not fixed.** The research found the evidence for any single default anecdotal.
- **Contextual retrieval (opt-in background job).**
  - A local model, or a remote one with `library_embeddings` consent and caching, writes a 50–100-token situating note per chunk.
  - The note is indexed in a separate column, never shown as source text, and never quoted.
  - Late chunking is the zero-LLM alternative and is evaluated in the same harness.
- **PDF quality path (optional extra `byauthorx[docling]`).**
  - Docling (MIT) runs in the existing parser subprocess.
  - It keeps page and bounding-box provenance for each chunk and imports PDF annotations as linked notes.
  - pypdf stays the default.
  - PyMuPDF is excluded: its AGPL license conflicts with the project's MIT license.

**Exit criteria:**

- The harness shows hybrid search with the local embedder and reranker beating Phase 1 hybrid on the thematic and continuity queries, within latency targets.
- Model download, hash verification, failure and offline behaviour are covered by tests that use a local fixture server.
- Both the standard and lean installers build in CI, and the size of each is recorded.

## 8 Phase 4: router, inspector, manuscript corpus and codex (router, R-rev, R-meta, C1, C2, L1, medium-term 5–6)

### 8.1 Manuscript as a corpus

`project_index.py` indexes the open document and sibling `.byax` files in the same project at block granularity:

- It reuses stable block IDs, so hits and citations point to blocks, not to offsets that drift.
- It updates incrementally on save and on explicit refresh, never in the background over the network.
- It uses the same FTS5 v2 schema and optional vectors as the libraries.

This provides:

- **project-wide find** across the open document and sibling documents;
- **saved searches** that update themselves (Scrivener-style collections);
- **backlinks and unlinked mentions** for headings, bookmarks and codex entries.

### 8.2 Codex and memory as project artifacts (C1)

- **Codex entries.** Entries (character, place, item, term) carry aliases, a case-sensitivity flag, excluded phrases, traits, and **hide from AI** at entry and trait level. They are stored in the document model, edited through the undo stack, and versioned.
- **Mention detection.** `codex.py` builds one Aho-Corasick automaton (pyahocorasick, BSD-3, or a pure-Python fallback) over every name and alias. It scans the selection and nearby text for R1 context and the whole manuscript for a mention heatmap.
- **Proposals.** The assistant never writes the codex directly. It proposes facts ("Mara's eyes are grey, ch. 3 ¶12") with provenance, through the existing review-proposal machinery, together with created and updated dates.
- **Series sharing (C2).** A codex and source libraries can be attached at series level, so several projects share them.

### 8.3 Router (R0–R4, R-rev, R-meta)

`router.py` picks a route with deterministic rules first:

- whether there is a selection;
- imperative or interrogative phrasing;
- codex hits;
- scope words ("whole book", "chapter 4");
- a model context size that is known and adequate.

The routes are as follows.

| Route | Context assembled | Escalation |
| --- | --- | --- |
| R0 selection | Selection, profile, protections | — |
| R1 local | Selection, ±N blocks, chapter summary, detected codex entries | R2 when an unknown entity is referenced |
| R2 lookup | Codex first, then hybrid retrieval over the manuscript and libraries, scoped and ranked by metadata (chapter, POV, tag) | R3 when the sufficiency check fails |
| R3 multi-hop | Budgeted tool loop over `search`, `semantic_search`, `get_outline`, `read_range`, `get_entity` | R4, or abstain after verified absence (Q14) |
| R4 full document | Cached snapshot when it fits the model's **real** context, otherwise chapter summaries | — |

- **R4 threshold.** Full context is used only below a model-dependent threshold: about 20,000 tokens for local models, larger for providers with verified long context. Top-k scales with context size: 3–5 at 8K or less, 15–25 at 32K or more.
- **Overrides.** The route and its reason are always shown, and the user can override them.
- **Privacy first.** Privacy rules run before any other rule. A `local_only` source forces a local model or excludes that source, and the inspector says which.
- **Learned routing.** A learned classifier is only considered once the harness has route-accuracy labels.

### 8.4 "What the AI saw" inspector and research log (L1)

- **Inspector.** A panel in the AI tab shows, for each request:
  - the route and reason;
  - pinned and retrieved items, each with token count, score source, library, local or remote, and pin and exclude controls;
  - the model and whether it is local or remote;
  - cache read and write;
  - the estimated cost, using the existing user-supplied rates;
  - anything dropped by budget or policy.

  Excluding an item and re-running is one action.
- **Research log.** For R3 runs, the log records each query issued, the candidate counts, what was reranked and kept, and what was cited, PRISMA-style. It is exportable with the conversation.

### 8.5 MCP and CLI parity (M1)

- **New read-only tools.**
  - `byax_library_search` with `method`.
  - `byax_project_search`.
  - `byax_entity`.
  - `byax_outline`, which is already covered by `inspect` and only needs its parameters exposed.
  - `byax_verify_citations`, which returns `GroundingRecord`s.
- **Rules.**
  - All of them are bounded, with continuations.
  - All of them are listed in `INTERFACE_INVENTORY` and are parity-tested.
  - None of them can configure libraries or embeddings (unchanged policy).
- **Differentiator.** These tools return **verified spans with support states**, not just text.

**Exit criteria:**

- Route accuracy is measured on labelled requests.
- The inspector is tested offscreen.
- Codex mention precision and recall are measured on the `novel` fixture.
- The project search latency target is met.
- MCP tools pass the parity tests.

## 9 Phase 5: model-based verification and bibliography integrity (V1, B1, Z1, medium-term 7 and 9)

- **Entailment (cascade stage 2).**
  - Claims are split into sentences, with simple clause splitting.
  - Each claim is scored against its cited span by a local entailment model from the catalogue. HHEM-2.1-Open is the first candidate, subject to a license check. Bespoke-MiniCheck is excluded because of its non-commercial license.
  - Results appear as advisory underlines: supported, partial or unsupported. They are categorical, never percentages.
  - This applies to AI claims and, in audit mode, to the writer's paraphrase citations.
  - Agreement with human labels is reported in the harness before it ships.
- **LLM judge (stage 3).** Optional and BYOK, using a different provider from the generator. It runs only on low-confidence cases and only under existing consent.
- **Bibliography health panel (B1).**
  - Checks:
    - unresolved or invalid DOIs;
    - metadata mismatches against Crossref and DataCite (fuzzy title, author and year), with OpenAlex as fallback;
    - retractions, from Crossref `update-to` relations or an offline Retraction Watch CSV imported by the user;
    - in-text citations versus the reference list;
    - citation density and reference age;
    - style-specific punctuation placement;
    - dead links, with an optional archived copy.
  - All network lookups go through the existing, so far unused, `citation_lookup` consent category.
  - "Not found" displays as "unverified, please check", never as "fake".
- **Bibliography exchange.**
  - RIS export.
  - Complete RIS import: ISBN, abstract and editors.
  - Additional CSL styles loaded from the CSL repository (CC BY-SA 3.0 styles, attributed in `docs/citation-licenses.md`).
- **Zotero (Z1).**
  - Read the Zotero local API (`localhost:23119`, read-only).
  - Import annotations with locators as linked notes.
  - Export `.bib` with citation keys.
- **Excerpts (A2).** Live excerpts with a back-pointer to the source, and a "where is this source used" view. Deleting a source leaves its excerpts unanchored and never erases them.

**Exit criteria:**

- Entailment agreement and false-positive rates are published in `docs/benchmarks/`.
- Bibliography checks run against recorded HTTP fixtures in CI; live checks are opt-in.

## 10 Phase 6: differentiators (12 months)

- **Claim-level provenance.** A per-span log records whether text was human-typed, AI-generated, AI-edited or pasted, together with the model and sources. Every AI-originated sentence carries a `GroundingRecord` that re-anchors when either side is edited.
- **Disclosure.** The provenance log can generate APA, MLA and Chicago AI-use disclosures. A C2PA text manifest is offered as an optional export only.
- **Version-pinned footnotes.** CSL footnotes are pinned by content hash, so a citation to a superseded draft is detected rather than hidden.
- **Temporal story knowledge base.** Codex facts are valid "as of chapter X", which powers continuity checks across chapters and books.
- **Hierarchical summaries.** RAPTOR-style chapter summaries let R4 run on small local models.
- **Optional LLM reranker.** Offered for thematic R3 queries only.
- **Published benchmarks.** Retrieval, citation-fidelity, false-absence and support-check benchmarks are published on named reference hardware for each release. The research found no other vendor publishes these. That makes the benchmarks a product feature.

## 11 Cross-cutting rules

**Dependency gate.** Before any new dependency or model is added:

1. Verify its license from the primary source and record it in `docs/third-party-notices.md`. The project is MIT; AGPL, GPL and non-commercial terms are excluded from bundled code and catalogue models.
2. Measure its effect in the harness.
3. Confirm that frozen packaging (`scripts/build.py`) still works on both target platforms, or make it an optional extra.

Pending verifications from the research: sqlite-vec, bge-reranker-v2-m3, answerai-colbert-small, Qwen3-Reranker, HHEM-2.1-Open, citeproc-py, GROBID and YAKE. YAKE's license reports conflict; prefer FTS5 vocabulary statistics.

**Security.**

- Retrieved text stays inside the untrusted-data framing, with provenance delimiters (spotlighting).
- The R3 loop has only read tools. It never combines private data, untrusted content and an outbound tool without confirmation (the "lethal trifecta").
- MCP keeps its no-configuration policy.

**Performance.**

- Indexing stays a cancellable, resumable job with per-file leases.
- Keyword search is available immediately and vectors fill in afterwards.
- Search is never on the UI thread.

**Positioning (P1), decided.**

- **AI is opt-in, not opt-out.** No model sees the user's writing until the user configures a provider and chooses to use it.
- **Users choose their own provider:** a hosted service with their own key, or a local model on their own computer.
- **Search first.** Retrieval, search, codex, continuity and citation audits are presented first, because they work without generative AI.
- **Where it is stated.** These statements are in `README.md` and `docs/user-guide.md` now. Each new AI-facing surface in this plan repeats them in plain language. Examples are the first-run prompt for on-device semantic search, the inspector, and the provider setup dialog.

**Docs per phase.** Following [Keeping docs honest](README.md#keeping-docs-honest), each phase updates the documents it affects:

- `docs/library-and-citations-guide.md`, `docs/cli-reference.md` and `docs/architecture.md` (modules and index schema);
- `docs/acceptance-matrix.md`, where every claim must be backed by tests or by measured numbers on recorded hardware;
- the `CHANGELOG.md` entry at release.

## 12 Sequencing and sizing

| Phase | Depends on | Rough size | Can ship as |
| --- | --- | --- | --- |
| 0 Measurement | — | 1–2 weeks | 0.3 internal |
| 1 Wire and harden | 0 | 3–4 weeks | 0.3 |
| 2 Click-and-verify citations | 1 (quote_match), 0 | 3–4 weeks | 0.3 or 0.4 |
| 3 Local semantic tier | 1 (vector_store), 0 | 4–6 weeks | 0.4 |
| 4 Router, inspector, manuscript, codex | 1, 2; benefits from 3 | 6–8 weeks | 0.5 |
| 5 Verification and bibliography | 2; entailment needs 3's model runtime | 5–7 weeks | 0.5 or 0.6 |
| 6 Differentiators | 2, 4, 5 | ongoing | 0.6+ |

- **Parallel work.** Phases 2 and 3 can run in parallel after Phase 1. The codex and project index in Phase 4 do not depend on any model and can start early.
- **Pull requests.** Each phase splits into reviewable pull requests that each pass the CI gates (`ruff format --check`, `ruff check`, `mypy`, `pytest` offscreen) and the harness thresholds.

## 13 Decisions (recorded 16 October 2026)

| # | Question | Decision | Where it is applied |
| --- | --- | --- | --- |
| 1 | RapidFuzz as a dependency | Approved as a core dependency | Phase 1 (section 5) |
| 2 | Model hosting and shipping | Mirror under project control on a `models-v<N>` GitHub Release, since GitHub rejects files over 100 MB in the repository. Include in the standard installer, with a lean installer to opt out | Section 7; spec §5.3 amended |
| 3 | Native citation APIs | Yes. CHAT mode first, starting with Anthropic. WORK mode keeps `[[cite:ID]]` | Section 6.1 |
| 4 | AI positioning | AI is opt-in, not opt-out. Users choose their own provider, local providers included | Section 2, section 11; `README.md`, `docs/user-guide.md` |
| 5 | Evaluation fixtures | *Pride and Prejudice*, the Sherlock Holmes *Adventures* and *Memoirs*, Gibbon vol. 1, 30 PLOS articles (CC BY 4.0), about 40 Wikipedia articles (CC BY-SA 4.0) and a synthetic scale set | Section 4 |

**Open points left by these decisions:**

- **Bundled models and AI opt-in.** The bundled models still need a user's opt-in before indexing (section 7). The maintainer can choose instead to turn on-device semantic indexing on by default, since nothing leaves the computer.
- **Fixture replacement.** The fixture set can be swapped for material closer to ByAuthorX's target writers, provided the replacement is redistributable.
