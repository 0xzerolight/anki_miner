"""Central resolver for the yt-dlp executable.

Resolution order (first hit wins):

1. **Config override** — ``config.ytdlp_location`` when set and the file exists.
2. **Verified downloaded copy** — ``ytdlp_download_dir()/<name>``
   (``~/.anki_miner/bin/``) when executable and covered by a matching SHA-256
   receipt. Legacy pre-receipt files are never selected.
3. **PATH** — the executable returned by ``shutil.which("yt-dlp")``.
4. **Bundled** — inside a PyInstaller frozen bundle, ``sys._MEIPASS/bin/<name>``.
5. **Fail closed** — raise when PATH resolved the *unverified* managed slot.
6. **Interpreter sibling** — non-frozen only: ``Path(sys.executable).parent/<name>``,
   the console script a ``pip``/``pipx`` install puts next to the interpreter.
7. **Fallback** — the bare literal ``"yt-dlp"``.

**Why the managed copy outranks PATH (tier 2 before 3).** A managed copy only
exists because the user pressed "Update yt-dlp now" or enabled auto-update, so it
is both deliberate and the freshest binary on the machine. With PATH first, a
successful update was inert: the app kept running the stale PATH binary, and the
next check compared against that stale version and re-downloaded every 24h forever.

**Why PATH still outranks the bundle (tier 3 before 4), unlike**
:mod:`anki_miner.utils.ffmpeg_resolver` **and** :mod:`anki_miner.utils.alass_resolver`,
which both check the bundle first. This asymmetry is deliberate, not an oversight —
do not "fix" it for consistency. ffmpeg and alass have no self-updater and are not
version-sensitive, so a bundled-first order costs them nothing. yt-dlp breaks
whenever YouTube changes something, so a user's own package-manager or pip binary is
usually *fresher* than a build-time pin. Bundled-first would silently downgrade
users who already have a working yt-dlp on PATH — the one population that never had
this bug — to a pinned binary that ages for the whole release cycle. The bundle's
job is to make a fresh install work at all, which it does from tier 4.

**Why the interpreter sibling comes after the fail-closed raise (6 after 5).**
``yt-dlp`` is a hard runtime dependency, so its console script sits next to
``sys.executable`` in any venv — including during the test suite. Placing this tier
before the raise would let a rejected receiptless managed binary fall through to a
real executable, quietly defeating the containment the raise exists for.

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

import contextlib
import hashlib
import os
import shutil
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from anki_miner.config import paths
from anki_miner.utils.bundled_binary import bundled_name, frozen_state

__all__ = [
    "resolve_ytdlp",
    "managed_ytdlp_lock",
    "ytdlp_available",
    "ytdlp_binary_name",
    "ytdlp_download_dir",
    "ytdlp_verification_receipt_path",
]

# Cache keyed by (override-as-str, frozen-state, meipass, download-dir-str,
# PATH-hit) so a changed override, bundle state, or PATH resolution is never
# masked. Cached managed paths are re-verified before every return.
_CACHE: dict[tuple, str] = {}

# Serializes resolver cache transactions and each app-managed yt-dlp generation
# with updater promotion. RLock permits one caller to cover resolution/argv
# construction while capability probes take the same lock around their
# subprocess lifetime.
_MANAGED_YTDLP_LOCK = threading.RLock()


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


@contextlib.contextmanager
def managed_ytdlp_lock(
    executable: str | Path | None = None,
    *,
    blocking: bool = True,
) -> Iterator[bool]:
    """Lock a resolver transaction, managed process lifetime, or promotion.

    ``executable=None`` always addresses the managed slot. Other executables do
    not share its Windows image lock and pass through without serialization.
    """
    if executable is not None:
        download_dir = ytdlp_download_dir()
        managed = download_dir / ytdlp_binary_name()
        if not (_is_managed_path(executable, managed) or _is_within_directory(executable, download_dir)):
            yield True
            return

    acquired = _MANAGED_YTDLP_LOCK.acquire(blocking=blocking)
    try:
        yield acquired
    finally:
        if acquired:
            _MANAGED_YTDLP_LOCK.release()


def resolve_ytdlp(config) -> str:
    """Resolve the yt-dlp executable path/literal for the given config."""
    with managed_ytdlp_lock():
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


def ytdlp_available(config) -> bool:
    """Return True when a yt-dlp executable is actually reachable for *config*.

    Deliberately NOT a copy of :func:`anki_miner.utils.alass_resolver.alass_available`,
    which calls its resolver bare. :func:`resolve_ytdlp` can raise
    ``FileNotFoundError`` (the fail-closed rejection of an unverified managed binary
    on PATH), and callers here are availability *probes* on paths documented as
    never raising — most importantly ``ValidationService.validate_setup``. So the
    raise is absorbed into ``False``: an unusable binary and no binary are the same
    answer to "can we mine YouTube".
    """
    try:
        resolved = resolve_ytdlp(config)
    except FileNotFoundError:
        return False
    if resolved == "yt-dlp":
        # The bare literal means nothing above the fallback tier resolved; it is
        # only usable if the OS can find it at spawn time.
        return shutil.which("yt-dlp") is not None
    return Path(resolved).exists()


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

    # A PATH entry resolving to the managed slot must still pass the receipt check;
    # PATH must not launder that file. Computed here because the fail-closed step
    # below reads it.
    managed_path_hit = path_ytdlp is not None and (
        _is_managed_path(path_ytdlp, downloaded) or _is_within_directory(path_ytdlp, download_dir)
    )

    # 2. App-managed copy, but only with a receipt matching its current bytes.
    #    Ahead of PATH so a completed update actually takes effect — see the
    #    module docstring.
    if _is_verified_managed_binary(downloaded):
        return str(downloaded)

    # 3. An executable that actually exists on PATH. Do not return the bare
    #    literal here: it would shadow the bundled tier below.
    if path_ytdlp is not None and not managed_path_hit:
        return path_ytdlp

    # 4. Bundled binary inside the frozen distributable. Deliberately after PATH
    #    (unlike ffmpeg/alass) — see the module docstring.
    if frozen and meipass is not None:
        bundled = Path(meipass) / "bin" / bundled_name("yt-dlp")
        if _is_runnable(bundled):
            return str(bundled)

    # 5. Fail closed on a rejected managed PATH hit. This MUST stay ahead of the
    #    sibling tier and the bare fallback: both would otherwise hand back a
    #    working executable and defeat the rejection.
    if managed_path_hit:
        raise FileNotFoundError("Refusing unverified managed yt-dlp executable on PATH")

    # 6. The console script a pip/pipx install drops next to the interpreter.
    #    `pipx install anki_miner` puts a working yt-dlp in the pipx venv's bin/,
    #    which is not on PATH, so without this tier it is never found. Frozen
    #    builds skip it: there sys.executable is the app itself, not an interpreter.
    #    Guarded exactly like PATH so it cannot become a second laundering route
    #    into the managed directory.
    if not frozen:
        sibling = Path(sys.executable).parent / ytdlp_binary_name()
        sibling_is_managed = _is_managed_path(sibling, downloaded) or _is_within_directory(sibling, download_dir)
        if not sibling_is_managed and _is_runnable(sibling):
            return str(sibling)

    # Historical fallback when nothing above resolved.
    return "yt-dlp"
