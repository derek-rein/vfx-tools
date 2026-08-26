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

## [0.9.10] — 2026-08-25

### Changed

- **CLI:** Ctrl-C cooperatively cancels `video2exr` / `exr2video` (exit 130)
  instead of leaving pool workers hanging.
- **Convert:** Video→EXR parallel path uses a thread pool so decoded RGB
  frames are not pickled across processes; EXR→Video ordered-encode staging
  is capped (~256 MiB) to limit out-of-order frame memory.

---

## [0.9.9] — 2026-08-25

### Security

- **oxideav PyO3 extension:** bump ``pyo3`` 0.25 → 0.29 (fixes
  GHSA-36hh-v3qg-5jq4 iterator OOB read) and matching ``numpy`` crate.

### Changed

- **README:** clearer upfront bullets for dailies (slate / burn-in / watermark),
  ProRes ladder, multi-core OCIO speed, and configurable OCIO configs.

---

## [0.9.8] — 2026-08-24

### Added

- **Experimental true 12-bit ProRes** (`prores_ox_4444` / `prores_ox_xq`):
  cross-platform RDD-36 ProRes-compatible encode via in-process
  **oxideav-prores** PyO3 bindings (``exr_prores``), packaged with Nuitka.
  Not Apple-certified; hidden when the extension is not built
  (`make oxideav-prores`). Software FFmpeg ProRes remains honest 10-bit.

### Changed

- **EXR → Video codec picker:** codecs are grouped in the dropdown (ProRes
  software / VideoToolbox / oxideav, CineForm, DNxHR, H.264/HEVC, FFV1).

---

## [0.9.7] — 2026-08-11

### Fixed

- **Video browser:** hide macOS AppleDouble metadata sidecars (``._clip.R3D``
  and similar) that share a media extension but are not real clips. The same
  filter applies to drag/drop, paste, R3D path detection, CLI probe, and
  convert ingest; forced sidecar paths fail with a clear error.

---

## [0.9.6] — 2026-08-11

### Changed

- **Internal media pipeline:** video and R3D decode share a common frame-source
  layer for convert and player prefetch (same OCIO/EXR write path; R3D scrub
  resolution follows the preview decode ladder).
- **Browser path paste:** shared helpers for ``file://``, quoted paths, and
  Nuke-style sequence navigation (sequence + video browsers, convert input).

### Fixed

- **Packaging:** never strip RED redistributables under ``r3d/`` on macOS/Linux
  release slim steps; R3D build/install scripts use shared ASCII-safe logging
  and macOS junk filters.

---

## [0.9.5] — 2026-08-11

### Fixed

- **R3D in packaged apps:** find ``libr3d_bridge`` / RED redistributables next to
  the binary even when Nuitka does not set ``sys.frozen`` (v0.9.4 shipped the
  ``r3d/`` folder but runtime discovery missed it).

---

## [0.9.4] — 2026-08-10

### Fixed

- **Windows Release scripts:** ASCII-only console logs in R3D install/fetch and
  OCIO helper scripts (no more `UnicodeEncodeError` on cp1252 after a successful
  R3D bundle install).

---

## [0.9.3] — 2026-08-10

### Added

- **Nuke-style path paste:** Pasting ``name.####.exr`` (or ``%04d``) into the
  EXR → Video input field resolves the sequence, frame range, and source color
  space the same way as Browse. In the sequence/video browser Folder field,
  paste navigates to the parent folder, selects the matching item, and switches
  to Preview so **Open** is ready.

### Fixed

- **R3D redistributable packaging:** skip macOS AppleDouble (``._*``) junk when
  copying RED Redistributable libraries so Linux `strip` does not fail in CI.

---

## [0.9.2] — 2026-08-10

### Fixed

- **Windows R3D bridge CI log:** use ASCII arrows in build script output so
  cp1252 consoles do not raise UnicodeEncodeError after a successful link.

---

## [0.9.1] — 2026-08-10

### Fixed

- **Windows R3D bridge build:** compile/link the RED bridge with `/MD` so it
  matches `R3DSDK-*-MD.lib` (fixes LNK2038 RuntimeLibrary mismatch and unresolved
  CRT imports on GitHub Actions Release).

---

## [0.9.0] — 2026-08-10

### Added

- **Optional RED R3D / N-RAW decode:** Video → EXR accepts `.r3d` and `.nev` when
  the R3D SDK bridge is built (`make r3d-bridge` + official RED R3D SDK).
  Decodes with IPP2 primary development (REDWideGamutRGB + Log3G10) for OCIO
  pipelines. See [docs/r3d.md](./docs/r3d.md). The proprietary SDK is not
  redistributed in source form.
