# Notes for Claude

## Version bumps

Whenever you are about to open a pull request, or the user asks for one, ask whether the change needs a
version bump, and which kind (patch, minor or major), before creating it. Do not assume either way.

When a bump is wanted, change every copy together (`tests/test_release_metadata.py` enforces this):

- `src/byax/__init__.py` (the source of truth), `pyproject.toml`, `packaging/windows.iss`
- the version line in `README.md`
- `uv.lock`, by running `uv lock`
- `CHANGELOG.md`: move the `[Unreleased]` entries under a new `## [x.y.z] - date` heading
