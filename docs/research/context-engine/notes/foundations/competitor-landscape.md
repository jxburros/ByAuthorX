# Competitive Landscape: Context, Retrieval, Source Grounding and Citation in Writing Tools (as of Oct 2026)

Research date: 2026-10-16. About 16 searches and fetches. Primary sources (Google Workspace Updates blog, Anthropic support, Novelcrafter help, NovelAI docs, Microsoft roadmap) were used where found; many 2026 details come only from secondary guides and are flagged. Products I could not research in this pass are listed under Gaps, not described from memory.

## Microsoft Word + Microsoft 365 Copilot

### Takeaway
Word Copilot grounds drafts on explicitly referenced items (up to 20 files, emails or meetings, plus folders) and on the Microsoft Graph. Citation quality is the weak spot: as of early October 2026, citations in Word Copilot were opt-in plain text, and the "deep citations" that link to a passage had slipped to a November 2026 GA. The Researcher agent is Microsoft's strongest cited-report feature.

### Cited Findings
- "Draft with Copilot" in Word can reference up to 20 items (files, emails, meetings), whole folders, and documents up to 1.5 million words or 3,000 pages. Folder references use the 10 most recent files. GA was expected to finish by early December 2025. These are 2025-era figures and may have changed — [ElevenForum repost of Microsoft Word blog](https://elevenforum.com/t/expanding-reference-capabilities-with-microsoft-365-copilot-in-word.35466); [handsontek admin summary](https://m365admin.handsontek.net/?p=13476)
- Earlier limits were much tighter. In Jan 2024, Office365ITPros warned against feeding large reference documents to Copilot for Word (outdated) — [Office365ITPros](https://office365itpros.com/2024/01/02/copilot-for-word-reference/)
- Licensing: from April 15, 2026, in-app Copilot in Word, Excel, PowerPoint and OneNote is limited to users with a Microsoft 365 Copilot license — [UAB IT notice](https://www.dpo.uab.edu/it/news/item/update-to-microsoft-copilot-availability-begins-april-15)
- Citation behavior in Word as of early Oct 2026:
  - Citations are added only when the user asks.
  - They appear as plain text with links, not Word footnotes or APA/MLA.
  - Auto-footnote citations were promised for September 2026 but had not appeared on the author's test machines (including Insiders with Copilot Frontier).
  - Deep citations moved from a June GA to November 2026, and from a May preview to October 2026.
  — [Office Watch, 2026](https://office-watch.com/2026/copilot-word-citations-image-editing/)
- Microsoft roadmap item 523223, "deep citations", links to "the relevant part of the referenced source". It covers Word and PowerPoint files first, then Meetings, Web and PDF. Status was "In development". Roadmap dates conflict across reposts — [Microsoft roadmap](https://www.microsoft.com/releasecommunications/api/v2/m365/523223); [handsontek repost listing Feb 2026 preview](https://m365admin.handsontek.net/?p=18425)
- Office Watch's own caveats on these citations:
  - A work citation can point to a superseded draft.
  - A cited source may not say what Copilot claims.
  — [Office Watch](https://office-watch.com/2026/copilot-word-citations-image-editing/)
- Researcher agent:
  - It runs multi-step research over work data (Teams, email, SharePoint can each be toggled) and the web.
  - It produces an editable, cited report that can be exported to Word or PowerPoint.
  - Reportedly it now uses Anthropic Claude alongside OpenAI models, with "Critique" and "Council" features meant to reduce errors.
  — [hubsite365](https://www.hubsite365.com/en-ww/crm-pages/researcher-agent-in-microsoft-365-copilot.htm); [hubsite365 (Critique/Council)](https://www.hubsite365.com/en-ww/crm-pages/researcher-agent-in-microsoft-365-copilot-generate-structured-source-cited-reports.htm) (secondary source)

### Inferences
- Microsoft offers the most context (Graph plus 20 items plus folders) but lags on citation granularity and placement in the document. Passage-level citation, emitted as real footnotes in the document, is still not shipped in Word. A word processor that does this today would beat Word.
- Selecting folders by the "10 most recent files" is a heuristic, not relevance-based. That is a likely source of missed context.

### Gaps
- No official Microsoft Learn page for 2026 Word Copilot limits was found. Whether the 20-item and 1.5M-word limits still hold is unconfirmed.
- Not confirmed whether deep citations actually shipped in Oct or Nov 2026.
- Not researched: details of the Microsoft Graph semantic index, and Word's legacy Editor/Researcher pane.

## Google Docs + Gemini, and NotebookLM (renamed Gemini Notebook)

### Takeaway
Google has merged NotebookLM into Gemini. Since September 23, 2026, Docs users can type "@" to ground Gemini drafts on a whole notebook, with inline citations. NotebookLM remains the reference UX for source-pinned grounding: the user curates the corpus, gets passage-level inline citations, and since May 2026 Drive sources stay synced.

### Cited Findings
- Sep 23, 2026 Workspace update: in Google Docs, typing "@" in the Gemini side panel or bottom bar references an existing Gemini Notebook.
  - Gemini then grounds output in that notebook's sources, with inline citations "so users can verify facts".
  - Plans: Business Standard/Plus, Enterprise Standard/Plus, Education Plus, Google AI Pro/Ultra.
  - It is on by default if Gemini in Drive is enabled.
  — [Google Workspace Updates](https://workspaceupdates.googleblog.com/2026/09/ground-ai-prompts-in-google-docs-on-existing-sources-from-Gemini-Notebook.html)
- Name and logo rollout of NotebookLM to "Gemini Notebook" reportedly began July 16, 2026. This is reported secondhand and was not confirmed on the Workspace blog itself — [DEV Community](https://dev.to/alifar/google-renames-notebooklm-to-gemini-notebook-as-workspace-integration-expands-5811)
- Jan 20, 2026: NotebookLM notebooks can be added as a source in the Gemini app — [Google Workspace Updates](https://workspaceupdates.googleblog.com/2026/01/take-notebooks-further-notebooklm-gemini.html)
- May 2026: NotebookLM syncs Drive sources automatically.
  - Edits to Docs, Sheets and Slides flow into the notebook.
  - Deletions and permissions are respected.
  — [Google Workspace Updates](https://workspaceupdates.googleblog.com/2026/05/keep-your-sources-up-to-date-with-automatic-Drive-syncing-in-NotebookLM.html)
- March 2026 beta (Google AI Pro/Ultra): a Docs overview summarizes relevant information from the user's files, with citations linking back to the documents. Drive features were geo-limited at launch — [IT Brief](https://itbrief.com.au/story/google-adds-powerful-new-gemini-tools-to-workspace-apps)
- Practitioner caveat: Gemini "can cite a real passage that doesn't support the sentence it's attached to" — [fast.io guide](https://fast.io/resources/gemini-google-docs-integration.md)
- NotebookLM source limits (secondary sources, which conflict):
  - Free: 50 sources per notebook. Pro: about 300. Ultra: about 500 to 600.
  - Per-source cap: about 500k words or 200 MB.
  - Google labels quotas as subject to change.
  — [superlore guide](https://superlore.ai/blog/notebooklm-source-limits-per-notebook-2026); [freeacademy](https://freeacademy.ai/lessons/notebooklm-setup-sources)
- April 2026 updates included automatic source labeling after 5 or more sources, bulk sharing, and flashcards — [pasqualepillitteri.it](https://pasqualepillitteri.it/en/news/1391/notebooklm-april-2026-update-auto-label-flashcards)

### Inferences
- Google's pattern is a curated, explicitly pinned corpus (the notebook) that can be attached to the editor with "@", plus live sync. A word processor should adopt this: a per-document or per-project "source set" that stays in sync with the original files.
- Neither Google nor Microsoft documents claim-level verification, that is, checking that the cited passage actually entails the sentence. Both rely on the user to click through.

### Gaps
- Official current NotebookLM limits and the Audio Overview and Deep Research specifics were not retrieved in this pass.
- Unconfirmed whether a plain "@file" Drive mention (without a notebook) grounds Gemini in Docs.

## Notion AI (and other all-in-one workspaces)

### Takeaway
Notion positions AI as enterprise search and Q&A across the workspace plus read-only connectors (Slack, Drive, GitHub, Jira, and others), with citations. Most 2026 specifics come from third parties.

### Cited Findings
- Notion's own wording: "every answer includes verified content and citations, using only the sources you choose" — [Notion Enterprise Search](https://www.notion.com/product/enterprise-search)
- Connectors cover Slack, Google Drive, GitHub, Linear, Jira and Notion Mail. A review puts the total at about 14 third-party tools. Connectors are read-only — [Slite guide](https://slite.com/ja/learn/how-to-use-notion-ai); [beginnersinai review](https://beginnersinai.org/notion-ai-review/)
- Custom Agents arrived in Notion 3.3 (Feb 24, 2026); they are persistent agents that run on schedules or triggers. The same review says they stopped being free on May 4, 2026 — [beginnersinai review](https://beginnersinai.org/notion-ai-review/)
- Slite (a competitor) claims Research Mode cites every claim and that "always cites sources" behavior arrived in March 2026. This is unconfirmed — [Slite](https://slite.com/learn/cómo-usar-notion-ai)

### Inferences
- In Notion, context is mostly selected automatically, with optional source scoping. "Verified" in Notion's marketing does not appear to mean that each claim is automatically checked against its source.

### Gaps
- Not researched in this pass: Coda AI, Craft, Apple Pages with Apple Intelligence (Writing Tools are known to be on-device or Private Cloud Compute, but no source was retrieved).

## Long-form and fiction tools

### Takeaway
Fiction tools solved "relevant context injection" with structured story databases. Two approaches stand out:
- **Novelcrafter's Codex** auto-detects entity names (and aliases) in the prompt and nearby text, then pulls in nested child entries.
- **NovelAI's Lorebook** uses keyword or regex triggers with a configurable scan depth.

Both are entity-triggered, deterministic and transparent. Neither is semantic RAG with citations. The main complaint is drift and inconsistency in long manuscripts.

### Cited Findings
- Novelcrafter gathers Codex context in this order:
  1. Mentions in the beat or chat message and the preceding text
  2. Manual scene references
  3. The POV character
  4. Global entries
  5. Child entries
  - Empty entries are dropped.
  - Categories are not sent to the AI.
  - Entry size is not limited, but the docs advise shorter entries so the context window isn't "used up".
  — [Novelcrafter help: Codex context in prompting](https://www.novelcrafter.com/help/faq/ai-and-prompting/codex-context-in-prompting)
- In Novelcrafter chat, naming a character automatically adds their Codex entry, and the user can also attach entries manually. Scene summaries have a "+ Codex" button to pin entries to every beat — [Novelcrafter course: Codex and chat](https://www.novelcrafter.com/courses/ultimate-beginners-guide/codex-and-chat)
- Kindlepreneur review: the strongest feature is that "Novelcrafter detects references to Codex entries in your prompt [and] automatically pull[s] that information" — [Kindlepreneur](https://kindlepreneur.com/novelcrafter-review/)
- NovelAI Lorebook:
  - Activation keys are case-insensitive. "/regex/" keys are supported, and "&" requires two keys to match together.
  - "Always On" entries are always inserted.
  - The search range goes up to 10,000 characters.
  — [NovelAI docs: Lorebook](https://docs.novelai.net/en/text/lorebook)
- Sudowrite Story Bible complaints:
  - Users report that rewrites change names and details defined in the Story Bible, and that the rewrite command box has a character limit — [Sudowrite feedback board](https://feedback.sudowrite.com/p/better-editing-rewriting-drafts-scenes-and-chapter-splitting-following-manuscript)
  - Context loss in manuscripts over 80K words (secondhand, citing a Jan 2026 Reddit thread) — [aitooltier](https://aitooltier.com/tools/sudowrite)
  - "Inconsistent AI memory" (low-confidence aggregator) — [marlvel.ai](https://marlvel.ai/apps/sudowrite-ai-novel-writing)
- Story Bible uses fixed fields with no custom fields or nested relationships (reviewer opinion). Lorebook entries linking 5 to 6 or more characters reportedly dilute each other (anecdotal) — [fast.io creative writing roundup](https://fast.io/resources/best-ai-for-creative-writing-2026/)

### Inferences
- For a word processor, the winning fiction pattern is a hybrid:
  - deterministic entity detection with aliases (Codex-style)
  - semantic retrieval over the manuscript itself, for earlier scenes where X happened
  - a visible "context inspector" showing exactly what was injected

  No fiction tool found here retrieves from the user's own prior chapters by meaning or cites which chapter a fact came from.

### Gaps
- Not researched: Scrivener (no native AI found), Plottr, Dabble, Atticus, Ulysses.

## Research and academic assistants, and chat-assistant project knowledge

### Takeaway
Academic tools ground answers in scholarly indexes and cite at the paper level. Scite adds citation-statement classification (supporting, contrasting, mentioning), which is the closest thing to claim-level evidence. Fabricated or out-of-index citations still occur and vary by tool. General assistants use file-capped projects; Claude switches to RAG when a project's knowledge nears the context limit.

### Cited Findings
- Scite extracts each citing sentence from full text and classifies it as supporting, contrasting or mentioning. Its Assistant is built on this database and is meant to minimize hallucination. Scite does not judge methodological soundness, and coverage varies by publisher — [Clemson LibGuide](https://clemson.libguides.com/scite/use); [Scite help](https://help.scite.ai/zh/article/what-is-scite-1gfjuck); [Educators Technology review, Sep 2026](https://www.educatorstechnology.com/2026/09/scite-review.html)
- Informal comparison (anecdotal, secondary):
  - Consensus was the most reliable, with verifiable quotes.
  - Elicit was weakest, with invented citations and unsupported claims.
  - Scite was mixed, sometimes citing papers outside its database.
  — [CO/AI](https://getcoai.com/?p=73363)
- Library guidance: these RAG tools pull "real citations from their respective pools", but human or librarian review is still needed — [HKUST Library](https://library.hkust.edu.hk/sc/trust-ai-lit-rev/); [UBC Wiki: Consensus](https://wiki.ubc.ca/Consensus)
- Claude Projects:
  - When project knowledge nears the context limit, RAG mode turns on automatically. It expands capacity "by up to 10x" and uses a project knowledge search tool — [Anthropic support](https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects)
  - A GitHub issue reports RAG triggering at about 13 files or 2% of capacity, contrary to the docs — [GitHub issue #25759](https://github.com/anthropics/claude-code/issues/25759)
- ChatGPT Projects file caps (third-party, checked Oct 8, 2026):
  - 5 files on Free, 25 on Plus/Go, 40 on Pro/Business/Enterprise.
  - About 512 MB per file, 10 files per upload batch.
  - Forum users report the UI enforcing lower caps than documented.
  — [fast.io](https://fast.io/resources/chatgpt-projects-file-limit/); [OpenAI community](https://community.openai.com/t/chatgpt-projects-20-file-limit/1064482)

### Inferences
- File-count caps (5 to 40) and opaque switching to RAG are real pain points. A writing app with an unlimited local index and a visible retrieval mode beats both.
- Scite-style support labels (supports, contradicts, only mentions) are a model for claim verification inside a word processor.

### Gaps
- Not researched in this pass: SciSpace, Zotero 7 plus AI plugins, Paperpal, Jenni AI, Writefull, Overleaf AI, Perplexity Pages/Spaces, ChatGPT Canvas, Gemini Gems, Elicit's current features, and the formal Jarry 2025 study.

## PKM and local-first AI

### Takeaway
Obsidian plugins show that fully local embeddings plus vault Q&A with cited notes work in practice. The pain points are index portability and per-device re-indexing.

### Cited Findings
- Obsidian Copilot's Vault QA chats over the whole vault from a local vector store and returns cited sources. It can run offline with Ollama — [obsidian-copilot GitHub](https://github.com/MartletH/obsidian-copilot)
- Smart Connections:
  - It indexes each note with a local embedding model and needs no API key.
  - The index lives in `.smart-env/` and must be regenerated per device when syncing via iCloud or Git.
  - Embedding generation runs on desktop only.
  — [insiderllm guide](https://insiderllm.com/guides/obsidian-local-llm-guide/); [promptquorum](https://www.promptquorum.com/power-local-llm/local-llm-with-obsidian-2026)
- Common local embedding models are nomic-embed-text (768-dim) and mxbai-embed-large (1024-dim) — [bestllmfor guide](https://bestllmfor.com/guide/obsidian-llm-local-ollama/)

### Inferences
- A local, private index is achievable with off-the-shelf embedding models. It is a strong differentiator against cloud-only Copilot, Gemini and Notion for privacy-sensitive writers.

### Gaps
- Not researched in this pass: Mem, Reflect, Logseq, Tana, Heptabase, Microsoft Recall, Apple Intelligence on-device details, LM Studio, AnythingLLM, GPT4All LocalDocs, Khoj. No claims are made about them here.

## Synthesis: table stakes, differentiators, and gaps

### Takeaway
By late 2026, table stakes are:
- explicit source pinning (@-mention of files or notebooks)
- workspace-wide automatic retrieval
- document-level clickable citations

The best tools add:
- a curated, live-synced corpus (NotebookLM / Gemini Notebook)
- entity-triggered context injection (Novelcrafter)
- evidence classification (Scite)

No tool found verifies that each cited passage actually supports its sentence, shows a transparent context inspector, or emits real bibliographic footnotes from grounded drafts.

### Cited Findings
- Even the big suites rely on the user to verify citations:
  - Gemini can attach real but non-supporting passages — [fast.io](https://fast.io/resources/gemini-google-docs-integration.md)
  - Word citations are plain text and can point to stale drafts — [Office Watch](https://office-watch.com/2026/copilot-word-citations-image-editing/)
- Passage-level "deep citations" were still unshipped in Word as of early Oct 2026 — [Office Watch](https://office-watch.com/2026/copilot-word-citations-image-editing/)
- Explicit corpora with live sync now exist (NotebookLM Drive sync, May 2026) — [Google Workspace Updates](https://workspaceupdates.googleblog.com/2026/05/keep-your-sources-up-to-date-with-automatic-Drive-syncing-in-NotebookLM.html)
- Deterministic, transparent context assembly (a documented order) exists in fiction tools — [Novelcrafter help](https://www.novelcrafter.com/help/faq/ai-and-prompting/codex-context-in-prompting)

### Inferences
Opportunities none of the researched tools fill well:
1. **Claim-level verification.** After generation, check entailment between each sentence and its cited passage. Flag sentences as supported, partial or unsupported, Scite-style.
2. **Passage-level anchors.** Make each citation a real footnote or bibliography entry, in the user's citation style, that jumps to the exact span. Version-pin it to avoid the "stale draft" problem.
3. **Context inspector.** Show exactly which sources and passages were injected and why (mention match, semantic match, pinned). Let the user pin or exclude them inline. This combines Novelcrafter's transparency with NotebookLM's curation.
4. **Hybrid retrieval over the document itself.** For long manuscripts, combine entity and alias triggers with semantic retrieval and recency. This targets the 80K-word drift complaints.
5. **Local-first, unlimited index.** Avoid the 5 to 40 file caps and opaque RAG switching. Keep private data on-device, with optional cloud models.
6. **Freshness.** Auto-sync sources and warn when a cited source changed after citation.

### Gaps
- Many 2026 numbers (NotebookLM tiers, ChatGPT file caps, Word reference limits) come from secondary sources and should be rechecked against vendor docs before publication.
- About half of the requested products were not covered in this pass (see section Gaps above). A follow-up pass is recommended for Apple Intelligence/Pages, Microsoft Recall, Perplexity, Zotero/Jenni/Paperpal, and Mem/Tana/Heptabase.
