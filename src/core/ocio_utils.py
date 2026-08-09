from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import PyOpenColorIO as OCIO

from .constants import (
    BUNDLED_ACES_STUDIO_KEY,
    OCIO_SOURCE_BUNDLED,
    OCIO_SOURCE_ENV,
    OCIO_SOURCE_FILE,
)
from .nuke_discover import (
    find_nuke_ocio_configs,
    is_nuke_source_key,
    resolve_nuke_config_path,
)

if TYPE_CHECKING:
    import numpy as np


def list_builtin_configs() -> list[tuple[str, str, bool]]:
    """Return [(internal_name, display_label, is_recommended), ...]."""
    reg = OCIO.BuiltinConfigRegistry()
    results = []
    for entry in reg.getBuiltinConfigs():
        name, label = entry[0], entry[1]
        recommended = entry[2] if len(entry) > 2 else False
        results.append((name, label, recommended))
    return results


def get_bundled_aces_studio_path() -> Path | None:
    """Locate the bundled 'super awesome' ACES Studio config (v4 / ACES 2.0).

    This is the official AcademySoftwareFoundation/OpenColorIO-Config-ACES
    studio config. It is a single small .ocio file (uses OCIO built-in
    transforms) containing a wide variety of camera input transforms/IDTs
    (ARRI Alexa, RED, Sony Venice, Canon, DJI, Apple Log, etc.) plus modern
    ACES Output Transforms. It is legally redistributable (BSD-3-Clause).

    Tries several locations to work in dev, Nuitka onefile, and especially
    macOS .app bundles created with --macos-create-app-bundle +
    --include-data-files.
    """
    filename = "aces-studio-v4.ocio"
    rel_path = Path("resources") / "ocio" / filename

    # 1. Direct relative to CWD (most dev runs)
    cand = Path.cwd() / rel_path
    if cand.is_file():
        return cand

    # 2. Walk up from this module (dev layouts, editable installs, etc.)
    here = Path(__file__).resolve()
    for _ in range(8):
        cand = here.parent / rel_path
        if cand.is_file():
            return cand
        if here.parent == here:
            break
        here = here.parent

    # 3. Frozen / Nuitka / PyInstaller style bundles
    is_frozen = getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")
    if is_frozen:
        # Nuitka typically sets sys.executable to the launcher inside the bundle
        exe = Path(sys.executable)
        base = Path(getattr(sys, "_MEIPASS", exe.parent))

        candidates = [
            base / rel_path,  # Contents/MacOS/resources/...
            base / "resources" / "ocio" / filename,
            base.parent / "Resources" / rel_path,  # Contents/Resources/...
            base / "Contents" / "Resources" / rel_path,
            exe.parent / rel_path,
            exe.parent / "resources" / "ocio" / filename,
        ]
        for p in candidates:
            if p and p.is_file():
                return p

        # Classic macOS .app layout: executable is in Contents/MacOS/
        if "Contents" in exe.parts:
            contents = exe.parents[exe.parts.index("Contents")]
            for sub in ("MacOS", "Resources", "."):
                p = contents / sub / rel_path if sub != "." else contents / rel_path
                if p.is_file():
                    return p
            # Sometimes data lands directly under Contents/
            p = contents / rel_path
            if p.is_file():
                return p

    # 4. Package data fallback (rare for this layout)
    try:
        import importlib.resources as ir

        for pkg in ("src.ocio_configs", "ocio_configs", "src"):
            try:
                if hasattr(ir, "files"):
                    root = ir.files(pkg)
                    if pkg.endswith("configs"):
                        p = root / filename
                    else:
                        p = root / "ocio_configs" / filename
                    if p.is_file():
                        return Path(str(p))
            except Exception:
                continue
    except Exception:
        pass

    return None


def list_app_configs() -> list[tuple[str, str, bool]]:
    """App-provided configs (our bundled super config first)."""
    p = get_bundled_aces_studio_path()
    if p:
        label = "ACES Studio Config (v4 • ACES 2.0 • cameras)"
        return [(BUNDLED_ACES_STUDIO_KEY, label, True)]
    # Fallback: if for some reason the file isn't there, surface nothing extra
    return []


def is_ocio_config_loadable(path: str | Path) -> tuple[bool, str]:
    """Return ``(ok, error_message)`` for whether *path* loads with this OCIO.

    Uses the process-linked :mod:`PyOpenColorIO` (normally 2.5+ after
    ``make ensure-ocio``). Configs that need builtins or profile versions this
    library lacks — e.g. some Foundry Nuke Studio ACES 2.0 configs when OCIO
    was rewired to 2.4 — return ``(False, …)``.
    """
    p = Path(path)
    if not p.is_file():
        return False, f"config file not found: {p}"
    try:
        OCIO.Config.CreateFromFile(str(p))
        return True, ""
    except Exception as e:
        return False, str(e).strip() or type(e).__name__


