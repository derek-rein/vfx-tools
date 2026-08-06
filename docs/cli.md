# EXR Converter — CLI reference

Command-line interface for **video ↔ OpenEXR** conversion with **OpenColorIO**.
The same entry point launches the **GUI** when no convert subcommand is given.

```bash
# From a source checkout
uv run python main.py --help
uv run python main.py video2exr --help
uv run python main.py exr2video --help

# Packaged binary (examples)
exr_converter --help
"/Applications/EXR Converter.app/Contents/MacOS/exr_converter" --help
```

Related: [Nuke integration](./nuke.md) · [README](../README.md)

---

## Modes

| Invocation | What it does |
|------------|----------------|
| `main.py` *(no subcommand)* | Open the GUI |
| `main.py video2exr …` | CLI: video → OCIO → EXR sequence |
| `main.py exr2video …` | CLI: EXR sequence → OCIO → video |

Global flags (before the subcommand):

| Flag | Meaning |
|------|---------|
| `--workers N` | CLI convert parallelism (`0` = auto, `1` = serial). May appear **before** the subcommand or **after** it. |
| `--smoke-test` | CI: launch GUI briefly and exit |
| `--open PATH` | **GUI only:** open this media on launch |
| `--gui-ocio PATH` | **GUI only:** load this OCIO config on launch |
| `--mode auto\|video2exr\|exr2video` | **GUI only:** which tab (`auto` from `--open`) |

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
```

| Option | Default | Notes |
|--------|---------|--------|
| `-i` / `--input` | *(required)* | Input video file |
| `-o` / `--output-dir` | `<input_dir>/<stem>/` | Directory for EXR frames |
| `--ocio` | bundled / `$OCIO` | Config file path |
| `--src` | auto | Source color space (probe + aliases) |
| `--dst` | ACEScg / scene_linear | Destination scene space |
| `--exr-compression` | `dwaa` | `none`, `rle`, `zip`, `zips`, `piz`, `pxr24`, `b44`, `b44a`, `dwaa`, `dwab` |
| `--dwa-level` | library | DWA level for `dwaa`/`dwab` (`0` = lossless) |
| `--zip-level` | library | ZIP level 1–9 for `zip`/`zips` |
| `--scale` | `1.0` | e.g. `0.5` half-res |
| `--padding` | `4` | Frame zero-pad width |
| `--start-frame` | `1001` | First output frame number |
| `--frame-range` | all | Nuke-style, e.g. `1-100`, `1-50x2` |
| `--deinterlace` | `auto` | `auto` / `on` / `off` |

**Output naming:** `stem.####.exr` inside the output directory (pad width from
`--padding`).

---

## `exr2video` — EXR → video

```bash
uv run python main.py exr2video -i ./plate
uv run python main.py exr2video -i ./plate -o review.mov --fps 24
uv run python main.py exr2video -i ./plate/plate.1001.exr -o review.mov --fps 24
uv run python main.py exr2video -i ./plate --codec h264 --crf 18
```

| Option | Default | Notes |
|--------|---------|--------|
| `-i` / `--input` | *(required)* | Sequence **directory** or any **existing frame** from the sequence (not a literal `####` string) |
| `-o` / `--output` | next to sequence | Video path; extension follows codec if omitted |
| `--fps` | `24` | Frame rate |
| `--ocio` | bundled / `$OCIO` | Config file path |
| `--src` | EXR meta / scene_linear | Scene-linear source space |
| `--dst` | display Rec.709 | Display / delivery space |
| `--scale` | `1.0` | Output scale |
| `--codec` | platform default | See codec ladder below |
| `--crf` | codec default | H.264 / HEVC quality |
| `--preset` | codec default | x264 / x265 preset |
| `--frame-range` | all | e.g. `1001-1100` |

### Codecs (honest bit depths)

| Key pattern | Notes |
|-------------|--------|
| `prores_*` (software) | FFmpeg `prores_ks` — **10-bit** encode (not true 12-bit) |
| `prores_vt_*` | **macOS only** VideoToolbox; 4444/XQ ~12-bit |
| `dnxhr_*` | DNxHR LB…444 |
| CineForm keys | 10 / 12-bit variants |
| `h264`, `hevc`, `hevc_8`, `hevc_12` | Delivery; CRF/preset apply |
| `ffv1`, `ffv1_12` | Archival FFV1 |

List available keys on your build:

```bash
uv run python main.py exr2video --help
```

---

## Color management notes

- **Transforms** are applied with **PyOpenColorIO** (not OIIO’s colorconvert).
- Omitted `--src` / `--dst` use probing + role fallbacks and
  `find_equivalent_space` so names still resolve across configs.
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
- [Nuke integration](./nuke.md)
- [Releasing](../AGENTS.md#releasing-and-deployment)
