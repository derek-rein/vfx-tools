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

## Documentation (required)

**User-facing docs live in [`docs/`](./docs/)** (Markdown source of truth). Hugo
builds them from [`site/`](./site/) and the **Docs** workflow publishes to
GitHub Pages: https://derek-rein.github.io/exr-converter/

| Rule | Detail |
|------|--------|
| When | Any change that affects how users run or integrate the app: CLI flags, GUI behavior, codecs, OCIO/defaults, install/packaging users notice, Nuke/helpers, post-convert prefs, launch flags |
| Where | Update the matching file under `docs/` in the **same PR / commit set** as the code (and [CHANGELOG.md](./CHANGELOG.md) when user-visible) |
| Map | [docs/cli.md](./docs/cli.md) CLI + GUI launch · [docs/gui.md](./docs/gui.md) GUI / prefs / overlays · [docs/nuke.md](./docs/nuke.md) Nuke menu · [docs/releasing.md](./docs/releasing.md) short release pointer · design notes only when the design changes |
| Local | `make docs-serve` (preview) · `make docs-build` (static `site/public/`) |
| Do not | Leave docs describing old flags/defaults/codecs. Do not hand-edit generated `site/public/`. Do not put the only copy of user docs solely in chat or PR text. |

Agents: **before finishing work**, check whether `docs/` still matches the
behavior you shipped. If you changed CLI/GUI/integration surface area and
skipped docs, treat that as incomplete and update them.

