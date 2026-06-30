"""Central resolver for the alass executable.

Resolution order (first hit wins):

1. **Config override** — ``config.alass_location`` when set and the file
   actually exists.
2. **Bundled** — inside a PyInstaller frozen bundle, ``sys._MEIPASS/bin/alass``
   (``alass.exe`` on Windows, otherwise ``alass``).
3. **Managed** — an in-app-downloaded binary at ``config.bin_root/alass``
   (``alass.exe`` on Windows). Installed by ``services.alass_installer`` for
   source/pip users who lack a bundled copy.
4. **PATH fallback** — the bare literal ``"alass"``.

The frozen-detection idiom mirrors ``anki_miner.gui.resources.get_resource_dir``.

Returning the bare literal (rather than an absolute ``shutil.which`` path) in the
non-frozen / no-override case is intentional: it preserves the historical behavior
that existing subprocess tests assert (``cmd[0] == "alass"``).
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Any

__all__ = ["alass_available", "resolve_alass"]

# Cache keyed by (name, override-as-str, bin-root-as-str, frozen-state, meipass)
# so that a changed override, bin_root, or frozen state is never masked by a
# stale entry.
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


def _resolve(base: str, override: Any, bin_root: Any) -> str:
    override_key = str(override) if override else None
    bin_root_key = str(bin_root) if bin_root else None
    frozen, meipass = _frozen_state()
    cache_key = (base, override_key, bin_root_key, frozen, meipass)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    resolved = _compute(base, override, bin_root, frozen, meipass)
    _CACHE[cache_key] = resolved
    return resolved


def _executable_file(path: Path) -> bool:
    """Return True if *path* is a file and (on POSIX) executable.

    X_OK is meaningless on Windows, so the executable check is skipped there. A
    present-but-non-executable file returns False so callers fall through rather
    than returning a path that fails later at subprocess time.
    """
    return path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def _compute(base: str, override: Any, bin_root: Any, frozen: bool, meipass: str | None) -> str:
    # 1. Config override.
    if override:
        override_path = Path(override)
        if override_path.is_file():
            return str(override_path)

    # 2. Bundled binary inside the frozen distributable. Require the executable
    #    bit (POSIX) so a present-but-non-exec bundle falls through instead of
    #    being returned and failing later at subprocess time.
    if frozen and meipass is not None:
        bundled = Path(meipass) / "bin" / _bundled_name(base)
        if _executable_file(bundled):
            return str(bundled)

    # 3. Managed in-app-downloaded binary under bin_root.
    if bin_root:
        managed = Path(bin_root) / _bundled_name(base)
        if _executable_file(managed):
            return str(managed)

    # 4. PATH fallback — bare literal.
    return base


def resolve_alass(config) -> str:
    """Resolve the alass executable path/literal for the given config."""
    return _resolve(
        "alass",
        getattr(config, "alass_location", None),
        getattr(config, "bin_root", None),
    )


def alass_available(alass_location, bin_root) -> bool:
    """Return True if alass is reachable for the given override + bin_root.

    Resolves through the same order as :func:`resolve_alass` (override ->
    bundled -> managed -> PATH) and reports whether the result is actually
    present: an explicit path must exist on disk, and the bare ``"alass"``
    literal must be found on PATH. Mirrors the retime tab's existing
    availability probe so the Settings panel and the retime tab agree on
    whether alass is usable.
    """
    resolved = _resolve("alass", alass_location, bin_root)
    if resolved == "alass":
        return shutil.which("alass") is not None
    return Path(resolved).exists()
