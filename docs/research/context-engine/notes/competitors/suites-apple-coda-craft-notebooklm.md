# Productivity suites and editors: AI context, retrieval, grounding and citation (Apple Pages/Apple Intelligence, Coda, Craft, Microsoft Graph semantic index, NotebookLM / Gemini Notebook), as of October 2026

Research date: 2026-10-16. Budget: about 16 tool calls. Coverage is uneven. Microsoft Learn and Google primary pages were fetched in full. Apple, Coda and Craft coverage relies mostly on secondary press or search snippets: help.coda.io returned 403, and no Apple newsroom page from 2026 was found. Items marked "(prior knowledge, unverified this session)" come from the researcher's training data, not from a source fetched here. Treat them as leads, not citations.

## Q1: Per product: context selection, retrieval, citation granularity, verification, privacy model, limits, 2025-2026 changes, complaints

### Takeaway
The products split into two camps.
- **Automatic, permission-trimmed retrieval:** the Microsoft Graph semantic index, plus Coda Brain and Craft's assistant over the whole space or workspace.
- **Explicit, curated source sets:** NotebookLM / Gemini Notebook, where the notebook is the context boundary.

Apple Writing Tools barely does retrieval. It works on the current selection. Apple's "personal context" is a Siri on-device knowledge graph, and it shipped in 2026 on Gemini-based Apple Foundation Models. Of these products, only NotebookLM offers passage-level inline citations that you can click through. None of them documents a separate claim-verification step.

### Cited Findings

**NotebookLM, now "Gemini Notebook"**
- **Rename confirmed by Google (primary source).** On July 16, 2026 Google announced that "NotebookLM is now Gemini Notebook". It is "the same standalone product", and it started as Project Tailwind at I/O 2023. Google says it has more than 30M users and over 600,000 organizations. — [Google blog, 2026-07-16](https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/)
- **New in the rename announcement:**
  - Each notebook gets a "secure cloud computer", so Gemini Notebook can write and run code for data analysis grounded in your sources. This is available now for AI Ultra users and Workspace customers with AI Ultra Access or AI Expanded Access, and rolls out to all Pro web users "over the coming weeks".
  - Notebooks can be created and accessed inside the Gemini app, with "full cross-app syncing".
  - Notebooks are coming "soon" to AI Mode in Search. — [Google blog](https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/)
