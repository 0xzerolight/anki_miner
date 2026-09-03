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

import logging
import os
import sys
from pathlib import Path
from typing import Any

from anki_miner.utils.bundled_binary import bundled_name, frozen_state
from anki_miner.utils.resolver_log import log_resolution, log_resolution_refused

__all__ = ["resolve_ffmpeg", "resolve_ffprobe"]

logger = logging.getLogger(__name__)

# Cache keyed by (name, override-as-str, frozen-state, meipass) so that a changed
# override or a change in frozen state is never masked by a stale entry.
# NOTE: the cache does not re-verify the resolved path on hit — if the override
# is deleted after the first call, a second call with the same inputs returns
# the stale cached path. Revisit if an in-app installer ever appears (asymmetry
# vs. ytdlp_resolver's re-verification).
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


def _executable_file(path: Path) -> bool:
    """True if *path* is a file and (Windows, or carries the POSIX exec bit)."""
    return path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def _compute(base: str, override: Any, frozen: bool, meipass: str | None) -> str:
    # Provenance is logged from here, never from `_resolve`: this runs only on a
    # cache miss, which bounds both the receipt and any refusal to once per cache
    # generation. `log_resolution` also dedupes on the outcome tuple, so a
    # cleared-and-recomputed cache that lands on the same binary stays silent.
    # 1. Config override.
    if override:
        override_path = Path(override)
        if _executable_file(override_path):
            log_resolution(logger, base, "override", str(override_path))
            return str(override_path)
        if override_path.exists():
            # Present but unusable is the whole diagnosis behind an "ffmpeg not
            # found" report from a user who did set the option: silently falling
            # through reads as the setting being ignored. A missing override is
            # not refused here — that is a typo, and the settings panel already
            # marks it.
            log_resolution_refused(logger, base, "override_not_executable", override=override_path)

    # 2. Bundled binary inside the frozen distributable. Require the executable
    #    bit (POSIX) so a present-but-non-exec bundle falls through to PATH
    #    instead of being returned and failing later at subprocess time. X_OK is
    #    meaningless on Windows, so skip the check there.
    if frozen and meipass is not None:
        bundled = Path(meipass) / "bin" / bundled_name(base)
        if _executable_file(bundled):
            log_resolution(logger, base, "bundled", str(bundled))
            return str(bundled)
        if bundled.exists():
            log_resolution_refused(logger, base, "bundled_not_executable", bundled=bundled)

    # 3. PATH fallback — bare literal.
    log_resolution(logger, base, "literal", base)
    return base


def resolve_ffmpeg(config) -> str:
    """Resolve the ffmpeg executable path/literal for the given config."""
    return _resolve("ffmpeg", getattr(config, "ffmpeg_location", None))


def resolve_ffprobe(config) -> str:
    """Resolve the ffprobe executable path/literal for the given config."""
    return _resolve("ffprobe", getattr(config, "ffprobe_location", None))
