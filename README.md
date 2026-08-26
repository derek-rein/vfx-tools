# EXR Converter

Desktop app and CLI for **EXR ↔ video** workflows — built for **VFX dailies**, review exports, and plate round-trips with **OpenColorIO** color management end to end.

**Why people use it**

- **Dailies-ready EXR → video** — prepend a **slate** frame, per-frame **burn-ins** (show / shot / version / frame tokens), and a **watermark** (tiled text, opacity/size/angle); preview overlays live in the slate editor before you encode
- **ProRes out of the box** — full software ladder (Proxy → XQ, default **422 HQ**), **Apple VideoToolbox** on macOS (fast HW encode; 4444/XQ ~12-bit class), plus experimental cross-platform **true 12-bit** RDD-36 presets (`prores_ox_*`) in release builds
- **Built for speed** — multi-core **OCIO worker pools** (auto worker count, `--workers` on CLI), ordered frame delivery, optional half-res **`--scale`**, and GPU OCIO in the built-in player / slate preview hot path
- **OCIO your way** — ships **ACES Studio Config v4** (camera IDTs, ACES outputs); also `$OCIO`, custom `.ocio` files, other built-ins, and **local Nuke install configs** (path reference only) from the GUI picker

Under the hood: **PyAV** (FFmpeg) for video, **OpenImageIO** for EXR and still sequences (**DPX**, **PNG**, **JPEG**, **WebP**), **PySide6** + **QPainter** for slate/overlays (no embedded browser).

**Optional RED R3D / N-RAW:** when built with the official RED R3D SDK bridge, **Video → EXR** can decode `.r3d` and `.nev` (IPP2 primary → Log3G10 REDWideGamutRGB for OCIO), including browser thumbnails, sequence-player preview, and camera/timecode metadata on written EXRs. Release binaries may ship only RED’s allowed Redistributable libraries in a private app folder — see [docs/r3d.md](docs/r3d.md) and **Help → About** for the redistributable notice.

