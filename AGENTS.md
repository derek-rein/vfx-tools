# AGENTS.md — context for coding agents

Instructions for AI agents and humans working in this repository. Keep this
file accurate when process or layout changes in ways that affect how work is done.

## What this project is

**EXR Converter** — desktop GUI + CLI for converting **video ↔ OpenEXR** with
**OpenColorIO**, slate / burn-in / watermark overlays, and Nuitka-packaged
binaries for Linux, macOS (arm64 + x86_64), and Windows.

| Item | Value |
|------|--------|
| Package / app | `exr-converter` / EXR Converter |
| Version source of truth | `pyproject.toml` `version` + `APP_VERSION` in `src/core/constants.py` (kept in sync by `scripts/bump_app_version.py`) |
| Python | **3.13 only** (`requires-python = "==3.13.*"`) |
| Deps / runs | **uv** (`uv sync`, `uv run …`) |
| Entry | `main.py` (GUI with no subcommand; CLI: `video2exr`, `exr2video`) |
| Repo | https://github.com/derek-rein/exr-converter |
| Org settings keys | `QSettings("VFXTools", "EXRConverter")` |

VFX Reference Platform CY2026-ish stack: PySide6 6.8, OCIO 2.5, OIIO, NumPy 2.x, PyAV.

## Changelog (required)

**Every user-visible change must update [CHANGELOG.md](./CHANGELOG.md) in the same PR / commit set.**

| Rule | Detail |
|------|--------|
| Where | Add bullets under `## [Unreleased]` using Keep a Changelog sections: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security` |
| Scope | GUI behavior, CLI flags, codecs, OCIO/defaults, packaging that users notice, breaking changes. Skip pure refactors, drive-by renames, and internal-only test plumbing unless they affect contributors. |
| Tone | Short, user-facing sentences. No issue-tracker dump. |
| On release | Move `[Unreleased]` items into a new `## [X.Y.Z] — YYYY-MM-DD` section, leave an empty `[Unreleased]` stub, update compare links at the bottom of the file. |
| Do not | Ship a tagged release without a matching changelog section. Do not rewrite history of already-published versions except to fix factual errors. |

Agents: if you implement a feature or fix and forget the changelog, treat that as incomplete work and add the entry before finishing.

## Layout (where to edit)

```text
main.py                 # GUI / CLI entry
src/
  core/                 # convert, video, EXR I/O, OCIO, codecs constants, sequences
  gui/                  # PySide6 window, tabs, slate UI, preferences, style
  render/               # slate, burn-in, watermark, tokens (QPainter)
  services/             # worker thread, presets, cache, slate model, prefetch
  rc_resources.py       # generated from resources.qrc — do not hand-edit
resources/              # icons, style.qss, OCIO config, screenshots
scripts/                # bump_app_version.py, ensure_ocio.py
tests/                  # pytest; integration + fixtures under tests/fixtures/
docs/                   # design notes (e.g. oxideav plan); release process lives here in AGENTS.md
.github/workflows/      # ci.yml (PR/main), release.yml (tag v*)
```

### Important modules

| Area | Start here |
|------|------------|
| Convert pipeline | `src/core/convert.py`, `src/core/video.py`, `src/core/exr_io.py` |
| Codec ladder / bit depth labels | `src/core/constants.py` |
| CLI | `src/cli.py` |
| Main window / post-convert actions | `src/gui/window.py` |
| Player prefs / reveal-in-folder | `src/gui/preferences.py` |
| Convert tabs / codecs UI | `src/gui/widgets.py` |
| Slate dialog / viewer | `src/gui/slate_widgets.py`, `src/services/slate_model.py` |
| Background convert | `src/services/worker.py` |

## Commands (local)

```bash
make sync          # uv sync + ensure OCIO 2.5+ linkage
make run           # GUI
make lint          # ruff check
make fmt           # ruff format + fix
make typecheck     # basedpyright
make test          # full pytest (needs QT_QPA_PLATFORM=offscreen — Makefile sets it)
make test-unit     # skip @pytest.mark.integration
make resources     # regenerate src/rc_resources.py after resources.qrc / icons change
make bundle        # local Nuitka standalone (does not publish a GitHub Release)
make clean
```

Prefer `make` / `uv run` over system `python` so the project venv is used.

### OCIO pitfall

Bundled ACES Studio config needs **OpenColorIO 2.5+**. Installing or upgrading
`oiio-python` can rewire `PyOpenColorIO` to a vendored **2.4** dylib. Symptom:
config version 2.5 cannot load on library 2.4. Fix: `make ensure-ocio` or
`make sync` (`scripts/ensure_ocio.py`).

### Qt / tests

- GUI tests and many unit tests need `QT_QPA_PLATFORM=offscreen` (Makefile `test` targets set this).
- Integration tests may skip if optional media is missing from `tests/fixtures/` — missing media is skip, not fail.

