"""Central resolver for the yt-dlp executable.

Resolution order (first hit wins):

1. **Config override** — ``config.ytdlp_location`` when set and the file exists.
2. **Downloaded copy** — ``ytdlp_download_dir()/<name>`` (``~/.anki_miner/bin/``)
   when present and executable. This is the app-managed binary that
   :mod:`anki_miner.services.ytdlp_updater` auto-downloads and keeps current.
3. **Bundled** — inside a PyInstaller frozen bundle, ``sys._MEIPASS/bin/<name>``
   (kept as a forward-compat tier; nothing is added to the spec today).
4. **PATH fallback** — the bare literal ``"yt-dlp"``.

Mirrors :mod:`anki_miner.utils.ffmpeg_resolver`: module-level ``_CACHE`` dict,
``_clear_cache()`` test/updater hook, ``_frozen_state()``, ``_bundled_name()``.

Returning the bare literal (rather than an absolute ``shutil.which`` path) in the
no-override / non-frozen / no-download case is intentional: it preserves the
historical behavior the YouTube subprocess tests assert (``cmd[0] == "yt-dlp"``).

**Cache correctness:** the cache must not mask a binary that was downloaded
*after* startup. Rather than stat the download dir on every call, the updater
calls :func:`_clear_cache` immediately after a successful install, so the next
resolve recomputes. The cache key otherwise matches ffmpeg_resolver's shape.
"""

import os
import sys
from pathlib import Path
from typing import Any

from anki_miner.config import paths

__all__ = ["resolve_ytdlp", "ytdlp_binary_name", "ytdlp_download_dir"]

# Cache keyed by (override-as-str, frozen-state, meipass, download-dir-str) so a
# changed override or frozen state is never masked by a stale entry. A binary
# that appears in the download dir after startup is picked up because the updater
# calls _clear_cache() after install.
_CACHE: dict[tuple, str] = {}


def _clear_cache() -> None:
    """Reset the module-level cache (test + post-install hook)."""
    _CACHE.clear()


def ytdlp_binary_name() -> str:
    """Return the platform-specific yt-dlp executable name."""
    return "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"


def ytdlp_download_dir() -> Path:
    """Return the app-managed yt-dlp download directory (``~/.anki_miner/bin``).

    Reads ``ANKI_MINER_HOME`` at call time (via the ``paths`` module) so test
    home-isolation that repoints ``paths.ANKI_MINER_HOME`` takes effect.
    """
    return paths.ANKI_MINER_HOME / "bin"


def _frozen_state() -> tuple[bool, str | None]:
    """Return (is_frozen, _MEIPASS) using the same idiom as get_resource_dir()."""
    frozen = bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")
    meipass = getattr(sys, "_MEIPASS", None) if frozen else None
    return frozen, meipass


def _bundled_name(base: str) -> str:
    """Return the platform-specific executable name (``.exe`` on Windows)."""
    return f"{base}.exe" if sys.platform == "win32" else base


def _is_runnable(path: Path) -> bool:
    """True if *path* is a file and (Windows, or has the POSIX exec bit).

    X_OK is meaningless on Windows, so the executable check is skipped there.
    """
    return path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def resolve_ytdlp(config) -> str:
    """Resolve the yt-dlp executable path/literal for the given config."""
    override = getattr(config, "ytdlp_location", None)
    override_key = str(override) if override else None
    frozen, meipass = _frozen_state()
    download_dir = ytdlp_download_dir()
    cache_key = (override_key, frozen, meipass, str(download_dir))
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    resolved = _compute(override, frozen, meipass, download_dir)
    _CACHE[cache_key] = resolved
    return resolved


def _compute(override: Any, frozen: bool, meipass: str | None, download_dir: Path) -> str:
    # 1. Config override.
    if override:
        override_path = Path(override)
        if override_path.is_file():
            return str(override_path)

    # 2. Downloaded, app-managed copy. Require the executable bit (POSIX) so a
    #    present-but-non-exec file falls through rather than failing later at
    #    subprocess time.
    downloaded = download_dir / ytdlp_binary_name()
    if _is_runnable(downloaded):
        return str(downloaded)

    # 3. Bundled binary inside the frozen distributable (forward-compat tier).
    if frozen and meipass is not None:
        bundled = Path(meipass) / "bin" / _bundled_name("yt-dlp")
        if _is_runnable(bundled):
            return str(bundled)

    # 4. PATH fallback — bare literal.
    return "yt-dlp"
