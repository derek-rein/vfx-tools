---
title: CLI reference
weight: 10
description: video2exr, exr2video, and GUI launch flags
---

Command-line interface for **video ↔ OpenEXR** conversion with **OpenColorIO**.
The same entry point launches the **GUI** when no convert subcommand is given.

Slate, burn-in, watermark, and post-convert toggles are **GUI-only** — see
[GUI](./gui.md).

```bash
# From a source checkout
uv run python main.py --help
uv run python main.py video2exr --help
uv run python main.py exr2video --help

# Packaged binary (examples)
exr_converter --help
"/Applications/EXR Converter.app/Contents/MacOS/exr_converter" --help
```

Related: [GUI](./gui.md) · [ProRes and VideoToolbox](./prores.md) · [Nuke integration](./nuke.md) · [README](../README.md)

---

## Modes

| Invocation | What it does |
|------------|----------------|
| `main.py` *(no subcommand)* | Open the GUI |
| `main.py video2exr …` | CLI: video → OCIO → EXR sequence |
| `main.py exr2video …` | CLI: image sequence (EXR / PNG / JPG / …) → OCIO → video |

Global flags (before the subcommand; convert flags may also appear after it):

| Flag | Meaning |
|------|---------|
| `-V` / `--version` | Print the app version (`pyproject.toml`) and exit |
| `--workers N` | CLI convert parallelism (`0` = auto, `1` = serial). May appear **before** the subcommand or **after** it (subcommand value wins). |
| `--smoke-test` | CI: launch GUI briefly, verify OCIO/ssl, exit |
| `--open PATH` | **GUI only:** open this media on launch |
| `--gui-ocio PATH` | **GUI only:** load this OCIO config on launch |
| `--mode auto\|video2exr\|exr2video` | **GUI only:** which tab (`auto` from `--open`) |

**Interrupt:** during CLI convert, **Ctrl-C** sets a cancel flag so the
pipeline stops cooperatively (pool work is cancelled) and the process exits
with status **130**.

---

## GUI launch (for shells & Nuke)

```bash
# Open GUI with an EXR sequence already loaded (EXR → Video tab)
uv run python main.py --open "/show/shot/plate.####.exr"

# Force Video → EXR tab + custom OCIO
uv run python main.py --open /path/clip.mov --mode video2exr \
  --gui-ocio /path/to/config.ocio

# Packaged app
open -a "EXR Converter" --args --open "/path/to/seq" --gui-ocio "$OCIO"
# or call the binary directly:
"/Applications/EXR Converter.app/Contents/MacOS/exr_converter" \
  --open "/path/to/seq" --gui-ocio "/path/to/config.ocio" --mode exr2video
```

OCIO resolution for the GUI when `--gui-ocio` is set: **custom file** source
(same as picking a config in the UI). Otherwise the app uses its saved preference
/ bundled ACES Studio / `$OCIO` as usual.

For a one-click Nuke menu that fills these in from a **Read** node, see
[nuke.md](./nuke.md).

---

## `video2exr` — video → EXR

```bash
uv run python main.py video2exr -i plate.mov
uv run python main.py video2exr -i plate.mov -o /tmp/exr_out --exr-compression zip
uv run python main.py video2exr -i plate.mov --frame-range 1-100 --workers 4
# Optional RED R3D / N-RAW (requires local R3D SDK bridge — docs/r3d.md):
uv run python main.py video2exr -i clip.R3D -o /tmp/exr_out \
  --src "Log3G10 REDWideGamutRGB" --dst ACEScg
```

