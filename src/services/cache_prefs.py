"""Playback cache budget preferences (Triton-aligned).

The default budget is **25% of physical RAM**, matching Triton's
``AppState`` default.  Prefetch scheduling reserves **25% of the
prefetch window** for lookback (frames behind the playhead); the rest
is aggressive cache-ahead.
"""

from __future__ import annotations

import os
import platform

from PySide6.QtCore import QSettings

from .app_settings import Keys

DEFAULT_CACHE_BUDGET_PCT = 25
DEFAULT_LOOKBACK_RATIO = 0.25  # 25% of prefetch window behind playhead
# Re-export canonical key (single registry in app_settings.Keys).
SETTINGS_KEY_CACHE_BUDGET_PCT = Keys.CACHE_BUDGET_PCT


def _qsettings_int(settings: QSettings, key: str, default: int) -> int:
    raw = settings.value(key, default)
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return default
    return default


def total_ram_bytes() -> int:
    """Return total physical RAM in bytes, or a 16 GB fallback."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return pages * page_size
    except (ValueError, OSError, AttributeError):
        pass
    if platform.system() == "Darwin":
        try:
            import subprocess

            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return int(out.strip())
        except Exception:
            pass
    return 16 * 1024**3


def load_cache_budget_pct(settings: QSettings) -> int:
    raw = _qsettings_int(
        settings,
        SETTINGS_KEY_CACHE_BUDGET_PCT,
        DEFAULT_CACHE_BUDGET_PCT,
    )
    return max(1, min(90, raw))


def save_cache_budget_pct(settings: QSettings, pct: int) -> None:
    settings.setValue(SETTINGS_KEY_CACHE_BUDGET_PCT, max(1, min(90, int(pct))))


def cache_budget_bytes(settings: QSettings) -> int:
    """Byte budget for the playback frame cache from *settings*."""
    pct = load_cache_budget_pct(settings)
    return int(total_ram_bytes() * pct / 100)
