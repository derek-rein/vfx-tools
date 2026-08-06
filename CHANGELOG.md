# Changelog

All notable changes to **EXR Converter** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Agents and contributors:** update this file in the same change set as user-visible
work. See [AGENTS.md](./AGENTS.md#changelog-required). Do not ship a release without
rolling the `[Unreleased]` section into a versioned heading.

---

## [Unreleased]

---

## [0.5.3] — 2026-08-06

### Added

- **CLI / GUI launch docs:** richer `-h` epilog; [docs/cli.md](docs/cli.md);
  README links.
- **GUI launch flags:** `--open`, `--gui-ocio`, `--mode` pre-fill media and OCIO
  when starting the app (no convert subcommand).
- **Nuke integration:** [integrations/nuke/](integrations/nuke/) menu script to
  open the selected Read with session OCIO; [docs/nuke.md](docs/nuke.md).
- **Slate thumbnail frame** preference (first / middle / last), integer in
  QSettings, editable under File → Preferences.

### Fixed

- **Slate thumbnail extraction (EXR → video only):** pick first/mid/last from the
  known EXR frame list (no video seek). OCIO ``src → slate authoring (sRGB)`` when
  a config is available; QImage buffer ownership / JPEG encode fixed. Video → EXR
  never uses slate/burn-in/watermark.
- **GUI `--open` / Nuke launch:** no longer overwritten by deferred QSettings
  input restore.
- **CLI `--workers`:** works before or after the subcommand.

---

## [0.5.2] — 2026-08-06

### Fixed

- **Windows Nuitka OpenImageIO LoadLibrary:** ship `OpenColorIO_2_4.dll` for OIIO
  (from env/cache or PyPI oiio-python wheel) while app OCIO stays on **2.5**.
  Re-running Release for `v0.5.1` kept failing because that tag predates the fix;
  packaging scripts live on the **tag commit** that is built.
- **CI/CD chain after merge:** Auto-tag dispatches Release via `workflow_dispatch`
  (`GITHUB_TOKEN` tag pushes do not start other workflows).
- Release dispatch documents `source_ref` for emergency rebuilds from another ref.

---

## [0.5.1] — 2026-08-06

### Fixed

- **Packaged app OpenColorIO 2.4 bug:** Nuitka was linking `PyOpenColorIO` to
  oiio-python’s vendored OCIO **2.4** (and could ship a 2.4-ABI extension), so the
  bundled ACES Studio v4 config (profile **2.5**) failed at convert while the UI
  could still look green. Release/local bundle now runs
  `scripts/fix_bundle_ocio.py` (restore 2.5 `.so` + dylib), `ensure_ocio` before
  Nuitka, and the smoke test loads the bundled config.
- **Nuke OCIO configs** that the linked OpenColorIO cannot load stay visible in
  the combo but are **greyed out** with a tooltip explaining why.
- Bundled ACES Studio no longer silently falls back to a library config on version
  mismatch (status matched convert failures).
- Note: Windows OIIO DLL packaging for this release was incomplete; use **0.5.2+**.

---

## [0.5.0] — 2026-08-06

### Added

- **Preferences** dialog (`File → Preferences…`) for the video player used by
  **Open result**: system default or a custom app/CLI path (IINA, VLC, mpv, etc.),
  with detected-player quick picks when present.
- **Show in folder** post-convert checkbox — reveals the output file or sequence
  folder in the OS file manager (`open -R` / Explorer select when possible).
- Unit tests for player preference helpers (`tests/test_player_prefs.py`).
- **[CHANGELOG.md](./CHANGELOG.md)** and **[AGENTS.md](./AGENTS.md)** — project
  history and agent/release process (release steps moved out of
  `docs/releasing.md`).

### Changed

- **Copy path** remains **on by default** for new installs; setting is still
  persisted after toggle.
- **Open result** uses the preferred player from Preferences instead of always
  using the OS default handler (often unreliable QuickTime for pro codecs).
  For EXR sequences, open-result is video-oriented; use **Show in folder**.

---

## [0.4.0] — 2026-08-06

### Added

- Honest codec bit-depth labeling in the UI and codec ladder.
- HEVC and FFV1 **12-bit** encode options where the stack supports them.
- Release documentation (`docs/releasing.md`) and protected-`main` PR-based
  release process notes.
- Plan for cross-platform 12-bit ProRes via oxideav
  (`docs/plan-12bit-prores-oxideav.md`) — not shipped; design only.

### Changed

- Codec help / display names reflect real encode depths (software ProRes is
  10-bit; VideoToolbox 4444/XQ ~12-bit on macOS).

### Fixed / CI

- Release workflow and Makefile polish around version inject and packaging.

---

## [0.3.0] — 2026-08-05

### Added

- Expanded codec ladder and presets (ProRes / DNxHR / CineForm / HEVC / H.264 /
  FFV1; VideoToolbox ProRes on macOS).
- Broader CLI surface and defaults for `video2exr` / `exr2video`.
- Nuke OCIO config discovery (`nuke_discover`) when local Foundry installs exist.
- Integration test suite for media conversions (`tests/test_integration_conversions.py`,
  fixtures under `tests/fixtures/`).
- `scripts/ensure_ocio.py` and `make ensure-ocio` / `make sync` to keep
  OpenColorIO **2.5+** linkage after `oiio-python` upgrades.
- CI: full pytest (unit + integration) on Linux, macOS, Windows; aggregate
  `ci-ok` gate for branch protection.

### Changed

- Project layout under `src/core`, `src/gui`, `src/render`, `src/services`.
- OCIO workflow and color-space UI improvements.

### Fixed

- OCIO version re-check after repair in CI.
- Ruff format / softened flaky watermark and VideoToolbox asserts in CI.

---

## [0.2.2] — 2026-08

### Fixed

- CI: `apt-get update` before installing `libegl1` on Linux runners.

---

## [0.2.1] — 2026-08

### Fixed

- Nuitka 4.x: bundle OCIO config with `--include-data-dir`.

---

## [0.2.0] — 2026-08

### Added

- **Deinterlace** interlaced video sources so EXR plates stay progressive
  (`src/core/video.py`, tests).

---

## [0.1.26] — 2026

### Fixed

- Bundle ACES Studio OCIO config in release builds.

---

## [0.1.25] — 2026

### Fixed

- Re-sign macOS bundle ad-hoc after slimming; repo naming / run docs updates.

---

## [0.1.24] — 2026

### Fixed

- macOS bundle-slim path so Release builds do not fail.

---

## [0.1.23] — 2026

### Added

- Tiled watermark, compositing-space overlay workflow, UI polish.
- Project restructure: `resources/`, `core`/`gui` split, expanded CI tests.

---

## [0.1.22] — 2026

### Fixed

- macOS install name: **EXR Converter.app** (not `exr_converter.app`).

---

## [0.1.21] — 2026

### Added

- Bundled ACES Studio OCIO config and playback/cache tooling.

---

## [0.1.20] and earlier

Early 0.1.x series established the app baseline:

- Video ↔ EXR conversion with OCIO
- QPainter slate (replacing QWebEngine), burn-in, watermark
- Slate model MVC and working-space overlay compositing
- Timeline scrubber / async shot preview
- EXR display-window handling, sequence browser, in-app update check
- Nuitka packaging for Linux AppImage, macOS DMG, Windows installer
- Cosign-signed release artifacts

Patch tags `v0.1.12`–`v0.1.19` were largely packaging, CI, and thumbnail path
hardening (QImage/QBuffer; exclude PIL from bundles).

---

## Links

- Releases: https://github.com/derek-rein/exr-converter/releases
- Compare tags: `https://github.com/derek-rein/exr-converter/compare/vA.B.C...vX.Y.Z`

[Unreleased]: https://github.com/derek-rein/exr-converter/compare/v0.5.3...HEAD
[0.5.3]: https://github.com/derek-rein/exr-converter/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/derek-rein/exr-converter/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/derek-rein/exr-converter/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/derek-rein/exr-converter/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/derek-rein/exr-converter/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/derek-rein/exr-converter/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/derek-rein/exr-converter/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/derek-rein/exr-converter/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/derek-rein/exr-converter/compare/v0.1.26...v0.2.0
[0.1.26]: https://github.com/derek-rein/exr-converter/compare/v0.1.25...v0.1.26
[0.1.25]: https://github.com/derek-rein/exr-converter/compare/v0.1.24...v0.1.25
[0.1.24]: https://github.com/derek-rein/exr-converter/compare/v0.1.23...v0.1.24
[0.1.23]: https://github.com/derek-rein/exr-converter/compare/v0.1.22...v0.1.23
[0.1.22]: https://github.com/derek-rein/exr-converter/compare/v0.1.21...v0.1.22
[0.1.21]: https://github.com/derek-rein/exr-converter/releases/tag/v0.1.21