| Option | Default | Notes |
|--------|---------|--------|
| `-i` / `--input` | *(required)* | Input video file (also `.r3d` / `.nev` when R3D support is built) |
| `-o` / `--output-dir` | `<input_dir>/<stem>/` | Directory for EXR frames |
| `--ocio` | bundled / `$OCIO` | Config file path |
| `--src` | auto | Stream color tags / codec ranking → **`Output - Rec.709`** (alias-resolved). R3D/N-RAW defaults toward **Log3G10 REDWideGamutRGB**. Not an OIIO still probe. |
| `--dst` | `ACEScg` / scene_linear | Destination scene space (role fallbacks; not media probing) |
| `--exr-compression` | `dwaa` | `none`, `rle`, `zip`, `zips`, `piz`, `pxr24`, `b44`, `b44a`, `dwaa`, `dwab` |
| `--dwa-level` | library | DWA level for `dwaa`/`dwab` (`0` = lossless) |
| `--zip-level` | library | ZIP level 1–9 for `zip`/`zips` |
| `--scale` | `1.0` | e.g. `0.5` half-res |
| `--padding` | `4` | Frame zero-pad width |
| `--start-frame` | `1001` | First output frame number |
| `--frame-range` | all | Nuke-style, e.g. `1-100`, `1-50x2` |
| `--deinterlace` | `auto` | `auto` / `on` / `off` |
| `--workers` | global | Parallel workers (`0` = auto, `1` = serial); overrides global `--workers` |

**Output naming:** `stem.####.exr` inside the output directory (pad width from
`--padding`). Default directory is `<input_parent>/<stem>/` (same idea as the GUI).

**RED R3D / N-RAW:** optional. Requires building the R3D bridge against the
official proprietary SDK — see [r3d.md](./r3d.md). Without it, `.r3d` / `.nev`
inputs error with a clear missing-SDK message.

---

## `exr2video` — image sequence → video

Primary path is **OpenEXR**. Also accepted: **DPX**, and display stills
**PNG**, **JPEG** (`.jpg` / `.jpeg`), **WebP**. Mixed folders prefer EXR,
then DPX, then display stills.

```bash
uv run python main.py exr2video -i ./plate
uv run python main.py exr2video -i ./plate -o review.mov --fps 24
uv run python main.py exr2video -i ./plate/plate.1001.exr -o review.mov --fps 24
uv run python main.py exr2video -i ./png_seq -o review.mp4 --codec h264 --fps 24
uv run python main.py exr2video -i ./plate --codec h264 --crf 18
```

