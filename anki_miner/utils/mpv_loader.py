"""Loader/orchestrator for python-mpv and the libmpv shared library.

This module is the ONLY place allowed to ``import mpv``. python-mpv dlopens
libmpv at *import* time: on POSIX it resolves the library exclusively via
``ctypes.util.find_library("mpv")`` (which parses the ldconfig cache and can
never see a PyInstaller-bundled ``.so``) and raises ``OSError`` when that
returns ``None``; on Windows it searches PATH first, then next to ``mpv.py``.
There is no env-var hook upstream, so loading a bundled libmpv requires
monkeypatching ``ctypes.util.find_library`` around the import — that patch,
plus the Windows DLL-directory registration, is what this module encapsulates.

Resolution order (first hit wins):

1. **Env override** — ``ANKI_MINER_LIBMPV`` pointing at a libmpv shared
   library. Fails closed: set-but-unresolvable raises ``MpvUnavailableError``
   instead of falling through (this makes the override usable to force the
   libmpv-absent path when testing).
2. **Bundled** — inside a PyInstaller frozen bundle, a libmpv shared library
   at the ``sys._MEIPASS`` root (dest ``.`` in the spec: python-mpv's Windows
   ``dirname(__file__)`` fallback, macOS ``@loader_path`` sibling resolution,
   and the Linux onedir loader path all want the libs there, not in ``bin/``).
3. **System** — plain ``import mpv`` with python-mpv's own search (ldconfig /
   PATH / Homebrew), i.e. the pip-install-with-system-mpv case.

LC_NUMERIC: libmpv requires the C numeric locale and Qt stomps the locale at
``QApplication`` construction, so :func:`_ensure_c_numeric` is asserted
immediately before **every** ``mpv.MPV(...)`` construction (factory and
probe), never once at import time.
"""

from __future__ import annotations

import ctypes.util
import locale
import logging
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only; never a runtime import
    import mpv

__all__ = [
    "MpvUnavailableError",
    "bundled_libmpv_path",
    "create_mpv_player",
    "load_mpv",
    "mpv_available",
    "mpv_probe_main",
]

logger = logging.getLogger(__name__)

_ENV_OVERRIDE = "ANKI_MINER_LIBMPV"

# Windows DLL basenames python-mpv probes via find_library, in its own order.
_WINDOWS_DLL_NAMES = ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll")

# Import-once cache: (module | None, error | None). Both outcomes are cached —
# a failed dlopen will not succeed later in the same process, and re-probing
# on every mpv_available() call would re-run a filesystem walk per widget.
_LOCK = threading.Lock()
_CACHED: tuple[Any, ImportError | None] | None = None
_RESOLVED_SOURCE: str | None = None


def _clear_cache() -> None:
    """Reset the module-level import cache (test helper)."""
    global _CACHED, _RESOLVED_SOURCE
    with _LOCK:
        _CACHED = None
        _RESOLVED_SOURCE = None


class MpvUnavailableError(ImportError):
    """python-mpv or the libmpv shared library could not be loaded."""


def _frozen_state() -> tuple[bool, str | None]:
    """Return (is_frozen, _MEIPASS) using the same idiom as get_resource_dir()."""
    frozen = bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")
    meipass = getattr(sys, "_MEIPASS", None) if frozen else None
    return frozen, meipass


def _cause_detail(exc: BaseException) -> str:
    """Return a grep-able suffix carrying the real OS error behind a dlopen failure.

    python-mpv wraps the underlying ``ctypes.CDLL`` ``OSError`` in a generic
    "could not load it" message via ``raise OSError(...) from e`` (see mpv.py),
    so the wrapper string DROPS the Windows ``WinError`` code — but the original
    is preserved on ``exc.__cause__``. On Windows a *present* libmpv-2.dll that
    fails to load is almost always a missing transitive dependency (WinError 126,
    e.g. an absent ``vulkan-1.dll`` on a box without a Vulkan-capable GPU driver);
    surfacing the code is what makes such field failures diagnosable. Returns ""
    when there is no distinct cause to report. Cross-platform safe: ``.winerror``
    is read via ``getattr`` (absent off Windows)."""
    cause = exc.__cause__
    if cause is None or cause is exc:
        return ""
    winerror = getattr(cause, "winerror", None)
    strerror = getattr(cause, "strerror", None)
    return f" [cause={cause!r} winerror={winerror} strerror={strerror!r}]"