def list_nuke_configs() -> list[tuple[str, str, bool, bool, str]]:
    """Local Nuke install configs (path references only — never bundled).

    Returns ``[(key, label, recommended, compatible, detail), ...]``.

    *recommended* is True for the first **compatible** Studio-kind entry
    (newest Nuke × preferred kind). *compatible* is False when the current
    OpenColorIO cannot load the file; the UI should still list those entries
    greyed-out. *detail* is the load error (or empty when compatible).
    """
    configs = find_nuke_ocio_configs()
    if not configs:
        return []

    # Probe once per unique path (cheap enough for a handful of Nuke configs).
    loadable: dict[str, tuple[bool, str]] = {}
    for cfg in configs:
        key = str(cfg.path)
        if key not in loadable:
            loadable[key] = is_ocio_config_loadable(cfg.path)

    out: list[tuple[str, str, bool, bool, str]] = []
    recommended_set = False
    for cfg in configs:
        ok, err = loadable[str(cfg.path)]
        label = f"{cfg.label}  (local)"
        if not ok:
            label = f"{cfg.label}  (incompatible)"
        recommend = False
        if ok and not recommended_set and cfg.kind.startswith("studio"):
            recommend = True
            recommended_set = True
        out.append((cfg.key, label, recommend, ok, err))
    return out


def _is_frozen_app() -> bool:
    """True when running inside a Nuitka / frozen binary (not a source venv)."""
    if getattr(sys, "frozen", False):
        return True
    # Nuitka sets __compiled__ on compiled modules.
    return globals().get("__compiled__") is not None


def _ocio_version_mismatch_hint(runtime: str) -> str:
    if _is_frozen_app():
        return (
            f"Runtime OpenColorIO is {runtime}; this app build needs 2.5+ to load "
            f"the bundled ACES Studio config.\n"
            f"This is a packaging bug (OpenImageIO’s OCIO 2.4 dylib was linked "
            f"instead of OpenColorIO 2.5). Install a newer EXR Converter release, "
            f"or run from source with: make ensure-ocio && make run"
        )
    return (
        f"Runtime OpenColorIO is {runtime}; the bundled ACES Studio config "
        f"needs 2.5+.  oiio-python sometimes rewires PyOpenColorIO to its "
        f"vendored 2.4 dylib.  Fix with:\n"
        f"  make ensure-ocio\n"
        f"  # or: uv pip install --reinstall-package opencolorio 'opencolorio>=2.5.1'"
    )


def _create_from_file(path: str | Path) -> OCIO.Config:
    """Load a config file, rewriting version-mismatch errors into a fix hint."""
    p = str(path)
    try:
        return OCIO.Config.CreateFromFile(p)
    except Exception as e:
        msg = str(e)
        if "not able to load that config version" in msg or "Maximum minor version" in msg:
            runtime = OCIO.GetVersion()
            raise RuntimeError(f"{msg}\n\n{_ocio_version_mismatch_hint(runtime)}") from e
        raise


def resolve_ocio_config(source: str, builtin_name: str = "", file_path: str = "") -> OCIO.Config:
    if source == OCIO_SOURCE_ENV:
        env = os.environ.get("OCIO", "")
        if env:
            p = Path(env).expanduser()
            if p.is_file():
                return _create_from_file(p)
        raise RuntimeError("$OCIO environment variable is not set or not a valid file.")
    if source == OCIO_SOURCE_FILE:
        if not file_path:
            raise RuntimeError("No config file path specified.")
        p = Path(file_path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"OCIO config not found: {file_path}")
        return _create_from_file(p)
    if source == OCIO_SOURCE_BUNDLED or source == BUNDLED_ACES_STUDIO_KEY:
        p = get_bundled_aces_studio_path()
        if p and p.is_file():
            # Do not silently fall back on version errors — that left the UI green
            # while convert failed with OCIO 2.4. Only fall back if the file is
            # missing entirely.
            return _create_from_file(p)
        # File missing from the bundle: try library studio configs.
        for candidate in (
            "studio-config-v2.2.0_aces-v1.3_ocio-v2.4",
            "studio-config-v2.1.0_aces-v1.3_ocio-v2.3",
            "studio-config-latest",
        ):
            try:
                return OCIO.Config.CreateFromBuiltinConfig(candidate)
            except Exception:
                continue
        raise RuntimeError(
            "Bundled ACES Studio config not found (requires OCIO 2.5+ at runtime) "
            "and no library fallback available. "
            + ("Reinstall EXR Converter." if _is_frozen_app() else "Run: make ensure-ocio")
        )
    if is_nuke_source_key(source):
        p = resolve_nuke_config_path(source)
        if p is None or not p.is_file():
            raise RuntimeError(
                f"Nuke OCIO config no longer found ({source}). "
                "Is Nuke still installed? Pick another config or reinstall Nuke."
            )
        return _create_from_file(p)
    return OCIO.Config.CreateFromBuiltinConfig(source or builtin_name)


