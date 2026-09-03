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

from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:  # pragma: no cover - typing only; never a runtime import
    import mpv

__all__ = [
    "MpvUnavailableError",
    "bundled_libmpv_path",
    "create_mpv_player",
    "load_mpv",
    "mpv_available",
    "mpv_probe_main",
    "resolved_source",
    "terminate_mpv_player",
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
_MPV_TERMINATE_TIMEOUT_S = 2.0

# mpv options that switch off its builtin Lua scripts, newest-named first within
# a rename pair. Every one of these is a script this player has no use for, and
# together they are what keeps LuaJIT out of the process (Issue #112, see
# create_mpv_player). NOT all of them exist in every libmpv — see
# _builtin_script_options.
_BUILTIN_SCRIPT_OPTIONS: tuple[str, ...] = (
    "osc",
    "ytdl",
    "load-stats-overlay",
    "load-console",  # mpv 0.40+ name
    "load-osd-console",  # pre-0.40 name; a deprecated alias on 0.40+
    "load-auto-profiles",
    "load-select",
    "load-positioning",
    "load-commands",
)
# Probed once per process: {kwarg_name: False} for the options this libmpv knows.
_SCRIPT_OPTIONS: dict[str, bool] | None = None


def _clear_cache() -> None:
    """Reset the module-level import cache (test helper)."""
    global _CACHED, _RESOLVED_SOURCE, _SCRIPT_OPTIONS
    with _LOCK:
        _CACHED = None
        _RESOLVED_SOURCE = None
        _SCRIPT_OPTIONS = None


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
        # source= is the tier that actually resolved. It reads None here, and
        # that is the point: it proves no tier won, distinguishing "libmpv never
        # loaded" from "libmpv loaded and then something else broke" in a report
        # where this warning is the only libmpv line.
        logger.warning(
            "libmpv unavailable via system search (%s)%s; frozen=%s meipass=%s bundled_found=%s source=%s",
            exc,
            _cause_detail(exc),
            frozen,
            meipass,
            bundled is not None,
            resolved_source(),
        )
        raise MpvUnavailableError(f"libmpv not available via system search: {exc}") from exc
    _RESOLVED_SOURCE = "system"
    return mpv_module


def resolved_source() -> str | None:
    """Return which libmpv this process resolved (``env:``/``bundled:``/``system``).

    Reports only what a real load already decided — it never triggers one. A
    diagnostics probe that dlopened libmpv would both change program state and
    risk the failure it is trying to describe.
    """
    return _RESOLVED_SOURCE


def mpv_available() -> bool:
    """Return True iff python-mpv AND libmpv are importable. Never raises."""
    try:
        load_mpv()
    except Exception as exc:  # noqa: BLE001 - availability probe must never raise
        # DEBUG and cheap: load_mpv caches both outcomes, so this re-raises the
        # ORIGINAL error without re-dlopening, and the widget-side callers ask
        # once per player. The one WARNING that explains the failure was already
        # emitted by the load that produced this cached error; what a False here
        # adds is which caller saw it and that the answer came from the cache.
        log_summary(
            logger,
            "libmpv unavailable",
            level=logging.DEBUG,
            error_type=type(exc).__name__,
            error=str(exc),
            source=resolved_source(),
        )
        return False
    return True


def _ensure_c_numeric() -> None:
    """Force LC_NUMERIC=C — required by libmpv before every MPV construction.

    Qt resets the process locale at QApplication construction, so this must run
    immediately before each ``mpv.MPV(...)`` call, not once at import.
    """
    locale.setlocale(locale.LC_NUMERIC, "C")


def _builtin_script_options(mpv_module: Any) -> dict[str, bool]:
    """Return the script-disabling kwargs *this* libmpv accepts, probed once.

    The names in :data:`_BUILTIN_SCRIPT_OPTIONS` came in over several mpv
    releases (``load-select``/``load-positioning``/``load-commands`` and the
    ``load-osd-console`` → ``load-console`` rename are 0.40), and python-mpv
    raises ``AttributeError('mpv option does not exist', -5, ...)`` out of
    ``MPV.__init__`` for an unknown one — passing the full set blind would take
    the video preview away from everyone on an older system libmpv (Debian 12
    ships 0.35, Ubuntu 24.04 0.37) to fix a Windows crash. An absent option
    means that script does not exist in that build either, so dropping it is
    exactly right.

    The probe sets the options on a bare ``mpv_create`` handle that is NEVER
    initialized: no core comes up, so no script — and no LuaJIT — is loaded by
    the probe itself. python-mpv's version floor is open (``mpv>=1.0.8``), so a
    missing private degrades to "disable nothing" plus a warning rather than
    breaking construction; ``test_python_mpv_exposes_probe_internals`` is what
    keeps that branch from going live unnoticed.
    """
    global _SCRIPT_OPTIONS
    with _LOCK:
        if _SCRIPT_OPTIONS is not None:
            return dict(_SCRIPT_OPTIONS)

        supported: dict[str, bool] = {}
        try:
            handle = mpv_module._mpv_create()
            try:
                for name in _BUILTIN_SCRIPT_OPTIONS:
                    try:
                        mpv_module._mpv_set_option_string(handle, name.encode("utf-8"), b"no")
                    except Exception:  # noqa: BLE001 - unknown option on this libmpv
                        continue
                    supported[name.replace("-", "_")] = False
            finally:
                mpv_module._mpv_terminate_destroy(handle)
        except Exception:  # noqa: BLE001 - probe failure must not cost the preview
            logger.warning("could not probe libmpv script options; builtin scripts stay enabled", exc_info=True)
            supported = {}

        # Only the deprecated spelling of the console option survives on <0.40;
        # setting both on a newer build just earns a deprecation warning.
        if "load_console" in supported:
            supported.pop("load_osd_console", None)

        _SCRIPT_OPTIONS = supported
        return dict(supported)


def _player_options(mpv_module: Any, *, video: bool) -> dict[str, Any]:
    """Return the libmpv options shared by the preview player and the probe.

    One builder, two callers, so the bundle smoke (:func:`mpv_probe_main`)
    constructs a core from the very option set the preview uses — an option the
    shipped libmpv rejects fails the smoke instead of the user's first play.
    """
    options: dict[str, Any] = {
        "vo": "libmpv" if video else "null",
        "video": "auto" if video else "no",
        "keep_open": "yes",
        "hwdec": "no",
        "pause": True,
        "input_default_bindings": False,
        "input_vo_keyboard": False,
        "load_scripts": False,
        "audio_display": "no",
        "sid": "no",
        "loglevel": "warn",
    }
    options.update(_builtin_script_options(mpv_module))
    return options


def create_mpv_player(
    log_handler: Callable[[str, str, str], None] | None = None,
    *,
    video: bool = True,
) -> mpv.MPV:
    """Build a libmpv handle configured for the embedded preview widget.

    - ``vo="libmpv"``: required by the render API (MpvRenderContext).
    - ``keep_open="yes"``: pause on EOF holding the last frame (QMediaPlayer
      end-of-media parity; replay is seek-0-then-unpause in the controller).
    - ``hwdec="no"``: software decode incl. dav1d AV1 — the reliability win
      that retired the Issue #82 apparatus.
    - ``pause=True``: present the first frame without starting playback.
    - ``sid="no"``: the widget's own subtitle overlay is the only subtitle
      surface; mpv must not render embedded subtitle tracks.
    - **No Lua, at all**: the embedded player uses none of mpv's scripting
      (bindings are off, controls and subtitles are ours, sources are local
      files), and any script that loads initializes LuaJIT, whose *normal,
      always-caught* internal unwinding raises first-chance SEH exceptions (code
      0xE24C4A02) on Windows. CPython's faulthandler — enabled for native-crash
      capture since 2.9.2 — dumps every thread on any SEH code it doesn't
      whitelist, and that GIL-less dump races the frame stacks of running
      threads (the python-mpv event thread churns hardest during playback): the
      dump itself dies with an access violation and takes the process with it.
      Word Curator crash, Issue #112.

      ``load_scripts=False`` is only half of it and was, alone, no fix at all:
      ``--load-scripts`` gates the *user* scripts directory, and mpv loads its
      builtin scripts (stats, console, select, positioning, commands, …) from
      ``mp_load_builtin_scripts`` on the option-change callback, which that flag
      never reaches. Each builtin is switched off by its own option — see
      :data:`_BUILTIN_SCRIPT_OPTIONS` and :func:`_builtin_script_options`.

    ``video=False`` builds an AUDIO-ONLY core for the case where no GL surface
    exists — the preview turned off by setting or by
    ``ANKI_MINER_NO_VIDEO_PREVIEW``. It is not a starved video core: asking for
    ``vo="libmpv"`` with no render context ever attached makes libmpv log
    "vo/libmpv: No render context set." instead of playing, so the video path
    has to be declined up front rather than left to fail.
    """
    mpv_module = load_mpv()
    _ensure_c_numeric()
    return mpv_module.MPV(**_player_options(mpv_module, video=video), log_handler=log_handler)


def terminate_mpv_player(player: Any, *, timeout_s: float = _MPV_TERMINATE_TIMEOUT_S) -> bool:
    """Call python-mpv's blocking terminate with a bounded caller-side join."""
    failed = threading.Event()

    def terminate() -> None:
        try:
            player.terminate()
        except Exception:  # noqa: BLE001 - teardown cannot escape a daemon helper
            failed.set()
            logger.exception("mpv terminate failed")

    thread = threading.Thread(target=terminate, daemon=True, name="mpv-terminate")
    thread.start()
    thread.join(timeout=max(timeout_s, 0.0))
    if thread.is_alive():
        # LR-08 ACCEPTED OPEN residual: fatal in-process MPV(...) construction and
        # use-after-free after timed-out terminate remain; an out-of-process mpv
        # renderer is a separate deferred item.
        logger.error("mpv terminate exceeded %.1fs; close continues", timeout_s)
        return False
    return not failed.is_set()


def mpv_probe_main() -> int:
    """Headless bundle-smoke probe (``ANKI_MINER_MPV_PROBE=1``).

    Loads libmpv through the normal resolution order and constructs a
    display-free core (the audio-only preview options plus ``ao=null`` — no GL,
    no audio device, offscreen-safe). Building it from :func:`_player_options`
    rather than a bare handle is deliberate: the smoke then proves the *shipped*
    libmpv accepts the exact option set the preview passes it. Prints a
    grep-able marker either way; exit code feeds bundle_smoke.sh.
    """
    try:
        mpv_module = load_mpv()
        _ensure_c_numeric()
        player = mpv_module.MPV(**_player_options(mpv_module, video=False), ao="null")
        version = player.mpv_version
        api = getattr(mpv_module, "MPV_VERSION", None)
        if not terminate_mpv_player(player):
            raise RuntimeError("mpv terminate timed out")
    except Exception as exc:  # noqa: BLE001 - probe reports, never crashes
        print(f"MPV_PROBE_FAIL: {exc}", flush=True)
        return 1
    print(
        f"MPV_PROBE_OK api={api} version={version!r} source={_RESOLVED_SOURCE}",
        flush=True,
    )
    return 0
