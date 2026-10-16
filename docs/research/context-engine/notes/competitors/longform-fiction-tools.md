# Long-form and fiction writing tools: research material, story knowledge, search and AI retrieval (as of 16 Oct 2026)

Research date: 2026-10-16. Roughly 25 search/fetch calls. "Primary" means the vendor's own site, release notes, changelog or forum staff. "Secondary" means reviews, aggregators or user posts. Several aggregator pages (aitoolsatlas, automateed, aimojo, marlvel, sudowrite.com blog posts about competitors) are affiliate or SEO pages, possibly AI-written, and are flagged where used. The baseline already known to the coordinator (Novelcrafter Codex mention detection and context order, NovelAI Lorebook triggers, Sudowrite Story Bible drift past ~80K words) is not re-researched here. Only the 2026 deltas are covered.

## Which story-knowledge and research-binder features are best-in-class and worth copying in ByAuthorX?

### Takeaway
The market has split. The big organizer and outliner tools (Scrivener, Plottr, Dabble, Ulysses, and apparently Campfire) chose deliberately in 2024-2026 not to build generative AI, or to rely only on OS-level AI. They compete on deterministic structure: collections/saved searches, backlinks, entity tracking, timelines and family trees, book bibles. The AI-native tools (Sudowrite, Novelcrafter, LivingWriter) keep improving visibility and control over which story knowledge the AI sees: per-entry hide toggles, live "which entries the AI is referencing" indicators, case-sensitive and excluded-phrase mention matching. None of them offers real semantic retrieval over research files with citations back to source notes. That is the clearest open niche for a local-first app with a source library.

### Cited Findings

