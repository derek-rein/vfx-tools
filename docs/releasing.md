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

**Short path:** merge a release PR that bumps `pyproject.toml` **and** rolls
`CHANGELOG.md` (`## [X.Y.Z]` + compare link) → **Auto-tag release** pushes
`vX.Y.Z` if missing → dispatches **Release** with
`gh workflow run Release --ref main -f tag=vX.Y.Z` → lint/tests/gate → Nuitka
→ Cosign → GitHub Release.

You do **not** need a manual tag push after merge (that was what skipped 0.5.0
until fixed). After merge, do **not** also push a local `vX.Y.Z` unless Auto-tag
failed — dual triggers race and waste runner minutes.

**Hard gates (automated):** Auto-tag and Release refuse to ship if
`CHANGELOG.md` lacks `## [X.Y.Z]` / the bottom compare link, or if the tag does
not match `pyproject.toml` version (except explicit emergency
`source_ref=` rebuilds).

**Provenance:** each asset gets a Cosign `.sigstore.json` bundle **and** a
GitHub Attestations record (`gh attestation verify`). Release notes include the
CHANGELOG section for that version plus verify commands. Optional SignPath
Authenticode for Windows is off until repository vars/secrets are set (see
AGENTS.md).

**Docs site:** Markdown under `docs/` is built with Hugo (`site/`) and published
by the **Docs** workflow on push to `main` (path filters under `docs/`, `site/`).
That is independent of the versioned app **Release** workflow. Preview locally
with `make docs-serve`. Public URL:
[derek-rein.github.io/exr-converter](https://derek-rein.github.io/exr-converter/).

Related design notes (not release machinery):
[plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md).