def load_config_from_source_info(config_source: str = "", config_path: str = "") -> OCIO.Config:
    """Load an OCIO config from the picklable (source, path) pair used by workers.

    Prefer *config_path* when it points at a real file; otherwise load a
    builtin named by *config_source*.  Falls back to :func:`resolve_ocio_for_cli`
    when neither is usable so callers never get ``None``.
    """
    if config_path:
        p = Path(config_path).expanduser()
        if p.is_file():
            try:
                return _create_from_file(p)
            except RuntimeError:
                raise
            except Exception:
                pass
    if config_source:
        try:
            return OCIO.Config.CreateFromBuiltinConfig(config_source)
        except Exception:
            pass
    return resolve_ocio_for_cli(None)


def resolve_ocio_for_cli(ocio_arg: str | None) -> OCIO.Config:
    if ocio_arg:
        p = Path(ocio_arg).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"OCIO config not found: {ocio_arg}")
        return _create_from_file(p)
    env = os.environ.get("OCIO")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return _create_from_file(p)
    # Prefer our bundled super config (rich cameras) when available
    app_cfgs = list_app_configs()
    if app_cfgs:
        key = app_cfgs[0][0]
        try:
            return resolve_ocio_config(key)
        except Exception:
            pass
    # Otherwise best library builtin
    builtins = list_builtin_configs()
    recommended = [b for b in builtins if b[2]]
    name = recommended[0][0] if recommended else builtins[-1][0]
    return OCIO.Config.CreateFromBuiltinConfig(name)


def color_space_families(config: OCIO.Config) -> dict[str, list[str]]:
    families: dict[str, list[str]] = defaultdict(list)
    for name in config.getColorSpaceNames():
        cs = config.getColorSpace(name)
        fam = cs.getFamily() or "Other"
        families[fam].append(name)
    return dict(families)


def resolve_alias(config: OCIO.Config, name: str) -> str:
    """Return the canonical color-space name for *name*, checking aliases.

    OCIO 2.x color spaces can have aliases (e.g. "ACEScg" might be aliased
    as "ACES - ACEScg" or "acescg").  ``config.getColorSpace(name)`` already
    resolves aliases, so if it returns a valid object the canonical name is
    ``cs.getName()``.

    We also apply a small set of app-level common-name fallbacks for popular
    camera logs (including Apple Log for iPhone cinematic footage) so users
    can type intuitive names like "apple log", "iphone log", "prores log", etc.
    """
    if not name:
        return ""
    # App-level convenience aliases for common camera encodings.
    # These help even if the active config uses slightly different naming.
    extra_aliases = {
        # Apple / iPhone
        "apple log": "Apple Log",
        "applelog": "Apple Log",
        "apple_log": "Apple Log",
        "iphone log": "Apple Log",
        "iphone": "Apple Log",
        "prores log": "Apple Log",
        "alog": "Apple Log",
        # Other popular shortcuts (extend as needed)
        "arri logc": "ARRI LogC3 (EI800)",
        "arri": "ARRI LogC3 (EI800)",
        "red": "Log3G10 REDWideGamutRGB",
        "red log3g10": "Log3G10 REDWideGamutRGB",
        "sony": "S-Log3 SGamut3.Cine",
        "venice": "S-Log3 SGamut3.Cine",
    }
    lowered = name.strip().lower()
    if lowered in extra_aliases:
        candidate = extra_aliases[lowered]
        try:
            cs = config.getColorSpace(candidate)
            if cs is not None:
                return cs.getName()
        except Exception:
            pass
        # If the preferred candidate isn't present, fall through to normal lookup
    try:
        cs = config.getColorSpace(name)
        if cs is not None:
            return cs.getName()
    except Exception:
        pass
    return ""


def _normalize_cs_token(name: str) -> str:
    """Collapse a colorspace name for fuzzy equality across configs."""
    s = name.strip().lower()
    # Drop common family prefixes used by ACES/CG configs.
    for prefix in (
        "output - ",
        "input - ",
        "utility - ",
        "aces - ",
        "display - ",
        "role - ",
    ):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    # Strip parenthetical EI / encoding notes: "ARRI LogC3 (EI800)" → "arri logc3"
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


