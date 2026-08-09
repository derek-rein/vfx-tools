# Changelog

All notable changes to **EXR Converter** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Agents and contributors:** update this file in the same change set as user-visible
work. See [AGENTS.md](./AGENTS.md#changelog-required). Do not ship a release without
rolling the `[Unreleased]` section into a versioned heading.

---

## [Unreleased]

### Added

- **Image sequences beyond EXR → video:** `exr2video` (CLI + GUI) accepts
  OpenEXR plus **DPX**, and display stills via OpenImageIO — **PNG**,
  **JPEG** (`.jpg` / `.jpeg`), and **WebP**. Sequence browse, drag-and-drop,
  and folder scan pick these up; mixed folders prefer EXR, then DPX.
  EXR/DPX default toward scene-linear; PNG/JPEG/WebP toward **sRGB**.
- **Sequence browser List | Grid | Preview:** segmented control switches
  between the metadata table, a thumbnail grid (async OIIO first-frame
  previews), and in-dialog playback of the **first** sequence in the folder
  (`SequencePlayer`). Inspect stays available; **Space** toggles Preview,
  **Esc** returns to list/grid. Mode is stored in `QSettings`
  (`ui/sequence_browser_view`).
- **Playback cache budget** lives in **File → Preferences** (not in browser
  Preview). Left/Right arrows step frames in the player.
- **Sequence browser layout memory:** window geometry, splitter sizes, and
  resizable list columns are stored in `QSettings`.
- **Reusable sequence player** (`src/gui/player/`): shared transport, RAM cache,
  and GPU/CPU OCIO display path used by the slate editor and browser preview.
- **Input/output path QoL:** path fields use a custom context menu with standard
  Cut / Copy / Paste / Select All plus **Copy File Path**, **Copy Folder Path**,
  and Open in Finder / Explorer (via `QDesktopServices`). ⌘-click (macOS) or
  Ctrl-click (Windows/Linux) on **Browse…** opens the folder when the path is
  valid.
- **Open result (Video → EXR):** opens the written sequence in a standalone
  built-in player window (cache strip shown; same GPU OCIO path as the slate
  editor). Opens the sequence named from the source video stem (not a mixed
  folder scan), with OCIO source locked to the convert destination space.
  Window geometry is clamped on-screen. **EXR → Video** still uses the preferred
  external video player.
- **Video → EXR is ingest-only:** slate / burn-in / watermark are never applied
  (removed from the convert API; **Slate** menu disabled on that tab).
- **Video → EXR output name:** the field pattern ``name.####.exr`` is respected
  (writes ``name.1001.exr``, …). Sequence discovery and writes use **dot pads
  only** (``name.####.ext``); underscore pads (``name_####.ext``) are ignored.
- **Video → EXR → player colour:** post-convert playback uses convert
  **destination** as OCIO source (e.g. ACEScg → working → display). File probes
  prefer ``exrconverter:dstColorSpace`` over OIIO’s often-wrong
  ``oiio:ColorSpace`` (commonly rewritten to ``lin_rec709``).

### Fixed

- **Sequence browser Preview crash:** two related Qt/PySide issues —
  (1) adding the first `QOpenGLWidget` to an *already shown* top-level window
  recreates the native surface (`RasterSurface` → `OpenGLSurface`, Qt 6.4+) and
  can SIGSEGV on macOS — the player/GPU plane is now created in the browse
  dialog constructor before show (same pattern as the slate editor);
  (2) tearing down with `setParent(None)` + `deleteLater()` while dropping the
  last Python ref double-destroyed the C++ QObject. GL is released explicitly;
  the player stays parented to the dialog. Viewer gain/gamma live in
  `nuke_slider.py`.

---

## [0.6.1] — 2026-08-08

### Fixed

- **Release CI:** run ``ensure_ocio`` before the Release workflow test matrix
  (same as CI). Without it, ``oiio-python`` can leave PyOpenColorIO on 2.4 and
  the bundled ACES Studio 2.5 config fails to load, aborting the gate.

---

## [0.6.0] — 2026-08-08

### Added

- **GPU OCIO slate/shot preview:** playback uses OpenColorIO’s GPU processor
  (GLSL 4.x via `QOpenGLWidget` + PyOpenGL). Full-resolution working-space
  frames from the RAM cache are texture-uploaded (float16 when available);
  burn-in/watermark are a separate cached overlay texture composited in the
  fragment shader; display/view and live gain/gamma are dynamic uniforms —
  not per-frame CPU `applyRGB` or full-plate alpha-over. CPU display remains
  as fallback when OpenGL is unavailable.

### Fixed

- **Slate viewer highlight headroom:** shot frames are read as unclamped
  float32 EXR (not uint16). UINT16 loads clamp values above 1.0, so exposing
  *down* could not recover jacket/highlight detail that Nuke still shows.
  Cache still uses float16 HDR working-space after ``src → working``.
- **EXR sequence browser Open:** selecting a sequence now opens *that*
  sequence (via its first frame path). Previously only the parent folder was
  returned, so multi-sequence folders always loaded sorted ``[0]`` — toggling
  Inspect could mask a stale/missing table selection. Open is enabled as soon
  as sequences are listed (first/prior row auto-selected).
- **Viewer gamma (Nuke-aligned):** slate/shot preview applies Nuke’s post-display
  power ``pow(rgb, 1/γ)`` *after* the OCIO display/view transform (GPU fragment
  uniform and CPU numpy). Previously gamma went through OCIO
  ``ExposureContrastTransform`` *before* display, which crushed mid-grays
  differently (wrong at extreme γ like 0.01). Gain remains pre-display
  (exposure stops). Slider still shows Nuke-style γ (1 = neutral).
- **Input restore on launch:** saved EXR/video path is re-probed into the
  validated model (not only shown in the field). ``textChanged`` no longer
  drops a loaded sequence when the field still shows the same source or the
  ``####`` display pattern. Input prefs are flushed with ``QSettings.sync()``
  so hard restarts keep the last path.
- **Submit Notes typing:** form→model updates no longer echo
  ``setPlainText`` on every keystroke (which reset the caret to the start).

### Changed

- **Viewer gain/gamma sliders:** procedural curves (log gain; asinh pivot-at-1
  gamma for denser resolution near neutral) with range-driven 1–2–5 nice ticks
  that thin by track width — no hard-coded tick tables. Numeric readouts turn
  red when the value is not exactly 1.0 (Nuke-style). Display/view combo width
  is capped so long OCIO labels no longer crush the tracks.
- **Slate/shot preview sampling:** nearest-neighbor (raw pixel grid) when zooming
  — no bilinear filtering on the plate/overlay textures (GPU) or pixmap transform
  (CPU fallback).
- **Viewer strip:** removed the decorative f-stop (aperture) control; gain/gamma
  and display/view remain.
- **Watermark defaults:** 40% opacity and **Tile across frame** on (was 35% /
  single stamp). Existing saved watermark prefs are unchanged.
- **Check for Updates:** opens the latest GitHub Releases page in the system
  browser (`QDesktopServices`) instead of downloading installers via Python
  HTTPS (avoids frozen-app SSL/CA issues).
- **Help menu:** non-clickable **Version X.Y.Z**; no site-link items
  (derekvfx.ca / ocio.cc remain in About).
- **About dialog:** dependency versions move into the scroll area; only the
  title and app version stay in the header.
- **Dependency:** `PyOpenGL` for GPU OCIO preview.

---

## [0.5.4] — 2026-08-08

### Added

- **Docs site (GitHub Pages):** write Markdown under `docs/`; Hugo builds from
  `site/` and the **Docs** workflow publishes to
  `https://derek-rein.github.io/exr-converter/`. Local preview: `make docs-serve`.
- **GUI docs:** [docs/gui.md](docs/gui.md) covers tabs, slate/burn-in/watermark,
  preferences, and post-convert actions.

### Changed

- **Docs accuracy:** CLI codec ladder (default `prores`, honest bit depths,
  default extensions), Nuke “Open EXR Converter only…”, and AGENTS.md now
  require updating `docs/` with user-visible work (same rule as the changelog).

### Fixed

- **Check for Updates on macOS packages:** Nuitka no longer excludes OpenSSL
  (`libssl` / `libcrypto`), which left Python without HTTPS and produced
  `urlopen error unknown url type: https`. If SSL is still unavailable, the app
  opens the GitHub releases page in the browser instead of only showing an error.
- **macOS Dock icon corners while running:** stop overriding the Dock tile with
  `setWindowIcon(:/icon.png)`. The running app now keeps the bundle `.icns` so
  open and closed Dock tiles match (no sharp square PNG override).

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

[Unreleased]: https://github.com/derek-rein/exr-converter/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/derek-rein/exr-converter/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/derek-rein/exr-converter/compare/v0.5.4...v0.6.0
[0.5.4]: https://github.com/derek-rein/exr-converter/compare/v0.5.3...v0.5.4
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