**Scrivener 3 (Literature & Latte), deterministic features**
- Collections come in two types: standard collections (hand-picked documents) and saved-search collections (live re-runnable search results, "like a smart folder") — [Kirkville (secondary, Kirk McElhearn, author of Take Control of Scrivener 3)](https://kirkville.com/how-to-use-scriveners-collections-to-access-groups-of-files-or-search-results-quickly/)
- Project Search can be scoped to titles, text, synopses, notes, keywords and so on. Match modes are any word, all words, exact phrase, whole word and regular expressions, with case sensitivity options — [Kirkville (secondary)](https://kirkville.com/page/47/)
- Bookmarks (new in v3) sit in the Inspector and can point to documents in the project, files on disk, or URLs — [MacStories review of Scrivener 3 (secondary)](https://www.macstories.net/reviews/scrivener-3-for-macos-is-more-flexible-and-powerful-than-ever-for-long-form-writing/); [Take Control of Scrivener 3 (secondary)](https://leanpub.com/tco-scrivener-3)
- Linguistic Focus highlights dialogue or parts of speech (adverbs, adjectives and so on), and is described as Mac-only — [Kirkville / Take Control summary (secondary)](https://kirkville.com/how-to-use-scriveners-collections-to-access-groups-of-files-or-search-results-quickly/)
- Web pages imported into Research are archived snapshots, not live pages. A forum reply says this is by design ("to archive them, as they were when you did so") — [L&L forum: Better web pages in Research](https://forum.literatureandlatte.com/t/better-web-pages-in-research-shared-research/32994)
- **PDF search is weak.** Project search generally cannot highlight hits inside PDFs. On iOS, PDF text is not indexed at all unless the PDF was imported on the Mac. Users fall back to pasting PDF text into notes, Spotlight, or DEVONthink — [L&L forum: Searching for text in Research PDF](https://forum.literatureandlatte.com/t/searching-for-text-in-research-pdf/46155); [L&L forum: search 'accounting' in a PDF](https://forum.literatureandlatte.com/t/is-there-a-way-to-search-for-example-the-word-accounting-in-a-pdf-file/125276); [L&L forum: research data across many projects](https://forum.literatureandlatte.com/t/access-research-data-for-many-writing-projects/32041)
- Research cannot be shared across projects. The thread asks for "shared Research" between projects — [L&L forum](https://forum.literatureandlatte.com/t/better-web-pages-in-research-shared-research/32994)

**Scrivener, AI**
- In the "Adding AI tools to Scrivener" thread (Mar 2023 to Jan 2024), L&L-affiliated poster kewms said: "We are not interested in any form of subscription model". They named the difficulty of choosing a provider ("we'd inevitably choose the 'wrong' option for some people"), the risk that vendors abandon integrations, and copyright ("We very much do not want to need to become international copyright experts") — [L&L forum thread](https://forum.literatureandlatte.com/t/adding-ai-tools-to-scrivener/133222)
- No official Scrivener AI feature or announced AI roadmap found as of Oct 2026. Reviews still describe it as a $59.99 one-time-purchase app with no built-in AI — [Elephas review (secondary, vendor of a competing AI tool)](https://elephas.app/blog/scrivener-review); [Capterra (secondary)](https://www.capterra.com/p/180597/Scrivener/)

**Ulysses (Ulysses GmbH), official release notes**
- v37 (3 Mar 2025): the Dashboard lists **backlinks**, meaning sheets that "link to the current sheet", and can jump to the link inside that sheet. Note and image attachments show in the Overview, and dragging text to the Dashboard creates a note — [Ulysses release notes](https://ulysses.app/release-notes/)
- v35 (4 Jun 2024): links to any heading within a sheet, including in other sheets. Internal links survive export/import via Markdown, DOCX and HTML — [Ulysses release notes](https://ulysses.app/release-notes/)
- v29 (Dec 2022): Projects gained per-project keywords, export settings and goals. Material sheets are a separate class of sheets in projects, and Aeon Timeline integration covers "projects and material sheets" (v31, Jul 2023) — [Ulysses release notes](https://ulysses.app/release-notes/)
- v39 (17 Nov 2025): Find Sheet / Find Group App Intents with configurable filters, "Search in Ulysses" with a preset term, Quick Open and search reaching archived projects, and Revision Mode ("advanced text check") moved into the main dashboard — [Ulysses release notes](https://ulysses.app/release-notes/)
- v40 (5 May 2026): Projects, groups and filters got a dedicated Edit view and a redesigned filter condition editor. Editor search added "Whole Words" and "Begins with" — [Ulysses release notes](https://ulysses.app/release-notes/)
- v41 (14 Sep 2026): a "Writing Tools/Siri AI" toolbar button and Siri on-screen awareness ("can be turned off on macOS"). New projects can start from a template, and Open Quickly got faster. Ulysses' AI is Apple's OS-level AI (first "ready for Writing Tools" in v36, 30 Oct 2024), not an in-house assistant. Latest is v41.2 (6 Oct 2026) — [Ulysses release notes](https://ulysses.app/release-notes/)
- Revision Mode's grammar/style check (since v20, 2020) covers 20+ languages. It is a server-side text checker, and earlier notes mention its reliability without internet — [Ulysses release notes](https://ulysses.app/release-notes/)

**Plottr**
- "Plottr in 2026" (published 8 Jan 2026, updated 19 May 2026): Plottr says it is **not** building AI features and wants to stay "human-centered, human-focused, human-crafted". 2025 releases were Storysnap (book bibles and virtual beta readers), Family Tree, Idea Board, Bookmarks, pinned preview panels, Offline Beta v2, and the Idea Threads capture app. The 2026 plan is to refine existing features, not add many — [Plottr in 2026](https://plottr.com/plottr-in-2026/)
- This reverses an earlier plan. In "Plottr's AI Future" (11 Mar 2024, updated 23 Dec 2024), CEO Cameron Sutter announced experimental AI brainstorming: random character/scene/place ideas, filling a character sheet, and summarizing your book — [Plottr's AI Future](https://plottr.com/plottrs-ai-future/)
- Whether those experimental AI features shipped and were then removed was not confirmed (see Gaps). Note also that "virtual beta readers" in Storysnap sounds AI-like, but the post does not say so.

**Dabble**
- Official "Dabble is AI-free" page (undated): "Dabble will never put words on the page for you". No chatbot, generation, rewriting, summarizing or autocomplete. Plot Grid and Story Notes "organize, track, and plan". Writing is never sent to a generative model. The only exception is Read to Me, which uses browser/OS TTS voices — [Dabble docs](https://www.dabblewriter.com/docs/reference/dabble-is-ai-free)
- Story Notes are described as a place for research, character profiles and world details. This comes from aggregator listings — [SoftwareAdvice AU (secondary)](https://www.softwareadvice.com.au/software/604739/Dabble)

**Campfire Writing**
- Update 41 is called "our first update of 2026", with a "major rework to the Encyclopedia Module" and panel upgrades. Update 42 added custom attributes, panel effects, a new projects dashboard and a theme editor. These details come from search-result snippets of campfirewriting.com update posts; the exact URLs and dates were not fetched, so treat them as secondary — [Campfire update posts index example](https://campfirewriting.com/learn/update37)
- The mobile app's last update was ~29 Jun / 2 Jul 2026 (v1.3.2) — [AppFollow listing (secondary)](https://apps.appfollow.io/ios/campfire-write-your-book/1626123915?country=mx)
- No official Campfire AI policy was found. An "AI writing assistant" request exists on Campfire's public feedback board, which implies no built-in assistant — [Campfire Upvoty feature request](https://campfiretechnology.upvoty.com/b/feature-requests/ai-writing-assistant)

**World Anvil**
- On its community voting board, World Anvil declined a user request for an AI novel writer, saying "AI features are not within the scope of World Anvil". The page is undated — [World Anvil suggestion](https://www.worldanvil.com/community/voting/suggestion/182ee13a-ceb8-4bb8-b53d-904bfb2a326e/view)
- Claims that World Anvil has an "AI Sage" that expands stub articles and AI article/timeline generators come **only** from a Sudowrite marketing blog (a competitor) and contradict the official reply above. They are **unverified and possibly fabricated** — [Sudowrite blog (secondary, competitor)](https://sudowrite.com/blog/what-is-the-best-ai-for-worldbuilding-we-tested-the-top-tools/)
- Community members object to AI art and AI-written articles winning World Anvil competitions — [World Anvil suggestion thread](https://www.worldanvil.com/community/voting/suggestion/7fa6274f-1432-4364-af8c-b2c63eefe836/view)

**LivingWriter (secondary only)**
- Cloud-based. "Smart linking" of characters, locations and objects in story elements. Standard tier has story-element management and a story bible. Premium adds AI Element Generation (with images), AI Chat with the manuscript, chapter/element-level AI Analysis, AI Rewrite and AI Outlines. The pricing data was "last verified March 2026" — [aitoolsatlas (secondary, aggregator)](https://aitoolsatlas.ai/tools/living-writer/pricing); [automateed (secondary, affiliate)](https://automateed.com/livingwriter). No primary LivingWriter docs were fetched, and how its manuscript chat chooses context is undocumented in the sources found.

**Atticus**
- No 2026 Atticus release notes or AI/context features were found. 2026 third-party comparisons still position it as a formatting tool ("Atticus formats, Scrivener organizes") — [builtwritten.com (secondary, competitor marketing)](https://www.builtwritten.com/blog/category/ai-book-tools)

**Sudowrite (2026 deltas, official changelog)**
- 29 Jan 2026: the Story Bible became collapsible, and "the AI still sees everything—you just don't have to" — [Sudowrite changelog](https://feedback.sudowrite.com/changelog/collapsible-story-bible)
- Apr 2025 (baseline context): Story Bible Detection in Draft gives "real time visibility into which Characters and Worldbuilding elements the AI is referencing". An eye icon hides a card or trait from the AI — [Sudowrite changelog](https://feedback.sudowrite.com/changelog/mag-story-bible-detection-and-visibility-update)
- Aug to Oct 2026:
  - Character Portraits (25 Sep) and Worldbuilding Images (7 Oct) generate images from Story Bible trait fields.
  - Chat is "twice as fast" with ~30% fewer errors "including fewer dropped-context issues" (22 Sep), and Rewrite moved to the in-house "Ballad" model.
  - New models (GPT-6 Sol/Astra/Luna, GPT-6.1 Sol, Opus 5.5, Sonnet 5.5, Gemini 3.7/3.8 Flash) are marketed specifically on "Story Bible adherence" and "continuity across chapters". Gemini Flash is pitched for "full-manuscript" Draft work because of its large context.
  - Chat "reads and inserts documents more reliably" (14 Aug).
  - [Sudowrite changelog](https://feedback.sudowrite.com/changelog)
- Sudowrite's blog describes a Series Folder that shares a Story Bible across books (not clearly dated) — [Sudowrite blog](https://sudowrite.com/blog/how-sudowrite-works/)
- Claim of "Muse 1.5" replacing the original Muse in 2026 — [aimojo (secondary, affiliate, low confidence)](https://aimojo.io/sudowrite-muse-1-5/)

**Novelcrafter (2026 deltas, official changelog)**
- 9 Jan 2026: opt-in experimental reasoning support. Thoughts are not shown or stored — [Novelcrafter changelog](https://www.novelcrafter.com/changelog/)
- 27 Jan 2026: "Codex entries are now detected faster while writing" — [Novelcrafter changelog](https://www.novelcrafter.com/changelog/)
- 21 Mar 2026: per-entry case-sensitive matching (to cut false mentions of names that are common words), excluded phrases (for example sentence-initial matches), multi-category entries, and a mention preview card toggle — [Novelcrafter changelog](https://www.novelcrafter.com/changelog/)
- 24 Sep 2026: "Big Organization Update" for acts/chapters/scenes. Codex extraction from Chat keeps nested lists, and Codex entries show created/updated dates — [Novelcrafter changelog](https://www.novelcrafter.com/changelog/)
- 2 Oct 2026: per-thread one-off model choice in Chat, and a fix to `pov` prompt functions for multi-POV scene beats — [Novelcrafter changelog](https://www.novelcrafter.com/changelog/)
- No 2026 entries cover semantic retrieval, local/offline mode or privacy — [Novelcrafter changelog](https://www.novelcrafter.com/changelog/)

### Inferences
- **Best-in-class deterministic features worth copying:**
  - Scrivener's saved-search collections (live smart folders with regex and scope options).
  - Ulysses' Dashboard backlinks plus heading-level links that survive DOCX/Markdown export.
  - Plottr's timeline, Family Tree and pinned preview panels (the reference panel stays visible while writing).
  - Novelcrafter's mention detection with case-sensitivity and excluded phrases.
  - Sudowrite's live "which entries the AI is using" indicator plus per-trait hide-from-AI toggles.
  - A ByAuthorX codex should combine these: structured entries, auto-detected mentions with false-positive controls, a backlinks pane, and a visible "AI context" panel the writer can override.
- **Largest open gap for a local-first app with a source library:** full-text and semantic search *inside* PDFs and archived web pages, shared across projects. Scrivener users hit exactly this wall and go to DEVONthink or Spotlight. None of the AI tools reviewed retrieves from research files with citations back to the source passage. This is a credible differentiator.
- **Positioning:** Plottr, Dabble, Scrivener, and apparently Campfire and World Anvil use "no AI" as a feature, or let users opt out, as Ulysses does with the Siri AI toggle. A sizable segment of writers distrusts AI. ByAuthorX should make the AI assistant fully optional and off by default for manuscript text, offer local models, and state no-training/no-upload guarantees in Dabble-style plain language.
- **Plottr's 2024 to 2026 reversal** (announced AI brainstorming, then publicly disavowed AI) suggests strong customer backlash risk for generation features in planning tools. Retrieval, lookup and continuity checking are probably more acceptable than generation. This is an inference, not stated by Plottr.
- **Sudowrite's 2026 strategy** for long-manuscript continuity is mainly "bigger/better models" (large-context Gemini, "Story Bible adherence" models), not smarter retrieval. That leaves room for deterministic and retrieval-based continuity tooling.

### Gaps
- No official Scrivener manual text was fetched. The Collections, Bookmarks and Linguistic Focus details come from secondary sources (McElhearn, MacStories). Unverified items include Scrivener 3.x point releases from 2025-2026, Windows feature parity for Linguistic Focus, and any 2025-2026 L&L statement on AI.
- Ulysses "material sheets" semantics (exclusion from export/word counts, etc.) are not documented in the release notes beyond their existence and Aeon Timeline integration.
- Whether Plottr ever shipped and then removed its 2024 experimental AI features, and whether Storysnap "virtual beta readers" uses AI, is unconfirmed.
- Campfire: the update 41/42 posts were not fetched directly, so dates are unknown. No AI policy was found. The "State of the Campfire: 2026" post could not be located.
- World Anvil: no primary source for a 2026 AI policy, and the "AI Sage" claim is unverified.
- LivingWriter and Atticus: no primary documentation was reached. How LivingWriter's manuscript chat picks context is unknown.
- Dabble: the Plot Grid / Story Notes search and linking mechanics were not documented in the sources fetched.

## What unmet needs do fiction and long-form writers repeatedly express about context, continuity and finding research?

### Takeaway
The recurring complaints are:
- Research inside PDFs and web archives can't be found, and research can't be shared across projects (Scrivener).
- AI tools forget or contradict established character and plot details over long manuscripts.
- Story bibles take too much upkeep and go unused because they sit apart from the draft.
- Writers distrust AI accuracy, copyright and data handling.

### Cited Findings
- Scrivener users cannot search or highlight text inside imported PDFs reliably, especially on iOS. Workarounds are copying PDF text into notes or using Spotlight or DEVONthink — [L&L forum](https://forum.literatureandlatte.com/t/searching-for-text-in-research-pdf/46155); [L&L forum](https://forum.literatureandlatte.com/t/is-there-a-way-to-search-for-example-the-word-accounting-in-a-pdf-file/125276)
- Users want research shared across multiple projects and live (not archived) web pages — [L&L forum](https://forum.literatureandlatte.com/t/better-web-pages-in-research-shared-research/32994); [L&L forum](https://forum.literatureandlatte.com/t/access-research-data-for-many-writing-projects/32041)
- A user requested excluding documents/folders from project search, implying noisy results in large projects — [L&L forum](https://forum.literatureandlatte.com/t/exclude-documents-folders-from-search/136127)
- A Windows user reported project search missing results in a large research project. This is an unconfirmed single report — [L&L forum](https://forum.literatureandlatte.com/t/text-search-in-large-projects/22889)
- On AI, Scrivener forum users complain of hallucinated sources (ChatGPT inventing a book and an ISBN), generic or outdated answers, and AI output that always needs rewriting. Others want AI for brainstorming and worldbuilding with pay-as-you-go pricing — [L&L forum thread](https://forum.literatureandlatte.com/t/adding-ai-tools-to-scrivener/133222)
- Summarized Sudowrite user reviews report the AI "frequently forgets character details and plot points established in the outline" — [Marlvel review summary (secondary aggregator)](https://marlvel.ai/apps/sudowrite-ai-novel-writing/reviews)
- A vendor launch post calls a character's eye color changing by chapter 3 "the #1 complaint we heard from writers using AI tools". This is self-interested marketing — [Product Hunt (secondary, vendor)](https://www.producthunt.com/p/novarrium-2/why-does-ai-forget-your-character-s-eye-color-by-chapter-3-we-fixed-it)
- Cross-book continuity is a pain point, for example a character who loses two fingers in book one and uses both hands in book three — [Nerdbot (secondary)](https://nerdbot.com/?p=300317)
- Story bibles get created and then ignored because they live in a different app than the draft — [Novela blog (secondary, vendor)](https://novela.so/en/blog/story-bible-that-gets-used). Some writers find bibles "take more work than I want to do" — [Mad Genius Club](https://madgeniusclub.com/2017/03/14/keeping-track/)
- Sudowrite itself has shipped fixes framed around "dropped-context issues" in Chat (Sep 2026), confirming that context loss is a live problem — [Sudowrite changelog](https://feedback.sudowrite.com/changelog)
- Novelcrafter shipped case-sensitive and excluded-phrase matching (Mar 2026) because names that are common words caused false Codex mentions, so false-positive context injection is a real problem — [Novelcrafter changelog](https://www.novelcrafter.com/changelog/)
- World Anvil community members object to AI-written and AI-art content — [World Anvil suggestion thread](https://www.worldanvil.com/community/voting/suggestion/7fa6274f-1432-4364-af8c-b2c63eefe836/view)

### Inferences
- **Unmet need 1: one searchable research corpus.** Writers want full-text plus semantic search across manuscript, notes, PDFs and archived web pages, shared across projects and series, with hits highlighted in place. ByAuthorX's local source library could directly answer the Scrivener PDF complaints.
- **Unmet need 2: low-maintenance story knowledge.** Codex entries should be created or updated from the manuscript (proposals the writer approves, as in Novelcrafter's Chat-to-Codex extraction), with mention detection and created/updated dates, rather than kept up by hand.
- **Unmet need 3: deterministic continuity checks.** Flag contradictions between codex facts and manuscript text, such as eye color or a missing limb, with citations to the exact passages. This works across books in a series and doesn't depend on a large context window.
- **Unmet need 4: transparency and control over AI context,** with attribution back to notes and sources. Sudowrite shows which entries are referenced, but none of the tools found cite the specific research passage. Pair this with privacy guarantees and local models to address the distrust voiced in Scrivener, World Anvil and Plottr communities.

### Gaps
- Reddit threads (r/scrivener, r/writing, r/fantasywriters, r/WritingWithAI) did not surface in searches, so direct Reddit quotes are missing. Recurring sentiment is drawn from the L&L forum and secondary summaries.
- No quantitative data (surveys, counts) on how often writers raise these needs was found.
