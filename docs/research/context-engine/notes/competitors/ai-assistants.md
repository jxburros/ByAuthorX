# General AI Assistants with Writing Surfaces: Context, Retrieval and Citations (as of 2026-10-16)

Research date: 2026-10-16. Budget-limited pass (about 15 tool calls). Sources marked **[secondary]** are third-party or aggregator pages, not vendor documentation or the original study. Several vendor help-center pages could not be fetched directly, and search-engine summaries of them were used instead. Those items are flagged.

## Which context and citation UX patterns are best-in-class and worth bringing into ByAuthorX?

### Takeaway
The patterns worth copying are these:
- **Scoped, user-visible context containers**: Claude and ChatGPT Projects, Gemini Gems, Perplexity Spaces, Lex knowledge bases.
- **An explicit indicator when retrieval replaces full-context reading**: Claude's "RAG-enabled" marker.
- **Citations that point to the specific source snippet and open the original**: ChatGPT company knowledge.
- **Memory you can view, edit and scope per project, plus an incognito or temporary mode**: Claude and ChatGPT.
- **Provenance tracked by process rather than guessed by detection**: Grammarly Authorship replay.

All of these suit a local-first word processor, where the corpus and the index stay on the device.

### Cited Findings

**Claude (Anthropic)**
- When project knowledge nears the context window limit, RAG mode switches on automatically. It raises project capacity "by up to 10x", and Claude then uses a project-knowledge search tool to pull relevant passages instead of loading everything. It is available on paid plans (Pro, Max, Team, Enterprise). — [Anthropic Help: RAG for Projects](https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects) (fetched 2026-10-16)
- Users cannot choose when RAG is used. Projects stay in-context when possible and can switch back if knowledge falls below the threshold. A visual indicator shows when a project is RAG-enabled. RAG works alongside web search, extended thinking and Research. Anthropic's best-practice advice is clear filenames, grouping related documents, and naming documents in prompts. Anthropic claims quality stays "consistent" with in-context processing, which has not been independently verified. — [Anthropic Help: RAG for Projects](https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects)
- One third-party guide says RAG starts at about 150K tokens of project knowledge. Anthropic's article gives no number. **[secondary, unverified]** — [hiredigital / search summary](https://hiredigital.com/blog/what-you-need-to-know-about-claude-projects-and-rag)
- Memory came first to Team and Enterprise (Sept 2025). Each project has its own memory, and users can view and edit what Claude remembers. Enterprise admins can turn memory off. Incognito chats are not saved to history or memory and are available to all users. Memory reached Pro and Max on Oct 23, 2025. — [Anthropic: Memory](https://www.anthropic.com/news/memory); [VentureBeat](https://venturebeat.com/ai/anthropic-adds-memory-to-claude-team-and-enterprise-incognito-for-all) **[secondary]**
- Memories can reportedly be exported per project, and import is described as experimental. **[secondary]** — [Techzine](https://www.techzine.eu/news/analytics/135728/anthropic-expands-claudes-memory-for-paid-users/)

**ChatGPT (OpenAI)**
- Memory has two controls: "Reference saved memories" (things you explicitly asked it to remember) and "Reference chat history" (past chats). Turning off saved memories also turns off chat history, and turning Memory off does not delete chats. Temporary Chat neither uses nor updates memory. — [OpenAI Memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq); [OpenAI: Memory and new controls](https://openai.com/index/memory-and-new-controls-for-chatgpt/)
- The chat-history memory upgrade reached Plus and Pro from April 2025, at first excluding the EU, UK, Switzerland, Norway, Iceland and Liechtenstein. Free users got a "lightweight" version later. — [OpenAI Memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq); [Gigazine](https://gigazine.net/gsc_news/en/20250411-chatgpt-memory-improved/) **[secondary]**
- Company Knowledge launched in October 2025 for Business, Enterprise and Edu. It draws on connectors such as Google Drive, SharePoint, Slack and GitHub. Answers list the sources used, with the specific snippets, and each citation opens the original. Results respect the user's existing permissions, and admins choose which connectors are enabled. While company knowledge is on, ChatGPT reportedly cannot search the web or make charts or images. — [VentureBeat](https://venturebeat.com/ai/openai-launches-company-knowledge-in-chatgpt-letting-you-access-your-firms) **[secondary]**; [Neowin](https://www.neowin.net/news/chatgpt-takes-on-microsoft-365-copilot-with-the-new-company-knowledge-feature/) **[secondary]**
- A June 2026 guide says OpenAI now groups connectors, file search and synced knowledge under "Apps in ChatGPT." This comes from a single low-authority source. **[secondary, weak]** — [technovids](https://technovids.com/chatgpt-apps-connectors-guide)

**Perplexity**
- Perplexity had the lowest citation failure rate of the eight tools the Tow Center tested (37%), and the EBU/BBC 2025 study rated it best for journalistic use. Its inline numbered source citations are the reference design that other products copy. — see the accuracy section below.

**Grammarly**
- Authorship replays how a document was written, "from the first paste to the last keystroke." It records typed versus pasted text, the websites text was copied from, and prompts to generative AI. Students share the report by URL. — [Grammarly blog](https://www.grammarly.com/blog/product/grammarly-authorship/); [TAMIU FAQ](https://www.tamiu.edu/distance/documents/grammarly-authorship-faqs.pdf)
- Authorship data is stored encrypted on the device while the student writes. Clicking "See report" sends it to Grammarly servers, where it is kept for up to 24 hours. — [TAMIU FAQ](https://www.tamiu.edu/distance/documents/grammarly-authorship-faqs.pdf) (institutional, secondary)
- Authorship "does not analyse the contents of the text... to try and identify whether it was written by AI." It tracks where text came from. — [Jisc National Centre for AI](https://nationalcentreforai.jiscinvolve.org/wp/?p=4577)
- Authorship can be turned on with one click in Word, Google Docs or Grammarly Docs for Blackboard assignments. — [Grammarly: Authorship in Blackboard](https://www.grammarly.com/blog/product/grammarly-authorship-is-now-available-in-blackboard/)
- In August 2025 Grammarly launched 8 AI agents in its AI-native "docs" editor, among them a **Citation Finder** and an **AI Detector**. The Citation Finder finds sources that support, contest or refine the claims in a draft and formats the references. The AI Detector was Pro-only at launch, and Enterprise and Edu access was planned for later in 2025. — [The Daily Star](https://online87.thedailystar.net/tech-startup/news/grammarly-launches-new-ai-agents-3965976) **[secondary]**; [CO/AI](https://getcoai.com/news/grammarly-launches-8-ai-agents-to-automate-writing-tasks/) **[secondary]**

**Lex.page**
- Lex V2 shipped as 5 releases in 2025: a new start page, AI line editing ("polish clunky sentences, fix transitions, add detail"), and in May 2025 **style guides and knowledge bases**. The founder's stated reason was that AI edits miss because they "sound nothing like you" and are "disconnected from the context around your writing." — [Lex newsletter: Lex V2 knowledge bases & style guides](https://buttondown.com/lex/archive/lex-v2-knowledge-bases-style-guides/); [Introducing Lex V2](https://buttondown.com/lex/archive/introducing-lex-v2/); [Lex V2 line editor](https://buttondown.com/lex/archive/lex-v2-the-line-editor/)
- Directories also list inline autocomplete, commands on selected text, document chat (including fact-checking ideas) and direct publishing to newsletters. Pricing claims conflict. **[secondary]** — [devtoollab](https://devtoollab.com/ai-tools/lex-ai)

**Microsoft Copilot (consumer)**
- Memory was announced in April 2025 at Microsoft's 50th-anniversary event. Users can choose what Copilot remembers or opt out, through a user dashboard. Actions and Deep Research were announced at the same event. — [AlternativeTo](https://alternativeto.net/news/2025/4/microsoft-adds-new-features-to-copilot-including-memory-actions-deep-research-and-more) **[secondary]**; [Winaero](https://winaero.com/copilot-gets-memory-web-actions-podcast-creation-and-more) **[secondary]**

**Gemini app**
- Google's education materials list file uploads of up to 1,500 pages, and bundle Gems, Canvas and Workspace (Drive) connections. — [Google for Education: Gemini](https://edu.google.com/ai/gemini-for-education/)
- Deep Research: Google's wording is "up to hundreds" of sources, with no published cap. The free tier is limited to 5 reports per month, and the API file limit rose from 20 MB to 100 MB on Jan 8, 2026. **[secondary]** — [suprmind.ai](https://suprmind.ai/hub/?p=5207)

### Inferences
- **Show which context mode is active.** Claude's "RAG-enabled" indicator matters because retrieval silently changes what the model "read." ByAuthorX should go further and show per answer which chunks or documents were retrieved. Claude only shows a project-level badge.
- **Cite snippets and jump to them.** The ChatGPT company knowledge pattern (snippet-level citation that opens the source) maps directly onto local files. Clicking a citation should open the local document scrolled to the passage, which is easier locally than in cloud tools.
- **Scope memory per project, make it editable, and offer incognito.** Claude's model is the cleanest fit for manuscripts and series: memory per book, visible and editable, with export.
- **Prefer provenance over detection.** Grammarly Authorship moved away from AI-text detection toward process logging, and educators find that more defensible. A local-first app could keep its own on-device provenance log (typed, pasted, AI-inserted, with the source of each), so nothing reaches a server, unlike Grammarly's 24-hour server step.
- **Pair a style guide with a knowledge base.** Lex's diagnosis (voice plus context) is a strong framing for an author-focused word processor.
- **Support and contest claims.** A citation finder that looks for sources that support or *contest* a claim (Grammarly) is a useful verification pattern beyond plain "add citation."

### Gaps
- I could not fetch the official Perplexity help pages on Spaces, Pages, Comet or Labs, or on citation format. No source found covers Comet's or Pages' context handling.
- I could not confirm Gemini Gems knowledge-file limits (count and size per Gem) or the mechanics of Drive grounding in Gems from Google's own help.
- I found nothing on Microsoft Copilot Pages in the consumer app, or on Copilot's consumer citation UX.
- I did not verify the details of Claude Artifacts, Research or the Citations UX (for example, how in-chat citations display) for this pass.
- I did not research ChatGPT Canvas or Deep Research citation display for this pass.

## What do independent evaluations say about citation accuracy?

### Takeaway
Independent studies say citation accuracy is poor across the board. In the Tow Center/CJR test (March 2025), the tools got the source wrong more than 60% of the time. Perplexity did best (37% failure) and Grok-3 worst (94%). In the EBU/BBC study (October 2025), about 45% of answers had at least one significant issue, and sourcing was the biggest problem (31%). Gemini was worst (about 76%), and Perplexity again did relatively well.

### Cited Findings
- **Tow Center for Digital Journalism / CJR (March 2025):** The study tested 8 tools: ChatGPT Search, Perplexity, Perplexity Pro, DeepSeek Search, Microsoft Copilot, Gemini, Grok-2 and Grok-3. It used 20 publishers and 1,600 queries. Each query gave a quote from an article and asked for the title, publisher, date and URL. The tools gave wrong answers more than 60% of the time overall. — [CJR: We compared eight AI search engines. They're all bad at citing news](https://www.cjr.org/tow_center/we-compared-eight-ai-search-engines-theyre-all-bad-at-citing-news.php)
- Perplexity had the lowest failure rate (37%) and Grok-3 the highest (94%). — [Nieman Lab](https://www.niemanlab.org/2025/03/ai-search-engines-fail-to-produce-accurate-citations-in-over-60-of-tests-according-to-new-tow-center-study/) **[secondary report of primary]**
- Gemini and Grok-3 gave more fabricated links than correct ones across 200 tests, and Grok-3 linked to 404 pages 154 times. The chatbots rarely declined to answer. Premium tiers were *more* confidently wrong than free tiers. Some tools appeared to bypass robots.txt and cited syndicated copies instead of the originals. — [CJR](https://www.cjr.org/tow_center/we-compared-eight-ai-search-engines-theyre-all-bad-at-citing-news.php); [Fortune](https://fortune.com/2025/03/18/ai-search-engines-confidently-wrong-citing-sources-columbia-study) **[secondary]**
- **EBU/BBC "News Integrity in AI Assistants" (Oct 21–22, 2025):** 22 public-service media organizations in 18 countries took part, covering 14 languages and more than 3,000 responses. 45% of answers had at least one significant issue. — [VRT announcement](https://communication.vrt.be/largest-study-of-its-kind-shows-ai-assistants-misrepresent-news-content-45-of-the-time-regardless-of-language-or-territory); [NPR](https://www.npr.org/sections/npr-extra/2025/10/21/g-s1-94424/global-study-on-news-integrity-in-ai-assistants-shows-need-for-safeguards-and-improved-accuracy)
- By issue type: sourcing 31%, accuracy about 20%, missing context about 14%. Gemini was worst (about 76% of responses had issues), and Perplexity was rated best for journalistic use. — [RadioInfo](https://radioinfo.com.au/?p=235432) **[secondary]**; [Screenvoice](https://www.screenvoice.cz/?p=40256) **[secondary]**
- Heise reports that problematic answers fell from 51% in the earlier BBC study (Feb 2025) to 37%, and that refusals dropped to 0.5%. This **conflicts** with the 45% headline. It may be a different metric, such as BBC-only data or "significant" versus any issue, but I could not confirm which. — [Heise](https://heise.de/-10798062) **[secondary]**
- The EBU released a "News Integrity in AI Assistants Toolkit" and is calling on regulators to enforce existing rules and for ongoing monitoring. — [NPR](https://www.npr.org/sections/npr-extra/2025/10/21/g-s1-94424/global-study-on-news-integrity-in-ai-assistants-shows-need-for-safeguards-and-improved-accuracy)

### Inferences
- In both studies, the most common failure is in sourcing and attribution, not in the answer text itself. For ByAuthorX, citations should be checked mechanically, not just generated. Check that a cited passage exists verbatim or near-verbatim in the local file, and flag citations that fail.
- Low refusal rates and premium tools being "confidently wrong" support a UX in which the assistant can say "not found in your sources." A local corpus makes that checkable.
- Perplexity's relative lead in both studies suggests that retrieval-first design (search, then cite numbered sources inline) works better than citations added after generation. That is an inference from rankings, not a measured cause.

### Gaps
- I found no independent 2026 follow-up study (for example, a Tow Center or EBU rerun) in this pass.
- I found no independent accuracy evaluation of citations over *user-uploaded files* (as opposed to web news) for Claude Projects, ChatGPT Projects or Gemini Gems. That is the most relevant case for ByAuthorX.
- Claude was not among the 8 Tow Center tools, and I did not confirm whether Claude was among the EBU-assessed assistants (reports name ChatGPT, Copilot, Gemini and Perplexity).

## What is new compared with the known baseline (Claude RAG ~10x with early-trigger bug report; ChatGPT Projects 5/25/40 files)?

### Takeaway
Both baseline figures still hold. The Claude "up to 10x" automatic RAG is confirmed in the current help article. ChatGPT Projects' 5/25/40 files (Free / Go+Plus / Pro+Business+Enterprise) is the figure secondary sources most often report, as of October 2026. What's new is mostly around them: memory rollouts with incognito and temporary modes, ChatGPT company knowledge with snippet citations, Grammarly agents, and Lex knowledge bases. I could not independently confirm the early-trigger bug report.

### Cited Findings
- Claude: RAG is automatic and cannot be controlled by users, and can switch back when knowledge drops below the threshold. This is still current. — [Anthropic Help](https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects)
- ChatGPT Projects files: Free 5, Go/Plus 25, Pro/Business/Enterprise 40. One source says this matches OpenAI documentation as of Oct 8, 2026. An older OpenAI help mirror still lists Plus at 20, which is now outdated. Edu at 40 is based on community reports only, and Team is unclear. **[secondary]** — [fast.io](https://fast.io/resources/chatgpt-projects-file-limit/); [elephas](https://elephas.app/resources/chatgpt-projects-limits-files-size-and-how-many-projects); [OpenAI File uploads FAQ mirror](https://help-lb.openai.com/en/articles/8555545-file-uploads-faq)
- ChatGPT per-file limits: 512 MB per file, about 2M tokens per text or document file, about 50 MB for spreadsheets, and 10 files per upload batch. Storage figures conflict: 25 GB per user and 100 GB per org versus 10 GB. **[secondary]** — [fast.io: max file size](https://fast.io/resources/chatgpt-max-file-size.md); [ai-toolbox](https://www.ai-toolbox.co/chatgpt-management-and-productivity/chatgpt-file-upload-limits-2026)
- Perplexity limits are **conflicting**. Per-file cap: 40 MB (Otio, citing Perplexity's File Uploads help as checked Oct 11, 2026), versus 25 MB (fast.io), versus 50 MB (a TXST university page). Files per Space: 50 (Pro, per fast.io and TXST). Enterprise Pro: 30 files per thread upload, 100 thread uploads a week, 500 files per Space, and 15,000 persistent files per user. **[all secondary]** — [otio](https://otio.ai/blog/perplexity-file-upload-limit-for-research); [fast.io](https://fast.io/resources/perplexity-spaces-file-limit/); [Perplexity AI Magazine (June 2026)](https://perplexityaimagazine.com/perplexity-hub/perplexity-file-upload-limit-2026/); [TXST](https://doit.txst.edu/txstai/txsttools/perplexity.html)
- TXST notes that the Perplexity free trial or pilot ended June 1, 2026. — [TXST](https://doit.txst.edu/txstai/txsttools/perplexity.html)
- Dated changes from 2025–2026:
  - Apr 2025: ChatGPT chat-history memory; Copilot Memory.
  - May 2025: Lex knowledge bases and style guides.
  - Aug 2025: Grammarly agents, including Citation Finder and AI Detector.
  - Sept/Oct 2025: Claude memory for Team/Enterprise (Sept) and Pro/Max (Oct 23), plus incognito.
  - Oct 2025: ChatGPT company knowledge.
  - Jan 8, 2026: Gemini API file limit raised to 100 MB **[secondary]**.

  Sources are cited in the sections above.

### Inferences
- Vendors are converging on the same design: auto-retrieval past the context window, project-scoped memory, and incognito modes. The open space for ByAuthorX is local verification and transparency, meaning showing which chunks were used and checking that citations actually resolve to text in the user's files. Volume limits are not the place to differentiate.

### Gaps
- I could not find or confirm the Claude "early RAG triggering" bug report in this pass. Keep it as baseline and unverified.
- I could not confirm official per-plan Perplexity Spaces limits, Gemini Gem knowledge limits (the commonly cited figure of 10 files per Gem was **not** verified here), or Claude project file caps per plan.
- Privacy and training opt-out settings per vendor (for example, the September 2025 change to Anthropic's consumer training policy, ChatGPT data controls, Perplexity's AI data retention toggle) were not researched in this pass.
