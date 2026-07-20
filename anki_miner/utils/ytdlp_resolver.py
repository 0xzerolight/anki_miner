"""Central resolver for the yt-dlp executable.

Resolution order (first hit wins):

1. **Config override** — ``config.ytdlp_location`` when set and the file exists.
2. **PATH** — the executable returned by ``shutil.which("yt-dlp")``.
3. **Verified downloaded copy** — ``ytdlp_download_dir()/<name>``
   (``~/.anki_miner/bin/``) when executable and covered by a matching SHA-256
   receipt. Legacy pre-receipt files are never selected.
4. **Bundled** — inside a PyInstaller frozen bundle, ``sys._MEIPASS/bin/<name>``
   (kept as a forward-compat tier; nothing is added to the spec today).
5. **Fallback** — the bare literal ``"yt-dlp"`` when PATH did not resolve the
   unverified managed slot.

Mirrors :mod:`anki_miner.utils.ffmpeg_resolver`: module-level ``_CACHE`` dict,
``_clear_cache()`` test/updater hook, and the shared ``frozen_state()`` /
``bundled_name()`` bundle helpers.

Returning the bare literal (rather than an absolute ``shutil.which`` path) in the
no-override / non-frozen / no-download case is intentional: it preserves the
historical behavior the YouTube subprocess tests assert (``cmd[0] == "yt-dlp"``).

**Cache correctness:** the updater clears the cache after install; the cache key
also includes the current PATH hit. A cached managed path is re-hashed before
every return so replacement or tampering cannot outlive a stale cache entry.
"""

import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from anki_miner.config import paths
from anki_miner.utils.bundled_binary import bundled_name, frozen_state

__all__ = [
    "resolve_ytdlp",
    "ytdlp_binary_name",
    "ytdlp_download_dir",
    "ytdlp_verification_receipt_path",
]

# Cache keyed by (override-as-str, frozen-state, meipass, download-dir-str,
# PATH-hit) so a changed override, bundle state, or PATH resolution is never
# masked. Cached managed paths are re-verified before every return.
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


def ytdlp_verification_receipt_path(binary: Path) -> Path:
    """Return the SHA-256 receipt path beside a managed *binary*."""
    return binary.with_name(f"{binary.name}.verified")


def _is_runnable(path: Path) -> bool:
    """True if *path* is a file and (Windows, or has the POSIX exec bit).

    X_OK is meaningless on Windows, so the executable check is skipped there.
    """
    return path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_verified_managed_binary(path: Path) -> bool:
    """True when *path* is runnable and its receipt matches its current bytes."""
    if not _is_runnable(path):
        return False
    receipt = ytdlp_verification_receipt_path(path)
    try:
        recorded = receipt.read_text(encoding="ascii").strip().lower()
        if len(recorded) != 64 or any(char not in "0123456789abcdef" for char in recorded):
            return False
        return _sha256_file(path) == recorded
    except (OSError, UnicodeError):
        return False


def _is_managed_path(candidate: str | Path, managed: Path) -> bool:
    """True when *candidate* names the managed slot, including aliases."""
    path = Path(candidate)
    try:
        return path.samefile(managed)
    except OSError:
        return path.resolve() == managed.resolve()


def _is_within_directory(candidate: str | Path, directory: Path) -> bool:
    """True when canonicalized *candidate* is inside *directory*."""
    candidate_path = Path(os.path.realpath(candidate))
    directory_path = Path(os.path.realpath(directory))
    try:
        candidate_path.relative_to(directory_path)
    except ValueError:
        return False
    return True


def resolve_ytdlp(config) -> str:
    """Resolve the yt-dlp executable path/literal for the given config."""
    override = getattr(config, "ytdlp_location", None)
    override_key = str(override) if override else None
    frozen, meipass = frozen_state()
    download_dir = ytdlp_download_dir()
    path_ytdlp = shutil.which("yt-dlp")
    cache_key = (override_key, frozen, meipass, str(download_dir), path_ytdlp)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        managed = download_dir / ytdlp_binary_name()
        cached_is_managed = _is_managed_path(cached, managed)
        if not cached_is_managed and not _is_within_directory(cached, download_dir):
            return cached
        if cached_is_managed and _is_verified_managed_binary(managed):
            return str(managed)
        del _CACHE[cache_key]

    resolved = _compute(override, frozen, meipass, download_dir, path_ytdlp)
    _CACHE[cache_key] = resolved
    return resolved


def _compute(
    override: Any,
    frozen: bool,
    meipass: str | None,
    download_dir: Path,
    path_ytdlp: str | None,
) -> str:
    downloaded = download_dir / ytdlp_binary_name()

    # 1. Config override.
    if override:
        override_path = Path(override)
        # Pointing the override at the managed slot must not bypass its
        # verification requirement.
        if override_path.is_file() and (
            not _is_managed_path(override_path, downloaded) or _is_verified_managed_binary(downloaded)
        ):
            return str(override_path)

    # 2. Prefer an executable that actually exists on PATH. Do not return the
    #    bare literal here: it would shadow a verified managed copy when PATH
    #    has no yt-dlp. A PATH entry resolving to the managed slot still needs
    #    the receipt check below; PATH must not launder that file.
    managed_path_hit = path_ytdlp is not None and (
        _is_managed_path(path_ytdlp, downloaded) or _is_within_directory(path_ytdlp, download_dir)
    )
    if path_ytdlp is not None and not managed_path_hit:
        return path_ytdlp

    # 3. App-managed copy, but only with a receipt matching its current bytes.
    if _is_verified_managed_binary(downloaded):
        return str(downloaded)

    # 4. Bundled binary inside the frozen distributable (forward-compat tier).
    if frozen and meipass is not None:
        bundled = Path(meipass) / "bin" / bundled_name("yt-dlp")
        if _is_runnable(bundled):
            return str(bundled)

    # 5. A bare fallback would repeat PATH lookup and execute the rejected
    #    managed hit. Fail closed after all trusted tiers instead.
    if managed_path_hit:
        raise FileNotFoundError("Refusing unverified managed yt-dlp executable on PATH")

    # Historical fallback when PATH had no yt-dlp at resolution time.
    return "yt-dlp"