def _soname_globs() -> tuple[str, ...]:
    """Per-OS glob patterns for a libmpv shared library, most specific first."""
    if sys.platform == "win32":
        return ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll")
    if sys.platform == "darwin":
        return ("libmpv.2.dylib", "libmpv.dylib", "libmpv.*.dylib")
    return ("libmpv.so.2", "libmpv.so.*", "libmpv.so")


def bundled_libmpv_path() -> Path | None:
    """Return the bundled libmpv path inside a frozen bundle, or None.

    Looks at the ``sys._MEIPASS`` root only (spec dest ``.``); returns None in
    non-frozen (dev/pip) processes.
    """
    frozen, meipass = _frozen_state()
    if not frozen or meipass is None:
        return None
    root = Path(meipass)
    for pattern in _soname_globs():
        for candidate in sorted(root.glob(pattern)):
            if candidate.is_file():
                return candidate
    return None


def _import_mpv_with_path(lib_path: Path) -> Any:
    """Import python-mpv forcing it to dlopen *lib_path*.

    python-mpv resolves libmpv through ``ctypes.util.find_library`` at import
    time (module attribute lookup, so a temporary monkeypatch is effective).
    On Windows, dependent DLLs must resolve from the library's own directory;
    ``LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR`` in python-mpv's CDLL flags covers the
    load itself and ``os.add_dll_directory`` covers transitive lookups.
    """
    resolved = str(lib_path)
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(lib_path.parent))

    original = ctypes.util.find_library
    hit_names = {"mpv", *_WINDOWS_DLL_NAMES}

    def _patched(name: str) -> str | None:
        if name in hit_names:
            return resolved
        return original(name)

    ctypes.util.find_library = _patched
    try:
        import mpv as mpv_module
    finally:
        ctypes.util.find_library = original
    return mpv_module


def load_mpv() -> Any:
    """Import and return the ``mpv`` module, resolving libmpv first.

    Idempotent and cached (success and failure). Raises
    :class:`MpvUnavailableError` when python-mpv or libmpv cannot be loaded.
    """
    global _CACHED, _RESOLVED_SOURCE
    with _LOCK:
        if _CACHED is not None:
            module, error = _CACHED
            if error is not None:
                raise error
            return module

        try:
            module = _load_mpv_uncached()
        except MpvUnavailableError as exc:
            _CACHED = (None, exc)
            raise
        _CACHED = (module, None)
        return module