- **R3D CI feed:** Release builds can fetch the full SDK from a private GitHub
  Release (`scripts/fetch_r3d_sdk.py` + secret `R3D_SDK_READ_TOKEN`), link the
  bridge, and ship only Redistributable libs + `libr3d_bridge` under a private
  app `r3d/` folder.
- **R3D preview & thumbnails:** Video browser grid and sequence-player scrub use
  low-res R3D SDK decode (not full premium). Convert remains full-quality.
- **R3D → EXR metadata:** Camera, ISO, lens, FPS, and per-frame timecode written
  as `exrconverter:r3d:*` attributes on output EXRs.
- **About dialog:** RED Redistributable end-user notice (button opens full text)
  when R3D support is present.
- **File browser row context menu:** List/Grid right-click on sequence and video
  browsers includes **Copy File Path** and **Copy Folder Path** (same actions as
  the Input/Output path fields). Video browser also gains Preview / Open on that
  menu for parity with the sequence browser.

### Fixed

- **File browser close while previewing:** the window close button (X) and Cancel
  now close the sequence and video browsers immediately, even when Preview is
  active. Escape still leaves Preview and returns to List/Grid first.
- **Input color space highlight:** the amber auto-detect cue only appears when
  media probe sets the source space, then clears on a timer (or immediately if
  you pick a space manually / load a preset). It no longer stays stuck on.

---

## [0.8.2] — 2026-08-10

### Fixed

- **GitHub Release publish when SignPath is off:** optional Windows Authenticode
  job no longer blocks the publish step (v0.8.1 built and Cosigned all platform
  binaries but never created the GitHub Release). Re-ships the 0.8.1 product
  fixes and release-pipeline hardening with a working publish path.

---

## [0.8.1] — 2026-08-10

### Changed

- **Release pipeline hardening:** tag/CHANGELOG gates, per-tag concurrency,
  pinned actions + uv 0.12.3, appimagetool 1.9.1 with SHA-256, Cosign identity
  bound to `release.yml`, GitHub build provenance attestations, CHANGELOG
  section in GitHub Release notes, optional SignPath Authenticode job (off until
  configured). Auto-tag uses `--ref main -f tag=vX.Y.Z` and skips re-dispatch
  while a Release run is already active.

### Fixed

- **Underscore EXR sequences load again:** folder browse / EXR → Video input
  re-accepts ``name_####.ext`` pads (e.g. ``04_5d_00000.exr``) that 0.7+ had
  ignored while standardizing **writes** on ``name.####.ext``. Frame range
  auto-fill and the sequence browser list both sequences when a folder mixes
  pads. Opening a folder prefers a sequence whose name matches the folder
  name. Video → EXR output still uses dot pads only.
- **Browser volumes / drives at top level:** the sequence and video file trees
  no longer hide external disks. On macOS, ``QFileSystemModel`` treats
  ``/Volumes`` as hidden under ``/``, so sticks never appeared; on Windows,
  only the system drive was rooted. Both browsers now use a multi-root tree
  (each mount is a top-level row) plus a **Volumes** section in the places
  sidebar. Mounts refresh every few seconds while Browse is open.

---

## [0.8.0] — 2026-08-09

### Changed

- **OCIO app anchor for overlays:** slate / burn-in / watermark paint is always
  linearised on a private ACES CG/Studio config the app controls (guaranteed
  `texture_paint` + `aces_interchange` / ACES2065-1), then bridged into the
  user config’s compositing space via interchange when available. User
  source/destination convert still uses only the selected (Nuke / show / file)
  config — internal transforms no longer depend on it having sRGB or AP0.

### Added

- **Built-in video player in Preferences:** **File → Preferences → Video player**
  offers **Built-in player** (default), System default, or Custom application.
  With **Open result** after EXR → Video, built-in opens the finished file in the
  same OCIO `SequencePlayer` window used for sequences (cache strip, GPU display).
- **AppSettings service:** process-wide typed QSettings façade
  (`src/services/app_settings.py`) with a key registry; browsers and the main
  window share one backend instead of ad-hoc `QSettings` factories.
- **Versioned convert presets:** JSON presets carry `schema_version` / `kind`;
  load normalizes legacy files and strips accidental I/O paths.
