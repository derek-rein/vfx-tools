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


def list_nuke_configs() -> list[tuple[str, str, bool]]:
    """Local Nuke install configs (path references only — never bundled).

    Returns ``[(key, label, recommended), ...]``.  *recommended* is True for
    the newest install's Studio ACES 2.0 config when present.
    """
    configs = find_nuke_ocio_configs()
    if not configs:
        return []
    # Mark the first entry (newest Nuke × preferred kind) as recommended.
    out: list[tuple[str, str, bool]] = []
    for i, cfg in enumerate(configs):
        label = f"{cfg.label}  (local)"
        out.append((cfg.key, label, i == 0 and cfg.kind.startswith("studio")))
    return out


def _create_from_file(path: str | Path) -> OCIO.Config:
    """Load a config file, rewriting version-mismatch errors into a fix hint."""
    p = str(path)
    try:
        return OCIO.Config.CreateFromFile(p)
    except Exception as e:
        msg = str(e)
        if "not able to load that config version" in msg or "Maximum minor version" in msg:
            runtime = OCIO.GetVersion()
            raise RuntimeError(
                f"{msg}\n\n"
                f"Runtime OpenColorIO is {runtime}; the bundled ACES Studio config "
                f"needs 2.5+.  oiio-python sometimes rewires PyOpenColorIO to its "
                f"vendored 2.4 dylib.  Fix with:\n"
                f"  make ensure-ocio\n"
                f"  # or: uv pip install --reinstall-package opencolorio 'opencolorio>=2.5.1'"
            ) from e
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
            try:
                return _create_from_file(p)
            except Exception:
                pass  # version too old or corrupt; fall through to library fallback
        # Graceful fallback to the best available library studio config (the "awesome cameras" one)
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
            "and no library fallback available. Run: make ensure-ocio"
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


