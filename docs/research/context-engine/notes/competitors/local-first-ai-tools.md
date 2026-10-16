# Local-first / On-device AI Tools: Document Context and Retrieval (as of 2026-10-16)

Research date: 2026-10-16. About 20 tool calls. Primary docs were used where they were reachable. Several official doc URLs returned 404: Open WebUI /features/rag and /getting-started/env-configuration, and the PrivateGPT storage page. Secondary or aggregator sources are marked **[secondary]**. Items marked **[unverified, prior knowledge]** come from training knowledge and were not confirmed this session. The report writer should treat them as leads, not facts.

## Q1: What are the proven local-first retrieval designs and UX patterns ByAuthorX should adopt or avoid?

### Takeaway
The products have converged on the same design. Documents that fit go in whole as "full context" or "pinned" text. Everything else goes through chunk + embed + top-k retrieval: roughly 1000-character or token chunks with 10–20% overlap, 3–6 chunks for small-context local models, and a similarity floor. Hybrid lexical+semantic search with optional reranking is the quality upgrade. The main local failure is not retrieval at all. It is a small default context window (Ollama 2K/4K) silently truncating whatever was retrieved. ByAuthorX already has SQLite FTS5 plus optional embeddings, which is the hybrid design Microsoft's own AppContentIndexer uses.

### Cited Findings