## Architecture notes agents should respect

1. **Color pipeline** — Overlays (slate / burn-in / watermark) composite in a
   scene-linear **working / compositing space** (prefer ACES2065-1 via
   `aces_interchange`, else `scene_linear`), then OCIO to display for encode.
   Do not “just paint in sRGB on top of linear” without going through the
   existing helpers.
2. **Video I/O is PyAV** — encode/decode via FFmpeg libs bundled with the `av`
   wheel. **PyAV does not ship `ffplay`.** Do not assume system FFmpeg/ffplay
   exists on user machines. Playback after convert uses the user’s preferred
   player from Preferences (system default or custom path).
3. **Software ProRes is 10-bit** in FFmpeg/`prores_ks`. True cross-platform
   12-bit ProRes is *not* available via current FFmpeg path; see
   `docs/plan-12bit-prores-oxideav.md` for the experimental oxideav plan.
   VideoToolbox ProRes (`prores_vt_*`) is **macOS-only**; UI must keep honest
   bit-depth labels.
4. **Slate is QPainter** — no Qt WebEngine. Do not reintroduce browser-based slate.
5. **Generated resources** — edit `resources.qrc` / files under `resources/`, then
   `make resources`. Do not hand-edit `src/rc_resources.py`.
6. **Presets / settings** — `QSettings` under org `VFXTools` / app `EXRConverter`.
   Post-convert toggles: `ui/copy_path_after` (default **true**), `ui/open_after`,
   `ui/show_folder_after`. Player: `player/mode` (`system`|`custom`), `player/path`.
7. **Nuitka** — Release builds strip many Qt modules (WebEngine, Svg, Pdf, …).
   Prefer PySide6-Essentials APIs already used in the tree. Bundle includes
   `resources/ocio` and package data for `av`, OIIO, OCIO, fileseq.

## Coding conventions

- Match existing style: type hints, `from __future__ import annotations`, Ruff
  line length 100, import order as Ruff formats.
- Prefer small, focused diffs. Do not drive-by-refactor unrelated modules.
- Tests: add unit tests next to behavior when practical; use
  `@pytest.mark.integration` only for real/synthetic media round-trips.
- After TypeScript-less Python work: `make lint` and relevant `make test-unit`
  or targeted `uv run pytest path/to/test.py`.
- Do not commit secrets, large binary fixtures, or local venv paths.

## Releasing and deployment

Canonical automation: **Makefile** (`release`, `bump`) and
**[`.github/workflows/release.yml`](.github/workflows/release.yml)**.
Use **`gh`** to watch CI and inspect GitHub Releases.

User-facing history: **[CHANGELOG.md](./CHANGELOG.md)** (update before tagging).

### Pipeline overview

```text
feature commits (PR → main)
        │
        ▼
 make release PART=patch|minor|major   (on release/X.Y.Z branch, PUSH=0)
        │  1. scripts/bump_app_version.py → pyproject + APP_VERSION
        │  2. uv lock
        │  3. commit: "release: X.Y.Z"  (only version files)
        │  4. tag: vX.Y.Z  (local)
        ▼
 PR → main → merge
        │
 git push origin vX.Y.Z
        ▼
 GitHub Actions “Release” on tag v*
        │  lint → test (3 OS) → gate → Nuitka → Cosign → GitHub Release
        ▼
 Artifacts: Linux AppImage, macOS DMG (arm64 + x86_64), Windows installer
            (+ .sigstore.json bundles)
```

Tags are plain semver: **`v1.2.3`**. The tag is the published app version
(workflow also injects `APP_VERSION` from the tag during the Nuitka build).

### Prerequisites