Targets the [VFX Reference Platform CY2026](https://vfxplatform.com/#reference-platform): Python 3.13, Qt/PySide 6.8, OpenColorIO 2.5, OpenEXR 3.4, NumPy 2.3.

## Downloads

[![Latest release](https://img.shields.io/github/v/release/derek-rein/exr-converter?label=latest)](https://github.com/derek-rein/exr-converter/releases)

| Platform | Download |
|----------|----------|
| Windows x64 | [**Installer (.exe)**](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-windows-x86_64-setup.exe) |
| macOS Apple Silicon | [**DMG**](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-macos-arm64.dmg) |
| macOS Intel | [**DMG**](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-macos-x86_64.dmg) |
| Linux x86_64 | [**AppImage**](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.AppImage) |

All release artifacts are [signed with Sigstore Cosign](https://docs.sigstore.dev/)
and carry [GitHub build provenance attestations](https://docs.github.com/en/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds)
— see the [releases page](https://github.com/derek-rein/exr-converter/releases)
for `cosign verify-blob` and `gh attestation verify` commands.

### Running on macOS

These builds use an **ad-hoc code signature** and are **not Apple-notarized** (like most open-source apps distributed outside the App Store). Because of that, Gatekeeper quarantines the download. After dragging **EXR Converter.app** into `/Applications`, clear the quarantine flag once from Terminal:

```bash
xattr -cr "/Applications/EXR Converter.app"
```

Then open it normally (double-click, or right-click → **Open**). If macOS still says the app "is damaged" or "can't be opened," that is the quarantine flag — the `xattr -cr` command above resolves it. This is the [standard macOS prompt](https://support.apple.com/en-us/102445) for apps without a paid Apple Developer signature; it does not indicate a problem with the build.

## Tech stack

| Layer | Notes |
|-------|--------|
| **Language & tooling** | Python 3.13, [uv](https://docs.astral.sh/uv/) for deps and runs, [Ruff](https://docs.astral.sh/ruff/) in CI, [Nuitka](https://nuitka.net/) for standalone bundles |
| **UI** | [PySide6](https://doc.qt.io/qtforpython/) (Qt 6.8), Nuke-inspired dark theme |
| **Imaging & color** | [OpenImageIO](https://openimageio.org/) (`oiio-python`), [OpenColorIO 2.5](https://opencolorio.org/) display/render transforms with a wide-gamut scene-linear **compositing space** for all overlay (slate / burn-in / watermark) compositing — prefers **ACES2065-1 (AP0)** via the `aces_interchange` role so sRGB-authored overlays are linearised and alpha-over'd without ever clipping or shifting the user's footage; falls back to the `scene_linear` role (e.g. ACEScg) on non-ACES configs.<br>Bundles the official **ACES Studio Config v4** (from [ASWF OpenColorIO-Config-ACES](https://github.com/AcademySoftwareFoundation/OpenColorIO-Config-ACES), BSD-3-Clause) which includes dozens of camera IDTs including **Apple Log** (iPhone 15/16 Pro cinematic / ProRes Log), ARRI LogC3/4, RED Log3G10, Sony S-Log/Venice, Canon, DJI, and many more. |
| **Video & sequences** | [PyAV](https://github.com/PyAV-Org/PyAV) (FFmpeg bindings) for video I/O, [fileseq](https://github.com/justinfx/fileseq) for frame sequences & ranges; optional **RED R3D SDK** bridge for `.r3d` / `.nev`; optional **oxideav-prores** PyO3 extension (`exr_prores`) for experimental cross-platform **12-bit** ProRes-compatible encode in release builds |
| **Slate / burn-in / watermark** | Native **QPainter** preview and offscreen capture (no embedded browser); burn-in and watermark are linearised into the working space and alpha-composited per-frame, then OCIO-transformed to display before encode |

CI runs on **GitHub Actions**; releases publish binaries for Linux, macOS (Apple Silicon + Intel), and Windows.

## Screenshot

![EXR Converter — EXR → Video](resources/screenshots/exr_converter_screenshot.png)

## GUI

```bash
uv run python main.py
```

No subcommand — opens the main window.

On **EXR → Video**, check **Prepend slate**, **Burn-in**, and **Watermark** to build review/dailies exports in one pass. The slate editor shows live overlays with GPU OCIO preview. Convert presets remember codec, scale, and color spaces (not I/O paths).

**OCIO config picker:** bundled **ACES Studio v4** (recommended default), `$OCIO`, custom file, other built-ins, or a **Nuke install config** (uses your local Foundry OCIO — not redistributed). Source/destination spaces are grouped by family (Input/ARRI, Input/Apple, Output/Rec.709, …). Override from CLI with `--ocio PATH` or `--src` / `--dst`.

Common camera sources on the bundled config include **Apple Log**, **ARRI LogC3**, **Log3G10 REDWideGamutRGB**, Sony, Canon, DJI, and many more.

## Documentation

Guides live under [`docs/`](docs/) (Markdown source of truth). The public site is
built with Hugo from [`site/`](site/) and published to GitHub Pages:

**https://derek-rein.github.io/exr-converter/**

| Guide | |
|-------|--|
| [CLI](docs/cli.md) | `video2exr` / `exr2video` / GUI launch flags |
| [GUI](docs/gui.md) | Tabs, overlays, preferences, post-convert, codec picker |
| [R3D / N-RAW](docs/r3d.md) | Optional RED SDK (license, build, CI, preview) |
| [12-bit ProRes (oxideav)](docs/plan-12bit-prores-oxideav.md) | Experimental RDD-36 12-bit ProRes via PyO3 |
| [Nuke](docs/nuke.md) | Menu: open selected Read + session OCIO |

```bash
make docs-serve   # local preview → http://127.0.0.1:1313/
make docs-build   # write site/public/
```

## CLI

**Full reference:** [docs/cli.md](docs/cli.md) · **GUI:** [docs/gui.md](docs/gui.md) · **Nuke:** [docs/nuke.md](docs/nuke.md)

Use the `video2exr` or `exr2video` subcommand to convert, or run with **no**
subcommand to open the GUI (optionally with `--open` / `--gui-ocio`).

**Video → EXR**

```bash
uv run python main.py video2exr -i clip.mov -o ./exr_out/
```

**EXR / image sequence → video**

```bash
uv run python main.py exr2video -i ./plate -o review.mov --fps 24
# or any frame file from the sequence:
uv run python main.py exr2video -i ./plate/plate.1001.exr -o review.mov --fps 24
# PNG / JPEG / DPX sequences work the same way:
uv run python main.py exr2video -i ./png_seq -o review.mp4 --codec h264 --fps 24
uv run python main.py exr2video -i ./dpx_seq -o review.mov --fps 24
```

**GUI with path pre-loaded** (also used by the Nuke integration)

```bash
uv run python main.py --open ./plate --gui-ocio "$OCIO" --mode exr2video
```

Common convert options:

| Option | Applies to | Notes |
|--------|------------|--------|
| `--ocio PATH` | both convert commands | OCIO config file (overrides `$OCIO`) |
| `--src` / `--dst` | both | OCIO color space names |
| `--workers N` | both | `0` = auto, `1` = single-threaded |
| `--scale FACTOR` | both | e.g. `0.5` for half resolution |
| `--exr-compression NAME` | `video2exr` | e.g. `dwaa`, `zip`, `none` (see `--help`) |
| `--codec KEY` | `exr2video` | ProRes / DNxHR / CineForm / HEVC / H.264 / FFV1 — see [docs/cli.md](docs/cli.md) for bit-depth notes and codec keys |

### EXR → video codecs (ProRes and more)

Default **`prores`** = software **ProRes 422 HQ** (cross-platform, **10-bit** encode). GUI codec menu is grouped by family.

| Family | Keys | Notes |
|--------|------|--------|
| **ProRes (software)** | `prores_proxy` … `prores_xq` | FFmpeg `prores_ks`; all profiles encode **10-bit** |
| **ProRes (VideoToolbox)** | `prores_vt_*` | **macOS only** — Apple HW encoder; faster; 4444/XQ ~12-bit class |
| **ProRes (oxideav)** | `prores_ox_4444`, `prores_ox_xq` | Experimental **true 12-bit** RDD-36 in release builds |
| **Also** | DNxHR, CineForm, H.264/HEVC, FFV1 | Delivery and lossless options — see [docs/cli.md](docs/cli.md#codecs-honest-bit-depths) |

**Quick picks:** macOS dailies → **`prores_vt_hq`** or **`prores_vt_4444`**; cross-platform ProRes → **`prores`**; cross-platform 12-bit 4:4:4 → **`prores_ox_4444`** (experimental). Software 4444/XQ are **not** true 12-bit despite probe labels.

```bash
uv run python main.py --help
uv run python main.py video2exr --help
uv run python main.py exr2video --help
```

## Requirements (running from source)

- **Python 3.13**
- [uv](https://docs.astral.sh/uv/) (recommended) or another PEP 621-compatible installer

```bash
git clone https://github.com/derek-rein/exr-converter.git
cd exr-converter
make sync   # uv sync + ensure OpenColorIO 2.5+ (see below)
```

> **OpenColorIO 2.5 note:** The bundled ACES Studio config needs OCIO **2.5+**.
> Installing / upgrading `oiio-python` can rewire `PyOpenColorIO` to its vendored
> **2.4** dylib. If you see *“config is version 2.5… library (2.4.0) is not able
> to load”*, run `make ensure-ocio` (or `make sync`).

## Building from source

Prerequisites: **Python 3.13**, [**uv**](https://docs.astral.sh/uv/), and a C compiler (Xcode CLT on macOS, MSVC on Windows, gcc on Linux).

```bash
uv sync
make bundle
```

This uses [Nuitka](https://nuitka.net/) to produce a standalone distributable:

| Platform | Output |
|----------|--------|
| macOS | `dist/EXR Converter.app` |
| Linux | `dist/exr_converter` (single binary) |
| Windows | `dist\main.dist\` (folder with `exr_converter.exe` + dependencies) |

Nuitka will auto-download `ccache` on first run. See the `Makefile` for the full set of flags.

## Development

| Target | Purpose |
|--------|---------|
| `make run` | Start the GUI |
| `make lint` / `make fmt` | Ruff check / format |
| `make test` | Run the pytest suite (unit + integration) |
| `make test-unit` | Unit tests only (skip integration) |
| `make resources` | Regenerate `src/rc_resources.py` from `resources.qrc` (needed after icon changes) |
| `make bundle` | Nuitka standalone bundle under `dist/` |
| `make oxideav-prores` | Build optional PyO3 12-bit ProRes extension (needs Rust + maturin) |
| `make clean` | Remove all build artifacts |

All static assets live under `resources/`: icons in `resources/icons/` (`icon.icns` / `icon.ico` / `icon.png`), UI images in `resources/images/`, the Qt stylesheet at `resources/style.qss`, the bundled OCIO config in `resources/ocio/`, and docs imagery in `resources/screenshots/`.

## Releases

Tags use plain semver: `v1.2.3`. Pushing a tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml) and publishes a GitHub Release with Linux AppImage, macOS DMGs (ARM64 + Intel), and a Windows installer.

| Doc | Contents |
|-----|----------|
| **[CHANGELOG.md](CHANGELOG.md)** | User-facing history (update with every visible change) |
| **[AGENTS.md](AGENTS.md#releasing-and-deployment)** | Full release process: protected `main`, PR, Makefile, `gh`, CI gates |

```bash
git checkout -b release/X.Y.Z
# Roll CHANGELOG [Unreleased] → [X.Y.Z] first
make release PART=minor PUSH=0   # bump + commit (+ optional local tag; main is PR-only)
git push -u origin HEAD
gh pr create --base main --title "release: X.Y.Z"
# after merge: Auto-tag release pushes vX.Y.Z if missing → Release workflow runs
gh run watch
gh release list --limit 5
```

### CI / release gates

| Workflow | When | Gate |
|----------|------|------|
| [CI](.github/workflows/ci.yml) | push / PR to `main` | Ruff + **full pytest suite** (unit + integration) on Linux, macOS, Windows. Aggregate job `ci-ok` is green only if every matrix cell passes. |
| [Release](.github/workflows/release.yml) | tag `v*` | Same lint + full suite must pass (`gate` job) **before** Nuitka builds, Cosign, or the GitHub Release is created. A failing test aborts the release; no artifacts are published. |

Enable **branch protection** on `main` and require the `ci-ok` status check so merges also require a green suite.

## License

MIT — see [`LICENSE`](LICENSE).

[derekvfx.ca](https://derekvfx.ca)

![](https://umami.derekvfx.ca/p/c3Aaarpz7)
