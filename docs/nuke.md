---
title: Nuke integration
weight: 20
description: Open a Read node and session OCIO from Nuke
---

Launch **EXR Converter** from Nuke with:

- the selected **Read** node’s file path (or the node under the cursor), and  
- this Nuke session’s **OCIO config** path when available.

Scripts live under [`integrations/nuke/`](../integrations/nuke/).

---

## Install

### 1. Point at the app (optional but recommended)

Set an environment variable to the **binary** (not only the `.app` wrapper):

```bash
# macOS app bundle binary
export EXR_CONVERTER="/Applications/EXR Converter.app/Contents/MacOS/exr_converter"

# Windows (example)
set EXR_CONVERTER=C:\Program Files\EXR Converter\exr_converter.exe

# or a source checkout
export EXR_CONVERTER="/path/to/exr-converter/.venv/bin/python"
export EXR_CONVERTER_ARGS="/path/to/exr-converter/main.py"   # optional second token
```

If `EXR_CONVERTER` is unset, the script probes common install locations:

- `/Applications/EXR Converter.app/Contents/MacOS/exr_converter`
- `~/Applications/EXR Converter.app/…`
- `%ProgramFiles%\EXR Converter\exr_converter.exe`
- `exr_converter` on `PATH`

### 2. Load the menu

**Option A — drop into `~/.nuke`**

```bash
cp integrations/nuke/exr_converter_nuke.py ~/.nuke/
cp integrations/nuke/menu.py ~/.nuke/exr_converter_menu.py
```

Ensure `~/.nuke/menu.py` imports it (create or append):

```python
try:
    import exr_converter_menu  # noqa: F401
except ImportError:
    pass
```

**Option B — `NUKE_PATH` plugin folder**

```text
$NUKE_PATH/
  exr_converter_nuke.py
  menu.py          # contents of integrations/nuke/menu.py
```

Restart Nuke. You should see:

**Nuke menu → EXR Converter**

| Command | Action |
|---------|--------|
| **Open selected Read…** | Launch with that Read’s path + session OCIO |
| **Open EXR Converter only…** | Launch the app with no pre-filled path |

Also under **Nodes → EXR Converter** for discoverability.

---

## Usage

1. Select a **Read** (or leave nothing selected and run the command with a
   single Read selected in the DAG).
2. **EXR Converter → Open selected Read…**
3. The GUI opens on **EXR → Video** with that path and Nuke’s OCIO config
   pre-selected (when a real `.ocio` path can be resolved).

Video Reads open the **Video → EXR** tab instead.

---

## What gets passed

```text
exr_converter --open <read file> --gui-ocio <config.ocio> --mode exr2video|video2exr
```

| Source | Knob / logic |
|--------|----------------|
| Media path | Read `file` knob (evaluated), sequence-friendly |
| OCIO | Root `customOCIOConfigPath` (and a few aliases) when set and on disk; else `$OCIO`; else omit (app default) |
| Mode | `.exr` / sequence → `exr2video`; common video extensions → `video2exr` |

See [CLI — GUI launch](./cli.md#gui-launch-for-shells--nuke) and [GUI](./gui.md)
for what the app does with those flags.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Menu missing | Confirm `menu.py` is loaded; check Script Editor for import errors |
| “Could not find EXR Converter” | Set `EXR_CONVERTER` to the binary path |
| Wrong OCIO | Set a custom OCIO path on the Nuke root, or `export OCIO=/path/config.ocio` before Nuke |
| App opens but empty input | Path may not expand (relative/missing frames); use an absolute evaluated path on the Read |
| OCIO greyed out in the app | Linked OpenColorIO cannot load that config (often version skew); pick a compatible config or fix OCIO 2.5 linkage |

---

## API (for custom tools)

```python
import exr_converter_nuke as ecn

# Selected node(s)
ecn.open_selected_read()

# Launch only
ecn.launch()

# Explicit path
ecn.launch(open_path="/show/shot/plate.%04d.exr", ocio_path="/configs/studio.ocio")
```

See module docstrings in `exr_converter_nuke.py`.