- Feature work committed (or stashed) before `make release`.
- Release commit stages **only**: `pyproject.toml`, `src/core/constants.py`, `uv.lock`.
- `uv` available; `scripts/bump_app_version.py` is plain Python 3.
- [GitHub CLI](https://cli.github.com/) (`gh auth status`) for checks / releases.
- **`main` is branch-protected** — no direct pushes; use PRs. Do not run
  `make release` with `PUSH=1` on `main`.

### Ship a version (protected main)

```bash
git checkout main && git pull origin main
git checkout -b release/X.Y.Z

# Before tagging: CHANGELOG.md must have [X.Y.Z] notes (move from [Unreleased])
make release PART=minor PUSH=0    # or patch / major
# → commit "release: X.Y.Z" + local tag vX.Y.Z
# Include CHANGELOG.md in the release PR if not already on main
# (bump only commits version files — commit changelog separately on the branch)

git push -u origin HEAD
gh pr create --base main --title "release: X.Y.Z" --body "…"
gh pr checks
gh pr merge --merge          # or --squash per preference

git checkout main && git pull origin main
# If tag still points at pre-merge SHA, retag the release commit on main when needed:
git tag -d vX.Y.Z 2>/dev/null || true
git tag vX.Y.Z
git push origin vX.Y.Z       # triggers Release workflow

gh run watch
gh release view vX.Y.Z
```

Historical: `release/0.3.0` → PR #1; `release/0.4.0` → PR #2.

### Make variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `PART` | `patch` | Semver segment: `patch` \| `minor` \| `major` |
| `PUSH` | `1` | Push branch + tag — use **`PUSH=0`** under branch protection |

Version-only bump (no git): `make bump PART=patch`.

### Choosing patch vs minor vs major

| Bump | Use when |
|------|----------|
| **patch** | Fixes, CI, docs-only, no new user-facing capabilities |
| **minor** | New codecs/presets, visible features, honest label/option expansions |
| **major** | Breaking CLI/API or intentional 1.0-style cut |

### After push: `gh`

```bash
gh run list --workflow=Release --limit 5
gh run watch
gh run view <run-id> --log-failed

gh release list --limit 10
gh release view vX.Y.Z
gh release download vX.Y.Z --pattern '*.dmg' --dir /tmp/exr-assets

gh run list --workflow=CI --limit 5
gh pr checks
```

### Release workflow jobs

| Job | Role |
|-----|------|
| `lint` | Ruff check + format |
| `test` | Full pytest on ubuntu / macos / windows |
| `gate` | Aborts release if lint or any OS test failed |
| `build` | Nuitka → AppImage / DMG / Windows setup |
| sign / publish | Cosign + GitHub Release upload |

Local package only: `make bundle` (no GitHub Release).

### Checklist before shipping

- [ ] Feature work tested (`make test` / PR CI)
- [ ] **[CHANGELOG.md](./CHANGELOG.md)** updated: `[Unreleased]` rolled into `[X.Y.Z]`
- [ ] On branch `release/X.Y.Z` (not direct push to `main`)
- [ ] Working tree free of unrelated unstaged work
- [ ] Correct `PART`; `make release … PUSH=0` → PR → merge → `git push origin vX.Y.Z`
- [ ] `gh run watch` green; `gh release view vX.Y.Z` has artifacts + signatures

### Troubleshooting

| Symptom | What to do |
|---------|------------|
| `GH013` / must use pull request | Use `release/X.Y.Z` + PR; `PUSH=0` |
| Required status checks on main | Ship via PR so `ci-ok` runs on the PR head |
| `No changes to commit` from `make release` | Version already bumped; `python3 scripts/bump_app_version.py show` |
| Tag exists locally not on remote | After merge, retag if needed, `git push origin vX.Y.Z`. Never force-push a published public tag lightly |
| Gate red on Release | Fix forward with a new patch; prefer not rewriting a published tag |
| Wrong version in GUI binary | Tag must be `vX.Y.Z`; check inject-version step and `APP_VERSION` on the tagged commit |
| OCIO 2.4 vs 2.5 in CI/local | `scripts/ensure_ocio.py` / `make ensure-ocio` |

### Artifact verification (users)

Cosign keyless signatures ship as `*.sigstore.json` next to each asset. See README
/ GitHub Release body for `cosign verify-blob` commands.

### macOS Gatekeeper

Builds use **ad-hoc** signature (not Apple notarized). Users clear quarantine:

```bash
xattr -cr "/Applications/EXR Converter.app"
```

## CI

| Workflow | Trigger | Gate |
|----------|---------|------|
| [CI](.github/workflows/ci.yml) | push / PR to `main` | Ruff + full pytest on 3 OS; job **`ci-ok`** requires all green |
| [Release](.github/workflows/release.yml) | tag `v*` | Same lint + tests via **`gate`** before Nuitka / publish |

Branch protection on `main` should require `ci-ok`.

## Docs map

| Doc | Purpose |
|-----|---------|
| [AGENTS.md](./AGENTS.md) | This file — agent/process context, release & deploy |
| [CHANGELOG.md](./CHANGELOG.md) | User-facing history (**must update**) |
| [README.md](./README.md) | Product overview, install, CLI |
| [docs/plan-12bit-prores-oxideav.md](./docs/plan-12bit-prores-oxideav.md) | Future 12-bit ProRes plan (not implemented) |
| [docs/releasing.md](./docs/releasing.md) | Stub pointing here (release process lives in AGENTS.md) |

## What not to do

- Do not assume system `ffmpeg` / `ffplay` / `mpv` are installed for core features.
- Do not claim software ProRes is 12-bit.
- Do not reintroduce Qt WebEngine for slate.
- Do not push version tags without a changelog section and green Release gate intent.
- Do not hand-edit `src/rc_resources.py` or force-push published release tags without a deliberate recovery process.
