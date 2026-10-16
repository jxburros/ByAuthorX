# ByAuthorX documentation

| If you want to… | Read |
| --- | --- |
| Install and write your first document | [User guide](user-guide.md) |
| Research, review and prepare a deliverable | [Research workflow](research-workflow.md) |
| Verify an installed release on real desktops | [Desktop acceptance checklist](desktop-release-checklist.md) |
| Evaluate usefulness with five target authors | [Author workflow evaluation](author-workflow-evaluation.md) |
| Script or automate the engine | [CLI reference](cli-reference.md) |
| Attach research libraries and cite sources | [Library and citations guide](library-and-citations-guide.md) |
| Configure AI review, chat and dictation | [Providers](providers.md) |
| Understand how views, formats and losses work | [File views](file-views.md), [compatibility](compatibility.md) |
| Fix a problem | [Troubleshooting](troubleshooting.md) |
| Understand the design | [Architecture](architecture.md), [implementation contract](implementation-contract.md) |
| Work on the code | [Development guide](development.md), [CONTRIBUTING](../CONTRIBUTING.md) |
| Build or release installers | [Packaging](packaging.md) |
| Check what is verified | [Acceptance matrix](acceptance-matrix.md), [interface parity](interface-parity.md), [accessibility audit](accessibility-audit.md) |
| Work with the brand and assets | [Brand and motion](brand.md), [design sources](../design/brand/README.md), [brand fonts](../assets/brand-fonts/README.md), [creative library](../assets/creative-library/README.md), [logo companions](../assets/logo-companion-library/README.md), [logo UI library](../assets/logo-ui-library/README.md) |
| Review licensing | [Third-party notices](third-party-notices.md), [citation style licenses](citation-licenses.md) |
| Review decisions and audits | [Co-editing spike](coediting-spike.md), [0.2 issue audit](issue-audit-0.2.md), [14 Oct 2026 issue audit](issue-audit-2026-10-14.md), [spec for libraries and citations](library-and-citations-spec.md) |
| Read the full requirements | [Product specification](../ByAuthorX-Product-Specification%20%281%29.md) |

Release history is in the [changelog](../CHANGELOG.md).

## Keeping docs honest

The project separates *implemented*, *tested offscreen* and *verified on a physical desktop or
live account*. Do not move a claim between those without recording evidence in the
[acceptance matrix](acceptance-matrix.md). When you change behavior, update the doc that owns it:
CLI flags in the [CLI reference](cli-reference.md), formats in [compatibility](compatibility.md),
schema changes in [architecture](architecture.md), and add a [changelog](../CHANGELOG.md) entry.
