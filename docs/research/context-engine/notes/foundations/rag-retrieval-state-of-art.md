# State of the Art in RAG and Retrieval Pipelines for a Local-First Desktop Writing App (as of Oct 2026)

Scope note: Research done 2026-10-16 with about 20 search/fetch calls. Many 2026 figures come from secondary or vendor blogs, and those are flagged. Items marked "[background, unverified this session]" come from prior knowledge (mostly pre-2026), were not re-checked, and may be superseded. They are kept out of Cited Findings for that reason.

## 1. Document ingestion and parsing (PDF/DOCX/HTML/EPUB; layout, tables, footnotes, page numbers)

### Takeaway
Docling (MIT code, permissively licensed default weights) is the safest default for a distributable desktop app, and it is competitive on CPU. Marker v2 reports the best accuracy on olmOCR-bench, but its Surya weights carry revenue/funding/competitor restrictions. MinerU is strongest on GPU and with formulas, but its 2026 custom license and unlabeled CPU weights complicate redistribution. All accuracy rankings are vendor-run or based on small samples, so the app should benchmark on its own corpus.

### Cited Findings
- **Marker v2.0.0 (released 2026-07-20)**, olmOCR-bench, self-reported by Datalab: 76.0 in balanced mode, 66.6 in fast mode, 43.6 with OCR disabled. Datalab also claims more than 5x the pages/sec of MinerU's pipeline backend. Neither figure has been independently reproduced. — [Particula (Sept 2026)](https://particula.tech/blog/docling-vs-mineru-vs-marker-pdf-parser)
- On Marker's own 2026 comparison, Marker scores 76.0 overall vs MinerU 72.7 vs Docling 50.3, on one document mix and one hardware setup. MinerU's VLM backend is not in that table. — vendor figures summarized via search, [Marktechpost (Jul 2026)](https://www.marktechpost.com/2026/07/24/datalab-marker-v2-vs-mineru-docling-and-liteparse-benchmark-breakdown/) (the page returned 403 on fetch, so only the snippet was seen)
- Particula found no independent head-to-head of all three tools at current versions and declined to rank them on accuracy. — [Particula](https://particula.tech/blog/docling-vs-mineru-vs-marker-pdf-parser)
- The Docling paper benchmarked speed against Marker and MinerU. Docling is competitive on CPU and Apple Silicon, and MinerU is faster with GPU acceleration. — [Docling paper, arXiv 2501.17887](https://arxiv.org/pdf/2501.17887)
- MinerU 2.5 (1.2B VLM) runs at about 4.7 s/page on an L4 GPU and covers formulas and chart data. This comes from a 6-PDF sample test against Granite-Docling, PaddleOCR-VL and XBerg. — [besthub benchmark](https://www.besthub.dev/articles/open-source-pdf-parser-benchmark-mineru-granite-docling-paddleocr-vl-and-xberg-9f184b033c66)
- OmniDocBench found that MinerU's reading order and recognition degrade on complex layouts, mainly because it wrongly merges multi-column text. Note that this finding comes from the older OmniDocBench paper (Dec 2024) and may be superseded. — [OmniDocBench, arXiv 2412.07626](https://arxiv.org/pdf/2412.07626)
- **Licenses (critical for a distributed app):**
  - **Docling 2.130.0 (2026-09-22):** MIT code. Default weights are apache-2.0, cdla-permissive-2.0 and mit. Optional VLM presets can pull third-party checkpoints with their own licenses (for example, chandra-ocr-2 is openrail). Table structure comes from TableFormer, which ships in the defaults. — [Particula](https://particula.tech/blog/docling-vs-mineru-vs-marker-pdf-parser)
  - **MinerU 4.0.7 (2026-09-23):** Apache 2.0 plus extra terms. A separate license is required above 100M MAU or USD 20M monthly revenue, online services must visibly credit MinerU, and the license terminates automatically if these terms are breached. Releases 3.0.9 and earlier were AGPL-3.0. Weights on the default CPU path (ONNX/llama.cpp) have no license tag. The GPU path uses apache-2.0 weights. — [Particula](https://particula.tech/blog/docling-vs-mineru-vs-marker-pdf-parser)
  - **Marker v2.0.0:** Code moved from GPL-3.0 to Apache 2.0 on 2026-07-17. The weights (Surya) use a modified OpenRAIL-M license. It is triggered by more than $5M prior-year revenue or more than $5M in funding (raised from $2M), or by a product that competes with Datalab. It also has attribution, share-alike and remote-use clauses. On Hugging Face the weights are tagged only "openrail". — [Particula](https://particula.tech/blog/docling-vs-mineru-vs-marker-pdf-parser)
- MinerU base-install model downloads are about 0.8 GB (basic tier) to about 2 GB (standard), or about 3 GB with vLLM. Marker defaults to fast mode on CPU. Docling runs its default models locally through torch. — [Particula](https://particula.tech/blog/docling-vs-mineru-vs-marker-pdf-parser)
- If tables and charts carry the answers, page-image retrieval (ColPali-style) may beat OCR for that part of the corpus. — [Particula](https://particula.tech/blog/docling-vs-mineru-vs-marker-pdf-parser)

### Inferences
- Suggested tiered parsing for the app:
  - **Fast path:** PyMuPDF / PyMuPDF4LLM for the text layer, page numbers and bounding boxes.
  - **Quality path:** Docling on CPU for layout, tables and reading order.
  - **Optional add-on:** a GPU path, with the license disclosed to the user.
- [background, unverified this session] PyMuPDF/PyMuPDF4LLM are AGPL-3.0 unless a commercial Artifex license is bought. That matters for a closed-source distributed app, and pypdf/pdfplumber (permissive) are the fallbacks.
- [background, unverified this session] GROBID (Apache-2.0, Java service) remains the specialist for scholarly PDFs. It produces TEI with header metadata, references, citation callouts and footnotes, which suits a citation-aware writing app, but it requires bundling a JVM or Docker.
- [background, unverified this session] For DOCX, python-docx or Docling's DOCX backend preserve footnotes and styles. For HTML web clips, trafilatura (Apache-2.0) is the standard boilerplate remover. For EPUB, ebooklib plus an HTML pipeline works.
- For citations, page numbers and bounding boxes must be stored per chunk at parse time. Docling's DoclingDocument retains provenance (page, bbox), which is a reason to prefer it over Markdown-only converters.

### Gaps
- No independent 2026 accuracy numbers for footnote and page-number fidelity across parsers.
- No independent CPU pages/sec figures for Docling vs Marker vs MinerU at their current 2026 versions.
- Could not fetch the Marktechpost Marker v2 breakdown (403).
- Unstructured, GROBID and LiteParse numbers were not verified in this session.

## 2. Chunking (fixed, semantic, structure-aware, late chunking, contextual retrieval, parent-child, propositions)

### Takeaway
The evidence favors simple, well-parameterized recursive or structure-aware chunks of about 200–800 tokens. Context-enriching techniques beat clever split-point selection: contextual retrieval (49% fewer retrieval failures with BM25, 67% with a reranker) and late chunking (a modest +1.5–1.9 nDCG points absolute). Semantic chunkers show inconsistent gains and sometimes underperform.

### Cited Findings
- **Anthropic Contextual Retrieval** prepends a 50–100-token LLM-generated situating context to each chunk before both embedding and BM25 indexing. Top-20 retrieval failure rates were:

  | Configuration | Failure rate | Reduction vs baseline |
  |---|---|---|
  | Baseline | 5.7% | — |
  | Contextual embeddings | 3.7% | 35% |
  | + contextual BM25 | 2.9% | 49% |
  | + reranking | 1.9% | 67% |

  Reranking took the top 150 candidates down to 20 using the Cohere reranker. Top-20 beat top-10 and top-5. Chunks are a few hundred tokens (the cost example uses 800). One-time cost is about $1.02 per million document tokens with prompt caching, assuming 8k-token docs and Claude 3 Haiku. Gemini Text 004 was the best embedding model tested, and Voyage was also tested. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- Anthropic also advises that if the knowledge base is under 200k tokens (about 500 pages), the whole thing can go in the prompt with prompt caching and RAG can be skipped. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- Anthropic's gains vary by domain, from about 41% on one codebase set to about 84% on fiction. It is also disputed whether the headline 67% is an average across embedding models or the best configuration. — [Medium / Almond AI critique](https://medium.com/almond-ai/responding-to-anthropics-contextual-retrieval-for-rag-apps-why-context-is-not-all-you-need-af503985aa55), [the-decoder](https://the-decoder.com/anthropic-boosts-rag-accuracy-with-context-aware-retrieval/)
- **Chroma's chunking research (Jul 2024; may be dated):** chunking choice significantly affects retrieval, some popular default settings perform poorly, and RecursiveCharacterTextSplitter performs well when its parameters are set properly. Chroma's ClusterSemanticChunker and LLMChunker were "competitive". In secondhand figures, recursive splitting at 200 tokens gets about 88.5% recall vs about 91.7% for the LLM chunker. — [Chroma research](https://research.trychroma.com/evaluating-chunking); numbers via [premai](https://www.premai.io/blog/rag-chunking-strategies-the-2026-benchmark-guide/) (not verified against the primary table)
- A 2026 FloTorch benchmark (secondhand) found that semantic chunking produced 43-token fragments on average and 54% end-to-end accuracy, vs 69% for recursive 512-token chunks. — [premai summary](https://www.premai.io/blog/rag-chunking-strategies-the-2026-benchmark-guide/)
- **Late chunking (Jina, arXiv 2409.04701)** embeds the whole long document and then mean-pools per chunk span after the transformer. It gains about +2.7–3.6% relative nDCG@10 on BEIR, roughly +1.5–1.9 points absolute, using jina-embeddings-v2-small with 256-token chunks. Gains grow with document length. It needs no LLM calls. — [arXiv 2409.04701](https://arxiv.org/html/2409.04701v1), [Jina blog](https://jina.ai/news/late-chunking-in-long-context-embedding-models/), [martilabs analysis](https://martilabs.substack.com/p/context-aware-retrieval-from-late)

### Inferences
- For a writer's library, a good default is structure-aware chunking: split on headings, paragraphs and pages using the parser's document tree, with targets of about 300–500 tokens and a small overlap. Keep parent-section pointers (small-to-big) so the reader can expand context and the app can cite a page.
- Contextual retrieval suits the "with AI" mode. It is cheap with a cloud model plus caching, and feasible but slow locally with a small LLM, so run it as a background job.
- Late chunking is free quality for the "no cloud" mode, provided the local embedding model has long context (Qwen3-Embedding has 32K, and jina v2/v3 support it). It requires custom pooling code rather than a stock sentence-transformers call.
- [background, unverified this session] Proposition indexing (Dense X Retrieval, Chen et al. 2023) showed gains on open-domain QA but multiplies index size and requires LLM preprocessing. It is likely not worth it for a laptop default.

### Gaps
- No primary 2026 head-to-head comparing contextual retrieval vs late chunking on the same corpus.
- Parent-child retrieval gains have not been quantified by a reliable source.

## 3. Lexical + dense hybrid; embedding models (local and cloud)

### Takeaway
Hybrid retrieval with BM25 plus dense embeddings fused by RRF is the standard baseline. For local use:
- **Qwen3-Embedding-0.6B** (Apache-2.0, 1024-dim with MRL down to 32, 32K context) is the best quality-per-size pick: MTEB-multilingual 64.33, English-v2 70.70.
- **EmbeddingGemma-300M** is the smallest strong option (under 200 MB quantized, MRL 768/512/256/128), but it is under the Gemma Terms of Use.
- **Qwen3-Embedding-4B/8B** need a GPU.

For cloud use, Gemini embedding-001 (about $0.15/M tokens), Voyage-4 (Jan 2026, MoE, shared embedding space across sizes) and OpenAI text-embedding-3 ($0.02–0.13/M) are the options.

### Cited Findings
- **Qwen3-Embedding**, official model card:

  | Model | MTEB-Multilingual mean (retrieval) | MTEB-English-v2 mean (retrieval) | C-MTEB mean |
  |---|---|---|---|
  | 0.6B | 64.33 (64.64) | 70.70 (61.83) | 66.33 |
  | 4B | 69.45 (69.60) | 74.60 (68.46) | 72.27 |
  | 8B | 70.58 (70.88) | 75.22 (69.44) | 73.84 |

  Comparison models, as listed on the same card (multilingual mean / retrieval):

  | Model | Multilingual mean | Multilingual retrieval |
  |---|---|---|
  | BGE-M3 | 59.56 | 54.60 |
  | multilingual-e5-large-instruct | 63.22 | 57.12 |
  | text-embedding-3-large | 58.93 | 59.27 |
  | Cohere-embed-multilingual-v3 | 61.12 | 59.16 |
  | Gemini Embedding | 68.37 | 67.71 |

  Specs: dimensions are 1024, 2560 and 4096, with MRL (0.6B user-defined from 32 to 1024) and 32K max sequence length. Query-side instructions add 1–5%. The 0.6B model is Apache-2.0. — [HF Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- Qwen3-Embedding-8B needs about 16 GB VRAM at FP16 or about 9 GB at Q8. The 0.6B model is about 1.5 GB and has native Ollama support. — [bestllmfor](https://bestllmfor.com/best/embeddings-self-hosted/), [d-central](https://d-central.tech/local-embedding-models/) (secondary)
- 2026 leaderboard newcomers above Qwen3-8B: Microsoft harrier-oss-v1-27b at 74.31 and Tencent KaLM-Embedding-Gemma3-12B at 72.32. These are too large for laptops. — [theairankings](https://theairankings.com/best-embedding-models/) (secondary, unverified)
- In a local RAG test on two scientific corpora, Qwen3-8B had the best nDCG@10 but the slowest indexing, and EmbeddingGemma had higher Recall@10. — [atomic.chat](https://atomic.chat/blog/guides/best-embedding-models-for-rag) (secondary)
- **EmbeddingGemma** has about 300M (308M) parameters, is built from Gemma 3, runs in under 200 MB RAM when quantized, and outputs 768 dimensions with MRL to 512/256/128. Google calls it the highest-ranked open multilingual model under 500M on MTEB. Third-party figures put it at 61.15 MTEB-multilingual-v2 and 68.36 English-v2 (unconfirmed). It is gated under the Gemma Terms of Use. — [Google DeepMind](https://deepmind.google/models/gemma/embeddinggemma/), [HF card mirror](https://huggingface.co/MichaelZag/embeddinggemma-300m)
- **Jina v3 and NV-Embed-v2** are CC-BY-NC (non-commercial). **jina-embeddings-v4** is listed as CC-BY-NC-4.0 on its HF card, but Jina's page names the Qwen Research License, so the sources conflict. Both licenses are restrictive. — [d-central](https://d-central.tech/local-embedding-models/), [HF jina-v4 README](https://huggingface.co/jinaai/jina-embeddings-v4/blob/c3adddde53298a4123684a1312f78ad3d56ae5d1/README.md), [Jina model page](https://jina.ai/models/jina-embeddings-v4/)
- **Cloud embeddings:**
  - gemini-embedding-001: about 68.3–68.8 MTEB, about $0.15/M tokens, 2,048-token input limit. Gemini Embedding 2 has been released but not submitted to MTEB.
  - OpenAI text-embedding-3-large: about $0.13/M, MTEB 64.6, 8,192-token context, no new OpenAI model since Jan 2024. text-embedding-3-small is about $0.02/M.
  - Voyage-4 (Jan 2026): MoE, with nano/lite/standard/large sharing one embedding space. Estimated pricing is about $0.12/M (large) and $0.02/M (lite). It has no public MTEB scores, only vendor evaluations.

  — [premai](https://blog.premai.io/best-embedding-models-for-rag-2026-ranked-by-mteb-score-cost-and-self-hosting), [rework](https://resources.rework.com/tools/ai-data-infra/best-embedding-models-2026), [andrew.ooo](https://andrew.ooo/answers/best-embedding-models-2026-ranked/) (secondary; check vendor pricing pages)
- **bm25s** (pure Python/NumPy, arXiv 2407.03618) is more than 100x faster than rank-bm25 on 10 of 14 BEIR datasets and up to 500x on one (for example, 1196 QPS on NFCorpus). It is reported to be about 2–10x faster than single-threaded Elasticsearch on many datasets (NQ: bm25s 41.85 QPS vs ES 12.16 vs rank-bm25 0.10). Version 0.2+ adds a numba backend for about 2x more speed. — [bm25s PyPI/GitHub](https://pypi.org/project/bm25s), [liner review](https://liner.com/ko/review/bm25s-orders-magnitude-faster-lexical-search-via-eager-sparse-scoring), [emergentmind](https://www.emergentmind.com/topics/bm25s)

### Inferences
- **SQLite FTS5** (built into Python's sqlite3, BM25 ranking via `bm25()`) is the most natural lexical index for a desktop app. It supports incremental updates, is transactional, and has no extra dependency. bm25s is faster for batch scoring but is an in-memory static index that is less convenient for incremental updates. [FTS5 details: background, unverified this session]
- **Recommended local stack:**
  - FTS5 BM25, plus Qwen3-Embedding-0.6B (or EmbeddingGemma if footprint dominates) via ONNX/llama.cpp GGUF, truncated with MRL to 256–512 dimensions.
  - Fuse with RRF (k=60 is conventional [background]).
  - Offer cloud embeddings (Voyage/Gemini/OpenAI) only as an opt-in.
- If a user switches embedding model, all chunks must be re-embedded. Store model ID and dimension per index. Voyage-4's shared space across sizes would reduce that cost within the Voyage family.
- [background, unverified this session] BGE-M3 (MIT) also emits learned sparse and ColBERT vectors from one model. That makes it a convenient single model for "hybrid in one pass", though it scores below Qwen3-0.6B on MTEB per the table above. SPLADE-v3 weights are CC-BY-NC (non-commercial). Learned sparse adds little over BM25 plus a strong dense model for a small personal corpus.
- Licenses favor Qwen3-Embedding (Apache-2.0), BGE-M3 (MIT), nomic-embed and mxbai/Snowflake Arctic (Apache-2.0 per my background knowledge) over Jina v3/v4 and Gemma-terms models.

### Gaps
- Licenses for nomic-embed-text-v2, mxbai-embed-large and Snowflake arctic-embed-l-v2 were not verified this session.
- No authoritative CPU throughput (chunks/sec) figures for Qwen3-0.6B vs EmbeddingGemma on laptop CPUs.
- Voyage-4 and Gemini Embedding 2 have no independent MTEB scores.

## 4. Late interaction and multi-vector (ColBERT, PLAID, answerai-colbert, ColPali)

### Takeaway
Small ColBERT models are now practical locally via PyLate:
- answerai-colbert-small-v1: 33M parameters, BEIR 53.79 vs ColBERTv2's 50.02.
- GTE-ModernColBERT-v1: BEIR about 54.7–54.9.

They give strong zero-shot quality, but storage is roughly tens of times larger than single-vector indexes. ColPali/ColQwen visual retrieval suits table- and figure-heavy PDFs, but costs about 130–530 KB per page uncompressed, so it fits an opt-in feature rather than a default.

### Cited Findings
- PyLate is built on Sentence Transformers, supports fine-tuning, inference and PLAID indexing, and includes BEIR loaders. — [PyLate paper, arXiv 2508.03555](https://arxiv.org/pdf/2508.03555), [GitHub](https://github.com/arn4/pylate)
- answerai-colbert-small-v1 (33M parameters) reports a BEIR average of 53.79 vs ColBERTv2.0's 50.02. PyLate reproduced 53.35, with the gap attributed to PLAID non-determinism. — [HF answerai-colbert-small-v1](https://huggingface.co/AnswerDotAI/answerai-colbert-small-v1), [PyLate paper](https://arxiv.org/pdf/2508.03555)
- GTE-ModernColBERT-v1 was the first PyLate model to beat ColBERT-small on BEIR, with 54.89 (paper) or 54.67 (model card). — [HF GTE-ModernColBERT-v1](https://huggingface.co/lightonai/GTE-ModernColBERT-v1)
- ColPali-style models store one 128-dimensional vector per image patch, roughly 1,000 per page. ColQwen2.5 averages about 743.5 vectors per page on ViDoRe v1. Storage is about 527 KB per page in float32 and about 132 KB per page in int8. — [Spheron blog](https://www.spheron.network/blog/colpali-multimodal-document-rag-gpu-cloud/), [arXiv 2609.21018](https://arxiv.org/pdf/2609.21018)
- On ViDoRe v2, ColQwen2.5 scores about 56–65 nDCG@5 across subsets. Compression to 64 vectors per page loses a few points (for example, 63.2 to 60.2 on English biomedical). ColQwen2/2.5 remain the usual starting point. 2026 work focuses on compression (pooling, pruning, Matryoshka). — [arXiv 2609.21018](https://arxiv.org/pdf/2609.21018), [arXiv 2601.20107](https://arxiv.org/pdf/2601.20107), [arXiv 2606.07654](https://arxiv.org/pdf/2606.07654), [Mixpeek](https://mixpeek.com/curated-lists/best-visual-document-retrieval-models)

### Inferences
- A practical laptop use of ColBERT is as a reranker over hybrid top-100 candidates: compute MaxSim with answerai-colbert-small on CPU. This avoids storing a full multi-vector index and gets most of the gain.
- ColPali/ColQwen (about 3B VLM) needs a GPU to index at a reasonable speed. Offer it as an opt-in for scanned or table-heavy sources, or skip it.

### Gaps
- No CPU latency numbers found for PyLate PLAID search or ColBERT reranking.
- The answerai-colbert license was not checked (believed Apache-2.0 [background]).

## 5. Rerankers (cross-encoders, LLM rerankers, CPU latency)

### Takeaway
Reranking is the single biggest late-stage gain: it took Anthropic's failure reduction from 49% to 67%. Locally, bge-reranker-v2-m3 (568M, encoder-only), jina-reranker-v2 (0.3B, but CC-BY-NC [background]) and Qwen3-Reranker-0.6B are the practical choices. Reranking top-20 to top-50 on CPU costs roughly 0.1–1 s per query. Cohere and Voyage rerank are the cloud options.

### Cited Findings
- In SkillRet (arXiv 2605.05726), reranking top-20 for one query took:

  | Reranker | Time per query (top-20) |
  |---|---|
  | jina-reranker-v2 (0.3B) | about 132 ms |
  | Qwen3-Reranker-0.6B | about 831 ms |
  | Qwen3-Reranker-8B | about 1,876 ms |

  Hardware was not stated in the excerpt. — [arXiv 2605.05726](https://arxiv.org/pdf/2605.05726)
- In MemReranker (arXiv 2605.06132), BGE-Reranker-v2-m3 averaged about 241 ms per call vs about 247 ms for a 0.6B Qwen-style reranker. The encoder-only design explains BGE's speed. Hardware was not specified. — [arXiv 2605.06132](https://arxiv.org/pdf/2605.06132)
- Qwen3-Reranker-8B scores about 77 nDCG@10 averaged on BEIR, and bge-reranker-v2-m3 is the most downloaded open reranker. One claim of 35 ms per pair is poorly sourced. — [Presenc AI (May 2026)](https://presenc.ai/research/best-open-weight-reranker-models-2026) (secondary)
- OpenVINO Model Server lists bge-reranker-v2-m3 and Qwen3-Reranker-0.6B as tested reranking models served via a Cohere-compatible API. This is relevant for Intel-laptop CPU acceleration. — [OpenVINO docs 2026](https://docs.openvino.ai/2026/model-server/ovms_demos_rerank.html)
- On BRIGHT (reasoning-intensive retrieval):

  | System | nDCG@10 |
  |---|---|
  | BM25 | about 14.5–14.8 |
  | Top MTEB embedder (original BRIGHT paper) | 18.0 |
  | ReasonIR-8B | 24.4 |
  | ReasonIR-8B + query rewriting | 29.9 |
  | ReasonIR-8B + LLM reranker | 36.9 |
  | Leaderboard top (Aug 2025) | about 46 |

  All top systems use LLMs. Query-side BM25 beats standard BM25 on BRIGHT. — [BRIGHT, ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/7a0f8055c838df8e62329a76c7c6403d-Abstract-Conference.html), [ReasonIR arXiv 2504.20595](https://arxiv.org/pdf/2504.20595), [Anserini BRIGHT baselines arXiv 2509.02558](https://arxiv.org/html/2509.02558v1)

### Inferences
- For the no-AI mode, use bge-reranker-v2-m3 (Apache-2.0 [background]) as an int8 ONNX model on top-30 candidates, with an "accurate" toggle.
- For the AI mode, an LLM reranker or a cloud rerank API helps most on thematic or "reasoning" queries that writers ask (for example, "where do my sources discuss X's motive").
- Benchmark rerank latency on the target laptops. The published figures lack hardware details.

### Gaps
- No reliable CPU-only latency figures on a named laptop CPU.
- Did not obtain the Contra Collective M5 Max results.
- Cohere Rerank 3.5/4 and Voyage rerank-2.5 pricing and accuracy were not verified this session.

## 6. Local vector stores for an embedded desktop app

### Takeaway
For tens of thousands of documents (about 10^5–10^6 chunks), brute-force or simple ANN is sufficient. sqlite-vec keeps everything in a single SQLite file next to FTS5, which suits a desktop app well, but it is brute-force only. LanceDB is the stronger embedded option if ANN, hybrid search and larger scale are needed. No rigorous head-to-head benchmark was found.

### Cited Findings
- sqlite-vec is brute force only ("currently"). It stores vectors in fixed-size chunks and scans for top-K, trading optimality for simplicity with no index to tune. It supports `vec_quantize_binary()` with 32x compression (1M vectors is about 32 MB) and recommends oversampling plus re-scoring. — [sqlite-vec binary quant guide](https://https-alexgarcia_xyz.proxy.bugsmash.io/sqlite-vec/guides/binary-quant.html), [sqlite-vec README (npm mirror)](https://cdn.jsdelivr.net/npm/@photostructure/sqlite-vec@1.2.0/README.md)
- The v0.1.0 release (Aug 2024) advertised support for 1M 128-dimensional vectors. — [Marktechpost 2024](https://www.marktechpost.com/2024/08/04/sqlite-vec-v0-1-0-released-portable-vector-database-extension-for-sqlite-with-support-for-1-million-128-dimensional-vectors-binary-quantization-and-extensive-sdks/)
- An anecdotal figure puts sqlite-vec search at about 500 ms for 500K 768-dimensional vectors. — [SitePoint](https://www.sitepoint.com/local-first-rag-vector-search-in-sqlite-with-hamming-distance/)
- sqliteai's separate "sqlite-vector" project is a different product. It uses quantized scan (TurboQuant) and is not comparable to sqlite-vec. — [sqlite-vector README](https://raw.githubusercontent.com/sqliteai/sqlite-vector/HEAD/README.md), [State of Vector Search in SQLite](https://marcobambini.substack.com/p/the-state-of-vector-search-in-sqlite)
- LanceDB is an embedded-first database ("think SQLite or DuckDB") for local and edge AI apps. As of May 2026, DuckDB can query Lance tables with vector and hybrid search from SQL. — [Zilliz comparison](https://zilliz.com/comparison/pinecone-vs-lancedb), [DuckDB blog May 2026](https://duckdb.org/2026/05/21/test-driving-lance)
- SQLite plus a vector extension is described as extremely popular for embedded and desktop projects because of its simplicity. This is anecdotal. — [Habr comments](https://habr.com/en/articles/961088/comments)

### Inferences
- **Sizing:** 30k documents at about 30 chunks each gives about 1M chunks. At 512 float32 dimensions that is 2 GB, at int8 512 MB, and in binary 64 MB. With binary quantization, brute-force sqlite-vec can scan this in well under a second, followed by float re-scoring of the top few hundred. A single-file SQLite database (documents, chunks, FTS5, vec0, provenance) gives atomic incremental updates and simple backup and sync, which matters for a writer's library.
- [background, unverified this session] Other embedded options:
  - LanceDB (Apache-2.0): IVF-PQ and HNSW indexes, built-in FTS and hybrid search, versioning.
  - Qdrant local mode (`QdrantClient(path=...)`, Apache-2.0): fine for small sets but uses brute-force/in-memory in local mode.
  - Chroma (Apache-2.0): embedded but heavier dependency footprint.
  - FAISS (MIT) and usearch (Apache-2.0): index libraries only, so the app must persist metadata itself.
  - DuckDB VSS: HNSW persistence is marked experimental.
- Recommendation: start with SQLite (FTS5 plus sqlite-vec). Abstract the vector layer behind an interface so it can be swapped to LanceDB or usearch if collections exceed about 2–5M chunks.

### Gaps
- No rigorous, current benchmark comparing sqlite-vec, LanceDB, Chroma and Qdrant-local on laptop hardware.
- Did not confirm whether sqlite-vec shipped ANN (IVF/DiskANN) in 2025–2026.

## 7. Advanced RAG (query rewriting/HyDE, multi-query, GraphRAG/LightRAG, RAPTOR, agentic retrieval, long context vs RAG, prompt caching)

### Takeaway
For a personal library, long context plus prompt caching is the simplest high-quality path for small scopes. Anthropic says to skip RAG under 200k tokens. Long-context models still degrade with length ("context rot", lost-in-the-middle, NoLiMa), so retrieval to narrow the input followed by long-context reasoning is the consensus pattern. GraphRAG is expensive. LightRAG and LazyGraphRAG cut costs, but evidence comes mostly from self-reports. Agentic, iterative retrieval with LLM query rewriting gives the biggest gains on reasoning-heavy queries (BRIGHT).

### Cited Findings
- Under 200k tokens (about 500 pages), put the whole knowledge base in the prompt with caching. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- **Chroma "Context Rot" (Jul 2025):** 18 models degrade with input length even on simple tasks. The effect is worse with distractors and with low lexical overlap between question and answer. Exact magnitudes (20–50% drops between 10K and 100K tokens; 30+ points for mid-context facts) vary across secondary write-ups. — [Particula summary](https://particula.tech/blog/chroma-context-rot-long-context-degradation), [glasp](https://glasp.ai/articles/context-rot-rag-long-context-hybrid)
- **NoLiMa (ICML 2025):** at 32K tokens with low-overlap questions, GPT-4o dropped from 99.3% to 69.7%, and 11 models fell below 50% of their short-context baselines. RULER-type findings show that effective context is much shorter than the advertised context. — [hackernoon / secondary](https://hackernoon.com/long-context-beats-rag-on-accuracyso-why-keep-retrieval), [Databricks](https://databricks.com/blog/long-context-rag-performance-llms)
- **Long Context vs RAG (Li et al., arXiv 2501.01880)** and **LaRA** (2,326 cases): there is no universal winner, and the better choice depends on model, task and retrieval quality. — [arXiv 2501.01880](https://arxiv.org/abs/2501.01880v1), [toolhalla](https://toolhalla.ai/blog/rag-vs-long-context-2026)
- **GraphRAG vs LightRAG:**
  - Anecdotal indexing costs for 500 pages: vector RAG under $5, LightRAG about $0.50, MS GraphRAG $50–200.
  - LightRAG's "6,000x fewer query tokens" claim (610k tokens vs 100) is self-reported.
  - GraphRAG has since added incremental `graphrag update` (source dates conflict), Dynamic Community Selection (-79% tokens), and LazyGraphRAG (about 0.1% of indexing cost at comparable quality).
  - FastGraphRAG uses noun-phrase extraction and produces noisier graphs.

  — [particula LightRAG vs GraphRAG](https://particula.tech/blog/lightrag-vs-graphrag), [tianpan.co (Apr 2026)](https://tianpan.co/blog/2026-04-09-graphrag-production-when-vector-search-hits-ceiling), [aicoolies](https://aicoolies.com/comparisons/lightrag-vs-graphrag)
- One source states that GraphRAG 3.3.0 shipped on "8 October 2026" but is attributed to a "March 2026" analysis. This is an internal date contradiction, so treat it as unreliable. — [lilys.ai](https://lilys.ai/en/notes/openai-agent-builder-20260208/lightrag-vs-graphrag-rag-efficiency)
- On BRIGHT, LLM query rewriting raised ReasonIR from 24.4 to 29.9 nDCG@10, and LLM reranking raised it to 36.9. The DIVER pipeline (expansion, fine-tuned retriever blended with BM25, then an LLM reranker) reports 41.6. — [ReasonIR](https://arxiv.org/pdf/2504.20595), [arXiv 2508.07995](https://arxiv.org/html/2508.07995v2)

### Inferences
- **Scoped queries:** for "this manuscript plus these 5 sources" (often under 200k tokens), send the full text with prompt caching to a cloud model. Use RAG for library-wide search.
- Graph and RAPTOR approaches suit "global/thematic" questions across a corpus ("what themes recur in my research notes?"). LLM-based indexing of a whole library is expensive and slow locally. Consider LazyGraphRAG-style deferred summarization, or per-document summaries (RAPTOR-lite) generated on demand.
- HyDE, multi-query and agentic loops all require an LLM, so they belong in AI mode only. The no-AI mode should rely on hybrid search, a reranker, and good UI (filters, facets, snippet highlighting).
- [background, unverified this session] RAPTOR (Sarthi et al., ICLR 2024) reported gains on QuALITY and NarrativeQA using recursive cluster summaries. HyDE (Gao et al. 2022) helps zero-shot dense retrieval but adds an LLM call of latency.

### Gaps
- No independent, rigorous evaluation of LightRAG vs naive hybrid RAG with a reranker was found.
- No 2026 numbers on 1M-token models (Gemini, Claude) recalling facts from writer-style corpora.
- Prompt-caching prices for 2026 models were not verified this session; consult the claude-api docs and vendor pages.

## 8. Evaluation (RAGAS, ARES, TruLens, BEIR/MTEB/BRIGHT; small domain eval sets; metrics)

### Takeaway
Measure retrieval separately from generation:
- **Retrieval:** recall@k and nDCG@10 on a small hand-labeled set of 50–200 queries.
- **Generation:** LLM-judged faithfulness and context precision/recall (RAGAS), spot-checked by humans.

Public benchmarks (MTEB, BEIR, BRIGHT) are only proxies, and BRIGHT shows that keyword- and embedding-based retrieval struggles badly on reasoning-heavy queries.

### Cited Findings
- RAGAS metrics:
  - **Faithfulness:** is the answer grounded in the context?
  - **Context precision:** how much of the retrieved material is useful?
  - **Context recall:** was the needed information retrieved?
  - **Answer relevancy.**

  These separate retrieval failures from generation failures. RAGAS can generate synthetic question/context/answer test sets from a corpus. — [RAGAS docs](https://docs.ragas.io/en/v0.1.21/concepts/metrics/), [Anyscale docs](https://docs.anyscale.com/rag/evaluation)
- LLM-judge reliability concerns include bias and self-preference. Mitigations are a different judge model, human spot-checks, and judge ensembles. ARES trains classifier judges and uses prediction-powered inference for confidence intervals. — [premai RAG evaluation 2026](https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026), [Manning ch.14](https://livebook.manning.com/book/retrieval-augmented-generation-the-seminal-papers/chapter-14)
- BRIGHT (ICLR 2025) has about 1,384–1,398 queries. BM25 scores about 14.5 nDCG@10 and the best MTEB model of the time scored 18.0. — [BRIGHT](https://proceedings.iclr.cc/paper_files/paper/2025/hash/7a0f8055c838df8e62329a76c7c6403d-Abstract-Conference.html)
- Anthropic's metric was 1 - recall@20 ("failure rate"). — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)

### Inferences
- **Build a dev eval set** from 3–5 representative writer libraries (public-domain books, arXiv PDFs, web clips) with about 100 queries each. Mix four query types:
  - known-item ("the quote about X")
  - factual
  - thematic
  - citation-locating ("which page says…")
- Label the gold chunk or page for each query. Track recall@5, recall@20, nDCG@10 and MRR in CI, with ranx or pytrec_eval [background].
- For a citation-focused app, add a **citation accuracy** metric: whether the cited page or passage actually supports the claim. Judge it with an LLM and validate on a human sample.

### Gaps
- No quantitative study of how well RAGAS faithfulness agrees with human labels.
- TruLens and current RAGAS API versions (0.2/0.3+) were not verified this session.

## 9. Incremental indexing and performance budgets on a laptop (tens of thousands of documents)

### Takeaway
The pipeline is feasible on CPU with these choices:
- small embedder (0.3–0.6B, int8/ONNX, MRL 256–512 dimensions)
- FTS5
- binary or int8 vectors in sqlite-vec
- background, resumable ingestion keyed by content hash

Parsing (layout models/OCR) and LLM contextualization are the dominant costs, not vector search.

### Cited Findings
- Qwen3-Embedding-8B indexes slowest in a local test. EmbeddingGemma has an under-200 MB footprint and Qwen3-0.6B about 1.5 GB. — [atomic.chat](https://atomic.chat/blog/guides/best-embedding-models-for-rag), [d-central](https://d-central.tech/local-embedding-models/)
- sqlite-vec binary quantization gives 32x smaller storage (1M vectors is about 32 MB). Reported latency is about 500 ms for 500K × 768-dimensional vectors (anecdotal). — [sqlite-vec guide](https://https-alexgarcia_xyz.proxy.bugsmash.io/sqlite-vec/guides/binary-quant.html), [SitePoint](https://www.sitepoint.com/local-first-rag-vector-search-in-sqlite-with-hamming-distance/)
- MinerU on L4 GPU runs at about 4.7 s per page for VLM parsing. — [besthub](https://www.besthub.dev/articles/open-source-pdf-parser-benchmark-mineru-granite-docling-paddleocr-vl-and-xberg-9f184b033c66)
- Contextual retrieval costs about $1.02 per million document tokens one-time via a cloud LLM with caching. — [Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- bm25s and FTS-style lexical search reaches hundreds to thousands of QPS on BEIR-size corpora. — [bm25s](https://pypi.org/project/bm25s)

### Inferences
- **Indexing budget for a 10k-document library** (rough, to be measured):
  - Assumptions: about 300 pages per document, about 3M pages, about 1.5B tokens. A typical writer library is far smaller, often about 1k documents.
  - Parsing: fast-path text extraction runs at roughly milliseconds per page. Layout-model parsing on CPU is likely seconds per page, so it should be selective (only when the text layer is poor or the user opts in).
  - Embedding: CPU throughput for a 0.6B model is likely around tens of chunks per second, which makes a full re-embed of a large library an overnight job. This is an estimate, not a measured value.
- **Design rules:**
  - Hash files by content and skip unchanged ones.
  - Process one document per transaction in a background QThread or subprocess, with a pause/resume queue and a progress UI.
  - Keep FTS available immediately and add vectors asynchronously, so search works before embeddings finish.
  - Store the embedding model and version per row so a model change can trigger lazy re-embedding.
  - Delete-cascade chunks when the source changes.
- **Query budget target:** under 300 ms for hybrid retrieval without reranking, and under 1 s with CPU reranking of top-30.

### Gaps
- No measured CPU embedding throughput (chunks/sec) on typical laptop CPUs for Qwen3-0.6B or EmbeddingGemma.
- No measured Docling CPU s/page at version 2.130.
- These should be measured in-house.
