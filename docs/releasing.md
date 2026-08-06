# Releasing EXR Converter

The full release and deployment process (protected `main`, Makefile, GitHub
Actions, `gh` CLI, checklist, troubleshooting) lives in:

**[AGENTS.md — Releasing and deployment](../AGENTS.md#releasing-and-deployment)**

User-facing history (required on every release):

**[CHANGELOG.md](../CHANGELOG.md)**

**Short path:** merge a release PR that bumps `pyproject.toml` → **Auto-tag
release** pushes `vX.Y.Z` if missing → **Release** builds and publishes. You do
not need a manual tag push after merge (that was what skipped 0.5.0 until fixed).

Related design notes (not release machinery):
[plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md).
