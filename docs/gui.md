---
title: GUI
weight: 15
description: Tabs, overlays, preferences, and post-convert actions
---

Desktop UI for **video ↔ OpenEXR** with **OpenColorIO**. Launch with no
subcommand:

```bash
uv run python main.py
# or packaged:
exr_converter
"/Applications/EXR Converter.app/Contents/MacOS/exr_converter"
```

Optional launch flags (`--open`, `--gui-ocio`, `--mode`) are documented in
[CLI — GUI launch](./cli.md#gui-launch-for-shells--nuke). Nuke can fill them in
from a Read — see [nuke.md](./nuke.md).

---

## Tabs

| Tab | Direction | Notes |
|-----|-----------|--------|
| **Video → EXR** | Decode video → OCIO → EXR sequence | **Ingest only** — never slate / burn-in / watermark (menu disabled on this tab). Output field uses ``name.####.exr``; that basename is written (not forced to the video stem). |
| **EXR → Video** | Image sequence → OCIO → video | OpenEXR primary; also DPX, PNG, JPEG, WebP. Slate / burn-in / watermark available. Sequences must be ``name.####.ext`` (dot pad). |

Mode can be forced with `--mode video2exr|exr2video`, or inferred from `--open`
(`auto`: image-sequence paths open **EXR → Video**, common video extensions open
**Video → EXR**).

### Input / output paths

Right-click the **Input** or **Output** path field for:

| Action | Behavior |
|--------|----------|
| **Cut / Copy / Paste / Select All** | Standard line-edit edit commands (same as ⌘/Ctrl+X/C/V/A) |
| **Copy File Path** | Full text from the field |
| **Copy Folder Path** | Containing directory (or the path itself when it is a folder / sequence pattern) |
| **Open in Finder** / **Show in Explorer** / **Open Containing Folder** | Open that folder via the system file manager (`QDesktopServices`) |

**Browse…** opens the normal browser or file dialog. With a valid path in the
field, **⌘-click** (macOS) or **Ctrl-click** (Windows/Linux) on **Browse…**
reveals that path in the OS file manager instead.

**Sequence browser** lists every supported still sequence in a folder. Mixed
folders prefer EXR, then DPX. Display-encoded stills (PNG/JPEG/WebP) auto-suggest
an **sRGB** source color space; EXR/DPX default toward scene-linear / metadata.

**Views:** the top bar has **List | Grid | Preview** plus **Inspect** (always
available). **List** is the metadata table; **Grid** shows first-frame
thumbnails (async OIIO downscale; EXR/DPX get a cheap display curve).
**Preview** replaces the list/grid with an in-dialog player for the **first**
sequence in the folder (typical VFX layout is one sequence per folder; if
several exist, the first wins). Folder tree and path stay visible. Browser
Preview uses the same GPU OCIO `SequencePlayer` as the slate editor (the GL
widget is created with the dialog, before it is shown, so the window starts as
an OpenGL surface). **Space** toggles Preview ↔ last list/grid mode; **Esc**
leaves Preview; **Left/Right** step frames. Double-click or **Open** commits
the selection into the convert tab. The slate editor reuses the player with
live burn-in/watermark overlays.

Window size, splitter positions, list/grid mode, and list column widths are
remembered in `QSettings` (`ui/sequence_browser_*`).

**Video browser** (Video → EXR input) mirrors the sequence browser with
**List | Grid | Preview**. **Grid** shows first-frame video thumbnails (PyAV).
**Preview** plays the selected file (or the first file in the folder) with the
same player transport, cache strip, and OCIO controls. **Space** toggles
Preview; **Esc** returns to list/grid. Folder path fields never expand the
dialog when paths are long (text elides; width follows the layout).

---

## Color (OCIO)

- Config picker: bundled **ACES Studio Config v4**, other built-ins, `$OCIO`, or
  a custom file. Incompatible Nuke/library configs may appear **greyed out** with
  a tooltip when the linked OpenColorIO cannot load them.
- Source / destination spaces follow the active config; aliases are remapped
  where possible (`find_equivalent_space`).
- Bundled ACES Studio needs **OpenColorIO 2.5+**. From source, run
  `make ensure-ocio` if OIIO rewired you to 2.4.

---

## Slate / burn-in / watermark

Available on **EXR → Video** only (checkboxes on that tab).

| Feature | Behavior |
|---------|----------|
| **Prepend slate** | One-frame slate composited before the sequence |
| **Burn-in** | Per-frame text overlay |
| **Watermark** | Text watermark (default **40%** opacity, **tiled** across frame; size/angle editable) |

Overlays are authored in a display/sRGB-oriented space, linearised into a
scene-linear **compositing space** (prefer ACES2065-1 via `aces_interchange`,
else `scene_linear`), alpha-composited, then OCIO-transformed to the encode
display space. Rendering is **QPainter** (no embedded browser).

**Slate thumbnail** (EXR → video): which source frame is used for the slate
still is set under **File → Preferences…** — first / middle / last of the
sequence. Extraction uses the known EXR frame list (not a video seek).

---

## Codecs

Same honest bit-depth ladder as the CLI. Default is software **ProRes 422 HQ**
(`prores`, **10-bit**). macOS-only VideoToolbox ProRes keys appear only on
Darwin. Full key list and bit depths: [CLI codecs](./cli.md#codecs-honest-bit-depths).

---

## After convert

Checkboxes under the convert actions (persisted in `QSettings`):

| Toggle | Default | Action |
|--------|---------|--------|
| **Copy path** | on | Copy the output path to the clipboard |
| **Open result** | off | **EXR → Video:** open the finished file with the preferred video player (Preferences). **Video → EXR:** open the sequence in a built-in player window (GPU OCIO when available, with the playback cache strip). |
| **Show in folder** | off | Reveal the file or sequence folder in the OS file manager |

### Preferences (`File → Preferences…`)

| Setting | Purpose |
|---------|---------|
| **Video player** | System default, or a custom app/CLI path (IINA, VLC, mpv, …). Used by **Open result** for video output. |
| **Slate thumbnail frame** | First / middle / last frame of the EXR sequence for the slate still |
| **Playback cache budget** | % of system RAM for decoded sequence frames (slate, browser Preview, post-convert sequence player) |

Settings org/app: `QSettings("VFXTools", "EXRConverter")`.

---

## Slate & overlay editor

Opened from **EXR → Video** when editing slate / burn-in / watermark.

- **Timeline + cache:** shot frames are prefetched into a RAM cache as
  working-space pixels (when OCIO is configured). Playback is **cache-first**
  (stalls until the next frame is in RAM).
- **Display:** prefers **GPU OCIO** (full-resolution texture + GLSL display/view
  transform). **Gain** is pre-display (exposure stops); **gamma** is Nuke-style
  post-display ``pow(rgb, 1/γ)`` (1 = identity; not the sRGB/Rec.1886 encode).
  Falls back to CPU OCIO if OpenGL is unavailable. Export is unchanged.
- **Overlays:** burn-in and watermark are composited in scene-linear working
  space so the preview matches the convert path.

---

## Help menu

| Item | Behavior |
|------|----------|
| **Check for Updates…** | Opens the [latest GitHub Release](https://github.com/derek-rein/exr-converter/releases/latest) in the system browser (no in-app download) |
| **About EXR Converter** | Title + version header; deps, links, OCIO notes, and license in a scroll area |
| **Version X.Y.Z** | Non-clickable; shows the running app version (below a separator) |

Site links stay in **About** only (not on the Help menu).

---

## macOS notes

- Release builds use an **ad-hoc** signature (not Apple notarized). After install:

  ```bash
  xattr -cr "/Applications/EXR Converter.app"
  ```

- The running Dock icon uses the bundle `.icns` (not a sharp PNG override).

---

## See also

- [CLI reference](./cli.md)
- [Nuke integration](./nuke.md)
- [README](../README.md)
