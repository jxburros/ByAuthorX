# Context engine research (October 2026)

This research looks at context, retrieval, citation, grounding and retrieval decision models, with and without AI. It asks how ByAuthorX can become the best word processor for finding and trusting its users' research. The [context engine implementation plan](../../context-engine-plan.md) turns the findings into work.

| Document | What it covers |
| --- | --- |
| [Retrieval and citation report](retrieval-and-citation-report.md) | ByAuthorX 0.2.0 against the state of the art. Covers hybrid retrieval, chunking, reranking, the R0–R4 router, the grounding cascade, the quick wins Q1–Q10, the medium-term and differentiator roadmap, licensing and the evaluation plan |
| [Competitor report](competitor-report.md) | About 35 more products: office suites, AI assistants, academic tools, PKM and reading apps, fiction tools and local RAG apps. It also covers independent studies of citation accuracy, along with the roadmap additions Q11–Q14 and the revisions to Q1, the router and the cascade |
| [notes/foundations/](notes/foundations/) | Researcher notes behind the first report: a code audit with `file:line` references, RAG state of the art, citation and grounding, deterministic search and knowledge tools, context engineering and decision models, and the first competitor pass |
| [notes/competitors/](notes/competitors/) | Researcher notes behind the competitor report |

## How to read it

- **Vendor or secondary claims.** The reports label claims as vendor-reported, secondary-sourced or unverified. Do not quote a figure externally without checking its label and source.
- **Line references.** `file:line` references describe the code at version 0.2.0 (commit `48ca3e3`). They will drift as the plan is implemented.
- **Licensing.** Licenses for models and libraries are "as reported" in the research. Verify each one before adding a dependency (see the plan's dependency gate).
- **Benchmarks.** Most headline numbers in the field are vendor-run. The plan's first deliverable is an in-house evaluation harness that produces our own numbers.