# Ordered groups of names that typically mean the same encoding across configs
# (ACES studio, CG, Nuke default, SPI, etc.).  Earlier entries are preferred
# when multiple candidates exist in the target config.
_EQUIV_GROUPS: tuple[tuple[str, ...], ...] = (
    (
        "ACEScg",
        "ACES - ACEScg",
        "lin_ap1",
        "ACES cg",
        "acescg",
    ),
    (
        "ACES2065-1",
        "ACES - ACES2065-1",
        "lin_ap0",
        "aces2065-1",
    ),
    (
        "ACEScct",
        "ACES - ACEScct",
        "acescct",
    ),
    (
        "ACEScc",
        "ACES - ACEScc",
    ),
    # Display-referred Rec.709 / video output (prefer proper display transforms)
    (
        "Output - Rec.709",
        "Rec.1886 Rec.709 - Display",
        "Gamma 2.4 Rec.709 - Display",
        "Gamma2.4 Rec.709 - Display",
        "Rec.709 - Display",
        "Rec.709",
        "rec709",
        "Output - Rec.709 (D60 sim.)",
        "sRGB Encoded Rec.709 (sRGB)",
    ),
    (
        "sRGB - Display",
        "Output - sRGB",
        "sRGB",
        "srgb",
        "sRGBf",
    ),
    (
        "Raw",
        "raw",
        "Utility - Raw",
        "data",
    ),
    (
        "linear",
        "Linear",
        "scene_linear",
        "lin_rec709",
        "Linear Rec.709 (sRGB)",
        "Linear Rec.709",
        "Utility - Linear - sRGB",
        "lin_srgb",
        "reference",
    ),
    (
        "Cineon",
        "cineon",
        "ADX10",
        "adx10",
    ),
    (
        "Apple Log",
        "AppleLog",
        "Apple Log Encoding",
    ),
)


def _lookup_in_config(config: OCIO.Config, name: str) -> str:
    """Return canonical name if *name* is a role, alias, or colorspace."""
    if not name:
        return ""
    try:
        cs = config.getColorSpace(name)
        if cs is not None:
            return cs.getName()
    except Exception:
        pass
    # Explicit role names (scene_linear, etc.)
    try:
        role_cs = config.getColorSpace(name.strip())
        if role_cs is not None:
            return role_cs.getName()
    except Exception:
        pass
    return ""


def find_equivalent_space(config: OCIO.Config, name: str) -> str:
    """Map *name* from a previous config to a space that exists in *config*.

    Resolution order:
      1. Exact / OCIO alias / role via :func:`resolve_alias`
      2. Known cross-config equivalence groups (ACES, Rec.709, …)
      3. Normalized fuzzy match against all spaces in *config*

    Returns ``""`` when nothing equivalent is found (caller should mark
    the UI selection invalid and require a manual pick).
    """
    if not name or config is None:
        return ""

    # 1. Direct / alias / convenience
    hit = resolve_alias(config, name)
    if hit:
        return hit
    hit = _lookup_in_config(config, name)
    if hit:
        return hit

    # 2. Equivalence groups (prefer earlier candidates in each group)
    needle = _normalize_cs_token(name)
    if needle:
        for group in _EQUIV_GROUPS:
            norms = {_normalize_cs_token(g) for g in group}
            if needle not in norms and name not in group:
                continue
            for candidate in group:
                found = _lookup_in_config(config, candidate)
                if found:
                    return found
            # Config may use a name not listed — match by normalized token,
            # preferring group order when multiple spaces normalize the same.
            by_norm: dict[str, str] = {}
            for cs_name in config.getColorSpaceNames():
                by_norm.setdefault(_normalize_cs_token(cs_name), cs_name)
            for candidate in group:
                hit = by_norm.get(_normalize_cs_token(candidate))
                if hit:
                    return hit

    # 3. Fuzzy: exact normalized token match against every space / alias
    if needle:
        for cs_name in config.getColorSpaceNames():
            if _normalize_cs_token(cs_name) == needle:
                return cs_name
            try:
                cs = config.getColorSpace(cs_name)
                if cs is not None:
                    for alias in cs.getAliases() or []:
                        if _normalize_cs_token(alias) == needle:
                            return cs_name
            except Exception:
                continue

        # 4. Soft containment for long unique names (e.g. camera IDTs)
        if len(needle) >= 8:
            soft: list[str] = []
            for cs_name in config.getColorSpaceNames():
                tok = _normalize_cs_token(cs_name)
                if needle in tok or tok in needle:
                    soft.append(cs_name)
            if len(soft) == 1:
                return soft[0]

    return ""


def make_cpu_processor(config: OCIO.Config, src: str, dst: str) -> OCIO.CPUProcessor:
    return config.getProcessor(src, dst).getDefaultCPUProcessor()


