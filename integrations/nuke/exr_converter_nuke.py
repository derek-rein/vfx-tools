"""Nuke helpers: open EXR Converter with a Read path + session OCIO.

Install
-------
Copy this file (and ``menu.py``) onto ``NUKE_PATH`` or into ``~/.nuke``, then
restart Nuke. See ``docs/nuke.md``.

Environment
-----------
``EXR_CONVERTER``
    Path to the ``exr_converter`` binary, or to ``python`` if launching from source.
``EXR_CONVERTER_ARGS``
    Optional extra argument inserted after the binary (e.g. path to ``main.py``
    when ``EXR_CONVERTER`` is a Python interpreter).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    import nuke
except ImportError:  # allow import outside Nuke for tooling
    nuke = None  # type: ignore[assignment]

_VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".mxf",
    ".webm",
    ".m4v",
    ".ts",
    ".mpg",
    ".mpeg",
}


def _message(msg: str) -> None:
    if nuke is not None:
        nuke.message(msg)
    else:
        print(msg, file=sys.stderr)


def find_exr_converter_command() -> list[str]:
    """Return argv prefix to launch EXR Converter, or raise FileNotFoundError."""
    env = os.environ.get("EXR_CONVERTER", "").strip()
    extra = os.environ.get("EXR_CONVERTER_ARGS", "").strip()
    candidates: list[str] = []
    if env:
        candidates.append(env)

    # Common install locations
    candidates.extend(
        [
            "/Applications/EXR Converter.app/Contents/MacOS/exr_converter",
            str(Path.home() / "Applications/EXR Converter.app/Contents/MacOS/exr_converter"),
            "exr_converter",
        ]
    )
    # Windows-style
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.append(str(Path(pf) / "EXR Converter" / "exr_converter.exe"))

    for c in candidates:
        p = Path(c).expanduser()
        if p.is_file() or (c == "exr_converter"):  # allow PATH lookup
            cmd = [str(p) if p.is_file() else c]
            if extra:
                # Support a single path (main.py) or shell-split later if needed
                cmd.append(extra)
            # PATH name: verify with which-like check only for bare name
            if p.is_file():
                return cmd
            # bare command on PATH
            from shutil import which

            hit = which(c)
            if hit:
                out = [hit]
                if extra:
                    out.append(extra)
                return out

    raise FileNotFoundError(
        "Could not find EXR Converter.\n\n"
        "Set EXR_CONVERTER to the binary, e.g.\n"
        '  export EXR_CONVERTER="/Applications/EXR Converter.app/Contents/MacOS/exr_converter"'
    )


def get_nuke_ocio_path() -> str:
    """Best-effort path to this Nuke session's OCIO config file."""
    if nuke is None:
        return os.environ.get("OCIO", "") or ""

    root = nuke.root()
    # Prefer an explicit custom file on the root.
    for knob_name in (
        "customOCIOConfigPath",
        "OCIO_config",
        "ocioConfigPath",
    ):
        try:
            kn = root[knob_name]
        except (NameError, KeyError, ValueError):
            continue
        try:
            val = kn.value()
        except Exception:
            continue
        if not val:
            continue
        path = Path(str(val)).expanduser()
        if path.is_file():
            return str(path)

    env = os.environ.get("OCIO", "")
    if env and Path(env).expanduser().is_file():
        return str(Path(env).expanduser())
    return ""


def _evaluate_read_file(node) -> str:
    """Return the evaluated file path from a Read (or similar) node."""
    try:
        kn = node["file"]
    except Exception as e:
        raise RuntimeError(f"Node has no 'file' knob: {node.name()}") from e
    # Prefer evaluate() so frame tokens expand where possible.
    try:
        path = kn.evaluate()
    except Exception:
        path = kn.value()
    return str(path or "").strip()


def guess_mode(path: str) -> str:
    """Return ``exr2video`` or ``video2exr`` from *path*."""
    p = Path(path.split()[0] if path else "")
    # Sequence patterns: plate.%04d.exr / plate.####.exr
    name = p.name.lower()
    if ".exr" in name or p.suffix.lower() == ".exr":
        return "exr2video"
    if p.suffix.lower() in _VIDEO_EXTS:
        return "video2exr"
    if p.is_dir():
        return "exr2video"
    return "exr2video"


def launch(
    open_path: str | None = None,
    ocio_path: str | None = None,
    mode: str | None = None,
) -> None:
    """Spawn EXR Converter (non-blocking)."""
    cmd = find_exr_converter_command()
    if open_path:
        cmd.extend(["--open", open_path])
    if ocio_path:
        cmd.extend(["--gui-ocio", ocio_path])
    if mode and mode != "auto":
        cmd.extend(["--mode", mode])

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        _message(f"Failed to launch EXR Converter:\n{e}\n\nCommand:\n{' '.join(cmd)}")
        return

    # Soft log in Nuke's script editor
    try:
        nuke.tprint("EXR Converter: " + " ".join(cmd))
    except Exception:
        pass


def open_selected_read() -> None:
    """Open the selected Read (or sole selected node with a file knob) in EXR Converter."""
    if nuke is None:
        _message("This command must be run inside Nuke.")
        return

    nodes = [n for n in nuke.selectedNodes() if n.Class() in ("Read", "ReadGeo", "DeepRead")]
    if not nodes:
        # Any selected node with a file knob
        nodes = [n for n in nuke.selectedNodes() if "file" in n.knobs()]
    if not nodes:
        _message("Select a Read node (or a node with a file path) first.")
        return
    if len(nodes) > 1:
        # Use the first; still useful in multi-select
        pass

    node = nodes[0]
    try:
        path = _evaluate_read_file(node)
    except RuntimeError as e:
        _message(str(e))
        return
    if not path:
        _message(f"{node.name()}: empty file path.")
        return

    ocio = get_nuke_ocio_path()
    mode = guess_mode(path)
    launch(open_path=path, ocio_path=ocio or None, mode=mode)


def open_this_read() -> None:
    """Callback for a knob on a Read — uses ``nuke.thisNode()``."""
    if nuke is None:
        return
    node = nuke.thisNode()
    try:
        path = _evaluate_read_file(node)
    except RuntimeError as e:
        _message(str(e))
        return
    if not path:
        _message(f"{node.name()}: empty file path.")
        return
    ocio = get_nuke_ocio_path()
    launch(open_path=path, ocio_path=ocio or None, mode=guess_mode(path))
