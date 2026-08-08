---
title: 12-bit ProRes plan (oxideav)
weight: 90
description: Research notes — not implemented
---

**Status:** research complete · implementation not started  
**Date:** 2026-08-06  
**Related code:** `src/core/constants.py`, `tests/test_codecs.py`, Nuitka `Makefile` / `.github/workflows/release.yml`

This document records bit-depth findings for FFmpeg/ProRes in EXR Converter, why software ProRes is 10-bit only, and a concrete plan to ship **true 12-bit ProRes-compatible** encodes on Windows, Linux, and macOS using the pure-Rust **oxideav-prores** stack, bundled with **Nuitka**.

---

## 1. Goals

| Goal | Notes |
|------|--------|
| True **12-bit** encode precision for a ProRes-family intermediate | Not just probe metadata that *says* 12-bit |
| **Cross-platform** | Win / Linux / macOS (arm64 + x86_64) in release CI |
| Fits existing **Nuitka standalone** packaging | No PyOxidizer migration |
| Honest UI | Experimental until interop is proven; never overclaim |
| Keep FFmpeg path for everything else | PyAV remains default for ProRes 10-bit, DNxHR, HEVC, etc. |

**Non-goals (v1):**

- Replacing all ProRes with oxideav  
- Apple-licensed / certified ProRes branding  
- ProRes RAW  
- In-process PyO3 until the sidecar is proven  

---

## 2. Findings: bit depth today

### 2.1 Measurement method (solid-patch mid-bin)

Encode two constant full-frame RGB fields in `rgb48le`:

- **B** — 10-bit lattice point in full-range 16-bit (e.g. `16384`, multiple of 64)  
- **B+32** — mid-bin between two 10-bit codes (needs **>10 bits** to distinguish)

Decode back to `rgb48le`, compare center-crop means:

| Δ (mean(B+32) − mean(B)) | Interpretation |
|--------------------------|----------------|
| **≈ 0** | Mid-bin collapsed → effective **≤10-bit** encode |
| **≈ 32** (or clearly >12) | Mid-bin kept → **12-bit-class** (or deeper) |

ProRes is lossy; exact 32 is not required, but collapse to 0 is definitive for 10-bit quantization.

Automated regressions live in `tests/test_codecs.py` (`TestBitDepthRoundtrip`).

### 2.2 Results (FFmpeg 8.1.1 + PyAV, local)

| Encoder | Path / pix_fmt | Mid-bin Δ | Claimed by probe | **True encode depth** |
|---------|----------------|-----------|------------------|------------------------|
| `prores_ks` 422 HQ | `yuv422p10le` | **0** | 10 | **10** |
| `prores_ks` 4444 | `yuva444p10le` | **0** | often **12** (`yuva444p12le`) | **10** |
| `prores_ks` XQ | `yuva444p10le` | **0** | often **12** | **10** |
| `prores_ks` + `yuva444p12le` | — | **rejected** | — | cannot open |
| `prores_videotoolbox` 422 | `p210le` | **0** | 10 | **10** |
| `prores_videotoolbox` 4444/XQ | `ayuv64le` | **~18–37** | 12 | **~12-bit class** |
| `libx265` 10 | `yuv420p10le` | **0** | 10 | **10** |
| `libx265` 12 | `yuv420p12le` | **~37** | 12 | **12** |
| `ffv1` 12 | `yuv444p12le` | **~32** | 12 | **12** |
| `cfhd` RGB | `gbrp12le` | **32** | 12 | **12** |

### 2.3 Why “FFmpeg ProRes is 12-bit” is a myth

1. **Encoder capability list** for `prores_ks` / `prores_aw`:
   ```text
   Supported pixel formats: yuv422p10le yuv444p10le yuva444p10le
   ```
   No `*12le` formats. Requesting 12-bit → auto-select or open failure.

2. **Decoder presentation:** FFmpeg often surfaces 4444/XQ as `yuva444p12le` with `bits_per_raw_sample=12` even when only 10 bits of precision were encoded. **Do not trust probe alone.**