**Microsoft: Windows semantic indexing / Windows AI APIs**
- Copilot+ PCs run semantic indexing alongside traditional indexing. It is on by default, and users can turn it off for specific locations or file types. Indexed data stays on the PC (or local to Cloud PC storage on AI-enabled Windows 365). Microsoft says none of it is stored by Microsoft or used to train models. Example from the page: searching "pasta" also surfaces "lasagna". — [Microsoft Support: Find your files and apps in Windows](https://support.microsoft.com/en-us/help/17161)
- Semantic search requires Copilot+ PC hardware with an NPU. The early 2024 rollout was Snapdragon-only, with 40+ TOPS NPUs, and AMD/Intel came later. — [getcoai.com aggregator **[secondary, 2024, likely outdated]**](https://privacy.getcoai.com/news/microsoft-is-enhancing-windows-search-for-copilot-pcs)
- Sources conflict on cloud files. One review says results include cloud-only items, while an earlier report says only locally stored, indexed files are searched. — [twit.tv **[secondary]**](https://twit.tv/posts/tech/what-can-windows-11s-semantic-search-do-you)
- **App Content Search / AppContentIndexer (developer API).** Apps index their own text and images. One plain-string query returns both **lexical matches** (exact text, including OCR'd text in images) and **semantic matches**. The embedding model, vector store and text index are "fully abstracted". The index is persisted to disk, so there is no re-index on launch. The API returns only **app-defined ContentIds**, and the app fetches the content from its own store. The docs explicitly list RAG as a use case. As of the doc's last update on 2026-07-15 (ms.date 2025-11-17), the API is "currently only available in Windows App SDK release 2.0 Experimental 4". Privacy: the index lives in the app's local app-data folder. The experimental release is "intended for indexing non-sensitive application content". The docs advise against indexing personal or confidential user data, and say there is no content moderation or bias mitigation. — [Microsoft Learn: App Content Search Overview](https://learn.microsoft.com/en-us/windows/ai/apis/app-content-search)
- **Recall** is opt-in at Copilot+ PC setup and removable as an optional feature. Snapshots and their vector database entries are encrypted, with keys in the TPM tied to Windows Hello Enhanced Sign-in Security. They are usable only inside a VBS Enclave after Windows Hello authorization, and authorization expires. Sensitive-content filtering (passwords, national IDs, credit cards) is on by default, and users can add apps and websites to a filter list. — [Windows Experience Blog, 2024-09-27](https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture); [PCWorld](https://www.pcworld.com/article/2470708/microsoft-now-confirms-you-can-opt-out-of-and-remove-windows-recall.html); [Redmondmag](https://redmondmag.com/articles/2024/09/27/windows-recall-security-guardrails.aspx); [Thurrott](https://www.thurrott.com/windows/310323/microsoft-details-how-it-made-windows-11s-recall-feature-more-secure)

**AnythingLLM (desktop and Docker)**
- Text splitting uses LangChain's RecursiveCharacterTextSplitter, the only strategy for all document types. The default chunk size is **1000 characters (~250 English tokens)** and is capped by the embedder's max length. An oversize setting falls back to the model limit and logs a warning. Chunking changes apply only to new embeddings, so you must remove and re-embed a document to apply them. — [AnythingLLM docs: Text splitting](https://docs.anythingllm.com/setup/embedder-configuration/text-splitting)
- The default overlap is reportedly 20 characters. — [fast.io guide **[secondary, unverified]**](https://fast.io/resources/anythingllm-context-window/). This conflicts with an insiderllm guide that says "default chunk size 1,000 tokens, Top K 4". The official docs say characters. — [insiderllm **[secondary]**](https://insiderllm.com/guides/anythingllm-setup-guide/)
- The default vector DB is a built-in, on-instance **LanceDB** with no configuration, so text and embeddings stay inside the app. — [AnythingLLM docs: LanceDB](https://docs.anythingllm.com/setup/vector-database-configuration/local/lancedb)
- **Attach vs. RAG.** By default, attached documents are inserted as **full text** into the chat. This is slower and costlier but "very accurate", and attachments are scoped to the thread. Embedded documents are chunked for RAG and available to all threads in the workspace. — [AnythingLLM docs: Chatting with documents](https://docs.anythingllm.com/chatting-with-documents/introduction)
- **Document pinning.** Pinning inserts an embedded document's full text into context, giving "far better answers at the expense of speed and cost". It is meant for documents that fit the context window or are critical. Pinned documents are excluded from RAG results, so they are not duplicated. — [same](https://docs.anythingllm.com/chatting-with-documents/introduction)
- **Similarity threshold** defaults to a **20%** minimum score. For hallucinations or poor answers, the docs suggest "No Restriction". **Max context snippets**: "4-6 for the majority of models". More can add noise. — [same](https://docs.anythingllm.com/chatting-with-documents/introduction)
- **Reranking** is the "Accuracy Optimized" search preference. It searches more chunks and reranks them, adding about 100–500 ms. It is LanceDB-only, set per workspace, and the model downloads on first use. — [same](https://docs.anythingllm.com/chatting-with-documents/introduction)
- The legacy (pre-1.8.5) doc says retrieval returns "4-6 text chunks", appended to the system message, with cosine distance. The LLM can "only see" documents embedded in the workspace, so whole-collection questions such as "titles of the 3 high-calorie meals" fail. — [AnythingLLM docs: LLM not using my docs (legacy)](https://docs.anythingllm.com/llm-not-using-my-docs)

**Open WebUI**
- The default embedder is `all-MiniLM-L6-v2`, run locally. Recommended alternatives: `nomic-embed-text` (multi-user with Ollama), `nomic-embed-text` or `mxbai-embed-large` (air-gapped), and OpenAI `text-embedding-3-small` (production with an API budget). All documents must be re-indexed after an embedder change. — [Open WebUI docs: RAG troubleshooting](https://docs.openwebui.com/troubleshooting/rag)
- **Published tuning table** (official):

  | Model context | Splitter | Chunk size | Overlap | Top K |
  |---|---|---|---|---|
  | Local models (≤8K) | token | 1000 | 100 | 3–5 |
  | Mixed | — | 1500 | 200 | 10 |
  | Cloud models (32K+) | — | 2000 | 200 | 15–25 |

  "Chunk Min Size Target" should be about 50–60% of chunk size to merge tiny fragments. With hybrid search on, **Top K Reranker** sets how many chunks reach the model. — [same](https://docs.openwebui.com/troubleshooting/rag)
- **Full Context Mode** is per-chat. It is best for one small-to-medium document (under ~50 pages) on a large-context model, and "can outperform retrieval" for documents under ~20K tokens. **Bypass Embedding and Retrieval** is an admin setting that still truncates at the context limit. — [same](https://docs.openwebui.com/troubleshooting/rag)
- **Context is the top failure.** Ollama sets default context from VRAM, and below 24 GiB it is 4096 tokens, which trims retrieved content. For web pages, use at least 8192 tokens. — [same](https://docs.openwebui.com/troubleshooting/rag). A third-party guide cites Ollama's older default `num_ctx` of 2048 as "way too small for RAG", causing silent context drops. — [kx.cloudingenium **[secondary]**](https://kx.cloudingenium.com/en/rag-ollama-chat-documents-local-ai-embeddings/)
- **Extraction.** The fallback for missing text is Apache Tika or Docling (or a custom engine), plus a "PDF Extract Images (OCR)" option for image-heavy PDFs. Docs recommend **previewing extracted content**, and pypdf 6.0.0+ handles PDFs better. Known bug: Docling citations can point to the wrong page, fixed by re-uploading. — [same](https://docs.openwebui.com/troubleshooting/rag)
- Hybrid search is a toggle plus a BM25 weight (0 = vector-only, 1 = BM25-only, default 0.5), with a recursive splitter default of 1500/200. — [Mintlify-hosted Open WebUI page **[secondary, unofficial mirror]**](https://www.mintlify.com/open-webui/open-webui/features/rag-integration)
- In April 2026, one admin raised chunk size to 4000 and top-K to 30, describing the default as "3 chunks of 1000 chars" with all-MiniLM-L6-v2, so that web search results would inform answers. — [NixOS config PR #1075 **[secondary, single user]**](https://gitea.hillion.co.uk/JakeHillion/nixos/pulls/1075)

**LM Studio**
- You can attach `.docx`, `.pdf` or `.txt`. If the document fits in the model's context, LM Studio inserts the full contents. If it is very long, it switches to RAG and extracts the relevant parts. The docs admit this "sometimes works well and sometimes needs tuning". User tip: include terms you expect to appear in the source. — [LM Studio docs: Chat with documents](https://lmstudio.ai/docs/app/basics/rag)

**GPT4All LocalDocs**
- LocalDocs splits folders into snippets and embeds each one with Nomic's on-device embedding models. The nearest snippets are added to context. — [GPT4All docs mirror (git.hackliberty.org) **[secondary mirror of official docs]**](https://git.hackliberty.org/AI/gpt4all/commit/62abecaec8a61896946dc4feb5dd574746e0acf0)
- v3.4.2 (Oct 2024) fixed "possible use of references from unselected collections" and hybrid search failing on an inconsistent DB. This shows LocalDocs uses hybrid search and shows references per collection. — [GitHub releases](https://github.com/nomic-ai/gpt4all/releases)
- The desktop app has had no release since **v3.10.0 (Feb 2025)**, and Ollama and LM Studio now cover similar ground. The project is effectively stalled. — [Real Python reference **[secondary]**](https://realpython.com/ref/ai-coding-tools/gpt4all/)

**Msty (Knowledge Stacks)**
- Msty offers Recursive Character and Sentence splitters, with chunk size and overlap controls. A **Chunks Visualizer** shows boundaries and overlaps once a stack has finished composing. — [Msty docs: Advanced Knowledge Stack settings](https://docs.msty.app/features/knowledge-stack/advanced-features); [Msty Studio docs: Chunks Visualizer](https://docs.msty.studio/knowledge-stacks/chunks-visualizer)
- Embeddings can be local ("completely private – nothing leaves your computer") or remote OpenAI ("more accurate"). Retrieval returns **15 chunks by default**, with a similarity threshold. — [Msty docs: Understanding embeddings](https://docs.msty.app/features/knowledge-stack/embeddings)

**Khoj**
- Khoj is AGPL-3.0, self-hostable, and has Obsidian and Emacs clients. The Obsidian plugin is at 2.0.0-beta.28. — [Obsidian community plugin page](https://community.obsidian.md/plugins/khoj)
- The self-hosted stack is reportedly the server, PostgreSQL + **pgvector**, SearXNG, and a code sandbox. Indexing a large vault takes time, and setup assumes comfort with Docker and Postgres. — [pyshine / review sites **[secondary]**](https://pyshine.com/khoj/)
- Khoj Cloud was reportedly discontinued on **2026-04-15**, and the OSS core is said to be unaffected. — [PromptQuorum review **[secondary, unconfirmed; contradicted by EveryDev listing]**](https://www.promptquorum.com/de/power-local-llm/khoj-ai-second-brain-review); [EveryDev](https://www.everydev.ai/tools/khoj)

**PrivateGPT (Zylon)**
- **Qdrant** is the default vector store, run "in local disk mode — no separate Qdrant server needed", though that mode is "not recommended for production". `embed_dim` must match the embedder (example: 1024). After changing models you must `make wipe` and re-ingest. — [PrivateGPT docs: Vector Store](https://docs.privategpt.dev/storage/vectordb.md)

**Jan**
- The current docs describe Jan Desktop as "chat, models, MCP connectors, and local API server" and Jan Agent as "Skills and Memory". The landing docs do not mention document RAG. — [Jan docs](https://docs.jan.ai/)

**Generic complaints and tuning (secondary)**
- "AI ignores document content" is attributed to over-large chunks or a query/content mismatch, and the suggested fix is 300–400-token chunks. For AnythingLLM, the advice is to raise top-K to 6–8 if answers are incomplete, or lower it to 2–3 if answers drift off-topic. — [insiderllm guides **[secondary]**](https://insiderllm.com/guides/anythingllm-setup-guide/); [insiderllm local RAG](https://insiderllm.com/guides/local-rag-search-documents-private-ai/)
- I did not get Reddit r/LocalLLaMA threads back from search (see Gaps).

### Inferences
- **Adopt a two-tier context model.** Use "pinned / full-text" for the current manuscript, chapter or series bible when it fits, and "retrieved chunks" for the rest of the library. AnythingLLM (pin), LM Studio (auto full-context when it fits) and Open WebUI (Full Context Mode) all converge on this. Pinned items should be excluded from retrieval to avoid duplication, as AnythingLLM does.
- **Hybrid is the default to aim for.** FTS5 BM25 plus optional embedding similarity mirrors AppContentIndexer (lexical + semantic, ID-only results, app owns content) and Open WebUI's BM25 weight (0.5). For a writing app, FTS5 alone is a strong no-embeddings fallback. Character names, invented places and jargon are exactly where small embedders such as all-MiniLM fail and BM25 succeeds.
- **Return IDs, not text, from the index.** As AppContentIndexer does, the index stores chunk/doc IDs plus offsets, and the canonical text stays in the app's document store. That makes citation jump-to-source trivial.
- **Defaults for a desktop writer.** Use roughly 800–1200-token chunks (or paragraph/scene-aware splits) with 10–15% overlap. Use Top K 3–5 for ≤8K-context local models, and scale to 10+ only when the model context is 32K+. Set a min-chunk merge, and use a low similarity floor (AnythingLLM's 20%) rather than a high one.
- **Budget the context explicitly and warn the user.** Silent truncation by Ollama's 2K/4K default `num_ctx` is the most-cited failure. ByAuthorX should set `num_ctx` explicitly when calling Ollama and show "N tokens of context used / dropped".
- **Re-embedding is unavoidable on embedder change** (AnythingLLM, Open WebUI, PrivateGPT all require it). Store embedder name and dimension per vector, detect mismatches, and offer a background re-index rather than failing.
- **Avoid:**
  - Opaque, unpreviewable extraction (Open WebUI explicitly advises previewing).
  - A single global chunk setting that silently does not apply to existing documents (AnythingLLM).
  - Indexing sensitive user content under an "experimental, non-sensitive only" API (AppContentIndexer's caveat). This matters if ByAuthorX is ever tempted to use Windows AI APIs for user manuscripts.
- **Unverified, prior knowledge, for the report writer to confirm:**
  - LM Studio bundles `nomic-embed-text-v1.5` (GGUF) as its default embedder and added MCP host support in v0.3.17 (mid-2025).
  - GPT4All is MIT-licensed, and LocalDocs uses Nomic Embed v1.5 with SQLite plus an HNSW index and a "Sources" expander showing file and snippet.
  - AnythingLLM is MIT-licensed and defaults to the built-in `all-MiniLM-L6-v2` (native) embedder.
  - Open WebUI changed its license in 2025 to a BSD-3-based license with a branding clause, and its default vector DB is Chroma.
  - PrivateGPT is Apache-2.0 and uses LlamaIndex.
  - Jan is Apache-2.0 (formerly AGPL).
  - Khoj's default embedder is a sentence-transformers bi-encoder plus cross-encoder rerank.

### Gaps
- Official Open WebUI defaults page (env-configuration) returned 404, so the exact current defaults for `CHUNK_SIZE`, `RAG_TOP_K` and `RAG_RERANKING_MODEL` were not confirmed. Secondary sources conflict: 1000 vs 1500 chunk size, and Top K 3 vs other values.
- LM Studio's default embedding model, the full-context vs RAG threshold, citation display, and MCP details were not on the docs page I fetched.
- Recall/Click to Do changes in 2025–2026 (GA dates, EU rollout, export features) were not found. Search only returned 2024 architecture posts.
- Apple Intelligence was not researched; assumed covered elsewhere.
- No Reddit/r/LocalLLaMA threads were retrieved, so complaint evidence is from tutorials and vendor troubleshooting docs, not community threads.
- No current Jan document-RAG details. PrivateGPT's default embedder and reranker were not on the vector store page.
- Licenses were confirmed only for Khoj (AGPL-3.0) this session.

## Q2: How do these tools let users see and control which documents/chunks were used?

### Takeaway
Control mostly happens *before* the query: workspace or collection scoping, pin vs embed, attach per thread, and selecting which collections are active. Visibility *after* the query is weaker. It is usually a collapsible list of sources or citations, and only Msty exposes a chunk-boundary visualizer. Known bugs in this area include references leaking from unselected collections (GPT4All) and citations pointing to the wrong page (Open WebUI with Docling). These show that citation fidelity is a real UX risk.

### Cited Findings
- **AnythingLLM** offers several controls:
  - Scopes: attach (thread-only, full text) vs. embed (workspace-wide, RAG) vs. **pin** (embedded but forced into context in full).
  - Retrieval filters: a per-workspace similarity threshold (default 20%) and a max-snippets limit (4–6 recommended).
  - Search preference: Default or Accuracy Optimized (rerank).
  
  — [AnythingLLM docs](https://docs.anythingllm.com/chatting-with-documents/introduction)
- **Open WebUI** offers per-chat Full Context Mode, admin "Bypass Embedding and Retrieval", a Top K / Top K Reranker budget, and extraction preview. Citations exist, with a known Docling wrong-page bug. — [Open WebUI RAG troubleshooting](https://docs.openwebui.com/troubleshooting/rag)
- **GPT4All** offers per-chat selection of LocalDocs collections, with references tied to collections. A fixed bug allowed references from *unselected* collections. — [GPT4All releases](https://github.com/nomic-ai/gpt4all/releases)
- **Msty** offers a Chunks Visualizer to inspect chunk boundaries and overlaps, a per-stack chunk count (default 15), a similarity threshold, and a local vs. remote embedder choice shown with explicit privacy wording. — [Msty Chunks Visualizer](https://docs.msty.studio/knowledge-stacks/chunks-visualizer); [Msty embeddings](https://docs.msty.app/features/knowledge-stack/embeddings)
- **LM Studio** handles this automatically: full document if it fits, otherwise RAG. The docs page gives no user-facing chunk visibility. — [LM Studio docs](https://lmstudio.ai/docs/app/basics/rag)
- **Windows semantic indexing**: the user controls which locations and file types are indexed. **AppContentIndexer** returns only ContentIds, so the app decides how to display them. — [Microsoft Support](https://support.microsoft.com/en-us/help/17161); [Microsoft Learn](https://learn.microsoft.com/en-us/windows/ai/apis/app-content-search)
- **Recall** users can exclude apps and websites, sensitive-content filtering is on by default, and Windows Hello gates access. — [Thurrott](https://www.thurrott.com/windows/310323/microsoft-details-how-it-made-windows-11s-recall-feature-more-secure); [Windows blog](https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture)

### Inferences
- For ByAuthorX, a "Context" side panel would combine the best pieces seen here:
  - Pinned documents (always included), with token cost per item.
  - Retrieved chunks for the last request, each with score, source document, chapter/scene, and a click to jump to the passage (enabled by an ID-plus-offset index).
  - Per-request toggles to exclude a chunk or document and re-run.
  - A visible "dropped due to context limit" indicator.
- Scoping by project/series ("workspace" or "stack") is the dominant mental model. A word processor's natural equivalent is "this manuscript + its research folder + series bible".
- Showing which embedder is in use and whether it is local or remote, as Msty does, is a cheap but strong privacy trust signal.
- Citation correctness needs tests. Both the GPT4All and Open WebUI bugs above were attribution errors, not retrieval errors.

### Gaps
- I did not confirm exactly how LM Studio, Jan, Khoj or PrivateGPT render citations in their UIs.
- No user-study or quantitative data was found on whether users actually inspect citations or chunk panels.
