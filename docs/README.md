# ByAuthorX documentation

Start with the [user guide](user-guide.md). It takes you from installing to a finished book. **Help → Open sample project** gives you something to practise on.

## Using ByAuthorX

| If you want to… | Read |
| --- | --- |
| Install and write your first document | [User guide](user-guide.md) |
| Attach research and cite sources | [Library and citations guide](library-and-citations-guide.md) |
| Set up AI review, chat and dictation, or keep everything local | [Providers](providers.md) |
| Know what a format keeps and what it loses | [File views](file-views.md), [compatibility](compatibility.md) |
| Understand grammar checking and how accurate it is | [Grammar](grammar.md) |
| Install dictionaries and grammar packs; see translation coverage | [Language packs and translations](translations.md) |
| Add your own checks or export hooks | [Extensions](extensions.md) |
| Fix a problem | [Troubleshooting](troubleshooting.md) |
| See how it works in pictures | [Visual guides and flowcharts](visual-guides/README.md) |

## Automating ByAuthorX

| If you want to… | Read |
| --- | --- |
| Run from source, use the command line, MCP or REST | [For developers and automation](developers.md) |
| Look up a command | [CLI reference](cli-reference.md) |
| Check that every feature is reachable from every interface | [Interface parity](interface-parity.md) |

## Building and contributing

| If you want to… | Read |
| --- | --- |
| Understand the design | [Architecture](architecture.md), [implementation contract](implementation-contract.md) |
| Work on the code | [Development guide](development.md), [CONTRIBUTING](../CONTRIBUTING.md) |
| Build or release installers | [Packaging](packaging.md) |
| Check what is verified | [Acceptance matrix](acceptance-matrix.md), [accessibility audit](accessibility-audit.md), [benchmarks and retrieval baseline](benchmarks/README.md), [published context engine benchmarks](benchmarks/context-engine.md) |
| Write in the ByAuthorX voice | [Voice and messaging](voice.md) |
| Work with the brand and assets | [Brand, design and motion](brand.md), [design sources](../design/brand/README.md), [brand fonts](../assets/brand-fonts/README.md), [creative library](../assets/creative-library/README.md), [logo companions](../assets/logo-companion-library/README.md), [logo UI library](../assets/logo-ui-library/README.md) |
| Review licensing | [Third-party notices](third-party-notices.md), [citation style licenses](citation-licenses.md) |
| Read the full requirements | [Product specification](../PRODUCT-SPECIFICATION.md) |

## Project history and planning

These are working documents for people who build ByAuthorX. They record decisions, plans and audits, not how to use the app.

| Document | What it is |
| --- | --- |
| [Context engine plan](context-engine-plan.md), [research](research/context-engine/README.md), [spec for libraries and citations](library-and-citations-spec.md) | How retrieval, citation and grounding were designed |
| [Competitiveness plan](competitiveness-plan.md) | Manuscript projects, publishing, interchange, grammar and reach |
| [Distinctive format ideas](distinctive-format-ideas.md) | Ideas that would make the `.byax` format stand apart |
| [Co-editing spike](coediting-spike.md), [0.2 issue audit](issue-audit-0.2.md), [14 Oct 2026 issue audit](issue-audit-2026-10-14.md) | Decisions and audits |

Release history is in the [changelog](../CHANGELOG.md).

## Keeping docs honest

The project separates *implemented*, *tested offscreen* and *verified on a physical desktop or
live account*. Do not move a claim between those without recording evidence in the
[acceptance matrix](acceptance-matrix.md). When you change behavior, update the doc that owns it:
CLI flags in the [CLI reference](cli-reference.md), formats in [compatibility](compatibility.md),
schema changes in [architecture](architecture.md), and add a [changelog](../CHANGELOG.md) entry.