# ---------------------------------------------------------------------------
# App anchor config — guaranteed spaces for *internal* transforms
# ---------------------------------------------------------------------------
#
# User configs (Nuke, $OCIO, show LUTs) are uncontrolled.  Anything the app
# authors itself (slate / burn-in / watermark paint → scene-linear) must not
# depend on them.  We keep a private ACES CG/Studio config as the **anchor**:
#
#   * texture_paint / sRGB authoring
#   * aces_interchange → ACES2065-1 (AP0)
#   * scene_linear → ACEScg
#
# Overlays are linearised on the anchor, then bridged into the user config's
# compositing space via ``aces_interchange`` when the user config provides it
# (same AP0 encoding by definition).  User src/dst convert still uses only
# the user config.

_app_anchor_config: OCIO.Config | None = None


def get_app_anchor_config() -> OCIO.Config:
    """Return a process-wide ACES config the app fully controls.

    Preference order: OCIO CG built-in (lean, always in the library) → Studio
    built-in → bundled Studio file → any recommended built-in.  Raises only if
    the linked OpenColorIO cannot provide any ACES config (broken install).
    """
    global _app_anchor_config
    if _app_anchor_config is not None:
        return _app_anchor_config

    preferred = (
        "cg-config-v4.0.0_aces-v2.0_ocio-v2.5",
        "cg-config-v2.2.0_aces-v1.3_ocio-v2.4",
        "studio-config-v4.0.0_aces-v2.0_ocio-v2.5",
        "studio-config-v2.2.0_aces-v1.3_ocio-v2.4",
        "cg-config-latest",
        "studio-config-latest",
    )
    for name in preferred:
        try:
            _app_anchor_config = OCIO.Config.CreateFromBuiltinConfig(name)
            return _app_anchor_config
        except Exception:
            continue

    # Bundled on-disk Studio (same family as library studio built-in).
    try:
        p = get_bundled_aces_studio_path()
        if p is not None and p.is_file():
            _app_anchor_config = _create_from_file(p)
            return _app_anchor_config
    except Exception:
        pass

    try:
        for bname, _label, recommended in list_builtin_configs():
            if not recommended:
                continue
            try:
                _app_anchor_config = OCIO.Config.CreateFromBuiltinConfig(bname)
                return _app_anchor_config
            except Exception:
                continue
        for bname, _label, _rec in list_builtin_configs():
            try:
                _app_anchor_config = OCIO.Config.CreateFromBuiltinConfig(bname)
                return _app_anchor_config
            except Exception:
                continue
    except Exception:
        pass

    raise RuntimeError(
        "No OCIO app-anchor config available. OpenColorIO 2.5+ with ACES "
        "built-ins is required for slate/overlay colour management."
    )


def _cs_name(config: OCIO.Config, name: str) -> str:
    """Canonical colorspace name if *name* resolves, else ``\"\"``."""
    if not name:
        return ""
    try:
        cs = config.getColorSpace(name)
        if cs is not None:
            return cs.getName()
    except Exception:
        pass
    return ""


def get_interchange_space(config: OCIO.Config) -> str:
    """Return ``aces_interchange`` / ACES2065-1 on *config*, or ``\"\"`` if absent."""
    role = getattr(OCIO, "ROLE_INTERCHANGE_SCENE", "aces_interchange")
    for name in (
        role,
        "aces_interchange",
        "ACES2065-1",
        "ACES - ACES2065-1",
        "lin_ap0",
    ):
        hit = _cs_name(config, name)
        if hit:
            return hit
    return ""


def get_working_space(config: OCIO.Config) -> str:
    """Return the canonical name of the OCIO ``scene_linear`` role.

    Used for the **user** convert path (source → working → dest).  Falls back
    to common alternate names.  For app-authored overlays prefer
    :func:`get_app_anchor_config` + :func:`linearize_overlay` instead of
    assuming this exists on an arbitrary show config.
    """
    candidates = (
        OCIO.ROLE_SCENE_LINEAR,
        "scene_linear",
        "compositing_linear",
        "ACES - ACEScg",
        "ACEScg",
        "Linear Rec.709 (sRGB)",
        "lin_rec709",
    )
    for name in candidates:
        hit = _cs_name(config, name)
        if hit:
            return hit
    raise RuntimeError("Could not resolve a scene-linear working colorspace from the OCIO config.")


def get_compositing_space(config: OCIO.Config) -> str:
    """Scene-linear space used to alpha-over overlays onto user frames.

    Prefers **ACES2065-1** via ``aces_interchange`` when *config* has it (wide
    gamut, matches app-anchor overlay linearisation).  Falls back to
    ``scene_linear``.  Overlay **paint** linearisation itself always runs on
    the app anchor — see :func:`linearize_overlay`.
    """
    ix = get_interchange_space(config)
    if ix:
        return ix
    return get_working_space(config)


