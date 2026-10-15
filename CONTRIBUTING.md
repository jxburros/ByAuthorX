# Contributing to ByAuthorX

Thanks for helping. Start with the [development guide](docs/development.md) for setup, layout,
tests and the CI gates, and the [architecture](docs/architecture.md) for the design rules.

1. Open or pick an issue; for behavior changes check the [product specification](ByAuthorX-Product-Specification%20%281%29.md)
   and the [acceptance matrix](docs/acceptance-matrix.md).
2. Branch from `main`, keep changes focused, and add tests. Run the CI gates locally:
   `ruff format --check .`, `ruff check src tests`, `mypy`, and `pytest` with `QT_QPA_PLATFORM=offscreen`.
3. Update the docs you affected (see [docs/README.md](docs/README.md#keeping-docs-honest)) and add a
   [CHANGELOG](CHANGELOG.md) entry under a new version heading when releasing.
4. Open a pull request describing what changed and how you verified it. State plainly what was *not* verified
   (physical desktops, live provider accounts).

Security issues: do not open a public issue with exploit details; contact the maintainer privately. Contributions
are licensed under the project's MIT [license](LICENSE).