- **Slate undo/redo:** in the slate editor, ⌘/Ctrl+Z and Shift+⌘/Ctrl+Z undo/redo
  **feature toggles** (slate / burn-in / watermark) and **Fill from slate**
  (burn-in bulk fill) via a model-owned `QUndoStack`. Free-text / spinner field
  edits persist live but are not stacked as document commands. Convert paths,
  OCIO config, and app preferences are not on the stack. Shortcuts do not steal
  text-field undo while a line edit has focus.

### Removed

- **Slate menu** on the main menu bar. Edit slate / burn-in / watermark from
  the **EXR → Video** tab (checkbox + edit button) as before.

### Fixed

- **Video / V2E open-result looks crushed dark:** convert Rec.709 → ACEScg was
  correct; the player used the config-wide default view (often ACES 2.0 SDR
  RRT), which crushes Rec.709-originated SDR. Video preview and Video→EXR
  **Open result** now soft-prefer a **video-monitoring** view from the config
  when viewing rules / ``sdr-video`` encodings provide one
  (``getDefaultView(display, videoCS)`` — no hard-coded view names). If the
  config has no such view, the config default is unchanged. Slate/camera EXR
  still uses the config default.
- **Multi-sequence identity:** convert tab, async open, and session restore keep
  the selected sequence via its first-frame path (not the parent folder alone);
  source color auto-detect probes that sequence, not sorted ``[0]`` in mixed
  folders. Sequence browser Preview follows the current list/grid selection.
- **Slate editor undo vs typing:** ⌘/Ctrl+Z prefers the focused line edit’s
  text undo before document commands.
- **Sequence preview colour:** unmapped OIIO tags no longer skip ``src→working``.
- **File browser state restore:** Video → EXR and EXR → Video input browsers now
  persist full layout and session separately (`ui/video_browser_*` vs
  `ui/sequence_browser_*`): List/Grid/Preview, Inspect on/off, splitters, table
  columns, last folder, selection, and directory-tree expansion/scroll. Window
  **size and position** are shared (`ui/browser_geometry`). When Browse opens
  the same folder as last time, the dialog reopens as left (tree, tabs, Inspect).
  The video browser now saves layout on close (it previously only remembered
  view mode).
- **Video playback / scrubbing:** browser and sequence-player video decode no
  longer labels the post-seek **keyframe** as the requested frame. Seek lands
  on the previous I-frame, then the decoder walks forward by presentation time
  to the exact index (FFmpeg/PyAV standard path). Forward play stays sequential;
  scrub coalesces the queue and limits reverse lookback so scrubbing does not
  thrash GOPs or jump frames.
- **Video preview colour:** `load_video` resolves a real OCIO source space
  (tab selection, else file metadata, else Rec.709) before building the same
  src→working worker transform the slate editor uses, so display/view is no
  longer applied to display-encoded video as if it were scene-linear.
- **Preview cache OCIO failures:** if `src→working` raises or returns `None`,
  the frame is not stored as float working-space (avoids washed/crushed
  display). Applies to video and EXR prefetch.
- **Browser Preview OCIO:** file browsers take an explicit preview context
  (config + source space + fps) instead of reading private attributes from
  `parent()`. Video browser cache strip matches sequence browser (Preferences).

---

## [0.7.0] — 2026-08-09

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
- **Video browser List | Grid | Preview:** first-frame video thumbs (PyAV),
  in-dialog playback via ``SequencePlayer``, and path fields that no longer
  grow the dialog when paths are long.

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

[Unreleased]: https://github.com/derek-rein/exr-converter/compare/v0.9.10...HEAD
[0.9.10]: https://github.com/derek-rein/exr-converter/compare/v0.9.9...v0.9.10
[0.9.9]: https://github.com/derek-rein/exr-converter/compare/v0.9.8...v0.9.9
[0.9.8]: https://github.com/derek-rein/exr-converter/compare/v0.9.7...v0.9.8
[0.9.7]: https://github.com/derek-rein/exr-converter/compare/v0.9.6...v0.9.7
[0.9.6]: https://github.com/derek-rein/exr-converter/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/derek-rein/exr-converter/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/derek-rein/exr-converter/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/derek-rein/exr-converter/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/derek-rein/exr-converter/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/derek-rein/exr-converter/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/derek-rein/exr-converter/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/derek-rein/exr-converter/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/derek-rein/exr-converter/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/derek-rein/exr-converter/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/derek-rein/exr-converter/compare/v0.6.1...v0.7.0
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