- **Continuity.** The URL stays at notebooklm.google and the same team runs it. Existing notebooks and shared links redirect, and the new name and logo roll out over several weeks. — [Google Workspace Updates blog, July 2026](https://workspaceupdates.googleblog.com/2026/07/notebooklm-now-gemini-notebook.html); corroborated by [Android Authority](https://www.androidauthority.com/gemini-notebook-3688235/) and [Stony Brook IT](https://it.stonybrook.edu/articles/notebooklm-now-gemini-notebook) (secondary).
- **Per-source limits (official).** 500,000 words per source, or up to 200MB for local uploads. "There's no page limit." Workspace, Education and Cloud customers can get higher limits, with no figures given. — [NotebookLM Help 16269187](https://support.google.com/notebooklm/answer/16269187)
- **Per-plan limits (official table).**

  | Plan | Notebooks per user | Sources per notebook |
  |---|---|---|
  | Standard/Free | 100 | 50 |
  | Google AI Plus | 200 | 100 |
  | Google AI Pro | 500 | 300 |
  | Ultra 20TB | 500 | 500 |
  | Ultra 30TB | 500 | 600 |

  Also on the page:
  - Usage limits are "Subject to Change". Daily quotas reset after 24h and monthly quotas after 30 days.
  - Some users get a report, flashcards, infographic, slide deck, Audio Overview or Video Overview generated automatically when they first add sources. These don't count toward limits.
  - Enterprise (via Google Cloud) gets "5X or more" artifacts.
  - A watermark is applied automatically in India, South Korea and Vietnam.

  — [NotebookLM Help 16213268 "Upgrade NotebookLM"](https://support.google.com/notebooklm/answer/16213268?hl=en)
- **Usage-limit claims (secondary, conflicting).**
  - Since September 2, 2026, chat and Studio usage is compute-based and refreshes every 5 hours. — [FreeAcademy (secondary)](https://freeacademy.ai/blog/notebooklm-free-vs-plus-2026)
  - An older model of fixed daily caps: about 500 chats/day on Pro and 5,000 on Ultra; Deep Research 10/month Free, 20/day Pro, 200/day Ultra. — [Elephas (secondary)](https://elephas.app/blog/notebooklm-source-limits.md)
  - Video Overviews are Ultra-only. — [FreeAcademy (secondary)](https://freeacademy.ai/blog/notebooklm-free-vs-plus-2026). This conflicts with the official help page, which lists Video Overviews among auto-generated items for "some users" without restricting them to Ultra.
- **Grounding quality evidence.**
  - Academic test: NotebookLM hallucinated 13% of the time versus 40% for ChatGPT and Gemini on document-based queries. The authors attribute this to RAG with explicit citations. — [arXiv 2509.25498](https://arxiv.org/pdf/2509.25498)
  - A literary-analysis paper found it can wrongly assert that relevant examples are absent from a text when they are present, and eventually fabricate examples after repeated queries. — [arXiv 2412.18270](https://arxiv.org/pdf/2412.18270)
- **Complaints (2026).** A blog post on the Gemini 3.1 Pro transition reports "source blindness": the model says "not found" when the material is in the sources. It also reports "consistency repair", where the model fills gaps with knowledge from training. — [ud.hk blog, 2026-07-07 (secondary, sourcing unclear)](https://ud.hk/en/blogs/insight/article/2026-07-07-notebooklm-no-hallucination)
- **Verification relies on the user.** Guides say clickable citations are the main check, but warn that a citation does not prove the claim is correct. Users should open the cited passage. — [st-hakky (secondary)](https://book.st-hakky.com/en/service/how-to-use-notebooklm-and-information-about-hallucination)

**Microsoft 365 Copilot / Microsoft Graph semantic index**
- **Mechanism.** The index is "an advanced lexical and semantic index" built from Microsoft Graph, using vectorized indices and searching "billions of vectors". This is combined with keyword matching plus "personalization and social matching", meaning ranking by signals from the user and their close network. Copilot uses it to append grounding to the prompt (step 2) and again for post-processing the LLM response (step 5). — [Microsoft Learn, Semantic indexing for Microsoft Copilot (ms.date 2026-04-23, updated 2026-08-18)](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Two index levels.** A tenant-level index built from text-based SharePoint Online files, and a user-level index of the user's working set (mailbox, documents they touch or share). Supported types: Word, PowerPoint, PDF, aspx and OneNote at both levels. The mailbox is user-level only. Delegated, shared and archived mailboxes are not supported, and neither is archived SharePoint. Copilot connector data is tenant-level. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Permission trimming.** Results surface "only if the user already has access", controlled by role-based access control. The site must remain searchable. Sharing a file with an org-wide link does not put it in the tenant index; it enters a user's index only when that user opens the link. Sensitivity labels are included in search trimming. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Admin controls.** Indexing "can't be disabled". Admins can exclude whole SharePoint sites by setting "Allow this site to appear in search results" to No, which removes the site from both Search and the semantic index at once. They can also use Purview DLP. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Freshness.** User mailbox content is indexed in near real time. New SharePoint docs accessible to 2+ users are indexed daily. Updates to already-indexed docs are indexed "immediately". — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Size.** "Files up to 512 MB are now supported for PDF, PPTX, and DOCX." — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Recent change: explicit scoping.** A user can attach a SharePoint library or folder (or paste its URL), and Copilot then constrains and ranks results within that scope using the library's column metadata. This is web only. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Privacy.** Index data stays in the tenant. The user index is stored where the user's mailbox is; the tenant index is stored in the SharePoint region, with EUDB respected. BYOK is supported. Indexed data isn't used to train foundation LLMs. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Roadmap RM503570.** Moves permission sync from full crawl only to incremental crawls. Target GA July CY2026, status "in development" as of 2026-02-12, shipping unconfirmed. This implies permission changes previously propagated with lag. — [Message Center mirror (secondary)](https://mc.merill.net/message/RM503570)
- **MC1323266.** Copilot answers from Power BI reports and semantic models for Frontier customers. It respects permissions, is on by default and can be disabled by admins. Rollout is June 2026; the August revision lists June 17 for Frontier Private and June 22 for Frontier Public. — [MC mirror](https://mc.merill.net/message/MC1323266)
- **Licensing (unverified).** A partner claim says that from April 15, 2026, full Copilot Chat integration in Word, Excel, PowerPoint and OneNote requires an M365 Copilot license. — [Orion Networks (secondary, unverified)](https://www.orionnetworks.net/microsoft-365-copilot-updates-whats-new-and-how-your-team-benefits/)

**Apple Pages + Apple Intelligence**
- **Original model (2024).** Writing Tools are system-wide and handle rewrite, proofread and summarize in Mail, Notes, Pages and others. Private Cloud Compute shifts work from the device to server models on Apple silicon. — [Apple Newsroom, June 2024](https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac)
- **January 2026: Gemini as the base model.** Apple and Google announced a multi-year collaboration in which the next generation of Apple Foundation Models will be based on Google's Gemini models and cloud technology. — [MacRumors, 2026-04-22](https://www.macrumors.com/2026/04/22/google-gemini-powered-siri-2026); [ACS Information Age](https://ia.acs.org.au/article/2026/apple-reveals-the-ai-behind-siri-s-big-2026-upgrade.html) (secondary)
- **Personal context.** The personalized Siri was delayed on 2025-03-07. Its first phase came with iOS 26.4, with fuller conversational Siri in iOS 27 (fall 2026). — [9to5Mac, 2026-03-20](https://9to5mac.com/2026/03/20/apples-gemini-powered-siri-upgrade-could-still-arrive-this-month/); [MacRumors](https://www.macrumors.com/2026/04/22/google-gemini-powered-siri-2026). One secondary source describes it as Siri building an on-device "graph of knowledge about you" — [smartprix (secondary)](https://us.smartprix.com/bytes/siri-powered-by-google-apples-most-startling-ai-partnership-yet/)
- **WWDC June 8, 2026.** Apple announced an upgraded Siri and new writing and productivity AI features. — [Digital Trends](https://digitaltrends.com/computing/wwdc-2026-ios-27-siri-ai-apple-intelligence-upgrades-and-everything-else-apple-announced). Pre-WWDC Bloomberg-sourced leaks described a Grammarly-like grammar checker showing original text beside suggested edits with accept/reject, plus "Write With Siri" and "Help Me Write". — [iLounge](https://www.ilounge.com/news/apple/apple-testing-out-grammar-checker-and-ai-writing-tools-for-upcoming-ios-27); [Business Today](https://www.businesstoday.in/amp/technology/artificial-intelligence/story/apples-ios-27-may-bring-ai-writing-tools-smart-wallpapers-and-more-532271-2026-05-19). BGR says Writing Tools were "revamped" but gives no specifics. — [BGR](https://bgr.com/2194403/apple-intelligence-new-features-ios-27/)
- **Regional availability.** One developer source says most iOS 27 features run on-device via Foundation Models on iPhone 15 Pro or later, and that Siri AI is initially unavailable in the EU and China. — [framesixty (low-reliability secondary)](https://framesixty.com/develop-app-ios/)

**Coda (Coda AI / Coda Brain), now part of Superhuman**
- **Corporate context.** Grammarly acquired Coda in December 2024 and made Coda co-founder Shishir Mehrotra CEO. On October 29, 2025 Grammarly rebranded as Superhuman, with a four-product suite: Coda, Grammarly, Superhuman Mail and a new "Superhuman Go" assistant. The suite includes an agent marketplace built on Coda's Pack platform. — [Superhuman Help](https://help.superhuman.com/hc/en-us/articles/46210093285773-What-s-changing-Grammarly-becomes-Superhuman); [Constellation Research](https://www.constellationr.com/insights/news/grammarly-gets-more-interesting-coda-superhuman-deals)
- **Coda Brain's role.** It surfaces company knowledge from connected systems inside docs. — [Constellation Research](https://www.constellationr.com/insights/news/grammarly-gets-more-interesting-coda-superhuman-deals)
- **Product page now redirects.** coda.io/product/coda-brain now 301-redirects to a help.coda.io article (40685056076685), which returned 403 to this session. This suggests Coda Brain was folded into a help-center explanation, possibly a transition to Superhuman Go, but the content is unverified. — [redirect observed on coda.io/product/coda-brain](https://coda.io/product/coda-brain)

**Craft**
- **Local models (February 2025).** Craft added experimental local models as the AI assistant engine on iPhone, iPad and Mac:
  - DeepSeek R1 1.5B (under 1GB), with a 7B (about 4GB) promised.
  - Llama 1B and 3B (about 700MB and 2GB).
  - They run fully offline once downloaded, and cloud models such as ChatGPT remain available. — [9to5Mac, 2025-02-05](https://9to5mac.com/2025/02/05/crafts-latest-update-could-change-how-you-use-ai-on-your-iphone/)
- **Assistant scope.** The assistant searches notes across your space, and rewrites and summarizes docs. — [App Store listing](https://apps.apple.com/us/app/craft-docs-notes-tasks-ai/id1487937127)
- **MCP server.** Craft exposes docs to external AI via MCP. The user creates a connection in the "Imagine" tab and chooses which documents it can reach. The server now supports space-level access: search with tag and date filters, plus create and edit documents. Setup guides cover ChatGPT (paid plan needed for custom apps) and "Craft Agents". — [Craft support: MCP](https://support.craft.do/en/integrate/mcp.md); [Craft Imagine: ChatGPT guide](https://www.craft.do/it/imagine/guide/mcp/chatgpt); [Craft Imagine: Craft Agents](https://www.craft.do/imagine/guide/mcp/craft_agents)

### Inferences
- **NotebookLM features from before 2026 (prior knowledge, unverified this session; reconfirm before citing):**
  - Audio Overviews (two-host podcast) launched September 2024.
  - Interactive Audio Overviews arrived December 2024.
  - Mind Maps arrived around March 2025.
  - "Discover sources", a web search that proposes up to about 10 sources with summaries for one-click import, arrived around April 2025.
  - Video Overviews (narrated slides) arrived around July/August 2025.
  - Deep Research inside NotebookLM, importing a research report and its sources into the notebook, arrived around November 2025.
  - Chat citations are numbered inline chips. Hovering shows the quoted passage; clicking opens the source scrolled to the passage. That is passage-level granularity, the finest of any product here.
  - Users can toggle sources on and off per query, an explicit pin/unpin of context.
  - Common complaints: source caps, inability to edit Audio Overviews, notes that lose citations once saved, and weak cross-notebook search.
- **Apple (prior knowledge, unverified this session):**
  - Writing Tools work on the current selection or document. They are not retrieval-grounded and produce no citations.
  - ChatGPT integration (iOS 18.2, December 2024) adds "Compose", and it asks consent before sending each request to OpenAI.
  - The 2024 promise of an on-device "semantic index" for personal context is what slipped to 2026.
  - Expect Pages to gain the iOS 27 grammar and accept/reject UI with no source grounding.
- **Coda Brain (prior knowledge, unverified this session):**
  - Announced at Snowflake Summit in June 2024 as an enterprise AI built on Snowflake Cortex.
  - Retrieves across Coda docs and Packs (Salesforce, Jira, Slack and others) with permission awareness.
  - Answers include source references and can be turned into live Coda tables.
- **Microsoft is the reference design for automatic retrieval with permission trimming.** It also exposes the core weakness: oversharing. Microsoft's own documentation spends most of its length on Purview, DLP and oversharing mitigation, and indexing cannot be turned off.

### Gaps
- No primary Apple source for 2026 (WWDC 2026 newsroom, Pages release notes, Writing Tools in macOS 27) was retrieved. Whether Writing Tools now routes to Gemini, and whether Pages gained any document or retrieval context, is unconfirmed.
- Coda Brain's current status was not verified because the help article returned 403. Neither was its citation format.
- No 2026 Craft changelog was found. Whether Craft's local models are still offered, or have moved beyond "experimental", is unknown.
- No primary documentation was fetched on NotebookLM citation granularity, Discover sources, Mind Maps, Video Overviews or the Deep Research integration.
- Usage quotas for NotebookLM / Gemini Notebook conflict between secondary sources (compute-based 5h refresh since 2026-09-02, versus fixed daily caps).
- No Reddit or forum complaint threads were retrieved for any product.
- No Microsoft documentation describes Copilot's citation granularity. The Learn page covers retrieval only.

## Q2: Best-in-class mechanisms worth copying for ByAuthorX (local-first desktop word processor)

### Takeaway
The patterns worth copying:
1. A NotebookLM-style explicit source set with toggles for each source and passage-level hover citations.
2. Microsoft-style dual indexes (lexical plus vector) with scoped queries that use metadata.
3. Craft-style local models plus an MCP server that exposes only the documents the user chooses.
4. Apple's original-beside-suggestion accept/reject UI for edits.

### Cited Findings
- **Notebook as the context boundary.** Source caps of 50 to 600 per notebook, 500k words or 200MB per source, and no page limit show the scale users expect from a curated corpus. — [NotebookLM Help 16269187](https://support.google.com/notebooklm/answer/16269187); [16213268](https://support.google.com/notebooklm/answer/16213268?hl=en)
- **Hybrid lexical and semantic index, plus relevance signals.** Microsoft combines keyword matching, vectors and personal or social signals. Attaching a folder or library scopes retrieval and adds column metadata as ranking signals. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Re-checking after generation.** Microsoft's data flow calls Graph and the semantic index again after the LLM response (step 5, post-processing). This is a pattern for verifying or attaching citations after drafting. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Incremental index updates.** Edits to indexed docs are re-indexed "immediately". — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Exclusion control.** One switch removes a site from both search and AI retrieval. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Local models and MCP.** Craft offers downloadable small local models (about 0.7 to 4GB) that run offline, alongside optional cloud models. Its MCP server exposes only documents the user picks, with tag and date filtered search. — [9to5Mac](https://9to5mac.com/2025/02/05/crafts-latest-update-could-change-how-you-use-ai-on-your-iphone/); [Craft MCP](https://support.craft.do/en/integrate/mcp.md)
- **Analysis grounded in sources.** Gemini Notebook's per-notebook code sandbox runs analysis on the sources rather than relying only on prose answers. — [Google blog](https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/)
- **Accept/reject edits.** The iOS 27 grammar UI reportedly shows the original beside each suggestion with accept/reject (leak-sourced). — [iLounge](https://www.ilounge.com/news/apple/apple-testing-out-grammar-checker-and-ai-writing-tools-for-upcoming-ios-27)

### Inferences
- For a novelist's manuscript plus research corpus, ByAuthorX should combine:
  - NotebookLM's explicit project source set with toggles for each source.
  - Microsoft's automatic hybrid retrieval inside that set.
  - Scoping by chapter, folder or tag, with metadata (POV, character, timeline) as ranking signals, mirroring Microsoft's library-column metadata.
- **Passage-level citations as the floor.** Each claim gets a chip; hovering shows the quote, clicking jumps to the passage in the manuscript or source. Add a post-generation pass that confirms each cited span actually supports the sentence. No product here documents automatic claim verification, so this would set ByAuthorX apart.
- **Index freshness is a differentiator for a local-first app.** Local incremental re-indexing on save can beat cloud tools' daily or periodic crawls.
- **Expose the local index over MCP** (as Craft does), so users' own Claude or ChatGPT can query the manuscript without uploading it.

### Gaps
- Without primary documentation on NotebookLM's citation UI or Coda Brain's citation and table output, the UX details above rest partly on prior knowledge.

## Q3: Limitations and complaints that reveal unmet needs; what is new versus the known baseline

### Takeaway
The unmet needs:
- Hard source caps and quota opacity (NotebookLM).
- Retrieval misses ("source blindness") and gap-filling from model knowledge even when sources are cited.
- Oversharing and the inability to opt out (Microsoft).
- Writing assistants that are not grounded at all (Apple).
- Opaque retention of products after acquisitions (Coda).

Compared with the baseline, the main news is that Google's NotebookLM-to-Gemini Notebook rename is confirmed (2026-07-16) and comes with code execution grounded in sources and Gemini-app and Search integration. Microsoft has also added scoping by folder with metadata, and Apple's AI now rests on Gemini-based models.

### Cited Findings
- **Retrieval misses and fabrication.**
  - Reported "source blindness" and "consistency repair" after the Gemini 3.1 Pro transition. — [ud.hk (secondary)](https://ud.hk/en/blogs/insight/article/2026-07-07-notebooklm-no-hallucination)
  - A literary-analysis study found false "absence" claims and eventually fabricated examples. — [arXiv 2412.18270](https://arxiv.org/pdf/2412.18270)
- **Quota opacity.** Limits are "Subject to Change", and secondary sources disagree on the quota model. — [Google Help](https://support.google.com/notebooklm/answer/16213268?hl=en); [FreeAcademy](https://freeacademy.ai/blog/notebooklm-free-vs-plus-2026) versus [Elephas](https://elephas.app/blog/notebooklm-source-limits.md)
- **Microsoft index cannot be turned off.** Semantic indexing "can't be disabled". Exclusion is by whole site only and affects Search and AI together. Mitigating oversharing requires Purview or DLP, which is E5 or licensed separately. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Permission-sync lag.** Incremental permission sync was still on the roadmap in 2026, which implies lag. — [RM503570 mirror](https://mc.merill.net/message/RM503570)
- **Unsupported content.** Shared, delegated and archived mailboxes and archived SharePoint content aren't indexed. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- **Apple delays and regional gaps.** The personalized-context Siri slipped a year (delayed 2025-03-07). Siri AI is reportedly unavailable in the EU and China at iOS 27 launch. — [9to5Mac](https://9to5mac.com/2026/03/20/apples-gemini-powered-siri-upgrade-could-still-arrive-this-month/); [framesixty (low reliability)](https://framesixty.com/develop-app-ios/)
- **What is new versus the baseline (dated):**
  - **2026-07-16:** NotebookLM became Gemini Notebook. It added a per-notebook code sandbox (Ultra and Workspace now; Pro rolling out), notebooks in the Gemini app with sync, and AI Mode in Search "soon". — [Google blog](https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/). This is related to the baseline's "Gemini in Docs @-notebook grounding (Sep 2026)": notebooks are becoming a context object that can be carried across Google surfaces.
  - **Microsoft Learn page updated 2026-08-18:** scoping by attached SharePoint library or folder with column metadata; 512MB file support. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
  - **June 2026:** Power BI semantic-model grounding for Frontier customers. — [MC1323266](https://mc.merill.net/message/MC1323266)
  - **January 2026:** Apple Foundation Models move to a Gemini base. **June 8, 2026:** WWDC revamps Writing Tools. — [MacRumors](https://www.macrumors.com/2026/04/22/google-gemini-powered-siri-2026); [Digital Trends](https://digitaltrends.com/computing/wwdc-2026-ios-27-siri-ai-apple-intelligence-upgrades-and-everything-else-apple-announced)
  - **2025-10-29:** Coda folded into the Superhuman suite, with the Superhuman Go assistant and an agent marketplace built on Packs. — [Superhuman Help](https://help.superhuman.com/hc/en-us/articles/46210093285773-What-s-changing-Grammarly-becomes-Superhuman)
  - **Craft (2025 to 2026):** offline local LLMs plus a space-level MCP server. — [9to5Mac](https://9to5mac.com/2025/02/05/crafts-latest-update-could-change-how-you-use-ai-on-your-iphone/); [Craft MCP](https://support.craft.do/en/integrate/mcp.md)

### Inferences
- **No product here verifies claims after generation.** Citations are offered as the user's verification tool. This is the same gap seen in the baseline (Word Copilot's deep citations slipping to November 2026), and a clear opening for ByAuthorX.
- **Consumer and local users have no strong source-grounded option.** Apple's privacy-first stack does not ground in documents, while the strongest grounding tools (NotebookLM, M365) are cloud-only.
- **Craft is the closest analogue to ByAuthorX.** It is local-first in spirit, with offline models and MCP. But it has not been shown to offer passage citations or a curated source set the way NotebookLM does. That combination looks unclaimed.
- **Vendor churn risk.** Coda Brain's apparent absorption into Superhuman and the NotebookLM rename show that cloud AI features change name and scope often. Owning the index locally is a stability argument for users.

### Gaps
- No confirmed list of Writing Tools features in Pages on iOS/macOS 27.
- Coda Brain citation behavior and 2026 status not verified.
- No first-hand user-complaint corpus (Reddit or forums) was gathered for any product in this session.
