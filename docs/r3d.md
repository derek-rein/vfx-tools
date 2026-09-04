---
title: RED R3D / N-RAW support
weight: 40
description: Optional RED R3D SDK integration for .R3D and Nikon N-RAW
---

**EXR Converter** can decode **RED R3D** (`.r3d`) and **Nikon N-RAW** (`.nev`)
clips when built against the official **RED R3D Software Developer’s Kit**.
Without the SDK bridge, those extensions are recognized in the UI but conversion
fails with a clear “SDK missing” message.

Related: [CLI](./cli.md) · [GUI](./gui.md) · [AGENTS.md](../AGENTS.md)

---

## What you get

| Item | Behavior |
|------|----------|
| Formats | `.r3d` (classic multi-part + R3D NE), `.nev` (N-RAW) |
| Decode | R3D SDK IPP2 **Primary Development** → **REDWideGamutRGB + Log3G10** |
| OCIO | Auto-detect source space prefers Log3G10 / RWG names on the active config |
| Output | Same video→EXR pipeline (EXR compression, scale ladder, frame range, workers) |
| GPU | **macOS Metal** (`GpuDecoder` + REDMetal) for classic R3D GPU decompress. **Windows / Linux:** SDK `R3DDecoder` — CUDA if an NVIDIA device is present, otherwise OpenCL. CPU fallback when no GPU init, or for clips Metal cannot GPU-decompress (N-RAW / R3D NE / RED ONE on macOS). Force CPU with `EXR_CONVERTER_R3D_CPU=1`. |
| Preview | Sequence player + video browser (half-res decode for scrub) |
| Thumbnails | Grid thumbs via sixteenth-res decode (fast ID, not full premium) |
| Metadata | Clip + per-frame timecode written to EXR as ``exrconverter:r3d:*`` attrs |

Scale maps to the SDK resolution ladder (full / half / quarter / …) for faster
proxies; odd scale factors may apply a small software resize after decode.

The SDK is cross-platform, but GPU APIs are not one path: **Metal** on macOS
(full GPU decompress), **CUDA then OpenCL** on Windows/Linux via managed
`R3DDecoder` (CPU decompress + GPU IPP2). CUDA needs an NVIDIA driver and the
CUDA runtime (`cudart`) on the library path; OpenCL needs a GPU ICD. No extra
compile-time CUDA/OpenCL SDK is required to build the bridge.

---

## License (read this)

The R3D SDK is **proprietary** (RED.COM / Nikon). Terms live in the
**SDK License Agreement** shipped with the SDK package — always verify the
copy you downloaded.

Summary of constraints that affect this project (not a substitute for the legal text):

- **Royalty-free** use to build applications that add significant functionality.
- You may redistribute **only** the **Redistributable** dynamic libraries (+ LUTs
  in original form) in a **private (non-shared) directory** used only by your app.
- You must **not** redistribute: headers, static libraries, sample source, or
  documentation.
- End-user distribution must include required **EULA** language protecting RED
  (no reverse engineering, ownership, disclaimers, liability limits, etc.).
- Pure open-source licenses can conflict with those mandatory end-user
  restrictions. Practical options: optional/offline install of redistributables,
  clear docs, and a product EULA when shipping binaries that include RED libs.

**Do not commit the R3D SDK tree into the public git repository.** This repo only
contains *our* bridge source (`native/r3d/`) and Python glue (`src/core/r3d.py`).

Contact RED for edge cases: `RED-r3dsdk@nikon.com`.

---

## Where the full SDK lives (maintainers)

