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

`main` is **branch-protected**: direct pushes are rejected (required status checks + **changes must be made through a pull request**). Do **not** rely on `make release` with `PUSH=1` while on `main` — the version commit may land locally and the push will fail.

### Protected `main` (current process)

```bash
# 0. Start from up-to-date main, clean tree
git checkout main
git pull origin main

# 1. Feature work (optional separate commits), then cut a release branch
git checkout -b release/X.Y.Z

# 2. Bump + commit + local tag only (do not push main)
make release PART=minor PUSH=0    # or patch / major
# → commit "release: X.Y.Z" + local tag vX.Y.Z

# 3. Push the branch and open a PR into main
git push -u origin HEAD
gh pr create --base main --title "release: X.Y.Z" --body "…"

# 4. Wait for CI on the PR
gh pr checks
# or: gh run watch

# 5. Merge when green (squash or merge per repo preference)
gh pr merge --merge          # or --squash

# 6. Update local main, ensure tag points at the release commit on main
git checkout main
git pull origin main
# If the tag still points at the pre-merge commit SHA, move it only if
# that commit is an ancestor of main (typical for merge commits):
git tag -d vX.Y.Z 2>/dev/null || true
git tag vX.Y.Z
git push origin vX.Y.Z       # triggers Release workflow (Nuitka + GitHub Release)

# 7. Watch publish
gh run watch
gh release view vX.Y.Z
```

Historical example: `release/0.3.0` → PR #1; `release/0.4.0` → PR #2.

### If `main` were unprotected (not our case)

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

### Make variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `PART` | `patch` | Which semver segment to increment |
| `PUSH` | `1` | Push branch + tag to `origin` — use **`PUSH=0`** under branch protection |

Local-only tag (no push — push yourself when ready):

```bash
make release PART=patch PUSH=0
git push origin HEAD            # only works if the branch is not protected
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

## Checklist before shipping

- [ ] Feature commits are done and tested (`make test` or rely on PR CI)  
- [ ] On a **`release/X.Y.Z` branch** (not a direct push to protected `main`)  
- [ ] Working tree has no *other* unstaged changes you still need (release only commits version files)  
- [ ] `PART` chosen correctly (patch / minor / major)  
- [ ] `make release … PUSH=0`, then `gh pr create`  
- [ ] PR CI green → merge → `git push origin vX.Y.Z`  
- [ ] `gh run watch` until Release workflow green; confirm `gh release view vX.Y.Z`  

---

## Troubleshooting

| Symptom | What to do |
|---------|------------|
| `GH013` / *Changes must be made through a pull request* | Expected on protected `main`. Use `release/X.Y.Z` + PR; `make release PUSH=0` |
| *5 of 5 required status checks are expected* | Push was blocked before CI could run on `main`. Ship via PR so checks run on the PR head |
| `No changes to commit` from `make release` | Version files already match the bump target, or bump failed; run `python3 scripts/bump_app_version.py show` and check `git status` |
| Tag already exists locally but not on remote | After merge: retag the commit on `main` if needed, then `git push origin vX.Y.Z`. **Never** force-push tags that already built a public release without a deliberate process |
| Gate red on Release workflow | Fix on a follow-up commit / patch version; prefer a new patch over rewriting a published tag |
| Wrong version in the GUI binary | Confirm tag is `vX.Y.Z` and the workflow’s “Inject version from tag” step ran; `APP_VERSION` in the tagged commit should match |
| Need a release without pushing yet | `make release PUSH=0`, then PR + push tag when ready |

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
