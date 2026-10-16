# PKM and Reading/Annotation Tools: Context, Retrieval, Linking and AI (as of 16 Oct 2026)

Scope: Mem, Reflect, Logseq (DB), Tana, Heptabase, Capacities, Reor, LiquidText, MarginNote 4, Readwise Reader, Hypothesis, Notion synced blocks. The research budget was about 17 tool calls across 12 products, so coverage is uneven. Items marked **[secondary]** come from aggregators, reviews or vendor-marketing reposts, not first-party pages. Items with no date are undated in the source.

## Key Question 1: Which knowledge-linking and annotation-to-manuscript patterns are best-in-class for a local-first desktop word processor (ByAuthorX) with a source library that has page/offset locators?

### Takeaway
No surveyed tool covers the whole path from a source passage to a draft and back. The strongest pieces are spread across several products, and ByAuthorX can combine them:
- **Hypothesis:** multi-selector anchoring with orphan handling.
- **LiquidText, MarginNote, Heptabase:** excerpts that stay live-linked to their source.
- **Logseq DB:** typed tags and queries in a local SQLite store, plus a markdown mirror.
- **Notion:** synced (transcluded) blocks.
- **Reor:** local embeddings that surface related notes automatically.

### Cited Findings

**Anchoring and locators (Hypothesis)**
- Hypothesis stores several selectors for each annotation:
  - a TextQuoteSelector: the exact text plus a 32-character prefix and suffix for context;
  - a RangeSelector;
  - a TextPositionSelector.
  
  Its "fuzzy anchoring" tries four strategies that combine these selectors to re-attach annotations after a document changes — [Hypothesis blog: Fuzzy Anchoring](https://web.hypothes.is/blog/fuzzy-anchoring/); [Hypothesis](https://web.hypothes.is/?p=1317)
- Annotations that cannot be re-attached are kept as "orphans," which Hypothesis now calls "unanchored." The original anchor text is shown struck through. An orphans tab was added in client v1.2.0 — [Hypothesis help: Unanchored annotations](https://web.hypothes.is/help/what-are-unanchored-annotations/); [Showing Orphaned Annotations](https://web.hypothes.is/blog/showing-orphaned-annotations)
- A 2015 study measured orphaned annotations and found they come mainly from lost or modified web pages — [arXiv 1512.06195](https://arxiv.org/pdf/1512.06195)
- The general approach (exact string, then fuzzy match by context overlap) survives minor rewrites but not deleted paragraphs or heavy paraphrase — [wal.sh](https://wal.sh/research/annotation-systems/) **[secondary, personal site]**

**Excerpt-to-workspace linking (LiquidText, MarginNote 4, Heptabase)**
- LiquidText:
  - Excerpts pulled into its workspace keep links to their original source and context. Users can draw live links across pages, documents and notes — [Techjockey](https://www.techjockey.com/detail/liquidtext); [GetApp](https://www.getapp.com.au/software/2060201/liquidtext) **[secondary]**
  - Real-time collaborative editing and commenting launched in April 2025, with cloud sync on the "Live" and "Live Unlimited" tiers — [Dealroom / directory profile](https://app.dealroom.co/companies/liquidtext) **[secondary, single source]**
- MarginNote 4:
  - v4.4.4–4.4.5 added custom AI: users configure their own provider, model and API key, and AI features then use it instead of MarginNote credits (a "Max" feature).
  - The same release improved AI question extraction (multi-column layouts, questions that span pages), added Export & Print, and added a Markdown text box — [MarginNote forum](https://forum.marginnote.com/t/ver-4-4-4-4-4-5-unlock-custom-ai-major-upgrades-to-ai-problem-extraction-export-print-markdown-text-box/11984)
  - The App Store page is titled "MarginNote 4: AI Notes·MindMap." Aggregator storefronts show last-update dates of 24 Mar 2026 and 9 Jun 2026, so the exact latest version is unclear — [App Store](https://apps.apple.com/us/app/marginnote-4-ai-notes-mindmap/id1531657269); [AppFollow](https://apps.appfollow.io/ios/marginnote-4-ai-notes-mindmap/1531657269?country=it) **[secondary]**
- Heptabase (from its public roadmap):
  - "Highlight and annotate text on Note cards": 13 Mar 2026, v1.86.1
  - "Web card": 11 Mar 2026, v1.85.1
  - OCR parsing of PDF content, images, equations and tables: 25 Sep 2025
  - Mobile PDF highlight and annotation is still listed as "Working," not shipped — [Heptabase Public Roadmap](https://wiki.heptabase.com/roadmap)
  - The December 2025 changelog added "Whiteboard: AI action for cards" (v1.82.0) — [Heptabase changelog 2025](https://wiki.heptabase.com/changelog/2025)
  - Reviewers describe importing a paper, highlighting sections and turning the highlights into cards on whiteboards — [aiindigo review 2026](https://aiindigo.com/blog/heptabase-review-2026-the-visual-knowledge-base-for-deep-thinkers) **[secondary]**

**Transclusion and synced content (Notion, Logseq)**
- Notion synced blocks:
  - The same content appears in several places, including across pages and workspaces, and an edit to any copy updates all of them. The original page is labeled "ORIGINAL."
  - Viewers need access to the original to see the content, and edit access to the original to edit any copy.
  - "Unsync" makes a copy independent. Warning: when there are more than 10 copies, "Unsync all" or deleting the original removes every copy, and undo cannot restore them — [Notion Help: Synced blocks](https://www.notion.com/help/synced-blocks)
- Logseq DB:
  - The legacy `((block-ref))` syntax is being deprecated. Block refs in titles are converted to page refs on import, while existing refs in block bodies still work.
  - Block refs survive hashtag link merges and resolve to title text in breadcrumbs (update of 27 Apr–16 May 2026) — [Logseq forum, 16 May 2026](https://discuss.logseq.com/t/whats-new-with-logseq-db-may-16th-2026/35020)

**Typed structure and queries (Logseq DB, Tana, Capacities)**
- Logseq DB public beta (marketed as "Logseq 2.0 Beta," tag 2.0.1, 13 Jul 2026):
  - Storage moves to local SQLite.
  - Adds unified nodes, typed properties, tags-as-classes, dashboard queries, paid invite-only RTC sync, and Logseq Publish.
  - The file-based version continues as "Logseq OG," maintained but getting no new features — [GitClear 2.0.1](https://www.gitclear.com/open_repos/logseq/logseq/release/2.0.1); [Kompozy](https://kompozy.io/news/logseq-2-0-db-version-beta) **[secondary]**; [zeli](https://zeli.app/story/48896229) **[secondary]**
  - The release notes call it "an early beta" with rough edges and advise backing up first — [releasebot](https://releasebot.io/updates/logseq/logseq) **[secondary]**
- Logseq Markdown Mirror (announced 16 May 2026):
  - Opt-in per graph. It writes a plain-markdown copy of DB blocks to disk, with block IDs stored in comments.
  - It is currently one-way; two-way sync is on a feature branch.
  - The same update added a `qmd query` CLI for multi-part queries that scripts or agents can drive, and a `logseq-answer-machine` agent skill.
  - Graph View V2 was rebuilt because "the old one was sluggish on big graphs"; scoped property values also load faster in dense graphs — [Logseq forum, 16 May 2026](https://discuss.logseq.com/t/whats-new-with-logseq-db-may-16th-2026/35020)
- Capacities:
  - When objects are added as AI context, their backlinks are added automatically (March 2026 release). Tags and collections could not yet be used as context.
  - Saved AI chats become objects: they appear in the backlinks of the objects used as their context and are full-text searchable (release 29).
  - An MCP connector for Pro users is in beta, limited to one space (release 62) — [Capacities release 29](https://capacities.io/whats-new/release-29); [release 62](https://capacities.io/whats-new/release-62); [releasebot summary](https://releasebot.io/updates/capacities) **[secondary for the March 2026 item]**

**Automatic related notes (Reor, Mem)**
- Reor:
  - Each note is chunked and embedded into a local LanceDB vector database, and related notes are linked automatically by vector similarity.
  - Q&A uses RAG over the notes, with Ollama built in or any OpenAI-compatible endpoint.
  - Everything is stored locally in a folder of markdown files with an Obsidian-like editor. Import is manual, and frontmatter may not parse correctly.
  - License is AGPL-3.0 — [Reor GitHub](https://github.com/reorproject/reor); [README mirror](https://cdn.jsdelivr.net/gh/reorproject/reor@main/README.md); [sourcepulse](https://www.sourcepulse.org/projects/1845548) **[secondary]**
- Mem: the App Store notes for v2.5.2 (undated) say "Heads Up" (formerly Copilot) finds related notes automatically, groups them by topic, and shows related meetings on a timeline. Chat can create, edit and organize notes — [App Store](https://apps.apple.com/py/app/mem-2-0-your-ai-second-brain/id1578757028?l=en-GB)

**Reading-to-export pipeline (Readwise)**
- The Readwise/Reader update of 6 Aug 2026 lists:
  - a web highlight tag manager to browse, filter and export highlights by tag;
  - an optional metadata block (author, title, URL, tags) in OneNote exports;
  - Obsidian export fixes and podcast transcript export;
  - more reliable CSV export for large libraries;
  - a Readwise MCP/CLI offering hybrid (semantic and full-text) search across documents and highlights to external AI tools (Claude, ChatGPT, Perplexity), with skills for Claude Code and Codex;
  - EPUB print page numbers — [Readwise Reader update Aug 2026](https://readwise.io/reader/update-aug2026)

### Inferences
These are my synthesis for ByAuthorX, not sourced claims.
- **Anchoring:** store each citation or excerpt anchor with several selectors: page and character offset (the existing locator), the quote text with roughly 32 characters of prefix and suffix, and a content hash. Re-anchor fuzzily when a source is replaced or re-OCR'd, and never silently drop a failed anchor. Show it as "unanchored" with the struck-through quote, the way Hypothesis does.
- **Transclusion:** an excerpt placed in the manuscript should be a live transclusion (Notion/Logseq style) carrying a back-pointer to the source locator. That allows "jump to source page," a list of every place a source passage is used, and "unsync" to turn it into plain quoted text for final export. Avoid Notion's footgun where deleting the original destroys every copy; deleting a source should orphan its excerpts, not erase them.
- **Typed sources:** a typed source library (tags as classes with typed properties, as in Logseq DB, Tana supertags and Capacities object types) plus saved queries ("all highlights tagged #method from sources after 2020 not yet cited in Chapter 3") is the deterministic layer competitors are converging on.
- **Local first:** Logseq's SQLite store with a markdown mirror and Reor's local LanceDB are the closest architectural precedents. Both point to a local DB as the source of truth plus a human-readable export.
- **Agent access:** exposing the library through MCP/CLI (Readwise, Capacities, Logseq CLI) is now table stakes for 2026 AI interoperability.

### Gaps
- I found no first-party 2025–2026 changelog for LiquidText and no evidence that LiquidText ships AI features (a DEVONthink forum thread is only a user wish) — [DEVONthink forum](https://discourse.devontechnologies.com/t/liquidtext-and-openai-integration-for-pdf-files/75730).
- I did not verify MarginNote 4's mind-map ↔ PDF excerpt linking details or Heptabase's PDF-highlight-to-card back-linking behavior from first-party docs.
- Tana's "live search" (search nodes) and supertag field mechanics were not verified from Tana docs in this pass; the Tana 2025 article fetch returned no feature list.
- Reflect: backlink mechanics were seen only in secondary reviews.
- Notion AI's citation behavior was not researched.

## Key Question 2: How do AI-enabled PKM tools show provenance (which note, block or highlight an answer came from)?

### Takeaway
The 2026 norm is chat over the whole library with inline citations that link to the exact passage. Readwise's Global Ghostreader (Aug 2026) is the clearest example, and Mem 2.0 shipped inline source annotations with "grounded" fact cards. Several tools let users pick context explicitly: @-mentions in Mem, objects plus their auto-included backlinks in Capacities, agents scoped to a supertag in Tana. How well citation deep links hold up is a known weak spot; Readwise had to fix links that jumped to the wrong spot.

### Cited Findings

**Readwise Reader**
- Global Ghostreader (6 Aug 2026, web and desktop; mobile "coming soon"):
  - Chats over the entire library, with "cited answers" whose citations link back to the referenced passages.
  - It is agentic: it can tag, move, edit metadata and triage, using skills such as Research a topic, Digest my reading and Find similar.
  - Mobile per-document chat knows the reader's current position, and its answers also link to the cited text.
  - A fix addressed citation links that jumped to the wrong spot on first click — [Readwise update Aug 2026](https://readwise.io/reader/update-aug2026)
- "Chat with Highlights" (Readwise web and mobile Review tab) is described as a super-powered search across every highlight — [Readwise docs](https://docs.readwise.io/readwise/guides/chat-with-highlights); [changelog](https://readwise.io/changelog/chat-with-highlights)
- An earlier third-party post describes referenced highlights shown in a sidebar, with inline links to exact highlight locations — [pjordan substack](https://pjordan.substack.com/p/ai-hits-readwise-chat-with-your-highlights) **[secondary, pre-2026]**

**Mem**
- The Mem 2.0 dev update (29 Aug 2024) promised the following, all announced rather than shipped at the time:
  - chat that gathers *all* relevant knowledge on a topic rather than a sample;
  - understanding of time references ("the draft I wrote yesterday");
  - inline source annotations;
  - fact cards showing only facts verified as grounded in the user's notes;
  - @-mentions of a note or collection to focus the chat;
  - side-by-side chat with a note — [Mem blog](https://get.mem.ai/blog/mem-2-0-dev-update-smarter-chat)
- Mem's 2024 year-in-review says inline source annotations, comprehensive recall and redesigned fact cards shipped in the Alpha — [Mem 2024 review](https://get.mem.ai/blog/2024-year-in-review)
- Side-by-side chat replaced 1.0's Smart Write & Edit — [Mem support](https://support.mem.ai/article/189-side-by-side-chat)

**Capacities**
- Context is chosen explicitly: multiple objects can be added, and their backlinks come along automatically (Mar 2026).
- Saved chats show up in the backlinks of the source objects, which gives a reverse provenance trail — [Capacities release 29](https://capacities.io/whats-new/release-29); [releasebot](https://releasebot.io/updates/capacities) **[secondary]**
- The roadmap (23 Apr 2026) plans "AI Assistant 2.0": a choice of model providers including EU-hosted models, and agentic chat that searches notes and the web and suggests edits or new objects — [Capacities What's next](https://capacities.io/whats-next)

**Tana**
- Meeting notetaker:
  - Bot-less, integrated into the desktop app (June 2025). It transcribes, summarizes and links notes to work.
  - It is started from a node with an audio-enabled supertag, or with Cmd/Ctrl+K "Start meeting with…" — [Tana Meeting Notetaker docs](https://outliner.tana.inc/docs/tana-meeting-notetaker)
- AI chat agents can be built for specific supertags.
- iOS voice chat entered early access in Dec 2025, with responses tailored to the user's supertags — [Tana changelog 2025 w50-51](https://outliner.tana.inc/changelog/2025-w50-51)
- Free plan: 500 AI credits per month (Sep–Oct 2025) — [Tana changelog 2025 w41-43](https://outliner.tana.inc/changelog/2025-w41-43)
- Custom agents gained voice sessions in May 2026 — [Tana changelog](https://www.tana.inc/changelog)
- Tana now separates "Tana Outliner" from the company's meeting platform, "Tana" — [Tana article](https://outliner.tana.inc/articles/whats-new-in-tana-2025-product-updates)

**Heptabase**
- AI Tutor app feature: 31 Mar 2026, v1.88.0
- AI-suggested questions for new chats: 22 Jan 2026, v1.83.4 — [Heptabase roadmap](https://wiki.heptabase.com/roadmap)

**Reor**
- Q&A over notes uses local RAG. Related notes come from vector similarity in LanceDB — [Reor GitHub](https://github.com/reorproject/reor)

**Logseq DB**
- No built-in AI chat. AI access is through the CLI and the `logseq-answer-machine` agent skill (May 2026) — [Logseq forum](https://discuss.logseq.com/t/whats-new-with-logseq-db-may-16th-2026/35020)

**Encryption vs. cloud AI (Reflect)**
- Reflect encrypts notes on the client with XChaCha20-Poly1305 before upload, using a key derived from the user's password; attachments are encrypted too — [Reflect academy: Security](https://reflect.academy/security-and-encryption); [Notion mirror](https://team-reflect.notion.site/Security-and-encryption-89450e351e724893b72898d9acc663fe)
- Reflect uses OpenAI GPT-4 and Whisper for writing help and transcription — [reflect.app](https://www.reflect.app/home/)
- A July 2026 article argues that end-to-end encryption and cloud AI cannot both apply to the same request, because the model has to read the plaintext — [calmara.app](https://calmara.app/blog/why-ai-note-apps-arent-end-to-end-encrypted) **[secondary, competitor blog]**
- A Privacy Guides thread is skeptical that Reflect's E2EE can be verified without open-source clients — [Privacy Guides forum](https://discuss.privacyguides.net/t/reflect-app-encrypted-note-taking-app/27531.md)

**Local vs. cloud summary**
| Tool | Where data and AI run |
|---|---|
| Reor | Fully local (Ollama) |
| Logseq DB | Local SQLite; paid E2E-encrypted sync in invite-only testing |
| MarginNote 4 | Bring your own API key |
| Readwise | Cloud, with BYOK GPT-5 models |
| Capacities | Cloud; EU-hosted models planned |
| Mem, Tana | Cloud |

Sources: [Reor](https://github.com/reorproject/reor), [Logseq forum](https://discuss.logseq.com/t/whats-new-with-logseq-db-may-16th-2026/35020), [MarginNote forum](https://forum.marginnote.com/t/ver-4-4-4-4-4-5-unlock-custom-ai-major-upgrades-to-ai-problem-extraction-export-print-markdown-text-box/11984), [Readwise](https://readwise.io/reader/update-aug2026), [Capacities](https://capacities.io/whats-next)

### Inferences
These are my synthesis, not sourced claims.
- **Provenance:** the best-in-class pattern is numbered inline citations that deep-link to the exact passage (Readwise), plus a "grounded only" mode that suppresses uncited claims (Mem fact cards). For ByAuthorX, every AI claim should resolve to a source ID plus page/offset locator and open the PDF/EPUB at the highlighted span. Citation deep-link accuracy should be a tested invariant, since Readwise had to ship a fix for it.
- **Context selection:** show context selection explicitly, using @-mentions or selected sources plus their linked notes (Capacities' auto-include-backlinks), and show the user which sources went into the answer before or alongside it.
- **Reverse provenance:** saving AI outputs as objects that appear in the backlinks of their sources (Capacities) gives a trail from source to derived text. This maps well onto "where is this source used in my manuscript."
- **Privacy:** for a local-first app, the honest position is local models (Ollama, as in Reor), or BYOK with explicit per-request disclosure of what text leaves the device. Claiming E2EE while sending plaintext to cloud AI draws criticism (the Reflect debate).

### Gaps
- **Citation UI details not confirmed from first-party sources:** whether Mem's 2026 citations link to block-level spans or whole notes; how Tana chat cites nodes; whether Capacities chat shows inline citations; whether Heptabase chat cites cards or PDF locations.
- **Performance at scale:** the only first-party statements found were Logseq's graph-view and dense-graph fixes and Readwise's CSV export fixes for large libraries. I found no benchmarks for any tool.
- **User complaints:** I gathered almost none from Reddit or forums in this pass. Known items are limited to Logseq DB's "early beta" warning, the loss of hand-editable markdown in Logseq DB (Kompozy **[secondary]**), Reor's manual import and frontmatter parsing issues, Readwise's wrong-spot citation links, and Privacy Guides' skepticism about Reflect's encryption.
- **Status not confirmed:** whether Reor is still maintained in 2026; a 2026 Mem changelog (the v2.5.2 notes are undated); Global Ghostreader on mobile after Aug 2026.