| Option | Default | Notes |
|--------|---------|--------|
| `-i` / `--input` | *(required)* | Sequence **directory** or any **existing frame** from the sequence (``name.####.ext`` or ``name_####.ext``; not a literal `####` path that does not exist on disk) |
| `-o` / `--output` | next to sequence | Video path; default extension follows codec family (below) |
| `--fps` | `24` | Frame rate |
| `--ocio` | bundled / `$OCIO` | Config file path |
| `--src` | format-aware | EXR/DPX: metadata / `scene_linear`. PNG/JPEG/WebP: sRGB-ish display space |
| `--dst` | `Output - Rec.709` | Display / delivery space |
| `--scale` | `1.0` | Output scale |
| `--codec` | `prores` | Codec key — see ladder below (default = ProRes 422 HQ, **10-bit** software) |
| `--crf` | codec default | H.264 / HEVC quality |
| `--preset` | codec default | x264 / x265 preset name |
| `--frame-range` | all | e.g. `1001-1100` |
| `--workers` | global | Same meaning as `video2exr` |

**Default output path** when `-o` is omitted: sibling of the sequence folder,
named after the folder, with a codec-appropriate extension:

| Codec family | Default extension |
|--------------|-------------------|
| ProRes / CineForm (default path) | `.mov` |
| `h264`, `hevc`, `hevc_8`, `hevc_12` | `.mp4` |
| `dnxhr_*` | `.mxf` |
| `ffv1`, `ffv1_12` | `.mkv` |

### Codecs (honest bit depths)

Keys match `exr2video --codec`. Software ProRes is **always 10-bit** encode
(`prores_ks`); do not treat 4444/XQ as true 12-bit on that path.

**VideoToolbox** (`prores_vt_*`) is **Apple’s hardware ProRes encoder** on
**macOS only** — faster than software, and 4444/XQ keep **~12-bit class**
precision that `prores_ks` does not. Full explanation:
[ProRes and VideoToolbox](./prores.md).

| Key | Encode (this app) | Notes |
|-----|-------------------|--------|
| `prores_proxy` | 10-bit 4:2:2 | Software ProRes Proxy |
| `prores_lt` | 10-bit 4:2:2 | Software ProRes LT |
| `prores_422` | 10-bit 4:2:2 | Software ProRes 422 |
| `prores` | 10-bit 4:2:2 | **Default** — software ProRes 422 HQ |
| `prores_4444` | 10-bit 4:4:4:4 | Software; not true 12-bit |
| `prores_xq` | 10-bit 4:4:4:4 | Software; not true 12-bit |
| `prores_vt_proxy` | 10-bit 4:2:2 | VideoToolbox 422 Proxy (macOS) |
| `prores_vt_lt` | 10-bit 4:2:2 | VideoToolbox 422 LT (macOS) |
| `prores_vt_422` | 10-bit 4:2:2 | VideoToolbox 422 (macOS) |
| `prores_vt_hq` | 10-bit 4:2:2 | VideoToolbox 422 HQ (macOS) |
| `prores_vt_4444` | ~12-bit 4:4:4:4 | VideoToolbox 4444 (macOS) |
| `prores_vt_xq` | ~12-bit 4:4:4:4 | VideoToolbox 4444 XQ (macOS) |
| `prores_ox_proxy` | **12-bit** 4:2:2 | Experimental RDD-36 via oxideav; requires `make oxideav-prores` |
| `prores_ox_lt` | **12-bit** 4:2:2 | Experimental RDD-36 via oxideav |
| `prores_ox_422` | **12-bit** 4:2:2 | Experimental RDD-36 via oxideav |
| `prores_ox_hq` | **12-bit** 4:2:2 | Experimental RDD-36 via oxideav |
| `prores_ox_4444` | **12-bit** 4:4:4 | Experimental RDD-36 via oxideav |
| `prores_ox_xq` | **12-bit** 4:4:4 | Experimental RDD-36 via oxideav |
| `cineform` | 10-bit 4:2:2 | GoPro CineForm |
| `cineform_rgb` | 12-bit RGB | CineForm RGB |
| `dnxhr_lb` / `sq` / `hq` | 8-bit 4:2:2 | DNxHR |
| `dnxhr_hqx` | 10-bit 4:2:2 | DNxHR HQX |
| `dnxhr_444` | 10-bit 4:4:4 | DNxHR 444 |
| `h264` | 8-bit 4:2:0 | CRF / preset apply |
| `hevc` | 10-bit 4:2:0 | Default HEVC key |
| `hevc_8` | 8-bit 4:2:0 | |
| `hevc_12` | 12-bit 4:2:0 | |
| `ffv1` | 10-bit 4:4:4 | Lossless |
| `ffv1_12` | 12-bit 4:4:4 | Lossless |

List keys on your build (filters macOS-only codecs on other OSes):

```bash
uv run python main.py exr2video --help
```

User guide for the three ProRes encoders (software vs VideoToolbox vs oxideav):
[ProRes and VideoToolbox](./prores.md). Research notes for the oxideav path:
[plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md). Experimental
presets `prores_ox_*` use in-process oxideav PyO3 bindings when `exr_prores` is
built (`make oxideav-prores`); they are omitted from `--codec` choices
otherwise.

---

## Color management notes

- **Transforms** are applied with **PyOpenColorIO** (not OIIO’s colorconvert).
- Omitted **`--src`**: **video2exr** uses stream TRC/primaries/codec candidates
  then **`Output - Rec.709`**; **exr2video** uses still metadata / format
  (scene-linear vs sRGB-ish). Omitted **`--dst`**: fixed defaults + roles
  (`ACEScg` / scene_linear for video→EXR; Rec.709 for EXR→video), not media
  probing. Names remap via `find_equivalent_space` when needed.
- Packaged app needs **OpenColorIO 2.5+** for the bundled ACES Studio v4 config.
  From source: `make ensure-ocio` if OIIO rewired you to 2.4.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Convert / argument / codec error |
| `2` | Smoke test: OCIO runtime &lt; 2.5 |
| `3` | Smoke test: bundled config failed to load |

---

## See also

- `main.py --help`, `video2exr --help`, `exr2video --help` (always current)
- [GUI](./gui.md)
- [Nuke integration](./nuke.md)
- [Releasing](./releasing.md)