Release process for maintainers stays in this file
([Releasing and deployment](#releasing-and-deployment)); keep `docs/releasing.md`
as a short pointer, not a second full copy.

## Layout (where to edit)

```text
main.py                 # GUI / CLI entry
src/
  core/                 # convert, video, EXR I/O, OCIO, codecs constants, sequences
  gui/                  # PySide6 window, tabs, slate UI, preferences, style
  gui/player/           # SequencePlayer, ShuttleBar, ImagePreviewView (shared playback)
  render/               # slate, burn-in, watermark, tokens (QPainter)
  services/             # worker thread, presets, cache, slate model, prefetch
  rc_resources.py       # generated from resources.qrc — do not hand-edit
native/
  r3d/                  # optional RED R3D C ABI bridge
  exr_prores/           # optional oxideav-prores PyO3 extension (12-bit ProRes)
resources/              # icons, style.qss, OCIO config, screenshots
scripts/                # bump_app_version.py, ensure_ocio.py, build_oxideav_prores.py
tests/                  # pytest; integration + fixtures under tests/fixtures/
docs/                   # user-facing Markdown (Hugo content source of truth)
site/                   # Hugo config + theme; mounts docs/ → GitHub Pages
.github/workflows/      # ci.yml, docs.yml, release.yml, auto-tag-release.yml
```

### Important modules

| Area | Start here |
|------|------------|
| Convert pipeline | `src/core/convert.py`, `src/core/video.py`, `src/core/exr_io.py` |
| Optional R3D / N-RAW | `src/core/r3d.py`, `native/r3d/`, `scripts/build_r3d_bridge.py`, `scripts/fetch_r3d_sdk.py`, `scripts/install_r3d_into_bundle.py`, [docs/r3d.md](./docs/r3d.md) |
| Optional 12-bit ProRes (oxideav) | `native/exr_prores/` (PyO3), `src/core/oxideav_prores.py`, `make oxideav-prores`, [docs/plan-12bit-prores-oxideav.md](./docs/plan-12bit-prores-oxideav.md) |
| Private full SDK (local) | `~/code/r3d-sdk-private/R3DSDKv9_2_1` — never commit; CI feed = private repo `derek-rein/r3d-sdk-private` release `sdk-9.2.1` |
| Codec ladder / bit depth labels | `src/core/constants.py` |
| CLI | `src/cli.py` |
| Main window / post-convert actions | `src/gui/window.py` |
| Player prefs / reveal-in-folder | `src/gui/preferences.py` |
| Convert tabs / codecs UI | `src/gui/widgets.py` |
| Sequence player | `src/gui/player/` (`SequencePlayer`, transport, preview view) |
| Slate dialog / viewer | `src/gui/slate_widgets.py` (composes player + overlay hooks), `src/gui/ocio_gpu_plane.py`, `src/services/slate_model.py` |
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
make oxideav-prores # build optional PyO3 12-bit ProRes extension (needs Rust)
make docs-serve    # Hugo local preview of docs/ (http://127.0.0.1:1313/)
make docs-build    # Hugo static build → site/public/
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

1. **Color pipeline** — User src/dst convert uses the **active user config**
   only. App-authored overlays (slate / burn-in / watermark) linearise on the
   private **app anchor** OCIO config (`get_app_anchor_config()` — guaranteed
   `texture_paint` + `aces_interchange` / ACES2065-1), then bridge into the
   user compositing space via interchange when available
   (`linearize_overlay`). Do not paint sRGB on linear without those helpers;
   do not assume the user/Nuke config has sRGB or AP0.
2. **Video I/O is PyAV** — encode/decode via FFmpeg libs bundled with the `av`
   wheel. **PyAV does not ship `ffplay`.** Do not assume system FFmpeg/ffplay
   exists on user machines. Playback after convert uses the user’s preferred
   player from Preferences (system default or custom path).
3. **Software ProRes is 10-bit** in FFmpeg/`prores_ks`. True cross-platform
   12-bit ProRes-compatible output is experimental via oxideav PyO3
   (`prores_ox_*`, `make oxideav-prores`); see
   `docs/plan-12bit-prores-oxideav.md`. VideoToolbox ProRes (`prores_vt_*`) is
   **macOS-only**; UI must keep honest bit-depth labels.
4. **Slate is QPainter** — no Qt WebEngine. Do not reintroduce browser-based slate.
   **Preview display** prefers **GPU OCIO** (`src/gui/ocio_gpu_plane.py`,
   `QOpenGLWidget` + PyOpenGL + OCIO `getDefaultGPUProcessor`). Do not put
   full-res CPU `applyRGB` back on the playback hot path. Export remains CPU.
5. **Generated resources** — edit `resources.qrc` / files under `resources/`, then
   `make resources`. Do not hand-edit `src/rc_resources.py`.
6. **Presets / settings / undo** — Prefer :class:`~src.services.app_settings.AppSettings`
   (`get_app_settings()`) over ad-hoc `QSettings(APP_ORG, APP_NAME)`. Keys live in
   `AppSettings.Keys` / domain modules. Layers: **app prefs** (player, cache,
   post-convert), **session** (last paths/options), **convert presets** (versioned
   JSON under AppData — no I/O paths), **slate document** (`SlateModel` + model-owned
   `QUndoStack`; not convert paths/OCIO). Post-convert: `ui/copy_path_after` (default
   **true**), `ui/open_after`, `ui/show_folder_after`. Open result: EXR→Video
   `player/mode` (`builtin`|`system`|`custom`); Video→EXR uses built-in player window.
7. **Nuitka** — Release builds strip many Qt modules (WebEngine, Svg, Pdf, …).
   Prefer PySide6-Essentials APIs already used in the tree. Bundle includes
   `resources/ocio` and package data for `av`, OIIO, OCIO, fileseq.
   **Check for Updates** opens the GitHub Releases page via
   `QDesktopServices` (system browser) — no in-app HTTPS download. Smoke test
   still checks Python `ssl` is importable in the frozen binary; keep
   `--include-module=ssl` and do **not** `--noinclude-dlls=libssl*` /
   `libcrypto*` if you reintroduce app HTTPS later.
8. **macOS Dock icon** — Do **not** call `setWindowIcon` / set a runtime PNG on
   Darwin. Dock must use the bundle `.icns` (`--macos-app-icon`). A Qt PNG
   override becomes `applicationIconImage` and shows sharp square corners while
   running. Windows/Linux still set `:/icon.png`.

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
        │  4. optional local annotated tag vX.Y.Z  (not required for publish)
        ▼
 PR → main → merge  (CHANGELOG must already have ## [X.Y.Z] + compare link)
        │
 Auto-tag release workflow (on push to main)
        │  refuse if CHANGELOG lacks ## [X.Y.Z]
        │  if pyproject version X.Y.Z has no tag → create + push annotated tag
        │  if no GitHub Release yet and no Release run active →
        │      `gh workflow run Release --ref main -f tag=vX.Y.Z`
        ▼
 GitHub Actions “Release”
   triggers: push tags v*  (human/PAT)  OR  workflow_dispatch (auto-tag)
   concurrency: one run per tag (cancel-in-progress: false)
        │  validate tag↔pyproject + CHANGELOG
        │  lint → test (3 OS) → gate → Nuitka (4 targets)
        │  → optional SignPath (Windows) → Cosign + GH attestations → publish
        ▼
 Artifacts: Linux AppImage, macOS DMG (arm64 + x86_64), Windows installer
            (+ .sigstore.json bundles; GH attestations for provenance)
```

Tags are plain semver: **`v1.2.3`**. The tag is the published app version
(workflow also injects `APP_VERSION` from the tag during the Nuitka build).

**Critical GitHub quirk:** a tag created with the default **`GITHUB_TOKEN` does
not start other workflows** (recursion guard). Auto-tag therefore **dispatches**
Release via `workflow_dispatch` after pushing the tag. The dispatch uses
**`--ref main`** so the *workflow file* is latest; the build checks out the tag
via `inputs.tag` (`RELEASE_REF`). Human `git push origin v*` with a real
user/PAT still triggers Release on `push: tags`.

**Do not double-fire:** after merge, prefer Auto-tag only. Do not also push the
local `vX.Y.Z` tag unless Auto-tag failed — a human tag push *and* a late
dispatch can race (concurrency serializes same-tag runs, but wastes minutes).

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

# Before tagging: CHANGELOG.md must have [X.Y.Z] notes + bottom compare link
# (Auto-tag and Release gate both refuse to ship without them.)
make release PART=minor PUSH=0    # or patch / major
# → commit "release: X.Y.Z" + local annotated tag vX.Y.Z
# Include CHANGELOG.md in the release PR if not already on main
# (bump only commits version files — commit changelog separately on the branch)

git push -u origin HEAD
gh pr create --base main --title "release: X.Y.Z" --body "…"
gh pr checks
gh pr merge --merge          # or --squash per preference

git checkout main && git pull origin main
# Auto-tag: creates vX.Y.Z if missing → dispatches Release (--ref main -f tag=…).
# Manual fallback if auto-tag is disabled or failed:
#   git tag -a vX.Y.Z -m "release: X.Y.Z" && git push origin vX.Y.Z
#   # or: gh workflow run Release --ref main -f tag=vX.Y.Z

gh run list --workflow="Auto-tag release" --limit 3
gh run list --workflow=Release --limit 3
gh run watch
gh release view vX.Y.Z
```

Historical: `release/0.3.0` → PR #1; `release/0.4.0` → PR #2; `release/0.5.0` → PR #3
(tag was pushed manually after merge; auto-tag added afterward).

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
| `lint` | Tag↔pyproject + CHANGELOG gate, then Ruff check + format |
| `test` | Full pytest on ubuntu / macos / windows |
| `gate` | Aborts release if lint or any OS test failed |
| `build` | Nuitka → AppImage / DMG / Windows setup (180m timeout; LTO + Qt strip aligned with `make bundle`) |
| `sign-windows` | Optional SignPath Authenticode (skipped unless vars configured; runs **before** Cosign) |
| `sign` | Assemble final bits → Cosign keyless + `actions/attest-build-provenance` |
| `release` | CHANGELOG section + verify notes → single GitHub Release upload |

Local package only: `make bundle` (no GitHub Release). Shared Nuitka strip/LTO
flags match CI; packaging (DMG/AppImage/Inno) is CI-only.

### Actions hygiene

- Third-party actions are **SHA-pinned** with trailing `# vX` comments.
- [`.github/dependabot.yml`](.github/dependabot.yml) opens weekly grouped PRs for
  `github-actions` bumps.
- `astral-sh/setup-uv` pins **uv 0.12.3** (not `latest`).
- AppImage packaging pins **appimagetool 1.9.1** and verifies SHA-256.

### SignPath (optional Windows Authenticode)

Skipped until you set repository **variables** (and matching **secrets**):

| Kind | Name |
|------|------|
| secret | `SIGNPATH_API_TOKEN`, `SIGNPATH_ORGANIZATION_ID` |
| variable | `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_SIGNING_POLICY_SLUG` |
| variable (optional) | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` (default `exr_converter`) |

Also install the SignPath GitHub App and create a project/policy/artifact
configuration in the SignPath dashboard. When `SIGNPATH_PROJECT_SLUG` is empty,
`sign-windows` is skipped and the unsigned Inno setup is published (Cosign still
applies).

### Checklist before shipping

- [ ] Feature work tested (`make test` / PR CI)
- [ ] **[CHANGELOG.md](./CHANGELOG.md)** updated: `[Unreleased]` rolled into `[X.Y.Z]` **and** bottom compare link `[X.Y.Z]:` updated
- [ ] On branch `release/X.Y.Z` (not direct push to `main`)
- [ ] Working tree free of unrelated unstaged work
- [ ] Correct `PART`; `make release … PUSH=0` → PR → merge
- [ ] Auto-tag (or manual tag / `gh workflow run`) fires Release; `gh run watch` green
- [ ] `gh release view vX.Y.Z` has artifacts + signatures
- [ ] After merge: **do not** also push a local tag unless Auto-tag failed

### Troubleshooting

| Symptom | What to do |
|---------|------------|
| `GH013` / must use pull request | Use `release/X.Y.Z` + PR; `PUSH=0` |
| Required status checks on main | Ship via PR so `ci-ok` runs on the PR head |
| `No changes to commit` from `make release` | Version already bumped; `python3 scripts/bump_app_version.py show` |
| Auto-tag refuses: CHANGELOG missing section | Add `## [X.Y.Z]` + `[X.Y.Z]:` compare link, merge fix PR |
| Tag exists locally not on remote | After merge, retag if needed, `git push origin vX.Y.Z`. Never force-push a published public tag lightly |
| Gate red on Release | Fix forward with a new patch; prefer not rewriting a published tag |
| Wrong version in GUI binary | Tag must be `vX.Y.Z`; check inject-version step and `APP_VERSION` on the tagged commit |
| OCIO 2.4 vs 2.5 in CI/local | `scripts/ensure_ocio.py` / `make ensure-ocio` |
| `Unable to reserve cache` (setup-uv) | Benign race if two writers share a key; CI/Release use `cache-suffix` + lint `save-cache: false` |
| Two Release runs for one tag | Concurrency group `release-vX.Y.Z` queues; avoid double-trigger (tag push + dispatch) |

### Artifact verification (users)

**Cosign** keyless signatures ship as `*.sigstore.json` next to each asset.
Auto-tag dispatches Release from **main**, so the OIDC certificate identity is
typically:

`https://github.com/derek-rein/exr-converter/.github/workflows/release.yml@refs/heads/main`

(A pure human `git push` of the tag uses `@refs/tags/vX.Y.Z` instead.)

```bash
cosign verify-blob <ARTIFACT_FILE> \
  --bundle <ARTIFACT_FILE>.sigstore.json \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp 'https://github.com/derek-rein/exr-converter/\.github/workflows/release\.yml@refs/(heads/main|tags/v.+)'
```

**GitHub Attestations** (SLSA-style build provenance) are also recorded:

```bash
gh attestation verify <ARTIFACT_FILE> -R derek-rein/exr-converter
```

### macOS Gatekeeper

Builds use **ad-hoc** signature (not Apple notarized). Users clear quarantine:

```bash
xattr -cr "/Applications/EXR Converter.app"
```

## CI

| Workflow | Trigger | Gate |
|----------|---------|------|
| [CI](.github/workflows/ci.yml) | push / PR to `main` | Ruff + full pytest on 3 OS; job **`ci-ok`** requires all green |
| [Docs](.github/workflows/docs.yml) | push / PR paths under `docs/`, `site/` | Hugo build; deploy to Pages on `main` only |
| [Auto-tag release](.github/workflows/auto-tag-release.yml) | push to `main` | CHANGELOG gate; if no `vX.Y.Z` tag → push tag; if no GitHub Release and no active run → `workflow_dispatch` Release |
| [Release](.github/workflows/release.yml) | tag `v*` **or** `workflow_dispatch` | Tag/CHANGELOG validate + lint + tests via **`gate`** before Nuitka / Cosign / publish |

Branch protection on `main` should require `ci-ok`.

## Docs map

| Doc | Purpose |
|-----|---------|
| [AGENTS.md](./AGENTS.md) | This file — agent/process context, release & deploy, **docs rules** |
| [CHANGELOG.md](./CHANGELOG.md) | User-facing history (**must update**) |
| [README.md](./README.md) | Product overview, install, short CLI |
| [docs/](./docs/) | User-facing Markdown (**must update** when behavior changes) |
| [site/](./site/) | Hugo config + theme; mounts `docs/` → GitHub Pages |
| [docs/cli.md](./docs/cli.md) | CLI + GUI launch flags |
| [docs/gui.md](./docs/gui.md) | GUI tabs, overlays, preferences, post-convert |
| [docs/nuke.md](./docs/nuke.md) | Nuke menu integration |
| [docs/plan-12bit-prores-oxideav.md](./docs/plan-12bit-prores-oxideav.md) | Future 12-bit ProRes plan (not implemented) |
| [docs/r3d.md](./docs/r3d.md) | Optional RED R3D / N-RAW (proprietary SDK; license + build) |
| [docs/releasing.md](./docs/releasing.md) | Short pointer here + docs-site note |
| [integrations/nuke/](./integrations/nuke/) | Nuke `menu.py` + helpers |

Public site: `make docs-serve` / `make docs-build`; workflow **Docs** deploys to
`https://derek-rein.github.io/exr-converter/` (Pages source = GitHub Actions).

## What not to do

- Do not assume system `ffmpeg` / `ffplay` / `mpv` are installed for core features.
- Do not claim software ProRes is 12-bit.
- Do not commit the proprietary RED R3D SDK (headers, static libs, docs, samples)
  or claim the SDK is open-source; only Redistributable dylibs/so/dll may ship,
  in a private app directory, with required EULA terms — see [docs/r3d.md](./docs/r3d.md).
- Do not reintroduce Qt WebEngine for slate.
- Do not push version tags without a changelog section and green Release gate intent.
- Do not hand-edit `src/rc_resources.py` or force-push published release tags without a deliberate recovery process.
- Do not finish user-visible work without updating **CHANGELOG.md** and **`docs/`** when the surface area changed.
