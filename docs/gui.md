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
| **Video → EXR** | Decode video → OCIO → EXR sequence | No slate / burn-in / watermark |
| **EXR → Video** | EXR sequence → OCIO → video | Slate, burn-in, and watermark available |

Mode can be forced with `--mode video2exr|exr2video`, or inferred from `--open`
(`auto`: EXR-like paths open **EXR → Video**, common video extensions open
**Video → EXR**).

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
| **Watermark** | Text watermark (opacity, size, angle, optional tile) |

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
| **Open result** | off | Open the **video** with the preferred player (Preferences). No-op for EXR output — use **Show in folder**. |
| **Show in folder** | off | Reveal the file or sequence folder in the OS file manager |

### Preferences (`File → Preferences…`)

| Setting | Purpose |
|---------|---------|
| **Video player** | System default, or a custom app/CLI path (IINA, VLC, mpv, …). Used by **Open result**. |
| **Slate thumbnail frame** | First / middle / last frame of the EXR sequence for the slate still |

Settings org/app: `QSettings("VFXTools", "EXRConverter")`.

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