def get_working_space(config: OCIO.Config) -> str:
    """Return the canonical name of the OCIO ``scene_linear`` role.

    All compositing inside the conversion pipeline happens in this
    scene-linear "working" colorspace.  Falls back to a few common
    alternate role / colorspace names so this works on stock ACES,
    Studio, and CG-Config builds.
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
        try:
            cs = config.getColorSpace(name)
            if cs is not None:
                return cs.getName()
        except Exception:
            continue
    raise RuntimeError("Could not resolve a scene-linear working colorspace from the OCIO config.")


def get_compositing_space(config: OCIO.Config) -> str:
    """Return the scene-linear space overlays are composited in.

    Slate / burn-in / watermark overlays are authored by QPainter in
    display-encoded sRGB. To bake them onto frames we linearise them and
    alpha-over in a scene-linear space. We deliberately prefer the widest
    available scene-referred gamut — **ACES2065-1 (AP0)**, via the
    ``aces_interchange`` role — so the user's footage is round-tripped through
    a gamut that encloses all visible colour and the composite never clips or
    shifts colours the user actually shot. Where overlay alpha is zero the
    over is a no-op, so user pixels are preserved bit-for-bit.

    Falls back to the config's ``scene_linear`` working space (e.g. ACEScg or
    Linear Rec.709) for non-ACES configs that have no AP0 space.
    """
    role = getattr(OCIO, "ROLE_INTERCHANGE_SCENE", "aces_interchange")
    candidates = (
        role,
        "ACES2065-1",
        "ACES - ACES2065-1",
        "lin_ap0",
        "aces2065_1",
    )
    for name in candidates:
        try:
            cs = config.getColorSpace(name)
            if cs is not None:
                return cs.getName()
        except Exception:
            continue
    return get_working_space(config)


def get_overlay_authoring_space(config: OCIO.Config) -> str:
    """Return the colorspace overlays (slate / burnin / watermark) are painted in.

    Overlays are authored in display-encoded sRGB (Qt's standard 8-bit
    rendering).  We resolve this via OCIO **roles** first — the standard way
    configs advertise their sRGB-encoded texture space:

    * ``texture_paint`` — the semantically correct role for app-painted,
      sRGB-encoded graphics (maps to ``sRGB - Texture`` in ACES configs);
    * ``color_picking`` — the common sRGB fallback used by colour pickers.

    Role names resolve through ``getColorSpace`` just like aliases.  If neither
    role is present we fall back to common literal names, then the working
    space.
    """
    texture_role = getattr(OCIO, "ROLE_TEXTURE_PAINT", "texture_paint")
    picking_role = getattr(OCIO, "ROLE_COLOR_PICKING", "color_picking")
    candidates = (
        texture_role,
        picking_role,
        "sRGB - Texture",
        "sRGB Texture",
        "Utility - sRGB - Texture",
        "sRGB",
        "Output - sRGB",
        "srgb",
    )
    for name in candidates:
        try:
            cs = config.getColorSpace(name)
            if cs is not None:
                return cs.getName()
        except Exception:
            continue
    return get_working_space(config)


def linearize_overlay(
    config: OCIO.Config,
    overlay_u8_rgba: np.ndarray,
    src_space: str = "",
    working_space: str = "",
) -> np.ndarray:
    """Convert an sRGB-encoded RGBA overlay (uint8) into working-space float32.

    Alpha is preserved unchanged (the OCIO transform only touches RGB).
    """
    import numpy as np

    if not src_space:
        src_space = get_overlay_authoring_space(config)
    if not working_space:
        working_space = get_working_space(config)

    rgb = overlay_u8_rgba[..., :3].astype(np.float32) / 255.0
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    cpu = make_cpu_processor(config, src_space, working_space)
    cpu.apply(OCIO.PackedImageDesc(rgb, w, h, 3))

    out = np.empty(overlay_u8_rgba.shape, dtype=np.float32)
    out[..., :3] = rgb
    out[..., 3] = overlay_u8_rgba[..., 3].astype(np.float32) / 255.0
    return out


def list_displays(config: OCIO.Config) -> list[str]:
    """Return the display names defined in *config*."""
    return list(config.getDisplays())


def list_views(config: OCIO.Config, display: str) -> list[str]:
    """Return the view names available for *display*."""
    return list(config.getViews(display))


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
    """Build a CPUProcessor for working → display/view with a *dynamic* ExposureContrastTransform.

    The returned EC transform is configured for live viewer controls (gain/gamma).
    Callers can retrieve the dynamic properties and mutate them cheaply without
    rebuilding the processor — this is the pattern used by RV, xStudio, and other
    professional OCIO viewers for responsive exposure/gain/gamma adjustments.

    Returns (cpu_proc, exposure_prop, gamma_prop) or (None, None, None) on failure.
    The props are obtained via DYNAMIC_PROPERTY_EXPOSURE / DYNAMIC_PROPERTY_GAMMA.
    """
    group = OCIO.GroupTransform()

    # Viewer adjustment transform — placed *before* the display curve.
    # LINEAR style is appropriate when working_space is scene-linear.
    ec = OCIO.ExposureContrastTransform()
    ec.setStyle(OCIO.EXPOSURE_CONTRAST_STYLE_LINEAR)
    ec.setExposure(0.0)
    ec.setGamma(1.0)
    ec.setPivot(0.18)
    ec.makeDynamic()
    group.appendTransform(ec)

    dvt = OCIO.DisplayViewTransform()
    dvt.setSrc(working_space)
    dvt.setDisplay(display)
    dvt.setView(view)
    group.appendTransform(dvt)

    try:
        proc = config.getProcessor(group).getDefaultCPUProcessor()
        exp_prop = proc.getDynamicProperty(OCIO.DYNAMIC_PROPERTY_EXPOSURE)
        gamma_prop = proc.getDynamicProperty(OCIO.DYNAMIC_PROPERTY_GAMMA)
        return proc, exp_prop, gamma_prop
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