| Location | Purpose |
|----------|---------|
| `~/code/r3d-sdk-private/R3DSDKv9_2_1/` | Local full SDK unpack (headers + static + redistributable) |
| Private GitHub repo [`derek-rein/r3d-sdk-private`](https://github.com/derek-rein/r3d-sdk-private) | README + package script only (**no SDK blobs in git**) |
| Private Release tag `sdk-9.2.1` asset `R3DSDKv9_2_1-full.tar.gz` | **CI build-time feed** for public `exr-converter` Releases |

Public **GitHub Release artifacts** for EXR Converter may include only:

- `libr3d_bridge.{dylib,so,dll}`
- RED `Redistributable/*` dynamic libraries

…under a private app folder (`…/r3d/`), never headers or static libs.

### Refresh the private CI feed

```bash
# After unpacking a new official SDK under ~/code/r3d-sdk-private/R3DSDKv9_*
cd ~/code/r3d-sdk-private
./scripts/package_release.sh
# retags/uploads R3DSDKv9_2_1-full.tar.gz to release sdk-9.2.1
```

---

## Developer setup (local)

```bash
# Full SDK already at the recommended path:
#   ~/code/r3d-sdk-private/R3DSDKv9_2_1

cd ~/code/exr-converter
make r3d-bridge
# → build/r3d/libr3d_bridge.* + build/r3d/redistributable/

# Or pull the same tarball CI uses (needs gh auth or R3D_SDK_READ_TOKEN):
make r3d-sdk-fetch   # → .r3d-sdk/R3DSDKv9_2_1 (gitignored)
make r3d-bridge
```

Override discovery:

| Env var | Meaning |
|---------|---------|
| `R3D_SDK_ROOT` | Unpacked SDK root for **building** the bridge |
| `EXR_CONVERTER_R3D_BRIDGE` | Path to `libr3d_bridge.*` (or its directory) |
| `EXR_CONVERTER_R3D_LIBS` / `R3D_SDK_LIBS` | Folder containing `REDR3D.*` redistributables |
| `EXR_CONVERTER_R3D_CPU` | Set to `1` to skip GPU decode (CPU only) |
| `R3D_SDK_READ_TOKEN` | PAT that can download the private Release asset |

Convert:

```bash
uv run python main.py video2exr -i /path/clip.R3D -o /tmp/exr_out \
  --src "Log3G10 REDWideGamutRGB" --dst ACEScg
```

---

## CI / GitHub Release builds

The **Release** workflow (Nuitka multi-OS):

1. If secret **`R3D_SDK_READ_TOKEN`** is set → download `sdk-9.2.1` / `R3DSDKv9_2_1-full.tar.gz` from the private repo.
2. Build `libr3d_bridge` (macOS / Linux / Windows + MSVC).
3. After Nuitka, copy **bridge + Redistributable only** into `…/r3d/` next to the executable.
4. Refuse the build if headers / `.a` / `.lib` appear under that folder.

If the secret is missing, Release still publishes the app **without** R3D support.

```bash
# Set or rotate the secret (fine-grained PAT with read on r3d-sdk-private is ideal)
gh secret set R3D_SDK_READ_TOKEN --repo derek-rein/exr-converter
```

---

## Packaging layout (shipped)

```text
macOS:  EXR Converter.app/Contents/MacOS/r3d/
Linux:  <dist>/r3d/
Windows:<dist>/r3d/
          libr3d_bridge.*
          REDR3D.*  REDDecoder.*  …   # original Redistributable names only
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `R3D bridge library not found` | `make r3d-bridge` / set `EXR_CONVERTER_R3D_BRIDGE` |
| `RED Redistributable libraries not found` | Set `EXR_CONVERTER_R3D_LIBS` or rebuild bridge (copies redistributables) |
| `InitializeSdk failed` | Wrong folder (must contain `REDR3D.*`); SDK version mismatch |
| CI skips R3D | Secret `R3D_SDK_READ_TOKEN` not set or cannot read private release |
| Wrong colors | Use Log3G10 / RWG source (auto-detect) — primary pipeline is not display Rec.709 |
| Multi-part classic R3D | Point at any part (e.g. `…_001.R3D`); the SDK loads siblings |
| `._….R3D` in browser | macOS AppleDouble metadata next to the real clip — hidden from the video browser (not media) |
| `.RMD` next to clip | RED metadata recipe sidecar — not listed as video (open the `.R3D`) |
| Convert log says `CPU` on a KOMODO `.R3D` | GPU init failed (Metal / CUDA / OpenCL). Rebuild `make r3d-bridge`. CUDA needs a NVIDIA driver + `cudart` next to the RED libs; OpenCL needs a GPU ICD. Force CPU with `EXR_CONVERTER_R3D_CPU=1` to compare. |

Sample clips (from RED / Nikon, not this repo):

- https://www.red.com/sample-r3d-files  
- Nikon N-RAW / R3D NE sample downloads (see SDK `Readme.txt`)