def _load_mpv_uncached() -> Any:
    global _RESOLVED_SOURCE

    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        override_path = Path(override)
        if not override_path.is_file():
            raise MpvUnavailableError(f"{_ENV_OVERRIDE}={override!r} does not exist (override fails closed)")
        try:
            module = _import_mpv_with_path(override_path)
        except (ImportError, OSError) as exc:
            raise MpvUnavailableError(f"failed to load libmpv from {_ENV_OVERRIDE}: {exc}") from exc
        _RESOLVED_SOURCE = f"env:{override_path}"
        return module

    bundled = bundled_libmpv_path()
    if bundled is not None:
        try:
            module = _import_mpv_with_path(bundled)
        except (ImportError, OSError) as exc:
            # Fall THROUGH to the system search instead of failing: a bundled
            # copy can be unloadable on this host (e.g. macOS bundles built
            # against a newer min-OS than the machine runs) while a
            # user-installed system libmpv works fine — `brew install mpv`
            # must be able to restore the preview.
            logger.warning(
                "Bundled libmpv at %s failed to load (%s)%s; trying system libmpv",
                bundled,
                exc,
                _cause_detail(exc),
            )
        else:
            _RESOLVED_SOURCE = f"bundled:{bundled}"
            return module

    try:
        import mpv as mpv_module
    except (ImportError, OSError) as exc:
        # OSError = python-mpv installed but libmpv missing (the common pip case).
        # Log once here (load_mpv caches, so this never spams the rotating file):
        # bundled=False on a frozen build means no libmpv shipped in the bundle
        # (a self-built/fork bundle), while bundled=True means the bundled copy
        # already failed above (its :191 warning carries the WinError) AND no
        # system libmpv rescued it. Either way the notice would otherwise be the
        # only trace, and mpv_available() swallows this raise silently.
        frozen, meipass = _frozen_state()
        logger.warning(
            "libmpv unavailable via system search (%s)%s; frozen=%s meipass=%s bundled_found=%s",
            exc,
            _cause_detail(exc),
            frozen,
            meipass,
            bundled is not None,
        )
        raise MpvUnavailableError(f"libmpv not available via system search: {exc}") from exc
    _RESOLVED_SOURCE = "system"
    return mpv_module


def mpv_available() -> bool:
    """Return True iff python-mpv AND libmpv are importable. Never raises."""
    try:
        load_mpv()
    except Exception:  # noqa: BLE001 - availability probe must never raise
        return False
    return True


def _ensure_c_numeric() -> None:
    """Force LC_NUMERIC=C — required by libmpv before every MPV construction.

    Qt resets the process locale at QApplication construction, so this must run
    immediately before each ``mpv.MPV(...)`` call, not once at import.
    """
    locale.setlocale(locale.LC_NUMERIC, "C")


def create_mpv_player(log_handler: Callable[[str, str, str], None] | None = None) -> mpv.MPV:
    """Build a libmpv handle configured for the embedded preview widget.

    - ``vo="libmpv"``: required by the render API (MpvRenderContext).
    - ``keep_open="yes"``: pause on EOF holding the last frame (QMediaPlayer
      end-of-media parity; replay is seek-0-then-unpause in the controller).
    - ``hwdec="no"``: software decode incl. dav1d AV1 — the reliability win
      that retired the Issue #82 apparatus.
    - ``pause=True``: present the first frame without starting playback.
    - ``sid="no"``: the widget's own subtitle overlay is the only subtitle
      surface; mpv must not render embedded subtitle tracks.
    """
    mpv_module = load_mpv()
    _ensure_c_numeric()
    return mpv_module.MPV(
        vo="libmpv",
        keep_open="yes",
        hwdec="no",
        pause=True,
        input_default_bindings=False,
        input_vo_keyboard=False,
        audio_display="no",
        sid="no",
        loglevel="warn",
        log_handler=log_handler,
    )


def mpv_probe_main() -> int:
    """Headless bundle-smoke probe (``ANKI_MINER_MPV_PROBE=1``).

    Loads libmpv through the normal resolution order and constructs a
    display-free core (``vo=null ao=null`` — no GL, offscreen-safe). Prints a
    grep-able marker either way; exit code feeds bundle_smoke.sh.
    """
    try:
        mpv_module = load_mpv()
        _ensure_c_numeric()
        player = mpv_module.MPV(vo="null", ao="null")
        version = player.mpv_version
        api = getattr(mpv_module, "MPV_VERSION", None)
        player.terminate()
    except Exception as exc:  # noqa: BLE001 - probe reports, never crashes
        print(f"MPV_PROBE_FAIL: {exc}", flush=True)
        return 1
    print(
        f"MPV_PROBE_OK api={api} version={version!r} source={_RESOLVED_SOURCE}",
        flush=True,
    )
    return 0