def get_overlay_authoring_space(config: OCIO.Config) -> str:
    """sRGB-encoded paint space on *config* (roles first, then common names).

    For **internal** paint (slate / burn-in), call with
    :func:`get_app_anchor_config` so the result is guaranteed.  User configs
    may lack these roles.
    """
    texture_role = getattr(OCIO, "ROLE_TEXTURE_PAINT", "texture_paint")
    picking_role = getattr(OCIO, "ROLE_COLOR_PICKING", "color_picking")
    candidates = (
        texture_role,
        picking_role,
        "sRGB Encoded Rec.709 (sRGB)",
        "sRGB - Texture",
        "sRGB Texture",
        "Utility - sRGB - Texture",
        "sRGB",
        "Output - sRGB",
        "srgb",
    )
    for name in candidates:
        hit = _cs_name(config, name)
        if hit:
            return hit
    return get_working_space(config)


def get_internal_overlay_authoring_space() -> str:
    """Guaranteed sRGB paint space from the app anchor config."""
    return get_overlay_authoring_space(get_app_anchor_config())


def get_internal_interchange_space() -> str:
    """Guaranteed ACES2065-1 (``aces_interchange``) from the app anchor config."""
    anchor = get_app_anchor_config()
    ix = get_interchange_space(anchor)
    if not ix:
        raise RuntimeError("App anchor OCIO config has no aces_interchange / ACES2065-1.")
    return ix


def _apply_rgb_processor(cpu: OCIO.CPUProcessor, rgb: np.ndarray) -> None:
    """In-place RGB float32 HxWx3 via OCIO PackedImageDesc."""
    h, w = rgb.shape[:2]
    cpu.apply(OCIO.PackedImageDesc(rgb, w, h, 3))


def bridge_scene_rgb_to_config(
    rgb: np.ndarray,
    *,
    src_config: OCIO.Config,
    src_space: str,
    dst_config: OCIO.Config,
    dst_space: str,
) -> np.ndarray:
    """Move scene-referred float RGB between configs via ``aces_interchange``.

    When both configs define interchange (ACES2065-1), RGB is transformed to
    AP0 on *src_config*, then from AP0 to *dst_space* on *dst_config*.  AP0
    encodings are interchangeable by definition across ACES configs.

    If either side lacks interchange, falls back to a same-config transform
    when *src_config* is *dst_config*, or to an anchor-side path into a space
    equivalent to *dst_space*.
    """
    import numpy as np

    out = np.ascontiguousarray(rgb, dtype=np.float32)
    if not src_space or not dst_space:
        return out

    src_ix = get_interchange_space(src_config)
    dst_ix = get_interchange_space(dst_config)

    # Fast path: already in the destination space on one shared config.
    if src_config is dst_config and src_space == dst_space:
        return out

    if src_ix and dst_ix:
        if src_space != src_ix:
            _apply_rgb_processor(make_cpu_processor(src_config, src_space, src_ix), out)
        # out is now AP0; dst_ix is also AP0 on the destination config.
        if dst_space != dst_ix:
            _apply_rgb_processor(make_cpu_processor(dst_config, dst_ix, dst_space), out)
        return out

    # Same config, no interchange — direct processor.
    if src_config is dst_config:
        _apply_rgb_processor(make_cpu_processor(src_config, src_space, dst_space), out)
        return out

    # Cross-config without shared interchange: try to finish on src_config
    # into a space whose name exists on both, then stop (caller may only need
    # encoding match for ACEScg / lin Rec.709).
    dst_on_src = find_equivalent_space(src_config, dst_space) or _cs_name(src_config, dst_space)
    if dst_on_src:
        if src_space != dst_on_src:
            _apply_rgb_processor(make_cpu_processor(src_config, src_space, dst_on_src), out)
        return out

    return out


