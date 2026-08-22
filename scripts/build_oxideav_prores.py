#!/usr/bin/env python3
"""Build the optional oxideav-prores PyO3 extension (``exr_prores``).

Requires a Rust toolchain (rustc/cargo) and maturin. The extension links
pure-Rust oxideav-prores and ships as a normal Python module so Nuitka
can include it without a subprocess sidecar.

Usage:
  python3 scripts/build_oxideav_prores.py
  # or:
  make oxideav-prores

Installs into the active environment (prefer ``uv run`` / project ``.venv``).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native" / "exr_prores"


def _have_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        action="store_true",
        default=True,
        help="Build release (default)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Build debug (overrides --release)",
    )
    args = parser.parse_args()
    release = not args.debug

    if not CRATE.is_dir():
        print(f"ERROR: missing crate at {CRATE}", file=sys.stderr)
        return 1
    if not _have_cmd("cargo") or not _have_cmd("rustc"):
        print(
            "ERROR: Rust toolchain required (cargo/rustc). Install from https://rustup.rs/",
            file=sys.stderr,
        )
        return 1

    maturin = shutil.which("maturin")
    if maturin is None:
        # Prefer project venv maturin if present.
        venv_maturin = ROOT / ".venv" / "bin" / "maturin"
        if venv_maturin.is_file():
            maturin = str(venv_maturin)
        else:
            print(
                "maturin not on PATH — installing into current environment…",
                file=sys.stderr,
            )
            subprocess.check_call([sys.executable, "-m", "pip", "install", "maturin>=1.7,<2.0"])
            maturin = shutil.which("maturin") or str(
                Path(sys.executable).resolve().parent / "maturin"
            )

    cmd = [maturin, "develop"]
    if release:
        cmd.append("--release")
    env = os.environ.copy()
    # Ensure cargo is visible when invoked via make/uv.
    path = env.get("PATH", "")
    cargo_bin = Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir() and str(cargo_bin) not in path:
        env["PATH"] = f"{cargo_bin}:{path}"
    print("+", " ".join(cmd), f"(cwd={CRATE})")
    subprocess.check_call(cmd, cwd=CRATE, env=env)

    # Smoke import.
    code = "import exr_prores; print(f'exr_prores {exr_prores.version()} OK')"
    subprocess.check_call([sys.executable, "-c", code], env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
