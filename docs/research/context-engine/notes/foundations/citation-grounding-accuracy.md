# Citation, Attribution, Grounding and Factual Accuracy for an AI-Assisted Desktop Word Processor (state as of Oct 2026)

Research date: 2026-10-16. Items marked "(background, not verified this session)" come from prior knowledge and were not re-checked against a live source here; the report writer should treat them as lower confidence or omit them.

## LLM citation/attribution mechanisms (Anthropic, OpenAI, Google, Cohere): how they work, granularity, reliability, cost

### Takeaway
Anthropic's Citations API offers the most useful granularity for a word processor: verbatim `cited_text` plus exact char offsets for text docs, 1-indexed page ranges for PDFs, and block indices for custom chunks. That `cited_text` is free in output tokens and guaranteed to point at a real span of a supplied document. Cohere also gives char spans (start/end in the response) mapped to sources. Gemini gives response-segment offsets mapped to grounding chunks. OpenAI file_search annotations point only at a file (no chunk or source span), so they are the weakest for span highlighting.

### Cited Findings
**Anthropic Citations API (GA; Claude API, Bedrock, Google Cloud, Microsoft Foundry, "Claude Platform on AWS"; all active models; ZDR-eligible)** — [Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Three document types:
  - Plain text: chunked into sentences; cited by `char_location`, 0-indexed char range with exclusive end.
  - PDF: text extracted and chunked into sentences; cited by `page_location`, 1-indexed pages with exclusive end page.
  - Custom content: your blocks are used as-is with no further chunking; cited by `content_block_location`, 0-indexed block range.
  - Search-result blocks are also citable. — [Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Chunking sets the minimum citation granularity. Sentence chunking lets Claude cite one sentence or chain consecutive sentences. For bullet points or transcripts, use custom content. For sentence-level citation within RAG chunks, put each chunk in its own plain-text document. — [Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Reliability: "citations are guaranteed to contain valid pointers to the provided documents" because the API parses them and extracts `cited_text` directly. In Anthropic's evaluations, the feature is "significantly more likely to cite the most relevant quotes" than prompt-based citing. (No numeric figure is published.) — [Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Cost: slight input-token increase from system-prompt additions and chunking. `cited_text` does not count toward output tokens, and it does not count as input when passed back in later turns. Citations work with prompt caching, token counting and batch processing. — [Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Limitations:
  - Text-only: no image citations, so scanned PDFs without a text layer are not citable.
  - Citations must be enabled on all or none of the documents in a request.
  - Citations are incompatible with structured outputs (the API returns a 400 error with `output_config.format`).
  - `title` and `context` fields are passed to the model but are never cited, which makes them useful for metadata such as JSON bibliographic data. — [Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/citations)

**OpenAI file_search (Responses API)**
- The `file_citation` annotation has `file_id`, `filename`, `index` and `type`. The reference calls `index` "the index of the file in the list of files", while an OpenAI staff forum reply says it is the position in the text where the citation goes. — [OpenAI API reference](https://developers.openai.com/api/reference/typescript/resources/responses); [OpenAI forum](https://community.openai.com/t/how-to-handle-file-citations-with-the-new-responses-api/1146454)
- Developers report the annotations map only to the file, not the chunk, and give a single insertion point rather than a start/end range. Retrieved results (via `include`) contain chunks that were not used in annotations. — [OpenAI forum](https://community.openai.com/t/file-search-annotations-reference-a-file-rather-than-a-chunk/1367438); [OpenAI forum](https://community.openai.com/t/how-can-i-correlate-inline-citations-with-file-search-results/1335592); [OpenAI file search guide](https://developers.openai.com/de-DE/api/docs/guides/tools-file-search)

**Google Gemini grounding**
- `groundingMetadata` contains:
  - `groundingChunks`: the retrieved sources (web URI and title, or `retrievedContext` for Vertex AI Search data stores).
  - `groundingSupports`: each has a `segment` (startIndex and endIndex in the response text) plus `groundingChunkIndices`, with optional `confidenceScores` seen in Vertex responses.
  - Google's docs present this as the way to build inline citations.
  - `groundingAttribution` is deprecated in favor of `groundingChunks`. — [Gemini grounding docs](https://ai.google.dev/gemini-api/docs/grounding/search-suggestions?hl=hi); [Google dev forum](https://discuss.google.dev/t/how-to-understand-the-json-returned-from-gemini-with-respect-to-citations/165811); [Firebase GroundingMetadata ref](https://firebase.google.com/docs/reference/swift/firebaseailogic/api/reference/Structs/GroundingMetadata); [Firebase Kotlin ref](https://firebase.google.com/docs/reference/kotlin/com/google/firebase/ai/type/GroundingMetadata)
- Vertex AI also supports grounding against your own search API. — [Vertex docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/grounding/grounding-with-your-search-api?authuser=0)

**Cohere RAG citations**
- v2 citations carry `start` and `end` (char indices in the response), the cited `text`, and `sources`. v1 used `document_ids`, for example `ChatCitation(start=50, end=124, ...)`. The end offset is exclusive. — [Cohere RAG citations](https://docs.cohere.com/docs/rag-citations); [Cohere v1 to v2 migration](https://docs.cohere.com/docs/migrating-v1-to-v2); [LangChain CohereCitation](https://python.langchain.com/api_reference/cohere/common/langchain_cohere.common.CohereCitation.html)

### Inferences
- The API shapes differ in a way that matters for the app:
  - Anthropic returns source-side spans: the exact quoted sentence and its location in the source. That maps directly onto "highlight this passage in the PDF/note" UX.
  - Gemini and Cohere mainly return output-side spans: which part of the answer is supported. Gemini then gives only chunk or URL-level source pointers.
  - A best-in-class app should normalize all of these to one internal model: {answer span → source doc → source span → verbatim quote}. For vendors without source spans, recover the quote by fuzzy-matching the answer segment against the retrieved chunk.
- Anthropic's "valid pointer" guarantee only means the quote really exists in the source. It does not mean the cited quote entails the claim, so a separate entailment check is still needed.
- Because citations and structured outputs are incompatible on Anthropic, any "insert citation-backed paragraph" feature must parse interleaved text and citation blocks rather than using JSON mode.
- Running custom-content documents with one block per paragraph or sentence of the user's source notes gives deterministic, app-controlled IDs. That makes it easy to link back to the user's own library.

### Gaps
- No published numeric accuracy (precision/recall) for any vendor's native citation feature was found.
- Exact current OpenAI `index` semantics are disputed. Test before relying on them.
- Current Gemini pricing for grounding with Google Search was not retrieved this session.
- Whether Gemini segment offsets are byte or char offsets was not confirmed from a primary source. Earlier developer reports suggested byte offsets in UTF-8 (background, not verified this session).

## Hallucination rates of citations and grounding benchmarks

### Takeaway
Fabricated references remain a measurable 2025–2026 problem. In a June 2025 test, GPT-4o fabricated about 20% of citations, and over half of the real ones had bibliographic errors. Fabricated references in published biomedical papers rose from 1 in 2,828 papers (2023) to 1 in 277 (early 2026). Domain RAG tools (legal) still hallucinated on 17–34% of queries in the 2024 Stanford study, which is older. On summarization faithfulness, the best models now hallucinate at about 2–4% on Vectara's leaderboard.

### Cited Findings
**Fabricated references**
- In a JMIR Mental Health study (2025), GPT-4o was asked for six literature reviews in June 2025, producing 176 citations. Results:
  - 35 (19.9%) were fabricated.
  - Of the 141 non-fabricated citations, 77 (54.6%) had bibliographic errors, most often incorrect DOIs. The press release gives 45.4%, which conflicts with the paper.
  - Fabrication was higher for less familiar topics and more specific prompts. — [JMIR Ment Health 2025](https://mental.jmir.org/2025/1/e80371); [EurekAlert](https://www.eurekalert.org/news-releases/1106130)
- The Lancet audit (Topaz et al., 2026) covered about 2.5M PMC OA papers and 125.6M references, Jan 2023 to Feb 2026. It verified references via DOI/PMID resolution, metadata comparison, Crossref, OpenAlex and Google Scholar, with manual validation. Findings:
  - 4,046 fabricated citations in 2,810 papers.
  - Rate of papers with a fabricated reference: 1 in 2,828 (2023), 1 in 458 (2025), 1 in 277 (early 2026).
  - The study does not attribute every fabricated reference to LLMs; paper mills and manipulation are also possible. — [Badalova & Mayr 2026, arXiv:2607.22693](https://arxiv.org/pdf/2607.22693); [STAT News](https://www.statnews.com/2026/05/07/lancet-study-finds-steep-rise-fraudulent-citations-academic-papers/)
- Zhao et al. (2026) studied about 111M references across arXiv, bioRxiv, SSRN and PMC using title matching against Semantic Scholar, OpenAlex and Google Scholar versus a pre-LLM baseline. They estimate about 146,932 hallucinated citations in 2025. Estimated 2025 rates: arXiv 0.39%, bioRxiv 0.21%, SSRN 1.91%, PMC 0.27%. — [Badalova & Mayr 2026](https://arxiv.org/pdf/2607.22693)
- Conferences: ACL 2026 camera-ready checks found citations to non-existent literature in more than 100 accepted papers. GPTZero found 50+ hallucinated references in ICLR 2026 submissions. arXiv announced bans for researchers using hallucinated references (Nature news, 2026). — [Badalova & Mayr 2026](https://arxiv.org/pdf/2607.22693)
- A 2026 paper in *Accountability in Research* argues that hallucinated citations may constitute research misconduct when citations function as data. — [Taylor & Francis 2026](https://www.tandfonline.com/doi/full/10.1080/08989621.2026.2645390)
- Older figures (55% of GPT-3.5 and 18% of GPT-4 citations fabricated) come from Walters & Wilder 2023 and are pre-2024. — [Badalova & Mayr 2026 references](https://arxiv.org/pdf/2607.22693)

**Legal AI (2024, older)**
- The Stanford RegLab/HAI preregistered study used 200+ legal queries. Lexis+ AI and Ask Practical Law AI hallucinated more than 17% of the time; Westlaw AI-Assisted Research more than 34% (VentureBeat reports 17–33%). All three beat GPT-4 without RAG. Thomson Reuters disputed the use of Practical Law for primary-law research. — [Stanford HAI](https://hai.stanford.edu/news/ai-trial-legal-models-hallucinate-1-out-6-queries); [VentureBeat](https://venturebeat.com/ai/stanford-study-finds-ai-legal-research-tools-prone-to-hallucinations)

**Benchmarks**
- Vectara Hallucination Leaderboard (HHEM-scored summarization, English only, proprietary dataset), last updated May 11, 2026:
  - Top entries: antgroup/finix_s1_32b 1.8%, openai/gpt-5.4-nano 3.1%, gemini-2.5-flash-lite 3.3%, Phi-4 3.7%.
  - It measures only summarization faithfulness, not QA or citation quality. — [Vectara GitHub](https://github.com/vectara/hallucination-leaderboard)
  - Third-party mirrors and blogs give conflicting frontier-model figures; treat them as unverified. — [benchmarklist](https://benchmarklist.com/benchmarks/vectara_hhem/)
- FACTS (Google DeepMind/Kaggle):
  - The original FACTS Grounding (Dec 2024) has 1,719 examples of long-form answers grounded in provided documents, scored by LLM judges. — [The Decoder](https://the-decoder.com/google-deepmind-launches-new-ai-fact-checking-benchmark-with-gemini-in-the-lead/)
  - The newer FACTS suite averages Grounding v2, multimodal, parametric and search factuality. Gemini 3 Pro reportedly leads with a 68.8 overall FACTS Score, ahead of Gemini 2.5 Pro (62.1) and GPT-5 (61.8). This is secondary-source data. — [DAIR.AI summary](https://academy.dair.ai/papers/facts-leaderboard)
- ALCE (Princeton, 2023) scores citation recall and precision with an NLI model. Agreement with humans is 85.1% (recall) and 77.6% (precision). The NLI model cannot detect partial support. — [ALCE paper](https://arxiv.org/abs/2305.14627v2)
- ALiiCE proposes atomic-claim-level positional citation evaluation. — [ALiiCE](https://arxiv.org/pdf/2406.13375)
- LongCite/LongBench-Cite (2024) evaluates sentence-level fine-grained citations in long-context QA. Citation quality covers full support, no irrelevant snippets, and fine-grained snippets; correctness is judged by GPT-4o. — [LongCite](https://arxiv.org/pdf/2409.02897)
- A 2025 survey covers LLM sourcing and attribution. — [LLM Sourcing survey](https://arxiv.org/pdf/2510.10161)
- In a medical-writing comparison, GPT-5 had a general hallucination rate of 9.6% versus 12.9% for GPT-4o. This measures general hallucination, not citations specifically. — [PMC12701941](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12701941/)

### Inferences
- The app should never let an LLM produce bibliographic entries from parametric memory. Every reference should come from a resolved identifier (DOI, ISBN, URL capture) in the user's library, with the model only selecting among existing entries.
- Even with RAG (the legal tools), error rates of 1 in 6 or worse were observed in 2024. Post-hoc verification should therefore be mandatory, not optional.

### Gaps
- No 2026 replication of the Stanford legal-AI study was found.
- Current SimpleQA and RAGTruth numbers for frontier models were not retrieved.
- Current FACTS Grounding-only scores per model were not found from a primary source; check the Kaggle leaderboard.

## Post-hoc verification: NLI/entailment checkers, claim decomposition, quote verification, local options

### Takeaway
Small local checkers exist and are good enough for flagging, not for proof:
- Bespoke-MiniCheck-7B: about 77.4% balanced accuracy on LLM-AggreFact (vendor figure), licensed CC BY-NC 4.0, so not usable commercially without a license.
- HHEM-2.1-Open: about 71.8%, open weights.
- Exact and fuzzy quote matching against the source is deterministic and should be the first line of defense.

### Cited Findings
- Bespoke-MiniCheck-7B reports 77.4% average balanced accuracy on LLM-AggreFact (11 datasets), versus 71.8% for Vectara HHEM-2.1. Its license is CC BY-NC 4.0 (non-commercial). It is distributed via Ollama (`bespoke-minicheck:7b`) for local use. — [Bespoke Labs](https://bespokelabs.ai/bespoke-minicheck); [Bespoke docs](https://docs.bespokelabs.ai/models/bespoke-minicheck); [Ollama mirror](https://mail233685.mx4414.netcup.net/library/bespoke-minicheck:7b)
- The LLM-AggreFact leaderboard ranks by average balanced accuracy (the mean of TPR and TNR). — [alphaXiv LLM-AggreFact](https://www.alphaxiv.org/benchmarks/the-university-of-texas-at-austin/llm-aggrefact)
- "Verify with Caution" (2025) warns that factuality metrics, including the top ones (Bespoke-7B, gpt-4-turbo), are imperfect and can mislead when used as ground truth. — [arXiv:2501.14883](https://arxiv.org/pdf/2501.14883)
- HHEM-2.1-Open, Vectara's evaluation model, is downloadable from Hugging Face and Kaggle. — [Vectara GitHub](https://github.com/vectara/hallucination-leaderboard)
- ALCE's NLI-based citation checking misses partial support. — [ALCE](https://arxiv.org/abs/2305.14627v2)
- Semantic-entropy hallucination detection (Farquhar et al., Nature 2024) is a sampling-based method. — cited in [Badalova & Mayr 2026](https://arxiv.org/pdf/2607.22693)

### Inferences
- Recommended verification pipeline, cheapest first:
  1. Deterministic quote check: normalize Unicode, whitespace and quotes, then exact match, then fuzzy match (e.g., rapidfuzz partial ratio) of any quoted string against the cited source span. This needs no ML and runs fully offline.
  2. Sentence or atomic-claim decomposition of the AI-written passage.
  3. Local NLI or entailment for each claim against its cited span: HHEM-2.1-Open, or MiniCheck (the Flan-T5 or RoBERTa variants, background: MIT-licensed, not verified this session); Bespoke-7B only with a commercial license.
  4. Optionally, an LLM judge for low-confidence cases.
- Flag "unsupported", "partially supported" and "no citation" states rather than auto-deleting.

### Gaps
- FActScore, SAFE, VeriScore, AlignScore and Chain-of-Verification were not re-researched this session. Background, not verified:
  - FActScore (Min et al., 2023) decomposes biography text into atomic facts checked against Wikipedia.
  - SAFE (Google DeepMind, 2024) uses an LLM plus Google Search per atomic fact.
  - VeriScore (2024) focuses on verifiable claims only.
  - AlignScore (2023) is a RoBERTa-based alignment model (MIT).
  - CoVe (Meta, 2023) is prompt-based self-verification.
- Licenses and current accuracies of the MiniCheck small variants were not verified this session.

## Citation-integrity checks for bibliographies (DOI resolution, OpenAlex, Semantic Scholar, retractions, fabricated references, quote-page matching, Scite)

### Takeaway
Retraction Watch data is now open via the Crossref REST API (`update-to` field, `filter=update-type:retraction`) and as a daily-updated CSV. Existing open hallucinated-reference checkers achieve high recall (up to 32 of 33) but many false positives (up to 53 of 71 real references flagged). Multi-source matching across Crossref, OpenAlex, Semantic Scholar, DBLP, arXiv and PubMed, plus human review, is the current best practice.

### Cited Findings
**Retractions**
- Crossref acquired the Retraction Watch database in September 2023, its first third-party metadata supplement. Retractions appear in the `update-to` field of REST API JSON, with `source` set to `publisher` or `retraction-watch`. — [Crossref docs](https://crossref.org/documentation/retrieve-metadata/retraction-watch/); [Crossref blog](https://www.crossref.org/blog/retraction-watch-retractions-now-in-the-crossref-api/)
- Query: `works?filter=update-type:retraction` returns retraction notices. To determine whether a specific work was retracted, look at that DOI's `update-to` and `updated-by` relations; there is no direct "retracted works" filter. — [Crossref community](https://community.crossref.org/t/retraction-watch-retractions-now-in-the-crossref-api-crossref/13119)
- A CSV of the full dataset is updated every working day and can be cloned from Crossref's GitLab, enabling offline lookup. — [Crossref docs](https://crossref.org/documentation/retrieve-metadata/retraction-watch/)
- An experimental Crossref Labs `update-nature` field also covers corrections and expressions of concern. — [Hauschke & Nazarovets 2025](https://elibrary.kubg.edu.ua/id/eprint/51506/1/hauschke-nazarovets-2025.pdf)

**Fabricated or mismatched reference detection tools**
- Badalova & Mayr (GESIS, arXiv July 2026) tested 5 tools on 104 manually verified references, 33 of them problematic:

  | Tool | Problematic refs caught (of 33) | Real refs wrongly flagged (of 71) | Databases |
  |---|---|---|---|
  | RefChecker | 32 | 36 | S2, OpenAlex, Crossref, DBLP, ACL Anthology; also LLM-assisted web search |
  | CheckIfExist | 31 | 34 | Crossref, S2, OpenAlex |
  | Hallucinator | 29 | 28 (most balanced) | Crossref, S2, OpenAlex, arXiv, DBLP, PubMed, Europe PMC, ACL |
  | HalRef | 24 | 53 | — |
  | HalluCiteChecker | 18 | 20 (fewest false positives, most misses) | local ACL Anthology, arXiv, DBLP |

  — [arXiv:2607.22693](https://arxiv.org/pdf/2607.22693)
- Common failure modes in those tools: inaccurate reference extraction from PDFs, sensitivity to BibTeX formatting, limited database coverage, API restrictions, and fiddly setup (GROBID or LLM extractors). General AI-text detectors such as Pangram cannot check references. Flagged items need manual comparison of DOI, title, authors and venue. — [arXiv:2607.22693](https://arxiv.org/pdf/2607.22693)
- Large audits combine DOI/PMID resolution, metadata comparison, title text similarity, LLM-assisted screening and manual validation. — [arXiv:2607.22693](https://arxiv.org/pdf/2607.22693)

### Inferences
- In-app checker design:
  - (a) Resolve each DOI via Crossref, or via DataCite for datasets and Zenodo/arXiv DOIs.
  - (b) Compare returned title, authors and year with the user's entry using fuzzy thresholds.
  - (c) For entries without a DOI, do title search on OpenAlex and Semantic Scholar and require cross-source consensus.
  - (d) Check Crossref `updated-by` for retraction, correction or expression of concern, or use the local Retraction Watch CSV offline.
  - (e) Show "not found" as "unverified, please check", not "fake", given the observed false-positive rates.
- Quote-to-page checks: when the user cites "p. 47" with a quotation and the PDF is in their library, extract that page's text and fuzzy-match the quote. This is offline and deterministic, and it pairs naturally with Anthropic's `page_location`.

### Gaps
- Scite Smart Citations (supporting/mentioning/contrasting classification), its API terms, and its 2026 status were not researched this session. Scite was acquired by Research Solutions in 2023 (background, not verified).
- OpenAlex and Semantic Scholar 2026 rate limits and pricing were not retrieved. OpenAlex introduced API keys and usage-based limits in 2025 (background, not verified this session).
- DataCite API specifics were not retrieved.

## Bibliographic stack (CSL, citeproc engines, formats, Zotero, PDF metadata extraction, ISBN/web capture, archiving)

### Takeaway
For a desktop app, the strongest permissively licensed CSL engine is Hayagriva (Rust, MIT/Apache-2.0). It supports all 2,600+ official CSL styles and BibTeX/BibLaTeX parsing; the styles themselves are CC BY-SA 3.0. Zotero 7's local API (localhost:23119) gives read-only, no-auth access to the user's library. GROBID (Apache-2.0) extracts references from PDFs with about 0.87–0.90 F1 on biomedical sets but degrades on humanities footnote styles, where LLMs now do better. Link rot is severe (38% of 2013 pages gone by 2023), so archiving web citations through Wayback SPN2 is worthwhile but unreliable.

### Cited Findings
**Hayagriva and CSL**
- Hayagriva (Typst's bibliography library) is dual-licensed MIT/Apache-2.0. It supports all styles in the official CSL repository (over 2,600) and parses BibTeX/BibLaTeX. Bundled CSL styles (archive feature) are CC BY-SA 3.0. Typst accepts Hayagriva YAML or BibLaTeX and custom CSL files. — [docs.rs hayagriva](https://docs.rs/crate/hayagriva/0.8.1); [GitHub](https://fr.github.com/typst/hayagriva); [Typst docs](https://typst.app/docs/reference/model/bibliography)

**Zotero 7 local API**
- Introduced in Zotero 7 (beta 88), the API is a near-complete, read-only implementation of Web API v3 at `http://localhost:23119/api/`.
- It requires the setting "Allow other applications on this computer to communicate with Zotero" (otherwise 403). It has no authentication, uses user ID 0, and the official docs say only GET is supported.
- It is separate from the connector endpoints (`/connector/...`).
- A third-party MCP README claims writes in "Zotero 10+". This is unverified. — [Zotero Local API docs](https://www.zotero.org/support/dev/web_api/v3/local_api); [zotero-dev announcement](https://groups.google.com/g/zotero-dev/c/ElvHhIFAXrY/m/fA7SKKwsAgAJ); [Zotero forum](https://forums.zotero.org/discussion/comment/493530)

**PDF reference extraction**
- GROBID: about 0.87 F1 for reference extraction and parsing on 1,943 PMC PDFs (90,125 references), and about 0.90 on bioRxiv, using its deep-learning citation model. — [GROBID docs](https://grobid.readthedocs.io/en/latest/Introduction/)
- A 2023 benchmark found GROBID led reference extraction at F1 0.79, ahead of CERMINE at 0.74. — [arXiv:2303.09957](https://arxiv.org/pdf/2303.09957)
- A 2026 benchmark on SSH (social sciences and humanities) papers found LLMs more robust than GROBID. Footnote-only and mixed citation regimes, especially in German, cause big drops, and GROBID degrades most. — [arXiv:2603.13651](https://arxiv.org/pdf/2603.13651)

**Link rot and archiving**
- Pew (May 2024): 38% of webpages that existed in 2013 were inaccessible a decade later, versus 8% of 2023 pages. A quarter of all pages that existed between 2013 and 2023 were gone by October 2023. — [Pew report PDF](https://pewresearch.org/wp-content/uploads/sites/20/2024/05/pl_2024.05.17_link-rot_report.pdf)
- Wayback Save Page Now 2 API: POST to `https://web.archive.org/save` with S3-style API keys (preferred over cookies), then poll a status endpoint. Errors include `error:blocked`. The waybackpy maintainers call the save API "very unreliable". Clients: npm `spn2`, PyPI `waybackpy`. — [SPN2 API doc](https://docs.google.com/document/d/1Nsv52MvSjbLb2PCpHlat0gkzw0EvtSgpKHu4mk0MnrA/mobilebasic); [waybackpy source](https://codeclimate.com/github/akamhy/waybackpy/waybackpy/save_api.py/source)

### Inferences
- Suggested architecture:
  - Store references internally as CSL-JSON, the lingua franca of citeproc-js, pandoc and Zotero.
  - Import and export BibTeX, BibLaTeX and RIS.
  - Render with Hayagriva for an MIT/Apache, fully offline engine, or with citeproc-js inside an Electron/JS app (see license gap).
  - Ship CSL styles separately to respect CC BY-SA attribution.
- Library sync: read Zotero via the local API (offline, no key). For writes, either use the Zotero Web API with a user key or export files; Better BibTeX's auto-export of .bib is a common integration path (background, not verified this session).
- Web citations: capture metadata, archive locally (e.g., a SingleFile/WARC snapshot), and also request a Wayback capture with retry and backoff. Store `archived-url` and access date in the record.

### Gaps
- Not verified this session:
  - citeproc-js license (believed to be dual CPAL/AGPL; background).
  - citeproc-py and pandoc citeproc details (pandoc is GPL-2.0+; background).
  - CSL 1.0.2 and CSL-M feature specifics.
  - Better BibTeX features.
  - Citoid/Zotero translation-server (AGPL; background).
  - Open Library ISBN API.
  - Perma.cc pricing and API.
  - Mendeley and EndNote API status in 2026.
  - CERMINE license (AGPL; background).

## Provenance and transparency (C2PA for text, AI vs human tracking, disclosure policies, citing AI)

### Takeaway
The C2PA spec (v2.4) defines a way to embed manifests in unstructured text using invisible Unicode variation selectors after NFC-normalized text. Implementations are early, third-party and uncertified, and the method is fragile to copy/paste and normalization. Style guides (APA, MLA, Chicago) each have AI-citation formats, but secondary sources disagree on APA's current form.

### Cited Findings
**C2PA for text**
- The C2PA Technical Specification 2.4 section "Embedding Manifests into Unstructured Text" places a manifest store, encoded as Unicode variation selectors and preceded by U+FEFF, after NFC-normalized text.
- Hard binding excludes the selector bytes and hashes the NFC-normalized remainder.
- The spec reportedly reserves this method for cases where no other embedding works; structured text and HTML have their own methods.
- Implementations: c2patxt (Python) and c2pa-unstructured-text (Rust, v0.2.0). Neither is a C2PA release nor conformance-certified. — [c2patxt PyPI](https://pypi.org/project/c2patxt/); [docs.rs c2pa-unstructured-text](https://docs.rs/crate/c2pa-unstructured-text/latest/source/README.md)

**Citing AI**
- APA:
  - One source claims APA's September 2025 update puts the company (e.g., OpenAI or Anthropic) in the author position. Others still show "ChatGPT (version) [Large language model]".
  - Grammarly says APA treats private chats as personal communication.
  - This is unresolved; check the APA Style blog directly. — [CASRAI guide](https://casrai.org/guides/how-to-cite-chatgpt-and-ai-tools-apa-mla-chicago); [Grammarly](https://www.grammarly.com/blog/citations/how-to-cite-chatgpt); [EditorWorld](https://www.editorworld.com/article/apa-citation-for-ai-tools)
- MLA 9: the prompt is the title, the tool is the container, followed by version, company and date. — [UF Business Library](https://businesslibrary.uflib.ufl.edu/cite-chatgpt)
- Chicago 18: footnote only; no bibliography entry unless there is a public link to the exchange; author-date form "(ChatGPT, 2025)". — [UF Business Library Chicago](https://businesslibrary.uflib.ufl.edu/ai-citation-styles/chicago)

### Inferences
- For AI vs human provenance inside the app, keep an internal per-span provenance log recording origin (human-typed, AI-generated, AI-edited, pasted), model, prompt, timestamp and the source citations used. Treat C2PA text manifests as an optional export, because variation selectors are routinely stripped by editors, email and CMSs.
- Auto-generate a disclosure statement and AI-tool citations in the selected style from that log.

### Gaps
- Publisher AI policies (Nature, Elsevier, Science, ICMJE, COPE) for 2026 were not retrieved. Background, not verified: they generally bar AI authorship, require disclosure in methods or acknowledgements, and Nature bars AI-generated images.
- The exact version in which C2PA text embedding was introduced was not determined.
- No data was found on watermark survival rates for text.

## UX patterns for showing grounding

### Takeaway
No primary UX research source was retrieved this session. The API data structures above directly enable the established patterns: inline citation chips, hover previews of `cited_text`, PDF page or span highlighting, and per-sentence support status.

### Cited Findings
- Anthropic returns `cited_text` plus char, page or block indices for each cited span, enough to render hover previews and jump-to-source highlighting. — [Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/citations)
- Gemini `groundingSupports` segments plus `confidenceScores` enable per-segment confidence display. Google docs describe using segment offsets to insert inline citations. — [Gemini grounding docs](https://ai.google.dev/gemini-api/docs/grounding/search-suggestions?hl=hi); [Google dev forum](https://discuss.google.dev/t/how-to-understand-the-json-returned-from-gemini-with-respect-to-citations/165811)
- HalluCiteChecker outputs a highlighted PDF of suspicious citations. RefChecker outputs per-reference errors, warnings and corrections. These are useful report UX precedents. — [arXiv:2607.22693](https://arxiv.org/pdf/2607.22693)

### Inferences
- Recommended patterns:
  - Numbered or author-date citation chips in the text, linked to library items.
  - A hover card showing the verbatim source span and page, with an "open at page" action.
  - Three-state underline per sentence: supported (entailment passed), weak or partial, unsupported or uncited.
  - Distinct styling for AI-originated text until the user accepts or edits it.
  - A bibliography health panel showing unresolved DOIs, metadata mismatches, retractions or corrections, and dead links with archived copies.
  - Never display a numeric "confidence %" without calibration; use categorical states.

### Gaps
- No user studies on citation-chip or confidence-indicator effectiveness (e.g., over-reliance effects) were retrieved this session.
- Product examples (Perplexity, NotebookLM, Elicit, Consensus, Scite Assistant, Word/Copilot) were not researched here.