3. **Industry sources agree** (encode, not decode):
   - [ASWF Encoding Guidelines — ProRes](https://academysoftwarefoundation.github.io/EncodingGuidelines/EncodeProres.html): *“prores_ks can only encode to 10-bits”*; 4444xq row notes *“ffmpeg will only generate up to 10-bit.”*
   - FFmpeg Trac **#8054** — *prores_ks codec does not allow 12bit pix_fmt* (open enhancement for years).
   - FFmpeg Trac **#7163** — 12-bit *decode* improvements; does not fix encode.
   - Community (BMD forums, Hybrid/Selur, vhs-decode wiki): same conclusion — FFmpeg still cannot *render* true 12-bit ProRes.

4. **Historical “max 10-bit FFmpeg” confusion** often mixed:
   - Old separate 8-bit vs 10-bit **libx264** builds  
   - Hardware encoders (NVENC, SVT-AV1) capped at 10  
   - ProRes software encode cap  
   with the FFmpeg **pixel format** layer, which fully supports 8–16-bit.

### 2.4 App alignment (already done)

After measurement, presets were corrected so labels match encode reality:

| Key family | `bit_depth` | Notes |
|------------|-------------|--------|
| `prores_*` (`prores_ks`) | **10** including 4444/XQ | `yuva444p10le` |
| `prores_vt_*` 422 | **10** | macOS |
| `prores_vt_4444` / `prores_vt_xq` | **12** | VT mid-bin keep |
| `hevc_12` | **12** | `yuv420p12le` Main 12 |
| `ffv1_12` | **12** | `yuv444p12le` |
| `cineform_rgb` | **12** | `gbrp12le` |

Cross-platform **true 12-bit** options today: **CineForm RGB**, **HEVC 12**, **FFV1 12**.  
Cross-platform **ProRes** with true 12-bit: **not available via FFmpeg**.

### 2.5 Licensed / NLE paths (not embeddable)

True cross-platform 12-bit ProRes exists in **closed tools**, not as a free library we can ship:

- Apple **VideoToolbox** — macOS only (we already expose this).  
- **Adobe** Premiere / Media Encoder — Win + Mac, licensed.  
- **DaVinci Resolve 19.1.4+** — ProRes encode on Win/Linux (2025); not a library.  
- Apple **ProRes Program** / commercial SDKs — license + redistribution terms.

These do not help a Nuitka-shipped Python app without a full NLE dependency.

---

## 3. Candidate: oxideav-prores

### 3.1 What it is

| Item | Detail |
|------|--------|
| Crate | [`oxideav-prores`](https://crates.io/crates/oxideav-prores) (docs: [docs.rs](https://docs.rs/oxideav-prores/)) |
| Org | [OxideAV](https://github.com/OxideAV) — pure-Rust media framework |
| Spec | **SMPTE RDD 36:2022** (published ProRes bitstream) |
| Profiles | All six: 422 Proxy/LT/Standard/HQ, **4444**, **4444 XQ** |
| Depths | **8 / 10 / 12 / 16-bit** YUV (`*P12Le`, `Yuva*P12Le`, etc.) |
| Alpha | Lossless alpha on 4444/XQ (per docs) |
| License | **MIT** |
| Dependencies | No `*-sys` / no system FFmpeg for the codec itself |

CLI surface of the wider project: `cargo install oxideav-cli` → `oxideav` (probe / remux / transcode). Codec can also be used as a library with oxideav container crates for MOV.

### 3.2 Why it fits EXR Converter

- Only public **cross-platform** stack that **claims** what FFmpeg never shipped: **encode** 12-bit 4444/XQ.  
- Pure Rust → static-ish helper binary, easy to CI-build on Win/Linux/macOS.  
- Same packaging model as “ship a native helper next to the frozen app.”  
- Leaves PyAV/FFmpeg for the rest of the codec ladder.

### 3.3 Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Interop (FCP / Premiere / Resolve reject or mis-decode) | **High** | Gate release on open-in-NLE tests + mid-bin proof |
| Young crate (0.x) | Medium | Pin version; experimental preset; optional disable |
| Not Apple-licensed | Medium (product/legal) | UI: “RDD-36 ProRes-compatible (experimental)” — avoid implying Apple certification |
| Mux / color metadata / alpha edge cases | Medium | v1: progressive 4444 12-bit, no alpha, explicit BT.709 tags |
| Encode speed vs `prores_ks` / VT | Unknown | Benchmark before making default |
| Maintenance of custom CLI + CI | Medium | Thin binary; pin Cargo.lock in repo |

---

## 4. Architecture

### 4.1 Chosen approach: sidecar helper (v1)

```
┌─────────────────────────────────────────────────────────────┐
│  EXR Converter (Python / Nuitka)                            │
│  OIIO + OCIO → display/working RGB (rgb48le)                │
│  slate / burn-in / watermark (existing path)                │
└───────────────────────────┬─────────────────────────────────┘
                            │ frames: temp PNG16 / raw planar
                            │ or stdin protocol (later)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  helpers/exr-prores  (Rust, release binary)                 │
│  oxideav-prores encode 12-bit 4444 / XQ                     │
│  oxideav MOV mux → .mov                                     │
└─────────────────────────────────────────────────────────────┘
```

**Why not PyO3 first:** binding + maturin wheels for four OS targets before the codec is proven is wasted work. Sidecar is testable with CLI alone and matches Nuitka `--include-data-files`.

**Why not full oxideav replace PyAV:** EXR, OCIO, and most codecs stay on OIIO/PyAV; only the 12-bit ProRes *output* path needs oxideav.

### 4.2 Suggested helper CLI (draft)

```text
exr-prores encode \
  --input-glob './tmp/frame.%04d.png' \
  --fps 24 \
  --profile 4444|xq \
  --bit-depth 12 \
  --pix-fmt yuv444p12   # or yuva444p12 when alpha ready \
  --color-primaries bt709 --color-trc bt709 --colorspace bt709 \
  --output out.mov
```

Constraints for v1:

- Progressive only  
- Even dimensions (ProRes macroblock rules)  
- Explicit colour tags (oxideav docs warn unknown 4444 metadata can break some decoders)  
- Exit non-zero on failure; print progress to stderr  

### 4.3 App integration points

| Area | Change |
|------|--------|
| `src/core/constants.py` | New keys e.g. `prores_ox_4444`, `prores_ox_xq` — `bit_depth=12`, chroma `4:4:4` / `4:4:4:4`, libav placeholder or custom flag |
| `src/core/convert.py` | Branch: if oxideav preset → write frame sequence (or pipe) → run helper → skip PyAV ProRes encode |
| `src/gui/widgets.py` | Help text: experimental; require helper present |
| `src/cli.py` | Same keys in `--codec` choices; default `.mov` |
| Helper discovery | Mirror OCIO frozen-path logic (`sys.executable` parent, macOS `Contents/MacOS/helpers/`, dev `helpers/`) |
| Absence of helper | Hide presets or disable with clear message (dev without Rust still works) |

Do **not** set `bit_depth=12` on `prores_ks` keys. Keep FFmpeg software ProRes honest at 10-bit.

### 4.4 Packaging (Nuitka — already the release path)

Repo already uses Nuitka standalone (`make bundle`, release matrix). **No PyOxidizer.**

1. **CI build job (each OS):** install Rust → `cargo build --release -p exr-prores` → place binary in `helpers/`.  
2. **Nuitka:**  
   ```text
   --include-data-files=helpers/exr-prores=helpers/exr-prores
   ```  
   (Windows: `exr-prores.exe`; post-step for `.app` layout if needed.)  
3. **Runtime:** resolve `helpers/exr-prores` next to the frozen executable.  
4. **Size:** expect a few MB for a focused binary; acceptable next to Qt/OIIO.

Dev: optional `make helper` / document `cargo build` so contributors can test without waiting for CI artifacts.

---

## 5. Implementation phases

### Phase 0 — Spike (local only) ✅ research done · ⬜ binary not built

- [ ] Create `tools/exr-prores/` (or `helpers/exr-prores/`) Cargo project  
- [ ] Depend on pinned `oxideav-prores` + MOV mux crates  
- [ ] Encode one solid-color 12-bit frame to `.mov`  
- [ ] Run **mid-bin test** (B vs B+32) on oxideav output  
- [ ] Open file in **Resolve** and **ffmpeg/ffprobe** (note: probe may still say 12-bit; trust mid-bin + visual/NLE)  
- [ ] Go/no-go: mid-bin kept **and** Resolve opens without black/error  

**Exit criteria:** mid-bin Δ clearly > 12; file plays in at least one major NLE on one OS.

### Phase 1 — Helper CLI + tests

- [ ] Stable CLI contract (args as above)  
- [ ] Cargo.lock committed; pinned crate versions  
- [ ] Unit/integration: mid-bin for 4444 and XQ at 12-bit  
- [ ] Optional: 10-bit encode path for parity comparison with `prores_ks`  
- [ ] Document build: `cargo build --release -p exr-prores`  

### Phase 2 — App wiring (behind experimental flag)

- [ ] Presets `prores_ox_4444` / `prores_ox_xq` in `VIDEO_CODECS`  
- [ ] Convert path: temp frame dump → subprocess → output `.mov`  
- [ ] Progress/cancel: kill helper process; clean temp dir  
- [ ] UI badge: “experimental · RDD-36 · requires helper”  
- [ ] Tests: skip if helper missing; run mid-bin when present  

### Phase 3 — CI + Nuitka

- [ ] `release.yml` / local `make bundle`: Rust toolchain + helper build  
- [ ] Include helper in all four artifacts (linux-x64, macos-arm64, macos-x86_64, windows-x64)  
- [ ] Smoke: mid-bin on each platform in CI (headless)  
- [ ] Manual checklist: open sample MOV in Resolve/Premiere/FCP before marking non-experimental  

### Phase 4 — Harden (only if Phase 0–3 pass)

- [ ] Alpha channel if needed  
- [ ] Pipe/raw protocol to avoid PNG temp I/O  
- [ ] Performance pass (threads, fewer copies)  
- [ ] Consider PyO3 only if subprocess overhead is a real problem  
- [ ] Promote UI copy from experimental → stable if interop is solid  

---

## 6. Acceptance tests

| Test | Pass criteria |
|------|----------------|
| Mid-bin solid patch | Δ(B+32 − B) **> 12** (ideally ~32) for oxideav 12-bit 4444 |
| Contrast with `prores_ks` | Same sources: `prores_ks` Δ **≈ 0** (regression guard) |
| Encode smoke | Helper produces non-empty `.mov`; ffprobe shows ProRes 4444/XQ |
| NLE open | Resolve (Win/Mac/Linux as available) opens timeline, no black frame |
| Frozen path | Nuitka app finds helper; experimental preset completes end-to-end |
| Cancel | Killing job terminates helper; no orphan processes |
| Missing helper | Clear error; no crash; FFmpeg codecs still work |

---

## 7. Labeling & honesty policy

| Product surface | Copy |
|-----------------|------|
| Software FFmpeg ProRes 4444/XQ | **10-bit** encode (`prores_ks`) |
| VideoToolbox 4444/XQ | **12-bit class** (macOS) |
| oxideav presets (v1) | **12-bit · experimental · RDD-36 ProRes-compatible** |
| Marketing | Do not say “Apple ProRes certified” or “identical to Final Cut ProRes” until interop matrix is complete |

This matches the project rule already encoded in `VideoCodecSpec`: *never imply higher precision than we actually encode.*

---

## 8. Alternatives if oxideav fails go/no-go

| Need | Path already in app |
|------|---------------------|
| Cross-platform true 12-bit intermediate | `cineform_rgb`, `ffv1_12` |
| Cross-platform 12-bit delivery | `hevc_12` |
| macOS true high-depth ProRes | `prores_vt_4444` / `prores_vt_xq` |
| Cross-platform ProRes for review | `prores` / `prores_ks` **10-bit** (honest) |

ASWF also pushes **OpenAPV** as a modern open intermediate; optional future preset, separate from ProRes.

---

## 9. Rough effort estimate

| Phase | Effort (order of magnitude) |
|-------|-----------------------------|
| Phase 0 spike | 1–2 days (including NLE checks) |
| Phase 1 CLI + tests | 2–4 days |
| Phase 2 app wiring | 2–3 days |
| Phase 3 CI + Nuitka | 1–2 days |
| Phase 4 harden | as needed |

Blocked primarily on **interop confidence**, not packaging.

---

## 10. References

### Internal

- `src/core/constants.py` — codec ladder + FFmpeg bit-depth comments  
- `tests/test_codecs.py` — mid-bin / pix_fmt honesty tests  
- `Makefile` — `make bundle` (Nuitka)  
- `.github/workflows/release.yml` — multi-OS release gate + build  

### External

- [ASWF Encode ProRes](https://academysoftwarefoundation.github.io/EncodingGuidelines/EncodeProres.html)  
- [ASWF Encode HEVC](https://academysoftwarefoundation.github.io/EncodingGuidelines/EncodeHevc.html)  
- FFmpeg Trac [#8054](https://trac.ffmpeg.org/ticket/8054), [#7163](https://trac.ffmpeg.org/ticket/7163)  
- [oxideav-prores](https://github.com/OxideAV/oxideav-prores) · [docs.rs](https://docs.rs/oxideav-prores/) · [OxideAV site](https://oxideav.github.io/)  
- SMPTE **RDD 36** (ProRes bitstream)  
- Apple ProRes white paper (format capability “up to 12-bit”; not FFmpeg software encode capability)  

### Local measurement note

Solid-patch mid-bin runs (2026-08) confirmed `prores_ks` 4444/XQ Δ=0 and VT 4444/XQ mid-bin keep; results drove the preset relabel and the decision to pursue oxideav rather than further FFmpeg flags.

---

## 11. Decision log

| Date | Decision |
|------|----------|
| 2026-08-06 | Confirmed FFmpeg `prores_ks` cannot encode true 12-bit; app labels fixed |
| 2026-08-06 | Added `hevc_12` and `ffv1_12` as honest cross-platform 12-bit options |
| 2026-08-06 | Chose **oxideav sidecar + Nuitka include** as plan for experimental cross-platform 12-bit ProRes-compatible output |
| — | *Pending:* Phase 0 go/no-go on mid-bin + Resolve open |

---

## 12. Next action

1. Scaffold `tools/exr-prores` and prove mid-bin + NLE open (**Phase 0**).  
2. If green, pin crates and land Phase 1 CLI under the repo with CI-optional build.  
3. Only then wire experimental presets and Nuitka packaging.
