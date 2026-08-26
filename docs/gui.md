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
| **Video → EXR** | Decode video → OCIO → EXR sequence | **Ingest only** — never slate / burn-in / watermark. Output field uses ``name.####.exr``; that basename is written (not forced to the video stem). Accepts common video containers plus optional **`.r3d` / `.nev`** when the [R3D SDK bridge](./r3d.md) is available (browser thumbs + player preview use low-res R3D decode; convert is full quality; camera/timecode metadata lands on EXRs). |
| **EXR → Video** | Image sequence → OCIO → video | OpenEXR primary; also DPX, PNG, JPEG, WebP. Slate / burn-in / watermark via that tab’s controls. Sequences may use ``name.####.ext`` or ``name_####.ext`` pads. |

Dragging the **Log** splitter up does not squash Input / Output / Options
fields — those controls keep a fixed readable height, and the convert form
scrolls if the pane is short. **Convert** and **Cancel** share that height;
the progress bar matches the input rows.

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

**Paste paths:** You can paste a Nuke-style sequence path
(``/show/shot.####.exr`` or ``%04d``) into the convert Input field — the app
resolves the sequence on disk, fills the frame range, and auto-detects source
color space. Pasting a folder, frame file, video, or ``####`` pattern into a
browser’s **Folder** field navigates there, highlights the matching item, and
opens **Preview** so you can hit **Open** immediately.

**Sequence browser** lists every supported still sequence in a folder
(``name.####.ext`` or ``name_####.ext``). Mixed folders prefer EXR, then DPX.
Display-encoded stills (PNG/JPEG/WebP) auto-suggest an **sRGB** source color
space; EXR/DPX default toward scene-linear / metadata.

**Views:** the top bar has **List | Grid | Preview** plus **Inspect** (always
available). **List** is the metadata table; **Grid** shows first-frame
thumbnails (async OIIO downscale; EXR/DPX get a cheap display curve).
**Preview** plays the **currently selected** sequence (or the first listed row
if nothing is selected). To preview another sequence, return to List/Grid,
select it, then enter Preview again. Folder tree and path stay visible.
Browser Preview uses the same GPU OCIO `SequencePlayer` as the slate editor
(the GL widget is created with the dialog before show). **Space** toggles
Preview ↔ last list/grid mode; **Esc** leaves Preview; **Left/Right** step
frames. Double-click or **Open** commits the **selected** sequence via its
**first-frame path** (not the folder alone), so multi-sequence directories keep
the chosen basename through convert, restore, and slate. Typing only a
directory still resolves a default sequence on disk (EXR first, then DPX;
prefers a basename matching the folder name when several sequences share the
folder). The slate editor reuses the player with live burn-in/watermark overlays.

**Video browser filters:** only real media extensions are listed (``.r3d``,
``.nev``, ``.mov``, ``.mp4``, …). macOS **AppleDouble** Finder sidecars
(``._clip.R3D``) that appear next to clips on network shares or non-HFS
volumes are hidden — they share the media extension but are resource-fork
metadata, not video. RED **`.RMD`** metadata files are never listed (not a
video extension).

Right-click a row in **List** or a tile in **Grid** (sequence and video
browsers) for:

| Action | Behavior |
|--------|----------|
| **Preview** | Switch to Preview for the selected item (**Space**) |
| **Open** | Accept the selection (same as the Open button) |
| **Copy File Path** | First-frame path (sequence) or video file path |
| **Copy Folder Path** | Containing directory |

(Same **Copy File Path** / **Copy Folder Path** labels as the Input/Output
path-field menu.)

**Volumes / drives:** both browsers show mounted volumes as **top-level** rows
in the folder tree and under a **Volumes** heading in the places sidebar
(system disk first, then other mounts by name). That covers:

| Platform | What you see |
|----------|----------------|
| **macOS** | Boot volume (e.g. Macintosh HD) plus each entry under `/Volumes` (USB, network, disk images). macOS hides `/Volumes` from a plain “root `/`” file model, so sticks would otherwise be invisible. |
| **Windows** | Each drive letter (`C:\`, `D:\`, …), not only the system drive. |
| **Linux** | `/` plus user-facing mounts (typically `/media/…`, `/mnt/…`, `/run/media/…`); pseudo filesystems (`proc`, `sysfs`, …) are omitted. |

The list refreshes every few seconds while Browse is open so plug-in / eject
updates without restarting the dialog.

**Browser layout memory** (both input browsers):

| What | Shared or separate? | Keys |
|------|---------------------|------|
| Window size + position | **Shared** (Video ↔ Sequence) | `ui/browser_geometry` |
| List \| Grid \| Preview, Inspect on/off, outer/content splitters, list column widths | **Per mode** | `ui/sequence_browser_*` / `ui/video_browser_*` |
| Folder tree expansion, tree scroll, last folder + selection | **Per mode**, restored when Browse reopens the **same** start directory as last time | `…_tree_expanded`, `…_tree_vscroll`, `…_last_dir`, `…_selected` |

If the convert tab’s current path maps to a **different** folder than the last
browse session, layout prefs (size, splitters, view, Inspect, columns) still
restore, but the tree focuses that new folder instead of replaying the previous
expansion set.

**Video browser** (Video → EXR input) mirrors the sequence browser with
**List | Grid | Preview**. **Grid** shows first-frame video thumbnails (PyAV).
**Preview** plays the selected file (or the first file in the folder) with the
same player transport, cache strip, and OCIO controls. Video decode seeks to
the previous keyframe then decodes forward to the exact frame (so scrubbing and
play stay frame-accurate on long GOPs). **Space** toggles Preview; **Esc**
returns to list/grid. Folder path fields never expand the dialog when paths are
long (text elides; width follows the layout).

---

## Color (OCIO)

- Config picker: bundled **ACES Studio Config v4**, other built-ins, `$OCIO`, or
  a custom file. Incompatible Nuke/library configs may appear **greyed out** with
  a tooltip when the linked OpenColorIO cannot load them.
- Source / destination spaces follow the **active (user) config**; aliases are
  remapped where possible (`find_equivalent_space`).
- **App-internal paint** (slate / burn-in / watermark) is linearised on a private
  ACES **app anchor** config (CG/Studio built-in) with guaranteed `texture_paint`
  and `aces_interchange` (ACES2065-1), then bridged into the user compositing
  space via interchange when the user config provides it. Convert of *your*
  footage still uses only the selected config.
- Bundled ACES Studio needs **OpenColorIO 2.5+**. From source, run
  `make ensure-ocio` if OIIO rewired you to 2.4.

---

## Presets (`Presets` menu)

Named **convert recipes** (JSON under app data) — color spaces, scale, codec key,
EXR compression, OCIO source. **Not** included: input/output paths, window
geometry, player/cache prefs, or full slate text (slate fields still use last
session via Preferences / QSettings).

Files are versioned (`schema_version`); older presets still load.

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
(`prores`, **10-bit**). The EXR → Video **Codec** control is a nested family
menu: open it, then pick a family (ProRes software, VideoToolbox, oxideav,
CineForm, DNxHR, H.264/HEVC, FFV1) and a profile inside that submenu.

**VideoToolbox** is Apple’s **hardware ProRes encoder**, **macOS only**. That
submenu is omitted on Windows and Linux. 422 profiles stay 10-bit; **4444 /
XQ** are **~12-bit class** (unlike software 4444/XQ, which encode 10-bit).
See [ProRes and VideoToolbox](./prores.md) for why the three ProRes families
exist and which key to pick.

Experimental **oxideav** 12-bit keys (`prores_ox_proxy` / `lt` / `422` / `hq`
/ `4444` / `xq`) appear when the `exr_prores` extension is built
(`make oxideav-prores`; included in release binaries). Full key list:
[CLI codecs](./cli.md#codecs-honest-bit-depths).

---

## After convert

Checkboxes under the convert actions (persisted in `QSettings`):

| Toggle | Default | Action |
|--------|---------|--------|
| **Copy path** | on | Copy the output path to the clipboard |
| **Open result** | off | **EXR → Video:** open the finished file with the preferred video player (Preferences; default **built-in**). **Video → EXR:** open the sequence in the built-in player window (GPU OCIO when available, with the playback cache strip). |
| **Show in folder** | off | Reveal the file or sequence folder in the OS file manager |

### Preferences (`File → Preferences…`)

| Setting | Purpose |
|---------|---------|
| **Video player** | **Built-in player** (default), **System default**, or a **custom** app/CLI path (IINA, VLC, mpv, …). Used by **Open result** after EXR → Video. Built-in uses the same `SequencePlayer` as sequence Preview (GPU OCIO + cache strip). |
| **Slate thumbnail frame** | First / middle / last frame of the EXR sequence for the slate still |
| **Playback cache budget** | % of system RAM for decoded frames (slate, browser Preview, post-convert built-in player) |

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
  Falls back to CPU OCIO if OpenGL is unavailable. The preview canvas
  (letterbox around the frame) is **black**, with a 1px white format box
  (Nuke-style) and the plate resolution right-justified under the
  bottom-right of the frame. Export is unchanged.
- **Overlays:** burn-in and watermark are composited in scene-linear working
  space so the preview matches the convert path.
- **Undo:** ⌘/Ctrl+Z and Shift+⌘/Ctrl+Z undo/redo **feature toggles** and
  **Fill from slate** (burn-in bulk fill). Free-text / spinner edits still
  persist live without stacking one undo step per keystroke. Convert paths
  and OCIO are outside the undo stack.
- **Video monitoring view:** for video Preview and Video→EXR **Open result**,
  the player asks the OCIO config for a view via viewing rules / video encodings
  (``getDefaultView(display, videoColorSpace)``) when available — e.g. ACES
  configs often resolve that to a colorimetric video view. If the config has no
  such rule, the config-wide default display/view is kept. Scene-linear camera
  EXR in the slate editor always uses the config default.

---

## Help menu

| Item | Behavior |
|------|----------|
| **Check for Updates…** | Opens the [latest GitHub Release](https://github.com/derek-rein/exr-converter/releases/latest) in the system browser (no in-app download) |
| **About EXR Converter** | Title + version header; deps, links, OCIO notes, and license in a scroll area |
| **Version X.Y.Z** | Non-clickable; running app version from `pyproject.toml` (same value as About, `--version`, and EXR `Software` metadata) |

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
