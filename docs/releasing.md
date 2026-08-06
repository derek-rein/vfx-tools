# Releasing EXR Converter

How versions are bumped, tagged, and published. Canonical automation lives in the **Makefile** and **`.github/workflows/release.yml`**. Use **`gh`** to watch CI and inspect GitHub Releases.

Related docs: [plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md) (future codec work — not part of the release machinery).

---

## Overview

```text
feature commits on main
        │
        ▼
 make release PART=patch|minor|major
        │  1. scripts/bump_app_version.py → pyproject + APP_VERSION
        │  2. uv lock
        │  3. commit: "release: X.Y.Z"  (only version files)
        │  4. tag: vX.Y.Z
        │  5. push branch + tag  (unless PUSH=0)
        ▼
 GitHub Actions “Release” workflow (on tag v*)
        │  lint → test (3 OS) → gate → Nuitka builds → sign → GitHub Release
        ▼
 Artifacts: Linux AppImage, macOS DMG (arm64 + x86_64), Windows installer
```

Tags are plain semver: **`v1.2.3`**. The tag is the source of truth for the published app version (the workflow also injects `APP_VERSION` from the tag during the Nuitka build).

---

## Prerequisites

- Clean **intent**: all feature work committed (or stashed) *before* `make release`.  
  The release target only stages:
  - `pyproject.toml`
  - `src/core/constants.py` (`APP_VERSION`)
  - `uv.lock`
- `uv` available; `scripts/bump_app_version.py` is plain Python 3.
- Push access to `origin` and permission to create tags.
- [GitHub CLI](https://cli.github.com/) (`gh`) authenticated for status / release inspection:

  ```bash
  gh auth status
  # expect: Logged in to github.com
  ```

---

## Everyday release (recommended)

From a clean tree with feature work already on `main` (or your release branch merged):

```bash
# Optional: see current version
python3 scripts/bump_app_version.py show
# → VERSION="0.3.0"
# → TAG="v0.3.0"

# Dry-run bump only (no files written)
python3 scripts/bump_app_version.py bump patch --dry-run

# Full release: bump + commit + tag + push (triggers CI)
make release PART=patch          # 0.3.0 → 0.3.1
make release PART=minor          # 0.3.0 → 0.4.0
make release PART=major          # 0.3.0 → 1.0.0
```

Defaults:

| Variable | Default | Meaning |
|----------|---------|---------|
| `PART` | `patch` | Which semver segment to increment |
| `PUSH` | `1` | Push branch + tag to `origin` |

Local-only tag (no push — push yourself when ready):

```bash
make release PART=patch PUSH=0
git push origin HEAD
git push origin vX.Y.Z
```

Version-only bump without git (review, then commit yourself):

```bash
make bump PART=patch
# edits pyproject.toml, constants.py, runs uv lock
```

---

## What `make release` does (step by step)

Implemented in the Makefile `release` target:

1. **`scripts/bump_app_version.py bump $(PART)`**  
   - Reads `version = "…"` from `pyproject.toml`  
   - Writes new semver there  
   - Syncs `APP_VERSION = "…"` in `src/core/constants.py`  
2. **`uv lock`** — refresh lockfile if needed  
3. **`scripts/bump_app_version.py show`** — export `VERSION` / `TAG` for the shell  
4. **`git add`** only the three version-related files  
5. **`git commit -m "release: $${VERSION}"`**  
6. **`git tag "v$${VERSION}"`** (annotated? lightweight — plain `git tag`)  
7. If `PUSH=1`: **`git push origin HEAD`** and **`git push origin "v…"`**  

The Release workflow starts on **tag push** (`on.push.tags: v*`).

---

## Choosing patch vs minor vs major

| Bump | Use when |
|------|----------|
| **patch** | Fixes, CI, docs-only, no new user-facing capabilities |
| **minor** | New codecs/presets, visible feature work, honest label changes that add options |
| **major** | Breaking CLI/API or intentional 1.0-style cut |

Examples from this project’s history: codec expansions and pipeline features have shipped as minor/patch releases via the same `make release` path.

---

## After push: GitHub CLI

### Watch the Release workflow

```bash
# Runs for this repo (must be inside the clone or pass -R owner/repo)
gh run list --workflow=Release --limit 5

# Follow the newest run live
gh run watch

# Or open in the browser
gh run view --web
```

If a run fails:

```bash
gh run list --workflow=Release --limit 3
gh run view <run-id> --log-failed
```

### Inspect published releases

```bash
gh release list --limit 10
gh release view v0.4.0
gh release view v0.4.0 --web
```

Download an asset:

```bash
gh release download v0.4.0 --pattern '*.dmg' --dir /tmp/exr-assets
```

### CI on pull requests / main

```bash
gh run list --workflow=CI --limit 5
gh pr checks   # when on a PR branch
```

Release **gate** (must be green before Nuitka): lint + full pytest on Linux, macOS, and Windows. A red gate aborts the release; no artifacts are published.

---

## Release workflow contents (summary)

File: [`.github/workflows/release.yml`](../.github/workflows/release.yml)

| Job | Role |
|-----|------|
| `lint` | Ruff check + format |
| `test` | Full pytest (unit + integration) on ubuntu / macos / windows |
| `gate` | Fails the whole release if lint or any test matrix cell failed |
| `build` | Nuitka standalone → AppImage / DMG / Windows setup (matrix) |
| (sign / publish) | Cosign + GitHub Release upload (see workflow for current steps) |

Build matrix (as of this writing):

- Linux x86_64 → AppImage  
- macOS arm64 → DMG  
- macOS x86_64 → DMG  
- Windows x86_64 → installer  

Nuitka is also available locally: `make bundle` (does **not** publish a GitHub Release).

---

## Checklist before `make release`

- [ ] Feature commits are done and tested (`make test` or rely on CI after push)  
- [ ] Working tree has no *other* unstaged changes you still need (release only commits version files)  
- [ ] `PART` chosen correctly (patch / minor / major)  
- [ ] You intend to trigger a full multi-OS Nuitka build (time + minutes on Actions)  
- [ ] After push: `gh run watch` until green; confirm `gh release view vX.Y.Z`  

---

## Troubleshooting

| Symptom | What to do |
|---------|------------|
| `No changes to commit` from `make release` | Version files already match the bump target, or bump failed; run `python3 scripts/bump_app_version.py show` and check `git status` |
| Tag already exists | Bump again, or delete local tag only if it was never pushed (`git tag -d vX.Y.Z`) — **never** force-push tags that already built a public release without a deliberate process |
| Gate red | Fix tests/lint on `main`, push a fix commit, then either retag carefully or cut `PART=patch` again — prefer a new patch version over rewriting history |
| Wrong version in the GUI binary | Confirm tag is `vX.Y.Z` and the workflow’s “Inject version from tag” step ran; `APP_VERSION` in the tagged commit should match |
| Need a release without pushing yet | `make release PUSH=0`, then push branch + tag when ready |

---

## Quick reference

```bash
# Status
python3 scripts/bump_app_version.py show
git tag -l 'v*' --sort=-v:refname | head
gh release list --limit 5
gh run list --workflow=Release --limit 3

# Ship a patch
make release PART=patch

# Ship a minor (new features / codecs)
make release PART=minor

# Watch
gh run watch
```