def linearize_overlay(
    config: OCIO.Config,
    overlay_rgba: np.ndarray,
    src_space: str = "",
    working_space: str = "",
) -> np.ndarray:
    """Convert sRGB-encoded RGBA overlay into user working-space float32.

    Accepts ``uint8`` (0–255) or ``float32`` (0–1) RGBA.

    **Always** linearises paint on the app anchor config (guaranteed
    ``texture_paint`` → ``aces_interchange`` / ACES2065-1), then bridges into
    the user *config*'s compositing space:

    1. Anchor: authoring → AP0 (never uses the user config).
    2. If the user config defines ``aces_interchange``, AP0 samples are valid
       in that role (OCIO scene-referred interchange); transform
       ``user_ix → working`` on the **user** config only when working is not
       already interchange.
    3. Else best-effort :func:`bridge_scene_rgb_to_config` (no silent name
       equality across unrelated configs).

    *config* is the **user** OCIO config.  *src_space* is ignored for the paint
    step (call-site compatibility).  Alpha is preserved unchanged.
    """
    import numpy as np

    anchor = get_app_anchor_config()
    auth = get_overlay_authoring_space(anchor)
    ap0 = get_interchange_space(anchor) or get_compositing_space(anchor)

    arr = np.asarray(overlay_rgba)
    if arr.dtype == np.uint8:
        rgb = arr[..., :3].astype(np.float32) / 255.0
        alpha = arr[..., 3].astype(np.float32) / 255.0
    else:
        rgb = np.clip(arr[..., :3].astype(np.float32), 0.0, None)
        alpha = np.clip(arr[..., 3].astype(np.float32), 0.0, 1.0)
    rgb = np.ascontiguousarray(rgb)

    if not working_space:
        try:
            working_space = get_compositing_space(config)
        except RuntimeError:
            # Pathological user config — stay in anchor AP0.
            working_space = ap0

    # Canonical names on the user config (roles resolve to real spaces).
    user_target = _cs_name(config, working_space) or working_space
    user_ix = get_interchange_space(config)

    # 1) App-owned: sRGB paint → AP0.
    _apply_rgb_processor(make_cpu_processor(anchor, auth, ap0), rgb)

    # 2) Bridge into user working / compositing space.
    if user_ix and user_target:
        # AP0 RGB is the aces_interchange encoding; identity when target is ix.
        if user_target != user_ix:
            _apply_rgb_processor(make_cpu_processor(config, user_ix, user_target), rgb)
    elif user_target and user_target != ap0:
        # No interchange on user config — try anchor→equivalent encoding.
        rgb = bridge_scene_rgb_to_config(
            rgb,
            src_config=anchor,
            src_space=ap0,
            dst_config=config,
            dst_space=user_target,
        )

    out = np.empty(arr.shape, dtype=np.float32)
    out[..., :3] = rgb
    out[..., 3] = alpha
    return out


def list_displays(config: OCIO.Config) -> list[str]:
    """Return the display names defined in *config*."""
    return list(config.getDisplays())


def list_views(config: OCIO.Config, display: str) -> list[str]:
    """Return the view names available for *display*."""
    return list(config.getViews(display))


def default_display_view(
    config: OCIO.Config,
    *,
    color_space: str = "",
) -> tuple[str, str]:
    """Return ``(display, view)`` from the config defaults / viewing rules.

    With *color_space*, uses OCIO 2 ``getDefaultView(display, colorSpace)`` so
    viewing rules (e.g. ``Any Video`` vs ``Any Scene-linear``) pick the right
    view. Without it, returns the config-wide default display/view.
    """
    display = ""
    view = ""
    try:
        display = str(config.getDefaultDisplay() or "")
    except Exception:
        display = ""
    if not display:
        try:
            names = list(config.getDisplays())
            display = names[0] if names else ""
        except Exception:
            return "", ""
    if color_space:
        try:
            # OCIO 2 overload: honour ViewingRules for this encoding/family.
            view = str(config.getDefaultView(display, color_space) or "")
        except Exception:
            view = ""
    if not view:
        try:
            view = str(config.getDefaultView(display) or "")
        except Exception:
            view = ""
    return display, view


# Color-space encodings that OCIO viewing rules treat as video (ACES CG/Studio).
_VIDEO_ENCODINGS = frozenset(
    {
        "sdr-video",
        "hdr-video",
        "edr-video",
        "display-linear",
    }
)


def preferred_video_monitoring_view(
    config: OCIO.Config,
    display: str = "",
) -> tuple[str, str]:
    """Best-effort ``(display, view)`` for monitoring video-originated SDR.

    Does **not** hard-code view names. Asks the config:

    1. ``getDefaultView(display, colorSpace)`` for any colorspace whose
       ``encoding`` is video-like (``sdr-video``, ``hdr-video``, …) — that is
       how ACES configs route ``Any Video`` viewing rules to e.g.
       ``Video (colorimetric)`` when present.
    2. Else empty strings (caller keeps the config-wide default).

    Unknown / older configs without encodings or the 2-arg API simply return
    ``("", "")``.
    """
    if not display:
        display, _ = default_display_view(config)
    if not display:
        return "", ""

    try:
        names = list(config.getColorSpaceNames())
    except Exception:
        return "", ""

    # Prefer common Rec.709 display names first (stable across ACES configs),
    # then any other video-encoded space — still only if the config defines them.
    preferred_cs: list[str] = []
    rest: list[str] = []
    for name in names:
        try:
            cs = config.getColorSpace(name)
            if cs is None:
                continue
            enc = (cs.getEncoding() or "").lower()
        except Exception:
            continue
        if enc not in _VIDEO_ENCODINGS:
            continue
        low = name.lower()
        if "rec.709" in low or "rec709" in low or "1886" in low:
            preferred_cs.append(name)
        else:
            rest.append(name)

    for cs_name in preferred_cs + rest:
        try:
            view = str(config.getDefaultView(display, cs_name) or "")
        except Exception:
            continue
        if not view:
            continue
        # Only accept if that view is actually listed for this display.
        try:
            if view not in list(config.getViews(display)):
                continue
        except Exception:
            continue
        return display, view
    return "", ""


