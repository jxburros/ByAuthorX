# Context Engineering, Memory, and Decision Models for LLM-Assisted Writing Tools (as of Oct 2026)

Scope: how a desktop word processor with an existing MCP server, a Chat/Work assistant mode, bring-your-own-key (BYOK) cloud providers and local model providers should decide what context to send, when to retrieve, how to route requests, and how to keep the user in control.

Source-verification note: items marked [fetched] were read during this session. Items marked [prior knowledge] are well-known arXiv papers cited from the researcher's background knowledge, with abstract-level claims that were not re-fetched this session. The report writer should treat their exact numbers as "per the paper" and spot-check them if they become central.

## 1. Context engineering: what to put in the window, and how long contexts degrade

### Takeaway
Treat context as a finite "attention budget" with diminishing returns. Every major 2025–26 write-up (Anthropic, Manus, Chroma) converges on the same approach: send small, high-signal context, keep stable material in a cacheable prefix, load bulk material just in time through tools, and compact or offload the rest to notes or files. Long-context benchmarks show that advertised window sizes overstate how much of a window a model can use well, especially when the query and the relevant passage share few words or when near-duplicate distractors are present. Both are common in manuscripts, with their recurring characters, drafts and revisions.

### Cited Findings
**Anthropic, "Effective context engineering for AI agents" (Sep 29, 2025) [fetched]**
- Recall drops as the context fills ("context rot"). This happens on all models, to different degrees. "Context, therefore, must be treated as a finite resource with diminishing marginal returns." — [Anthropic Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Two retrieval styles:
  - Pre-loading: retrieval runs before inference, usually embedding search.
  - Just-in-time: the agent holds "lightweight identifiers (file paths, stored queries, web links, etc.)" and loads the content with tools at runtime. Runtime exploration is slower and needs guidance to avoid wasting context.

  The article suggests a hybrid of the two may work best. — [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Claude Code is the hybrid example: "CLAUDE.md files are naively dropped into context up front," while glob and grep "navigate its environment and retrieve files just-in-time." — [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Long-horizon techniques:
  - Compaction: summarize a conversation near the window limit and start a new window. Tune for recall first, then precision. Clearing old tool results is the lightest form.
  - Structured note-taking: NOTES.md files, to-do lists.
  - Sub-agents: each returns "a condensed, distilled summary" of roughly 1,000–2,000 tokens.
  - Tool sets: keep them small and non-overlapping, because ambiguity between tools hurts performance.

  — [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

**Anthropic context-management API (Sep 29, 2025) [search-verified]**
- Context editing automatically removes stale tool calls and results. The memory tool is a file-based directory the model can create, read, update and delete in, and it persists across conversations.
- In Anthropic's internal agentic-search evaluation, memory tool plus context editing improved performance by 39% over baseline. Context editing alone improved it by 29%.
- In a 100-turn web-search evaluation, context editing cut token use by 84% and let agents finish workflows that otherwise ran out of context.
- These are internal evaluations with no independent replication found. Some press reports wrongly credit the 84% to the combination of the two features.

— [Anthropic news: context management](https://www.anthropic.com/news/context-management); [Claude blog](https://claude.com/blog/context-management)

**Manus, "Context Engineering for AI Agents: Lessons from Building Manus" (Yichao "Peak" Ji, Jul 18, 2025) [fetched]**
- KV-cache hit rate is "the single most important metric for a production-stage AI agent." The input:output token ratio is about 100:1. Cached Claude Sonnet input cost $0.30/MTok against $3/MTok uncached, a 10x difference (2025 prices). — [Manus blog](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- "Mask, don't remove" tools: adding or removing tools mid-task invalidates the cache and confuses the model. Use consistent tool-name prefixes (`browser_`, `shell_`) and constrain the model's choice of tool instead. — [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- Use the file system as external, restorable memory: you can drop a page's content from context as long as you keep its URL or path. — [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- Recitation: rewriting a todo.md keeps goals in recent attention across tasks of about 50 tool calls. — [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- Keep errors in context so the model learns from them. Vary serialization to avoid few-shot ruts; in Manus's example, an agent fell into a repetitive pattern while reviewing 20 resumes. — [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)

**Chroma, "Context Rot" technical report (Jul 14, 2025) [fetched]**
- 18 LLMs tested: Anthropic 5, OpenAI 7, Google 3, Alibaba 3. — [Chroma Research](https://www.trychroma.com/research/context-rot)
- Performance fell faster with input length when the needle and the question were less semantically similar. — [Chroma](https://www.trychroma.com/research/context-rot)
- A single distractor lowered accuracy, four lowered it further, and the impact was uneven across distractors. — [Chroma](https://www.trychroma.com/research/context-rot)
- All 18 models did better on shuffled haystacks than on logically coherent ones, which the authors call "surprising." — [Chroma](https://www.trychroma.com/research/context-rot)
- LongMemEval, 306 prompts: full prompts averaged about 113k tokens and focused prompts about 300 tokens. Every model did notably better on the focused version. — [Chroma](https://www.trychroma.com/research/context-rot)
- Claude models hallucinated least and abstained most; GPT models hallucinated most. — [Chroma](https://www.trychroma.com/research/context-rot)

**NoLiMa (ICML 2025; arXiv Feb 2025, revised Jul 2025) [fetched abstract]**
- The benchmark removes literal lexical overlap between question and needle. Of 13 models claiming at least 128K context, 11 fell below 50% of their short-context baseline at 32K. GPT-4o fell from 99.3% to 69.7%. Reasoning and chain-of-thought did not fix this. — [arXiv 2502.05167](https://arxiv.org/abs/2502.05167)

**Other long-context benchmarks [prior knowledge, not re-fetched]**
- RULER (NVIDIA, 2024): synthetic tasks beyond needle-in-a-haystack, such as multi-key retrieval, variable tracking and aggregation. It found most models' "effective" context length was well below the advertised length. — [arXiv 2404.06654](https://arxiv.org/abs/2404.06654)
- Michelangelo (Google DeepMind, 2024): "latent structure queries" measuring long-context reasoning beyond retrieval. — [arXiv 2409.12640](https://arxiv.org/abs/2409.12640)
- "Lost in the Middle" (2023): U-shaped accuracy by position, with information in the middle of the context used least. This is older and may be partly superseded by newer models, but the positional caution still appears in vendor guidance. — [arXiv 2307.03172](https://arxiv.org/abs/2307.03172)
- Fiction.LiveBench: a fiction-comprehension long-context benchmark directly relevant to manuscripts. I did not fetch current 2026 leaderboard numbers. — [fiction.live](https://fiction.live/stories/Fiction-liveBench/oQdzQvKHw8JyXbN87)

### Inferences
- In a manuscript, the needle (for example, "what color were her eyes in ch. 3") has low lexical overlap with the question and many near-duplicate distractors: earlier drafts, similar scenes, other characters. These are exactly the conditions under which Chroma and NoLiMa show the steepest decline. Even when the whole novel fits in the window, a writing tool should prefer targeted retrieval plus a compact "story bible" over dumping the full manuscript for precision questions.
- Full-manuscript context is still justified for holistic tasks: pacing, theme, continuity sweeps, and summaries. Treat these as a distinct route (see section 2), not the default.
- Recommended prompt layout, ordered for caching:
  1. Tools.
  2. System instructions and app persona.
  3. Project memory: style guide, character bible, glossary.
  4. Optionally, the full manuscript snapshot, when the full-document route is chosen.
  5. Conversation.
  6. Volatile items: selection, cursor context, retrieved snippets, timestamps.
- Manus's "mask, don't remove" lesson applies to Chat vs Work mode. Keep one stable tool list and restrict tools per mode (for example with allowed_tools or tool_choice). Rebuilding the tool list breaks caching.

### Gaps
- No current (2026) Fiction.LiveBench scores were fetched, and no independent 2026 study quantified context rot on frontier 2026 models (the Opus 5.x and GPT-6 era). Chroma's study covers mid-2025 models.

## 2. Decision models for retrieval: when to retrieve, how to route, agentic search vs vectors, long context vs RAG

### Takeaway
The research line runs from "always retrieve" to "decide whether, what and how much to retrieve per query." For a writing tool, the practical design is a light query classifier or router. It picks among no-retrieval, selection-only, targeted retrieval (lexical, semantic, or entity-indexed), agentic multi-step search, and full-document long context, with corrective fallbacks when retrieval confidence is low. Coding agents moved from vector RAG to agentic grep/glob search, but that evidence is specific to code, which is precise and lexical. Prose needs hybrid retrieval: lexical plus semantic plus a structured entity index.

### Cited Findings
**Adaptive retrieval papers [prior knowledge, abstracts not re-fetched]**
- Self-RAG (Asai et al., 2023): trains the model to emit reflection tokens that decide whether to retrieve and to critique the relevance and support of passages, retrieving on demand. — [arXiv 2310.11511](https://arxiv.org/abs/2310.11511)
- FLARE (Jiang et al., 2023): active retrieval during generation. It drafts the next sentence and, when it contains low-confidence tokens, uses that sentence as a query to retrieve and regenerate. — [arXiv 2305.06983](https://arxiv.org/abs/2305.06983)
- Corrective RAG (CRAG; Yan et al., 2024): a lightweight retrieval evaluator grades retrieved documents as Correct, Incorrect or Ambiguous. It triggers knowledge refinement or a fallback search, plus a decompose-then-recompose filter. — [arXiv 2401.15884](https://arxiv.org/abs/2401.15884)
- Adaptive-RAG (Jeong et al., NAACL 2024): a small trained classifier predicts query complexity and routes to no retrieval, single-step retrieval, or multi-step iterative retrieval, balancing accuracy against cost. — [arXiv 2403.14403](https://arxiv.org/abs/2403.14403)
- Self-Route / "RAG or Long-Context LLMs?" (Li et al., Google DeepMind, 2024): with enough resources long context beat RAG on average, but RAG was far cheaper. Self-Route has the model judge whether the retrieved chunks are enough to answer, and escalates to long context only if not. This kept performance close to long context at a much lower cost. — [arXiv 2407.16833](https://arxiv.org/abs/2407.16833)

**Agentic search vs vector search**
- Boris Cherny of Anthropic has said early Claude Code used RAG with a local vector database, but agentic search (glob and grep) "worked better." The primary statements are social posts and talks, and the dates and wording circulate mostly through secondary write-ups. — [AI Engineer Europe 2026 talk, "RAG is dead, right?"](https://www.ai.engineer/talks/UM6sFg_jdlE-rag-is-dead-right-kuba-rogut); [SmartScope analysis](https://smartscope.blog/en/ai-development/practices/rag-debate-agentic-search-code-exploration/)
- Caveat from the same analysis: the debate concerns code exploration, and semantic indexes still win for concept search, very large corpora and non-code knowledge. The conference talk's traces are illustrative, not a controlled benchmark. — [SmartScope](https://smartscope.blog/en/ai-development/practices/rag-debate-agentic-search-code-exploration/); [AI Engineer](https://www.ai.engineer/talks/UM6sFg_jdlE-rag-is-dead-right-kuba-rogut)
- A secondary blog claims Windsurf, Cline, Devin and Amp also dropped vector indexes. Unverified. — [besthub.dev](https://www.besthub.dev/articles/why-top-ai-coding-agents-dropped-vector-databases-for-grep-based-retrieval-e16a7a8ee9f5)
- Anthropic recommends a hybrid: preload what is always needed and retrieve the rest just in time. — [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

### Inferences
Suggested routing table for a writing app. This is synthesis, not sourced; route names are illustrative.

| Route | Trigger examples | Context |
|---|---|---|
| R0 no-retrieval | "make this punchier", grammar, tone on a selection | selection + style guide |
| R1 local | "continue this paragraph" | selection + N surrounding paragraphs + scene summary |
| R2 targeted lookup | "what did I name the innkeeper?" | entity index / glossary first, then hybrid BM25 + embedding search over chunks, then quote with location |
| R3 agentic multi-hop | "find every place Mara lies to Tom" | tool loop: `search_text`, `list_chapters`, `read_range`, `get_entity`, with a budget (e.g., N tool calls) |
| R4 full-document | "assess pacing of act 2", "summarize the book" | cached full manuscript or hierarchical chapter summaries |

- Cheap routing signals: whether there is a selection, imperative edit verbs vs interrogatives, named entities found in the project glossary, and scope words ("everywhere", "whole book", "chapter 4").
- Start with rules plus a small classifier, as in Adaptive-RAG. Add a CRAG- or Self-Route-style sufficiency check: if retrieved evidence is judged insufficient, escalate one tier (R2 to R3 to R4) or abstain.
- Writers search for exact names and phrases, so lexical tools (grep-like `search_text` with regex and whole-word options) should be first-class. Embeddings help with "the scene where they argue about money." Agentic exploration over document-structure tools (chapters, scenes, headings) mirrors Claude Code's glob and grep.
- Keep the index local and incremental. A manuscript of 100–150k words, roughly 130–200k tokens, is small enough to re-index on save.

### Gaps
- No 2025–26 benchmark directly compares agentic lexical search, vector RAG and full long context on fiction or manuscript editing tasks. This is a real evidence gap; the app should run its own offline evaluation (section 7).

## 3. Prompt caching economics for re-sending a manuscript

### Takeaway
All three major providers now discount cached prefixes heavily. Anthropic reads cost 0.1x base input, or 0.05x on the latest Opus and Sonnet 5.5. OpenAI reads cost 0.1x on GPT-5.6 and later. Gemini's implicit-cache discount is 75%. Re-sending a stable manuscript prefix every turn is therefore affordable if the prefix stays byte-identical and the volatile content sits at the end.

### Cited Findings
**Anthropic [fetched docs, current Oct 2026]**
- Price multipliers:
  - 5-minute cache write: 1.25x base input.
  - 1-hour cache write: 2x.
  - Cache read: 0.1x. Opus 5.5 and Sonnet 5.5 read at 0.05x; Fable 5.1 and Mythos 5.1 read at 0.025x.

  — [Claude docs: Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Minimum cacheable length is 512 tokens on current models (Opus 5.5, Sonnet 5.5, Haiku 5.5, and others) and 1,024–4,096 on older ones. Shorter prompts are silently not cached; check `cache_creation_input_tokens` and `cache_read_input_tokens`. — [Claude docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- TTL is 5 minutes by default and refreshes on each hit; 1 hour is optional. Up to 4 breakpoints are allowed, with a 20-block lookback. Prefix order is tools → system → messages. Changing tool definitions invalidates everything; toggling citations or web search invalidates system and messages. — [Claude docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

**OpenAI [fetched docs]**
- GPT-5.6 and later:
  - Minimum 1,024 visible input tokens.
  - Cache reads cost 0.1x the uncached input rate (0.05x for GPT-6.1 Sol). Cache writes cost 1.25x.
  - TTL is `"30m"`.
  - Explicit `prompt_cache_breakpoint` is available, and `prewarm: true` caches shared context before the first request.

  — [OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
- Earlier models: retention is `in_memory` (about 5–10 minutes, up to 1 hour) or `24h`. `prompt_cache_key` helps route requests to the same cache; aim for about 15 requests per minute per key. — [OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching)
- "Cache reuse requires the entire rendered prefix to match." Compaction, summarization and truncation reset reuse, so append new messages rather than rewriting earlier turns. — [OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching)

**Google Gemini [search-verified, partly older]**
- Implicit caching is automatic on Gemini 2.5 and newer, with a 75% discount and no storage fee. The 2025 announcement put minimums at 1,024 tokens (2.5 Flash) and 2,048 (2.5 Pro). Explicit caching charges storage by duration. — [Google Developers Blog (2025)](https://developers.googleblog.com/en/gemini-2-5-models-now-support-implicit-caching/); [Vertex AI docs](https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview)
- Gemini 3 figures from a third party (4,096-token implicit minimum; explicit caching at least 32,768 tokens with a 1-hour default TTL) are unverified. — [fast.io](https://fast.io/resources/gemini-context-caching/)

### Inferences
- Worked example: a 150k-token manuscript on a model with a $3/MTok base input price.
  - Uncached: about $0.45 per turn.
  - Cached read at 0.1x: about $0.045 per turn.
  - Cached read at 0.05x: about $0.0225 per turn.
  - One 5-minute write: about $0.56; a 1-hour write: about $0.90.

  These are illustrative calculations; check each provider's current base prices.
- Every keystroke edit to the manuscript changes the prefix. Order the cached block as a snapshot that refreshes only at checkpoints (on save, or every N minutes), and send recent edits as a small trailing diff. Alternatively, cache per chapter, so earlier chapters stay as a stable prefix and only the chapter being edited sits after the last breakpoint.
- Under BYOK, show estimated cost and cache-hit rate per request so users understand the economics. Local models get the equivalent benefit from KV-cache and prefix reuse in llama.cpp, vLLM or Ollama, and the same ordering discipline applies.

### Gaps
- Current per-token prices and official Gemini 3.x cache minimums were not confirmed from a primary Google page.

## 4. Memory systems: short- and long-term, project memory, user-editable transparency

### Takeaway
The production pattern for 2025–26 is explicit, inspectable, scoped memory: files or records the user can view and edit, scoped per project. Hidden global profiles are out. For a writing app, the natural memory units are project artifacts — style guide, character bible, glossary, timeline, open threads/todo — stored as editable documents and injected (or retrieved) deterministically, plus a small, reviewable set of auto-extracted facts.

### Cited Findings
- MemGPT/Letta (Packer et al., 2023): an OS-inspired virtual context. The model pages information between a limited "main context" (core memory blocks) and external archival and recall storage using function calls. Letta is the productized successor. [prior knowledge] — [arXiv 2310.08560](https://arxiv.org/abs/2310.08560)
- Mem0 (2025), evaluated on LOCOMO:
  - 26% relative improvement over OpenAI's memory on LLM-as-a-Judge.
  - 91% lower p95 latency and more than 90% token savings compared with full-context.
  - The graph variant scored about 2% higher than base Mem0.

  These are the authors' own results. — [arXiv 2504.19413](https://arxiv.org/abs/2504.19413)
- Zep/Graphiti (2025): a temporally aware knowledge graph that tracks when facts were valid. This is relevant to story timelines where facts change over the course of the book. [prior knowledge] — [arXiv 2501.13956](https://arxiv.org/abs/2501.13956)
- Claude memory gives each project its own memory and lets users view and edit what Claude remembers through a summary view in settings. Users can ask Claude to forget specific things, and Incognito chats are not saved. It launched for Team and Enterprise (Sep 2025) and expanded to Pro and Max on Oct 23, 2025. — [Anthropic: Bringing memory to Claude](https://www.anthropic.com/news/memory); [Maginative](https://www.maginative.com/article/anthropic-brings-project-based-memory-to-paid-claude-users/)
- Anthropic's memory tool is a file directory the model can create, read, update and delete in, and it persists across sessions. — [Anthropic context management](https://www.anthropic.com/news/context-management)
- Claude Code's CLAUDE.md is preloaded project memory, an always-in-context file. — [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Forrester's characterization, via secondary press: Claude uses project-scoped memory with strict isolation instead of a global profile. ChatGPT has had memory since April 2024. — [MindStudio comparison](https://www.mindstudio.ai/blog/claude-memory-vs-chatgpt-memory-comparison)

### Inferences
Recommended memory layers for the app:
1. **Session (short-term):** conversation history, with compaction and tool-result clearing.
2. **Project memory (pinned, user-authored or approved):**
   - `style_guide.md` (voice, tense, POV, spelling variant).
   - `characters/*.md` (bible).
   - `glossary` (names, places, invented terms).
   - `timeline`.
   - `notes/todo` (Manus-style recitation for Work mode).

   Always loaded if small, about 2–5k tokens. Otherwise loaded as an index plus just-in-time reads.
3. **Auto-extracted facts (proposed, not silent):** the assistant proposes "Remember: Mara's eyes are grey (ch. 3, ¶12)?" The user accepts, edits or rejects. Each fact stores provenance (source location) and validity (Zep-style "as of chapter X").
4. **User-level preferences**, kept separate from project memory, with export, delete and incognito.

Further design points:
- Memory edits should flow through the same document model and undo stack as the manuscript, so memory is a visible, versioned artifact rather than hidden state.
- With local models, memory files never leave the machine. With BYOK cloud, show which memory files were sent.

### Gaps
- I found no public evaluation of memory systems on creative-writing continuity tasks, and no current side-by-side of ChatGPT's memory controls.

## 5. Model routing: local vs cloud, cost and latency, privacy-based routing

### Takeaway
Learned routers and cascades (RouteLLM and similar) can cut cost roughly 2x or more at equal quality by sending easy queries to cheap models. For a BYOK and local writing app, the dominant routing axes are privacy (user-marked sensitive documents stay local), task class (embedding, classification and short edits go local; long-context reasoning goes to the frontier), and availability or cost caps. Routing should be policy-first and user-visible, not a black box.

### Cited Findings
- RouteLLM (LMSYS/Berkeley, 2024): routers trained on human preference data with augmentation, choosing at inference time between a strong and a weak model. Costs fell "by over 2 times in certain cases" without compromising quality, and routers still worked when the model pair was swapped at test time. — [arXiv 2406.18665](https://arxiv.org/abs/2406.18665)
- Adaptive-RAG's small complexity classifier is a template for cheap routing decisions. [prior knowledge] — [arXiv 2403.14403](https://arxiv.org/abs/2403.14403)
- Self-Route-style escalation (cheap path first, escalate on self-judged insufficiency) is effectively a cascade. [prior knowledge] — [arXiv 2407.16833](https://arxiv.org/abs/2407.16833)
- OWASP LLM02:2025 (Sensitive Information Disclosure) and LLM10:2025 (Unbounded Consumption) are the relevant risk categories for routing policy and cost caps. — [OWASP GenAI Top 10 2025](https://genai.owasp.org/llm-top-10/)

### Inferences
Routing policy, in order of precedence:
1. **Hard privacy rules.** A document or project flagged "local only" never goes to cloud providers. Enforce this in the context assembler, not just the model picker, so retrieved snippets from a local-only doc can't leak into a cloud request.
2. **Capability.** Full-manuscript tasks need a model whose context window fits. If only a small local model qualifies, fall back to hierarchical summaries.
3. **Task class.**
   - Local: embeddings, the routing classifier, spellcheck-tier edits, and autocomplete.
   - User's chosen cloud model: deep revision, critique, and multi-hop agentic search.
4. **Budget and latency.** Apply a per-session spend cap (BYOK) and fall back on provider errors.

Further design points:
- Show the chosen model and why ("Local: document marked private"), with a per-request override.
- Keep embeddings local by default (small embedding models run fine on CPU). That avoids sending the whole manuscript to an embedding API, and the index stays valid across chat-provider switches.

### Gaps
- No 2026-specific router benchmarks were retrieved. RouteLLM is 2024 and its specific model pairs are superseded, though the method still applies.

## 6. MCP as of 2026, exposing a document library, and security (prompt injection, tool poisoning)

### Takeaway
MCP has had two notable revisions:
- **2025-11-25:** added URL-mode elicitation, tool calling inside sampling, experimental Tasks, icons, tool-naming guidance, and OAuth improvements.
- **2026-07-28:** made the protocol stateless, removing the initialize handshake and protocol-level sessions. It added Multi Round-Trip Requests (MRTR) for server-to-client interaction, promoted MCP Apps and Tasks to official extensions, and adopted a formal deprecation policy.

The security consensus is that prompt injection is unsolved. Mitigate it architecturally: avoid the "lethal trifecta," mark untrusted content as untrusted, permission write and egress tools, and require human confirmation for destructive actions.

### Cited Findings
**2025-11-25 changelog [fetched]**
- Changes include:
  - OIDC discovery and incremental scope consent.
  - Icons for tools, resources and prompts.
  - Tool-name guidance (SEP-986).
  - URL-mode elicitation (SEP-1036), and elicitation enums and defaults.
  - Tool calling in sampling through `tools` and `toolChoice` (SEP-1577).
  - Experimental tasks for durable polling (SEP-1686).
  - Client ID Metadata Documents for OAuth.
  - Input validation errors returned as tool execution errors so the model can self-correct (SEP-1303).
  - 403 responses for invalid Origin headers.
  - Updated security best practices.
  - JSON Schema 2020-12 as the default dialect.

  — [MCP changelog 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/changelog)

**2026-07-28 revision [fetched blog index, plus secondary sources]**
- Released July 28, 2026. "The new protocol revision goes stateless, removing the initialize handshake and the protocol-level session," so servers can sit behind round-robin load balancers. It introduces MRTR, makes MCP Apps (interactive UI returned by tools) an official extension, adds the Tasks extension for long-running work, and adopts a formal deprecation policy. — [MCP blog](https://blog.modelcontextprotocol.io/tags/mcp/)
- The release candidate was locked May 21, 2026, with a 10-week validation window. The C# SDK v2.0 implements it and is backward compatible. — [mcpservers.org blog](https://blog.mcpservers.org/posts/mcp-spec-2026-07-28); [Microsoft .NET blog](https://devblogs.microsoft.com/dotnet/announcing-v20-of-the-official-mcp-csharp-sdk/)
- Sources consulted did not say whether resources, prompts, roots, sampling or elicitation changed or were deprecated in 2026-07-28. Check the full spec.

**Security [fetched or search-verified]**
- OWASP Top 10 for LLM Applications 2025: LLM01 Prompt Injection, LLM02 Sensitive Information Disclosure, LLM03 Supply Chain, LLM04 Data and Model Poisoning, LLM05 Improper Output Handling, LLM06 Excessive Agency, LLM07 System Prompt Leakage, LLM08 Vector and Embedding Weaknesses, LLM09 Misinformation, LLM10 Unbounded Consumption. — [OWASP](https://genai.owasp.org/llm-top-10/)
- Simon Willison's "lethal trifecta" (Jun 2025): private data, exposure to untrusted content, and the ability to communicate externally. Combining all three enables exfiltration, so avoid the combination. — [simonwillison.net](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)
- "Design Patterns for Securing LLM Agents against Prompt Injections" (Jun 2025): 11 authors from IBM, Invariant Labs, ETH Zurich, Google, Microsoft and others propose six patterns that limit agent capability, including action-selector, plan-then-execute, map-reduce, dual LLM, code-then-execute (CaMeL-style) and context-minimization. The authors are pessimistic about general-purpose agents having reliable guarantees. — [arXiv 2506.08837](https://arxiv.org/html/2506.08837v1); [Willison review](https://simonwillison.net/2025/Jun/13/prompt-injection-design-patterns/)
- Spotlighting (Microsoft, 2024): transforms input to mark where it came from, using delimiting, datamarking or encoding. On GPT-family models it cut attack success from above 50% to below 2%, with little effect on task performance. These are older models; the result is a mitigation, not a guarantee. — [arXiv 2403.14720](https://arxiv.org/abs/2403.14720)
- Tool poisoning, where malicious instructions sit in MCP tool descriptions, is a known MCP threat class (Invariant Labs, 2025). It is covered by the spec's security best practices page. I did not fetch it this session. — [MCP Security Best Practices](https://modelcontextprotocol.io/specification/draft/basic/security_best_practices)

### Inferences
**Exposing the library through the app's MCP server**
- Resources: chapters and scenes as resource URIs (e.g., `manuscript://project/ch/3`), plus project memory files as resources.
- Read tools, small and non-overlapping: `search_text`, `semantic_search`, `get_outline`, `read_range`, `get_entity`.
- Write tools, kept separate and named distinctly (e.g., `propose_edit` returns a diff the user accepts). Avoid direct mutation unless the user grants it per session.
- Prompts: reusable prompts for "line edit", "continuity check", and similar tasks.
- Use elicitation (form or URL mode) for confirmation flows.
- Honor roots or scopes so external agents see only the projects the user shares, and respect "local-only" flags for external agents too.
- Return validation errors as tool errors (SEP-1303) so external agents can self-correct.

**Treat imported content as untrusted**
- Pasted web research, imported .docx files, and comments from collaborators are untrusted content. Wrap retrieved chunks in spotlighting delimiters with provenance, and instruct the model that this text is data.
- In Work mode, avoid the trifecta: when untrusted content is in context, disable egress tools (web fetch, email, MCP tools to third-party servers), or require confirmation.

**Third-party MCP servers the assistant consumes**
- Show tool descriptions to the user, pin tool versions or hashes to detect "rug-pull" description changes, and run them with least privilege.

### Gaps
- I did not obtain an authoritative list of 2026-07-28 feature-level changes to resources, prompts, sampling, roots or elicitation. I also did not verify OWASP's Agentic Top 10 (2026), which exists only as a navigation link on the OWASP page.

## 7. Uncertainty, abstention, provenance UI, and evaluation/observability

### Takeaway
Users stay in control when the app shows what the AI saw, lets them pin or exclude sources, cites locations for factual claims about the manuscript, and lets the model say "not found in your manuscript." Production quality comes from tracing every context assembly (route, retrieved items, tokens, cache hits, model) and running offline evals on a fixed set of manuscript Q&A and edit tasks, fed by user feedback.

### Cited Findings
- In Chroma's tests, Claude models abstained most and hallucinated least, and GPT models hallucinated most. Abstention behavior differs by provider and should be tested per model. — [Chroma](https://www.trychroma.com/research/context-rot)
- CRAG's retrieval evaluator (Correct, Incorrect, Ambiguous) and Self-RAG's support and relevance critique tokens give explicit signals for whether to answer or abstain. [prior knowledge] — [CRAG](https://arxiv.org/abs/2401.15884); [Self-RAG](https://arxiv.org/abs/2310.11511)
- OWASP LLM09:2025 Misinformation names overreliance and hallucination as a top risk. — [OWASP](https://genai.owasp.org/llm-top-10/)
- Anthropic's context-management results (39%, 29%, 84%) came from internal evals designed per workflow, an example of task-specific evals driving context design. — [Anthropic](https://www.anthropic.com/news/context-management)
- Prompt-cache telemetry fields (`cache_creation_input_tokens`, `cache_read_input_tokens`) are available for observability. — [Claude docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

### Inferences
**"What the AI saw" panel**, per request:
- Route chosen and why.
- Model and provider (local or cloud).
- List of context items, each with source location, token count, and pin or exclude toggles: selection, memory files, retrieved snippets, full-document flag.
- Cache hit percentage and estimated cost.

Pinned items are always included; excluded items are filtered from all future retrieval.

**Abstention rules**
- For factual questions about the manuscript (R2 and R3), require cited spans (chapter and paragraph). If retrieval evidence is graded insufficient after one escalation, answer "I couldn't find this in the manuscript", show the closest matches, and do not guess.
- Ask the user, through elicitation or a UI prompt, when a question is ambiguous ("Which Tom?").

**Evaluation**
1. Golden set per sample manuscript: entity and continuity questions with known answers, including near-duplicate distractors such as earlier drafts. Edit tasks are graded for style-guide adherence.
2. Measure retrieval recall@k for each route, answer accuracy, abstention correctness, tokens and cost, and latency, separately for each provider and local model.
3. Gate releases on regressions.
4. Log thumbs-up/down and accept/reject on proposed edits, linked to the trace, so failing traces join the eval set.
5. Keep traces local by default to match the app's privacy stance.

### Gaps
- I found no 2026 primary-source data on calibrated-confidence methods for frontier models, such as verbalized-confidence accuracy. No specific tracing vendors (LangSmith, Langfuse, Phoenix) were researched this session.
