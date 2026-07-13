"""Central resolver for the ffmpeg/ffprobe executables.

Resolution order (first hit wins):

1. **Config override** — ``config.ffmpeg_location`` / ``config.ffprobe_location``
   when set and the file actually exists.
2. **Bundled** — inside a PyInstaller frozen bundle, ``sys._MEIPASS/bin/<name>``
   (``ffmpeg.exe`` / ``ffprobe.exe`` on Windows, otherwise ``ffmpeg`` / ``ffprobe``).
3. **PATH fallback** — the bare literal ``"ffmpeg"`` / ``"ffprobe"``.

The frozen-detection idiom mirrors ``anki_miner.gui.resources.get_resource_dir``.

Returning the bare literal (rather than an absolute ``shutil.which`` path) in the
non-frozen / no-override case is intentional: it preserves the historical behavior
that existing subprocess tests assert (``cmd[0] == "ffmpeg"``).
"""

import os
import sys
from pathlib import Path
from typing import Any

from anki_miner.utils.bundled_binary import bundled_name, frozen_state

__all__ = ["resolve_ffmpeg", "resolve_ffprobe"]

# Cache keyed by (name, override-as-str, frozen-state, meipass) so that a changed
# override or a change in frozen state is never masked by a stale entry.
_CACHE: dict[tuple, str] = {}


def _clear_cache() -> None:
    """Reset the module-level cache (test helper)."""
    _CACHE.clear()


def _resolve(base: str, override: Any) -> str:
    override_key = str(override) if override else None
    frozen, meipass = frozen_state()
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
        bundled = Path(meipass) / "bin" / bundled_name(base)
        if bundled.is_file() and (sys.platform == "win32" or os.access(bundled, os.X_OK)):
            return str(bundled)

    # 3. PATH fallback — bare literal.
    return base


def resolve_ffmpeg(config) -> str:
    """Resolve the ffmpeg executable path/literal for the given config."""
    return _resolve("ffmpeg", getattr(config, "ffmpeg_location", None))


def resolve_ffprobe(config) -> str:
    """Resolve the ffprobe executable path/literal for the given config."""
    return _resolve("ffprobe", getattr(config, "ffprobe_location", None))