def make_display_processor(
    config: OCIO.Config,
    src_space: str,
    display: str,
    view: str,
    exposure: float = 0.0,
    gamma: float = 1.0,
) -> OCIO.CPUProcessor:
    """Build a CPUProcessor for OCIO DisplayViewTransform with exposure/gamma.

    The resulting processor converts from *src_space* through the given
    display/view pair, with exposure (in stops) and gamma applied via
    ``ExposureContrastTransform``.
    """
    group = OCIO.GroupTransform()

    if exposure != 0.0 or gamma != 1.0:
        ec = OCIO.ExposureContrastTransform()
        ec.setExposure(exposure)
        ec.setGamma(gamma)
        ec.setPivot(0.18)
        group.appendTransform(ec)

    dvt = OCIO.DisplayViewTransform()
    dvt.setSrc(src_space)
    dvt.setDisplay(display)
    dvt.setView(view)
    group.appendTransform(dvt)

    return config.getProcessor(group).getDefaultCPUProcessor()


def make_viewer_display_processor(
    config: OCIO.Config,
    working_space: str,
    display: str,
    view: str,
) -> tuple[OCIO.CPUProcessor | None, object | None, object | None]:
    """Build a CPUProcessor for working → display/view with dynamic gain (exposure).

    EC is LINEAR (scene-linear gain as exposure stops) **before** the display
    transform — matching Nuke's gain → Viewer Process order.

    Viewer **gamma is not** wired through OCIO EC. Nuke applies
    ``pow(display, 1/γ)`` *after* the Viewer Process; callers must apply that
    post-pass themselves (GPU fragment uniform / numpy).

    Returns ``(cpu_proc, exposure_prop, None)`` — third slot kept for call-site
    compatibility (was formerly a gamma dynamic property).
    """
    group = OCIO.GroupTransform()

    # Gain only — before the display curve. Gamma stays at identity here.
    ec = OCIO.ExposureContrastTransform()
    ec.setStyle(OCIO.EXPOSURE_CONTRAST_LINEAR)
    ec.setExposure(0.0)
    ec.setGamma(1.0)
    ec.setPivot(0.18)
    ec.makeExposureDynamic()
    group.appendTransform(ec)

    dvt = OCIO.DisplayViewTransform()
    dvt.setSrc(working_space)
    dvt.setDisplay(display)
    dvt.setView(view)
    group.appendTransform(dvt)

    try:
        proc = config.getProcessor(group).getDefaultCPUProcessor()
        exp_prop = proc.getDynamicProperty(OCIO.DYNAMIC_PROPERTY_EXPOSURE)
        return proc, exp_prop, None
    except Exception:
        return None, None, None


def config_source_info(source_key: str, file_path: str = "") -> tuple[str, str]:
    """Return (config_source, config_path) suitable for pickling to worker processes.

    *config_source* is either a builtin config name or an empty string.
    *config_path* is a file path when the source is a file or $OCIO env.

    For our bundled ACES studio we resolve to the real on-disk path so that
    worker processes (which do not share our Python import context) can simply
    load it via CreateFromFile — exactly like a user custom config.
    """
    if source_key == OCIO_SOURCE_FILE:
        return ("", file_path)
    if source_key == OCIO_SOURCE_ENV:
        env = os.environ.get("OCIO", "")
        return ("", env)
    if source_key == OCIO_SOURCE_BUNDLED or source_key == BUNDLED_ACES_STUDIO_KEY:
        p = get_bundled_aces_studio_path()
        if p and p.is_file():
            return ("", str(p))
        # If we couldn't find it, fall back to a library builtin name
        return ("studio-config-v2.2.0_aces-v1.3_ocio-v2.4", "")
    if is_nuke_source_key(source_key):
        p = resolve_nuke_config_path(source_key)
        if p and p.is_file():
            return ("", str(p))
        return ("", "")
    return (source_key, "")
