"""Central resolver for the alass executable.

Resolution order (first hit wins):

1. **Config override** — ``config.alass_location`` when set and the file
   actually exists.
2. **Bundled** — inside a PyInstaller frozen bundle, ``sys._MEIPASS/bin/alass``
   (``alass.exe`` on Windows, otherwise ``alass``).
3. **PATH fallback** — the bare literal ``"alass"``.

The frozen-detection idiom mirrors ``anki_miner.gui.resources.get_resource_dir``.

Returning the bare literal (rather than an absolute ``shutil.which`` path) in the
non-frozen / no-override case is intentional: it preserves the historical behavior
that existing subprocess tests assert (``cmd[0] == "alass"``).
"""

import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["resolve_alass"]

# Cache keyed by (name, override-as-str, frozen-state, meipass) so that a changed
# override or a change in frozen state is never masked by a stale entry.
_CACHE: dict[tuple, str] = {}


def _clear_cache() -> None:
    """Reset the module-level cache (test helper)."""
    _CACHE.clear()


def _frozen_state() -> tuple[bool, str | None]:
    """Return (is_frozen, _MEIPASS) using the same idiom as get_resource_dir()."""
    frozen = bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")
    meipass = getattr(sys, "_MEIPASS", None) if frozen else None
    return frozen, meipass


def _bundled_name(base: str) -> str:
    """Return the platform-specific executable name (``.exe`` on Windows)."""
    return f"{base}.exe" if sys.platform == "win32" else base


def _resolve(base: str, override: Any) -> str:
    override_key = str(override) if override else None
    frozen, meipass = _frozen_state()
    cache_key = (base, override_key, frozen, meipass)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    resolved = _compute(base, override, frozen, meipass)
    _CACHE[cache_key] = resolved
    return resolved


def _compute(base: str, override: Any, frozen: bool, meipass: str | None) -> str:
    # 1. Config override.
    if override:
        override_path = Path(override)
        if override_path.is_file():
            return str(override_path)

    # 2. Bundled binary inside the frozen distributable. Require the executable
    #    bit (POSIX) so a present-but-non-exec bundle falls through to PATH
    #    instead of being returned and failing later at subprocess time. X_OK is
    #    meaningless on Windows, so skip the check there.
    if frozen and meipass is not None:
        bundled = Path(meipass) / "bin" / _bundled_name(base)
        if bundled.is_file() and (sys.platform == "win32" or os.access(bundled, os.X_OK)):
            return str(bundled)

    # 3. PATH fallback — bare literal.
    return base


def resolve_alass(config) -> str:
    """Resolve the alass executable path/literal for the given config."""
    return _resolve("alass", getattr(config, "alass_location", None))
