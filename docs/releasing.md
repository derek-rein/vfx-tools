---
title: Releasing
weight: 30
description: Tagging, Auto-tag, and the Release workflow
---

The full release and deployment process (protected `main`, Makefile, GitHub
Actions, `gh` CLI, checklist, troubleshooting) lives in:

**[AGENTS.md — Releasing and deployment](../AGENTS.md#releasing-and-deployment)**

User-facing history (required on every release):

**[CHANGELOG.md](../CHANGELOG.md)**

**Short path:** merge a release PR that bumps `pyproject.toml` → **Auto-tag
release** pushes `vX.Y.Z` if missing → **Release** builds and publishes. You do
not need a manual tag push after merge (that was what skipped 0.5.0 until fixed).

**Docs site:** Markdown under `docs/` is built with Hugo (`site/`) and published
by the **Docs** workflow on push to `main` (path filters under `docs/`, `site/`).
That is independent of the versioned app **Release** workflow. Preview locally
with `make docs-serve`. Public URL:
[derek-rein.github.io/exr-converter](https://derek-rein.github.io/exr-converter/).

Related design notes (not release machinery):
[plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md).
